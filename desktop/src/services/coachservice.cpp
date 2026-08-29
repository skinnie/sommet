#include "coachservice.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QUrl>
#include <QVector>
#include <algorithm>
#include <cmath>

namespace {
// Same maths as coach/src/coach.ts and the Fresh Today / Ride Coach mockups - k=42 for
// Fitness, k=7 for Fatigue, the standard exponentially-weighted Performance-Management
// formulation. Kept as free functions (not methods) so there's exactly one place this can
// drift from the TypeScript original.
double alphaCtl() { return 1.0 - std::exp(-1.0 / 42.0); }
double alphaAtl() { return 1.0 - std::exp(-1.0 / 7.0); }
}

CoachService::CoachService(QObject *parent) : QObject(parent)
{
    m_settings.beginGroup(QStringLiteral("coach"));
    m_chatBackend = m_settings.value(QStringLiteral("chatBackend"), QStringLiteral("canned")).toString();
    m_catalogueSource = m_settings.value(QStringLiteral("catalogueSource"), QStringLiteral("sample")).toString();
    m_systmMcpUrl = m_settings.value(QStringLiteral("systmMcpUrl")).toString();
    m_settings.endGroup();

    openActivitiesDb();

    // Bundled catalogue, read once via Qt's compiled-in resource system (see CMakeLists.txt
    // RESOURCES) - not a filesystem path, so it works the same in a packaged build as it
    // does here.
    QFile f(QStringLiteral(":/qt/qml/AmbitApp/assets/coach/systm-sample.json"));
    if (f.open(QIODevice::ReadOnly)) {
        const auto doc = QJsonDocument::fromJson(f.readAll());
        for (const auto &v : doc.array()) {
            const auto o = v.toObject();
            m_sampleCatalogue.append({o.value(QStringLiteral("name")).toString(),
                                       o.value(QStringLiteral("tss")).toDouble(),
                                       o.value(QStringLiteral("if")).toDouble(),
                                       int(o.value(QStringLiteral("dur")).toDouble())});
        }
    }

    computeReadiness();
    // Opening line, same as the mockup's apply('rested') seeding the transcript on load.
    appendBubble(QStringLiteral("coach"),
                 m_readiness.value(QStringLiteral("sentence")).toString(), pickWorkouts(
                     intensityForLight(m_readiness.value(QStringLiteral("light")).toString()), 90));

    if (m_catalogueSource == QStringLiteral("live") && !m_systmMcpUrl.isEmpty()) {
        refreshCatalogueLive([]() {});
    }
}

void CoachService::openActivitiesDb()
{
    // Named connection distinct from ActivityService's own "activities" connection - same
    // file, read-only use here, but Qt's addDatabase() silently steals a connection name
    // reused across classes (ActivityService.h's own header comment already documents this
    // exact trap).
    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("coach_activities"));
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    m_db.setDatabaseName(dir + QStringLiteral("/activities.db"));
    // Deliberately NOT creating the table here - ActivityService owns that. If it hasn't
    // run yet (fresh install, no watch ever synced), the SELECT below just finds no table
    // and computeReadiness() falls back to its own "no history yet" default.
    m_db.open();
}

