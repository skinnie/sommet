import * as Ble from './MageneBle';
import { concat, le16, le32, u16, u32, fitCrc16 } from './MageneBle';

// Magene C406 device operations - the TypeScript port of tools/magene_import.py (ride list +
// download) and tools/magene_device.py (battery, info, clock, altitude, profile, settings).
// Same frames, same checks; see those files' docstrings for the protocol notes.

const BATTERY = '00002a19-0000-1000-8000-00805f9b34fb';
const DEVINFO: Record<string, string> = {
  '00002a29-0000-1000-8000-00805f9b34fb': 'manufacturer',
  '00002a24-0000-1000-8000-00805f9b34fb': 'model',
  '00002a25-0000-1000-8000-00805f9b34fb': 'serial',
  '00002a26-0000-1000-8000-00805f9b34fb': 'firmware',
  '00002a27-0000-1000-8000-00805f9b34fb': 'software',
  '00002a28-0000-1000-8000-00805f9b34fb': 'hardware',
};

/** Connect (bonding on first use), run `fn`, always disconnect. */
export async function withMagene<T>(address: string, fn: (mtu: number) => Promise<T>): Promise<T> {
  const { mtu } = await Ble.connect(address);
  try {
    return await fn(mtu);
  } finally {
    await Ble.disconnect().catch(() => undefined);
  }
}

// ---- rides (magene_import.py) --------------------------------------------------------------

