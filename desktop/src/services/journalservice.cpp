#include "journalservice.h"
#include "apppaths.h"

#include <QDateTime>
#include <QFile>
#include <QJsonDocument>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSqlError>
#include <QSqlQuery>
#include <QStringList>
#include <QUrl>
#include <QUrlQuery>
#include <QVariant>
#include <algorithm>

// ---------------------------------------------------------------------------
// The candidate relationships the plain-stats pass tests for. This is NOT a
// model and NOT ML — it's a short, explicit list of "lenses" (mostly drawn
// straight from André's own spec examples + the knowledge priors) that we
// count co-occurrence for. Add a row to extend; nothing here is treated as
// established — every hit becomes an OBSERVATION and at most a HYPOTHESIS.
//   antecedent  : day-tag that must be present
//   consequent  : day-tag we check for
//   nextDay     : true -> consequent is checked on the FOLLOWING day
//   observation : "On N of the last M days with {ant} ... {cons}"
//   hypothesis  : the tentative, never-asserted-as-fact chain
struct Lens {
    const char *antecedent;
    const char *consequent;
    bool nextDay;
    const char *antLabel;
    const char *consLabel;
    const char *hypothesis;
};
static const Lens kLenses[] = {
    {"stressed",    "low_energy",           false, "high work stress", "low evening energy",
        "High work stress may contribute to low evening energy."},
    {"late_screen", "poor_morning",         true,  "late screen use",  "a poorer morning",
        "Late screen use in the evening may contribute to poorer mornings."},
    {"exercise",    "good_mood",            false, "exercise",         "a better mood",
        "Easy exercise may contribute to improved mood."},
    {"exercise",    "better_after_exercise",false, "exercise",         "feeling better afterwards",
        "Exercise may lift how André feels, even on harder days."},
    {"late_coffee", "sleep_interruption",   false, "late caffeine",    "interrupted sleep",
        "Caffeine later in the day may contribute to interrupted sleep."},
    {"outdoor",     "good_mood",            false, "outdoor time",     "a better mood",
        "Time outdoors may contribute to a better mood."},
    {"food",        "sleepy_after_eating",  false, "eating",           "feeling sleepy afterwards",
        "Larger or later meals may contribute to post-meal sleepiness."},
    {"low_outdoor", "low_energy",           false, "little outdoor time", "low energy",
        "Days with little time outside may relate to lower energy."},
};

JournalService::JournalService(QObject *parent) : QObject(parent)
{
    openDb();
    ensureSchema();
    seedKnowledge();
    loadEntries();
    loadExperiments();
    loadKnowledge();
    computeInsights();
}

QString JournalService::apiKey() const
{
    // Reuse the SAME key CoachService stores — one Anthropic key for the whole app.
    return m_settings.value(QStringLiteral("coach/anthropicApiKey")).toString();
}

bool JournalService::anthropicKeySet() const
{
    return !apiKey().isEmpty();
}

void JournalService::openDb()
{
    const QString dir = AppPaths::databaseDir();

    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("journal_db"));
    m_db.setDatabaseName(dir + QStringLiteral("/journal.db"));
    if (!m_db.open())
        setLastError(QStringLiteral("Could not open journal.db: %1").arg(m_db.lastError().text()));

    // Read-only view onto ActivityService's own DB — enrichment only, we never write here.
    // (Same trap CoachService documents: a distinct connection name so addDatabase() doesn't
    // steal ActivityService's "activities" connection.)
    m_actDb = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("journal_activities"));
    m_actDb.setDatabaseName(dir + QStringLiteral("/activities.db"));
    m_actDb.open();   // ok to fail silently — no history yet just means no activity enrichment
}

void JournalService::ensureSchema()
{
    if (!m_db.isOpen()) return;
    QSqlQuery q(m_db);
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS journal_entries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, raw_text TEXT NOT NULL, "
        "created_at TEXT NOT NULL, interpreted_at TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER, date TEXT, time TEXT, "
        "category TEXT, value TEXT, source TEXT, confidence REAL, text_ref TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS observations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER, date TEXT, "
        "category TEXT, valence TEXT, text TEXT, text_ref TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS context_data ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, kind TEXT, json TEXT, "
        "UNIQUE(date, kind))"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS knowledge_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, statement TEXT, "
        "evidence_level TEXT, source TEXT, variables TEXT, caveats TEXT, "
        "UNIQUE(topic, statement))"));
    // Provenance for the growing base (1+2->3). Added via ALTER so existing journal.db files
    // gain the columns; the errors on a second run (column exists) are expected and ignored,
    // same pattern ActivityService uses for its own migrations.
    q.exec(QStringLiteral("ALTER TABLE knowledge_items ADD COLUMN origin TEXT"));       // seed|model|pubmed
    q.exec(QStringLiteral("ALTER TABLE knowledge_items ADD COLUMN source_url TEXT"));
    q.exec(QStringLiteral("ALTER TABLE knowledge_items ADD COLUMN fetched_at TEXT"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS hypotheses ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, statement TEXT, chain TEXT, "
        "support_count INTEGER, contradict_count INTEGER, observation_count INTEGER, "
        "confidence REAL, first_observed TEXT, last_observed TEXT, status TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS experiments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, hypothesis_id INTEGER, description TEXT, "
        "start_date TEXT, end_date TEXT, status TEXT, created_at TEXT)"));
}

void JournalService::seedKnowledge()
{
    if (!m_db.isOpen()) return;
    QFile f(QStringLiteral(":/qt/qml/AmbitApp/assets/journal/knowledge.json"));
    if (!f.open(QIODevice::ReadOnly)) return;
    const QJsonObject root = QJsonDocument::fromJson(f.readAll()).object();
    const QJsonArray items = root.value(QStringLiteral("items")).toArray();
    QSqlQuery q(m_db);
    for (const auto &v : items) {
        const QJsonObject o = v.toObject();
        QStringList vars;
        for (const auto &vv : o.value(QStringLiteral("variables")).toArray())
            vars << vv.toString();
        // Idempotent upsert on (topic, statement): re-seed on every start so edits to the
        // bundled JSON land, but never duplicate.
        q.prepare(QStringLiteral(
            "INSERT INTO knowledge_items (topic, statement, evidence_level, source, variables, "
            "caveats, origin) VALUES (?,?,?,?,?,?, 'seed') "
            "ON CONFLICT(topic, statement) DO UPDATE SET "
            "evidence_level=excluded.evidence_level, source=excluded.source, "
            "variables=excluded.variables, caveats=excluded.caveats"));
        q.addBindValue(o.value(QStringLiteral("topic")).toString());
        q.addBindValue(o.value(QStringLiteral("statement")).toString());
        q.addBindValue(o.value(QStringLiteral("evidence_level")).toString());
        q.addBindValue(o.value(QStringLiteral("source")).toString());
        q.addBindValue(vars.join(QStringLiteral(",")));
        q.addBindValue(o.value(QStringLiteral("caveats")).toString());
        q.exec();
    }
}

