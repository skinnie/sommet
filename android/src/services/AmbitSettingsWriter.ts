import { readSettingsRaw, writeSettingsRaw } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { SettingField, decodeSettings } from './AmbitSettingsReader';
import {
  WRITE_TEMPLATES,
  KEY_SCREEN,
  ENUM_VALUES,
  BOOL_ENTRIES,
} from './AmbitSettingsTemplates';

// Which per-device write plumbing to use. Ambit3 family and Traverse share the same 0x1101
// template mechanism but with different entry ids (their schemas differ); Kailash never gets
// here (it writes single entries over 0x1201). Defaults to 'ambit3' for callers that predate
// the per-device split.
export type WriteDevice = 'ambit3' | 'traverse' | 'kailash';

// Real, hardware-confirmed write dance, 2026-08-08: read the full settings blob fresh,
// patch exactly the bytes for one field (found by its real entry ID, from whichever field
// table the caller passes - AMBIT3_SETTINGS_FIELDS or KAILASH_SETTINGS_FIELDS, never a
// cached/assumed byte offset), write the whole blob back, and re-read to confirm - mirrors
// the companion research project's tools/settings_write.py's own write_one() exactly,
// including its "prove it, don't just trust the ACK" standard: `ok` here is only true
// once a fresh re-read actually shows the new value, not just because the native write
// call itself didn't throw.

const MAGIC = [0x53, 0x42, 0x45, 0x4d, 0x30, 0x31, 0x30, 0x32]; // "SBEM0102"

/** Every entry in an SBEM payload, in wire order: [entryId, data]. */
export function splitEntries(bytes: Uint8Array): { prefix: Uint8Array; entries: Array<[number, Uint8Array]> } | null {
  const head = findMagic(bytes);
  if (head < 0) return null;
  const entries: Array<[number, Uint8Array]> = [];
  let off = head + 8;
  while (off + 2 <= bytes.length) {
    const id = bytes[off];
    let len = bytes[off + 1];
    off += 2;
    if (len === 0xff) {
      if (off + 4 > bytes.length) break;
      len = (bytes[off] | (bytes[off + 1] << 8) | (bytes[off + 2] << 16) | (bytes[off + 3] << 24)) >>> 0;
      off += 4;
    }
    entries.push([id, bytes.subarray(off, off + len)]);
    off += len;
  }
  return { prefix: bytes.subarray(0, head), entries };
}

export function encodeEntry(id: number, data: Uint8Array): Uint8Array {
  // Lengths of 0xff or more use the 4-byte escape, exactly as the reader expects.
  if (data.length < 0xff) {
    const out = new Uint8Array(2 + data.length);
    out[0] = id; out[1] = data.length; out.set(data, 2);
    return out;
  }
  const out = new Uint8Array(6 + data.length);
  out[0] = id; out[1] = 0xff;
  out[2] = data.length & 0xff;
  out[3] = (data.length >>> 8) & 0xff;
  out[4] = (data.length >>> 16) & 0xff;
  out[5] = (data.length >>> 24) & 0xff;
  out.set(data, 6);
  return out;
}

/** Whether the watch's CURRENT value for an entry is one we can represent.
 *
 * SuuntoLink drops a field from its write template entirely when the answer is no - proven
 * in the captures, where a watch left holding GpsPositionFormat=15 makes SuuntoLink's own
 * write shrink and omit that entry. Re-sending a value we cannot interpret, or substituting
 * a legal one, would both be worse than leaving the field for the watch to keep owning. */
function representable(device: WriteDevice, entryId: number, data: Uint8Array): boolean {
  const values = ENUM_VALUES[device][entryId];
  if (values) return data.length >= 1 && values.includes(data[0]);
  if (BOOL_ENTRIES[device].includes(entryId)) return data.length === 1 && (data[0] === 0 || data[0] === 1);
  return true;
}

/** SuuntoLink's own 0x1101 payload: the prefix with its last byte flipped 0x00 -> 0x01, the
 * magic, then only the entries belonging to `screen` - patched where this write changes one.
 *
 * This is what keeps the paired phone's BLE bond keys off the wire. The previous version
 * echoed the entire settings blob back to the watch on every change, IdentityResolvingKey
 * and EncodingKey included; those entries are on no screen, so they are simply never
 * emitted here. Mirrors the desktop's build_write_payload(), which is verified byte-exact
 * against all 134 captured SuuntoLink writes. */
