import AsyncStorage from '@react-native-async-storage/async-storage';

// The Ember easter egg (2026-08-26, desktop parity — André: "ember... only available via easter
// egg"). Ten taps on the version label in Settings → About flips this, exactly like the
// desktop's Theme.emberUnlocked, and the Ember tile then appears on Home.
//
// Persisted, so it stays unlocked across launches once found — an egg you have to re-find every
// time is just an obstacle. Plain AsyncStorage rather than the Keychain: this is a UI
// preference, not a credential.

const KEY = 'ember.unlocked';

export async function isEmberUnlocked(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(KEY)) === '1';
  } catch {
    return false;        // storage unavailable: stay locked rather than leaking the egg
  }
}

export async function setEmberUnlocked(on: boolean): Promise<void> {
  await AsyncStorage.setItem(KEY, on ? '1' : '0');
}