void JournalService::loadKnowledge()
{
    m_knowledge.clear();
    if (m_db.isOpen()) {
        // Cited (pubmed) first, then model-derived, then seed — and newest fetches on top —
        // so the base visibly "grows real" as André deepens topics.
        QSqlQuery q(QStringLiteral(
            "SELECT id, topic, statement, evidence_level, source, variables, caveats, "
            "COALESCE(origin,'seed'), COALESCE(source_url,''), COALESCE(fetched_at,'') "
            "FROM knowledge_items ORDER BY "
            "CASE COALESCE(origin,'seed') WHEN 'pubmed' THEN 0 WHEN 'model' THEN 1 ELSE 2 END, "
            "fetched_at DESC, id DESC"), m_db);
        while (q.next()) {
            m_knowledge.append(QVariantMap{
                {QStringLiteral("id"), q.value(0).toInt()},
                {QStringLiteral("topic"), q.value(1).toString()},
                {QStringLiteral("statement"), q.value(2).toString()},
                {QStringLiteral("evidenceLevel"), q.value(3).toString()},
                {QStringLiteral("source"), q.value(4).toString()},
                {QStringLiteral("caveats"), q.value(6).toString()},
                {QStringLiteral("origin"), q.value(7).toString()},
                {QStringLiteral("sourceUrl"), q.value(8).toString()},
                {QStringLiteral("fetchedAt"), q.value(9).toString()},
            });
        }
    }
    emit knowledgeChanged();
}

// ---------------------------------------------------------------------------
// Loading / display
// ---------------------------------------------------------------------------

void JournalService::refresh()
{
    loadEntries();
    loadExperiments();
    loadKnowledge();
    computeInsights();
}

QVariantList JournalService::activitiesForDate(const QString &date) const
{
    QVariantList out;
    if (!m_actDb.isOpen()) return out;
    QSqlQuery q(m_actDb);
    // start_time is ISO (e.g. 2026-08-24T07:00:00Z); the date is its first 10 chars.
    q.prepare(QStringLiteral(
        "SELECT name, duration_s, distance_m, sport_type_raw FROM activities "
        "WHERE substr(start_time,1,10) = ? ORDER BY start_time"));
    q.addBindValue(date);
    if (!q.exec()) return out;
    while (q.next()) {
        out.append(QVariantMap{
            {QStringLiteral("name"), q.value(0).toString()},
            {QStringLiteral("durationMin"), q.value(1).toInt() / 60},
            {QStringLiteral("distanceKm"), q.value(2).toDouble() / 1000.0},
            {QStringLiteral("sportTypeRaw"), q.value(3).toInt()},
        });
    }
    return out;
}

void JournalService::loadEntries()
{
    m_entries.clear();
    if (m_db.isOpen()) {
        QSqlQuery q(QStringLiteral(
            "SELECT id, date, raw_text, created_at, interpreted_at FROM journal_entries "
            "ORDER BY date DESC, id DESC"), m_db);
        while (q.next()) {
            const int id = q.value(0).toInt();
            const QString date = q.value(1).toString();

            QVariantList events;
            {
                QSqlQuery e(m_db);
                e.prepare(QStringLiteral(
                    "SELECT time, category, value, confidence FROM events WHERE entry_id=? "
                    "ORDER BY CASE WHEN time IS NULL OR time='' THEN 1 ELSE 0 END, time"));
                e.addBindValue(id);
                e.exec();
                while (e.next())
                    events.append(QVariantMap{
                        {QStringLiteral("time"), e.value(0).toString()},
                        {QStringLiteral("category"), e.value(1).toString()},
                        {QStringLiteral("value"), e.value(2).toString()},
                        {QStringLiteral("confidence"), e.value(3).toDouble()},
                    });
            }
            QVariantList obs;
            {
                QSqlQuery o(m_db);
                o.prepare(QStringLiteral(
                    "SELECT category, valence, text FROM observations WHERE entry_id=?"));
                o.addBindValue(id);
                o.exec();
                while (o.next())
                    obs.append(QVariantMap{
                        {QStringLiteral("category"), o.value(0).toString()},
                        {QStringLiteral("valence"), o.value(1).toString()},
                        {QStringLiteral("text"), o.value(2).toString()},
                    });
            }
            QVariantMap weather;
            {
                QSqlQuery w(m_db);
                w.prepare(QStringLiteral(
                    "SELECT json FROM context_data WHERE date=? AND kind='weather'"));
                w.addBindValue(date);
                w.exec();
                if (w.next())
                    weather = QJsonDocument::fromJson(w.value(0).toString().toUtf8())
                                  .object().toVariantMap();
            }

            m_entries.append(QVariantMap{
                {QStringLiteral("id"), id},
                {QStringLiteral("date"), date},
                {QStringLiteral("rawText"), q.value(2).toString()},
                {QStringLiteral("createdAt"), q.value(3).toString()},
                {QStringLiteral("interpreted"), !q.value(4).toString().isEmpty()},
                {QStringLiteral("events"), events},
                {QStringLiteral("observations"), obs},
                {QStringLiteral("weather"), weather},
                {QStringLiteral("activities"), activitiesForDate(date)},
            });
        }
    }
    emit entriesChanged();
}

void JournalService::loadExperiments()
{
    m_experiments.clear();
    if (m_db.isOpen()) {
        QSqlQuery q(QStringLiteral(
            "SELECT id, description, start_date, end_date, status FROM experiments "
            "WHERE status != 'dismissed' ORDER BY created_at DESC"), m_db);
        while (q.next()) {
            const QDate s = QDate::fromString(q.value(2).toString(), Qt::ISODate);
            const QDate e = QDate::fromString(q.value(3).toString(), Qt::ISODate);
            m_experiments.append(QVariantMap{
                {QStringLiteral("id"), q.value(0).toInt()},
                {QStringLiteral("description"), q.value(1).toString()},
                {QStringLiteral("startDate"), q.value(2).toString()},
                {QStringLiteral("endDate"), q.value(3).toString()},
                {QStringLiteral("status"), q.value(4).toString()},
                {QStringLiteral("days"), (s.isValid() && e.isValid()) ? s.daysTo(e) : 0},
            });
        }
    }
    emit experimentsChanged();
}

// ---------------------------------------------------------------------------
// Saving + interpretation (LLM boundary #1)
// ---------------------------------------------------------------------------

