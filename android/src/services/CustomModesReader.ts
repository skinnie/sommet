// Decodes the Ambit3 CustomModes flash region (sport modes) - a third, distinct on-device
// encoding besides fixed-stride binary records (Routes/Waypoints/GpsSGEE) and SBEM0102
// (DeviceSettings/POIs/ActivityTracking): Suunto's own "BXml" binary-encoded tree format.
// Every tag is a 4-byte header - [u16 LE tag_id][u16 LE length] - followed by that many
// content bytes, which may themselves be further nested tags.
//
// This is a direct TS port of the companion research project's tools/custom_modes.py,
// reverse-engineered 2026-08-05 (see custom_modes_andre.md for the full derivation). Tag
// IDs, FIELD_TYPES and SETTING_FIELDS below are copied verbatim from that file - not
// re-derived - to avoid any risk of silent divergence between the two decoders.

export const DEVICE_CUSTOM = 0x003;
export const EXERCISE_MODES = 0x100;
export const EXERCISE_MODES_MODE = 0x101;
export const EXERCISE_MODES_SETTING_NAME_LEN64 = 0x103;
export const EXERCISE_MODES_DISPLAYS = 0x105;
export const EXERCISE_MODES_DISPLAY = 0x106;
export const EXERCISE_MODES_DISP_SETTING = 0x107;
export const EXERCISE_MODES_DISP_FIELD = 0x108;
export const EXERCISE_MODES_DISP_FIELD_SETTING = 0x109;
export const EXERCISE_MODES_DISP_FIELD_SHORTCUT = 0x10A;
export const EXERCISE_MODES_TYPE = 0x10B;
export const EXERCISE_MODES_RULES = 0x10C;
export const EXERCISE_MODES_RULE = 0x10D;
export const SPORT_MODES = 0x200;
export const SPORT_MODE = 0x210;
export const SPORT_MODE_ACTIVITY_ID = 0x213;
export const SPORT_MODE_EXERCISE = 0x214;
export const SPORT_MODE_SETTING_NAME_LEN64 = 0x215;

