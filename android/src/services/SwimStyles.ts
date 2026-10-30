import { readSettingsRaw, writeSettingsRaw, getDeviceInfo } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { splitEntries, encodeEntry } from './AmbitSettingsWriter';
import { saveWatchBackup } from './WatchBackup';

// The swim styles a watch has learned ("Swimming style training") - desktop twin:
// tools/swim_styles.py. They are settings: group Sports.Swimming.StyleCoeff, one record per
// stroke, each its own entry [u8 style][4 x int16 LE]. A stroke never taught reads all zeros.
//
// The entry id comes from each watch's own schema (the desktop reads it live; here it is a
// table, checked against the Emu/Finch/Ibisbill/Kaka descriptors 2026-10-30): the Vertical
// numbers it 0x41, the rest of the Ambit3 family 0x40. Same strokes and layout on all four.
export const SWIM_STYLE_ENTRY: Record<string, number> = {
  Emu: 0x40, Finch: 0x40, Ibisbill: 0x40, Kaka: 0x41,
};
export const STYLE_NAMES = ['Other', 'Butterfly', 'Backstroke', 'Breaststroke', 'Freestyle', 'Drill'];
const RECORD = 9;   // 1 + 4 x 2

export interface SwimStyle { id: number; style: string; vectors: number[]; }
export interface SwimChange { id: number; style: string; from: number[]; to: number[]; }

export const supportsSwimStyles = (model?: string) => !!model && model in SWIM_STYLE_ENTRY;
export const isTaught = (s: SwimStyle) => s.vectors.some(v => v !== 0);

function decodeRecord(data: Uint8Array): SwimStyle {
  const view = new DataView(data.buffer, data.byteOffset, data.length);
  const id = data[0];
  const vectors = [0, 1, 2, 3].map(i => view.getInt16(1 + i * 2, true));
  return { id, style: STYLE_NAMES[id] ?? `Style ${id}`, vectors };
}

function encodeRecord(id: number, vectors: number[]): Uint8Array {
  const out = new Uint8Array(RECORD);
  const view = new DataView(out.buffer);
  out[0] = id;
  vectors.forEach((v, i) => view.setInt16(1 + i * 2, v, true));
  return out;
}

/** The records in wire order; null when the reply has no settings. */
export function decodeSwimStyles(bytes: Uint8Array, entryId: number): SwimStyle[] | null {
  const split = splitEntries(bytes);
  if (!split) return null;
  return split.entries.filter(([id, d]) => id === entryId && d.length === RECORD)
    .map(([, d]) => decodeRecord(d));
}

/** The 0x1101 payload putting `wanted` on the watch (matched by stroke id; strokes not listed
 * keep the watch's values), or an error. Only the swim style entries are sent - the read's
 * header with its write flag set, the magic, every record (patched where it changes). */
export function planSwimRestore(read: Uint8Array, entryId: number, wanted: SwimStyle[]):
  { payload: Uint8Array | null; changes: SwimChange[]; error?: string } {
  const split = splitEntries(read);
  if (!split) return { payload: null, changes: [], error: 'no settings in the watch\'s reply' };
  const records = split.entries.filter(([id, d]) => id === entryId && d.length === RECORD);
  if (!records.length) return { payload: null, changes: [], error: 'the watch\'s settings carry no swim styles' };
  const current = records.map(([, d]) => decodeRecord(d));
  const next: Uint8Array[] = records.map(([, d]) => new Uint8Array(d));
  const changes: SwimChange[] = [];
  for (const w of wanted) {
    const n = current.findIndex(c => c.id === w.id);
    if (n < 0) return { payload: null, changes: [], error: `${w.style} is not on this watch` };
    if (!Array.isArray(w.vectors) || w.vectors.length !== 4) {
      return { payload: null, changes: [], error: `${w.style}: expected 4 numbers` };
    }
    if (w.vectors.some(v => !Number.isInteger(v) || v < -32768 || v > 32767)) {
      return { payload: null, changes: [], error: `${w.style}: not a value the watch can store` };
    }
    if (w.vectors.every((v, i) => v === current[n].vectors[i])) continue;
    next[n] = encodeRecord(w.id, w.vectors);
    changes.push({ id: w.id, style: current[n].style, from: current[n].vectors, to: w.vectors });
  }
  if (!changes.length) return { payload: null, changes };
  const prefix = new Uint8Array(split.prefix);
  if (prefix.length > 0) prefix[prefix.length - 1] = 0x01;
  const magic = read.subarray(split.prefix.length, split.prefix.length + 8);
  const chunks = [prefix, new Uint8Array(magic), ...next.map(d => encodeEntry(entryId, d))];
  const out = new Uint8Array(chunks.reduce((s, c) => s + c.length, 0));
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return { payload: out, changes };
}

/** The connected watch's swim styles (the caller has connected), or null if it has none. */
export async function readSwimStyles(model?: string): Promise<SwimStyle[] | null> {
  const entryId = model ? SWIM_STYLE_ENTRY[model] : undefined;
  if (entryId === undefined) return null;
  return decodeSwimStyles(base64ToBytes(await readSettingsRaw()), entryId);
}

/** Put `wanted` on the connected watch: its current settings are saved first, only the swim
 * style entries are written, and a re-read confirms. */
export async function writeSwimStyles(model: string | undefined, wanted: SwimStyle[]):
  Promise<{ ok: boolean; changes: SwimChange[]; error?: string }> {
  const entryId = model ? SWIM_STYLE_ENTRY[model] : undefined;
  if (entryId === undefined) return { ok: false, changes: [], error: 'this watch has no swim styles' };
  const beforeB64 = await readSettingsRaw();
  const plan = planSwimRestore(base64ToBytes(beforeB64), entryId, wanted);
  if (plan.error) return { ok: false, changes: [], error: plan.error };
  if (!plan.payload) return { ok: true, changes: [] };
  let serial = 'unknown';
  try { serial = (await getDeviceInfo()).serial || serial; } catch { /* label only */ }
  await saveWatchBackup('swim-styles', beforeB64, serial);
  await writeSettingsRaw(bytesToBase64(plan.payload));
  const after = decodeSwimStyles(base64ToBytes(await readSettingsRaw()), entryId) ?? [];
  const ok = plan.changes.every(c => {
    const got = after.find(a => a.id === c.id);
    return !!got && got.vectors.every((v, i) => v === c.to[i]);
  });
  return { ok, changes: plan.changes, error: ok ? undefined : 'the watch did not keep the new swim styles' };
}
