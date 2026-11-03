import React, { useEffect, useState } from 'react';
import { Modal, View, Text, TextInput, TouchableOpacity, StyleSheet } from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { PulseDots, SegmentedControl, StatusChip, StatusNote } from './ui/primitives';
import {
  FoundScale, findScales, forgetScale, getBodyProfile, getScaleAddress, isScaleAvailable, scaleStatus,
  setBodyProfile, setScaleAddress, startListening, stopListening, BodyProfile,
} from '../services/ScaleService';

// Bluetooth scale: set-up and manage. The Android twin of desktop/qml/components/ScaleSetupDialog.qml
// (same steps, same words). The scale (Etekcity FIT 8S) only broadcasts while someone stands on it,
// so there is no pairing: 1 step on it, 2 pick what was heard, 3 check the display against the
// number shown, 4 height and sex - only when neither intervals.icu nor the watch has them.

type Step = 0 | 1 | 2 | 3 | 4;   // 0 = manage (already set up)

export function ScaleSetupModal({ visible, onClose, onChanged }: {
  visible: boolean; onClose: () => void; onChanged: () => void;
}) {
  const t = useV3Theme();
  const [step, setStep] = useState<Step>(1);
  const [address, setAddress] = useState<string | null>(null);
  const [found, setFound] = useState<FoundScale[]>([]);
  const [chosen, setChosen] = useState<FoundScale | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const [profile, setProfile] = useState<BodyProfile | null>(null);
  const [heightText, setHeightText] = useState('');
  const [sex, setSex] = useState<'M' | 'F'>('M');
  const status = scaleStatus();

  useEffect(() => {
    if (!visible) return;
    (async () => {
      const a = await getScaleAddress();
      setAddress(a); setFound([]); setChosen(null); setError(''); setSearching(false);
      setStep(a ? 0 : 1);
      const p = await getBodyProfile();
      setProfile(p); setHeightText(p ? String(p.heightCm) : ''); setSex(p?.sex ?? 'M');
    })();
  }, [visible]);

  async function search() {
    setSearching(true); setError('');
    try { setFound(await findScales(20)); }
    catch (e: any) { setError(String(e?.message ?? e)); setFound([]); }
    setSearching(false); setStep(2);
  }

  const btn = [styles.button, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }];
  const txt = { color: t.text, fontSize: v3Type.body, fontWeight: '600' as const };
  // Forget: the calm red of a delete (desktop picks #5, RoundedButton.danger).
  const dangerBtn = [styles.button, { backgroundColor: t.error + '1A', borderColor: t.error, borderRadius: v3Radius.small }];
  const dangerTxt = { color: t.error, fontSize: v3Type.body, fontWeight: '700' as const };
  const body = { color: t.text, fontSize: v3Type.body, marginBottom: v3Spacing.small };
  const muted = { color: t.mutedText, fontSize: v3Type.caption, marginBottom: v3Spacing.small };
  const h = parseFloat(heightText.replace(',', '.'));

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.scrim}>
        <View style={[styles.sheet, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.text, fontSize: v3Type.bodyLarge, fontWeight: '700', marginBottom: v3Spacing.small }}>
            Bluetooth scale
          </Text>

          {step === 0 && (<>
            {/* The state as a tag, like the Weight page (desktop picks #6). */}
            <StatusChip label={status.listening ? 'Listening' : 'Not listening'}
              tone={status.listening ? 'success' : 'neutral'} style={{ marginBottom: v3Spacing.small }} />
            <Text style={muted}>Scale {address}</Text>
            <StatusNote text={status.error} tone="error" style={{ marginTop: 0, marginBottom: v3Spacing.small }} />
            <TouchableOpacity style={btn} onPress={async () => { status.listening ? stopListening() : await startListening(); onChanged(); }}>
              <Text style={txt}>{status.listening ? 'Stop listening' : 'Start listening'}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={dangerBtn} onPress={async () => { await forgetScale(); setAddress(null); setStep(1); onChanged(); }}>
              <Text style={dangerTxt}>Forget scale</Text>
            </TouchableOpacity>
            <TouchableOpacity style={btn} onPress={() => setStep(4)}><Text style={txt}>Body profile…</Text></TouchableOpacity>
            <Text style={muted}>Android listens while the app is open.</Text>
          </>)}

          {step === 1 && (<>
            <Text style={muted}>Step 1 of 4</Text>
            <Text style={body}>
              Turn Bluetooth on, press Search, then step on the scale barefoot and stay until the display locks.
              The scale only talks while someone is on it.
            </Text>
            <Text style={muted}>Supported: Etekcity FIT 8S. No pairing, no app and no account are needed.</Text>
            {!isScaleAvailable() && (
              <StatusNote text="This build has no Bluetooth scale support." tone="warning" closable={false}
                style={{ marginTop: 0, marginBottom: v3Spacing.small }} />
            )}
            {/* Busy (desktop picks #5): the name stays and dots pulse while it searches. */}
            <TouchableOpacity style={[btn, { flexDirection: 'row', justifyContent: 'center', gap: 8 }]}
              disabled={searching || !isScaleAvailable()} onPress={search}
              accessibilityRole="button" accessibilityState={{ busy: searching }}>
              <Text style={txt}>Search</Text>
              {searching && <PulseDots color={t.text} size={5} />}
            </TouchableOpacity>
            {searching && <Text style={muted}>Searching… step on the scale</Text>}
          </>)}

          {step === 2 && (<>
            <Text style={muted}>Step 2 of 4</Text>
            <StatusNote text={error} tone="error" style={{ marginTop: 0, marginBottom: v3Spacing.small }} />
            {error.length === 0 && found.length === 0 && (
              <Text style={body}>No scale heard. Check that Bluetooth is on and that you stood on the scale while searching, then try again.</Text>
            )}
            {found.map(f => (
              <TouchableOpacity key={f.address} style={btn} onPress={() => { setChosen(f); setStep(3); }}>
                <Text style={txt}>Etekcity scale {f.address} · {f.weightKg.toFixed(2)} kg · signal {f.rssi} dBm</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity style={btn} onPress={() => setStep(1)}><Text style={txt}>Search again</Text></TouchableOpacity>
          </>)}

          {step === 3 && chosen && (<>
            <Text style={muted}>Step 3 of 4</Text>
            <Text style={body}>
              The scale sent {chosen.weightKg.toFixed(2)} kg. The display rounds to 0.05 kg, so it may differ by up to 0.05. Does it match?
            </Text>
            <TouchableOpacity style={btn} onPress={async () => {
              await setScaleAddress(chosen.address); setAddress(chosen.address);
              await startListening(); onChanged();
              // Height and sex already known (intervals.icu or the watch): nothing to ask.
              if (await getBodyProfile()) onClose(); else setStep(4);
            }}><Text style={txt}>Yes, it matches</Text></TouchableOpacity>
            <TouchableOpacity style={btn} onPress={() => setStep(2)}><Text style={txt}>No</Text></TouchableOpacity>
          </>)}

          {step === 4 && (<>
            <Text style={muted}>Step 4 of 4</Text>
            <Text style={body}>
              Body fat and water are estimated from height and sex. They come from intervals.icu or your watch when either has them; type them only to override.
            </Text>
            {profile && (
              <Text style={muted}>
                Now: {profile.heightCm} cm, {profile.sex === 'M' ? 'male' : 'female'} ({profile.source === 'manual' ? 'typed by hand' : profile.source === 'watch' ? 'from your watch' : 'from intervals.icu'})
              </Text>
            )}
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: v3Spacing.small }}>
              <TextInput value={heightText} onChangeText={setHeightText} placeholder="height cm" keyboardType="decimal-pad"
                placeholderTextColor={t.mutedText}
                style={[styles.input, { color: t.text, backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]} />
              {/* Two choices = one switch (desktop picks #14). */}
              <SegmentedControl options={['Male', 'Female']} index={sex === 'F' ? 1 : 0}
                onChange={i => setSex(i === 1 ? 'F' : 'M')} />
            </View>
            <View style={{ flexDirection: 'row' }}>
              <TouchableOpacity style={[btn, { marginRight: v3Spacing.small, opacity: h >= 100 && h <= 230 ? 1 : 0.4 }]} disabled={!(h >= 100 && h <= 230)}
                onPress={async () => { await setBodyProfile(h, sex); onChanged(); onClose(); }}>
                <Text style={txt}>Save</Text>
              </TouchableOpacity>
              <TouchableOpacity style={btn} onPress={onClose}><Text style={txt}>Skip</Text></TouchableOpacity>
            </View>
          </>)}

          <TouchableOpacity style={[btn, { marginTop: v3Spacing.small }]} onPress={onClose}><Text style={txt}>Close</Text></TouchableOpacity>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: { flex: 1, backgroundColor: 'rgba(0,0,0,0.55)', justifyContent: 'center', alignItems: 'center', padding: v3Spacing.medium },
  sheet: { width: '100%', maxWidth: 420, borderWidth: 1, padding: v3Spacing.medium },
  button: { borderWidth: 1, paddingVertical: 10, paddingHorizontal: v3Spacing.medium, alignItems: 'center', marginBottom: v3Spacing.small },
  input: { borderWidth: 1, paddingHorizontal: v3Spacing.small, paddingVertical: 8, fontSize: v3Type.body, width: 120, marginRight: v3Spacing.small },
});
