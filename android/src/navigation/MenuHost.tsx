import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView, Animated, BackHandler, Keyboard, useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { NavigationContainerRefWithCurrent, StackActions } from '@react-navigation/native';
import Icon from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { t as tr } from '../i18n';
import { useNavMenu, MenuItem } from './MenuContext';
import { MenuStyle, FAN_SLOTS, FAN_MAX, BAR_MAX, shownFavourites, navIdForRoute, gridColumns } from './menuModel';

// The menu, drawn once above every page (it used to live on Home only). Four styles, picked per
// device in Settings > Appearance (see menuModel.ts): the pop-up list, the fan-out and the floating
// bar sit over the bottom of the page; the side menu sits on the left of every page on tablets.
// Flat, hairline borders and no shadows, like the rest of the app.

const FAB = 58;           // the round ☰
const EDGE = 18;          // its distance from the right and bottom edges
const BUBBLE = 48;        // a round button in the fan
const RAIL_W = 244;       // the side menu
const BAR_BOTTOM = 14;    // the floating bar's distance from the bottom
const BAR_ROOM = BAR_BOTTOM + BUBBLE + 12 + 8;   // the bar's height plus a gap

// Room a page with its own buttons at the bottom right (the activity map) keeps free for the menu.
export function menuBottomRoom(style: MenuStyle): number {
  if (style === 'side') return 0;
  return style === 'bar' ? BAR_ROOM : EDGE + FAB + 8;
}

function Burger({ color }: { color: string }) {
  return (
    <View>
      <View style={[s.line, { backgroundColor: color }]} />
      <View style={[s.line, { backgroundColor: color, marginVertical: 4 }]} />
      <View style={[s.line, { backgroundColor: color }]} />
    </View>
  );
}

// The ☰ that folds the tablet's side menu: in Home's top bar and next to every page's back arrow.
export function MenuBurger() {
  const th = useV3Theme();
  const m = useNavMenu();
  return (
    <TouchableOpacity onPress={m.toggleRail} style={s.burgerBtn} accessibilityLabel={tr.menuButton}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
      <Burger color={th.text} />
    </TouchableOpacity>
  );
}

