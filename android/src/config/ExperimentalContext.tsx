import React, { createContext, useContext, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Experimental features (2026-08-14, André: "add app zone, intervals workout, smart sensor").
// Reworked 2026-08-17 (André: "make a toggle for each one of them, put experimental features on
// title") from ONE master switch to one persisted flag per feature, so each unproven feature can
// be enabled independently. All default OFF so nobody who never opens the section is exposed to
// an unproven flash write - these are cable-tested, community-feedback features. Same
// AsyncStorage/context pattern as ThemeModeContext. The three feature SCREENS are always
// registered in navigation; a flag only controls whether Settings offers its entry point.

// appZone + coach added 2026-10-22 (desktop parity: the desktop has a sidebar switch for each,
// both on by default, so turning them into switches here changes nothing until one is turned off).
// magene added 2026-10-22: Settings > Show in the sidebar > Devices (desktop parity, BikeDevices.
// mageneShown). Off: no Magene card on Home, not in the pairing list, not offered by Routes or the
// Workout Calendar - still remembered.
export type ExpFeature = 'intervals' | 'appZone' | 'smartSensor' | 'coach' | 'workoutCalendar' | 'magene';
export const EXP_FEATURES: ExpFeature[] = ['intervals', 'appZone', 'smartSensor', 'coach', 'workoutCalendar', 'magene'];
const storageKey = (f: ExpFeature) => `sommet:experimental:${f}`;

type FeatureFlags = Record<ExpFeature, boolean>;
const ALL_OFF: FeatureFlags = { intervals: false, appZone: false, smartSensor: false, coach: false, workoutCalendar: false, magene: false };
// UX fix #1 (André, 2026-09-02): default these ON so owners find the features they bought the
// watch for (structured workouts / App Zone via `intervals`, the HR belt via `smartSensor`, the
// workout calendar). Desktop parity. A user who explicitly turned one OFF (stored '0') is still
// respected below. (No T6/GPS-Pod flags here: T6/X6 is desktop-only, GPS Track Pod isn't on Android.)
const DEFAULTS: FeatureFlags = { intervals: true, appZone: true, smartSensor: true, coach: true, workoutCalendar: true, magene: false };

interface ExperimentalContextValue {
  features: FeatureFlags;
  setFeature: (f: ExpFeature, v: boolean) => void;
  anyEnabled: boolean;
}

const ExperimentalContext = createContext<ExperimentalContextValue>({
  features: ALL_OFF,
  setFeature: () => {},
  anyEnabled: false,
});

export function ExperimentalProvider({ children }: { children: React.ReactNode }) {
  const [features, setFeatures] = useState<FeatureFlags>(DEFAULTS);

  useEffect(() => {
    (async () => {
      const loaded: FeatureFlags = { ...DEFAULTS };
      for (const f of EXP_FEATURES) {
        try {
          // Explicit stored value wins ('1' on, '0' off); absence keeps the DEFAULTS value.
          const v = await AsyncStorage.getItem(storageKey(f));
          if (v === '1') loaded[f] = true;
          else if (v === '0') loaded[f] = false;
          // Never set: the Magene switch starts on if one is already paired (MageneStore's key).
          else if (f === 'magene') loaded[f] = !!(await AsyncStorage.getItem('mageneDevice'));
        } catch { /* ignore */ }
      }
      // One-time migration from the old single master toggle: if it was on, enable all three.
      try {
        if ((await AsyncStorage.getItem('sommet:experimental')) === '1') {
          for (const f of EXP_FEATURES) loaded[f] = true;
          await AsyncStorage.removeItem('sommet:experimental');
          for (const f of EXP_FEATURES) await AsyncStorage.setItem(storageKey(f), '1');
        }
      } catch { /* ignore */ }
      setFeatures(loaded);
    })();
  }, []);

  function setFeature(f: ExpFeature, v: boolean) {
    setFeatures(prev => ({ ...prev, [f]: v }));
    AsyncStorage.setItem(storageKey(f), v ? '1' : '0').catch(() => {});
  }

  const anyEnabled = EXP_FEATURES.some(f => features[f]);

  return (
    <ExperimentalContext.Provider value={{ features, setFeature, anyEnabled }}>
      {children}
    </ExperimentalContext.Provider>
  );
}

export function useExperimental(): ExperimentalContextValue {
  return useContext(ExperimentalContext);
}
