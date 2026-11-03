import { useSyncExternalStore } from 'react';
import { Linking, NativeModules, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import RNFS from 'react-native-fs';
import { APP_VERSION } from '../config/version';

// "Is there a newer Sommet?" (André, 2026-11-03, 🟣124 = tell you and take you there). Asks
// GitHub's public releases API for the latest release once a day (and from the Settings
// button), compares its tag with this app's version, and on request downloads the release's
// APK and hands it to Android's installer (AppUpdateModule.kt), which asks the user to
// confirm. Only the request reaches GitHub: no identifier, no usage data. The desktop twin is
// desktop/src/services/updateservice.cpp; the "What's new" lines are the changelog headings
// tools/release_notes.py puts in the release text.

export const LATEST_RELEASE_API = 'https://api.github.com/repos/skinnie/sommet/releases/latest';
// Only links into this project's own releases are ever opened or downloaded.
export const RELEASES_PREFIX = 'https://github.com/skinnie/sommet/releases/';
// "Once a day", a little early so a tablet opened every morning still gets its daily look.
export const CHECK_EVERY_MS = 20 * 3600 * 1000;
const MAX_HIGHLIGHTS = 8;
const STORAGE_KEY = 'sommet.appUpdate.v1';

export interface ReleaseInfo {
  version: string;        // "0.2.58"
  pageUrl: string;        // the release page
  apkUrl: string | null;  // the APK attached to the release
  apkSize: number;        // bytes, 0 when GitHub didn't say
  highlights: string[];   // "What's new" lines
}

export function versionParts(v: string): number[] | null {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(v ?? '');
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

// "v0.2.58" newer than "0.2.57"? Tags and versions both accepted; garbage is never newer.
export function isNewer(latest: string, current: string): boolean {
  const a = versionParts(latest);
  const b = versionParts(current);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

// tools/release_notes.py writes one "## 2026-11-03: Title (0.2.57)" heading per change.
export function highlightsFrom(body: string): string[] {
  const out: string[] = [];
  for (const line of (body ?? '').split(/\r?\n/)) {
    const m = /^##\s+\d{4}-\d{2}-\d{2}:\s*(.+?)\s*$/.exec(line);
    if (m) out.push(m[1]);
    if (out.length >= MAX_HIGHLIGHTS) break;
  }
  return out;
}

// GitHub's "latest release" JSON -> what the note needs; null when it isn't a usable release.
export function parseRelease(json: any): ReleaseInfo | null {
  if (!json || typeof json !== 'object' || json.draft || json.prerelease) return null;
  const parts = versionParts(String(json.tag_name ?? ''));
  const pageUrl = String(json.html_url ?? '');
  if (!parts || !pageUrl.startsWith(RELEASES_PREFIX)) return null;
  const apk = (Array.isArray(json.assets) ? json.assets : []).find((a: any) =>
    typeof a?.name === 'string' && a.name.toLowerCase().endsWith('.apk')
    && String(a.browser_download_url ?? '').startsWith(RELEASES_PREFIX));
  return {
    version: parts.join('.'),
    pageUrl,
    apkUrl: apk ? String(apk.browser_download_url) : null,
    apkSize: apk && Number.isFinite(apk.size) ? Number(apk.size) : 0,
    highlights: highlightsFrom(String(json.body ?? '')),
  };
}

// ── State shared by Home's note and Settings → About ──────────────────────────────────────

export type UpdateStatus = '' | 'upToDate' | 'available' | 'error';
export type UpdatePhase = 'idle' | 'checking' | 'downloading' | 'installing';
// Problems the note explains in a sentence (texts in i18n: update*).
export type UpdateProblem = '' | 'downloadFailed' | 'installFailed' | 'mismatch';

export interface UpdateState {
  loaded: boolean;
  autoCheck: boolean;
  lastChecked: number | null;   // ms since epoch
  latest: ReleaseInfo | null;
  dismissed: string | null;     // version whose Home note was closed
  status: UpdateStatus;
  phase: UpdatePhase;
  progress: number;             // 0..1 while downloading
  problem: UpdateProblem;
  needsAllow: boolean;          // Android hasn't allowed Sommet to install apps yet
}

const initial: UpdateState = {
  loaded: false, autoCheck: true, lastChecked: null, latest: null, dismissed: null,
  status: '', phase: 'idle', progress: 0, problem: '', needsAllow: false,
};

let state: UpdateState = initial;
const listeners = new Set<() => void>();

function set(patch: Partial<UpdateState>) {
  state = { ...state, ...patch };
  listeners.forEach(l => l());
}

async function save() {
  const { autoCheck, lastChecked, latest, dismissed } = state;
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ autoCheck, lastChecked, latest, dismissed }));
  } catch { /* a lost save only means one more check */ }
}

export function getUpdateState(): UpdateState {
  return state;
}

export function subscribeUpdate(l: () => void): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function useAppUpdate(): UpdateState {
  return useSyncExternalStore(subscribeUpdate, getUpdateState);
}

export const updateAvailable = (s: UpdateState, current = APP_VERSION): boolean =>
  !!s.latest && isNewer(s.latest.version, current);
export const showUpdateNotice = (s: UpdateState, current = APP_VERSION): boolean =>
  updateAvailable(s, current) && s.dismissed !== s.latest?.version;

// Only Android installs APKs; elsewhere (iOS) there is nothing to offer.
export const updatesSupported = (): boolean => Platform.OS === 'android';

let loading: Promise<void> | null = null;

