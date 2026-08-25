#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>
#include <QVariantMap>

// Real, 2026-08-09 - the Suunto App install chain (tools/apps.py, tools/workout_install.py,
// custom_modes.py) is fully reverse-engineered, hardware-confirmed, and wired into
// backend/server.py's own three endpoints. Kept as its own service rather than folded into
// CustomModesService - this is genuinely a different data domain (a 13,104-entry app catalog
// and the watch's own separate Apps flash region), not sport-mode structure itself.
//
// How an installed app actually RENDERS (training_program_andre.md Findings 44-46,
// hardware-confirmed): the app's rule-engine slot (51/52/53) is appended as a
// DISP_FIELD_SHORTCUT on a chosen display field, so that row CYCLES to the app on button
// presses - it does NOT overwrite the field's Type. And catalog binaries already carry the
// 8-byte IAMRULE magic, which workout_install.py strips before writing so it isn't doubled
// into corrupt bytecode. Both were the reasons earlier installs showed "--"; both are fixed
// in the shared tools this service calls, so no logic here needs to know either.
class AppsService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // Real apps currently installed on the watch (tools/apps.py's own decode of the Apps
    // flash region). Each entry: {ruleIdx, name, activityId, binaryLength, catalogMatch?:
    // {ruleId, name, categoryId, description}}. ruleIdx is confirmed to be exactly the
    // RuleIdx a sport mode's own RULE record references - a UI can label the app cycling on a
    // display field's shortcut with the real app name by matching that RuleIdx against this
    // list's own ruleIdx.
    Q_PROPERTY(QVariantList installedApps READ installedApps NOTIFY installedAppsChanged)
    Q_PROPERTY(bool searching READ searching NOTIFY searchingChanged)
    // Real catalog search results (data/suunto_apps/ - this app's own bundled copy of
    // SuuntoLink's real Suunto Apps catalog). Each entry: {ruleId, name, categoryId,
    // activityId, description, userCount, compatibleVariants}.
    Q_PROPERTY(QVariantList searchResults READ searchResults NOTIFY searchResultsChanged)
    Q_PROPERTY(bool installing READ installing NOTIFY installingChanged)
    // Result of the last install() call - {ok, dryRun?, wouldBeRuleIdx?, ruleIdx?, name,
    // ruleId, error?}. A UI reads this right after install() rather than a separate signal
    // payload, matching how every other write-result property in this app already works.
    Q_PROPERTY(QVariantMap lastInstallResult READ lastInstallResult NOTIFY lastInstallResultChanged)
    // Whether a Suunto Apps catalog is present at all, and how many entries. The App Zone
    // catalog is Suunto's proprietary content and the App Zone service is dead, so nothing is
    // shipped or hosted - the user imports their own SuuntoLink suunto-apps/index.json (André,
    // 2026-08-14: "import it from a suunto link installation"; and for Linux, where SuuntoLink
    // isn't installed, the same file copied over). hasCatalog gates the UI between the import
    // prompt and the app browser.
    Q_PROPERTY(bool hasCatalog READ hasCatalog NOTIFY catalogStatusChanged)
    Q_PROPERTY(int catalogCount READ catalogCount NOTIFY catalogStatusChanged)
    Q_PROPERTY(bool importing READ importing NOTIFY importingChanged)
    // Per-app "log this app's output into recorded Moves" state (EXERCISE_MODES_RULE.LogRule),
    // one row per activated Suunto App across every sport mode. Each entry: {mode, modeName,
    // slot, ruleIdx, app, logRule}. Only activated apps (UseRule=1) are included - those are
    // the apps a user actually records with, and the only ones a logging toggle makes sense
    // for. Populated by refreshLogging() (GET /api/apps/logging).
    Q_PROPERTY(QVariantList loggedApps READ loggedApps NOTIFY loggedAppsChanged)
    // A LogRule toggle write (POST /api/apps/logging) is in flight - the UI disables the
    // switch it came from until this clears, matching how every other watch write here gates.
    Q_PROPERTY(bool loggingBusy READ loggingBusy NOTIFY loggingBusyChanged)