void CoachService::computeReadiness()
{
    const QDate today = QDate::currentDate();
    const QDate doneSince = today.addDays(-7);   // "recently done" window for pickWorkouts()
    m_recentDone.clear();

    QMap<QDate, double> loadByDay;   // day -> minutes trained that day (the load proxy)
    if (m_db.isOpen()) {
        QSqlQuery q(QStringLiteral(
            "SELECT start_time, duration_s, name FROM activities "
            "WHERE start_time IS NOT NULL AND start_time != ''"), m_db);
        while (q.next()) {
            const QDateTime dt = QDateTime::fromString(q.value(0).toString(), Qt::ISODate);
            if (!dt.isValid()) continue;
            const double minutes = q.value(1).toDouble() / 60.0;
            loadByDay[dt.date()] += minutes;
            // Anything trained in the last 7 days is a candidate for exclusion from picks.
            // Name-matching is best-effort: a ride recorded on the watch carries its sport-mode
            // name, not the SYSTM session name, so this only fires when the two genuinely match
            // (e.g. a workout named the same way) — but it never wrongly excludes.
            if (dt.date() >= doneSince) {
                const QString norm = normalizeName(q.value(2).toString());
                if (!norm.isEmpty()) m_recentDone.insert(norm);
            }
        }
    }

    QDate start = today.addDays(-119);   // cap history depth - bounded, still plenty for a 42d ramp
    if (!loadByDay.isEmpty()) {
        const QDate earliest = loadByDay.firstKey();
        if (earliest > start) start = earliest;
    } else {
        start = today;   // no history at all - a single, flat "day"
    }

    QVector<QDate> days;
    QVector<double> ctlArr, atlArr, freshArr;
    double ctl = 0.0, atl = 0.0;
    const double ac = alphaCtl(), aa = alphaAtl();
    for (QDate d = start; d <= today; d = d.addDays(1)) {
        days.append(d);
        freshArr.append(ctl - atl);            // freshness AS OF THE START of this day
        const double load = loadByDay.value(d, 0.0);
        ctl = ctl * (1 - ac) + load * ac;
        atl = atl * (1 - aa) + load * aa;
        ctlArr.append(ctl);
        atlArr.append(atl);
    }

    const int n = days.size();
    const double finalCtl = ctlArr.last(), finalAtl = atlArr.last(), finalTsb = freshArr.last();

    double rampPerWeek = 0.0;
    for (int d = n - 1; d >= qMax(7, n - 28); --d)
        rampPerWeek = qMax(rampPerWeek, ctlArr[d] - ctlArr[d - 7]);

    QString light = finalTsb > -10 ? QStringLiteral("green")
                  : finalTsb > -25 ? QStringLiteral("yellow") : QStringLiteral("red");
    if (light == QStringLiteral("green") && rampPerWeek > 7) light = QStringLiteral("tempered");

    QString sentence;
    if (light == QStringLiteral("green"))
        sentence = QStringLiteral("You're fresh today — fully rested. Good day for something hard if you've got it planned.");
    else if (light == QStringLiteral("tempered"))
        sentence = QStringLiteral("You're fresh, but your fitness has climbed fast this month. Good day to train — ease into the hard part rather than going in cold.");
    else if (light == QStringLiteral("yellow"))
        sentence = QStringLiteral("You're carrying some fatigue. Nothing alarming — see how the legs feel and adapt if today's plan feels heavy.");
    else
        sentence = QStringLiteral("You're dug in deep right now. Lean toward rest or something gentle today.");

    m_readiness = QVariantMap{
        {QStringLiteral("fitness"), finalCtl}, {QStringLiteral("fatigue"), finalAtl},
        {QStringLiteral("freshness"), finalTsb}, {QStringLiteral("rampPerWeek"), rampPerWeek},
        {QStringLiteral("light"), light}, {QStringLiteral("sentence"), sentence},
        {QStringLiteral("basis"), QVariantList{QStringLiteral("load")}},
    };

    m_chartSeries.clear();
    const int keep = qMin(42, n);
    for (int i = n - keep; i < n; ++i) {
        m_chartSeries.append(QVariantMap{
            {QStringLiteral("date"), days[i].toString(Qt::ISODate)},
            {QStringLiteral("fitness"), ctlArr[i]}, {QStringLiteral("fatigue"), atlArr[i]},
        });
    }

    m_todaysPicks = pickWorkouts(intensityForLight(light), 90);
    emit readinessChanged();
}

QString CoachService::intensityBucket(double intensityFactor)
{
    // Mirrors coach/src/adapters/systmLibrary.ts's bucket() exactly - SYSTM has no single
    // "intensity" field in this offline sample, so it's derived from IF the same way there.
    if (intensityFactor < 0.60) return QStringLiteral("recovery");
    if (intensityFactor < 0.75) return QStringLiteral("endurance");
    if (intensityFactor < 0.82) return QStringLiteral("tempo");
    return QStringLiteral("hard");
}