// BXmlIdMapping - the leaf field-type / screen-template dictionary, reproduced verbatim
// from custom_modes.py's own FIELD_TYPES (95 entries, generated programmatically from that
// file rather than hand-copied, to rule out transcription errors).
export const FIELD_TYPES: { value: number; name: string }[] = [
  { value: 0, name: "FT_SHORTCUT" }, { value: 1, name: "FT_TIME" }, { value: 2, name: "FT_TIME_SEC" },
  { value: 3, name: "FT_DATE" }, { value: 4, name: "FT_WEEKDAY" }, { value: 5, name: "FT_TIMER" },
  { value: 6, name: "FT_ALTI" }, { value: 7, name: "FT_BARO" }, { value: 8, name: "FT_COMPASS" },
  { value: 9, name: "FT_HEART_RATE_AVG" }, { value: 10, name: "FT_DISTANCE" }, { value: 11, name: "FT_VELOCITY" },
  { value: 12, name: "FT_AVGSPEED" }, { value: 13, name: "FT_TEMPERATURE" }, { value: 14, name: "FT_BARO_GRAPH" },
  { value: 15, name: "FT_ALTI_GRAPH" }, { value: 16, name: "FT_COMPASSCARDINAL" }, { value: 17, name: "FT_TOTAL_CALORIES" },
  { value: 18, name: "FT_REF_ALTI" }, { value: 19, name: "FT_ENERGY_SYMBOL" }, { value: 20, name: "FT_HEART_RATE_PEAK" },
  { value: 21, name: "FT_HEART_RATE_CURR" }, { value: 22, name: "FT_HEART_RATE_GRAPH" }, { value: 23, name: "FT_CADENCE" },
  { value: 24, name: "FT_NAVI_ARROW" }, { value: 25, name: "FT_NAVI_DISTANCE" }, { value: 26, name: "FT_NAVI_DIRECTION" },
  { value: 27, name: "FT_DUAL_TIME" }, { value: 28, name: "FT_PACE" }, { value: 29, name: "FT_AVG_PACE" },
  { value: 30, name: "FT_RECOVERY_TIME" }, { value: 31, name: "FT_TE" }, { value: 32, name: "FT_EXERCISE_ALTI_GRAPH" },
  { value: 51, name: "FT_RULE_ENGINE_0" }, { value: 52, name: "FT_RULE_ENGINE_1" }, { value: 53, name: "FT_RULE_ENGINE_2" },
  { value: 54, name: "FT_STOPWATCH_ABC" }, { value: 55, name: "FT_SW_ABC_LAP_TIME" }, { value: 56, name: "FT_SW_ABC_LAP" },
  { value: 57, name: "FT_SW_TIME" }, { value: 58, name: "FT_DEVELOPER_TITLE" }, { value: 59, name: "FT_TEXT_ADJUST" },
  { value: 60, name: "FT_CALIBRATED_DISTANCE" }, { value: 61, name: "FT_INTERVAL_VALUE" }, { value: 62, name: "FT_INTERVAL_TITLE" },
  { value: 63, name: "FT_SPLIT_LAP_DISTANCE" }, { value: 64, name: "FT_SWIM_STYLE_TITLE" }, { value: 66, name: "FT_DRILL_TITLE" },
  { value: 68, name: "FT_BATTERY_CHARGE" }, { value: 70, name: "FT_SPORT_LAP_STOPWATCH" }, { value: 72, name: "FT_SPORT_LAP_AVGSPEED" },
  { value: 74, name: "FT_BIKE_POWER_AVG" }, { value: 76, name: "FT_BIKE_POWER_10S" }, { value: 78, name: "FT_BIKE_POWER_LAP" },
  { value: 80, name: "FT_BIKE_POWER_LAP_MAX" }, { value: 82, name: "FT_SWIM_STYLE" }, { value: 84, name: "FT_SWIM_STROKES" },
  { value: 86, name: "FT_SWIM_PACE" }, { value: 88, name: "FT_SWIM_AVG_PACE" }, { value: 90, name: "FT_SWIM_LAP_DISTANCE" },
  { value: 92, name: "FT_SWIM_LAP_RATE" }, { value: 94, name: "FT_SWIM_LAP_SWOLF" }, { value: 96, name: "FT_SWIM_POOL_STROKES" },
  { value: 98, name: "FT_SWIM_POOL_PACE" }, { value: 100, name: "FT_SWIM_INT_TIME" }, { value: 102, name: "FT_SWIM_INT_STROKES" },
  { value: 104, name: "FT_SWIM_INT_PACE" }, { value: 106, name: "FT_SWIM_REST_TIME" }, { value: 256, name: "PID_RUNNER_GPS_TEMPLATE_1" },
  { value: 257, name: "PID_RUNNER_GPS_TEMPLATE_1G_GRAPH" }, { value: 258, name: "PID_RUNNER_GPS_TEMPLATE_2" }, { value: 259, name: "PID_RUNNER_GPS_TEMPLATE_3" },
  { value: 260, name: "PID_RUNNER_GPS_TEMPLATE_4" }, { value: 261, name: "PID_RUNNER_GPS_TEMPLATE_5" }, { value: 262, name: "PID_RUNNER_GPS_TEMPLATE_6" },
  { value: 263, name: "PID_RUNNER_GPS_TEMPLATE_7_WAYPOINT" }, { value: 264, name: "PID_RUNNER_GPS_TEMPLATE_8_LOCATION" }, { value: 265, name: "PID_RUNNER_GPS_TEMPLATE_9_WAYPOINT_NAME" },
  { value: 272, name: "PID_RUNNER_GPS_TEMPLATE_10_INPUT" }, { value: 273, name: "PID_RUNNER_GPS_TEMPLATE_11_COMPASS_NAVIGATE" }, { value: 274, name: "PID_RUNNER_GPS_TEMPLATE_12_SETTINGS" },
  { value: 275, name: "PID_RUNNER_GPS_TEMPLATE_13_TWOWAY_CHOICE" }, { value: 276, name: "PID_RUNNER_GPS_TEMPLATE_14_ONEWAY_CHOICE" }, { value: 277, name: "PID_RUNNER_GPS_TEMPLATE_15_ADJUST" },
  { value: 278, name: "PID_RUNNER_GPS_TEMPLATE_16_LOADING" }, { value: 279, name: "PID_RUNNER_GPS_TEMPLATE_17_POPUP" }, { value: 280, name: "PID_RUNNER_GPS_TEMPLATE_18_MEMORY" },
  { value: 281, name: "PID_RUNNER_GPS_TEMPLATE_19_MODE_TITLE" }, { value: 288, name: "PID_RUNNER_GPS_TEMPLATE_20_CALIBRATION" }, { value: 289, name: "PID_RUNNER_GPS_TEMPLATE_21_UTMMGRS_LOCATION" },
  { value: 290, name: "PID_RUNNER_GPS_TEMPLATE_22_NAVIGATION" }, { value: 291, name: "PID_RUNNER_GPS_TEMPLATE_11_MILS_COMPASS_NAVIGATE" }, { value: 336, name: "PID_RUNNER_GPS_TEMPLATE_50_MAP_DRAW" },
  { value: 512, name: "PID_RUNNER_GPS_TEMPLATE_100_DEBUG" }, { value: 65534, name: "MT_NONE" },
  // Synced with tools/custom_modes.py FIELD_TYPES (23 entries Android lacked, incl. 0x0047/
  // 0x0049), 2026-08-17 - shortcut fields were showing raw hex.
  { value: 0x0021, name: "FT_ASCENT" },
  { value: 0x0022, name: "FT_DESCENT" },
  { value: 0x002C, name: "FT_VERTICAL_SPEED" },
  { value: 0x002D, name: "FT_LAP_NUMBER" },
  { value: 0x002E, name: "FT_LAP_TIME" },
  { value: 0x002F, name: "FT_LAP_DISTANCE" },
  { value: 0x0030, name: "FT_LAP_AVG_PACE" },
  { value: 0x0043, name: "FT_BIKE_POWER" },
  { value: 0x0047, name: "FT_SPORT_LAP_DISTANCE" },
  { value: 0x0049, name: "FT_SPORT_LAP_AVG_PACE" },
  { value: 0x004B, name: "FT_BIKE_POWER_3S" },
  { value: 0x004D, name: "FT_BIKE_POWER_30S" },
  { value: 0x0053, name: "FT_SWIM_TOTAL_DISTANCE" },
  { value: 0x0057, name: "FT_SWIM_AVG_STROKE_RATE" },
  { value: 0x0059, name: "FT_SWIM_AVG_SWOLF" },
  { value: 0x0067, name: "FT_SWIM_INT_STROKE_RATE" },
  { value: 0x0069, name: "FT_SWIM_INT_SWOLF" },
  { value: 0x006B, name: "FT_RULE_ENGINE_3" },
  { value: 0x006C, name: "FT_RULE_ENGINE_4" },
  { value: 0x006E, name: "FT_RULE_ENGINE_1_GRAPH" },
  { value: 0x0070, name: "FT_RULE_ENGINE_3_GRAPH" },
  { value: 0x00AD, name: "FT_RUNNING_PERFORMANCE" },
  { value: 0x00B0, name: "FT_RUNNING_PERFORMANCE_GRAPH" },
];

