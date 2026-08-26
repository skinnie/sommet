import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';

// Coach readiness — the Android counterpart of desktop/src/services/coachservice.cpp
// (André, 2026-08-26: "port everything to android").
//
// One deliberate difference, and it is an IMPROVEMENT rather than a shortcut: the desktop
// computes CTL/ATL itself by walking activities.db and applying exponential averages, because
// the Ambit3 has no power meter and no decoded HR strap, so its "load" is duration-based
// (see coachservice.cpp's own note: "Load is duration-based (minutes/day) — this device family
// has no power meter or HR strap decoded yet"). intervals.icu already publishes ctl/atl/rampRate
// per day, computed from whatever real TSS each activity carried across every device the athlete
// uses — so reading those is both less code and more accurate than re-deriving a weaker proxy.
//
// The thresholds, the light names and the sentences below are copied exactly from the desktop
// so both apps say the same thing about the same day.

export type ReadinessLight = 'green' | 'tempered' | 'yellow' | 'red';

export interface Readiness {
  fitness: number;      // CTL
  fatigue: number;      // ATL
  freshness: number;    // TSB = CTL - ATL
  rampPerWeek: number;
  light: ReadinessLight;
  sentence: string;
}

export interface ChartPoint {
  date: string;
  fitness: number;
  fatigue: number;
}

// Same wording as the desktop's coachservice.cpp, verbatim.
const SENTENCES: Record<ReadinessLight, string> = {
  green: "You're fresh today — fully rested. Good day for something hard if you've got it planned.",
  tempered: "You're fresh, but your fitness has climbed fast this month. Good day to train — ease into the hard part rather than going in cold.",
  yellow: "You're carrying some fatigue. Nothing alarming — see how the legs feel and adapt if today's plan feels heavy.",
  red: "You're dug in deep right now. Lean toward rest or something gentle today.",
};

// Same cut-offs as the desktop: TSB > -10 green, > -25 yellow, else red; a green day with a
// steep 7-day CTL ramp is downgraded to "tempered" (fresh, but climbing fast).
export function lightFor(freshness: number, rampPerWeek: number): ReadinessLight {
  let light: ReadinessLight = freshness > -10 ? 'green' : freshness > -25 ? 'yellow' : 'red';
  if (light === 'green' && rampPerWeek > 7) light = 'tempered';
  return light;
}

export interface CoachData {
  readiness: Readiness | null;
  chart: ChartPoint[];
}

// `days` bounds the chart window; readiness itself always comes from the most recent day that
// actually has ctl/atl, which is not necessarily today (intervals fills a day in once it has
// processed it).
export async function loadCoachData(days = 90): Promise<CoachData> {
  const rows = await fetchWellnessRaw(days);
  const withLoad = rows.filter(r => typeof r.ctl === 'number' && typeof r.atl === 'number');
  if (withLoad.length === 0) return { readiness: null, chart: [] };

  const last = withLoad[withLoad.length - 1];
  const fitness = last.ctl as number;
  const fatigue = last.atl as number;
  const freshness = fitness - fatigue;

  // intervals publishes its own rampRate (CTL change per week). Fall back to measuring the
  // 7-day CTL delta ourselves when it is absent, which is what the desktop does.
  let rampPerWeek = typeof last.rampRate === 'number' ? last.rampRate : 0;
  if (typeof last.rampRate !== 'number' && withLoad.length > 7) {
    const prev = withLoad[withLoad.length - 8];
    rampPerWeek = fitness - (prev.ctl as number);
  }

  const light = lightFor(freshness, rampPerWeek);
  return {
    readiness: { fitness, fatigue, freshness, rampPerWeek, light, sentence: SENTENCES[light] },
    chart: withLoad.map(r => ({ date: r.date, fitness: r.ctl as number, fatigue: r.atl as number })),
  };
}

// WellnessService.fetchWellness() deliberately narrows to the health metrics its two screens
// need and drops ctl/atl/rampRate. Rather than widen that shared shape for one caller, this
// reads the same endpoint and keeps the training-load fields instead.
interface LoadDay { date: string; ctl?: number; atl?: number; rampRate?: number }

async function fetchWellnessRaw(days: number): Promise<LoadDay[]> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return [];

  const newest = new Date();
  const oldest = new Date(newest.getTime() - days * 24 * 3600 * 1000);
  const ymd = (d: Date) => d.toISOString().slice(0, 10);
  const url = `https://intervals.icu/api/v1/athlete/${encodeURIComponent(creds.athleteId)}/wellness`
    + `?oldest=${ymd(oldest)}&newest=${ymd(newest)}`;
  const resp = await fetch(url, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) throw new Error(`intervals.icu wellness: HTTP ${resp.status}`);
  const rows = await resp.json();
  if (!Array.isArray(rows)) return [];

  return rows
    .map((r: any) => ({
      date: String(r?.id ?? ''),
      ctl: typeof r?.ctl === 'number' ? r.ctl : undefined,
      atl: typeof r?.atl === 'number' ? r.atl : undefined,
      rampRate: typeof r?.rampRate === 'number' ? r.rampRate : undefined,
    }))
    .filter(r => r.date)
    .sort((a, b) => a.date.localeCompare(b.date));
}