void JournalService::saveEntry(const QString &date, const QString &rawText,
                               const QVariantMap &weather)
{
    if (!m_db.isOpen() || rawText.trimmed().isEmpty()) return;
    const QString d = date.isEmpty() ? QDate::currentDate().toString(Qt::ISODate) : date;

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO journal_entries (date, raw_text, created_at) VALUES (?,?,?)"));
    q.addBindValue(d);
    q.addBindValue(rawText);   // stored EXACTLY as written — never overwritten
    q.addBindValue(QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
    if (!q.exec()) { setLastError(q.lastError().text()); return; }
    const int entryId = q.lastInsertId().toInt();

    // Snapshot the weather for this day, if QML handed us one — WeatherService only knows
    // "now", so this is the only way per-day weather survives for later reflection.
    if (!weather.isEmpty()) {
        QSqlQuery w(m_db);
        w.prepare(QStringLiteral(
            "INSERT INTO context_data (date, kind, json) VALUES (?, 'weather', ?) "
            "ON CONFLICT(date, kind) DO UPDATE SET json=excluded.json"));
        w.addBindValue(d);
        w.addBindValue(QString::fromUtf8(
            QJsonDocument(QJsonObject::fromVariantMap(weather)).toJson(QJsonDocument::Compact)));
        w.exec();
    }

    loadEntries();
    if (anthropicKeySet())
        runInterpretation(entryId, rawText, d);
    else
        computeInsights();   // still refresh insights from whatever is already interpreted
}

void JournalService::interpretEntry(int entryId)
{
    if (!m_db.isOpen() || !anthropicKeySet()) return;
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("SELECT raw_text, date FROM journal_entries WHERE id=?"));
    q.addBindValue(entryId);
    if (q.exec() && q.next())
        runInterpretation(entryId, q.value(0).toString(), q.value(1).toString());
}