function buildWritePayload(
  device: WriteDevice,
  read: Uint8Array,
  screen: string,
  patch: { entryId: number; occurrence: number; data: Uint8Array },
): Uint8Array | null {
  const split = splitEntries(read);
  const wanted = WRITE_TEMPLATES[device][screen];
  if (!split || !wanted) return null;

  const byId = new Map<number, Uint8Array[]>();
  for (const [id, data] of split.entries) {
    const list = byId.get(id);
    if (list) list.push(data); else byId.set(id, [data]);
  }

  const chunks: Uint8Array[] = [];
  const prefix = new Uint8Array(split.prefix);
  if (prefix.length > 0) prefix[prefix.length - 1] = 0x01;
  chunks.push(prefix, new Uint8Array(MAGIC));

  for (const entryId of wanted) {
    const occurrences = byId.get(entryId) ?? [];
    for (let n = 0; n < occurrences.length; n++) {
      if (entryId === patch.entryId && n === patch.occurrence) {
        chunks.push(encodeEntry(entryId, patch.data));
      } else if (representable(device, entryId, occurrences[n])) {
        chunks.push(encodeEntry(entryId, occurrences[n]));
      }
    }
  }

  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

function findMagic(bytes: Uint8Array): number {
  for (let i = 0; i + MAGIC.length <= bytes.length; i++) {
    let match = true;
    for (let j = 0; j < MAGIC.length; j++) {
      if (bytes[i + j] !== MAGIC[j]) { match = false; break; }
    }
    if (match) return i;
  }
  return -1;
}

/** Real byte offset of `field`'s own entry within this specific reply - walked fresh every
 * time, the same defensive principle settings_write.py's own write_one() uses, so a stale
 * offset from an earlier read (or, worse, another device's schema) can never be reused by
 * accident. Returns the entry's own start plus `field.byteOffset` (0 for every field
 * except a packed multi-value entry like Kailash's HomeLocation - see
 * AmbitSettingsReader.ts's own SettingField comment). Returns null if the entry isn't in
 * this reply at all, or is too short for byteOffset + byteWidth. */
function findFieldOffset(bytes: Uint8Array, field: SettingField): number | null {
  const head = findMagic(bytes);
  if (head < 0) return null;
  let off = head + 8;
  const need = (field.byteOffset ?? 0) + field.byteWidth;
  while (off + 2 <= bytes.length) {
    const id = bytes[off];
    let len = bytes[off + 1];
    off += 2;
    if (len === 0xff) {
      if (off + 4 > bytes.length) break;
      len = (bytes[off] | (bytes[off + 1] << 8) | (bytes[off + 2] << 16) | (bytes[off + 3] << 24)) >>> 0;
      off += 4;
    }
    if (id === field.entryId) {
      return len >= need ? off + (field.byteOffset ?? 0) : null;
    }
    off += len;
  }
  return null;
}

function encodeField(bytes: Uint8Array, offset: number, field: SettingField, value: number): void {
  // A float field carries a fractional raw value (compass declination in radians, value*π/180)
  // - Math.round would flatten it to 0/1. Only integer-wire fields (scaled or not) round.
  const raw = field.scale
    ? (field.float ? value * field.scale : Math.round(value * field.scale))
    : value;
  const view = new DataView(bytes.buffer, bytes.byteOffset + offset, field.byteWidth);
  if (field.float) { view.setFloat32(0, raw, true); return; }
  if (field.byteWidth === 1) { (field.signed ? view.setInt8 : view.setUint8).call(view, 0, raw); return; }
  if (field.byteWidth === 2) { (field.signed ? view.setInt16 : view.setUint16).call(view, 0, raw, true); return; }
  (field.signed ? view.setInt32 : view.setUint32).call(view, 0, raw, true);
}

export interface WriteSettingResult {
  ok: boolean;
  key: string;
  previousValue: number | null;
  requestedValue: number;
  confirmedValue: number | null;
  error?: string;
}

/** Real write: reads the current settings blob, patches one field, writes it back, and
 * re-reads to confirm. `fields` must be the same table (AMBIT3_SETTINGS_FIELDS or
 * KAILASH_SETTINGS_FIELDS) the caller used to decode the row it's writing back - the same
 * per-device table discipline settings_write.py's own settings_table(product_id) enforces
 * on the desktop side. `value` is the enum's own raw integer / the number's own value -
 * never a display label. Rejects only on a genuine transport error (native write
 * throwing, or the watch never answering the read-back at all); a write that goes through
 * but doesn't stick resolves with `ok: false`, matching write_one()'s own contract. */
export async function writeSetting(
  key: string,
  value: number,
  fields: SettingField[],
  device: WriteDevice = 'ambit3',
): Promise<WriteSettingResult> {
  const field = fields.find(f => f.key === key);
  if (!field) {
    return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
      error: `unknown setting ${key}` };
  }
  // Real, hardware-independent range check, same bounds settings_write.py's own
  // write_one() enforces before ever sending a byte - a coordinate typo (e.g. a stray
  // digit making a 500-degree latitude) should never even reach the watch.
  if (key === 'home_latitude' && (value < -90 || value > 90)) {
    return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
      error: `${key}=${value} out of range [-90, 90]` };
  }
  if (key === 'home_longitude' && (value < -180 || value > 180)) {
    return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
      error: `${key}=${value} out of range [-180, 180]` };
  }

  const beforeB64 = await readSettingsRaw();
  const before = base64ToBytes(beforeB64);

  // Birth year (utf8 "YYYY-01-01"): overwrite ONLY the 4 year digits in the existing entry,
  // preserving its length and "-MM-DD" tail. A same-length patch is the safest write there is
  // - no re-encoding of a variable-length text entry, exactly what desktop's write_one() does
  // for this field (SuuntoLink only ever edits the year).
  if (field.kind === 'year') {
    if ((field.min !== undefined && value < field.min) || (field.max !== undefined && value > field.max)) {
      return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
        error: `${key}=${value} out of range [${field.min}, ${field.max}]` };
    }
    const split = splitEntries(before);
    const existing = split?.entries.find(([id]) => id === field.entryId)?.[1];
    if (!existing || existing.length < 4) {
      return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
        error: `entry 0x${field.entryId.toString(16)} (${key}) not in this watch's current settings reply` };
    }
    let prevText = '';
    for (let i = 0; i < existing.length; i++) prevText += String.fromCharCode(existing[i]);
    const prevYear = parseInt((prevText.match(/\d{4}/) || ['0'])[0], 10) || null;
    const yearStr = String(Math.round(value)).padStart(4, '0').slice(0, 4);
    const data = new Uint8Array(existing);
    for (let i = 0; i < 4; i++) data[i] = yearStr.charCodeAt(i);
    const screen = KEY_SCREEN[device][key];
    const payload = screen
      ? buildWritePayload(device, before, screen, { entryId: field.entryId, occurrence: 0, data })
      : null;
    if (!payload) {
      return { ok: false, key, previousValue: prevYear, requestedValue: value, confirmedValue: null,
        error: `${key} has no write template - this setting is not writable from the app` };
    }
    await writeSettingsRaw(bytesToBase64(payload));
    const afterYear = decodeSettings(await readSettingsRaw(), fields).find(s => s.key === key);
    const confYear = afterYear ? afterYear.value : null;
    return { ok: confYear === Math.round(value), key, previousValue: prevYear,
      requestedValue: value, confirmedValue: confYear,
      error: confYear === Math.round(value) ? undefined : `write not confirmed (watch shows ${confYear})` };
  }

  const offset = findFieldOffset(before, field);
  if (offset === null) {
    return { ok: false, key, previousValue: null, requestedValue: value, confirmedValue: null,
      error: `entry 0x${field.entryId.toString(16)} (${key}) not in this watch's current settings reply` };
  }
  const previousView = new DataView(before.buffer, before.byteOffset + offset, field.byteWidth);
  const previousRaw = field.float ? previousView.getFloat32(0, true)
    : field.byteWidth === 1 ? (field.signed ? previousView.getInt8(0) : previousView.getUint8(0))
    : field.byteWidth === 2 ? (field.signed ? previousView.getInt16(0, true) : previousView.getUint16(0, true))
    : (field.signed ? previousView.getInt32(0, true) : previousView.getUint32(0, true));
  const previousValue = field.scale ? previousRaw / field.scale : previousRaw;

  // Build the entry's own new bytes, then send ONLY the screen this field belongs to.
  //
  // This used to copy the whole settings blob, patch it and write it all back - which
  // re-sent the paired phone's BLE bond keys (IdentityResolvingKey / EncodingKey) to the
  // watch on every single settings change. The desktop stopped doing that on 2026-08-10 by
  // adopting SuuntoLink's own per-screen templates; this is the same fix, using the same
  // tables (generated from settings_write.py by tools/gen_android_settings_templates.py, so
  // they cannot drift apart).
  //
  // The Ambit3 family (Ambit3 + Traverse) sends ONLY the screen this field belongs to, so the
  // paired phone's BLE bond keys never ride along. Kailash has no such per-screen template and
  // no bond keys in a cable reply, so it takes the original whole-blob patch: change the one
  // field in place and write the whole settings blob back. (0x1201 single-entry pushes are the
  // BLE path; over cable Kailash uses the same 0x1101 write as everyone else.)
  const screen = device === 'kailash' ? null : KEY_SCREEN[device][key];
  const entryStart = offset - (field.byteOffset ?? 0);
  const entryData = new Uint8Array(before.subarray(entryStart, entryStart + field.byteWidth + (field.byteOffset ?? 0)));
  encodeField(entryData, field.byteOffset ?? 0, field, value);

  let payload: Uint8Array | null = null;
  if (device === 'kailash') {
    const patched = new Uint8Array(before);
    encodeField(patched, offset, field, value);
    payload = patched;
  } else if (screen) {
    payload = buildWritePayload(device, before, screen, {
      entryId: field.entryId,
      occurrence: 0,
      data: entryData,
    });
  }
  if (!payload) {
    // No template for this key. The desktop refuses rather than falling back to the whole
    // blob, because falling back is exactly the behaviour being removed - and a field on no
    // screen (Display.Contrast) is one SuuntoLink itself never writes.
    return { ok: false, key, previousValue, requestedValue: value, confirmedValue: null,
      error: `${key} has no write template - this setting is not writable from the app` };
  }
  await writeSettingsRaw(bytesToBase64(payload));

  const after = decodeSettings(await readSettingsRaw(), fields);
  const confirmed = after.find(s => s.key === key);
  const confirmedValue = confirmed ? confirmed.value : null;

  // Real precision pitfall, found while verifying this exact change: a scaled field
  // (home_latitude/home_longitude) can only round-trip 7 decimal digits - any real user
  // input with more precision than that gets legitimately rounded by the raw int32
  // encoding, so comparing confirmedValue directly against the caller's own unrounded
  // `value` would report `ok: false` on nearly every real write even though it landed
  // exactly as precisely as the format allows. Compare on the same rounded-to-raw
  // representation both sides actually went through instead - mirrors
  // settings_write.py's own write_one(), which compares confirmed_raw against
  // raw_new_value (integers), never the display-scaled floats.
  let ok: boolean;
  if (field.float) {
    // A float field (compass declination) carries a fractional raw value, so rounding it to
    // an int - as the scaled-integer path below does - would collapse every value to 0 and
    // "confirm" every write. Compare in display units with a small tolerance instead (0.05°,
    // well under the 0.1° step).
    ok = confirmedValue !== null && Math.abs(confirmedValue - value) < 0.05;
  } else {
    const requestedRaw = field.scale ? Math.round(value * field.scale) : value;
    const confirmedRaw = confirmedValue !== null
      ? (field.scale ? Math.round(confirmedValue * field.scale) : confirmedValue)
      : null;
    ok = confirmedRaw === requestedRaw;
  }

  return {
    ok,
    key,
    previousValue,
    requestedValue: value,
    confirmedValue,
  };
}
