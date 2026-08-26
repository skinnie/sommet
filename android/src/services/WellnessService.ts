import AsyncStorage from '@react-native-async-storage/async-storage';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';

// intervals.icu wellness feed — the single source behind BOTH the Weight and Health screens
// (André, 2026-08-26: "port everything to android"). The desktop equivalents are C++ services
// that reach Garmin Connect through the Python backend; Android has no such backend and no
// Garmin Connect CLOUD client at all (its GarminModule is the USB eTrex, not the cloud), so
// this deliberately uses the one provider Android can actually reach directly.
//
// That is a real, checked limitation rather than an oversight: verified against the live API
// (2026-08-26) that intervals.icu genuinely carries this athlete's history —
//   weight 201 values, restingHR 664, sleepSecs 388, vo2max 63, steps 101, hrv 51
// over ~2.5 years — so the screens have real data without Garmin. What Android cannot show
// that the desktop can is Garmin-only material (Index-scale body composition, body battery).
//
// One gotcha worth writing down: a wellness row's `tempWeight` is a BOOLEAN flag ("this weight
// was carried forward, not measured today"), NOT a weight value. Reading it as a number gives
// `true`/`false` instead of kilograms. The real number is `weight`, and it is null on days with
// no weigh-in - which is most days, so callers must filter nulls rather than assume a value.

const API_BASE = 'https://intervals.icu/api/v1';
const MANUAL_KEY = 'weight.manualEntries';   // local weigh-ins, same idea as the desktop's

export interface WellnessDay {
  date: string;              // "YYYY-MM-DD"
  weight?: number;           // kg
  restingHR?: number;
  hrv?: number;
  steps?: number;
  sleepHours?: number;
  vo2max?: number;
}

export interface WeightPoint {
  date: string;
  weightKg: number;
  source: 'intervals' | 'manual';
}

function ymd(d: Date): string {
  return d.toISOString().slice(0, 10);
}

// Raw wellness rows for a window, newest last. Returns [] when intervals.icu isn't connected
// rather than throwing, so a screen can render its "connect first" state instead of an error.
export async function fetchWellness(days = 365): Promise<WellnessDay[]> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return [];

  const newest = new Date();
  const oldest = new Date(newest.getTime() - days * 24 * 3600 * 1000);
  const url = `${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}/wellness`
    + `?oldest=${ymd(oldest)}&newest=${ymd(newest)}`;
  // Basic auth + an explicit UA, same as ApiIntervalsIcu/IntervalsStats: intervals.icu sits
  // behind Cloudflare and 403s some default agents.
  const resp = await fetch(url, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) throw new Error(`intervals.icu wellness: HTTP ${resp.status}`);
  const rows = await resp.json();
  if (!Array.isArray(rows)) return [];

  const out: WellnessDay[] = [];
  for (const r of rows) {
    const date = String(r?.id ?? '');          // wellness rows are keyed by date in `id`
    if (!date) continue;
    const day: WellnessDay = { date };
    // Only copy values that are really present - a null here means "not recorded that day",
    // which is different from zero and must not be charted as a real reading.
    if (typeof r.weight === 'number') day.weight = r.weight;
    if (typeof r.restingHR === 'number') day.restingHR = r.restingHR;
    if (typeof r.hrv === 'number') day.hrv = r.hrv;
    if (typeof r.steps === 'number') day.steps = r.steps;
    if (typeof r.vo2max === 'number') day.vo2max = r.vo2max;
    if (typeof r.sleepSecs === 'number') day.sleepHours = r.sleepSecs / 3600;
    out.push(day);
  }
  out.sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

// ---- manual weigh-ins (local only) ------------------------------------------------------
// Kept on the device, never uploaded. Mirrors the desktop's own manual store so the two behave
// the same way; merged in below at a higher priority than intervals for the same day.

export async function loadManualWeights(): Promise<WeightPoint[]> {
  try {
    const raw = await AsyncStorage.getItem(MANUAL_KEY);
    const list = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .filter((e: any) => e && typeof e.date === 'string' && typeof e.weightKg === 'number')
      .map((e: any) => ({ date: e.date, weightKg: e.weightKg, source: 'manual' as const }));
  } catch {
    return [];                                  // a corrupt store must not break the screen
  }
}

export async function addManualWeight(date: string, weightKg: number): Promise<void> {
  const list = await loadManualWeights();
  const next = list.filter(e => e.date !== date);        // one entry per day, newest wins
  next.push({ date, weightKg, source: 'manual' });
  next.sort((a, b) => a.date.localeCompare(b.date));
  await AsyncStorage.setItem(MANUAL_KEY, JSON.stringify(next));
}

export async function removeManualWeight(date: string): Promise<void> {
  const list = await loadManualWeights();
  await AsyncStorage.setItem(MANUAL_KEY, JSON.stringify(list.filter(e => e.date !== date)));
}

// ---- merged weight series ---------------------------------------------------------------

// One point per day, oldest first. A manual entry always wins over intervals for the same day:
// you typed it here, on purpose, so it is the more deliberate reading - the same precedence the
// desktop uses.
//
// Deliberately NO source picker here, unlike the desktop's Settings toggle: that toggle exists
// to choose between intervals.icu and GARMIN, and Android cannot reach Garmin Connect at all
// (see this file's header). With only one remote provider a picker would be a control with
// nothing to pick, so this always merges intervals + manual.
export async function loadWeightSeries(days = 365): Promise<WeightPoint[]> {
  const byDate = new Map<string, WeightPoint>();
  try {
    for (const d of await fetchWellness(days)) {
      if (typeof d.weight === 'number')
        byDate.set(d.date, { date: d.date, weightKg: d.weight, source: 'intervals' });
    }
  } catch {
    // Offline or API failure: fall through to whatever manual entries exist rather than
    // showing nothing at all.
  }
  for (const m of await loadManualWeights()) byDate.set(m.date, m);

  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}
