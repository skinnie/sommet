import React, { useCallback, useMemo, useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { ActivityRecord, getAllActivities } from '../database/db';
import { colorForName } from '../services/ActivityColors';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { t, dateLocale, fmtDate } from '../i18n';

// Calendar - port of desktop/qml/CalendarPage.qml (real request, 2026-08-11, with a reference
// screenshot: a month grid where each day carries a coloured dot for the activity recorded
// that day, sized by how much was done, a small grey dot for a rest day, today picked out).
// Uses the same per-sport colour resolution (colorForName -> ActivityColors, the TS port of
// desktop's ActivityTypes.forName) as the Totals port, so a mixed-device history reads as one
// consistent calendar rather than one look per device. Derived entirely from the local DB,
// like the desktop page - no device traffic.

const MIN_DOT = 26;
const MAX_DOT = 36;

function activityDate(a: ActivityRecord): Date | null {
  if (!a.date) return null;
  const d = new Date(a.date);
  return isNaN(d.getTime()) ? null : d;
}

export default function CalendarScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  const today = useMemo(() => new Date(), []);
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth()); // 0-11
  // The month's activities are listed under the grid (André, 2026-10-04: the screen used the top
  // of a tablet and left the rest blank). Tapping a day narrows the list to it; again clears.
  const navigation = useNavigation<any>();
  const [selectedDay, setSelectedDay] = useState<number | null>(null);
  const [activities, setActivities] = useState<ActivityRecord[]>([]);

  useFocusEffect(useCallback(() => {
    getAllActivities().then(setActivities).catch(() => {});
  }, []));

  function goPrevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1); } else setViewMonth(m => m - 1);
  }
  function goNextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1); } else setViewMonth(m => m + 1);
  }
  React.useEffect(() => { setSelectedDay(null); }, [viewYear, viewMonth]);
  function goToday() { setViewYear(today.getFullYear()); setViewMonth(today.getMonth()); }

  const isCurrentMonth = viewYear === today.getFullYear() && viewMonth === today.getMonth();

  const monthActivities = useMemo(() => activities.filter(a => {
    const d = activityDate(a);
    return d && d.getFullYear() === viewYear && d.getMonth() === viewMonth;
  }), [activities, viewYear, viewMonth]);

  // Activities bucketed by day-of-month, and the month's busiest day (by total duration) -
  // the yardstick every dot is scaled against, so "biggest dot" always means "most time
  // spent" within the month on screen, the same relative read as the reference.
  const { byDay, maxDaySeconds } = useMemo(() => {
    const map: Record<number, ActivityRecord[]> = {};
    for (const a of monthActivities) {
      const d = activityDate(a)!;
      (map[d.getDate()] ||= []).push(a);
    }
    let max = 0;
    for (const key of Object.keys(map)) {
      const secs = map[Number(key)].reduce((s, a) => s + (a.duration_s || 0), 0);
      if (secs > max) max = secs;
    }
    return { byDay: map, maxDaySeconds: max };
  }, [monthActivities]);

  function dotSizeFor(daySeconds: number): number {
    if (maxDaySeconds <= 0) return MIN_DOT;
    const ratio = Math.min(1, daySeconds / maxDaySeconds);
    return Math.round(MIN_DOT + ratio * (MAX_DOT - MIN_DOT));
  }

  // Monday-first 7-wide grid, padded to full weeks with null cells (blank, not bleeding the
  // adjacent months in) - identical to desktop's weeks builder.
  const weeks = useMemo(() => {
    const first = new Date(viewYear, viewMonth, 1);
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const leading = (first.getDay() + 6) % 7; // JS getDay 0=Sun -> Monday=col 0
    type Cell = { day: number; isToday: boolean; activities: ActivityRecord[]; dotSize: number } | null;
    const cells: Cell[] = [];
    for (let i = 0; i < leading; i++) cells.push(null);
    for (let day = 1; day <= daysInMonth; day++) {
      const dayActivities = byDay[day] || [];
      const seconds = dayActivities.reduce((s, a) => s + (a.duration_s || 0), 0);
      cells.push({
        day,
        isToday: isCurrentMonth && day === today.getDate(),
        activities: dayActivities,
        dotSize: dotSizeFor(seconds),
      });
    }
    while (cells.length % 7 !== 0) cells.push(null);
    const rows: Cell[][] = [];
    for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7));
    return rows;
  }, [viewYear, viewMonth, byDay, maxDaySeconds, isCurrentMonth, today]);

  const weekdayLabels = useMemo(() => {
    // Narrow single-letter labels, Monday-first, locale-aware (same read as the reference's
    // M T W T F S S without hardcoding). 2024-01-01 is a Monday.
    const fmt = new Intl.DateTimeFormat(dateLocale, { weekday: 'narrow' });
    return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
  }, []);

  const listed = useMemo(() => monthActivities
    .filter(a => selectedDay === null || activityDate(a)!.getDate() === selectedDay)
    .slice().sort((x, y) => (y.date || '').localeCompare(x.date || '')),
  [monthActivities, selectedDay]);

  const monthTitle = new Date(viewYear, viewMonth, 1)
    .toLocaleDateString(dateLocale, { month: 'long', year: 'numeric' });

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Card>
        {/* Month nav */}
        <View style={styles.navRow}>
          <TouchableOpacity onPress={goPrevMonth} hitSlop={10}>
            <Icon name="chevronLeft" size={22} color={theme.text} />
          </TouchableOpacity>
          <View style={styles.navCenter}>
            <Text style={styles.monthText}>{monthTitle}</Text>
            <Text style={styles.monthSub}>{t.calendarActivities(monthActivities.length)}</Text>
          </View>
          {!isCurrentMonth ? (
            <TouchableOpacity onPress={goToday} style={styles.todayBtn} hitSlop={8}>
              <Text style={styles.todayBtnText}>{t.calendarToday}</Text>
            </TouchableOpacity>
          ) : <View style={{ width: 22 }} />}
          <TouchableOpacity onPress={goNextMonth} hitSlop={10}>
            <Icon name="chevronRight" size={22} color={theme.text} />
          </TouchableOpacity>
        </View>

        {/* Weekday header */}
        <View style={styles.weekdayRow}>
          {weekdayLabels.map((lbl, i) => (
            <Text key={i} style={styles.weekdayLabel}>{lbl}</Text>
          ))}
        </View>

        {/* Weeks */}
        {weeks.map((week, wi) => (
          <View key={wi} style={styles.weekRow}>
            {week.map((cell, ci) => (
              <DayCell key={ci} cell={cell} theme={theme} selected={!!cell && cell.day === selectedDay}
                onPress={cell && cell.activities.length > 0
                  ? () => setSelectedDay(d => (d === cell.day ? null : cell.day)) : undefined} />
            ))}
          </View>
        ))}
      </Card>

      {/* Legend */}
      <View style={styles.legendRow}>
        <View style={[styles.legendDot, { backgroundColor: theme.mutedText, opacity: 0.25 }]} />
        <Text style={styles.legendText}>{t.calendarLegendRest}</Text>
        <View style={[styles.legendDot, { backgroundColor: colorForName('Running') }]} />
        <Text style={styles.legendText}>{t.calendarLegendActivity}</Text>
      </View>

      {/* The month's activities, newest first (or just the tapped day's). */}
      {listed.length > 0 && (
        <Card>
          <Text style={styles.listTitle}>
            {selectedDay ? fmtDate(new Date(viewYear, viewMonth, selectedDay)) : monthTitle}
          </Text>
          {listed.map(a => (
            <TouchableOpacity key={a.id} style={styles.listRow} activeOpacity={0.7}
              onPress={() => navigation.navigate('Map', { activity: a })}>
              <View style={[styles.legendDot, { backgroundColor: colorForName(a.activity_type) }]} />
              <Text style={styles.listDate}>{fmtDate(a.date)}</Text>
              <Text style={styles.listSport} numberOfLines={1}>{a.activity_type || t.unknownActivity}</Text>
              <Text style={styles.listNum}>{a.distance_m > 0 ? `${(a.distance_m / 1000).toFixed(1)} km` : ''}</Text>
              <Text style={styles.listNum}>{fmtDur(a.duration_s)}</Text>
            </TouchableOpacity>
          ))}
        </Card>
      )}
    </ScrollView>
  );
}

