// Byte-exact proof that the TS TrainingProgram builder matches the Python
// tools/training_program.py builder, whose output is HARDWARE-CONFIRMED (2026-09-03, Ambit3
// Sport fw 2.4.17: the native "Today" planned-move card displays). The fixture is regenerated
// from the Python builder; this guards against a TS-vs-Python divergence.

import { buildTrainingItem, buildTrainingProgram } from '../TrainingProgramCodec';
import fx from './trainingprogram_fixture.json';

declare const Buffer: { from(data: string, encoding: string): Uint8Array };
const b64 = (s: string) => new Uint8Array(Buffer.from(s, 'base64'));

function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

describe('TrainingProgramCodec', () => {
  test('each 40-byte item matches the Python builder', () => {
    for (const it of fx.items) {
      const got = buildTrainingItem({
        activityId: it.activityId, durationMinutes: it.durationMinutes, intensity: it.intensity,
        name: it.name, dayOffset: it.dayOffset, distance: (it as any).distance,
        completed: (it as any).completed, moveId: (it as any).moveId,
      });
      expect(got.length).toBe(40);
      expect(bytesEqual(got, b64(it.bytes))).toBe(true);
    }
  });

  test('the full program blob (header + items) matches the Python builder', () => {
    const items = fx.items.map(it => buildTrainingItem({
      activityId: it.activityId, durationMinutes: it.durationMinutes, intensity: it.intensity,
      name: it.name, dayOffset: it.dayOffset, distance: (it as any).distance,
      completed: (it as any).completed, moveId: (it as any).moveId,
    }));
    const got = buildTrainingProgram(items, fx.baseDate);
    // header: [u16 year][u8 month][u8 day][3C 46 50 5A][u16 count][u16 0xFFFF] - the signature is
    // what the watch firmware validates (hardware-confirmed 2026-09-03), so pin it explicitly too.
    expect(Array.from(got.slice(4, 8))).toEqual([0x3c, 0x46, 0x50, 0x5a]);
    expect(Array.from(got.slice(10, 12))).toEqual([0xff, 0xff]);
    expect(bytesEqual(got, b64(fx.program))).toBe(true);
  });
});
