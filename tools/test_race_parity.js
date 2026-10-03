#!/usr/bin/env node
// Race Plan parity: the Android TypeScript twins (android/src/services/race/*.ts) must give the SAME
// answers as the desktop's Python engine (tools/race_*.py) on the same inputs (André, 2026-09-29:
// Race Plan on Android "everything in one go"). Compiles the twins with the app's own tsc into a temp
// dir, runs each case through the Python tool (its CLI, exactly as the backend calls it) and through
// the twin, and compares the JSON field by field: strings/booleans exactly, numbers to 1e-9 relative.
//
//   node tools/test_race_parity.js [route.gpx|ride.fit ...]
//
// Without arguments it uses the routes it can find (BRM 200 Thierache, the 2026-06-06 BRM600 day FIT...).
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const RACE_SRC = path.join(ROOT, 'android', 'src', 'services', 'race');
const OUT = fs.mkdtempSync(path.join(os.tmpdir(), 'race-parity-'));

// ---- compile the twins -----------------------------------------------------------------------------
const tsc = path.join(ROOT, 'android', 'node_modules', '.bin', 'tsc');
// raceApi.ts is the app glue (AsyncStorage/RNFS), not a twin of a Python module.
const files = fs.readdirSync(RACE_SRC).filter(f => f.endsWith('.ts') && f !== 'raceApi.ts').map(f => path.join(RACE_SRC, f));
execFileSync(tsc, ['--outDir', OUT, '--rootDir', path.join(ROOT, 'android', 'src', 'services'), '--module', 'commonjs',
                   '--target', 'es2020', '--skipLibCheck', '--esModuleInterop', '--strict', 'false', '--noImplicitAny', 'false',
                   '--lib', 'es2020,dom', ...files], { stdio: 'inherit' });
const req = m => require(path.join(OUT, 'race', m));

// ---- routes ----------------------------------------------------------------------------------------
function pointsFromFit(p) {
  const { streamsFromFit } = require(path.join(ROOT, 'shared', 'activity_streams.js'));
  const st = streamsFromFit(new Uint8Array(fs.readFileSync(p)), 3000);
  const s = st.streams, out = [];
  for (let i = 0; i < s.t.length; i++) {
    if (s.lat[i] == null || s.lon[i] == null) continue;
    out.push({ lat: s.lat[i], lon: s.lon[i], ele: s.alt ? s.alt[i] : null });
  }
  return out;
}
function loadRoute(p) {
  if (p.toLowerCase().endsWith('.fit')) return { name: path.basename(p), points: pointsFromFit(p), fit: p };
  return { name: path.basename(p), gpx: fs.readFileSync(p, 'utf8') };
}
const defaults = [
  path.join(os.homedir(), 'Downloads', 'brm200_thierache-22420880-1790285622-203.gpx'),
  path.join(os.homedir(), 'Downloads', 'lh2025v3.oacDzGCh_vXmMdeNH1f7k.gpx'),
  ...fs.readdirSync(path.join(ROOT, '..', '..', 'ambit-app', 'garmin-import')).filter(f => f.startsWith('2026-06-06_06-02-21'))
    .map(f => path.join(ROOT, '..', '..', 'ambit-app', 'garmin-import', f)),
].filter(p => fs.existsSync(p));
const routes = (process.argv.slice(2).length ? process.argv.slice(2) : defaults).map(loadRoute);

// ---- compare ---------------------------------------------------------------------------------------
function diff(a, b, where, out) {
  if (out.length > 12) return;
  if (typeof a === 'number' && typeof b === 'number') {
    if (!(Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a)))) out.push(`${where}: py ${a} vs ts ${b}`);
    return;
  }
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') {
    if (a !== b) out.push(`${where}: py ${JSON.stringify(a)} vs ts ${JSON.stringify(b)}`);
    return;
  }
  if (Array.isArray(a) !== Array.isArray(b)) { out.push(`${where}: type`); return; }
  if (Array.isArray(a)) {
    if (a.length !== b.length) out.push(`${where}: length py ${a.length} vs ts ${b.length}`);
    for (let i = 0; i < Math.min(a.length, b.length); i++) diff(a[i], b[i], `${where}[${i}]`, out);
    return;
  }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (!(k in a)) out.push(`${where}.${k}: missing in py`);
    else if (!(k in b)) out.push(`${where}.${k}: missing in ts`);
    else diff(a[k], b[k], `${where}.${k}`, out);
  }
}

