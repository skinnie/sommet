import { readLegacyRegion, writeLegacyRegion, connect, disconnect, isBleTransportActive } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';

// Ambit 1/2 (Bluebird) sport-mode READ - the one thing openambit can't do for this family
// (it only blind-writes). Ported from the desktop tools/legacy_sport_modes.py, validated
// byte-for-byte against a real Ambit2. The Ambit3 CustomModes path (readCustomModesRaw, a
// 12288-byte SBEM region) does NOT apply here and throws on an Ambit2 - this reads region
// 0x2000's nested TLV instead (readRegion is generic 0x0b17, no new native code).
//
// Region 0x2000, nested [u16 tag][u16 len][body]: 0x0003 root / 0x0100 modes / 0x0101 one
// mode / 0x0102 its 90-byte settings blob / 0x0105-0x010a displays / 0x0200,0x0210 multisport
// groups. The 90-byte blob is openambit's ambit_sport_mode_settings_t (Ambit2; Ambit1 is 76,
// handled by only decoding fields inside the blob length). hrbelt_and_pods bitfield decoded
// from a real capture (matches SportModeCodec's HW_* masks).

const REGION_ADDR = 0x2000;
const REGION_BYTES = 16384;
const TAG_SETTINGS = 0x0102;
const CONTAINERS = new Set([
  0x0003, 0x0100, 0x0101, 0x0105, 0x0106, 0x0107, 0x0108, 0x0109, 0x0200, 0x0210,
]);
const POD = { hrBelt: 0x0003, powerPod: 0x0040, cadencePod: 0x0080, footPod: 0x0100, bikePod: 0x0800 };

export interface LegacyMode {
  name: string;
  activityId: number;
  modeId: number;
  gpsInterval: number;
  recordingInterval: number;
  autolapM: number;
  altiBaroMode: number;
  heartrateMin: number;
  heartrateMax: number;
  useHrLimits: boolean;
  useHw: number; // raw hrbelt_and_pods bitfield (maps to ExerciseMode.useHw)
  hrBelt: boolean;
  footPod: boolean;
  bikePod: boolean;
  powerPod: boolean;
  cadencePod: boolean;
}

function u16(b: Uint8Array, o: number): number {
  return b[o] | (b[o + 1] << 8);
}

function decodeSettings(blob: Uint8Array): LegacyMode {
  let name = '';
  for (let i = 0; i < 16 && blob[i]; i++) name += String.fromCharCode(blob[i]); // latin1
  const pods = u16(blob, 22);
  return {
    name,
    activityId: u16(blob, 16),
    modeId: u16(blob, 18),
    altiBaroMode: u16(blob, 24),
    gpsInterval: u16(blob, 26),
    recordingInterval: u16(blob, 28),
    autolapM: u16(blob, 30),
    heartrateMax: u16(blob, 32),
    heartrateMin: u16(blob, 34),
    useHrLimits: u16(blob, 36) !== 0,
    useHw: pods,
    hrBelt: (pods & POD.hrBelt) !== 0,
    powerPod: (pods & POD.powerPod) !== 0,
    cadencePod: (pods & POD.cadencePod) !== 0,
    footPod: (pods & POD.footPod) !== 0,
    bikePod: (pods & POD.bikePod) !== 0,
  };
}

function parseRegion(data: Uint8Array): LegacyMode[] {
  const modes: LegacyMode[] = [];
  const walk = (b: Uint8Array) => {
    let o = 0;
    while (o + 4 <= b.length) {
      const tag = u16(b, o);
      const ln = u16(b, o + 2);
      const body = b.subarray(o + 4, o + 4 + ln);
      if (body.length !== ln) break; // truncated tail
      // The settings blob is 90 bytes on the Ambit2 but only 76 on the Ambit1 (five capabilities
      // dropped - see tools/legacy_sport_modes.py). decodeSettings reads only fields inside the
      // blob length (all within the first 38 bytes), so accept both; the old >=90 gate silently
      // dropped every Ambit1 mode (André, 2026-08-27: "ambit 1 sports mode android don't work").
      if (tag === TAG_SETTINGS && body.length >= 38) modes.push(decodeSettings(body));
      else if (CONTAINERS.has(tag) && ln >= 4) walk(body);
      o += 4 + ln;
      if (tag === 0 && ln === 0) break;
    }
  };
  walk(data);
  return modes.filter(m => m.name);
}

/** Read the connected Ambit1/2's sport modes off region 0x2000. The watch must be connected. */
export async function readLegacySportModes(): Promise<LegacyMode[]> {
  const b64 = await readLegacyRegion(REGION_ADDR, REGION_BYTES);
  if (!b64) return [];
  return parseRegion(base64ToBytes(b64));
}

