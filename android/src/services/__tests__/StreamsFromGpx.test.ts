// A move with no FIT (read from an Ambit) gets its Charts from its own GPX track (2026-11-19).
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { streamsFromGpx } = require('../../config/activityStreams');

function gpx(points: string[]): string {
  return '<?xml version="1.0"?><gpx xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
    + points.join('') + '</trkseg></trk></gpx>';
}

// One point a second, 0.0001 degree of latitude apart (about 11.1 m/s), written the way the
// Android watch reader writes them (jni_bridge.cpp): atemp, hr, cad, then power.
function pt(i: number, sensors = true): string {
  const ext = sensors
    ? `<extensions><gpxtpx:TrackPointExtension><gpxtpx:atemp>21.5</gpxtpx:atemp><gpxtpx:hr>${120 + i}</gpxtpx:hr>`
      + `<gpxtpx:cad>80</gpxtpx:cad></gpxtpx:TrackPointExtension><power>200</power></extensions>`
    : '';
  const t = new Date(Date.UTC(2026, 9, 9, 13, 0, i)).toISOString();
  return `<trkpt lat="${(45 + i * 0.0001).toFixed(7)}" lon="6.0000000"><ele>${100 + i}.0</ele><time>${t}</time>${ext}</trkpt>`;
}

describe('streamsFromGpx', () => {
  it('gives time, distance, speed, altitude and the sensor channels from the track', () => {
    const o = streamsFromGpx(gpx(Array.from({ length: 61 }, (_, i) => pt(i))), 2000, null);
    expect(o.ok).toBe(true);
    expect(o.from_track).toBe(true);
    expect(o.records).toBe(61);
    expect(o.channels).toEqual(expect.arrayContaining(['dist', 'v', 'alt', 'hr', 'cad', 'pw', 'temp', 'lat', 'lon']));
    expect(o.summary.elapsed_s).toBe(60);
    expect(o.summary.dist_m).toBeGreaterThan(660);
    expect(o.summary.dist_m).toBeLessThan(675);
    expect(o.streams.v[30]).toBeCloseTo(11.1, 0);
    expect(o.streams.alt[0]).toBeCloseTo(100, 0);
    expect(o.streams.hr[10]).toBe(130);
    expect(o.streams.cad[10]).toBe(80);
    expect(o.streams.pw[10]).toBe(200);
    expect(o.streams.temp[10]).toBe(22);
  });

  it('doubles cadence for a move on foot, as the FIT path does', () => {
    const o = streamsFromGpx(gpx(Array.from({ length: 5 }, (_, i) => pt(i))), 2000, 1);
    expect(o.streams.cad[2]).toBe(160);
  });

  it('works on a track with positions only', () => {
    const o = streamsFromGpx(gpx(Array.from({ length: 5 }, (_, i) => pt(i, false))), 2000, null);
    expect(o.channels).not.toContain('hr');
    expect(o.records).toBe(5);
  });

  it('has no records when the track has no times', () => {
    const o = streamsFromGpx(gpx(['<trkpt lat="45" lon="6"><ele>1</ele></trkpt>']), 2000, null);
    expect(o.records).toBe(0);
  });
});
