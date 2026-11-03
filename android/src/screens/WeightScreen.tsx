import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity,
  ActivityIndicator, RefreshControl,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import { fmtDate } from '../i18n';
import {
  loadWeightSeries, addManualWeight, removeManualWeight, WeightPoint,
} from '../services/WellnessService';
import { getScaleAddress, onScaleChange, resumeIfConfigured, scaleStatus } from '../services/ScaleService';
import { ScaleSetupModal } from '../components/ScaleSetupModal';
import { Button, EmptyState, StatusChip, StatusNote } from '../components/ui/primitives';
import { ErrorBanner } from '../components/ui/ErrorBanner';

// Where a reading came from, in the words used everywhere (glossary: "source").
const SOURCE_LABEL: Record<string, string> = {
  scale: 'Scale', intervals: 'intervals.icu', manual: 'Manual', garmin: 'Garmin',
};
function timeAgo(tSec: number | null): string {
  if (!tSec) return 'none yet';
  const s = Math.max(0, Math.round(Date.now() / 1000 - tSec));
  if (s < 90) return 'just now';
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

// Weight — the Android counterpart of desktop/qml/pages/WeightPage.qml (André, 2026-08-26:
// "port everything to android"). Body-weight history from intervals.icu, merged with manual
// weigh-ins typed here.
//
// Differences from the desktop version, all forced by what Android can reach:
//   - No Garmin Connect feed (Android has no backend and no Garmin cloud client; its GarminModule
//     is the USB eTrex). Body composition here comes ONLY from a Bluetooth scale (estimated).
//   - No source picker, because there is only one remote provider (intervals.icu) to pick.
//     See WellnessService.ts's header for the full reasoning.
//   - The scale is heard while the app is open (no background service yet).

export default function WeightScreen() {
  const t = useV3Theme();
  const navigation = useNavigation<any>();
  const [series, setSeries] = useState<WeightPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [dateInput, setDateInput] = useState('');
  const [kgInput, setKgInput] = useState('');
  const [scaleOpen, setScaleOpen] = useState(false);
  const [scaleAddr, setScaleAddr] = useState<string | null>(null);
  const [scaleTick, setScaleTick] = useState(0);          // re-render when the scale reports
  const [fresh, setFresh] = useState('');                 // "74.6 kg" for a few seconds
  const [formError, setFormError] = useState('');         // why Save didn't save, under the form

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

  // The scale reports by itself: a new weigh-in appears with a short highlight, no pull to refresh.
  useEffect(() => {
    getScaleAddress().then(setScaleAddr);
    resumeIfConfigured().catch(() => {});
    let timer: ReturnType<typeof setTimeout> | undefined;
    const off = onScaleChange(async () => {
      setScaleTick(n => n + 1);
      setScaleAddr(await getScaleAddress());
      const s = await loadWeightSeries();
      setSeries(s);
      {
        const last = s[s.length - 1] as (WeightPoint & { t?: number }) | undefined;
        // Highlight only a weigh-in that just arrived, not one that was already there.
        if (last?.source === 'scale' && last.t && Date.now() / 1000 - last.t < 60) {
          setFresh(`${last.weightKg.toFixed(1)} kg`);
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => setFresh(''), 10000);
        }
      }
    });
    return () => { off(); if (timer) clearTimeout(timer); };
  }, []);
  const sc = scaleStatus();
  void scaleTick;
  const lastScale = [...series].reverse().find(p => p.source === 'scale') as (WeightPoint & { t?: number }) | undefined;

  const latest = series.length > 0 ? series[series.length - 1] : null;
  // Change across the whole loaded window - the same "over N weigh-ins" figure the desktop
  // shows, rather than a day-over-day delta that would mostly be noise.
  const change = series.length > 1 ? latest!.weightKg - series[0].weightKg : null;

  async function saveManual() {
    const kg = parseFloat(kgInput.replace(',', '.'));
    // #10 (André, 2026-09-02): tell the user why, instead of silently doing nothing.
    // WeightScreen uses plain literals (no i18n import), so keep these literal.
    // Day-first like every other date in the app (André, 2026-10-03: "Use only the first for
    // everything"); the stored key stays ISO. The old YYYY-MM-DD typing is still understood.
    const dm = dateInput.trim().match(/^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/);
    const iso = dm ? `${dm[3]}-${dm[2].padStart(2, '0')}-${dm[1].padStart(2, '0')}`
      : /^\d{4}-\d{2}-\d{2}$/.test(dateInput.trim()) ? dateInput.trim() : '';
    // Shown as a note under the form (desktop picks #7), not a pop-up.
    if (!iso || isNaN(new Date(iso).getTime())) {
      setFormError('Enter the date as DD/MM/YYYY (for example 02/09/2026).');
      return;
    }
    if (!(kg > 0)) {
      setFormError('Enter a valid weight in kg.');
      return;
    }
    setFormError('');
    await addManualWeight(iso, kg);
    setShowAdd(false); setKgInput('');
    refresh();
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {/* Bluetooth scale status - tap to set up or manage. The state is a tag, like Settings ›
          Connections (desktop picks #6). */}
      <TouchableOpacity onPress={() => setScaleOpen(true)}
        style={[styles.row, { marginBottom: v3Spacing.medium, flexWrap: 'wrap', gap: v3Spacing.small }]}>
        <Text style={{ color: t.text, fontSize: v3Type.body }}>Bluetooth scale</Text>
        <StatusChip
          label={!scaleAddr ? 'Not set up' : sc.listening ? 'Listening' : 'Not listening'}
          tone={sc.listening ? 'success' : 'neutral'}
        />
        {(!scaleAddr || sc.listening) && (
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            {!scaleAddr ? 'tap to set up' : `last weigh-in ${timeAgo(lastScale?.t ?? null)}`}
          </Text>
        )}
        {fresh.length > 0 && (
          <Text style={{ color: t.success, fontSize: v3Type.body, fontWeight: '700' }}>
            New weigh-in: {fresh}
          </Text>
        )}
      </TouchableOpacity>
      <ScaleSetupModal visible={scaleOpen} onClose={() => setScaleOpen(false)}
        onChanged={async () => { setScaleAddr(await getScaleAddress()); setScaleTick(n => n + 1); refresh(); }} />

      {loading && series.length === 0 && (
        <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />
      )}

      {/* A failed read: one plain sentence, Try again and Send logs (desktop picks #19). */}
      <ErrorBanner
        detail={loading ? '' : error}
        context="reading weigh-ins"
        showPlainDetail
        onRetry={refresh}
      />

      {/* Nothing yet: what to do next (desktop picks #11). #9 (André, 2026-09-02): one-tap
          route to the shared connection settings. */}
      {!loading && series.length === 0 && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <EmptyState
            icon="weight"
            title="No weigh-ins yet"
            text="Nothing in the last year. Connect intervals.icu, add one by hand below, or step on your Bluetooth scale."
            actionLabel="Open Settings → Connections"
            onAction={() => navigation.navigate('Settings', { section: 'connections' })}
          />
        </View>
      )}

      {latest && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <View style={styles.row}>
            <View>
              <Text style={[styles.label, { color: t.mutedText }]}>Latest</Text>
              <Text style={[styles.big, { color: t.text }]}>{latest.weightKg.toFixed(1)} kg</Text>
              <Text style={[styles.caption, { color: t.mutedText }]}>
                {fmtDate(latest.date) || latest.date}{SOURCE_LABEL[latest.source] ? `  ·  ${SOURCE_LABEL[latest.source]}` : ''}
              </Text>
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

      {/* Body composition: only from the Bluetooth scale, and always an estimate */}
      {latest?.bodyFatPct !== undefined && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700', marginBottom: v3Spacing.small }}>
            {latest.estimated ? 'Body composition (estimated)' : 'Body composition'}
          </Text>
          <View style={[styles.row, { flexWrap: 'wrap' }]}>
            {([['Body fat', latest.bodyFatPct, '%'], ['Body water', latest.bodyWaterPct, '%'], ['BMI', latest.bmi, '']] as const)
              .filter(([, v]) => v !== undefined)
              .map(([label, v, unit]) => (
                <View key={label} style={{ marginRight: v3Spacing.large, marginBottom: v3Spacing.small }}>
                  <Text style={[styles.label, { color: t.mutedText }]}>{label}</Text>
                  <Text style={{ color: t.text, fontSize: v3Type.subtitle, fontWeight: '700' }}>{v}{unit}</Text>
                </View>
              ))}
          </View>
        </View>
      )}
      {latest?.bodyFatPct !== undefined && latest.estimated && (
        <Text style={[styles.caption, { color: t.mutedText, marginBottom: v3Spacing.medium }]}>
          Estimated from the scale's impedance (Sun et al. 2003). A foot-to-foot scale can be off by several points, so follow the trend, not one reading.
        </Text>
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
            setDateInput(fmtDate(new Date()));
            setFormError('');
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
              placeholder="DD/MM/YYYY"
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
          <StatusNote text={formError} tone="error" style={{ marginTop: v3Spacing.small }} />
          <View style={[styles.row, { marginTop: v3Spacing.small, justifyContent: 'flex-end' }]}>
            <TouchableOpacity
              onPress={() => { setShowAdd(false); setFormError(''); }}
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
              <Text style={{ color: t.text, fontSize: v3Type.body }}>{fmtDate(p.date) || p.date}</Text>
              <Text style={{ color: t.text, fontSize: v3Type.body }}>{p.weightKg.toFixed(1)} kg</Text>
              {/* Remove is a calm red button (desktop picks #5, RoundedButton.danger). */}
              <Button label="Remove" danger grow={false}
                style={{ paddingVertical: 5, paddingHorizontal: 12 }}
                onPress={async () => { await removeManualWeight(p.date); refresh(); }} />
            </View>
          ))}
        </View>
      )}

      <Text style={[styles.caption, { color: t.mutedText, marginTop: v3Spacing.small }]}>
        Merged from your Bluetooth scale, intervals.icu and manual entries. For a day your scale (or
        you, by hand) has a reading, that one is shown; otherwise the intervals.icu reading.
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
