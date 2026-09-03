import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet, Animated,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { ActivityRecord, getAllActivities } from '../database/db';
import { activityForName, colorForName } from '../services/ActivityColors';
import { distanceLines, hoursLines } from '../services/TotalsFacts';
import { Card } from '../components/ui/Card';
import { useV3Theme } from '../theme/v3';
import { t } from '../i18n';

// Totals - port of desktop/qml/TotalsPage.qml (real request, 2026-08-11): "For the year -
// Hours outside (gps activities), Kms by #activity (run + bike defaults, person can choose),
// Energy spent (sum up all kcals)". Everything is derived from the activities already in the
// local DB (the one source Android already syncs every device into), so it is correct the
// moment Activities is - no new device traffic, exactly like the desktop page.
//
// One honest divergence from desktop: Android's activity rows carry no kcal (they are rebuilt
// from the synced GPX, which has no energy channel - see db.ts's ActivityRecord and
// GpxParser.extractGpxMetadata), so the "Energy spent" card degrades to a short note rather
// than showing a false 0. The desktop reads energyKcal off the watch's ExerciseLog; when
// Android grows that read, TotalsFacts.energyLines() is already ported and the card lights up.

function yearOf(a: ActivityRecord): number {
  if (!a.date) return 0;
  const d = new Date(a.date);
  return isNaN(d.getTime()) ? 0 : d.getFullYear();
}

function formatKm(meters: number): string {
  const km = meters / 1000;
  return `${km.toLocaleString('en-GB', { maximumFractionDigits: km >= 100 ? 0 : 1 })} km`;
}