void JournalService::runInterpretation(int entryId, const QString &rawText, const QString &date)
{
    setInterpreting(true);
    setLastError(QString());

    const QString system = QStringLiteral(
        "You extract structured observations from a personal daily journal. You are NOT a "
        "doctor or coach: do NOT diagnose, do NOT judge, do NOT infer causes, do NOT invent "
        "anything the text does not say. Treat every topic — including sex, porn, alcohol — "
        "neutrally, like any other event.\n\n"
        "Return ONLY a JSON object, no prose, no markdown fences, of this exact shape:\n"
        "{\n"
        "  \"events\": [ {\"time\": \"HH:MM\" or null, \"category\": \"<one word>\", "
        "\"value\": \"<short description in the journal's own terms>\", "
        "\"confidence\": 0.0-1.0, \"ref\": \"<the phrase this came from>\"} ],\n"
        "  \"observations\": [ {\"category\": \"<short slug>\", "
        "\"valence\": \"positive\"|\"negative\"|\"neutral\", "
        "\"text\": \"<the feeling in the journal's own words>\", \"ref\": \"<source phrase>\"} ]\n"
        "}\n\n"
        "Prefer these event categories when they fit: wake, sleep, sleep_interruption, coffee, "
        "food, work, meeting, walking, cycling, exercise, social, screen, meditation, "
        "journaling, porn, masturbation, relaxation, outdoor, life_event. Otherwise use a "
        "sensible one-word category.\n"
        "For observations use short slugs like: groggy, tired, low_energy, good_mood, bored, "
        "stressed, energetic, better_after_exercise, sleepy_after_eating, poor_morning.\n"
        "Do NOT turn feelings into fake numbers (never 'fatigue = 9/10'). Keep the qualitative "
        "wording. Omit anything not actually in the text. If a time is only approximate, still "
        "give your best HH:MM; if truly none, use null."
    );

    QNetworkRequest req{QUrl(QStringLiteral("https://api.anthropic.com/v1/messages"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("x-api-key", apiKey().toUtf8());
    req.setRawHeader("anthropic-version", "2023-06-01");

    QJsonObject body;
    body[QStringLiteral("model")] = QStringLiteral("claude-sonnet-5");
    body[QStringLiteral("max_tokens")] = 1500;
    body[QStringLiteral("system")] = system;
    QJsonArray messages;
    QJsonObject userMsg;
    userMsg[QStringLiteral("role")] = QStringLiteral("user");
    userMsg[QStringLiteral("content")] = QStringLiteral("Journal for %1:\n\n%2").arg(date, rawText);
    messages.append(userMsg);
    body[QStringLiteral("messages")] = messages;

    auto *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, entryId]() {
        reply->deleteLater();
        setInterpreting(false);
        if (reply->error() != QNetworkReply::NoError) {
            setLastError(QStringLiteral("Interpretation failed: %1").arg(reply->errorString()));
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto content = doc.object().value(QStringLiteral("content")).toArray();
        QString text;
        if (!content.isEmpty())
            text = content.first().toObject().value(QStringLiteral("text")).toString();

        // Be tolerant of a fenced ```json block if the model adds one anyway.
        text = text.trimmed();
        if (text.startsWith(QStringLiteral("```"))) {
            const int nl = text.indexOf(QLatin1Char('\n'));
            if (nl >= 0) text = text.mid(nl + 1);
            if (text.endsWith(QStringLiteral("```"))) text.chop(3);
        }
        const int lb = text.indexOf(QLatin1Char('{'));
        const int rb = text.lastIndexOf(QLatin1Char('}'));
        if (lb >= 0 && rb > lb) text = text.mid(lb, rb - lb + 1);

        const QJsonObject parsed = QJsonDocument::fromJson(text.toUtf8()).object();
        if (parsed.isEmpty()) {
            setLastError(QStringLiteral("Could not read the extracted data (empty/invalid)."));
            return;
        }
        persistInterpretation(entryId, parsed);
        loadEntries();
        computeInsights();
    });
}

void JournalService::persistInterpretation(int entryId, const QJsonObject &parsed)
{
    if (!m_db.isOpen()) return;
    QString date;
    {
        QSqlQuery d(m_db);
        d.prepare(QStringLiteral("SELECT date FROM journal_entries WHERE id=?"));
        d.addBindValue(entryId);
        if (d.exec() && d.next()) date = d.value(0).toString();
    }

    QSqlQuery del(m_db);
    del.prepare(QStringLiteral("DELETE FROM events WHERE entry_id=?"));
    del.addBindValue(entryId); del.exec();
    del.prepare(QStringLiteral("DELETE FROM observations WHERE entry_id=?"));
    del.addBindValue(entryId); del.exec();

    for (const auto &v : parsed.value(QStringLiteral("events")).toArray()) {
        const QJsonObject o = v.toObject();
        QSqlQuery e(m_db);
        e.prepare(QStringLiteral(
            "INSERT INTO events (entry_id, date, time, category, value, source, confidence, text_ref) "
            "VALUES (?,?,?,?,?,'journal',?,?)"));
        e.addBindValue(entryId);
        e.addBindValue(date);
        e.addBindValue(o.value(QStringLiteral("time")).isNull()
                           ? QString() : o.value(QStringLiteral("time")).toString());
        e.addBindValue(normalizeSlug(o.value(QStringLiteral("category")).toString()));
        e.addBindValue(o.value(QStringLiteral("value")).toString());
        e.addBindValue(o.value(QStringLiteral("confidence")).toDouble());
        e.addBindValue(o.value(QStringLiteral("ref")).toString());
        e.exec();
    }
    for (const auto &v : parsed.value(QStringLiteral("observations")).toArray()) {
        const QJsonObject o = v.toObject();
        QSqlQuery ob(m_db);
        ob.prepare(QStringLiteral(
            "INSERT INTO observations (entry_id, date, category, valence, text, text_ref) "
            "VALUES (?,?,?,?,?,?)"));
        ob.addBindValue(entryId);
        ob.addBindValue(date);
        ob.addBindValue(normalizeSlug(o.value(QStringLiteral("category")).toString()));
        ob.addBindValue(o.value(QStringLiteral("valence")).toString());
        ob.addBindValue(o.value(QStringLiteral("text")).toString());
        ob.addBindValue(o.value(QStringLiteral("ref")).toString());
        ob.exec();
    }

    QSqlQuery mark(m_db);
    mark.prepare(QStringLiteral("UPDATE journal_entries SET interpreted_at=? WHERE id=?"));
    mark.addBindValue(QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
    mark.addBindValue(entryId);
    mark.exec();
}

QString JournalService::normalizeSlug(const QString &s) const
{
    QString out = s.trimmed().toLower();
    out.replace(QLatin1Char(' '), QLatin1Char('_'));
    out.replace(QLatin1Char('-'), QLatin1Char('_'));
    return out;
}

void JournalService::deleteEntry(int entryId)
{
    if (!m_db.isOpen()) return;
    for (const char *t : {"events", "observations"}) {
        QSqlQuery q(m_db);
        q.prepare(QStringLiteral("DELETE FROM %1 WHERE entry_id=?").arg(QString::fromLatin1(t)));
        q.addBindValue(entryId); q.exec();
    }
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("DELETE FROM journal_entries WHERE id=?"));
    q.addBindValue(entryId); q.exec();
    loadEntries();
    computeInsights();
}

// ---------------------------------------------------------------------------
// Reflection — plain statistics (NOT ML)
// ---------------------------------------------------------------------------

QMap<QDate, QSet<QString>> JournalService::dayTags() const
{
    QMap<QDate, QSet<QString>> tags;
    QSet<QString> outdoorDays;   // to derive "low_outdoor" as the complement over active days
    if (!m_db.isOpen()) return tags;

    // Which days we have ANY interpreted content for — the universe for "low_outdoor".
    QSet<QDate> knownDays;

    QSqlQuery ev(QStringLiteral("SELECT date, time, category FROM events"), m_db);
    while (ev.next()) {
        const QDate d = QDate::fromString(ev.value(0).toString(), Qt::ISODate);
        if (!d.isValid()) continue;
        knownDays.insert(d);
        const QString cat = ev.value(2).toString();
        const QString time = ev.value(1).toString();
        int hour = -1;
        if (time.length() >= 2) hour = time.left(2).toInt();

        if (cat == QStringLiteral("coffee")) {
            tags[d].insert(QStringLiteral("coffee"));
            if (hour >= 15) tags[d].insert(QStringLiteral("late_coffee"));
        } else if (cat == QStringLiteral("screen")) {
            tags[d].insert(QStringLiteral("screen"));
            if (hour < 0 || hour >= 21 || hour < 4) tags[d].insert(QStringLiteral("late_screen"));
        } else if (cat == QStringLiteral("cycling") || cat == QStringLiteral("walking")
                   || cat == QStringLiteral("exercise")) {
            tags[d].insert(QStringLiteral("exercise"));
            if (cat != QStringLiteral("exercise")) {
                tags[d].insert(QStringLiteral("outdoor"));
                outdoorDays.insert(d.toString(Qt::ISODate));
            }
        } else if (cat == QStringLiteral("outdoor")) {
            tags[d].insert(QStringLiteral("outdoor"));
            outdoorDays.insert(d.toString(Qt::ISODate));
        } else if (cat == QStringLiteral("work") || cat == QStringLiteral("meeting")) {
            tags[d].insert(QStringLiteral("work"));
        } else if (cat == QStringLiteral("food")) {
            tags[d].insert(QStringLiteral("food"));
        } else if (cat == QStringLiteral("sleep_interruption")) {
            tags[d].insert(QStringLiteral("sleep_interruption"));
        }
    }

    QSqlQuery ob(QStringLiteral("SELECT date, category, valence FROM observations"), m_db);
    while (ob.next()) {
        const QDate d = QDate::fromString(ob.value(0).toString(), Qt::ISODate);
        if (!d.isValid()) continue;
        knownDays.insert(d);
        const QString cat = ob.value(1).toString();
        const QString valence = ob.value(2).toString();
        // Map the free-ish observation slugs onto the canonical day-tags the lenses use.
        if (cat.contains(QStringLiteral("stress")))            tags[d].insert(QStringLiteral("stressed"));
        if (cat.contains(QStringLiteral("bored")))             tags[d].insert(QStringLiteral("bored"));
        if (cat.contains(QStringLiteral("good_mood"))
            || (cat.contains(QStringLiteral("mood")) && valence == QStringLiteral("positive"))
            || cat.contains(QStringLiteral("energetic")))      tags[d].insert(QStringLiteral("good_mood"));
        if (cat.contains(QStringLiteral("tired")) || cat.contains(QStringLiteral("exhaust"))
            || cat.contains(QStringLiteral("low_energy")) || cat.contains(QStringLiteral("dead"))
            || cat.contains(QStringLiteral("sleepy")))         tags[d].insert(QStringLiteral("low_energy"));
        if (cat.contains(QStringLiteral("groggy")) || cat.contains(QStringLiteral("poor_morning")))
            { tags[d].insert(QStringLiteral("groggy")); tags[d].insert(QStringLiteral("poor_morning")); }
        if (cat.contains(QStringLiteral("better_after_exercise"))) tags[d].insert(QStringLiteral("better_after_exercise"));
        if (cat.contains(QStringLiteral("sleepy_after_eating"))
            || (cat.contains(QStringLiteral("sleepy")) && cat.contains(QStringLiteral("eat"))))
            tags[d].insert(QStringLiteral("sleepy_after_eating"));
    }

    // "low_outdoor" = a known day with no outdoor tag. Only meaningful once we have days.
    for (const QDate &d : knownDays)
        if (!outdoorDays.contains(d.toString(Qt::ISODate)))
            tags[d].insert(QStringLiteral("low_outdoor"));

    return tags;
}

void JournalService::computeInsights()
{
    m_insights.clear();
    if (!m_db.isOpen()) { emit insightsChanged(); return; }

    const QMap<QDate, QSet<QString>> tags = dayTags();
    struct Scored { QVariantMap row; double score; };
    QList<Scored> scored;

    for (const Lens &L : kLenses) {
        const QString ant = QLatin1String(L.antecedent);
        const QString cons = QLatin1String(L.consequent);
        int antDays = 0, both = 0;
        QDate first, last;
        for (auto it = tags.constBegin(); it != tags.constEnd(); ++it) {
            if (!it.value().contains(ant)) continue;
            antDays++;
            if (!first.isValid()) first = it.key();
            last = it.key();
            bool consHit = false;
            if (L.nextDay) {
                const auto nxt = tags.constFind(it.key().addDays(1));
                consHit = (nxt != tags.constEnd() && nxt.value().contains(cons));
            } else {
                consHit = it.value().contains(cons);
            }
            if (consHit) both++;
        }
        if (antDays < 3) continue;   // not enough to say anything at all

        const double ratio = double(both) / double(antDays);
        // Confidence: the ratio, gently discounted for small samples (never overstated).
        const double confidence = ratio * (1.0 - 1.0 / double(antDays + 1));

        QString status;
        if (ratio < 0.25 && antDays >= 4)                 status = QStringLiteral("contradicted");
        else if (ratio >= 0.6 && antDays >= 5)            status = QStringLiteral("supported");
        else if (ratio >= 0.5 && both >= 3)               status = QStringLiteral("emerging");
        else if (ratio >= 0.25)                           status = QStringLiteral("inconclusive");
        else                                              status = QStringLiteral("candidate");

        const QString key = ant + QStringLiteral("->") + cons;
        // Upsert the hypothesis row so it accumulates across refreshes.
        QSqlQuery up(m_db);
        up.prepare(QStringLiteral(
            "INSERT INTO hypotheses (key, statement, chain, support_count, contradict_count, "
            "observation_count, confidence, first_observed, last_observed, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET support_count=excluded.support_count, "
            "contradict_count=excluded.contradict_count, observation_count=excluded.observation_count, "
            "confidence=excluded.confidence, last_observed=excluded.last_observed, "
            "status=excluded.status"));
        up.addBindValue(key);
        up.addBindValue(QLatin1String(L.hypothesis));
        up.addBindValue(QStringLiteral("%1 → %2").arg(QLatin1String(L.antLabel), QLatin1String(L.consLabel)));
        up.addBindValue(both);
        up.addBindValue(antDays - both);
        up.addBindValue(antDays);
        up.addBindValue(confidence);
        up.addBindValue(first.toString(Qt::ISODate));
        up.addBindValue(last.toString(Qt::ISODate));
        up.addBindValue(status);
        up.exec();
        const int hid = [&]{
            QSqlQuery s(m_db); s.prepare(QStringLiteral("SELECT id FROM hypotheses WHERE key=?"));
            s.addBindValue(key); s.exec(); return s.next() ? s.value(0).toInt() : -1;
        }();

        // Only surface if it says something (skip pure "candidate" noise and contradicted-with-few).
        if (status == QStringLiteral("candidate")) continue;

        const QString observation = QStringLiteral(
            "On %1 of %2 days with %3, André also reported %4.")
            .arg(both).arg(antDays).arg(QLatin1String(L.antLabel), QLatin1String(L.consLabel));

        QVariantMap row{
            {QStringLiteral("kind"), status == QStringLiteral("contradicted")
                                         ? QStringLiteral("observation") : QStringLiteral("hypothesis")},
            {QStringLiteral("kindLabel"), status == QStringLiteral("contradicted")
                                              ? QStringLiteral("Observation") : QStringLiteral("Hypothesis")},
            {QStringLiteral("title"), observation},
            {QStringLiteral("detail"), status == QStringLiteral("contradicted")
                                           ? QStringLiteral("So far the pattern isn't holding up — worth noting, not acting on.")
                                           : QLatin1String(L.hypothesis)},
            {QStringLiteral("count"), antDays},
            {QStringLiteral("confidence"), confidence},
            {QStringLiteral("status"), status},
            {QStringLiteral("hypothesisId"), hid},
        };
        // Rank: supported > emerging > inconclusive/contradicted, then by sample size.
        double s = confidence + antDays * 0.01;
        if (status == QStringLiteral("supported")) s += 2.0;
        else if (status == QStringLiteral("emerging")) s += 1.0;
        scored.append({row, s});
    }

    std::sort(scored.begin(), scored.end(),
              [](const Scored &a, const Scored &b) { return a.score > b.score; });
    for (int i = 0; i < scored.size() && i < 6; ++i)
        m_insights.append(scored[i].row);

    if (m_insights.isEmpty()) {
        m_insights.append(QVariantMap{
            {QStringLiteral("kind"), QStringLiteral("quiet")},
            {QStringLiteral("kindLabel"), QStringLiteral("")},
            {QStringLiteral("title"), QStringLiteral("Nothing particularly meaningful has emerged yet.")},
            {QStringLiteral("detail"), QStringLiteral(
                "Keep writing — patterns need a handful of days before there's anything worth pointing at. "
                "That's perfectly normal.")},
        });
    }
    emit insightsChanged();
}

// ---------------------------------------------------------------------------
// Knowledge growth: 1 (Claude's own knowledge) + 2 (real Europe PMC sources)
// FUSED into 3 (the local base). The ONLY code here that reaches the public
// internet — and it sends ONLY a generic science topic string, never André's
// journal text or logged habits. His habits only decide WHICH topics to pull.
// ---------------------------------------------------------------------------

// tag -> {human topic label, generic Europe PMC query}. No personal data — these are the
// same phrases anyone researching the topic would type. Add a row to teach a new lens.
struct TopicMap { const char *tag; const char *label; const char *query; };
static const TopicMap kTopics[] = {
    {"late_coffee",         "Afternoon caffeine & sleep", "afternoon caffeine consumption sleep quality"},
    {"coffee",              "Caffeine & sleep",           "caffeine sleep quality dose timing"},
    {"late_screen",         "Evening screens & sleep",    "evening screen light exposure sleep onset latency"},
    {"stressed",            "Stress & fatigue",           "psychological stress perceived fatigue recovery"},
    {"exercise",            "Exercise & mood",            "acute aerobic exercise mood affect"},
    {"outdoor",             "Daylight & mood",            "outdoor daylight exposure mood wellbeing"},
    {"sleepy_after_eating", "Meals & sleepiness",         "postprandial somnolence meal composition"},
    {"sleep_interruption",  "Fragmented sleep",           "nocturnal awakenings sleep fragmentation causes"},
    {"poor_morning",        "Morning grogginess",         "sleep inertia grogginess morning alertness"},
    {"bored",               "Boredom & mood",             "boredom mood motivation state"},
    {"masturbation",        "Sex & sleepiness",           "sexual activity orgasm prolactin sleepiness"},
    {"meditation",          "Mindfulness & stress",       "mindfulness meditation perceived stress"},
    {"journaling",          "Expressive writing",         "expressive writing journaling wellbeing"},
    {"social",              "Social contact & mood",      "social interaction mood wellbeing"},
    {"low_energy",          "Perceived energy",           "perceived energy vitality determinants daily"},
};

QString JournalService::topicQueryForTag(const QString &tag)
{
    for (const auto &t : kTopics)
        if (tag == QLatin1String(t.tag)) return QString::fromLatin1(t.query);
    return QString();
}

QVariantList JournalService::knowledgeTopics() const
{
    // Topics André's own logged habits actually touch, marked with whether the local base
    // already has a deepened (model/pubmed) item for them. This is the menu the UI offers.
    QSet<QString> present;
    const auto tags = dayTags();
    for (auto it = tags.constBegin(); it != tags.constEnd(); ++it)
        present.unite(it.value());

    QSet<QString> haveTopics;
    if (m_db.isOpen()) {
        QSqlQuery q(QStringLiteral(
            "SELECT DISTINCT topic FROM knowledge_items WHERE origin IN ('model','pubmed')"), m_db);
        while (q.next()) haveTopics.insert(q.value(0).toString());
    }

    QVariantList out;
    QSet<QString> seenLabels;
    for (const auto &t : kTopics) {
        if (!present.contains(QLatin1String(t.tag))) continue;
        const QString label = QString::fromLatin1(t.label);
        if (seenLabels.contains(label)) continue;   // coffee + late_coffee etc. can overlap
        seenLabels.insert(label);
        out.append(QVariantMap{
            {QStringLiteral("topic"), label},
            {QStringLiteral("query"), QString::fromLatin1(t.query)},
            {QStringLiteral("have"), haveTopics.contains(label)},
        });
    }
    return out;
}

void JournalService::deepenScience()
{
    if (!anthropicKeySet()) {
        setEnrichStatus(QStringLiteral("Add an Anthropic key (Settings → Coach) to ground and store sources."));
        return;
    }
    m_enrichQueue.clear();
    for (const auto &tv : knowledgeTopics()) {
        const auto t = tv.toMap();
        if (t.value(QStringLiteral("have")).toBool()) continue;   // already deepened
        m_enrichQueue.append({t.value(QStringLiteral("topic")).toString(),
                              t.value(QStringLiteral("query")).toString()});
    }
    if (m_enrichQueue.isEmpty()) {
        setEnrichStatus(QStringLiteral("Everything your habits touch is already in the local base."));
        return;
    }
    setEnriching(true);
    processEnrichQueue();
}

void JournalService::enrichTopic(const QString &topic, const QString &query)
{
    if (!anthropicKeySet()) {
        setEnrichStatus(QStringLiteral("Add an Anthropic key (Settings → Coach) first."));
        return;
    }
    QString q = query;
    if (q.isEmpty()) {
        for (const auto &t : kTopics)
            if (topic == QLatin1String(t.label)) { q = QString::fromLatin1(t.query); break; }
    }
    if (q.isEmpty()) q = topic;
    m_enrichQueue.append({topic, q});
    if (!m_enriching) { setEnriching(true); processEnrichQueue(); }
}

void JournalService::processEnrichQueue()
{
    if (m_enrichQueue.isEmpty()) {
        setEnriching(false);
        setEnrichStatus(QStringLiteral("Done."));
        loadKnowledge();
        return;
    }
    const auto job = m_enrichQueue.first();
    setEnrichStatus(QStringLiteral("Looking up: %1…").arg(job.first));
    fetchEuropePmc(job.first, job.second);
}

void JournalService::fetchEuropePmc(const QString &topic, const QString &query)
{
    // Europe PMC REST — free, no key, real peer-reviewed abstracts + citations. resultType=core
    // includes abstractText so Claude can ground against the actual source, not a title alone.
    QUrl url(QStringLiteral("https://www.ebi.ac.uk/europepmc/webservices/rest/search"));
    QUrlQuery qq;
    qq.addQueryItem(QStringLiteral("query"),
                    query + QStringLiteral(" AND (SRC:MED) AND HAS_ABSTRACT:Y"));
    qq.addQueryItem(QStringLiteral("format"), QStringLiteral("json"));
    qq.addQueryItem(QStringLiteral("pageSize"), QStringLiteral("5"));
    qq.addQueryItem(QStringLiteral("resultType"), QStringLiteral("core"));
    qq.addQueryItem(QStringLiteral("sort"), QStringLiteral("CITED desc"));   // well-cited first
    url.setQuery(qq);

    auto *reply = m_net.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply, topic]() {
        reply->deleteLater();
        QJsonArray citations;
        if (reply->error() == QNetworkReply::NoError) {
            const auto results = QJsonDocument::fromJson(reply->readAll()).object()
                                     .value(QStringLiteral("resultList")).toObject()
                                     .value(QStringLiteral("result")).toArray();
            for (const auto &rv : results) {
                const QJsonObject r = rv.toObject();
                const QString src = r.value(QStringLiteral("source")).toString();
                const QString id = r.value(QStringLiteral("id")).toString();
                const QString doi = r.value(QStringLiteral("doi")).toString();
                QString link = doi.isEmpty()
                    ? QStringLiteral("https://europepmc.org/article/%1/%2").arg(src, id)
                    : QStringLiteral("https://doi.org/%1").arg(doi);
                QString abs = r.value(QStringLiteral("abstractText")).toString();
                if (abs.length() > 900) abs = abs.left(900) + QStringLiteral("…");
                citations.append(QJsonObject{
                    {QStringLiteral("title"), r.value(QStringLiteral("title")).toString()},
                    {QStringLiteral("authors"), r.value(QStringLiteral("authorString")).toString()},
                    {QStringLiteral("year"), r.value(QStringLiteral("pubYear")).toString()},
                    {QStringLiteral("journal"), r.value(QStringLiteral("journalTitle")).toString()},
                    {QStringLiteral("url"), link},
                    {QStringLiteral("abstract"), abs},
                });
            }
        }
        // Even with zero citations (offline / no hits) we still ground with Claude's own
        // knowledge -> origin "model". That's the "1" feeding "3" when "2" isn't available.
        groundAndStore(topic, citations);
    });
}

