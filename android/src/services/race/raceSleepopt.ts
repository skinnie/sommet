// Twin of tools/race_sleepopt.py: "Suggest my sleep" - rebuild the timeline for many nightly no-ride
// windows with the two-process model and rank them (finish time, worst alertness, cutoffs, beds).
// Parity-tested. Like the desktop it gives up after TIME_BUDGET_S (a slow phone tries fewer plans).
import { eventFromDict, athleteFromDict, bikeFromDict, RaceEvent, AthleteInputs, BikeInputs } from './raceEvent';
import { parseGpxPoints } from './geo';
import { buildTimeline } from './raceTimeline';
import { analyzeDays } from './raceDays';
import { band, DANGER_ALERTNESS, TIRED_ALERTNESS } from './raceAlertness';
import { pyRound } from './pyCompat';

export const BEDS_H = [21.5, 22.0, 22.5, 23.0, 23.5, 0.0, 0.5, 1.0, 1.5, 2.0];
export const LENGTHS_H = [1.5, 3.0, 4.5, 6.0, 7.5];
const TIME_BUDGET_S = 75.0;
const pad = (n: number) => String(n).padStart(2, '0');
const pyMod = (a: number, n: number) => ((a % n) + n) % n;

function hhmm(h: number): string {
  h = pyMod(h, 24.0);
  return `${pad(Math.trunc(h))}:${pad(pyMod(Math.trunc(pyRound((h - Math.trunc(h)) * 60)), 60))}`;
}

function run(body: any, event: RaceEvent, athlete: AthleteInputs | null, bike: BikeInputs | null, habit: any, nr: any) {
  const fatigue = { enabled: true, model: 'twoprocess', bed_h: habit.bed_h ?? 22.0, wake_h: habit.wake_h ?? 6.0 };
  return buildTimeline(event, athlete, bike, {
    stops_s: body.stops_s ?? null, stop_total_s: body.stop_total_s ?? null, stop_profile: body.stop_profile ?? null,
    stop_events: body.stop_events ?? null, wind_speed_delta_kmh: Number(body.wind_speed_delta_kmh || 0.0),
    fatigue, no_ride: nr,
  });
}

const same = (a: any, b: any) => JSON.stringify(a) === JSON.stringify(b);   // Python `in` compares dicts by value

// Shared by the synchronous loop (parity test) and the async one (the app: yields between plans so
// the screen stays responsive, and reports progress).
function prepare(body: any) {
  const event = eventFromDict(body.event || {});
  if (!event.points.length && event.gpx) event.points = parseGpxPoints(event.gpx);
  const athlete = body.athlete ? athleteFromDict(body.athlete) : null;
  const bike = body.bike ? bikeFromDict(body.bike) : null;
  const habit = body.habit || {};
  const pois = body.pois ?? null;
  const summarize = (tl: any, bed: number, length: number) => {
    const al = tl.alertness || {};
    const days = (tl.no_ride || tl.controls.some((r: any) => r.sleep_s)) ? analyzeDays(tl, pois) : {};
    const nights = (days.nights || []).map((n: any) => ({
      km: n.km, start: n.start.slice(11, 16), end: n.end.slice(11, 16), hours: n.hours,
      no_bed: n.no_bed, nearest_bed: n.sleep_at.length ? n.sleep_at[0] : null,
    }));
    return { bed: hhmm(bed), wake: hhmm(bed + length), hours: length, finish: tl.finish_eta_dt,
             elapsed_h: pyRound(tl.elapsed_time_s / 3600.0, 1), min_alertness: al.min ?? null, min_at: al.min_at ?? null,
             band: band(al.min || 0.0), worst_margin_s: tl.worst_margin_s ?? null, nights };
  };
  const one = (bed: number | null, length: number) =>
    run(body, event, athlete, bike, habit, bed === null ? null : { start_h: bed, end_h: pyMod(bed + length, 24.0) });
  return { habit, summarize, one };
}

function finish(results: any[], baseline: any, habit: any) {
  const ok = (r: any) => (r.min_alertness || 0) >= DANGER_ALERTNESS && (r.worst_margin_s === null || r.worst_margin_s >= 0);
  const feasible = results.filter(ok).map((r, i) => [r, i] as [any, number])
    .sort((a, b) => (a[0].elapsed_h - b[0].elapsed_h) || (a[1] - b[1])).map(x => x[0]);
  const safe = feasible.filter(r => (r.min_alertness || 0) >= TIRED_ALERTNESS);
  const options = (safe.length ? safe : feasible).slice(0, 6);
  if (results.length) {
    let best = results[0];
    for (const r of results) {
      const ka = r.min_alertness || 0, kb = best.min_alertness || 0;
      if (ka > kb || (ka === kb && -r.elapsed_h > -best.elapsed_h)) best = r;
    }
    if (!options.some(o => same(o, best))) options.push(best);
  }
  return {
    ok: true, options, baseline,
    recommended: options.length && safe.some(s => same(s, options[0])) ? 0 : null,
    tried: results.length,
    note: `Sleep-science model (sleep pressure + body clock shifted to your usual ${hhmm(habit.bed_h ?? 22.0)}-${hhmm(habit.wake_h ?? 6.0)}), not fitted to your rides; treat alertness as relative.`,
  };
}

export function suggestSleep(body: any, beds = BEDS_H, lengths = LENGTHS_H): any {
  const { habit, summarize, one } = prepare(body);
  const t0 = Date.now();
  const baseTl = one(null, 0);
  if (!baseTl.ok) return { ok: false, error: baseTl.error ?? 'timeline failed' };
  const baseline: any = summarize(baseTl, 0.0, 0.0);
  baseline.bed = null; baseline.wake = null;
  const results: any[] = [];
  outer: for (const bed of beds) {
    for (const length of lengths) {
      if ((Date.now() - t0) / 1000 > TIME_BUDGET_S) break outer;
      const tl = one(bed, length);
      if (tl.ok) results.push(summarize(tl, bed, length));
    }
  }
  return finish(results, baseline, habit);
}

/** The app's version: same plans, same ranking, but yields to the UI between plans. */
export async function suggestSleepAsync(body: any, onProgress?: (done: number, total: number) => void,
                                        beds = BEDS_H, lengths = LENGTHS_H): Promise<any> {
  const { habit, summarize, one } = prepare(body);
  const t0 = Date.now();
  const tick = () => new Promise<void>(r => setTimeout(r, 0));
  await tick();
  const baseTl = one(null, 0);
  if (!baseTl.ok) return { ok: false, error: baseTl.error ?? 'timeline failed' };
  const baseline: any = summarize(baseTl, 0.0, 0.0);
  baseline.bed = null; baseline.wake = null;
  const results: any[] = [];
  const total = beds.length * lengths.length;
  let n = 0;
  outer: for (const bed of beds) {
    for (const length of lengths) {
      if ((Date.now() - t0) / 1000 > TIME_BUDGET_S) break outer;
      const tl = one(bed, length);
      if (tl.ok) results.push(summarize(tl, bed, length));
      onProgress?.(++n, total);
      await tick();
    }
  }
  return finish(results, baseline, habit);
}

/** race_sleepopt.py main() */
export function sleepOptionsFromBody(body: any): any {
  try { return suggestSleep(body); } catch (e: any) { return { ok: false, error: String(e?.message ?? e) }; }
}
