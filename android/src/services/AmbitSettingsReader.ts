import { base64ToBytes } from './Base64';
import { MODE_OWNED_UNITS } from './AmbitSettingsTemplates';

// Decodes a real sml.DeviceSettings reply (0x1100) - two curated tables, one per real
// schema family, mirroring the companion research project's tools/settings_write.py
// exactly (same fields, same real entry IDs/enum choices, all confirmed live against
// André's own connected watches, 2026-08-08 - see that file's own docstring and
// custom_modes_andre.md's "settings write" sections for the full story, including a real
// bug found there: entry IDs are assigned per schema descriptor and are NOT portable
// across watches - reusing an ID read from one device's schema against another silently
// hits a different field, with no error at all).
//
// Unlike the desktop tool, this hardcodes the real entry IDs directly rather than parsing
// a schema descriptor file at runtime - Android doesn't ship (and has no use for) the
// SuuntoLink `descr+SERIAL+FW` file the desktop side reads. AMBIT3_SETTINGS_FIELDS was
// read live off a real Ambit3 Peak (Ambit3/Traverse/Ambit2 schema family);
// KAILASH_SETTINGS_FIELDS was read live off a real Kailash - its own real, differently-
// numbered, smaller schema (e.g. Display.Invert is entry 0x27 there vs. the Ambit3's
// 0x20), sourced from the real 7R iOS app's own settings screenshots
// (assets/APK/kailash/IMG_2741.png, IMG_2742.png), not the Ambit3's SuuntoLink ones.
//
// Every field here is a plain top-level SBEM entry (id/len/data, no nested GROUP the way
// DeviceHistory's LogHeaders was - see KailashHistoryReader.ts for that shape) - one
// value, one entry, decoded directly.

// 'year' is a utf8 "YYYY-01-01" string on the wire (Personal.BirthDay), shown/edited as a
// plain year - decoded and written specially, not through the numeric path.
export type SettingKind = 'bool' | 'enum' | 'number' | 'coord' | 'year';

// How SuuntoLink presents a field (ported from tools/settings_write.py AMBIT3_DISPLAY):
// radio for 2-3 choices, dropdown for a long list, checkbox for a standalone bool, slider
// for a range. Android only needs to distinguish 'dropdown' (render a real dropdown menu)
// from the rest today, but the full set is carried so the two apps stay in step.
export type SettingControl =
  'radio' | 'dropdown' | 'checkbox' | 'slider' | 'number' | 'declination' | 'coord' | 'readonly' | 'year';
// Which SuuntoLink settings screen the field lives on - drives the section grouping.
export type SettingScreen = 'general' | 'units' | 'personal' | 'other';

export interface SettingChoice {
  value: number;
  label: string;
}

export interface SettingField {
  key: string;
  entryId: number;
  kind: SettingKind;
  // 'bool' -> 1 byte (0/1). 'enum' -> 1 byte, raw integer indexes into `choices`.
  // 'number'/'coord' -> byteWidth bytes, unsigned unless `signed` is set; 'float' for float32.
  // byteWidth 2 (uint16) is used by Personal.Weight (kg*100 on the wire).
  byteWidth: 1 | 2 | 4;
  signed?: boolean;
  float?: boolean;
  choices?: SettingChoice[];
  // Display bounds/step for a numeric field, in the DISPLAY unit (after `scale`) - the same
  // ranges SuuntoLink's own UI enforces (tools/settings_write.py AMBIT3_RANGES), so the app
  // refuses an out-of-range write the way SuuntoLink does. `unit` is shown after the value.
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  // Byte offset *within* the entry's own value - default 0. Needed for entries that pack
  // more than one field (e.g. Kailash's home-location entry: two int32s in one 8-byte
  // entry) - every other field in this file has exactly one value per entry, offset 0.
  byteOffset?: number;
  // Raw integer -> real value divisor (e.g. 1e7 for a lat/lon degrees*1e7 encoding,
  // the same convention this project's POI lat/lon already uses). Default 1 (no scaling).
  scale?: number;
  // Desktop-parity display metadata, ported from tools/settings_write.py AMBIT3_DISPLAY
  // (2026-08-16). That table's own header says it exists "so the desktop and Android UIs
  // render identically off one table instead of drifting apart" - Android had drifted (none
  // of these three were carried), so SettingsScreen was title-casing the raw key for a name
  // and rendering every enum as a chip row. `label` is SuuntoLink's own field name, `control`
  // is how SuuntoLink presents it, `screen` is which settings screen it belongs to.
  label?: string;
  control?: SettingControl;
  screen?: SettingScreen;
  // Shown only while another setting's value differs from `notValue` (the Traverse's "alert
  // before" minutes appear only while that alert is on) - same rule as the desktop page.
  showWhen?: { field: string; notValue: number };
}

