import React, { useEffect } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useV3Theme } from '../theme/v3';
import { APP_VERSION } from '../config/version';
import { useNavMenu, MenuItem } from './MenuContext';
import { MenuBurger } from './MenuHost';

// Home's top bar, and the hand-over of Home's place list to the menu.
//
// History: 2026-08-29 one ☰ top-left on Home only - a strip sliding up from the bottom on phones,
// a side menu on tablets. 2026-11-02 (André, 🟣113): the menu moved out of Home into MenuHost, drawn
// above every page, in the style each device picks (pop-up list, fan-out, floating bar, or the side
// menu on tablets). Home still decides which places show (that depends on what is connected), so
// it builds the list and passes it here, as before; this hands it to the menu after every render.

export type NavShellItem = MenuItem;

export function NavShell({ items, children }: {
  items: NavShellItem[];
  selectedId?: string;     // kept for callers; the menu now works out the current place itself
  children: React.ReactNode;
}) {
  const t = useV3Theme();
  const insets = useSafeAreaInsets();
  const m = useNavMenu();

  // After every render: the tap handlers stay current; the menu redraws only when a place changes.
  useEffect(() => { m.publishItems(items); });

  return (
    <View style={styles.rootCol}>
      <View style={[styles.appbar, { paddingTop: insets.top + 6, backgroundColor: t.card, borderBottomColor: t.border }]}>
        <View style={styles.barRow}>
          {/* The ☰ only folds the tablet's side menu; the other styles open from the bottom. */}
          {m.style === 'side' ? <MenuBurger /> : <View style={{ width: 8 }} />}
          <Text style={[styles.appBrand, { color: t.text }]}>Sommet</Text>
          <Text style={[styles.navVer, { color: t.mutedText }]}>v{APP_VERSION}</Text>
        </View>
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  rootCol: { flex: 1 },
  appbar: { paddingHorizontal: 6, paddingBottom: 6, borderBottomWidth: StyleSheet.hairlineWidth, zIndex: 5 },
  barRow: { height: 40, flexDirection: 'row', alignItems: 'center' },
  appBrand: { fontSize: 17, fontWeight: '700', marginLeft: 4 },
  navVer: { fontSize: 11, marginLeft: 8 },
  content: { flex: 1 },
});
