// Bryton Aero 60 athlete profile (System/Profile.bin) read/write, pure TS.
//
// Twin of the desktop tools/bryton_profile.py (offsets hardware-confirmed by write->device-display
// round-trip, 2026-09-24). The profile the user sets in the device's Settings->Profile lives in
// Profile.bin (6695 B; the rest is display grid/zone layouts). Writing is safe - no enforced
// checksum - but only ever touch these known offsets and keep the file length unchanged. Some
// values are stored twice (a user copy + a zone-table header copy); both are written together.

export type BrytonProfileField =
  'gender' | 'age' | 'height' | 'weight' | 'maxHr' | 'lthr' | 'restHr' | 'ftp' | 'map';

type Kind = 'u8' | 'u16' | 'f32';
interface FieldSpec { kind: Kind; offsets: number[]; min: number; max: number; }

// canonical offset first; extra offsets are duplicate copies kept in sync
const FIELDS: Record<BrytonProfileField, FieldSpec> = {
  gender: { kind: 'u8',  offsets: [0x910], min: 0, max: 1 },   // 1 = male
  age:    { kind: 'u8',  offsets: [0x911], min: 5, max: 120 },
  height: { kind: 'f32', offsets: [0x912], min: 80, max: 250 },
  weight: { kind: 'f32', offsets: [0x91a], min: 20, max: 250 },
  maxHr:  { kind: 'u16', offsets: [0x922, 0xcbb], min: 100, max: 240 },
  lthr:   { kind: 'u16', offsets: [0x924, 0xcd9], min: 80, max: 230 },
  restHr: { kind: 'u16', offsets: [0x926], min: 30, max: 120 },
  ftp:    { kind: 'u16', offsets: [0xd15], min: 30, max: 600 },
  map:    { kind: 'u16', offsets: [0xcf7], min: 40, max: 800 },
};

export const EXPECT_SIZE = 6695;

export type BrytonProfile = Record<BrytonProfileField, number>;

function get(view: DataView, spec: FieldSpec): number {
  const o = spec.offsets[0];
  if (spec.kind === 'u8') return view.getUint8(o);
  if (spec.kind === 'u16') return view.getUint16(o, true);
  return Math.round(view.getFloat32(o, true) * 10) / 10;
}

export function readBrytonProfile(bytes: Uint8Array): BrytonProfile {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = {} as BrytonProfile;
  (Object.keys(FIELDS) as BrytonProfileField[]).forEach(k => { out[k] = get(view, FIELDS[k]); });
  return out;
}

// Returns a NEW Uint8Array with the given fields patched (all copies). Validates bounds and length.
export function writeBrytonProfile(bytes: Uint8Array, changes: Partial<BrytonProfile>): Uint8Array {
  if (bytes.length !== EXPECT_SIZE) throw new Error(`Profile.bin is ${bytes.length} B, expected ${EXPECT_SIZE}`);
  for (const [name, value] of Object.entries(changes) as [BrytonProfileField, number][]) {
    const spec = FIELDS[name];
    if (!spec) throw new Error(`unknown profile field ${name}`);
    if (value < spec.min || value > spec.max) throw new Error(`${name}=${value} out of [${spec.min},${spec.max}]`);
  }
  const copy = new Uint8Array(bytes);            // never mutate the input
  const view = new DataView(copy.buffer);
  for (const [name, value] of Object.entries(changes) as [BrytonProfileField, number][]) {
    const spec = FIELDS[name];
    for (const o of spec.offsets) {
      if (spec.kind === 'u8') view.setUint8(o, Math.round(value) & 0xff);
      else if (spec.kind === 'u16') view.setUint16(o, Math.round(value) & 0xffff, true);
      else view.setFloat32(o, value, true);
    }
  }
  return copy;
}