function runPy(tool, body) {
  const f = path.join(OUT, 'body.json');
  fs.writeFileSync(f, JSON.stringify(body));
  const out = execFileSync('python3', [path.join(ROOT, 'tools', tool), f], { maxBuffer: 1 << 28 }).toString().trim().split('\n');
  return JSON.parse(out[out.length - 1]);
}

// In-process Python: `code` is a function body that sees `body` (the JSON) and sets `result`.
function runPyCode(code, body) {
  const f = path.join(OUT, 'body.json');
  fs.writeFileSync(f, JSON.stringify(body));
  const script = `import json, sys\nsys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'tools'))})\n` +
                 `body = json.load(open(${JSON.stringify(f)}))\nresult = None\n${code}\nprint(json.dumps(result))`;
  const out = execFileSync('python3', ['-c', script], { maxBuffer: 1 << 28 }).toString().trim().split('\n');
  return JSON.parse(out[out.length - 1]);
}

let pass = 0, fail = 0;
function brief(x) {
  if (!x || typeof x !== 'object') return JSON.stringify(x);
  const f = [];
  if ('ok' in x) f.push('ok=' + x.ok);
  for (const k of ['windows', 'nights', 'days', 'alerts', 'options', 'controls', 'lines', 'summary']) if (Array.isArray(x[k])) f.push(`${k}=${x[k].length}`);
  if (x.categories) f.push('cats=' + Object.entries(x.categories).map(([k, v]) => `${k}:${(v.pois || []).length}`).join(','));
  if (x.profile) f.push('base=' + x.profile.base_speed_kmh);
  if (x.error) f.push('error=' + x.error);
  return f.join(' ') + ` (${JSON.stringify(x).length} B)`;
}
function report(name, py, ts) {
  if (process.env.V) console.log(`   ${name}: ${brief(py)}`);
  const out = [];
  diff(py, JSON.parse(JSON.stringify(ts)), '', out);
  if (out.length) { fail++; console.log(`FAIL ${name}\n  ` + out.join('\n  ')); }
  else { pass++; console.log(`PASS ${name}`); }
}
function check(name, tool, body, ts) {
  const py = runPy(tool, body);
  if (process.env.V) console.log(`   ${name}: ${brief(py)}`);
  const tj = JSON.parse(JSON.stringify(ts(body)));
  const out = [];
  diff(py, tj, '', out);
  if (out.length) { fail++; console.log(`FAIL ${name}\n  ` + out.join('\n  ')); }
  else { pass++; console.log(`PASS ${name}`); }
}

// ---- cases -----------------------------------------------------------------------------------------
const { planFromBody } = req('raceEvent.js');
const { timelineFromBody } = req('raceTimeline.js');
const { pyRound } = req('pyCompat.js');

// pyRound against Python round() on awkward values (ties, binary near-ties, negatives)
{
  const xs = [0.5, 1.5, 2.5, 2.675, 1.005, -0.5, -2.5, 0.125, 0.375, 12345.6785, 1e-7, 3.14159, 2.5e-5, 0.045, 1234.5];
  const ns = [0, 1, 2, 3];
  const py = JSON.parse(execFileSync('python3', ['-c',
    `import json; xs=${JSON.stringify(xs)}; print(json.dumps([[round(x,n) for n in ${JSON.stringify(ns)}] for x in xs]))`]).toString());
  const ts = xs.map(x => ns.map(n => pyRound(x, n)));
  const out = []; diff(py, ts, 'pyRound', out);
  if (out.length) { fail++; console.log('FAIL pyRound\n  ' + out.join('\n  ')); } else { pass++; console.log('PASS pyRound'); }
}

