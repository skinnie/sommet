#pragma once

#include <QDate>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QMap>
#include <QNetworkAccessManager>
#include <QPair>
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
    // The accumulating local knowledge base (the "3" in André's 1+2->3 design). Newest first:
    // [{topic, statement, evidenceLevel, source, sourceUrl, origin, fetchedAt, caveats}].
    // origin is "seed" (bundled), "model" (Claude's own knowledge) or "pubmed" (Europe PMC,
    // grounded + cited). As this fills, reflection leans on it and calls out less.
    Q_PROPERTY(QVariantList knowledge READ knowledge NOTIFY knowledgeChanged)
    // Topics Sommet could still deepen online, derived from YOUR logged habits (generic science
    // phrases only — never your data): [{topic, query, have(bool)}].
    Q_PROPERTY(QVariantList knowledgeTopics READ knowledgeTopics NOTIFY knowledgeChanged)

    Q_PROPERTY(bool interpreting READ interpreting NOTIFY interpretingChanged)
    Q_PROPERTY(bool asking READ asking NOTIFY askingChanged)
    Q_PROPERTY(bool enriching READ enriching NOTIFY enrichingChanged)
    Q_PROPERTY(QString enrichStatus READ enrichStatus NOTIFY enrichStatusChanged)
    Q_PROPERTY(bool anthropicKeySet READ anthropicKeySet NOTIFY anthropicKeySetChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

public:
    explicit JournalService(QObject *parent = nullptr);

    QVariantList entries() const { return m_entries; }
    QVariantList insights() const { return m_insights; }
    QVariantList experiments() const { return m_experiments; }
    QVariantList messages() const { return m_messages; }
    QVariantList knowledge() const { return m_knowledge; }
    QVariantList knowledgeTopics() const;
    bool interpreting() const { return m_interpreting; }
    bool asking() const { return m_asking; }
    bool enriching() const { return m_enriching; }
    QString enrichStatus() const { return m_enrichStatus; }
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

    // Knowledge growth (1+2->3). EXPLICIT, opt-in: these are the ONLY methods that reach the
    // public internet, and they send ONLY a generic science topic string — never journal text,
    // never logged habits. deepenScience() walks every topic your habits touch that isn't yet
    // in the local base (or is stale) and enriches each; enrichTopic() does one.
    Q_INVOKABLE void deepenScience();
    Q_INVOKABLE void enrichTopic(const QString &topic, const QString &query = {});
    Q_INVOKABLE void deleteKnowledge(int knowledgeId);

signals:
    void entriesChanged();
    void insightsChanged();
    void experimentsChanged();
    void messagesChanged();
    void knowledgeChanged();
    void interpretingChanged();
    void askingChanged();
    void enrichingChanged();
    void enrichStatusChanged();
    void anthropicKeySetChanged();
    void lastErrorChanged();

private:
    void openDb();
    void ensureSchema();
    void seedKnowledge();                 // upsert assets/journal/knowledge.json (idempotent)
    void loadEntries();                   // -> m_entries
    void loadExperiments();               // -> m_experiments
    void loadKnowledge();                 // -> m_knowledge (the local base)
    void computeInsights();               // the plain-stats co-occurrence pass -> m_insights

    // --- LLM boundary (the methods that talk to a model) ---
    void runInterpretation(int entryId, const QString &rawText, const QString &date);
    void persistInterpretation(int entryId, const QJsonObject &parsed);
    QString buildReflectionContext() const;   // the plain-text digest handed to ask()

    // --- knowledge growth 1+2->3 (the ONLY code that reaches the public internet) ---
    void processEnrichQueue();                             // sequential, one topic at a time
    void fetchEuropePmc(const QString &topic, const QString &query);   // step 2 (real sources)
    void groundAndStore(const QString &topic, const QJsonArray &citations);  // step 1 fused w/ 2
    void storeKnowledge(const QString &topic, const QJsonObject &item, const QString &origin,
                        const QString &sourceUrl);
    // Maps a habit day-tag onto a generic, personal-data-free science query. Empty = not a
    // topic we look up. This is where "cross with my habits" lives: habits pick the topics.
    static QString topicQueryForTag(const QString &tag);

    // --- enrichment (local, no model) ---
    QVariantList activitiesForDate(const QString &date) const;

    // --- stats helpers ---
    // Per-day set of normalized tags derived from events + observations, for co-occurrence.
    QMap<QDate, QSet<QString>> dayTags() const;

    QString apiKey() const;
    QString normalizeSlug(const QString &s) const;

    void setInterpreting(bool v);
    void setAsking(bool v);
    void setEnriching(bool v);
    void setEnrichStatus(const QString &s);
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
    QVariantList m_knowledge;
    bool m_interpreting = false;
    bool m_asking = false;
    bool m_enriching = false;
    QString m_enrichStatus;
    QString m_lastError;

    // deepenScience() queue: pending [topic, query] pairs, processed one at a time.
    QList<QPair<QString, QString>> m_enrichQueue;
};
