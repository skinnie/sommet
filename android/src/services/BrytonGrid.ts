import { NativeModules } from 'react-native';
import { base64ToBytes, bytesToBase64 } from './Base64';

// Bryton Aero 60 data screens (System/Grid.ini over USB) - the TypeScript port of
// tools/bryton_grid.py: same parsing, same in-place key edits (line order and the trailing NUL
// kept), same validation. BrytonGrid.test.ts pins it to the Python output. Format notes: see
// that file's docstring.

const Native = (NativeModules as any).BrytonUsb as {
  readFile(path: string): Promise<string>;
  writeFile(path: string, base64: string): Promise<boolean>;
};
const GRID_PATH = 'System/Grid.ini';
const SPECIAL: Record<string, string> = { '1012': 'Lap 1', '1013': 'Lap 2', '1014': 'Follow Track', '1015': 'Altitude' };

// Generated from tools/bryton_grid.py (catalogue()).
export const GRID_GROUPS: { group: string; fields: { id: number; name: string }[] }[] = [{"group": "Time", "fields": [{"id": 7, "name": "Time"}, {"id": 8, "name": "Ride Time"}, {"id": 9, "name": "Trip Time"}, {"id": 51, "name": "Lap Time"}, {"id": 60, "name": "Lap Count"}, {"id": 52, "name": "Last Lap Time"}, {"id": 10, "name": "Sunrise"}, {"id": 11, "name": "Sunset"}]}, {"group": "Speed", "fields": [{"id": 2, "name": "Speed"}, {"id": 3, "name": "Avg Speed"}, {"id": 4, "name": "Max Speed"}, {"id": 46, "name": "Lap Avg Speed"}, {"id": 48, "name": "Last Lap Avg Speed"}, {"id": 47, "name": "Lap Max Speed"}]}, {"group": "Distance", "fields": [{"id": 5, "name": "Distance"}, {"id": 49, "name": "Lap Distance"}, {"id": 59, "name": "ODO"}, {"id": 50, "name": "Last Lap Distance"}, {"id": 64, "name": "Trip1"}, {"id": 65, "name": "Trip2"}]}, {"group": "Altitude", "fields": [{"id": 14, "name": "Altitude"}, {"id": 18, "name": "Grade"}, {"id": 16, "name": "Alt. Gain"}, {"id": 17, "name": "Alt. Loss"}, {"id": 19, "name": "Uphill Dist."}, {"id": 20, "name": "Downhill Dist."}, {"id": 15, "name": "Max Alt."}]}, {"group": "Energy", "fields": [{"id": 81, "name": "Power Kilojoules"}, {"id": 0, "name": "Calories"}]}, {"group": "Temperature", "fields": [{"id": 1, "name": "Temp."}]}, {"group": "HR", "fields": [{"id": 21, "name": "Heart Rate"}, {"id": 22, "name": "Avg Heart Rate"}, {"id": 23, "name": "Max Heart Rate"}, {"id": 43, "name": "Max Heart Rate %"}, {"id": 44, "name": "LTHR%"}, {"id": 80, "name": "Heart Rate Zone"}, {"id": 42, "name": "LTHR Zone"}, {"id": 53, "name": "Lap Avg HR"}, {"id": 56, "name": "Lap LTHR%"}, {"id": 55, "name": "Lap MHR %"}, {"id": 57, "name": "Last Lap  Avg HR"}]}, {"group": "Cadence", "fields": [{"id": 24, "name": "Cadence"}, {"id": 25, "name": "Avg Cadence"}, {"id": 26, "name": "Max Cadence"}, {"id": 61, "name": "Lap Avg Cadence"}, {"id": 58, "name": "Last Lap  Avg Cadence"}]}, {"group": "Power", "fields": [{"id": 29, "name": "Power Now"}, {"id": 31, "name": "Avg Power"}, {"id": 30, "name": "Max Power"}, {"id": 38, "name": "Lap Avg  Power"}, {"id": 39, "name": "Lap Max  Power"}, {"id": 28, "name": "3s Power"}, {"id": 79, "name": "10s Power"}, {"id": 27, "name": "30s Power"}, {"id": 69, "name": "Normalized Power"}, {"id": 70, "name": "Training Stress Score"}, {"id": 68, "name": "Intensity Factor"}, {"id": 66, "name": "Specific Power"}, {"id": 32, "name": "FTP Zone"}, {"id": 36, "name": "FTP%"}, {"id": 33, "name": "MAP Zone"}, {"id": 37, "name": "MAP%"}, {"id": 89, "name": "Lap  Normalized Power"}, {"id": 40, "name": "Last Lap Avg Power"}, {"id": 41, "name": "Last Lap  Max Power"}, {"id": 91, "name": "Left Power"}, {"id": 92, "name": "Right Power"}]}, {"group": "Pedal Analysis", "fields": [{"id": 71, "name": "Current PB L-R"}, {"id": 72, "name": "Avg PB L-R"}, {"id": 76, "name": "Current PS L-R"}, {"id": 77, "name": "Avg PS L-R"}, {"id": 78, "name": "Max PS L-R"}, {"id": 73, "name": "Current TE L-R"}, {"id": 74, "name": "Avg TE L-R"}, {"id": 75, "name": "Max TE L-R"}]}, {"group": "Heading", "fields": [{"id": 82, "name": "Heading"}]}, {"group": "Di2 / E-Shifting", "fields": [{"id": 83, "name": "Di2 battery level"}, {"id": 84, "name": "Front Gear"}, {"id": 85, "name": "Rear Gear"}, {"id": 86, "name": "Gears"}, {"id": 87, "name": "Gear Combo"}, {"id": 88, "name": "Gear Ratio"}, {"id": 90, "name": "ESS battery level"}]}];
export const GRID_TABLE: Record<string, [number, number][]> = {"2": [[100, 75], [100, 25]], "3": [[100, 75], [50, 25], [50, 25]], "4": [[100, 33], [100, 33], [50, 34], [50, 34]], "5": [[100, 50], [50, 25], [50, 25], [50, 25], [50, 25]], "6": [[50, 33], [50, 33], [50, 33], [50, 33], [50, 34], [50, 34]], "7": [[50, 33], [50, 33], [50, 33], [50, 33], [100, 16], [50, 17], [50, 17]], "8": [[50, 33], [50, 33], [50, 33], [50, 33], [50, 16], [50, 16], [50, 17], [50, 17]], "9": [[50, 33], [50, 33], [100, 16], [50, 16], [50, 16], [50, 17], [50, 17], [50, 17], [50, 17]], "10": [[50, 33], [50, 33], [50, 16], [50, 16], [50, 16], [50, 16], [50, 17], [50, 17], [50, 17], [50, 17]]};
export const FIELD_NAME: Record<number, string> = Object.fromEntries(
  GRID_GROUPS.flatMap(g => g.fields.map(f => [f.id, f.name] as [number, string])));

