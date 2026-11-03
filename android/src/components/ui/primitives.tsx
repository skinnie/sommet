import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, TextInput, TextInputProps, TouchableOpacity, ActivityIndicator, ViewStyle,
  useWindowDimensions, Modal, ScrollView, Animated, Easing, StyleProp,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../../theme/v3';
import { Card } from './Card';
import Icon, { IconName } from './Icon';

// v3.0 UI port (2026-08-09, "go all the way to the new theming") - this was the v2.5.0
// black/white/grey UI's shared kit; every screen that imports from here now gets desktop's
// real teal palette instead, the same way HomeScreen did first as the proof of concept.
// tokens.ts/useTheme() still exist (nothing here deletes them) but nothing in this file
// reads them anymore - every color below comes from useV3Theme(), so a future palette tweak
// in theme/v3.ts (or in Theme.qml, which this is copied from) changes every screen at once.
//
// Desktop has no literal counterpart for several of these (Chip/Badge/ActionTile/IconBadge/
// WarningNote don't exist in qml/components/ - QML's real components are Card/RoundedButton/
// RoundedSwitch/etc.) so those get a *faithful-in-spirit*, not pixel-copied, v3 treatment:
// same palette, spacing and radius tokens, same "rounded surfaces + shadow, not borders"
// design language as Card.qml's own header comment describes, applied to shapes desktop
// doesn't have a specific answer for.

// ── Section — a Card with an optional title/description ────────────────────
export function Section({
  title, description, children, style,
}: { title?: string; description?: string; children?: React.ReactNode; style?: ViewStyle }) {
  const t = useV3Theme();
  return (
    <Card style={[{ marginBottom: v3Spacing.medium }, style]}>
      {!!title && (
        <Text style={{ fontSize: v3Type.heading, fontWeight: '700', color: t.text, marginBottom: description ? 6 : 0 }}>
          {title}
        </Text>
      )}
      {!!description && (
        <Text style={{ fontSize: v3Type.body, color: t.mutedText, lineHeight: 19, marginBottom: 4 }}>
          {description}
        </Text>
      )}
      {children}
    </Card>
  );
}

// ── Button — filled (primary) / outline (secondary) / text (tertiary) ─────
type ButtonVariant = 'filled' | 'outline' | 'text';
export function Button({
  label, onPress, disabled, loading, busy, danger, accent, icon, variant = 'filled', tone = 'default', grow = true, style,
}: {
  label: string; onPress: () => void; disabled?: boolean;
  // Working (André, 2026-11-02, desktop picks #5, RoundedButton.busy): the label stays, so the
  // button keeps its name, three dots pulse after it, and taps are ignored until it's done.
  // `loading` is the older name for the same thing. Use it instead of swapping the label to
  // "Saving…".
  loading?: boolean; busy?: boolean;
  // Delete / Remove / Forget (desktop picks #5, RoundedButton.danger): a calm red - 10% red
  // fill, red hairline, bold red label. tone='alert' is the older name for the same thing.
  danger?: boolean;
  // The one main action, filled green (RoundedButton.accent), e.g. an empty page's button.
  accent?: boolean;
  icon?: IconName; variant?: ButtonVariant; tone?: 'default' | 'alert'; grow?: boolean; style?: ViewStyle;
}) {
  const t = useV3Theme();
  const isBusy = !!(busy || loading);
  const isDanger = !!danger || tone === 'alert';
  // 2026-08-15 (André, design-parity audit: desktop is the baseline; "buttons that are not
  // the same, outlines of buttons that are not the same"). Ported from
  // desktop/qml/components/RoundedButton.qml: every plain action button is a card-surfaced
  // button with a 1px border and a Theme.text label; `variant` distinguishes a bordered button
  // (filled/outline) from a borderless text button (tertiary).
  const isText = variant === 'text';
  const isAccent = !!accent && !isDanger && !isText;
  // A button that is disabled only because it's working keeps full strength (desktop _live).
  const live = !disabled || isBusy;
  const fg = isDanger ? t.error
    : isAccent ? t.card
    : (live ? t.text : t.mutedText);
  const bg = isText ? 'transparent'
    : isDanger ? t.error + '1A'
    : isAccent ? t.primary
    : t.card;
  const borderColor = isDanger ? t.error : isAccent ? t.primary : t.mutedText;

  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled || isBusy}
      activeOpacity={0.75}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled && !isBusy, busy: isBusy }}
      style={[
        {
          flexGrow: grow ? 1 : 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
          paddingVertical: isText ? 9 : 11, paddingHorizontal: v3Spacing.medium, borderRadius: v3Radius.small,
          backgroundColor: bg,
          borderColor: isText ? undefined : borderColor,
          borderWidth: isText ? 0 : 1,
          opacity: live ? 1 : 0.5,
        },
        style,
      ]}
    >
      {!!icon && <Icon name={icon} size={16} color={fg} />}
      <Text style={{ color: fg, fontWeight: isDanger ? '700' : '600', fontSize: v3Type.bodyLarge }}>{label}</Text>
      {isBusy && <View style={{ marginLeft: 2 }}><PulseDots color={fg} size={5} /></View>}
    </TouchableOpacity>
  );
}