export const FIELD_TYPE_NAMES: Map<number, string> = new Map(FIELD_TYPES.map(f => [f.value, f.name]));
export const FIELD_TYPE_VALUES: Map<string, number> = new Map(FIELD_TYPES.map(f => [f.name, f.value]));

export function fieldTypeName(value: number): string {
  // Real bug, found 2026-08-09 while verifying numberDisplays() against a real capture:
  // custom_modes.py's own fallback is `f"0x{tpl:04x}"` (zero-padded to 4 hex digits, e.g.
  // "0x0127") - this was unpadded ("0x127"), which is why GPS_SYSTEM_TAIL/NON_GPS_SYSTEM_TAIL
  // (both end in the literal string "0x0127") never matched and isBuiltIn was always false.
  // Also a real, pre-existing display bug on its own: any screen using an unconfirmed
  // template showed "0x127" instead of desktop's "0x0127".
  return FIELD_TYPE_NAMES.get(value) ?? `0x${value.toString(16).padStart(4, '0')}`;
}

// Real, 2026-08-09 ("on custom sports it shows the variable names, can't we have the normal
// names?") - direct port of tools/custom_modes.py's own FIELD_TYPE_LABELS, curated by hand
// for every real FT_* entry (not a mechanical guess). Two are hardware-confirmed real names
// from this project's own live testing: FT_VELOCITY really does show as "Speed" and
// FT_HEART_RATE_CURR really does show as "Heart Rate" (custom_modes_andre.md). Same real
// fix desktop's DataPickerDialog.qml/SportModesPage.qml already got - Android's own
// SportModesScreen.tsx was still showing raw enum names until now.
export const FIELD_TYPE_LABELS: Record<string, string> = {
  // Synced verbatim with tools/custom_modes.py FIELD_TYPE_LABELS (2026-08-17): the
  // SuuntoLink-accurate human labels; Android had 26 stale/guessed variants.

  FT_SHORTCUT: 'Shortcut',
  FT_TIME: 'Day time',
  FT_TIME_SEC: 'Time (with seconds)',
  FT_DATE: 'Date',
  FT_WEEKDAY: 'Weekday',
  FT_TIMER: 'Chrono',
  FT_ALTI: 'Altitude',
  FT_BARO: 'Sea level air pressure',
  FT_COMPASS: 'Compass Heading',
  FT_HEART_RATE_AVG: 'Avg heart rate',
  FT_DISTANCE: 'Distance',
  FT_VELOCITY: 'Speed',
  FT_AVGSPEED: 'Average speed',
  FT_TEMPERATURE: 'Temperature',
  FT_BARO_GRAPH: 'Barograph',
  FT_ALTI_GRAPH: 'Altitude Graph',
  FT_COMPASSCARDINAL: 'Compass Direction',
  FT_TOTAL_CALORIES: 'Calories',
  FT_REF_ALTI: 'Reference Altitude',
  FT_ENERGY_SYMBOL: 'Energy Level',
  FT_HEART_RATE_PEAK: 'Heart Rate (peak)',
  FT_HEART_RATE_CURR: 'Heart rate',
  FT_HEART_RATE_GRAPH: 'Heart Rate Graph',
  FT_CADENCE: 'Cadence',
  FT_NAVI_ARROW: 'Navigation Arrow',
  FT_NAVI_DISTANCE: 'Navigation Distance',
  FT_NAVI_DIRECTION: 'Navigation Direction',
  FT_DUAL_TIME: 'Dual time',
  FT_PACE: 'Pace',
  FT_AVG_PACE: 'Average pace',
  FT_RECOVERY_TIME: 'Recovery Time',
  FT_TE: 'Peak Training Effect',
  FT_EXERCISE_ALTI_GRAPH: 'Altitude Graph (exercise)',
  FT_RULE_ENGINE_0: 'Suunto App Slot 1',
  FT_RULE_ENGINE_1: 'Suunto App Slot 2',
  FT_RULE_ENGINE_2: 'Suunto App Slot 3',
  FT_VERTICAL_SPEED: 'Vertical speed',
  FT_ASCENT: 'Ascent',
  FT_DESCENT: 'Descent',
  FT_LAP_NUMBER: 'Lap number',
  FT_LAP_TIME: 'Lap time',
  FT_LAP_DISTANCE: 'Lap distance',
  FT_LAP_AVG_PACE: 'Lap avg pace',
  FT_BIKE_POWER: 'Power',
  FT_BIKE_POWER_3S: 'Power 3s',
  FT_BIKE_POWER_30S: 'Power 30s',
  FT_SPORT_LAP_AVG_PACE: 'Current activity avg pace',
  FT_SWIM_TOTAL_DISTANCE: 'Distance',
  FT_RUNNING_PERFORMANCE: 'Running performance',
  FT_RUNNING_PERFORMANCE_GRAPH: 'Running performance (graph)',
  FT_RULE_ENGINE_3: 'Suunto App Slot 4',
  FT_RULE_ENGINE_4: 'Suunto App Slot 5',
  FT_RULE_ENGINE_1_GRAPH: 'Suunto App Slot 2 (graph)',
  FT_RULE_ENGINE_3_GRAPH: 'Suunto App Slot 4 (graph)',
  FT_STOPWATCH_ABC: 'Stopwatch',
  FT_SW_ABC_LAP_TIME: 'Stopwatch Lap Time',
  FT_SW_ABC_LAP: 'Stopwatch Lap',
  FT_SW_TIME: 'Stopwatch Time',
  FT_DEVELOPER_TITLE: 'Developer Title',
  FT_TEXT_ADJUST: 'Text Adjust',
  FT_CALIBRATED_DISTANCE: 'Distance (calibrated)',
  FT_INTERVAL_VALUE: 'Interval Value',
  FT_INTERVAL_TITLE: 'Interval Title',
  FT_SPLIT_LAP_DISTANCE: 'Split/Lap Distance',
  FT_SWIM_STYLE_TITLE: 'Swim Style Title',
  FT_DRILL_TITLE: 'Drill Title',
  FT_BATTERY_CHARGE: 'Battery charge',
  FT_SPORT_LAP_STOPWATCH: 'Current activity duration',
  FT_SPORT_LAP_AVGSPEED: 'Current activity avg speed',
  FT_BIKE_POWER_AVG: 'Average power',
  FT_BIKE_POWER_10S: 'Power 10s',
  FT_BIKE_POWER_LAP: 'Lap power',
  FT_BIKE_POWER_LAP_MAX: 'Lap maximum power',
  FT_SWIM_AVG_SWOLF: 'Average SWOLF',
  FT_SWIM_INT_STROKE_RATE: 'Interval stroke rate',
  FT_SWIM_INT_SWOLF: 'Interval avg SWOLF',
  FT_SWIM_STYLE: 'Previous pool length style',
  FT_SWIM_STROKES: 'Swim Strokes',
  FT_SWIM_PACE: 'Swim Pace',
  FT_SWIM_AVG_PACE: 'Average swim pace',
  FT_SWIM_AVG_STROKE_RATE: 'Average stroke rate',
  FT_SPORT_LAP_DISTANCE: 'Current activity distance',
  FT_SWIM_LAP_DISTANCE: 'Lap distance',
  FT_SWIM_LAP_RATE: 'Lap stroke rate',
  FT_SWIM_LAP_SWOLF: 'Lap avg SWOLF',
  FT_SWIM_POOL_STROKES: 'Pool Swim Strokes',
  FT_SWIM_POOL_PACE: 'Pool Swim Pace',
  FT_SWIM_INT_TIME: 'Interval duration',
  FT_SWIM_INT_STROKES: 'Interval stroke count',
  FT_SWIM_INT_PACE: 'Interval pace',
  FT_SWIM_REST_TIME: 'Rest time',
};

