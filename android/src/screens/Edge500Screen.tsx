import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, ActivityIndicator } from 'react-native';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Button, StatusNote } from '../components/ui/primitives';
import { Card } from '../components/ui/Card';
import { getAthleteThresholds } from '../services/ApiIntervalsIcu';
import { diffProfile, fmtProfile, PROFILE_LABELS, type ProfileDiff } from '../services/MageneProfileSync';
import {
  connectedEdge, edgeDisplayName, readEdgeProfile, writeEdgeProfile, edgeProfileForDiff, EDGE_FIELD,
} from '../services/Edge500Usb';
import type { Edge500Profile, Edge500Field } from '../services/Edge500Profile';

// GPS settings for a Garmin Edge 500 - the Android counterpart of the desktop GPS settings page's
// Edge profile card + "Sync profile" dialog (GpsSettingsPage.qml isEdge, /api/edge500/profile/*),
// laid out like the Bryton screen. intervals.icu is the source of truth and values go one way
// only, onto the device (André, 2026-10-27). The Edge's own files are never written: patched
// copies go into Garmin/NewFiles and the Edge loads them when it is unplugged.

export default function Edge500Screen() {
  const t = useV3Theme();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [device, setDevice] = useState<Edge500Profile | null>(null);
  const [diff, setDiff] = useState<ProfileDiff[] | null>(null);   // null = intervals.icu not connected
  const [applying, setApplying] = useState(false);
  const [applyMsg, setApplyMsg] = useState('');
  const [applyOk, setApplyOk] = useState(false);
  const vol = connectedEdge();

  const load = useCallback(async () => {
    setStatus('loading'); setError('');
    try {
      const dev = await readEdgeProfile();
      setDevice(dev);
      const icu = await getAthleteThresholds().catch(() => null);
      setDiff(icu ? diffProfile(edgeProfileForDiff(dev) as any, icu) : null);
      setStatus('ready');
    } catch (e: any) {
      setError(String(e?.message ?? e)); setStatus('error');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const apply = useCallback(async () => {
    if (!diff?.length) return;
    setApplying(true); setApplyMsg('');
    const changes: Partial<Record<Edge500Field, number>> = {};
    for (const d of diff) changes[EDGE_FIELD[d.field]] = d.intervals;
    try {
      await writeEdgeProfile(changes);
      setApplyOk(true);
      setApplyMsg('Profile ready ✓ — unplug the Edge to load it.');
    } catch (e: any) {
      setApplyOk(false);
      setApplyMsg(String(e?.message ?? e));
    } finally {
      setApplying(false);
    }
  }, [diff]);

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium, alignItems: 'center' }}>
      <View style={{ width: '100%', maxWidth: 640, gap: v3Spacing.medium }}>

        {status === 'loading' && (
          <Card><View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small }}>
            <ActivityIndicator color={t.primary} />
            <Text style={{ color: t.text, fontSize: v3Type.body }}>Reading the Edge profile…</Text>
          </View></Card>
        )}

        {status === 'error' && (
          <Card>
            <Text style={{ color: t.error, fontSize: v3Type.body, marginBottom: v3Spacing.small }}>{error}</Text>
            <Text style={{ color: t.mutedText, fontSize: v3Type.body, marginBottom: v3Spacing.medium }}>
              Plug the Edge 500 into this device with a USB-OTG cable that carries data, wait for it on Home, then retry.
            </Text>
            <Button label="Retry" icon="sync" onPress={load} grow={false} />
          </Card>
        )}

        {status === 'ready' && device && (
          <>
            <Card>
              <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>{edgeDisplayName(vol)}</Text>
              {!!vol?.firmwareVersion && (
                <Text style={{ color: t.mutedText, fontSize: v3Type.body, marginTop: 2 }}>Firmware {vol.firmwareVersion}</Text>
              )}
              <Text style={{ color: t.mutedText, fontSize: v3Type.body, marginTop: 2 }}>
                Device profile: FTP {device.ftp ?? '—'} W · LTHR {device.lthr ?? '—'} · Max HR {device.max_hr ?? '—'} · {device.weight ?? '—'} kg
              </Text>
            </Card>

            <Card>
              <Text style={{ color: t.text, fontSize: v3Type.heading, fontWeight: '700' }}>Profile</Text>
              <Text style={{ color: t.mutedText, fontSize: v3Type.body, marginTop: 2, marginBottom: v3Spacing.medium }}>
                FTP, LTHR, max HR, weight, height, gender and age — compare with intervals.icu and pick the right values. The Edge's zones follow max HR and FTP. Unplug the Edge afterwards: it loads the new profile when it leaves USB mode.
              </Text>
              {diff === null && (
                <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
                  Connect intervals.icu in Settings to compare the profile.
                </Text>
              )}
              {diff !== null && diff.length === 0 && (
                <Text style={{ color: t.success, fontSize: v3Type.body }}>Device and intervals.icu already match. ✓</Text>
              )}
              {diff !== null && diff.length > 0 && (
                <>
                  <Text style={{ color: t.mutedText, fontSize: v3Type.body, marginBottom: v3Spacing.medium }}>
                    These differ. Apply writes the intervals.icu values to the Edge.
                  </Text>
                  {diff.map(d => (
                    <View key={d.field} style={{ marginBottom: v3Spacing.medium }}>
                      <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '600', marginBottom: 6 }}>{PROFILE_LABELS[d.field]}</Text>
                      <Text style={{ color: t.text, fontSize: v3Type.body }}>
                        {fmtProfile(d.field, d.device)} → {fmtProfile(d.field, d.intervals)}
                      </Text>
                    </View>
                  ))}
                  <StatusNote text={applyMsg} tone={applyOk ? 'success' : 'error'} />
                  <Button label="Apply" busy={applying} icon="check" onPress={apply} disabled={applying || applyOk} />
                </>
              )}
            </Card>
          </>
        )}
      </View>
    </ScrollView>
  );
}
