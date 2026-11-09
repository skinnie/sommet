import React, { createContext, useContext, useEffect, useState } from 'react';
import { useColorScheme } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// v2.5.0 — theme mode is now user-selectable (Settings, top section) instead
// of always following the OS. Persisted so the choice survives an app
// restart; defaults to 'system' so anyone who never opens the setting gets
// the previous (OS-driven) behavior unchanged.

export type ThemeMode = 'light' | 'dark' | 'system';
const STORAGE_KEY = 'sommet:themeMode';

interface ThemeModeContextValue {
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
  isDark: boolean;
}

const ThemeModeContext = createContext<ThemeModeContextValue>({
  mode: 'system',
  setMode: () => {},
  isDark: false,
});

export function ThemeModeProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>('system');

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY).then(v => {
      if (v === 'light' || v === 'dark' || v === 'system') setModeState(v);
    }).catch(() => {});
  }, []);

  function setMode(m: ThemeMode) {
    setModeState(m);
    AsyncStorage.setItem(STORAGE_KEY, m).catch(() => {});
  }

  const isDark = mode === 'system' ? systemScheme === 'dark' : mode === 'dark';

  return (
    <ThemeModeContext.Provider value={{ mode, setMode, isDark }}>
      {children}
    </ThemeModeContext.Provider>
  );
}

export function useThemeMode(): ThemeModeContextValue {
  return useContext(ThemeModeContext);
}
