import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, RefreshControl,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import { getIntervalsIcuCredentials } from '../services/ApiIntervalsIcu';

// Ember — the Android counterpart of desktop/qml/pages/EmberPage.qml (André, 2026-08-26:
// "ember, it is a 'screen' that recaps what is on my iphone, also only available via easter
// egg"). A read-only recap of the fasting/calorie/water logging done in the Ember PWA on the
// phone; nothing is logged from here.
//
// Different data path from the desktop, because it has to be: the desktop reads Ember's own
// local backend (/api/ember on 127.0.0.1:8766), which Android has no equivalent of. Ember
// already pushes its daily totals to intervals.icu wellness (tools/ember_to_intervals.py), so
// this reads them from there. Verified those fields really arrive (2026-08-26): kcalConsumed
// 1240, hydrationVolume 1.2 L, plus carbohydrates/protein/fatTotal and the FastingTime /
// Coffees custom fields.
//
// Requires Ember's "Also sync to intervals.icu" toggle to be on - without it there is nothing
// to read, and the screen says so rather than showing an empty chart.

const API_BASE = 'https://intervals.icu/api/v1';

interface EmberDay {
  date: string;
  kcal?: number;
  waterL?: number;
  fastingH?: number;
  coffees?: number;
  carbs?: number;
  protein?: number;
  fat?: number;
}

async function fetchEmber(days = 30): Promise<EmberDay[]> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return [];
  const newest = new Date();
  const oldest = new Date(newest.getTime() - days * 24 * 3600 * 1000);
  const ymd = (d: Date) => d.toISOString().slice(0, 10);
  const resp = await fetch(
    `${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}/wellness`
    + `?oldest=${ymd(oldest)}&newest=${ymd(newest)}`,
    { headers: { Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`), 'User-Agent': 'Sommet/1.0' } },
  );
  if (!resp.ok) throw new Error(`intervals.icu wellness: HTTP ${resp.status}`);
  const rows = await resp.json();
  if (!Array.isArray(rows)) return [];

  return rows
    .map((r: any) => {
      const d: EmberDay = { date: String(r?.id ?? '') };
      if (typeof r.kcalConsumed === 'number') d.kcal = r.kcalConsumed;
      if (typeof r.hydrationVolume === 'number') d.waterL = r.hydrationVolume;
      if (typeof r.carbohydrates === 'number') d.carbs = r.carbohydrates;
      if (typeof r.protein === 'number') d.protein = r.protein;
      if (typeof r.fatTotal === 'number') d.fat = r.fatTotal;
      // The two custom fields ember_to_intervals.py creates. intervals returns custom fields as
      // top-level keys named by their code, so they are read the same way as the native ones.
      if (typeof r.FastingTime === 'number') d.fastingH = r.FastingTime;
      if (typeof r.Coffees === 'number') d.coffees = r.Coffees;
      return d;
    })
    .filter(d => d.date)
    .sort((a, b) => a.date.localeCompare(b.date));
}

export default function EmberScreen() {
  const t = useV3Theme();
  const [days, setDays] = useState<EmberDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try { setDays(await fetchEmber(30)); }
    catch (e: any) { setError(String(e?.message ?? e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const withData = days.filter(d =>
    d.kcal !== undefined || d.waterL !== undefined || d.fastingH !== undefined);
  const latest = withData.length > 0 ? withData[withData.length - 1] : null;

  const series = (sel: (d: EmberDay) => number | undefined) =>
    days.filter(d => sel(d) !== undefined).map(d => ({ date: d.date, value: sel(d) as number }));

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && withData.length === 0 && (
        <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />
      )}

      {error.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.error, fontSize: v3Type.body }}>{error}</Text>
        </View>
      )}

      {!loading && withData.length === 0 && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            Nothing logged yet. Ember runs on your phone — turn on "Also sync to intervals.icu"
            there and its daily totals will show up here.
          </Text>
        </View>
      )}

      {latest && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={[styles.label, { color: t.mutedText }]}>Latest — {latest.date}</Text>
          <View style={styles.tiles}>
            {[
              { k: 'Calories', v: latest.kcal, u: '' },
              { k: 'Water', v: latest.waterL, u: ' L' },
              { k: 'Fasting', v: latest.fastingH, u: ' h' },
              { k: 'Coffees', v: latest.coffees, u: '' },
            ].filter(m => m.v !== undefined).map(m => (
              <View key={m.k} style={[styles.tile, { backgroundColor: t.cardNested, borderRadius: v3Radius.small }]}>
                <Text style={[styles.tileLabel, { color: t.mutedText }]}>{m.k}</Text>
                <Text style={[styles.tileValue, { color: t.text }]}>
                  {typeof m.v === 'number' ? (m.v % 1 === 0 ? m.v : m.v.toFixed(1)) : '—'}{m.u}
                </Text>
              </View>
            ))}
          </View>
          {(latest.carbs !== undefined || latest.protein !== undefined || latest.fat !== undefined) && (
            <Text style={[styles.caption, { color: t.mutedText, marginTop: v3Spacing.small }]}>
              {[
                latest.carbs !== undefined ? `carbs ${latest.carbs.toFixed(0)} g` : '',
                latest.protein !== undefined ? `protein ${latest.protein.toFixed(0)} g` : '',
                latest.fat !== undefined ? `fat ${latest.fat.toFixed(0)} g` : '',
              ].filter(Boolean).join(' · ')}
            </Text>
          )}
        </View>
      )}

      {series(d => d.kcal).length > 1 && (
        <MetricChart label="Calories in" series={series(d => d.kcal)} />
      )}
      {series(d => d.waterL).length > 1 && (
        <MetricChart label="Water" unit=" L" decimals={1} series={series(d => d.waterL)} />
      )}
      {series(d => d.fastingH).length > 1 && (
        <MetricChart label="Fasting hours" unit=" h" decimals={1} series={series(d => d.fastingH)} />
      )}

      {withData.length > 0 && (
        <Text style={[styles.caption, { color: t.mutedText }]}>
          Logged in Ember on your phone, read back from intervals.icu. Read-only here.
        </Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  label: { fontSize: v3Type.label, fontWeight: '600', marginBottom: v3Spacing.small },
  tiles: { flexDirection: 'row', flexWrap: 'wrap' },
  tile: { flex: 1, minWidth: '22%', paddingVertical: 10, alignItems: 'center', marginHorizontal: 3 },
  tileLabel: { fontSize: v3Type.tiny, fontWeight: '600' },
  tileValue: { fontSize: v3Type.bodyLarge, fontWeight: '700' },
  caption: { fontSize: v3Type.caption },
});