QString CoachService::intensityForLight(const QString &light)
{
    // Mirrors coach/src/coach.ts's recommend(): red -> recovery, green -> hard, everything
    // else (tempered/yellow) -> endurance.
    if (light == QStringLiteral("red")) return QStringLiteral("recovery");
    if (light == QStringLiteral("green")) return QStringLiteral("hard");
    return QStringLiteral("endurance");
}

QString CoachService::normalizeName(const QString &s)
{
    return s.trimmed().toLower();
}

QVariantList CoachService::pickWorkouts(const QString &intensity, int maxMinutes, int limit) const
{
    const QList<CatalogueRow> &rows =
        (m_catalogueSource == QStringLiteral("live") && !m_liveCatalogue.isEmpty())
            ? m_liveCatalogue : m_sampleCatalogue;

    QList<CatalogueRow> matched;
    for (const auto &r : rows) {
        if (intensityBucket(r.intensityFactor) != intensity) continue;
        if (r.durationSec > maxMinutes * 60) continue;
        matched.append(r);
    }
    std::sort(matched.begin(), matched.end(),
              [](const CatalogueRow &a, const CatalogueRow &b) { return a.tss > b.tss; });

    // Drop anything trained in the last 7 days so the coach stops re-serving a session you've
    // just done (André, 2026-08-28: "recommending the same 2 exercises for 3 days in a row").
    // If that would empty the pool (small catalogue, everything recently done), fall back to
    // the full sorted list rather than showing nothing.
    QList<CatalogueRow> pool;
    for (const auto &r : matched)
        if (!m_recentDone.contains(normalizeName(r.name))) pool.append(r);
    if (pool.isEmpty()) pool = matched;

    // Rotate the starting point by the day, so even when nothing gets excluded the two picks
    // still change from one day to the next instead of always being the top-TSS pair. Stable
    // within a single day (same julian-day offset), varied across days, wraps around the pool.
    QVariantList out;
    const int n = pool.size();
    if (n == 0) return out;
    const int offset = int(QDate::currentDate().toJulianDay() % n);
    for (int k = 0; k < limit && k < n; ++k) {
        const auto &r = pool[(offset + k) % n];
        out.append(QVariantMap{
            {QStringLiteral("name"), r.name}, {QStringLiteral("durationSec"), r.durationSec},
            {QStringLiteral("load"), r.tss}, {QStringLiteral("intensityFactor"), r.intensityFactor},
            {QStringLiteral("intensity"), intensity},
        });
    }
    return out;
}

void CoachService::refreshCatalogueLive(std::function<void()> onDone)
{
    // Honest limitation (see this class's own header comment): joaodrp/wahoo-systm-mcp
    // speaks MCP over stdio, not plain HTTP - this GETs systmMcpUrl expecting a small bridge
    // in front of it that returns the same JSON array shape as the bundled sample
    // ([{name, tss, if, dur, steps}]). Real code; needs that bridge actually running.
    QNetworkRequest req{QUrl(m_systmMcpUrl)};
    auto *reply = m_net.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply, onDone]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            setLastError(QStringLiteral("live catalogue fetch failed: %1").arg(reply->errorString()));
            onDone();
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        QList<CatalogueRow> rows;
        for (const auto &v : doc.array()) {
            const auto o = v.toObject();
            rows.append({o.value(QStringLiteral("name")).toString(),
                         o.value(QStringLiteral("tss")).toDouble(),
                         o.value(QStringLiteral("if")).toDouble(),
                         int(o.value(QStringLiteral("dur")).toDouble())});
        }
        m_liveCatalogue = rows;
        computeReadiness();   // todaysPicks may change with the new catalogue
        onDone();
    });
}

void CoachService::refreshReadiness() { computeReadiness(); }

