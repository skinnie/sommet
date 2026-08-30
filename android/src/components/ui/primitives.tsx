import React, { useState } from 'react';
import {
  View, Text, TextInput, TextInputProps, TouchableOpacity, ActivityIndicator, ViewStyle,
  useWindowDimensions, Modal, ScrollView,
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
  label, onPress, disabled, loading, icon, variant = 'filled', tone = 'default', grow = true, style,
}: {
  label: string; onPress: () => void; disabled?: boolean; loading?: boolean;
  icon?: IconName; variant?: ButtonVariant; tone?: 'default' | 'alert'; grow?: boolean; style?: ViewStyle;
}) {
  const t = useV3Theme();
  const isAlert = tone === 'alert';
  // 2026-08-15 (André, design-parity audit: desktop is the baseline; "buttons that are not
  // the same, outlines of buttons that are not the same"). Ported faithfully from
  // desktop/qml/components/RoundedButton.qml: on desktop EVERY plain action button (Save,
  // Connect, Import, Disconnect...) is a card-surfaced button with a 1px Theme.mutedText
  // border and a Theme.text label - it only fills with Theme.primary when `checked` (a
  // selected/toggle state, which in this RN app is expressed by dedicated controls -
  // Toggle, the Appearance selector, chip rows - not by this Button). So a filled teal CTA
  // was Android-only; matched to desktop's bordered look here. `variant` now distinguishes a
  // bordered button (filled/outline - both desktop's idle look, mutedText border) from a
  // borderless text button (tertiary). tone='alert' recolors border+label to Theme.error for
  // destructive actions, the same way desktop tints a destructive action red.
  const isText = variant === 'text';
  const fg = isAlert
    ? t.error
    : (disabled || loading ? t.mutedText : t.text);
  const borderColor = isAlert ? t.error : t.mutedText;

  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled || loading}
      activeOpacity={0.75}
      style={[
        {
          flexGrow: grow ? 1 : 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
          paddingVertical: isText ? 9 : 11, paddingHorizontal: v3Spacing.medium, borderRadius: v3Radius.small,
          backgroundColor: isText ? 'transparent' : t.card,
          borderColor: isText ? undefined : borderColor,
          borderWidth: isText ? 0 : 1,
          opacity: disabled || loading ? 0.5 : 1,
        },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={fg} />
      ) : (
        <>
          {!!icon && <Icon name={icon} size={16} color={fg} />}
          <Text style={{ color: fg, fontWeight: '600', fontSize: v3Type.bodyLarge }}>{label}</Text>
        </>
      )}
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
  value, onValueChange, disabled,
}: { value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean }) {
  const t = useV3Theme();
  return (
    <TouchableOpacity
      onPress={() => onValueChange(!value)}
      disabled={disabled}
      activeOpacity={0.8}
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
// card menu whose selected row is tinted Theme.primary+'26' (the exact popup highlight
// desktop uses).
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
                      backgroundColor: sel ? t.primary + '26' : 'transparent',
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
      {busy ? <ActivityIndicator size="small" color={t.primary} /> : <Icon name={icon} size={20} color={t.text} />}
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
