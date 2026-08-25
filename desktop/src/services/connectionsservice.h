#pragma once

#include <QHash>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QSettings>
#include <QString>
#include <QTcpServer>

// Home and Settings both showed a static "Intervals.icu / Runalyze / Strava" list with grey
// dots and no way to actually click into any of them - found 2026-08-07 via real testing
// ("you can't click to setup or input your key, our android app has that well implemented").
//
// Checked what the real Android app (oss/opensportsync-main) actually does before building
// this, twice - first for Intervals.icu, then again after André corrected an assumption
// about Runalyze:
// - Intervals.icu: simple personal API-key auth (HTTP Basic, athleteId + a key from
//   intervals.icu's own Settings -> Developer Settings, NOT OAuth -
//   src/services/ApiIntervalsIcu.ts).
// - Runalyze: also simple API-key auth (a single token header, NOT OAuth -
//   src/services/ApiRunalyze.ts) - the original version of this class wrongly lumped it in
//   with Strava as "needs real OAuth," corrected once actually checked.
// - Strava: genuinely real OAuth2 (src/services/ApiStrava.ts - real client ID/secret from
//   its own registered app, authorize/token URLs, refresh tokens). Built for real 2026-08-07:
//   opensportsync uses a custom URL scheme (opensportsync://oauth/strava) for the redirect,
//   which needs the app registered as a URL handler with the OS - real, doable on Linux, but
//   heavier than this app currently needs. A local loopback HTTP callback server
//   (http://127.0.0.1:<ephemeral port>/callback, opened via QDesktopServices::openUrl into
//   the system browser) is the standard equivalent for a desktop app and needs no OS
//   registration - Strava's own OAuth docs list "http://localhost" as a valid Authorization
//   Callback Domain for exactly this case. Same client_id/client_secret/token/refresh_token
//   shape as the real Android app either way - see connectStrava()/exchangeStravaCode().
//
// Dropbox / Google Drive / OneDrive (added 2026-08-12, "implement the ones that the user can
// set up easily by itself: open a login site and approve, or copy an API key to a file") -
// same self-serve principle as Strava, not a shared AmbitApp-owned app: each user registers
// their own free app on the provider's own developer console (a five-minute click-through,
// same as Strava's own strava.com/settings/api step) and pastes the client ID (+secret where
// the provider needs one) in here. All three use the same loopback-callback OAuth flow as
// Strava, generalised into startCloudOAuth()/exchangeCloudCode() below rather than pasted
// three more times - Dropbox and Google Drive use the classic client_id+client_secret
// Authorization Code flow (same shape as Strava); OneDrive/Microsoft Graph uses PKCE instead
// (no client secret - Microsoft's own recommended flow for a public/native client, and one
// fewer thing for the user to manage since personal Microsoft accounts can't use app-only
// auth anyway). All three request the provider's own "app-scoped folder" permission (Dropbox
// App Folder, Google `drive.file`, OneDrive `Files.ReadWrite.AppFolder`) rather than full
// Drive/Dropbox access - least privilege, and it means the app never has to browse or pick a
// folder, only ever sees what it created itself. Used by CloudStorageService for the actual
// backup upload/list/download - this class only owns the connect/disconnect/token lifecycle.
//
// Credentials stored via QSettings (this app's own org/name, set in main.cpp) - local,
// plain-text config, the same tier of storage most small desktop apps use for this; not an
// OS keychain, worth revisiting if this app ever handles anything more sensitive than a
// personal read/write API key.
class ConnectionsService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool intervalsIcuConnected READ intervalsIcuConnected NOTIFY intervalsIcuChanged)
    Q_PROPERTY(QString intervalsIcuAthleteId READ intervalsIcuAthleteId NOTIFY intervalsIcuChanged)
    // Garmin Connect (André, 2026-08-24): login lives in Settings; the OAuth token store is
    // owned backend-side (tools/garmin_weight.py), so here we only track a connected flag +
    // the email, set once a login succeeds. See garminConnected / setGarminConnected.
    Q_PROPERTY(bool garminConnected READ garminConnected NOTIFY garminChanged)
    Q_PROPERTY(QString garminEmail READ garminEmail NOTIFY garminChanged)
    // What Garmin syncs: activities in, and an export scope out (weight + health merge on their
    // own pages). garminExportScope mirrors the intervals export scope: manual/suunto/etrex/all.
    Q_PROPERTY(bool garminImportActivities READ garminImportActivities WRITE setGarminImportActivities NOTIFY syncFlagsChanged)
    Q_PROPERTY(QString garminExportScope READ garminExportScope WRITE setGarminExportScope NOTIFY syncFlagsChanged)

    // intervals.icu sync-menu toggles (Andre, 2026-08-18): the user picks what "Sync now"
    // runs. Manual, not background - each is a plain persisted on/off. Defaults keep the
    // watch-writing one off (opt-in) and the read-only pulls on.
    Q_PROPERTY(bool syncImportGear READ syncImportGear WRITE setSyncImportGear NOTIFY syncFlagsChanged)
    Q_PROPERTY(bool syncStatsToWatch READ syncStatsToWatch WRITE setSyncStatsToWatch NOTIFY syncFlagsChanged)
    Q_PROPERTY(bool syncActivityLevel READ syncActivityLevel WRITE setSyncActivityLevel NOTIFY syncFlagsChanged)
    Q_PROPERTY(bool syncImportActivities READ syncImportActivities WRITE setSyncImportActivities NOTIFY syncFlagsChanged)
    // Import activities from the Garmin Connect cloud account (André, 2026-08-24) - distinct
    // from the eTrex USB import; shares the Garmin login done on the Weight page.
    Q_PROPERTY(bool syncImportGarmin READ syncImportGarmin WRITE setSyncImportGarmin NOTIFY syncFlagsChanged)
    Q_PROPERTY(bool syncExportActivities READ syncExportActivities WRITE setSyncExportActivities NOTIFY syncFlagsChanged)
    // Export-scope selector (André, 2026-08-24): "manual" (per-activity only), "suunto" (watch
    // moves), "etrex" (Garmin eTrex device moves), "all" (suunto+etrex). Source of truth for
    // export; shares the QSettings key ActivityService reads for its auto-export.
    Q_PROPERTY(QString exportScope READ exportScope WRITE setExportScope NOTIFY syncFlagsChanged)
    // How far back the activity import pulls: 0 = everything, else the last N days (André: "let
    // user decide").
    Q_PROPERTY(int syncImportDays READ syncImportDays WRITE setSyncImportDays NOTIFY syncFlagsChanged)
    Q_PROPERTY(bool runalyzeConnected READ runalyzeConnected NOTIFY runalyzeChanged)
    Q_PROPERTY(bool stravaConnected READ stravaConnected NOTIFY stravaChanged)
    Q_PROPERTY(bool stravaConnecting READ stravaConnecting NOTIFY stravaConnectingChanged)
    Q_PROPERTY(QString stravaClientId READ stravaClientId NOTIFY stravaChanged)
    Q_PROPERTY(QString stravaError READ stravaError NOTIFY stravaErrorChanged)

    Q_PROPERTY(bool dropboxConnected READ dropboxConnected NOTIFY dropboxChanged)
    Q_PROPERTY(bool dropboxConnecting READ dropboxConnecting NOTIFY dropboxConnectingChanged)
    Q_PROPERTY(QString dropboxClientId READ dropboxClientId NOTIFY dropboxChanged)
    Q_PROPERTY(QString dropboxError READ dropboxError NOTIFY dropboxErrorChanged)

    Q_PROPERTY(bool googleDriveConnected READ googleDriveConnected NOTIFY googleDriveChanged)
    Q_PROPERTY(bool googleDriveConnecting READ googleDriveConnecting NOTIFY googleDriveConnectingChanged)
    Q_PROPERTY(QString googleDriveClientId READ googleDriveClientId NOTIFY googleDriveChanged)
    Q_PROPERTY(QString googleDriveError READ googleDriveError NOTIFY googleDriveErrorChanged)

    Q_PROPERTY(bool oneDriveConnected READ oneDriveConnected NOTIFY oneDriveChanged)
    Q_PROPERTY(bool oneDriveConnecting READ oneDriveConnecting NOTIFY oneDriveConnectingChanged)
    Q_PROPERTY(QString oneDriveClientId READ oneDriveClientId NOTIFY oneDriveChanged)
    Q_PROPERTY(QString oneDriveError READ oneDriveError NOTIFY oneDriveErrorChanged)

