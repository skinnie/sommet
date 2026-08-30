import { readCustomModesRaw, writeCustomModesRaw } from '../native/AmbitUsbModule';
import { getCustomModesCache, setCustomModesCache } from './CustomModesCache';

// The current CustomModes region — from the in-session cache the Sport Modes screen seeded on
// read, or a fresh read if the cache is cold. Lets an edit skip re-reading the 12KB region it
// already has, cutting each edit from ~2 reads + a write to ~1 read + a write over BLE.
async function currentRegion(): Promise<Uint8Array> {
  const cached = getCustomModesCache();
  if (cached) return new Uint8Array(cached);
  const bytes = base64ToBytes(await readCustomModesRaw());
  setCustomModesCache(bytes);
  return bytes;
}
import { base64ToBytes, bytesToBase64 } from './Base64';
import {
  readTag, fieldTypeName, FIELD_TYPE_VALUES, NAME_FIELD_WIDTH, SETTING_FIELD_OFFSETS,
  DEVICE_CUSTOM, EXERCISE_MODES, EXERCISE_MODES_MODE, EXERCISE_MODES_SETTING_NAME_LEN64,
  EXERCISE_MODES_DISPLAYS, EXERCISE_MODES_DISP_FIELD,
  EXERCISE_MODES_DISP_FIELD_SETTING, SPORT_MODES, SPORT_MODE, SPORT_MODE_SETTING_NAME_LEN64,
} from './CustomModesReader';

// Real, hardware-confirmed mechanism on desktop, 2026-08-08 - NOT yet hardware-confirmed on
// Android specifically (see writeCustomModesRaw()'s own doc comment in
// native/AmbitUsbModule.ts for exactly what is and isn't proven). Ports the companion
// research project's three real write tools (custom_modes_rename_test.py,
// custom_modes_field_write_test.py, custom_modes_display_field_write_test.py) exactly: the
// target byte offset is always found live by walking the just-read region's own real BXml
// tag tree - never hardcoded/assumed from a prior read or a cached decode - and every write
// re-reads afterward to confirm, the same "prove it, don't just trust the ACK" standard
// AmbitSettingsWriter.ts's own writeSetting() already follows.

const ISO8859_15_FROM_UNICODE: Record<number, number> = {
  0x20AC: 0xA4, 0x0160: 0xA6, 0x0161: 0xA8, 0x017D: 0xB4,
  0x017E: 0xB8, 0x0152: 0xBC, 0x0153: 0xBD, 0x0178: 0xBE,
};

function encodeIso885915(s: string): Uint8Array {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    const mapped = ISO8859_15_FROM_UNICODE[code];
    if (mapped !== undefined) {
      out[i] = mapped;
    } else if (code <= 0xFF) {
      out[i] = code;
    } else {
      throw new Error(`"${s[i]}" (U+${code.toString(16)}) has no ISO-8859-15 representation`);
    }
  }
  return out;
}

function decodeNameField(bytes: Uint8Array, offset: number, width: number): string {
  let end = offset + width;
  while (end > offset && bytes[end - 1] === 0) end--;
  // Only used here to compare against a caller-supplied target name - matches
  // decodeIso885915 in CustomModesReader.ts, kept local to avoid a circular import for
  // what's a one-line function.
  let s = '';
  const ISO8859_15_TO_UNICODE: Record<number, number> = {
    0xA4: 0x20AC, 0xA6: 0x0160, 0xA8: 0x0161, 0xB4: 0x017D,
    0xB8: 0x017E, 0xBC: 0x0152, 0xBD: 0x0153, 0xBE: 0x0178,
  };
  for (let i = offset; i < end; i++) s += String.fromCharCode(ISO8859_15_TO_UNICODE[bytes[i]] ?? bytes[i]);
  return s;
}

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function countDiff(a: Uint8Array, b: Uint8Array): number {
  let n = 0;
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) if (a[i] !== b[i]) n++;
  return n;
}

/** Every real content-byte offset where `name`'s own 64-byte NUL-padded field lives in the
 * currently-parsed BXml tree - walked exactly the way custom_modes_rename_test.py's own
 * find_name_offsets() does (DEVICE_CUSTOM -> EXERCISE_MODES -> EXERCISE_MODES_MODE, and
 * DEVICE_CUSTOM -> SPORT_MODES -> SPORT_MODE -> SPORT_MODE_SETTING_NAME_LEN64) - never a
 * blind byte search (that Python tool's own docstring records a real false-positive third
 * match found that way, sitting past the confirmed BXml body). */
