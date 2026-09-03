import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, RefreshControl,
  TextInput, TouchableOpacity,
} from 'react-native';
import Svg, { Path, Line } from 'react-native-svg';
import { useNavigation } from '@react-navigation/native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { loadCoachData, CoachData, ReadinessLight, WorkoutPick } from '../services/CoachService';
import {
  sendCoachMessage, hasAnthropicKey, ChatMessage, ChatBackend,
} from '../services/CoachChat';

// Coach — the Android counterpart of desktop/qml/pages/CoachPage.qml's readiness beacon
// (André, 2026-08-26: "port everything to android").
//
// Both halves of the desktop page: the readiness beacon (traffic light, fitness/fatigue/
// freshness, trend chart) and the chat. The chat runs on canned replies by default and on the
// real Claude API once the user adds their OWN Anthropic key in Settings - see CoachChat.ts.

export default function CoachScreen() {
  const t = useV3Theme();
  const navigation = useNavigation<any>();
  const [data, setData] = useState<CoachData>({ readiness: null, chart: [], picks: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await loadCoachData(90));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // ---- chat ----
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [backend, setBackend] = useState<ChatBackend>('canned');

  // The backend follows whether a key is actually stored: no key means canned, so the chat is
  // never silently dead waiting on a credential the user never entered.
  useEffect(() => {
    hasAnthropicKey().then(has => setBackend(has ? 'claude' : 'canned'));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next: ChatMessage[] = [...messages, { role: 'me', text }];
    setMessages(next);
    setInput('');
    setSending(true);
    try {
      const reply = await sendCoachMessage(next, data.readiness, backend);
      setMessages([...next, { role: 'coach', text: reply }]);
    } catch (e: any) {
      setMessages([...next, { role: 'coach', text: String(e?.message ?? e) }]);
    } finally {
      setSending(false);
    }
  }, [input, sending, messages, data.readiness, backend]);

  // Semantic colours, matching the desktop's own mapping of light -> Theme colour.
  const lightColor = (l: ReadinessLight): string =>
    l === 'green' ? t.success
    : l === 'tempered' ? '#8FA33B'
    : l === 'yellow' ? t.warning
    : t.error;

  const lightLabel = (l: ReadinessLight): string =>
    l === 'green' ? 'Fresh'
    : l === 'tempered' ? 'Fresh, ease in'
    : l === 'yellow' ? 'Some fatigue'
    : 'Deep fatigue';

  const r = data.readiness;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && !r && <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />}

      {error.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.error, fontSize: v3Type.body }}>{error}</Text>
        </View>
      )}

      {!loading && !r && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            No training load yet. Connect intervals.icu — readiness is computed from
            the fitness and fatigue it tracks.
          </Text>
          {/* #9 (André, 2026-09-02): one-tap route to the shared connection settings. */}
          <TouchableOpacity
            onPress={() => navigation.navigate('Settings')}
            style={{ marginTop: v3Spacing.medium, alignSelf: 'flex-start',
                     paddingVertical: v3Spacing.small, paddingHorizontal: v3Spacing.medium,
                     borderRadius: v3Radius.card, borderWidth: 1, borderColor: t.border }}>
            <Text style={{ color: t.primary, fontSize: v3Type.body }}>Open Settings → Connections</Text>
          </TouchableOpacity>
        </View>
      )}

      {r && (
        <>
          <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
            <Text style={[styles.label, { color: t.mutedText }]}>Today</Text>
            <View style={styles.beacon}>
              <View style={[styles.dot, { backgroundColor: lightColor(r.light) }]} />
              <Text style={[styles.lightText, { color: lightColor(r.light) }]}>{lightLabel(r.light)}</Text>
            </View>
            <Text style={[styles.sentence, { color: t.mutedText }]}>{r.sentence}</Text>

            {/* The three numbers behind the light, as flat tiles - the same trio and the same
                flat cardNested treatment the desktop uses. */}
            <View style={styles.tiles}>
              {[
                { k: 'Fitness', v: r.fitness },
                { k: 'Fatigue', v: r.fatigue },
                { k: 'Freshness', v: r.freshness },
              ].map(m => (
                <View key={m.k} style={[styles.tile, { backgroundColor: t.cardNested, borderRadius: v3Radius.small }]}>
                  <Text style={[styles.tileLabel, { color: t.mutedText }]}>{m.k}</Text>
                  <Text style={[styles.tileValue, { color: t.text }]}>{Math.round(m.v)}</Text>
                </View>
              ))}
            </View>
          </View>

          {data.picks.length > 0 && (
            <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
              <Text style={[styles.label, { color: t.mutedText, marginBottom: v3Spacing.small }]}>Today's picks</Text>
              {data.picks.map((p: WorkoutPick, i) => (
                <View key={i} style={[styles.pick, { backgroundColor: t.cardNested, borderRadius: v3Radius.small }]}>
                  <View style={styles.pickHead}>
                    <Text style={[styles.pickName, { color: t.text }]} numberOfLines={2}>{p.name}</Text>
                    <Text style={[styles.pickDur, { color: t.mutedText }]}>
                      {p.durationSec ? Math.round(p.durationSec / 60) + 'min' : ''}
                    </Text>
                  </View>
                  <Text style={[styles.pickMeta, { color: t.mutedText }]}>
                    {p.intensity} · IF {p.intensityFactor ? p.intensityFactor.toFixed(2) : '—'} · {p.load ? Math.round(p.load) : '—'} TSS
                  </Text>
                </View>
              ))}
            </View>
          )}

          {data.chart.length > 1 && (
            <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
              <View style={styles.legend}>
                <View style={[styles.swatch, { backgroundColor: t.primary }]} />
                <Text style={[styles.tileLabel, { color: t.mutedText, marginRight: v3Spacing.medium }]}>Fitness</Text>
                <View style={[styles.swatch, { backgroundColor: t.mutedText, opacity: 0.6 }]} />
                <Text style={[styles.tileLabel, { color: t.mutedText }]}>Fatigue</Text>
              </View>
              <FitnessChart chart={data.chart} fitnessColor={t.primary} fatigueColor={t.mutedText} grid={t.border} />
            </View>
          )}

          <Text style={[styles.caption, { color: t.mutedText }]}>
            Fitness and fatigue come from intervals.icu, computed across every device you use.
          </Text>
        </>
      )}

      {/* ── Chat ── */}
      <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card, marginTop: v3Spacing.medium }]}>
        <Text style={[styles.label, { color: t.mutedText, marginBottom: v3Spacing.small }]}>
          {backend === 'claude' ? 'Coach' : 'Coach (pre-written replies)'}
        </Text>

        {messages.length === 0 && (
          <Text style={[styles.caption, { color: t.mutedText, marginBottom: v3Spacing.small }]}>
            {backend === 'claude'
              ? 'Ask about today, and I read your real training when I answer.'
              : 'Add your Anthropic API key in Settings for a real conversation. Until then these are pre-written answers.'}
          </Text>
        )}

        {messages.map((m, i) => (
          <View
            key={i}
            style={[
              styles.bubble,
              m.role === 'me'
                ? { backgroundColor: t.primary, alignSelf: 'flex-end' }
                : { backgroundColor: t.cardNested, alignSelf: 'flex-start' },
            ]}
          >
            <Text style={{ color: m.role === 'me' ? '#fff' : t.text, fontSize: v3Type.body }}>{m.text}</Text>
          </View>
        ))}

        {sending && <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.small }} />}

        {/* Suggestion chips - the same three the desktop offers. */}
        {!sending && (
          <View style={styles.chips}>
            {['Something shorter', 'Outdoor instead', 'Send it to my watch'].map(label => (
              <TouchableOpacity
                key={label}
                onPress={() => { setInput(label); }}
                style={[styles.chip, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]}
              >
                <Text style={{ color: t.text, fontSize: v3Type.caption, fontWeight: '600' }}>{label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        <View style={styles.inputRow}>
          <TextInput
            value={input}
            onChangeText={setInput}
            placeholder="Ask the coach anything…"
            placeholderTextColor={t.mutedText}
            onSubmitEditing={send}
            returnKeyType="send"
            style={[styles.input, { color: t.text, backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]}
          />
          <TouchableOpacity
            onPress={send}
            disabled={!input.trim() || sending}
            style={[styles.sendBtn, {
              backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small,
              opacity: !input.trim() || sending ? 0.5 : 1,
            }]}
          >
            <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '600' }}>Send</Text>
          </TouchableOpacity>
        </View>
      </View>
    </ScrollView>
  );
}

function FitnessChart({
  chart, fitnessColor, fatigueColor, grid,
}: {
  chart: { date: string; fitness: number; fatigue: number }[];
  fitnessColor: string;
  fatigueColor: string;
  grid: string;
}) {
  // Real measured width, not a stretched fixed viewBox - see MetricChart.tsx for why
  // (preserveAspectRatio="none" scales strokes with the axis and renders them thick/blocky).
  const [w, setW] = useState(0);
  const H = 110, padT = 6, padB = 6, padX = 2;
  const W = w || 300;
  // Both series share ONE scale, otherwise the fitness/fatigue crossover - the whole point of
  // this chart - would be a meaningless artefact of two different axes.
  const all = chart.flatMap(p => [p.fitness, p.fatigue]);
  const max = Math.max(...all) * 1.1 || 1;

  const x = (i: number) => padX + (W - padX * 2) * (i / (chart.length - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const path = (sel: (p: typeof chart[0]) => number) =>
    chart.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(sel(p)).toFixed(1)}`).join(' ');

  return (
    <View onLayout={e => setW(Math.round(e.nativeEvent.layout.width))} style={{ width: '100%' }}>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
        <Line x1={padX} y1={H - padB} x2={W - padX} y2={H - padB} stroke={grid} strokeWidth={1} />
        <Path d={path(p => p.fatigue)} stroke={fatigueColor} strokeWidth={1.2} fill="none"
              strokeDasharray="3,3" opacity={0.7} />
        <Path d={path(p => p.fitness)} stroke={fitnessColor} strokeWidth={1.8} fill="none" />
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  beacon: { alignItems: 'center', marginTop: v3Spacing.medium },
  dot: { width: 64, height: 64, borderRadius: 32 },
  lightText: { fontSize: v3Type.largeTitle, fontWeight: '700', marginTop: v3Spacing.small },
  sentence: { fontSize: v3Type.label, textAlign: 'center', marginTop: v3Spacing.small, marginBottom: v3Spacing.medium },
  tiles: { flexDirection: 'row', justifyContent: 'space-between' },
  tile: { flex: 1, paddingVertical: 10, alignItems: 'center', marginHorizontal: 3 },
  tileLabel: { fontSize: v3Type.tiny, fontWeight: '600' },
  tileValue: { fontSize: v3Type.bodyLarge, fontWeight: '700' },
  pick: { padding: 10, marginBottom: v3Spacing.small },
  pickHead: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  pickName: { flex: 1, fontSize: v3Type.label, fontWeight: '700', marginRight: v3Spacing.small },
  pickDur: { fontSize: v3Type.caption },
  pickMeta: { fontSize: v3Type.caption, marginTop: 3 },
  legend: { flexDirection: 'row', alignItems: 'center', marginBottom: v3Spacing.small },
  bubble: { maxWidth: '85%', paddingHorizontal: 12, paddingVertical: 9, borderRadius: 14, marginBottom: v3Spacing.small },
  chips: { flexDirection: 'row', flexWrap: 'wrap', marginBottom: v3Spacing.medium },
  chip: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 8, marginRight: 6, marginBottom: 6 },
  inputRow: { flexDirection: 'row', alignItems: 'center' },
  input: { flex: 1, borderWidth: 1, paddingHorizontal: v3Spacing.small, paddingVertical: 9, fontSize: v3Type.body, marginRight: v3Spacing.small },
  sendBtn: { borderWidth: 1, paddingHorizontal: v3Spacing.medium, paddingVertical: 10 },
  swatch: { width: 12, height: 4, borderRadius: 2, marginRight: 6 },
  caption: { fontSize: v3Type.caption },
});
