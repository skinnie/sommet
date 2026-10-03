// Round values for a chart's vertical scale (André, 2026-10-03: the Health, Weight and Coach
// graphs showed "a line with no values on the vertical axis"). Returns 3-5 ticks inside
// [min, max] on the largest 1 / 2 / 2.5 / 5 x 10^n step that still gives three, and how many
// decimals they need.
export function niceTicks(min: number, max: number): { ticks: number[]; decimals: number } {
  if (!isFinite(min) || !isFinite(max) || max <= min) return { ticks: [], decimals: 0 };
  const mag = Math.pow(10, Math.floor(Math.log10(max - min)));
  for (const m of [5, 2.5, 2, 1, 0.5, 0.25, 0.2, 0.1]) {
    const step = m * mag;
    const ticks: number[] = [];
    for (let k = Math.ceil(min / step - 1e-9); k * step <= max + step * 1e-9; k++)
      ticks.push(Number((k * step).toPrecision(12)));
    if (ticks.length >= 3) {
      let decimals = 0;
      while (decimals < 4 && Math.abs(step * Math.pow(10, decimals) - Math.round(step * Math.pow(10, decimals))) > 1e-9)
        decimals++;
      return { ticks, decimals };
    }
  }
  return { ticks: [], decimals: 0 };
}