// ── Toggle — pill switch, a faithful port of desktop's RoundedSwitch.qml ───
// 2026-08-15 (André, design-parity audit: desktop is the baseline; "Don't forget toggles,
// go to the detail"). React Native's built-in <Switch> can't reproduce desktop's switch:
// its ON track was a translucent primary+'88' (not a solid fill), its thumb stayed card in
// both states, and it can't carry the off-state border desktop uses. desktop/qml/components/
// RoundedSwitch.qml is a 36x20 pill: ON = solid Theme.primary track + Theme.card thumb; OFF
// = Theme.card track with a 1px Theme.mutedText border + a Theme.mutedText thumb (so an off
// switch is always visible, its own 2026-08-10 fix). This mirrors those values exactly, with
// the same 16px handle that sits 2px from each end.
export function Toggle({
  value, onValueChange, disabled, accessibilityLabel,
}: {
  value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean;
  // What the switch turns on, for screen readers (the pill has no text of its own).
  accessibilityLabel?: string;
}) {
  const t = useV3Theme();
  return (
    <TouchableOpacity
      onPress={() => onValueChange(!value)}
      disabled={disabled}
      activeOpacity={0.8}
      accessibilityRole="switch"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ checked: value, disabled: !!disabled }}
      style={{
        width: 36, height: 20, borderRadius: 10, justifyContent: 'center',
        backgroundColor: value ? t.primary : t.card,
        borderWidth: value ? 0 : 1, borderColor: t.mutedText,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <View style={{
        width: 16, height: 16, borderRadius: 8,
        backgroundColor: value ? t.card : t.mutedText,
        marginLeft: value ? 18 : 2,
      }} />
    </TouchableOpacity>
  );
}

