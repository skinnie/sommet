import AsyncStorage from '@react-native-async-storage/async-storage';
import { getSommetSyncCfg, fetchT, adoptNewToken } from './SommetSync';
import type { WeightPoint } from './WellnessService';

// Weight readings on the user's own Sommet Sync server (the NAS), the same collection the desktop
// uses (André, 2026-11-20: "can't we have weight figures saved on the NAS, so if intervals.icu is
// not connected we still have the readings?"). One record per day, uid = the date, the merged
// reading of that day with the source it came from. Pulled in full (a reading a day is a few
// thousand small records at most) and kept here too, so the readings also show offline.

const CACHE_KEY = 'weight.nasCache';

export async function loadNasCache(): Promise<WeightPoint[]> {
  try { return JSON.parse((await AsyncStorage.getItem(CACHE_KEY)) || '[]'); } catch { return []; }
}

function clean(r: any): WeightPoint | null {
  if (!r || typeof r.date !== 'string' || !(Number(r.weightKg) > 0)) return null;
  const { uid, srv, updated_at, ...rest } = r;   // eslint-disable-line @typescript-eslint/no-unused-vars
  return { ...rest, weightKg: Number(r.weightKg) } as WeightPoint;
}

/** The server's readings, or null when it isn't set up or can't be reached (then use the cache). */
export async function pullNasWeights(): Promise<WeightPoint[] | null> {
  const cfg = await getSommetSyncCfg();
  if (!cfg || !cfg.token) return null;
  try {
    const res = await fetchT(cfg.url + '?' + new URLSearchParams({ c: 'weight', since: '0' }).toString(),
      { method: 'GET', headers: { 'X-Sommet-Token': cfg.token } }, 15000);
    await adoptNewToken(cfg.url, cfg.token, res);
    if (!res.ok) return null;
    const body = JSON.parse((await res.text()) || '{}');
    const out = ((body.records ?? []) as any[]).map(clean).filter((p): p is WeightPoint => !!p);
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(out));
    return out;
  } catch { return null; }
}

function same(a: WeightPoint, b: WeightPoint): boolean {
  const ka = Object.keys(a).filter(k => (a as any)[k] !== undefined).sort();
  const kb = Object.keys(b).filter(k => (b as any)[k] !== undefined).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i] && (a as any)[k] === (b as any)[k]);
}

/** Send the readings the server doesn't hold exactly. Only after a successful pull this run. */
export async function pushNasWeights(merged: WeightPoint[], onServer: WeightPoint[]): Promise<number> {
  const cfg = await getSommetSyncCfg();
  if (!cfg || !cfg.token) return 0;
  const have = new Map(onServer.map(p => [p.date, p]));
  const now = Date.now();
  const send = merged.filter(p => p.date && p.weightKg > 0 && !(have.has(p.date) && same(have.get(p.date)!, p)));
  if (send.length === 0) return 0;
  try {
    const res = await fetchT(cfg.url + '?' + new URLSearchParams({ c: 'weight' }).toString(), {
      method: 'POST', headers: { 'X-Sommet-Token': cfg.token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: send.map(p => ({ ...p, uid: p.date, updated_at: now })), deleted: [] }),
    }, 15000);
    await adoptNewToken(cfg.url, cfg.token, res);
    if (!res.ok) return 0;
    for (const p of send) have.set(p.date, p);
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(Array.from(have.values())));
    return send.length;
  } catch { return 0; }
}
