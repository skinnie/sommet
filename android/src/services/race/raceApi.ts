// The Race Plan's "backend" on Android: the same calls the desktop page makes to /api/race/*, answered
// in-process by the TypeScript twins of tools/race_*.py (parity-tested), with the desktop's JSON stores
// (~/.sommet/race_stops.json, race_scenarios.json) kept in AsyncStorage instead. Request and response
// shapes are the desktop's, so RacePlanScreen mirrors RacePlanPage.qml call for call.
import AsyncStorage from '@react-native-async-storage/async-storage';
import RNFS from 'react-native-fs';
import { planFromBody } from './raceEvent';
import { timelineFromBody } from './raceTimeline';
import { weatherFromBody } from './raceWeather';
import { sleepFromBody } from './raceSleep';
import { poisFromBody } from './racePois';
import { alertsFromBody } from './raceAlerts';
import { daysFromBody } from './raceDays';
import { suggestSleepAsync } from './raceSleepopt';
import { importRoadbook } from './roadbookImport';
import { calibrateFitSummary } from './raceCalibration';
import * as stopmem from './raceStopmem';
import * as scen from './raceScenarios';

const K = {
  stops: 'race:stops', scenarios: 'race:scenarios', customTag: 'race:customTag', enabled: 'race:enabled', session: 'race:session',
};

async function readJson<T>(key: string, dflt: T): Promise<T> {
  try { const v = await AsyncStorage.getItem(key); return v ? JSON.parse(v) : dflt; } catch { return dflt; }
}
const writeJson = (key: string, v: any) => AsyncStorage.setItem(key, JSON.stringify(v)).catch(() => undefined);

/** Yield to the UI before a heavier synchronous computation (a long route's timeline). */
const tick = () => new Promise<void>(r => setTimeout(r, 0));

export const raceApi = {
  planCreate: async (body: any) => { await tick(); return planFromBody(body); },
  timeline: async (body: any) => { await tick(); return timelineFromBody(body); },
  weather: (body: any) => weatherFromBody(body),                       // online (Open-Meteo)
  sleep: async (body: any) => { await tick(); return sleepFromBody(body); },
  pois: async (body: any) => { await tick(); return poisFromBody(body); },
  alerts: async (body: any) => { await tick(); return alertsFromBody(body); },
  days: async (body: any) => { await tick(); return daysFromBody(body); },
  sleepOptions: (body: any, onProgress?: (d: number, t: number) => void) => suggestSleepAsync(body, onProgress),
  roadbook: async (body: { text?: string }) => importRoadbook(body),

  /** One-FIT calibration: the FIT is decoded by the app's shared decoder (activity_streams). */
  calibrateFit: async (path: string) => {
    const { streamsFromFit } = require('../../config/activityStreams');
    const b64 = await RNFS.readFile(path, 'base64');
    const bin = atob(b64);                                   // Hermes has atob
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    await tick();
    const st = streamsFromFit(bytes, 500);
    if (!st || !st.ok) return { ok: false, error: 'could not read that FIT' };
    return calibrateFitSummary(st.summary || {});
  },

  stopSuggest: async (body: { distance_km: number }) =>
    stopmem.suggest(Number(body.distance_km || 0), await readJson(K.stops, { points: [] })),
  stopRecord: async (body: { distance_km: number; hours: number }) => {
    const [r, st] = stopmem.record(Number(body.distance_km || 0), Number(body.hours || 0), await readJson(K.stops, { points: [] }));
    await writeJson(K.stops, st);
    return r;
  },

  scenarioSave: async (body: { name: string; ui: any; summary: any }) => {
    const [r, st] = scen.save(body.name, body.ui, body.summary, await readJson(K.scenarios, { scenarios: [] }));
    if (r.ok) await writeJson(K.scenarios, st);
    return r;
  },
  scenarioList: async () => scen.listing(await readJson(K.scenarios, { scenarios: [] })),
  scenarioGet: async (body: { name: string }) => scen.get(body.name, await readJson(K.scenarios, { scenarios: [] })),
  scenarioDelete: async (body: { name: string }) => {
    const [r, st] = scen.remove(body.name, await readJson(K.scenarios, { scenarios: [] }));
    await writeJson(K.scenarios, st);
    return r;
  },
};

// ---- settings + the sticky session (the desktop's PlanStore / Settings) ----------------------------

/** "Show Race Plan in the menu" (desktop: Theme.racePlanEnabled, off by default). */
export const isRacePlanEnabled = async () => (await AsyncStorage.getItem(K.enabled).catch(() => null)) === '1';
export const setRacePlanEnabled = (on: boolean) => AsyncStorage.setItem(K.enabled, on ? '1' : '0').catch(() => undefined);

/** What the rider's PitStopper custom tag means (cemetery | water | food | other); cemetery by default. */
export const getCustomTag = async () => (await AsyncStorage.getItem(K.customTag).catch(() => null)) || 'cemetery';
export const setCustomTag = (t: string) => AsyncStorage.setItem(K.customTag, t).catch(() => undefined);

/** The planner's answers + loaded route, so leaving the screen doesn't lose them. */
export const loadSession = () => readJson<any>(K.session, null);
export const saveSession = (ui: any) => writeJson(K.session, ui);
