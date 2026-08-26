import { writePersonalSetting, readPersonalSettings } from '../native/AmbitUsbModule';

// Ambit 1/2 (Bluebird) legacy personal-settings WRITE. Field offsets in the 0x0b00/0x0b01
// struct, reverse-engineered from a real SuuntoLink<->Ambit2 USB capture (2026-08-26, see
// docs/ambit2_protocol_findings.md). Offsets are family-common (Ambit1 132 B / Ambit2 188 B
// share them); the native side (AmbitUsbModule.writePersonalSetting) reads the whole struct,
// patches this one field, and writes it back at the device's own length. `scale` is the
// raw->display multiplier - matching AmbitPersonalSettingsReader.ts - so display->raw is
// value / scale. Only the "Personal" profile fields are writable here (the ones the capture
// proved); units/alarm/etc. are read-only until each is captured the same way.
export interface WritableField {
  offset: number;
  width: 1 | 2;
  scale?: number;
}

export const AMBIT12_WRITABLE: Record<string, WritableField> = {
  gender:        { offset: 55, width: 1 },              // 1 male / 0 female
  birth_year:    { offset: 50, width: 2 },
  weight:        { offset: 48, width: 2, scale: 0.01 }, // display kg, stored kg*100
  height:        { offset: 56, width: 1 },              // cm
  max_hr:        { offset: 52, width: 1 },              // bpm
  rest_hr:       { offset: 53, width: 1 },              // bpm
  fitness_level: { offset: 54, width: 1 },
};

export function isWritablePersonalField(key: string): boolean {
  return key in AMBIT12_WRITABLE;
}

/** display value -> raw int for the struct (inverse of the reader's scale), range-checked. */
export function toRawValue(key: string, displayValue: number): number {
  const f = AMBIT12_WRITABLE[key];
  if (!f) throw new Error(`not a writable personal field: ${key}`);
  const raw = f.scale ? Math.round(displayValue / f.scale) : Math.round(displayValue);
  const max = f.width === 2 ? 65535 : 255;
  if (raw < 0 || raw > max) throw new Error(`value out of range for ${key}: ${raw}`);
  return raw;
}

/**
 * Write one personal-settings field (display value) to a connected Ambit1/2, then re-read to
 * confirm the watch actually applied it (this project's "prove it, don't trust the ACK" rule).
 * Returns the confirmed display value read back. Throws on write failure or read-back mismatch.
 * The watch must already be connected.
 */
export async function writePersonalField(key: string, displayValue: number): Promise<number> {
  const f = AMBIT12_WRITABLE[key];
  if (!f) throw new Error(`not a writable personal field: ${key}`);
  const raw = toRawValue(key, displayValue);
  const ok = await writePersonalSetting(f.offset, f.width, raw);
  if (!ok) throw new Error(`native write failed for ${key}`);
  const obj = JSON.parse(await readPersonalSettings()) as Record<string, number>;
  const back = obj[key];
  if (typeof back !== 'number') throw new Error(`read-back missing ${key}`);
  const backDisplay = f.scale ? Math.round(back * f.scale * 100) / 100 : back;
  const wantDisplay = f.scale ? Math.round(displayValue * 100) / 100 : Math.round(displayValue);
  if (backDisplay !== wantDisplay) {
    throw new Error(`read-back mismatch for ${key}: wrote ${wantDisplay}, watch has ${backDisplay}`);
  }
  return backDisplay;
}