// ── Dropdown — a value picker matching desktop's RoundedComboBox.qml ───────
// 2026-08-16 (André: "on desktop we have dropdown menus"). SuuntoLink (and desktop, off the
// same tools/settings_write.py AMBIT3_DISPLAY table) shows a settings field with a long list
// of choices - Language, GPS position format, Backlight mode - as a dropdown, not the chip
// row Android was rendering for every enum. Styled after RoundedComboBox.qml: a card-surfaced
// box with a 1px Theme.mutedText border, the current label + a chevron, opening a bordered
// card menu whose selected row is tinted 12% Theme.primary (the popup highlight desktop uses,
// Qt.alpha(Theme.primary, 0.12); '1F' is 12% in React Native's #RRGGBBAA).
export function Dropdown({
  value, choices, onSelect, disabled,
}: {
  value: number;
  choices: { value: number; label: string }[];
  onSelect: (v: number) => void;
  disabled?: boolean;
}) {
  const t = useV3Theme();
  const [open, setOpen] = useState(false);
  const current = choices.find(c => c.value === value)?.label ?? String(value);
  return (
    <>
      <TouchableOpacity
        onPress={() => setOpen(true)}
        disabled={disabled}
        activeOpacity={0.75}
        style={{
          flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 6,
          minWidth: 130, maxWidth: 210, flexShrink: 1,
          backgroundColor: t.card, borderColor: t.mutedText, borderWidth: 1,
          borderRadius: v3Radius.card, paddingLeft: v3Spacing.medium, paddingRight: 10, paddingVertical: 8,
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <Text numberOfLines={1} style={{ color: t.text, fontSize: v3Type.body, flexShrink: 1 }}>{current}</Text>
        {/* No down-chevron glyph in the icon font; chevronRight rotated 90° points down. */}
        <View style={{ transform: [{ rotate: '90deg' }] }}>
          <Icon name="chevronRight" size={18} color={t.mutedText} />
        </View>
      </TouchableOpacity>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <TouchableOpacity
          activeOpacity={1}
          onPress={() => setOpen(false)}
          style={{ flex: 1, backgroundColor: '#00000066', justifyContent: 'center', padding: 28 }}
        >
          <View style={{
            backgroundColor: t.card, borderColor: t.mutedText, borderWidth: 1,
            borderRadius: v3Radius.card, maxHeight: '70%', overflow: 'hidden',
          }}>
            <ScrollView>
              {choices.map(c => {
                const sel = c.value === value;
                return (
                  <TouchableOpacity
                    key={c.value}
                    onPress={() => { onSelect(c.value); setOpen(false); }}
                    style={{
                      paddingVertical: 12, paddingHorizontal: v3Spacing.medium,
                      backgroundColor: sel ? t.primary + '1F' : 'transparent',
                    }}
                  >
                    <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: sel ? '700' : '400' }}>
                      {c.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </View>
        </TouchableOpacity>
      </Modal>
    </>
  );
}

// ── StatusLine — replaces the old colored dot + text pattern ──────────────
export function StatusLine({ text, tone = 'muted' }: { text: string; tone?: 'muted' | 'alert' }) {
  const t = useV3Theme();
  const color = tone === 'alert' ? t.error : t.mutedText;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 10 }}>
      <View style={{ width: 7, height: 7, borderRadius: 3.5, backgroundColor: color, flexShrink: 0 }} />
      <Text style={{ fontSize: v3Type.label, fontWeight: '600', color, flexShrink: 1 }}>{text}</Text>
    </View>
  );
}

// ── WarningNote — neutral surface, warning-colored icon ────────────────────
export function WarningNote({ children }: { children: React.ReactNode }) {
  const t = useV3Theme();
  return (
    <View style={{
      flexDirection: 'row', gap: 8, alignItems: 'flex-start',
      backgroundColor: t.card, borderRadius: v3Radius.small, padding: 12, marginTop: 8,
    }}>
      <Icon name="warning" size={15} color={t.warning} />
      <Text style={{ flex: 1, fontSize: v3Type.label, color: t.mutedText, lineHeight: 17 }}>{children}</Text>
    </View>
  );
}

// ── Chip — small filled pill, e.g. "Connected" - tinted with the primary
// color (a real status pill, not a neutral bordered box) ───────────────────
export function Chip({ label, icon }: { label: string; icon?: IconName }) {
  const t = useV3Theme();
  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', gap: 5, alignSelf: 'flex-start',
      backgroundColor: t.primary + '1F', borderRadius: 999, paddingVertical: 5, paddingHorizontal: 10, marginTop: 8,
    }}>
      {!!icon && <Icon name={icon} size={12} color={t.primary} />}
      <Text style={{ fontSize: v3Type.label, fontWeight: '600', color: t.primary }}>{label}</Text>
    </View>
  );
}

// ── Badge — tiny neutral label pill, e.g. "v2.5.15" ─────────────────────────
export function Badge({ label }: { label: string }) {
  const t = useV3Theme();
  return (
    <Text style={{
      fontSize: v3Type.tiny, fontWeight: '700', letterSpacing: 0.3, color: t.mutedText,
      backgroundColor: t.mutedText + '1A',
      borderRadius: v3Radius.small - 2, paddingHorizontal: 6, paddingVertical: 2, overflow: 'hidden',
    }}>
      {label}
    </Text>
  );
}

