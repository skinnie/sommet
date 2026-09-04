pragma Singleton
import QtQuick
import AmbitApp

// WeatherService gives raw Open-Meteo numbers; this turns a WMO weather code into an icon
// glyph and a human label - real presentation logic, kept out of both the C++ Service (which
// shouldn't know about Icons.qml) and HomePage.qml (which shouldn't know WMO code ranges).
QtObject {
    function iconFor(code) {
        if (code === 0) return Icons.weatherSunny;
        if (code === 1 || code === 2) return Icons.weatherPartlyCloudy;
        if (code === 3) return Icons.weatherCloudy;
        if (code === 45 || code === 48) return Icons.weatherFoggy;
        if (code >= 51 && code <= 67) return Icons.weatherRainy;
        if (code >= 80 && code <= 82) return Icons.weatherRainy;
        if (code === 71 || code === 73 || code === 75 || code === 77) return Icons.weatherSnowy;
        if (code === 85 || code === 86) return Icons.weatherSnowy;
        if (code === 95 || code === 96 || code === 99) return Icons.weatherThunderstorm;
        return Icons.weatherPartlyCloudy;
    }

    function labelFor(code) {
        if (code === 0) return qsTr("Clear sky");
        if (code === 1) return qsTr("Mainly clear");
        if (code === 2) return qsTr("Partly cloudy");
        if (code === 3) return qsTr("Overcast");
        if (code === 45 || code === 48) return qsTr("Fog");
        if (code >= 51 && code <= 57) return qsTr("Drizzle");
        if (code >= 61 && code <= 67) return qsTr("Rain");
        if (code >= 80 && code <= 82) return qsTr("Rain showers");
        if (code === 71 || code === 73 || code === 75 || code === 77) return qsTr("Snow");
        if (code === 85 || code === 86) return qsTr("Snow showers");
        if (code === 95 || code === 96 || code === 99) return qsTr("Thunderstorm");
        return qsTr("Unknown");
    }

    function dayLabel(isoDate, index) {
        if (index === 0) return qsTr("Today");
        const d = new Date(isoDate);
        // Qt.locale() (no args) is the OS's default *regional* locale, which can be set to
        // e.g. Chinese even when Windows' display language is English - the app ships no
        // translations at all (main.cpp never loads a QTranslator), so every other string
        // here stays English regardless of OS locale. Pin this one to English too instead
        // of letting it go rogue and mix 周六/周日 into an otherwise-English forecast row
        // (real bug, reported by a Windows user with an Ambit2, 2026-09-04).
        return d.toLocaleDateString(Qt.locale("en"), "ddd");
    }

    readonly property string currentIcon: iconFor(WeatherService.currentWeatherCode)
    readonly property string currentLabel: labelFor(WeatherService.currentWeatherCode)
}
