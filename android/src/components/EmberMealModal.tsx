import React, { useEffect, useRef, useState } from 'react';
import {
  Modal, Pressable, View, Text, TextInput, ScrollView, ActivityIndicator, StyleSheet,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import {
  Food, portion, searchFoods, recentFoods, rememberFood, isBarcode,
} from '../services/EmberFood';

// Ember "log a meal" sheet - twin of desktop/qml/components/EmberMealDialog.qml (issue #20).
// Search Open Food Facts + USDA by name or barcode digits (recent picks show while the box is
// empty, offline too), pick a food, set the grams, log. No match? "Just kcal" logs a number.

export interface MealLog { name: string; kcal: number; protein: number; carbs: number; fat: number }

export function EmberMealModal({ visible, fasting, onClose, onLog }: {
  visible: boolean;
  fasting: boolean;
  onClose: () => void;
  onLog: (meal: MealLog) => void;
}) {
  const t = useV3Theme();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Food[]>([]);
  const [recent, setRecent] = useState<Food[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<Food | null>(null);
  const [grams, setGrams] = useState('100');
  const [manualKcal, setManualKcal] = useState('');
  const seq = useRef(0);

  useEffect(() => {
    if (!visible) return;
    setQuery(''); setResults([]); setPicked(null); setManualKcal('');
    recentFoods().then(setRecent);
  }, [visible]);

  // Debounced search; a newer query wins over a slower older answer.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults([]); setSearching(false); return; }
    const my = ++seq.current;
    setSearching(true);
    const id = setTimeout(async () => {
      const r = await searchFoods(q);
      if (my === seq.current) { setResults(r); setSearching(false); }
    }, isBarcode(q) ? 0 : 450);
    return () => clearTimeout(id);
  }, [query]);

  const pick = (f: Food) => { setPicked(f); setGrams(String(Math.round(f.servingG ?? 100))); };
  const g = Number(grams.replace(',', '.'));
  const p = picked && g > 0 ? portion(picked, g) : null;

  const logPicked = () => {
    if (!picked || !p) return;
    rememberFood(picked);
    onLog({ name: `${picked.name} (${Math.round(g)} g)`, ...p });
    onClose();
  };
  const logManual = () => {
    const k = Math.round(Number(manualKcal));
    if (!(k > 0)) return;
    onLog({ name: query.trim() || 'Meal', kcal: k, protein: 0, carbs: 0, fat: 0 });
    onClose();
  };

  const list = query.trim().length >= 2 ? results : recent;
  const card = { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card };
  const input = [styles.input, { color: t.text, borderColor: t.border, backgroundColor: t.cardNested, borderRadius: v3Radius.small }];

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={[styles.sheet, card]} onPress={() => {}}>
          <Text style={{ color: t.text, fontSize: v3Type.label, fontWeight: '700', marginBottom: v3Spacing.small }}>
            {picked ? picked.name : 'Log a meal'}
          </Text>

          {!picked && (
            <>
              <TextInput style={input} value={query} onChangeText={setQuery} autoFocus
                placeholder="Search a food or type a barcode" placeholderTextColor={t.mutedText} />
              <Text style={{ color: t.mutedText, fontSize: v3Type.tiny, marginTop: 4 }}>
                {query.trim().length >= 2 ? 'Open Food Facts · USDA FoodData Central' : recent.length ? 'Recent' : ' '}
              </Text>
              <ScrollView style={{ maxHeight: 320 }} keyboardShouldPersistTaps="handled">
                {searching && <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.medium }} />}
                {!searching && list.map(f => (
                  <Pressable key={`${f.source}:${f.id}:${f.name}`} style={[styles.row, { borderColor: t.border }]} onPress={() => pick(f)}>
                    <View style={{ flex: 1, paddingRight: v3Spacing.small }}>
                      <Text style={{ color: t.text, fontSize: v3Type.body }} numberOfLines={1}>{f.name}</Text>
                      {!!f.brand && <Text style={{ color: t.mutedText, fontSize: v3Type.tiny }} numberOfLines={1}>{f.brand}</Text>}
                    </View>
                    <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{Math.round(f.kcal100)} kcal/100 g</Text>
                  </Pressable>
                ))}
                {!searching && query.trim().length >= 2 && results.length === 0 && (
                  <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginVertical: v3Spacing.small }}>
                    Nothing found (or offline).
                  </Text>
                )}
              </ScrollView>
              <View style={[styles.manual, { borderColor: t.border }]}>
                <TextInput style={[input, { flex: 1 }]} value={manualKcal} onChangeText={setManualKcal}
                  keyboardType="number-pad" placeholder="Just kcal" placeholderTextColor={t.mutedText} />
                <Pressable onPress={logManual} style={[styles.btn, { borderColor: t.border }]}>
                  <Text style={{ color: t.primary, fontWeight: '700' }}>Log</Text>
                </Pressable>
              </View>
            </>
          )}

          {picked && (
            <>
              <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>
                {picked.brand ? `${picked.brand} · ` : ''}{Math.round(picked.kcal100)} kcal per 100 g
              </Text>
              <View style={styles.manual}>
                <TextInput style={[input, { width: 90 }]} value={grams} onChangeText={setGrams} keyboardType="decimal-pad" selectTextOnFocus />
                <Text style={{ color: t.text, fontSize: v3Type.body, marginLeft: v3Spacing.small }}>g</Text>
              </View>
              {p && (
                <Text style={{ color: t.text, fontSize: v3Type.bodyLarge, fontWeight: '700', marginTop: v3Spacing.small }}>
                  {p.kcal} kcal
                  <Text style={{ color: t.mutedText, fontSize: v3Type.caption, fontWeight: '400' }}>
                    {`  ·  P ${p.protein} g · C ${p.carbs} g · F ${p.fat} g`}
                  </Text>
                </Text>
              )}
              {fasting && (
                <Text style={{ color: t.warning, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
                  Logging this ends your fast.
                </Text>
              )}
              <View style={[styles.manual, { justifyContent: 'flex-end' }]}>
                <Pressable onPress={() => setPicked(null)} style={[styles.btn, { borderColor: t.border }]}>
                  <Text style={{ color: t.mutedText }}>Back</Text>
                </Pressable>
                <Pressable onPress={logPicked} style={[styles.btn, { borderColor: t.border, marginLeft: v3Spacing.small }]}>
                  <Text style={{ color: t.primary, fontWeight: '700' }}>Log meal</Text>
                </Pressable>
              </View>
            </>
          )}
          <Text style={{ color: t.mutedText, fontSize: v3Type.tiny, marginTop: v3Spacing.small }}>
            Food data © Open Food Facts contributors (ODbL) and USDA FoodData Central.
          </Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'center', padding: v3Spacing.large },
  sheet: { borderWidth: 1, padding: v3Spacing.medium },
  input: { borderWidth: 1, paddingHorizontal: 10, paddingVertical: 8, fontSize: v3Type.body },
  row: { paddingVertical: 10, borderTopWidth: 1, flexDirection: 'row', alignItems: 'center' },
  manual: { flexDirection: 'row', alignItems: 'center', marginTop: v3Spacing.small },
  btn: { borderWidth: 1, borderRadius: 10, paddingVertical: 8, paddingHorizontal: 14 },
});
