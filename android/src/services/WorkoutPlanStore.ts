import AsyncStorage from '@react-native-async-storage/async-storage';
import { base64ToBytes, bytesToBase64 } from './Base64';

// The Workout Calendar's plan, kept on the phone so it survives an app restart (it used to live
// only in the screen's state). Works the same with or without intervals.icu: the plan is local
// first; intervals.icu is an optional source (import) on top. Compiled watch apps carry a binary,
// stored as base64. Past entries older than a week are dropped on load so it doesn't grow forever.

const KEY = 'workoutCalendarPlan.v1';
const KEEP_PAST_DAYS = 7;

export async function loadPlan<T extends { date: string; compiled?: any }>(): Promise<T[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return [];
    const cutoff = new Date(Date.now() - KEEP_PAST_DAYS * 86400000).toISOString().slice(0, 10);
    return (JSON.parse(raw) as any[])
      .filter(e => e && typeof e.date === 'string' && e.date >= cutoff)
      .map(e => (e.compiled && typeof e.compiled.binary === 'string'
        ? { ...e, compiled: { ...e.compiled, binary: base64ToBytes(e.compiled.binary) } } : e));
  } catch { return []; }
}

export async function savePlan(plan: { compiled?: any }[]): Promise<void> {
  try {
    const out = plan.map(e => (e.compiled?.binary instanceof Uint8Array
      ? { ...e, compiled: { ...e.compiled, binary: bytesToBase64(e.compiled.binary) } } : e));
    await AsyncStorage.setItem(KEY, JSON.stringify(out));
  } catch { /* storage full / unavailable: the screen keeps working in memory */ }
}
