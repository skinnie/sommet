// Kailash settings writes on Android (watch-protocol/10 K7, 2026-11-26): one entry, nothing else,
// as the desktop's tools/settings_write.py _write_single_entry, which André's Kailash merged over
// the cable on 2026-08-10. Before this, Android wrote the whole settings blob back, which also
// re-sent the paired phone's bond record (entry 0x35, keys included). All bytes here are made up.
// Jest's node environment provides Buffer at runtime; the RN tsconfig has no @types/node.
const mockBuffer = (globalThis as any).Buffer;
let mockWatch: Uint8Array;
const mockWrites: Uint8Array[] = [];

jest.mock('../../native/AmbitUsbModule', () => ({
  readSettingsRaw: jest.fn(async () => mockBuffer.from(mockWatch).toString('base64')),
  writeSettingsRaw: jest.fn(async (b64: string) => {
    const sent = new Uint8Array(mockBuffer.from(b64, 'base64'));
    mockWrites.push(sent);
    mockWatch = mockMerge(mockWatch, sent);              // the Kailash merges a partial write (2026-08-10)
  }),
}));


import { KAILASH_SETTINGS_FIELDS } from '../AmbitSettingsReader';
import { splitEntries, encodeEntry, writeSetting } from '../AmbitSettingsWriter';

const MAGIC = Array.from('SBEM0102', c => c.charCodeAt(0));

function build(entries: Array<[number, number[]]>): Uint8Array {
  const out: number[] = [0, 0, 0, 0, 1, 0, ...MAGIC];
  for (const [id, data] of entries) out.push(...Array.from(encodeEntry(id, new Uint8Array(data))));
  return new Uint8Array(out);
}

function mockMerge(current: Uint8Array, sent: Uint8Array): Uint8Array {
  const cur = splitEntries(current)!;
  const upd = new Map(splitEntries(sent)!.entries.map(([id, d]) => [id, Array.from(d)]));
  return build(cur.entries.map(([id, d]) => [id, upd.get(id) ?? Array.from(d)]));
}

const i32 = (v: number) => [v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >> 24) & 0xff];
const BOND = [0x00, 0xf0, 0x06, 0xff, 0, ...Array.from('aa:bb:cc:dd:ee:ff\0', c => c.charCodeAt(0)),
  ...Array.from('11:22\0', c => c.charCodeAt(0)), 0, 0, 0, 0];   // a made-up bond record

beforeEach(() => {
  mockWrites.length = 0;
  const entries: Array<[number, number[]]> = [];
  for (let id = 0x01; id <= 0x27; id++) entries.push([id, id === 0x05 ? [0, 0, 0, 0] : [0]]);
  entries.push([0x35, BOND]);
  entries.push([0x36, [...i32(451234567), ...i32(61234567)]]);   // made-up home, 45.12 / 6.12
  mockWatch = build(entries);
});

describe('Kailash settings', () => {
  it("lists the 7R app's settings and nothing else", () => {
    // The 7R app's settings screen (assets/APK/kailash/IMG_2741/2742), the NotificationsOff its
    // captures push, and display_dark (seen switching on the watch). The descriptor's other
    // fields (declination, views, shortcuts...) were removed on 2026-11-26: the 7R app never
    // shows or writes them, and the watch has no menu for the declination.
    expect(Object.fromEntries(KAILASH_SETTINGS_FIELDS.map(f => [f.key, f.entryId]))).toEqual({
      date_format: 0x01, tones: 0x02, vibration: 0x03, units_mode: 0x07, language: 0x08,
      time_format: 0x09, display_contrast: 0x0f, backlight_mode: 0x10, backlight_brightness: 0x11,
      storm_alarm: 0x17, display_dark: 0x27, home_latitude: 0x36, home_longitude: 0x36,
      notifications_off: 0x13,
    });
  });

  it('writes one entry, never the bond record, and confirms it by a re-read', async () => {
    const r = await writeSetting('notifications_off', 1, KAILASH_SETTINGS_FIELDS, 'kailash');
    expect(r.ok).toBe(true);
    expect(mockWrites).toHaveLength(1);
    const sent = splitEntries(mockWrites[0])!;
    expect(sent.entries.map(([id]) => id)).toEqual([0x13]);
    expect(Array.from(sent.prefix)).toEqual([0, 0, 0, 0, 1, 0]);   // the read's own header
    expect(Array.from(sent.entries[0][1])).toEqual([1]);
  });

  it('sends the whole home-location entry when one of its two values changes', async () => {
    const r = await writeSetting('home_longitude', 7.5, KAILASH_SETTINGS_FIELDS, 'kailash');
    expect(r.ok).toBe(true);
    const sent = splitEntries(mockWrites[0])!;
    expect(sent.entries.map(([id]) => id)).toEqual([0x36]);
    expect(Array.from(sent.entries[0][1])).toEqual([...i32(451234567), ...i32(75000000)]);
  });
});