// One day: the number sits ON the sport circle (not above it), the same overlap the desktop
// page settled on. A rest day gets a small low-opacity grey dot so the number stays legible;
// an activity day a filled circle in the first activity's sport colour, sized by the day's
// total duration relative to the month. Today with no activity yet keeps its own solid pill;
// today with an activity gets a thin primary ring around the real circle so "today" and "what
// you did" are both shown. A second sport that day adds one outer ring in its colour.
function fmtDur(seconds: number): string {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`;
}

function DayCell({ cell, theme, selected, onPress }: {
  cell: any; theme: ReturnType<typeof useV3Theme>; selected?: boolean; onPress?: () => void;
}) {
  const styles = createStyles(theme);
  if (!cell) return <View style={styles.dayCell} />;

  const count: number = cell.activities.length;
  const hasActivity = count > 0;
  const isToday: boolean = cell.isToday;
  const primaryColor = hasActivity ? colorForName(cell.activities[0].activity_type) : theme.mutedText;
  const secondColor = count > 1 ? colorForName(cell.activities[1].activity_type) : null;

  const circleSize = hasActivity ? cell.dotSize : MIN_DOT - 4;
  const numberOnCircle = hasActivity || isToday;

  return (
    <TouchableOpacity style={[styles.dayCell, selected && styles.daySelected]} activeOpacity={0.6}
      disabled={!onPress} onPress={onPress}>
      <View style={styles.dayInner}>
        {/* second-sport outer ring */}
        {secondColor && (
          <View style={[styles.ring, {
            width: cell.dotSize + 6, height: cell.dotSize + 6, borderRadius: (cell.dotSize + 6) / 2,
            borderColor: secondColor,
          }]} />
        )}
        {/* today ring (with activity) */}
        {isToday && hasActivity && (
          <View style={[styles.ring, {
            width: cell.dotSize + 10, height: cell.dotSize + 10, borderRadius: (cell.dotSize + 10) / 2,
            borderColor: theme.primary,
          }]} />
        )}
        {/* today, no activity yet: solid pill */}
        {isToday && !hasActivity && (
          <View style={{ position: 'absolute', width: MIN_DOT, height: MIN_DOT, borderRadius: MIN_DOT / 2, backgroundColor: theme.primary }} />
        )}
        {/* the day circle (skipped for today-no-activity, which has its pill above) */}
        {!(isToday && !hasActivity) && (
          <View style={{
            position: 'absolute',
            width: circleSize, height: circleSize, borderRadius: circleSize / 2,
            backgroundColor: primaryColor,
            opacity: hasActivity ? 1 : 0.25,
          }} />
        )}
        <Text style={[
          styles.dayNumber,
          { color: numberOnCircle ? theme.card : theme.text, fontWeight: isToday ? '800' : '500' },
        ]}>
          {cell.day}
        </Text>
      </View>
    </TouchableOpacity>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 16, gap: 16, paddingBottom: 40 },
  // 2026-08-15 design-parity audit: matched to desktop's page title (Theme.fontSizeTitle 18,
  // font.bold) - was 20/800, larger and heavier than the same heading renders on desktop.
  title: { fontSize: 18, fontWeight: '700', color: t.text },

  navRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  navCenter: { flex: 1, alignItems: 'center' },
  monthText: { fontSize: 16, fontWeight: '800', color: t.text, textTransform: 'capitalize' },
  monthSub: { fontSize: 11, color: t.mutedText, marginTop: 1 },
  todayBtn: {
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
  },
  todayBtnText: { fontSize: 12, color: t.primary, fontWeight: '700' },

  weekdayRow: { flexDirection: 'row', marginTop: 14, marginBottom: 4 },
  weekdayLabel: { flex: 1, textAlign: 'center', fontSize: 11, color: t.mutedText, fontWeight: '700' },

  weekRow: { flexDirection: 'row', marginTop: 8 },
  dayCell: { flex: 1, height: 48, alignItems: 'center', justifyContent: 'center' },
  dayInner: { width: MAX_DOT + 8, height: MAX_DOT + 8, alignItems: 'center', justifyContent: 'center' },
  ring: { position: 'absolute', borderWidth: 2, backgroundColor: 'transparent' },
  dayNumber: { fontSize: 13 },

  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  legendDot: { width: 12, height: 12, borderRadius: 6 },
  legendText: { fontSize: 12, color: t.mutedText, marginRight: 8 },
  daySelected: { backgroundColor: t.primary + '22', borderRadius: 10 },
  listTitle: { fontSize: 14, fontWeight: '700', color: t.text, marginBottom: 6, textTransform: 'capitalize' },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9,
             borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.border },
  listDate: { fontSize: 13, color: t.mutedText, width: 86 },
  listSport: { flex: 1, fontSize: 14, color: t.text, fontWeight: '600' },
  listNum: { fontSize: 13, color: t.text, width: 76, textAlign: 'right' },
});
