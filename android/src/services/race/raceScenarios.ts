// Twin of tools/race_scenarios.py (the pure part): named what-if scenarios {name, saved_at, ui,
// summary}. Store in/out like raceStopmem; the app persists it in AsyncStorage (raceStorage.ts).

export interface Scenario { name: string; saved_at: string; ui: any; summary: any }
export interface ScenarioStore { scenarios: Scenario[] }

const pad = (n: number) => String(n).padStart(2, '0');

/** datetime.now().isoformat(timespec="seconds") in the device's local time. */
export function nowIsoSeconds(d = new Date()): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function save(name: string, ui: any, summary: any, store: ScenarioStore, savedAt = nowIsoSeconds()): [any, ScenarioStore] {
  const n = (name || '').trim();
  if (!n) return [{ ok: false, error: 'a name is required' }, store];
  const rows = (store.scenarios || []).filter(s => s.name !== n);
  rows.push({ name: n, saved_at: savedAt, ui: ui || {}, summary: summary || {} });
  return [{ ok: true, n: rows.length }, { ...store, scenarios: rows }];
}

export function listing(store: ScenarioStore) {
  const rows = (store.scenarios || []).map(s => ({ name: s.name, saved_at: s.saved_at, summary: s.summary || {} }));
  // Python: sort(key=saved_at or "", reverse=True) - stable, so equal times keep reverse... Python's
  // reverse sort keeps equal items in ORIGINAL order (reverse=True preserves stability).
  const idx = rows.map((r, i) => [r, i] as [typeof r, number]);
  idx.sort((a, b) => {
    const ka = a[0].saved_at || '', kb = b[0].saved_at || '';
    return ka < kb ? 1 : ka > kb ? -1 : a[1] - b[1];
  });
  return { ok: true, scenarios: idx.map(x => x[0]) };
}

export function get(name: string, store: ScenarioStore) {
  const s = (store.scenarios || []).find(x => x.name === name);
  return s ? { ok: true, scenario: s } : { ok: false, error: 'not found' };
}

export function remove(name: string, store: ScenarioStore): [any, ScenarioStore] {
  const rows = (store.scenarios || []).filter(s => s.name !== name);
  return [{ ok: true, n: rows.length }, { ...store, scenarios: rows }];
}
