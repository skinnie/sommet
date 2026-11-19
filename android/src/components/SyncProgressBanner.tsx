// Sommet Sync progress (sync/12, André 2026-11-19: "maybe we should add a progress bar for sync
// no? and warn about freezes"). On every screen while a sync runs; a failed sync stays until
// closed. Parity with desktop's SyncProgressBanner.qml: same words, same bar, same first-sync line.
import React, { useEffect, useRef, useState } from 'react';
import { Animated, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useV3Theme } from '../theme/v3';
import {
  SommetSyncProgress, clearSommetSyncError, getSommetSyncProgress, onSommetSyncProgress,
} from '../services/SommetSync';

function stageText(stage: SommetSyncProgress['stage']): string {
  if (stage === 'moves') return 'Saving activities';
  if (stage === 'sending') return 'Sending to your server';
  return '';
}

export function SyncProgressBanner() {
  const theme = useV3Theme();
  const [p, setP] = useState<SommetSyncProgress>(getSommetSyncProgress());
  useEffect(() => onSommetSyncProgress(setP), []);
  const [barW, setBarW] = useState(0);
  const slide = useRef(new Animated.Value(0)).current;
  const indeterminate = p.stage !== '' && p.total <= 0;
  useEffect(() => {
    if (!indeterminate) return;
    const loop = Animated.loop(Animated.timing(slide, { toValue: 1, duration: 1200, useNativeDriver: true }));
    slide.setValue(0);
    loop.start();
    return () => loop.stop();
  }, [indeterminate, slide]);

  if (p.stage === '' && !p.error) return null;
  const fmt = (n: number) => n.toLocaleString();
  const title = p.error
    ? `Sommet Sync failed: ${p.error}`
    : `Sommet Sync · ${stageText(p.stage)}` + (p.total > 0 ? `  ${fmt(p.done)} / ${fmt(p.total)}` : '');
  const s = styles(theme);
  return (
    <View style={[s.box, p.error ? { borderColor: theme.error } : null]} accessibilityRole="progressbar"
          accessibilityLabel={title}>
      <View style={s.row}>
        <Text style={[s.title, p.error ? { color: theme.error } : null]} numberOfLines={1}>{title}</Text>
        {!!p.error && (
          <TouchableOpacity onPress={clearSommetSyncError} hitSlop={12}>
            <Text style={s.close}>Close</Text>
          </TouchableOpacity>
        )}
      </View>
      {!p.error && (
        <View style={s.track} onLayout={e => setBarW(e.nativeEvent.layout.width)}>
          {p.total > 0
            ? <View style={[s.fill, { width: `${Math.min(100, (p.done / p.total) * 100)}%` }]} />
            : <Animated.View style={[s.fill, { width: '25%', transform: [{
                translateX: slide.interpolate({ inputRange: [0, 1], outputRange: [-barW * 0.25, barW] }) }] }]} />}
        </View>
      )}
      {p.first && !p.error && (
        <Text style={s.detail}>
          First sync: all your activities and their tracks are coming in. This can take a few minutes; you can keep using Sommet.
        </Text>
      )}
    </View>
  );
}

const styles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  box: {
    marginHorizontal: 16, marginTop: 8, marginBottom: 4, paddingHorizontal: 16, paddingVertical: 8,
    backgroundColor: t.card, borderRadius: 8, borderWidth: 1, borderColor: t.border, gap: 6,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { flex: 1, color: t.text, fontSize: 13, fontWeight: '600' },
  close: { color: t.primary, fontSize: 12 },
  track: { height: 4, borderRadius: 2, backgroundColor: t.cardNested, overflow: 'hidden' },
  fill: { height: 4, borderRadius: 2, backgroundColor: t.primary },
  detail: { color: t.mutedText, fontSize: 12 },
});
