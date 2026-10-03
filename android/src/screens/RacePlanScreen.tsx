import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, TextInput, ScrollView, StyleSheet, TouchableOpacity, Modal, Pressable, Share, Linking } from 'react-native';
import RNFS from 'react-native-fs';
import { useV3Theme, v3Spacing, v3Type, v3Radius } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, Toggle, Dropdown } from '../components/ui/primitives';
import { pickGpxFile } from '../native/AmbitUsbModule';
import { PdfTextReader, PdfTextResult } from '../components/PdfTextReader';
import { raceApi, getCustomTag, setCustomTag, loadSession, saveSession } from '../services/race/raceApi';

// Race Plan on Android (André, 2026-09-29: "everything in one go") - a port of the desktop's
// RacePlanPage.qml, call for call: the same six questions one by one, then the plan (timeline, cutoffs,
// weather, sleep, days & nights, resupply, critical points, what-if, saved scenarios). The maths are the
// TypeScript twins of tools/race_*.py (services/race, parity-tested against the Python); raceApi answers
// the calls the desktop sends to its backend. Water/food/cemetery stops come from a PitStopper export
// loaded AS the route, exactly like the desktop.

const GOOD = '#2e9e6b', BAD = '#d6453f', AMBER = '#e0912f';
const CUSTOM_TAGS = ['cemetery', 'water', 'food', 'other'];
const CUSTOM_TAG_LABELS = ['Cemetery (likely water)', 'Water point', 'Food or drink', 'Something else (not a refill)'];
const STEP_TITLES = ['Your route', 'When do you start?', 'How fast do you ride?', 'Time off the bike',
  'Hours you never ride', 'Checkpoints & time limits'];

interface Ctrl { label: string; km: string; hours: string; opens: string }
interface PStop { label: string; km: string; min: string }

