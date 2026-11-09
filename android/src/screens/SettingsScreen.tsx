import AsyncStorage from '@react-native-async-storage/async-storage';
import { isRacePlanEnabled, setRacePlanEnabled } from '../services/race/raceApi';
import { ADVANCED_POWER_KEY } from '../components/activity/ActivityPanel';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, Alert, ScrollView, ActivityIndicator, Linking, Modal, Platform, LayoutChangeEvent,
} from 'react-native';
import { RouteProp, useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import { useThemeMode, ThemeMode } from '../theme/ThemeModeContext';
import { MenuSettings } from '../navigation/MenuSettings';
import { useExperimental } from '../config/ExperimentalContext';
// The experimental features, one toggle each (André, 2026-08-17). Screen names must match the
// RootStackParamList routes.
// 2026-10-22 (desktop parity): same names, descriptions and order as the desktop's "Show in the
// sidebar" card - Intervals, App Zone, Smart Sensor, Coach. Training Program is Android-only here
// (the desktop shows it without a switch). GPS Track Pod isn't on Android; T6/X6 is desktop-only.
const EXP_FEATURE_ROWS = [
  { flag: 'intervals' as const,   label: 'Intervals',    desc: 'Build interval workouts.',              screen: 'Intervals' as const },
  { flag: 'appZone' as const,     label: 'App Zone',     desc: 'Build & install Suunto Apps.',          screen: 'AppZone' as const },
  { flag: 'smartSensor' as const, label: 'Smart Sensor', desc: 'Suunto HR belt over Bluetooth.',        screen: 'SmartSensor' as const },
  { flag: 'coach' as const,       label: 'Coach',        desc: 'Readiness beacon + chat, v2 concept.',  screen: 'Coach' as const },
  { flag: 'workoutCalendar' as const, label: t.experimentalWorkoutCalendar, desc: t.experimentalWorkoutCalendarDesc, screen: 'WorkoutCalendar' as const },
];
import { isMarkSyncedEnabled, setMarkSyncedEnabled as persistMarkSynced } from '../services/MarkSynced';
import { isStrapHrvEnabled, setStrapHrvEnabled as persistStrapHrv } from '../services/StrapHrvPref';
import { useDemo } from '../config/DemoContext';
import { DemoDevicePicker } from '../components/ui/DemoDevicePicker';
import Icon, { IconName } from '../components/ui/Icon';
import { CREDITS_URL } from '../legal/credits';
import {
  getRunalyzeApiKey, saveRunalyzeApiKey, removeRunalyzeApiKey,
} from '../services/ApiRunalyze';
import {
  getIntervalsIcuCredentials, saveIntervalsIcuCredentials, removeIntervalsIcuCredentials,
} from '../services/ApiIntervalsIcu';
import {
  setAnthropicKey, clearAnthropicKey, hasAnthropicKey, getChatBackend, setChatBackend, ChatBackend,
} from '../services/CoachChat';
import {
  isCatalogAvailable, hasCatalog, importCatalog, getEntries, invalidateEntries,
} from '../services/CatalogService';
import { useWeatherLocation, locateByIp, setManualLocation } from '../services/WeatherService';
import { isMageneAvailable } from '../services/MageneBle';
import { isEmberUnlocked, setEmberUnlocked } from '../services/EmberUnlock';
import { getEmberSyncCfg, setEmberSyncCfg } from '../services/EmberSync';
import { getUsdaApiKey, setUsdaApiKey } from '../services/EmberFood';
import { getSommetSyncCfg, setSommetSyncCfg, testSommetSync, runSommetSync } from '../services/SommetSync';
// Gear <-> intervals.icu import/sync lives here (in the intervals.icu connection), not on the
// Gear screen (André, 2026-08-18: "that options regarding intervals.icu should be on settings,
// when you connect to intervals.icu"). The Gear screen just shows the gear now.
import { importFromIntervals, runGearMirror, resolveConflict } from '../services/GearMirrorService';
import { GearConflict } from '../services/GearDiff';
import { importActivitiesFromIntervals } from '../services/IntervalsImport';
import { getSleepScoreSource, setSleepScoreSource } from '../services/WellnessService';
import {
  isAuthenticated as stravaIsAuth, getAuthorizationUrl as stravaAuthUrl, logout as stravaLogout,
} from '../services/ApiStrava';
import { getScaleAddress, scaleStatus } from '../services/ScaleService';
import { getSendWeighIns, setSendWeighIns, getSendBodyFat, setSendBodyFat } from '../services/IntervalsWeighIn';
import { ScaleSetupModal } from '../components/ScaleSetupModal';
import {
  getMapProvider, setMapProvider, MapProvider, MAP_PROVIDER_LABELS,
} from '../services/MapProviderService';
import { detectAttachedDeviceType } from '../native/AmbitUsbModule';
import { getTileCacheSizeBytes, clearTileCache } from '../services/TileCache';
import { t } from '../i18n';
import { APP_VERSION } from '../config/version';
import { useV3Theme } from '../theme/v3';
import { Button, Dropdown, FieldRow, IconBadge, Toggle, StatusChip, StatusNote, SegmentedControl } from '../components/ui/primitives';
import { SettingsSection } from '../components/ui/SettingsSection';
import UpdateNotice from '../components/UpdateNotice';
import { checkNow, initAppUpdate, setAutoCheck, updateAvailable, updatesSupported, useAppUpdate } from '../services/AppUpdate';

// Real, 2026-08-09 ("no button to change provider, nor in the settings like the desktop
// version") - same 3 real choices MapScreen.tsx/TrackMapScreen.tsx's own in-map layer
// control offers, same MAP_PROVIDER_LABELS names, with this screen's own localized
// descriptive suffix (matching desktop SettingsPage.qml's "(standard)"/"(cycling-focused)"
// RadioButton text).
const MAP_PROVIDER_OPTIONS: { provider: MapProvider; label: () => string }[] = [
  { provider: 'ign',     label: () => t.mapProviderIgnLabel },
  { provider: 'osm',     label: () => t.mapProviderOsmLabel },
  { provider: 'cyclosm', label: () => t.mapProviderCyclosmLabel },
];

function formatCacheSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const THEME_OPTIONS: { mode: ThemeMode; icon: IconName; label: () => string }[] = [
  { mode: 'light',  icon: 'sun',  label: () => t.themeLight },
  { mode: 'dark',   icon: 'moon', label: () => t.themeDark },
  { mode: 'system', icon: 'auto', label: () => t.themeSystem },
];


// ── The Settings split (André, 2026-10-21, "progressive disclosure"; desktop SettingsPage.qml is
// the baseline). Connections, Appearance, Show in the sidebar and Backup & restore stay open at the
// top; everything else is one closed row each (SettingsSection) showing its current value. Which
// rows are open is remembered between visits.
const OPEN_SECTIONS_KEY = 'sommet:settings:openSections';
const CLOSED_SECTIONS = ['activities', 'health', 'maps', 'weather', 'ember', 'coach', 'database',
  'catalog', 'experimental', 'testing', 'firmware', 'devices', 'about'];
// How many apps the Coach's offline workout catalogue holds (data/systm-sample.json).
const COACH_SAMPLE_SESSIONS = 55;

// One account in the Connections card: name, status, and a real button (desktop parity, and the
// a11y audit of 2026-10-21: the old "— Log in" lines read as plain text, not as something to tap).
// The state is a status tag, as on desktop (André, 2026-11-02, desktop picks #6): green when
// connected, grey otherwise, with the detail (the athlete id) beside it in grey.
function ConnectionRow({ name, status, connected, detail, action, onPress }: {
  name: string; status: string; connected: boolean; detail?: string; action: string; onPress: () => void;
}) {
  const t = useV3Theme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6 }}>
      <Text style={{ fontSize: 14, color: t.text, flexShrink: 1 }} numberOfLines={1}>{name}</Text>
      <StatusChip label={status} tone={connected ? 'success' : 'neutral'} />
      <Text style={{ flex: 1, fontSize: 13, color: t.mutedText }} numberOfLines={1}>{detail ?? ''}</Text>
      <Button label={action} variant="outline" grow={false} onPress={onPress} />
    </View>
  );
}

