import React from 'react';
import { View, StyleSheet, StyleProp, ViewStyle } from 'react-native';
import { useV3Theme, v3Radius, v3Spacing } from '../../theme/v3';

// Real, 2026-08-09 (v3.0 UI port) - the RN equivalent of desktop/qml/components/Card.qml:
// "the base surface every content card in the app builds on... one implementation, so a
// future design tweak changes every card at once."
// 2026-08-25 (André: "no shadows, all app, desktop android, same for previous rules") -
// Card.qml itself dropped its shadow in favour of a hairline border; mutualised here the same
// way the palette/rounded-corners rules already are.
export function Card({
  children,
  padding = v3Spacing.medium,
  style,
}: {
  children: React.ReactNode;
  padding?: number;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: t.card, borderRadius: v3Radius.card, borderColor: t.border, padding },
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
  },
});
