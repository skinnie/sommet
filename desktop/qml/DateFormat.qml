pragma Singleton
import QtQuick

// Day-first dates, everywhere, regardless of the machine's locale.
//
// André, 2026-08-27: "why the heck on the app the date is MM/DD/YYYY, it should be
// DD/MM/YYYY everywhere". The cause was Qt.locale() - this machine runs LANG=en_US.UTF-8, so
// Locale.ShortFormat rendered 26 August as "8/26/26" and backup timestamps as "8/23/26 1:31
// AM". The app is used in France by a Portuguese speaker; en_US is an artefact of the shell
// environment, not a preference, and following it was the bug.
//
// So the pattern is explicit rather than locale-derived. Qt.formatDate/formatDateTime with an
// explicit pattern is locale-independent for the numeric fields, which is exactly what is
// wanted here: a date should not silently change shape because of an env var.
//
// Times are 24-hour for the same reason - "1:31 AM" is the same en_US artefact.
//
// Deliberately NOT applied to the spelled-out formats elsewhere ("MMMM yyyy" on the Calendar
// header, "ddd" on the weather strip): those are words, and translating words is a language
// question this change has no business answering.
QtObject {
    // 27/08/2026 - the everyday date, used by every activity card, row and summary line.
    function date(value) {
        if (!value) return "";
        const d = value instanceof Date ? value : new Date(value);
        if (isNaN(d.getTime())) return "";
        return Qt.formatDate(d, "dd/MM/yyyy");
    }

    // 27/08/2026 14:05 - when the time of day matters (backups, which can be minutes apart).
    function dateTime(value) {
        if (!value) return "";
        const d = value instanceof Date ? value : new Date(value);
        if (isNaN(d.getTime())) return "";
        return Qt.formatDateTime(d, "dd/MM/yyyy HH:mm");
    }
}