void CoachService::appendBubble(const QString &role, const QString &text, const QVariantList &cards)
{
    m_messages.append(QVariantMap{{QStringLiteral("role"), role}, {QStringLiteral("text"), text},
                                   {QStringLiteral("cards"), cards}});
    emit messagesChanged();
}

void CoachService::resetConversation()
{
    m_messages.clear();
    appendBubble(QStringLiteral("coach"),
                 m_readiness.value(QStringLiteral("sentence")).toString(),
                 pickWorkouts(intensityForLight(m_readiness.value(QStringLiteral("light")).toString()), 90));
}

void CoachService::sendMessage(const QString &text)
{
    const QString trimmed = text.trimmed();
    if (trimmed.isEmpty()) return;
    appendBubble(QStringLiteral("me"), trimmed);
    setSending(true);
    if (m_chatBackend == QStringLiteral("claude") && anthropicKeySet()) {
        replyClaude(trimmed);
    } else {
        replyCanned(trimmed);
    }
}

void CoachService::replyCanned(const QString &userText)
{
    const QString t = userText.toLower();
    const QString light = m_readiness.value(QStringLiteral("light")).toString();
    const QString intensity = intensityForLight(light);

    if (t.contains(QStringLiteral("short"))) {
        appendBubble(QStringLiteral("coach"), QStringLiteral("Sure — trimmed it down:"),
                     pickWorkouts(intensity, 30));
    } else if (t.contains(QStringLiteral("outdoor"))) {
        appendBubble(QStringLiteral("coach"),
            QStringLiteral("I don't have live weather wired into this reply yet — check the "
                           "forecast on Home, then pick a route from Routes and go."));
    } else if (t.contains(QStringLiteral("watch")) || t.contains(QStringLiteral("send"))) {
        const auto picks = m_todaysPicks;
        const QString name = picks.isEmpty() ? QStringLiteral("today's session")
                                              : picks.first().toMap().value(QStringLiteral("name")).toString();
        appendBubble(QStringLiteral("coach"),
            QStringLiteral("Sending \"%1\" to your watch isn't wired up from this page yet — "
                           "Ambit3Sink exists in the coach scaffold (planned-move + App-Zone "
                           "routes), it just isn't called from here.").arg(name));
    } else {
        appendBubble(QStringLiteral("coach"),
            QStringLiteral("I'm running in canned mode right now (Settings → Coach → Chat "
                           "backend, switch to Claude for a real conversation). I only "
                           "recognise a few keywords here: try \"something shorter\", "
                           "\"outdoor instead\", or \"send it to my watch\"."));
    }
    setSending(false);
}