// ── IconBadge — circular icon container, e.g. card headers ─────────────────
export function IconBadge({ icon, size = 34 }: { icon: IconName; size?: number }) {
  const t = useV3Theme();
  return (
    <View style={{
      width: size, height: size, borderRadius: size / 2,
      backgroundColor: t.primary + '1A',
      alignItems: 'center', justifyContent: 'center',
    }}>
      <Icon name={icon} size={size * 0.5} color={t.primary} />
    </View>
  );
}

// ── FieldRow — icon-prefixed text input ─────────────────────────────────────
export function FieldRow({ icon, style, ...inputProps }: { icon: IconName } & TextInputProps) {
  const t = useV3Theme();
  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', gap: 8,
      backgroundColor: t.card, borderColor: t.mutedText + '33', borderWidth: 1,
      borderRadius: v3Radius.small, paddingHorizontal: 12, marginTop: 10,
    }}>
      <Icon name={icon} size={15} color={t.mutedText} />
      <TextInput
        placeholderTextColor={t.mutedText}
        style={[{ flex: 1, color: t.text, fontSize: v3Type.bodyLarge, paddingVertical: 10 }, style]}
        {...inputProps}
      />
    </View>
  );
}

// ── ActionTile — Card-surfaced square used on Home's action grid ───────────
export function ActionTile({
  icon, label, progress, onPress, onLongPress, disabled, busy, basis, grow,
}: {
  icon: IconName; label: string; progress?: string; onPress: () => void; onLongPress?: () => void; disabled?: boolean; busy?: boolean;
  // Column width as a flex-basis. When omitted it adapts to the screen: two columns on
  // phones, three on roomy/landscape/tablet widths. flexGrow 0 keeps a lone trailing tile
  // at its column width (centered by the row) rather than stretching full-width. The row
  // is width-capped upstream, so this stays clean from phones to tablets.
  basis?: string | number;
  // Real, 2026-08-09 ("enlarge the synced and gps buttons to match together the width of
  // either [card]") - Home's own 2-tile sync/GPS row left a big gap on either side of a
  // fixed-percentage-width pair inside the same width-capped row the weather/device cards
  // fill completely. flexGrow:1 + flexBasis:0 (standard "share the row evenly" flex, not a
  // fixed percentage) makes the pair always fill that row exactly, whatever its cap is.
  grow?: boolean;
}) {
  const t = useV3Theme();
  const { width, height } = useWindowDimensions();
  // Column count adapts to the screen (row is width-capped upstream: ~560 portrait,
  // ~960 landscape), targeting ~170 px tiles:
  //   landscape/roomy → ~5 across   ·   portrait tablet → 3 across   ·   phone → 2.
  // Orientation-based (not width-only) so a portrait tablet — wide but tall — stays a
  // 3-column column layout, matching the pre-landscape look.
  const roomy = width > height && width >= 700;
  const effectiveBasis = basis ?? (roomy ? '18%' : width < 480 ? '48%' : '30%');
  return (
    <TouchableOpacity
      onPress={onPress}
      onLongPress={onLongPress}
      disabled={disabled}
      activeOpacity={0.75}
      style={{
        flexBasis: grow ? 0 : effectiveBasis, flexGrow: grow ? 1 : 0, minWidth: 84,
        // No shadows (André, 2026-08-25: "all app, desktop android, same for previous rules") -
        // dropped the RN shadow/elevation this tile leaned on for definition; a hairline border
        // in the idle state does that job now instead (was transparent - shadow-only before).
        backgroundColor: t.card, borderColor: busy ? t.primary : t.border, borderWidth: busy ? 1.4 : 1,
        borderRadius: v3Radius.card - 2, paddingVertical: 14, paddingHorizontal: 6,
        alignItems: 'center', justifyContent: 'center', gap: 6,
        opacity: disabled && !busy ? 0.5 : 1,
      }}
    >
      {busy
        ? <View style={{ height: 20, justifyContent: 'center' }}><PulseDots /></View>
        : <Icon name={icon} size={20} color={t.text} />}
      <Text style={{ fontSize: v3Type.tiny, fontWeight: '700', letterSpacing: 0.3, color: t.text, textAlign: 'center' }} numberOfLines={2}>
        {label}
      </Text>
      {/* Always rendered (space when idle) so every tile reserves the same
          height whether or not it's currently showing progress — otherwise
          the busy tile grows a line taller than its neighbors. */}
      <Text style={{ fontSize: v3Type.tiny - 1, color: t.mutedText }}>{progress ?? ' '}</Text>
    </TouchableOpacity>
  );
}

