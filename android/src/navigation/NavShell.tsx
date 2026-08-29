import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView, Animated,
  LayoutAnimation, Platform, UIManager, useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Icon, { IconName } from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { APP_VERSION } from '../config/version';

// Nav shell. Reworked 2026-08-29 (André) into ONE hamburger everywhere, matching the desktop:
//   - Phone (min side < 700): the nav is a MODAL DRAWER — ☰ slides it over the content, a scrim
//     dims the page, tapping away closes it. Hidden by default so the whole screen is content.
//   - Tablet / desktop (min side >= 700): the nav is a DOCKED rail beside the content; ☰ collapses
//     it to zero width and the content reflows to fill. Shown by default (there's room).
// Either way it's the same list: Home pinned at the top, Settings pinned at the bottom, the
// middle grouped (Training / Your watch / Advanced) and scrollable — a 1:1 port of
// desktop/qml/components/NavRail.qml. Presentational only: callers build `items` (each with its
// own onPress + optional `group`) and decide visibility themselves, exactly as before.
//
// This replaces the previous split (bottom tab row on phone, always-on icon rail on tablet): a
// bottom bar squeezed ~11-15 destinations into unreadable slivers, and neither could be hidden.
const TABLET_MIN_SIDE = 700;

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

export interface NavShellItem {
  id: string;
  label: string;
  icon: IconName;
  onPress: () => void;
  // Which drawer group the item sits under. Home/Settings are pinned and need no group; an item
  // with no (or an unknown) group falls into the first group so nothing is ever dropped.
  group?: 'training' | 'watch' | 'adv';
}

const GROUP_ORDER: Array<[NonNullable<NavShellItem['group']>, string]> = [
  ['training', 'Training'],
  ['watch', 'Your watch'],
  ['adv', 'Advanced'],
];

