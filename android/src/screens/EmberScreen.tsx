import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, RefreshControl, Pressable, Modal, Alert,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import { EmberBars } from '../components/EmberBars';
import {
  loadEmber, saveEmber, applyLog, emberSummary, EmberData, EmberSummary, LogBody,
} from '../services/EmberStore';
import { syncEmberNas } from '../services/EmberSync';

// Ember - the interactive logger, matching desktop/qml/pages/EmberPage.qml (2026-08-27, André:
// full feature parity). Fasting start/stop, one-tap coffee/water tiles with long-press menus,
// calories-in, and the 14-day charts. Logs to a local store (EmberStore) and two-way syncs with
// the shared NAS store (EmberSync) - the same store the iPhone PWA and desktop use - so a coffee
// logged on any device shows on the others. (Was a read-only intervals recap before.)

// Fast-aware drinks, same list as the desktop page.
const COFFEES = [
  { name: 'Espresso', kcal: 3, caffeine: 63, breaksFast: false, isCoffee: true },
  { name: 'Black coffee', kcal: 2, caffeine: 95, breaksFast: false, isCoffee: true },
  { name: 'Americano', kcal: 3, caffeine: 95, breaksFast: false, isCoffee: true },
  { name: 'Green tea', kcal: 2, caffeine: 28, breaksFast: false, isCoffee: false },
  { name: 'Black tea', kcal: 2, caffeine: 47, breaksFast: false, isCoffee: false },
  { name: 'Coffee with milk', kcal: 20, caffeine: 95, breaksFast: true, isCoffee: true },
  { name: 'Latte', kcal: 120, caffeine: 128, breaksFast: true, isCoffee: true },
  { name: 'Cappuccino', kcal: 80, caffeine: 128, breaksFast: true, isCoffee: true },
];
const WATERS = [
  { name: 'Glass', ml: 250 },
  { name: 'Bottle', ml: 500 },
  { name: 'Large bottle', ml: 750 },
  { name: 'Sparkling water', ml: 250 },
];

function hhmm(ms: number): string {
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return `${h}:${`${m}`.padStart(2, '0')}`;
}