// Real, live-confirmed 2026-08-08 against André's own Ambit3 Peak - every value below
// matched the real SuuntoLink "General Settings" screenshots exactly once entry IDs were
// derived correctly (assets/ambit3 pcap/v2/general ambit settings/).
// AMBIT3_SETTINGS_FIELDS / TRAVERSE_SETTINGS_FIELDS are GENERATED per device from each watch's
// own schema descriptor (tools/gen_android_settings_templates.py), because entry ids differ per
// schema family - the Traverse shifts nearly every id off the Ambit3's, so ONE hardcoded table
// decoded a Traverse's Personal fields off the wrong bytes (verified live 2026-08-16). Both apps
// now derive ids from the connected watch's own schema. See AmbitSettingsTemplates.ts.
export { AMBIT3_FIELDS as AMBIT3_SETTINGS_FIELDS, TRAVERSE_FIELDS as TRAVERSE_SETTINGS_FIELDS, VERTICAL_FIELDS as VERTICAL_SETTINGS_FIELDS } from './AmbitSettingsTemplates';

// Kailash's own real, smaller schema (41 entries total vs. the Ambit3's ~324) - every
// field here is exactly what the real 7R iOS app's own settings screen shows (see this
// file's own header comment for the screenshot source), except `display_dark`: not shown
// in the 7R app's own UI at all, but included anyway - it's the one field independently,
// live-hardware-confirmed on this exact watch (André confirmed on the Kailash's own
// screen that it visibly switched Light -> Dark), a stronger bar than everything else in
// this table has individually cleared. Real, live-confirmed 2026-08-08.
export const KAILASH_SETTINGS_FIELDS: SettingField[] = [
  { key: 'date_format', entryId: 0x01, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'DDMM' }, { value: 1, label: 'MMDD' }] },
  { key: 'tones', entryId: 0x02, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'Buttons off' }, { value: 1, label: 'All on' }, { value: 2, label: 'All off' }] },
  { key: 'vibration', entryId: 0x03, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'Buttons off' }, { value: 1, label: 'All on' }, { value: 2, label: 'All off' }] },
  { key: 'units_mode', entryId: 0x07, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'Metric' }, { value: 1, label: 'Imperial' }] },
  { key: 'language', entryId: 0x08, kind: 'enum', byteWidth: 1,
    choices: [
      { value: 0, label: 'Dansk' }, { value: 1, label: 'Deutsch' }, { value: 2, label: 'English' },
      { value: 3, label: 'Espanol' }, { value: 4, label: 'Francais' }, { value: 5, label: 'Italiano' },
      { value: 6, label: 'Nederlands' }, { value: 7, label: 'Norsk' }, { value: 8, label: 'Portugues' },
      { value: 9, label: 'Suomi' }, { value: 10, label: 'Svenska' }, { value: 11, label: 'Chinese' },
      { value: 12, label: 'Japanese' }, { value: 13, label: 'Korean' }, { value: 14, label: 'Cestina' },
      { value: 15, label: 'Polski' }, { value: 16, label: 'Russian' },
    ] },
  { key: 'time_format', entryId: 0x09, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: '24h' }, { value: 1, label: '12h' }] },
  { key: 'display_contrast', entryId: 0x0f, kind: 'number', byteWidth: 1 },
  { key: 'backlight_mode', entryId: 0x10, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'Normal' }, { value: 1, label: 'Off' }, { value: 2, label: 'Night' }, { value: 3, label: 'Toggle' }] },
  { key: 'backlight_brightness', entryId: 0x11, kind: 'number', byteWidth: 1 },
  { key: 'storm_alarm', entryId: 0x17, kind: 'bool', byteWidth: 1 },
  { key: 'display_dark', entryId: 0x27, kind: 'bool', byteWidth: 1 },
  // Real, found 2026-08-08 from two real iOS PacketLogger BLE captures of the 7R app
  // (kailashsethome.pklg / kailashsnotificationsandsethome.pklg), then confirmed
  // byte-exact against this watch's own real schema descriptor: entry 0x36 is a GROUP,
  // sml.DeviceSettings.HomeLocation, packing two little-endian int32 sub-fields
  // (Latitude, Longitude), each with a real <MOD> tag confirming the degrees*1e7
  // encoding this project's own POI format already uses; the capture's values decode to the
  // home location set in the 7R app. Writable (AmbitSettingsWriter.ts's own writeSetting(), range-checked to
  // [-90,90]/[-180,180] before anything is sent, "prove it by re-read" like every other
  // field) - not yet hardware-confirmed, same as every field in this table beyond
  // display_dark, the only one individually checked live on this specific watch so far.
  { key: 'home_latitude', entryId: 0x36, kind: 'coord', byteWidth: 4,
    signed: true, byteOffset: 0, scale: 1e7 },
  { key: 'home_longitude', entryId: 0x36, kind: 'coord', byteWidth: 4,
    signed: true, byteOffset: 4, scale: 1e7 },
  // 2026-11-26 (watch-protocol/10 K7): the rest of the Kailash's own settings list, as on the
  // desktop (tools/settings_write.py KAILASH_SETTINGS). NotificationsOff is what the 7R app
  // pushes; the others are in its descriptor, not yet seen written on the watch. Alarm, dual
  // time and daylight saving left out (André: set on the watch).
  { key: 'notifications_off', entryId: 0x13, kind: 'bool', byteWidth: 1 },
  { key: 'gps_position_format', entryId: 0x04, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'WGS84 d' }, { value: 1, label: 'WGS84 dm' }, { value: 2, label: 'WGS84 dms' }, { value: 3, label: 'UTM' }, { value: 4, label: 'MGRS' }, { value: 5, label: 'British (BNG)' }, { value: 6, label: 'Finnish (ETRS-TM35FIN)' }, { value: 7, label: 'Finnish (KKJ)' }, { value: 8, label: 'Irish (IG)' }, { value: 9, label: 'Swedish (RT90)' }, { value: 10, label: 'Swiss (CH1903)' }, { value: 11, label: 'UTM - NAD27 Alaska' }, { value: 12, label: 'UTM - NAD27 Conus' }, { value: 13, label: 'UTM - NAD83' }, { value: 14, label: 'NZTM2000' }] },
  // Radians on the watch, degrees on screen: the generated KAILASH_FIELDS row and the desktop.
  { key: 'compass_declination', entryId: 0x05, kind: 'number', byteWidth: 4, float: true,
    scale: Math.PI / 180, control: 'declination', unit: '\u00b0', min: -90, max: 90 },
  { key: 'button_lock_time_mode', entryId: 0x06, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'All buttons' }, { value: 1, label: 'Actions only' }] },
  { key: 'gps_time_keeping', entryId: 0x0a, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'On' }, { value: 1, label: 'Off' }] },
  { key: 'vertical_speed_time_unit', entryId: 0x20, kind: 'enum', byteWidth: 1,
    choices: [{ value: 0, label: 'min' }, { value: 1, label: 'hour' }] },
  { key: 'view_exercise_compass', entryId: 0x18, kind: 'bool', byteWidth: 1 },
  { key: 'view_abc_compass', entryId: 0x19, kind: 'bool', byteWidth: 1 },
  { key: 'view_stopwatch', entryId: 0x1a, kind: 'bool', byteWidth: 1 },
  { key: 'view_swimming_drill', entryId: 0x1b, kind: 'bool', byteWidth: 1 },
  { key: 'view_countdown_timer', entryId: 0x1c, kind: 'bool', byteWidth: 1 },
  { key: 'view_activity_tracking', entryId: 0x1d, kind: 'bool', byteWidth: 1 },
  { key: 'view_alti_baro', entryId: 0x1e, kind: 'bool', byteWidth: 1 },
  { key: 'view_step_counter', entryId: 0x1f, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_altitude', entryId: 0x21, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_sunset_sunrise', entryId: 0x22, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_steps', entryId: 0x23, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_battery', entryId: 0x24, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_seconds', entryId: 0x25, kind: 'bool', byteWidth: 1 },
  { key: 'shortcut_baro_trend', entryId: 0x26, kind: 'bool', byteWidth: 1 },
];

