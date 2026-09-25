import AsyncStorage from '@react-native-async-storage/async-storage';

// The Magene C406 this phone was paired with. It's Bluetooth-only and asleep most of the time,
// so Home remembers it after the first "Find Magene" scan (the bond is kept by Android) and the
// Workout Calendar / Magene screen can reach it without scanning again.
const KEY = 'mageneDevice';
// Profile sync with intervals.icu, asked ONCE (desktop ProfileSyncPrompt): 'auto' = keep the
// device in sync on every connect, 'manual' = never ask again. Unset = not asked yet.
const SYNC_KEY = 'mageneProfileSync';

export interface KnownMagene { address: string; name: string }

export async function getKnownMagene(): Promise<KnownMagene | null> {
  try { const raw = await AsyncStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
export async function setKnownMagene(m: KnownMagene | null): Promise<void> {
  try { m ? await AsyncStorage.setItem(KEY, JSON.stringify(m)) : await AsyncStorage.removeItem(KEY); } catch { /* ignore */ }
}
// Per device kind (the desktop keeps one per kind too, Sommet.conf [bikeProfileSync]).
const syncKey = (kind: 'magene' | 'bryton') => (kind === 'magene' ? SYNC_KEY : 'brytonProfileSync');
export async function getProfileSyncMode(kind: 'magene' | 'bryton' = 'magene'): Promise<'auto' | 'manual' | ''> {
  try { return ((await AsyncStorage.getItem(syncKey(kind))) as any) || ''; } catch { return ''; }
}
export async function setProfileSyncMode(mode: 'auto' | 'manual', kind: 'magene' | 'bryton' = 'magene'): Promise<void> {
  try { await AsyncStorage.setItem(syncKey(kind), mode); } catch { /* ignore */ }
}