export interface GridPage {
  page: number; title: string; special: string | null; enabled: number; fixed: boolean;
  count: number; sizes: number[]; fields: number[]; layouts: Record<string, number[]>;
}
export interface GridChange { page: number; count?: number; fields?: number[]; enabled?: number }

const KEY = /^(\d+)-(\d+)$/;

function split(bytes: Uint8Array): { lines: string[]; tail: Uint8Array } {
  let end = bytes.length;
  while (end > 0 && bytes[end - 1] === 0) end--;
  const text = decodeURIComponent(escape(String.fromCharCode(...bytes.subarray(0, end))));
  return { lines: text.split('\n'), tail: bytes.slice(end) };
}

function sections(lines: string[]): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  let cur: string | null = null;
  lines.forEach((line, i) => {
    const s = line.trim();
    if (s.startsWith('[') && s.endsWith(']')) { cur = s.slice(1, -1); out[cur] = {}; }
    else if (cur !== null && s.includes('=')) out[cur][s.split('=')[0].trim()] = i;
  });
  return out;
}
const val = (lines: string[], i: number) => lines[i].split('=').slice(1).join('=').trim();
const sizesOf = (keys: Record<string, number>) =>
  [...new Set(Object.keys(keys).filter(k => KEY.test(k)).map(k => parseInt(k, 10)))].sort((a, b) => a - b);

