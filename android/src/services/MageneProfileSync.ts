import type { AthleteThresholds } from './ApiIntervalsIcu';
import type { MageneProfile } from './MageneDevice';

// Magene profile vs intervals.icu - the mobile twin of the desktop's /api/magene/profile/compare
// (server.py _BRYTON_COMPARE_FIELDS + _MAGENE_TO_COMMON). intervals.icu is the source of truth;
// FTP/LTHR/Max HR/weight can go either way, gender/height/age only onto the device (intervals.icu
// doesn't take them through this API).

export type ProfileField = 'ftp' | 'lthr' | 'maxHr' | 'map' | 'weight' | 'height' | 'sex' | 'age';
// map: Bryton only (the Magene profile has none, so it's skipped there); one-way like height/age.
export const PROFILE_FIELDS: ProfileField[] = ['ftp', 'lthr', 'maxHr', 'map', 'weight', 'height', 'sex', 'age'];
export const TWO_WAY: ProfileField[] = ['ftp', 'lthr', 'maxHr', 'weight'];
export const PROFILE_LABELS: Record<ProfileField, string> = {
  ftp: 'FTP (W)', lthr: 'LTHR (bpm)', maxHr: 'Max HR (bpm)', map: 'MAP (W)', weight: 'Weight (kg)',
  height: 'Height (cm)', sex: 'Gender', age: 'Age',
};

export interface ProfileDiff { field: ProfileField; device: number; intervals: number }

export function intervalsValue(icu: AthleteThresholds, f: ProfileField): number | undefined {
  return f === 'sex' ? icu.gender : (icu as any)[f];
}

export function diffProfile(dev: MageneProfile, icu: AthleteThresholds): ProfileDiff[] {
  const out: ProfileDiff[] = [];
  for (const f of PROFILE_FIELDS) {
    const d = (dev as any)[f] as number | undefined;
    const i = intervalsValue(icu, f);
    if (d == null || i == null) continue;
    const differs = f === 'weight' ? Math.abs(d - i) >= 0.5 : d !== i;
    if (differs) out.push({ field: f, device: d, intervals: i });
  }
  return out;
}

export function fmtProfile(f: ProfileField, v: number | undefined): string {
  if (v == null) return '—';
  if (f === 'sex') return v === 1 ? 'Male' : 'Female';
  return String(v);
}