// ── Logo — mountain mark + optional "Sommet" wordmark ────────────────────
export function Logo({ size = 64, wordmark = true }: { size?: number; wordmark?: boolean }) {
  const t = useV3Theme();
  return (
    <View style={{ alignItems: 'center' }}>
      <Icon name="mountain" size={size} color={t.primary} />
      {wordmark && (
        <Text style={{ marginTop: 3, fontSize: Math.round(size * 0.26), fontWeight: '800', color: t.text, letterSpacing: 0.4 }}>
          Sommet
        </Text>
      )}
    </View>
  );
}

// ── ExportedFileRow — filename + a share link, used by the Garmin screens ──
export function ExportedFileRow({
  fileName, onShare, shareLabel,
}: { fileName: string; onShare: () => void; shareLabel: string }) {
  const t = useV3Theme();
  return (
    <View style={{
      flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
      marginTop: 8, paddingTop: 8, borderTopWidth: 1, borderTopColor: t.mutedText + '22',
    }}>
      <Text style={{ color: t.mutedText, fontSize: v3Type.label, flex: 1, marginRight: 10 }} numberOfLines={1}>
        {fileName}
      </Text>
      <TouchableOpacity onPress={onShare}>
        <Text style={{ color: t.primary, fontSize: v3Type.label, fontWeight: '700' }}>{shareLabel}</Text>
      </TouchableOpacity>
    </View>
  );
}

// ═══ André's 2026-11-02 picks, ported from the desktop components of the same name ═════════
// (desktop/qml/components/PulseDots, StatusChip, StatusNote, SearchField, EmptyState, StatTile,
// SegmentedControl, SkeletonList, RoundedCheckBox). Same sizes, colours and wording as desktop.

export type StatusTone = 'success' | 'warning' | 'error' | 'primary' | 'neutral';
function toneColor(t: ReturnType<typeof useV3Theme>, tone: string): string {
  return tone === 'success' ? t.success
    : tone === 'warning' ? t.warning
    : tone === 'error' ? t.error
    : tone === 'primary' || tone === 'working' ? t.primary
    : t.mutedText;
}

// ── PulseDots — Sommet's "working" mark: three dots that pulse in turn (160 ms apart) ──────
export function PulseDots({
  color, size = 6, gap = 4, running = true,
}: { color?: string; size?: number; gap?: number; running?: boolean }) {
  const t = useV3Theme();
  const c = color ?? t.primary;
  const dots = useRef([0, 1, 2].map(() => new Animated.Value(0.25))).current;
  useEffect(() => {
    if (!running) {
      dots.forEach(d => d.setValue(0.25));
      return;
    }
    const loops = dots.map((d, i) => Animated.loop(Animated.sequence([
      Animated.delay(i * 160),
      Animated.timing(d, { toValue: 1, duration: 320, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.timing(d, { toValue: 0.25, duration: 320, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.delay((2 - i) * 160),
    ])));
    loops.forEach(l => l.start());
    return () => loops.forEach(l => l.stop());
  }, [running, dots]);
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap }} accessible accessibilityLabel="Working">
      {dots.map((d, i) => (
        <Animated.View key={i} style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: c, opacity: d }} />
      ))}
    </View>
  );
}

// ── StatusChip — one small status tag: Connected, Experimental, Soon, Due, Not set up ──────
// A light tint, a thin border and the text, all in the tone's colour (desktop picks #6).
export function StatusChip({
  label, tone = 'neutral', style,
}: { label: string; tone?: StatusTone; style?: StyleProp<ViewStyle> }) {
  const t = useV3Theme();
  const c = toneColor(t, tone);
  return (
    <View
      accessible
      accessibilityLabel={label}
      style={[{
        alignSelf: 'flex-start', borderRadius: v3Radius.small, borderWidth: 1, borderColor: c,
        backgroundColor: c + (tone === 'neutral' ? '14' : '1A'), paddingHorizontal: 6, paddingVertical: 2,
      }, style]}
    >
      <Text style={{ color: c, fontSize: v3Type.caption }}>{label}</Text>
    </View>
  );
}

