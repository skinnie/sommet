import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, ActivityIndicator } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useV3Theme, v3Spacing, v3Radius, v3Type } from '../theme/v3';
import { Button, StatusLine, Toggle } from '../components/ui/primitives';
import { Card } from '../components/ui/Card';
import {
  connectBryton, disconnectBryton, readProfile, writeProfile, isBrytonUsbAvailable,
} from '../services/BrytonUsb';
import { getAthleteThresholds, putAthleteThresholds } from '../services/ApiIntervalsIcu';
import type { BrytonProfile } from '../services/BrytonProfile';

// Bryton Aero 60 hub — the Android counterpart of the desktop Home Bryton card + "Sync profile"
// dialog (BrytonProfileDialog.qml). Connects to the Aero 60 over USB (BrytonUsb / libaums),
// reconciles its FTP/LTHR/Max HR/weight against intervals.icu, and links to the Workout Builder.
// Responsive: a single centred column that stays readable on a phone and doesn't sprawl on a tablet.

type Field = 'ftp' | 'lthr' | 'maxHr' | 'weight';
const FIELDS: Field[] = ['ftp', 'lthr', 'maxHr', 'weight'];
const LABELS: Record<Field, string> = { ftp: 'FTP (W)', lthr: 'LTHR (bpm)', maxHr: 'Max HR (bpm)', weight: 'Weight (kg)' };
const PREF_KEY = 'brytonProfileSource';