const athlete = { weight_kg: 86, speed_profile: { base_speed_kmh: 26.7, confidence: 'high', model_source: 'personal', n_recent_rides: 12 } };
for (const r of routes) {
  const ev = (extra) => Object.assign({ name: r.name, event_type: 'BRM', start_dt: '2026-06-06T06:00:00',
                                        gpx: r.gpx || null, points: r.points || [] }, extra || {});
  // plan/create
  check(`${r.name} plan (placeholder)`, 'race_event.py', { event: ev() }, planFromBody);
  check(`${r.name} plan (personal)`, 'race_event.py', { event: ev(), athlete, bike: { bike_weight_kg: 9 } }, planFromBody);

  // the route length, for controls
  const tl0 = timelineFromBody({ event: ev(), athlete });
  const km = tl0.timeline.distance_km;
  const ctrls = [0.23, 0.48, 0.71].map((f, i) => ({
    label: `C${i + 1}`, distance_km: Math.round(km * f * 10) / 10,
    cutoff_dt: `2026-06-0${6 + Math.floor((6 + km * f / 15) / 24)}T${String(Math.floor((6 + km * f / 15) % 24)).padStart(2, '0')}:30:00`,
    open_dt: `2026-06-06T${String(Math.min(23, Math.floor(6 + km * f / 32))).padStart(2, '0')}:10:00`,
  }));
  ctrls.push({ label: 'Finish', distance_km: km, cutoff_dt: '2026-06-08T00:00:00', open_dt: null });

  const cases = {
    'bare': {},
    'controls+stops_s': { event: ev({ cutoffs: ctrls }), stops_s: [1200, 900, 1500, 0] },
    'stop_total_s': { event: ev({ cutoffs: ctrls }), stop_total_s: 5.5 * 3600 },
    'stop_profile+overrides': { event: ev({ cutoffs: ctrls }), stop_profile: { ratio: 0.21, source: 'personal' }, control_overrides: { 1: 2700 } },
    'no controls, stop_total': { stop_total_s: 4 * 3600 },
    'sleep windows+events': { event: ev({ cutoffs: ctrls }), stop_total_s: 3 * 3600,
      sleep_windows: [{ km: km * 0.6, duration_s: 3.5 * 3600 }, { km: km * 0.3, duration_s: 1200, nap: true }],
      stop_events: [{ km: km * 0.4, duration_s: 1800 }, { km: km * 0.05, duration_s: 600 }] },
    'wind headwind': { event: ev({ cutoffs: ctrls }), stop_total_s: 2 * 3600, wind_speed_delta_kmh: -1.7 },
    'wind tailwind': { stop_total_s: 2 * 3600, wind_speed_delta_kmh: 2.3 },
    'fatigue linear': { event: ev({ cutoffs: ctrls }), stop_total_s: 6 * 3600, fatigue: { enabled: true, awake_at_start_s: 7200 },
      sleep_windows: [{ km: km * 0.55, duration_s: 3 * 3600 }] },
    'fatigue twoprocess': { event: ev({ cutoffs: ctrls }), stop_total_s: 6 * 3600,
      fatigue: { enabled: true, model: 'twoprocess', bed_h: 22.0, wake_h: 6.0 } },
    'no-ride 00-03': { event: ev({ cutoffs: ctrls }), stop_total_s: 4 * 3600, no_ride: { start_h: 0, end_h: 3 } },
    'curfew 21-06 + twoprocess': { stop_total_s: 3 * 3600, no_ride: { start_h: 21, end_h: 6 },
      fatigue: { enabled: true, model: 'twoprocess', bed_h: 23.5, wake_h: 7.25 } },
    'lump sleep': { event: ev({ cutoffs: ctrls }), stops_s: [0, 0, 0, 0], sleep: { enabled: true, duration_s: 5400 } },
  };
  for (const [name, extra] of Object.entries(cases)) {
    const body = Object.assign({ event: ev(), athlete }, extra);
    check(`${r.name} timeline ${name}`, 'race_timeline.py', body, timelineFromBody);
  }
}

// ---- the other modules (async: weather is) ---------------------------------------------------------
const { importRoadbook } = req('roadbookImport.js');
const stopmem = req('raceStopmem.js');
const scen = req('raceScenarios.js');
const { daysFromBody } = req('raceDays.js');
const { sleepFromBody } = req('raceSleep.js');
const { weatherFromBody } = req('raceWeather.js');
const { alertsFromBody } = req('raceAlerts.js');
const { poisFromBody } = req('racePois.js');
const { suggestSleep } = req('raceSleepopt.js');
const { calibrateFitSummary } = req('raceCalibration.js');

