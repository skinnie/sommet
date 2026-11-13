// A Traverse Alpha's counted shots reach the app through the move's GPX: <shots> in the
// extensions and one <wpt> of <type>shot</type> per shot that has a position. The GPX below is
// written by hand in the form tools/exercise_log.py gives a Hunting move with two shots.
jest.mock('fast-xml-parser', () => jest.requireActual('fast-xml-parser'));

import { extractGpxMetadata, extractShotPoints } from '../GpxParser';
import { metricRaw, metricValue, ALL_METRICS } from '../ActivityMetrics';

const HUNT = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><time>2026-11-08T06:30:00Z</time></metadata>
  <wpt lat="46.0207000" lon="7.7491000"><time>2026-11-08T06:30:06Z</time><sym>Shot</sym><type>shot</type></wpt>
  <wpt lat="46.0207000" lon="7.7491000"><time>2026-11-08T06:30:07Z</time><sym>Shot</sym><type>shot</type></wpt>
  <wpt lat="60.3000000" lon="25.0000000"><name>Camp</name><type>poi</type></wpt>
  <trk><name>Hunting</name>
    <extensions>
      <duration>12</duration>
      <distance>0</distance>
      <sport_type>98</sport_type>
      <shots>2</shots>
    </extensions>
  <trkseg>
    <trkpt lat="46.0207000" lon="7.7491000"><ele>50.0</ele><time>2026-11-08T06:30:01Z</time></trkpt>
    <trkpt lat="46.0207000" lon="7.7491000"><ele>50.0</ele><time>2026-11-08T06:30:08Z</time></trkpt>
  </trkseg></trk>
</gpx>`;

test('the shot count and each positioned shot are read', () => {
  const meta = extractGpxMetadata(HUNT);
  expect(meta.shots).toBe(2);
  expect(meta.shotPoints).toEqual([
    { latitude: 46.0207, longitude: 7.7491, time: '2026-11-08T06:30:06Z' },
    { latitude: 46.0207, longitude: 7.7491, time: '2026-11-08T06:30:07Z' },
  ]);
  expect(meta.activityType).toBe('Hunting');     // a waypoint never renames the move
});

test('a waypoint that is not a shot is left out', () => {
  expect(extractShotPoints(HUNT)).toHaveLength(2);
});

test('a move without shots has none', () => {
  const plain = HUNT.replace(/<wpt[\s\S]*?<\/wpt>\n/g, '').replace('<shots>2</shots>', '');
  const meta = extractGpxMetadata(plain);
  expect(meta.shots).toBe(0);
  expect(meta.shotPoints).toEqual([]);
});

test('Shots is a list column', () => {
  expect(ALL_METRICS.map(m => m.key)).toContain('shots');
  const m = { shots: 2 } as any;
  expect(metricRaw(m, 'shots')).toBe(2);
  expect(metricValue(m, 'shots')).toBe('2');
  expect(metricValue({ shots: 0 } as any, 'shots')).toBe('');
});