export default function BrytonScreen() {
  const t = useV3Theme();
  const navigation = useNavigation<any>();

  const [status, setStatus] = useState<'idle' | 'connecting' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const [device, setDevice] = useState<BrytonProfile | null>(null);
  const [intervals, setIntervals] = useState<Record<Field, number | undefined> | null>(null);
  const [choice, setChoice] = useState<Record<Field, 'device' | 'intervals'>>({
    ftp: 'intervals', lthr: 'intervals', maxHr: 'intervals', weight: 'intervals',
  });
  const [remember, setRemember] = useState(true);
  const [applying, setApplying] = useState(false);
  const [applyMsg, setApplyMsg] = useState('');

  const connect = useCallback(async () => {
    setStatus('connecting'); setError(''); setApplyMsg('');
    try {
      const res = await connectBryton();
      setDeviceName(res.name || 'Bryton Aero 60');
      const dev = await readProfile();
      setDevice(dev);
      const icu = await getAthleteThresholds();
      setIntervals(icu ? { ftp: icu.ftp, lthr: icu.lthr, maxHr: icu.maxHr, weight: icu.weight } : null);
      // restore remembered per-field sources
      try {
        const raw = await AsyncStorage.getItem(PREF_KEY);
        if (raw) setChoice(c => ({ ...c, ...JSON.parse(raw) }));
      } catch { /* ignore */ }
      setStatus('ready');
    } catch (e: any) {
      setError(String(e?.message ?? e)); setStatus('error');
    }
  }, []);

  useEffect(() => { connect(); return () => { disconnectBryton(); }; }, [connect]);

  const diffs: Field[] = device && intervals
    ? FIELDS.filter(f => {
        const dv = device[f], iv = intervals[f];
        if (dv == null || iv == null) return false;
        return f === 'weight' ? Math.abs(dv - iv) >= 0.5 : dv !== iv;
      })
    : [];

  const apply = useCallback(async () => {
    if (!device || !intervals) return;
    setApplying(true); setApplyMsg('');
    const toDevice: Partial<BrytonProfile> = {};
    const toIntervals: { ftp?: number; lthr?: number; maxHr?: number; weight?: number } = {};
    for (const f of diffs) {
      if (choice[f] === 'intervals') (toDevice as any)[f] = intervals[f];
      else (toIntervals as any)[f] = device[f];
    }
    try {
      if (remember) await AsyncStorage.setItem(PREF_KEY, JSON.stringify(choice));
      if (Object.keys(toDevice).length) await writeProfile(toDevice);
      if (Object.keys(toIntervals).length) await putAthleteThresholds(toIntervals);
      setApplyMsg('Saved.');
      await connect();
    } catch (e: any) {
      setApplyMsg(String(e?.message ?? e));
    } finally {
      setApplying(false);
    }
  }, [device, intervals, diffs, choice, remember, connect]);

  const fmt = (f: Field, v: number | undefined) => (v == null ? '—' : String(v));

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium, alignItems: 'center' }}>
      <View style={{ width: '100%', maxWidth: 640, gap: v3Spacing.medium }}>

        {!isBrytonUsbAvailable() && (
          <Card><Text style={{ color: t.error }}>USB support isn't available in this build.</Text></Card>
        )}

        {status === 'connecting' && (
          <Card><View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small }}>
            <ActivityIndicator color={t.primary} />
            <Text style={{ color: t.text }}>Connecting to the Bryton over USB…</Text>
          </View></Card>
        )}

        {status === 'error' && (
          <Card>
            <Text style={{ color: t.error, marginBottom: v3Spacing.small }}>{error}</Text>
            <Text style={{ color: t.mutedText, marginBottom: v3Spacing.medium }}>
              Plug the Aero 60 into this device with a USB-OTG cable that carries data, then retry.
            </Text>
            <Button label="Retry" icon="sync" onPress={connect} grow={false} />
          </Card>
        )}

        {status === 'ready' && device && (
          <>
            <Card>
              <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>{deviceName}</Text>
              <Text style={{ color: t.mutedText, marginTop: 2 }}>
                Device profile: FTP {device.ftp} W · LTHR {device.lthr} · Max HR {device.maxHr} · {device.weight} kg
              </Text>
            </Card>

            {!intervals && (
              <Card><Text style={{ color: t.mutedText }}>
                Connect intervals.icu in Settings to reconcile FTP / LTHR / Max HR / weight.
              </Text></Card>
            )}

            {intervals && diffs.length === 0 && (
              <Card><Text style={{ color: t.success }}>Device and intervals.icu already match. ✓</Text></Card>
            )}

            {intervals && diffs.length > 0 && (
              <Card>
                <Text style={{ color: t.text, fontSize: v3Type.heading, fontWeight: '700' }}>Sync profile</Text>
                <Text style={{ color: t.mutedText, marginTop: 2, marginBottom: v3Spacing.medium }}>
                  Pick the value that's right for each — it's written to the device and/or intervals.icu.
                </Text>

                {diffs.map(f => (
                  <View key={f} style={{ marginBottom: v3Spacing.medium }}>
                    <Text style={{ color: t.text, fontWeight: '600', marginBottom: 6 }}>{LABELS[f]}</Text>
                    <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
                      {(['device', 'intervals'] as const).map(src => {
                        const sel = choice[f] === src;
                        const val = src === 'device' ? device[f] : intervals[f];
                        return (
                          <TouchableOpacity key={src} activeOpacity={0.8}
                            onPress={() => setChoice(c => ({ ...c, [f]: src }))}
                            style={{
                              flex: 1, padding: v3Spacing.small, borderRadius: v3Radius.small,
                              borderWidth: 1, borderColor: sel ? t.primary : t.border,
                              backgroundColor: sel ? t.primary : t.card, alignItems: 'center',
                            }}>
                            <Text style={{ color: sel ? t.card : t.mutedText, fontSize: v3Type.caption }}>
                              {src === 'device' ? 'Bryton' : 'intervals.icu'}
                            </Text>
                            <Text style={{ color: sel ? t.card : t.text, fontWeight: '700' }}>{fmt(f, val)}</Text>
                          </TouchableOpacity>
                        );
                      })}
                    </View>
                  </View>
                ))}

                <View style={{ marginBottom: v3Spacing.medium }}>
                  <Toggle value={remember} onValueChange={setRemember} />
                  <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>Remember these choices</Text>
                </View>

                {applyMsg ? <StatusLine text={applyMsg} tone={applyMsg === 'Saved.' ? 'muted' : 'alert'} /> : null}
                <Button label={applying ? 'Saving…' : 'Apply'} icon="check"
                  onPress={apply} disabled={applying} />
              </Card>
            )}
          </>
        )}
      </View>
    </ScrollView>
  );
}