// ── StatusNote — the result of an action, under the button that caused it (picks #7) ──────
// A card with a thin coloured bar on the left, an icon, the text, and a × to close it. Hidden
// while `text` is empty; a new text shows it again after a close. tone 'working' is a step still
// running: pulsing dots and no ×.
export function StatusNote({
  text, tone = 'success', closable, style,
}: {
  text?: string | null; tone?: 'success' | 'warning' | 'error' | 'working';
  closable?: boolean; style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  const [closed, setClosed] = useState(false);
  useEffect(() => { setClosed(false); }, [text]);
  if (!text || closed) return null;
  const c = toneColor(t, tone);
  const canClose = closable ?? tone !== 'working';
  return (
    <View
      accessibilityRole={tone === 'error' ? 'alert' : undefined}
      accessibilityLiveRegion="polite"
      style={[{
        flexDirection: 'row', alignItems: 'flex-start', marginTop: 10,
        backgroundColor: t.card, borderColor: t.border, borderWidth: 1, borderRadius: v3Radius.small,
        paddingVertical: 8, paddingLeft: 14, paddingRight: canClose ? 4 : 12, minHeight: 34,
      }, style]}
    >
      <View style={{ position: 'absolute', left: 5, top: 6, bottom: 6, width: 3, borderRadius: 1.5, backgroundColor: c }} />
      <View style={{ width: 16, minHeight: 17, alignItems: 'center', justifyContent: 'center', marginRight: 8 }}>
        {tone === 'working'
          ? <PulseDots color={c} size={5} gap={2} />
          : <Icon name={tone === 'error' ? 'error' : tone === 'warning' ? 'warning' : 'check'} size={16} color={c} />}
      </View>
      <Text style={{ flex: 1, color: t.text, fontSize: v3Type.label, lineHeight: 17 }}>{text}</Text>
      {canClose && (
        <TouchableOpacity
          onPress={() => setClosed(true)}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={{ width: 24, height: 24, marginTop: -3, marginLeft: 6, alignItems: 'center', justifyContent: 'center', borderRadius: v3Radius.small }}
        >
          <Icon name="close" size={16} color={t.mutedText} />
        </TouchableOpacity>
      )}
    </View>
  );
}

// ── SearchField — magnifier on the left, a × to clear once there's text (picks #9) ─────────
// Same field look as desktop's RoundedTextField: cardNested, hairline, 2px green border when
// focused, round corners.
export function SearchField({
  value, onChangeText, placeholder = 'Search', style, ...rest
}: TextInputProps & { value: string; onChangeText: (s: string) => void }) {
  const t = useV3Theme();
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<TextInput>(null);
  return (
    <View style={[{
      flexDirection: 'row', alignItems: 'center', minHeight: 40,
      backgroundColor: t.cardNested, borderRadius: v3Radius.small,
      borderWidth: focused ? 2 : 1, borderColor: focused ? t.primary : t.border,
      paddingLeft: focused ? 9 : 10, paddingRight: focused ? 3 : 4,
    }, style as ViewStyle]}>
      <Icon name="search" size={16} color={t.mutedText} />
      <TextInput
        ref={inputRef}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={t.mutedText}
        returnKeyType="search"
        {...rest}
        onFocus={e => { setFocused(true); rest.onFocus?.(e); }}
        onBlur={e => { setFocused(false); rest.onBlur?.(e); }}
        style={{ flex: 1, color: t.text, fontSize: v3Type.body, paddingVertical: 8, paddingHorizontal: 8 }}
      />
      {value.length > 0 && (
        <TouchableOpacity
          onPress={() => { onChangeText(''); inputRef.current?.focus(); }}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel="Clear"
          style={{ width: 28, height: 28, alignItems: 'center', justifyContent: 'center', borderRadius: v3Radius.small }}
        >
          <Icon name="close" size={16} color={t.mutedText} />
        </TouchableOpacity>
      )}
    </View>
  );
}

// ── EmptyState — what an empty page says (picks #11) ─────────────────────────────────────
// An icon, a title, one sentence on what to do, and optionally one button that does it.
export function EmptyState({
  icon, title, text, actionLabel, onAction, style,
}: {
  icon?: IconName; title: string; text?: string; actionLabel?: string; onAction?: () => void;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  return (
    <View style={[{ alignItems: 'center', paddingVertical: v3Spacing.large, paddingHorizontal: v3Spacing.medium, gap: v3Spacing.small }, style]}>
      {!!icon && (
        <View style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: t.cardNested, alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={icon} size={24} color={t.primary} />
        </View>
      )}
      <Text style={{ color: t.text, fontSize: v3Type.subtitle, fontWeight: '700', textAlign: 'center' }}>{title}</Text>
      {!!text && <Text style={{ color: t.mutedText, fontSize: v3Type.body, textAlign: 'center', lineHeight: 19 }}>{text}</Text>}
      {!!actionLabel && !!onAction && (
        <View style={{ marginTop: 2 }}>
          <Button label={actionLabel} onPress={onAction} accent grow={false} />
        </View>
      )}
    </View>
  );
}

// ── StatTile — one number in a tile (picks #12) ──────────────────────────────────────────
// A label (with an optional small tag such as "3 days old"), the value with its unit, and a
// short caption under it such as "▲ 4 vs 7-day average".
export function StatTile({
  label, value, unit, caption, badge, valueColor, style,
}: {
  label: string; value: string; unit?: string; caption?: string; badge?: string;
  valueColor?: string; style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  return (
    <View
      accessible
      accessibilityLabel={`${label}: ${value}${unit ? ' ' + unit : ''}${caption ? ', ' + caption : ''}`}
      style={[{ backgroundColor: t.cardNested, borderRadius: v3Radius.small, paddingHorizontal: 12, paddingVertical: 10, gap: 2 }, style]}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{label}</Text>
        {!!badge && (
          <View style={{ borderWidth: 1, borderColor: t.border, borderRadius: 6, paddingHorizontal: 4 }}>
            <Text style={{ color: t.mutedText, fontSize: v3Type.tiny }}>{badge}</Text>
          </View>
        )}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 4 }}>
        <Text style={{ color: valueColor ?? t.text, fontSize: v3Type.largeTitle, fontWeight: '700' }}>{value}</Text>
        {!!unit && <Text style={{ color: t.mutedText, fontSize: v3Type.label }}>{unit}</Text>}
      </View>
      {!!caption && <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{caption}</Text>}
    </View>
  );
}

// ── SegmentedControl — one switch for two or three choices (picks #14) ───────────────────
// A track, and the chosen part as a card with bold text. Each part is as wide as its bold
// label, so the switch doesn't change width with the choice.
export function SegmentedControl({
  options, index, onChange, disabled, style,
}: {
  options: string[]; index: number; onChange: (i: number) => void; disabled?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  return (
    <View
      accessibilityRole="radiogroup"
      style={[{
        flexDirection: 'row', alignSelf: 'flex-start', maxWidth: '100%', gap: 2, padding: 3,
        backgroundColor: t.cardNested, borderColor: t.border, borderWidth: 1, borderRadius: v3Radius.small + 2,
        opacity: disabled ? 0.5 : 1,
      }, style]}
    >
      {options.map((o, i) => {
        const chosen = i === index;
        return (
          <TouchableOpacity
            key={o + i}
            disabled={disabled}
            onPress={() => { if (!chosen) onChange(i); }}
            activeOpacity={0.75}
            accessibilityRole="radio"
            accessibilityLabel={o}
            accessibilityState={{ checked: chosen, disabled: !!disabled }}
            style={{
              flexShrink: 1, minHeight: 30, paddingHorizontal: 14, justifyContent: 'center', borderRadius: v3Radius.small,
              backgroundColor: chosen ? t.card : 'transparent',
              borderWidth: 1, borderColor: chosen ? t.border : 'transparent',
            }}
          >
            {/* The bold copy sets the width; the visible label sits on top of it. */}
            <Text numberOfLines={1} style={{ fontSize: v3Type.body, fontWeight: '700', opacity: 0 }}>{o}</Text>
            <Text numberOfLines={1} style={{
              position: 'absolute', left: 0, right: 0, textAlign: 'center',
              fontSize: v3Type.body, fontWeight: chosen ? '700' : '400', color: chosen ? t.text : t.mutedText,
            }}>{o}</Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );
}

// ── SkeletonList — grey rows shaped like the list to come, while a long read runs (#13) ───
// The rows pulse between full and half strength every 1.2 s. Show it only while the list is
// empty and loading.
export function SkeletonList({ count = 6, rowHeight = 56 }: { count?: number; rowHeight?: number }) {
  const t = useV3Theme();
  const pulse = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: 0.5, duration: 600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [pulse]);
  return (
    <Animated.View style={{ opacity: pulse, gap: v3Spacing.small }} accessible accessibilityLabel="Loading">
      {Array.from({ length: count }, (_, i) => (
        <View key={i} style={{
          height: rowHeight, borderRadius: v3Radius.small, backgroundColor: t.card,
          borderWidth: 1, borderColor: t.border, flexDirection: 'row', alignItems: 'center', paddingLeft: 12, gap: 12,
        }}>
          <View style={{ width: 34, height: 34, borderRadius: v3Radius.small, backgroundColor: t.cardNested }} />
          <View style={{ flex: 1, gap: 6 }}>
            <View style={{ width: '55%', height: 9, borderRadius: 4.5, backgroundColor: t.cardNested }} />
            <View style={{ width: '30%', height: 9, borderRadius: 4.5, backgroundColor: t.cardNested }} />
          </View>
        </View>
      ))}
    </Animated.View>
  );
}

// ── Checkbox — RoundedCheckBox with the drawn tick (picks #15) ───────────────────────────
// The icon font has no bare checkmark and the circle-tick read as a circle inside a square:
// two rounded bars in an L, turned 45°, popping in over 120 ms.
export function Checkbox({
  value, onValueChange, label, disabled, labelSize = v3Type.body, gap = 8,
}: {
  value: boolean; onValueChange: (v: boolean) => void; label?: string; disabled?: boolean;
  labelSize?: number; gap?: number;
}) {
  const t = useV3Theme();
  const anim = useRef(new Animated.Value(value ? 1 : 0)).current;
  useEffect(() => {
    Animated.timing(anim, { toValue: value ? 1 : 0, duration: 120, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, [value, anim]);
  const scale = anim.interpolate({ inputRange: [0, 1], outputRange: [0.4, 1] });
  return (
    <TouchableOpacity
      onPress={() => onValueChange(!value)}
      disabled={disabled}
      activeOpacity={0.75}
      accessibilityRole="checkbox"
      accessibilityLabel={label}
      accessibilityState={{ checked: value, disabled: !!disabled }}
      hitSlop={6}
      style={{ flexDirection: 'row', alignItems: 'center', gap, paddingVertical: 4, opacity: disabled ? 0.5 : 1 }}
    >
      <View style={{
        width: 22, height: 22, borderRadius: 6, borderWidth: 1,
        backgroundColor: value ? t.primary : t.card, borderColor: value ? t.primary : t.mutedText,
        alignItems: 'center', justifyContent: 'center',
      }}>
        <Animated.View style={{ width: 6, height: 11, marginTop: -2, opacity: anim, transform: [{ rotate: '45deg' }, { scale }] }}>
          <View style={{ position: 'absolute', right: 0, top: 0, width: 2.4, height: 11, borderRadius: 1.2, backgroundColor: t.card }} />
          <View style={{ position: 'absolute', left: 0, bottom: 0, width: 6, height: 2.4, borderRadius: 1.2, backgroundColor: t.card }} />
        </Animated.View>
      </View>
      {!!label && <Text style={{ flexShrink: 1, color: t.text, fontSize: labelSize }}>{label}</Text>}
    </TouchableOpacity>
  );
}
