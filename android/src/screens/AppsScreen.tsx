import React from 'react';
import { View, Text, ScrollView, StyleSheet } from 'react-native';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { Button } from '../components/ui/primitives';
import { useV3Theme } from '../theme/v3';

// Apps launcher (2026-08-29, André). One 'Apps' nav entry that opens the two App-Zone
// tools, mirroring the desktop's AppZonePage ("one card, two options"):
//   - Workout Builder  -> IntervalsScreen (an interval Suunto App for a data screen; the guided
//     WORKOUT-menu workout is the Training Program's - the card links there too).
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
        {/* Said what it does (2026-10-22 audit): this builder makes an interval Suunto App for a
            sport mode's data screen, compiled by the user on the Ambit Apps site (Suunto forum) (no automated
            compile on Android - see IntervalsService.ts). The native guided workout - WORKOUT
            menu, target band + step text, like the desktop's Workout Builder - is the Training
            Program's, so the card points there too. */}
        <Text style={s.desc}>
          Native workouts (planned moves, and a link to the Training Program for guided ones), or a
          workout as a Suunto App: compiled on the Ambit Apps site from the Suunto forum, imported
          here and installed on a sport mode's data screen.
        </Text>
        <Button label="Open Workout Builder" onPress={() => navigation.navigate('Intervals')} />
        <Text style={[s.desc, { marginTop: 14 }]}>
          For a guided workout in the watch's WORKOUT menu (target band + step text, like the
          desktop's Workout Builder), plan it on a day in the Training Program.
        </Text>
        <Button label="Open Training Program" variant="outline"
          onPress={() => navigation.navigate('WorkoutCalendar', { watch: true })} />
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
