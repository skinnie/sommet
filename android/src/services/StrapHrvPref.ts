// ─── StrapHrvPref ─────────────────────────────────────────────────────────────
// Opt-in toggle for the "morning HRV from a BLE heart-rate strap" feature (Polar Verity Sense,
// COOSPO HW9, or any strap that reports R-R over the standard Heart Rate service). Default OFF,
// so an intervals.icu-only user never sees the Measure card. This mirrors the desktop's
// health/coospoHrvEnabled QSettings toggle (SettingsPage.qml / HealthService), giving both
// platforms the same behaviour: the strap card on the Health screen appears only when the
// native strap module is present AND the user has enabled it here.
//
// Persisted as a single boolean flag, exactly like MarkSynced.ts.

import AsyncStorage from '@react-native-async-storage/async-storage';

export const STRAP_HRV_STORAGE_KEY = 'ambitapp:strapHrvEnabled';

/** Whether the user has enabled strap morning-HRV. Default false. */
export async function isStrapHrvEnabled(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(STRAP_HRV_STORAGE_KEY)) === '1';
  } catch {
    return false;
  }
}

export async function setStrapHrvEnabled(enabled: boolean): Promise<void> {
  try {
    await AsyncStorage.setItem(STRAP_HRV_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    // AsyncStorage failure just means the toggle won't persist; not fatal.
  }
}
