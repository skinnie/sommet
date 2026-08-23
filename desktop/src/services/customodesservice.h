#pragma once

#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QStringList>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>

// Ambit3 CustomModes (sport modes) - real, hardware-confirmed 2026-08-08. See
// docs/reference/ (custom-modes notes) for the full story: renaming a mode, changing its own settings
// (Autolap/HR limits/pod search), and changing which data a display row shows are all
// real, live-verified capabilities as of this session, not theoretical. Wraps backend/
// server.py's /api/customodes (GET) and its three write endpoints
// (rename/field/display-field), which in turn wrap tools/custom_modes.py and this
// session's three new write tools - the curated field semantics (SETTING_FIELDS,
// FIELD_TYPES) live there, not duplicated here.
class CustomModesService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool ok READ ok NOTIFY modesChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // Each entry: {name, activityId, customModeId, useHw, autolap, hrHigh, hrLow,
    // hrLimitsUse, autoStart, autoPause, autoScrolling, backlightMode, displayMode,
    // quickNavigation, displays: [{index, template, fields: [{indexName, type, typeName}]}]}
    Q_PROPERTY(QVariantList modes READ modes NOTIFY modesChanged)
    // Multisport modes - real, 2026-08-11 (André, item 22): "triathlon/multisport mode is
    // missing from our menu, and it is on the watch". It was never missing data: a multisport
    // mode lives in the SPORT_MODE slots, not the exercise modes this list carries, and has
    // no displays of its own - only an ordered list of legs pointing at other modes.
    Q_PROPERTY(QVariantList multisportModes READ multisportModes NOTIFY modesChanged)
    // How many of the watch's sport-mode slots are actually spent, counted the way
    // SuuntoLink's own countSportModes() counts them (2026-08-12): one per menu entry,
    // multisport combos included. NOT modes().length - the factory-fitted "Transition"
    // has a mode but no menu entry and costs nothing, while a combo has a menu entry but
    // no mode of its own and costs a slot. See docs/reference/ (custom-modes notes)'s 2026-08-12 section.
    Q_PROPERTY(int sportModesUsed READ sportModesUsed NOTIFY modesChanged)
    // The "Select activity" catalogue: every activity a mode can be, each flagged with
    // whether it is one of the three that can hold several legs. Fetched once; static.
    Q_PROPERTY(QVariantList activities READ activities NOTIFY activitiesChanged)
    Q_PROPERTY(int minMultisportLegs READ minMultisportLegs NOTIFY activitiesChanged)
    Q_PROPERTY(int maxMultisportLegs READ maxMultisportLegs NOTIFY activitiesChanged)
    // Each entry: {value, name} - the real FIELD_TYPES catalog, fetched once and cached;
    // a UI builds its own "which data" picker from this.
    Q_PROPERTY(QVariantList fieldTypes READ fieldTypes NOTIFY fieldTypesChanged)
    Q_PROPERTY(QVariantList rowMenu READ rowMenu NOTIFY rowMenuChanged)
    Q_PROPERTY(bool rowMenuMultiValue READ rowMenuMultiValue NOTIFY rowMenuChanged)
    Q_PROPERTY(int rowMenuMaxValues READ rowMenuMaxValues NOTIFY rowMenuChanged)
    // Name of the mode a write is currently in flight for, or empty.
    // This watch's own limits and features, from the generated per-device table. Defaults
    // are the Ambit3 family's so a UI has sane numbers before the fetch lands.
    Q_PROPERTY(QVariantMap capabilities READ capabilities NOTIFY capabilitiesChanged)
    Q_PROPERTY(QString writingMode READ writingMode NOTIFY writingModeChanged)

