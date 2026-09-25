// A themed action menu - the phone twin of the desktop ThemedMenu opened by right-click. RN has
// no native context menu, so it's a centred rounded-card Modal (same look as MetricColumnMenu),
// opened by a long press. Items with `visible: false` are dropped, like the desktop's collapsing
// ThemedMenuItem.

import React from 'react';
import { Modal, Pressable, Text, StyleSheet } from 'react-native';
import { useV3Theme } from '../../theme/v3';

export interface ActionMenuItem { label: string; onPress: () => void; visible?: boolean; tone?: 'default' | 'alert'; disabled?: boolean }

export function ActionMenu({ visible, title, items, onClose }: {
  visible: boolean; title?: string; items: ActionMenuItem[]; onClose: () => void;
}) {
  const th = useV3Theme();
  const s = styles(th);
  const shown = items.filter(i => i.visible !== false);
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={s.backdrop} onPress={onClose}>
        <Pressable style={s.card} onPress={() => {}}>
          {!!title && <Text style={s.heading}>{title}</Text>}
          {shown.map((it, i) => (
            <Pressable key={i} disabled={it.disabled} style={({ pressed }) => [s.row, pressed && { backgroundColor: th.primary + '26' }]}
              onPress={() => { onClose(); it.onPress(); }}>
              <Text style={[s.rowText, it.tone === 'alert' && { color: th.error }, it.disabled && { color: th.mutedText }]}>{it.label}</Text>
            </Pressable>
          ))}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = (th: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: '#00000066', justifyContent: 'center', padding: 28 },
  card: { backgroundColor: th.card, borderColor: th.border, borderWidth: 1, borderRadius: 16, paddingVertical: 8, overflow: 'hidden' },
  heading: { fontSize: 13, color: th.mutedText, paddingHorizontal: 16, paddingVertical: 8 },
  row: { paddingVertical: 12, paddingHorizontal: 16, borderRadius: 8, marginHorizontal: 4 },
  rowText: { fontSize: 15, color: th.text },
});
