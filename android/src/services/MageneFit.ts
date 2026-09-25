// Minimal FIT reader for bike-computer rides - the TypeScript port of the desktop's
// tools/fit_decode.py (same field numbers, scales, invalid sentinels, developer-field skipping,
// and the sport/sub_sport naming that splits a Magene outdoor ride "Cycling" from a training
// ride "Indoor cycling"). Produces the session summary + track points, and a GPX carrying the
// summary as the extension tags GpxParser.extractGpxMetadata reads (<duration>, <distance>,
// <energy>, <avg_hr>, ...), so an indoor ride with no GPS still shows its real totals.

const FIT_EPOCH = 631065600;
const SEMI = 180 / 2 ** 31;

// base type -> [size, signed/float kind, invalid sentinel]
type Kind = 'u' | 's' | 'f' | 'str';
const BASE: Record<number, [number, Kind, number | null]> = {
  0x00: [1, 'u', 0xff], 0x01: [1, 's', 0x7f], 0x02: [1, 'u', 0xff], 0x83: [2, 's', 0x7fff],
  0x84: [2, 'u', 0xffff], 0x85: [4, 's', 0x7fffffff], 0x86: [4, 'u', 0xffffffff], 0x07: [1, 'str', null],
  0x88: [4, 'f', null], 0x89: [8, 'f', null], 0x0a: [1, 'u', 0x00], 0x8b: [2, 'u', 0x0000],
  0x8c: [4, 'u', 0x00000000], 0x0d: [1, 'u', 0xff],
};

const SPORT: Record<number, string> = {
  0: 'Activity', 1: 'Running', 2: 'Cycling', 5: 'Swimming', 11: 'Walking', 17: 'Hiking',
  18: 'Multisport', 25: 'Indoor cycling',
};
const SUB_SPORT: Record<string, string> = {
  '2,5': 'Indoor cycling', '2,6': 'Indoor cycling', '2,58': 'Indoor cycling',
  '2,8': 'Mountain biking', '1,1': 'Treadmill',
};

export interface FitPoint { t: number; lat?: number; lon?: number; ele?: number }
export interface FitRide {
  sport: string;
  startTime: string | null;
  durationSeconds: number;
  movingSeconds: number;
  distanceMeters: number;
  ascentMeters: number;
  descentMeters: number;
  energyKcal: number;
  avgHr: number; maxHr: number; avgCadence: number; maxCadence: number;
  points: FitPoint[];
}

function value(d: DataView, o: number, size: number, base: number, little: boolean): number | null {
  const bt = BASE[base];
  const [esize, kind, invalid] = bt ?? [size, 'u' as Kind, null];
  if (kind === 'str' || size < esize) return null;
  let v: number;
  if (kind === 'f') v = esize === 4 ? d.getFloat32(o, little) : d.getFloat64(o, little);
  else if (esize === 1) v = kind === 's' ? d.getInt8(o) : d.getUint8(o);
  else if (esize === 2) v = kind === 's' ? d.getInt16(o, little) : d.getUint16(o, little);
  else if (esize === 4) v = kind === 's' ? d.getInt32(o, little) : d.getUint32(o, little);
  else return null;
  if (invalid !== null && v === invalid) return null;
  return v;
}