export default function EmberScreen() {
  const t = useV3Theme();
  const [data, setData] = useState<EmberData>({ entries: [], fasts: [], deleted: [] });
  const [summary, setSummary] = useState<EmberSummary>({ today: {
    kcal: 0, coffees: 0, waterMl: 0, fastActive: false, fastStart: null, fastGoalHours: 16 } as any,
    days: [], fasts: [] });
  const [now, setNow] = useState(Date.now());
  const [menu, setMenu] = useState<null | 'coffee' | 'water'>(null);
  const [syncing, setSyncing] = useState(false);
  const dataRef = useRef(data);
  dataRef.current = data;

  const apply = useCallback((d: EmberData) => { setData({ ...d }); setSummary(emberSummary(d)); }, []);

  // Load + best-effort NAS pull on open.
  const refresh = useCallback(async () => {
    setSyncing(true);
    const d = await loadEmber();
    apply(d);
    try { if (await syncEmberNas(d)) { await saveEmber(d); } } catch { /* offline is fine */ }
    apply(d);
    setSyncing(false);
  }, [apply]);

  useEffect(() => { refresh(); }, [refresh]);

  // Tick the fasting elapsed time.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Log one event: apply locally, save, reflect, then push to the NAS in the background.
  const log = useCallback(async (body: LogBody) => {
    const d = applyLog(dataRef.current, body);
    await saveEmber(d);
    apply(d);
    try { if (await syncEmberNas(d)) await saveEmber(d); apply(d); } catch { /* best-effort */ }
  }, [apply]);

  const today = summary.today;
  const fasting = today.fastActive && !!today.fastStart;
  const elapsedMs = fasting ? now - (today.fastStart as number) : 0;
  const frac = fasting ? Math.min(1, elapsedMs / (today.fastGoalHours * 3600000)) : 0;

  const series = (sel: (d: any) => number | undefined) =>
    summary.days.filter(d => sel(d) !== undefined).map(d => ({ date: d.date, value: sel(d) as number }));
  const ymd = (ts: number) => {
    const d = new Date(ts);
    return `${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, '0')}-${`${d.getDate()}`.padStart(2, '0')}`;
  };
  const fastSeries = summary.fasts.map(f => ({ date: ymd(f.end), value: f.hours }));

  // Streak = consecutive days back from today with a completed fast; avg fast over the window
  // (mirrors EmberPage.qml streak()/avgFast()).
  const avgFast = summary.fasts.length
    ? Math.round((summary.fasts.reduce((s, f) => s + f.hours, 0) / summary.fasts.length) * 10) / 10 : 0;
  const fastDays = new Set(summary.fasts.map(f => ymd(f.end)));
  if (today.fastActive) fastDays.add(ymd(Date.now())); // an in-progress fast keeps today's streak alive
  let streak = 0;
  for (const d = new Date(); fastDays.has(ymd(d.getTime())); d.setDate(d.getDate() - 1)) streak++;

  function confirmStop() {
    Alert.alert('End fast?', fasting ? `You're at ${hhmm(elapsedMs)}.` : '', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'End fast', style: 'destructive', onPress: () => log({ type: 'fast-end' }) },
    ]);
  }

  const card = { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card };
  const nested = { backgroundColor: t.cardNested, borderRadius: v3Radius.small };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={syncing} onRefresh={refresh} tintColor={t.primary} />}
    >
      <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>Ember</Text>
      <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginBottom: v3Spacing.medium }}>
        Tap a tile to log · long-press coffee or water for more
      </Text>

      <View style={styles.tiles}>
        {/* Fasting */}
        <Pressable style={[styles.tile, nested]} onPress={() => fasting ? confirmStop() : log({ type: 'fast-start', goalHours: 16 })}>
          <View style={[styles.ring, { borderColor: fasting ? t.warning : t.border }]}>
            <Text style={{ color: t.mutedText, fontSize: v3Type.tiny, fontWeight: '700' }}>
              {fasting ? `${Math.round(frac * 100)}%` : '○'}
            </Text>
          </View>
          <Text style={[styles.val, { color: t.text }]}>{fasting ? hhmm(elapsedMs) : '—'}</Text>
          <Text style={{ color: t.warning, fontSize: v3Type.tiny, fontWeight: '700' }}>
            {fasting ? 'tap to stop' : 'tap to fast'}
          </Text>
        </Pressable>

        {/* Calories */}
        <View style={[styles.tile, nested]}>
          <Text style={[styles.val, { color: t.text }]}>{today.kcal}</Text>
          <Text style={[styles.sub, { color: t.mutedText }]}>kcal in</Text>
        </View>

        {/* Coffee */}
        <Pressable style={[styles.tile, nested]}
          onPress={() => log({ type: 'drink', name: 'Espresso', kcal: 3, caffeineMg: 63, isCoffee: true, breaksFast: false })}
          onLongPress={() => setMenu('coffee')}>
          <Text style={[styles.val, { color: t.text }]}>{today.coffees}</Text>
          <Text style={[styles.sub, { color: t.mutedText }]}>coffees</Text>
          <Text style={{ color: t.hard ?? t.primary, fontSize: v3Type.tiny, fontWeight: '700' }}>tap +</Text>
        </Pressable>

        {/* Water */}
        <Pressable style={[styles.tile, nested]}
          onPress={() => log({ type: 'water', volumeMl: 250 })}
          onLongPress={() => setMenu('water')}>
          <Text style={[styles.val, { color: t.text }]}>{(today.waterMl / 1000).toFixed(1)} L</Text>
          <Text style={[styles.sub, { color: t.mutedText }]}>water</Text>
          <Text style={{ color: t.primary, fontSize: v3Type.tiny, fontWeight: '700' }}>tap +250</Text>
        </Pressable>

        {/* Day streak */}
        <View style={[styles.tile, nested]}>
          <Text style={[styles.val, { color: t.text }]}>{streak}</Text>
          <Text style={[styles.sub, { color: t.mutedText }]}>day streak</Text>
        </View>

        {/* Avg fast (14d) */}
        <View style={[styles.tile, nested]}>
          <Text style={[styles.val, { color: t.text }]}>{avgFast} h</Text>
          <Text style={[styles.sub, { color: t.mutedText }]}>avg fast 14d</Text>
        </View>
      </View>

      {/* Bars for fasting + calories, line for water - matches desktop EmberPage.qml */}
      {fastSeries.length > 1 &&
        <EmberBars label="Fasting hours" unit="h" decimals={1} goal={today.fastGoalHours || 16}
          barColor={t.warning} series={fastSeries} />}
      {series(d => d.kcal).length > 1 &&
        <EmberBars label="Calories in" unit=" kcal" barColor={t.success} series={series(d => d.kcal)} />}
      {series(d => d.waterL).length > 1 &&
        <MetricChart label="Water (litres)" unit=" L" decimals={1} series={series(d => d.waterL)} />}

      <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
        Logged here and synced with the Ember store on your other devices.
      </Text>

      {/* Coffee / water menu */}
      <Modal visible={menu !== null} transparent animationType="fade" onRequestClose={() => setMenu(null)}>
        <Pressable style={styles.backdrop} onPress={() => setMenu(null)}>
          <Pressable style={[styles.sheet, card]} onPress={() => {}}>
            <Text style={{ color: t.text, fontSize: v3Type.label, fontWeight: '700', marginBottom: v3Spacing.small }}>
              {menu === 'coffee' ? 'Add a drink' : 'Add water'}
            </Text>
            {menu === 'coffee' && COFFEES.map(c => (
              <Pressable key={c.name} style={[styles.row, { borderColor: t.border }]}
                onPress={() => { setMenu(null); log({ type: 'drink', name: c.name, kcal: c.kcal, caffeineMg: c.caffeine, isCoffee: c.isCoffee, breaksFast: c.breaksFast }); }}>
                <Text style={{ color: t.text, fontSize: v3Type.body }}>{c.name}</Text>
                <Text style={{ color: c.breaksFast ? t.warning : t.mutedText, fontSize: v3Type.caption }}>
                  {c.kcal} kcal · {c.caffeine} mg{c.breaksFast ? ' · breaks fast' : ''}
                </Text>
              </Pressable>
            ))}
            {menu === 'water' && WATERS.map(w => (
              <Pressable key={w.name} style={[styles.row, { borderColor: t.border }]}
                onPress={() => { setMenu(null); log({ type: 'water', volumeMl: w.ml }); }}>
                <Text style={{ color: t.text, fontSize: v3Type.body }}>{w.name}</Text>
                <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{w.ml} ml</Text>
              </Pressable>
            ))}
          </Pressable>
        </Pressable>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small, marginBottom: v3Spacing.medium },
  tile: { flexGrow: 1, flexBasis: '15%', minWidth: 92, paddingVertical: 14, alignItems: 'center', borderWidth: 1, borderColor: 'transparent' },
  ring: { width: 40, height: 40, borderRadius: 20, borderWidth: 3, alignItems: 'center', justifyContent: 'center', marginBottom: 4 },
  val: { fontSize: v3Type.bodyLarge, fontWeight: '700' },
  sub: { fontSize: v3Type.tiny, fontWeight: '600' },
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'center', padding: v3Spacing.large },
  sheet: { borderWidth: 1, padding: v3Spacing.medium },
  row: { paddingVertical: 12, borderTopWidth: 1, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
});
