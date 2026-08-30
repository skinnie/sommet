// Sun & moon events for a place and day — sunrise/sunset, the three twilights, solar noon,
// moonrise/moonset/transit, moon phase/illumination/age. Faithful TypeScript port of the
// desktop's tools/astro.py (Paul Schlyter's low-precision formulae + an altitude-sampling pass
// for rise/set/twilight crossings). Pure computation, offline, no network — the "will I finish
// in the light?" maths behind the route-weather planner. Keep in sync with astro.py.

const DEG = Math.PI / 180.0;

// Altitude of a body's centre at rise/set, degrees. Sun uses -0.833 (refraction+semidiameter);
// the moon's horizon parallax nets to ~ +0.125.
const H_SUN = -0.833;
const H_CIVIL = -6.0;
const H_NAUTICAL = -12.0;
const H_ASTRO = -18.0;
const H_MOON = 0.125;

const SYNODIC_MONTH = 29.530588853; // days, new→new moon

const rev = (x: number): number => ((x % 360) + 360) % 360;
const atan2d = (y: number, x: number): number => Math.atan2(y, x) / DEG;
const sind = (d: number): number => Math.sin(d * DEG);
const cosd = (d: number): number => Math.cos(d * DEG);

// A naive-UTC instant, carried as its UTC calendar parts (mirrors Python's naive datetime).
interface UT { y: number; mo: number; d: number; h: number; mi: number; s: number; }

function utFromDate(dt: Date): UT {
  return { y: dt.getUTCFullYear(), mo: dt.getUTCMonth() + 1, d: dt.getUTCDate(),
           h: dt.getUTCHours(), mi: dt.getUTCMinutes(), s: dt.getUTCSeconds() };
}

// Schlyter day number: days since 2000-01-00 00:00 UT.
function daynum(u: UT): number {
  const n = 367 * u.y
    - Math.floor((7 * (u.y + Math.floor((u.mo + 9) / 12))) / 4)
    + Math.floor((275 * u.mo) / 9) + u.d - 730530;
  return n + (u.h + u.mi / 60.0 + u.s / 3600.0) / 24.0;
}

interface Sun { L: number; lon: number; ra: number; dec: number; obl: number; Msun: number; }

function sunPos(d: number): Sun {
  const w = 282.9404 + 4.70935e-5 * d;
  const e = 0.016709 - 1.151e-9 * d;
  const M = rev(356.0470 + 0.9856002585 * d);
  const obl = 23.4393 - 3.563e-7 * d;
  const L = rev(w + M);
  const E = M + (180.0 / Math.PI) * e * sind(M) * (1 + e * cosd(M));
  const xv = cosd(E) - e;
  const yv = Math.sqrt(1 - e * e) * sind(E);
  const v = rev(atan2d(yv, xv));
  const lon = rev(v + w);
  const xs = cosd(lon), ys = sind(lon);
  const xe = xs;
  const ye = ys * cosd(obl);
  const ze = ys * sind(obl);
  const ra = rev(atan2d(ye, xe));
  const dec = atan2d(ze, Math.hypot(xe, ye));
  return { L, lon, ra, dec, obl, Msun: M };
}

interface Moon { lon: number; lat: number; ra: number; dec: number; }

