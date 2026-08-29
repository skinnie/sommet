import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card } from './ui/Card';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { useWeather, weatherEmoji } from '../services/WeatherService';
import { t, dateLocale } from '../i18n';

// Same real WMO code ranges as WeatherService.ts's own weatherEmoji() / desktop's
// WeatherViewModel.qml labelFor() - kept here (not in WeatherService.ts) so it can use this
// screen's own i18n strings directly instead of threading the whole `t` object through a
// generic-record parameter.
function weatherLabel(code: number): string {
  if (code === 0) return t.weatherClear;
  if (code === 1) return t.weatherMainlyClear;
  if (code === 2) return t.weatherPartlyCloudy;
  if (code === 3) return t.weatherOvercast;
  if (code === 45 || code === 48) return t.weatherFog;
  if (code >= 51 && code <= 57) return t.weatherDrizzle;
  if (code >= 61 && code <= 67) return t.weatherRain;
  if (code >= 80 && code <= 82) return t.weatherRainShowers;
  if (code === 71 || code === 73 || code === 75 || code === 77) return t.weatherSnow;
  if (code === 85 || code === 86) return t.weatherSnowShowers;
  if (code === 95 || code === 96 || code === 99) return t.weatherThunderstorm;
  return t.weatherUnknown;
}

// Real, 2026-08-09 (v3.0 UI port, "replicate the desktop version feature wise") - ports
// desktop/qml/components/WeatherCard.qml's exact layout: hidden until hasFetchedOnce, then
// either a friendly offline message or the real current-conditions row (icon/place/temp/
// label, wind + high/low) plus a 3-day forecast row. Same real "don't show an error, just a
// plain offline message" rule as the QML original.
export function WeatherCard() {
  const theme = useV3Theme();
  const weather = useWeather();

  if (!weather.hasFetchedOnce) return null;

  return (
    <Card style={styles.card}>
      {!weather.available || !weather.data ? (
        <View style={styles.offlineRow}>
          <Text style={styles.offlineEmoji}>☁️</Text>
          <Text style={[styles.offlineText, { color: theme.mutedText }]}>{t.weatherOffline}</Text>
        </View>
      ) : (
        // Real bug, found live on a real wide tablet screen (2026-08-09, "check the home
        // page the orientation of icons of weather can be improved") - sideInfo used
        // marginLeft:'auto' to push wind/high-low to the card's own far edge, which reads
        // fine on a phone-width card but left a huge, unbalanced gap on a wide tablet-rail
        // layout card. Capped to a real max content width and centered instead, so the
        // whole block stays visually grouped regardless of how wide the card itself gets.
        //
        // Follow-up, same day ("alignment of icons could be better, top two in the same
        // line but align in the middle of the 3 down") - contentWrap is a Column, whose
        // default cross-axis behavior (alignItems:'stretch') was forcing BOTH mainRow and
        // forecastRow to the full 420px contentWrap width even though the two rows have
        // very different natural content widths (mainRow: icon+temp+wind, tight; forecastRow:
        // 3 columns spread via space-between, wide) - so the two rows had different visual
        // centers and never lined up. mainRow/forecastRow now both use alignSelf:'center' to
        // size to their own content instead of stretching, so their centers coincide.
        <View style={styles.contentWrap}>
          <View style={styles.mainRow}>
            <Text style={styles.mainEmoji} textBreakStrategy="simple">{weatherEmoji(weather.data.currentWeatherCode)}</Text>
            <View style={styles.mainInfo}>
              {!!weather.placeName && (
                <Text style={[styles.placeName, { color: theme.mutedText }]}>{weather.placeName}</Text>
              )}
              <Text style={[styles.temp, { color: theme.text }]}>
                {Math.round(weather.data.currentTemperature)}°
              </Text>
              <Text style={[styles.condLabel, { color: theme.mutedText }]}>
                {weatherLabel(weather.data.currentWeatherCode)}
              </Text>
            </View>
            <View style={styles.sideInfo}>
              <Text style={[styles.sideLine, { color: theme.mutedText }]}>
                {t.weatherWind(Math.round(weather.data.windSpeed))}
              </Text>
              <Text style={[styles.sideLine, { color: theme.mutedText }]}>
                {t.weatherHighLow(Math.round(weather.data.todayHigh), Math.round(weather.data.todayLow))}
              </Text>
            </View>
          </View>

          <View style={styles.forecastRow}>
            {weather.data.forecast.map((day, i) => (
              <View key={day.date} style={styles.forecastCol}>
                <Text style={[styles.forecastDay, { color: theme.mutedText }]}>
                  {i === 0 ? t.weatherToday : new Date(day.date).toLocaleDateString(dateLocale, { weekday: 'short' })}
                </Text>
                <Text style={styles.forecastEmoji}>{weatherEmoji(day.code)}</Text>
                <Text style={[styles.forecastTemp, { color: theme.text }]}>
                  {t.weatherHighLowShort(Math.round(day.high), Math.round(day.low))}
                </Text>
              </View>
            ))}
          </View>
        </View>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: { width: '100%', alignItems: 'center' },
  offlineRow: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.medium },
  offlineEmoji: { fontSize: 28 },
  offlineText: { flex: 1, fontSize: v3Type.bodyLarge },
  // Real cap, not a full-bleed stretch - on a wide tablet-rail card this content block
  // would otherwise spread thin across the whole width. 420 keeps the main row's own
  // three sections (icon/temp, wind+high-low) close enough to read as one group.
  contentWrap: { width: '100%', maxWidth: 420, gap: v3Spacing.medium, alignItems: 'center' },
  // alignSelf:'center' - see the header comment above: without this, both rows stretched
  // to contentWrap's full width by default (Column's alignItems:'stretch'), so mainRow's
  // tight icon+temp+wind cluster and forecastRow's spread-out 3 columns had different
  // visual centers. Sizing each row to its own content instead makes both centers coincide.
  mainRow: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.medium, alignSelf: 'center' },
  // includeFontPadding/textAlignVertical: emoji glyphs render with extra vertical padding
  // baked into Android's own font metrics that plain text doesn't have, throwing off
  // alignItems:'center' against the numbers/labels beside them - real, found live on device.
  mainEmoji: { fontSize: 40, includeFontPadding: false, textAlignVertical: 'center' },
  mainInfo: { gap: 2 },
  placeName: { fontSize: v3Type.label },
  temp: { fontSize: v3Type.display, fontWeight: '800' },
  condLabel: { fontSize: v3Type.bodyLarge },
  sideInfo: { marginLeft: v3Spacing.large, gap: 2, alignItems: 'flex-end' },
  sideLine: { fontSize: v3Type.label },
  forecastRow: { flexDirection: 'row', gap: v3Spacing.large, alignSelf: 'center' },
  forecastCol: { alignItems: 'center', gap: 4 },
  forecastDay: { fontSize: v3Type.label },
  forecastEmoji: { fontSize: 22, includeFontPadding: false, textAlignVertical: 'center' },
  forecastTemp: { fontSize: v3Type.label, fontWeight: '600' },
});
