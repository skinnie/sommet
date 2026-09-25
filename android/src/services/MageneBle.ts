import { NativeModules, NativeEventEmitter, EmitterSubscription } from 'react-native';
import { base64ToBytes, bytesToBase64 } from './Base64';

// Magene C406 BLE transport - wraps the thin native `MageneBle` module (MageneBleModule.kt) the
// way the desktop's tools wrap bleak: connect (bonds), write CC02/CC03, read a characteristic,
// and CC02/CC03 notifications. The protocol itself is in MageneDevice.ts / MageneRoute.ts /
// MageneWorkout.ts, ports of tools/magene_*.py.
//
// Android runs one GATT op at a time, so every native call goes through `serial()`.

const Native = (NativeModules as any).MageneBle as
  | {
      scan(ms: number): Promise<MageneScanResult[]>;
      connect(address: string): Promise<{ mtu: number; bonded: boolean }>;
      write(ch: 'cc02' | 'cc03', b64: string, withResponse: boolean): Promise<void>;
      read(uuid: string): Promise<string>;
      getMtu(): Promise<number>;
      disconnect(): Promise<void>;
    }
  | undefined;

export interface MageneScanResult { address: string; name: string; rssi: number }

export function isMageneAvailable(): boolean {
  return !!Native;
}

const emitter = Native ? new NativeEventEmitter(Native as any) : null;

let chain: Promise<unknown> = Promise.resolve();
function serial<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn);
  chain = next.catch(() => undefined);
  return next;
}

export type NotifyHandler = (ch: 'cc02' | 'cc03', data: Uint8Array) => void;

/** Subscribe to CC02/CC03 notifications; returns the unsubscribe function. */
export function onNotify(handler: NotifyHandler): () => void {
  if (!emitter) return () => undefined;
  const sub: EmitterSubscription = emitter.addListener('MageneNotify', (e: { ch: 'cc02' | 'cc03'; data: string }) =>
    handler(e.ch, base64ToBytes(e.data)),
  );
  return () => sub.remove();
}

function need() {
  if (!Native) throw new Error('The Magene module is missing from this build');
  return Native;
}

export const scan = (ms = 6000) => need().scan(ms);
export const connect = (address: string) => serial(() => need().connect(address));
export const disconnect = () => serial(() => need().disconnect());
export const getMtu = () => need().getMtu();
export const write = (ch: 'cc02' | 'cc03', data: Uint8Array, withResponse = true) =>
  serial(() => need().write(ch, bytesToBase64(data), withResponse));
export const read = async (uuid: string) => base64ToBytes(await serial(() => need().read(uuid)));

export const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

/** Write a CC02 frame and return the first CC02 notification starting with `prefix` (or null
 *  after `timeoutMs`) - magene_device._cmd_once. The listener is attached before the write. */
export async function command(frame: Uint8Array, prefix: number[] | null, timeoutMs = 5000): Promise<Uint8Array | null> {
  let resolve!: (v: Uint8Array | null) => void;
  const reply = new Promise<Uint8Array | null>(r => { resolve = r; });
  const off = onNotify((ch, d) => {
    if (ch !== 'cc02') return;
    if (!prefix || prefix.every((b, i) => d[i] === b)) resolve(d);
  });
  const timer = setTimeout(() => resolve(null), timeoutMs);
  try {
    await write('cc02', frame, true);
    return await reply;
  } finally {
    clearTimeout(timer);
    off();
  }
}

// Little-endian helpers shared by the Magene modules.
export function u16(b: Uint8Array, o: number) { return b[o] | (b[o + 1] << 8); }
export function u32(b: Uint8Array, o: number) { return (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0; }
export function le16(v: number) { return [v & 0xff, (v >>> 8) & 0xff]; }
export function le32(v: number) { return [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]; }
export function concat(...parts: (Uint8Array | number[])[]): Uint8Array {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p as any, o); o += p.length; }
  return out;
}

/** FIT SDK CRC-16 (magene_import._fit_crc16). */
export function fitCrc16(data: Uint8Array, start = 0, end = data.length): number {
  const t = [0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
             0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400];
  let crc = 0;
  for (let i = start; i < end; i++) {
    const byte = data[i];
    let tmp = t[crc & 0xf];
    crc = (crc >> 4) & 0x0fff;
    crc = crc ^ tmp ^ t[byte & 0xf];
    tmp = t[crc & 0xf];
    crc = (crc >> 4) & 0x0fff;
    crc = crc ^ tmp ^ t[(byte >> 4) & 0xf];
  }
  return crc;
}
