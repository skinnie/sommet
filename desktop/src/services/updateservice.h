#pragma once

#include <QDateTime>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QStringList>
#include <QTimer>

class QNetworkReply;

// "Is there a newer Sommet?" (André, 2026-11-03, 🟣124 = tell you and take you there).
// Asks GitHub's public releases API for the latest release once a day (and when the Settings
// button is pressed), compares its tag with this build's version, and offers the download made
// for this computer: the .dmg on a Mac, the .zip on Windows, the AppImage on Linux (release
// page elsewhere). The app installs nothing itself; the browser downloads the file. Only the
// request reaches GitHub: no identifier, no usage data. The Android twin is
// android/src/services/AppUpdate.ts.
class UpdateService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(QString currentVersion READ currentVersion CONSTANT)
    Q_PROPERTY(bool checking READ checking NOTIFY changed)
    // A release newer than this build is known (from today's check or the last saved one).
    Q_PROPERTY(bool available READ available NOTIFY changed)
    // `available`, and the note on Home hasn't been closed for this version.
    Q_PROPERTY(bool showNotice READ showNotice NOTIFY changed)
    Q_PROPERTY(QString latestVersion READ latestVersion NOTIFY changed)
    // The release's "## date: title" changelog headings, without the date (tools/release_notes.py).
    Q_PROPERTY(QStringList highlights READ highlights NOTIFY changed)
    // True when this computer has its own download (.dmg / .zip); otherwise Download opens the page.
    Q_PROPERTY(bool hasDirectDownload READ hasDirectDownload NOTIFY changed)
    // "" (not checked yet), "upToDate", "available" or "error".
    Q_PROPERTY(QString state READ state NOTIFY changed)
    Q_PROPERTY(QString lastChecked READ lastChecked NOTIFY changed)
    Q_PROPERTY(bool autoCheck READ autoCheck WRITE setAutoCheck NOTIFY autoCheckChanged)

public:
    explicit UpdateService(QObject *parent = nullptr);

    QString currentVersion() const;
    bool checking() const { return m_checking; }
    bool available() const;
    bool showNotice() const;
    QString latestVersion() const { return m_latestVersion; }
    QStringList highlights() const { return m_highlights; }
    bool hasDirectDownload() const { return !m_downloadUrl.isEmpty(); }
    QString state() const { return m_state; }
    QString lastChecked() const;
    bool autoCheck() const;
    void setAutoCheck(bool on);

    // Asks GitHub now (the Settings button). The daily check calls it too.
    Q_INVOKABLE void check();
    // Opens this computer's download in the browser, or the release page when there is none.
    Q_INVOKABLE void download();
    Q_INVOKABLE void openReleasePage();
    // Closes the note on Home until a newer version comes out. Settings still shows it.
    Q_INVOKABLE void dismiss();

    // "v0.2.58" newer than "0.2.57"? Tags and versions both accepted; garbage is never newer.
    static bool isNewer(const QString &latest, const QString &current);
    static QStringList highlightsFrom(const QString &body);

signals:
    void changed();
    void autoCheckChanged();

private:
    void checkIfDue();
    void handleReply(QNetworkReply *reply);
    // Reads tag/page/assets/body out of GitHub's JSON; false when it isn't a usable release.
    bool applyRelease(const QByteArray &json);
    void loadSaved();

    QNetworkAccessManager m_nam;
    QTimer m_daily;
    bool m_checking = false;
    QString m_state;
    QString m_latestVersion;
    QString m_pageUrl;
    QString m_downloadUrl;
    QStringList m_highlights;
    QDateTime m_lastChecked;
    QString m_dismissed;
};