public:
    explicit ConnectionsService(QObject *parent = nullptr);

    bool intervalsIcuConnected() const { return !m_intervalsIcuAthleteId.isEmpty(); }
    QString intervalsIcuAthleteId() const { return m_intervalsIcuAthleteId; }

    bool garminConnected() const {
        return QSettings().value(QStringLiteral("connections/garmin/connected"), false).toBool();
    }
    QString garminEmail() const {
        return QSettings().value(QStringLiteral("connections/garmin/email")).toString();
    }
    // Set by WeightService after a successful/failed Garmin login (the network call lives there).
    Q_INVOKABLE void setGarminConnected(bool on, const QString &email) {
        QSettings s;
        s.setValue(QStringLiteral("connections/garmin/connected"), on);
        if (on && !email.isEmpty())
            s.setValue(QStringLiteral("connections/garmin/email"), email);
        emit garminChanged();
    }
    Q_INVOKABLE void disconnectGarmin() {
        QSettings s;
        s.remove(QStringLiteral("connections/garmin/connected"));
        s.remove(QStringLiteral("connections/garmin/email"));
        emit garminChanged();
    }
    bool garminImportActivities() const { return syncFlag("garminImportActivities", false); }
    void setGarminImportActivities(bool v) { setSyncFlag("garminImportActivities", v); }
    QString garminExportScope() const {
        return QSettings().value(QStringLiteral("garmin/exportScope"),
                                 QStringLiteral("manual")).toString();
    }
    void setGarminExportScope(const QString &v) {
        if (v == garminExportScope())
            return;
        QSettings().setValue(QStringLiteral("garmin/exportScope"), v);
        emit syncFlagsChanged();
    }

    bool syncImportGear() const { return syncFlag("importGear", true); }
    void setSyncImportGear(bool v) { setSyncFlag("importGear", v); }
    bool syncStatsToWatch() const { return syncFlag("statsToWatch", false); }
    void setSyncStatsToWatch(bool v) { setSyncFlag("statsToWatch", v); }
    bool syncActivityLevel() const { return syncFlag("activityLevel", true); }
    void setSyncActivityLevel(bool v) { setSyncFlag("activityLevel", v); }
    bool syncImportActivities() const { return syncFlag("importActivities", false); }
    void setSyncImportActivities(bool v) { setSyncFlag("importActivities", v); }
    bool syncImportGarmin() const { return syncFlag("importGarmin", false); }
    void setSyncImportGarmin(bool v) { setSyncFlag("importGarmin", v); }
    // Derived from exportScope so any legacy binding still reads "is export on?".
    bool syncExportActivities() const { return exportScope() != QStringLiteral("manual"); }
    void setSyncExportActivities(bool v) {
        setExportScope(v ? QStringLiteral("suunto") : QStringLiteral("manual"));
    }
    QString exportScope() const {
        return QSettings().value(QStringLiteral("intervals/exportScope"),
                                 QStringLiteral("manual")).toString();
    }
    void setExportScope(const QString &v) {
        if (v == exportScope())
            return;
        QSettings().setValue(QStringLiteral("intervals/exportScope"), v);
        emit syncFlagsChanged();
    }
    int syncImportDays() const;
    void setSyncImportDays(int v);
    bool runalyzeConnected() const { return m_runalyzeConnected; }
    bool stravaConnected() const { return !m_stravaRefreshToken.isEmpty(); }
    bool stravaConnecting() const { return m_stravaConnecting; }
    QString stravaClientId() const { return m_stravaClientId; }
    QString stravaError() const { return m_stravaError; }

    bool dropboxConnected() const { return !m_cloudRefreshToken.value(kDropbox).isEmpty(); }
    bool dropboxConnecting() const { return m_cloudConnecting.value(kDropbox); }
    QString dropboxClientId() const { return m_cloudClientId.value(kDropbox); }
    QString dropboxError() const { return m_cloudError.value(kDropbox); }

    bool googleDriveConnected() const { return !m_cloudRefreshToken.value(kGoogleDrive).isEmpty(); }
    bool googleDriveConnecting() const { return m_cloudConnecting.value(kGoogleDrive); }
    QString googleDriveClientId() const { return m_cloudClientId.value(kGoogleDrive); }
    QString googleDriveError() const { return m_cloudError.value(kGoogleDrive); }

    bool oneDriveConnected() const { return !m_cloudRefreshToken.value(kOneDrive).isEmpty(); }
    bool oneDriveConnecting() const { return m_cloudConnecting.value(kOneDrive); }
    QString oneDriveClientId() const { return m_cloudClientId.value(kOneDrive); }
    QString oneDriveError() const { return m_cloudError.value(kOneDrive); }

    Q_INVOKABLE void saveIntervalsIcu(const QString &athleteId, const QString &apiKey);
    Q_INVOKABLE void disconnectIntervalsIcu();
    // Never exposed as a Q_PROPERTY - read once, into a form field, not bound in a Text
    // anywhere. QSettings' own storage is already plain text; no reason to also keep the
    // key sitting in a live QML property for longer than the dialog needs it.
    Q_INVOKABLE QString intervalsIcuApiKey() const;

    Q_INVOKABLE void saveRunalyze(const QString &apiKey);
    Q_INVOKABLE void disconnectRunalyze();
    Q_INVOKABLE QString runalyzeApiKey() const;

    // Starts the real OAuth2 flow: saves clientId/clientSecret, opens the system browser to
    // Strava's authorize page, and listens on a local loopback port for the redirect. Ends by
    // emitting stravaChanged() (success) or stravaErrorChanged() (any failure - server bind,
    // user never approves within the timeout, token exchange rejected).
    Q_INVOKABLE void connectStrava(const QString &clientId, const QString &clientSecret);
    Q_INVOKABLE void disconnectStrava();
    Q_INVOKABLE QString stravaClientSecret() const;

    // Same shape as connectStrava() - see this class's header comment for what differs
    // (least-privilege app-folder scopes) and what doesn't (loopback callback flow).
    Q_INVOKABLE void connectDropbox(const QString &clientId, const QString &clientSecret);
    Q_INVOKABLE void disconnectDropbox();
    Q_INVOKABLE QString dropboxClientSecret() const;

    Q_INVOKABLE void connectGoogleDrive(const QString &clientId, const QString &clientSecret);
    Q_INVOKABLE void disconnectGoogleDrive();
    Q_INVOKABLE QString googleDriveClientSecret() const;

    // No client secret parameter - OneDrive/Microsoft Graph uses PKCE (see header comment).
    Q_INVOKABLE void connectOneDrive(const QString &clientId);
    Q_INVOKABLE void disconnectOneDrive();

    // CloudStorageService (a separate class, not a friend of this one) reads/writes the same
    // "connections/<provider>/..." QSettings groups directly rather than going through a live
    // C++ pointer to this singleton - the same way two independent QSettings instances in
    // this app always share the one underlying store. Two QML_SINGLETON classes with no
    // established constructor-injection wiring in main.cpp (Qt creates each lazily on first
    // QML use) made that simpler than adding cross-singleton lookup machinery for it. It does
    // duplicate the token-refresh POST shape (~30 lines, not the whole OAuth dance) - see its
    // own header comment.

