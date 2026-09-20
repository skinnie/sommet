#pragma once

#include <QDate>
#include <QJsonArray>
#include <QJsonObject>
#include <QMap>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QSet>
#include <QSettings>
#include <QString>
#include <QSqlDatabase>
#include <QVariantList>
#include <QVariantMap>

// JournalService — the "Personal Connection MVP" (2026-09-21, André's spec: "help André
// notice connections between how he lives, his environment, his body, his behaviour and how
// he feels"). NOT a health optimiser, NOT a coach, NOT a dashboard. The core loop is:
//
//     free-form journal  ->  structured experience  ->  context  ->  reflection
//
// This is modelled line-for-line on CoachService (its own local SQLite DB + the SAME
// Anthropic Messages API path CoachService already uses), deliberately reusing the existing
// architecture rather than inventing a second one:
//   * Storage: a local journal.db in AppPaths::databaseDir() (next to activities.db).
//   * LLM: reuses the EXISTING coach/anthropicApiKey QSetting — one key for the whole app,
//     André never enters it twice. The LLM is used ONLY to (a) extract events/observations
//     from the raw text and (b) answer a free-form reflection question. Both are isolated in
//     their own methods (interpretEntry / ask) so the journal + structured data could later
//     run against a local model by swapping just those two boundaries. No key -> the raw
//     journal still saves and displays; interpretation is simply skipped.
//   * Enrichment: activity comes from the SAME activities.db ActivityService owns (read-only
//     here); the weather snapshot is passed in from QML at save time (WeatherService is a QML
//     singleton). Missing data is always acceptable — nothing is fabricated or inferred.
//
// Reflection is plain statistics, NOT ML: simple co-occurrence over day-tags, weighted by the
// bundled knowledge priors (assets/journal/knowledge.json). The system NEVER presents a
// hypothesis as fact — insights/answers separate FACT / OBSERVATION / HYPOTHESIS /
// SCIENTIFIC CONTEXT. No scores, no streaks, no gamification, no notifications.
class JournalService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // Recent entries, newest first, each already joined with its extracted events/observations
    // and its captured context:
    // [{id, date, rawText, createdAt, interpreted(bool),
    //   events:[{time, category, value, confidence}],
    //   observations:[{category, valence, text}],
    //   weather:{...}|{}, activities:[{name, sport, durationMin, distanceKm}]}]
    Q_PROPERTY(QVariantList entries READ entries NOTIFY entriesChanged)
    // Surfaced reflections, most interesting first. Each is one of the four kinds:
    // [{kind:"observation"|"hypothesis"|"quiet", title, detail, kindLabel, count, confidence,
    //   status, hypothesisId}]. A single {kind:"quiet"} row when nothing meaningful emerged.
    Q_PROPERTY(QVariantList insights READ insights NOTIFY insightsChanged)
    // Active/!dismissed experiments: [{id, description, startDate, endDate, status, days}].
    Q_PROPERTY(QVariantList experiments READ experiments NOTIFY experimentsChanged)
    // Free-form reflection transcript this session: [{role:"me"/"sommet", text}].
    Q_PROPERTY(QVariantList messages READ messages NOTIFY messagesChanged)

    Q_PROPERTY(bool interpreting READ interpreting NOTIFY interpretingChanged)
    Q_PROPERTY(bool asking READ asking NOTIFY askingChanged)
    Q_PROPERTY(bool anthropicKeySet READ anthropicKeySet NOTIFY anthropicKeySetChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

public:
    explicit JournalService(QObject *parent = nullptr);

    QVariantList entries() const { return m_entries; }
    QVariantList insights() const { return m_insights; }
    QVariantList experiments() const { return m_experiments; }
    QVariantList messages() const { return m_messages; }
    bool interpreting() const { return m_interpreting; }
    bool asking() const { return m_asking; }
    bool anthropicKeySet() const;
    QString lastError() const { return m_lastError; }

    // Reload entries + context + insights from disk. Call on page load.
    Q_INVOKABLE void refresh();

    // Save a raw journal entry EXACTLY as written (never overwritten). `weather` is an optional
    // snapshot map from QML (WeatherService) captured at save time; pass {} when unavailable.
    // After saving, if a key is set, kicks off interpretation automatically.
    Q_INVOKABLE void saveEntry(const QString &date, const QString &rawText,
                               const QVariantMap &weather = {});
    // Re-run LLM interpretation for one entry (e.g. after adding an API key). Replaces that
    // entry's derived events/observations; never touches raw_text.
    Q_INVOKABLE void interpretEntry(int entryId);
    Q_INVOKABLE void deleteEntry(int entryId);

    // Ask Sommet a free-form question ("noticed anything about my energy lately?"). Assembles
    // journal + observations + activity + weather + relevant knowledge + current hypotheses,
    // then answers via Claude, keeping FACT/OBSERVATION/HYPOTHESIS/SCIENCE separate.
    Q_INVOKABLE void ask(const QString &question);
    Q_INVOKABLE void resetConversation();

    // Habit experiments — small, reversible, optional. Created only when the user chooses to.
    Q_INVOKABLE void createExperiment(const QString &description, int days, int hypothesisId = -1);
    Q_INVOKABLE void updateExperimentStatus(int experimentId, const QString &status);

signals:
    void entriesChanged();
    void insightsChanged();
    void experimentsChanged();
    void messagesChanged();
    void interpretingChanged();
    void askingChanged();
    void anthropicKeySetChanged();
    void lastErrorChanged();

private:
    void openDb();
    void ensureSchema();
    void seedKnowledge();                 // upsert assets/journal/knowledge.json (idempotent)
    void loadEntries();                   // -> m_entries
    void loadExperiments();               // -> m_experiments
    void computeInsights();               // the plain-stats co-occurrence pass -> m_insights

    // --- LLM boundary (the ONLY two methods that talk to a model) ---
    void runInterpretation(int entryId, const QString &rawText, const QString &date);
    void persistInterpretation(int entryId, const QJsonObject &parsed);
    QString buildReflectionContext() const;   // the plain-text digest handed to ask()

    // --- enrichment (local, no model) ---
    QVariantList activitiesForDate(const QString &date) const;

    // --- stats helpers ---
    // Per-day set of normalized tags derived from events + observations, for co-occurrence.
    QMap<QDate, QSet<QString>> dayTags() const;

    QString apiKey() const;
    QString normalizeSlug(const QString &s) const;

    void setInterpreting(bool v);
    void setAsking(bool v);
    void setLastError(const QString &e);
    void appendBubble(const QString &role, const QString &text);

    QSqlDatabase m_db;         // journal.db (own connection name)
    QSqlDatabase m_actDb;      // activities.db, read-only, own connection name
    QNetworkAccessManager m_net;
    QSettings m_settings;

    QVariantList m_entries;
    QVariantList m_insights;
    QVariantList m_experiments;
    QVariantList m_messages;
    bool m_interpreting = false;
    bool m_asking = false;
    QString m_lastError;
};
