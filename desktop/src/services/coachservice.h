#pragma once

#include <QDate>
#include <QJsonArray>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QSet>
#include <QSettings>
#include <QSqlDatabase>
#include <QVariantList>
#include <QVariantMap>
#include <functional>

// Coach (v2 concept, 2026-08-21 — "implement it", one window: the readiness beacon + chat
// + the adapter seams behind both). This is the FIRST real build of the design sketched in
// three Artifact mockups (Fresh Today / Ride Coach / Pluggable Coach) and the standalone
// `coach/` scaffold (kept local-only, its own git repo, deliberately not part of this public
// repo — see that directory's README). Rather than depend on coach/'s Node/TypeScript code
// from this Qt/C++ app (a real cross-language coupling this app has no other example of),
// the SAME architecture — canonical readiness model, pluggable library/chat backends — is
// reimplemented here in C++, following coach/src/coach.ts's own maths line for line.
//
// Readiness is computed from THIS app's own local activity history (activities.db, the same
// database ActivityService already owns) — real, not fabricated, though honestly limited:
// this device family (Ambit3/Traverse/Kailash) has no power meter or HR strap decoded
// anywhere in this project, so the load signal is duration-based (minutes per day) rather
// than a true TSS/hrTSS. coach/src/model.ts's own comment already anticipated this
// ("Must be pluggable — most watches have no power meter") — duration is today's plug.
//
// Two independent toggles, both real (André, 2026-08-21: "can we have both with a toggle?"):
//   chatBackend      "canned"  — rule-based replies, zero setup, zero cost (default)
//                     "claude" — real Anthropic Messages API call, needs an API key
//                                (ANTHROPIC key from Settings, NOT the claude.ai subscription)
//   catalogueSource  "sample"  — the bundled coach/data/systm-sample.json (55 real SYSTM
//                                sessions, static, works offline)
//                     "live"   — GETs systmMcpUrl expecting the SAME JSON shape as the sample
//                                file. Honest limitation: joaodrp/wahoo-systm-mcp speaks MCP
//                                (stdio JSON-RPC), not plain HTTP — "live" here means a small
//                                HTTP bridge in front of it, which this app does not itself
//                                provide. Real code, just needs that bridge running.
class CoachService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // {fitness, fatigue, freshness, rampPerWeek, light ("green"/"tempered"/"yellow"/"red"),
    //  sentence, basis (["load"])}
    Q_PROPERTY(QVariantMap readiness READ readiness NOTIFY readinessChanged)
    // Last 42 days: [{date, fitness, fatigue}, ...] — for the beacon's sparkline.
    Q_PROPERTY(QVariantList chartSeries READ chartSeries NOTIFY readinessChanged)
    // Today's catalogue picks for the current light, [{name, durationSec, load, intensity}].
    Q_PROPERTY(QVariantList todaysPicks READ todaysPicks NOTIFY readinessChanged)
    // Chat transcript this session: [{role: "me"/"coach", text, cards: [...]}].
    Q_PROPERTY(QVariantList messages READ messages NOTIFY messagesChanged)
    Q_PROPERTY(bool sending READ sending NOTIFY sendingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

    Q_PROPERTY(QString chatBackend READ chatBackend WRITE setChatBackend NOTIFY chatBackendChanged)
    Q_PROPERTY(QString catalogueSource READ catalogueSource WRITE setCatalogueSource NOTIFY catalogueSourceChanged)
    Q_PROPERTY(bool anthropicKeySet READ anthropicKeySet NOTIFY anthropicKeySetChanged)
    Q_PROPERTY(QString systmMcpUrl READ systmMcpUrl WRITE setSystmMcpUrl NOTIFY systmMcpUrlChanged)

public:
    explicit CoachService(QObject *parent = nullptr);

    QVariantMap readiness() const { return m_readiness; }
    QVariantList chartSeries() const { return m_chartSeries; }
    QVariantList todaysPicks() const { return m_todaysPicks; }
    QVariantList messages() const { return m_messages; }
    bool sending() const { return m_sending; }
    QString lastError() const { return m_lastError; }

    QString chatBackend() const { return m_chatBackend; }
    void setChatBackend(const QString &v);
    QString catalogueSource() const { return m_catalogueSource; }
    void setCatalogueSource(const QString &v);
    bool anthropicKeySet() const;
    QString systmMcpUrl() const { return m_systmMcpUrl; }
    void setSystmMcpUrl(const QString &v);

    // Recomputes readiness from activities.db — call on page load and after a sync.
    Q_INVOKABLE void refreshReadiness();
    // Appends a "me" bubble, then a "coach" reply (canned or via Claude, per chatBackend).
    Q_INVOKABLE void sendMessage(const QString &text);
    Q_INVOKABLE void resetConversation();
    Q_INVOKABLE void setAnthropicApiKey(const QString &key);   // write-only, never read back
    Q_INVOKABLE void clearAnthropicApiKey();

signals:
    void readinessChanged();
    void messagesChanged();
    void sendingChanged();
    void lastErrorChanged();
    void chatBackendChanged();
    void catalogueSourceChanged();
    void anthropicKeySetChanged();
    void systmMcpUrlChanged();

private:
    struct CatalogueRow { QString name; double tss; double intensityFactor; int durationSec; };

    void openActivitiesDb();
    void computeReadiness();                   // the real CTL/ATL/TSB pass over activities.db
    static QString intensityBucket(double intensityFactor);   // mirrors systmLibrary.ts bucket()
    static QString intensityForLight(const QString &light);   // mirrors coach.ts recommend()
    static QString normalizeName(const QString &s);           // for matching completed vs catalogue
    QList<CatalogueRow> loadSampleCatalogue() const;
    void refreshCatalogueLive(std::function<void()> onDone);  // GET systmMcpUrl, async
    QVariantList pickWorkouts(const QString &intensity, int maxMinutes, int limit = 2) const;

    void appendBubble(const QString &role, const QString &text, const QVariantList &cards = {});
    void replyCanned(const QString &userText);
    void replyClaude(const QString &userText);
    void setSending(bool v);
    void setLastError(const QString &e);

    QSqlDatabase m_db;
    QNetworkAccessManager m_net;
    QSettings m_settings;
    QList<CatalogueRow> m_sampleCatalogue;   // coach/data/systm-sample.json, cached at startup
    // Normalized names of workouts completed in the last 7 days — pickWorkouts() drops these
    // so the coach stops re-recommending a session you've just done. Refreshed in
    // computeReadiness() (which already has activities.db open).
    QSet<QString> m_recentDone;

    QVariantMap m_readiness;
    QVariantList m_chartSeries;
    QVariantList m_todaysPicks;
    QVariantList m_messages;
    bool m_sending = false;
    QString m_lastError;

    QString m_chatBackend = QStringLiteral("canned");
    QString m_catalogueSource = QStringLiteral("sample");
    QString m_systmMcpUrl;
    QList<CatalogueRow> m_liveCatalogue;   // populated when catalogueSource == "live"
};
