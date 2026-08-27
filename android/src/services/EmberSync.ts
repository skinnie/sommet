import AsyncStorage from '@react-native-async-storage/async-storage';
import { EmberData, EmberEntry, ensureUids } from './EmberStore';

// Two-way merge of the Ember log with the shared NAS store - the Android port of the desktop
// backend's _ember_sync/_ember_http (server.py). All three clients (iPhone PWA, desktop, Android)
// read/write the same per-day documents on the NAS, keyed by uid with tombstones, so a coffee
// logged on one device shows on the others and a delete propagates. Config (url + token) is set
// in Settings. Best-effort: any network failure leaves the local store untouched.

const CFG_KEY = 'ember.sync.cfg';
const EMBER_COFFEE_IDS = new Set(['black_coffee', 'espresso', 'americano', 'coffee_milk', 'latte', 'cappuccino']);

export interface EmberSyncCfg { url: string; token: string; }

export async function getEmberSyncCfg(): Promise<EmberSyncCfg | null> {
  try {
    const raw = await AsyncStorage.getItem(CFG_KEY);
    const c = raw ? JSON.parse(raw) : null;
    return c && c.url ? { url: c.url, token: c.token ?? '' } : null;
  } catch { return null; }
}

export async function setEmberSyncCfg(cfg: EmberSyncCfg | null): Promise<void> {
  if (!cfg || !cfg.url) { await AsyncStorage.removeItem(CFG_KEY); return; }
  await AsyncStorage.setItem(CFG_KEY, JSON.stringify({ url: cfg.url, token: cfg.token ?? '' }));
}

function dayKey(ts: number): string {
  const d = new Date(ts || 0);
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

async function emberHttp(url: string, token: string, method: 'GET' | 'POST',
                          params?: Record<string, string>, body?: any): Promise<any | null> {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  const headers: Record<string, string> = {};
  if (token) headers['X-Ember-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6000);
    const res = await fetch(url + q, {
      method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (res.status === 404) return {};        // no file yet - normal on first GET
    if (!res.ok) return null;
    const raw = await res.text();
    return raw.trim() ? JSON.parse(raw) : {};
  } catch { return null; }
}

function normRemoteEntry(e: any): EmberEntry {
  let t = e.type;
  if (t === 'drink' && EMBER_COFFEE_IDS.has(e.drinkId)) t = 'coffee'; // a phone coffee-family drink
  return {
    uid: e.uid, ts: e.ts, type: t, name: e.name ?? 'Drink',
    kcal: Number(e.kcal || 0), caffeineMg: Number(e.caffeineMg || 0), volumeMl: Number(e.volumeMl || 0),
  };
}

/**
 * Two-way merge for today + yesterday against the NAS store (port of _ember_sync). Mutates
 * `data` and returns whether anything changed (caller saves if so). No-op without a config.
 */
export async function syncEmberNas(data: EmberData): Promise<boolean> {
  const cfg = await getEmberSyncCfg();
  if (!cfg) return false;
  const { url, token } = cfg;
  let changed = ensureUids(data);
  const now = Date.now();
  const days = Array.from(new Set([dayKey(now), dayKey(now - 86400 * 1000)])).sort();
  const deleted = new Set<string>(data.deleted ?? []);
  const seenE = new Set(data.entries.map(e => e.uid));
  const seenF = new Set(data.fasts.map(f => f.uid));

  for (const day of days) {
    const remote = await emberHttp(url, token, 'GET', { date: day });
    if (remote === null) continue;            // unreachable this round - leave local untouched
    for (const u of remote.deleted ?? []) deleted.add(u);
    for (const e of remote.entries ?? []) {
      const uid = e.uid;
      if (uid && !seenE.has(uid) && !deleted.has(uid)) {
        data.entries.push(normRemoteEntry(e)); seenE.add(uid); changed = true;
      }
    }
    for (const f of remote.fasts ?? []) {
      const uid = f.uid;
      if (!uid || deleted.has(uid)) continue;
      if (!seenF.has(uid)) {
        data.fasts.push({ uid, start: f.start, end: f.end ?? null, goalHours: f.goalHours ?? 16 });
        seenF.add(uid); changed = true;
      } else {
        for (const lf of data.fasts) if (lf.uid === uid && lf.end === null && f.end) { lf.end = f.end; changed = true; }
      }
    }
    // a tombstone from another device drops the matching local item
    const before = data.entries.length + data.fasts.length;
    data.entries = data.entries.filter(e => !deleted.has(e.uid!));
    data.fasts = data.fasts.filter(f => !deleted.has(f.uid!));
    if (data.entries.length + data.fasts.length !== before) changed = true;
    // push the union for this day back up
    const dayE = data.entries.filter(e => dayKey(e.ts) === day).map(e => ({
      uid: e.uid, ts: e.ts, type: e.type, name: e.name, kcal: e.kcal ?? 0,
      caffeineMg: e.caffeineMg ?? 0, volumeMl: e.volumeMl ?? 0,
    }));
    const dayF = data.fasts.filter(f => dayKey(f.start) === day).map(f => ({
      uid: f.uid, start: f.start, end: f.end, goalHours: f.goalHours ?? 16,
    }));
    await emberHttp(url, token, 'POST', undefined, {
      source: 'ember', date: day, updated: now,
      entries: dayE, fasts: dayF, deleted: Array.from(deleted).sort(),
    });
  }
  data.deleted = Array.from(deleted);
  return changed;
}
