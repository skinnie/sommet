// GPX route -> TCX course for a Garmin Edge 500 - the TypeScript twin of the desktop's
// tools/edge500_course.py (hardware-checked on a real Edge 500, fw 3.30, 2026-11-05). Pure text in,
// text out; Edge500Usb.ts puts the file in Garmin/NewFiles.
//
// The Edge's own GarminDevice.xml lists "FitnessCourses" as TCX (TrainingCenterDatabase v2),
// InputToUnit, in Garmin/NewFiles. The Edge turns the TCX into Garmin/Courses/<name>_course.fit
// when it leaves USB mode, and it then shows under Training > Courses. One course is thinned to
// MAX_POINTS so a long brevet still loads quickly. A course needs a time per point (for the
// Virtual Partner); like Garmin's own converters the points are laid out at a steady SPEED_KMH
// from a fixed start, so the same route always gives the same file.

import { pyFixed, pyRound } from './Edge500Profile';

export const NAME_MAX = 15;
export const MAX_POINTS = 3000;
const SPEED_KMH = 25.0;
const START_MS = Date.UTC(2010, 0, 1);

export type CoursePoint = [number, number, number | null];   // lat, lon, ele

const NS = '(?:[\\w.-]+:)?';

/** [lat, lon, ele|null] from track points, else route points (tools/edge500_course.py parse_gpx). */
export function parseGpx(text: string): CoursePoint[] {
  for (const tag of ['trkpt', 'rtept']) {
    const pts: CoursePoint[] = [];
    const re = new RegExp(`<${NS}${tag}\\b([^>]*?)(?:/>|>([\\s\\S]*?)</${NS}${tag}\\s*>)`, 'g');
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      const attr = (k: string) => {
        const a = new RegExp(`(?:^|\\s)${k}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`).exec(m![1]);
        return a ? (a[1] ?? a[2]) : undefined;
      };
      const lat = Number(attr('lat')), lon = Number(attr('lon'));
      if (!isFinite(lat) || !isFinite(lon) || attr('lat') === undefined || attr('lon') === undefined) {
        throw new Error(`${tag} without lat/lon`);
      }
      let ele: number | null = null;
      const inner = m[2] ?? '';
      const eleRe = new RegExp(`<${NS}ele\\b[^>]*>([\\s\\S]*?)</${NS}ele\\s*>`, 'g');
      let e: RegExpExecArray | null;
      while ((e = eleRe.exec(inner))) {
        const s = e[1].trim();
        if (!s) continue;
        const v = Number(s);
        ele = isFinite(v) ? v : null;
      }
      pts.push([lat, lon, ele]);
    }
    if (pts.length) return pts;
  }
  return [];
}

export function haversine(a: CoursePoint, b: CoursePoint): number {
  const r = 6371008.8;
  const rad = Math.PI / 180;
  const p1 = a[0] * rad, p2 = b[0] * rad;
  const dp = p2 - p1, dl = (b[1] - a[1]) * rad;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1.0, Math.sqrt(h)));
}

/** At most n points, evenly spread, always keeping the first and last. */
export function thin<T>(pts: T[], n = MAX_POINTS): T[] {
  if (pts.length <= n) return [...pts];
  const step = (pts.length - 1) / (n - 1);
  const out: T[] = [];
  for (let i = 0; i < n; i++) out.push(pts[pyRound(i * step)]);
  return out;
}

/** Python's re.sub(r"[^\w \-]", "", s) with re.UNICODE. */
export const stripName = (s: string) => (s || '').replace(/[^\p{L}\p{N}_ \-]/gu, '');

export function courseName(name: string): string {
  const clean = stripName(name).trim() || 'Sommet';
  return Array.from(clean).slice(0, NAME_MAX).join('').trim();
}

/** The file name the course gets in Garmin/NewFiles. */
export function courseFileName(name: string): string {
  return courseName(name).replace(/[^\p{L}\p{N}_\-]/gu, '_') + '.tcx';
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// datetime(2010,1,1) + timedelta(seconds=t), printed as %Y-%m-%dT%H:%M:%SZ (whole seconds,
// after timedelta's own round-half-even to microseconds).
function ts(t: number): string {
  const x = t * 1e6;
  const ip = Math.trunc(x);
  const us = ip + pyRound(x - ip);
  const sec = (us - (us % 1e6)) / 1e6;
  return new Date(START_MS + sec * 1000).toISOString().slice(0, 19) + 'Z';
}

export function toTcx(points: CoursePoint[], name: string): string {
  if (points.length < 2) throw new Error('a route needs at least 2 points');
  const pts = thin(points);
  const nm = courseName(name);
  const dist = [0.0];
  for (let i = 1; i < pts.length; i++) dist.push(dist[i - 1] + haversine(pts[i - 1], pts[i]));
  const mps = SPEED_KMH / 3.6;
  const total = dist[dist.length - 1];
  const totalS = total / mps;
  const f6 = (v: number) => pyFixed(v, 6), f1 = (v: number) => pyFixed(v, 1);
  const x = ['<?xml version="1.0" encoding="UTF-8"?>',
    '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">',
    `<Folders><Courses><CourseFolder Name="Courses"><CourseNameRef><Id>${esc(nm)}</Id></CourseNameRef>`
      + '</CourseFolder></Courses></Folders>',
    `<Courses><Course><Name>${esc(nm)}</Name>`,
    `<Lap><TotalTimeSeconds>${f1(totalS)}</TotalTimeSeconds><DistanceMeters>${f1(total)}</DistanceMeters>`,
    `<BeginPosition><LatitudeDegrees>${f6(pts[0][0])}</LatitudeDegrees><LongitudeDegrees>${f6(pts[0][1])}`
      + '</LongitudeDegrees></BeginPosition>',
    `<EndPosition><LatitudeDegrees>${f6(pts[pts.length - 1][0])}</LatitudeDegrees><LongitudeDegrees>${f6(pts[pts.length - 1][1])}`
      + '</LongitudeDegrees></EndPosition>',
    '<Intensity>Active</Intensity></Lap>', '<Track>'];
  pts.forEach(([lat, lon, ele], i) => {
    const d = dist[i];
    x.push(`<Trackpoint><Time>${ts(d / mps)}</Time><Position><LatitudeDegrees>${f6(lat)}</LatitudeDegrees>`
      + `<LongitudeDegrees>${f6(lon)}</LongitudeDegrees></Position>`
      + (ele !== null ? `<AltitudeMeters>${f1(ele)}</AltitudeMeters>` : '')
      + `<DistanceMeters>${f1(d)}</DistanceMeters></Trackpoint>`);
  });
  x.push('</Track></Course></Courses></TrainingCenterDatabase>', '');
  return x.join('\n');
}
