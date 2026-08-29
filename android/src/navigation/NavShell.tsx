import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView, Animated,
  LayoutAnimation, Platform, UIManager, useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Icon, { IconName } from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { APP_VERSION } from '../config/version';

// Nav shell. Reworked 2026-08-29 (André). One ☰ everywhere, but two shapes for two form factors:
//   - Phone (min side < 700): ☰ (top-left, in the app bar) reveals a BOTTOM menu that slides up —
//     a horizontal, scrollable strip of every destination (Settings included, nothing pinned).
//     Hidden by default, so it costs no vertical space until you ask for it; a tap on the dimmed
//     content, or on a destination, hides it again. Same in portrait and landscape.
//   - Tablet / desktop (min side >= 700): ☰ toggles a DOCKED rail beside the content; collapsing
//     it reflows the page to full width. That rail is the flat, scrollable list (no groups - André
//     2026-08-29) with Home pinned at the top; Settings scrolls with the rest (a port of
//     qml/components/NavRail.qml).
// Presentational only: callers build `items` (each with its own onPress + optional `group`, used
// only by the tablet rail's grouping) and decide visibility themselves, exactly as before.
//
// This replaces the previous split (an always-on bottom tab row on phone that squeezed ~11-15
// destinations into unreadable slivers, and an always-on icon rail on tablet - neither hideable).
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
  const rest = items.filter(i => i.id !== 'home');   // flat, in order; Settings is naturally last

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

  // Phone bottom-menu cell: a small icon-over-label card, kept a readable width so the strip
  // scrolls sideways rather than squeezing. Selecting it navigates and hides the menu.
  function BottomItem({ item }: { item: NavShellItem }) {
    const sel = item.id === selectedId;
    return (
      <TouchableOpacity
        style={[styles.bItem, sel && { backgroundColor: t.primary }]}
        activeOpacity={0.7}
        onPress={() => { item.onPress(); close(); }}
      >
        <Icon name={item.icon} size={22} color={sel ? t.card : t.text} />
        <Text style={[styles.bLabel, { color: sel ? t.card : t.text }]} numberOfLines={1}>{item.label}</Text>
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
      {/* Flat list, no groups (André 2026-08-29: "why you introduced advanced ... don't introduce
          stuff without me asking"). Home is pinned above; everything else - Settings included, as
          its natural last position in `items` - scrolls here in order. */}
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 8, paddingBottom: 8 + insets.bottom }} showsVerticalScrollIndicator={false}>
        {rest.map(it => <Row key={it.id} item={it} />)}
      </ScrollView>
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
            style={[styles.scrim, { opacity: anim.interpolate({ inputRange: [0, 1], outputRange: [0, 0.35] }) }]}
          >
            <TouchableOpacity style={{ flex: 1 }} activeOpacity={1} onPress={close} />
          </Animated.View>
          <Animated.View
            pointerEvents={open ? 'auto' : 'none'}
            style={[styles.bottomBar, {
              backgroundColor: t.card,
              borderTopColor: t.border,
              paddingBottom: insets.bottom,
              transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [180, 0] }) }],
            }]}
          >
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.bottomScroll}>
              {items.map(it => <BottomItem key={it.id} item={it} />)}
            </ScrollView>
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

  // Phone: the bottom slide-up menu (a horizontal scrollable strip of every destination).
  bottomBar: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingTop: 6, borderTopWidth: StyleSheet.hairlineWidth, zIndex: 50 },
  bottomScroll: { flexGrow: 1, justifyContent: 'space-around', alignItems: 'center', paddingHorizontal: 8 },
  bItem: { minWidth: 74, alignItems: 'center', paddingVertical: 6, paddingHorizontal: 6, borderRadius: 12, marginHorizontal: 2 },
  bLabel: { fontSize: 10, marginTop: 2, maxWidth: 82 },

  navHead: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 10, flexDirection: 'row', alignItems: 'baseline', borderBottomWidth: StyleSheet.hairlineWidth },
  navBrand: { fontSize: 17, fontWeight: '700' },
  navVer: { fontSize: 11, marginLeft: 8 },

  pin: { padding: 8 },
  pinBottom: { borderTopWidth: StyleSheet.hairlineWidth },


  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 11, paddingHorizontal: 12, borderRadius: 10, marginBottom: 2 },
  rowLabel: { fontSize: 14.5 },
});
