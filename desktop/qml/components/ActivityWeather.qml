import QtQuick
import AmbitApp

// The weather a move was done in - one line under the activity map (André, 2026-09-27, "2 super
// nice"). Open-Meteo history for the hours the move spans, at its first GPS point, plus how much
// of the track went into / across / with the wind. ActivityWeatherService does the fetching and
// caching; this only asks and draws. Hidden for moves without GPS or while offline.
Row {
    id: root
    property var activity
    property var track: []
    property var weather: ({})
    readonly property string _key: activity ? (activity.startTime || "") : ""
    readonly property bool _has: weather && weather.tempMax !== undefined

    visible: _has
    spacing: Theme.spacingMedium

    function _ask() {
        weather = ({})
        if (!activity || !track || track.length === 0) return
        const w = ActivityWeatherService.request(_key, activity.startTime || "",
                                                 activity.durationSeconds || 0, track)
        if (w && w.tempMax !== undefined) weather = w
    }
    on_KeyChanged: _ask()
    onTrackChanged: _ask()

    Connections {
        target: ActivityWeatherService
        function onReady(key, w) { if (key === root._key) root.weather = w }
    }

    function _temp() {
        const lo = Math.round(weather.tempMin), hi = Math.round(weather.tempMax)
        return lo === hi ? qsTr("%1 °C").arg(hi) : qsTr("%1–%2 °C").arg(lo).arg(hi)
    }
    function _windShare() {
        if (weather.headShare === undefined) return ""
        const pct = function (x) { return Math.round(x * 100) }
        return qsTr("headwind %1% · crosswind %2% · tailwind %3%")
            .arg(pct(weather.headShare)).arg(pct(weather.crossShare)).arg(pct(weather.tailShare))
    }

    Icon {
        glyph: root._has ? WeatherViewModel.iconFor(root.weather.code) : Icons.weatherCloudy
        size: 20; color: Theme.primary
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: root._has ? WeatherViewModel.labelFor(root.weather.code) + " · " + root._temp()
                          + (root.weather.rainMm >= 0.2 ? qsTr(" · %1 mm rain").arg(root.weather.rainMm.toFixed(1)) : "")
                        : ""
        color: Theme.text; font.pixelSize: Theme.fontSizeLabel; font.bold: true
    }
    Icon {
        glyph: Icons.wind; size: 18; color: Theme.mutedText
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: root._has ? qsTr("%1 km/h %2 (gusts %3)").arg(Math.round(root.weather.windKmh))
                              .arg(root.weather.windCompass).arg(Math.round(root.weather.gustKmh))
                          + (root._windShare() ? " · " + root._windShare() : "")
                        : ""
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
    }
}
