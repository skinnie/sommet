import React, { createContext, useContext, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { DemoDevice, demoDeviceFor } from '../services/DemoDevices';

// Testing mode state (André, 2026-08-16, ported from desktop): pretend a device is connected so
// the app can be explored without one. Persisted (enabled + which watch), same AsyncStorage +
// context pattern as ThemeModeContext / ExperimentalContext. Default OFF.

const ENABLED_KEY = 'sommet:demo:enabled';
const VARIANT_KEY = 'sommet:demo:variant';

interface DemoContextValue {
  enabled: boolean;
  variant: string;
  device: DemoDevice;
  setEnabled: (v: boolean) => void;
  setVariant: (v: string) => void;
}

const DemoContext = createContext<DemoContextValue>({
  enabled: false,
  variant: 'Emu',
  device: demoDeviceFor('Emu'),
  setEnabled: () => {},
  setVariant: () => {},
});

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabledState] = useState(false);
  const [variant, setVariantState] = useState('Emu');

  useEffect(() => {
    AsyncStorage.getItem(ENABLED_KEY).then(v => { if (v === '1') setEnabledState(true); }).catch(() => {});
    AsyncStorage.getItem(VARIANT_KEY).then(v => { if (v) setVariantState(v); }).catch(() => {});
  }, []);

  function setEnabled(v: boolean) {
    setEnabledState(v);
    AsyncStorage.setItem(ENABLED_KEY, v ? '1' : '0').catch(() => {});
  }
  function setVariant(v: string) {
    setVariantState(v);
    AsyncStorage.setItem(VARIANT_KEY, v).catch(() => {});
  }

  return (
    <DemoContext.Provider value={{ enabled, variant, device: demoDeviceFor(variant), setEnabled, setVariant }}>
      {children}
    </DemoContext.Provider>
  );
}

export function useDemo(): DemoContextValue {
  return useContext(DemoContext);
}
