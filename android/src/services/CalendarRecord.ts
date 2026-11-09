// Which dated workouts this tablet put on each watch, like the desktop's
// ~/SommetBackups/calendar/installed-<serial>.json. The tablet and the PC keep separate plans;
// with this record each sync erases only its own workouts and past ones, and leaves the other
// device's upcoming ones (TrainingCalendarCore.leftToOthers, André 2026-11-01).
// Kept in the app's private files under calendar/installed-<serial>.json, a list of labels.

import RNFS from 'react-native-fs';

export const CALENDAR_DIR = `${RNFS.DocumentDirectoryPath}/calendar`;

const safe = (s: string) => s.replace(/[^A-Za-z0-9]/g, '_');

export function recordPath(serial: string): string {
  return `${CALENDAR_DIR}/installed-${safe(serial)}.json`;
}

/** This tablet's workouts on watch `serial`; empty when the watch is unknown or nothing is
 * recorded yet (then nothing upcoming counts as its own, and nothing upcoming is erased unless
 * it is in the plan). */
export async function loadInstalled(serial: string): Promise<Set<string>> {
  if (!serial) return new Set();
  try {
    const data = JSON.parse(await RNFS.readFile(recordPath(serial), 'utf8'));
    return new Set(Array.isArray(data) ? data.filter((x) => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

export async function saveInstalled(serial: string, labels: string[]): Promise<void> {
  if (!serial) return;
  await RNFS.mkdir(CALENDAR_DIR);
  await RNFS.writeFile(recordPath(serial), JSON.stringify(labels), 'utf8');
}
