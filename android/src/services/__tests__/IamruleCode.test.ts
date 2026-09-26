// IamruleCode (TS port of tools/iamrule_code.py) against live-compiler outputs, plus byte-for-byte
// parity with the Python patcher (python_* fixtures = tools/iamrule_code.add_lights outputs).
import fx from './iamrule_oracle.fixture.json';
import {
  addLights, callBytes, callSites, CALL_BEEP, CALL_LIGHT, guidanceSites, instructions, layout,
  lightIfStep, splice, validate,
} from '../IamruleCode';
import { lightChoices, stepSequence, withSiUnits } from '../GuidedWorkoutCore';
import type { Workout } from '../WorkoutSource';

const fromHex = (h: string) => Uint8Array.from(h.match(/../g) ?? [], x => parseInt(x, 16));
const B = (k: keyof typeof fx) => fromHex(fx[k] as string);
const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
const LIGHT = callBytes(CALL_LIGHT);
const BEEP = callBytes(CALL_BEEP);

describe('IamruleCode', () => {
  test('every compiler fixture decodes and validates', () => {
    for (const k of Object.keys(fx).filter(k => !k.startsWith('_'))) expect(() => validate(B(k as keyof typeof fx))).not.toThrow();
  });

  test('splice reproduces the compiler byte-exact (even + odd, both directions)', () => {
    const both = B('hand_beep_light_even'); const none = B('hand_no_calls_even');
    const pos = callSites(both, CALL_BEEP)[0];
    const bl = new Uint8Array([...BEEP, ...LIGHT]);
    expect(hex(splice(none, pos, 0, bl))).toBe(hex(both));
    expect(hex(splice(both, pos, 14))).toBe(hex(none));
    const lit = B('hand_light_odd'); const base = B('hand_no_calls_odd');
    const lpos = callSites(lit, CALL_LIGHT)[0];
    expect(hex(splice(base, lpos, 0, LIGHT))).toBe(hex(lit));
    expect(hex(splice(lit, lpos, 7))).toBe(hex(base));
  });

  test('condition block is the compiler\'s own `if (X == a || ...) Suunto.light();`', () => {
    const b = B('hand_light_if_chain');
    const pcs = instructions(b).map(i => i.pc);
    const code = b.subarray(layout(b).code);
    const [l1, l2] = callSites(b, CALL_LIGHT);
    expect(hex(lightIfStep(7, [1, 3, 5], 10))).toBe(hex(code.subarray(pcs[pcs.indexOf(l1) - 12], l1 + 7)));
    expect(hex(lightIfStep(7, [2], 10))).toBe(hex(code.subarray(pcs[pcs.indexOf(l2) - 4], l2 + 7)));
  });

  test('step counter counts the repeat-expanded sequence', () => {
    expect(guidanceSites(B('guidance_2step')).total).toBe(2);
    expect(guidanceSites(B('guidance_3step')).total).toBe(3);
    expect(guidanceSites(B('guidance_repeat_3x2')).total).toBe(8);
  });

  test('matches the Python patcher byte-for-byte', () => {
    expect(hex(addLights(B('guidance_2step_hr'), { onStepStart: [0, 1], onLimits: [], onFinish: true })[0]))
      .toBe(fx.python_all_steps_2hr);
    expect(hex(addLights(B('guidance_repeat_3x2'), { onStepStart: [1, 3, 5], onLimits: [], onFinish: false, expectedTotal: 8 })[0]))
      .toBe(fx.python_repeat_run_only);
    expect(hex(addLights(B('guidance_2step_hr'), { onStepStart: [1], onLimits: [0], onFinish: true, expectedTotal: 2 })[0]))
      .toBe(fx.python_hr_limits_step0);
  });

  test('refuses a step-count mismatch, is idempotent', () => {
    expect(() => addLights(B('guidance_repeat_3x2'), { onStepStart: [0], onLimits: [], onFinish: true, expectedTotal: 4 })).toThrow();
    const [once] = addLights(B('guidance_2step'), { onStepStart: [0, 1], onLimits: [], onFinish: true });
    expect(addLights(once, { onStepStart: [0], onLimits: [], onFinish: true })).toEqual([once, 0]);
  });
});

describe('GuidedWorkoutCore light helpers', () => {
  const hr = (min: number, max: number) => ({ targetName: 'hr', valueRange: { min, max } });
  const wk: Workout = { name: 'W2', steps: [
    { type: { typeName: 'warmup' }, duration: { durationName: 'time', value: 300 }, target: hr(56, 157), notify: { light: false } },
    { type: { typeName: 'repeatStart', value: 3 } },
    { type: { typeName: 'interval' }, duration: { durationName: 'time', value: 90 }, target: hr(158, 166), notify: { light: true, limitLight: true } },
    { type: { typeName: 'recovery' }, duration: { durationName: 'time', value: 120 }, target: { targetName: 'none' } },
    { type: { typeName: 'repeatEnd' } },
  ] };

  test('step sequence expands repeats like the compiled program', () => {
    expect(stepSequence(wk).map(s => s.type.typeName)).toEqual(
      ['warmup', 'interval', 'recovery', 'interval', 'recovery', 'interval', 'recovery']);
  });

  test('light choices: start defaults on, limits only when ticked and targeted', () => {
    expect(lightChoices(wk)).toEqual({ onStepStart: [1, 2, 3, 4, 5, 6], onLimits: [1, 3, 5], onFinish: true, expectedTotal: 7 });
  });

  test('target ranges go to the compiler in SI units, the stored workout is untouched', () => {
    const c = withSiUnits(wk);
    expect(c.steps[0].target!.valueRange).toEqual({ min: 56 / 60, max: 157 / 60 });
    expect(wk.steps[0].target!.valueRange).toEqual({ min: 56, max: 157 });
    const t = (targetName: string, min: number, max: number) =>
      withSiUnits({ steps: [{ type: { typeName: 'interval' }, target: { targetName, valueRange: { min, max } } }] }).steps[0].target!.valueRange!;
    const near = (r: { min: number; max: number }, min: number, max: number) => {
      expect(r.min).toBeCloseTo(min, 9); expect(r.max).toBeCloseTo(max, 9);
    };
    near(t('pace', 5, 6), 0.3, 0.36);             // min/km -> s/m
    near(t('speed', 10, 12), 10 / 3.6, 12 / 3.6); // km/h -> m/s
    near(t('cadence', 80, 90), 80 / 60, 1.5);     // rpm -> rev/s
    near(t('power', 200, 250), 200, 250);
    const d = (durationName: string, value: number) =>
      withSiUnits({ steps: [{ type: { typeName: 'interval' }, duration: { durationName, value } }] }).steps[0].duration!.value;
    expect(d('energy', 50)).toBeCloseTo(209200, 6);   // kcal -> J
    expect(d('hr_above', 150)).toBeCloseTo(2.5, 9);   // bpm -> beats/s
    expect(d('hr_below', 120)).toBeCloseTo(2.0, 9);
    expect(d('time', 60)).toBe(60);
  });
});
