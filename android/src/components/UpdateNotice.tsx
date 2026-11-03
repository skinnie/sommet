import React from 'react';
import { StyleProp, Text, TouchableOpacity, View, ViewStyle } from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { t } from '../i18n';
import { APP_VERSION } from '../config/version';
import Icon from './ui/Icon';
import { Button, PulseDots } from './ui/primitives';
import {
  downloadAndInstall, dismissUpdate, openReleasePage, showUpdateNotice, updateAvailable,
  updatesSupported, useAppUpdate,
} from '../services/AppUpdate';

// "Sommet 0.2.58 is ready" (André, 2026-11-03, 🟣124): the newer version, the first lines of
// what's new, and Download and install. On Home it closes with ×, until the next version
// comes out; Settings → About shows it without the ×. Same look as StatusNote: a card with a
// thin coloured bar on the left. The desktop twin is qml/components/UpdateNotice.qml.
export default function UpdateNotice({
  closable = true, maxHighlights = 3, style,
}: {
  closable?: boolean; maxHighlights?: number; style?: StyleProp<ViewStyle>;
}) {
  const theme = useV3Theme();
  const s = useAppUpdate();
  const latest = s.latest;
  const show = updatesSupported() && (closable ? showUpdateNotice(s) : updateAvailable(s));
  if (!show || !latest) return null;

  const busy = s.phase === 'downloading' || s.phase === 'installing';
  const progressText = s.phase === 'downloading' ? t.updateDownloading(Math.round(s.progress * 100))
    : s.phase === 'installing' ? t.updateOpening : '';
  const problemText = s.problem === 'downloadFailed' ? t.updateDownloadFailed
    : s.problem === 'installFailed' ? t.updateInstallFailed
    : s.problem === 'mismatch' ? t.updateMismatch : '';
  const more = latest.highlights.length > maxHighlights;

  return (
    <View
      accessibilityRole="alert"
      accessibilityLabel={t.updateReady(latest.version)}
      style={[{
        backgroundColor: theme.card, borderColor: theme.border, borderWidth: 1,
        borderRadius: v3Radius.small, paddingVertical: v3Spacing.medium,
        paddingLeft: 18, paddingRight: closable ? 40 : v3Spacing.medium,
        flexDirection: 'row', gap: v3Spacing.small,
      }, style]}
    >
      <View style={{ position: 'absolute', left: 5, top: 8, bottom: 8, width: 3, borderRadius: 1.5, backgroundColor: theme.primary }} />
      <Icon name="download" size={20} color={theme.primary} />
      <View style={{ flex: 1, gap: 4 }}>
        <Text style={{ color: theme.text, fontSize: v3Type.body, fontWeight: '700' }}>{t.updateReady(latest.version)}</Text>
        <Text style={{ color: theme.mutedText, fontSize: v3Type.label }}>{t.updateYouHave(APP_VERSION)}</Text>
        {latest.highlights.length > 0 && (
          <Text style={{ color: theme.text, fontSize: v3Type.label, marginTop: 2 }}>{t.updateWhatsNew}</Text>
        )}
        {latest.highlights.slice(0, maxHighlights).map(h => (
          <Text key={h} style={{ color: theme.text, fontSize: v3Type.label }}>{`• ${h}`}</Text>
        ))}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: v3Spacing.small, marginTop: v3Spacing.small }}>
          <Button accent grow={false} busy={busy} label={t.updateInstall} onPress={() => { downloadAndInstall(); }} />
          <Button variant="outline" grow={false} label={more ? t.updateSeeAll : t.updateReleaseNotes} onPress={openReleasePage} />
          {!!progressText && (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <PulseDots color={theme.primary} size={5} gap={3} running />
              <Text style={{ color: theme.mutedText, fontSize: v3Type.label }}>{progressText}</Text>
            </View>
          )}
        </View>
        {s.needsAllow && s.phase !== 'downloading' && (
          <Text style={{ color: theme.mutedText, fontSize: v3Type.caption }}>{t.updateAllowHint}</Text>
        )}
        {!!problemText && (
          <Text style={{ color: theme.error, fontSize: v3Type.label }}>{problemText}</Text>
        )}
      </View>
      {closable && (
        <TouchableOpacity
          onPress={() => { dismissUpdate(); }}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel={t.updateClose}
          style={{ position: 'absolute', right: 8, top: 8, padding: 4, borderRadius: v3Radius.small }}
        >
          <Icon name="close" size={16} color={theme.mutedText} />
        </TouchableOpacity>
      )}
    </View>
  );
}