const MAGIC = [0x53, 0x42, 0x45, 0x4d, 0x30, 0x31, 0x30, 0x32]; // "SBEM0102"

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

interface SbemEntry { id: number; start: number; end: number }

function sbemEntries(bytes: Uint8Array, start: number): SbemEntry[] {
  const out: SbemEntry[] = [];
  let off = start + 8;
  while (off + 2 <= bytes.length) {
    const id = bytes[off];
    let len = bytes[off + 1];
    off += 2;
    if (len === 0xff) {
      if (off + 4 > bytes.length) break;
      len = (bytes[off] | (bytes[off + 1] << 8) | (bytes[off + 2] << 16) | (bytes[off + 3] << 24)) >>> 0;
      off += 4;
    }
    const end = off + len;
    if (end > bytes.length) break;
    out.push({ id, start: off, end });
    off = end;
  }
  return out;
}

function decodeField(bytes: Uint8Array, entry: SbemEntry, field: SettingField): number {
  // Personal.BirthDay is a utf8 "YYYY-01-01" string, not a number - read the whole entry as
  // ASCII and pull the leading 4-digit year out of it. SuuntoLink only ever edits the year.
  if (field.kind === 'year') {
    let s = '';
    for (let i = entry.start; i < entry.end; i++) s += String.fromCharCode(bytes[i]);
    const m = s.match(/\d{4}/);
    return m ? parseInt(m[0], 10) : 0;
  }
  const off = entry.start + (field.byteOffset ?? 0);
  const view = new DataView(bytes.buffer, bytes.byteOffset + off, field.byteWidth);
  const raw = field.float ? view.getFloat32(0, true)
    : field.byteWidth === 1 ? (field.signed ? view.getInt8(0) : view.getUint8(0))
    : field.byteWidth === 2 ? (field.signed ? view.getInt16(0, true) : view.getUint16(0, true))
    : (field.signed ? view.getInt32(0, true) : view.getUint32(0, true));
  return field.scale ? raw / field.scale : raw;
}