// A switch with a label to its right - the desktop's RoundedSwitch rows (switch first, then the
// words), used inside the rows below. Tapping the words flips it too.
function SwitchLine({ value, onValueChange, label, a11yLabel, accent = false }: {
  value: boolean; onValueChange: (v: boolean) => void; label: string;
  // What a screen reader calls the switch, when `label` is only its state ("On", "Off").
  a11yLabel?: string;
  // Desktop colours a state label primary while on (Theme.primary vs Theme.mutedText).
  accent?: boolean;
}) {
  const t = useV3Theme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8 }}>
      <Toggle value={value} onValueChange={onValueChange} accessibilityLabel={a11yLabel ?? label} />
      <TouchableOpacity style={{ flex: 1 }} activeOpacity={0.7} onPress={() => onValueChange(!value)}
        accessible={false}>
        <Text style={{ fontSize: 13, lineHeight: 19, color: accent ? (value ? t.primary : t.mutedText) : t.text }}>{label}</Text>
      </TouchableOpacity>
    </View>
  );
}

// One switch in the "Show in the sidebar" card: the switch, then the page's name, the experimental
// tag and what it is for. Tapping the words flips the switch - or, for a feature with its own
// screen that is already on, opens that screen (the old Experimental card's behaviour).
function SidebarSwitch({ title, description, value, onValueChange, onOpen }: {
  title: string; description: string; value: boolean;
  onValueChange: (v: boolean) => void; onOpen?: () => void;
}) {
  const t = useV3Theme();
  const opens = !!onOpen && value;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingVertical: 8 }}>
      <View style={{ paddingTop: 1 }}>
        <Toggle value={value} onValueChange={onValueChange} accessibilityLabel={title} />
      </View>
      <TouchableOpacity
        style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 }}
        activeOpacity={0.7}
        onPress={() => (opens ? onOpen!() : onValueChange(!value))}
      >
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ fontSize: 14, fontWeight: '700', color: t.text }}>{title}</Text>
            <StatusChip label="Experimental" tone="warning" />
          </View>
          <Text style={{ fontSize: 13, color: t.mutedText, lineHeight: 19, marginTop: 2 }}>{description}</Text>
        </View>
        {opens && <Icon name="chevronRight" size={18} color={t.mutedText} />}
      </TouchableOpacity>
    </View>
  );
}

