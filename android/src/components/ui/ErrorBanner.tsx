import React, { useEffect, useState } from 'react';
import { View, Text, StyleProp, ViewStyle, Platform } from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../../theme/v3';
import { Button } from './primitives';
import { appendLog, isPlainSentence, reportProblem, userMessage } from '../../services/LogService';

// One error banner (André, 2026-11-02, desktop picks #19) - the Android port of
// desktop/qml/components/ErrorBanner.qml. A screen passes the raw error as `detail`; the banner
// shows one plain sentence, writes the detail to the log file (LogService), and offers Try again
// (when the screen can retry), Details (the raw text, on demand) and Send logs.
//
//   <ErrorBanner detail={error} context="reading weigh-ins" showPlainDetail onRetry={refresh} />
//
// showPlainDetail: when the detail is already one plain sentence for the user, show it instead
// of the general sentence (same rule as desktop, LogService.isPlainSentence).
export function ErrorBanner({
  detail, context = '', onRetry, showPlainDetail = false, style,
}: {
  detail?: string | null;
  // What the app was doing, in the user's words; goes into the log and the report.
  context?: string;
  // Opt-in Try again: pass the same refresh that failed.
  onRetry?: () => void;
  showPlainDetail?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useV3Theme();
  const [showDetail, setShowDetail] = useState(false);
  const text = (detail ?? '').trim();

  useEffect(() => {
    setShowDetail(false);
    if (text) appendLog((context ? `${context}: ` : '') + text);
  }, [text, context]);

  if (!text) return null;
  const plain = showPlainDetail && isPlainSentence(text);

  return (
    <View
      accessibilityRole="alert"
      style={[{
        backgroundColor: t.card, borderColor: t.error, borderWidth: 1, borderRadius: v3Radius.card,
        padding: v3Spacing.medium, gap: v3Spacing.small, marginBottom: v3Spacing.medium,
      }, style]}
    >
      <Text style={{ color: t.text, fontSize: v3Type.body, lineHeight: 19 }}>
        {plain ? text : userMessage()}
      </Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small }}>
        {!!onRetry && <Button label="Try again" onPress={onRetry} grow={false} />}
        {!plain && (
          <Button label={showDetail ? 'Hide details' : 'Details'} onPress={() => setShowDetail(v => !v)} grow={false} />
        )}
        <Button label="Send logs" onPress={() => { reportProblem(context); }} grow={false} />
      </View>
      {showDetail && !plain && (
        <View style={{
          backgroundColor: t.cardNested, borderColor: t.border, borderWidth: 1,
          borderRadius: v3Radius.small, padding: v3Spacing.small,
        }}>
          <Text
            selectable
            style={{
              color: t.mutedText, fontSize: v3Type.caption,
              fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
            }}
          >
            {text}
          </Text>
        </View>
      )}
    </View>
  );
}