function moonPos(d: number, sun: Sun): Moon {
  const N = rev(125.1228 - 0.0529538083 * d);
  const inc = 5.1454;
  const w = rev(318.0634 + 0.1643573223 * d);
  const e = 0.054900;
  const M = rev(115.3654 + 13.0649929509 * d);
  const obl = sun.obl;

  let E = M + (180.0 / Math.PI) * e * sind(M) * (1 + e * cosd(M));
  for (let i = 0; i < 6; i++) {
    E = E - (E - (180.0 / Math.PI) * e * sind(E) - M) / (1 - e * cosd(E));
  }
  const xv = cosd(E) - e;
  const yv = Math.sqrt(1 - e * e) * sind(E);
  const v = rev(atan2d(yv, xv));
  const r = Math.hypot(xv, yv);

  const vw = v + w;
  const xh = r * (cosd(N) * cosd(vw) - sind(N) * sind(vw) * cosd(inc));
  const yh = r * (sind(N) * cosd(vw) + cosd(N) * sind(vw) * cosd(inc));
  const zh = r * (sind(vw) * sind(inc));
  let lon = rev(atan2d(yh, xh));
  let lat = atan2d(zh, Math.hypot(xh, yh));

  const Ls = sun.L, Ms = sun.Msun;
  const Lm = rev(N + w + M);
  const Dm = rev(Lm - Ls);
  const F = rev(Lm - N);
  lon += (-1.274 * sind(M - 2 * Dm)
    + 0.658 * sind(2 * Dm)
    - 0.186 * sind(Ms)
    - 0.059 * sind(2 * M - 2 * Dm)
    - 0.057 * sind(M - 2 * Dm + Ms)
    + 0.053 * sind(M + 2 * Dm)
    + 0.046 * sind(2 * Dm - Ms)
    + 0.041 * sind(M - Ms)
    - 0.035 * sind(Dm)
    - 0.031 * sind(M + Ms)
    - 0.015 * sind(2 * F - 2 * Dm)
    + 0.011 * sind(M - 4 * Dm));
  lat += (-0.173 * sind(F - 2 * Dm)
    - 0.055 * sind(M - F - 2 * Dm)
    - 0.046 * sind(M + F - 2 * Dm)
    + 0.033 * sind(F + 2 * Dm)
    + 0.017 * sind(2 * M + F));
  lon = rev(lon);

  const xg = cosd(lon) * cosd(lat);
  const yg = sind(lon) * cosd(lat);
  const zg = sind(lat);
  const xe = xg;
  const ye = yg * cosd(obl) - zg * sind(obl);
  const ze = yg * sind(obl) + zg * cosd(obl);
  const ra = rev(atan2d(ye, xe));
  const dec = atan2d(ze, Math.hypot(xe, ye));
  return { lon, lat, ra, dec };
}

function altitude(ra: number, dec: number, u: UT, lat: number, lon: number, sunL: number): number {
  const ut = u.h + u.mi / 60.0 + u.s / 3600.0;
  const gmst0 = rev(sunL + 180.0) / 15.0;
  const lst = (gmst0 + ut + lon / 15.0) * 15.0;
  const ha = rev(lst - ra);
  return Math.asin(sind(lat) * sind(dec) + cosd(lat) * cosd(dec) * cosd(ha)) / DEG;
}

function sampleDay(y: number, mo: number, d: number, lat: number, lon: number, tzH: number, stepMin = 4) {
  // local midnight in UTC = local_midnight - tz
  const baseMs = Date.UTC(y, mo - 1, d) - tzH * 3600_000;
  const mins: number[] = [], sunAlt: number[] = [], moonAlt: number[] = [];
  for (let t = 0; t <= 1440; t += stepMin) {
    const u = utFromDate(new Date(baseMs + t * 60_000));
    const dn = daynum(u);
    const s = sunPos(dn);
    const m = moonPos(dn, s);
    mins.push(t);
    sunAlt.push(altitude(s.ra, s.dec, u, lat, lon, s.L));
    moonAlt.push(altitude(m.ra, m.dec, u, lat, lon, s.L));
  }
  return { mins, sunAlt, moonAlt };
}

function crossings(mins: number[], alts: number[], h: number): Array<[number, 'up' | 'down']> {
  const out: Array<[number, 'up' | 'down']> = [];
  for (let i = 1; i < alts.length; i++) {
    const a = alts[i - 1] - h, b = alts[i] - h;
    if (a === 0.0) {
      out.push([mins[i - 1], alts[i] > alts[i - 1] ? 'up' : 'down']);
    } else if ((a < 0) !== (b < 0)) {
      const frac = a / (a - b);
      const mn = mins[i - 1] + frac * (mins[i] - mins[i - 1]);
      out.push([mn, b > a ? 'up' : 'down']);
    }
  }
  return out;
}

