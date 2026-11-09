// Up to 0.2.66 the app saved its settings under keys starting "ambitapp:" (its old name). They
// now start "sommet:". This copies each old key to its new name once (a new key that already
// exists wins), then removes the old keys. Runs before the first screen draws (App.tsx), so no
// setting is read under the new name before it has been moved. Never throws: if storage fails,
// the app starts with default settings, as it would on a fresh install.
import AsyncStorage from '@react-native-async-storage/async-storage';

const OLD = 'ambitapp:';
const NEW = 'sommet:';

export async function migrateOldStorageKeys(): Promise<void> {
  try {
    const keys = (await AsyncStorage.getAllKeys()).filter(k => k.startsWith(OLD));
    if (keys.length === 0) return;
    const pairs = await AsyncStorage.multiGet(keys);
    const target = pairs.map(([k, v]) => [NEW + k.slice(OLD.length), v] as const);
    const existing = new Set(
      (await AsyncStorage.multiGet(target.map(([k]) => k)))
        .filter(([, v]) => v !== null).map(([k]) => k));
    const toWrite = target.filter(([k, v]) => v !== null && !existing.has(k)) as [string, string][];
    if (toWrite.length) await AsyncStorage.multiSet(toWrite);
    await AsyncStorage.multiRemove(keys);
  } catch {
    // keep going with whatever is there
  }
}
