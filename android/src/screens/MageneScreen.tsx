import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, TextInput, ActivityIndicator, Modal, Pressable } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useV3Theme, v3Spacing, v3Radius, v3Type } from '../theme/v3';
import { Button, StatusLine, Toggle } from '../components/ui/primitives';
import { Card } from '../components/ui/Card';
import { getAthleteThresholds, putAthleteThresholds, type AthleteThresholds } from '../services/ApiIntervalsIcu';
import {
  withMagene, readProfile, setProfile, readSettings, writeSettings, altitudeCorrect, type MageneProfile,
} from '../services/MageneDevice';
import { sendRoute } from '../services/MageneRoute';
import { readPages, writePages, FIELD_GROUPS, FIELD_NAMES, MAX_FIELDS, MIN_FIELDS, MAX_PAGES } from '../services/MagenePages';
import { pickFile } from '../services/CatalogService';
import { base64ToBytes, bytesToUtf8 } from '../services/Base64';
import { getKnownMagene, type KnownMagene } from '../services/MageneStore';
import { diffProfile, fmtProfile, PROFILE_LABELS, TWO_WAY, type ProfileField } from '../services/MageneProfileSync';

// Magene C406 hub - the Android counterpart of the desktop Home Magene card's Sync profile
// (BrytonProfileDialog in Magene mode), Device settings (MageneSettingsDialog) and Routes
// "Send to Magene". Same layout as BrytonScreen: one centred column, max 640 wide. Every action
// is its own short BLE connection (the C406 sleeps; a held link drains it and blocks other apps).

const PREF_KEY = 'mageneProfileSource';

