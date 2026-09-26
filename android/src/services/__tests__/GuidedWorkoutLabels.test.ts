import { withDefaultLabels } from '../GuidedWorkoutCore';

// Same rule as guided_workout.py with_default_labels: a step without text shows its type's word.
test('withDefaultLabels fills only empty real steps', () => {
  const wk: any = { name: 'W', steps: [
    { type: { typeName: 'warmup' }, duration: { durationName: 'time', value: 60 } },
    { type: { typeName: 'repeatStart', value: 2 } },
    { type: { typeName: 'interval' }, duration: { durationName: 'time', value: 30 }, text: 'Fast' },
    { type: { typeName: 'recovery' }, duration: { durationName: 'time', value: 30 }, text: '  ' },
    { type: { typeName: 'repeatEnd' } },
  ] };
  const out = withDefaultLabels(wk);
  expect(out.steps.map((s: any) => s.text)).toEqual(['Warmup', undefined, 'Fast', 'Recovery', undefined]);
  expect(wk.steps[0].text).toBeUndefined();  // the stored plan is left alone
});