void JournalService::groundAndStore(const QString &topic, const QJsonArray &citations)
{
    if (!anthropicKeySet()) { m_enrichQueue.removeFirst(); processEnrichQueue(); return; }

    QString abstracts;
    for (int i = 0; i < citations.size(); ++i) {
        const QJsonObject c = citations[i].toObject();
        abstracts += QStringLiteral("[%1] %2 (%3, %4)\n%5\n\n")
            .arg(i).arg(c.value(QStringLiteral("title")).toString(),
                        c.value(QStringLiteral("authors")).toString(),
                        c.value(QStringLiteral("year")).toString(),
                        c.value(QStringLiteral("abstract")).toString());
    }
    if (abstracts.isEmpty()) abstracts = QStringLiteral("(no sources retrieved — use only well-established consensus)");

    const QString system = QStringLiteral(
        "You curate ONE entry for a personal journaling app's local science base. Given a TOPIC "
        "and REAL study abstracts (possibly none), write a single concise, honest consensus "
        "statement a careful clinician would accept — plain language, no hype, no fake precision. "
        "Ground it in the abstracts plus well-established consensus. NEVER invent a citation. "
        "Pick the SINGLE best supporting citation index from the list, or -1 if none genuinely "
        "fits. Grade the evidence honestly. Return ONLY JSON: {\"statement\": \"...\", "
        "\"evidence_level\": \"strong|moderate|preliminary|uncertain\", \"caveats\": \"...\", "
        "\"variables\": [\"day-tags this relates to\"], \"citation_index\": <int>}");

    QNetworkRequest req{QUrl(QStringLiteral("https://api.anthropic.com/v1/messages"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("x-api-key", apiKey().toUtf8());
    req.setRawHeader("anthropic-version", "2023-06-01");

    QJsonObject body;
    body[QStringLiteral("model")] = QStringLiteral("claude-sonnet-5");
    body[QStringLiteral("max_tokens")] = 700;
    body[QStringLiteral("system")] = system;
    QJsonArray messages;
    messages.append(QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
        {QStringLiteral("content"), QStringLiteral("TOPIC: %1\n\nABSTRACTS:\n%2").arg(topic, abstracts)}});
    body[QStringLiteral("messages")] = messages;

    auto *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, topic, citations]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto content = QJsonDocument::fromJson(reply->readAll()).object()
                                     .value(QStringLiteral("content")).toArray();
            QString text = content.isEmpty() ? QString()
                : content.first().toObject().value(QStringLiteral("text")).toString();
            text = text.trimmed();
            const int lb = text.indexOf(QLatin1Char('{')), rb = text.lastIndexOf(QLatin1Char('}'));
            if (lb >= 0 && rb > lb) text = text.mid(lb, rb - lb + 1);
            const QJsonObject item = QJsonDocument::fromJson(text.toUtf8()).object();

            if (!item.isEmpty() && !item.value(QStringLiteral("statement")).toString().isEmpty()) {
                int ci = item.value(QStringLiteral("citation_index")).toInt(-1);
                QString origin = QStringLiteral("model"), url, source;
                if (ci >= 0 && ci < citations.size()) {
                    const QJsonObject c = citations[ci].toObject();
                    origin = QStringLiteral("pubmed");
                    url = c.value(QStringLiteral("url")).toString();
                    source = QStringLiteral("%1 (%2), %3")
                        .arg(c.value(QStringLiteral("authors")).toString(),
                             c.value(QStringLiteral("year")).toString(),
                             c.value(QStringLiteral("journal")).toString());
                } else {
                    source = QStringLiteral("Claude (built-in knowledge, %1)")
                                 .arg(QDate::currentDate().toString(Qt::ISODate));
                }
                // Pack "<url> | <human source>" when cited; storeKnowledge() unpacks it.
                storeKnowledge(topic, item, origin,
                               url.isEmpty() ? source : url + QStringLiteral(" | ") + source);
            }
        }
        m_enrichQueue.removeFirst();
        loadKnowledge();
        processEnrichQueue();
    });
}