const PID_PREFIX = 'PID_RUNNER_GPS_TEMPLATE_';

/** Human-readable label for a real field-type name - the curated dict above, or a
 * real-but-honest fallback for the PID_RUNNER_GPS_TEMPLATE_* screen-template entries this
 * project has never confirmed the visual meaning of: strips the common prefix and reformats
 * the enum's own real suffix, without inventing new meaning. Direct port of
 * custom_modes.py's own field_type_label(). */
// Friendly name for a built-in (non-editable) system screen, from its template. The desktop
// renders these as icons (Map/Compass...); Android shows a text message, so map the template to
// the same short name instead of the raw PID_RUNNER_GPS_TEMPLATE_* code. André, 2026-08-17.
export function builtInScreenName(templateName: string): string {
  if (templateName.indexOf('MAP_DRAW') >= 0) return 'Map';
  if (templateName.indexOf('COMPASS_NAVIGATE') >= 0)
    return templateName.indexOf('MILS') >= 0 ? 'Compass / Navigate (mils)' : 'Compass / Navigate';
  if (templateName.indexOf('NAVIGATION') >= 0) return 'Navigation';
  if (templateName.indexOf('GRAPH') >= 0) return 'Graph';
  return '';
}

export function fieldTypeLabel(name: string): string {
  if (name in FIELD_TYPE_LABELS) return FIELD_TYPE_LABELS[name];
  if (name.startsWith(PID_PREFIX)) {
    const rest = name.slice(PID_PREFIX.length);
    const sepIndex = rest.indexOf('_');
    const num = sepIndex === -1 ? rest : rest.slice(0, sepIndex);
    const words = sepIndex === -1 ? '' : rest.slice(sepIndex + 1);
    const pretty = words
      .split('_')
      .filter(Boolean)
      .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
      .join(' ');
    return `Screen ${num}` + (pretty ? ` - ${pretty}` : '');
  }
  return name;
}