void CoachService::replyClaude(const QString &userText)
{
    const QVariantMap r = m_readiness;
    QStringList picksLines;
    for (const auto &pv : m_todaysPicks) {
        const auto p = pv.toMap();
        picksLines << QStringLiteral("- %1 (%2 min, TSS %3)")
            .arg(p.value(QStringLiteral("name")).toString())
            .arg(p.value(QStringLiteral("durationSec")).toInt() / 60)
            .arg(p.value(QStringLiteral("load")).toDouble(), 0, 'f', 0);
    }
    // No onboarding UI built yet for the rider profile coach/src/model.ts defines
    // (mainSports/secondarySports/weatherMatters/recoveryMenu) - honestly a placeholder
    // until that screen exists, not fabricated as if it were real user input.
    const QString system = QStringLiteral(
        "You are Sommet's training coach for a Suunto Ambit3 watch user. Be concise and warm, "
        "like a knowledgeable training partner, not a corporate assistant. Never use jargon "
        "(no \"TSS\"/\"CTL\"/\"TSB\" in your reply - translate to plain language).\n\n"
        "Today's readiness: %1 (%2). Fitness %3, Fatigue %4, Freshness %5.\n"
        "Today's catalogue picks for this readiness:\n%6\n\n"
        "Rider profile: not yet collected by this app (no onboarding screen built) - don't "
        "assume specifics about their sport or goals beyond what they tell you in the chat."
    ).arg(r.value(QStringLiteral("light")).toString(), r.value(QStringLiteral("sentence")).toString())
     .arg(r.value(QStringLiteral("fitness")).toDouble(), 0, 'f', 0)
     .arg(r.value(QStringLiteral("fatigue")).toDouble(), 0, 'f', 0)
     .arg(r.value(QStringLiteral("freshness")).toDouble(), 0, 'f', 0)
     .arg(picksLines.isEmpty() ? QStringLiteral("(none match today's duration/intensity)")
                                : picksLines.join(QStringLiteral("\n")));

    QNetworkRequest req{QUrl(QStringLiteral("https://api.anthropic.com/v1/messages"))};
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("x-api-key", m_settings.value(QStringLiteral("coach/anthropicApiKey")).toString().toUtf8());
    req.setRawHeader("anthropic-version", "2023-06-01");

    QJsonObject body;
    body[QStringLiteral("model")] = QStringLiteral("claude-sonnet-5");
    body[QStringLiteral("max_tokens")] = 500;
    body[QStringLiteral("system")] = system;
    QJsonArray messages;
    QJsonObject userMsg;
    userMsg[QStringLiteral("role")] = QStringLiteral("user");
    userMsg[QStringLiteral("content")] = userText;
    messages.append(userMsg);
    body[QStringLiteral("messages")] = messages;

    auto *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            setLastError(QStringLiteral("Claude API error: %1").arg(reply->errorString()));
            appendBubble(QStringLiteral("coach"),
                QStringLiteral("(couldn't reach Claude: %1 — falling back, try again in a moment)")
                    .arg(reply->errorString()));
            setSending(false);
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto content = doc.object().value(QStringLiteral("content")).toArray();
        QString text;
        if (!content.isEmpty()) text = content.first().toObject().value(QStringLiteral("text")).toString();
        if (text.isEmpty()) text = QStringLiteral("(empty reply from Claude)");
        appendBubble(QStringLiteral("coach"), text);
        setSending(false);
    });
}

void CoachService::setChatBackend(const QString &v)
{
    if (m_chatBackend == v) return;
    m_chatBackend = v;
    m_settings.setValue(QStringLiteral("coach/chatBackend"), v);
    emit chatBackendChanged();
}

void CoachService::setCatalogueSource(const QString &v)
{
    if (m_catalogueSource == v) return;
    m_catalogueSource = v;
    m_settings.setValue(QStringLiteral("coach/catalogueSource"), v);
    emit catalogueSourceChanged();
    if (v == QStringLiteral("live") && !m_systmMcpUrl.isEmpty()) {
        refreshCatalogueLive([]() {});
    } else {
        computeReadiness();
    }
}

bool CoachService::anthropicKeySet() const
{
    return !m_settings.value(QStringLiteral("coach/anthropicApiKey")).toString().isEmpty();
}

void CoachService::setAnthropicApiKey(const QString &key)
{
    m_settings.setValue(QStringLiteral("coach/anthropicApiKey"), key.trimmed());
    emit anthropicKeySetChanged();
}

void CoachService::clearAnthropicApiKey()
{
    m_settings.remove(QStringLiteral("coach/anthropicApiKey"));
    emit anthropicKeySetChanged();
}

void CoachService::setSystmMcpUrl(const QString &v)
{
    if (m_systmMcpUrl == v) return;
    m_systmMcpUrl = v;
    m_settings.setValue(QStringLiteral("coach/systmMcpUrl"), v);
    emit systmMcpUrlChanged();
    if (m_catalogueSource == QStringLiteral("live") && !v.isEmpty()) {
        refreshCatalogueLive([]() {});
    }
}

void CoachService::setSending(bool v)
{
    if (m_sending == v) return;
    m_sending = v;
    emit sendingChanged();
}

void CoachService::setLastError(const QString &e)
{
    m_lastError = e;
    emit lastErrorChanged();
}
