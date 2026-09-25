import * as Ble from './MageneBle';
import { concat } from './MageneBle';

// Magene C406 data screens - the TypeScript port of tools/magene_pages.py (format, field
// catalogue, limits; decoded from the OneLap APK) plus the read/write of magene_device.py
// read-pages / write-pages. Block: <pageCount><totalLen> then per page <n><n field codes>.

export const MAX_PAGES = 30;
export const MIN_FIELDS = 2;
export const MAX_FIELDS = 8;
export const EMPTY = 0xff;

// Generated from tools/magene_pages.py GROUPS (keep in sync - Magene.test.ts checks it).
export const FIELD_GROUPS: { group: string; fields: { code: number; name: string }[] }[] = [{"group": "Speed", "fields": [{"code": 16, "name": "Speed"}, {"code": 17, "name": "Avg Speed"}, {"code": 18, "name": "Max Speed"}, {"code": 19, "name": "[lap] Avg Speed"}, {"code": 20, "name": "[lap] Max Speed"}, {"code": 27, "name": "PRE Avg SPD"}, {"code": 28, "name": "PRE Max SPD"}, {"code": 26, "name": "BEST SPD"}]}, {"group": "Cadence", "fields": [{"code": 32, "name": "Current CAD"}, {"code": 33, "name": "Avg CAD"}, {"code": 34, "name": "Max CAD"}, {"code": 35, "name": "[lap] Avg CAD"}, {"code": 36, "name": "[lap] Max CAD"}, {"code": 43, "name": "PRE Avg CAD"}, {"code": 44, "name": "PRE Max CAD"}]}, {"group": "Heart rate", "fields": [{"code": 48, "name": "Current HR"}, {"code": 49, "name": "Avg HR"}, {"code": 50, "name": "Max HR"}, {"code": 51, "name": "[lap] Avg HR"}, {"code": 52, "name": "[lap] Max HR"}, {"code": 59, "name": "PRE AVG HR"}, {"code": 61, "name": "Heart Rate Zones"}, {"code": 60, "name": "LTHR%"}, {"code": 58, "name": "MHR%"}]}, {"group": "Power", "fields": [{"code": 64, "name": "Current Power"}, {"code": 65, "name": "Avg Power"}, {"code": 66, "name": "Max Power"}, {"code": 67, "name": "[lap] Avg Power"}, {"code": 68, "name": "[lap] Max Power"}, {"code": 69, "name": "Avg Power (last lap)"}, {"code": 70, "name": "Max Power (last lap)"}, {"code": 71, "name": "Estimated power"}, {"code": 80, "name": "3S POWER"}, {"code": 81, "name": "5S POWER"}, {"code": 82, "name": "10S POWER"}, {"code": 83, "name": "15S POWER"}, {"code": 84, "name": "3S AP MAX"}, {"code": 85, "name": "5S AP MAX"}, {"code": 86, "name": "10S AP MAX"}, {"code": 87, "name": "15S AP MAX"}, {"code": 96, "name": "Normalized Power®"}, {"code": 97, "name": "Training Stress®"}, {"code": 98, "name": "Intensity Factor®"}, {"code": 99, "name": "Variability Index"}, {"code": 106, "name": "FTP%"}, {"code": 90, "name": "Power kJ"}, {"code": 100, "name": "BALANCE"}, {"code": 91, "name": "Avg Left/Right Balance"}, {"code": 92, "name": "3s Left/Right Balance"}, {"code": 93, "name": "5s Left/Right Balance"}, {"code": 94, "name": "15s Left/Right Balance"}, {"code": 101, "name": "Torque effectiveness"}, {"code": 102, "name": "Pedal Smoothness"}, {"code": 107, "name": "POWER ZONE"}, {"code": 108, "name": "W/KG"}, {"code": 111, "name": "3s Power-to-Weight Ratio"}, {"code": 109, "name": "LAP W/KG"}, {"code": 110, "name": "PRE W/KG"}]}, {"group": "Distance", "fields": [{"code": 113, "name": "Total Distance"}, {"code": 114, "name": "[lap] Total Distance"}, {"code": 115, "name": "PRE DIST"}]}, {"group": "Slope", "fields": [{"code": 128, "name": "Current Slope"}, {"code": 129, "name": "Avg Slope"}, {"code": 130, "name": "Max Slope"}, {"code": 131, "name": "[lap] Avg Slope"}, {"code": 132, "name": "[lap] Max Slope"}]}, {"group": "Elevation", "fields": [{"code": 144, "name": "Current Elev."}, {"code": 145, "name": "Avg Elev."}, {"code": 146, "name": "Max Elev."}, {"code": 147, "name": "[lap] Avg Elev."}, {"code": 148, "name": "[lap] Max Elev."}]}, {"group": "Elevation gain & loss", "fields": [{"code": 160, "name": "ELE ASCENT"}, {"code": 165, "name": "ELE DESCENT"}, {"code": 161, "name": "LAP ASCENT"}, {"code": 166, "name": "LAP DESCENT"}, {"code": 170, "name": "VAM"}, {"code": 174, "name": "PRE GAIN"}, {"code": 172, "name": "Vertical descent speed VDM"}, {"code": 175, "name": "PRE LOSS"}, {"code": 169, "name": "Ascent/descent 0xA9"}, {"code": 171, "name": "Ascent/descent 0xAB"}, {"code": 173, "name": "Ascent/descent 0xAD"}]}, {"group": "Time", "fields": [{"code": 177, "name": "Moving Time"}, {"code": 178, "name": "[lap] Time"}, {"code": 176, "name": "Total"}, {"code": 179, "name": "Clock"}, {"code": 180, "name": "Sunset Time"}, {"code": 181, "name": "Sunrise Time"}, {"code": 186, "name": "Previous lap time"}, {"code": 187, "name": "Best Time"}]}, {"group": "Calories", "fields": [{"code": 242, "name": "Calories"}, {"code": 243, "name": "[lap] Calories"}, {"code": 250, "name": "CAL/H"}]}, {"group": "Others", "fields": [{"code": 240, "name": "Total laps"}, {"code": 241, "name": "Current Temp"}]}, {"group": "Electronic shifting", "fields": [{"code": 192, "name": "Gear"}, {"code": 193, "name": "Front Gear"}, {"code": 194, "name": "Rear Gear"}, {"code": 195, "name": "Front Power"}, {"code": 196, "name": "Rear Power"}]}, {"group": "Empty", "fields": [{"code": 255, "name": "Empty"}]}];

