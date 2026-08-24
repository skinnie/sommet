import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Icon, { IconName } from '../components/ui/Icon';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';

// Real, 2026-08-09 (v3.0 UI port, "adaptive: tabs on phone, rail on tablet" - André's own
// choice). Ports desktop/qml/components/NavRail.qml's real pattern (a persistent list of
// destinations, each item's `visible` gated by connected-device type, selection by string id
// not index) onto two different RN shells rather than one: a bottom tab row on phone (native
// mobile pattern, thumb-reachable) and a left-side rail on tablet (closer to desktop's own
// layout, which has the horizontal room for it). Same `items`/`selectedId` data either way -
// only the chrome differs, matching rule 2's "UI evolutive according to the device" (there,
// about the connected watch; here, about the screen it's running on).
//
// Presentational only, same as NavRail.qml: callers build `items` (each with its own
// onPress calling navigation.navigate(...)) and decide visibility themselves via
// isKailash/isGarmin/etc, exactly like NavRail.qml's own `visible: !HomeViewModel.isKailash`
// bindings - this component doesn't know what a Kailash or a Garmin is.
//
// Width-only breakpoint (>=700), not orientation-gated like HomeScreen's own `roomy` - a
// side rail is a horizontal-space decision, not a landscape-vs-portrait one (a tablet held
// in portrait still has the width for it).
const TABLET_BREAKPOINT = 700;

export interface NavShellItem {
  id: string;
  label: string;
  icon: IconName;
  onPress: () => void;
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
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const isTablet = width >= TABLET_BREAKPOINT;

  if (isTablet) {
    return (
      <View style={styles.railRoot}>
        <View style={[styles.rail, { backgroundColor: t.card }]}>
          {items.map(item => (
            <NavShellButton key={item.id} item={item} selected={item.id === selectedId} vertical t={t} />
          ))}
        </View>
        <View style={styles.content}>{children}</View>
      </View>
    );
  }

  return (
    <View style={styles.tabRoot}>
      <View style={styles.content}>{children}</View>
      {/* 2026-08-24 (André, iPhone layout pass). paddingBottom must clear the device's
          bottom safe area (the iPhone home-indicator, ~34pt on a notched phone) so the tab
          row isn't overlapped by it; on Android insets.bottom is 0 so the fixed 10pt still
          applies. Kept as a Math.max floor rather than an additive so the padding never
          shrinks below the original 10pt on hardware with no inset. */}
      <View style={[styles.tabBar, { backgroundColor: t.card, paddingBottom: Math.max(10, insets.bottom) }]}>
        {items.map(item => (
          <NavShellButton key={item.id} item={item} selected={item.id === selectedId} vertical={false} t={t} />
        ))}
      </View>
    </View>
  );
}

function NavShellButton({
  item, selected, vertical, t,
}: { item: NavShellItem; selected: boolean; vertical: boolean; t: ReturnType<typeof useV3Theme> }) {
  // 2026-08-15 (André, design-parity audit: desktop is the baseline). Matched to
  // desktop/qml/components/NavItem.qml exactly: the selected destination is a SOLID
  // Theme.primary fill with its icon+label drawn in Theme.card (the surface color), not the
  // faint accent+'22' tint with primary-colored text this used before. Unselected items are
  // Theme.text (desktop's full-contrast idle color), not a greyed mutedText.
  const fg = selected ? t.card : t.text;
  return (
    <TouchableOpacity
      style={[vertical ? styles.railItem : styles.tabItem, selected && { backgroundColor: t.primary }]}
      onPress={item.onPress}
      activeOpacity={0.7}
    >
      <Icon name={item.icon} size={22} color={fg} />
      <Text
        style={[
          vertical ? styles.railLabel : styles.tabLabel,
          { color: fg },
        ]}
        numberOfLines={1}
      >
        {item.label}
      </Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  railRoot: { flex: 1, flexDirection: 'row' },
  rail: { width: 96, paddingTop: v3Spacing.medium, paddingHorizontal: v3Spacing.small },
  railItem: {
    alignItems: 'center', paddingVertical: v3Spacing.small, borderRadius: 10, marginBottom: 2,
  },
  railLabel: { fontSize: v3Type.tiny, marginTop: 4, textAlign: 'center' },

  tabRoot: { flex: 1 },
  tabBar: {
    // paddingBottom is applied inline from the safe-area inset (see render); paddingTop: 6
    // keeps the row's top breathing room.
    flexDirection: 'row', paddingTop: 6,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: '#00000022',
  },
  tabItem: { flex: 1, alignItems: 'center', paddingVertical: 4, borderRadius: 10 },
  tabLabel: { fontSize: v3Type.tiny, marginTop: 2 },

  content: { flex: 1 },
});