// EXERCISE_MODES_SETTING_NAME_LEN64's content: a 64-byte Name field, then this fixed
// sequence of uint16 fields (all "<H" in custom_modes.py's own SETTING_FIELDS) - copied
// verbatim, same order, same names, so a UI built against this can round-trip against the
// desktop's own write tools without a second name-mapping layer.
export const SETTING_FIELD_NAMES = [
  'ActivityID', 'CustomModeIdLow', 'CustomModeIdHigh', 'UseHw', 'AltiBaroMode',
  'GpsPowerMode', 'RecordingInterval', 'Autolap', 'HrHigh', 'HrLow', 'HrLimitsUse',
  'AutoStart', 'AutoPause', 'AutoScrolling', 'IntTimerFlags', 'IntTimerCount',
] as const;
export type SettingFieldName = typeof SETTING_FIELD_NAMES[number];

export const NAME_FIELD_WIDTH = 64;

/** {fieldName: byteOffset within the settings block} - offset 0 is the start of the
 * 64-byte Name field itself. Every field here is a uint16 (2 bytes), so this is simply
 * 64 + 2*index - computed from SETTING_FIELD_NAMES' own declared order, not hardcoded,
 * matching custom_modes_field_write_test.py's own field_offsets(). */
export const SETTING_FIELD_OFFSETS: Record<string, number> = Object.fromEntries(
  SETTING_FIELD_NAMES.map((name, i) => [name, NAME_FIELD_WIDTH + i * 2]),
);