export const FIELD_NAMES: Record<number, string> = Object.fromEntries(
  FIELD_GROUPS.flatMap(g => g.fields.map(f => [f.code, f.name] as [number, string])));

export function decodePages(block: Uint8Array): number[][] {
  if (block.length < 2) throw new Error('pages block too short');
  const count = block[0], total = block[1];
  const body = block.subarray(2, 2 + total);
  if (body.length !== total) throw new Error(`pages block says ${total} bytes, has ${block.length - 2}`);
  const pages: number[][] = [];
  for (let i = 0; i < body.length;) {
    const n = body[i];
    const page = Array.from(body.subarray(i + 1, i + 1 + n));
    if (page.length !== n) throw new Error('truncated page');
    pages.push(page);
    i += 1 + n;
  }
  if (pages.length !== count) throw new Error(`pages block says ${count} pages, has ${pages.length}`);
  return pages;
}

export function validatePages(pages: number[][]) {
  if (pages.length < 1 || pages.length > MAX_PAGES) throw new Error(`1..${MAX_PAGES} screens`);
  pages.forEach((p, k) => {
    if (p.length < MIN_FIELDS || p.length > MAX_FIELDS) throw new Error(`screen ${k + 1}: ${MIN_FIELDS}..${MAX_FIELDS} fields`);
    for (const c of p) if (!(c in FIELD_NAMES)) throw new Error(`screen ${k + 1}: unknown field code 0x${c.toString(16)}`);
  });
}

export function encodePages(pages: number[][]): Uint8Array {
  validatePages(pages);
  const body: number[] = [];
  for (const p of pages) body.push(p.length, ...p);
  if (body.length > 255) throw new Error('layout too large for the device (255-byte limit)');
  return new Uint8Array([pages.length, body.length, ...body]);
}

async function readRaw(): Promise<Uint8Array | null> {
  const r = await Ble.command(new Uint8Array([0x40, 0x42]), [0x40, 0x42]);
  return r && r.length >= 3 && r[2] === 0 ? r : null;
}

export async function readPages(): Promise<number[][]> {
  const r = await readRaw();
  if (!r) throw new Error('No screens reply from the C406');
  return decodePages(r.subarray(3));
}

/** Write only over a layout we could read in this format; success = the device reads back
 *  exactly what was written (magene_device.py write-pages). */
export async function writePages(pages: number[][]): Promise<number[][]> {
  const block = encodePages(pages);
  const cur = await readRaw();
  if (!cur) throw new Error('Could not read the current screens');
  decodePages(cur.subarray(3));
  const ack = await Ble.command(concat([0x40, 0x43], block), [0x40, 0x43], 8000);
  if (ack && ack.length >= 3 && ack[2] === 2) await Ble.sleep(1500);
  const after = await readRaw();
  const back = after ? Array.from(after.subarray(3)) : [];
  if (back.length !== block.length || back.some((b, i) => b !== block[i])) throw new Error('The C406 didn’t take the new screens');
  return decodePages(after!.subarray(3));
}
