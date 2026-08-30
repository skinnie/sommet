import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, FlatList, TouchableOpacity,
  StyleSheet, RefreshControl, Alert, ScrollView,
} from 'react-native';

import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import {
  ActivityRecord, getAllActivities, markActivitySynced,
  deleteActivity, updateActivityType, isActivityDeleted,
} from '../database/db';
import { readGpxFile, listGpxFiles } from '../services/GpxService';
import { extractGpxMetadata, GpxMetadata } from '../services/GpxParser';
import RNFS from 'react-native-fs';
import { t, fmtDate } from '../i18n';
import { useV3Theme } from '../theme/v3';
import { ActivityThumbnail } from '../components/ActivityThumbnail';
import Icon, { IconName } from '../components/ui/Icon';
import { activityIconName } from '../services/ActivityColors';
import {
  getViewMode, setViewMode as persistViewMode, getActivityColumns, setActivityColumns, ViewMode,
} from '../services/ListViewPrefs';
import { ViewModeToggle } from '../components/ui/ViewModeToggle';
import {
  ALL_METRICS, MetricValues, metricLabel, metricValue, metricRaw, metricsAvailableFor,
} from '../services/ActivityMetrics';
import { MetricColumnMenu } from '../components/ui/MetricColumnMenu';
import { deleteIntervalsIcuActivity } from '../services/ApiIntervalsIcu';

type Nav = NativeStackNavigationProp<RootStackParamList, 'LogList'>;

const ALL = t.all;

// An activity plus its richer metrics (re-parsed from the move's GPX for the configurable
// columns). Cached module-wide by id+synced_at so re-focusing the screen doesn't re-read files.
type EnrichedActivity = ActivityRecord & { metrics: MetricValues };
const _metricsCache = new Map<string, GpxMetadata>();

function buildMetrics(a: ActivityRecord, gpx?: GpxMetadata): MetricValues {
  const distanceM = a.distance_m || 0;
  const durationS = a.duration_s || 0;
  return {
    distanceM, durationS,
    ascentM: a.d_plus || 0,
    descentM: gpx?.descentM || 0,
    energyKcal: gpx?.energyKcal || 0,
    avgHr: gpx?.avgHr || 0,
    maxHr: gpx?.maxHr || 0,
    avgCadence: gpx?.avgCadence || 0,
    maxCadence: gpx?.maxCadence || 0,
    avgSpeedMh: gpx?.avgSpeedMh || (distanceM > 0 && durationS > 0 ? distanceM / (durationS / 3600) : 0),
    maxSpeedMh: gpx?.maxSpeedMh || 0,
    recoveryS: gpx?.recoveryS || 0,
    peakTe: gpx?.peakTe || 0,
    poolLengths: gpx?.poolLengths || 0,
    maxAltM: gpx?.maxAltM || 0,
    paceSecPerKm: gpx?.paceSecPerKm || (distanceM > 0 && durationS > 0 ? durationS / (distanceM / 1000) : 0),
  };
}