interface Tag { tagId: number; length: number }

/** A single BXml tag header: [u16 LE tag_id][u16 LE length], content follows. */
export function readTag(bytes: Uint8Array, offset: number): Tag | null {
  if (offset + 4 > bytes.length) return null;
  const tagId = bytes[offset] | (bytes[offset + 1] << 8);
  const length = bytes[offset + 2] | (bytes[offset + 3] << 8);
  return { tagId, length };
}

// ISO-8859-15 (not plain ISO-8859-1 / Latin-1): CustomModes names are encoded this way on
// the watch. Differs from Latin-1 at exactly 8 code points (the euro sign and a handful of
// accented letters) - everything else is a direct byte<->code-point mapping.
const ISO8859_15_TO_UNICODE: Record<number, number> = {
  0xA4: 0x20AC, 0xA6: 0x0160, 0xA8: 0x0161, 0xB4: 0x017D,
  0xB8: 0x017E, 0xBC: 0x0152, 0xBD: 0x0153, 0xBE: 0x0178,
};

export function decodeIso885915(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(ISO8859_15_TO_UNICODE[b] ?? b);
  return s;
}

function decodeNameField(bytes: Uint8Array, offset: number, width: number): string {
  let end = offset + width;
  while (end > offset && bytes[end - 1] === 0) end--;
  return decodeIso885915(bytes.subarray(offset, end));
}

export interface ExerciseModeSettings {
  name: string;
  activityId: number;
  useHw: number;
  altiBaroMode: number;
  recordingInterval: number;
  autolap: number;
  hrHigh: number;
  hrLow: number;
  hrLimitsUse: number;
}

function decodeSettings(bytes: Uint8Array, offset: number): ExerciseModeSettings {
  const name = decodeNameField(bytes, offset, NAME_FIELD_WIDTH);
  const at = (field: string) => offset + SETTING_FIELD_OFFSETS[field];
  const u16 = (o: number) => bytes[o] | (bytes[o + 1] << 8);
  return {
    name,
    activityId: u16(at('ActivityID')),
    useHw: u16(at('UseHw')),
    altiBaroMode: u16(at('AltiBaroMode')),
    recordingInterval: u16(at('RecordingInterval')),
    autolap: u16(at('Autolap')),
    hrHigh: u16(at('HrHigh')),
    hrLow: u16(at('HrLow')),
    hrLimitsUse: u16(at('HrLimitsUse')),
  };
}

