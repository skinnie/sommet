// Ambit3 TrainingProgram region (the NATIVE Movescount-era "planned move" / "Today 1/2" card),
// port of tools/training_program.py. HARDWARE-CONFIRMED 2026-09-03 on an Ambit3 Sport (fw
// 2.4.17): with the header signature below, TIME mode -> [Next] shows the planned move exactly
// as the Movescount-era user guide §3.39 describes. The format was decompiled from the watch's
// MSP430X firmware (assets/Firmware/re-out/sfi2_code_recovery_notes.md, "MSP430X hunt (pass 7)").
// The TS builder is proven byte-identical to the Python builder (TrainingProgramCodec.test.ts).

export const TRAINING_PROGRAM_BASE = 0x001000;
export const TRAINING_PROGRAM_REGION_SIZE = 3072;
const ITEM_SIZE = 40;

const UNI_TO_ISO: Record<number, number> = {
  0x20ac: 0xa4, 0x0160: 0xa6, 0x0161: 0xa8, 0x017d: 0xb4, 0x017e: 0xb8, 0x0152: 0xbc, 0x0153: 0xbd, 0x0178: 0xbe,
};
function encodeIso(s: string, max: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < s.length && out.length < max; i++) {
    const c = s.charCodeAt(i);
    const m = UNI_TO_ISO[c];
    out.push(m !== undefined ? m : (c <= 0xff ? c : 0x3f));
  }
  return out;
}
const pushU16 = (a: number[], v: number) => a.push(v & 0xff, (v >> 8) & 0xff);
const pushU32 = (a: number[], v: number) => a.push(v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff);

export interface TrainingItem {
  activityId: number;
  durationMinutes: number;
  intensity: number;   // 1-5
  name: string;
  dayOffset?: number;  // days from the header base date; 0 = the base/earliest move itself
  completed?: boolean;
  moveId?: number;
  distance?: number;   // metres
}

/** One 40-byte TrainingProgram item. Exact port of build_training_item (Finding 29 layout). */
export function buildTrainingItem(it: TrainingItem): Uint8Array {
  const out: number[] = [];
  out.push((it.dayOffset ?? 0) & 0xff);          // off 0
  out.push(it.completed ? 1 : 0);                // off 1
  pushU16(out, it.activityId);                   // off 2
  pushU32(out, it.moveId ?? 0);                  // off 4
  pushU32(out, it.distance ?? 0);                // off 8
  pushU16(out, it.durationMinutes);              // off 12
  out.push(it.intensity & 0xff);                 // off 14
  out.push(0);                                   // off 15 padding
  const name = encodeIso(it.name, 23);           // off 16..38
  for (let i = 0; i < 23; i++) out.push(name[i] ?? 0);
  out.push(0);                                   // off 39 padding
  if (out.length !== ITEM_SIZE) throw new Error(`training item is ${out.length} bytes, expected ${ITEM_SIZE}`);
  return Uint8Array.from(out);
}

/** Header bytes 4..7: the watch firmware's signature constant. The MSP430X watch firmware
 * (fw 2.4.17, FUN_00059a3a via the TrainingProgram reload FUN_0004c250) requires four strictly
 * increasing bytes with the last < 0x65 here before it will parse the region at all; anything
 * else makes it substitute an EMPTY program, hiding the TIME-mode "Today" card. Every write
 * before 2026-09-03 put 0 / 0xFFFFFFFF here - that was the whole reason nothing ever showed. */
export const HEADER_SIGNATURE = [0x3c, 0x46, 0x50, 0x5a];

export interface BaseDate { year: number; month: number; day: number; }

/** The full TrainingProgram blob: 12-byte header [u16 year][u8 month][u8 day][SIGNATURE 3C 46 50 5A]
 * [u16 count][u16 0xFFFF] then the items back to back. Exact port of build_training_program
 * (tools/training_program.py). `baseDate` is the EARLIEST move's calendar date (year 2013-2099);
 * each item's dayOffset counts from it. Returns the USED bytes; the caller writes them via
 * writeRegion(TRAINING_PROGRAM_BASE, blob, blob.length). */
export function buildTrainingProgram(items: Uint8Array[], baseDate: BaseDate): Uint8Array {
  if (baseDate.year < 2013 || baseDate.year > 2099 || baseDate.month < 1 || baseDate.month > 12 || baseDate.day < 1 || baseDate.day > 31) {
    throw new Error(`training program base date out of the firmware's range: ${JSON.stringify(baseDate)}`);
  }
  const header: number[] = [];
  pushU16(header, baseDate.year);                // off 0
  header.push(baseDate.month & 0xff);            // off 2
  header.push(baseDate.day & 0xff);              // off 3
  header.push(...HEADER_SIGNATURE);              // off 4..7 (REQUIRED)
  pushU16(header, items.length);                 // off 8
  pushU16(header, 0xffff);                       // off 10
  const total = new Uint8Array(header.length + items.reduce((n, i) => n + i.length, 0));
  total.set(header, 0);
  let p = header.length;
  for (const it of items) { total.set(it, p); p += it.length; }
  return total;
}
