import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import Icon from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { t as tr } from '../i18n';
import { useNavMenu } from './MenuContext';
import { styleChoices, maxFavourites, toggleFavourite } from './menuModel';

// Settings > Appearance > Menu: how the menu opens on this device, and which places the fan and the
// floating bar show (André, 2026-11-02: "toggle between fan out, pop up list and floating bar").
export function MenuSettings() {
  const th = useV3Theme();
  const m = useNavMenu();
  const [full, setFull] = useState(false);
  const picking = m.style === 'fan' || m.style === 'bar' ? m.style : null;
  const max = picking ? maxFavourites(picking) : 0;
  const picked = picking ? m.favourites[picking] : [];

  function tap(id: string) {
    if (!picking) return;
    const r = toggleFavourite(picked, id, max);
    setFull(r.full);
    if (!r.full) m.setFavourites({ ...m.favourites, [picking]: r.list });
  }

  return (
    <View style={s.block}>
      <Text style={[s.title, { color: th.text }]}>{tr.menuStyleTitle}</Text>
      <Text style={[s.desc, { color: th.mutedText }]}>{tr.menuStyleDesc}</Text>
      <View style={s.choices}>
        {styleChoices(m.isTablet).map(style => {
          const sel = m.style === style;
          return (
            <TouchableOpacity
              key={style}
              activeOpacity={0.75}
              onPress={() => { setFull(false); m.setStyle(style); }}
              style={[s.choice, { borderColor: sel ? th.primary : th.mutedText + '33', backgroundColor: sel ? th.primary : th.card }]}
            >
              <Text style={[s.choiceLabel, { color: sel ? th.card : th.text }]}>{tr.menuStyleName(style)}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
      <Text style={[s.desc, { color: th.mutedText, marginTop: 8 }]}>{tr.menuStyleHint(m.style)}</Text>

      {picking && (
        <View style={{ marginTop: 10 }}>
          <Text style={[s.sub, { color: th.text }]}>{tr.menuPlacesTitle(picking, max)}</Text>
          <Text style={[s.desc, { color: th.mutedText }]}>{tr.menuPlacesHint}</Text>
          <View style={s.chips}>
            {m.items.map(it => {
              const at = picked.indexOf(it.id);
              const sel = at >= 0;
              return (
                <TouchableOpacity
                  key={it.id}
                  activeOpacity={0.75}
                  onPress={() => tap(it.id)}
                  accessibilityState={{ selected: sel }}
                  style={[s.chip, { borderColor: sel ? th.primary : th.border, backgroundColor: sel ? th.primary : th.card }]}
                >
                  {sel && <Text style={[s.chipNum, { color: th.card }]}>{at + 1}</Text>}
                  <Icon name={it.icon} size={16} color={sel ? th.card : th.text} />
                  <Text style={[s.chipLabel, { color: sel ? th.card : th.text }]} numberOfLines={1}>{it.label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
          {full && <Text style={[s.desc, { color: th.warning, marginTop: 6 }]}>{tr.menuPlacesFull(max)}</Text>}
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  block: { marginTop: 18 },
  title: { fontSize: 13.5, fontWeight: '600', marginBottom: 2 },
  sub: { fontSize: 13.5, fontWeight: '600', marginBottom: 2 },
  desc: { fontSize: 13, lineHeight: 19 },
  choices: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 10 },
  choice: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: 10, paddingHorizontal: 10, borderRadius: 10, borderWidth: 1.4 },
  choiceLabel: { fontSize: 13, fontWeight: '600' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 7, paddingHorizontal: 10, borderRadius: 10, borderWidth: 1 },
  chipNum: { fontSize: 12, fontWeight: '700' },
  chipLabel: { fontSize: 13 },
});