// "03/11/2026 04:10", the date style used across the app.
function fmtChecked(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function SettingsScreen() {
  const theme = useV3Theme();
  const update = useAppUpdate();
  useEffect(() => { initAppUpdate().catch(() => {}); }, []);
  const styles = createStyles(theme);
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { features, setFeature, anyEnabled } = useExperimental();
  const demo = useDemo();
  const [demoPickerOpen, setDemoPickerOpen] = useState(false);
  const { mode, setMode } = useThemeMode();

  // ── Settings split: which closed rows are open (persisted), and links from other screens. ──
  const [openSections, setOpenSections] = useState<string[]>([]);
  useEffect(() => {
    AsyncStorage.getItem(OPEN_SECTIONS_KEY)
      .then(v => {
        const saved: string[] = v ? JSON.parse(v) : [];
        // Merge, so a row a link already opened isn't closed by the late load.
        setOpenSections(prev => Array.from(new Set([...saved, ...prev])));
      })
      .catch(() => undefined);
  }, []);
  const isOpen = (id: string) => openSections.includes(id);
  const setSectionOpen = useCallback((id: string, open: boolean) => {
    setOpenSections(prev => {
      const next = prev.filter(s => s !== id).concat(open ? [id] : []);
      AsyncStorage.setItem(OPEN_SECTIONS_KEY, JSON.stringify(next)).catch(() => undefined);
      return next;
    });
  }, []);
  const toggleSection = (id: string) => () => setSectionOpen(id, !isOpen(id));

  // Links land on the right section: Health/Weight/Coach's "Open Settings → Connections" pass
  // { section: 'connections' }. A closed row is opened; then the screen scrolls to it.
  const route = useRoute<RouteProp<RootStackParamList, 'Settings'>>();
  const scrollRef = useRef<ScrollView>(null);
  const cardY = useRef<Record<string, number>>({});
  const rowY = useRef<Record<string, number>>({});
  // Some rows appear a moment after the screen opens (Ember, Coach, the catalog: their on/off
  // state is read from storage, which can take a few seconds right after the app starts), which
  // moves the rows under them. So after a link the scroll follows the target as the layout
  // settles - for up to 4 s, and only until the user touches the page.
  const pendingScroll = useRef<{ id: string; until: number } | null>(null);
  const scrollToPending = useCallback(() => {
    const p = pendingScroll.current;
    if (!p) return;
    if (Date.now() > p.until) { pendingScroll.current = null; return; }
    const y = cardY.current[p.id] ?? (cardY.current.rows ?? 0) + (rowY.current[p.id] ?? 0);
    // The screen's own top padding (20) stays above the section, as when it first opens.
    scrollRef.current?.scrollTo({ y: Math.max(0, y - 20), animated: true });
  }, []);
  const recordCard = (id: string) => (e: LayoutChangeEvent) => {
    cardY.current[id] = e.nativeEvent.layout.y;
    scrollToPending();
  };
  const recordRow = (id: string) => (e: LayoutChangeEvent) => {
    rowY.current[id] = e.nativeEvent.layout.y;
    scrollToPending();
  };
  const requestedSection = route.params?.section;
  useEffect(() => {
    if (!requestedSection) return;
    if (CLOSED_SECTIONS.includes(requestedSection)) setSectionOpen(requestedSection, true);
    pendingScroll.current = { id: requestedSection, until: Date.now() + 4000 };
    // Short delay so an opened row is laid out before measuring where it is.
    setTimeout(scrollToPending, 150);
    navigation.setParams({ section: undefined } as never);
  }, [requestedSection, setSectionOpen, navigation, scrollToPending]);

  const [runalyzeKey, setRunalyzeKey]     = useState('');
  const [savedKey, setSavedKey]           = useState<string | null>(null);
  const [saving, setSaving]               = useState(false);
  const [stravaAuth, setStravaAuth]       = useState(false);
  const [scaleAddr, setScaleAddr]         = useState<string | null>(null);
  const [scaleOpen, setScaleOpen]         = useState(false);
  const [sendWeighIns, setSendWeighInsState] = useState(true);
  const [sendBodyFat, setSendBodyFatState]   = useState(false);

  const [intervalsAthleteId, setIntervalsAthleteId] = useState('');
  const [intervalsApiKey, setIntervalsApiKey]       = useState('');
  const [intervalsSaved, setIntervalsSaved]         = useState(false);
  // Coach chat key (2026-08-26, desktop parity). The user's OWN Anthropic key - never bundled,
  // and NOT a claude.ai subscription, which is the thing people try first and that cannot work.
  const [anthropicKey, setAnthropicKeyInput]        = useState('');
  const [anthropicSaved, setAnthropicSaved]         = useState(false);
  const [savingAnthropic, setSavingAnthropic]       = useState(false);
  // Settings -> Coach -> "Chat with a real AI coach" (desktop CoachService.chatBackend).
  const [chatBackend, setChatBackendState]          = useState<ChatBackend>('canned');
  function handleChatBackend(on: boolean) {
    const v: ChatBackend = on ? 'claude' : 'canned';
    setChatBackendState(v);
    setChatBackend(v).catch(() => undefined);
  }
  // Suunto Apps Catalog (desktop parity): imported from the user's own SuuntoLink index.json.
  // null = still checking. The count needs the catalog read once (a few MB), so it is read after
  // the screen has drawn.
  const [catalogEntries, setCatalogEntries]         = useState<number | null>(null);
  const [catalogImported, setCatalogImported]       = useState(false);
  const [catalogImporting, setCatalogImporting]     = useState(false);
  const [catalogError, setCatalogError]             = useState('');
  useEffect(() => {
    if (!isCatalogAvailable()) return;
    const timer = setTimeout(async () => {
      try {
        const has = await hasCatalog();
        setCatalogImported(has);
        setCatalogEntries(has ? (await getEntries()).length : 0);
      } catch { setCatalogEntries(0); }
    }, 300);
    return () => clearTimeout(timer);
  }, []);
  async function handleImportCatalog() {
    setCatalogImporting(true);
    setCatalogError('');
    try {
      const r = await importCatalog();
      invalidateEntries();
      setCatalogImported(true);
      setCatalogEntries(r.count);
    } catch (e: any) {
      // The picker being closed without a file is not an error worth showing.
      const msg = e?.message ?? String(e);
      if (!/cancel/i.test(msg)) setCatalogError(msg);
    } finally {
      setCatalogImporting(false);
    }
  }
  // Weather (desktop parity): where the forecast on Home is for.
  const weatherLoc = useWeatherLocation();
  const [weatherManual, setWeatherManual]           = useState(false);
  const [latText, setLatText]                       = useState('');
  const [lonText, setLonText]                       = useState('');
  useEffect(() => { setWeatherManual(weatherLoc.manual); }, [weatherLoc.manual]);
  useEffect(() => {
    setLatText(String(weatherLoc.latitude));
    setLonText(String(weatherLoc.longitude));
  }, [weatherLoc.latitude, weatherLoc.longitude]);
  function handleApplyWeather() {
    const lat = parseFloat(latText.replace(',', '.'));
    const lon = parseFloat(lonText.replace(',', '.'));
    if (isNaN(lat) || isNaN(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      Alert.alert('Weather', 'Enter a latitude (-90 to 90) and a longitude (-180 to 180).');
      return;
    }
    setWeatherManual(true);
    setManualLocation(lat, lon);
  }
  // Ember visibility (2026-08-29): an open opt-in toggle now (the 10-tap easter egg was retired).
  // `emberOn` mirrors the persisted flag via isEmberUnlocked/setEmberUnlocked; the storage key is
  // unchanged so an already-unlocked install keeps Ember showing.
  const [emberOn, setEmberOn]                       = useState(false);
  const [racePlanOn, setRacePlanOn] = useState(false);
  useEffect(() => { isRacePlanEnabled().then(setRacePlanOn); }, []);
  // Ember NAS sync config (2026-08-27): the shared store URL + token the iPhone PWA and desktop
  // use (desktop keeps it in ember/sync.json). Lets Android's interactive Ember merge across
  // devices instead of logging local-only.
  const [emberSyncUrl, setEmberSyncUrl]             = useState('');
  const [emberSyncToken, setEmberSyncToken]         = useState('');
  const [savingEmberSync, setSavingEmberSync]       = useState(false);
  // Personal USDA FoodData Central key for Ember's food search (2026-09-28); empty = DEMO_KEY.
  const [usdaKey, setUsdaKey]                       = useState('');
  // Database (#SYNC): the user's own server/NAS is the only cross-device sync on the phone.
  const [sommetSyncUrl, setSommetSyncUrl]           = useState('');
  const [sommetSyncToken, setSommetSyncToken]       = useState('');
  const [savingSommet, setSavingSommet]             = useState(false);
  const [syncingSommet, setSyncingSommet]           = useState(false);
  // Whether a server is saved (for the Database row's summary; the fields can hold unsaved edits).
  const [sommetSaved, setSommetSaved]               = useState(false);
  const [sommetStatus, setSommetStatus]             = useState('');
  const [sommetTone, setSommetTone] = useState<'success' | 'error' | 'working'>('success');
  // Which coordinate row (if any) is currently being picked on a map - desktop parity with
  // WatchSettingsPage's "Pick on a map". null = picker closed.
  const [savingIntervals, setSavingIntervals]       = useState(false);
  const [gearImporting, setGearImporting]           = useState(false);
  const [gearSyncing, setGearSyncing]               = useState(false);
  const [actsImporting, setActsImporting]           = useState(false);

  // v3.0 UI port (2026-08-09, "settings was completely reworked in our desktop app...
  // proceed") - desktop's real Connections card (SettingsPage.qml) is one compact card with
  // a status-dot row per service, tap-to-open a Dialog with that service's own form - not
  // four separate always-expanded full-height sections like this screen had. Same real
  // handlers/state above, this only changes which one is visible at a time.
  const [openConnection, setOpenConnection] = useState<
    'strava' | 'runalyze' | 'intervals' | null
  >(null);

  const [mapProvider, setMapProviderState] = useState<MapProvider>('ign');

  // Experimental "mark synced workouts as synced" toggle - own persisted flag (MarkSynced.ts),
  // independent of the master experimental switch, default OFF.
  const [markSyncedEnabled, setMarkSyncedEnabledState] = useState(false);
  // Morning HRV from a BLE heart-rate strap - own persisted flag (StrapHrvPref.ts), default OFF.
  // Mirrors the desktop health/coospoHrvEnabled toggle so both platforms gate the strap card.
  const [strapHrvEnabled, setStrapHrvEnabledState] = useState(false);
  const [icuSleepScore, setIcuSleepScore] = useState(false);
  useEffect(() => { getSleepScoreSource().then(v => setIcuSleepScore(v === 'intervals')).catch(() => {}); }, []);
  // Activity screen: normalized power, TSS, IF and work on rides (desktop parity: Settings ->
  // Activities -> Advanced power numbers). Off by default.
  const [advancedPower, setAdvancedPower] = useState(false);
  useEffect(() => { AsyncStorage.getItem(ADVANCED_POWER_KEY).then(v => setAdvancedPower(v === '1')).catch(() => undefined); }, []);
  function handleToggleAdvancedPower(v: boolean) {
    setAdvancedPower(v);
    AsyncStorage.setItem(ADVANCED_POWER_KEY, v ? '1' : '0').catch(() => undefined);
  }

  const [tileCacheBytes, setTileCacheBytes] = useState<number | null>(null);
  const [clearingCache, setClearingCache] = useState(false);

  useFocusEffect(useCallback(() => {
    getTileCacheSizeBytes().then(setTileCacheBytes).catch(() => setTileCacheBytes(0));
  }, []));

  async function handleClearTileCache() {
    setClearingCache(true);
    try {
      await clearTileCache();
      setTileCacheBytes(0);
    } finally {
      setClearingCache(false);
    }
  }

  // Real, 2026-08-08 ("Settings on ambit 3 - if they are already cracked to be changed by
  // cable, we will need to build a UI for it"). Not auto-loaded on focus like the API
  // credentials above - reading needs the watch actually connected via USB, which isn't
  // guaranteed just because this screen is open, so it's behind an explicit "Read
  // Settings" tap instead (same "explicit action, no surprise USB traffic" spirit as the
  // rest of this app's own on-demand connect/read/disconnect flows - see PoiService.ts).
  // Real, 2026-08-10 ("watch settings, hide it when garmin is plugged") - the AmbitSettings
  // cable protocol below is Suunto-only; a real Garmin connected live surfaced that this
  // card was showing (and its "Read Settings" tap would just fail) with a Garmin plugged
  // in. Re-checked on every focus, same on-demand pattern as the rest of this app (no
  // context/prop-drilling from Home - this screen queries what it needs itself).
  const [isGarminAttached, setIsGarminAttached] = useState(false);
  // Only detects whether a Garmin is attached, to hide the Firmware card below. Watch settings
  // moved to their own screen (WatchSettingsScreen, 2026-08-29), so this no longer reads the watch.
  useFocusEffect(useCallback(() => {
    detectAttachedDeviceType().then(t => setIsGarminAttached(t === 'garmin')).catch(() => {});
  }, []));



  useFocusEffect(useCallback(() => {
    getRunalyzeApiKey().then(k => {
      setSavedKey(k);
      setRunalyzeKey(k ?? '');
    });
    stravaIsAuth().then(setStravaAuth);
    getScaleAddress().then(setScaleAddr);
    getSendWeighIns().then(setSendWeighInsState);
    getSendBodyFat().then(setSendBodyFatState);
    getIntervalsIcuCredentials().then(creds => {
      setIntervalsSaved(!!creds);
      setIntervalsAthleteId(creds?.athleteId ?? '');
      setIntervalsApiKey(creds?.apiKey ?? '');
    });
    // Reflect whether a Coach key is already stored (the key itself is never read back into
    // the field - only whether one exists).
    hasAnthropicKey().then(setAnthropicSaved);
    getChatBackend().then(setChatBackendState);
    isEmberUnlocked().then(setEmberOn);
    getEmberSyncCfg().then(c => { if (c) { setEmberSyncUrl(c.url); setEmberSyncToken(c.token); } });
    getUsdaApiKey().then(setUsdaKey);
    getSommetSyncCfg().then(c => {
      if (c) { setSommetSyncUrl(c.url); setSommetSyncToken(c.token); }
      setSommetSaved(!!c?.url);
    });
    getMapProvider().then(setMapProviderState);
    isMarkSyncedEnabled().then(setMarkSyncedEnabledState);
    isStrapHrvEnabled().then(setStrapHrvEnabledState);
  }, []));

  function handleToggleMarkSynced(v: boolean) {
    setMarkSyncedEnabledState(v);
    persistMarkSynced(v);
  }

  function handleToggleStrapHrv(v: boolean) {
    setStrapHrvEnabledState(v);
    persistStrapHrv(v);
  }

  function handleSetMapProvider(p: MapProvider) {
    setMapProviderState(p);
    setMapProvider(p);
  }

  async function handleSaveRunalyze() {
    if (!runalyzeKey.trim()) {
      Alert.alert(t.emptyKey, t.emptyKeyMsg);
      return;
    }
    setSaving(true);
    try {
      await saveRunalyzeApiKey(runalyzeKey.trim());
      setSavedKey(runalyzeKey.trim());
      Alert.alert(t.savedOk, t.keySaved);
    } finally {
      setSaving(false);
    }
  }

  async function handleRemoveRunalyze() {
    await removeRunalyzeApiKey();
    setSavedKey(null);
    setRunalyzeKey('');
    Alert.alert(t.deleted, t.keyDeleted);
  }

  async function handleSaveEmberSync() {
    setSavingEmberSync(true);
    try {
      const url = emberSyncUrl.trim();
      await setEmberSyncCfg(url ? { url, token: emberSyncToken.trim() } : null);
      Alert.alert('Ember', url ? 'Ember sync saved.' : 'Ember sync cleared (local only).');
    } catch (e: any) {
      Alert.alert('Error', e?.message ?? 'Failed to save');
    } finally {
      setSavingEmberSync(false);
    }
  }

  async function handleSaveSommet() {
    setSavingSommet(true);
    try {
      const url = sommetSyncUrl.trim();
      await setSommetSyncCfg(url ? { url, token: sommetSyncToken.trim() } : null);
      setSommetStatus(url ? 'Saved.' : 'Sync turned off.'); setSommetTone('success');
      setSommetSaved(!!url);
    } catch (e: any) {
      setSommetStatus(e?.message ?? 'Failed to save'); setSommetTone('error');
    } finally {
      setSavingSommet(false);
    }
  }

  // Desktop parity: "Stop syncing" forgets the server; the copy on this phone stays.
  async function handleStopSommet() {
    await setSommetSyncCfg(null);
    setSommetSyncUrl('');
    setSommetSyncToken('');
    setSommetSaved(false);
    setSommetStatus('Stopped syncing.'); setSommetTone('success');
  }

  async function handleTestSommet() {
    setSommetStatus('Testing…'); setSommetTone('working');
    const r = await testSommetSync(sommetSyncUrl, sommetSyncToken);
    setSommetStatus(r.message); setSommetTone(r.ok ? 'success' : 'error');
  }

  async function handleSyncNow() {
    // Save first so the sync uses the fields as shown, then run and report the counts.
    setSyncingSommet(true);
    setSommetStatus('Syncing…'); setSommetTone('working');
    try {
      const url = sommetSyncUrl.trim();
      await setSommetSyncCfg(url ? { url, token: sommetSyncToken.trim() } : null);
      setSommetSaved(!!url);
      const r = await runSommetSync();
      setSommetStatus(r.ok
        ? `Synced — ${r.pulled} received, ${r.pushed} sent.`
        : 'Couldn’t reach the server — will retry later.');
      setSommetTone(r.ok ? 'success' : 'error');
    } catch (e: any) {
      setSommetStatus(e?.message ?? 'Sync failed'); setSommetTone('error');
    } finally {
      setSyncingSommet(false);
    }
  }

  async function handleSaveIntervals() {
    if (!intervalsAthleteId.trim() || !intervalsApiKey.trim()) {
      Alert.alert(t.emptyCreds, t.emptyCredsMsg);
      return;
    }
    setSavingIntervals(true);
    try {
      await saveIntervalsIcuCredentials(intervalsAthleteId.trim(), intervalsApiKey.trim());
      setIntervalsSaved(true);
      Alert.alert(t.savedOk, t.credsSaved);
    } finally {
      setSavingIntervals(false);
    }
  }

  async function handleSaveAnthropic() {
    setSavingAnthropic(true);
    try {
      await setAnthropicKey(anthropicKey);
      setAnthropicSaved(true);
      setAnthropicKeyInput('');       // don't keep the secret in component state after saving
    } finally {
      setSavingAnthropic(false);
    }
  }

  async function handleRemoveAnthropic() {
    await clearAnthropicKey();
    setAnthropicSaved(false);
    setAnthropicKeyInput('');
  }

  async function handleRemoveIntervals() {
    await removeIntervalsIcuCredentials();
    setIntervalsSaved(false);
    setIntervalsAthleteId('');
    setIntervalsApiKey('');
    Alert.alert(t.deleted, t.credsDeleted);
  }

  // ── Gear <-> intervals.icu (moved here from the Gear screen). Import (pull-only) is the
  // primary path; two-way Sync is secondary and stops on a real two-sided edit to ask. ──
  async function handleGearImport() {
    setGearImporting(true);
    try {
      const n = await importFromIntervals();
      Alert.alert(t.gearScreenTitle, t.gearImportDone(n));
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setGearImporting(false);
    }
  }

  async function handleGearSync() {
    setGearSyncing(true);
    try {
      const res = await runGearMirror();
      if (res.conflicts.length > 0) await resolveGearConflictsSequentially(res.conflicts);
      else Alert.alert(t.gearScreenTitle, t.gearSyncDone(res.pulled, res.pushed));
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setGearSyncing(false);
    }
  }

  function resolveGearConflictsSequentially(conflicts: GearConflict[]): Promise<void> {
    return new Promise(resolve => {
      let i = 0;
      const next = () => {
        if (i >= conflicts.length) { resolve(); return; }
        const c = conflicts[i++];
        Alert.alert(
          t.gearConflictTitle,
          t.gearConflictBody(c.local.name || c.remote?.name || ''),
          [
            { text: t.gearConflictKeepLocal, onPress: async () => { await resolveConflict(c, 'local'); next(); } },
            { text: t.gearConflictKeepRemote, onPress: async () => { await resolveConflict(c, 'remote'); next(); } },
          ],
          { cancelable: false },
        );
      };
      next();
    });
  }

  // Import ALL activities from intervals.icu into the local DB (André, 2026-08-18). Pull-only;
  // idempotent (dedups against what's already here + watch moves). Lives in the intervals.icu
  // connection, like the gear import.
  async function handleImportActivities() {
    setActsImporting(true);
    try {
      const r = await importActivitiesFromIntervals();
      Alert.alert(t.intervalsSection,
        `Imported ${r.imported} activit${r.imported === 1 ? 'y' : 'ies'} from intervals.icu.`);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setActsImporting(false);
    }
  }

  async function handleStravaConnect() {
    try {
      const url = stravaAuthUrl();
      await Linking.openURL(url);
    } catch (e: any) {
      Alert.alert(t.stravaError, e?.message ?? String(e));
    }
  }

  async function handleStravaDisconnect() {
    await stravaLogout();
    setStravaAuth(false);
    Alert.alert('Strava', t.stravaDisconnected);
  }

  return (
    // keyboardShouldPersistTaps: a tap on Save/Apply works while the keyboard is up, instead of
    // only closing the keyboard.
    <ScrollView ref={scrollRef} style={styles.root} contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
      onScrollBeginDrag={() => { pendingScroll.current = null; }}>
      {/* ── Connections - real, 2026-08-09 ("settings was completely reworked in our
          desktop app... proceed"). Ports SettingsPage.qml's real Connections card: one
          compact card, a status-dot row per service, tap opens that service's own form in
          a modal - not four always-expanded full-height sections. ── */}
      {/* 2026-10-21 (Settings split): first card on the screen, each account with its own button. */}
      <View style={styles.section} onLayout={recordCard('connections')}>
        <View style={styles.cardHead}>
          <IconBadge icon="sync" />
          <Text style={styles.cardTitle}>{t.connectionsSection}</Text>
        </View>
        <ConnectionRow
          name={t.intervalsSection}
          connected={intervalsSaved}
          status={intervalsSaved ? 'Connected' : 'Not connected'}
          detail={intervalsSaved && intervalsAthleteId ? `athlete ${intervalsAthleteId}` : ''}
          action={intervalsSaved ? 'Manage' : 'Set up'}
          onPress={() => setOpenConnection('intervals')}
        />
        <ConnectionRow
          name={t.runalyzeSection}
          connected={!!savedKey}
          status={savedKey ? 'Connected' : 'Not connected'}
          action={savedKey ? 'Manage' : 'Set up'}
          onPress={() => setOpenConnection('runalyze')}
        />
        <ConnectionRow
          name={t.stravaSection}
          connected={stravaAuth}
          status={stravaAuth ? 'Connected' : 'Not connected'}
          action={stravaAuth ? 'Manage' : 'Connect'}
          onPress={() => setOpenConnection('strava')}
        />
        {/* Bluetooth body scale (Etekcity FIT 8S): listened to directly, no account. */}
        <ConnectionRow
          name="Bluetooth scale"
          connected={!!scaleAddr && scaleStatus().listening}
          status={!scaleAddr ? 'Not set up' : scaleStatus().listening ? 'Listening' : 'Not listening'}
          action={scaleAddr ? 'Manage' : 'Set up'}
          onPress={() => setScaleOpen(true)}
        />
        <ScaleSetupModal visible={scaleOpen} onClose={() => setScaleOpen(false)}
          onChanged={async () => setScaleAddr(await getScaleAddress())} />
        {/* Dropbox / Google Drive / OneDrive rows removed (André, 2026-08-16): cloud backup is
            now just "save to a folder" on the Backup screen (point it at a cloud sync folder),
            so there's no OAuth "connection" to set up here anymore. */}
      </View>

      <Modal visible={openConnection === 'strava'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.stravaSection}</Text>
            <Text style={styles.sectionDesc}>{t.stravaSettingsDesc}</Text>
            {stravaAuth ? (
              <>
                <StatusChip label={t.stravaConnectedStatus} tone="success" style={{ marginTop: 8 }} />
                <View style={styles.row}>
                  <Button label={t.stravaDisconnectBtn} variant="text" grow={false} onPress={handleStravaDisconnect} />
                </View>
              </>
            ) : (
              <View style={styles.row}>
                <Button label={t.connect} variant="filled" onPress={handleStravaConnect} />
              </View>
            )}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      <Modal visible={openConnection === 'runalyze'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.runalyzeSection}</Text>
            <Text style={styles.sectionDesc}>{t.runalyzeDesc}</Text>
            <Text style={styles.sectionDesc}>
              {t.runalyzeApiHint}
              <Text style={styles.link}>{t.runalyzeApiLink}</Text>
            </Text>
            <FieldRow
              icon="key"
              value={runalyzeKey}
              onChangeText={setRunalyzeKey}
              placeholder={t.apiKeyPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
            <View style={styles.row}>
              <Button label={t.saveBtn} variant="filled" loading={saving} onPress={handleSaveRunalyze} />
              {!!savedKey && (
                <Button label={t.deleteBtn} icon="delete" variant="outline" tone="alert" grow={false} onPress={handleRemoveRunalyze} />
              )}
            </View>
            {!!savedKey && <StatusChip label={t.keyStored} tone="success" style={{ marginTop: 10 }} />}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      <Modal visible={openConnection === 'intervals'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.intervalsSection}</Text>
            <Text style={styles.sectionDesc}>{t.intervalsDesc}</Text>
            <Text style={styles.sectionDesc}>
              {t.intervalsApiHint}
              <Text style={styles.link}>{t.intervalsApiLink}</Text>
            </Text>
            <FieldRow
              icon="person"
              value={intervalsAthleteId}
              onChangeText={setIntervalsAthleteId}
              placeholder={t.athleteIdPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
            />
            <FieldRow
              icon="key"
              value={intervalsApiKey}
              onChangeText={setIntervalsApiKey}
              placeholder={t.apiKeyPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
            <View style={styles.row}>
              <Button label={t.saveBtn} variant="filled" loading={savingIntervals} onPress={handleSaveIntervals} />
              {intervalsSaved && (
                <Button label={t.deleteBtn} icon="delete" variant="outline" tone="alert" grow={false} onPress={handleRemoveIntervals} />
              )}
            </View>
            {intervalsSaved && <StatusChip label={t.credsStored} tone="success" style={{ marginTop: 10 }} />}

            {/* Gear import/sync - lives here in the intervals.icu connection (André, 2026-08-18),
                only once credentials are stored. Import pulls your gear down; Sync is two-way. */}
            {intervalsSaved && (
              <>
                <Text style={[styles.cardTitle, { marginTop: 16 }]}>{t.gearScreenTitle}</Text>
                <View style={styles.row}>
                  <Button label={t.gearImportBtn} variant="filled" loading={gearImporting} onPress={handleGearImport} />
                  <Button label={t.gearSyncBtn} variant="outline" grow={false} loading={gearSyncing} onPress={handleGearSync} />
                </View>
                <Text style={styles.sectionDesc}>{t.gearImportHint}</Text>
                {/* Import ALL activities from intervals.icu into the app (André, 2026-08-18). */}
                <Text style={[styles.cardTitle, { marginTop: 16 }]}>Activities</Text>
                <View style={styles.row}>
                  <Button label="Import activities" variant="filled" loading={actsImporting} onPress={handleImportActivities} />
                </View>
                <Text style={styles.sectionDesc}>
                  Pulls every activity from intervals.icu into the app (list, Totals, Calendar).
                  Already-synced watch moves are skipped, so nothing double-counts.
                </Text>
                {/* 🟣76 (2026-10-27): Sommet's own weigh-ins go to intervals.icu; devices never do. */}
                <Text style={[styles.cardTitle, { marginTop: 16 }]}>Weigh-ins</Text>
                <SwitchLine value={sendWeighIns} label="Send my weigh-ins to intervals.icu"
                  onValueChange={v => { setSendWeighInsState(v); setSendWeighIns(v); }} />
                {sendWeighIns && (
                  <SwitchLine value={sendBodyFat} label="Also send the estimated body fat"
                    onValueChange={v => { setSendBodyFatState(v); setSendBodyFat(v); }} />
                )}
                <Text style={styles.sectionDesc}>
                  Each new weigh-in from your Bluetooth scale, or typed on the Weight screen, is added to
                  that day in intervals.icu. Body fat from the scale is an estimate, and intervals.icu
                  won't show it as one.
                </Text>
              </>
            )}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      {/* ── Apparence ── */}
      <View style={styles.section} onLayout={recordCard('appearance')}>
        <View style={styles.cardHead}>
          <IconBadge icon={mode === 'light' ? 'sun' : mode === 'dark' ? 'moon' : 'auto'} />
          <Text style={styles.cardTitle}>{t.appearanceSection}</Text>
        </View>
        <Text style={styles.sectionDesc}>{t.appearanceDesc}</Text>
        {/* One switch for the three choices (André, 2026-11-02, desktop picks #14). */}
        <SegmentedControl
          style={{ marginTop: 12 }}
          options={THEME_OPTIONS.map(o => o.label())}
          index={THEME_OPTIONS.findIndex(o => o.mode === mode)}
          onChange={i => setMode(THEME_OPTIONS[i].mode)}
        />
        {/* The menu's style on this device, and the places in the fan or the bar (🟣113). */}
        <MenuSettings />
      </View>

      {/* ── Show in the sidebar (Settings split, decision 1): every switch that shows or hides a page
          in the menu, together. They used to sit in the Race Plan, Ember and Experimental cards; all
          are still experimental. ── */}
      <View style={styles.section} onLayout={recordCard('sidebar')}>
        <View style={styles.cardHead}>
          <Text style={styles.cardTitle}>Show in the sidebar</Text>
        </View>
        {/* Race Plan (2026-09-29, desktop parity: same wording, off by default). */}
        <SidebarSwitch
          title="Race Plan"
          description="BRM/ultra race planner: import a GPX + roadbook, then get realistic arrival times, cutoff margins, weather, sleep and resupply along the route. Still rough — turn it on to try it."
          value={racePlanOn}
          onValueChange={async v => { await setRacePlanEnabled(v); setRacePlanOn(v); }}
        />
        {/* Ember (2026-08-29, desktop parity): openly opt-in, off by default. Its sync and food-search
            settings are in the Ember row below, shown once it's on. */}
        <SidebarSwitch
          title="Ember"
          description="Fast & calorie tracking companion. Logs made on your phone show here as charts. Still rough — turn it on to try it."
          value={emberOn}
          onValueChange={async v => { await setEmberUnlocked(v); setEmberOn(v); }}
        />
        {/* One switch per experimental feature (André, 2026-08-17). Turning one on opens it right
            away (App Zone should jump straight to the Suunto catalog); once on, tapping its name
            reopens it. */}
        {EXP_FEATURE_ROWS.map(f => (
          <SidebarSwitch
            key={f.flag}
            title={f.label}
            description={f.desc}
            value={features[f.flag]}
            onValueChange={v => { setFeature(f.flag, v); if (v) navigation.navigate(f.screen); }}
            onOpen={() => navigation.navigate(f.screen)}
          />
        ))}
        {/* Devices (André, 2026-10-22; desktop parity): one switch per Bluetooth bike computer. Off:
            no card on Home, not in the pairing list, not offered by Routes - still remembered.
            Plugged-in devices always show. On Android that's the Magene (the desktop also has the
            Bryton Aero 60 and the Wahoo ELEMNT over Bluetooth). */}
        {isMageneAvailable() && (
          <>
            <View style={{ marginTop: 10 }}>
              <Text style={[styles.connRowText, { fontWeight: '700' }]}>Devices</Text>
              <Text style={{ fontSize: 12, color: theme.mutedText, marginTop: 2 }}>
                A card on Home and a choice in the Bluetooth pairing list. Plugged-in devices always show.
              </Text>
            </View>
            <SidebarSwitch
              title="Magene C406"
              description="Bike GPS over Bluetooth."
              value={features.magene}
              onValueChange={v => setFeature('magene', v)}
            />
          </>
        )}
        <Text style={styles.sectionDesc}>{t.experimentalToggleDesc}</Text>
        {anyEnabled && (
          <Text style={[styles.sectionDesc, { color: theme.warning, marginTop: 10 }]}>{t.experimentalWarningBanner}</Text>
        )}
      </View>

      {/* ── Backup & restore — desktop parity: SettingsPage.qml's Backup card (moved out of the
          nav with Offline maps, André 2026-08-31). Was a top-level Home item on Android. ── */}
      <View style={styles.section} onLayout={recordCard('backup')}>
        <View style={styles.cardHead}>
          <IconBadge icon="backup" />
          <Text style={styles.cardTitle}>Backup &amp; restore</Text>
        </View>
        <Text style={styles.sectionDesc}>
          Back up your watch and app data, and restore from a backup.
        </Text>
        <Button label="Open backup & restore…" icon="backup" variant="outline"
          onPress={() => navigation.navigate('Backup')} style={{ marginTop: 10 }} />
      </View>

      {/* ── Everything else: one closed row each, opened in place (Settings split). ── */}
      <View style={[styles.section, styles.rowsCard]} onLayout={recordCard('rows')}>
        {/* Activities - the extra power numbers most rides don't need (desktop parity). */}
        <SettingsSection
          title="Activities"
          summary={`Advanced power numbers: ${advancedPower ? 'on' : 'off'}`}
          open={isOpen('activities')}
          onToggle={toggleSection('activities')}
          onLayout={recordRow('activities')}
        >
          <SwitchLine value={advancedPower} onValueChange={handleToggleAdvancedPower} label="Advanced power numbers" />
          <Text style={styles.sectionDesc}>
            Adds normalized power, training load (TSS), intensity factor and work to rides with a power
            meter. Uses the FTP from your intervals.icu settings.
          </Text>
        </SettingsSection>

        {/* ── Health - morning HRV from a BLE heart-rate strap (André, 2026-09-04). Opt-in,
            default OFF, mirroring the desktop health/coospoHrvEnabled toggle: an intervals.icu-
            only user sees nothing; a strap user turns this on and the "Measure morning HRV" card
            appears on the Health screen. Any strap advertising the HR service works (Verity Sense,
            COOSPO, …) - the reader is device-agnostic. ── */}
        <SettingsSection
          title="Health"
          summary={`Sleep score: ${icuSleepScore ? 'from intervals.icu' : "Sommet's own"} · strap HRV: ${strapHrvEnabled ? 'on' : 'off'}`}
          icon="health"
          open={isOpen('health')}
          onToggle={toggleSection('health')}
          onLayout={recordRow('health')}
        >
          {/* Same controls as desktop, minus what Android has no source for: no Garmin Connect
              (so no sleep/HRV provider choice) and no Ambit3 5+5 HRV import. */}
          <Text style={[styles.sectionDesc, { fontSize: 11, lineHeight: 16 }]}>
            Resting HR, steps, HRV and sleep come from intervals.icu, whatever device feeds it.
          </Text>
          {/* Which sleep score Health shows (André, 2026-10-04: "can we make it selectable?"). */}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
            <Text style={[styles.connRowText, { flex: 1 }]}>Sleep score</Text>
            <Dropdown
              value={icuSleepScore ? 1 : 0}
              choices={[{ value: 0, label: "Sommet's own" }, { value: 1, label: 'From intervals.icu' }]}
              onSelect={v => { setIcuSleepScore(v === 1); setSleepScoreSource(v === 1 ? 'intervals' : 'sommet').catch(() => {}); }}
            />
          </View>
          {/* Morning HRV from a BLE heart-rate strap (André, 2026-09-04). Opt-in, default OFF,
              mirroring the desktop health/coospoHrvEnabled switch. */}
          <SwitchLine
            value={strapHrvEnabled}
            onValueChange={handleToggleStrapHrv}
            label="Heart-rate-strap morning HRV (Polar Verity Sense, COOSPO, …) — measure directly over Bluetooth, no watch. Turn on to show the Measure card on the Health page."
            a11yLabel="Heart-rate-strap morning HRV"
          />
        </SettingsSection>

        {/* ── Maps - real, 2026-08-09 ("no button to change provider, nor in the settings
            like the desktop version"). Ports SettingsPage.qml's real Maps card (icon + title
            + "Provider: tiles from X" + exclusive toggle buttons), with IGN added as a
            genuine Android-only third option (MapProviderService.ts's own header comment). ── */}
        <SettingsSection
          title={t.mapsSection}
          summary={`${MAP_PROVIDER_LABELS[mapProvider]} · offline tile cache ${tileCacheBytes === null ? '…' : formatCacheSize(tileCacheBytes)}`}
          icon="route"
          open={isOpen('maps')}
          onToggle={toggleSection('maps')}
          onLayout={recordRow('maps')}
        >
          <Text style={styles.sectionDesc}>{t.mapsProviderDesc(MAP_PROVIDER_LABELS[mapProvider])}</Text>
          <SegmentedControl
            style={{ marginTop: 6 }}
            options={MAP_PROVIDER_OPTIONS.map(o => o.label())}
            index={MAP_PROVIDER_OPTIONS.findIndex(o => o.provider === mapProvider)}
            onChange={i => handleSetMapProvider(MAP_PROVIDER_OPTIONS[i].provider)}
          />
          {/* Offline maps — desktop parity: SettingsPage.qml's Maps card has this button; on Android
              it used to be a top-level menu item (moved here, André 2026-09-24). Download any area of
              the world so the map works with no signal. */}
          <Button label="Download offline maps…" icon="download" variant="outline"
            onPress={() => navigation.navigate('OfflineMaps')} style={{ marginTop: 10 }} />
          {/* placed after the provider chips; the tile-cache readout follows below */}
          {/* Real, 2026-08-10 ("let's go for the offline maps solution") - TileCache.ts's own
              cache (route/activity map tiles saved for offline use via MapScreen.tsx's own
              download button) is unbounded until cleared manually - a simple size readout +
              clear action here, the same "let the user manage it" pattern this project uses
              for the GPX/exports cache directories elsewhere, not automatic eviction. */}
          <View style={styles.cacheRow}>
            <Text style={styles.sectionDesc}>
              {t.offlineMapCacheSize(tileCacheBytes === null ? '…' : formatCacheSize(tileCacheBytes))}
            </Text>
            <Button
              label={t.offlineMapClearCache}
              variant="outline"
              tone="alert"
              grow={false}
              loading={clearingCache}
              disabled={!tileCacheBytes}
              onPress={handleClearTileCache}
            />
          </View>
        </SettingsSection>

        {/* ── Weather (desktop parity, 2026-10-22): where Home's forecast is for. ── */}
        <SettingsSection
          title="Weather"
          summary={weatherLoc.placeName ? `Open-Meteo · ${weatherLoc.placeName}` : 'Provider: Open-Meteo'}
          icon="sun"
          open={isOpen('weather')}
          onToggle={toggleSection('weather')}
          onLayout={recordRow('weather')}
        >
          <Text style={[styles.sectionDesc, { fontSize: 12 }]}>Provider: Open-Meteo</Text>
          <Text style={[styles.connRowText, { marginTop: 4 }]}>Location source</Text>
          <View style={[styles.chipRow, { justifyContent: 'flex-start', marginTop: 6 }]}>
            {[{ manual: false, label: 'This phone (IP-based)' }, { manual: true, label: 'Manual' }].map(o => {
              const on = weatherManual === o.manual;
              return (
                <TouchableOpacity
                  key={o.label}
                  style={[styles.chip, on && styles.chipActive]}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                  onPress={() => { setWeatherManual(o.manual); if (!o.manual) locateByIp(); }}
                >
                  <Text style={[styles.chipText, on && styles.chipTextActive]}>{o.label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <View style={{ flex: 1 }}>
              <FieldRow icon="poi" value={latText} onChangeText={setLatText} placeholder="Latitude"
                keyboardType="numbers-and-punctuation" accessibilityLabel="Latitude" />
            </View>
            <View style={{ flex: 1 }}>
              <FieldRow icon="poi" value={lonText} onChangeText={setLonText} placeholder="Longitude"
                keyboardType="numbers-and-punctuation" accessibilityLabel="Longitude" />
            </View>
          </View>
          <View style={styles.row}>
            <Button label="Apply" variant="outline" grow={false} onPress={handleApplyWeather} />
          </View>
        </SettingsSection>

        {/* Ember's own settings (sync, food search), once it's on in "Show in the sidebar". */}
        {emberOn && (
        <SettingsSection
          title="Ember"
          summary="Ember sync · food search key"
          icon="ember"
          open={isOpen('ember')}
          onToggle={toggleSection('ember')}
          onLayout={recordRow('ember')}
        >
          <Text style={styles.cardTitle}>Ember sync</Text>
          <Text style={styles.sectionDesc}>
            Shared store URL + token so Ember logs merge with your other devices (the same
            ember/sync.json the desktop uses). Leave blank to log on this device only.
          </Text>
          <FieldRow
            icon="link"
            value={emberSyncUrl}
            onChangeText={setEmberSyncUrl}
            placeholder="https://192.168.1.102/ember/sync.php"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <FieldRow
            icon="key"
            value={emberSyncToken}
            onChangeText={setEmberSyncToken}
            placeholder="token"
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
          />
          <View style={styles.row}>
            <Button label={t.saveBtn} variant="filled" loading={savingEmberSync} onPress={handleSaveEmberSync} />
          </View>

          <Text style={[styles.cardTitle, { marginTop: 12 }]}>Food search</Text>
          <Text style={styles.sectionDesc}>
            Generic foods come from USDA FoodData Central. Without your own key a shared demo key
            is used, limited to about 30 searches an hour. The key is free and stays on this phone.
          </Text>
          <FieldRow
            icon="key"
            value={usdaKey}
            onChangeText={setUsdaKey}
            placeholder="USDA API key (optional)"
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
          />
          <View style={styles.row}>
            <Button label="Get a free key" variant="outline"
              onPress={() => Linking.openURL('https://fdc.nal.usda.gov/api-key-signup')} />
            <Button label={t.saveBtn} variant="filled"
              onPress={async () => { await setUsdaApiKey(usdaKey); Alert.alert('Ember', usdaKey.trim() ? 'USDA key saved.' : 'USDA key cleared (demo key).'); }} />
          </View>
        </SettingsSection>
        )}

        {/* ── Coach (desktop parity, 2026-10-22): the reply choice and the Anthropic key, which used
            to sit in the intervals.icu window. Shown while Coach is on in "Show in the sidebar". ── */}
        {features.coach && (
        <SettingsSection
          title="Coach"
          summary={`Replies: ${chatBackend === 'claude' ? 'Claude API' : 'pre-written'} · catalogue: bundled sample`}
          icon="coach"
          open={isOpen('coach')}
          onToggle={toggleSection('coach')}
          onLayout={recordRow('coach')}
        >
          <Text style={[styles.connRowText, { fontWeight: '700' }]}>Chat with a real AI coach</Text>
          <Text style={[styles.sectionDesc, { fontSize: 11, lineHeight: 16, marginTop: 2 }]}>
            Off, the coach replies from a few pre-written answers. On, it talks to Claude about your
            real training. This needs an API key you create at console.anthropic.com — that's a
            developer key, separate from a claude.ai subscription (your normal Claude login won't
            work here), and it's billed separately, a few cents per chat.
          </Text>
          <SwitchLine
            value={chatBackend === 'claude'}
            onValueChange={handleChatBackend}
            label={chatBackend === 'claude' ? 'Claude API' : 'Canned replies'}
            a11yLabel="Chat with a real AI coach"
            accent
          />
          {chatBackend === 'claude' && (
            <>
              <FieldRow
                icon="key"
                value={anthropicKey}
                onChangeText={setAnthropicKeyInput}
                placeholder={anthropicSaved ? 'Key saved (enter to replace)' : 'Anthropic API key'}
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
                accessibilityLabel="Anthropic API key"
              />
              <View style={styles.row}>
                <Button label={t.saveBtn} variant="filled" grow={false} loading={savingAnthropic}
                        disabled={!anthropicKey.trim()} onPress={handleSaveAnthropic} />
                {anthropicSaved && (
                  <Button label="Clear" variant="outline" danger grow={false} onPress={handleRemoveAnthropic} />
                )}
              </View>
            </>
          )}
          <Text style={[styles.connRowText, { fontWeight: '700', marginTop: 16 }]}>Workout catalogue</Text>
          <Text style={[styles.sectionDesc, { marginTop: 2 }]}>
            Bundled sample ({COACH_SAMPLE_SESSIONS} sessions). The live wahoo-systm-mcp catalogue is on
            the desktop app only.
          </Text>
        </SettingsSection>
        )}

        {/* ── Database (#SYNC / André 2026-09-20): sync this phone with the computers through the
            user's own server/NAS — the only way to include the phone. A copy always stays on the
            phone (offline-first). No folder option here: phones can't keep their DB in a synced
            folder (OS sandbox) — that's desktop-only. ── */}
        <SettingsSection
          title="Database"
          summary={`Sync across devices: ${sommetSaved ? 'on' : 'off'}`}
          icon="sync"
          open={isOpen('database')}
          onToggle={toggleSection('database')}
          onLayout={recordRow('database')}
        >
          <Text style={styles.sectionDesc}>
            Sync this phone with your computers through your own server or NAS — the only way to
            include the phone. A copy always stays on this phone, so it works offline.
          </Text>
          <FieldRow
            icon="link"
            value={sommetSyncUrl}
            onChangeText={setSommetSyncUrl}
            placeholder="https://…ts.net/sommet/sync.php"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <FieldRow
            icon="key"
            value={sommetSyncToken}
            onChangeText={setSommetSyncToken}
            placeholder="token"
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry
          />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            <Button label="Test" variant="outline" onPress={handleTestSommet} />
            <Button label={t.saveBtn} variant="filled" loading={savingSommet} onPress={handleSaveSommet} />
            <Button label="Sync now" variant="outline" loading={syncingSommet} disabled={!sommetSaved} onPress={handleSyncNow} />
          </View>
          {sommetSaved && (
            <Button label="Stop syncing" variant="outline" grow={false} onPress={handleStopSommet}
              style={{ marginTop: 8, alignSelf: 'flex-start' }} />
          )}
          <StatusNote text={sommetStatus} tone={sommetTone} />
        </SettingsSection>

        {/* ── Suunto Apps Catalog (desktop parity, 2026-10-22). Same import the App Zone screen has. ── */}
        {isCatalogAvailable() && (
        <SettingsSection
          title="Suunto Apps Catalog"
          summary={catalogEntries === null ? ''
            : catalogImported ? `Imported - ${catalogEntries} apps available`
            : 'Not imported - only apps you create are available'}
          icon="download"
          open={isOpen('catalog')}
          onToggle={toggleSection('catalog')}
          onLayout={recordRow('catalog')}
        >
          <Text style={[styles.sectionDesc, { fontSize: 11, lineHeight: 16 }]}>
            The official catalog of ~13,100 pre-compiled Suunto Apps - interval timers, HR-zone
            displays, and thousands of Movescount-era community apps. This app never ships or
            downloads it (most of it is other people's own work, not ours to redistribute) - import
            your own copy of suunto-apps/index.json from your own SuuntoLink installation instead.
            Suunto stopped updating this file when Movescount was retired, so any copy you have is
            the same copy - there's nothing to keep up to date. If you don't import it, you'll still
            have every app you create yourself in the Workout Builder - you just won't have this
            existing catalog to browse and install from too.
          </Text>
          {catalogEntries !== null && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 }}>
              {catalogImported && <Icon name="check" size={16} color={theme.success} />}
              <Text style={{ fontSize: 12, color: theme.mutedText }}>
                {catalogImported ? `Imported - ${catalogEntries} apps available`
                  : 'Not imported - only apps you create are available'}
              </Text>
            </View>
          )}
          <StatusNote text={catalogError} tone="error" />
          <Button
            label={catalogImported ? 'Import a different copy' : 'Import Catalog'}
            icon="download" variant="outline" grow={false} loading={catalogImporting}
            disabled={catalogImporting || catalogEntries === null}
            onPress={handleImportCatalog} style={{ marginTop: 10, alignSelf: 'flex-start' }} />
        </SettingsSection>
        )}

        {/* The feature switches moved to "Show in the sidebar" (2026-10-21). */}
        <SettingsSection
          title={t.experimentalSection}
          summary={`Mark synced workouts: ${markSyncedEnabled ? 'on' : 'off'}`}
          icon="watch"
          open={isOpen('experimental')}
          onToggle={toggleSection('experimental')}
          onLayout={recordRow('experimental')}
        >
          {/* Mark-synced write-back - independent opt-in, not gated by the master toggle
              (2026-08-16). Writes the watch's own per-move synced flag so the Suunto app /
              SuuntoLink don't duplicate; tradeoff spelled out so nobody enables it blindly. */}
          <Text style={[styles.connRowText, { fontWeight: '700' }]}>{t.markSyncedLabel}</Text>
          <Text style={[styles.sectionDesc, { marginTop: 2 }]}>{t.markSyncedDesc}</Text>
          <SwitchLine value={markSyncedEnabled} onValueChange={handleToggleMarkSynced}
            label={markSyncedEnabled ? 'On' : 'Off'} a11yLabel={t.markSyncedLabel} accent />
        </SettingsSection>

        {/* ── Testing mode (2026-08-16, ported from desktop): pretend a device is connected so
            the app can be explored without one. Toggle + a picker of the watches the app knows
            (Ambit3 family / Traverse / Kailash). Default OFF. ── */}
        <SettingsSection
          title={t.testingSection}
          summary={demo.enabled ? t.testingOnShowing(demo.device.name) : 'Off'}
          icon="watch"
          open={isOpen('testing')}
          onToggle={toggleSection('testing')}
          onLayout={recordRow('testing')}
        >
          <Text style={styles.sectionDesc}>{t.testingDesc}</Text>
          <SwitchLine value={demo.enabled} onValueChange={demo.setEnabled}
            label={demo.enabled ? t.testingOnShowing(demo.device.name) : 'Off'} a11yLabel={t.testingSection} accent />
          {demo.enabled && (
            <View style={{ marginTop: 10 }}>
              <Button label={t.testingChange} onPress={() => setDemoPickerOpen(true)} variant="outline" grow={false}
                style={{ alignSelf: 'flex-start' }} />
            </View>
          )}
        </SettingsSection>

        {/* Firmware flashing is USB-only (no USB on iOS), and not for a Garmin. */}
        {!isGarminAttached && Platform.OS !== 'ios' && (
        <SettingsSection
          title="Firmware"
          summary="Flash watch firmware over USB"
          icon="watch"
          open={isOpen('firmware')}
          onToggle={toggleSection('firmware')}
          onLayout={recordRow('firmware')}
        >
          <Text style={styles.sectionDesc}>
            Flash watch firmware over USB. Advanced and irreversible — intended for a supervised
            session. Opens the firmware tool.
          </Text>
          <Button label="Open firmware tool" icon="upload" variant="outline"
            onPress={() => navigation.navigate('Firmware')} style={{ marginTop: 10 }} />
        </SettingsSection>
        )}

        {/* ── Supported devices (desktop parity, 2026-10-22): shown while a Garmin eTrex is plugged in. ── */}
        {isGarminAttached && (
        <SettingsSection
          title="Supported devices"
          summary="What Sommet can read, and how it connects"
          icon="settings"
          open={isOpen('devices')}
          onToggle={toggleSection('devices')}
          onLayout={recordRow('devices')}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: theme.mutedText }} />
            <Text style={{ fontSize: 12, color: theme.mutedText }}>Suunto Ambit 3 (USB)</Text>
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: theme.success }} />
            <Text style={{ fontSize: 12, color: theme.text }}>Garmin eTrex — connected</Text>
          </View>
        </SettingsSection>
        )}

        {/* ── About / disclaimer ── */}
        <SettingsSection
          title={t.aboutSection}
          summary={updateAvailable(update) && update.latest
            ? t.aboutVersionReady(APP_VERSION, update.latest.version) : t.aboutVersion(APP_VERSION)}
          open={isOpen('about')}
          onToggle={toggleSection('about')}
          onLayout={recordRow('about')}
          last
        >
          {/* The Ember easter egg (10 taps here) was retired 2026-08-29 - Ember is now an open
              opt-in toggle in its own card above, so the version is just plain text again. */}
          <Text style={styles.sectionDesc}>{t.aboutVersion(APP_VERSION)}</Text>

          {/* Updates (André, 2026-11-03, 🟣124): is there a newer Sommet on GitHub? */}
          {updatesSupported() && (
            <View style={{ gap: 8, marginBottom: 6 }}>
              <UpdateNotice closable={false} maxHighlights={8} />
              <StatusNote
                tone={update.phase === 'checking' ? 'working' : update.status === 'error' ? 'error' : 'success'}
                closable={false}
                text={update.phase === 'checking' ? t.updateChecking
                  : update.status === 'error' ? t.updateError
                  : update.status === 'upToDate' ? t.updateUpToDate : ''}
              />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <Button grow={false} label={t.updateCheck} busy={update.phase === 'checking'}
                  onPress={() => { checkNow(); }} />
                {update.lastChecked != null && (
                  <Text style={{ fontSize: 12, color: theme.mutedText }}>
                    {t.updateLastChecked(fmtChecked(update.lastChecked))}
                  </Text>
                )}
              </View>
              <SwitchLine value={update.autoCheck} onValueChange={v => { setAutoCheck(v); }} label={t.updateAutoCheck} />
              <Text style={styles.sectionDesc}>{t.updateAutoCheckDesc}</Text>
            </View>
          )}
          <Text style={[styles.sectionDesc, { marginTop: 10 }]}>{t.aboutDisclaimer}</Text>
          <Text style={styles.sectionDesc}>{t.aboutLicenses}</Text>
          {/* The full credits list lives on GitHub, same page as the PC app (André, 2026-11-03). */}
          <Text style={styles.sectionDesc}>{t.aboutBuiltOn}</Text>
          <View style={{ flexDirection: 'row', marginTop: 4 }}>
            <Button variant="outline" grow={false} label={t.aboutCreditsLink}
              onPress={() => { Linking.openURL(CREDITS_URL).catch(() => {}); }} />
          </View>
        </SettingsSection>
      </View>

      <DemoDevicePicker
        visible={demoPickerOpen}
        currentVariant={demo.variant}
        onPick={demo.setVariant}
        onClose={() => setDemoPickerOpen(false)}
      />

    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 20 },
  section: {
    backgroundColor: t.card,
    borderColor: t.mutedText + '33',
    borderWidth: 1,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  cardTitle: { fontSize: 16, fontWeight: '700', color: t.text },
  sectionDesc: { fontSize: 13, color: t.mutedText, marginBottom: 6, lineHeight: 19 },
  link: { color: t.text, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 10, marginTop: 12 },
  cacheRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10, gap: 10 },
  rowsCard: { padding: 4 },
  themeRow: { flexDirection: 'row', gap: 8, marginTop: 12 },
  viewPrefBlock: { marginTop: 14 },
  viewPrefLabel: { fontSize: 13.5, color: t.text, fontWeight: '600', marginBottom: 2 },
  themeOption: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1.4,
    borderColor: t.mutedText + '33',
    backgroundColor: t.card,
  },
  themeOptionSelected: {
    backgroundColor: t.primary,
    borderColor: t.primary,
  },
  themeOptionLabel: { fontSize: 13, fontWeight: '600', color: t.text },
  themeOptionLabelSelected: { color: t.card },
  btnText: { color: t.card, fontWeight: '600', fontSize: 14 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, flexShrink: 1, justifyContent: 'flex-end' },
  // Same tinted-primary pill treatment as primitives.tsx's own Chip - a real selected
  // state, not a neutral bordered box.
  chip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
    borderWidth: 1, borderColor: t.mutedText + '33', backgroundColor: t.card,
  },
  chipActive: { borderColor: t.primary, backgroundColor: t.primary + '1F' },
  chipText: { color: t.mutedText, fontSize: 12 },
  chipTextActive: { color: t.primary, fontWeight: '600' },
  // ── Connections card - compact tap-to-open rows + modal (v3.0 UI port) ──
  connRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  connDot: { width: 8, height: 8, borderRadius: 4 },
  connRowText: { color: t.text, fontSize: 14, flex: 1 },
  modalOverlay: {
    flex: 1, backgroundColor: '#00000066', justifyContent: 'flex-end',
  },
  modalBox: {
    backgroundColor: t.background, borderTopLeftRadius: 20, borderTopRightRadius: 20,
    padding: 20, maxHeight: '85%',
  },
});
