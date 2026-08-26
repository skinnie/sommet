import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity,
  ActivityIndicator, RefreshControl,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import {
  loadWeightSeries, addManualWeight, removeManualWeight, WeightPoint,
} from '../services/WellnessService';

// Weight — the Android counterpart of desktop/qml/pages/WeightPage.qml (André, 2026-08-26:
// "port everything to android"). Body-weight history from intervals.icu, merged with manual
// weigh-ins typed here.
//
// Two honest differences from the desktop version, both forced by what Android can reach:
//   - No Garmin Index scale feed, and so no body-composition rows (fat/muscle/water). The
//     desktop gets those through the Python backend's python-garminconnect; Android has no
//     backend and no Garmin Connect cloud client (its GarminModule is the USB eTrex).
//   - No source picker, because with Garmin unavailable there is only one remote provider to
//     pick. See WellnessService.ts's header for the full reasoning.

export default function WeightScreen() {
  const t = useV3Theme();
  const [series, setSeries] = useState<WeightPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [dateInput, setDateInput] = useState('');
  const [kgInput, setKgInput] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setSeries(await loadWeightSeries());
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const latest = series.length > 0 ? series[series.length - 1] : null;
  // Change across the whole loaded window - the same "over N weigh-ins" figure the desktop
  // shows, rather than a day-over-day delta that would mostly be noise.
  const change = series.length > 1 ? latest!.weightKg - series[0].weightKg : null;

  async function saveManual() {
    const kg = parseFloat(kgInput.replace(',', '.'));
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateInput) || !(kg > 0)) return;
    await addManualWeight(dateInput, kg);
    setShowAdd(false); setKgInput('');
    refresh();
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && series.length === 0 && (
        <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />
      )}

      {error.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.error, fontSize: v3Type.body }}>{error}</Text>
        </View>
      )}

      {!loading && series.length === 0 && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            No weigh-ins yet. Connect intervals.icu in Settings, or add one below.
          </Text>
        </View>
      )}

      {latest && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <View style={styles.row}>
            <View>
              <Text style={[styles.label, { color: t.mutedText }]}>Latest</Text>
              <Text style={[styles.big, { color: t.text }]}>{latest.weightKg.toFixed(1)} kg</Text>
              <Text style={[styles.caption, { color: t.mutedText }]}>{latest.date}</Text>
            </View>
            {change !== null && (
              <View style={{ marginLeft: v3Spacing.large * 2 }}>
                <Text style={[styles.label, { color: t.mutedText }]}>Change</Text>
                <Text style={[styles.mid, { color: change > 0 ? t.warning : change < 0 ? t.success : t.mutedText }]}>
                  {change > 0 ? '+' : ''}{change.toFixed(1)} kg
                </Text>
                <Text style={[styles.caption, { color: t.mutedText }]}>
                  over {series.length} weigh-ins
                </Text>
              </View>
            )}
          </View>
        </View>
      )}

      {series.length > 1 && (
        <MetricChart
          label="Weight"
          unit=" kg"
          decimals={1}
          series={series.map(p => ({ date: p.date, value: p.weightKg }))}
        />
      )}

      {/* Manual weigh-in */}
      {!showAdd ? (
        <TouchableOpacity
          onPress={() => {
            setDateInput(new Date().toISOString().slice(0, 10));
            setShowAdd(true);
          }}
          style={[styles.button, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]}
        >
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '600' }}>+ Add weigh-in</Text>
        </TouchableOpacity>
      ) : (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <View style={styles.row}>
            <TextInput
              value={dateInput}
              onChangeText={setDateInput}
              placeholder="YYYY-MM-DD"
              placeholderTextColor={t.mutedText}
              style={[styles.input, { color: t.text, backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small, flex: 1.4 }]}
            />
            <TextInput
              value={kgInput}
              onChangeText={setKgInput}
              placeholder="kg"
              placeholderTextColor={t.mutedText}
              keyboardType="decimal-pad"
              style={[styles.input, { color: t.text, backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small, flex: 1 }]}
            />
          </View>
          <View style={[styles.row, { marginTop: v3Spacing.small, justifyContent: 'flex-end' }]}>
            <TouchableOpacity
              onPress={() => setShowAdd(false)}
              style={[styles.button, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small, marginRight: v3Spacing.small }]}
            >
              <Text style={{ color: t.text, fontSize: v3Type.body }}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={saveManual}
              style={[styles.button, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]}
            >
              <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '600' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* Manual entries are the only rows a user can remove - intervals readings belong to
          intervals.icu and are not editable from here. */}
      {series.filter(p => p.source === 'manual').length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={[styles.label, { color: t.mutedText, marginBottom: v3Spacing.small }]}>Manual entries</Text>
          {series.filter(p => p.source === 'manual').reverse().map(p => (
            <View key={p.date} style={[styles.row, { justifyContent: 'space-between', paddingVertical: 6 }]}>
              <Text style={{ color: t.text, fontSize: v3Type.body }}>{p.date}</Text>
              <Text style={{ color: t.text, fontSize: v3Type.body }}>{p.weightKg.toFixed(1)} kg</Text>
              <TouchableOpacity onPress={async () => { await removeManualWeight(p.date); refresh(); }} hitSlop={8}>
                <Text style={{ color: t.error, fontSize: v3Type.body }}>Remove</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      )}

      <Text style={[styles.caption, { color: t.mutedText, marginTop: v3Spacing.small }]}>
        From intervals.icu plus your own manual weigh-ins. Body composition (fat, muscle) needs a
        Garmin Index scale, which only the desktop app can read.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  row: { flexDirection: 'row', alignItems: 'center' },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  big: { fontSize: v3Type.display, fontWeight: '700' },
  mid: { fontSize: v3Type.title, fontWeight: '700' },
  caption: { fontSize: v3Type.caption },
  button: { borderWidth: 1, paddingVertical: 10, paddingHorizontal: v3Spacing.medium, alignItems: 'center', marginBottom: v3Spacing.medium },
  input: { borderWidth: 1, paddingHorizontal: v3Spacing.small, paddingVertical: 8, fontSize: v3Type.body, marginRight: v3Spacing.small },
});
