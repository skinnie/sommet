import * as Ble from './MageneBle';
import { concat, le16, le32, sleep } from './MageneBle';

// Shared pieces of the C406 file transfer (routes + workouts) - ports of magene_route.py's
// protobuf helpers, `_packets` and `transfer_file`. See that file for the format notes.

// ---- minimal protobuf wire encoding (non-negative varints; zigzag for sint32) ----------------
export function varint(n: number): number[] {
  if (n < 0) throw new Error('negative int32 varints are not used by the C406 formats');
  const out: number[] = [];
  let v = Math.floor(n);
  for (;;) {
    const b = v % 128;
    v = Math.floor(v / 128);
    if (v) out.push(b | 0x80);
    else { out.push(b); return out; }
  }
}
const tag = (field: number, wire: number) => varint(field * 8 + wire);
export const pbInt32 = (field: number, value: number) => [...tag(field, 0), ...varint(Math.trunc(value))];
export function pbSint32(field: number, value: number): number[] {
  const n = Math.trunc(value) | 0;
  const zz = ((n << 1) ^ (n >> 31)) >>> 0;
  return [...tag(field, 0), ...varint(zz)];
}
export const pbMsg = (field: number, body: number[] | Uint8Array) =>
  [...tag(field, 2), ...varint(body.length), ...Array.from(body)];

// ---- packets + transfer ---------------------------------------------------------------------
const MAGIC = [0xa5, 0x5a, 0x5a, 0xa5];

export function packets(file: Uint8Array, mtu: number): Uint8Array[] {
  const chunk = Math.max(20, mtu - 17);
  const n = Math.ceil(file.length / chunk);
  const out: Uint8Array[] = [];
  for (let i = 0; i < n; i++) {
    const part = file.subarray(i * chunk, (i + 1) * chunk);
    out.push(concat(MAGIC, le32(file.length), le16(n), le16(i + 1), [part.length], part));
  }
  return out;
}

/** Info command on CC02, packets on CC03 paced by the device's `40 8c 00 <n>` credit grants
 *  (ungated if it never grants any), then TransFormEnd `40 52`. */
export async function transferFile(info: Uint8Array, pkts: Uint8Array[]): Promise<{ error: string | null; packetsSent: number }> {
  let credit = 0;
  let error: string | null = null;
  const off = Ble.onNotify((ch, b) => {
    if (ch !== 'cc02' || b.length < 2 || b[1] !== 0x8c) return;
    const status = b.length > 2 ? b[2] : 0;
    if (status !== 0) { error = `device nack (0x8c status ${status})`; return; }
    credit += b.length > 3 ? b[3] : 1;
  });
  try {
    await Ble.write('cc02', info, true);
    await sleep(1500);
    let ungated = credit === 0;
    let pos = 0;
    let stalled = 0;
    while (pos < pkts.length && error === null) {
      if (ungated || pos < credit) {
        await Ble.write('cc03', pkts[pos], false);
        pos++;
        stalled = 0;
        await sleep(15);
      } else {
        await sleep(50);
        stalled += 50;
        if (stalled > 8000) ungated = true;
      }
    }
    await sleep(300);
    await Ble.write('cc02', new Uint8Array([0x40, 0x52]), true);
    await sleep(600);
    return { error, packetsSent: pos };
  } finally {
    off();
  }
}