export interface DecodedSetting {
  key: string;
  path: string;
  kind: SettingKind;
  value: number;
  choices?: SettingChoice[];
  // Carried through from the field so the UI can show SuuntoLink's own name, pick the right
  // control (dropdown vs radio), and group by settings screen - see SettingField.
  label?: string;
  control?: SettingControl;
  screen?: SettingScreen;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  showWhen?: { field: string; notValue: number };
  // True for a unit field the watch owns while units_mode is Metric/Imperial: shown but not
  // editable (only "Advanced" frees them), the same rule desktop/settings_write.py enforce.
  locked?: boolean;
}

/** Decodes a base64 sml.DeviceSettings reply (see readSettingsRaw() in
 * native/AmbitUsbModule.ts) against `fields` (AMBIT3_SETTINGS_FIELDS or
 * KAILASH_SETTINGS_FIELDS - the caller must pick the right one for the connected watch,
 * the same discipline settings_write.py's own settings_table(product_id) enforces on the
 * desktop side). Returns every field actually present in this reply - a field missing
 * from a given reply simply yields no row for it, not an error, the same "missing is not
 * broken" rule KailashHistoryReader.ts's own decode already follows. */
export function decodeSettings(b64: string, fields: SettingField[]): DecodedSetting[] {
  if (!b64) return [];
  const bytes = base64ToBytes(b64);
  const head = findMagic(bytes);
  if (head < 0) return [];

  const entries = sbemEntries(bytes, head);
  const byId = new Map<number, SbemEntry>();
  for (const e of entries) byId.set(e.id, e);

  const out: DecodedSetting[] = [];
  for (const field of fields) {
    const entry = byId.get(field.entryId);
    if (!entry || entry.end - entry.start < (field.byteOffset ?? 0) + field.byteWidth) continue;
    out.push({
      key: field.key,
      path: `sml.DeviceSettings.${field.key}`,
      kind: field.kind,
      value: decodeField(bytes, entry, field),
      choices: field.choices,
      label: field.label,
      control: field.control,
      screen: field.screen,
      min: field.min,
      max: field.max,
      step: field.step,
      unit: field.unit,
      showWhen: field.showWhen,
    });
  }
  // The seven unit fields the watch owns while units_mode is Metric(0)/Imperial(1) - shown but
  // not editable then; only "Advanced"(2) frees them. Marked here (post-decode) so the UI gets
  // it from one place, the same rule desktop's read_all() applies.
  const mode = out.find(r => r.key === 'units_mode')?.value;
  if (mode === 0 || mode === 1) {
    for (const r of out) if (MODE_OWNED_UNITS.includes(r.key)) r.locked = true;
  }
  return out;
}