signals:
    void intervalsIcuChanged();
    void garminChanged();
    void syncFlagsChanged();
    void runalyzeChanged();
    void stravaChanged();
    void stravaConnectingChanged();
    void stravaErrorChanged();

    void dropboxChanged();
    void dropboxConnectingChanged();
    void dropboxErrorChanged();
    void googleDriveChanged();
    void googleDriveConnectingChanged();
    void googleDriveErrorChanged();
    void oneDriveChanged();
    void oneDriveConnectingChanged();
    void oneDriveErrorChanged();

private:
    void setStravaError(const QString &message);
    void handleStravaCallback(const QString &requestLine);
    void exchangeStravaCode(const QString &clientId, const QString &clientSecret,
                             const QString &code);
    void stopStravaCallbackServer();

    // provider is one of kDropbox/kGoogleDrive/kOneDrive below - the QSettings group name and
    // the internal QHash key are the same string throughout this class.
    struct CloudOAuthConfig {
        QString authUrl;
        QString tokenUrl;
        QString scope;
        bool pkce = false;
        QList<QPair<QString, QString>> extraAuthParams;
    };
    static CloudOAuthConfig cloudConfig(const QString &provider);
    void startCloudOAuth(const QString &provider, const QString &clientId, const QString &clientSecret);
    void exchangeCloudCode(const QString &provider, const QString &clientId, const QString &clientSecret,
                            const QString &code, const QString &redirectUri, const QString &codeVerifier);
    void setCloudError(const QString &provider, const QString &message);
    void setCloudConnecting(const QString &provider, bool value);
    void stopCloudCallbackServer(const QString &provider);
    void emitCloudChanged(const QString &provider);
    static QString cloudGroup(const QString &provider);

    QSettings m_settings;
    QString m_intervalsIcuAthleteId;
    bool syncFlag(const char *name, bool def) const;
    void setSyncFlag(const char *name, bool v);
    bool m_runalyzeConnected = false;

    QNetworkAccessManager m_network;
    QTcpServer *m_stravaCallbackServer = nullptr;
    QString m_stravaClientId;
    QString m_stravaRefreshToken;
    bool m_stravaConnecting = false;
    QString m_stravaError;

    static const QString kDropbox;
    static const QString kGoogleDrive;
    static const QString kOneDrive;

    QHash<QString, QString> m_cloudClientId;
    QHash<QString, QString> m_cloudRefreshToken;
    QHash<QString, QString> m_cloudAccessToken;
    QHash<QString, qint64> m_cloudExpiresAt;
    QHash<QString, bool> m_cloudConnecting;
    QHash<QString, QString> m_cloudError;
    QHash<QString, QTcpServer *> m_cloudCallbackServers;
};