/** The ride file name the desktop uses: the ride id is its UTC start as a Unix timestamp. */
export function rideName(rideId: number): string {
  const d = new Date(rideId * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}-${p(d.getUTCHours())}-${p(d.getUTCMinutes())}-${p(d.getUTCSeconds())}.fit`;
}

export async function rideList(): Promise<number[]> {
  const rides: number[] = [];
  const seen = new Set<number>();
  let cursor = 0;
  for (;;) {
    if (seen.has(cursor)) break;
    seen.add(cursor);
    const msg = await Ble.command(new Uint8Array([0x40, 0x49, ...le32(cursor)]), [0x40, 0x49], 10000);
    if (!msg || msg.length < 5 || msg[2] !== 0) break;
    const more = msg[4];
    // Like the OneLap app: every 4-byte id from byte 5 to the end of the packet; byte 4 != 0 = more
    // pages, next cursor = the page's last id (BikeComputerRecordUpload.handler_read_next_page).
    const ids: number[] = [];
    for (let i = 5; i + 4 <= msg.length; i += 4) ids.push(u32(msg, i));
    rides.push(...ids);
    if (!more || ids.length === 0) break;
    cursor = ids[ids.length - 1];
  }
  return rides;
}

/** Reassemble a ride from CC03 chunks and verify both FIT CRCs (magene_import._reassemble_fit). */
export function reassembleFit(chunks: Uint8Array[]): Uint8Array | null {
  const sorted = [...chunks].sort((a, b) => u16(a, 10) - u16(b, 10));
  const stream = concat(...sorted.map(c => c.subarray(13)));
  if (stream.length < 14 || String.fromCharCode(...stream.subarray(8, 12)) !== '.FIT') return null;
  const headerLen = stream[0];
  const dataSize = u32(stream, 4);
  const total = headerLen + dataSize + 2;
  if (stream.length < total) return null;
  const fit = stream.slice(0, total);
  if (fitCrc16(fit, 0, 12) !== u16(fit, 12)) return null;
  if (fitCrc16(fit, 0, headerLen + dataSize) !== u16(fit, headerLen + dataSize)) return null;
  return fit;
}

export async function downloadRide(rideId: number, timeoutMs = 20000): Promise<Uint8Array | null> {
  const chunks: Uint8Array[] = [];
  let finish!: (ok: boolean) => void;
  const done = new Promise<boolean>(r => { finish = r; });
  const off = Ble.onNotify((ch, b) => {
    if (ch !== 'cc03' || b.length < 13 || u32(b, 0) !== rideId) return;
    chunks.push(b);
    if (u32(b, 4) === 0) finish(true);
  });
  const timer = setTimeout(() => finish(false), timeoutMs);
  try {
    const ack = await Ble.command(new Uint8Array([0x40, 0x4a, ...le32(rideId)]), [0x40, 0x4a], 10000);
    if (!ack || ack.length < 3 || ack[2] !== 0) return null;
    if (!(await done)) return null;
    return reassembleFit(chunks);
  } finally {
    clearTimeout(timer);
    off();
  }
}

// ---- device control (magene_device.py) ------------------------------------------------------

export async function readBattery(): Promise<number | null> {
  try { return (await Ble.read(BATTERY))[0]; } catch { return null; }
}

export async function readInfo(): Promise<Record<string, string | null>> {
  const out: Record<string, string | null> = {};
  for (const [uuid, name] of Object.entries(DEVINFO)) {
    try {
      const v = await Ble.read(uuid);
      out[name] = String.fromCharCode(...v).replace(/\0+$/, '').trim();
    } catch { out[name] = null; }
  }
  return out;
}

export async function setTime(): Promise<boolean> {
  const now = Math.floor(Date.now() / 1000);
  return (await Ble.command(new Uint8Array([0x40, 0x4e, ...le32(now)]), [0x40, 0x4e])) !== null;
}

/** Local UTC offset in seconds, DST included (tm_gmtoff on the desktop). */
export function localOffsetSeconds(): number {
  return -new Date().getTimezoneOffset() * 60;
}

export async function setTimezone(offsetSeconds = localOffsetSeconds()): Promise<boolean> {
  const hours = Math.round(offsetSeconds / 3600) & 0xff;
  const ok = await Ble.command(new Uint8Array([0x40, 0x4f, hours]), [0x40, 0x4f]);
  const off = await Ble.command(new Uint8Array([0x40, 0x57, ...le32(offsetSeconds >>> 0)]), [0x40, 0x57]);
  return ok !== null || off !== null;
}

export async function altitudeCorrect(): Promise<boolean> {
  return (await Ble.command(new Uint8Array([0x40, 0x55]), [0x40, 0x55], 8000)) !== null;
}

export interface MageneProfile {
  sex: number; age: number; height: number; maxHr: number; lthr: number;
  ftp: number; bikeWeight: number; weight: number;
}

export async function readProfile(): Promise<MageneProfile | null> {
  const a = await Ble.command(new Uint8Array([0x40, 0x40]), [0x40, 0x40]);
  if (!a || a.length < 14) return null;
  const b = a.subarray(3);
  return {
    sex: b[0], age: b[1], height: b[2], maxHr: b[3], lthr: b[4],
    ftp: u16(b, 5), bikeWeight: u16(b, 7) / 100, weight: u16(b, 9) / 100,
  };
}

/** Change only the given fields (the device stores the whole struct - read, merge, write). */
export async function setProfile(changes: Partial<MageneProfile>): Promise<MageneProfile | null> {
  const cur = await readProfile();
  if (!cur) return null;
  const m = { ...cur, ...Object.fromEntries(Object.entries(changes).filter(([, v]) => v != null)) } as MageneProfile;
  const bw = Math.round((m.bikeWeight > 0 ? m.bikeWeight : 10) * 100);
  const frame = new Uint8Array([
    0x40, 0x41, m.sex & 0xff, m.age & 0xff, m.height & 0xff, m.maxHr & 0xff, m.lthr & 0xff,
    ...le16(m.ftp & 0xffff), ...le16(bw & 0xffff), ...le16(Math.round(m.weight * 100) & 0xffff),
  ]);
  return (await Ble.command(frame, [0x40, 0x41])) ? m : null;
}

// ---- function settings (magene_device.py read-settings / set-settings) -------------------------

type Fmt = 'B' | 'H' | 'I';
const SIZE: Record<Fmt, number> = { B: 1, H: 2, I: 4 };

function settingsLayout(n: number): [string, number, Fmt][] | null {
  if (n <= 19) return null;
  const lay: [string, number, Fmt][] = [
    ['timezoneOffset', 3, 'I'],
    ['autoBacklight', 7, 'B'], ['backlightDuration', 8, 'H'], ['backlightLevel', 10, 'B'],
    ['autoOff', 11, 'B'], ['autoPause', 12, 'B'], ['promptTone', 13, 'B'],
    ['keyTone', 14, 'B'], ['startReminder', 15, 'B'], ['estimatedPower', 16, 'B'],
    ['hrAlert', 17, 'B'], ['powerAlert', 18, 'H'],
  ];
  if (n >= 24) lay.push(['autoLap', 20, 'B'], ['autoLapType', 21, 'B'], ['autoLapValue', 22, 'H']);
  return lay;
}

const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, i) => a + i);
const ONOFF = [0, 1];
export const SETTINGS_ALLOWED: Record<string, number[]> = {
  autoBacklight: ONOFF,
  backlightDuration: [0, 5, 10, 15, 30, 60],
  backlightLevel: [0, 1, 2],
  autoOff: [0, 5, 10, 15, 20, 30, 40, 60],
  autoPause: range(0, 10),
  promptTone: ONOFF, keyTone: ONOFF, startReminder: ONOFF, estimatedPower: ONOFF,
  hrAlert: [0, ...range(100, 240)],
  powerAlert: [0, ...range(100, 2500)],
  autoLap: ONOFF, autoLapType: ONOFF,
  autoLapValue: range(1, 1000),
};

function readField(b: Uint8Array, off: number, fmt: Fmt) {
  return fmt === 'B' ? b[off] : fmt === 'H' ? u16(b, off) : u32(b, off);
}

export function decodeSettings(reply: Uint8Array): Record<string, number> | null {
  const lay = settingsLayout(reply.length);
  if (!lay) return null;
  const out: Record<string, number> = {};
  for (const [name, off, fmt] of lay) out[name] = readField(reply, off, fmt);
  return out;
}

async function readSettingsRaw(): Promise<Uint8Array | null> {
  const a = await Ble.command(new Uint8Array([0x40, 0x4c]), [0x40, 0x4c]);
  return a && a.length >= 4 && a[2] === 0 ? a : null;
}

export async function readSettings(): Promise<Record<string, number> | null> {
  const raw = await readSettingsRaw();
  return raw ? decodeSettings(raw) : null;
}

/** Patch `changes` into the block read from the device; every name, value and offset is checked
 *  before a byte changes (throws on anything unknown / out of range). */
export function patchSettings(reply: Uint8Array, changes: Record<string, number>): Uint8Array {
  const lay = settingsLayout(reply.length);
  if (!lay) throw new Error(`unsupported settings block (${reply.length}-byte reply)`);
  const where = new Map(lay.map(([n, o, f]) => [n, [o, f] as [number, Fmt]]));
  const block = reply.slice(3);
  for (const [name, raw] of Object.entries(changes)) {
    const allowed = SETTINGS_ALLOWED[name];
    const w = where.get(name);
    if (!allowed || !w) throw new Error(`${name} is not a writable setting on this device`);
    const v = Math.round(raw);
    if (!allowed.includes(v)) throw new Error(`${name}=${v} is outside what the device accepts`);
    const [off, fmt] = w;
    const bo = off - 3;
    if (bo < 0 || bo + SIZE[fmt] > block.length) throw new Error(`${name} offset outside the block`);
    const bytes = fmt === 'B' ? [v & 0xff] : fmt === 'H' ? le16(v) : le32(v);
    block.set(bytes, bo);
  }
  return block;
}

export async function writeSettings(changes: Record<string, number>): Promise<Record<string, number> | null> {
  const raw = await readSettingsRaw();
  if (!raw) throw new Error('Could not read the current settings');
  const block = patchSettings(raw, changes);
  const ack = await Ble.command(concat([0x40, 0x4d], block), [0x40, 0x4d]);
  if (!ack || ack.length < 3 || ack[2] !== 0) throw new Error('The C406 refused the change');
  return readSettings();
}
