import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, RefreshControl,
  TextInput, TouchableOpacity,
} from 'react-native';
import Svg, { Path, Line, Text as SvgText } from 'react-native-svg';
import { niceTicks } from '../components/chartTicks';
import { fmtDate } from '../i18n';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { loadCoachData, CoachData, ReadinessLight, WorkoutPick } from '../services/CoachService';
import {
  sendCoachMessage, hasAnthropicKey, getChatBackend, ChatMessage, ChatBackend, CoachCard,
  msgUnreachable,
} from '../services/CoachChat';
import Icon from '../components/ui/Icon';
import Markdown from '../components/Markdown';
import { Button, PulseDots, StatTile, StatusChip, StatusNote } from '../components/ui/primitives';

// Coach — the Android counterpart of desktop/qml/pages/CoachPage.qml's readiness beacon
// (André, 2026-08-26: "port everything to android").
//
// Both halves of the desktop page: the readiness beacon (traffic light, fitness/fatigue/
// freshness, trend chart) and the chat. The chat runs on canned replies by default and on the
// real Claude API once the user adds their OWN Anthropic key in Settings - see CoachChat.ts.

function plural(n: number): string {
  return n === 1 ? '1 move' : `${n} moves`;
}

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
  const [keySet, setKeySet] = useState(false);
  const aiOn = backend === 'claude' && keySet;

  // Keep the newest bubble (and the writing dots) in view, like the desktop's chat list: scroll
  // to the end once the content has grown after a message was added or sending started.
  const scrollRef = useRef<ScrollView>(null);
  const scrollPending = useRef(false);
  useEffect(() => {
    if (messages.length > 0 || sending) scrollPending.current = true;
  }, [messages.length, sending]);

  // Settings -> Coach picks the replies (desktop parity). Re-read on every visit, so coming back
  // from Settings shows the new choice. Claude without a key still answers pre-written
  // (sendCoachMessage), so the chat is never silently dead.
  useFocusEffect(useCallback(() => {
    getChatBackend().then(setBackend);
    hasAnthropicKey().then(setKeySet);
  }, []));

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next: ChatMessage[] = [...messages, { role: 'me', text }];
    setMessages(next);
    setInput('');
    setSending(true);
    try {
      const reply = await sendCoachMessage(next, data.readiness, backend, data.picks);
      setMessages([...next, { role: 'coach', ...reply }]);
    } catch (e: any) {
      // A local note (desktop wording): shown, never sent back to Claude.
      setMessages([...next, { role: 'coach', text: msgUnreachable(String(e?.message ?? e)), local: true }]);
    } finally {
      setSending(false);
    }
  }, [input, sending, messages, data.readiness, data.picks, backend]);

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
      ref={scrollRef}
      onContentSizeChange={() => {
        if (!scrollPending.current) return;
        scrollPending.current = false;
        scrollRef.current?.scrollToEnd({ animated: true });
      }}
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && !r && <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />}

      <StatusNote text={error} tone="error" style={{ marginTop: 0, marginBottom: v3Spacing.medium }} />

      {!loading && !r && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            No training load yet. Sync a move from the watch, or connect intervals.icu for
            the fitness and fatigue it tracks across every device.
          </Text>
          {/* #9 (André, 2026-09-02): one-tap route to the shared connection settings. */}
          <TouchableOpacity
            onPress={() => navigation.navigate('Settings', { section: 'connections' })}
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
              {data.picks.map((p: WorkoutPick, i) => <WorkoutCard key={i} pick={p} />)}
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
              <FitnessChart chart={data.chart} fitnessColor={t.primary} fatigueColor={t.mutedText} grid={t.border}
                labelColor={t.mutedText} />
            </View>
          )}

          <Text style={[styles.caption, { color: t.mutedText }]}>
            {r.basis === 'intervals'
              ? 'Fitness and fatigue come from intervals.icu, computed across every device you use.'
              : (r.hrMoves ?? 0) > 0
                ? `Estimated on this phone: heart-rate load for ${plural(r.hrMoves ?? 0)}, duration × sport for ${plural(r.durationMoves ?? 0)} without HR. Connect intervals.icu for load from every device.`
                : 'Estimated on this phone from each move\'s duration and sport — no heart rate in recent moves, or max/rest HR not read from the watch yet. Connect intervals.icu for load from every device.'}
          </Text>
        </>
      )}

      {/* ── Chat ── */}
      <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card, marginTop: v3Spacing.medium }]}>
        {/* Same header as the desktop chat: what replies you get, and a settings button that opens
            Settings -> Coach (2026-10-22). */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small, marginBottom: v3Spacing.small }}>
          <View style={{ flex: 1 }}>
            <Text style={[styles.label, { color: t.mutedText }]}>Coach</Text>
            {/* "AI chat on" as the app's status tag (desktop picks #6). */}
            {aiOn
              ? <StatusChip label="AI chat on" tone="success" style={{ marginTop: 2 }} />
              : (
                <Text style={[styles.caption, { color: t.mutedText }]}>
                  {backend === 'claude'
                    ? 'Add your API key in Settings → Coach to turn on AI chat'
                    : 'Pre-written answers — turn on AI chat in Settings → Coach'}
                </Text>
              )}
          </View>
          <TouchableOpacity
            onPress={() => navigation.navigate('Settings', { section: 'coach' })}
            accessibilityRole="button"
            accessibilityLabel="Coach settings"
            hitSlop={8}
            style={{ padding: 8, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.mutedText, backgroundColor: t.card }}
          >
            <Icon name="settings" size={18} color={t.text} />
          </TouchableOpacity>
        </View>

        {messages.length === 0 && aiOn && (
          <Text style={[styles.caption, { color: t.mutedText, marginBottom: v3Spacing.small }]}>
            Ask about today, and I read your real training when I answer.
          </Text>
        )}

        {messages.map((m, i) => (
          <View key={i} style={{ marginBottom: v3Spacing.small }}>
            {m.text.length > 0 && (
              <View
                style={[
                  styles.bubble,
                  m.role === 'me'
                    ? { backgroundColor: t.primary, alignSelf: 'flex-end' }
                    : { backgroundColor: t.cardNested, alignSelf: 'flex-start' },
                ]}
              >
                {/* The coach's bubbles show Claude's formatting (bold, lists, links) instead of
                    raw ** and dashes; your own stay plain text (desktop picks #4). */}
                {m.role === 'me'
                  ? <Text style={{ color: '#fff', fontSize: v3Type.body }}>{m.text}</Text>
                  : <Markdown text={m.text} color={t.text} linkColor={t.primary} fontSize={v3Type.body}
                      codeBackground={t.border} />}
              </View>
            )}
            {/* Cards from Claude (desktop picks #18): a workout, one number in a tile, or a tip. */}
            {m.role === 'coach' && (m.cards ?? []).map((c, ci) => <ChatCard key={ci} card={c} />)}
          </View>
        ))}

        {/* While Claude writes: a coach bubble with the app's pulsing dots (desktop picks #4). */}
        {sending && (
          <View
            accessible
            accessibilityLabel="The coach is writing"
            style={[styles.bubble, styles.typing, { backgroundColor: t.cardNested, alignSelf: 'flex-start' }]}
          >
            <PulseDots color={t.mutedText} />
          </View>
        )}

        {/* Suggestion chips. "Send it to my watch" is left out until sending from the Coach
            works: today it only answered that it isn't wired up (desktop picks #17). */}
        {!sending && (
          <View style={styles.chips}>
            {['Something shorter', 'Outdoor instead'].map(label => (
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
          {/* Busy while Claude writes: keeps its name, dots pulse, taps are ignored (picks #5). */}
          <Button label="Send" onPress={send} busy={sending} disabled={!input.trim()} grow={false} />
        </View>
      </View>
    </ScrollView>
  );
}

// One workout: today's catalogue picks and Claude's workout cards use the same look.
function WorkoutCard({ pick, inChat }: { pick: Partial<WorkoutPick>; inChat?: boolean }) {
  const t = useV3Theme();
  return (
    <View style={[styles.pick, { backgroundColor: t.cardNested, borderRadius: v3Radius.small },
                  inChat && { marginBottom: 0 }]}>
      <View style={styles.pickHead}>
        <Text style={[styles.pickName, { color: t.text }]} numberOfLines={2}>{pick.name}</Text>
        <Text style={[styles.pickDur, { color: t.mutedText }]}>
          {pick.durationSec ? Math.round(pick.durationSec / 60) + 'min' : ''}
        </Text>
      </View>
      <Text style={[styles.pickMeta, { color: t.mutedText }]}>
        {pick.intensity} · IF {pick.intensityFactor ? pick.intensityFactor.toFixed(2) : '—'} · {pick.load ? Math.round(pick.load) : '—'} TSS
      </Text>
    </View>
  );
}

// A card under a coach bubble. Same width rule as the desktop: at most 280, or 78% of the chat.
function ChatCard({ card }: { card: CoachCard }) {
  const t = useV3Theme();
  const box = { alignSelf: 'flex-start' as const, width: '78%' as const, maxWidth: 280 };
  if (card.type === 'stat') {
    return (
      <StatTile
        label={card.label ?? ''}
        value={card.value ?? ''}
        unit={card.unit}
        caption={card.caption}
        style={[box, { marginTop: v3Spacing.small }]}
      />
    );
  }
  if (card.type === 'note') {
    if (!card.text) return null;
    return (
      <View style={[box, styles.note, { backgroundColor: t.cardNested, borderRadius: v3Radius.small }]}>
        <Text style={{ color: t.text, fontSize: v3Type.label }}>{card.text}</Text>
      </View>
    );
  }
  return (
    <View style={[box, { marginTop: v3Spacing.small }]}>
      <WorkoutCard pick={card} inChat />
    </View>
  );
}

function FitnessChart({
  chart, fitnessColor, fatigueColor, grid, labelColor,
}: {
  chart: { date: string; fitness: number; fatigue: number }[];
  fitnessColor: string;
  fatigueColor: string;
  grid: string;
  labelColor: string;
}) {
  // Real measured width, not a stretched fixed viewBox - see MetricChart.tsx for why
  // (preserveAspectRatio="none" scales strokes with the axis and renders them thick/blocky).
  const [w, setW] = useState(0);
  const H = 110, padT = 6, padB = 6, padR = 2;
  const W = w || 300;
  // Both series share ONE scale, otherwise the fitness/fatigue crossover - the whole point of
  // this chart - would be a meaningless artefact of two different axes.
  const all = chart.flatMap(p => [p.fitness, p.fatigue]);
  const max = Math.max(...all) * 1.1 || 1;
  // Vertical scale (2026-10-03): round load values down the left, same as MetricChart.
  const { ticks } = niceTicks(0, max);
  const padX = 8 + Math.max(1, ...ticks.map(v => String(Math.round(v)).length)) * 6;

  const x = (i: number) => padX + (W - padX - padR) * (i / (chart.length - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const path = (sel: (p: typeof chart[0]) => number) =>
    chart.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(sel(p)).toFixed(1)}`).join(' ');

  return (
    <View onLayout={e => setW(Math.round(e.nativeEvent.layout.width))} style={{ width: '100%' }}>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
        <Line x1={padX} y1={H - padB} x2={W - padR} y2={H - padB} stroke={grid} strokeWidth={1} />
        {ticks.filter(v => v > 0).map(v => (
          <React.Fragment key={v}>
            <Line x1={padX} y1={y(v)} x2={W - padR} y2={y(v)} stroke={grid} strokeOpacity={0.5} strokeWidth={1} />
            <SvgText x={padX - 5} y={y(v) + 3} fill={labelColor} fontSize={10} textAnchor="end">{Math.round(v)}</SvgText>
          </React.Fragment>
        ))}
        <Path d={path(p => p.fatigue)} stroke={fatigueColor} strokeWidth={1.2} fill="none"
              strokeDasharray="3,3" opacity={0.7} />
        <Path d={path(p => p.fitness)} stroke={fitnessColor} strokeWidth={1.8} fill="none" />
      </Svg>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 4, paddingLeft: padX }}>
        <Text style={{ color: labelColor, fontSize: v3Type.tiny }}>{fmtDate(chart[0].date) || chart[0].date}</Text>
        <Text style={{ color: labelColor, fontSize: v3Type.tiny }}>
          {fmtDate(chart[chart.length - 1].date) || chart[chart.length - 1].date}
        </Text>
      </View>
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
  bubble: { maxWidth: '85%', paddingHorizontal: 12, paddingVertical: 9, borderRadius: 14 },
  typing: { minHeight: 34, paddingHorizontal: 14, justifyContent: 'center', marginBottom: v3Spacing.small },
  note: { padding: 10, marginTop: v3Spacing.small },
  chips: { flexDirection: 'row', flexWrap: 'wrap', marginBottom: v3Spacing.medium },
  chip: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 8, marginRight: 6, marginBottom: 6 },
  inputRow: { flexDirection: 'row', alignItems: 'center' },
  input: { flex: 1, borderWidth: 1, paddingHorizontal: v3Spacing.small, paddingVertical: 9, fontSize: v3Type.body, marginRight: v3Spacing.small },
  swatch: { width: 12, height: 4, borderRadius: 2, marginRight: 6 },
  caption: { fontSize: v3Type.caption },
});
