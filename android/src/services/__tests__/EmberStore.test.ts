jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
import { applyLog, EmberData } from '../EmberStore';

// A logged meal ends an active fast (issue #20: the meal sheet warns first) - same rule as the
// desktop backend's _handle_ember_log, checked there against a throwaway HOME, 2026-09-27.
describe('meal', () => {
  it('logs kcal/macros and ends the fast when breaksFast', () => {
    let d: EmberData = { entries: [], fasts: [], deleted: [] };
    d = applyLog(d, { type: 'fast-start', goalHours: 16 });
    expect(d.fasts[0].end).toBeNull();
    d = applyLog(d, { type: 'meal', name: 'Skyr naturel (150 g)', kcal: 93, protein: 17, carbs: 6, fat: 0, breaksFast: true });
    expect(d.entries[0]).toMatchObject({ type: 'meal', kcal: 93, protein: 17, carbs: 6, fat: 0 });
    expect(d.fasts[0].end).not.toBeNull();
  });
  it('leaves the fast alone without breaksFast (older callers)', () => {
    let d: EmberData = { entries: [], fasts: [], deleted: [] };
    d = applyLog(d, { type: 'fast-start' });
    d = applyLog(d, { type: 'meal', name: 'x', kcal: 10 });
    expect(d.fasts[0].end).toBeNull();
  });
});
