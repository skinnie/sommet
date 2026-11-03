// LogService (André, 2026-11-02, desktop picks #19): the log file, its size cap, Send logs, and
// the "already a plain sentence" rule ErrorBanner shares with the desktop.

const mockFiles: Record<string, string> = {};
jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/data/user/0/app/files',
  exists: jest.fn(async (p: string) => p in mockFiles),
  writeFile: jest.fn(async (p: string, s: string) => { mockFiles[p] = s; }),
  appendFile: jest.fn(async (p: string, s: string) => { mockFiles[p] = (mockFiles[p] ?? '') + s; }),
  readFile: jest.fn(async (p: string) => mockFiles[p] ?? ''),
  stat: jest.fn(async (p: string) => ({ size: (mockFiles[p] ?? '').length })),
}));

const mockOpenURL = jest.fn();
const mockShare = jest.fn();
jest.mock('react-native', () => ({
  Platform: { OS: 'android', Version: 30 },
  Linking: { openURL: (...a: unknown[]) => mockOpenURL(...a) },
  Share: { share: (...a: unknown[]) => mockShare(...a) },
}));

import {
  appendLog, isPlainSentence, logPath, logTimestamp, reportProblem, tailOfLog, trimLog,
  userMessage, REPORT_ADDRESS, MAX_LOG_CHARS,
} from '../LogService';

beforeEach(() => {
  for (const k of Object.keys(mockFiles)) delete mockFiles[k];
  mockOpenURL.mockReset();
  mockShare.mockReset();
});

describe('isPlainSentence (same rule as desktop ErrorBanner._isPlain)', () => {
  test('one short sentence for the user is plain', () => {
    expect(isPlainSentence("Sommet's watch helper isn't running - close Sommet and open it again.")).toBe(true);
    expect(isPlainSentence('  The watch went to sleep.  ')).toBe(true);
    expect(isPlainSentence('Is the scale on?')).toBe(true);
  });
  test('raw text is not', () => {
    expect(isPlainSentence('')).toBe(false);
    expect(isPlainSentence('intervals.icu wellness: HTTP 403')).toBe(false);          // no full stop
    expect(isPlainSentence('Error transferring http://127.0.0.1:8766/api - server replied: Bad Gateway.')).toBe(false);
    expect(isPlainSentence('TypeError: Network request failed.\n  at fetch')).toBe(false);
    expect(isPlainSentence('Traceback (most recent call last): boom.')).toBe(false);
    expect(isPlainSentence('NullPointerException in the module.')).toBe(false);
    expect(isPlainSentence('x'.repeat(200) + '.')).toBe(false);                        // too long
  });
});

test('the general sentence is the desktop one', () => {
  expect(userMessage()).toBe('An error has occurred. Please send the logs.');
});

test('timestamps are local time without an offset, like Qt::ISODate', () => {
  expect(logTimestamp(new Date(2026, 10, 2, 9, 5, 7))).toBe('2026-11-02T09:05:07');
});

test('lines are appended with a timestamp, and a multi-line detail stays one entry', async () => {
  await appendLog('reading weigh-ins: first');
  await appendLog('reading activities: line one\nline two');
  const text = mockFiles[logPath()];
  const lines = text.trimEnd().split('\n');
  expect(lines).toHaveLength(3);
  expect(lines[0]).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} reading weigh-ins: first$/);
  expect(lines[1]).toMatch(/reading activities: line one$/);
  expect(lines[2]).toBe('  line two');
});

test('a log past the cap keeps its tail, starting on a whole line', async () => {
  mockFiles[logPath()] = Array.from({ length: 8000 }, (_, i) => `2026-11-02T00:00:00 old line ${i}`).join('\n') + '\n';
  expect(mockFiles[logPath()].length).toBeGreaterThan(MAX_LOG_CHARS);
  await appendLog('newest');
  const text = mockFiles[logPath()];
  expect(text.length).toBeLessThanOrEqual(MAX_LOG_CHARS);
  expect(text.startsWith('2026-11-02T00:00:00 old line ')).toBe(true);
  expect(text.trimEnd().endsWith('newest')).toBe(true);
});

test('trimLog leaves a short text alone and never cuts a line in half', () => {
  expect(trimLog('a\nb\n', 100)).toBe('a\nb\n');
  expect(trimLog('first line\nsecond\nthird\n', 12)).toBe('third\n');
});

test('tailOfLog says when there is no file yet, and returns the end of a long one', async () => {
  expect(await tailOfLog()).toBe('(no log file yet)');
  mockFiles[logPath()] = 'x'.repeat(3000) + 'END';
  const tail = await tailOfLog(100);
  expect(tail).toHaveLength(100);
  expect(tail.endsWith('END')).toBe(true);
});

test('Send logs opens a mail to the report address with the log tail', async () => {
  await appendLog('reading health data: intervals.icu wellness: HTTP 500');
  mockOpenURL.mockResolvedValue(undefined);
  await reportProblem('reading health data');
  expect(mockOpenURL).toHaveBeenCalledTimes(1);
  const url: string = mockOpenURL.mock.calls[0][0];
  expect(url.startsWith(`mailto:${REPORT_ADDRESS}?subject=`)).toBe(true);
  const body = decodeURIComponent(url.split('&body=')[1]);
  expect(body).toContain('----- last log lines -----');
  expect(body).toContain('intervals.icu wellness: HTTP 500');
  expect(body).toContain('REPORTED: reading health data');
  expect(mockShare).not.toHaveBeenCalled();
});

test('without a mail app, Send logs falls back to the share sheet', async () => {
  mockOpenURL.mockRejectedValue(new Error('No Activity found to handle Intent'));
  mockShare.mockResolvedValue({ action: 'sharedAction' });
  await reportProblem('reading weigh-ins');
  expect(mockShare).toHaveBeenCalledTimes(1);
  const arg = mockShare.mock.calls[0][0];
  expect(arg.message.startsWith(`To: ${REPORT_ADDRESS}`)).toBe(true);
  expect(arg.message).toContain('REPORTED: reading weigh-ins');
});
