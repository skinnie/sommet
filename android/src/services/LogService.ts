import RNFS from 'react-native-fs';
import { Linking, Platform, Share } from 'react-native';
import { APP_VERSION } from '../config/version';

// The app's log file and "Send logs" (André, 2026-11-02, desktop picks #19) - the Android port of
// desktop/src/services/logservice.cpp. Screens used to print a raw error (an HTTP status, a stack
// line) in front of the user. Now ErrorBanner shows one plain sentence, the raw detail goes here,
// and Send logs opens a mail with the last lines of this file.

// André's own GitHub address, the same constant as the desktop's kReportAddress.
export const REPORT_ADDRESS = '5618623+skinnie@users.noreply.github.com';

// Mail apps truncate long mailto: links; 1500 characters carry the failure and its context
// (desktop kInlineLogChars).
const INLINE_LOG_CHARS = 1500;

// The file stays small: past MAX_LOG_CHARS it is cut back to its last KEEP_LOG_CHARS.
export const MAX_LOG_CHARS = 200_000;
export const KEEP_LOG_CHARS = 100_000;

export function logPath(): string {
  return `${RNFS.DocumentDirectoryPath}/ambitapp.log`;
}

// The one sentence shown for an error, the desktop's LogService.userMessage().
export function userMessage(): string {
  return 'An error has occurred. Please send the logs.';
}

// When a detail is already one plain sentence for the user ("Sommet's watch helper isn't
// running - close Sommet and open it again."), ErrorBanner can show it instead of the general
// sentence. Raw text (addresses, tracebacks, network strings) still gets the general sentence.
// Same rule as desktop ErrorBanner._isPlain.
export function isPlainSentence(s: string): boolean {
  const t = (s ?? '').trim();
  return t.length > 0 && t.length <= 200
    && /[.!?]$/.test(t)
    && !/[\n\r]/.test(t)
    && !/:\/\/|Traceback|Errno|Exception|server replied|Error transferring/i.test(t);
}

// Local time without an offset, like the desktop's Qt::ISODate ("2026-11-02T22:44:48").
export function logTimestamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// The end of a too-long log, starting at a line break so no line is cut in half.
export function trimLog(text: string, keep: number = KEEP_LOG_CHARS): string {
  if (text.length <= keep) return text;
  const tail = text.slice(text.length - keep);
  const nl = tail.indexOf('\n');
  return nl >= 0 ? tail.slice(nl + 1) : tail;
}

// One write at a time, so two errors at once don't interleave or race the trim.
let queue: Promise<void> = Promise.resolve();

export function appendLog(line: string): Promise<void> {
  const entry = `${logTimestamp()} ${String(line).replace(/\r?\n/g, '\n  ')}\n`;
  queue = queue.then(async () => {
    try {
      const path = logPath();
      if (await RNFS.exists(path)) {
        await RNFS.appendFile(path, entry, 'utf8');
        const stat = await RNFS.stat(path);
        if (Number(stat.size) > MAX_LOG_CHARS) {
          await RNFS.writeFile(path, trimLog(await RNFS.readFile(path, 'utf8')), 'utf8');
        }
      } else {
        await RNFS.writeFile(path, entry, 'utf8');
      }
    } catch {
      // Never let logging break the screen that is reporting an error.
    }
  });
  return queue;
}

export async function tailOfLog(maxChars: number = INLINE_LOG_CHARS): Promise<string> {
  await queue;
  try {
    const path = logPath();
    if (!(await RNFS.exists(path))) return '(no log file yet)';
    const all = await RNFS.readFile(path, 'utf8');
    return all.length > maxChars ? all.slice(all.length - maxChars) : all;
  } catch {
    return '(no log file yet)';
  }
}

export function reportBody(tail: string): string {
  return 'Describe what you were doing when this happened:\n\n\n'
    + '----- system -----\n'
    + `Sommet ${APP_VERSION} on Android ${String(Platform.Version)}\n`
    + `Log file: ${logPath()}\n\n`
    + `----- last log lines -----\n${tail}\n`;
}

// Send logs: a mail to André with the last log lines, like the desktop's reportProblem(). No
// mail app on the device: the system share sheet with the same text and the address on top.
export async function reportProblem(context: string): Promise<void> {
  if (context) await appendLog(`REPORTED: ${context}`);
  const body = reportBody(await tailOfLog());
  const subject = 'Sommet problem report';
  const url = `mailto:${REPORT_ADDRESS}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  try {
    await Linking.openURL(url);
  } catch {
    try {
      await Share.share({ title: subject, message: `To: ${REPORT_ADDRESS}\n\n${body}` });
    } catch {
      // The user closed the share sheet, or nothing can share: nothing more to do.
    }
  }
}