function first(cr: Array<[number, 'up' | 'down']>, dir: 'up' | 'down'): number | null {
  for (const [mn, dr] of cr) if (dr === dir) return mn;
  return null;
}

export function hhmm(mn: number | null): string | null {
  if (mn === null) return null;
  const m = ((Math.round(mn) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

function phaseName(elong: number): string {
  const e = rev(elong);
  const names: Array<[number, string]> = [
    [22.5, 'new moon'], [67.5, 'waxing crescent'], [112.5, 'first quarter'],
    [157.5, 'waxing gibbous'], [202.5, 'full moon'], [247.5, 'waning gibbous'],
    [292.5, 'last quarter'], [337.5, 'waning crescent'],
  ];
  for (const [upper, name] of names) if (e < upper) return name;
  return 'new moon';
}

export interface AstroEvents {
  sun: Record<string, string | null>;
  sun_min: Record<string, number | null>;
  moon: Record<string, string | null>;
  moon_min: Record<string, number | null>;
  moon_phase: string;
  moon_illumination: number;
  moon_age_days: number;
}

/** All sun/moon events for a local calendar date (y, mo=1-12, d) at lat/lon, UTC offset tzH
 * (may be fractional). Times are "HH:MM" local, plus *_min minutes-of-day. null where the
 * event doesn't occur that day (e.g. polar day). */
export function events(y: number, mo: number, d: number, lat: number, lon: number, tzH: number): AstroEvents {
  const { mins, sunAlt, moonAlt } = sampleDay(y, mo, d, lat, lon, tzH);

  const sunX: Record<number, Array<[number, 'up' | 'down']>> = {};
  for (const h of [H_SUN, H_CIVIL, H_NAUTICAL, H_ASTRO]) sunX[h] = crossings(mins, sunAlt, h);
  let noonI = 0;
  for (let i = 1; i < sunAlt.length; i++) if (sunAlt[i] > sunAlt[noonI]) noonI = i;
  const moonX = crossings(mins, moonAlt, H_MOON);
  let transitI = 0;
  for (let i = 1; i < moonAlt.length; i++) if (moonAlt[i] > moonAlt[transitI]) transitI = i;

  const noonUtc = utFromDate(new Date(Date.UTC(y, mo - 1, d, 12) - tzH * 3600_000));
  const s = sunPos(daynum(noonUtc));
  const m = moonPos(daynum(noonUtc), s);
  const elong = rev(m.lon - s.lon);
  const illum = (1 - cosd(elong)) / 2.0;
  const age = SYNODIC_MONTH * elong / 360.0;

  const sunMin: Record<string, number | null> = {
    astronomical_dawn: first(sunX[H_ASTRO], 'up'),
    nautical_dawn: first(sunX[H_NAUTICAL], 'up'),
    civil_dawn: first(sunX[H_CIVIL], 'up'),
    sunrise: first(sunX[H_SUN], 'up'),
    solar_noon: mins[noonI],
    sunset: first(sunX[H_SUN], 'down'),
    civil_dusk: first(sunX[H_CIVIL], 'down'),
    nautical_dusk: first(sunX[H_NAUTICAL], 'down'),
    astronomical_dusk: first(sunX[H_ASTRO], 'down'),
  };
  const moonMin: Record<string, number | null> = {
    moonrise: first(moonX, 'up'),
    moonset: first(moonX, 'down'),
    transit: moonAlt[transitI] > H_MOON ? mins[transitI] : null,
  };
  const mapHH = (o: Record<string, number | null>) => {
    const r: Record<string, string | null> = {};
    for (const k of Object.keys(o)) r[k] = hhmm(o[k]);
    return r;
  };
  return {
    sun: mapHH(sunMin), sun_min: sunMin,
    moon: mapHH(moonMin), moon_min: moonMin,
    moon_phase: phaseName(elong),
    moon_illumination: Math.round(illum * 1000) / 1000,
    moon_age_days: Math.round(age * 10) / 10,
  };
}
