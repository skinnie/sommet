import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, TextInput, TouchableOpacity } from 'react-native';
import { useV3Theme, v3Spacing, v3Radius, v3Type } from '../theme/v3';
import { Button, StatusLine } from '../components/ui/primitives';
import { Card } from '../components/ui/Card';
import { connectBryton, readProfile, sendNativeWorkout } from '../services/BrytonUsb';
import type {
  BrytonUnit, BrytonIntensity, BrytonMode, BrytonStep, BrytonWorkout,
} from '../services/BrytonFit';
import type { BrytonProfile } from '../services/BrytonProfile';

// Bryton Workout Builder — the Android twin of desktop BrytonWorkoutBuilderPage.qml, mirroring the
// Bryton app's "Plan Workout" screen: pick UNIT / BASED ON / INTERVAL once, then a flat list of
// Warm Up / Work / Recovery / Cool Down steps. Targets are typed straight in the unit (%FTP/…), and
// for % units we show the watt/bpm the device would resolve, using the device's own thresholds.
// Sends as a native .fit into System/Plan/Cycling. Responsive: one centred column, max 640 wide.

const UNITS: { key: BrytonUnit; label: string }[] = [
  { key: 'ftp', label: 'FTP' }, { key: 'mhr', label: 'MHR' }, { key: 'lthr', label: 'LTHR' },
  { key: 'speed', label: 'Speed' }, { key: 'cadence', label: 'Cadence' },
];
const INTENSITIES: { key: BrytonIntensity; label: string }[] = [
  { key: 'warmup', label: 'Warm Up' }, { key: 'work', label: 'Work' },
  { key: 'recovery', label: 'Recovery' }, { key: 'cooldown', label: 'Cool Down' },
];

interface Row { intensity: BrytonIntensity; durVal: string; low: string; high: string; }