export default function LogListScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const navigation = useNavigation<Nav>();
  const [activities, setActivities] = useState<EnrichedActivity[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [activeFilter, setActiveFilter] = useState<string>(ALL);
  // View mode (map/list) is the persisted Settings preference; re-read on focus so a change
  // in Settings takes effect when the user returns here.
  const [viewMode, setViewMode] = useState<ViewMode>('map');
  // Configurable metric columns (persisted) + sort. Sorting is by whichever column, chosen
  // from that column's dropdown, or the default newest-first by upload date.
  const [columns, setColumns] = useState<string[]>(['distance', 'duration', 'ascent', 'calories']);
  const [sortKey, setSortKey] = useState<string>('uploaded');
  const [sortDesc, setSortDesc] = useState(true);
  const [menuCol, setMenuCol] = useState<number | null>(null);   // which column's dropdown is open
  useFocusEffect(useCallback(() => {
    getViewMode('activities').then(setViewMode);
    getActivityColumns().then(setColumns);
  }, []));
  function changeViewMode(m: ViewMode) { setViewMode(m); persistViewMode('activities', m); }

  function persistColumns(next: string[]) {
    setColumns(next);
    setActivityColumns(next);
  }
  function setColumn(idx: number, key: string) {
    const c = columns.slice(); c[idx] = key; persistColumns(c);
  }
  function removeColumn(idx: number) {
    if (columns.length <= 1) return;
    const c = columns.slice(); c.splice(idx, 1); persistColumns(c);
  }
  function addColumn() {
    const unused = ALL_METRICS.find(m => columns.indexOf(m.key) === -1);
    if (unused) persistColumns(columns.concat([unused.key]));
  }
  function sortByColumn(key: string, desc: boolean) {
    setSortKey(key); setSortDesc(desc);
  }

  // Calendar + Totals are activity-analytics views (desktop TotalsPage/CalendarPage parity).
  // On Android they're reached from here, the Activities screen, rather than the Home nav
  // shell: the phone's bottom tab row is already full, and these three (list / calendar /
  // totals) are one family of "what have I done" views, so grouping them is the adaptive
  // choice (rule 2, "UI evolutive according to the device").
  React.useLayoutEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <View style={styles.headerActions}>
          <TouchableOpacity onPress={() => navigation.navigate('Calendar')} hitSlop={8}>
            <Icon name="calendar" size={22} color={theme.text} />
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Totals')} hitSlop={8}>
            <Icon name="chart" size={22} color={theme.text} />
          </TouchableOpacity>
        </View>
      ),
    });
  }, [navigation, theme, styles]);

  const load = useCallback(async () => {
    try {
      // 1. Reconstruire depuis GPX orphelins — en ignorant la liste noire
      const [gpxPaths, existing] = await Promise.all([listGpxFiles(), getAllActivities()]);
      const dbIds = new Set(existing.map(a => a.id));
      for (const path of gpxPaths) {
        const id = path.split('/').pop()?.replace('.gpx', '') ?? '';
        if (!id || dbIds.has(id)) continue;
        if (await isActivityDeleted(id)) continue;   // ← liste noire
        try {
          const xml  = await readGpxFile(path);
          const meta = extractGpxMetadata(xml);
          await markActivitySynced({
            id,
            synced_at: Date.now(),
            gpx_path: path,
            date:       meta.date,
            duration_s: meta.durationS,
            distance_m: meta.distanceM,
            d_plus:     meta.dPlus,
            activity_type: meta.activityType,
          });
        } catch (_) {}
      }
      // 2. Charger + réparer les activity_type manquants
      const data = await getAllActivities();
      for (const a of data) {
        if (a.activity_type) continue;
        try {
          const xml  = await readGpxFile(a.gpx_path);
          const meta = extractGpxMetadata(xml);
          if (meta.activityType) {
            await updateActivityType(a.id, meta.activityType);
            a.activity_type = meta.activityType;
          }
        } catch (_) {}
      }
      // Enrich each activity with its richer metrics for the configurable columns. Read from
      // the move's own GPX (the DB only stores the core four); cached by id+synced_at so
      // re-focusing doesn't re-read the files. Reads run in parallel and never fail the list.
      const enriched: EnrichedActivity[] = await Promise.all(data.map(async a => {
        const cacheKey = `${a.id}:${a.synced_at}`;
        let gpx = _metricsCache.get(cacheKey);
        if (!gpx && a.gpx_path) {
          try {
            gpx = extractGpxMetadata(await readGpxFile(a.gpx_path));
            _metricsCache.set(cacheKey, gpx);
          } catch { /* keep whatever the DB core gives */ }
        }
        return { ...a, metrics: buildMetrics(a, gpx) };
      }));
      setActivities(enriched);
    } catch (e) {
      Alert.alert(t.loadError, String(e));
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  async function handleRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  function confirmDelete(item: ActivityRecord) {
    // Say plainly when the delete also removes the remote copy - it is irreversible, so the
    // confirm must not imply this only hides it locally.
    const alsoRemote = item.id.startsWith('icu:');
    Alert.alert(
      t.deleteTitle,
      t.deleteMsg(formatDate(item.date))
        + (alsoRemote ? '\n\nThis also deletes it permanently from intervals.icu.' : ''),
      [
        { text: t.cancel, style: 'cancel' },
        {
          text: t.delete,
          style: 'destructive',
          onPress: async () => {
            await deleteActivity(item.id);           // ajoute à la liste noire
            if (item.gpx_path) {
              await RNFS.unlink(item.gpx_path).catch(() => {});
            }
            // An activity that CAME FROM intervals.icu is also deleted there (2026-08-26,
            // desktop parity - André chose "also delete from the source" over a local-only
            // hide). Watch-recorded moves have no remote copy to remove. Fire-and-forget: the
            // local delete already stands, and a network failure must not resurrect the row.
            if (item.id.startsWith('icu:')) {
              deleteIntervalsIcuActivity(item.id).catch(() => {});
            }
            await load();
          },
        },
      ]
    );
  }

  // ─── Filtres ────────────────────────────────────────────────────────────────

  const filterTypes = useMemo(() => {
    const types = new Set(activities.map(a => a.activity_type).filter(Boolean));
    return [ALL, ...Array.from(types).sort()];
  }, [activities]);

  const filtered = useMemo(() => {
    const base = (activeFilter === ALL
      ? activities
      : activities.filter(a => a.activity_type === activeFilter)).slice();
    // Sort by the chosen column (any metric), or newest-first by upload date (default).
    base.sort((a, b) => {
      let c: number;
      if (sortKey === 'uploaded') {
        c = (a.synced_at || 0) - (b.synced_at || 0);
        // Tie-break by the activity's own date, so a bulk re-import (every move stamped with the
        // same synced_at) still lists newest-activity-first instead of arbitrary order.
        if (c === 0) c = String(a.date || '').localeCompare(String(b.date || ''));
      } else {
        c = metricRaw(a.metrics, sortKey) - metricRaw(b.metrics, sortKey);
      }
      return sortDesc ? -c : c;
    });
    return base;
  }, [activities, activeFilter, sortKey, sortDesc]);

  // ─── Rendu ──────────────────────────────────────────────────────────────────

  if (activities.length === 0) {
    return (
      <View style={styles.empty}>
        {/* 2026-08-11 (André, A1): was a raw mailbox emoji, which Android renders
            in full colour (orange) and no style can tint. */}
        <Icon name="list" size={44} color={theme.mutedText} />
        <Text style={styles.emptyText}>{t.noActivities}</Text>
        <Text style={styles.emptyHint}>{t.connectHint}</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      {/* Barre de filtres */}
      {filterTypes.length > 2 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.filterBar}
          contentContainerStyle={styles.filterBarContent}
        >
          {filterTypes.map(type => {
            const active = activeFilter === type;
            return (
              <TouchableOpacity
                key={type}
                style={[styles.chip, active && styles.chipActive]}
                onPress={() => setActiveFilter(type)}
                activeOpacity={0.7}
              >
                {type !== ALL && (
                  <Icon
                    name={activityIconName(type)}
                    size={14}
                    color={active ? theme.primary : theme.mutedText}
                  />
                )}
                <Text style={[styles.chipText, active && styles.chipTextActive]}>
                  {type === ALL ? type : capitalize(type)}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      )}

      {/* Configurable metric columns (port of desktop): a scrollable row of dropdown pills,
          one per column, plus an elegant "+" to add. Each pill opens a menu to sort by it,
          change which metric it shows (no duplicates), or remove it.
          The map/list view dropdown (moved here from Settings, André 2026-08-16) is the FIRST
          item on the left of this same row - matching desktop, where it sits far left on the
          header line "as text, as a dropdown menu as the other stuff". Persisted. */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.colBar}
        contentContainerStyle={styles.colBarContent}
      >
        <ViewModeToggle mode={viewMode} onChange={changeViewMode} />
        {columns.map((key, idx) => {
          const active = sortKey === key;
          return (
            <TouchableOpacity
              key={`${key}-${idx}`}
              style={[styles.colPill, active && styles.colPillActive]}
              onPress={() => setMenuCol(idx)}
              activeOpacity={0.7}
            >
              <Text style={[styles.colPillText, active && styles.colPillTextActive]}>{metricLabel(key)}</Text>
              {active && <Text style={styles.colPillArrow}>{sortDesc ? '↓' : '↑'}</Text>}
              <Text style={[styles.colCaret, active && styles.colPillTextActive]}>▾</Text>
            </TouchableOpacity>
          );
        })}
        {columns.length < ALL_METRICS.length && (
          <TouchableOpacity style={styles.addPill} onPress={addColumn} activeOpacity={0.7}>
            <Text style={styles.addPillText}>+</Text>
          </TouchableOpacity>
        )}
      </ScrollView>

      <FlatList
        style={styles.list}
        data={filtered}
        keyExtractor={item => item.id}
        ListEmptyComponent={
          <View style={styles.emptyFilter}>
            <Text style={styles.emptyFilterText}>{t.noFilter}</Text>
          </View>
        }
        ListFooterComponent={
          <Text style={styles.deleteHint}>{t.deleteHint}</Text>
        }
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={theme.text} />}
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.card}
            onPress={() => navigation.navigate('Map', { activity: item })}
            onLongPress={() => confirmDelete(item)}
            activeOpacity={0.75}
          >
            {viewMode === 'map' && !!item.gpx_path && (
              <View style={styles.cardThumb}>
                <ActivityThumbnail gpxPath={item.gpx_path} height={72} />
              </View>
            )}
            <View style={styles.cardRow}>
              <View style={styles.cardLeft}>
                <Text style={styles.cardDate}>{formatDate(item.date)}</Text>
                {!!item.activity_type && (
                  <View style={styles.cardTypeRow}>
                    <Icon name={activityIconName(item.activity_type)} size={12} color={theme.mutedText} />
                    <Text style={styles.cardType}>{capitalize(item.activity_type)}</Text>
                  </View>
                )}
                {/* Which device recorded it (2026-08-26, desktop parity). Only imported
                    activities carry one - a move read off the connected watch leaves it empty,
                    because the watch is implied - so this line simply doesn't render for those. */}
                {!!item.device && (
                  <Text style={styles.cardDevice} numberOfLines={1}>{item.device}</Text>
                )}
              </View>
              <Text style={styles.cardArrow}>›</Text>
            </View>
            {/* The configured metrics, in the watch's own values. A metric the move never
                recorded is skipped (metricValue returns ""), so cards stay tidy. */}
            <View style={styles.metricsRow}>
              {columns.map(key => {
                const val = metricValue(item.metrics, key);
                if (!val) return null;
                return (
                  <View key={key} style={styles.metricCell}>
                    <Text style={styles.metricValue}>{val}</Text>
                    <Text style={styles.metricLabel}>{metricLabel(key)}</Text>
                  </View>
                );
              })}
            </View>
          </TouchableOpacity>
        )}
      />

      {menuCol !== null && (
        <MetricColumnMenu
          visible
          currentKey={columns[menuCol]}
          metrics={metricsAvailableFor(columns, menuCol)}
          canRemove={columns.length > 1}
          onSort={desc => sortByColumn(columns[menuCol], desc)}
          onPick={key => setColumn(menuCol, key)}
          onRemove={() => removeColumn(menuCol)}
          onClose={() => setMenuCol(null)}
        />
      )}
    </View>
  );
}

// ─── Helpers d'affichage ──────────────────────────────────────────────────────

// Real, 2026-08-10 ("change the orange icons to something more aligned with our material
// design and colors") - was raw emoji (🚴🏃🥾...), full-color glyphs that can't be tinted
// and visibly clashed with the rest of this app's monochrome icon language. Maps onto
// Icon.tsx's own small set (cycling/running/walking are new; trail/ski/alpinisme types
// reuse the existing 'mountain' icon rather than drawing a one-off glyph for a handful of
// rarer types; anything else falls back to the existing generic 'activity' icon).
function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatDate(iso: string): string {
  if (!iso) return t.unknownDate;
  // Day-first dd/MM/yyyy, matching the desktop activity rows (ActivityViewModel.formatDate).
  return fmtDate(iso) || t.unknownDate;
}

function formatDuration(seconds: number): string {
  if (!seconds) return '--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}`;
  return `${m}m${String(s).padStart(2, '0')}`;
}

function formatDist(meters: number): string {
  if (!meters) return '--';
  return meters >= 1000
    ? `${(meters / 1000).toFixed(2)} km`
    : `${meters} m`;
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  headerActions: { flexDirection: 'row', gap: 18, paddingRight: 4 },
  list: { flex: 1, padding: 12 },
  filterBar: { maxHeight: 52, backgroundColor: t.background },
  filterBarContent: { paddingHorizontal: 12, paddingVertical: 8, gap: 8 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: t.card,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: t.mutedText + '33',
  },
  // v3.0 UI port - active filter chip picks up the same tinted-primary treatment as
  // primitives.tsx's own Chip (a real selected/status state, not just a color swap).
  chipActive: {
    backgroundColor: t.primary + '1F',
    borderColor: t.primary,
  },
  chipText: { fontSize: 13, color: t.mutedText, fontWeight: '500' },
  chipTextActive: { color: t.primary, fontWeight: '700' },
  empty: {
    flex: 1, alignItems: 'center', justifyContent: 'center',
    backgroundColor: t.background, padding: 32,
  },
  emptyIcon: { fontSize: 48, marginBottom: 16 },
  emptyText: { fontSize: 18, color: t.text, fontWeight: '600', marginBottom: 8 },
  emptyHint: { fontSize: 13, color: t.mutedText, textAlign: 'center' },
  emptyFilter: { paddingVertical: 40, alignItems: 'center' },
  emptyFilterText: { color: t.mutedText, fontSize: 14 },
  deleteHint: { fontSize: 11, color: t.mutedText, textAlign: 'center', marginTop: 8, marginBottom: 16 },
  // No shadows (André, 2026-08-25: "all app, desktop android, same for previous rules") -
  // matches Card.qml/Card.tsx's own hairline-border-instead-of-shadow look.
  card: {
    backgroundColor: t.card,
    borderRadius: 16,
    padding: 16,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: t.border,
  },
  // v3.0 UI port - real per-activity track preview (ActivitiesPage.qml parity), see
  // ActivityThumbnail.tsx's own comment on why it's a lightweight SVG shape, not a live map.
  cardThumb: { marginBottom: 10 },
  cardRow: { flexDirection: 'row', alignItems: 'center' },
  cardLeft: { flex: 1 },
  cardDate: { fontSize: 15, color: t.text, fontWeight: '600', marginBottom: 2 },
  cardDevice: { fontSize: 11, color: t.mutedText, marginTop: 1 },
  cardTypeRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 3 },
  cardType: { fontSize: 12, color: t.mutedText },
  cardSub: { fontSize: 13, color: t.mutedText },
  // ── Configurable column bar + metric cells ──
  colBar: { maxHeight: 48, backgroundColor: t.background },
  colBarContent: { paddingHorizontal: 12, paddingVertical: 8, gap: 8, alignItems: 'center' },
  colPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: t.card,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: t.mutedText + '33',
  },
  colPillActive: { backgroundColor: t.primary + '1F', borderColor: t.primary },
  colPillText: { fontSize: 12.5, color: t.text, fontWeight: '500' },
  colPillTextActive: { color: t.primary, fontWeight: '700' },
  colPillArrow: { fontSize: 12.5, color: t.primary, fontWeight: '700' },
  colCaret: { fontSize: 11, color: t.mutedText },
  addPill: {
    width: 30, height: 30, borderRadius: 15,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: t.mutedText + '55',
  },
  addPillText: { fontSize: 18, color: t.mutedText, lineHeight: 20 },
  metricsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 18, marginTop: 10 },
  metricCell: { minWidth: 60 },
  metricValue: { fontSize: 14, color: t.text, fontWeight: '700' },
  metricLabel: { fontSize: 10.5, color: t.mutedText, marginTop: 1 },
  cardRight: { alignItems: 'flex-end' },
  cardDPlus: { fontSize: 13, color: t.mutedText, marginBottom: 4 },
  cardArrow: { fontSize: 22, color: t.mutedText },
});