public:
    explicit AppsService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }
    QVariantList installedApps() const { return m_installedApps; }
    bool searching() const { return m_searching; }
    QVariantList searchResults() const { return m_searchResults; }
    bool installing() const { return m_installing; }
    QVariantMap lastInstallResult() const { return m_lastInstallResult; }
    bool hasCatalog() const { return m_hasCatalog; }
    int catalogCount() const { return m_catalogCount; }
    bool importing() const { return m_importing; }
    QVariantList loggedApps() const { return m_loggedApps; }
    bool loggingBusy() const { return m_loggingBusy; }

    // GET /api/apps/catalog_status - is a catalog present, and how big. No watch access.
    Q_INVOKABLE void refreshCatalogStatus();

    // POST /api/apps/import - extract a user-selected SuuntoLink suunto-apps/index.json into
    // this app's compact catalog. `path` is a local filesystem path (a QML FileDialog gives a
    // file:// URL - strip it with the URL's toLocalFile() before calling). No watch access.
    Q_INVOKABLE void importCatalog(const QString &path);

    // GET /api/apps - real, read-only (apps.py's own fast probe-first path), safe any time.
    Q_INVOKABLE void refreshInstalledApps();

    // GET /api/apps/catalog?q=&variant=&category= - no watch touched, a local file search.
    // `categoryId` < 0 means "any category" (QML has no clean "omit this arg" for an
    // optional int, so this stands in for that).
    Q_INVOKABLE void searchCatalog(const QString &query, const QString &variant,
                                    int categoryId = -1);

    // POST /api/apps/install. `mode` is the same 0-based EXERCISE_MODES_MODE index
    // CustomModesService.modes' own array position already is - not the mode's name.
    // confirm:false gets a real preview (wouldBeRuleIdx, built from the already-safe
    // /api/apps data - see backend/server.py's own _handle_apps_install comment for why
    // this never calls the write-capable tool at all in that case) without touching the
    // watch; confirm:true performs the real write. Same "explicit UI action is the
    // confirmation" rule as every other write in this app.
    Q_INVOKABLE void install(int mode, int display, int field, int ruleId, bool confirm);

    // GET /api/apps/logging - read-only, safe any time (needs a connected watch). Fills
    // loggedApps with each activated app and whether its output is being logged.
    Q_INVOKABLE void refreshLogging();

    // POST /api/apps/logging - flip ONE app's LogRule on the watch. `mode`/`slot` are the
    // same indices refreshLogging()'s own rows carry. A real single-byte flash write, guarded
    // by tools/app_logging.py's own "exactly one LogRule byte changed" safety gate; on success
    // loggedApps is re-read so the UI reflects the watch. The switch being toggled is the
    // user's confirmation, same rule as every other write in this app.
    Q_INVOKABLE void setLogging(int mode, int slot, bool on);

signals:
    void loadingChanged();
    void lastErrorChanged();
    void installedAppsChanged();
    void searchingChanged();
    void searchResultsChanged();
    void installingChanged();
    void lastInstallResultChanged();
    void catalogStatusChanged();
    void importingChanged();
    // Emitted after importCatalog() finishes - ok, and the entry count on success.
    void catalogImported(bool ok, int count, const QString &error);
    void loggedAppsChanged();
    void loggingBusyChanged();
    // Emitted after a setLogging() write finishes - ok, plus an error string on failure.
    void loggingToggled(bool ok, const QString &error);

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QString m_lastError;
    QVariantList m_installedApps;
    bool m_searching = false;
    QVariantList m_searchResults;
    bool m_installing = false;
    QVariantMap m_lastInstallResult;
    bool m_hasCatalog = false;
    int m_catalogCount = 0;
    bool m_importing = false;
    QVariantList m_loggedApps;
    bool m_loggingBusy = false;

    void setLoading(bool value);
    void setLastError(const QString &message);
    void setSearching(bool value);
    void setInstalling(bool value);
    void setImporting(bool value);
    void setLoggingBusy(bool value);

    static QUrl backendUrl(const QString &path);
};
