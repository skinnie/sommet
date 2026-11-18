import React, { useMemo, useState } from 'react';
import { LayoutChangeEvent, StyleSheet, Text, View } from 'react-native';
import { SvgXml } from 'react-native-svg';
import { activityForName } from '../services/ActivityColors';

// move-page/03 (André, 2026-11-18): a move with no GPS track shows its sport where the map would
// be - faint contour lines in a map tile's colours and the sport's Ambit badge, so it sits well
// next to the real maps. Drawn by shared/sport_picture.js, the same file desktop's
// SportPicture.qml uses, so both apps show the same picture.
const P = require('../config/sportPicture');
const TYPES: { id: number; name: string; color: string; style: string; svg: string }[] =
  require('../config/activity_types.json');

export function SportPicture({ sport, seed, height = 160, radius = 12 }: {
  sport: string | null | undefined;   // the move's sport name (activity_type)
  seed: string;                       // anything stable for the move (its start), varies the lines
  height?: number;
  radius?: number;
}) {
  const [width, setWidth] = useState(0);
  const entry = useMemo(() => {
    const id = activityForName(sport).id;
    return TYPES.find(t => t.id === id) ?? TYPES[0];
  }, [sport]);
  const xml = useMemo(
    () => (width > 0 ? P.sportPictureSvg(entry, width, height, P.seedOf(seed)) : null),
    [entry, width, height, seed]);
  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.round(e.nativeEvent.layout.width);
    if (w > 0 && w !== width) setWidth(w);
  };
  const label = sport ? sport.charAt(0).toUpperCase() + sport.slice(1) : entry.name;
  return (
    <View onLayout={onLayout}
      style={[styles.box, { height, borderRadius: radius, backgroundColor: P.LAND }]}
      accessibilityRole="image" accessibilityLabel={`${label}, no GPS track`}>
      {xml && <SvgXml xml={xml} width={width} height={height} />}
      <Text style={[styles.name, { top: P.nameTop(height), color: P.NAME_COLOUR }]} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { width: '100%', overflow: 'hidden' },
  name: { position: 'absolute', left: 12, right: 12, textAlign: 'center', fontSize: 13, fontWeight: '600' },
});
