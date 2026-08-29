import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet,
  Alert, ActivityIndicator, useWindowDimensions, ScrollView, Linking,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import { runSync, SyncState } from '../services/SyncService';
import { getGearAlerts } from '../services/GearAlerts';
import { updateOrbitalData, OrbitalUpdateState } from '../services/SgeeService';
import { refreshActivityClassOnWatch } from '../services/AmbitSettingsService';
import {
  connect as ambitConnect, disconnect as ambitDisconnect, getDeviceInfo, AmbitDeviceInfo,
  listDevices, selectDevice, AmbitUsbDevice,
  wasLaunchedViaUsbAttach, onUsbAttached, detectAttachedDeviceType, AttachedDeviceType,
  readDeviceHistoryRaw, readDeviceLogRaw, setBleTransportActive, saveToDownloads,
  setDateTime,
} from '../native/AmbitUsbModule';
import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  scanAndConnect as bleScanAndConnect, scanAndConnectTo as bleScanAndConnectTo,
  listBondedWatches, BondedWatch, onBleDisconnected,
} from '../native/AmbitBleModule';
import * as Garmin from '../native/GarminModule';
import type { GarminConnectResult } from '../native/GarminModule';
import { syncGarminActivities, GarminActivitySyncState } from '../services/GarminActivityService';
import { kailashDeviceProvider } from '../services/devices/KailashDeviceProvider';
import { ambitBleDeviceProvider } from '../services/devices/AmbitBleDeviceProvider';
import { decodeDeviceHistory, KailashHistory } from '../services/KailashHistoryReader';
import { decodeDeviceLog, realTrackPoints, deviceLogToGpx, KailashDeviceLog } from '../services/KailashDeviceLogReader';
import { getAllActivities, ActivityRecord } from '../database/db';
import { distanceLines } from '../services/TotalsFacts';
import { isEmberUnlocked } from '../services/EmberUnlock';
import { APP_VERSION } from '../config/version';
import { useDemo } from '../config/DemoContext';
import { useExperimental } from '../config/ExperimentalContext';
import { manualUrlFor, garminManualUrlFor } from '../config/manuals';
import { t, fmtDate as fmtDateShared } from '../i18n';
import Icon from '../components/ui/Icon';
import { ActionTile, Badge, Button, Chip, StatusLine } from '../components/ui/primitives';
// v3.0 UI port (2026-08-09, "go all the way to the new theming") - the whole screen (not
// just the connected dashboard) is on the v3 palette now; tokens.ts/useTheme() aren't
// imported here anymore at all.
import { useV3Theme } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { WeatherCard } from '../components/WeatherCard';
import { TrackPreview } from '../components/TrackPreview';
import { NavShell, NavShellItem } from '../navigation/NavShell';

// Real, 2026-08-08: Kailash ("Hoopoe") answers the same USB init + 0x0000 device-info
// commands every Ambit/Traverse does (AmbitUsbModule.kt's SUUNTO_PID_NAMES/
// device_filter.xml both now include its real product ID) - detectAttachedDeviceType()
// already reports it as "ambit", no separate branch needed there. This is the one place
// that distinguishes it: everywhere below that would otherwise assume Ambit3's own
// ExerciseLog/sport-mode shape switches to the Kailash-specific path instead.
const isKailash = (info: AmbitDeviceInfo | null) => info?.model === 'Hoopoe';
// Traverse (Jabiru) / Traverse Alpha (Loon) - no TrainingProgram region in their 0x0b21 map
// (confirmed in the real traverse pcaps), so they can't hold planned moves. Used to hide
// Intervals for them, same as it's already hidden for the Kailash. André, 2026-08-18.
const isTraverse = (info: AmbitDeviceInfo | null) => info?.model === 'Jabiru' || info?.model === 'Loon';
// Ambit 1 / Ambit 2 family (Bluebird / Duck / Colibri / Greentit codenames - openambit
// device_support.c). These use the LEGACY ambit driver: no SBEM 0x0b21 region map, so no App
// Zone / Apps region and no guided-workout support at all. Intervals + the Workout Calendar are
// App-Zone features, so they must be hidden for this family (André, 2026-08-27: "not sure ambit
// 1 and 2 support workouts" - they don't).
const isAmbit12 = (info: AmbitDeviceInfo | null) =>
  info?.model === 'Bluebird' || info?.model === 'Duck' || info?.model === 'Colibri' || info?.model === 'Greentit';

// Multi-watch switcher (2026-08-16): one unified entry per pickable watch, spanning both
// transports — a cabled watch (USB, keyed by its stable USB path) or a paired one (BLE, keyed
// by its Bluetooth address). Reconciles the two earlier switchers (USB-path vs productId): the
// USB path wins as the id because it tells apart two watches of the *same* model, which a
// productId can't. `name` is already the friendly device name from the native list.
type SwitcherWatch = {
  key: string;
  name: string;
  transport: 'usb' | 'ble';
  usbDeviceName?: string; // USB: the path passed to selectDevice()
  bleAddress?: string;    // BLE: the MAC passed to scanAndConnectTo()
};
// Trim the redundant "Suunto " so a pill reads "Kailash", "Ambit3 Peak", etc.
const watchPillName = (name: string) => name.replace(/^Suunto\s+/i, '');

// Persisted switcher choice (2026-08-16), so the app reconnects to the watch the user last
// picked instead of grabbing whichever enumerates first on every launch. Keyed by USB productId
// (stable per model across replug, unlike the USB path) or BLE address, never the volatile path.
const SELECTED_WATCH_KEY = 'ambit.selectedWatch.v1';
type SavedWatch = { transport: 'usb'; productId: number } | { transport: 'ble'; bleAddress: string };

type Nav = NativeStackNavigationProp<RootStackParamList, 'Home'>;
type ActiveAction = 'sync' | 'orbital';

// v2.3.2 beta — how the device area of Home progresses. 'searching'/'connecting'
// happen automatically (no manual "Connect" tap anymore, per André's spec: the
// OS's USB_DEVICE_ATTACHED intent-filter + device_filter.xml already launches/
// forefronts the app when something is plugged in — this state machine is what
// runs once that's happened, not a replacement for it).
type ConnPhase = 'searching' | 'connecting' | 'connected' | 'timeout' | 'connect-error';

// Real, 2026-08-09 (v3.0 planning: "via usb should be auto detected, refresh rate
// 2seconds if no usb is detected") - was 1200ms, bumped to the real requested 2s. Bluetooth
// stays button-triggered (homeBleConnectBtn / handleBleConnectRef), not auto-polled - the
// watch's BLE advertising window is short and scanning continuously would drain it for no
// benefit, see the BLE-connect comments further down this file.
const SEARCH_POLL_MS = 2000;
const SEARCH_TIMEOUT_MS = 15000;
const CONNECTED_POLL_MS = 4000;

