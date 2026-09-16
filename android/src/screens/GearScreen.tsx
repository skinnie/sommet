import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, Modal,
} from 'react-native';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { t } from '../i18n';
import {
  LocalGear, LocalReminder, getAllGear, getReminders, upsertGear, softDeleteGear, setGearBaseline,
  getAssignments, setAssignment, newLocalId, getGearLedger,
} from '../database/gearDb';
import { computeGearTotals, reminderPercentUsed, GearTotal } from '../services/GearTotals';
import {
  createReminderNow, deleteReminderNow, snoozeReminderNow,
} from '../services/GearMirrorService';

// Reminder interval unit chosen in the add-reminder modal.
type ReminderUnit = 'distance' | 'time' | 'days' | 'activities';

// Gear tracker (v3). Local-first store; the intervals.icu Import/Sync controls now live in
// Settings -> intervals.icu connection (André, 2026-08-18), so this screen just shows and edits
// your gear. A component/part is a gear row with parentId set to its parent; reminders hang off
// a gear. Auto-assign of the default bike/shoes to a synced move lives in GearAutoAssign.

// Common Ambit sport types offered in the default-gear picker.
const SPORT_TYPES = ['Cycling', 'Mountain biking', 'Running', 'Trail running', 'Walking', 'Hiking', 'Orienteering'];

interface GearWithReminders extends LocalGear { reminders: LocalReminder[] }