function findNameOffsets(bytes: Uint8Array, name: string): number[] {
  const root = readTag(bytes, 0);
  if (!root || root.tagId !== DEVICE_CUSTOM) return [];
  let cursor = 4;
  const end = 4 + root.length;
  const offsets: number[] = [];
  const target = name.trim();

  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;

    if (tag.tagId === EXERCISE_MODES) {
      let subCursor = content;
      const subEnd = content + tag.length;
      while (subCursor < subEnd) {
        const subTag = readTag(bytes, subCursor);
        if (!subTag) break;
        const subContent = subCursor + 4;
        if (subTag.tagId === EXERCISE_MODES_MODE) {
          const nameTag = readTag(bytes, subContent);
          if (nameTag && nameTag.tagId === EXERCISE_MODES_SETTING_NAME_LEN64) {
            const nameOff = subContent + 4;
            if (decodeNameField(bytes, nameOff, NAME_FIELD_WIDTH) === target) offsets.push(nameOff);
          }
        }
        subCursor = subContent + subTag.length;
      }
    } else if (tag.tagId === SPORT_MODES) {
      let subCursor = content;
      const subEnd = content + tag.length;
      while (subCursor < subEnd) {
        const subTag = readTag(bytes, subCursor);
        if (!subTag) break;
        const subContent = subCursor + 4;
        if (subTag.tagId === SPORT_MODE) {
          let slotCursor = subContent;
          const slotEnd = subContent + subTag.length;
          while (slotCursor < slotEnd) {
            const innerTag = readTag(bytes, slotCursor);
            if (!innerTag) break;
            const innerContent = slotCursor + 4;
            if (innerTag.tagId === SPORT_MODE_SETTING_NAME_LEN64) {
              if (decodeNameField(bytes, innerContent, innerTag.length) === target) offsets.push(innerContent);
            }
            slotCursor = innerContent + innerTag.length;
          }
        }
        subCursor = subContent + subTag.length;
      }
    }

    cursor = content + tag.length;
  }
  return offsets;
}

/** Real content offset of `modeName`'s own settings block (the same base
 * CustomModesReader.ts's own decodeSettings() is called with) - found by walking the real
 * tag tree, mirroring custom_modes_field_write_test.py's own find_settings_base(). Returns
 * null if not found. */
function findSettingsBase(bytes: Uint8Array, modeName: string): number | null {
  const root = readTag(bytes, 0);
  if (!root || root.tagId !== DEVICE_CUSTOM) return null;
  let cursor = 4;
  const end = 4 + root.length;

  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES) {
      let subCursor = content;
      const subEnd = content + tag.length;
      while (subCursor < subEnd) {
        const subTag = readTag(bytes, subCursor);
        if (!subTag) break;
        const subContent = subCursor + 4;
        if (subTag.tagId === EXERCISE_MODES_MODE) {
          const nameTag = readTag(bytes, subContent);
          if (nameTag && nameTag.tagId === EXERCISE_MODES_SETTING_NAME_LEN64) {
            const base = subContent + 4;
            if (decodeNameField(bytes, base, NAME_FIELD_WIDTH) === modeName) return base;
          }
        }
        subCursor = subContent + subTag.length;
      }
    }
    cursor = content + tag.length;
  }
  return null;
}

interface DispFieldLocation { offset: number; currentIndex: number; currentType: number }

function findInDisplayFields(bytes: Uint8Array, offset: number, length: number, fieldIndex: number): DispFieldLocation | null {
  const end = offset + length;
  let cursor = offset;
  let idx = 0;
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES_DISP_FIELD) {
      if (idx === fieldIndex) {
        const settingTag = readTag(bytes, content);
        if (settingTag && settingTag.tagId === EXERCISE_MODES_DISP_FIELD_SETTING) {
          const settingContent = content + 4;
          const currentIndex = bytes[settingContent] | (bytes[settingContent + 1] << 8);
          const currentType = bytes[settingContent + 2] | (bytes[settingContent + 3] << 8);
          return { offset: settingContent, currentIndex, currentType };
        }
        return null;
      }
      idx++;
    }
    cursor = content + tag.length;
  }
  return null;
}

function findInDisplays(bytes: Uint8Array, offset: number, length: number, displayIndex: number, fieldIndex: number): DispFieldLocation | null {
  const end = offset + length;
  let cursor = offset;
  let idx = 0;
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (idx === displayIndex) return findInDisplayFields(bytes, content, tag.length, fieldIndex);
    idx++;
    cursor = content + tag.length;
  }
  return null;
}