export function MenuHost({ navRef, routeName, children }: {
  navRef: NavigationContainerRefWithCurrent<any>;
  routeName: string;
  children: React.ReactNode;
}) {
  const th = useV3Theme();
  const m = useNavMenu();
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);       // the list, or the fan
  const [sheet, setSheet] = useState(false);     // every place, from More
  const [keyboard, setKeyboard] = useState(false);
  const pop = useRef(new Animated.Value(0)).current;
  const sheetIn = useRef(new Animated.Value(0)).current;
  // The dimming behind an open menu follows both the list/fan and the sheet. Built once.
  const shaded = useRef(Animated.add(pop, sheetIn).interpolate({ inputRange: [0, 1], outputRange: [0, 0.35], extrapolate: 'clamp' })).current;

  useEffect(() => {
    Animated.timing(pop, { toValue: open ? 1 : 0, duration: 220, useNativeDriver: true }).start();
  }, [open, pop]);
  useEffect(() => {
    Animated.timing(sheetIn, { toValue: sheet ? 1 : 0, duration: 240, useNativeDriver: true }).start();
  }, [sheet, sheetIn]);

  function closeAll() { setOpen(false); setSheet(false); }

  // A new page or a new style starts with the menu closed.
  useEffect(() => { closeAll(); }, [routeName, m.style]);

  // Out of the way while typing (the Coach chat, names, search boxes).
  useEffect(() => {
    const a = Keyboard.addListener('keyboardDidShow', () => setKeyboard(true));
    const b = Keyboard.addListener('keyboardDidHide', () => setKeyboard(false));
    return () => { a.remove(); b.remove(); };
  }, []);

  // The phone's Back closes an open menu before it leaves the page.
  useEffect(() => {
    if (!open && !sheet) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { closeAll(); return true; });
    return () => sub.remove();
  }, [open, sheet]);

  const presentIds = m.items.map(i => i.id);
  const selectedId = navIdForRoute(routeName, presentIds);
  const byId = (id: string) => m.items.find(i => i.id === id);

  function go(id: string) {
    closeAll();
    if (id === selectedId) return;                        // already there
    const item = m.itemsRef.current.find(i => i.id === id);
    if (!item) return;
    // Every jump starts from Home, so Back always leads to Home, not through a chain of pages.
    if (navRef.isReady() && navRef.getCurrentRoute()?.name !== 'Home') navRef.dispatch(StackActions.popToTop());
    item.onPress();
  }

  function Row({ item, onPress }: { item: MenuItem; onPress: () => void }) {
    const sel = item.id === selectedId;
    return (
      <TouchableOpacity style={[s.row, sel && { backgroundColor: th.primary }]} activeOpacity={0.7} onPress={onPress}>
        <Icon name={item.icon} size={20} color={sel ? th.card : th.text} />
        <Text style={[s.rowLabel, { color: sel ? th.card : th.text }]} numberOfLines={1}>{item.label}</Text>
      </TouchableOpacity>
    );
  }

  // ── Tablet: the side menu, on every page ──
  // The pages always sit in the same place in this tree, whatever the style: switching style then
  // only adds or removes the menu. (A different tree around the pages made React rebuild every page,
  // so Home started over and reconnected to the watch - found on the tablet, 02/11.)
  const side = m.style === 'side';
  const home = byId('home');
  const rest = m.items.filter(i => i.id !== 'home');
  const rail = side ? (
    <View style={[s.rail, {
      width: m.railOpen ? RAIL_W : 0,
      backgroundColor: th.card,
      borderRightColor: m.railOpen ? th.border : 'transparent',
    }]}>
      {/* fixed width inside, so nothing reflows while the side menu folds */}
      <View style={{ width: RAIL_W, flex: 1, paddingTop: insets.top }}>
        {home && <View style={s.pin}><Row item={home} onPress={() => go('home')} /></View>}
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 8, paddingBottom: 8 + insets.bottom }}
          showsVerticalScrollIndicator={false}>
          {rest.map(it => <Row key={it.id} item={it} onPress={() => go(it.id)} />)}
        </ScrollView>
      </View>
    </View>
  ) : null;

  // ── The pop-up list, the fan-out and the floating bar, over the bottom of every page ──
  const show = !side && !keyboard && m.items.length > 0;
  const fabRight = EDGE + insets.right;
  const fabBottom = EDGE + insets.bottom;
  const cx = fabRight + FAB / 2;                 // the round ☰'s centre, from the right edge
  const cy = fabBottom + FAB / 2;                // ... and from the bottom edge

  const cols = gridColumns(width);
  // Room inside the sheet: its side padding (12 + 12) and its two hairline borders. Rounded down,
  // or the last place of a full row wraps to the next one.
  const cellW = Math.floor((width - 24 - 2 - insets.left - insets.right) / cols);
  const fanIds = [...shownFavourites(m.favourites.fan, presentIds, FAN_MAX), 'more'];
  const barIds = shownFavourites(m.favourites.bar, presentIds, BAR_MAX);
  const more = () => { setOpen(false); setSheet(true); };

  return (
    <View style={[s.rowRoot, { backgroundColor: th.background }]}>
      {rail}
      <View style={s.colRoot}>
      {children}

      {show && (
        <>
          <Animated.View pointerEvents={open || sheet ? 'auto' : 'none'} style={[s.scrim, { opacity: shaded }]}>
            <TouchableOpacity style={{ flex: 1 }} activeOpacity={1} onPress={closeAll} accessibilityLabel={tr.menuClose} />
          </Animated.View>

          {m.style === 'list' && (
            <Animated.View
              pointerEvents={open ? 'auto' : 'none'}
              style={[s.pop, {
                right: fabRight, bottom: fabBottom + FAB + 10,
                maxHeight: height - insets.top - fabBottom - FAB - 40,
                backgroundColor: th.card, borderColor: th.border, opacity: pop,
                transform: [{ scale: pop.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1] }) }],
                transformOrigin: 'bottom right',
              }]}
            >
              <ScrollView contentContainerStyle={{ padding: 6 }} showsVerticalScrollIndicator={false}>
                {m.items.map(it => <Row key={it.id} item={it} onPress={() => go(it.id)} />)}
              </ScrollView>
            </Animated.View>
          )}

          {m.style === 'fan' && fanIds.map((id, i) => {
            const [left, up] = FAN_SLOTS[i];
            const isMore = id === 'more';
            const it = byId(id);
            const label = isMore ? tr.menuMore : it?.label ?? '';
            const sel = id === selectedId;
            return (
              <Animated.View
                key={id}
                pointerEvents={open ? 'box-none' : 'none'}
                style={[s.fanWrap, {
                  right: cx - 48, bottom: cy - BUBBLE / 2, opacity: pop,
                  transform: [
                    { translateX: pop.interpolate({ inputRange: [0, 1], outputRange: [0, -left] }) },
                    { translateY: pop.interpolate({ inputRange: [0, 1], outputRange: [0, -up] }) },
                    { scale: pop.interpolate({ inputRange: [0, 1], outputRange: [0.4, 1] }) },
                  ],
                }]}
              >
                <TouchableOpacity
                  style={[s.bubble, { backgroundColor: sel ? th.primary : th.card, borderColor: sel ? th.primary : th.border }]}
                  onPress={isMore ? more : () => go(id)} accessibilityLabel={label}
                >
                  {isMore || !it
                    ? <Text style={[s.dots, { color: th.primary }]}>⋯</Text>
                    : <Icon name={it.icon} size={22} color={sel ? th.card : th.primary} />}
                </TouchableOpacity>
                <View style={s.fanLabelWrap} pointerEvents="none">
                  <Text style={[s.fanLabel, { color: th.text, backgroundColor: th.card }]} numberOfLines={1}>{label}</Text>
                </View>
              </Animated.View>
            );
          })}

          {(m.style === 'fan' || m.style === 'bar') && (
            <Animated.View
              pointerEvents={sheet ? 'auto' : 'none'}
              style={[s.sheet, {
                backgroundColor: th.card, borderColor: th.border,
                paddingLeft: 12 + insets.left, paddingRight: 12 + insets.right,
                paddingBottom: 16 + insets.bottom + (m.style === 'bar' ? BAR_ROOM : 0),
                maxHeight: height - insets.top - 24,
                transform: [{ translateY: sheetIn.interpolate({ inputRange: [0, 1], outputRange: [height, 0] }) }],
              }]}
            >
              <View style={[s.handle, { backgroundColor: th.border }]} />
              <ScrollView showsVerticalScrollIndicator={false}>
                <View style={s.grid}>
                  {m.items.map(it => {
                    const sel = it.id === selectedId;
                    return (
                      <TouchableOpacity key={it.id} style={[s.cell, { width: cellW }, sel && { backgroundColor: th.primary }]}
                        activeOpacity={0.7} onPress={() => go(it.id)}>
                        <Icon name={it.icon} size={24} color={sel ? th.card : th.text} />
                        <Text style={[s.cellLabel, { color: sel ? th.card : th.text }]} numberOfLines={2}>{it.label}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </ScrollView>
            </Animated.View>
          )}

          {(m.style === 'list' || m.style === 'fan') && (
            <TouchableOpacity
              style={[s.fab, { right: fabRight, bottom: fabBottom, backgroundColor: th.primary }]}
              activeOpacity={0.8}
              onPress={() => (open || sheet ? closeAll() : setOpen(true))}
              accessibilityLabel={open || sheet ? tr.menuClose : tr.menuButton}
            >
              {open || sheet
                ? <Text style={[s.cross, { color: th.card }]}>×</Text>
                : <Burger color={th.card} />}
            </TouchableOpacity>
          )}

          {m.style === 'bar' && (
            <View pointerEvents="box-none" style={[s.barWrap, { bottom: BAR_BOTTOM + insets.bottom }]}>
              <View style={[s.bar, { backgroundColor: th.card, borderColor: th.border }]}>
                {barIds.map(id => {
                  const it = byId(id);
                  if (!it) return null;
                  const sel = id === selectedId && !sheet;
                  return (
                    <TouchableOpacity key={id} style={[s.barBtn, sel && { backgroundColor: th.primary + '26' }]}
                      onPress={() => go(id)} accessibilityLabel={it.label}>
                      <Icon name={it.icon} size={22} color={sel ? th.primary : th.mutedText} />
                    </TouchableOpacity>
                  );
                })}
                <TouchableOpacity style={[s.barBtn, sheet && { backgroundColor: th.primary + '26' }]}
                  onPress={() => setSheet(v => !v)} accessibilityLabel={sheet ? tr.menuClose : tr.menuMore}>
                  <Text style={[s.dots, { color: sheet ? th.primary : th.mutedText }]}>⋯</Text>
                </TouchableOpacity>
              </View>
            </View>
          )}
        </>
      )}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  rowRoot: { flex: 1, flexDirection: 'row' },
  colRoot: { flex: 1 },

  line: { width: 18, height: 2, borderRadius: 1 },
  burgerBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', borderRadius: 10 },

  rail: { overflow: 'hidden', borderRightWidth: StyleSheet.hairlineWidth },
  pin: { padding: 8, paddingBottom: 0 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 11, paddingHorizontal: 12, borderRadius: 10, marginBottom: 2 },
  rowLabel: { fontSize: 14.5, flexShrink: 1 },

  scrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000', zIndex: 40 },

  pop: { position: 'absolute', zIndex: 50, width: 248, borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, overflow: 'hidden' },

  fanWrap: { position: 'absolute', zIndex: 50, width: 96, height: BUBBLE, alignItems: 'center' },
  bubble: { width: BUBBLE, height: BUBBLE, borderRadius: BUBBLE / 2, borderWidth: StyleSheet.hairlineWidth, alignItems: 'center', justifyContent: 'center' },
  fanLabelWrap: { position: 'absolute', top: BUBBLE + 3, left: 0, right: 0, alignItems: 'center' },
  fanLabel: { fontSize: 11, fontWeight: '500', paddingHorizontal: 6, paddingVertical: 1, borderRadius: 6, overflow: 'hidden', maxWidth: 96 },
  dots: { fontSize: 20, lineHeight: 22, letterSpacing: 1 },

  sheet: { position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 52, paddingTop: 8, borderTopWidth: StyleSheet.hairlineWidth, borderLeftWidth: StyleSheet.hairlineWidth, borderRightWidth: StyleSheet.hairlineWidth, borderTopLeftRadius: 20, borderTopRightRadius: 20 },
  handle: { width: 36, height: 4, borderRadius: 2, alignSelf: 'center', marginBottom: 10 },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  cell: { alignItems: 'center', gap: 5, paddingVertical: 10, paddingHorizontal: 4, borderRadius: 14 },
  cellLabel: { fontSize: 12, textAlign: 'center' },

  fab: { position: 'absolute', zIndex: 60, width: FAB, height: FAB, borderRadius: FAB / 2, alignItems: 'center', justifyContent: 'center' },
  cross: { fontSize: 30, lineHeight: 32, fontWeight: '300' },

  barWrap: { position: 'absolute', left: 0, right: 0, zIndex: 60, alignItems: 'center' },
  bar: { flexDirection: 'row', gap: 4, padding: 5, borderWidth: StyleSheet.hairlineWidth, borderRadius: 30 },
  barBtn: { width: BUBBLE, height: BUBBLE, borderRadius: BUBBLE / 2, alignItems: 'center', justifyContent: 'center' },
});