const iso = (fitSeconds: number | null | undefined) =>
  fitSeconds == null ? null : new Date((fitSeconds + FIT_EPOCH) * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');

export function decodeFit(fit: Uint8Array): FitRide {
  if (fit.length < 14 || String.fromCharCode(...fit.subarray(8, 12)) !== '.FIT') throw new Error('not a FIT file');
  const dv = new DataView(fit.buffer, fit.byteOffset, fit.byteLength);
  const headerSize = fit[0];
  const end = Math.min(fit.length, headerSize + dv.getUint32(4, true));
  const defs: Record<number, { g: number; little: boolean; fields: [number, number, number][]; dev: number }> = {};
  const points: FitPoint[] = [];
  const s: Record<string, number> = {};
  let lastTs: number | null = null;
  let i = headerSize;

  const readData = (local: number, compTs: number | null): number | null => {
    const def = defs[local];
    if (!def) return null;
    const vals: Record<number, number | null> = {};
    for (const [num, size, base] of def.fields) {
      vals[num] = i + size <= end ? value(dv, i, size, base, def.little) : null;
      i += size;
    }
    i += def.dev;
    const ts: number | null = vals[253] ?? null;
    if (def.g === 20) {
      const lat = vals[0], lon = vals[1];
      if (lat != null && lon != null) {
        const p: FitPoint = { t: (ts ?? compTs) ?? 0, lat: lat * SEMI, lon: lon * SEMI };
        const ele = vals[78] != null ? vals[78]! / 5 - 500 : vals[2] != null ? vals[2]! / 5 - 500 : undefined;
        if (ele !== undefined) p.ele = ele;
        points.push(p);
      }
    } else if (def.g === 18) {
      const map: [number, string, number][] = [
        [5, 'sport', 1], [6, 'sub', 1], [2, 'start', 1], [7, 'elapsed', 1000], [8, 'timer', 1000],
        [9, 'dist', 100], [11, 'kcal', 1], [22, 'asc', 1], [23, 'desc', 1],
        [16, 'avgHr', 1], [17, 'maxHr', 1], [18, 'avgCad', 1], [19, 'maxCad', 1],
      ];
      for (const [f, k, scale] of map) if (vals[f] != null) s[k] = vals[f]! / scale;
    }
    return ts;
  };

  while (i < end) {
    const hdr = fit[i++];
    if (hdr & 0x80) {
      const local = (hdr >> 5) & 0x03, off = hdr & 0x1f;
      if (lastTs !== null) {
        let ts: number = (lastTs & ~0x1f) | off;
        if (off < (lastTs & 0x1f)) ts += 0x20;
        lastTs = ts;
      }
      readData(local, lastTs);
    } else if (hdr & 0x40) {
      const local = hdr & 0x0f;
      i++; // reserved
      const little = fit[i++] === 0;
      const g = little ? fit[i] | (fit[i + 1] << 8) : (fit[i] << 8) | fit[i + 1];
      i += 2;
      const n = fit[i++];
      const fields: [number, number, number][] = [];
      for (let k = 0; k < n; k++) { fields.push([fit[i], fit[i + 1], fit[i + 2]]); i += 3; }
      let dev = 0;
      if (hdr & 0x20) { const nd = fit[i++]; for (let k = 0; k < nd; k++) { dev += fit[i + 1]; i += 3; } }
      defs[local] = { g, little, fields, dev };
    } else {
      const ts = readData(hdr & 0x0f, lastTs);
      if (ts !== null) lastTs = ts;
    }
  }

  const start = s.start ?? (points[0]?.t ?? null);
  const sport = SUB_SPORT[`${s.sport},${s.sub}`] ?? SPORT[s.sport as number] ?? 'Activity';
  return {
    sport,
    startTime: iso(start),
    durationSeconds: Math.trunc(s.elapsed || 0),
    movingSeconds: Math.trunc(s.timer || 0),
    distanceMeters: s.dist || 0,
    ascentMeters: s.asc || 0,
    descentMeters: s.desc || 0,
    energyKcal: Math.trunc(s.kcal || 0),
    avgHr: s.avgHr || 0, maxHr: s.maxHr || 0, avgCadence: s.avgCad || 0, maxCadence: s.maxCad || 0,
    points,
  };
}

export function rideToGpx(r: FitRide, creator = 'Sommet (Magene C406)'): string {
  const ext = [
    ['duration', r.durationSeconds], ['distance', Math.round(r.distanceMeters)], ['energy', r.energyKcal],
    ['avg_hr', r.avgHr], ['max_hr', r.maxHr], ['avg_cadence', r.avgCadence], ['max_cadence', r.maxCadence],
    ['descent', r.descentMeters],
  ].filter(([, v]) => (v as number) > 0).map(([k, v]) => `<${k}>${v}</${k}>`).join('');
  const pts = r.points.map(p => {
    const ele = p.ele !== undefined ? `<ele>${p.ele.toFixed(1)}</ele>` : '';
    const t = p.t ? `<time>${iso(p.t)}</time>` : '';
    return `<trkpt lat="${p.lat!.toFixed(7)}" lon="${p.lon!.toFixed(7)}">${ele}${t}</trkpt>`;
  });
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<gpx version="1.1" creator="${creator}" xmlns="http://www.topografix.com/GPX/1/1">`,
    r.startTime ? `<metadata><time>${r.startTime}</time></metadata>` : '',
    '<trk>', `<name>${r.sport}</name>`, ext ? `<extensions>${ext}</extensions>` : '',
    '<trkseg>', ...pts, '</trkseg>', '</trk>', '</gpx>',
  ].filter(Boolean).join('\n');
}