// Module-scope so their component identity is stable across renders — an inline component defined
// in the render body is recreated every render and, under Fabric, its <Text> failed to paint
// (André saw empty selector boxes, 2026-09-24).
function Seg({ t, selected, label, onPress }:
  { t: any; selected: boolean; label: string; onPress: () => void }) {
  return (
    <TouchableOpacity activeOpacity={0.8} onPress={onPress}
      style={{
        paddingVertical: 8, paddingHorizontal: 12, borderRadius: v3Radius.small, borderWidth: 1,
        borderColor: selected ? t.primary : t.border, backgroundColor: selected ? t.primary : t.card,
      }}>
      <Text style={{ color: selected ? t.card : t.text, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  );
}

function NumField({ t, value, onChange, w = 64 }:
  { t: any; value: string; onChange: (s: string) => void; w?: number }) {
  return (
    <TextInput value={value} onChangeText={onChange} keyboardType="numeric"
      style={{
        width: w, borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small,
        color: t.text, backgroundColor: t.surface, paddingHorizontal: 8, paddingVertical: 6,
      }} placeholderTextColor={t.mutedText} />
  );
}

export default function BrytonWorkoutBuilderScreen() {
  const t = useV3Theme();

  const [name, setName] = useState('New Workout');
  const [unit, setUnit] = useState<BrytonUnit>('ftp');
  const [rangeMode, setRangeMode] = useState(true);
  const [intervalMode, setIntervalMode] = useState<BrytonMode>('time');
  const [rows, setRows] = useState<Row[]>([
    { intensity: 'warmup', durVal: '10', low: '55', high: '65' },
    { intensity: 'work', durVal: '5', low: '88', high: '95' },
    { intensity: 'cooldown', durVal: '10', low: '50', high: '55' },
  ]);
  const [device, setDevice] = useState<BrytonProfile | null>(null);
  const [msg, setMsg] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    (async () => {
      try { await connectBryton(); setDevice(await readProfile()); } catch { /* preview only */ }
    })();
  }, []);

  const isPct = unit === 'ftp' || unit === 'mhr' || unit === 'lthr';
  const suffix = unit === 'speed' ? 'km/h' : unit === 'cadence' ? 'rpm' : '%';

  const preview = (pct: number): string => {
    if (!device || !isPct) return '';
    const base = unit === 'ftp' ? device.ftp : unit === 'mhr' ? device.maxHr : device.lthr;
    if (!base) return '';
    const v = Math.round((pct / 100) * base);
    return `≈ ${v}${unit === 'ftp' ? ' W' : ' bpm'}`;
  };

  const addStep = (intensity: BrytonIntensity) => {
    const lo = isPct ? (intensity === 'work' ? '88' : '55') : unit === 'cadence' ? '85' : '25';
    const hi = isPct ? (intensity === 'work' ? '95' : '65') : unit === 'cadence' ? '90' : '30';
    setRows(r => [...r, { intensity, durVal: intensity === 'work' ? '5' : '10', low: lo, high: hi }]);
  };
  const setRow = (i: number, patch: Partial<Row>) =>
    setRows(r => r.map((row, j) => (j === i ? { ...row, ...patch } : row)));
  const removeRow = (i: number) => setRows(r => r.filter((_, j) => j !== i));

  const send = useCallback(async () => {
    if (rows.length === 0) { setMsg('Add at least one step.'); return; }
    setSending(true); setMsg('');
    try {
      const steps: BrytonStep[] = rows.map(r => {
        const dur = intervalMode === 'time' ? Math.round(Number(r.durVal) * 60) : Math.round(Number(r.durVal) * 1000);
        const low = Number(r.low);
        return { intensity: r.intensity, duration: dur, low, high: rangeMode ? Number(r.high) : low };
      });
      const w: BrytonWorkout = {
        name, unit, basedOn: rangeMode ? 'range' : 'target', intervalMode, steps,
      };
      const file = await sendNativeWorkout(w);
      setMsg(`Sent “${file}” to the Bryton.`);
    } catch (e: any) {
      setMsg(String(e?.message ?? e));
    } finally {
      setSending(false);
    }
  }, [rows, name, unit, rangeMode, intervalMode]);


  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium, alignItems: 'center' }}>
      <View style={{ width: '100%', maxWidth: 640, gap: v3Spacing.medium }}>

        <Card>
          <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>Workout Builder</Text>
          <Text style={{ color: t.mutedText, marginTop: 2 }}>
            Targets are in the workout's unit; the watt/bpm shown uses the device's own thresholds.
          </Text>

          <Text style={{ color: t.mutedText, marginTop: v3Spacing.medium, marginBottom: 4 }}>Name</Text>
          <TextInput value={name} onChangeText={setName}
            style={{
              borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text,
              backgroundColor: t.surface, paddingHorizontal: 10, paddingVertical: 8,
            }} placeholderTextColor={t.mutedText} />

          <Text style={{ color: t.mutedText, marginTop: v3Spacing.medium, marginBottom: 4 }}>Unit</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small }}>
            {UNITS.map(u => <Seg t={t} key={u.key} selected={unit === u.key} label={u.label} onPress={() => setUnit(u.key)} />)}
          </View>

          <Text style={{ color: t.mutedText, marginTop: v3Spacing.medium, marginBottom: 4 }}>Based on</Text>
          <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
            <Seg t={t} selected={rangeMode} label="Range" onPress={() => setRangeMode(true)} />
            <Seg t={t} selected={!rangeMode} label="Target" onPress={() => setRangeMode(false)} />
          </View>

          <Text style={{ color: t.mutedText, marginTop: v3Spacing.medium, marginBottom: 4 }}>Interval</Text>
          <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
            <Seg t={t} selected={intervalMode === 'time'} label="Time" onPress={() => setIntervalMode('time')} />
            <Seg t={t} selected={intervalMode === 'distance'} label="Distance" onPress={() => setIntervalMode('distance')} />
          </View>
        </Card>

        <Card>
          <Text style={{ color: t.text, fontSize: v3Type.heading, fontWeight: '700', marginBottom: v3Spacing.small }}>Steps</Text>
          {rows.map((r, i) => (
            <View key={i} style={{
              backgroundColor: t.surface, borderRadius: v3Radius.small, padding: v3Spacing.small,
              marginBottom: v3Spacing.small, gap: 6,
            }}>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                {INTENSITIES.map(it => (
                  <Seg t={t} key={it.key} selected={r.intensity === it.key} label={it.label}
                    onPress={() => setRow(i, { intensity: it.key })} />
                ))}
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small, flexWrap: 'wrap' }}>
                <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>
                  {intervalMode === 'time' ? 'min' : 'km'}
                </Text>
                <NumField t={t} value={r.durVal} onChange={s => setRow(i, { durVal: s })} />
                <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{rangeMode ? `low ${suffix}` : suffix}</Text>
                <NumField t={t} value={r.low} onChange={s => setRow(i, { low: s })} />
                {rangeMode && <>
                  <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{`high ${suffix}`}</Text>
                  <NumField t={t} value={r.high} onChange={s => setRow(i, { high: s })} />
                </>}
                <Text style={{ color: t.secondary, fontSize: v3Type.caption }}>
                  {preview(rangeMode ? Math.round((Number(r.low) + Number(r.high)) / 2) : Number(r.low))}
                </Text>
                <TouchableOpacity onPress={() => removeRow(i)}>
                  <Text style={{ color: t.error, paddingHorizontal: 6 }}>✕</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small, marginTop: 4 }}>
            {INTENSITIES.map(it => (
              <Button key={it.key} label={`+ ${it.label}`} variant="text" grow={false}
                onPress={() => addStep(it.key)} />
            ))}
          </View>
        </Card>

        {msg ? <StatusLine text={msg} tone={msg.startsWith('Sent') ? 'muted' : 'alert'} /> : null}
        <Button label={sending ? 'Sending…' : 'Send to Bryton'} icon="sync" onPress={send} disabled={sending} />
      </View>
    </ScrollView>
  );
}