public:
    explicit CustomModesService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool ok() const { return m_ok; }
    QString lastError() const { return m_lastError; }
    QVariantList modes() const { return m_modes; }
    QVariantList multisportModes() const { return m_multisportModes; }
    int sportModesUsed() const { return m_sportModesUsed; }
    QVariantList activities() const { return m_activities; }
    int minMultisportLegs() const { return m_minMultisportLegs; }
    int maxMultisportLegs() const { return m_maxMultisportLegs; }
    QVariantList fieldTypes() const { return m_fieldTypes; }
    QVariantList rowMenu() const { return m_rowMenu; }
    bool rowMenuMultiValue() const { return m_rowMenuMultiValue; }
    int rowMenuMaxValues() const { return m_rowMenuMaxValues; }
    QVariantMap capabilities() const { return m_capabilities; }
    QString writingMode() const { return m_writingMode; }

    // GET /api/customodes - real, read-only (a single ~12KB flash read), safe any time.
    Q_INVOKABLE void refresh();

    // GET /api/customodes/field-types - no watch touched, fetch once and cache.
    Q_INVOKABLE void refreshFieldTypes();
    // What may go on one particular row, as SuuntoLink itself would offer it: grouped into
    // its categories, in its order, with its labels, filtered by the mode's sport and the
    // display's type. Unlike fieldTypes() - which is the whole 95-entry catalogue and is
    // wrong to show a user directly, since most of it is not offerable on a given row - this
    // is per-row and answers "what can this row become". Results land in rowMenu.
    Q_INVOKABLE void refreshRowMenu(int activityId, int template_, const QString &row);
    // GET /api/customodes/capabilities?variant=<codename>. Call with DeviceService.model.
    Q_INVOKABLE void refreshCapabilities(const QString &variant);

    // POST /api/customodes/rename, confirm:true - real write, applied immediately (same
    // "explicit UI action is the confirmation" rule as SettingsWriteService). Refreshes
    // modes() afterward either way, so the UI always reflects the watch's own real state.
    Q_INVOKABLE void renameMode(const QString &fromName, const QString &toName);

    // POST /api/customodes/field, confirm:true. `fields` maps SETTING_FIELDS names
    // (Autolap, HrHigh, HrLow, HrLimitsUse, UseHw, ...) to their new integer values.
    Q_INVOKABLE void writeField(const QString &mode, const QVariantMap &fields);

    // POST /api/customodes/interval-timer, confirm:true. Sets a mode's on-watch Interval
    // Timer. `type` is "time" (high/low in seconds) or "distance" (high/low in meters); when
    // disabling, type/high/low are ignored. Same busy/refresh handling as writeField.
    Q_INVOKABLE void setIntervalTimer(const QString &mode, bool enabled, const QString &type,
                                      int high, int low, int repetitions);

    // POST /api/customodes/display-field, confirm:true. `newType` is a FIELD_TYPES name
    // (e.g. "FT_HEART_RATE_CURR") - real, live-confirmed finding: `type`, not `index`, is
    // what actually selects the rendered content for the common case (see this class's
    // own header comment). `index` is left unchanged by this call on purpose - a UI
    // shouldn't need to touch it given what's now known.
    // Real, 2026-08-10 (items 6/7/10): structural display edits - add/remove a display,
    // change its type, set a row's values - applied as ONE region write. `edits` is the
    // list the UI has staged; the watch has no per-field command for sport modes, so every
    // save rewrites the whole ~7.5 KB region and writing per click would cost a full write
    // per click. Staging and saving once is also what SuuntoLink does. The tool behind this
    // refuses any write unless it can first reproduce the watch's current region byte-for-
    // byte (tools/custom_modes_edit.py).
    Q_INVOKABLE void applyDisplayEdits(const QString &mode, const QVariantList &edits);
    Q_INVOKABLE void writeDisplayField(const QString &mode, int display, int field,
                                        const QString &newType);

    // GET /api/customodes/activities - the "Select activity" catalogue and the multisport
    // leg bounds. Static-file lookup on the backend, no watch touched; fetch once.
    Q_INVOKABLE void refreshActivities();

    // --- the mode LIST itself, real 2026-08-12 (BUGS_ANDRE items 20/21/23) --------------
    // Creating a sport mode is three region writes and deleting is one, both behind
    // tools/sport_mode_manage.py, whose --selftest reproduces all 16 real SuuntoLink
    // transitions in assets/pcap/removeandaddsportsmodeandmultisport byte-exact. Every
    // limit is enforced there rather than here, so the two apps cannot drift apart: 10
    // modes with a combo spending one of them, 2 combos, 2-6 legs, and a mode a combo
    // still uses cannot be deleted. A refusal comes back as manageResult(false, why) with
    // the tool's own wording - it is a real answer to show the user, not an error.
    // `variant` is the connected device's own codename (DeviceService.model) - a freshly
    // created mode's built-in display set is not the same fixture on every watch family
    // (Traverse/Traverse Alpha's real one is a different length and mostly different Types
    // from Ambit3's - see sport_mode_manage.py's TRAVERSE_NEW_MODE_DISPLAYS), so this has to
    // reach the tool or a real Traverse gets Ambit3's wrong list.
    Q_INVOKABLE void createMode(const QString &name, int activityId, const QString &variant);
    Q_INVOKABLE void deleteMode(const QString &name);
    // `legs` is the ORDER the watch steps through, by mode name; repeats are allowed and
    // are how a triathlon gets two transitions. `rename` is only read when editing.
    Q_INVOKABLE void saveMultisport(const QString &action, const QString &name,
                                     int activityId, const QStringList &legs,
                                     const QString &rename = QString());
    Q_INVOKABLE void deleteMultisport(const QString &name);

signals:
    void loadingChanged();
    void modesChanged();
    void fieldTypesChanged();
    void rowMenuChanged();
    void capabilitiesChanged();
    void activitiesChanged();
    void lastErrorChanged();
    void writingModeChanged();
    void displayEditsApplied(bool ok);
    // ok=false carries the tool's own refusal text ("10/10 sport modes already used...",
    // "'Transition' is a leg of Ironman..."), which the UI shows verbatim.
    void manageResult(bool ok, const QString &message);

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    bool m_ok = false;
    QString m_lastError;
    QVariantList m_modes;
    QVariantList m_multisportModes;
    QVariantList m_fieldTypes;
    QVariantList m_rowMenu;
    bool m_rowMenuMultiValue = false;
    int m_rowMenuMaxValues = 1;
    QVariantMap m_capabilities;
    QVariantList m_activities;
    int m_minMultisportLegs = 2;
    int m_maxMultisportLegs = 6;
    int m_sportModesUsed = 0;
    QString m_writingMode;

    // Shared plumbing for the four calls above: POST, report, refresh.
    void postManage(const QString &path, const QJsonObject &body, const QString &busyName);

    void setLoading(bool value);
    void setLastError(const QString &message);
    void setWritingMode(const QString &mode);

    static QUrl backendUrl(const QString &path);
};