// ─── Composant principal ──────────────────────────────────────────────────────
export default function HomeScreen() {
  // v3.0 UI port (2026-08-09, "go all the way to the new theming") - the whole screen is on
  // the v3 palette now, including the pre-connect phase screens (searching/no-device/
  // connecting/error/later) that were deliberately left on the old theme during the initial
  // proof-of-concept pass. `theme` is kept as the local name (not renamed to `v3`) purely to
  // avoid a much larger diff below - every value it returns is v3Colors now.
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<Nav>();
  const demo = useDemo();
  const { features: expFeatures } = useExperimental();  // gate Intervals / Smart Sensor menu items
  // deviceName/deviceSub keep their existing font metrics (createStyles(theme) above still
  // owns size/weight/spacing) - these two exist only because deviceName/deviceSub are style
  // *objects* (not just color), so overriding color needs a second style-array entry rather
  // than editing the object in place.
  const v3TextStyle = { color: theme.text };
  const v3MutedStyle = { color: theme.mutedText };

  const { width: winWidth, height: winHeight } = useWindowDimensions();
  // "Roomy" = actually landscape AND wide enough (a landscape tablet / large window),
  // where the connected screen lays its cards side by side and uses more tile columns.
  // Must be orientation-based, not width-only: this tablet is 800 px wide in PORTRAIT,
  // so a width-only test wrongly treated portrait as roomy. Portrait always keeps the
  // single-column layout. Kept in sync with ActionTile's own copy of this check.
  const roomy = winWidth > winHeight && winWidth >= 700;
  const [sync, setSync] = useState<SyncState>({ phase: 'idle', current: 0, total: 0, newCount: 0 });
  const [orbital, setOrbital] = useState<OrbitalUpdateState>({ phase: 'idle' });
  const [lastActive, setLastActive] = useState<ActiveAction>('sync');

  // Garmin activity sync — runs inline from the "Sync Activities" button, no
  // sub-screen (per André's feedback: "just like the suunto counterpart, it
  // should read the activities and log them. no sub menu needed").
  const [garminSync, setGarminSync] = useState<GarminActivitySyncState>({ phase: 'idle', current: 0, total: 0, newCount: 0 });
  const garminSyncBusy = garminSync.phase !== 'idle' && garminSync.phase !== 'done' && garminSync.phase !== 'error';

  const syncBusy    = sync.phase    !== 'idle' && sync.phase    !== 'done' && sync.phase    !== 'error';
  const orbitalBusy = orbital.phase !== 'idle' && orbital.phase !== 'done' && orbital.phase  !== 'error';
  const isBusy = syncBusy || orbitalBusy || garminSyncBusy;

  // Real, 2026-08-10 - see AmbitUsbModule.ts's own setDateTime() comment for the mechanism
  // (cable Ambit3's proven 0x0300/0x0302 pair, or Kailash's real BLE-only 0x1201 pushes).
  // No status persists across screens - a one-shot "did it work" line is enough here.
  const [timeSyncBusy, setTimeSyncBusy] = useState(false);
  // Ember easter-egg state (2026-08-26): the tile below only renders once the Settings
  // version-label taps have unlocked it. Re-read on every focus, not just on mount - Home stays
  // mounted while you visit Settings, so a mount-only read left the freshly-unlocked tile
  // invisible until the app was restarted (caught on the tablet, 2026-08-26).
  const [emberUnlocked, setEmberUnlockedState] = useState(false);
  useFocusEffect(useCallback(() => { isEmberUnlocked().then(setEmberUnlockedState); }, []));
  const [timeSyncMsg, setTimeSyncMsg] = useState<string | null>(null);
  const handleSyncTime = useCallback(async () => {
    setTimeSyncBusy(true);
    setTimeSyncMsg(null);
    // Real, 2026-08-10 ("why it does that? doesn't make much sense... after error we
    // should be able to try again") - USB isn't a persistent link in this app: every
    // other USB operation (handleSync via SyncService.ts, connectFlow's own device-info/
    // history read above) already does its own connect()-operate-disconnect() cycle
    // rather than assuming one stays open, because connectFlow() itself disconnects
    // right after the initial info/history read, before the user can tap anything.
    // This button inherited neither half of that - it assumed a connection that was
    // usually already gone by the time it was tappable, and failed the exact same way
    // on every retry since nothing ever reconnected. BLE has no such gap: `bleConnected`
    // is kept accurate even when the watch drops the link on its own (the
    // onBleDisconnected listener below flips it back to false the moment that happens -
    // see its own comment for the real bug this fixed, 2026-08-21), so only reconnect
    // for the USB path.
    const usingBle = bleConnectedRef.current;
    try {
      if (!usingBle) {
        await ambitConnect();
      }
      try {
        await setDateTime();
        setTimeSyncMsg(t.homeTimeSyncOk);
      } finally {
        if (!usingBle) {
          await ambitDisconnect().catch(() => {});
        }
      }
    } catch (e: any) {
      setTimeSyncMsg(t.homeTimeSyncFailed(e?.message ?? String(e)));
    } finally {
      setTimeSyncBusy(false);
    }
  }, [t]);

  // ── Connecting flow (v2.3.2 beta) ────────────────────────────────────────
  const [phase, setPhase] = useState<ConnPhase>('searching');
  const [deviceType, setDeviceType] = useState<AttachedDeviceType>('none');
  const [connectError, setConnectError] = useState<string | undefined>();
  const [waitingSeconds, setWaitingSeconds] = useState<number | null>(null);
  const [ambitInfo, setAmbitInfo] = useState<AmbitDeviceInfo | null>(null);
  // Multi-watch switcher (2026-08-16): every cabled Suunto (USB) and every paired one (BLE),
  // plus which one is active. The picker shows when there's more than one to choose between,
  // across both transports (e.g. 2 paired, or 2 paired + 1 cabled).
  const [attachedDevices, setAttachedDevices] = useState<AmbitUsbDevice[]>([]);
  const [bondedWatches, setBondedWatches] = useState<BondedWatch[]>([]);
  const [selectedWatch, setSelectedWatch] = useState<string | null>(null);   // USB path of active cabled watch
  const [connectedBleAddress, setConnectedBleAddress] = useState<string | null>(null); // MAC of active BLE watch
  const [garminInfo, setGarminInfo] = useState<GarminConnectResult | null>(null);

  // Locally-synced activities, for the desktop-parity "This year" + "Last Activity" cards
  // (HomePage.qml). Read from the same local DB the Activities/Totals screens use - no watch
  // round-trip, works disconnected. Refreshed on focus so a fresh sync shows up here too.
  const [activities, setActivities] = useState<ActivityRecord[]>([]);
  useFocusEffect(useCallback(() => {
    getAllActivities().then(setActivities).catch(() => {});
  }, []));

  // Gear maintenance summary — surface due/soon service reminders where the user actually looks.
  const [gearAlerts, setGearAlerts] = useState<{ due: number; soon: number }>({ due: 0, soon: 0 });
  useFocusEffect(useCallback(() => {
    getGearAlerts().then(a => setGearAlerts({ due: a.due.length, soon: a.soon.length })).catch(() => {});
  }, []));

  // "This year" = the most recent year that has data (matches desktop/TotalsScreen: a January
  // visit still shows last year's real numbers rather than zeros).
  const thisYear = useMemo(() => {
    let year = -1;
    for (const a of activities) {
      const y = new Date(a.date).getFullYear();
      if (!isNaN(y) && y > year) year = y;
    }
    if (year < 0) return null;
    let meters = 0, seconds = 0, count = 0;
    for (const a of activities) {
      if (new Date(a.date).getFullYear() !== year) continue;
      meters += a.distance_m || 0;
      seconds += a.duration_s || 0;
      count += 1;
    }
    const facts = distanceLines(meters);
    return { year, meters, seconds, count, teaser: facts.length > 0 ? facts[0] : '' };
  }, [activities]);

  // Newest activity, for the "Last Activity" card. getAllActivities() is already sorted by
  // date DESC (db.ts), so the first row is the most recent.
  const lastActivity = activities.length > 0 ? activities[0] : null;

  // Cabled + paired watches merged into the switcher's unified shape (see SwitcherWatch).
  const allWatches: SwitcherWatch[] = [
    ...attachedDevices.map(d => ({
      key: `usb:${d.deviceName}`, name: d.name, transport: 'usb' as const, usbDeviceName: d.deviceName,
    })),
    ...bondedWatches.map(b => ({
      key: `ble:${b.address}`, name: b.name, transport: 'ble' as const, bleAddress: b.address,
    })),
  ];
  // Re-read both lists (cheap; no watch round-trip) so the picker reflects what's plugged in
  // and what's paired. Called on search + on connect. listBondedWatches degrades to [] when the
  // native method or BLUETOOTH_CONNECT permission is missing, so this never throws.
  const refreshWatchLists = useCallback(() => {
    listDevices().then(setAttachedDevices).catch(() => {});
    listBondedWatches().then(setBondedWatches).catch(() => {});
  }, []);
  // Kailash only - visited cities/countries, travel stats, and the real activity-mode
  // logbook, all fetched once at connect time (see connectFlow's own 'ambit' branch below).
  const [kailashHistory, setKailashHistory] = useState<KailashHistory | null>(null);
  // Kailash only - the ephemeral GPS track (DeviceLog 0x53) fetched over the live BLE link
  // at connect time; null when the store was empty (already drained by the 7R app). Held so
  // the "export track" action below can write it without a second read. See handleBleConnect.
  const [kailashTrack, setKailashTrack] = useState<KailashDeviceLog | null>(null);
  const [kailashExportBusy, setKailashExportBusy] = useState(false);
  // Distinguishes a BLE connect attempt from connectFlow('ambit')'s USB one for
  // the "connecting…" message only — both land on the same deviceType==='ambit'.
  const [bleAttempt, setBleAttempt] = useState(false);
  // True once connected over BLE. Gates two things off the USB-only machinery:
  // (1) the connected-state watchdog below must NOT poll detectAttachedDeviceType()
  //     (USB-only — it returns 'none' for a BLE link and would evict us back to the
  //     no-device screen, which is exactly what happened before this flag existed);
  // (2) sync must use the BLE device provider (no USB connect()), see handleSync.
  const [bleConnected, setBleConnected] = useState(false);

  // Refs mirroring the state above — the search-poll interval and the
  // attach-event listener are both set up once and must always see the
  // latest phase/deviceType, not whatever was captured when they started.
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const deviceTypeRef = useRef(deviceType);
  deviceTypeRef.current = deviceType;
  const bleConnectedRef = useRef(bleConnected);
  bleConnectedRef.current = bleConnected;

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function stopSearchTimers() {
    if (pollTimerRef.current) { clearInterval(pollTimerRef.current); pollTimerRef.current = null; }
    if (timeoutTimerRef.current) { clearTimeout(timeoutTimerRef.current); timeoutTimerRef.current = null; }
  }

  async function connectFlow(type: 'ambit' | 'garmin') {
    setDeviceType(type);
    setBleAttempt(false);
    setBleConnected(false);          // this is a USB connect path
    bleConnectedRef.current = false;
    setBleTransportActive(false);    // USB path — connect()/disconnect() hit USB
    setConnectError(undefined);
    setPhase('connecting');

    if (type === 'ambit') {
      try {
        const info = await ambitConnect();
        let devInfo: AmbitDeviceInfo | null = null;
        try { devInfo = await getDeviceInfo(); } catch { /* non-fatal — hide the info block below */ }
        // Real, 2026-08-08 ("resumind: 7r button, last city visit... if we could import
        // this data which is on the watch and read it to our app would be awesome").
        // Fetched here, still connected - readDeviceHistoryRaw() needs the same open link
        // getDeviceInfo() just used, not a second connect(). Non-fatal like devInfo above:
        // a failed history read shouldn't block showing the rest of Home.
        if (isKailash(devInfo)) {
          try {
            const b64 = await readDeviceHistoryRaw();
            setKailashHistory(decodeDeviceHistory(b64));
          } catch { setKailashHistory(null); }
        } else {
          setKailashHistory(null);
        }
        await ambitDisconnect().catch(() => {});
        setAmbitInfo(devInfo ?? {
          name: info.name, model: '', serial: '', fwVersion: '', hwVersion: '', battery: -1,
        });
        // Refresh cabled + paired watches so the switcher shows every one you can pick.
        refreshWatchLists();
        setPhase('connected');
        handleSyncRef.current(); // preserve existing auto-sync-on-connect behavior
      } catch (e: any) {
        setConnectError(e?.message ?? t.unknownError);
        setPhase('connect-error');
      }
      return;
    }

    // Garmin — Garmin.connect() already retries internally for up to ~45s
    // while the mass-storage volume finishes mounting (see GarminModule.kt);
    // onMountWaiting surfaces that wait here instead of a silent hang.
    const unsubscribe = Garmin.onMountWaiting(e => setWaitingSeconds(e.secondsLeft));
    try {
      const result = await Garmin.connect();
      setGarminInfo(result);
      setPhase('connected');
      handleGarminSyncRef.current(result); // mirror Ambit's auto-sync-on-connect behavior
    } catch (e: any) {
      setConnectError(`${e?.code ?? ''} ${e?.message ?? t.unknownError}`.trim());
      setPhase('connect-error');
    } finally {
      unsubscribe();
      setWaitingSeconds(null);
    }
  }
  const connectFlowRef = useRef(connectFlow);
  connectFlowRef.current = connectFlow;

  // Multi-watch switcher (2026-08-16): connect the whole dashboard (device info, history, sync)
  // to the picked watch, over whichever transport it lives on. A cabled watch reconnects
  // immediately; a paired (BLE) watch goes through the same scan-and-connect the Bluetooth
  // button uses, pinned to that watch, so the user just triggers "Sync now"/"Pair Mobile App"
  // on it. No-op if the watch is already the active one.
  async function handleSelectWatch(watch: SwitcherWatch) {
    persistSelection(watch); // remember the choice for next launch
    if (watch.transport === 'usb') {
      if (selectedWatch === watch.usbDeviceName && !bleConnectedRef.current) return;
      setSelectedWatch(watch.usbDeviceName ?? null);
      setConnectedBleAddress(null);
      await selectDevice(watch.usbDeviceName ?? null).catch(() => {});
      await connectFlowRef.current('ambit');
    } else {
      if (connectedBleAddress === watch.bleAddress) return;
      setSelectedWatch(null);
      await handleBleConnectRef.current(watch.bleAddress);
    }
  }

  // BLE connect (2026-08-08) — there was previously no way to reach a BLE
  // pairing flow at all from Home: startSearching()/detectAttachedDeviceType()
  // only ever look for a USB attach event, and the BLE send/export buttons
  // already sitting in RouteScreen.tsx are unreachable until phase==='connected',
  // which only USB/Garmin detection could ever produce. This mirrors
  // connectFlow('ambit')'s post-connect half (same getDeviceInfo() call, same
  // phase==='connected'/deviceType==='ambit' target state) so the existing
  // action tiles — including Routes, which is where the real BLE send/export
  // UI lives — become reachable the same way a cable connection already does.
  // Ambit3/Traverse only; unlike Garmin/Ambit-over-USB this needs the user to
  // trigger the watch's own menu action first, right before scanning, since
  // its BLE advertising window is short (same reasoning as RouteScreen.tsx's
  // waitForSyncNowTap).
  // `targetAddress` (multi-watch switcher, 2026-08-16): when the user picked a specific paired
  // watch, pin the scan to that one so another paired watch soliciting nearby can't be grabbed
  // instead. Omitted (generic Bluetooth button) → connect to the first compatible watch found.
  async function handleBleConnect(targetAddress?: string) {
    // Straight to scanning — no confirmation dialog. The scan already waits ~15 s
    // (SCAN_TIMEOUT_MS in AmbitBleModule.kt), which is the watch's advertising
    // window, so the user just triggers "Pair Mobile App"/"Sync now" on the watch
    // while "Connecting via Bluetooth…" is showing. Removing the extra tap loses
    // no function (the old t.homeBleReadyMsg guidance now lives on that screen).
    stopSearchTimers();
    setDeviceType('ambit');
    setBleAttempt(true);
    setConnectError(undefined);
    setPhase('connecting');
    try {
      const connectedAddr = await (targetAddress ? bleScanAndConnectTo(targetAddress) : bleScanAndConnect());
      let devInfo: AmbitDeviceInfo | null = null;
      try { devInfo = await getDeviceInfo(); } catch { /* non-fatal — hide the info block below */ }
      setAmbitInfo(devInfo);
      // Kailash (2026-08-09, KAILASH-BLE-FINDINGS.md Finding 7): while the BLE link is
      // live, read the persistent activity summaries (DeviceHistory 0x67 → the on-screen
      // panel) and the EPHEMERAL GPS sample store (DeviceLog 0x53 → an exportable track).
      // 0x53 only has real samples over an active BLE session and before the 7R app drains
      // it, so this read must happen here, on the live link. Both non-fatal.
      if (isKailash(devInfo)) {
        try {
          setKailashHistory(decodeDeviceHistory(await readDeviceHistoryRaw()));
        } catch { setKailashHistory(null); }
        try {
          const log = decodeDeviceLog(await readDeviceLogRaw());
          setKailashTrack(log && realTrackPoints(log).length > 0 ? log : null);
        } catch { setKailashTrack(null); }
      } else {
        setKailashHistory(null);
        setKailashTrack(null);
      }
      setBleConnected(true);
      bleConnectedRef.current = true;
      setBleTransportActive(true);   // route all connect()/disconnect() through BLE
      setConnectedBleAddress(targetAddress ?? connectedAddr ?? null); // highlights the active BLE watch in the picker
      setSelectedWatch(null);
      refreshWatchLists();
      setPhase('connected');
      // Auto-sync on BLE connect (André, 2026-08-17). The Ambit3's Bluetooth link is watch-
      // driven and short-lived - it comes up when the user triggers "Sync now"/"Pair Mobile
      // App" on the watch and can close again within seconds. Waiting for the user to then tap
      // Sync in the app often missed that window ("activities didn't sync"), so we spend the
      // freshly-open link immediately, the same convenience USB attach already has. handleSync
      // is transport-aware (uses the BLE provider here), so this reads over the live link.
      handleSyncRef.current();
    } catch (e: any) {
      setBleConnected(false);
      bleConnectedRef.current = false;
      setBleTransportActive(false);
      setConnectedBleAddress(null);
      setConnectError(e?.message ?? t.unknownError);
      setPhase('connect-error');
    }
  }
  const handleBleConnectRef = useRef(handleBleConnect);
  handleBleConnectRef.current = handleBleConnect;

  // Real bug, found live 2026-08-21 (André: "the watch kinda don't reconnect after a
  // while"). This screen used to assume a BLE link, once up, "stays up until explicitly
  // closed" (see the old comment near bleConnectedRef's other uses) — but the watch's own
  // BLE session really is short-lived and watch-driven (ambit_app_ble_workflow_reliability
  // memory), so it drops on its own after a while with nothing here ever finding out. The
  // native side (AmbitBleModule.kt) reconnects fine when re-triggered — this was purely a
  // stale-UI-state bug: `bleConnected` never flipped back to false, so the app kept
  // claiming "Connected" long after the real link was gone, with no visible way to retry.
  // Mirrors the existing connect-error cleanup (handleBleConnect's own catch block) rather
  // than inventing a new UI state.
  useEffect(() => {
    return onBleDisconnected(() => {
      if (!bleConnectedRef.current) return;   // already knew (e.g. mid handleBleConnect retry)
      setBleConnected(false);
      bleConnectedRef.current = false;
      setBleTransportActive(false);
      setConnectedBleAddress(null);
      setConnectError(t.homeBleDisconnectedError);
      setPhase('connect-error');
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Writes the Kailash track fetched at connect time (kailashTrack) to Downloads as GPX.
  // No watch round-trip here — the samples were already read over the live link in
  // handleBleConnect, because DeviceLog (0x53) is ephemeral (see KailashDeviceLogReader.ts).
  async function handleExportKailashTrack() {
    if (!kailashTrack || kailashExportBusy) return;
    setKailashExportBusy(true);
    try {
      const pts = realTrackPoints(kailashTrack);
      const gpx = deviceLogToGpx(kailashTrack, `Kailash ${pts[0]?.time ?? ''}`.trim());
      if (!gpx) { Alert.alert(t.homeKailashTrackTitle, t.homeKailashExportEmpty); return; }
      const stamp = (pts[0]?.time ?? new Date().toISOString()).replace(/[:.]/g, '-');
      const fileName = `kailash_${stamp}.gpx`;
      const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
      await RNFS.writeFile(path, gpx, 'utf8');
      await saveToDownloads(path, fileName, 'application/gpx+xml');
      Alert.alert(t.homeKailashTrackTitle, t.homeKailashExportDone.replace('%d', String(pts.length)));
    } catch (e: any) {
      Alert.alert(t.homeKailashTrackTitle, e?.message ?? t.unknownError);
    } finally {
      setKailashExportBusy(false);
    }
  }

  function startSearching() {
    stopSearchTimers();
    // Testing mode: don't hunt for a real watch - the demo effect below already presents the
    // chosen sample device as connected.
    if (demoEnabledRef.current) return;
    setPhase('searching');
    setConnectError(undefined);
    // Populate the picker up front so paired (BLE) watches are offerable on the no-device
    // screen even before anything is cabled — a BLE-only user has nothing to auto-connect.
    refreshWatchLists();

    const poll = async () => {
      // Hold the first auto-connect until the persisted watch choice has been restored (below),
      // so we connect straight to the saved watch instead of first-found then switching.
      if (!restoredRef.current) return;
      const type = await detectAttachedDeviceType().catch(() => 'none' as const);
      if (type !== 'none') {
        stopSearchTimers();
        connectFlowRef.current(type);
      }
    };
    poll(); // immediate check, don't wait for the first interval tick
    pollTimerRef.current = setInterval(poll, SEARCH_POLL_MS);
    timeoutTimerRef.current = setTimeout(() => {
      stopSearchTimers();
      setPhase(p => (p === 'searching' ? 'timeout' : p));
    }, SEARCH_TIMEOUT_MS);
  }
  const startSearchingRef = useRef(startSearching);
  startSearchingRef.current = startSearching;

  // Testing mode (ported from desktop): when on, short-circuit the whole connect flow and
  // present the chosen sample device as connected, so the app can be explored without a watch.
  // When turned off, resume the normal USB/BLE search.
  const demoEnabledRef = useRef(demo.enabled);
  demoEnabledRef.current = demo.enabled;
  useEffect(() => {
    if (demo.enabled) {
      stopSearchTimers();
      setBleConnected(false);
      bleConnectedRef.current = false;
      setBleTransportActive(false);
      setGarminInfo(null);
      setDeviceType('ambit');
      setAmbitInfo(demo.device);
      setKailashHistory(null);
      setKailashTrack(null);
      setConnectError(undefined);
      setPhase('connected');
    } else if (phaseRef.current === 'connected' && deviceTypeRef.current === 'ambit') {
      // Was showing the demo device - go back to looking for a real one.
      setAmbitInfo(null);
      startSearchingRef.current();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo.enabled, demo.variant]);

  // Restore the persisted watch choice once, before the first auto-connect (poll() waits on
  // restoredRef). For USB we prime the native selection so the very first connect targets the
  // saved watch; for BLE we only remember it (auto-connecting BLE on launch would drain the
  // watch's short advertising window), so a paired watch is just highlighted, tap to connect.
  const restoredRef = useRef(false);
  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(SELECTED_WATCH_KEY);
        if (raw) {
          const saved: SavedWatch = JSON.parse(raw);
          if (saved.transport === 'usb') {
            const devs = await listDevices().catch(() => [] as AmbitUsbDevice[]);
            const match = devs.find(d => d.productId === saved.productId);
            if (match) {
              await selectDevice(match.deviceName).catch(() => {});
              setSelectedWatch(match.deviceName);
            }
          }
        }
      } catch { /* corrupt/empty selection - fall back to first-found */ }
      restoredRef.current = true;
      startSearchingRef.current(); // now connect, honoring any restored selection
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist the switcher choice so it survives an app restart. Keyed by the stable productId /
  // BLE address, not the volatile USB path.
  const persistSelection = useCallback((watch: SwitcherWatch) => {
    let saved: SavedWatch | null = null;
    if (watch.transport === 'usb') {
      const pid = attachedDevices.find(d => d.deviceName === watch.usbDeviceName)?.productId;
      if (pid != null) saved = { transport: 'usb', productId: pid };
    } else if (watch.bleAddress) {
      saved = { transport: 'ble', bleAddress: watch.bleAddress };
    }
    if (saved) AsyncStorage.setItem(SELECTED_WATCH_KEY, JSON.stringify(saved)).catch(() => {});
  }, [attachedDevices]);

  // On every focus: if we're already showing a connected device, just check
  // it's still there rather than restarting the whole search/connect dance
  // (that would re-trigger auto-sync every time the user comes back from
  // another screen). Otherwise (first mount, or the user hadn't connected
  // yet), run the normal search.
  useFocusEffect(useCallback(() => {
    // Testing mode owns the connection state - don't run the real USB re-check/search.
    if (demoEnabledRef.current) return () => {};
    // A BLE connection is invisible to detectAttachedDeviceType() (USB-only), so
    // never run the USB re-check while BLE-connected — it would falsely see
    // 'none' and bounce back to the search screen.
    if (bleConnectedRef.current) return () => {};
    if (phaseRef.current === 'connected') {
      detectAttachedDeviceType().then(type => {
        if (type === 'none' || type !== deviceTypeRef.current) startSearchingRef.current();
      }).catch(() => {});
    } else {
      startSearchingRef.current();
    }
    return () => stopSearchTimers();
  }, []));

  // Live refresh while sitting on the connected dashboard: rather than only
  // re-checking on focus (which misses "watch unplugged while I was looking
  // at this screen"), poll periodically. If nothing's attached any more,
  // fall back to the no-device screen; if a *different* device answers
  // (e.g. the Ambit was swapped for the eTrex), jump straight into that
  // device's connect flow instead of bouncing through an extra search step.
  useEffect(() => {
    if (phase !== 'connected') return;
    const iv = setInterval(async () => {
      // BLE links aren't visible to the USB attach check — skip the watchdog
      // entirely while BLE-connected so it can't evict us to the no-device screen.
      if (bleConnectedRef.current) return;
      const type = await detectAttachedDeviceType().catch(() => 'none' as const);
      if (type === deviceTypeRef.current) return;
      if (type === 'none') startSearchingRef.current();
      else connectFlowRef.current(type);
    }, CONNECTED_POLL_MS);
    return () => clearInterval(iv);
  }, [phase]);

  async function handleSync() {
    if (isBusy) return;
    setLastActive('sync');
    try {
      // Real, 2026-08-08: Kailash has no ExerciseLog for the default provider's native
      // walker to find (see KailashDeviceProvider.ts's own header comment) - its one real
      // activity source is the passive TrackLog region, decoded to GPX in TS and routed
      // through this exact same sync pipeline (writeGpxFile/markActivitySynced) so no new
      // "synced activities" list/UI is needed for it.
      // Over BLE the connection is already up (GATT server + handshake); use the
      // BLE provider so sync doesn't try a USB connect() ("check cable" error).
      // The native getLogs itself is transport-agnostic (operates on the shared
      // device), so activity read works the same either way once connected.
      const provider = bleConnectedRef.current
        ? ambitBleDeviceProvider
        : (isKailash(ambitInfo) ? kailashDeviceProvider : undefined);
      await runSync(setSync, provider);
      // Recalculate the watch's activity class from the athlete's latest intervals.icu
      // training on every sync (André, 2026-08-18: "recalculate activity level on each sync
      // usb and bluetooth"). No-op if intervals.icu isn't connected, or on Ambit1/2/Kailash;
      // writes only when the class changed. Fire-and-forget - must never break activity sync.
      refreshActivityClassOnWatch().catch(() => {});
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
      setSync(s => ({ ...s, phase: 'error' }));
    }
  }

  // Auto-sync when the watch is plugged in — see AndroidManifest.xml's
  // USB_DEVICE_ATTACHED intent-filter + MainActivity.onNewIntent(). A ref
  // keeps this always pointing at the latest handleSync/isBusy rather than
  // whatever was captured when the effect first ran, since the listener
  // below is subscribed once on mount, not re-subscribed on every render.
  const handleSyncRef = useRef(handleSync);
  handleSyncRef.current = handleSync;

  // v2.3 beta: USB_DEVICE_ATTACHED now also fires for Garmin devices
  // (device_filter.xml) — it no longer implies "the Ambit was plugged in" by
  // itself. A live attach event jumps straight into the connect flow (no
  // need to wait out the search poll — we already know something's there).
  useEffect(() => {
    function onAttach() {
      stopSearchTimers();
      // In Testing mode, ignore a real watch being plugged in - the sample device stays.
      if (demoEnabledRef.current) return;
      // Defer to an active Bluetooth session (2026-08-16): plugging a watch in shouldn't yank
      // the dashboard off the BLE watch you're already using. Just surface the newly-cabled
      // watch in the switcher — tap its pill to switch. Auto-connect-on-plug still works
      // normally when nothing is connected.
      if (bleConnectedRef.current) { refreshWatchLists(); return; }
      detectAttachedDeviceType().then(type => {
        if (type !== 'none') connectFlowRef.current(type);
      }).catch(() => {});
    }
    wasLaunchedViaUsbAttach().then(was => { if (was) onAttach(); }).catch(() => {});
    return onUsbAttached(onAttach);
  }, []);

  async function handleOrbital() {
    if (isBusy) return;
    setLastActive('orbital');
    try {
      // Over BLE the GATT session is already open - hand updateOrbitalData the BLE provider so
      // it writes the orbit file on that live link instead of attempting a USB connect() (which
      // fails with a "check cable" error). Same USB-vs-BLE branch handleSync uses.
      const provider = bleConnectedRef.current ? ambitBleDeviceProvider : undefined;
      await updateOrbitalData(setOrbital, provider);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
      setOrbital({ phase: 'error', error: e?.message });
    }
  }

  async function handleGarminSync(result?: GarminConnectResult) {
    const target = result ?? garminInfo;
    if (isBusy || !target) return;
    try {
      await syncGarminActivities(target, setGarminSync);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
      setGarminSync(s => ({ ...s, phase: 'error', error: e?.message }));
    }
  }
  const handleGarminSyncRef = useRef(handleGarminSync);
  handleGarminSyncRef.current = handleGarminSync;

  const syncLabel = syncBusy ? syncPhaseLabel(sync.phase)
    : sync.phase === 'done' ? t.synced
    : sync.phase === 'error' ? t.retry
    : t.homeActivitiesBtn;

  const orbitalLabel = orbitalBusy ? orbitalPhaseLabel(orbital.phase)
    : orbital.phase === 'done' ? t.gpsDone
    : orbital.phase === 'error' ? t.retry
    : t.gpsUpdate;

  const garminSyncLabel = garminSyncBusy
    ? (garminSync.phase === 'reading' ? t.conn : t.read)
    : garminSync.phase === 'done' ? t.synced
    : garminSync.phase === 'error' ? t.retry
    : t.homeSyncActivitiesBtn;

  // Which of the two Ambit-only operations last ran drives the status line
  // below — same derivation as before, just expressed as a tone (muted vs.
  // the one alert color) instead of a hardcoded hex per phase.
  const statusPhase = lastActive === 'orbital' ? orbital.phase : sync.phase;
  const statusText = lastActive === 'orbital' ? orbitalStatusMessage(orbital) : syncStatusMessage(sync);
  const statusTone: 'muted' | 'alert' = statusPhase === 'error' ? 'alert' : 'muted';

  // ── Device area render (searching/connecting/timeout/error states) ───────
  // searching / timeout are now rendered INSIDE the unified card page below (desktop parity:
  // Home is always the card dashboard now (desktop parity) - EVERY non-connected state
  // (searching / timeout / connecting / connect-error) renders the same hero card on the
  // page below, never a separate full-screen splash. `connected` gates the parts that
  // genuinely need a live watch.
  const connected = phase === 'connected';
  const connectingMsg = deviceType === 'garmin' && waitingSeconds !== null
    ? t.garminWaitingForMount(waitingSeconds)
    : deviceType === 'ambit' ? (bleAttempt ? t.homeConnectingBle : t.homeConnectingAmbit) : t.connecting;

  // v3.0 UI port - the persistent nav shell (NavRail.qml's real pattern: a fixed item list,
  // visibility gated by connected-device type, selection by string id). Garmin routes to
  // GarminRoute/GarminPoi (its own params-carrying screens, see App.tsx's RootStackParamList
  // comment on why those are separate from Route/Poi) instead of Ambit's Route/Poi. Kailash
  // excludes Routes/POIs/Sport Modes, same real reasoning as the ActionTile grid below already
  // encoded (Kailash's own memory map has no route-following feature or CustomModes region).
  // Device-specific destinations only appear once a watch is connected (nothing to act on
  // otherwise) - Home/Activities/Settings are always reachable.
  const navItems: NavShellItem[] = [
    { id: 'home', label: t.homeNavHome, icon: 'mountain', onPress: () => {} },
    { id: 'activities', label: t.viewActivities, icon: 'list', onPress: () => navigation.navigate('LogList'), group: 'training' },
    // Totals + Calendar (2026-08-29, desktop parity): activity-analytics views, always reachable
    // (read the local activity DB, no watch needed) - like the desktop nav.
    { id: 'totals', label: 'Totals', icon: 'chart', onPress: () => navigation.navigate('Totals') },
    { id: 'calendar', label: 'Calendar', icon: 'calendar', onPress: () => navigation.navigate('Calendar') },
    ...(!connected ? [] : deviceType === 'garmin'
      ? [
          { id: 'routes', label: t.homeRoutesBtn, icon: 'route' as const, onPress: () => garminInfo && navigation.navigate('GarminRoute', { info: garminInfo }), group: 'watch' as const },
          { id: 'pois', label: t.homePoisBtn, icon: 'poi' as const, onPress: () => garminInfo && navigation.navigate('GarminPoi', { info: garminInfo }), group: 'watch' as const },
        ]
      : !isKailash(ambitInfo)
        ? [
            { id: 'routes', label: t.homeRoutesBtn, icon: 'route' as const, onPress: () => navigation.navigate('Route'), group: 'watch' as const },
            { id: 'pois', label: t.homePoisBtn, icon: 'poi' as const, onPress: () => navigation.navigate('Poi'), group: 'watch' as const },
          ]
        : []),
    ...(connected ? [{ id: 'backup', label: t.backupButton, icon: 'backup' as const, onPress: () => navigation.navigate('Backup', { deviceModel: ambitInfo?.model }), group: 'watch' as const }] : []),
    ...(connected && deviceType === 'ambit' && !isKailash(ambitInfo)
      ? [{ id: 'sportModes', label: t.sportModesButton, icon: 'watch' as const, onPress: () => navigation.navigate('SportModes', { overBle: bleConnectedRef.current, variant: ambitInfo?.model }), group: 'watch' as const }]
      : []),
    // Firmware (2026-08-29, desktop parity): USB-only, HIDDEN over Bluetooth - a bad flash can
    // brick the watch and flashing is cable/USB-OTG only (same brick-safety rule as the desktop's
    // NavRail). Suunto family (incl. Kailash over USB), not Garmin. Also still in Settings.
    ...(connected && deviceType === 'ambit' && !bleConnected
      ? [{ id: 'firmware', label: 'Firmware', icon: 'sync' as const, onPress: () => navigation.navigate('Firmware') }]
      : []),
    // Experimental menu items - appear only when their toggle is on (Settings > Experimental
    // features). Intervals rides the Suunto App-Zone/CustomModes mechanism, so it's Ambit-only
    // and needs a connected watch, same gating as the desktop's NavRail. Smart Sensor is a
    // standalone BLE HR belt, so it shows whenever enabled. André, 2026-08-17.
    // Apps (2026-08-29, desktop parity): one entry -> a launcher with the Workout Builder +
    // the Suunto app catalog (AppsScreen), replacing the old separate 'Intervals' item. Same
    // App-Zone gating as before: Ambit3-family only, connected, not Kailash/Traverse/Ambit1-2.
    ...(expFeatures.intervals && connected && deviceType === 'ambit' && !isKailash(ambitInfo) && !isTraverse(ambitInfo) && !isAmbit12(ambitInfo)
      ? [{ id: 'apps', label: 'Apps', icon: 'apps' as const, onPress: () => navigation.navigate('Apps') }]
      : []),
    ...(expFeatures.smartSensor
      ? [{ id: 'smartSensor', label: t.experimentalSmartSensor, icon: 'link' as const, onPress: () => navigation.navigate('SmartSensor'), group: 'adv' as const }]
      : []),
    // Workout Calendar - same Apps/CustomModes mechanism as Intervals (guided-workout binaries
    // in the WORKOUT menu), so it's gated identically: Ambit-only, connected, not Kailash/Traverse.
    ...(expFeatures.workoutCalendar && connected && deviceType === 'ambit' && !isKailash(ambitInfo) && !isTraverse(ambitInfo) && !isAmbit12(ambitInfo)
      ? [{ id: 'workoutCalendar', label: t.experimentalWorkoutCalendar, icon: 'chart' as const, onPress: () => navigation.navigate('WorkoutCalendar'), group: 'adv' as const }]
      : []),
    // Gear tracker (v3): derived from the local gear DB + intervals.icu, so it's always
    // reachable — no connected watch needed, not gated behind Experimental.
    { id: 'gear', label: t.gearButton, icon: 'cycling' as const, onPress: () => navigation.navigate('Gear'), group: 'training' as const },
    // Weight/Health (2026-08-26, desktop parity): both read intervals.icu's wellness feed, so
    // like Gear they need no connected watch and sit unconditionally in this list.
    { id: 'coach', label: 'Coach', icon: 'coach' as const, onPress: () => navigation.navigate('Coach'), group: 'training' as const },
    // Ember: off by default, shown once the user opts in via the open toggle in Settings
    // (the 10-tap easter egg was retired 2026-08-29, matching the desktop). `emberUnlocked` is
    // that persisted opt-in flag - the storage key is unchanged, so an already-on install keeps it.
    ...(emberUnlocked
      ? [{ id: 'ember', label: 'Ember', icon: 'ember' as const, onPress: () => navigation.navigate('Ember'), group: 'training' as const }]
      : []),
    { id: 'health', label: 'Health', icon: 'health' as const, onPress: () => navigation.navigate('Health'), group: 'training' as const },
    { id: 'weight', label: 'Weight', icon: 'weight' as const, onPress: () => navigation.navigate('Weight'), group: 'training' as const },
    { id: 'settings', label: t.settingsTitle, icon: 'settings', onPress: () => navigation.navigate('Settings') },
  ];

  return (
    <NavShell items={navItems} selectedId="home">
    <ScrollView
      style={styles.scroll}
      // Home is the only screen with headerShown:false (App.tsx), so nothing else reserves
      // the top safe area for it. On a notched/Dynamic-Island iPhone (e.g. the 13 mini,
      // ~50pt top inset) the "Sommet" header would otherwise sit under the notch. Floor at
      // the style's original 56pt so Android (insets.top ≈ status-bar height) is unchanged.
      // 2026-08-24, André — iPhone layout pass.
      contentContainerStyle={[styles.scrollContent, { paddingTop: Math.max(56, insets.top + 12) }]}
      showsVerticalScrollIndicator={false}
    >

      {/* ── Header ── */}
      <View style={styles.header}>
        <Text style={styles.appName}>Sommet</Text>
        <Badge label={`v${APP_VERSION}`} />
      </View>

      {/* Gear maintenance summary — only when something needs attention; taps through to Gear. */}
      {(gearAlerts.due > 0 || gearAlerts.soon > 0) && (
        <TouchableOpacity
          activeOpacity={0.8}
          onPress={() => navigation.navigate('Gear')}
          style={{
            flexDirection: 'row', alignItems: 'center', gap: 8, marginHorizontal: 16, marginBottom: 8,
            paddingVertical: 10, paddingHorizontal: 14, borderRadius: 10, borderWidth: 1,
            backgroundColor: (gearAlerts.due > 0 ? theme.error : theme.warning) + '14',
            borderColor: (gearAlerts.due > 0 ? theme.error : theme.warning) + '80',
          }}
        >
          <Icon name="warning" size={16} color={gearAlerts.due > 0 ? theme.error : theme.warning} />
          <Text style={{ flex: 1, fontSize: 13, color: theme.text }}>
            {gearAlerts.due > 0 ? t.gearDueCount(gearAlerts.due) : t.gearSoonCount(gearAlerts.soon)}
          </Text>
          <Icon name="chevronRight" size={16} color={theme.mutedText} />
        </TouchableOpacity>
      )}
      {/* Real, 2026-08-09 ("the icon of the watch could be like 20% bigger while in
          vertical, on horizontal is ok") - portrait-only scale-up; roomy (landscape+wide)
          keeps the original 40. Not tied to `roomy` itself since a narrow landscape phone
          is neither roomy nor portrait and should also keep the unscaled size.
          Real, 2026-08-10 ("icon: please make it 10% bigger") - a real Garmin eTrex
          connected live revealed the etrex glyph reads a little small next to the Suunto
          watch glyph at the same nominal size - a further 10% bump, on top of the same
          portrait/landscape scaling above, Garmin-only. */}
      <Icon
        name={deviceType === 'garmin' ? 'etrex' : 'watch'}
        size={Math.round((winWidth < winHeight ? 48 : 40) * (deviceType === 'garmin' ? 1.1 : 1))}
        color={connected ? theme.text : theme.mutedText}
      />

      {/* ── Device info cards. Portrait: one centered column. Roomy/landscape: a
          side-by-side wrapping row (each card sized to share the width), so the space
          is used instead of stretching one card per line. ── */}
      <View style={[styles.cardStack, roomy && styles.cardStackRoomy]}>
      {/* ── Non-connected hero card. Desktop parity: EVERY non-connected state (searching,
          timeout/no-watch, actively connecting, connect error) lives in this one card on the
          dashboard, holding the right message + connect options, instead of a separate
          full-screen splash. `busy` = actively connecting or scanning (spinner, no buttons). ── */}
      {!connected && (() => {
        const busy = phase === 'connecting';
        const title = phase === 'searching' ? t.homeSearchingTitle
          : phase === 'connecting' ? connectingMsg
          : phase === 'connect-error' ? (connectError ?? t.homeNoDeviceTitle)
          : t.homeNoDeviceTitle;
        const sub = phase === 'searching' ? t.homeTagline
          : phase === 'connecting' ? (bleAttempt ? t.homeBleReadyMsg : '')
          : phase === 'connect-error' ? '' : t.homeNoDeviceSub;
        return (
          <Card style={[roomy ? styles.deviceCardRoomyFull : styles.deviceCardCol, styles.deviceCardInner]}>
            <Text style={[styles.deviceName, v3TextStyle]}>{title}</Text>
            {sub.length > 0 && <Text style={[styles.deviceSub, v3MutedStyle]}>{sub}</Text>}
            {(phase === 'searching' || busy) && (
              <View style={{ marginTop: 12 }}>
                <ActivityIndicator size="small" color={theme.text} />
              </View>
            )}
            {/* Connect options - hidden while actively connecting (nothing to do but wait).
                Multi-watch switcher: one direct-connect button per already-paired watch. */}
            {!busy && (
              <View style={styles.heroButtons}>
                {phase === 'timeout' && (
                  <Button label={t.homeConnectRetryBtn} onPress={startSearching} variant="text" grow={false} />
                )}
                {phase === 'connect-error' && (
                  <Button
                    label={t.homeConnectRetryBtn}
                    onPress={() => bleAttempt ? handleBleConnectRef.current() : connectFlowRef.current(deviceType === 'garmin' ? 'garmin' : 'ambit')}
                    variant="text"
                    grow={false}
                  />
                )}
                {bondedWatches.map(b => (
                  <Button
                    key={b.address}
                    label={t.homeBleConnectWatchBtn(watchPillName(b.name))}
                    onPress={() => handleBleConnectRef.current(b.address)}
                    variant="text"
                    grow={false}
                  />
                ))}
                <Button label={t.homeBleConnectBtn} onPress={() => handleBleConnectRef.current()} variant="text" grow={false} />
              </View>
            )}
          </Card>
        );
      })()}
      {deviceType === 'garmin' && garminInfo && (() => {
        const vol = garminInfo.volumes.find(v => v.hasGarminDeviceXml) ?? garminInfo.volumes[0];
        return (
          <Card style={[roomy ? styles.deviceCardRoomy : styles.deviceCardCol, styles.deviceCardInner]}>
            <Text style={[styles.deviceName, v3TextStyle]}>{vol?.model ?? t.garminUnknownModel}</Text>
            {!!vol?.firmwareVersion && (
              <Text style={[styles.deviceSub, v3MutedStyle]}>{t.garminFirmwareLabel} {vol.firmwareVersion}</Text>
            )}
            <View style={styles.deviceMetaRow}>
              <Text style={[styles.deviceSub, v3MutedStyle]}>
                {garminInfo.hasSdCard ? t.garminSdCardPresent : t.garminSdCardAbsent}
              </Text>
              <Chip icon="check" label={t.homeDeviceConnectedStatus} />
            </View>
            {/* Real, 2026-08-11 (André: "I added etrex manuals to the files, can you link
                it to the supported devices?") - same mechanism as the Ambit manual link
                below, family-matched (garminManualUrlFor()) since Garmin's own model text
                doesn't map 1:1 the way Suunto codenames do. */}
            <TouchableOpacity
              style={styles.timeSyncRow}
              onPress={() => Linking.openURL(garminManualUrlFor(vol?.model))}
            >
              <Icon name="info" size={14} color={theme.primary} />
              <Text style={[styles.timeSyncText, { color: theme.primary }]}>
                {t.homeManualLink}
              </Text>
            </TouchableOpacity>
          </Card>
        );
      })()}
      {deviceType === 'ambit' && ambitInfo && (
        <Card style={[roomy ? styles.deviceCardRoomy : styles.deviceCardCol, styles.deviceCardInner]}>
          <Text style={[styles.deviceName, v3TextStyle]}>{ambitInfo.name}</Text>
          {/* Multi-watch switcher: shown with more than one watch to choose between, across both
              transports (cabled USB + paired BLE). Tapping reconnects the whole dashboard to it —
              cabled watches immediately, paired ones over Bluetooth (desktop has the same picker). */}
          {/* Multi-watch switcher: every cabled + paired watch (shown when there's more than one
              to choose between), plus a "pair a new Bluetooth watch" action that's always
              available so you can add a BLE watch to the picker straight from here. */}
          <View style={styles.watchSwitcher}>
            {allWatches.length > 1 && allWatches.map(w => {
              const active = w.transport === 'usb'
                ? (!bleConnected && (selectedWatch ? w.usbDeviceName === selectedWatch : w.name === ambitInfo.name))
                : (bleConnected && connectedBleAddress === w.bleAddress);
              return (
                <TouchableOpacity
                  key={w.key}
                  onPress={() => handleSelectWatch(w)}
                  activeOpacity={0.75}
                  style={[
                    styles.watchChip,
                    { borderColor: theme.mutedText },
                    active && { backgroundColor: theme.primary, borderColor: theme.primary },
                  ]}
                >
                  <Icon
                    name={w.transport === 'ble' ? 'bluetooth' : 'link'}
                    size={12}
                    color={active ? theme.card : theme.mutedText}
                  />
                  <Text style={[styles.watchChipText, { color: active ? theme.card : theme.text }]}>
                    {watchPillName(w.name)}
                  </Text>
                </TouchableOpacity>
              );
            })}
            <TouchableOpacity
              key="__pair"
              onPress={() => handleBleConnectRef.current()}
              activeOpacity={0.75}
              style={[styles.watchChip, styles.watchChipAction, { borderColor: theme.mutedText }]}
            >
              <Icon name="bluetooth" size={12} color={theme.mutedText} />
              <Text style={[styles.watchChipText, { color: theme.mutedText }]}>
                {t.homePairWatchPill}
              </Text>
            </TouchableOpacity>
          </View>
          {!!(ambitInfo.fwVersion || ambitInfo.hwVersion) && (
            <Text style={[styles.deviceSub, v3MutedStyle]}>
              {ambitInfo.fwVersion ? `${t.garminFirmwareLabel} ${ambitInfo.fwVersion}` : ''}
              {ambitInfo.hwVersion ? `  ·  ${t.homeHwLabel} ${ambitInfo.hwVersion}` : ''}
            </Text>
          )}
          {/* Serial number - desktop's hero-card info grid shows it; parity, 2026-08-16. */}
          {!!ambitInfo.serial && (
            <Text style={[styles.deviceSub, v3MutedStyle]}>{t.homeSerialLabel} {ambitInfo.serial}</Text>
          )}
          <View style={styles.deviceMetaRow}>
            {ambitInfo.battery >= 0 && (
              <View style={styles.deviceBattery}>
                <Icon name="battery" size={15} color={theme.mutedText} />
                <Text style={[styles.deviceBatteryText, v3MutedStyle]}>{ambitInfo.battery}%</Text>
              </View>
            )}
            <Chip icon="check" label={t.homeDeviceConnectedStatus} />
            {/* Real, 2026-08-09 ("via usb should be auto detected... bluetooth yes it needs
                a button") - desktop's own hero card never needed this (USB-only), but Android
                genuinely has two transports now, so the connected card says which one is
                live. bleConnected is the same ref this screen already uses to gate the USB
                watchdog/sync-provider choice - not a new signal invented for this label. */}
            <Chip icon={bleConnected ? 'link' : 'check'} label={t.homeConnVia(bleConnected ? t.homeConnViaBle : t.homeConnViaUsb)} />
          </View>
          {/* Real, 2026-08-10 - was gated off for cable Kailash while that path was
              unconfirmed; a real cable capture (kailashsynctimefrom...) then showed it's
              the exact same 0x1201 mechanism as BLE, just without the second NextTime
              push - no gating needed, device_driver_ambit3.c's date_time_set() dispatches
              correctly on either transport now. */}
          {/* The three card actions - Sync time, GPS data (orbital), View manual - lined up on
              one centered horizontal row (André, 2026-08-16: "lining up will make it more
              beautiful", both orientations), wrapping to more lines only if they don't fit. */}
          <View style={styles.deviceLinksRow}>
            <TouchableOpacity
              style={styles.deviceLink}
              disabled={timeSyncBusy}
              onPress={handleSyncTime}
            >
              <Icon name="sync" size={14} color={theme.primary} />
              <Text style={[styles.timeSyncText, { color: theme.primary }]}>
                {timeSyncBusy ? t.connecting : (timeSyncMsg ?? t.homeSyncTimeButton)}
              </Text>
            </TouchableOpacity>
            {/* GPS orbital data (SGEE/A-GPS) - André, 2026-08-16 ("I don't see any gps"). It used
                to live only as a tile at the very bottom of this scroll, invisible on a phone;
                surfaced here in the device card next to Sync time, matching desktop's HomePage.qml
                which shows "GPS orbit" right in the hero card. Both are self-correcting update
                actions, so they sit together. Works over USB and BLE (see handleOrbital). */}
            <TouchableOpacity
              style={styles.deviceLink}
              disabled={isBusy}
              onPress={handleOrbital}
            >
              <Icon name="satellite" size={14} color={theme.primary} />
              <Text style={[styles.timeSyncText, { color: theme.primary }]}>
                {orbitalBusy ? orbitalPhaseLabel(orbital.phase)
                  : orbital.phase === 'done' ? t.gpsDoneMsg
                  : orbital.phase === 'error' ? t.retry
                  : t.gpsIdle}
              </Text>
            </TouchableOpacity>
            {/* Real, 2026-08-11 (André: "put the manual next to hardware") - opens the real
                Suunto user-guide PDF for whichever model is connected (manualUrlFor(),
                config/manuals.ts - same table as desktop's HomeViewModel.qml manualUrl) in
                the OS's own PDF viewer/browser, same Linking.openURL() mechanism as any other
                "open outside the app" action here. */}
            <TouchableOpacity
              style={styles.deviceLink}
              onPress={() => Linking.openURL(manualUrlFor(ambitInfo.model))}
            >
              <Icon name="info" size={14} color={theme.primary} />
              <Text style={[styles.timeSyncText, { color: theme.primary }]}>
                {t.homeManualLink}
              </Text>
            </TouchableOpacity>
          </View>
        </Card>
      )}

      {/* ── Kailash travel history - real, 2026-08-08 ("if we could import this data
          which is on the watch and read it to our app would be awesome"). Restyled onto
          the theme-redesign's own deviceCard pattern (2026-08-08 merge) - same as the
          ambitInfo card just above: deviceName for the title, deviceSub (repeated) for
          each muted detail line, rather than the pre-redesign deviceInfoBox styles this
          screen no longer defines. ── */}
      {deviceType === 'ambit' && isKailash(ambitInfo) && kailashHistory && (
        <Card style={[roomy ? styles.deviceCardRoomyFull : styles.deviceCardCol, styles.deviceCardInner]}>
          <Text style={[styles.deviceName, v3TextStyle]}>{t.homeKailashTravelTitle}</Text>
          <Text style={[styles.deviceSub, v3MutedStyle]}>
            {t.homeKailashCitiesLabel} {kailashHistory.citiesVisited}
            {'  ·  '}{t.homeKailashCountriesLabel} {kailashHistory.countriesVisited}
          </Text>
          {kailashHistory.hasLastKnownLocation && (
            <Text style={[styles.deviceSub, v3MutedStyle]}>
              {kailashHistory.lastKnownLatitude.toFixed(4)}, {kailashHistory.lastKnownLongitude.toFixed(4)}
              {kailashHistory.lastKnownCountry ? ` (${kailashHistory.lastKnownCountry})` : ''}
            </Text>
          )}
          <Text style={[styles.deviceSub, v3MutedStyle]}>
            {t.homeKailashTravelledLabel} {(kailashHistory.travelledDistanceMeters / 1000).toFixed(1)} km
            {'  ·  '}{t.homeKailashFurthestLabel} {(kailashHistory.furthestFromHomeMeters / 1000).toFixed(1)} km
          </Text>
          {kailashHistory.sessions.length > 0 && (
            <Text style={[styles.deviceSub, v3MutedStyle]}>
              {t.homeKailashLogbookLabel} {kailashHistory.sessions.length}
            </Text>
          )}
        </Card>
      )}

      {/* ── Kailash visited-places world map - real, 2026-08-10 ("desktop version has more
          functions that were not passed to the android version, like for example the map
          with locations, can you implement it please?"). Ports desktop HomePage.qml's own
          MapView(markers: KailashService.visitedPlaces) - multiMarker mode (TrackPreview.tsx's
          own header comment) so each visited place gets its own dot instead of a nonsense
          polyline connecting unrelated cities in array order. ── */}
      {deviceType === 'ambit' && isKailash(ambitInfo) && kailashHistory && kailashHistory.visitedPlaces.length > 0 && (
        <Card style={[roomy ? styles.deviceCardRoomyFull : styles.deviceCardCol, styles.deviceCardInner]}>
          <Text style={[styles.deviceName, v3TextStyle]}>
            {t.homeKailashPlacesTitle(kailashHistory.visitedPlaces.length)}
          </Text>
          <TrackPreview points={kailashHistory.visitedPlaces} multiMarker height={160} />
        </Card>
      )}

      {deviceType === 'ambit' && isKailash(ambitInfo) && kailashTrack && (
        <Card style={[roomy ? styles.deviceCardRoomyFull : styles.deviceCardCol, styles.deviceCardInner]}>
          <Text style={[styles.deviceName, v3TextStyle]}>{t.homeKailashTrackTitle}</Text>
          <Text style={[styles.deviceSub, v3MutedStyle]}>
            {realTrackPoints(kailashTrack).length} {t.homeKailashTrackPoints}
          </Text>
          <Button
            label={t.homeKailashTrackExport}
            onPress={handleExportKailashTrack}
            disabled={kailashExportBusy}
            grow={false}
          />
        </Card>
      )}
      </View>

      {/* ── This year - desktop HomePage.qml's headline totals surfaced on Home, and a
          doorway to the Totals screen. Uses locally-synced activities (no watch needed);
          hidden until there's at least one. ── */}
      {thisYear && thisYear.count > 0 && (
        <View style={[styles.weatherWrap, roomy && styles.weatherWrapRoomy]}>
          <Card style={styles.deviceCardInner}>
            <TouchableOpacity activeOpacity={0.8} onPress={() => navigation.navigate('Totals')}>
              <View style={styles.cardTitleRow}>
                <Text style={[styles.deviceName, v3TextStyle]}>{t.homeThisYearTitle}</Text>
                <Text style={[styles.cardLink, { color: theme.primary }]}>{t.homeOpenTotals}</Text>
              </View>
              <View style={styles.statRow}>
                <View style={styles.statCol}>
                  <Text style={[styles.statLabel, v3MutedStyle]}>{t.homeStatDistance}</Text>
                  <Text style={[styles.statValue, v3TextStyle]}>{fmtKm(thisYear.meters)}</Text>
                </View>
                <View style={styles.statCol}>
                  <Text style={[styles.statLabel, v3MutedStyle]}>{t.homeStatTime}</Text>
                  <Text style={[styles.statValue, v3TextStyle]}>{fmtDuration(thisYear.seconds)}</Text>
                </View>
                <View style={styles.statCol}>
                  <Text style={[styles.statLabel, v3MutedStyle]}>{t.homeStatActivities}</Text>
                  <Text style={[styles.statValue, v3TextStyle]}>{thisYear.count}</Text>
                </View>
              </View>
              {thisYear.teaser.length > 0 && (
                <Text style={[styles.teaser, v3MutedStyle]}>{thisYear.teaser}</Text>
              )}
            </TouchableOpacity>
          </Card>
        </View>
      )}

      {/* ── Weather - real, 2026-08-09 (v3.0 UI port, "replicate the desktop version
          feature wise"). Same real placement as HomePage.qml: right after the device hero
          card(s), before the actions row. Collapses to nothing on its own (renders null)
          until the first fetch attempt finishes, same as WeatherCard.qml's own
          hasFetchedOnce gate.
          Real bug, found live ("something looks bizarre, sizing wise they are not the
          same, and we have all this space on the borders") - WeatherCard's own Card had no
          width cap of its own, so on a wide tablet it stretched to the ScrollView's full
          width while every card above it (deviceCardCol/cardStackRoomy) is capped -
          differently-sized cards stacked directly on top of each other. Follow-up ("on
          horizontal, the device info and weather are not the same width") - a flat
          CONTENT_MAX_WIDTH cap alone only matched portrait: roomy mode's device card(s) sit
          in cardStackRoomy, capped at 960 not CONTENT_MAX_WIDTH, so weatherWrap now follows
          the exact same roomy switch cardStack itself uses, instead of a single fixed cap. ── */}
      <View style={[styles.weatherWrap, roomy && styles.weatherWrapRoomy]}>
        <WeatherCard />
      </View>

      {/* ── Last Activity - desktop HomePage.qml's card, the newest locally-synced move, a
          doorway to the Activities list. Info only (no map, per desktop's own designer pass). ── */}
      {lastActivity && (
        <View style={[styles.weatherWrap, roomy && styles.weatherWrapRoomy]}>
          <Card style={styles.deviceCardInner}>
            <TouchableOpacity activeOpacity={0.8} onPress={() => navigation.navigate('LogList')}>
              <View style={styles.cardTitleRow}>
                <Text style={[styles.deviceName, v3TextStyle]}>{t.homeLastActivityTitle}</Text>
                <Text style={[styles.cardLink, { color: theme.primary }]}>{t.homeOpenActivities}</Text>
              </View>
              <View style={styles.lastActivityRow}>
                <Icon name="list" size={26} color={theme.primary} />
                <View style={styles.lastActivityInfo}>
                  <Text style={[styles.lastActivityName, v3TextStyle]}>
                    {lastActivity.activity_type || t.homeUntitledActivity}
                  </Text>
                  <Text style={[styles.deviceSub, v3MutedStyle]}>{fmtDate(lastActivity.date)}</Text>
                </View>
              </View>
              <View style={styles.statRow}>
                <Text style={[styles.lastActivityStat, v3TextStyle]}>{fmtKm(lastActivity.distance_m)}</Text>
                <Text style={[styles.lastActivityStat, v3TextStyle]}>{fmtDuration(lastActivity.duration_s)}</Text>
                {lastActivity.d_plus > 0 && (
                  <Text style={[styles.lastActivityStat, v3TextStyle]}>{Math.round(lastActivity.d_plus)} m ↑</Text>
                )}
              </View>
            </TouchableOpacity>
          </Card>
        </View>
      )}

      {/* ── Actions : uniquement les actions réelles (sync/GPS) - Routes/POIs/Backup/
          Sport Modes/Settings sont maintenant des destinations du NavShell ci-dessus,
          pas des actions sur cette carte (v3.0 UI port, 2026-08-09). ── */}
      {connected && (deviceType === 'garmin' ? (
        <View style={[styles.actionsRow, roomy && styles.actionsRowRoomy]}>
          <ActionTile
            icon="sync"
            label={garminSyncLabel}
            progress={garminSync.phase === 'writing' && garminSync.total > 0 ? `${garminSync.current}/${garminSync.total}` : undefined}
            busy={garminSyncBusy}
            onPress={() => handleGarminSync()}
            disabled={isBusy}
            grow
          />
        </View>
      ) : (
        <View style={[styles.actionsRow, roomy && styles.actionsRowRoomy]}>
          {/* GPS orbital update moved up into the device card next to Sync time (André,
              2026-08-16 - it was invisible buried down here on a phone), so this row is just
              the primary activities sync now. */}
          <ActionTile
            icon="sync"
            label={syncLabel}
            progress={sync.phase !== 'idle' && sync.total > 0 ? `${sync.current}/${sync.total}` : undefined}
            busy={syncBusy}
            onPress={handleSync}
            disabled={isBusy}
            grow
          />
        </View>
      ))}

      {/* ── Statut ── */}
      {connected && <StatusLine text={statusText} tone={statusTone} />}

    </ScrollView>
    </NavShell>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

// Desktop-parity formatters for the This year / Last Activity cards (match TotalsScreen).
function fmtKm(meters: number): string {
  const km = meters / 1000;
  return `${km.toLocaleString('en-GB', { maximumFractionDigits: km >= 100 ? 0 : 1 })} km`;
}
function fmtDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function fmtDate(iso: string): string {
  return fmtDateShared(iso); // day-first dd/MM/yyyy, matching the desktop (see i18n/fmtDate)
}

function syncPhaseLabel(phase: SyncState['phase']): string {
  switch (phase) {
    case 'connecting': return t.conn;
    case 'fetching':   return t.read;
    case 'writing':    return t.save;
    default:           return '…';
  }
}

function syncStatusMessage(sync: SyncState): string {
  switch (sync.phase) {
    case 'idle':       return t.idle;
    case 'connecting': return t.connecting;
    case 'fetching':   return t.fetching;
    case 'writing':    return t.writing;
    case 'done':       return t.done(sync.newCount);
    case 'error':      return sync.error ?? t.error;
    default:           return '';
  }
}

function orbitalPhaseLabel(phase: OrbitalUpdateState['phase']): string {
  switch (phase) {
    case 'connecting':  return t.conn;
    case 'downloading': return t.gpsDownloading;
    case 'writing':     return t.save;
    default:            return '…';
  }
}

function orbitalStatusMessage(s: OrbitalUpdateState): string {
  switch (s.phase) {
    case 'idle':        return t.gpsIdle;
    case 'connecting':  return t.connecting;
    case 'downloading': return t.gpsDownloadingMsg;
    case 'writing':     return t.writing;
    case 'done':        return t.gpsDoneMsg;
    case 'error':        return s.error ?? t.error;
    default:             return '';
  }
}

// ─── Styles ───────────────────────────────────────────────────────────────────

// Cap the content column so cards/tiles form a tidy centered stack instead of
// stretching edge-to-edge on a wide (landscape/tablet) screen. Portrait phones are
// narrower than this, so they're unaffected — width:'100%' still wins there.
const CONTENT_MAX_WIDTH = 560;

function createStyles(t: ReturnType<typeof useV3Theme>) {
  return StyleSheet.create({
    // The connected screen scrolls (a lot of content: device cards + a 2-column tile
    // grid + footer), so on short/landscape screens nothing clips.
    scroll: {
      flex: 1,
      backgroundColor: t.background,
    },
    // Real bug, found live ("on vertical there is a lot of space between the
    // cards...guess it may be 'replicating' something from horizontal") - this was
    // copy-pasted from `container` (used by the sparse pre-connect device-flow screens,
    // where space-between + flexGrow really is the layout: a handful of items centered
    // and spread across a mostly-empty screen). The connected dashboard has real,
    // differently-sized content (device cards, weather, a tile grid, a status line) -
    // space-between distributed ALL leftover portrait screen height between those few
    // children, stretching the gaps between them far past what any of them needed. A
    // fixed gap between sections, no artificial full-height spreading, fixes it.
    scrollContent: {
      flexGrow: 1,
      alignItems: 'center',
      gap: 24,
      paddingVertical: 56,
      paddingHorizontal: 24,
    },
    deviceFlowContainer: {
      flex: 1,
      backgroundColor: t.background,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: 32,
      gap: 24,
    },
    // Real, 2026-08-10 ("the initial screen while we are waiting for devices to connect
    // has a big mix of type of letters, sizes, can you uniformize it") - deviceFlowTitle
    // had no explicit fontWeight at all (defaulting to regular, 400), looking flimsy next
    // to the wordmark's own bold 800 right above it; the tagline was italic (the only
    // italic text anywhere in this app) and didn't scale with deviceFlowScale the way the
    // wordmark/title/badge all do, so it looked disproportionately small on a tablet. Both
    // fixed: one consistent weight scheme (wordmark 800 > title 600 > tagline 500 > badge
    // 700, small-pill convention) and one consistent scale factor throughout.
    deviceFlowTitle: {
      color: t.mutedText,
      fontWeight: '600',
      fontSize: 16,
      textAlign: 'center',
      lineHeight: 23,
    },
    deviceFlowError: {
      color: t.error,
    },
    deviceFlowButtons: {
      gap: 10,
      width: '100%',
      alignItems: 'center',
    },
    deviceFlowLogo: {
      alignItems: 'center',
      gap: 10,
    },
    deviceFlowTagline: {
      fontWeight: '500',
      fontSize: 12.5,
      textAlign: 'center',
    },
    header: {
      alignItems: 'center',
      gap: 6,
    },
    appName: {
      fontSize: 26,
      fontWeight: '800',
      color: t.text,
      letterSpacing: 1.5,
    },
    // Wrapper around the device-info cards. Portrait: a centered column. Roomy: a
    // centered wrapping row so the cards sit side by side (see cardStackRoomy).
    cardStack: {
      width: '100%',
      alignItems: 'center',
      gap: 8,
    },
    cardStackRoomy: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      maxWidth: 960,
      justifyContent: 'center',
      alignItems: 'stretch',
      gap: 10,
    },
    // v3.0 UI port - the old flat-theme surface (background/border) is gone; Card supplies
    // its own v3-palette background/radius/shadow now. This keeps only what's still real
    // layout, not color: centering the card's own content.
    deviceCardInner: {
      alignItems: 'center',
      justifyContent: 'center',
    },
    // Portrait: one full-width card per row, capped so it doesn't stretch on a tablet.
    deviceCardCol: {
      width: '100%',
      maxWidth: CONTENT_MAX_WIDTH,
    },
    // Roomy: cards share the row, ~3 across (min 250 so they wrap to 2 when narrower).
    deviceCardRoomy: {
      flexBasis: '31%',
      flexGrow: 1,
      minWidth: 250,
    },
    // Roomy, but full-width: forces the card onto its own row (flexBasis 100%) so the
    // Kailash panels stack one-up/one-down like portrait instead of sitting beside the
    // watch card (André, 2026-08-15: "on horizontal the watch and travel history are side
    // by side, would prefer one up and other down like in the vertical").
    deviceCardRoomyFull: {
      flexBasis: '100%',
      flexGrow: 1,
    },
    // Same cap as deviceCardCol - see the JSX comment above on why WeatherCard needed this
    // (it's the only card on this screen that wasn't already capped to CONTENT_MAX_WIDTH).
    weatherWrap: {
      width: '100%',
      maxWidth: CONTENT_MAX_WIDTH,
    },
    // roomy: matches cardStackRoomy's own 960 cap, same reasoning as deviceCardRoomy above.
    weatherWrapRoomy: {
      maxWidth: 960,
    },
    deviceName: {
      color: t.text,
      fontSize: 15,
      fontWeight: '700',
      textAlign: 'center',
    },
    // Multi-watch switcher pills (2026-08-16) - a bordered pill per attached watch, the
    // selected one filled with Theme.primary (same selected-state language as the Appearance
    // and nav selectors).
    watchSwitcher: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      justifyContent: 'center',
      gap: 8,
      marginTop: 10,
    },
    watchChip: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 5,
      borderWidth: 1,
      borderRadius: 999,
      paddingHorizontal: 12,
      paddingVertical: 6,
    },
    watchChipText: {
      fontSize: 12,
      fontWeight: '700',
    },
    // The "pair a new Bluetooth watch" action pill — dashed to read as an add-action, not a watch.
    watchChipAction: {
      borderStyle: 'dashed',
    },
    deviceBattery: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 4,
    },
    deviceBatteryText: {
      color: t.mutedText,
      fontSize: 11,
      fontWeight: '600',
    },
    deviceSub: {
      color: t.mutedText,
      fontSize: 11.5,
      textAlign: 'center',
      marginTop: 2,
    },
    deviceMetaRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 14,
      marginTop: 8,
    },
    timeSyncRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
      marginTop: 10,
    },
    // The three card actions (Sync time / GPS data / View manual) on one centered horizontal
    // row, wrapping only if they overflow (André, 2026-08-16).
    deviceLinksRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      alignItems: 'center',
      justifyContent: 'center',
      columnGap: 22,
      rowGap: 8,
      marginTop: 12,
    },
    deviceLink: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 6,
    },
    heroButtons: {
      alignItems: 'center',
      gap: 4,
      marginTop: 12,
      width: '100%',
    },
    // ── This year / Last Activity cards (desktop-parity) ──
    cardTitleRow: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      width: '100%',
    },
    cardLink: {
      fontSize: 11.5,
      fontWeight: '600',
    },
    statRow: {
      flexDirection: 'row',
      alignItems: 'flex-start',
      gap: 28,
      marginTop: 12,
      width: '100%',
    },
    statCol: {
      alignItems: 'flex-start',
    },
    statLabel: {
      fontSize: 11,
      marginBottom: 2,
    },
    statValue: {
      fontSize: 16,
      fontWeight: '700',
    },
    teaser: {
      fontSize: 11.5,
      fontStyle: 'italic',
      marginTop: 12,
      width: '100%',
    },
    lastActivityRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 12,
      marginTop: 12,
      width: '100%',
    },
    lastActivityInfo: {
      flex: 1,
      alignItems: 'flex-start',
    },
    lastActivityName: {
      fontSize: 14,
      fontWeight: '700',
    },
    lastActivityStat: {
      fontSize: 12.5,
    },
    timeSyncText: {
      fontSize: 12,
      fontWeight: '600',
    },
    actionsRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      width: '100%',
      maxWidth: CONTENT_MAX_WIDTH,
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
    },
    // Roomy: widen the tile grid to match the side-by-side cards (tiles go 3 columns,
    // handled inside ActionTile's own width check).
    actionsRowRoomy: {
      maxWidth: 960,
    },
    settingsBtn: {
      width: 52,
      height: 52,
      borderRadius: 16,
      borderWidth: 1,
      borderColor: t.mutedText + '33',
      backgroundColor: t.card,
      alignItems: 'center',
      justifyContent: 'center',
    },
  });
}