void JournalService::storeKnowledge(const QString &topic, const QJsonObject &item,
                                    const QString &origin, const QString &sourceUrl)
{
    if (!m_db.isOpen()) return;
    QStringList vars;
    for (const auto &vv : item.value(QStringLiteral("variables")).toArray())
        vars << vv.toString();

    // One fresh enriched item per topic: drop any prior model/pubmed row for this topic (seed
    // rows are left untouched), then insert. Keeps the base from piling duplicates on re-deepen.
    QSqlQuery del(m_db);
    del.prepare(QStringLiteral(
        "DELETE FROM knowledge_items WHERE topic=? AND origin IN ('model','pubmed')"));
    del.addBindValue(topic);
    del.exec();

    // sourceUrl arrives as "<url> | <human source>" when cited, or just the human source string.
    QString url, human = sourceUrl;
    const int sep = sourceUrl.indexOf(QStringLiteral(" | "));
    if (sep >= 0) { url = sourceUrl.left(sep); human = sourceUrl.mid(sep + 3); }
    else if (sourceUrl.startsWith(QStringLiteral("http"))) { url = sourceUrl; human.clear(); }

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO knowledge_items (topic, statement, evidence_level, source, variables, "
        "caveats, origin, source_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(topic, statement) DO UPDATE SET evidence_level=excluded.evidence_level, "
        "source=excluded.source, variables=excluded.variables, caveats=excluded.caveats, "
        "origin=excluded.origin, source_url=excluded.source_url, fetched_at=excluded.fetched_at"));
    q.addBindValue(topic);
    q.addBindValue(item.value(QStringLiteral("statement")).toString());
    q.addBindValue(item.value(QStringLiteral("evidence_level")).toString());
    q.addBindValue(human);
    q.addBindValue(vars.join(QStringLiteral(",")));
    q.addBindValue(item.value(QStringLiteral("caveats")).toString());
    q.addBindValue(origin);
    q.addBindValue(url);
    q.addBindValue(QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
    q.exec();
}