type Choice = { v: number; t: string };
const onOff = (key: string, label: string) => ({ key, label, kind: 'switch' as const });
const choice = (key: string, label: string, choices: Choice[]) => ({ key, label, kind: 'choice' as const, choices });
const SETTINGS_UI = [
  onOff('autoBacklight', 'Auto backlight'),
  choice('backlightDuration', 'Backlight duration', [{ v: 0, t: 'Always on' }, ...[5, 10, 15, 30, 60].map(s => ({ v: s, t: `${s} s` }))]),
  choice('backlightLevel', 'Backlight level', [{ v: 0, t: 'Low' }, { v: 1, t: 'Medium' }, { v: 2, t: 'High' }]),
  choice('autoOff', 'Auto power-off', [{ v: 0, t: 'Off' }, ...[5, 10, 15, 20, 30, 40, 60].map(m => ({ v: m, t: `${m} min` }))]),
  choice('autoPause', 'Auto pause', [{ v: 0, t: 'Off' }, ...[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(k => ({ v: k, t: `< ${k} km/h` }))]),
  onOff('promptTone', 'Prompt tone'),
  onOff('keyTone', 'Key tone'),
  onOff('startReminder', 'Start-ride reminder'),
  onOff('estimatedPower', 'Estimated power'),
];

function Seg({ t, selected, label, onPress }: { t: any; selected: boolean; label: string; onPress: () => void }) {
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.8}
      style={{
        paddingVertical: 6, paddingHorizontal: 10, borderRadius: v3Radius.small, borderWidth: 1,
        borderColor: selected ? t.primary : t.border, backgroundColor: selected ? t.primary : t.card,
      }}>
      <Text style={{ color: selected ? t.card : t.text, fontSize: v3Type.caption, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  );
}

export default function MageneScreen() {
  const t = useV3Theme();
  const [magene, setMagene] = useState<KnownMagene | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error' | 'none'>('loading');
  const [error, setError] = useState('');
  const [device, setDevice] = useState<MageneProfile | null>(null);
  const [icu, setIcu] = useState<AthleteThresholds | null>(null);
  const [pick, setPick] = useState<Record<string, 'device' | 'intervals'>>({});
  const [remember, setRemember] = useState(true);
  const [settings, setSettings] = useState<Record<string, number> | null>(null);
  const [edited, setEdited] = useState<Record<string, number>>({});
  const [pages, setPages] = useState<number[][] | null>(null);
  const [editedPages, setEditedPages] = useState<number[][]>([]);
  const [picker, setPicker] = useState<{ page: number; slot: number } | null>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    setStatus('loading'); setError(''); setMsg('');
    const m = await getKnownMagene();
    setMagene(m);
    if (!m) { setStatus('none'); return; }
    try {
      const [prof, sets, pgs] = await withMagene(m.address, async () =>
        [await readProfile(), await readSettings(), await readPages().catch(() => null)] as const);
      setPages(pgs);
      setEditedPages(pgs ? pgs.map(p => [...p]) : []);
      setDevice(prof);
      setSettings(sets);
      setEdited(sets ? { ...sets } : {});
      setIcu(await getAthleteThresholds().catch(() => null));
      try { const raw = await AsyncStorage.getItem(PREF_KEY); if (raw) setPick(JSON.parse(raw)); } catch { /* ignore */ }
      setStatus('ready');
    } catch (e: any) {
      setError(String(e?.message ?? e)); setStatus('error');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const diffs = device && icu ? diffProfile(device, icu) : [];
  const sideFor = (f: ProfileField) => (TWO_WAY.includes(f) ? pick[f] ?? 'intervals' : 'intervals');

  const applyProfile = async () => {
    if (!magene || !device || !icu) return;
    setBusy('profile'); setMsg('');
    const toDevice: Partial<MageneProfile> = {};
    const toIcu: { ftp?: number; lthr?: number; maxHr?: number; weight?: number } = {};
    for (const d of diffs) {
      if (sideFor(d.field) === 'intervals') (toDevice as any)[d.field] = d.intervals;
      else (toIcu as any)[d.field] = d.device;
    }
    try {
      if (remember) await AsyncStorage.setItem(PREF_KEY, JSON.stringify(pick));
      if (Object.keys(toDevice).length) {
        const r = await withMagene(magene.address, () => setProfile(toDevice));
        if (!r) throw new Error('The C406 didn’t accept the profile');
      }
      if (Object.keys(toIcu).length && !(await putAthleteThresholds(toIcu))) throw new Error('intervals.icu update failed');
      await load();
      setMsg('Profile saved ✓');
    } catch (e: any) { setMsg(String(e?.message ?? e)); } finally { setBusy(''); }
  };

  const changes = settings ? Object.fromEntries(Object.entries(edited).filter(([k, v]) => settings[k] !== v)) : {};
  const saveSettings = async () => {
    if (!magene) return;
    setBusy('settings'); setMsg('');
    try {
      const after = await withMagene(magene.address, () => writeSettings(changes));
      if (after) { setSettings(after); setEdited({ ...after }); }
      setMsg('Settings saved ✓');
    } catch (e: any) { setMsg(String(e?.message ?? e)); } finally { setBusy(''); }
  };

  const pagesChanged = !!pages && JSON.stringify(pages) !== JSON.stringify(editedPages);
  const editPages = (fn: (p: number[][]) => void) => setEditedPages(cur => { const n = cur.map(p => [...p]); fn(n); return n; });
  const savePages = async () => {
    if (!magene) return;
    setBusy('pages'); setMsg('');
    try {
      const back = await withMagene(magene.address, () => writePages(editedPages));
      setPages(back); setEditedPages(back.map(p => [...p]));
      setMsg('Data screens saved ✓');
    } catch (e: any) { setMsg(String(e?.message ?? e)); } finally { setBusy(''); }
  };

  const doRoute = async () => {
    if (!magene) return;
    try {
      const f = await pickFile();
      setBusy('route'); setMsg('');
      const gpx = bytesToUtf8(base64ToBytes(f.base64));
      const r = await sendRoute(magene.address, gpx);
      setMsg(r.ok ? `Route “${f.name}” sent (${r.points} points) ✓` : (r.error || 'Route send failed'));
    } catch (e: any) {
      if (e?.message !== 'CANCELLED' && e?.code !== 'CANCELLED') setMsg(String(e?.message ?? e));
    } finally { setBusy(''); }
  };

  const doAltitude = async () => {
    if (!magene) return;
    setBusy('alt'); setMsg('');
    try {
      const ok = await withMagene(magene.address, () => altitudeCorrect());
      setMsg(ok ? 'Altitude calibrated from the C406’s GPS ✓' : 'Altitude calibration failed');
    } catch (e: any) { setMsg(String(e?.message ?? e)); } finally { setBusy(''); }
  };

  const text = { color: t.text, fontSize: v3Type.body };
  const muted = { color: t.mutedText, fontSize: v3Type.body };
  const heading = { color: t.text, fontSize: v3Type.heading, fontWeight: '700' as const };

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium, alignItems: 'center' }}>
      <View style={{ width: '100%', maxWidth: 640, gap: v3Spacing.medium }}>
        {status === 'none' && (
          <Card><Text style={muted}>No Magene paired yet — use “Find Magene” on Home first.</Text></Card>
        )}
        {status === 'loading' && (
          <Card><View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small }}>
            <ActivityIndicator color={t.primary} /><Text style={text}>Connecting to the Magene over Bluetooth…</Text>
          </View></Card>
        )}
        {status === 'error' && (
          <Card>
            <Text style={{ color: t.error, fontSize: v3Type.body, marginBottom: v3Spacing.small }}>{error}</Text>
            <Text style={[muted, { marginBottom: v3Spacing.medium }]}>Wake the C406 (any button) and keep it close, then retry.</Text>
            <Button label="Retry" icon="sync" onPress={load} grow={false} />
          </Card>
        )}

        {status === 'ready' && magene && (
          <>
            <Card>
              <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>{magene.name || 'Magene C406'}</Text>
              {device && <Text style={[muted, { marginTop: 2 }]}>
                Device profile: FTP {device.ftp} W · LTHR {device.lthr} · Max HR {device.maxHr} · {device.weight} kg · age {device.age}
              </Text>}
              <View style={{ flexDirection: 'row', gap: v3Spacing.small, marginTop: v3Spacing.medium, flexWrap: 'wrap' }}>
                <Button label={busy === 'route' ? 'Sending…' : 'Send route (GPX)'} icon="route" onPress={doRoute} disabled={!!busy} grow={false} />
                <Button label={busy === 'alt' ? 'Calibrating…' : 'Calibrate altitude'} onPress={doAltitude} disabled={!!busy} variant="text" grow={false} />
              </View>
              {msg ? <StatusLine text={msg} tone={msg.includes('✓') ? 'muted' : 'alert'} /> : null}
            </Card>

            <Card>
              <Text style={heading}>Profile</Text>
              {!icu && <Text style={[muted, { marginTop: 4 }]}>Connect intervals.icu in Settings to compare.</Text>}
              {icu && diffs.length === 0 && <Text style={{ color: t.success, fontSize: v3Type.body, marginTop: 4 }}>Device and intervals.icu already match. ✓</Text>}
              {icu && diffs.length > 0 && (
                <>
                  <Text style={[muted, { marginTop: 2, marginBottom: v3Spacing.medium }]}>
                    Pick the value that's right for each. Gender, height and age can only go to the device.
                  </Text>
                  {diffs.map(d => (
                    <View key={d.field} style={{ marginBottom: v3Spacing.medium }}>
                      <Text style={[text, { fontWeight: '600', marginBottom: 6 }]}>{PROFILE_LABELS[d.field]}</Text>
                      <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
                        {(['device', 'intervals'] as const).map(src => {
                          const sel = sideFor(d.field) === src;
                          const locked = !TWO_WAY.includes(d.field) && src === 'device';
                          return (
                            <TouchableOpacity key={src} activeOpacity={0.8} disabled={locked}
                              onPress={() => setPick(p => ({ ...p, [d.field]: src }))}
                              style={{
                                flex: 1, padding: v3Spacing.small, borderRadius: v3Radius.small, borderWidth: 1,
                                borderColor: sel ? t.primary : t.border, backgroundColor: sel ? t.primary : t.card,
                                alignItems: 'center', opacity: locked ? 0.5 : 1,
                              }}>
                              <Text style={{ color: sel ? t.card : t.mutedText, fontSize: v3Type.caption }}>{src === 'device' ? 'Magene' : 'intervals.icu'}</Text>
                              <Text style={{ color: sel ? t.card : t.text, fontSize: v3Type.body, fontWeight: '700' }}>
                                {fmtProfile(d.field, src === 'device' ? d.device : d.intervals)}
                              </Text>
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
                  <Button label={busy === 'profile' ? 'Saving…' : 'Apply'} icon="check" onPress={applyProfile} disabled={!!busy} />
                </>
              )}
            </Card>

            {pages && (
              <Card>
                <Text style={heading}>Data screens</Text>
                <Text style={[muted, { marginTop: 2 }]}>Tap a field to change it. {MIN_FIELDS}–{MAX_FIELDS} fields per screen.</Text>
                {editedPages.map((pg, pi) => (
                  <View key={pi} style={{ marginTop: v3Spacing.medium, padding: v3Spacing.small, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.border }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <Text style={[text, { fontWeight: '700', flex: 1 }]}>Screen {pi + 1}</Text>
                      <Seg t={t} label="▲" selected={false} onPress={() => pi > 0 && editPages(p => { const [x] = p.splice(pi, 1); p.splice(pi - 1, 0, x); })} />
                      <Seg t={t} label="▼" selected={false} onPress={() => pi < editedPages.length - 1 && editPages(p => { const [x] = p.splice(pi, 1); p.splice(pi + 1, 0, x); })} />
                      <Seg t={t} label="Remove" selected={false} onPress={() => editedPages.length > 1 && editPages(p => { p.splice(pi, 1); })} />
                    </View>
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                      {pg.map((code, si) => (
                        <Seg key={si} t={t} selected={false} label={FIELD_NAMES[code] ?? `0x${code.toString(16)}`}
                          onPress={() => setPicker({ page: pi, slot: si })} />
                      ))}
                      {pg.length < MAX_FIELDS && <Seg t={t} selected={false} label="+" onPress={() => editPages(p => { p[pi].push(0xff); })} />}
                      {pg.length > MIN_FIELDS && <Seg t={t} selected={false} label="−" onPress={() => editPages(p => { p[pi].pop(); })} />}
                    </View>
                  </View>
                ))}
                <View style={{ flexDirection: 'row', gap: v3Spacing.small, marginTop: v3Spacing.medium, flexWrap: 'wrap' }}>
                  <Button label="+ Screen" onPress={() => editPages(p => { p.push([0x10, 0xb1]); })} disabled={editedPages.length >= MAX_PAGES} variant="text" grow={false} />
                  <Button label={busy === 'pages' ? 'Saving…' : pagesChanged ? 'Save to C406' : 'No changes'} icon="check"
                    onPress={savePages} disabled={!!busy || !pagesChanged} grow={false} />
                </View>
              </Card>
            )}

            {settings && (
              <Card>
                <Text style={heading}>Device settings</Text>
                {SETTINGS_UI.filter(s => settings[s.key] !== undefined).map(s => (
                  <View key={s.key} style={{ marginTop: v3Spacing.medium }}>
                    <Text style={[text, { marginBottom: 6 }]}>{s.label}</Text>
                    {s.kind === 'switch'
                      ? <Toggle value={(edited[s.key] || 0) > 0} onValueChange={v => setEdited(e => ({ ...e, [s.key]: v ? 1 : 0 }))} />
                      : <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                          {s.choices.map(c => (
                            <Seg key={c.v} t={t} label={c.t} selected={edited[s.key] === c.v}
                              onPress={() => setEdited(e => ({ ...e, [s.key]: c.v }))} />
                          ))}
                        </View>}
                  </View>
                ))}
                <AlertRow t={t} label="Heart-rate alert (bpm)" value={edited.hrAlert} on={170} lo={100} hi={240}
                  visible={settings.hrAlert !== undefined} onChange={v => setEdited(e => ({ ...e, hrAlert: v }))} />
                <AlertRow t={t} label="Power alert (W)" value={edited.powerAlert} on={1000} lo={100} hi={2500}
                  visible={settings.powerAlert !== undefined} onChange={v => setEdited(e => ({ ...e, powerAlert: v }))} />
                {settings.autoLap !== undefined && (
                  <View style={{ marginTop: v3Spacing.medium }}>
                    <Text style={[text, { marginBottom: 6 }]}>Auto lap</Text>
                    <Toggle value={edited.autoLap === 1} onValueChange={v => setEdited(e => ({
                      ...e, autoLap: v ? 1 : 0, ...(v && !(e.autoLapValue > 0) ? { autoLapType: 0, autoLapValue: 100 } : {}),
                    }))} />
                    {edited.autoLap === 1 && (
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6 }}>
                        <Text style={muted}>every</Text>
                        <NumInput t={t}
                          value={edited.autoLapType === 0 ? String((edited.autoLapValue || 0) / 10) : String(edited.autoLapValue || 0)}
                          onDone={n => setEdited(e => ({ ...e, autoLapValue: e.autoLapType === 0 ? Math.round(n * 10) : Math.round(n) }))} />
                        <Seg t={t} label="km" selected={edited.autoLapType === 0} onPress={() => setEdited(e => ({ ...e, autoLapType: 0, autoLapValue: 100 }))} />
                        <Seg t={t} label="min" selected={edited.autoLapType === 1} onPress={() => setEdited(e => ({ ...e, autoLapType: 1, autoLapValue: 30 }))} />
                      </View>
                    )}
                  </View>
                )}
                <View style={{ marginTop: v3Spacing.medium }}>
                  <Button label={busy === 'settings' ? 'Saving…' : Object.keys(changes).length ? 'Save to C406' : 'No changes'}
                    icon="check" onPress={saveSettings} disabled={!!busy || Object.keys(changes).length === 0} />
                </View>
              </Card>
            )}
          </>
        )}
      </View>
      <Modal visible={picker != null} transparent animationType="fade" onRequestClose={() => setPicker(null)}>
        <Pressable style={{ flex: 1, backgroundColor: '#00000066', justifyContent: 'center', padding: 24 }} onPress={() => setPicker(null)}>
          <Pressable onPress={() => {}} style={{ backgroundColor: t.card, borderRadius: 16, borderWidth: 1, borderColor: t.border, maxHeight: '80%', overflow: 'hidden' }}>
            <ScrollView contentContainerStyle={{ padding: v3Spacing.medium }}>
              {FIELD_GROUPS.map(g => (
                <View key={g.group} style={{ marginBottom: v3Spacing.small }}>
                  <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginBottom: 4 }}>{g.group}</Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {g.fields.map(f => (
                      <Seg key={f.code} t={t} label={f.name}
                        selected={!!picker && editedPages[picker.page]?.[picker.slot] === f.code}
                        onPress={() => { if (picker) editPages(p => { p[picker.page][picker.slot] = f.code; }); setPicker(null); }} />
                    ))}
                  </View>
                </View>
              ))}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </ScrollView>
  );
}

function NumInput({ t, value, onDone }: { t: any; value: string; onDone: (n: number) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <TextInput value={v} onChangeText={setV} keyboardType="numeric"
      onEndEditing={() => { const n = parseFloat(v); if (Number.isFinite(n) && n > 0) onDone(n); }}
      style={{
        width: 70, borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text,
        backgroundColor: t.surface, paddingHorizontal: 8, paddingVertical: 4, fontSize: v3Type.body,
      }} />
  );
}

// An alert value: 0 = off; switching on uses the OneLap app's own default (170 bpm / 1000 W).
function AlertRow({ t, label, value, on, lo, hi, visible, onChange }: {
  t: any; label: string; value?: number; on: number; lo: number; hi: number; visible: boolean; onChange: (v: number) => void;
}) {
  if (!visible) return null;
  const enabled = (value || 0) > 0;
  return (
    <View style={{ marginTop: v3Spacing.medium }}>
      <Text style={{ color: t.text, fontSize: v3Type.body, marginBottom: 6 }}>{label}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Toggle value={enabled} onValueChange={v => onChange(v ? on : 0)} />
        {enabled && <NumInput t={t} value={String(value)} onDone={n => onChange(Math.min(hi, Math.max(lo, Math.round(n))))} />}
      </View>
    </View>
  );
}
