// Twin of tools/race_days.py: the nights (forced rests + planned sleep >= 2 h), the days between
// them, the equal-distance comparison and the beds near each night. Parity-tested.
import { NDT, iso, diffSeconds, parseIso19, pyRound, pyFixed, pySignedFixed } from './pyCompat';

const MIN_NIGHT_S = 2 * 3600;

export function analyzeDays(timeline: any, pois: any = null, reachKm = 20.0): any {
  const rows: any[] = timeline.controls || [];
  const totalKm = Number(timeline.distance_km || 0.0);
  if (!rows.length || totalKm <= 0) return { ok: false, error: 'no timeline' };
  let nights: Array<{ km: number; start: NDT; end: NDT; source: string }> = [];
  rows.forEach((r, idx) => {
    for (const x of r.rests || []) {
      nights.push({ km: Number(x.km), start: parseIso19(x.start), end: parseIso19(x.end), source: 'no-ride hours' });
    }
    if ((r.sleep_s || 0) >= MIN_NIGHT_S && idx !== rows.length - 1) {
      nights.push({ km: Number(r.distance_km), start: parseIso19(r.arrival_dt), end: parseIso19(r.depart_dt),
                    source: 'planned sleep at ' + String(r.label) });
    }
  });
  nights = nights.filter(n => diffSeconds(n.end, n.start) >= MIN_NIGHT_S);
  nights = nights.map((n, i) => [n, i] as [typeof n, number]).sort((a, b) => (a[0].km - b[0].km) || (a[1] - b[1])).map(x => x[0]);

  const beds: any[] = [];
  for (const cat of ['shelter']) {
    for (const p of (((pois || {}).categories || {})[cat] || {}).pois || []) beds.push(p);
  }

  const outNights = nights.map((n, i) => {
    const near = beds.filter(b => Math.abs(b.km - n.km) <= reachKm)
      .map((b, j) => [b, j] as [any, number])
      .sort((a, b) => (Math.abs(a[0].km - n.km) - Math.abs(b[0].km - n.km)) || (a[1] - b[1])).map(x => x[0]);
    return {
      n: i + 1, km: pyRound(n.km, 1), start: iso(n.start), end: iso(n.end),
      hours: pyRound(diffSeconds(n.end, n.start) / 3600.0, 1), source: n.source,
      sleep_at: near.slice(0, 3).map(b => ({ name: b.name ?? null, km: b.km, offset_km: pyRound(b.km - n.km, 1),
                                             kind: b.kind || b.subtype || null })),
      n_beds: near.length, no_bed: beds.length > 0 && !near.length,
    };
  });

  const start = parseIso19(timeline.start_dt), finish = parseIso19(timeline.finish_eta_dt);
  const edges: Array<[number, NDT]> = [[0.0, start], ...nights.map(n => [n.km, n.end] as [number, NDT])];
  const ends: Array<[number, NDT]> = [...nights.map(n => [n.km, n.start] as [number, NDT]), [totalKm, finish]];
  const days = edges.map(([k0, t0], i) => {
    const [k1, t1] = ends[i];
    return { day: i + 1, from_km: pyRound(k0, 1), to_km: pyRound(k1, 1), km: pyRound(k1 - k0, 1), start: iso(t0), end: iso(t1) };
  });
  const nDays = days.length;
  const even: number[] = [];
  for (let i = 1; i < nDays; i++) even.push(pyRound(totalKm * i / nDays, 1));

  const lines: string[] = [];
  outNights.forEach((n, i) => {
    const e = i < even.length ? even[i] : null;
    let bit = '';
    if (e !== null) {
      const d = n.km - e;
      bit = ` - an equal-distance split would end this day at km ${pyFixed(e, 0)} (${d >= 0 ? '+' : '-'}${pyFixed(Math.abs(d), 0)} km)`;
    }
    lines.push(`Night ${n.n}: rest ${n.start.slice(11, 16)}-${n.end.slice(11, 16)} (${pyFixed(n.hours, 1)} h) at km ${pyFixed(n.km, 0)}${bit}.`);
    if (n.no_bed) {
      lines.push(`   No accommodation within ${pyFixed(reachKm, 0)} km of km ${pyFixed(n.km, 0)} - plan a bivouac or move the rest.`);
    } else if (n.sleep_at.length) {
      const b = n.sleep_at[0];
      lines.push(`   Nearest bed: ${String(b.name)} at km ${pyFixed(b.km, 0)} (${pySignedFixed(b.offset_km, 0)} km); ${n.n_beds} within ${pyFixed(reachKm, 0)} km.`);
    }
  });
  if (outNights.length) {
    const sug = Number(timeline.sleep_suggested_s || 0.0);
    const per = sug ? sug / outNights.length : 0.0;
    const short = outNights.filter(n => per && n.hours * 3600 < per * 0.75);
    if (short.length) {
      lines.push(`Your rest is shorter than the ~${pyFixed(per / 3600.0, 1)} h a night that a ride this long usually needs (${short.length} night${short.length > 1 ? 's' : ''}).`);
    }
  }
  return { ok: true, nights: outNights, days, even_km: even, lines };
}

/** race_days.py main() */
export function daysFromBody(body: any): any {
  try {
    if (!body.timeline) throw new Error("'timeline'");
    return analyzeDays(body.timeline, body.pois ?? null, Number(body.reach_km || 20.0));
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
