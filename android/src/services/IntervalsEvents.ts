import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import type { Workout, WorkoutStep } from './WorkoutSource';

// Mirror a Workout Calendar entry onto the athlete's intervals.icu calendar - the TypeScript port
// of tools/intervals_events.py (same workout_doc, same external_id, same create-then-update rule).
// The plan stays local first: without intervals.icu nothing here runs. With it, each workout made
// in Sommet is also a planned workout there. No duplicates: the entry keeps its intervals.icu event
// id after the first push and every later push updates that event; its external_id is
// "sommet:<uid>" so the importer recognises it.

const API_BASE = 'https://intervals.icu/api/v1';
const TYPE_TEXT: Record<string, string> = {
  warmup: 'Warm up', interval: 'Interval', recovery: 'Recovery', cooldown: 'Cool down', work: 'Interval', rest: 'Recovery',
};
const TARGETS: Record<string, [string, string]> = { power: ['power', 'w'], hr: ['hr', 'bpm'], cadence: ['cadence', 'rpm'] };

export interface MirrorEntry {
  uid: string; date: string; workout: Workout; device?: string; mode?: string; icuEventId?: number;
}

export const newUid = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;

function step(st: WorkoutStep): any {
  const tn = st.type.typeName;
  const out: any = { text: TYPE_TEXT[tn] ?? tn.charAt(0).toUpperCase() + tn.slice(1) };
  if (tn === 'warmup') out.warmup = true;
  else if (tn === 'cooldown') out.cooldown = true;
  const d = st.duration;
  if (d?.durationName === 'time') out.duration = Math.trunc(d.value || 0);
  else if (d?.durationName === 'distance') out.distance = Math.trunc(d.value || 0);
  const t: any = st.target;
  if (t && TARGETS[t.targetName]) {
    const [key, units] = TARGETS[t.targetName];
    const lo = t.valueRange?.min ?? t.value;
    const hi = t.valueRange?.max ?? lo;
    if (lo != null) out[key] = { start: lo, end: hi, units };
  }
  return out;
}

/** Flat repeatStart/repeatEnd -> intervals.icu workout_doc (nested reps). */
export function workoutDoc(w: Workout): { steps: any[] } {
  const steps: any[] = [];
  const src = w.steps ?? [];
  for (let i = 0; i < src.length; i++) {
    const tn = src[i].type.typeName;
    if (tn === 'repeatStart') {
      const reps = Math.trunc(src[i].type.value || 1);
      const inner: any[] = [];
      for (i++; i < src.length && src[i].type.typeName !== 'repeatEnd'; i++) inner.push(step(src[i]));
      steps.push({ text: `${reps}x`, reps, steps: inner });
    } else if (tn !== 'repeatEnd') {
      steps.push(step(src[i]));
    }
  }
  return { steps };
}

export function sportType(e: MirrorEntry): string {
  if (e.device === 'bryton' || e.device === 'magene') return 'Ride';
  const mode = (e.mode ?? '').toLowerCase();
  for (const [word, t] of [['run', 'Run'], ['trail', 'Run'], ['walk', 'Walk'], ['hik', 'Walk'], ['swim', 'Swim'], ['row', 'Rowing'], ['ski', 'NordicSki']]) {
    if (mode.includes(word)) return t;
  }
  return 'Ride';
}

export function eventBody(e: MirrorEntry) {
  return {
    category: 'WORKOUT',
    start_date_local: `${e.date}T00:00:00`,
    name: e.workout.name || 'Workout',
    type: sportType(e),
    external_id: `sommet:${e.uid}`,
    workout_doc: workoutDoc(e.workout),
  };
}

async function call(method: string, path: string, body?: any): Promise<Response | null> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return null;
  return fetch(`${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}${path}`, {
    method,
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`), 'User-Agent': 'Sommet/1.0',
      ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
}

/** Create or update the entry's event. Returns the event id, or null without intervals.icu. */
export async function pushEntry(e: MirrorEntry): Promise<number | null> {
  const body = eventBody(e);
  if (e.icuEventId) {
    const r = await call('PUT', `/events/${e.icuEventId}`, body);
    if (!r) return null;
    if (r.ok) return (await r.json()).id ?? e.icuEventId;
    if (r.status !== 404) throw new Error(`intervals.icu: ${r.status}`);
    // deleted there meanwhile - create it again, once
  }
  const r = await call('POST', '/events', body);
  if (!r) return null;
  if (!r.ok) throw new Error(`intervals.icu: ${r.status}`);
  return (await r.json()).id ?? null;
}

export async function deleteEvent(eventId: number): Promise<void> {
  const r = await call('DELETE', `/events/${eventId}`);
  if (r && !r.ok && r.status !== 404) throw new Error(`intervals.icu: ${r.status}`);
}