export default function GearScreen() {
  const theme = useV3Theme();
  const s = styles(theme);

  const [gear, setGear] = useState<GearWithReminders[]>([]);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [totals, setTotals] = useState<Map<string, GearTotal>>(new Map());
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    const all = await getAllGear();
    const withRem: GearWithReminders[] = [];
    for (const g of all) withRem.push({ ...g, reminders: await getReminders(g.id) });
    setGear(withRem);
    setAssignments(await getAssignments());
    // Tallied distance = baseline + moves attributed here since the baseline moment. When you've
    // typed a starting number (baselineAt > 0) that is the baseline; otherwise the intervals total
    // at last import (lastSyncedAt) is, exactly as before.
    const ledger = await getGearLedger();
    const baselines = all.map(g => (g.baselineAt && g.baselineAt > 0)
      ? { id: g.id, distanceM: g.startingDistanceM ?? 0, timeS: g.startingTimeS ?? 0, baselineAt: g.baselineAt }
      : { id: g.id, distanceM: g.distanceM, timeS: g.timeS, baselineAt: g.lastSyncedAt });
    setTotals(computeGearTotals(baselines, ledger));
  }, []);

  useEffect(() => {
    (async () => {
      await reload();
      setLoading(false);
    })();
  }, [reload]);

  const tops = gear.filter(g => !g.parentId);
  const bikes = tops.filter(g => g.type.toLowerCase() === 'bike');
  const shoes = tops.filter(g => g.type.toLowerCase().startsWith('shoe'));
  const partsOf = (id: string) => gear.filter(g => g.parentId === id);

  // ── Text-input modal (add/rename) ───────────────────────────────────────────
  const [prompt, setPrompt] = useState<{ title: string; value: string; onOk: (v: string) => void } | null>(null);
  function askText(title: string, initial: string, onOk: (v: string) => void) {
    setPrompt({ title, value: initial, onOk });
  }

  async function addGear(type: 'Bike' | 'Shoes') {
    askText(type === 'Bike' ? t.gearAddBike : t.gearAddShoes, '', async name => {
      if (!name.trim()) return;
      const now = Date.now();
      await upsertGear({
        id: newLocalId(type === 'Bike' ? 'bike' : 'shoe'), remoteId: null, parentId: null,
        name: name.trim(), type, distanceM: 0, timeS: 0, retired: false, isPrimary: false,
        updatedAt: now, lastSyncedAt: 0, remoteSnapshot: '', deleted: false,
      });
      await reload();
    });
  }

  async function addPart(parent: LocalGear) {
    // Part type defaults to a generic "Other"; intervals.icu accepts a free-form gear type.
    askText(t.gearAddPart, '', async name => {
      if (!name.trim()) return;
      const now = Date.now();
      await upsertGear({
        id: newLocalId('part'), remoteId: null, parentId: parent.id, name: name.trim(),
        type: 'Other', distanceM: 0, timeS: 0, retired: false, isPrimary: false,
        updatedAt: now, lastSyncedAt: 0, remoteSnapshot: '', deleted: false,
      });
      await reload();
    });
  }

  async function rename(g: LocalGear) {
    askText(t.gearName, g.name, async name => {
      if (!name.trim()) return;
      await upsertGear({ ...g, name: name.trim(), updatedAt: Date.now() });
      await reload();
    });
  }

  async function toggleRetired(g: LocalGear) {
    await upsertGear({ ...g, retired: !g.retired, updatedAt: Date.now() });
    await reload();
  }

  async function setBaseline(g: LocalGear) {
    const curKm = Math.round(((g.baselineAt && g.baselineAt > 0 ? (g.startingDistanceM ?? 0) : g.distanceM)) / 1000);
    askText('Starting mileage (km)', String(curKm), async v => {
      const km = parseFloat(v);
      if (isNaN(km)) return;
      await setGearBaseline(g.id, km, 0);   // rides count on top from now on; syncs across devices
      await reload();
    });
  }

  async function setPrimary(g: LocalGear) {
    // one primary per type
    for (const other of gear.filter(x => x.type === g.type && x.isPrimary && x.id !== g.id)) {
      await upsertGear({ ...other, isPrimary: false, updatedAt: Date.now() });
    }
    await upsertGear({ ...g, isPrimary: true, updatedAt: Date.now() });
    await reload();
  }

  function confirmDelete(g: LocalGear) {
    Alert.alert(g.name, t.gearDelete + '?', [
      { text: t.cancel, style: 'cancel' },
      { text: t.gearDelete, style: 'destructive', onPress: async () => { await softDeleteGear(g.id); await reload(); } },
    ]);
  }

  // ── Reminders ───────────────────────────────────────────────────────────────
  const [reminderFor, setReminderFor] = useState<LocalGear | null>(null);
  const [remName, setRemName] = useState('');
  const [remKind, setRemKind] = useState<ReminderUnit>('distance');
  const [remValue, setRemValue] = useState('');

  function openReminder(g: LocalGear) {
    setReminderFor(g); setRemName(''); setRemKind('distance'); setRemValue('');
  }
  async function saveReminder() {
    if (!reminderFor) return;
    const v = parseFloat(remValue) || 0;
    await createReminderNow(reminderFor, {
      name: remName.trim() || t.gearAddReminder,
      distanceM: remKind === 'distance' ? v * 1000 : 0,   // km -> m
      timeS: remKind === 'time' ? v * 3600 : 0,           // h -> s
      days: remKind === 'days' ? Math.round(v) : 0,
      activities: remKind === 'activities' ? Math.round(v) : 0,
    });
    setReminderFor(null);
    await reload();
  }

  // Due-ness computed LOCALLY from the reminder's reset-baseline and the gear's tracked total
  // (independent of intervals' percent_used). Snoozed reminders don't read as due until the snooze
  // lapses.
  function dueState(owner: LocalGear, r: LocalReminder): 'due' | 'soon' | null {
    if (r.snoozedUntil != null && Date.now() < r.snoozedUntil) return null;
    const gt = totals.get(owner.id);
    const pct = reminderPercentUsed(r, {
      distanceM: gt?.distanceM ?? owner.distanceM,
      timeS: gt?.timeS ?? owner.timeS,
      activitiesSinceReset: gt?.addedCount ?? 0,
    }, Date.now());
    if (pct >= 100) return 'due';
    if (pct >= 90) return 'soon';
    return null;
  }
  function reminderLabel(r: LocalReminder): string {
    if (r.distanceM > 0) return `${(r.distanceM / 1000).toFixed(0)} km`;
    if (r.timeS > 0) return `${(r.timeS / 3600).toFixed(0)} h`;
    if (r.days > 0) return `${r.days} d`;
    if (r.activities > 0) return `${r.activities}×`;
    return '—';
  }

  if (loading) {
    return <View style={[s.root, { justifyContent: 'center', alignItems: 'center' }]}><ActivityIndicator color={theme.primary} /></View>;
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      {gear.length === 0 && <Text style={s.desc}>{t.gearEmpty}</Text>}

      {/* Bikes & shoes */}
      {([['Bike', t.gearBikes, bikes], ['Shoes', t.gearShoes, shoes]] as const).map(([type, label, list]) => (
        <View key={type} style={{ width: '100%' }}>
          <View style={s.sectionHead}>
            <Text style={s.section}>{label}</Text>
            <TouchableOpacity onPress={() => addGear(type)}><Text style={s.addLink}>＋ {type === 'Bike' ? t.gearAddBike : t.gearAddShoes}</Text></TouchableOpacity>
          </View>
          {list.map(g => (
            <Card key={g.id} style={{ width: '100%', marginBottom: 10, opacity: g.retired ? 0.55 : 1 }}>
              <View style={s.gearHead}>
                <TouchableOpacity style={{ flex: 1 }} onPress={() => rename(g)}>
                  <Text style={s.gearName}>{g.name}{g.isPrimary ? ` · ${t.gearPrimaryShort}` : ''}{g.retired ? ` · ${t.gearRetired}` : ''}</Text>
                  <Text style={s.gearSub}>
                    {((totals.get(g.id)?.distanceM ?? g.distanceM) / 1000).toFixed(0)} km
                    {(() => { const a = totals.get(g.id); return a && a.addedM > 0
                      ? <Text style={{ color: theme.primary }}>{`  ↑ ${t.gearTrackedHere((a.addedM / 1000).toFixed(0), a.addedCount)}`}</Text>
                      : null; })()}
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => setPrimary(g)}><Icon name="check" size={18} color={g.isPrimary ? theme.primary : theme.mutedText} /></TouchableOpacity>
                <TouchableOpacity onPress={() => confirmDelete(g)} style={{ marginLeft: 14 }}><Icon name="delete" size={18} color={theme.mutedText} /></TouchableOpacity>
              </View>

              {/* Parts */}
              {partsOf(g.id).map(p => (
                <View key={p.id} style={s.partRow}>
                  <TouchableOpacity style={{ flex: 1 }} onPress={() => rename(p)}><Text style={s.partName}>• {p.name}</Text></TouchableOpacity>
                  <TouchableOpacity onPress={() => openReminder(p)}><Text style={s.smallLink}>{t.gearAddReminder}</Text></TouchableOpacity>
                  <TouchableOpacity onPress={() => confirmDelete(p)} style={{ marginLeft: 12 }}><Icon name="delete" size={15} color={theme.mutedText} /></TouchableOpacity>
                </View>
              ))}

              {/* Reminders on the gear itself and its parts */}
              {[g, ...partsOf(g.id)].flatMap(owner => owner.reminders.map(r => ({ owner, r })))
                .map(({ owner, r }) => {
                  const st = dueState(owner, r);
                  const color = st === 'due' ? theme.error : st === 'soon' ? theme.warning : theme.mutedText;
                  return (
                    <View key={r.id} style={s.remRow}>
                      <Icon name="warning" size={13} color={color} />
                      <Text style={[s.remText, { color }]}>{r.name} · {reminderLabel(r)}{st ? ` · ${st === 'due' ? t.gearDue : t.gearDueSoon}` : ''}</Text>
                      <TouchableOpacity onPress={() => snoozeReminderNow(owner, r, 30).then(reload)}><Text style={s.smallLink}>{t.gearSnooze}</Text></TouchableOpacity>
                      <TouchableOpacity onPress={() => deleteReminderNow(owner, r).then(reload)} style={{ marginLeft: 10 }}><Icon name="delete" size={14} color={theme.mutedText} /></TouchableOpacity>
                    </View>
                  );
                })}

              <View style={s.gearActions}>
                <TouchableOpacity onPress={() => setBaseline(g)}><Text style={s.smallLink}>Set km</Text></TouchableOpacity>
                <TouchableOpacity onPress={() => addPart(g)}><Text style={s.smallLink}>＋ {t.gearAddPart}</Text></TouchableOpacity>
                <TouchableOpacity onPress={() => openReminder(g)}><Text style={s.smallLink}>＋ {t.gearAddReminder}</Text></TouchableOpacity>
                <TouchableOpacity onPress={() => toggleRetired(g)}><Text style={s.smallLink}>{g.retired ? t.gearUnretire : t.gearRetire}</Text></TouchableOpacity>
              </View>
            </Card>
          ))}
        </View>
      ))}

      {/* Default gear per sport */}
      {gear.length > 0 && (
        <Card style={{ width: '100%' }}>
          <Text style={s.section}>{t.gearDefaultFor}</Text>
          {SPORT_TYPES.map(sport => {
            const chosen = gear.find(g => g.id === assignments[sport]);
            return (
              <TouchableOpacity key={sport} style={s.assignRow} onPress={() => cycleAssignment(sport)}>
                <Text style={s.assignSport}>{sport}</Text>
                <Text style={[s.assignGear, { color: chosen ? theme.primary : theme.mutedText }]}>{chosen ? chosen.name : t.gearNoDefault}</Text>
              </TouchableOpacity>
            );
          })}
          <Text style={[s.desc, { marginTop: 8 }]}>{t.gearAssignedTo('…').replace('…', '')}</Text>
        </Card>
      )}

      {/* Text prompt modal */}
      <Modal visible={prompt != null} transparent animationType="fade" onRequestClose={() => setPrompt(null)}>
        <View style={s.modalOverlay}>
          <View style={s.promptBox}>
            <Text style={s.title}>{prompt?.title}</Text>
            <TextInput style={[s.input, { marginTop: 12 }]} autoFocus value={prompt?.value ?? ''}
              onChangeText={v => setPrompt(p => p && { ...p, value: v })} placeholderTextColor={theme.mutedText} />
            <View style={s.promptBtns}>
              <TouchableOpacity onPress={() => setPrompt(null)}><Text style={[s.smallLink, { color: theme.mutedText }]}>{t.cancel}</Text></TouchableOpacity>
              <TouchableOpacity onPress={() => { const p = prompt; setPrompt(null); p?.onOk(p.value); }}><Text style={s.smallLink}>OK</Text></TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Reminder modal */}
      <Modal visible={reminderFor != null} transparent animationType="fade" onRequestClose={() => setReminderFor(null)}>
        <View style={s.modalOverlay}>
          <View style={s.promptBox}>
            <Text style={s.title}>{t.gearAddReminder}{reminderFor ? ` · ${reminderFor.name}` : ''}</Text>
            <TextInput style={[s.input, { marginTop: 12 }]} placeholder={t.gearName} value={remName}
              onChangeText={setRemName} placeholderTextColor={theme.mutedText} />
            <View style={s.chipRow}>
              {([['distance', t.gearReminderDistance], ['time', t.gearReminderTime], ['days', t.gearReminderDays], ['activities', t.gearReminderActivities]] as const).map(([k, lbl]) => (
                <TouchableOpacity key={k} style={[s.chip, remKind === k && s.chipActive]} onPress={() => setRemKind(k)}>
                  <Text style={[s.chipText, remKind === k && s.chipTextActive]}>{lbl}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <TextInput style={[s.input, { marginTop: 10 }]} keyboardType="numeric"
              placeholder={remKind === 'distance' ? 'km' : remKind === 'time' ? 'h' : remKind === 'days' ? 'days' : 'activities'}
              value={remValue} onChangeText={setRemValue} placeholderTextColor={theme.mutedText} />
            <View style={s.promptBtns}>
              <TouchableOpacity onPress={() => setReminderFor(null)}><Text style={[s.smallLink, { color: theme.mutedText }]}>{t.cancel}</Text></TouchableOpacity>
              <TouchableOpacity onPress={saveReminder}><Text style={s.smallLink}>OK</Text></TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );

  // Tap a sport to cycle through: none -> each gear -> none.
  async function cycleAssignment(sport: string) {
    const options = [null, ...gear.filter(g => !g.retired).map(g => g.id)];
    const cur = assignments[sport] ?? null;
    const idx = options.indexOf(cur);
    const nextVal = options[(idx + 1) % options.length];
    await setAssignment(sport, nextVal);
    setAssignments(await getAssignments());
  }
}

const styles = (th: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: th.background },
  content: { padding: 16, gap: 12 },
  title: { fontSize: 16, fontWeight: '800', color: th.text },
  section: { fontSize: 14, fontWeight: '800', color: th.text },
  sectionHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, marginTop: 4 },
  addLink: { fontSize: 12.5, color: th.primary, fontWeight: '700' },
  smallLink: { fontSize: 12.5, color: th.primary, fontWeight: '700' },
  desc: { fontSize: 12.5, color: th.mutedText, lineHeight: 18 },
  gearHead: { flexDirection: 'row', alignItems: 'center' },
  gearName: { fontSize: 15, fontWeight: '700', color: th.text },
  gearSub: { fontSize: 12, color: th.mutedText, marginTop: 2 },
  partRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8, paddingLeft: 4 },
  partName: { fontSize: 13.5, color: th.text },
  remRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6, paddingLeft: 4 },
  remText: { flex: 1, fontSize: 12 },
  gearActions: { flexDirection: 'row', gap: 18, marginTop: 12, flexWrap: 'wrap' },
  assignRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: th.mutedText + '22' },
  assignSport: { fontSize: 14, color: th.text },
  assignGear: { fontSize: 13, fontWeight: '700' },
  btn: { paddingVertical: 11, borderRadius: 10, alignItems: 'center', backgroundColor: th.primary + '1F', borderWidth: 1, borderColor: th.primary },
  btnText: { color: th.primary, fontWeight: '700', fontSize: 13 },
  btnGhost: { paddingVertical: 10, borderRadius: 10, alignItems: 'center', marginTop: 10, borderWidth: 1, borderColor: th.mutedText + '44' },
  btnGhostText: { color: th.mutedText, fontWeight: '700', fontSize: 12.5 },
  rowCenter: { flexDirection: 'row', alignItems: 'center' },
  input: { backgroundColor: th.background, borderRadius: 8, borderWidth: 1, borderColor: th.mutedText + '33', paddingHorizontal: 12, paddingVertical: 9, color: th.text, fontSize: 14 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999, backgroundColor: th.card, borderWidth: 1, borderColor: th.mutedText + '33' },
  chipActive: { backgroundColor: th.primary + '1F', borderColor: th.primary },
  chipText: { fontSize: 13, color: th.mutedText },
  chipTextActive: { color: th.primary, fontWeight: '700' },
  modalOverlay: { flex: 1, backgroundColor: '#00000066', justifyContent: 'center', padding: 24 },
  promptBox: { backgroundColor: th.background, borderRadius: 16, padding: 20 },
  promptBtns: { flexDirection: 'row', justifyContent: 'flex-end', gap: 24, marginTop: 16 },
});
