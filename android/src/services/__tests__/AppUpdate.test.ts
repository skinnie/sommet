// App updates (André, 2026-11-03, 🟣124): the GitHub release check, the once-a-day rule, the
// Home note's close, and the download + hand-over to Android's installer.

const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async (k: string) => mockStore[k] ?? null),
  setItem: jest.fn(async (k: string, v: string) => { mockStore[k] = v; }),
}));

const mockFiles: Record<string, number> = {};
const mockDownload = jest.fn();
jest.mock('react-native-fs', () => ({
  CachesDirectoryPath: '/data/user/0/app/cache',
  exists: jest.fn(async (p: string) => Object.keys(mockFiles).some(f => f.startsWith(p))),
  unlink: jest.fn(async (p: string) => { for (const f of Object.keys(mockFiles)) if (f.startsWith(p)) delete mockFiles[f]; }),
  mkdir: jest.fn(async () => {}),
  stat: jest.fn(async (p: string) => ({ size: mockFiles[p] ?? 0 })),
  downloadFile: (...a: unknown[]) => mockDownload(...a),
}));

const mockOpenURL = jest.fn(async () => {});
const mockInstall = jest.fn(async () => {});
jest.mock('react-native', () => ({
  Platform: { OS: 'android' },
  Linking: { openURL: (...a: unknown[]) => mockOpenURL(...(a as [])) },
  NativeModules: {
    AppUpdate: {
      canInstall: jest.fn(async () => true),
      openInstallSettings: jest.fn(async () => {}),
      installApk: (...a: unknown[]) => mockInstall(...(a as [])),
    },
  },
}));

jest.mock('../../config/version', () => ({ APP_VERSION: '0.2.57', BUILD_SHA: 'test' }));

import {
  __resetAppUpdateForTests, checkNow, dismissUpdate, downloadAndInstall, getUpdateState,
  highlightsFrom, initAppUpdate, isNewer, parseRelease, showUpdateNotice, updateAvailable,
  CHECK_EVERY_MS, LATEST_RELEASE_API, UPDATES_DIR,
} from '../AppUpdate';

const BODY = [
  '## 2026-11-05: Update note on both apps (0.2.58)', '', '- A note on Home.', '',
  '## 2026-11-04: Something between releases (Android)', '', '- Fixed.', '',
  '**Full Changelog**: https://github.com/skinnie/sommet/compare/v0.2.57...v0.2.58',
].join('\r\n');

const release = (over: Record<string, unknown> = {}) => ({
  tag_name: 'v0.2.58',
  html_url: 'https://github.com/skinnie/sommet/releases/tag/v0.2.58',
  draft: false,
  prerelease: false,
  body: BODY,
  assets: [
    { name: 'Sommet-macos.dmg', size: 10, browser_download_url: 'https://github.com/skinnie/sommet/releases/download/v0.2.58/Sommet-macos.dmg' },
    { name: 'Sommet_v0.2.58.apk', size: 1234, browser_download_url: 'https://github.com/skinnie/sommet/releases/download/v0.2.58/Sommet_v0.2.58.apk' },
  ],
  ...over,
});

const reply = (status: number, json?: unknown) =>
  jest.fn(async () => ({ ok: status >= 200 && status < 300, status, json: async () => json }));

beforeEach(() => {
  __resetAppUpdateForTests();
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  for (const k of Object.keys(mockFiles)) delete mockFiles[k];
  mockDownload.mockReset();
  mockInstall.mockReset();
  mockInstall.mockImplementation(async () => {});
  mockOpenURL.mockClear();
});

describe('versions and release text', () => {
  test('isNewer compares the three numbers, tags or plain', () => {
    expect(isNewer('v0.2.58', '0.2.57')).toBe(true);
    expect(isNewer('v0.3.0', '0.2.99')).toBe(true);
    expect(isNewer('v0.2.57', '0.2.57')).toBe(false);
    expect(isNewer('v0.2.9', '0.2.10')).toBe(false);
    expect(isNewer('nightly', '0.2.57')).toBe(false);
  });

  test('What\'s new = the dated changelog headings, not GitHub\'s own lines', () => {
    expect(highlightsFrom(BODY)).toEqual([
      'Update note on both apps (0.2.58)',
      'Something between releases (Android)',
    ]);
    expect(highlightsFrom('')).toEqual([]);
  });

  test('parseRelease keeps only this project\'s links, and finds the APK', () => {
    const r = parseRelease(release());
    expect(r).toEqual({
      version: '0.2.58',
      pageUrl: 'https://github.com/skinnie/sommet/releases/tag/v0.2.58',
      apkUrl: 'https://github.com/skinnie/sommet/releases/download/v0.2.58/Sommet_v0.2.58.apk',
      apkSize: 1234,
      highlights: ['Update note on both apps (0.2.58)', 'Something between releases (Android)'],
    });
    expect(parseRelease(release({ html_url: 'https://example.com/sommet' }))).toBeNull();
    expect(parseRelease(release({ draft: true }))).toBeNull();
    expect(parseRelease(release({ prerelease: true }))).toBeNull();
    expect(parseRelease(release({ tag_name: 'latest' }))).toBeNull();
    const foreignApk = parseRelease(release({
      assets: [{ name: 'Sommet.apk', size: 1, browser_download_url: 'https://evil.example/Sommet.apk' }],
    }));
    expect(foreignApk?.apkUrl).toBeNull();
  });
});