// ─── WRITE (2026-08-27, André: "go for both") ────────────────────────────────────────────────
// The legacy sport-mode region 0x2000 is a nested TLV, written whole and then COMMITTED. Editing a
// field is a read-modify-write: read the region, patch the target mode's 0x0102 settings blob in
// place, write the whole region back (native writeLegacyRegion does the 0x0b16 chunks + the 0x0b18
// commit tail), re-read to confirm. Everything except the patched field is preserved byte-for-byte.
//
// The 0x0b18 commit tail is MANDATORY: without it the watch acks the 0x0b16 chunks but discards
// them (they read back in-session then revert after a reconnect). Its `extra` word is 0xffffffff
// for this region - proven content-independent across 160 real writes in the SuuntoLink<->Ambit2
// capture assets/pcap/ambit2_suuntolink_settings_sportmodes.pcap.
const SPORT_MODE_COMMIT_EXTRA = 0xffffffff;

export interface LegacyModePatch {
  name?: string;            // <=16 chars, latin1, null-padded
  autolapM?: number;
  heartrateMin?: number;
  heartrateMax?: number;
  useHrLimits?: boolean;
  useHw?: number;           // raw hrbelt_and_pods bitfield
}

/** Absolute byte offset (into the region) of each named mode's 0x0102 settings blob, in the SAME
 * order readLegacySportModes returns them (nameless blobs filtered out identically). */
function modeBlobOffsets(data: Uint8Array): number[] {
  const offsets: number[] = [];
  const walk = (start: number, end: number) => {
    let o = start;
    while (o + 4 <= end) {
      const tag = u16(data, o);
      const ln = u16(data, o + 2);
      const bodyStart = o + 4;
      const bodyEnd = bodyStart + ln;
      if (bodyEnd > end) break; // truncated tail
      if (tag === TAG_SETTINGS && ln >= 38) {
        if (decodeSettings(data.subarray(bodyStart, bodyEnd)).name) offsets.push(bodyStart);
      } else if (CONTAINERS.has(tag) && ln >= 4) {
        walk(bodyStart, bodyEnd);
      }
      o = bodyEnd;
      if (tag === 0 && ln === 0) break;
    }
  };
  walk(0, data.length);
  return offsets;
}

/**
 * Edit one Ambit1/2 sport mode's fields and write region 0x2000 back. `modeIndex` is the index in
 * readLegacySportModes()' output. Returns the re-read mode list so the caller can confirm the watch
 * applied it. The watch must be connected.
 */
export async function writeLegacySportMode(modeIndex: number, patch: LegacyModePatch): Promise<LegacyMode[]> {
  // Ambit1/2 are USB-only (no BLE), but keep the same transport guard the rest of the app uses:
  // over an already-open link don't re-connect, over USB open a short-lived connection for the
  // whole read-patch-write-verify cycle (the raw readLegacyRegion/writeLegacyRegion need g_device).
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    const b64 = await readLegacyRegion(REGION_ADDR, REGION_BYTES);
    if (!b64) throw new Error('Could not read the sport-mode region.');
    const data = base64ToBytes(b64);
    const offsets = modeBlobOffsets(data);
    if (modeIndex < 0 || modeIndex >= offsets.length) {
      throw new Error(`Sport mode ${modeIndex} not found (region has ${offsets.length}).`);
    }
    const blob = offsets[modeIndex];
    const out = Uint8Array.from(data); // copy - patch this, leave everything else byte-exact
    const setU16 = (fieldOff: number, v: number) => {
      out[blob + fieldOff] = v & 0xff;
      out[blob + fieldOff + 1] = (v >> 8) & 0xff;
    };
    if (patch.autolapM !== undefined) setU16(30, Math.max(0, Math.min(65535, Math.round(patch.autolapM))));
    if (patch.heartrateMax !== undefined) setU16(32, Math.max(0, Math.min(65535, Math.round(patch.heartrateMax))));
    if (patch.heartrateMin !== undefined) setU16(34, Math.max(0, Math.min(65535, Math.round(patch.heartrateMin))));
    if (patch.useHrLimits !== undefined) setU16(36, patch.useHrLimits ? 1 : 0);
    if (patch.useHw !== undefined) setU16(22, patch.useHw & 0xffff);
    if (patch.name !== undefined) {
      for (let i = 0; i < 16; i++) out[blob + i] = i < patch.name.length ? (patch.name.charCodeAt(i) & 0xff) : 0;
    }
    const ok = await writeLegacyRegion(REGION_ADDR, bytesToBase64(out), SPORT_MODE_COMMIT_EXTRA);
    if (!ok) throw new Error('The watch did not accept the sport-mode write.');
    return parseRegion(base64ToBytes(await readLegacyRegion(REGION_ADDR, REGION_BYTES))); // re-read to verify, same connection
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
