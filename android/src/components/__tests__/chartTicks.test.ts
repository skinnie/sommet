import { niceTicks } from '../chartTicks';

describe('niceTicks', () => {
  it('gives round values inside the range', () => {
    expect(niceTicks(85.4, 93.2).ticks).toEqual([87.5, 90, 92.5]);
    expect(niceTicks(0, 62).ticks).toEqual([0, 25, 50]);
    expect(niceTicks(40, 80).ticks).toEqual([40, 60, 80]);
    expect(niceTicks(48, 71).ticks).toEqual([50, 60, 70]);
  });
  it('uses decimals only when the step needs them', () => {
    const r = niceTicks(2.9, 4.1);
    expect(r.ticks.map(v => v.toFixed(r.decimals))).toEqual(['3.0', '3.5', '4.0']);
    expect(niceTicks(10, 200).decimals).toBe(0);
  });
  it('is empty for a degenerate range', () => {
    expect(niceTicks(5, 5).ticks).toEqual([]);
  });
});