// Loads the saved answer once, then asks GitHub if the last answer is about a day old.
// Home calls it on start; calling it again is free.
export function initAppUpdate(fetchFn: typeof fetch = fetch, now = Date.now()): Promise<void> {
  if (!updatesSupported()) return Promise.resolve();
  if (!loading) {
    loading = (async () => {
      // An APK downloaded before this start has been installed or given up: free the space.
      RNFS.exists(UPDATES_DIR).then(e => (e ? RNFS.unlink(UPDATES_DIR) : undefined)).catch(() => {});
      try {
        const raw = await AsyncStorage.getItem(STORAGE_KEY);
        const saved = raw ? JSON.parse(raw) : {};
        const latest: ReleaseInfo | null =
          saved.latest && String(saved.latest.pageUrl ?? '').startsWith(RELEASES_PREFIX) ? saved.latest : null;
        set({
          loaded: true,
          autoCheck: saved.autoCheck !== false,
          lastChecked: typeof saved.lastChecked === 'number' ? saved.lastChecked : null,
          latest,
          dismissed: typeof saved.dismissed === 'string' ? saved.dismissed : null,
        });
        if (state.lastChecked) set({ status: updateAvailable(state) ? 'available' : 'upToDate' });
      } catch {
        set({ loaded: true });
      }
    })();
  }
  return loading.then(() => checkIfDue(fetchFn, now));
}

export async function checkIfDue(fetchFn: typeof fetch = fetch, now = Date.now()): Promise<void> {
  if (!state.autoCheck || state.phase !== 'idle') return;
  if (state.lastChecked != null && now - state.lastChecked < CHECK_EVERY_MS) return;
  await checkNow(fetchFn, now);
}

export async function checkNow(fetchFn: typeof fetch = fetch, now = Date.now()): Promise<void> {
  if (state.phase !== 'idle') return;
  set({ phase: 'checking', problem: '' });
  try {
    const res = await fetchFn(LATEST_RELEASE_API, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': `Sommet/${APP_VERSION}` },
    });
    if (res.status === 404) {
      // No published release at all: nothing is newer than this app.
      set({ phase: 'idle', status: 'upToDate', lastChecked: now });
    } else {
      const info = res.ok ? parseRelease(await res.json()) : null;
      if (!info) throw new Error(`release check: HTTP ${res.status}`);
      set({ phase: 'idle', latest: info, lastChecked: now });
      set({ status: updateAvailable(state) ? 'available' : 'upToDate' });
    }
    await save();
  } catch {
    // Offline, GitHub busy, or not a release: the last good answer stays; tomorrow tries again.
    set({ phase: 'idle', status: 'error' });
  }
}

export async function setAutoCheck(on: boolean): Promise<void> {
  set({ autoCheck: on });
  await save();
  if (on) await checkIfDue();
}

// Closes the Home note until a newer version comes out. Settings still shows it.
export async function dismissUpdate(): Promise<void> {
  if (!state.latest) return;
  set({ dismissed: state.latest.version });
  await save();
}

export function openReleasePage(): void {
  const url = state.latest?.pageUrl;
  if (url && url.startsWith(RELEASES_PREFIX)) Linking.openURL(url).catch(() => {});
}

interface AppUpdateNative {
  canInstall(): Promise<boolean>;
  openInstallSettings(): Promise<void>;
  installApk(path: string, expectedVersion: string): Promise<void>;
}
const native = (): AppUpdateNative | undefined => NativeModules.AppUpdate as AppUpdateNative | undefined;

export const UPDATES_DIR = `${RNFS.CachesDirectoryPath}/updates`;

// Downloads the release's APK into the cache, checks its size, and opens Android's installer.
export async function downloadAndInstall(): Promise<void> {
  const info = state.latest;
  const mod = native();
  if (!info || state.phase !== 'idle') return;
  if (!info.apkUrl || !info.apkUrl.startsWith(RELEASES_PREFIX) || !mod) {
    openReleasePage();
    return;
  }
  set({ phase: 'downloading', progress: 0, problem: '' });
  const path = `${UPDATES_DIR}/Sommet_v${info.version}.apk`;
  try {
    // Only one APK is ever kept: older downloads go first.
    if (await RNFS.exists(UPDATES_DIR)) await RNFS.unlink(UPDATES_DIR);
    await RNFS.mkdir(UPDATES_DIR);
    const job = RNFS.downloadFile({
      fromUrl: info.apkUrl,
      toFile: path,
      headers: { 'User-Agent': `Sommet/${APP_VERSION}` },
      progressDivider: 2,
      progress: r => {
        if (r.contentLength > 0) set({ progress: r.bytesWritten / r.contentLength });
      },
    });
    const result = await job.promise;
    const size = (await RNFS.stat(path)).size;
    if (result.statusCode !== 200 || (info.apkSize > 0 && Number(size) !== info.apkSize)) {
      throw new Error(`download: HTTP ${result.statusCode}, ${size} of ${info.apkSize} bytes`);
    }
  } catch {
    set({ phase: 'idle', problem: 'downloadFailed' });
    RNFS.unlink(path).catch(() => {});
    return;
  }
  set({ phase: 'installing', progress: 1, needsAllow: !(await mod.canInstall().catch(() => true)) });
  try {
    await mod.installApk(path, info.version);
    set({ phase: 'idle' });
  } catch (e: any) {
    set({ phase: 'idle', problem: e?.code === 'APK_MISMATCH' || e?.code === 'APK_DAMAGED' ? 'mismatch' : 'installFailed' });
  }
}

// Test hook: back to the state of a fresh start.
export function __resetAppUpdateForTests(): void {
  state = initial;
  loading = null;
  listeners.clear();
}
