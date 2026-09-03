import { connect, disconnect, readRegion, writeRegion, isBleTransportActive } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { resolveRegion } from './MemoryMap';
import { generateSource, buildCompileRequest, Workout } from './WorkoutSource';
import { installCompiledApp, InstallState } from './AppInstall';
import { BaseDate, buildTrainingItem, buildTrainingProgram, TrainingItem } from './TrainingProgramCodec';

// EXPERIMENTAL - the two Intervals mechanisms (André 2026-08-14, "ship both"):
//  1) an interval Suunto App: build workout -> App-Zone source -> the user compiles it on
//     Pavel's PUBLIC compiler site in their own browser -> they import the compiled result ->
//     install via the proven App-Zone path (installCompiledApp).
//  2) a native planned move: build the TrainingProgram blob -> write via writeRegion.
//
// IMPORTANT (why there is no automated compile here): the App-Zone workout compiler is run by
// pavel.samokha, a current Suunto employee, who flagged the compiler/format as legally
// sensitive. This app therefore ships NO compiler key and makes NO automated API call - it
// only GENERATES the source and links out to his public site; the user compiles there and
// imports the result. (An earlier version of this file shipped his private x-functions-key and
// POSTed to his function endpoint - removed 2026-08-14. Do not reintroduce either.)
//
// Everything deterministic is proven byte-exact vs the reference tools (WorkoutSource /
// AppsCodec / AppInstall / TrainingProgramCodec tests). The compile step and on-watch result
// are outside our control by design.

// Pavel's PUBLIC compiler site (no key, opened in the user's own browser). NOT the private
// function endpoint.
export const COMPILE_SITE_URL = 'https://ambitapps.z6.web.core.windows.net';

/** The full App-Zone compiler input for a workout (HEADER block + generated source), to hand
 * to the user for pasting into the public compiler site. No network. */
export function buildWorkoutRequest(workout: Workout, name: string): string {
  const [source, ownVars] = generateSource(workout);
  return buildCompileRequest(source, ownVars, name);
}

export interface CompiledApp { binary: Uint8Array; activityId: number; name: string }

// Minimal UTF-8 decoder (Hermes doesn't guarantee TextDecoder). Only used to sniff/parse a
// small JSON compiler-result file; non-JSON binaries fail JSON.parse and fall through to raw.
function utf8ToString(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  while (i < bytes.length) {
    const b = bytes[i++];
    if (b < 0x80) out += String.fromCharCode(b);
    else if (b < 0xe0) out += String.fromCharCode(((b & 0x1f) << 6) | (bytes[i++] & 0x3f));
    else if (b < 0xf0) out += String.fromCharCode(((b & 0x0f) << 12) | ((bytes[i++] & 0x3f) << 6) | (bytes[i++] & 0x3f));
    else {
      const cp = ((b & 0x07) << 18) | ((bytes[i++] & 0x3f) << 12) | ((bytes[i++] & 0x3f) << 6) | (bytes[i++] & 0x3f);
      const c = cp - 0x10000;
      out += String.fromCharCode(0xd800 + (c >> 10), 0xdc00 + (c & 0x3ff));
    }
  }
  return out;
}

/** Parse a compiled app the user imported back from the compiler site. Flexible: accepts the
 * compiler's JSON output ({binary:[...], activityId?, name?}) or a raw binary file (bytes are
 * the bytecode). `fileBase64` is the picked file's raw contents, base64. */
export function parseCompiledApp(fileBase64: string, fallbackName: string): CompiledApp {
  const raw = base64ToBytes(fileBase64);
  // Try JSON first (the compiler site's typical download).
  try {
    const text = utf8ToString(raw).trim();
    if (text.startsWith('{') || text.startsWith('[')) {
      const obj: any = JSON.parse(text);
      const entry = Array.isArray(obj) ? obj[0] : obj;
      if (entry && Array.isArray(entry.binary)) {
        return {
          binary: Uint8Array.from(entry.binary as number[]),
          activityId: (entry.activityId ?? 0) & 0xff,
          name: entry.name ?? fallbackName,
        };
      }
    }
  } catch { /* not JSON - fall through to raw bytes */ }
  // Otherwise treat the file itself as the compiled bytecode.
  return { binary: raw, activityId: 0, name: fallbackName };
}

/** Install an already-compiled interval app (from parseCompiledApp) onto a sport-mode screen,
 * via the proven App-Zone install path. */
export async function installCompiledInterval(
  compiled: CompiledApp, modeIndex: number, displayIndex: number, fieldIndex: number,
  onState: (s: InstallState) => void,
): Promise<boolean> {
  return installCompiledApp(compiled.binary, compiled.activityId, compiled.name, modeIndex, displayIndex, fieldIndex, onState);
}

// ─── native planned move ────────────────────────────────────────────────────────────────
export interface PlannedMoveState {
  phase: 'idle' | 'connecting' | 'writing' | 'verifying' | 'done' | 'error';
  error?: string;
}

/** Write a list of planned moves to the TrainingProgram region (writeRegion, no commit).
 * HARDWARE-CONFIRMED format (2026-09-03, Ambit3 Sport fw 2.4.17): the watch shows the native
 * "Today 1/2" card in TIME mode -> [Next]. `baseDate` is the EARLIEST move's calendar date; each
 * item's dayOffset counts from it. See TrainingProgramCodec.ts for the header signature the
 * firmware validates. */
export async function writePlannedMoves(
  items: TrainingItem[], baseDate: BaseDate, onState: (s: PlannedMoveState) => void,
): Promise<boolean> {
  // Transport (André, 2026-08-17): over BLE the link is already open; the USB connect() would
  // pop the OTG prompt and tear down the BLE session. read/writeRegion + writeCustomModesRaw act
  // on the shared native device either way. Same fix as CustomModesService.
  const overBle = isBleTransportActive();
  onState({ phase: overBle ? 'writing' : 'connecting' });
  if (!overBle) {
    try { await connect(); } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' }); return false;
    }
  }
  try {
    // Only watches that actually declare a TrainingProgram region can take planned moves - the
    // Traverse and Kailash don't have one at all, and writing the hardcoded Ambit3 base to them
    // would hit the wrong flash. Resolve the real base from the watch's own map; refuse if absent.
    const region = await resolveRegion('TrainingProgram');
    if (!region) {
      onState({ phase: 'error', error: 'This watch has no planned-moves (TrainingProgram) region.' });
      return false;
    }
    const blob = buildTrainingProgram(items.map(buildTrainingItem), baseDate);
    if (blob.length > region.size) {
      onState({ phase: 'error', error: 'Planned moves exceed the watch\'s TrainingProgram region.' });
      return false;
    }
    onState({ phase: 'writing' });
    if (!await writeRegion(region.base, bytesToBase64(blob), blob.length)) {
      onState({ phase: 'error', error: 'TrainingProgram write was not acknowledged.' }); return false;
    }
    onState({ phase: 'verifying' });
    const back = base64ToBytes(await readRegion(region.base, blob.length));
    for (let i = 0; i < blob.length; i++) if (back[i] !== blob[i]) {
      onState({ phase: 'error', error: 'TrainingProgram read back different bytes than written.' }); return false;
    }
    onState({ phase: 'done' });
    return true;
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Write failed' });
    return false;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