/** Real content offset of the DISP_FIELD_SETTING leaf at (modeName, displayIndex,
 * fieldIndex) - walked live from the actual tag tree, mirroring
 * custom_modes_display_field_write_test.py's own find_field_setting_offset(). */
function findFieldSettingOffset(bytes: Uint8Array, modeName: string, displayIndex: number, fieldIndex: number): DispFieldLocation | null {
  const root = readTag(bytes, 0);
  if (!root || root.tagId !== DEVICE_CUSTOM) return null;
  let cursor = 4;
  const end = 4 + root.length;

  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId !== EXERCISE_MODES) { cursor = content + tag.length; continue; }

    let subCursor = content;
    const subEnd = content + tag.length;
    while (subCursor < subEnd) {
      const subTag = readTag(bytes, subCursor);
      if (!subTag) break;
      const subContent = subCursor + 4;
      if (subTag.tagId === EXERCISE_MODES_MODE) {
        const nameTag = readTag(bytes, subContent);
        if (nameTag && nameTag.tagId === EXERCISE_MODES_SETTING_NAME_LEN64 &&
            decodeNameField(bytes, subContent + 4, NAME_FIELD_WIDTH) === modeName) {
          let modeCursor = subContent;
          const modeEnd = subContent + subTag.length;
          while (modeCursor < modeEnd) {
            const modeTag = readTag(bytes, modeCursor);
            if (!modeTag) break;
            const modeContent = modeCursor + 4;
            if (modeTag.tagId === EXERCISE_MODES_DISPLAYS) {
              return findInDisplays(bytes, modeContent, modeTag.length, displayIndex, fieldIndex);
            }
            modeCursor = modeContent + modeTag.length;
          }
          return null;
        }
      }
      subCursor = subContent + subTag.length;
    }
    cursor = content + tag.length;
  }
  return null;
}

async function reReadMatches(modified: Uint8Array): Promise<boolean> {
  const after = base64ToBytes(await readCustomModesRaw());
  return bytesEqual(after, modified);
}

export interface RenameModeResult {
  ok: boolean;
  from: string;
  to: string;
  offsets: number[];
  bytesChanged: number;
  error?: string;
}

/** Renames one exercise mode's name everywhere it really appears in the live, currently-
 * parsed BXml tree - both its own EXERCISE_MODES_SETTING_NAME_LEN64 field and the matching
 * SPORT_MODE_SETTING_NAME_LEN64 field of any multisport slot referencing it. Mirrors
 * custom_modes_rename_test.py exactly. */
export async function renameMode(fromName: string, toName: string): Promise<RenameModeResult> {
  const nameBytes = encodeIso885915(toName);
  if (nameBytes.length + 1 > NAME_FIELD_WIDTH) {
    return { ok: false, from: fromName, to: toName, offsets: [], bytesChanged: 0,
      error: `"${toName}" is ${nameBytes.length + 1} bytes encoded, doesn't fit in the ${NAME_FIELD_WIDTH}-byte field.` };
  }

  const before = await currentRegion();
  const offsets = findNameOffsets(before, fromName);
  if (offsets.length === 0) {
    return { ok: false, from: fromName, to: toName, offsets: [], bytesChanged: 0,
      error: `"${fromName}" not found anywhere in the real, currently-parsed BXml tree.` };
  }

  const padded = new Uint8Array(NAME_FIELD_WIDTH);
  padded.set(nameBytes);
  const modified = new Uint8Array(before);
  for (const off of offsets) modified.set(padded, off);
  const bytesChanged = countDiff(before, modified);

  await writeCustomModesRaw(bytesToBase64(modified));
  setCustomModesCache(modified);   // keep the cache current so the next edit doesn't re-read
  const matches = await reReadMatches(modified);

  return { ok: matches, from: fromName, to: toName, offsets, bytesChanged,
    error: matches ? undefined : 'Write sent but re-read does not match the intended edit.' };
}

export interface WriteFieldResult {
  ok: boolean;
  mode: string;
  fields: Record<string, { previous: number; requested: number; offset: number }>;
  bytesChanged: number;
  error?: string;
}

/** Writes one or more of an exercise mode's fixed uint16 settings fields (Autolap, HrHigh,
 * HrLow, HrLimitsUse, UseHw, ...). Mirrors custom_modes_field_write_test.py exactly - each
 * field's byte offset is computed from SETTING_FIELD_OFFSETS' own real declared order, not
 * a hardcoded magic number. */
