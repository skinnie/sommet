import { readLegacyRegion } from '../native/AmbitUsbModule';
import { base64ToBytes } from './Base64';

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
      if (tag === TAG_SETTINGS && body.length >= 90) modes.push(decodeSettings(body));
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