export default function TotalsScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  const [activities, setActivities] = useState<ActivityRecord[]>([]);
  const [selectedYear, setSelectedYear] = useState<number>(0);
  // null until the automatic default is computed once; after that it holds the user's
  // manual pick, so re-deriving byActivity on a year change never clobbers their choice.
  const [featured, setFeatured] = useState<number[] | null>(null);

  useFocusEffect(useCallback(() => {
    getAllActivities().then(setActivities).catch(() => {});
  }, []));

  // Years that actually have data, newest first (same guard as desktop: the picker can never
  // land on an empty year by accident).
  const years = useMemo(() => {
    const seen = new Set<number>();
    for (const a of activities) { const y = yearOf(a); if (y > 0) seen.add(y); }
    return Array.from(seen).sort((x, y) => y - x);
  }, [activities]);

  // Default to the most recent year with data rather than "now", so a January visit does not
  // open on an empty screen.
  useEffect(() => {
    if (years.length > 0 && years.indexOf(selectedYear) === -1) setSelectedYear(years[0]);
  }, [years]); // eslint-disable-line react-hooks/exhaustive-deps

  const yearActivities = useMemo(
    () => activities.filter(a => yearOf(a) === selectedYear),
    [activities, selectedYear],
  );

  // "Hours outside": only activities that recorded a track. On Android every synced activity
  // has its GPX file (gpx_path is NOT NULL - see db.ts), which is exactly the "has a GPS
  // track" condition the desktop tests with a.track.length.
  const withTrack = useMemo(
    () => yearActivities.filter(a => !!a.gpx_path),
    [yearActivities],
  );
  const hoursOutside = useMemo(
    () => withTrack.reduce((s, a) => s + (a.duration_s || 0), 0) / 3600,
    [withTrack],
  );

  // Distance grouped by resolved activity id - the automatic grouping desktop's header
  // comment describes: "Running" and "Trail running" land in one bucket on their own, a
  // renamed custom mode still resolves through the same table (activityForName).
  const byActivity = useMemo(() => {
    const groups: Record<number, { id: number; name: string; meters: number; count: number }> = {};
    for (const a of yearActivities) {
      const type = activityForName(a.activity_type);
      if (!groups[type.id]) groups[type.id] = { id: type.id, name: type.name, meters: 0, count: 0 };
      groups[type.id].meters += a.distance_m || 0;
      groups[type.id].count += 1;
    }
    return Object.values(groups).sort((x, y) => y.meters - x.meters);
  }, [yearActivities]);

  // Featured groups: default to running + cycling per the request, else the two biggest.
  // Recomputed only while the user has not made a manual pick (featured === null).
  useEffect(() => {
    if (featured !== null) return;
    const wanted = byActivity
      .filter(g => { const nm = g.name.toLowerCase(); return nm.includes('run') || nm.includes('cycl') || nm.includes('bik'); })
      .map(g => g.id);
    setFeatured(wanted.length > 0 ? wanted : byActivity.slice(0, 2).map(g => g.id));
  }, [byActivity, featured]);

  // A manual year change should re-seed the automatic default for the new year rather than
  // carrying last year's featured sports over - reset the pick when the year changes.
  useEffect(() => { setFeatured(null); }, [selectedYear]);

  const activeFeatured = featured ?? [];
  function toggleFeatured(id: number) {
    const next = activeFeatured.slice();
    const at = next.indexOf(id);
    if (at >= 0) next.splice(at, 1); else next.push(id);
    setFeatured(next);
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>{t.totalsTitle}</Text>
        <View style={styles.yearRow}>
          {years.map(y => (
            <TouchableOpacity
              key={y}
              style={[styles.yearChip, y === selectedYear && styles.yearChipActive]}
              onPress={() => setSelectedYear(y)}
              activeOpacity={0.7}
            >
              <Text style={[styles.yearChipText, y === selectedYear && styles.yearChipTextActive]}>{y}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {yearActivities.length === 0 && (
        <Text style={styles.emptyText}>
          {activities.length === 0 ? t.totalsEmptyNoData : t.totalsEmptyYear}
        </Text>
      )}

      {yearActivities.length > 0 && (
        <>
          {/* Hours outside */}
          <TotalsCard
            theme={theme}
            title={t.totalsHoursTitle}
            headline={`${Math.round(hoursOutside)} h`}
            subtitle={t.totalsHoursSubtitle(withTrack.length)}
            lines={hoursLines(hoursOutside)}
          />

          {/* Distance by activity - grouped chips */}
          <Card>
            <Text style={styles.cardTitle}>{t.totalsDistanceTitle}</Text>
            <Text style={styles.cardDesc}>{t.totalsDistanceDesc}</Text>
            <View style={styles.chipFlow}>
              {byActivity.map(g => {
                const on = activeFeatured.indexOf(g.id) >= 0;
                return (
                  <TouchableOpacity
                    key={g.id}
                    style={[styles.actChip, on && { borderColor: theme.primary }]}
                    onPress={() => toggleFeatured(g.id)}
                    activeOpacity={0.7}
                  >
                    <View style={[styles.actDot, { backgroundColor: colorForName(g.name) }]} />
                    <Text style={[styles.actChipText, on && { fontWeight: '700', color: theme.text }]}>
                      {g.name} · {formatKm(g.meters)}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </Card>

          {/* One card per featured sport */}
          {byActivity.filter(g => activeFeatured.indexOf(g.id) >= 0 && g.meters > 0).map(g => (
            <TotalsCard
              key={g.id}
              theme={theme}
              accent={colorForName(g.name)}
              title={g.name}
              headline={formatKm(g.meters)}
              subtitle={t.totalsActivitiesCount(g.count)}
              lines={distanceLines(g.meters)}
            />
          ))}

          {/* Energy - not captured from GPX on Android (see file header) */}
          <Card>
            <Text style={styles.cardTitle}>{t.totalsEnergyTitle}</Text>
            <Text style={styles.cardDesc}>{t.totalsEnergyUnavailable}</Text>
          </Card>
        </>
      )}
    </ScrollView>
  );
}

// One total with its rotating, sourced equivalent underneath - the repeated unit of the page,
// ported from desktop's TotalsCard.qml (equivalents rotate every 6 s rather than listing all
// at once; a wall of six facts stops being either funny or random). Cross-fades on change so
// it reads as one line updating, not two flickering.
function TotalsCard({
  theme, title, headline, subtitle, lines, accent,
}: {
  theme: ReturnType<typeof useV3Theme>;
  title: string;
  headline: string;
  subtitle?: string;
  lines: string[];
  accent?: string;
}) {
  const styles = createStyles(theme);
  const [index, setIndex] = useState(0);
  const opacity = useRef(new Animated.Value(1)).current;

  // Reset to the first line whenever the facts change (a different year / re-read), so the
  // card never shows an equivalent belonging to the previous number.
  useEffect(() => { setIndex(0); }, [lines]);

  useEffect(() => {
    if (lines.length <= 1) return;
    const iv = setInterval(() => {
      Animated.timing(opacity, { toValue: 0, duration: 160, useNativeDriver: true }).start(() => {
        setIndex(i => (i + 1) % lines.length);
        Animated.timing(opacity, { toValue: 1, duration: 220, useNativeDriver: true }).start();
      });
    }, 6000);
    return () => clearInterval(iv);
  }, [lines, opacity]);

  return (
    <Card>
      <Text style={styles.cardKicker}>{title}</Text>
      <Text style={[styles.cardHeadline, accent ? { color: accent } : null]}>{headline}</Text>
      {!!subtitle && <Text style={styles.cardDesc}>{subtitle}</Text>}
      {lines.length > 0 && (
        <Animated.Text style={[styles.cardEquivalent, { opacity }]} numberOfLines={2}>
          {lines[Math.min(index, lines.length - 1)]}
        </Animated.Text>
      )}
    </Card>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 16, gap: 14, paddingBottom: 40 },
  headerRow: { gap: 10 },
  // 2026-08-15 design-parity audit: matched to desktop's page title (Theme.fontSizeTitle 18,
  // font.bold) - was 20/800, larger and heavier than the same heading renders on desktop.
  title: { fontSize: 18, fontWeight: '700', color: t.text },
  yearRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  yearChip: {
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: 999,
    backgroundColor: t.card, borderWidth: 1, borderColor: t.mutedText + '33',
  },
  yearChipActive: { backgroundColor: t.primary + '1F', borderColor: t.primary },
  yearChipText: { fontSize: 13, color: t.mutedText, fontWeight: '600' },
  yearChipTextActive: { color: t.primary, fontWeight: '800' },
  emptyText: { color: t.mutedText, fontSize: 14, lineHeight: 20, paddingVertical: 24 },

  cardKicker: { fontSize: 13, color: t.mutedText, fontWeight: '700' },
  cardHeadline: { fontSize: 20, fontWeight: '800', color: t.text, marginTop: 2 },
  cardTitle: { fontSize: 14, fontWeight: '700', color: t.text },
  cardDesc: { fontSize: 11.5, color: t.mutedText, marginTop: 4, lineHeight: 17 },
  cardEquivalent: { fontSize: 13, color: t.primary, marginTop: 8, minHeight: 34, lineHeight: 18 },

  chipFlow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  actChip: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: t.card, borderWidth: 1, borderColor: t.mutedText + '4D',
  },
  actDot: { width: 12, height: 12, borderRadius: 6 },
  actChipText: { fontSize: 13, color: t.mutedText },
  moreText: { color: t.mutedText, fontSize: 13, marginTop: 4 },
});
