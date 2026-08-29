import React, { useEffect } from 'react';
import { Alert, Linking, StatusBar } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';

// v3.0 UI port (2026-08-09, "go all the way to the new theming") - the app shell itself
// (status bar, every native-stack header's background/tint) is the last real holdout;
// nothing in the app imports theme/useTheme.ts anymore after this.
import { useV3Theme } from './src/theme/v3';
import { ThemeModeProvider, useThemeMode } from './src/theme/ThemeModeContext';
import { ExperimentalProvider } from './src/config/ExperimentalContext';
import { DemoProvider } from './src/config/DemoContext';
import HomeScreen from './src/screens/HomeScreen';
import LogListScreen from './src/screens/LogListScreen';
import MapScreen from './src/screens/MapScreen';
import SettingsScreen from './src/screens/SettingsScreen';
import PoiScreen from './src/screens/PoiScreen';
import RouteScreen from './src/screens/RouteScreen';
import GarminRouteScreen from './src/screens/GarminRouteScreen';
import GarminPoiScreen from './src/screens/GarminPoiScreen';
import BackupScreen from './src/screens/BackupScreen';
import SportModesScreen from './src/screens/SportModesScreen';
import FirmwareScreen from './src/screens/FirmwareScreen';
import TotalsScreen from './src/screens/TotalsScreen';
import CalendarScreen from './src/screens/CalendarScreen';
import SmartSensorScreen from './src/screens/SmartSensorScreen';
import AppZoneScreen from './src/screens/AppZoneScreen';
import AppsScreen from './src/screens/AppsScreen';
import WatchSettingsScreen from './src/screens/WatchSettingsScreen';
import IntervalsScreen from './src/screens/IntervalsScreen';
import WorkoutCalendarScreen from './src/screens/WorkoutCalendarScreen';
import GearScreen from './src/screens/GearScreen';
// Weight/Health - desktop parity (2026-08-26). Both read intervals.icu's wellness feed via
// src/services/WellnessService.ts; see that file for what Android can and cannot reach.
import WeightScreen from './src/screens/WeightScreen';
import HealthScreen from './src/screens/HealthScreen';
import CoachScreen from './src/screens/CoachScreen';
import EmberScreen from './src/screens/EmberScreen';
import RouteWeatherScreen from './src/screens/RouteWeatherScreen';
import type { GarminConnectResult } from './src/native/GarminModule';
import { ActivityRecord } from './src/database/db';
import { handleOAuthCallback as handleStravaCallback } from './src/services/ApiStrava';
import { t, dateLocale } from './src/i18n';

// ─── Types de navigation ──────────────────────────────────────────────────────

export type RootStackParamList = {
  Home: undefined;
  LogList: undefined;
  Map: { activity: ActivityRecord };
  Settings: undefined;
  Poi: undefined;
  Route: undefined;
  // v2.3.2 beta: HomeScreen connects to the Garmin device itself (see its
  // connecting-flow state machine) and hands the already-fetched info over
  // here — neither screen has its own Connect step. Activities sync runs
  // inline from Home (no screen — see GarminActivityService.ts), so there's
  // no "Garmin" route anymore, just these two, mirroring the Ambit Route/Poi
  // screens per André's feedback.
  GarminRoute: { info: GarminConnectResult };
  GarminPoi: { info: GarminConnectResult };
  Backup: { deviceModel?: string } | undefined;
  // Real, 2026-08-08 - Ambit3-only (Kailash's own memory map has no CustomModes region),
  // HomeScreen only routes here for that device type - see SportModesScreen.tsx.
  // overBle: the watch is connected over Bluetooth, so the sport-mode reads/writes must run on
  // the already-open BLE link instead of opening a USB connection (which pops the OTG prompt).
  SportModes: { overBle?: boolean; variant?: string } | undefined;
  Firmware: undefined;
  // Activity-analytics views (2026-08-13, port of desktop TotalsPage/CalendarPage). Both are
  // derived purely from the local activity DB, so they're reachable any time (no device
  // needed) - launched from the Activities screen header, not the device-gated Home shell.
  Totals: undefined;
  Calendar: undefined;
  // Weight/Health (2026-08-26, port of desktop WeightPage/HealthPage). Both read intervals.icu
  // wellness, so like Totals/Calendar they need no connected device and are reachable any time.
  Weight: undefined;
  Health: undefined;
  // Coach readiness (2026-08-26): intervals.icu training load, no device needed.
  Coach: undefined;
  // Ember recap - hidden until the Settings easter egg unlocks it (EmberUnlock.ts).
  Ember: undefined;
  // Experimental (2026-08-14) - gated behind the Experimental flag (see ExperimentalContext),
  // reached from the Settings > Experimental section. App-Zone + Intervals write flash and are
  // unproven on Android hardware; Smart Sensor is a separate BLE peripheral (the HR belt).
  SmartSensor: undefined;
  AppZone: undefined;
  Apps: undefined;
  WatchSettings: undefined;
  Intervals: undefined;
  // The Calendar feature (2026-08-21) - dated native guided workouts in the WORKOUT menu,
  // named "dd/mm_name". "WorkoutCalendar" (not "Calendar") deliberately - that route name is
  // already the activity-history month grid above; this is a different feature entirely.
  WorkoutCalendar: undefined;
  // Gear tracker (v3): bikes/shoes + components (parts) + service reminders, local-first and
  // mirrored two-way to intervals.icu. Derived from the local gear DB, no device needed.
  Gear: undefined;
  // Weather along a route (2026-08-29, port of the desktop Plan page's Weather panel —
  // weather_route.py + astro.py, both verified equal in TS). Sun/moon + Open-Meteo forecast at
  // each point's ETA, temp-coloured profile + verdict. `route` optional (a demo route is used
  // when none is passed); no watch needed.
  RouteWeather: { route?: Array<{ lat: number; lon: number; ele?: number | null }>; name?: string } | undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

// ─── Handler deep link OAuth2 ─────────────────────────────────────────────────

const STRAVA_OAUTH_PREFIX      = 'opensportsync://oauth/strava';

async function processOAuthUrl(url: string | null) {
  if (!url) return;
  try {
    const code = new URL(url).searchParams.get('code');
    if (!code) throw new Error(t.oauthMissingCode);

    if (url.startsWith(STRAVA_OAUTH_PREFIX)) {
      await handleStravaCallback(code);
      Alert.alert('Strava', t.stravaConnected);
    }
  } catch (e: any) {
    Alert.alert(t.error, e?.message);
  }
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <ThemeModeProvider>
      <ExperimentalProvider>
        <DemoProvider>
          <AppShell />
        </DemoProvider>
      </ExperimentalProvider>
    </ThemeModeProvider>
  );
}