export interface DisplayField {
  index: number;
  indexName: string;
  type: number;
  typeName: string;
  // The DISP_FIELD_SHORTCUT leaves (u16 each). A data field (or an installed Suunto App) is
  // encoded as a shortcut, NOT as the field Type - so a field showing "Avg pace" has Type 0
  // and a shortcut for it. Ported from custom_modes.py's decode, 2026-08-17.
  shortcuts: number[];
  // What the row actually shows (fieldDisplayLabel = custom_modes.py's row_values()): the
  // shortcut's data field when Type is 0 with a shortcut, "Empty" when Type 0 and none, else
  // the Type's label. Was showing the raw Type label ("Shortcut") for shortcut fields.
  typeLabel: string;
}

// custom_modes.py row_values(): a shortcut field (Type 0) reads as its shortcut's data field,
// or "Empty" if it has none; any other Type reads as that Type's label.
function fieldDisplayLabel(type: number, shortcuts: number[]): string {
  if (type === 0 && shortcuts.length > 0) {
    return shortcuts.map(t => fieldTypeLabel(fieldTypeName(t))).join(', ');
  }
  if (type === 0) return 'Empty';
  return fieldTypeLabel(fieldTypeName(type));
}

function decodeDispField(bytes: Uint8Array, offset: number, length: number): DisplayField {
  const end = offset + length;
  let cursor = offset;
  const field: DisplayField = {
    index: 0, indexName: fieldTypeName(0), type: 0, typeName: fieldTypeName(0),
    shortcuts: [], typeLabel: 'Empty',
  };
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES_DISP_FIELD_SETTING) {
      const idx = bytes[content] | (bytes[content + 1] << 8);
      const typ = bytes[content + 2] | (bytes[content + 3] << 8);
      field.index = idx;
      field.indexName = fieldTypeName(idx);
      field.type = typ;
      field.typeName = fieldTypeName(typ);
    } else if (tag.tagId === EXERCISE_MODES_DISP_FIELD_SHORTCUT) {
      field.shortcuts.push(bytes[content] | (bytes[content + 1] << 8));
    }
    cursor = content + tag.length;
  }
  field.typeLabel = fieldDisplayLabel(field.type, field.shortcuts);
  return field;
}

export interface Display {
  template: number;
  templateName: string;
  // Real, 2026-08-09 - human-readable label (fieldTypeLabel(templateName)), same real fix
  // desktop's SportModesPage.qml already has.
  templateLabel: string;
  // The display's own Type, the second u16 of its DISP_SETTING ([u16 Template][u16 Type],
  // custom_modes.py decode_display). 10 = a screen the user set up; anything else is one of
  // the watch's built-in views.
  type: number;
  fields: DisplayField[];
  // Filled in by numberDisplays() after all of a mode's displays are collected - a single
  // display can't know its own screenNumber/isBuiltIn in isolation (the classification looks
  // at the *tail* of the whole list).
  screenNumber: number | null;
  isBuiltIn: boolean;
}

function decodeDisplay(bytes: Uint8Array, offset: number, length: number): Display {
  const end = offset + length;
  let cursor = offset;
  const display: Display = {
    template: 0, templateName: fieldTypeName(0), templateLabel: fieldTypeLabel(fieldTypeName(0)),
    type: 0, fields: [], screenNumber: null, isBuiltIn: false,
  };
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES_DISP_SETTING) {
      const tpl = bytes[content] | (bytes[content + 1] << 8);
      display.template = tpl;
      display.templateName = fieldTypeName(tpl);
      display.templateLabel = fieldTypeLabel(display.templateName);
      if (tag.length >= 4) display.type = bytes[content + 2] | (bytes[content + 3] << 8);
    } else if (tag.tagId === EXERCISE_MODES_DISP_FIELD) {
      display.fields.push(decodeDispField(bytes, content, tag.length));
    }
    cursor = content + tag.length;
  }
  return display;
}

// Which displays are the user's own screens: the ones whose Type is 10, as the desktop has
// counted them since 2026-08-12 (tools/custom_modes.py user_display_count). Across 1329 modes in
// assets/pcap the Type-10 displays are always a contiguous prefix. This replaced matching the
// end of the list against two known built-in "tails": a mode whose list ended any other way
// (one without the guidance display, for example) had all its built-in views counted as
// screens, and showed "8/8" where the watch has 5.
export const USER_DISPLAY_TYPE = 10;