void JournalService::deleteKnowledge(int knowledgeId)
{
    if (!m_db.isOpen()) return;
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("DELETE FROM knowledge_items WHERE id=?"));
    q.addBindValue(knowledgeId);
    q.exec();
    loadKnowledge();
}

// ---------------------------------------------------------------------------
// Free-form reflection question (LLM boundary #2)
// ---------------------------------------------------------------------------

QString JournalService::buildReflectionContext() const
{
    QStringList lines;

    // Recent raw journals (last few days) — André's own words, first-class.
    lines << QStringLiteral("## RECENT JOURNAL (André's own words)");
    int n = 0;
    for (const auto &ev : m_entries) {
        if (n++ >= 7) break;
        const auto e = ev.toMap();
        QString raw = e.value(QStringLiteral("rawText")).toString();
        if (raw.length() > 600) raw = raw.left(600) + QStringLiteral("…");
        lines << QStringLiteral("[%1] %2").arg(e.value(QStringLiteral("date")).toString(), raw);
    }

    // What the data shows: recent activities from activities.db.
    lines << QStringLiteral("\n## ACTIVITY DATA (from the watch/bike, factual)");
    int an = 0;
    for (const auto &ev : m_entries) {
        if (an >= 10) break;
        const auto e = ev.toMap();
        for (const auto &av : e.value(QStringLiteral("activities")).toList()) {
            const auto a = av.toMap();
            lines << QStringLiteral("[%1] %2 — %3 min, %4 km")
                         .arg(e.value(QStringLiteral("date")).toString(),
                              a.value(QStringLiteral("name")).toString())
                         .arg(a.value(QStringLiteral("durationMin")).toInt())
                         .arg(a.value(QStringLiteral("distanceKm")).toDouble(), 0, 'f', 1);
            an++;
        }
    }
    if (an == 0) lines << QStringLiteral("(no activity data available)");

    // What Sommet has noticed (stats), passed as tentative, never as fact.
    lines << QStringLiteral("\n## PATTERNS SOMMET HAS NOTICED (statistical, tentative)");
    for (const auto &iv : m_insights) {
        const auto i = iv.toMap();
        if (i.value(QStringLiteral("kind")).toString() == QStringLiteral("quiet")) continue;
        lines << QStringLiteral("- %1 [%2]").arg(i.value(QStringLiteral("title")).toString(),
                                                 i.value(QStringLiteral("status")).toString());
    }

    // Relevant scientific priors — matched loosely by the tags present in recent days.
    QSet<QString> present;
    const auto tags = dayTags();
    for (auto it = tags.constBegin(); it != tags.constEnd(); ++it)
        present.unite(it.value());
    lines << QStringLiteral("\n## SCIENTIFIC CONTEXT (general priors, not about André)");
    lines << QStringLiteral("(Prefer these local items; where one carries a citation you may name "
                            "it, e.g. \"per Smith 2021\". Do NOT invent citations.)");
    if (m_db.isOpen()) {
        // Cited (pubmed) items first, then model-derived, then bundled seeds — so the reflection
        // leans on the real, sourced knowledge the base has accumulated (1+2->3).
        QSqlQuery k(QStringLiteral(
            "SELECT statement, evidence_level, variables, caveats, COALESCE(origin,'seed'), "
            "COALESCE(source,''), COALESCE(source_url,'') FROM knowledge_items ORDER BY "
            "CASE COALESCE(origin,'seed') WHEN 'pubmed' THEN 0 WHEN 'model' THEN 1 ELSE 2 END"), m_db);
        int kn = 0;
        while (k.next() && kn < 10) {
            bool relevant = present.isEmpty();
            for (const QString &v : k.value(2).toString().split(QLatin1Char(',')))
                if (present.contains(v.trimmed())) { relevant = true; break; }
            if (!relevant) continue;
            const QString origin = k.value(4).toString();
            QString cite;
            if (origin == QStringLiteral("pubmed"))
                cite = QStringLiteral("; source: %1 %2").arg(k.value(5).toString(), k.value(6).toString());
            else if (origin == QStringLiteral("model"))
                cite = QStringLiteral("; source: general knowledge, uncited");
            lines << QStringLiteral("- %1 (evidence: %2; caveat: %3%4)")
                         .arg(k.value(0).toString(), k.value(1).toString(), k.value(3).toString(), cite);
            kn++;
        }
    }

    return lines.join(QLatin1Char('\n'));
}

