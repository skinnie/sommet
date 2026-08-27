import AsyncStorage from '@react-native-async-storage/async-storage';

// Ember local log store - the Android port of the desktop backend's Ember logic
// (server.py _ember_load/_ember_save/_handle_ember_log/_ember_summary). The desktop keeps the
// log in a local JSON file behind its Python backend; the Android app is standalone, so it owns
// the same store here (AsyncStorage) and computes the same summary. Cross-device merge with the
// iPhone PWA / desktop happens in EmberSync.ts against the shared NAS store, exactly like the
// desktop's _ember_sync. Data model: entries[] (drinks/food/water), fasts[], deleted[] (uid
// tombstones so a delete propagates across devices).

const KEY = 'ember.store';

export interface EmberEntry {
  uid?: string;
  ts: number;
  type: 'coffee' | 'water' | 'meal' | 'drink';
  name: string;
  kcal?: number;
  caffeineMg?: number;
  volumeMl?: number;
  protein?: number;
  carbs?: number;
  fat?: number;
}

export interface EmberFast {
  uid?: string;
  start: number;
  end: number | null;
  goalHours: number;
}

export interface EmberData {
  entries: EmberEntry[];
  fasts: EmberFast[];
  deleted: string[];
}

export interface EmberToday {
  kcal: number;
  coffees: number;
  waterMl: number;
  fastActive: boolean;
  fastStart: number | null;
  fastGoalHours: number;
}

export interface EmberSummary {
  today: EmberToday;
  days: { date: string; kcal: number; coffee: number; waterL: number }[];
  fasts: { start: number; end: number; hours: number }[];
}

export type LogBody =
  | { type: 'coffee' }
  | { type: 'coffee-undo' }
  | { type: 'water'; volumeMl?: number }
  | { type: 'meal'; name?: string; kcal?: number; protein?: number; carbs?: number; fat?: number }
  | { type: 'drink'; name?: string; kcal?: number; caffeineMg?: number; volumeMl?: number; isCoffee?: boolean; breaksFast?: boolean }
  | { type: 'fast-start'; goalHours?: number }
  | { type: 'fast-end' };

export async function loadEmber(): Promise<EmberData> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    const d = raw ? JSON.parse(raw) : {};
    return { entries: d.entries ?? [], fasts: d.fasts ?? [], deleted: d.deleted ?? [] };
  } catch {
    return { entries: [], fasts: [], deleted: [] };
  }
}

export async function saveEmber(data: EmberData): Promise<void> {
  try { await AsyncStorage.setItem(KEY, JSON.stringify(data)); } catch { /* best-effort */ }
}

// Stable device-tagged ids (mirrors _ember_ensure_uids) so a re-push dedupes and a pulled event
// keeps its origin id. Android events are tagged "an-" (desktop uses "dt-", the phone its own).
export function ensureUids(data: EmberData): boolean {
  let changed = false;
  for (const e of data.entries) if (!e.uid) { e.uid = `an-${e.ts}-${e.type}`; changed = true; }
  for (const f of data.fasts) if (!f.uid) { f.uid = `an-${f.start}-fast`; changed = true; }
  return changed;
}

/** Apply one log event to the store (port of _handle_ember_log). Returns the mutated data. */
export function applyLog(data: EmberData, body: LogBody): EmberData {
  const now = Date.now();
  switch (body.type) {
    case 'coffee':
      data.entries.push({ ts: now, type: 'coffee', name: 'Coffee', kcal: 2, caffeineMg: 95 });
      break;
    case 'coffee-undo':
      for (let i = data.entries.length - 1; i >= 0; i--) {
        if (data.entries[i].type === 'coffee') {
          const uid = data.entries[i].uid;
          if (uid) data.deleted.push(uid); // already synced -> tombstone so the delete propagates
          data.entries.splice(i, 1);
          break;
        }
      }
      break;
    case 'water':
      data.entries.push({ ts: now, type: 'water', name: 'Water', volumeMl: Math.round(body.volumeMl ?? 250) });
      break;
    case 'meal':
      data.entries.push({
        ts: now, type: 'meal', name: body.name ?? 'Meal', kcal: Math.round(body.kcal ?? 0),
        protein: Math.round(body.protein ?? 0), carbs: Math.round(body.carbs ?? 0), fat: Math.round(body.fat ?? 0),
      });
      break;
    case 'drink': {
      const et = body.isCoffee ? 'coffee' : (body.volumeMl ? 'water' : 'drink');
      data.entries.push({
        ts: now, type: et as EmberEntry['type'], name: body.name ?? 'Drink',
        kcal: Math.round(body.kcal ?? 0), caffeineMg: Math.round(body.caffeineMg ?? 0),
        volumeMl: Math.round(body.volumeMl ?? 0),
      });
      if (body.breaksFast) for (const f of data.fasts) if (f.end === null) f.end = now;
      break;
    }
    case 'fast-start':
      for (const f of data.fasts) if (f.end === null) f.end = now;
      data.fasts.push({ start: now, end: null, goalHours: Math.round(body.goalHours ?? 16) });
      break;
    case 'fast-end':
      for (const f of data.fasts) if (f.end === null) f.end = now;
      break;
  }
  return data;
}

/** 14-day summary + today's totals + active fast (port of _ember_summary). */
export function emberSummary(data: EmberData): EmberSummary {
  const now = Date.now();
  const d0 = new Date(); d0.setHours(0, 0, 0, 0);
  const t0 = d0.getTime();
  const since = now - 14 * 86400 * 1000;
  const dayKey = (ts: number) => {
    const dt = new Date(ts);
    const m = `${dt.getMonth() + 1}`.padStart(2, '0');
    const day = `${dt.getDate()}`.padStart(2, '0');
    return `${dt.getFullYear()}-${m}-${day}`;
  };
  const entries = data.entries;
  const today = entries.filter(e => (e.ts ?? 0) >= t0);
  const active = data.fasts.find(f => f.end === null) ?? null;
  const perday: Record<string, { date: string; kcal: number; coffee: number; waterL: number }> = {};
  for (const e of entries) {
    if ((e.ts ?? 0) < since) continue;
    const k = dayKey(e.ts);
    const r = perday[k] ?? (perday[k] = { date: k, kcal: 0, coffee: 0, waterL: 0 });
    r.kcal += e.kcal ?? 0;
    r.waterL += (e.volumeMl ?? 0) / 1000;
    if (e.type === 'coffee') r.coffee += 1;
  }
  for (const r of Object.values(perday)) r.waterL = Math.round(r.waterL * 100) / 100;
  const fasts = data.fasts
    .filter(f => f.end && f.end >= since)
    .map(f => ({ start: f.start, end: f.end as number, hours: Math.round((f.end! - f.start) / 3600000 * 10) / 10 }));
  return {
    today: {
      kcal: today.reduce((s, e) => s + (e.kcal ?? 0), 0),
      coffees: today.filter(e => e.type === 'coffee').length,
      waterMl: today.reduce((s, e) => s + (e.volumeMl ?? 0), 0),
      fastActive: !!active,
      fastStart: active ? active.start : null,
      fastGoalHours: active ? active.goalHours : 16,
    },
    days: Object.keys(perday).sort().map(k => perday[k]),
    fasts,
  };
}
