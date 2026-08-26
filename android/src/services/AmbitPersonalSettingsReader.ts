import { DecodedSetting, SettingChoice } from './AmbitSettingsReader';

// Ambit 1 / Ambit 2 family (USB-only) personal-settings decode.
//
// These 2012-era watches don't use the Ambit3/Kailash SBEM sml.DeviceSettings (0x1100) at
// all — they answer the legacy personal-settings command, which libambit parses into a fixed
// ambit_personal_settings_t struct (bundled device_driver_ambit.c / personal.c, from
// openambit — assets/openambit-master.zip). nativeAmbitReadPersonalSettings() returns the
// user-facing fields as JSON of field -> raw int; this maps each to a labelled DecodedSetting
// so the exact same settings UI the Ambit3/Kailash path uses renders them unchanged.
//
// Option labels mirror two references in this repo's assets:
//   - openambit's ambit_personal_settings_t (src/libambit/libambit.h, personal.c)
//   - the Movescount Emulation Project's own schema-settings
//     (assets/Movescount_Emu/ServerFiles/data/schema-settings) — its enum option meanings
//     (Backlight Mode 0-4, AltiBaro 0-2, etc.), which are shared across the Ambit family.
//
// READ-ONLY here for now — but NOT because the watch can't be written. SuuntoLink (which
// replaced Moveslink for these watches) DOES write their personal settings via its
// BluebirdDriver; openambit/opensportsync just never implemented the write (they only define
// the unused 0x0b01 id). To make our own write byte-exact we need a SuuntoLink Ambit 1/2
// settings-change USB capture to reverse-engineer, the same way the Ambit3 0x1101 write was
// proven — obtainable once the hardware is in hand. Until then, display only.

const LANGUAGES: SettingChoice[] = [
  { value: 0, label: 'Dansk' }, { value: 1, label: 'Deutsch' }, { value: 2, label: 'English' },
  { value: 3, label: 'Espanol' }, { value: 4, label: 'Francais' }, { value: 5, label: 'Italiano' },
  { value: 6, label: 'Nederlands' }, { value: 7, label: 'Norsk' }, { value: 8, label: 'Portugues' },
  { value: 9, label: 'Suomi' }, { value: 10, label: 'Svenska' }, { value: 11, label: 'Chinese' },
  { value: 12, label: 'Japanese' }, { value: 13, label: 'Korean' }, { value: 14, label: 'Cestina' },
  { value: 15, label: 'Polski' }, { value: 16, label: 'Russian' },
];

interface PersonalField {
  key: string;
  kind: 'bool' | 'enum' | 'number';
  choices?: SettingChoice[];
  // Raw-int -> display-value multiplier (e.g. weight is stored as kg*100). Default 1.
  scale?: number;
  // Editable-field metadata (2026-08-26): control 'number' renders a text+Save editor in
  // SettingsScreen; min/max are the display-unit bounds handleSetNumber enforces; unit is
  // shown after the value. Only set on the writable personal-profile fields (see
  // AmbitPersonalSettingsWriter.AMBIT12_WRITABLE); every other field stays read-only.
  control?: 'number';
  min?: number;
  max?: number;
  unit?: string;
}