void JournalService::ask(const QString &question)
{
    if (question.trimmed().isEmpty()) return;
    appendBubble(QStringLiteral("me"), question);

    if (!anthropicKeySet()) {
        appendBubble(QStringLiteral("sommet"), QStringLiteral(
            "I can store and structure your journal offline, but answering free-form questions "
            "needs an Anthropic API key (Settings → Coach). Meanwhile, the patterns above are "
            "computed locally with no AI."));
        return;
    }

    setAsking(true);
    setLastError(QString());

    const QString system = QStringLiteral(
        "You are Sommet's reflection companion for André. Your ONLY job is to help him notice "
        "connections between how he lives and how he feels — NOT to optimise, score, coach, "
        "diagnose or prescribe. Be concise, warm and honest. Never invent data. Missing data is "
        "fine to admit.\n\n"
        "CRITICAL: keep four things clearly separate, and label them, in this order when each "
        "applies:\n"
        "  • FACT — only what André actually wrote, or what the activity data actually shows.\n"
        "  • OBSERVATION — a pattern across days, stated with the counts (\"on 6 of 9 days…\").\n"
        "  • HYPOTHESIS — a tentative 'may' link; NEVER assert causality as established.\n"
        "  • SCIENTIFIC CONTEXT — general research priors, explicitly not about André specifically. "
        "Prefer the SCIENTIFIC CONTEXT items provided below over your own recall; when one carries "
        "a source, you may name it (e.g. \"per Smith 2021\"). NEVER fabricate a citation, a study "
        "or a statistic.\n\n"
        "No wellness scores, no readiness numbers, no guilt, no daily to-dos. If nothing "
        "meaningful has emerged, say so plainly. You may, at most, gently offer ONE small, "
        "reversible experiment if the evidence genuinely warrants it — as an option, not advice.\n\n"
        "Here is what is known right now:\n\n%1"
    ).arg(buildReflectionContext());

    QNetworkRequest req{QUrl(QStringLiteral("https://api.anthropic.com/v1/messages"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("x-api-key", apiKey().toUtf8());
    req.setRawHeader("anthropic-version", "2023-06-01");

    QJsonObject body;
    body[QStringLiteral("model")] = QStringLiteral("claude-sonnet-5");
    body[QStringLiteral("max_tokens")] = 700;
    body[QStringLiteral("system")] = system;
    QJsonArray messages;
    QJsonObject userMsg;
    userMsg[QStringLiteral("role")] = QStringLiteral("user");
    userMsg[QStringLiteral("content")] = question;
    messages.append(userMsg);
    body[QStringLiteral("messages")] = messages;

    auto *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        setAsking(false);
        if (reply->error() != QNetworkReply::NoError) {
            setLastError(QStringLiteral("Reflection failed: %1").arg(reply->errorString()));
            appendBubble(QStringLiteral("sommet"),
                QStringLiteral("(couldn't reach Claude just now — try again in a moment)"));
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto content = doc.object().value(QStringLiteral("content")).toArray();
        QString text;
        if (!content.isEmpty())
            text = content.first().toObject().value(QStringLiteral("text")).toString();
        if (text.isEmpty()) text = QStringLiteral("(empty reply)");
        appendBubble(QStringLiteral("sommet"), text);
    });
}

void JournalService::resetConversation()
{
    m_messages.clear();
    emit messagesChanged();
}

// ---------------------------------------------------------------------------
// Experiments
// ---------------------------------------------------------------------------

void JournalService::createExperiment(const QString &description, int days, int hypothesisId)
{
    if (!m_db.isOpen() || description.trimmed().isEmpty()) return;
    const QDate start = QDate::currentDate();
    const QDate end = start.addDays(days > 0 ? days : 7);
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO experiments (hypothesis_id, description, start_date, end_date, status, created_at) "
        "VALUES (?,?,?,?, 'active', ?)"));
    q.addBindValue(hypothesisId);
    q.addBindValue(description.trimmed());
    q.addBindValue(start.toString(Qt::ISODate));
    q.addBindValue(end.toString(Qt::ISODate));
    q.addBindValue(QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
    q.exec();
    loadExperiments();
}

void JournalService::updateExperimentStatus(int experimentId, const QString &status)
{
    if (!m_db.isOpen()) return;
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("UPDATE experiments SET status=? WHERE id=?"));
    q.addBindValue(status);
    q.addBindValue(experimentId);
    q.exec();
    loadExperiments();
}

// ---------------------------------------------------------------------------
// setters
// ---------------------------------------------------------------------------

void JournalService::appendBubble(const QString &role, const QString &text)
{
    m_messages.append(QVariantMap{{QStringLiteral("role"), role}, {QStringLiteral("text"), text}});
    emit messagesChanged();
}

void JournalService::setInterpreting(bool v)
{
    if (m_interpreting == v) return;
    m_interpreting = v;
    emit interpretingChanged();
}

void JournalService::setAsking(bool v)
{
    if (m_asking == v) return;
    m_asking = v;
    emit askingChanged();
}

void JournalService::setEnriching(bool v)
{
    if (m_enriching == v) return;
    m_enriching = v;
    emit enrichingChanged();
}

void JournalService::setEnrichStatus(const QString &s)
{
    if (m_enrichStatus == s) return;
    m_enrichStatus = s;
    emit enrichStatusChanged();
}

void JournalService::setLastError(const QString &e)
{
    if (m_lastError == e) return;
    m_lastError = e;
    emit lastErrorChanged();
    if (!e.isEmpty()) qWarning("JournalService: %s", qPrintable(e));
}