function AppShell() {
  const { isDark } = useThemeMode();
  const theme = useV3Theme();

  useEffect(() => {
    // App ouverte depuis un deep link (app déjà lancée)
    const sub = Linking.addEventListener('url', ({ url }) => processOAuthUrl(url));
    // App lancée via le deep link (app froide)
    Linking.getInitialURL().then(processOAuthUrl);
    return () => sub.remove();
  }, []);

  return (
    <SafeAreaProvider>
      <StatusBar
        barStyle={isDark ? 'light-content' : 'dark-content'}
        backgroundColor={theme.background}
      />
      <NavigationContainer>
        <Stack.Navigator
          initialRouteName="Home"
          screenOptions={{
            headerStyle: { backgroundColor: theme.card },
            headerTintColor: theme.text,
            headerTitleStyle: { fontWeight: '700' },
            headerShadowVisible: false,
            contentStyle: { backgroundColor: theme.background },
          }}
        >
          <Stack.Screen
            name="Home"
            component={HomeScreen}
            options={{ headerShown: false }}
          />
          <Stack.Screen
            name="LogList"
            component={LogListScreen}
            options={{ title: t.logListTitle }}
          />
          <Stack.Screen
            name="Map"
            component={MapScreen}
            options={({ route }) => ({
              title: route.params.activity.date
                ? new Date(route.params.activity.date).toLocaleDateString(dateLocale)
                : t.mapFallback,
            })}
          />
          <Stack.Screen
            name="Settings"
            component={SettingsScreen}
            options={{ title: t.settingsTitle }}
          />
          <Stack.Screen
            name="Poi"
            component={PoiScreen}
            options={{ title: t.poiScreenTitle }}
          />
          <Stack.Screen
            name="Route"
            component={RouteScreen}
            options={{ title: t.routeScreenTitle }}
          />
          <Stack.Screen
            name="GarminRoute"
            component={GarminRouteScreen}
            options={{ title: t.garminRouteScreenTitle }}
          />
          <Stack.Screen
            name="GarminPoi"
            component={GarminPoiScreen}
            options={{ title: t.garminPoiScreenTitle }}
          />
          <Stack.Screen
            name="Backup"
            component={BackupScreen}
            options={{ title: t.backupScreenTitle }}
          />
          <Stack.Screen
            name="SportModes"
            component={SportModesScreen}
            options={{ title: t.sportModesScreenTitle }}
          />
          <Stack.Screen
            name="Firmware"
            component={FirmwareScreen}
            options={{ title: 'Firmware' }}
          />
          <Stack.Screen
            name="Totals"
            component={TotalsScreen}
            options={{ title: t.totalsScreenTitle }}
          />
          <Stack.Screen
            name="Weight"
            component={WeightScreen}
            options={{ title: 'Weight' }}
          />
          <Stack.Screen
            name="Health"
            component={HealthScreen}
            options={{ title: 'Health' }}
          />
          <Stack.Screen
            name="Coach"
            component={CoachScreen}
            options={{ title: 'Coach' }}
          />
          <Stack.Screen
            name="Ember"
            component={EmberScreen}
            options={{ title: 'Ember' }}
          />
          <Stack.Screen
            name="RouteWeather"
            component={RouteWeatherScreen}
            options={{ title: 'Weather along route' }}
          />
          <Stack.Screen
            name="Calendar"
            component={CalendarScreen}
            options={{ title: t.calendarScreenTitle }}
          />
          <Stack.Screen
            name="SmartSensor"
            component={SmartSensorScreen}
            options={{ title: t.smartSensorScreenTitle }}
          />
          <Stack.Screen
            name="AppZone"
            component={AppZoneScreen}
            options={{ title: t.appZoneScreenTitle }}
          />
          <Stack.Screen
            name="Apps"
            component={AppsScreen}
            options={{ title: 'Apps' }}
          />
          <Stack.Screen
            name="WatchSettings"
            component={WatchSettingsScreen}
            options={{ title: 'Watch settings' }}
          />
          <Stack.Screen
            name="Intervals"
            component={IntervalsScreen}
            options={{ title: t.intervalsScreenTitle }}
          />
          <Stack.Screen
            name="WorkoutCalendar"
            component={WorkoutCalendarScreen}
            options={{ title: t.experimentalWorkoutCalendar }}
          />
          <Stack.Screen
            name="Gear"
            component={GearScreen}
            options={{ title: t.gearScreenTitle }}
          />
        </Stack.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