/** Mutates each display in place with its screenNumber (1-based, null for a built-in view)
 * and isBuiltIn flag - same as custom_modes.py's _displays_to_json(). The array's own order
 * (the index the writers address) never changes. */
function numberDisplays(displays: Display[]): void {
  displays.forEach((d, i) => {
    d.isBuiltIn = d.type !== USER_DISPLAY_TYPE;
    d.screenNumber = d.isBuiltIn ? null : i + 1;
  });
}

/** One mode's EXERCISE_MODES_DISPLAYS body -> its displays, numbered. Also the Ambit1/2 region's
 * (AmbitLegacySportModes.decodeLegacyRegion): its display list is this same record format. */
export function decodeDisplayList(bytes: Uint8Array, offset: number, length: number): Display[] {
  const displays: Display[] = [];
  let cursor = offset;
  const end = offset + length;
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES_DISPLAY) displays.push(decodeDisplay(bytes, content, tag.length));
    cursor = content + tag.length;
  }
  numberDisplays(displays);
  return displays;
}

export interface ExerciseMode {
  settings: ExerciseModeSettings;
  displays: Display[];
}

function decodeExerciseMode(bytes: Uint8Array, offset: number, length: number): ExerciseMode {
  const end = offset + length;
  let cursor = offset;
  const mode: ExerciseMode = {
    settings: { name: '', activityId: 0, useHw: 0, altiBaroMode: 0, recordingInterval: 0,
      autolap: 0, hrHigh: 0, hrLow: 0, hrLimitsUse: 0 },
    displays: [],
  };
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === EXERCISE_MODES_SETTING_NAME_LEN64) {
      mode.settings = decodeSettings(bytes, content);
    } else if (tag.tagId === EXERCISE_MODES_DISPLAYS) {
      mode.displays.push(...decodeDisplayList(bytes, content, tag.length));
    }
    cursor = content + tag.length;
  }
  numberDisplays(mode.displays);
  return mode;
}

export interface SportModeSlot {
  name: string;
  activityId: number;
  legs: number[];
}

function decodeSportModeSlot(bytes: Uint8Array, offset: number, length: number): SportModeSlot {
  const end = offset + length;
  let cursor = offset;
  const slot: SportModeSlot = { name: '', activityId: 0, legs: [] };
  while (cursor < end) {
    const tag = readTag(bytes, cursor);
    if (!tag) break;
    const content = cursor + 4;
    if (tag.tagId === SPORT_MODE_SETTING_NAME_LEN64) {
      slot.name = decodeNameField(bytes, content, tag.length);
    } else if (tag.tagId === SPORT_MODE_ACTIVITY_ID) {
      slot.activityId = bytes[content] | (bytes[content + 1] << 8);
    } else if (tag.tagId === SPORT_MODE_EXERCISE) {
      slot.legs.push(bytes[content] | (bytes[content + 1] << 8));
    }
    cursor = content + tag.length;
  }
  return slot;
}

export interface DecodedCustomModes {
  exerciseModes: ExerciseMode[];
  sportModes: SportModeSlot[];
}

/** Decodes a raw CustomModes flash dump (12288 bytes, from readCustomModesRaw()). Mirrors
 * custom_modes.py's own decode() exactly - same tag walk, same field names. */
export function decode(bytes: Uint8Array): DecodedCustomModes {
  const root = readTag(bytes, 0);
  if (!root || root.tagId !== DEVICE_CUSTOM) {
    throw new Error(`expected DEVICE_CUSTOM at offset 0, got ${root ? `0x${root.tagId.toString(16)}` : 'nothing'}`);
  }
  let cursor = 4;
  const end = 4 + root.length;
  const result: DecodedCustomModes = { exerciseModes: [], sportModes: [] };

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
          result.exerciseModes.push(decodeExerciseMode(bytes, subContent, subTag.length));
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
          const slot = decodeSportModeSlot(bytes, subContent, subTag.length);
          if (slot.name) result.sportModes.push(slot);
        }
        subCursor = subContent + subTag.length;
      }
    }

    cursor = content + tag.length;
  }
  return result;
}