// A PitStopper-style export laid along a route: every category key, hours, loop passes, placeholder
// and decorated names, entities, a custom tag - deterministic.
function syntheticPitstopper(points) {
  const kinds = [
    ['water', 'Drinking Water', 'Drinking Water', 'drinking_w'], ['food', 'Restaurants. Hours: Mo-Sa 08:30-19:30; Su off. Website: x', 'Restaurant', 'Chez Paul'],
    ['coffee', 'Cafes', 'Restaurant', '^Café du Coin L20m'], ['shopping', 'Supermarkets. Hours: 24/7', 'Shopping Center', 'Norma'],
    ['generic', 'POI', 'Dot', 'POI3 R12m'], ['lodging', 'Full name: Le Grand Hôtel &amp; Spa. Hotels', 'Lodging', 'Le Grand H'],
    ['camping', 'Campgrounds', 'Campground', 'camp_site'], ['bike_shop', 'Bicycle Repair', 'Car Repair', 'Cycles Pro R139m'],
    ['bike_parking', 'Bicycle Parking', 'Parking Area', 'bicycle_pa'], ['restroom', 'Toilets', 'Restroom', 'toilets'],
    ['gas', 'Gas Stations. Hours: Mo-Su 06:00-22:00', 'Gas Station', 'Total'], ['bar', 'Pubs. Hours: Mo-Su 18:00-02:00', 'Restaurant', 'Le Pub'],
    ['convenience_store', 'Convenience Stores. Hours: Mo-Fr 07:00-12:00,14:00-19:00', 'Convenience Store', 'Spar'],
    ['atm', 'ATMs', 'Bank', 'atm'], ['water', 'Non-potable Water', 'Drinking Water', 'Tap'], ['generic', 'Castles', 'Dot', 'Château'],
    ['shopping', 'Clothing', 'Shopping Center', 'Clothes'], ['generic', 'Alpine Huts', 'Dot', 'Refuge'],
  ];
  const out = [];
  const step = Math.max(1, Math.floor(points.length / 90));
  for (let i = 0, k = 0; i < points.length; i += step, k++) {
    const [key, desc, sym, name] = kinds[k % kinds.length];
    const p = points[i];
    const loop = k % 11 === 5 ? `. Multi-pass POI: encountered 2 times. Outbound at ${(k * 3.1).toFixed(2)}km, Return at ${(k * 3.1 + 40).toFixed(2)}km` : '';
    out.push(`<wpt lat="${(p.lat + ((k % 3) - 1) * 0.0007).toFixed(6)}" lon="${(p.lon + ((k % 5) - 2) * 0.0005).toFixed(6)}">` +
             `<name>${name}</name><cmt>${key}${loop}</cmt><desc>${desc}</desc><sym>${sym}</sym></wpt>`);
  }
  return `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">${out.join('\n')}</gpx>`;
}