export function parseGrid(bytes: Uint8Array): GridPage[] {
  const { lines } = split(bytes);
  const pages: GridPage[] = [];
  for (const [name, keys] of Object.entries(sections(lines))) {
    const m = /^Page(\d+)_Cycling$/.exec(name);
    if (!m) continue;
    const sizes = sizesOf(keys);
    const count = 'type' in keys ? parseInt(val(lines, keys.type), 10) : (sizes[0] ?? 0);
    const layouts: Record<string, number[]> = {};
    for (const n of sizes) {
      const cells: number[] = [];
      for (let c = 1; c <= n; c++) if (`${n}-${c}` in keys) cells.push(parseInt(val(lines, keys[`${n}-${c}`]), 10));
      layouts[String(n)] = cells;
    }
    const special = 'name' in keys ? val(lines, keys.name) : null;
    const enabled = 'isEnabled' in keys ? parseInt(val(lines, keys.isEnabled), 10) : 1;
    pages.push({
      page: parseInt(m[1], 10), title: (special && SPECIAL[special]) || `Data ${m[1]}`, special,
      enabled, fixed: enabled === 2, count, sizes, fields: layouts[String(count)] ?? [], layouts,
    });
  }
  return pages.sort((a, b) => a.page - b.page);
}

export function patchGrid(bytes: Uint8Array, changes: GridChange[]): Uint8Array {
  const { lines, tail } = split(bytes);
  const secs = sections(lines);
  for (const ch of changes) {
    const keys = secs[`Page${ch.page}_Cycling`];
    if (!keys) throw new Error(`no screen ${ch.page} on this device`);
    const sizes = sizesOf(keys);
    const count = ch.count ?? parseInt(val(lines, keys.type), 10);
    if (!sizes.includes(count)) throw new Error(`screen ${ch.page}: ${count} fields isn't a layout it has`);
    if (ch.count != null) lines[keys.type] = `type=${count}`;
    if (ch.fields) {
      if (ch.fields.length !== count) throw new Error(`screen ${ch.page}: ${count} fields expected`);
      ch.fields.forEach((f, i) => {
        if (!(f in FIELD_NAME)) throw new Error(`screen ${ch.page}: unknown field id ${f}`);
        const k = `${count}-${i + 1}`;
        if (!(k in keys)) throw new Error(`screen ${ch.page}: no cell ${k} in the file`);
        lines[keys[k]] = `${k}=${f}`;
      });
    }
    if (ch.enabled != null) {
      if (!('isEnabled' in keys)) throw new Error(`screen ${ch.page} has no isEnabled key`);
      const cur = parseInt(val(lines, keys.isEnabled), 10);
      if (cur === 2 && ch.enabled !== 2) throw new Error(`screen ${ch.page} is always shown on the device`);
      if (![0, 1].includes(ch.enabled) && !(cur === 2 && ch.enabled === 2)) throw new Error('enabled must be 0 or 1');
      lines[keys.isEnabled] = `isEnabled=${ch.enabled}`;
    }
  }
  const text = unescape(encodeURIComponent(lines.join('\n')));
  const out = new Uint8Array(text.length + tail.length);
  for (let i = 0; i < text.length; i++) out[i] = text.charCodeAt(i);
  out.set(tail, text.length);
  return out;
}

export async function readGrid(): Promise<GridPage[]> {
  return parseGrid(base64ToBytes(await Native.readFile(GRID_PATH)));
}

/** Read-modify-write (never a blind write), then read back. */
export async function writeGrid(changes: GridChange[]): Promise<GridPage[]> {
  const cur = base64ToBytes(await Native.readFile(GRID_PATH));
  const next = patchGrid(cur, changes);
  await Native.writeFile(GRID_PATH, bytesToBase64(next));
  return readGrid();
}