export async function writeField(modeName: string, fields: Record<string, number>): Promise<WriteFieldResult> {
  for (const key of Object.keys(fields)) {
    if (!(key in SETTING_FIELD_OFFSETS)) {
      return { ok: false, mode: modeName, fields: {}, bytesChanged: 0, error: `unknown field "${key}"` };
    }
  }
  if (Object.keys(fields).length === 0) {
    return { ok: false, mode: modeName, fields: {}, bytesChanged: 0, error: 'no fields given' };
  }

  const before = await currentRegion();
  const base = findSettingsBase(before, modeName);
  if (base === null) {
    return { ok: false, mode: modeName, fields: {}, bytesChanged: 0,
      error: `"${modeName}" not found as a real exercise mode in this reply.` };
  }

  const modified = new Uint8Array(before);
  const changed: WriteFieldResult['fields'] = {};
  for (const [key, value] of Object.entries(fields)) {
    const off = base + SETTING_FIELD_OFFSETS[key];
    const previous = before[off] | (before[off + 1] << 8);
    modified[off] = value & 0xFF;
    modified[off + 1] = (value >> 8) & 0xFF;
    changed[key] = { previous, requested: value, offset: off };
  }
  const bytesChanged = countDiff(before, modified);

  await writeCustomModesRaw(bytesToBase64(modified));
  setCustomModesCache(modified);   // keep the cache current so the next edit doesn't re-read
  const matches = await reReadMatches(modified);

  return { ok: matches, mode: modeName, fields: changed, bytesChanged,
    error: matches ? undefined : 'Write sent but re-read does not match the intended edit.' };
}

export interface WriteDisplayFieldResult {
  ok: boolean;
  mode: string;
  display: number;
  field: number;
  previousIndex: string;
  previousType: string;
  index: string;
  type: string;
  error?: string;
}

function resolveFieldType(raw: string): number | null {
  if (FIELD_TYPE_VALUES.has(raw)) return FIELD_TYPE_VALUES.get(raw)!;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** Changes which data a display row shows - the Index and/or Type half of one
 * EXERCISE_MODES_DISP_FIELD_SETTING leaf. Mirrors custom_modes_display_field_write_test.py
 * exactly, including its real, live-confirmed 2026-08-08 finding: for the common
 * Index=FT_TIME "numeric value slot" case, Type (not Index) is what actually selects the
 * rendered content - see that file's own docstring and custom_modes_andre.md. Give
 * newIndexName and/or newTypeName; the field the caller omits is left unchanged. */
export async function writeDisplayField(
  modeName: string, displayIndex: number, fieldIndex: number,
  newIndexName?: string, newTypeName?: string,
): Promise<WriteDisplayFieldResult> {
  const blank = { previousIndex: '', previousType: '', index: '', type: '' };
  if (newIndexName === undefined && newTypeName === undefined) {
    return { ok: false, mode: modeName, display: displayIndex, field: fieldIndex, ...blank,
      error: 'give an index and/or type - nothing to change otherwise.' };
  }
  const newIndex = newIndexName !== undefined ? resolveFieldType(newIndexName) : null;
  const newType = newTypeName !== undefined ? resolveFieldType(newTypeName) : null;
  if ((newIndexName !== undefined && newIndex === null) || (newTypeName !== undefined && newType === null)) {
    return { ok: false, mode: modeName, display: displayIndex, field: fieldIndex, ...blank,
      error: 'not a known field type name and not a valid integer.' };
  }

  const before = await currentRegion();
  const located = findFieldSettingOffset(before, modeName, displayIndex, fieldIndex);
  if (!located) {
    return { ok: false, mode: modeName, display: displayIndex, field: fieldIndex, ...blank,
      error: `"${modeName}" display ${displayIndex} field ${fieldIndex} not found in the real parsed tree.` };
  }
  const { offset, currentIndex, currentType } = located;
  const finalIndex = newIndex !== null ? newIndex : currentIndex;
  const finalType = newType !== null ? newType : currentType;

  const modified = new Uint8Array(before);
  modified[offset] = finalIndex & 0xFF;
  modified[offset + 1] = (finalIndex >> 8) & 0xFF;
  modified[offset + 2] = finalType & 0xFF;
  modified[offset + 3] = (finalType >> 8) & 0xFF;

  await writeCustomModesRaw(bytesToBase64(modified));
  setCustomModesCache(modified);   // keep the cache current so the next edit doesn't re-read
  const matches = await reReadMatches(modified);

  return {
    ok: matches, mode: modeName, display: displayIndex, field: fieldIndex,
    previousIndex: fieldTypeName(currentIndex), previousType: fieldTypeName(currentType),
    index: fieldTypeName(finalIndex), type: fieldTypeName(finalType),
    error: matches ? undefined : 'Write sent but re-read does not match the intended edit.',
  };
}