describe('checking', () => {
  test('a newer release shows the note; the answer is saved', async () => {
    const fetchFn = reply(200, release());
    await initAppUpdate(fetchFn as unknown as typeof fetch, 1_000_000);
    expect(fetchFn).toHaveBeenCalledWith(LATEST_RELEASE_API, expect.anything());
    const s = getUpdateState();
    expect(s.status).toBe('available');
    expect(updateAvailable(s)).toBe(true);
    expect(showUpdateNotice(s)).toBe(true);
    expect(JSON.parse(mockStore['sommet.appUpdate.v1']).latest.version).toBe('0.2.58');
  });

  test('asks GitHub at most about once a day', async () => {
    const first = reply(200, release());
    await initAppUpdate(first as unknown as typeof fetch, 1_000_000);
    __resetAppUpdateForTests();                      // the app starts again an hour later
    const second = reply(200, release());
    await initAppUpdate(second as unknown as typeof fetch, 1_000_000 + 3600_000);
    expect(second).not.toHaveBeenCalled();
    expect(getUpdateState().status).toBe('available');   // from the saved answer
    __resetAppUpdateForTests();                      // and the next day
    const third = reply(200, release({ tag_name: 'v0.2.59', html_url: 'https://github.com/skinnie/sommet/releases/tag/v0.2.59' }));
    await initAppUpdate(third as unknown as typeof fetch, 1_000_000 + CHECK_EVERY_MS + 1);
    expect(third).toHaveBeenCalled();
    expect(getUpdateState().latest?.version).toBe('0.2.59');
  });

  test('the same version is up to date; no release at all too', async () => {
    await checkNow(reply(200, release({ tag_name: 'v0.2.57' })) as unknown as typeof fetch);
    expect(getUpdateState().status).toBe('upToDate');
    expect(showUpdateNotice(getUpdateState())).toBe(false);
    __resetAppUpdateForTests();
    await checkNow(reply(404) as unknown as typeof fetch);
    expect(getUpdateState().status).toBe('upToDate');
  });

  test('offline keeps the last good answer', async () => {
    await checkNow(reply(200, release()) as unknown as typeof fetch);
    await checkNow(jest.fn(async () => { throw new Error('offline'); }) as unknown as typeof fetch);
    expect(getUpdateState().status).toBe('error');
    expect(getUpdateState().latest?.version).toBe('0.2.58');
    expect(getUpdateState().phase).toBe('idle');
  });

  test('closing the Home note lasts until a newer version', async () => {
    await checkNow(reply(200, release()) as unknown as typeof fetch);
    await dismissUpdate();
    expect(showUpdateNotice(getUpdateState())).toBe(false);
    expect(updateAvailable(getUpdateState())).toBe(true);     // Settings still shows it
    await checkNow(reply(200, release({ tag_name: 'v0.2.59', html_url: 'https://github.com/skinnie/sommet/releases/tag/v0.2.59' })) as unknown as typeof fetch);
    expect(showUpdateNotice(getUpdateState())).toBe(true);
  });

  test('the daily check can be turned off', async () => {
    mockStore['sommet.appUpdate.v1'] = JSON.stringify({ autoCheck: false });
    const fetchFn = reply(200, release());
    await initAppUpdate(fetchFn as unknown as typeof fetch);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(getUpdateState().autoCheck).toBe(false);
  });
});

describe('download and install', () => {
  const apk = `${UPDATES_DIR}/Sommet_v0.2.58.apk`;

  test('downloads the APK, checks its size, and opens the installer', async () => {
    await checkNow(reply(200, release()) as unknown as typeof fetch);
    mockDownload.mockImplementation((o: { toFile: string }) => {
      mockFiles[o.toFile] = 1234;
      return { promise: Promise.resolve({ statusCode: 200 }) };
    });
    await downloadAndInstall();
    expect(mockDownload).toHaveBeenCalledWith(expect.objectContaining({
      fromUrl: 'https://github.com/skinnie/sommet/releases/download/v0.2.58/Sommet_v0.2.58.apk',
      toFile: apk,
    }));
    expect(mockInstall).toHaveBeenCalledWith(apk, '0.2.58');
    expect(getUpdateState().problem).toBe('');
    expect(getUpdateState().phase).toBe('idle');
  });

  test('a short download is not installed', async () => {
    await checkNow(reply(200, release()) as unknown as typeof fetch);
    mockDownload.mockImplementation((o: { toFile: string }) => {
      mockFiles[o.toFile] = 600;
      return { promise: Promise.resolve({ statusCode: 200 }) };
    });
    await downloadAndInstall();
    expect(mockInstall).not.toHaveBeenCalled();
    expect(getUpdateState().problem).toBe('downloadFailed');
  });

  test('a file that isn\'t this version is refused by the native check', async () => {
    await checkNow(reply(200, release()) as unknown as typeof fetch);
    mockDownload.mockImplementation((o: { toFile: string }) => {
      mockFiles[o.toFile] = 1234;
      return { promise: Promise.resolve({ statusCode: 200 }) };
    });
    mockInstall.mockImplementation(async () => { throw Object.assign(new Error('x'), { code: 'APK_MISMATCH' }); });
    await downloadAndInstall();
    expect(getUpdateState().problem).toBe('mismatch');
  });

  test('a release without an APK opens its page instead', async () => {
    await checkNow(reply(200, release({ assets: [] })) as unknown as typeof fetch);
    await downloadAndInstall();
    expect(mockDownload).not.toHaveBeenCalled();
    expect(mockOpenURL).toHaveBeenCalledWith('https://github.com/skinnie/sommet/releases/tag/v0.2.58');
  });
});