(async () => {
  const pyTools = JSON.stringify(path.join(ROOT, 'tools'));
  // roadbook: the Python selftest sample + variants (CP / Control / h-times / dots)
  const sample = runPyCode('import roadbook_import as r\nresult = r._SAMPLE', {});
  const variants = [sample,
    'CP1 : ARRAS          12.5    54.0   7h32   9h40\nCP2 - DOULLENS  D925  33,0   87.0   9:05   12h52\nBAPAUME   10,0\nControl 3 - AMIENS  40.2 127.2 10:21 15:28\nC4-ARRIVEE   0,0 200,1  12:12  19h30\n',
    'nothing here\nC1 - X 12 13 no times\n',
    // PDF text: minus sign / em dash separators, a wrapped name (times on the next line, with and
    // without a trailing hyphen), a control with no times followed by another control, form feeds
    'C1 \u2212 HIRSON  62,4  121,5  9:40  14:10\fC2 \u2014 VERDUN  341,0  15:44  6:10\n' +
      'C3 - CH\u00c2LONS-\n  EN-CHAMPAGNE    Mairie    143,2   264,7   13:21   2:39\n' +
      'C4 - SAINTE\n MENEHOULD   Caf\u00e9   40,0  304,7  14:30  4:40\nC5 - NO TIME\nC6 - METZ  403,5  17:44  10:54\n'];
  for (const [i, t] of variants.entries()) check(`roadbook variant ${i}`, 'roadbook_import.py', { text: t }, importRoadbook);
  check('roadbook empty', 'roadbook_import.py', {}, importRoadbook);

  // stop memory: the same sequence of records / suggestions on both sides
  const ops = [['s', 200], ['r', 205, 0.8], ['s', 400], ['r', 600, 8.0], ['s', 400], ['s', 590], ['r', 612, 9.0], ['r', 310, 2.0],
               ['s', 150], ['s', 1300], ['s', 305], ['r', 1000, 15.25]];
  const pySm = runPyCode(`import race_stopmem as m, tempfile, os\np = os.path.join(tempfile.mkdtemp(), 's.json')\nresult = []\nfor op in body['ops']:\n    result.append(m.suggest(op[1], p) if op[0] == 's' else m.record(op[1], op[2], p))\nresult.append(m._load(p))`, { ops });
  let store = { points: [] }; const tsSm = [];
  for (const op of ops) { if (op[0] === 's') tsSm.push(stopmem.suggest(op[1], store)); else { const [r, st] = stopmem.record(op[1], op[2], store); tsSm.push(r); store = st; } }
  tsSm.push(store);
  report('stopmem sequence', pySm, tsSm);

  // scenarios (saved_at is the clock - compared without it)
  const sops = [['save', 'Baseline', { baseSpeed: '26' }, { elapsed_time_s: 111600 }], ['save', 'Less sleep', { a: 1 }, { e: 2 }],
                ['save', 'Baseline', { baseSpeed: '27' }, { e: 3 }], ['list'], ['get', 'Baseline'], ['get', 'Nope'], ['delete', 'Less sleep'], ['list'], ['save', '  ', {}, {}]];
  const strip = x => JSON.parse(JSON.stringify(x, (k, v) => (k === 'saved_at' ? undefined : v)));
  const pySc = runPyCode(`import race_scenarios as m, tempfile, os\np = os.path.join(tempfile.mkdtemp(), 's.json')\nresult = []\nfor op in body['ops']:\n    if op[0] == 'save': result.append(m.save(op[1], op[2], op[3], p))\n    elif op[0] == 'list': result.append(m.listing(p))\n    elif op[0] == 'get': result.append(m.get(op[1], p))\n    else: result.append(m.delete(op[1], p))`, { ops: sops });
  let sst = { scenarios: [] }; const tsSc = [];
  for (const op of sops) {
    if (op[0] === 'save') { const [r, st] = scen.save(op[1], op[2], op[3], sst, '2026-09-29T10:00:00'); tsSc.push(r); sst = st; }
    else if (op[0] === 'list') tsSc.push(scen.listing(sst));
    else if (op[0] === 'get') tsSc.push(scen.get(op[1], sst));
    else { const [r, st] = scen.remove(op[1], sst); tsSc.push(r); sst = st; }
  }
  report('scenarios sequence', strip(pySc), strip(tsSc));

  for (const r of routes) {
    const pts = r.points && r.points.length ? r.points : runPyCode('import geo_util\nresult = geo_util.parse_gpx_points(body["gpx"])', { gpx: r.gpx });
    const ev = (extra) => Object.assign({ name: r.name, event_type: 'BRM', start_dt: '2026-06-06T21:00:00', points: pts }, extra || {});
    const base = timelineFromBody({ event: ev(), athlete }).timeline;
    const km = base.distance_km;
    const ctrls = [0.3, 0.62].map((f, i) => ({ label: `C${i + 1}`, distance_km: Math.round(km * f * 10) / 10,
      cutoff_dt: `2026-06-0${7 + Math.floor((21 + km * f / 16) / 24) - 1}T${String(Math.floor((21 + km * f / 16) % 24)).padStart(2, '0')}:00:00`, open_dt: null }));
    ctrls.push({ label: 'Finish', distance_km: km, cutoff_dt: '2026-06-09T12:00:00', open_dt: null });
    const tlBodies = {
      curfew: { event: ev({ cutoffs: ctrls }), athlete, stop_total_s: 3 * 3600, no_ride: { start_h: 23, end_h: 5 } },
      planned: { event: ev({ cutoffs: ctrls }), athlete, stop_total_s: 2 * 3600, sleep_windows: [{ km: km * 0.45, duration_s: 4 * 3600 }] },
      plain: { event: ev({ cutoffs: ctrls }), athlete, stop_total_s: 2 * 3600 },
    };
    const shelter = { categories: { shelter: { pois: [{ name: 'Hotel A', km: pyRound(km * 0.2, 1), kind: 'Hotel' },
      { name: 'Camp B', km: pyRound(km * 0.5, 1), kind: 'Camp' }, { name: 'Gîte', km: pyRound(km * 0.52, 1), subtype: 'lodging' }] } } };
    for (const [tn, tb] of Object.entries(tlBodies)) {
      const tl = runPy('race_timeline.py', tb).timeline;
      check(`${r.name} days ${tn}`, 'race_days.py', { timeline: tl, pois: shelter, reach_km: 25 }, daysFromBody);
      check(`${r.name} days ${tn} no pois`, 'race_days.py', { timeline: tl }, daysFromBody);
      const sb = { points: pts, controls: tl.controls, start_dt: tl.start_dt, tz: 2.0, suggested_total_s: tl.sleep_suggested_s || 3 * 3600,
                   weather: [{ km: 0, temp_c: 14 }, { km: km * 0.5, temp_c: 6.4 }, { km, temp_c: 9 }] };
      check(`${r.name} sleep ${tn}`, 'race_sleep.py', sb, sleepFromBody);
      // weather at controls with a fixed forecast (no network) on both sides
      const wb = { points: pts, controls: tl.controls, tz: 2.0 };
      const pyW = runPyCode(`import race_weather\ndef fx(samples):\n    return [{"temp_c": 20.0 - k * 1.7 + s["eta_dt"].hour * 0.1, "feels_c": 18.5 - k, "rain_mm": 0.35 * (k % 2), "wind_kmh": 8.0 + k * 3.3, "wind_dir_deg": (37.0 * k + s["eta_dt"].hour * 5) % 360} for k, s in enumerate(samples)]\nresult = race_weather.race_weather(body["controls"], points=body["points"], tz_offset_h=body["tz"], fetch=fx)`, wb);
      const fx = samples => samples.map((sm, k) => ({ temp_c: 20.0 - k * 1.7 + sm.eta_dt.getUTCHours() * 0.1, feels_c: 18.5 - k, rain_mm: 0.35 * (k % 2),
                                                      wind_kmh: 8.0 + k * 3.3, wind_dir_deg: (37.0 * k + sm.eta_dt.getUTCHours() * 5) % 360 }));
      const tsW = await weatherFromBody(wb, fx);
      report(`${r.name} weather ${tn}`, pyW, tsW);
      // POIs along this route, then alerts from everything
      const poiGpx = syntheticPitstopper(pts);
      for (const [pn, extra] of Object.entries({ fwd: {}, reverse: { reverse: true }, water_tag: { custom_tag: 'water', water_l_per_100km: 3.5, carry_l: 2.0 } })) {
        check(`${r.name} pois ${tn} ${pn}`, 'race_pois.py', Object.assign({ points: pts, poi_gpx: poiGpx }, extra), poisFromBody);
      }
      const pois = runPy('race_pois.py', { points: pts, poi_gpx: poiGpx });
      const eta = [{ km: 0, dt: tl.start_dt }, ...tl.controls.map(c => ({ km: c.distance_km, dt: c.arrival_dt }))];
      check(`${r.name} open hours ${tn}`, 'race_pois.py', { pois, eta }, poisFromBody);
      check(`${r.name} alerts ${tn}`, 'race_alerts.py', { points: pts, timeline: tl, weather: pyW, pois }, alertsFromBody);
    }
    check(`${r.name} alerts route only`, 'race_alerts.py', { gpx: r.gpx || undefined, points: r.gpx ? undefined : pts }, alertsFromBody);
    // sleep optimiser (fewer candidates to keep the test short)
    const beds = [22.0, 0.0, 1.5], lengths = [1.5, 4.5];
    const sob = { event: ev({ cutoffs: ctrls }), athlete, stop_total_s: 3 * 3600, habit: { bed_h: 22.5, wake_h: 6.5 }, pois: shelter };
    const pySo = runPyCode(`import race_sleepopt as m\nm.BEDS_H, m.LENGTHS_H = ${JSON.stringify(beds)}, ${JSON.stringify(lengths)}\nresult = m.suggest(body)`, sob);
    report(`${r.name} sleep options`, pySo, suggestSleep(sob, beds, lengths));
    // one-FIT calibration (FIT routes only): tools/fit_decode vs the app's decoder
    if (r.fit) {
      const { streamsFromFit } = require(path.join(ROOT, 'shared', 'activity_streams.js'));
      const st = streamsFromFit(new Uint8Array(fs.readFileSync(r.fit)), 2000);
      check(`${r.name} calibrate`, 'race_calibrate.py', { fit_path: r.fit }, () => calibrateFitSummary(st.summary));
    }
  }
  // the Python selftest's own PitStopper sample on its synthetic route
  const selfGpx = runPyCode('import race_pois as m\nresult = m._synthetic_gpx()', {});
  const selfRoute = runPyCode('import race_pois as m\nresult = m._synthetic_route()', {});
  for (const tag of ['cemetery', 'water', 'other']) check(`pois selftest sample (${tag})`, 'race_pois.py', { points: selfRoute, poi_gpx: selfGpx, custom_tag: tag }, poisFromBody);
  check('pois selftest reversed', 'race_pois.py', { points: selfRoute, poi_gpx: selfGpx, reverse: true }, poisFromBody);

  console.log(`\n${pass} passed, ${fail} failed`);
  fs.rmSync(OUT, { recursive: true, force: true });
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
