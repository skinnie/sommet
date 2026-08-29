import React from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { Button } from '../components/ui/primitives';
import { useV3Theme } from '../theme/v3';

// Apps launcher (2026-08-29, André). One 'Apps' nav entry that opens the two Ambit3 App-Zone
// tools, mirroring the desktop's AppZonePage ("one card, two options"):
//   - Workout Builder  -> IntervalsScreen (a structured interval workout, installed as a guided
//     workout in the watch's WORKOUT menu).
//   - Browse Suunto Apps -> AppZoneScreen (import your SuuntoLink catalog + install pre-made apps
//     onto a sport mode's data screen).
// The separate 'Intervals' nav item was retired in favour of this. Workout Calendar stays its own
// item - it is planned workouts (the desktop's Training Program), not an app.
export default function AppsScreen({ navigation }: any) {
  const t = useV3Theme();
  const s = styles(t);
  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }} contentContainerStyle={{ padding: 16 }}>
      <Card>
        <View style={s.row}>
          <Icon name="chart" size={22} color={t.primary} />
          <Text style={s.title}>Workout Builder</Text>
        </View>
        <Text style={s.desc}>
          Build a structured interval workout and install it as a guided workout in the watch's
          WORKOUT menu (target band + step text). Works offline except for the final compile step.
        </Text>
        <Button label="Open Workout Builder" onPress={() => navigation.navigate('Intervals')} />
      </Card>

      <View style={{ height: 12 }} />

      <Card>
        <View style={s.row}>
          <Icon name="list" size={22} color={t.primary} />
          <Text style={s.title}>Browse Suunto Apps</Text>
        </View>
        <Text style={s.desc}>
          Import your SuuntoLink app catalog, then install a pre-made app onto a sport mode's data
          screen. (Sommet ships none of Suunto's catalog — you import your own index.json.)
        </Text>
        <Button label="Open App Catalog" variant="outline" onPress={() => navigation.navigate('AppZone')} />
      </Card>
    </ScrollView>
  );
}

const styles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 8 },
  title: { fontSize: 16, fontWeight: '700', color: t.text },
  desc: { fontSize: 13, lineHeight: 19, marginBottom: 14, color: t.mutedText },
});