export function NavShell({
  items,
  selectedId,
  children,
}: {
  items: NavShellItem[];
  selectedId: string;
  children: React.ReactNode;
}) {
  const t = useV3Theme();
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const isTablet = Math.min(width, height) >= TABLET_MIN_SIDE;

  // Docked rail: open by default on tablet. Drawer: closed by default on phone.
  const [open, setOpen] = useState(isTablet);
  useEffect(() => { setOpen(isTablet); }, [isTablet]);

  // Phone drawer slide/scrim animation (0 = closed, 1 = open).
  const anim = useRef(new Animated.Value(isTablet ? 1 : 0)).current;
  useEffect(() => {
    if (isTablet) return;               // tablet uses a width collapse, not this slide
    Animated.timing(anim, { toValue: open ? 1 : 0, duration: 240, useNativeDriver: true }).start();
  }, [open, isTablet, anim]);

  function toggle() {
    if (isTablet) LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setOpen(o => !o);
  }
  function close() { setOpen(false); }

  const home = items.find(i => i.id === 'home');
  const settings = items.find(i => i.id === 'settings');
  const middle = items.filter(i => i.id !== 'home' && i.id !== 'settings');
  const inGroup = (g: string) =>
    middle.filter(i => (i.group || 'training') === g);

  function Row({ item }: { item: NavShellItem }) {
    const sel = item.id === selectedId;
    return (
      <TouchableOpacity
        style={[styles.row, sel && { backgroundColor: t.primary }]}
        activeOpacity={0.7}
        onPress={() => { item.onPress(); if (!isTablet) close(); }}
      >
        <Icon name={item.icon} size={20} color={sel ? t.card : t.text} />
        <Text style={[styles.rowLabel, { color: sel ? t.card : t.text }]} numberOfLines={1}>
          {item.label}
        </Text>
      </TouchableOpacity>
    );
  }

  const navList = (
    <View style={{ flex: 1 }}>
      <View style={[styles.navHead, { borderBottomColor: t.border }]}>
        <Text style={[styles.navBrand, { color: t.text }]}>Sommet</Text>
        <Text style={[styles.navVer, { color: t.mutedText }]}>v{APP_VERSION}</Text>
      </View>
      {home && <View style={styles.pin}><Row item={home} /></View>}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 8 }} showsVerticalScrollIndicator={false}>
        {GROUP_ORDER.map(([g, title]) => {
          const gi = inGroup(g);
          if (!gi.length) return null;
          return (
            <View key={g}>
              <Text style={[styles.groupHead, { color: t.mutedText }]}>{title.toUpperCase()}</Text>
              {gi.map(it => <Row key={it.id} item={it} />)}
            </View>
          );
        })}
      </ScrollView>
      {settings && (
        <View style={[styles.pin, styles.pinBottom, { borderTopColor: t.border, paddingBottom: 8 + insets.bottom }]}>
          <Row item={settings} />
        </View>
      )}
    </View>
  );

  return (
    <View style={styles.rootCol}>
      {/* App bar with the one hamburger (Home is the only screen wrapped in NavShell). */}
      <View style={[styles.appbar, { paddingTop: insets.top, backgroundColor: t.card, borderBottomColor: t.border }]}>
        <TouchableOpacity onPress={toggle} style={styles.burger} accessibilityLabel="Toggle menu" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <View style={[styles.bar, { backgroundColor: t.text }]} />
          <View style={[styles.bar, { backgroundColor: t.text, marginVertical: 4 }]} />
          <View style={[styles.bar, { backgroundColor: t.text }]} />
        </TouchableOpacity>
        <Text style={[styles.appBrand, { color: t.text }]}>Sommet</Text>
      </View>

      <View style={styles.bodyRow}>
        {isTablet && (
          <View style={[styles.dockRail, {
            width: open ? 244 : 0,
            backgroundColor: t.card,
            borderRightColor: open ? t.border : 'transparent',
          }]}>
            {/* keep contents from reflowing while the width animates to 0 */}
            <View style={{ width: 244, flex: 1 }}>{navList}</View>
          </View>
        )}
        <View style={styles.content}>{children}</View>
      </View>

      {!isTablet && (
        <>
          <Animated.View
            pointerEvents={open ? 'auto' : 'none'}
            style={[styles.scrim, { opacity: anim.interpolate({ inputRange: [0, 1], outputRange: [0, 0.45] }) }]}
          >
            <TouchableOpacity style={{ flex: 1 }} activeOpacity={1} onPress={close} />
          </Animated.View>
          <Animated.View
            style={[styles.drawer, {
              backgroundColor: t.card,
              borderRightColor: t.border,
              paddingTop: insets.top,
              transform: [{ translateX: anim.interpolate({ inputRange: [0, 1], outputRange: [-320, 0] }) }],
            }]}
          >
            {navList}
          </Animated.View>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  rootCol: { flex: 1 },

  appbar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 6, paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, zIndex: 5 },
  burger: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: 10 },
  bar: { width: 18, height: 2, borderRadius: 1 },
  appBrand: { fontSize: 17, fontWeight: '700', marginLeft: 4 },

  bodyRow: { flex: 1, flexDirection: 'row' },
  content: { flex: 1 },

  dockRail: { overflow: 'hidden', borderRightWidth: StyleSheet.hairlineWidth },

  scrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000', zIndex: 40 },
  drawer: { position: 'absolute', top: 0, left: 0, bottom: 0, width: 288, borderRightWidth: StyleSheet.hairlineWidth, zIndex: 50 },

  navHead: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, flexDirection: 'row', alignItems: 'baseline', borderBottomWidth: StyleSheet.hairlineWidth },
  navBrand: { fontSize: 17, fontWeight: '700' },
  navVer: { fontSize: 11, marginLeft: 8 },

  pin: { padding: 8 },
  pinBottom: { borderTopWidth: StyleSheet.hairlineWidth },

  groupHead: { fontSize: 10.5, letterSpacing: 0.6, fontWeight: '700', paddingHorizontal: 12, paddingTop: 12, paddingBottom: 5 },

  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 11, paddingHorizontal: 12, borderRadius: 10, marginBottom: 2 },
  rowLabel: { fontSize: 14.5 },
});
