import { fmtDate } from '../i18n';

// The Health number tiles (André, 2026-11-02, desktop picks #12): how old the latest value is,
// and how it compares with the 7 days before it. Same rules and words as desktop HealthPage.qml
// (oldBadge / trend / tileCaption).

export interface Point { date: string; value: number }

function day(iso: string): Date {
  return new Date(String(iso).slice(0, 10) + 'T00:00:00');
}

// "5 days old" when the latest value is more than 2 days old; '' otherwise.
export function oldBadge(series: Point[], now: Date = new Date()): string {
  if (!series || series.length === 0) return '';
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const age = Math.round((today.getTime() - day(series[series.length - 1].date).getTime()) / 86400000);
  return age > 2 ? `${age} days old` : '';
}

// "▲ 4 bpm vs 7-day average": the latest value against the mean of the readings from the 7 days
// before it. Needs at least 3 of them; otherwise ''. `series` is oldest first.
export function trend(series: Point[], unit: string, decimals = 0): string {
  if (!series || series.length < 4) return '';
  const last = series[series.length - 1];
  const lastDay = day(last.date).getTime();
  let sum = 0;
  let n = 0;
  for (let i = series.length - 2; i >= 0; --i) {
    const days = Math.round((lastDay - day(series[i].date).getTime()) / 86400000);
    if (days > 7) break;
    if (days >= 1) { sum += series[i].value; n++; }
  }
  if (n < 3) return '';
  const diff = last.value - sum / n;
  const size = Math.abs(diff).toFixed(decimals);
  if (Number(size) === 0) return 'Same as the 7-day average';
  return (diff > 0 ? '▲ ' : '▼ ') + size + (unit ? ' ' + unit : '') + ' vs 7-day average';
}

// The tile caption: the trend when there is one, else the date of the value.
export function tileCaption(series: Point[], unit: string, decimals = 0): string {
  const t = trend(series, unit, decimals);
  if (t) return t;
  const last = series[series.length - 1];
  return last ? (fmtDate(last.date) || last.date) : '';
}