const AMBIT12_PERSONAL_FIELDS: PersonalField[] = [
  { key: 'date_format', kind: 'enum', choices: [{ value: 0, label: 'DDMM' }, { value: 1, label: 'MMDD' }] },
  { key: 'tones', kind: 'enum', choices: [{ value: 0, label: 'Buttons off' }, { value: 1, label: 'All on' }, { value: 2, label: 'All off' }] },
  { key: 'gps_position_format', kind: 'enum', choices: [
      { value: 0, label: 'WGS84 d' }, { value: 1, label: 'WGS84 dm' }, { value: 2, label: 'WGS84 dms' },
      { value: 3, label: 'UTM' }, { value: 4, label: 'MGRS' }, { value: 5, label: 'British (BNG)' },
      { value: 6, label: 'Finnish (ETRS-TM35FIN)' }, { value: 7, label: 'Finnish (KKJ)' },
      { value: 8, label: 'Irish (IG)' }, { value: 9, label: 'Swedish (RT90)' }, { value: 10, label: 'Swiss (CH1903)' },
      { value: 11, label: 'UTM NAD27 Alaska' }, { value: 12, label: 'UTM NAD27 Conus' },
      { value: 13, label: 'UTM NAD83' }, { value: 14, label: 'NZTM2000' },
    ] },
  { key: 'button_lock_sport_mode', kind: 'enum', choices: [{ value: 0, label: 'All buttons' }, { value: 1, label: 'Actions only' }] },
  { key: 'button_lock_time_mode', kind: 'enum', choices: [{ value: 0, label: 'All buttons' }, { value: 1, label: 'Actions only' }] },
  { key: 'units_mode', kind: 'enum', choices: [{ value: 0, label: 'Metric' }, { value: 1, label: 'Imperial' }, { value: 2, label: 'Advanced' }] },
  { key: 'language', kind: 'enum', choices: LANGUAGES },
  { key: 'time_format', kind: 'enum', choices: [{ value: 0, label: '24h' }, { value: 1, label: '12h' }] },
  { key: 'gps_time_keeping', kind: 'enum', choices: [{ value: 0, label: 'On' }, { value: 1, label: 'Off' }] },
  { key: 'alti_baro_mode', kind: 'enum', choices: [{ value: 0, label: 'Altimeter' }, { value: 1, label: 'Barometer' }, { value: 2, label: 'Automatic' }] },
  { key: 'backlight_mode', kind: 'enum', choices: [
      { value: 0, label: 'Normal' }, { value: 1, label: 'Off' }, { value: 2, label: 'Night' },
      { value: 3, label: 'Toggle' }, { value: 4, label: 'Automatic' },
    ] },
  { key: 'backlight_brightness', kind: 'number' },
  { key: 'display_dark', kind: 'bool' },
  { key: 'storm_alarm', kind: 'bool' },
  // Personal profile - present in the legacy struct (personal.c), surfaced read-only. These
  // are the fields a user recognises from the watch's own "Personal" menu. Ambit 1/2 have no
  // settings-write command in the protocol (no 0x0b01 in any real capture), so display only.
  // Personal profile - now WRITABLE on the Ambit1/2 (0x0b01, reverse-engineered from a real
  // SuuntoLink<->Ambit2 capture 2026-08-26; see AmbitPersonalSettingsWriter.ts). control
  // 'number'/enum + min/max make SettingsScreen render them editable for legacy watches.
  { key: 'gender', kind: 'enum', choices: [{ value: 1, label: 'Male' }, { value: 0, label: 'Female' }] },
  { key: 'birth_year', kind: 'number', control: 'number', min: 1920, max: 2035 },
  { key: 'weight', kind: 'number', scale: 0.01, control: 'number', min: 0, max: 300, unit: 'kg' }, // kg*100
  { key: 'height', kind: 'number', control: 'number', min: 0, max: 255, unit: 'cm' },
  { key: 'max_hr', kind: 'number', control: 'number', min: 0, max: 255, unit: 'bpm' },
  { key: 'rest_hr', kind: 'number', control: 'number', min: 0, max: 255, unit: 'bpm' },
  { key: 'fitness_level', kind: 'number', control: 'number', min: 0, max: 255 },
];

/** Decodes the JSON from readPersonalSettings() (native/AmbitUsbModule.ts) into the same
 * DecodedSetting[] shape the Ambit3/Kailash reader produces, so the settings UI renders it
 * unchanged. Fields the watch didn't report are skipped. Returns [] on malformed input. */
export function decodePersonalSettings(json: string): DecodedSetting[] {
  let obj: Record<string, unknown>;
  try { obj = JSON.parse(json); } catch { return []; }
  const out: DecodedSetting[] = [];
  const thisYear = new Date().getFullYear();
  for (const f of AMBIT12_PERSONAL_FIELDS) {
    const v = obj[f.key];
    if (typeof v !== 'number') continue;
    // Profile fields are often unset on a watch (0) or hold a placeholder; only show a value
    // that's actually plausible, so the display never renders nonsense for a blank profile.
    if ((f.key === 'weight' || f.key === 'height' || f.key === 'max_hr' || f.key === 'rest_hr'
         || f.key === 'fitness_level') && v === 0) continue;
    if (f.key === 'birth_year' && (v < 1900 || v > thisYear)) continue;
    const value = f.scale ? Math.round(v * f.scale * 10) / 10 : v;
    out.push({ key: f.key, path: f.key, kind: f.kind, value, choices: f.choices,
               control: f.control, min: f.min, max: f.max, unit: f.unit });
  }
  return out;
}