const p2 = (n: number) => (n < 10 ? '0' : '') + n;
function hhmmToHours(t: string, dflt: number) {
  const m = /^\s*(\d{1,2})(?::(\d{2}))?\s*$/.exec(t || '');
  if (!m) return dflt;
  const h = parseInt(m[1], 10), mi = m[2] ? parseInt(m[2], 10) : 0;
  return h < 24 && mi < 60 ? h + mi / 60 : dflt;
}
function fmtDur(sec: number | null | undefined) {
  if (sec === null || sec === undefined) return '—';
  const neg = sec < 0; sec = Math.abs(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return (neg ? '-' : '') + h + 'h' + (m < 10 ? '0' + m : m);
}
// Engine times are local-naive ISO ("YYYY-MM-DDTHH:MM:SS[.ffffff]"): read the wall clock off the text.
const fmtClock = (iso?: string | null) => (iso ? iso.slice(11, 16) : '—');
function fmtClockDay(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)));
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()] + ' ' + iso.slice(11, 16);
}
function toLocalIso(dt: Date) {
  return `${dt.getFullYear()}-${p2(dt.getMonth() + 1)}-${p2(dt.getDate())}T${p2(dt.getHours())}:${p2(dt.getMinutes())}:${p2(dt.getSeconds())}`;
}
/** "HH:MM" -> first Date strictly after afterMs with that clock time (multi-day cutoffs, monotonic). */
function clockToDt(afterMs: number, hhmm: string) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || '');
  if (!m) return null;
  const hh = parseInt(m[1], 10), mm = parseInt(m[2], 10);
  if (hh > 23 || mm > 59) return null;
  const b = new Date(afterMs);
  let d = new Date(b.getFullYear(), b.getMonth(), b.getDate(), hh, mm, 0);
  while (d.getTime() <= afterMs) d = new Date(d.getTime() + 86400000);
  return d;
}
const today = () => { const d = new Date(); return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`; };


// ---- small building blocks (module scope: stable identities, so a TextInput keeps its focus) ------
function Cap({ children, color, bold, style }: any) {
  const t = useV3Theme();
  return <Text style={[{ fontSize: v3Type.caption, color: color || t.mutedText, fontWeight: bold ? '700' : '400' }, style]}>{children}</Text>;
}
function Body({ children, color, bold, size, style }: any) {
  const t = useV3Theme();
  return <Text style={[{ fontSize: size || v3Type.body, color: color || t.text, fontWeight: bold ? '700' : '400' }, style]}>{children}</Text>;
}
function Field({ value, onChange, placeholder, width, numeric, onEnd }: any) {
  const t = useV3Theme();
  return <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={t.mutedText}
    keyboardType={numeric ? 'decimal-pad' : 'default'} onEndEditing={onEnd}
    style={[{ borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text, backgroundColor: t.card,
              paddingHorizontal: 10, paddingVertical: 6, fontSize: v3Type.body }, width ? { width } : { flex: 1 }]} />;
}
function Divider() {
  const t = useV3Theme();
  return <View style={{ height: 1, backgroundColor: t.border, marginVertical: v3Spacing.small }} />;
}
function Stat({ label, value, color, big }: any) {
  return (
    <View style={{ marginRight: v3Spacing.large, marginBottom: 6 }}>
      <Cap>{label}</Cap>
      <Body size={big ? v3Type.title : v3Type.subtitle} bold={big} color={color}>{value}</Body>
    </View>);
}
function Check({ value, onChange, label }: any) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <Body style={{ flex: 1, marginRight: 12 }}>{label}</Body>
      <Toggle value={value} onValueChange={onChange} />
    </View>);
}

export default function RacePlanScreen() {
  const t = useV3Theme();
  const s = styles(t);
  const scrollRef = useRef<ScrollView>(null);

  // ---- answers -----------------------------------------------------------------------------------
  const [gpxText, setGpxText] = useState('');
  const [gpxName, setGpxName] = useState('');
  const [startDate, setStartDate] = useState(today());
  const [startTime, setStartTime] = useState('06:00');
  const [eventName, setEventName] = useState('');
  const [baseSpeed, setBaseSpeed] = useState('');
  const [calibratedProfile, setCalibratedProfile] = useState<any>(null);
  const [calibNote, setCalibNote] = useState('');
  const [stopTotalH, setStopTotalH] = useState('');
  const [stopUserSet, setStopUserSet] = useState(false);
  const [sleepH, setSleepH] = useState('');
  const [fatigueOn, setFatigueOn] = useState(false);
  const [usualBed, setUsualBed] = useState('22:00');
  const [usualWake, setUsualWake] = useState('06:00');
  const [noRideOn, setNoRideOn] = useState(false);
  const [noRideFrom, setNoRideFrom] = useState('23:00');
  const [noRideTo, setNoRideTo] = useState('03:30');
  const [controls, setControls] = useState<Ctrl[]>([]);
  const [plannedStops, setPlannedStops] = useState<PStop[]>([]);
  const [controlOverrides, setControlOverrides] = useState<Record<string, number>>({});
  const [windFold, setWindFold] = useState(false);
  const [customTag, setCustomTagState] = useState('cemetery');
  useEffect(() => { getCustomTag().then(setCustomTagState); }, []);

  // ---- results -----------------------------------------------------------------------------------
  const [step, setStep] = useState(0);
  const [showResults, setShowResults] = useState(false);
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [routeKm, setRouteKm] = useState(0);
  const [timeline, setTimeline] = useState<any>(null);
  const [basePayload, setBasePayload] = useState<any>(null);
  const [weather, setWeather] = useState<any>(null);
  const [sleepPlan, setSleepPlan] = useState<any>(null);
  const [alerts, setAlerts] = useState<any>(null);
  const [pois, setPois] = useState<any>(null);
  const [openGaps, setOpenGaps] = useState<any>(null);
  const [daysPlan, setDaysPlan] = useState<any>(null);
  const [sleepOpts, setSleepOpts] = useState<any>(null);
  const [sleepOptsBusy, setSleepOptsBusy] = useState('');
  const [alertsImportantOnly, setAlertsImportantOnly] = useState(false);
  const [scenario, setScenario] = useState<any>(null);
  const [whatifSpeed, setWhatifSpeed] = useState(0);
  const [whatifStopMin, setWhatifStopMin] = useState(0);
  const [whatifSleepH, setWhatifSleepH] = useState(0);
  const [scenarios, setScenarios] = useState<any[]>([]);
  const [rbOpen, setRbOpen] = useState(false);
  const [rbText, setRbText] = useState('');
  const [pdfB64, setPdfB64] = useState<string | null>(null);   // a roadbook PDF being read
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const autoStopDone = useRef(false);
  // A recompute asked for from an event handler runs on the NEXT render, with the fresh answers (a
  // setTimeout would call computeTimeline from the old render and read the old state).
  const [recomputeReq, setRecomputeReq] = useState<{ n: number; results?: boolean } | null>(null);
  const requestRecompute = (results?: boolean) => setRecomputeReq(r => ({ n: (r ? r.n : 0) + 1, results }));
  const restored = useRef(false);

  const usingGenericPace = !calibratedProfile && !(parseFloat(baseSpeed) > 0);

  // ---- the sticky session (desktop PlanStore): answers survive leaving the screen ----------------
  const uiState = useCallback(() => ({
    gpx: gpxText, gpxName, startDate, startTime, eventName, baseSpeed, stopTotal: stopTotalH, sleep: sleepH, fatigueOn,
    plannedStops, usualBed, usualWake, noRideOn, noRideFrom, noRideTo, calibratedProfile, controls, controlOverrides,
  }), [gpxText, gpxName, startDate, startTime, eventName, baseSpeed, stopTotalH, sleepH, fatigueOn, plannedStops, usualBed,
       usualWake, noRideOn, noRideFrom, noRideTo, calibratedProfile, controls, controlOverrides]);
  const applyUi = (u: any) => {
    setGpxText(u.gpx || ''); setGpxName(u.gpxName || '');
    setStartDate(u.startDate || today()); setStartTime(u.startTime || '06:00'); setEventName(u.eventName || '');
    setBaseSpeed(u.baseSpeed || ''); setStopTotalH(u.stopTotal || ''); setSleepH(u.sleep || '');
    setFatigueOn(!!u.fatigueOn); setUsualBed(u.usualBed || '22:00'); setUsualWake(u.usualWake || '06:00');
    setNoRideOn(!!u.noRideOn); setNoRideFrom(u.noRideFrom || '23:00'); setNoRideTo(u.noRideTo || '03:30');
    setPlannedStops(u.plannedStops || []); setCalibratedProfile(u.calibratedProfile || null);
    setControlOverrides(u.controlOverrides || {});
    setControls((u.controls || []).map((c: any) => ({ label: c.label || '', km: c.km || '', hours: c.hours || '', opens: c.opens || '' })));
    setStopUserSet(!!(u.stopTotal && String(u.stopTotal).length)); autoStopDone.current = true;
  };
  useEffect(() => {
    loadSession().then(u => { if (u && u.gpx) applyUi(u); restored.current = true; }).catch(() => { restored.current = true; });
    refreshScenarios();
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!restored.current) return;
    const id = setTimeout(() => saveSession(uiState()), 500);
    return () => clearTimeout(id);
  }, [uiState]);

  const startIso = () => `${(startDate || '').trim()}T${(startTime || '06:00').trim().length === 5 ? startTime.trim() : '06:00'}:00`;

  // ---- the route (+ PitStopper POIs when the file carries them) ----------------------------------
  async function importPois(gpx: string) {
    if (gpx.indexOf('<wpt') < 0) { setPois(null); return; }
    const res = await raceApi.pois({ gpx, poi_gpx: gpx, custom_tag: await getCustomTag(), water_l_per_100km: 2.0, carry_l: 1.5 });
    setPois(res && res.ok && (res.imported || 0) > 0 ? res : null);
  }
  async function loadGpx() {
    try {
      const path = await pickGpxFile();
      const gpx = await RNFS.readFile(path, 'utf8');
      if (!gpx) { setStatusMsg("Couldn't read that file"); return; }
      // The picker hands over a renamed copy ("picked_route_<time>.gpx"): name it by the GPX's own name.
      let name = decodeURIComponent(path.split('/').pop() || 'route.gpx');
      if (/^picked_route_\d+\.gpx$/i.test(name)) {
        const m = /<name>\s*(?:<!\[CDATA\[)?([^<\]]+)/.exec(gpx);
        if (m && m[1].trim()) name = m[1].trim();
      }
      setGpxName(name);
      setGpxText(gpx);
      setControlOverrides({}); setPlannedStops([]); setTimeline(null);
      autoStopDone.current = false; setStopUserSet(false); setStopTotalH('');
      setStatusMsg('Reading route…');
      importPois(gpx);
    } catch (e: any) {
      if (e?.code !== 'GPX_PICK_CANCELLED') setStatusMsg("Couldn't read that file: " + (e?.message ?? e));
    }
  }
  // First pass on a new route: distance + the learned stop estimate (desktop computeTimeline on load).
  useEffect(() => { if (gpxText && restored.current) computeTimeline(); }, [gpxText]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (gpxText) importPois(gpxText); }, [gpxText]);                     // eslint-disable-line react-hooks/exhaustive-deps

  async function calibrateFromFit() {
    try {
      const path = await pickGpxFile();
      setCalibNote('Calibrating from your ride…');
      const res = await raceApi.calibrateFit(path);
      if (res && res.ok && res.profile) {
        setCalibratedProfile(res.profile); setBaseSpeed('' + res.profile.base_speed_kmh);
        setCalibNote(`Calibrated from your ride (${res.ride.distance_km} km / ${res.ride.ascent_m} m): base ${res.profile.base_speed_kmh} km/h.`);
      } else setCalibNote("Couldn't calibrate: " + (res && res.error ? res.error : 'unknown error'));
    } catch (e: any) {
      if (e?.code !== 'GPX_PICK_CANCELLED') setCalibNote("Couldn't calibrate: " + (e?.message ?? e));
    }
  }

  async function importRoadbookText(text: string) {
    setStatusMsg('Reading roadbook…');
    const res = await raceApi.roadbook({ text });
    if (!res || !res.ok || !res.controls) { setStatusMsg("Couldn't read roadbook: " + (res && res.error ? res.error : 'unknown error')); return; }
    const cs: Ctrl[] = [];
    for (const c of res.controls) {
      if ((c.km || 0) <= 0.05) continue;         // the start control is the start time, not a checkpoint
      cs.push({ label: c.name ? `${c.label} ${c.name}` : c.label, km: '' + c.km, hours: c.close || '', opens: c.open || '' });
    }
    setControls(cs);
    setStatusMsg(`Imported ${cs.length} controls from the roadbook.`);
  }
  // PDF roadbook: the desktop runs pdftotext; here pdf.js reads it (PdfTextReader) into the same text.
  async function importRoadbookPdf() {
    try {
      const path = await pickGpxFile();                      // any file; copied into the app cache
      setRbOpen(false);                                      // the outcome shows in the page's status line
      const size = Number((await RNFS.stat(path)).size);
      const b64 = size <= 20e6 ? await RNFS.readFile(path, 'base64') : null;
      RNFS.unlink(path).catch(() => undefined);
      if (b64 === null) { setStatusMsg("Couldn't read roadbook: that file is too big for a roadbook"); return; }
      if (!b64.startsWith('JVBER')) { setStatusMsg("Couldn't read roadbook: that file isn't a PDF"); return; }   // "%PDF"
      setStatusMsg('Reading roadbook…');
      setPdfB64(b64);
    } catch (e: any) {
      if (e?.code !== 'GPX_PICK_CANCELLED') setStatusMsg("Couldn't read that file: " + (e?.message ?? e));
    }
  }
  function onPdfText(r: PdfTextResult) {
    setPdfB64(null);
    if (!r.ok) {
      setStatusMsg("Couldn't read roadbook: " + (r.error === 'password' ? 'the PDF is password-protected'
        : `could not read the PDF (${r.error})`) + '; paste the table text instead');
      return;
    }
    if (!(r.text || '').trim()) { setStatusMsg("Couldn't read roadbook: this PDF has no text (a scanned page?); paste the table text instead"); return; }
    importRoadbookText(r.text || '');
  }

  // ---- the plan (desktop computeTimeline / applyFolds / fetch*) -----------------------------------
  function buildPayload() {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(startIso());
    if (!m) return null;
    const startMs = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0).getTime();
    if (isNaN(startMs)) return null;
    const cutoffs: any[] = [];
    let prevMs = startMs, prevOpenMs = startMs;
    controls.forEach((c, i) => {
      const km = parseFloat(c.km);
      if (isNaN(km)) return;
      const co: any = { label: c.label || `Control ${i + 1}`, distance_km: km };
      const dt = clockToDt(prevMs, (c.hours || '').trim());
      if (dt) { co.cutoff_dt = toLocalIso(dt); prevMs = dt.getTime(); }
      const od = clockToDt(prevOpenMs, (c.opens || '').trim());
      if (od) { co.open_dt = toLocalIso(od); prevOpenMs = od.getTime(); }
      cutoffs.push(co);
    });
    const athlete: any = { weight_kg: 75.0 };
    const base = parseFloat(baseSpeed);
    if (calibratedProfile && base > 0) athlete.speed_profile = calibratedProfile;
    else if (base > 0) athlete.speed_profile = { base_speed_kmh: base, confidence: 'low', model_source: 'generic', n_recent_rides: 0 };
    else athlete.speed_profile = { base_speed_kmh: 22.0, confidence: 'low', model_source: 'generic', n_recent_rides: 0 };
    const payload: any = {
      event: { name: eventName || 'Race', event_type: 'BRM', start_dt: startIso(), gpx: gpxText, points: [], cutoffs },
      athlete, bike: { bike_weight_kg: 10.0, load_weight_kg: 5.0, bike_type: 'road' },
    };
    const events: any[] = []; let named = 0;
    for (const ps of plannedStops) {
      const km = parseFloat(ps.km), mins = parseFloat(ps.min);
      if (!isNaN(km) && !isNaN(mins) && mins > 0) { events.push({ km, duration_s: mins * 60 }); named += mins * 60; }
    }
    if (events.length) payload.stop_events = events;
    const T = parseFloat(stopTotalH);
    if (!isNaN(T) && T >= 0 && stopTotalH.length > 0) payload.stop_total_s = Math.max(0, T * 3600 - named);
    else payload.stop_profile = { ratio: 0.18, source: 'default', confidence: 'low' };
    if (fatigueOn) payload.fatigue = { enabled: true, model: 'twoprocess', bed_h: hhmmToHours(usualBed, 22), wake_h: hhmmToHours(usualWake, 6) };
    if (noRideOn) payload.no_ride = { start_h: hhmmToHours(noRideFrom, 23), end_h: hhmmToHours(noRideTo, 3.5) };
    if (Object.keys(controlOverrides).length) payload.control_overrides = controlOverrides;
    return payload;
  }

  async function computeTimeline(opts: { results?: boolean; stopTotalOverride?: string } = {}) {
    if (!gpxText) { setStatusMsg('Load a GPX first'); return; }
    const payload = buildPayload();
    if (!payload) { setStatusMsg('Check the start date/time'); return; }
    if (opts.stopTotalOverride !== undefined) {
      const T = parseFloat(opts.stopTotalOverride);
      if (!isNaN(T)) { delete payload.stop_profile; payload.stop_total_s = Math.max(0, T * 3600 - (payload.stop_events || []).reduce((a: number, e: any) => a + e.duration_s, 0)); }
    }
    setBusy(true); setStatusMsg('Computing timeline…');
    const res = await raceApi.timeline(payload);
    setBusy(false);
    if (!(res && res.ok && res.timeline)) { setStatusMsg('Error: ' + (res && res.error ? res.error : 'timeline failed')); return; }
    const tl = res.timeline;
    setTimeline(tl); setRouteKm(tl.distance_km); setBasePayload(payload);
    setScenario(null); setWhatifSpeed(0); setWhatifStopMin(0); setWhatifSleepH(0);
    setStatusMsg(''); setWeather(null); setSleepPlan(null); setAlerts(null); setSleepOpts(null);
    // First time on a fresh route with no stop estimate: pre-fill from the learned memory, recompute.
    if (!stopTotalH.length && opts.stopTotalOverride === undefined && !autoStopDone.current) {
      autoStopDone.current = true;
      const r = await raceApi.stopSuggest({ distance_km: tl.distance_km });
      if (r && r.ok) { setStopTotalH('' + r.hours); return computeTimeline({ ...opts, stopTotalOverride: '' + r.hours }); }
    }
    if (stopUserSet && stopTotalH.length) raceApi.stopRecord({ distance_km: tl.distance_km, hours: parseFloat(stopTotalH) });
    if (opts.results || showResults) fetchWeather(tl, payload);
  }

  async function fetchWeather(tl: any, payload: any) {
    const ctrls = tl.controls.map((c: any) => ({ label: c.label, distance_km: c.distance_km, arrival_dt: c.arrival_dt }));
    let wx: any = null;
    try { const r = await raceApi.weather({ gpx: gpxText, controls: ctrls, tz: -new Date().getTimezoneOffset() / 60 }); wx = r && r.ok ? r : null; }
    catch { wx = null; }
    setWeather(wx);
    const sp = await fetchSleep(tl, wx);
    fetchAlerts(tl, wx);
    applyFolds(tl, payload, wx, sp);
  }
  async function fetchSleep(tl: any, wx: any) {
    const ctrls = tl.controls.map((c: any) => ({ label: c.label, distance_km: c.distance_km, arrival_dt: c.arrival_dt, margin_s: c.margin_s }));
    const body: any = { gpx: gpxText, controls: ctrls, start_dt: startIso(), suggested_total_s: tl.sleep_suggested_s || 0,
                        tz: -new Date().getTimezoneOffset() / 60 };
    if (wx && wx.controls) body.weather = wx.controls;
    const r = await raceApi.sleep(body);
    const sp = r && r.ok ? r : null;
    setSleepPlan(sp);
    return sp;
  }
  async function fetchAlerts(tl: any, wx: any) {
    const body: any = { gpx: gpxText, timeline: tl };
    if (wx) body.weather = wx;
    if (pois) body.pois = pois;
    const r = await raceApi.alerts(body);
    setAlerts(r && r.ok ? r : null);
  }
  async function applyFolds(tl: any, base: any, wx: any, sp: any, wind = windFold) {
    const p = JSON.parse(JSON.stringify(base));
    const sleepSecs = parseFloat(sleepH) > 0 && !noRideOn ? parseFloat(sleepH) * 3600 : 0;
    p.sleep_windows = [];
    if (sleepSecs > 0) {
      const ws = sp && sp.windows ? sp.windows : [];
      if (ws.length) {
        const planned = ws.reduce((a: number, w: any) => a + w.duration_s, 0);
        const scale = planned > 0 ? sleepSecs / planned : 1;
        p.sleep_windows = ws.map((w: any) => ({ km: w.km, duration_s: Math.round(w.duration_s * scale) }));
      } else if (tl && tl.distance_km > 0) p.sleep_windows = [{ km: tl.distance_km * 0.6, duration_s: sleepSecs }];
    }
    p.wind_speed_delta_kmh = 0;
    if (wind && wx && wx.summary && wx.summary.net_head_kmh !== undefined) {
      const net = wx.summary.net_head_kmh;
      p.wind_speed_delta_kmh = -(net > 0 ? 0.5 : 0.3) * net;
    }
    setBasePayload(p);
    const need = p.sleep_windows.length > 0 || Math.abs(p.wind_speed_delta_kmh || 0) > 0.05;
    if (!need) return;
    const r = await raceApi.timeline(p);
    if (r && r.ok && r.timeline) setTimeline(r.timeline);
  }

  useEffect(() => { if (recomputeReq) computeTimeline({ results: recomputeReq.results }); }, [recomputeReq]);   // eslint-disable-line react-hooks/exhaustive-deps

  // open-hours gaps + days & nights follow the timeline (desktop onTimelineChanged)
  useEffect(() => {
    if (!timeline || !pois || !timeline.controls) { setOpenGaps(null); return; }
    const eta = [{ km: 0, dt: timeline.start_dt }, ...timeline.controls.map((c: any) => ({ km: c.distance_km, dt: c.arrival_dt }))];
    raceApi.pois({ pois, eta }).then(r => setOpenGaps(r && r.ok ? r : null));
  }, [timeline, pois]);
  useEffect(() => {
    if (!timeline || !showResults) { setDaysPlan(null); return; }
    const body: any = { timeline };
    if (pois) body.pois = pois;
    raceApi.days(body).then(r => setDaysPlan(r && r.ok && r.nights && r.nights.length > 0 ? r : null));
  }, [timeline, pois, showResults]);

  async function suggestSleep() {
    if (!basePayload) return;
    const body = JSON.parse(JSON.stringify(basePayload));
    delete body.sleep_windows; delete body.sleep; delete body.no_ride; delete body.fatigue;
    body.habit = { bed_h: hhmmToHours(usualBed, 22), wake_h: hhmmToHours(usualWake, 6) };
    if (pois) body.pois = pois;
    setSleepOptsBusy('Trying plans…'); setSleepOpts(null);
    const r = await raceApi.sleepOptions(body, (d, n) => setSleepOptsBusy(`Trying plans… ${d}/${n}`));
    setSleepOptsBusy('');
    setSleepOpts(r && r.ok ? r : null);
    if (!(r && r.ok)) setStatusMsg("Couldn't compute sleep options: " + (r && r.error ? r.error : ''));
  }
  const sleepinessWord = (b: string) => (b === 'fine' ? 'awake enough' : b === 'tired' ? 'tired but OK' : 'too sleepy to ride safely');
  function useSleepOption(o: any) {
    setNoRideOn(true); setFatigueOn(true); setNoRideFrom(o.bed); setNoRideTo(o.wake);
    requestRecompute(true);
  }

  // ---- planned stops ----------------------------------------------------------------------------
  const recompute = () => { if (gpxText) requestRecompute(); };
  function suggestStops() {
    if (!timeline || !timeline.controls) return;
    const kmAtClock = (hh: number) => {
      for (const c of timeline.controls) if (parseInt(c.arrival_dt.slice(11, 13), 10) >= hh) return Math.round(c.distance_km);
      return null;
    };
    const a: PStop[] = [];
    const lunch = kmAtClock(13); if (lunch) a.push({ label: 'Lunch', km: '' + lunch, min: '30' });
    const dinner = kmAtClock(20); if (dinner && (!lunch || dinner > lunch)) a.push({ label: 'Dinner', km: '' + dinner, min: '40' });
    setPlannedStops(a); recompute();
  }
  function shortStopsLeftMin() {
    const T = parseFloat(stopTotalH); if (isNaN(T)) return -1;
    const named = plannedStops.reduce((a, ps) => a + (parseFloat(ps.min) || 0), 0);
    // whole minutes, cut like fmtDur (so 1.03 h reads 1h01 here and in STOPS)
    return Math.max(0, Math.floor(T * 60 - named + 1e-6));
  }

  // ---- what-if ----------------------------------------------------------------------------------
  async function applyWhatif(sp: number, stopMin: number, sleepHrs: number) {
    if (!basePayload || !timeline) return;
    const p = JSON.parse(JSON.stringify(basePayload));
    if (sp !== 0 && p.athlete && p.athlete.speed_profile) p.athlete.speed_profile.base_speed_kmh += sp;
    if (stopMin !== 0) {
      if (p.stop_total_s !== undefined) p.stop_total_s = Math.max(0, p.stop_total_s + stopMin * 60);
      else if (p.stops_s) p.stops_s = p.stops_s.map((x: number) => Math.max(0, x + stopMin * 60));
    }
    if (sleepHrs > 0) p.sleep = { enabled: true, duration_s: sleepHrs * 3600 };
    const r = await raceApi.timeline(p);
    if (r && r.ok && r.timeline) setScenario(r.timeline);
  }
  const deltaTxt = (a: number, b: number) => { const d = Math.round(a - b); return Math.abs(d) < 30 ? 'no change' : (d > 0 ? '+' : '−') + fmtDur(Math.abs(d)); };
  const newlyAtRisk = () => {
    if (!scenario || !timeline) return 0;
    let n = 0;
    for (let i = 0; i < scenario.controls.length && i < timeline.controls.length; i++) {
      const b = timeline.controls[i].margin_s, sc = scenario.controls[i].margin_s;
      if (b !== null && sc !== null && b >= 0 && sc < 0) n++;
    }
    return n;
  };

  // ---- verdict, roadbook, scenarios --------------------------------------------------------------
  function verdictText() {
    if (!timeline) return '';
    let v = `You'd finish ~${fmtClockDay(timeline.finish_eta_dt)}`;
    let fin: any = null;
    for (let i = timeline.controls.length - 1; i >= 0; i--) if (timeline.controls[i].margin_s !== null) { fin = timeline.controls[i]; break; }
    if (fin) v += fin.margin_s >= 0 ? ` — about ${fmtDur(fin.margin_s)} inside the limit.` : ` — about ${fmtDur(-fin.margin_s)} OVER the limit.`;
    else v += '.';
    if (timeline.worst_margin_s !== null && timeline.worst_margin_control) {
      const w = timeline.worst_margin_s;
      v += ` Tightest: ${timeline.worst_margin_control}, ${w >= 0 ? `${fmtDur(w)} spare` : `${fmtDur(-w)} short`}.`;
    }
    return v;
  }
  const verdictIsBad = () => {
    if (!timeline) return false;
    for (let i = timeline.controls.length - 1; i >= 0; i--) if (timeline.controls[i].margin_s !== null) return timeline.controls[i].margin_s < 0;
    return false;
  };
  const wxFor = (i: number) => (weather && weather.controls && i < weather.controls.length ? weather.controls[i] : null);
  function buildRoadbook() {
    if (!timeline) return '';
    const L: string[] = [];
    const pad = (x: any, n: number) => { let v = '' + x; while (v.length < n) v += ' '; return v; };
    const padL = (x: any, n: number) => { let v = '' + x; while (v.length < n) v = ' ' + v; return v; };
    L.push(`${eventName || 'Race'} — ${timeline.distance_km} km, ${timeline.total_ascent_m} m climb`);
    L.push('Start: ' + startIso().replace('T', ' ').slice(0, 16));
    L.push(verdictText());
    L.push(`Moving ${fmtDur(timeline.moving_time_s)}  ·  stops ${fmtDur(timeline.stop_time_s)}` +
           (timeline.sleep_time_s > 0 ? `  ·  sleep ${fmtDur(timeline.sleep_time_s)}` : '') + `  ·  elapsed ${fmtDur(timeline.elapsed_time_s)}`);
    L.push('');
    L.push('checkpoint            km   opens   arrive   ride    km/h  stop   margin   temp  wind');
    timeline.controls.forEach((c: any, i: number) => {
      const w = wxFor(i);
      const m = c.margin_s === null ? '—' : (c.margin_s < 0 ? '-' + fmtDur(-c.margin_s) : '+' + fmtDur(c.margin_s));
      const early = c.early_s !== undefined && c.early_s !== null && c.early_s > 600;
      L.push(pad(c.label, 20) + ' ' + padL(c.distance_km, 5) + '  ' + padL(c.opens_dt ? fmtClock(c.opens_dt) : '—', 5) + '  ' + pad(fmtClock(c.arrival_dt), 7) +
             ' ' + padL(fmtDur(c.moving_time_s), 6) + '  ' + padL(c.avg_speed_kmh, 5) + ' ' + padL(Math.round(c.stop_s / 60) + 'm', 5) + ' ' + padL(m, 7) +
             (w ? '  ' + padL(Math.round(w.temp_c) + '°', 4) + '  ' + w.wind_rel + (w.is_dark ? ' (dark)' : '') : '') +
             (early ? `  [too early: wait ${fmtDur(c.early_s)}]` : ''));
    });
    if (pois && pois.summary && pois.summary.length) { L.push(''); L.push('Resupply:'); for (const x of pois.summary) L.push('  ' + x); }
    if (sleepPlan && sleepPlan.windows && sleepPlan.windows.length) {
      L.push(''); L.push('Sleep:');
      for (const w of sleepPlan.windows) L.push(`  ${w.start_local}–${w.end_local} (~${(w.duration_s / 3600).toFixed(1)}h) near km ${w.km} — ${w.reason}`);
    }
    if (alerts && alerts.alerts && alerts.alerts.length) {
      L.push(''); L.push('Critical points:');
      for (const a of alerts.alerts) L.push(`  [${a.severity}] km ${Math.round(a.km)} — ${a.text}`);
    }
    return L.join('\n');
  }
  async function refreshScenarios() { const r = await raceApi.scenarioList(); setScenarios(r && r.ok ? r.scenarios : []); }
  async function saveScenario(name: string) {
    const summary = timeline ? { distance_km: timeline.distance_km, finish: fmtClockDay(timeline.finish_eta_dt), moving_time_s: timeline.moving_time_s,
      stop_time_s: timeline.stop_time_s, sleep_time_s: timeline.sleep_time_s, elapsed_time_s: timeline.elapsed_time_s,
      worst_margin_s: timeline.worst_margin_s, worst_margin_control: timeline.worst_margin_control } : {};
    const r = await raceApi.scenarioSave({ name, ui: uiState(), summary });
    setStatusMsg(r && r.ok ? 'Scenario saved.' : 'Save failed.');
    refreshScenarios();
  }
  async function openScenario(name: string) {
    const r = await raceApi.scenarioGet({ name });
    if (!(r && r.ok && (r as any).scenario)) return;
    applyUi((r as any).scenario.ui || {});
    setShowResults(true);
    requestRecompute(true);
  }

  // ---- wizard ------------------------------------------------------------------------------------
  function next() {
    if (step < STEP_TITLES.length - 1) { setStep(step + 1); return; }
    setShowResults(true);
    computeTimeline({ results: true });
    scrollRef.current?.scrollTo({ y: 0, animated: false });
  }

  const tlOk = !!(timeline && timeline.ok);

  return (
    <ScrollView ref={scrollRef} style={s.root} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      <View>
        <Cap>Plan when you'll reach each checkpoint — and whether you'll beat the time limits.</Cap>
      </View>

      {!showResults && (
        <Card style={{ width: '100%', gap: v3Spacing.small }}>
          <View style={[s.row, { justifyContent: 'space-between' }]}>
            <Cap>Step {step + 1} of {STEP_TITLES.length}</Cap>
            <View style={s.row}>
              {STEP_TITLES.map((_, i) => (
                <View key={i} style={{ width: 9, height: 9, borderRadius: 4.5, marginLeft: 6,
                  backgroundColor: i <= step ? t.primary : t.border, opacity: i === step ? 1 : (i < step ? 0.85 : 0.5) }} />))}
            </View>
          </View>
          <Body size={v3Type.title} bold>{STEP_TITLES[step]}</Body>

          {step === 0 && (<View style={{ gap: v3Spacing.small }}>
            <Body color={gpxName ? t.text : t.mutedText} size={gpxName ? v3Type.subtitle : v3Type.body}>
              {gpxName ? `✓ ${gpxName}${routeKm > 0 ? `  ·  ${routeKm} km` : ''}` : "Load the GPX of the route you're planning."}
            </Body>
            <Button label={gpxName ? 'Change route' : 'Load GPX'} variant="filled" grow={false} onPress={loadGpx} />
            {!pois ? (
              <View style={s.nested}>
                <Body bold>Add water, food & cemetery stops (optional)</Body>
                <Cap>{'1.  Open pitstopper.net and upload your route (GPX).\n2.  Tick what you want to find — e.g. Water, Food & Drink, and a custom tag for cemeteries — then Search.\n3.  Export → Export a file → GPX (not FIT: FIT cuts the names).\n4.  Load that file here instead of your original — it is the same route plus the places.'}</Cap>
                <Cap style={{ fontStyle: 'italic' }}>If you change the route afterwards (cut it, extend it), export again from PitStopper.</Cap>
                <Button label="Open pitstopper.net" variant="text" grow={false} onPress={() => Linking.openURL('https://pitstopper.net')} />
              </View>
            ) : (
              <View style={{ gap: 4 }}>
                <Cap>✓ {pois.imported || 0} places along the route (water, food, cemeteries…) — they'll show up in your plan's warnings.</Cap>
                {/* PitStopper does not export the NAME of a custom tag, so say what yours is (desktop Route page). */}
                {!!(pois.groups && pois.groups['Custom tag'] > 0) && (<>
                  <Cap>My PitStopper custom tag is a…</Cap>
                  <Dropdown value={Math.max(0, CUSTOM_TAGS.indexOf(customTag))}
                    choices={CUSTOM_TAG_LABELS.map((l, i) => ({ value: i, label: l }))}
                    onSelect={async (i: number) => { const tag = CUSTOM_TAGS[i]; setCustomTagState(tag); await setCustomTag(tag); importPois(gpxText); }} />
                </>)}
              </View>
            )}
          </View>)}

          {step === 1 && (<View style={{ gap: v3Spacing.small }}>
            <View style={s.row}>
              <Field value={startDate} onChange={setStartDate} placeholder="YYYY-MM-DD" width={150} />
              <Field value={startTime} onChange={setStartTime} placeholder="HH:MM" width={100} />
            </View>
            <Field value={eventName} onChange={setEventName} placeholder="Event name (optional)" />
          </View>)}

          {step === 2 && (<View style={{ gap: v3Spacing.small }}>
            <View style={s.row}>
              <Field value={baseSpeed} onChange={(v: string) => { setBaseSpeed(v); setCalibratedProfile(null); setCalibNote(''); }}
                placeholder="Your steady speed on a flat road, e.g. 25 km/h" numeric />
              <Button label="From a ride" variant="text" grow={false} onPress={calibrateFromFit} />
            </View>
            <Cap>{calibNote || "Type your flat-road cruising speed, or pick a past ride (FIT) and we'll work it out. Leave it blank and we'll assume a typical ~22 km/h — you can always come back and refine it."}</Cap>
          </View>)}

          {step === 3 && (<View style={{ gap: v3Spacing.small }}>
            <Body bold size={v3Type.label}>Food & rest stops</Body>
            <Field value={stopTotalH} onChange={(v: string) => { setStopTotalH(v); setStopUserSet(true); }} placeholder="Hours, all short stops added up" width={220} numeric />
            <Cap>Your control/food/rest stops, added up — spread across the checkpoints. NOT sleep (that's below). Leave blank for a typical estimate.</Cap>
            <Body bold size={v3Type.label} style={{ marginTop: v3Spacing.small }}>Sleep</Body>
            <Field value={sleepH} onChange={setSleepH} placeholder="Hours of sleep (0 if none)" width={220} numeric />
            <Cap color={timeline && timeline.sleep_suggested_s > 0 ? AMBER : t.mutedText}>
              {timeline && timeline.sleep_suggested_s > 0
                ? `🌙 Placed as ONE block at night, at the best control — so checkpoints before it are unaffected and later ones shift by your sleep. Typical for this distance: about ${fmtDur(timeline.sleep_suggested_s)}.`
                : 'Placed as one block at night, at the best control. Enter 0 for a ride you\'ll do without sleeping.'}
            </Cap>
            <Check value={fatigueOn} onChange={setFatigueOn} label="Multi-day ride — model fatigue" />
            {fatigueOn && <Cap>For rides that run into the night (or several): you slow down as sleep pressure builds and your body clock dips, and a short night leaves some of it behind. Uses your usual sleep (next step). Leave off for a day ride.</Cap>}
          </View>)}

          {step === 4 && (<View style={{ gap: v3Spacing.small }}>
            <Body bold size={v3Type.label}>Your usual sleep</Body>
            <View style={s.row}>
              <Cap>Bed</Cap><Field value={usualBed} onChange={setUsualBed} placeholder="HH:MM" width={90} />
              <Cap>Wake</Cap><Field value={usualWake} onChange={setUsualWake} placeholder="HH:MM" width={90} />
            </View>
            <Cap>Your body clock and how fast you get sleepy follow this. Used when "model fatigue" is on, and by "Suggest my sleep" in your plan.</Cap>
            <Body bold size={v3Type.label} style={{ marginTop: v3Spacing.small }}>Hours you never ride</Body>
            <Check value={noRideOn} onChange={setNoRideOn} label="There are hours I never ride (night, curfew…)" />
            {noRideOn && (
              <View style={s.row}>
                <Cap>From</Cap><Field value={noRideFrom} onChange={setNoRideFrom} placeholder="HH:MM" width={90} />
                <Cap>to</Cap><Field value={noRideTo} onChange={setNoRideTo} placeholder="HH:MM" width={90} />
              </View>)}
            <Cap>{noRideOn
              ? 'The plan never has you riding in these hours: you stop and rest until they end. That rest IS your sleep, so the sleep field above is ignored. Examples: 23:00 to 03:30 (what you did on the BRM600 Verdun), or 00:00 to 03:00, or 21:00 to 06:00 (a curfew like Bikingman Corsica 2021). Your plan then shows the days and nights this creates — and how they compare with cutting the route into equal days.'
              : 'Optional. Tick it if you refuse to ride at certain hours, or the event forbids it. Leave it off and sleep is placed where the body clock says it is worst to ride.'}</Cap>
          </View>)}

          {step === 5 && (<View style={{ gap: v3Spacing.small }}>
            <Cap>From your brevet card — import the roadbook, or skip and just get a finish time.</Cap>
            <View style={s.row}>
              <Button label="Import roadbook" variant="text" grow={false} onPress={() => setRbOpen(true)} />
              <Button label="+ Add" variant="text" grow={false} onPress={() => setControls(c => [...c, { label: '', km: '', hours: '', opens: '' }])} />
            </View>
            {controls.length > 0 && (
              <View style={s.row}>
                <Cap style={{ flex: 1 }}>Label</Cap><Cap style={{ width: 64 }}>km</Cap><Cap style={{ width: 64 }}>opens</Cap>
                <Cap style={{ width: 76 }}>must arrive by</Cap><View style={{ width: 28 }} />
              </View>)}
            {controls.map((c, i) => {
              const set = (f: keyof Ctrl, v: string) => setControls(cs => cs.map((x, j) => (j === i ? { ...x, [f]: v } : x)));
              return (
                <View key={i} style={s.row}>
                  <Field value={c.label} onChange={(v: string) => set('label', v)} placeholder={`Control ${i + 1}`} />
                  <Field value={c.km} onChange={(v: string) => set('km', v)} placeholder="km" width={64} numeric />
                  <Field value={c.opens} onChange={(v: string) => set('opens', v)} placeholder="opens" width={64} />
                  <Field value={c.hours} onChange={(v: string) => set('hours', v)} placeholder="by HH:MM" width={76} />
                  <TouchableOpacity onPress={() => setControls(cs => cs.filter((_, j) => j !== i))} style={{ width: 28, alignItems: 'center' }}>
                    <Body color={t.mutedText}>✕</Body>
                  </TouchableOpacity>
                </View>);
            })}
          </View>)}

          <View style={[s.row, { marginTop: v3Spacing.medium, justifyContent: 'space-between' }]}>
            {step > 0 ? <Button label="◂ Back" variant="text" grow={false} onPress={() => setStep(step - 1)} /> : <View />}
            <Button label={step < STEP_TITLES.length - 1 ? 'Next ▸' : (busy ? 'Computing…' : 'See my plan ▸')} variant="filled" grow={false}
              disabled={(step === 0 && !gpxText) || busy} onPress={next} />
          </View>
          {!!statusMsg && <Cap color={/^(Error|Couldn't|No )/.test(statusMsg) ? BAD : t.mutedText}>{statusMsg}</Cap>}
        </Card>
      )}

      {showResults && (
        <View style={[s.row, { flexWrap: 'wrap' }]}>
          <Button label="◂ Edit answers" variant="text" grow={false} onPress={() => setShowResults(false)} />
          <View style={{ flex: 1 }} />
          <Button label={busy ? 'Computing…' : 'Recompute'} variant="text" grow={false} disabled={busy || !gpxText} onPress={() => computeTimeline({ results: true })} />
          {tlOk && <Button label="Export roadbook" variant="text" grow={false} onPress={() => Share.share({ message: buildRoadbook(), title: (eventName || gpxName || 'Race') + ' roadbook' })} />}
          {tlOk && <Button label="Save scenario" variant="text" grow={false} onPress={() => { setSaveName(eventName || gpxName || 'Scenario'); setSaveOpen(true); }} />}
        </View>
      )}
      {showResults && !!statusMsg && <Cap color={/^(Error|Couldn't|No )/.test(statusMsg) ? BAD : t.mutedText}>{statusMsg}</Cap>}

      {showResults && tlOk && (
        <Card style={{ width: '100%', gap: 4 }}>
          <Body size={v3Type.subtitle} bold color={verdictIsBad() ? BAD : GOOD}>{verdictText()}</Body>
          {usingGenericPace && <Cap color={AMBER}>⚠ This uses a cautious generic 22 km/h, not your pace — go back and set your speed (or “From a ride”) for your real finish time.</Cap>}
          <View style={[s.row, { flexWrap: 'wrap', marginTop: 6 }]}>
            <Stat label="FINISH" value={fmtClock(timeline.finish_eta_dt)} big />
            <Stat label="MOVING" value={fmtDur(timeline.moving_time_s)} />
            <Stat label="STOPS" value={fmtDur(timeline.stop_time_s)} />
            {timeline.sleep_time_s > 0 && <Stat label="SLEEP" value={fmtDur(timeline.sleep_time_s)} />}
            <Stat label="ELAPSED" value={fmtDur(timeline.elapsed_time_s)} />
            <Stat label="TIGHTEST CUTOFF" value={timeline.worst_margin_s !== null ? fmtDur(timeline.worst_margin_s) : '—'}
              color={timeline.worst_margin_s !== null && timeline.worst_margin_s < 0 ? BAD : GOOD} />
          </View>

          <Divider />
          <View style={s.row}>
            <Body bold size={v3Type.label} style={{ flex: 1 }}>Planned stops</Body>
            <Button label="Suggest" variant="text" grow={false} onPress={suggestStops} />
            <Button label="+ Add" variant="text" grow={false} onPress={() => { setPlannedStops(a => [...a, { label: 'Stop', km: timeline ? '' + Math.round(timeline.distance_km / 2) : '', min: '20' }]); recompute(); }} />
          </View>
          {plannedStops.map((ps, i) => {
            const set = (f: keyof PStop, v: string) => setPlannedStops(a => a.map((x, j) => (j === i ? { ...x, [f]: v } : x)));
            return (
              <View key={i} style={s.row}>
                <Field value={ps.label} onChange={(v: string) => set('label', v)} placeholder="Stop name" />
                <Field value={ps.km} onChange={(v: string) => set('km', v)} placeholder="km" width={70} numeric onEnd={recompute} />
                <Field value={ps.min} onChange={(v: string) => set('min', v)} placeholder="min" width={60} numeric onEnd={recompute} />
                <TouchableOpacity onPress={() => { setPlannedStops(a => a.filter((_, j) => j !== i)); recompute(); }} style={{ width: 28, alignItems: 'center' }}>
                  <Body color={t.mutedText}>✕</Body>
                </TouchableOpacity>
              </View>);
          })}
          <Cap>{shortStopsLeftMin() < 0 ? 'Set your food & rest time to split it into planned stops + short stops.'
            : `⏱ Left for short stops: ${fmtDur(shortStopsLeftMin() * 60)}, spread through the ride (more in the 2nd half, when you're tired).`}</Cap>
          <Cap>{timeline.model_source === 'personal' ? `Speed: your calibrated pace (${timeline.confidence} confidence).`
            : timeline.model_source === 'generic' ? 'Speed: a generic estimate — enter your typical average or calibrate from a ride for a personal prediction.'
              : timeline.model_source === 'physics' ? `Speed: a weight-based estimate (${timeline.confidence} confidence).`
                : 'Speed is a placeholder — set your typical average for a real prediction.'}</Cap>
          {timeline.sleep_suggested_s > 0 && !sleepPlan && <Cap color={t.text}>Long ride — a sleep stop may be worth planning; checking the best window…</Cap>}
          {timeline.sleep_suggested_s > 0 && sleepPlan && !(sleepPlan.windows && sleepPlan.windows.length) && <Cap>You can ride this one through — no sleep stop needed.</Cap>}
          {weather && weather.summary && (<>
            <Cap color={t.text}>{`Weather: ${weather.summary.temp_min_c}–${weather.summary.temp_max_c}°C · wind ≤${weather.summary.wind_max_kmh} km/h${weather.summary.rain_max_mm > 0 ? ` · rain ≤${weather.summary.rain_max_mm} mm` : ''} · ${weather.verdict || ''}`}</Cap>
            <Check value={windFold} onChange={(v: boolean) => { setWindFold(v); applyFolds(timeline, basePayload, weather, sleepPlan, v); }}
              label={'Adjust finish for the prevailing wind' + (weather.summary.net_head_kmh !== undefined
                ? (Math.abs(weather.summary.net_head_kmh) < 1 ? ' (net wind ≈ 0 on this route)'
                  : weather.summary.net_head_kmh > 0 ? ` (net headwind ~${Math.round(weather.summary.net_head_kmh)} km/h)`
                    : ` (net tailwind ~${Math.round(-weather.summary.net_head_kmh)} km/h)`) : '')} />
          </>)}

          <Divider />
          <View style={s.row}>
            <Cap style={{ flex: 1 }}>Control</Cap><Cap style={s.num}>km</Cap><Cap style={s.num}>arrive</Cap><Cap style={s.num}>ride</Cap>
            <Cap style={s.numS}>km/h</Cap><Cap style={s.num}>margin</Cap>{weather && <Cap style={s.numS}>°C</Cap>}{weather && <Cap style={{ width: 76 }}>wind/sky</Cap>}
          </View>
          {timeline.controls.map((c: any, i: number) => {
            const early = c.early_s !== undefined && c.early_s !== null && c.early_s > 600;
            const w = wxFor(i);
            return (
              <View key={i} style={s.row}>
                <Cap color={t.text} style={{ flex: 1 }}>{c.label}</Cap>
                <Cap color={t.text} style={s.num}>{c.distance_km}</Cap>
                <Cap color={early ? AMBER : t.text} style={s.num}>{fmtClock(c.arrival_dt) + (early ? ' ⏳' : '')}</Cap>
                <Cap style={s.num}>{fmtDur(c.moving_time_s)}</Cap>
                <Cap style={s.numS}>{c.avg_speed_kmh}</Cap>
                <Cap bold color={c.margin_s === null ? t.mutedText : (c.margin_s < 0 ? BAD : GOOD)} style={s.num}>{c.margin_s === null ? '—' : fmtDur(c.margin_s)}</Cap>
                {weather && <Cap color={t.text} style={s.numS}>{w ? Math.round(w.temp_c) + '°' : ''}</Cap>}
                {weather && <Cap color={w ? (w.wind_rel === 'headwind' ? BAD : w.wind_rel === 'tailwind' ? GOOD : AMBER) : t.mutedText} style={{ width: 76 }}>
                  {w ? w.wind_rel.charAt(0).toUpperCase() + w.wind_rel.slice(1) + (w.is_dark ? ' · night' : '') : ''}</Cap>}
              </View>);
          })}
          {timeline.controls.some((c: any) => c.early_s !== null && c.early_s > 600) &&
            <Cap color={AMBER}>⏳ You'd reach it before it opens and wait: {timeline.controls.filter((c: any) => c.early_s !== null && c.early_s > 600).map((c: any) => `${c.label} (opens ${fmtClock(c.opens_dt)}, ${fmtDur(c.early_s)} early)`).join(', ')}</Cap>}
          {timeline.per_control_provisional && <Cap>Per-control times are approximate.</Cap>}

          <Divider />
          <View style={s.row}>
            <Body bold size={v3Type.caption} style={{ flex: 1 }}>Suggest my sleep</Body>
            <Button label={sleepOptsBusy || 'Try sleep plans'} variant="text" grow={false} disabled={!!sleepOptsBusy || !basePayload} onPress={suggestSleep} />
          </View>
          {!sleepOpts && <Cap>Tries different bedtimes and lengths on your ride and ranks them: finish time against how sleepy you get at your sleepiest moment (awake enough / tired but OK / too sleepy to ride safely).</Cap>}
          {sleepOpts && (<>
            <Cap color={BAD}>{`No sleep: finish ${fmtClockDay(sleepOpts.baseline.finish)}. Your sleepiest moment is ${fmtClockDay(sleepOpts.baseline.min_at)} — ${sleepinessWord(sleepOpts.baseline.band)}.`}</Cap>
            {sleepOpts.options.map((o: any, i: number) => {
              const n1 = o.nights && o.nights.length ? o.nights[0] : null;
              return (
                <View key={i} style={s.row}>
                  <Cap style={{ flex: 1 }} color={o.band === 'fine' ? t.text : (o.band === 'tired' ? AMBER : BAD)}>
                    {`${o.bed}–${o.wake} (${o.hours} h): finish ${fmtClockDay(o.finish)} · sleepiest at ${fmtClockDay(o.min_at)} — ${sleepinessWord(o.band)}` +
                     (n1 ? ` · night 1 at km ${Math.round(n1.km)}, ${n1.no_bed ? 'no bed within 20 km' : (n1.nearest_bed ? `bed: ${n1.nearest_bed.name} (${Math.abs(n1.nearest_bed.offset_km)} km)` : 'beds unknown')}` : '')}
                  </Cap>
                  <Button label="Use" variant="text" grow={false} onPress={() => useSleepOption(o)} />
                </View>);
            })}
            <Cap style={{ fontStyle: 'italic' }}>{sleepOpts.note}</Cap>
          </>)}

          {daysPlan && (<>
            <Divider />
            <Body bold size={v3Type.caption}>Days & nights</Body>
            {daysPlan.days.map((d: any) => (
              <Cap key={d.day} color={t.text}>{`Day ${d.day}: km ${Math.round(d.from_km)} → ${Math.round(d.to_km)} (${Math.round(d.km)} km), ${fmtClockDay(d.start)} → ${fmtClockDay(d.end)}`}</Cap>))}
            {daysPlan.lines.map((l: string, i: number) => (
              <Cap key={i} color={l.indexOf('No accommodation') >= 0 || l.indexOf('shorter') >= 0 ? BAD : t.mutedText}>{l}</Cap>))}
          </>)}

          {sleepPlan && sleepPlan.windows && sleepPlan.windows.length > 0 && (<>
            <Divider />
            <Body bold size={v3Type.caption}>{noRideOn ? 'What the body clock would suggest (you set your own hours)' : 'Sleep plan'}</Body>
            {sleepPlan.windows.map((w: any) => (
              <Cap key={w.night} color={w.cutoff_ok ? t.text : BAD}>
                {`Night ${w.night}: sleep ${w.start_local}–${w.end_local} (~${(w.duration_s / 3600).toFixed(1)}h) near km ${w.km}${w.near_control ? ` (${w.near_control})` : ''} — ${w.reason}` +
                 (w.temp_c !== null ? `, ${Math.round(w.temp_c)}°C` : '') + (w.cutoff_ok ? '' : '  ⚠ tightens a cutoff')}
              </Cap>))}
            <Cap>Suggestion only — placed in the hours worst for riding (darkest, coldest, body-clock low).</Cap>
          </>)}

          {pois && pois.summary && pois.summary.length > 0 && (<>
            <Divider />
            <Body bold size={v3Type.caption}>Resupply</Body>
            {pois.summary.map((l: string, i: number) => <Cap key={i} color={l.indexOf('⚠') >= 0 ? BAD : t.text}>{l}</Cap>)}
          </>)}
          {openGaps && pois && openGaps.lines.map((l: string, i: number) => <Cap key={'og' + i} color={t.text}>{l}</Cap>)}
          {openGaps && pois && <Cap style={{ fontStyle: 'italic' }}>{openGaps.note}</Cap>}
          {!(pois && pois.summary && pois.summary.length) && <Cap style={{ marginTop: v3Spacing.small }}>Tip: load your route's PitStopper export (step 1) to see water, food & services here and in the critical points.</Cap>}

          {alerts && alerts.alerts && alerts.alerts.length > 0 && (<>
            <Divider />
            <Check value={alertsImportantOnly} onChange={setAlertsImportantOnly} label="Critical points — only warnings" />
            {alerts.alerts.filter((a: any) => !alertsImportantOnly || a.severity !== 'info').map((a: any, i: number) => (
              <View key={i} style={s.row}>
                <Cap style={{ width: 56 }}>{'km ' + Math.round(a.km)}</Cap>
                <Cap style={{ flex: 1 }} color={a.severity === 'critical' ? BAD : (a.severity === 'warn' ? AMBER : t.mutedText)}>{a.text}</Cap>
              </View>))}
          </>)}
        </Card>
      )}

      {showResults && tlOk && (
        <Card style={{ width: '100%', gap: v3Spacing.small }}>
          <Body bold>What if…</Body>
          {([['Speed', `${whatifSpeed > 0 ? '+' : ''}${whatifSpeed} km/h`, () => { const v = whatifSpeed - 1; setWhatifSpeed(v); applyWhatif(v, whatifStopMin, whatifSleepH); },
              () => { const v = whatifSpeed + 1; setWhatifSpeed(v); applyWhatif(v, whatifStopMin, whatifSleepH); }],
             ['Stops', `${whatifStopMin > 0 ? '+' : ''}${whatifStopMin} min`, () => { const v = whatifStopMin - 15; setWhatifStopMin(v); applyWhatif(whatifSpeed, v, whatifSleepH); },
              () => { const v = whatifStopMin + 15; setWhatifStopMin(v); applyWhatif(whatifSpeed, v, whatifSleepH); }],
             ['Sleep', `+${whatifSleepH} h`, () => { const v = Math.max(0, whatifSleepH - 0.5); setWhatifSleepH(v); applyWhatif(whatifSpeed, whatifStopMin, v); },
              () => { const v = whatifSleepH + 0.5; setWhatifSleepH(v); applyWhatif(whatifSpeed, whatifStopMin, v); }]] as Array<[string, string, () => void, () => void]>)
            .map(([label, val, minus, plus]) => (
              <View key={label} style={s.row}>
                <Cap color={t.text} style={{ width: 70 }}>{label}</Cap>
                <Button label="−" variant="text" grow={false} onPress={minus} />
                <Cap color={t.text} style={{ width: 90, textAlign: 'center' }}>{val}</Cap>
                <Button label="+" variant="text" grow={false} onPress={plus} />
              </View>))}
          {scenario && (<>
            <Divider />
            <View style={[s.row, { flexWrap: 'wrap' }]}>
              <Stat label="NEW FINISH" value={fmtClock(scenario.finish_eta_dt)} big />
              <Stat label="finish change" value={deltaTxt(scenario.elapsed_time_s, timeline.elapsed_time_s)}
                color={scenario.elapsed_time_s > timeline.elapsed_time_s + 30 ? BAD : GOOD} />
              <Stat label="cutoff change" value={scenario.worst_margin_s !== null && timeline.worst_margin_s !== null ? deltaTxt(scenario.worst_margin_s, timeline.worst_margin_s) : '—'}
                color={scenario.worst_margin_s !== null && scenario.worst_margin_s < 0 ? BAD : t.text} />
            </View>
            {newlyAtRisk() > 0 && <Cap color={BAD}>⚠ {newlyAtRisk()} control(s) would now miss the cutoff.</Cap>}
          </>)}
        </Card>
      )}

      {showResults && scenarios.length > 0 && (
        <Card style={{ width: '100%', gap: 4 }}>
          <Body bold>Saved scenarios</Body>
          <View style={s.row}>
            <Cap style={{ flex: 1 }}>name</Cap><Cap style={{ width: 72 }}>finish</Cap><Cap style={s.num}>elapsed</Cap>
            <Cap style={s.num}>tightest</Cap><View style={{ width: 100 }} />{/* Open (64) + gap + ✕ (28) */}
          </View>
          {scenarios.map((sc: any) => (
            <View key={sc.name} style={s.row}>
              <Cap color={t.text} style={{ flex: 1 }}>{sc.name}</Cap>
              <Cap color={t.text} style={{ width: 72 }}>{(sc.summary && sc.summary.finish) || '—'}</Cap>
              <Cap color={t.text} style={s.num}>{sc.summary && sc.summary.elapsed_time_s ? fmtDur(sc.summary.elapsed_time_s) : '—'}</Cap>
              <Cap color={sc.summary && sc.summary.worst_margin_s < 0 ? BAD : GOOD} style={s.num}>
                {sc.summary && sc.summary.worst_margin_s !== null && sc.summary.worst_margin_s !== undefined
                  ? (sc.summary.worst_margin_s < 0 ? '-' + fmtDur(-sc.summary.worst_margin_s) : '+' + fmtDur(sc.summary.worst_margin_s)) : '—'}</Cap>
              <View style={{ width: 64, alignItems: 'flex-end' }}>
                <Button label="Open" variant="text" grow={false} onPress={() => openScenario(sc.name)} />
              </View>
              <TouchableOpacity onPress={() => raceApi.scenarioDelete({ name: sc.name }).then(refreshScenarios)} style={{ width: 28, alignItems: 'center' }}>
                <Body color={t.mutedText}>✕</Body>
              </TouchableOpacity>
            </View>))}
        </Card>
      )}

      {pdfB64 && <PdfTextReader base64={pdfB64} onDone={onPdfText} />}

      {/* Import roadbook: paste the control table, or From PDF… (read by pdf.js; the desktop uses pdftotext) */}
      <Modal visible={rbOpen} transparent animationType="fade" onRequestClose={() => setRbOpen(false)}>
        <Pressable style={s.backdrop} onPress={() => setRbOpen(false)}>
          <Pressable style={s.dialog} onPress={() => {}}>
            <Body bold size={v3Type.subtitle}>Import roadbook</Body>
            <Cap>Paste the control table from your brevet roadbook (the whole page is fine — only the "C1 …" control lines with a closing time are used). Each control's official closing time becomes its "must arrive by".</Cap>
            <TextInput multiline value={rbText} onChangeText={setRbText} placeholder="C1 - FROIDCHAPELLE   …   112,5   9:19   12:30"
              placeholderTextColor={t.mutedText} style={[s.input, { minHeight: 140, textAlignVertical: 'top' }]} />
            <View style={s.row}>
              <Cap style={{ flex: 1 }}>…or load a PDF roadbook:</Cap>
              <Button label="From PDF…" variant="text" grow={false} onPress={importRoadbookPdf} />
            </View>
            <View style={[s.row, { justifyContent: 'flex-end' }]}>
              <Button label="Cancel" variant="text" grow={false} onPress={() => setRbOpen(false)} />
              <Button label="Import" variant="filled" grow={false} onPress={() => { setRbOpen(false); importRoadbookText(rbText); }} />
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      <Modal visible={saveOpen} transparent animationType="fade" onRequestClose={() => setSaveOpen(false)}>
        <Pressable style={s.backdrop} onPress={() => setSaveOpen(false)}>
          <Pressable style={s.dialog} onPress={() => {}}>
            <Body bold size={v3Type.subtitle}>Save scenario</Body>
            <Cap>Name this scenario (e.g. "start 04:00, 2h sleep") to compare later.</Cap>
            <TextInput value={saveName} onChangeText={setSaveName} placeholder="Scenario name" placeholderTextColor={t.mutedText} style={s.input} />
            <View style={[s.row, { justifyContent: 'flex-end' }]}>
              <Button label="Cancel" variant="text" grow={false} onPress={() => setSaveOpen(false)} />
              <Button label="Save" variant="filled" grow={false} onPress={() => { setSaveOpen(false); saveScenario(saveName); }} />
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </ScrollView>
  );
}

const styles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: v3Spacing.medium, gap: v3Spacing.medium, paddingBottom: 48 },
  row: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small },
  input: { borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text, backgroundColor: t.card,
           paddingHorizontal: 10, paddingVertical: 6, fontSize: v3Type.body },
  nested: { backgroundColor: t.cardNested, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.border, padding: v3Spacing.medium, gap: v3Spacing.small },
  num: { width: 56, textAlign: 'right' },
  numS: { width: 40, textAlign: 'right' },
  backdrop: { flex: 1, backgroundColor: '#00000088', justifyContent: 'center', padding: v3Spacing.large },
  dialog: { backgroundColor: t.card, borderRadius: v3Radius.card, borderWidth: 1, borderColor: t.border, padding: v3Spacing.medium, gap: v3Spacing.small },
});

