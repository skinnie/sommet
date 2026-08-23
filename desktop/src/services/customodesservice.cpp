#include "customodesservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QUrlQuery>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

CustomModesService::CustomModesService(QObject *parent) : QObject(parent)
{
}

QUrl CustomModesService::backendUrl(const QString &path)
{
    return QUrl(kBackendBase + path);
}

void CustomModesService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void CustomModesService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

void CustomModesService::setWritingMode(const QString &mode)
{
    if (m_writingMode == mode)
        return;
    m_writingMode = mode;
    emit writingModeChanged();
}

void CustomModesService::refresh()
{
    setLoading(true);
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/customodes"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_ok = (reply->error() == QNetworkReply::NoError) && obj.value(QStringLiteral("ok")).toBool();

        if (!m_ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/customodes: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/customodes: %1")
                    .arg(obj.value(QStringLiteral("error")).toString(
                        obj.value(QStringLiteral("stderr")).toString())));
            emit modesChanged();
            return;
        }

        QVariantList modes;
        for (const auto &m : obj.value(QStringLiteral("exerciseModes")).toArray()) {
            const auto mode = m.toObject();
            QVariantMap row;
            row[QStringLiteral("name")] = mode.value(QStringLiteral("name")).toString();
            row[QStringLiteral("activityId")] = mode.value(QStringLiteral("activityId")).toInt();
            row[QStringLiteral("appCount")] = mode.value(QStringLiteral("appCount")).toInt();
            // Whether a multisport mode uses this one as a leg. SuuntoLink refuses to delete
            // such a mode ("You can't delete this sport mode because it's used in one of your
            // multisport modes") - deleting it would leave the multisport pointing at
            // nothing.
            row[QStringLiteral("usedByMultisport")] =
                mode.value(QStringLiteral("usedByMultisport")).toBool();
            // Whether this mode has a menu entry of its own on the watch. The factory
            // "Transition" has none - which is why the list can be one longer than the
            // "N/10 used" beside it, and the card says so rather than leaving it a puzzle.
            row[QStringLiteral("inWatchMenu")] =
                mode.value(QStringLiteral("inWatchMenu")).toBool();
            row[QStringLiteral("useHw")] = mode.value(QStringLiteral("useHw")).toInt();
            row[QStringLiteral("autolap")] = mode.value(QStringLiteral("autolap")).toInt();
            row[QStringLiteral("hrHigh")] = mode.value(QStringLiteral("hrHigh")).toInt();
            row[QStringLiteral("hrLow")] = mode.value(QStringLiteral("hrLow")).toInt();
            row[QStringLiteral("hrLimitsUse")] = mode.value(QStringLiteral("hrLimitsUse")).toInt();
            row[QStringLiteral("recordingInterval")] =
                mode.value(QStringLiteral("recordingInterval")).toInt();
            // The Interval Timer object ({enabled, type, high, low, repetitions}) passed
            // straight through - SportModesPage binds its whole section to mode.intervalTimer.
            row[QStringLiteral("intervalTimer")] =
                mode.value(QStringLiteral("intervalTimer")).toObject().toVariantMap();

            QVariantList displays;
            for (const auto &d : mode.value(QStringLiteral("displays")).toArray()) {
                const auto disp = d.toObject();
                QVariantMap dispRow;
                dispRow[QStringLiteral("index")] = disp.value(QStringLiteral("index")).toInt();
                dispRow[QStringLiteral("template")] = disp.value(QStringLiteral("template")).toString();
                dispRow[QStringLiteral("templateId")] = disp.value(QStringLiteral("templateId")).toInt();
                dispRow[QStringLiteral("templateLabel")] = disp.value(QStringLiteral("templateLabel")).toString();
                dispRow[QStringLiteral("isBuiltIn")] = disp.value(QStringLiteral("isBuiltIn")).toBool();
                // Real bug, found 2026-08-09 ("screen unidentified where it should show screen
                // number"): this dispRow was never given screenNumber/isBuiltIn at all, even
                // though custom_modes.py's own _displays_to_json() already computes both - QML
                // read undefined for both, so every screen showed "Screen undefined" and the
                // built-in system-tail screens (see _GPS_SYSTEM_TAIL/_NON_GPS_SYSTEM_TAIL) got
                // counted as real ones in the "Displays (N/8)" total. screenNumber is real JSON
                // null for built-ins - toInt() on that would silently become 0, so this stays
                // an unset QVariant instead, same as the Python side's None.
                const auto screenNumberValue = disp.value(QStringLiteral("screenNumber"));
                dispRow[QStringLiteral("screenNumber")] =
                    screenNumberValue.isNull() ? QVariant() : QVariant(screenNumberValue.toInt());

                QVariantList fields;
                int fieldIdx = 0;
                for (const auto &f : disp.value(QStringLiteral("fields")).toArray()) {
                    const auto field = f.toObject();
                    QVariantMap fieldRow;
                    fieldRow[QStringLiteral("field")] = fieldIdx++;
                    fieldRow[QStringLiteral("indexName")] = field.value(QStringLiteral("indexName")).toString();
                    fieldRow[QStringLiteral("type")] = field.value(QStringLiteral("type")).toInt();
                    fieldRow[QStringLiteral("typeLabel")] = field.value(QStringLiteral("typeLabel")).toString();
                    // Real bug, found 2026-08-11: these three were never copied, so the QML
                    // read undefined for all of them. A row is named Top/Center/Bottom rather
                    // than numbered, and it can hold SEVERAL values that the watch steps
                    // through - custom_modes.py has reported `rowLabel`, `values` and
                    // `isMultiValue` since the item-11 fix, but they stopped here. Without
                    // them a multi-value row fell back to its own typeLabel, which for such a
                    // row is the placeholder "Shortcut" - so the page showed one meaningless
                    // entry instead of the real values, and the row editor had no current
                    // selection to pre-tick.
                    fieldRow[QStringLiteral("rowLabel")] = field.value(QStringLiteral("rowLabel")).toString();
                    fieldRow[QStringLiteral("isMultiValue")] = field.value(QStringLiteral("isMultiValue")).toBool();
                    QVariantList values;
                    for (const auto &v : field.value(QStringLiteral("values")).toArray()) {
                        const auto value = v.toObject();
                        QVariantMap valueRow;
                        valueRow[QStringLiteral("type")] = value.value(QStringLiteral("type")).toInt();
                        valueRow[QStringLiteral("label")] = value.value(QStringLiteral("label")).toString();
                        values.append(valueRow);
                    }
                    fieldRow[QStringLiteral("values")] = values;
                    fields.append(fieldRow);
                }
                dispRow[QStringLiteral("fields")] = fields;
                displays.append(dispRow);
            }
            row[QStringLiteral("displays")] = displays;

            modes.append(row);
        }
        m_modes = modes;

        QVariantList multisport;
        for (const auto &m : obj.value(QStringLiteral("sportModes")).toArray()) {
            const auto slot = m.toObject();
            if (!slot.value(QStringLiteral("isMultisport")).toBool())
                continue;               // an ordinary sport mode is just a pointer to a mode
            QVariantMap row;
            row[QStringLiteral("name")] = slot.value(QStringLiteral("name")).toString();
            row[QStringLiteral("activityId")] = slot.value(QStringLiteral("activityId")).toInt();
            QStringList legs;
            for (const auto &n : slot.value(QStringLiteral("legNames")).toArray())
                legs.append(n.toString());
            row[QStringLiteral("legNames")] = legs;
            multisport.append(row);
        }
        m_multisportModes = multisport;
        // Every SPORT_MODES entry costs one of the watch's slots, singles and combos
        // alike - SuuntoLink's own countSportModes(). See this class's header.
        m_sportModesUsed = obj.value(QStringLiteral("sportModes")).toArray().size();

        setLastError(QString());
        emit modesChanged();
    });
}

void CustomModesService::refreshFieldTypes()
{
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(backendUrl(QStringLiteral("/api/customodes/field-types"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool())
            return;  // non-fatal - a UI picker just has nothing to show without it

        QVariantList types;
        for (const auto &t : obj.value(QStringLiteral("fieldTypes")).toArray()) {
            const auto entry = t.toObject();
            QVariantMap row;
            row[QStringLiteral("value")] = entry.value(QStringLiteral("value")).toInt();
            row[QStringLiteral("name")] = entry.value(QStringLiteral("name")).toString();
            row[QStringLiteral("label")] = entry.value(QStringLiteral("label")).toString();
            types.append(row);
        }
        m_fieldTypes = types;
        emit fieldTypesChanged();
    });
}

void CustomModesService::refreshRowMenu(int activityId, int template_, const QString &row)
{
    // Static-file lookup on the backend, no watch involved - safe to call on every dialog
    // open. The menu depends on all three arguments: SuuntoLink offers a swimming mode
    // different values from a running one, a graph's row different values from a 3-field
    // one, and the bottom row is the only one that takes several.
    QUrl url = backendUrl(QStringLiteral("/api/customodes/row-menu"));
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("activity"), QString::number(activityId));
    query.addQueryItem(QStringLiteral("template"), QString::number(template_));
    query.addQueryItem(QStringLiteral("row"), row);
    url.setQuery(query);

    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError
            || !obj.value(QStringLiteral("ok")).toBool()) {
            m_rowMenu.clear();
            m_rowMenuMultiValue = false;
            m_rowMenuMaxValues = 1;
            emit rowMenuChanged();
            return;
        }

        QVariantList categories;
        for (const auto &c : obj.value(QStringLiteral("categories")).toArray()) {
            const auto category = c.toObject();
            QVariantList values;
            for (const auto &v : category.value(QStringLiteral("values")).toArray()) {
                const auto value = v.toObject();
                QVariantMap entry;
                entry[QStringLiteral("fieldId")] = value.value(QStringLiteral("fieldId")).toInt();
                entry[QStringLiteral("label")] = value.value(QStringLiteral("label")).toString();
                entry[QStringLiteral("name")] = value.value(QStringLiteral("name")).toString();
                values.append(entry);
            }
            QVariantMap group;
            group[QStringLiteral("label")] = category.value(QStringLiteral("label")).toString();
            group[QStringLiteral("values")] = values;
            categories.append(group);
        }
        m_rowMenu = categories;
        m_rowMenuMultiValue = obj.value(QStringLiteral("multiValue")).toBool();
        m_rowMenuMaxValues = obj.value(QStringLiteral("maxValues")).toInt(1);
        emit rowMenuChanged();
    });
}

void CustomModesService::refreshCapabilities(const QString &variant)
{
    if (variant.isEmpty())
        return;
    QUrl url = backendUrl(QStringLiteral("/api/customodes/capabilities"));
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("variant"), variant);
    url.setQuery(query);

    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError
            || !obj.value(QStringLiteral("ok")).toBool())
            return;                     // keep whatever defaults the UI already has
        m_capabilities = obj.toVariantMap();
        emit capabilitiesChanged();
    });
}

void CustomModesService::refreshActivities()
{
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(backendUrl(QStringLiteral("/api/customodes/activities"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError
            || !obj.value(QStringLiteral("ok")).toBool())
            return;  // non-fatal - the activity picker just has nothing to offer yet

        QVariantList list;
        for (const auto &a : obj.value(QStringLiteral("activities")).toArray()) {
            const auto entry = a.toObject();
            QVariantMap row;
            row[QStringLiteral("id")] = entry.value(QStringLiteral("id")).toInt();
            row[QStringLiteral("name")] = entry.value(QStringLiteral("name")).toString();
            row[QStringLiteral("color")] = entry.value(QStringLiteral("color")).toString();
            row[QStringLiteral("isMultisport")] =
                entry.value(QStringLiteral("isMultisport")).toBool();
            list.append(row);
        }
        m_activities = list;
        m_minMultisportLegs = obj.value(QStringLiteral("minLegs")).toInt(2);
        m_maxMultisportLegs = obj.value(QStringLiteral("maxLegs")).toInt(6);
        emit activitiesChanged();
    });
}

void CustomModesService::postManage(const QString &path, const QJsonObject &body,
                                     const QString &busyName)
{
    setWritingMode(busyName);

    QNetworkRequest request(backendUrl(path));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply =
        m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, path, busyName] {
        reply->deleteLater();
        if (m_writingMode == busyName)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        // A refusal is a real answer with the tool's own wording ("10/10 sport modes
        // already used...", "'Transition' is a leg of Ironman..."), not a fault - it goes
        // to the UI as-is rather than into the error banner, which is for things that
        // broke rather than things the watch does not allow.
        const QString message = writeOk
            ? QString()
            : (reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST %1: %2").arg(path, reply->errorString())
                : obj.value(QStringLiteral("error")).toString(
                    QStringLiteral("the write was not confirmed")));
        emit manageResult(writeOk, message);
        // Re-read either way, so what the UI shows is always the watch's own state rather
        // than what we hoped it would become.
        refresh();
    });
}

void CustomModesService::createMode(const QString &name, int activityId, const QString &variant)
{
    QJsonObject body;
    body[QStringLiteral("action")] = QStringLiteral("create");
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("activityId")] = activityId;
    if (!variant.isEmpty())
        body[QStringLiteral("variant")] = variant;
    body[QStringLiteral("confirm")] = true;
    postManage(QStringLiteral("/api/customodes/mode"), body, name);
}

void CustomModesService::deleteMode(const QString &name)
{
    QJsonObject body;
    body[QStringLiteral("action")] = QStringLiteral("delete");
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("confirm")] = true;
    postManage(QStringLiteral("/api/customodes/mode"), body, name);
}

void CustomModesService::saveMultisport(const QString &action, const QString &name,
                                         int activityId, const QStringList &legs,
                                         const QString &rename)
{
    QJsonArray legArray;
    for (const QString &leg : legs)
        legArray.append(leg);

    QJsonObject body;
    body[QStringLiteral("action")] = action;          // "create" or "edit"
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("activityId")] = activityId;
    body[QStringLiteral("legs")] = legArray;
    if (!rename.isEmpty())
        body[QStringLiteral("rename")] = rename;
    body[QStringLiteral("confirm")] = true;
    postManage(QStringLiteral("/api/customodes/multisport"), body, name);
}

void CustomModesService::deleteMultisport(const QString &name)
{
    QJsonObject body;
    body[QStringLiteral("action")] = QStringLiteral("delete");
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("confirm")] = true;
    postManage(QStringLiteral("/api/customodes/multisport"), body, name);
}

void CustomModesService::renameMode(const QString &fromName, const QString &toName)
{
    setWritingMode(fromName);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/customodes/rename")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("from")] = fromName;
    body[QStringLiteral("to")] = toName;
    body[QStringLiteral("confirm")] = true;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, fromName] {
        reply->deleteLater();
        if (m_writingMode == fromName)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/customodes/rename: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/customodes/rename: %1").arg(
                    obj.value(QStringLiteral("error")).toString(QStringLiteral("write not confirmed"))));
        } else {
            setLastError(QString());
        }
        refresh();
    });
}

void CustomModesService::writeField(const QString &mode, const QVariantMap &fields)
{
    setWritingMode(mode);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/customodes/field")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("fields")] = QJsonObject::fromVariantMap(fields);
    body[QStringLiteral("confirm")] = true;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, mode] {
        reply->deleteLater();
        if (m_writingMode == mode)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/customodes/field: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/customodes/field: %1").arg(
                    obj.value(QStringLiteral("error")).toString(QStringLiteral("write not confirmed"))));
        } else {
            setLastError(QString());
        }
        refresh();
    });
}

void CustomModesService::setIntervalTimer(const QString &mode, bool enabled, const QString &type,
                                          int high, int low, int repetitions)
{
    setWritingMode(mode);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/customodes/interval-timer")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("enabled")] = enabled;
    if (enabled) {
        body[QStringLiteral("type")] = type;
        body[QStringLiteral("high")] = high;
        body[QStringLiteral("low")] = low;
    }
    body[QStringLiteral("repetitions")] = repetitions;
    body[QStringLiteral("confirm")] = true;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, mode] {
        reply->deleteLater();
        if (m_writingMode == mode)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/customodes/interval-timer: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/customodes/interval-timer: %1").arg(
                    obj.value(QStringLiteral("error")).toString(QStringLiteral("write not confirmed"))));
        } else {
            setLastError(QString());
        }
        refresh();
    });
}

void CustomModesService::applyDisplayEdits(const QString &mode, const QVariantList &edits)
{
    if (edits.isEmpty())
        return;
    setWritingMode(mode);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/customodes/displays")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("edits")] = QJsonArray::fromVariantList(edits);
    body[QStringLiteral("confirm")] = true;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, mode] {
        reply->deleteLater();
        if (m_writingMode == mode)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/customodes/displays: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/customodes/displays: %1").arg(
                    obj.value(QStringLiteral("error")).toString(QStringLiteral("write not confirmed"))));
        } else {
            setLastError(QString());
        }
        emit displayEditsApplied(writeOk);
        refresh();
    });
}

void CustomModesService::writeDisplayField(const QString &mode, int display, int field,
                                            const QString &newType)
{
    setWritingMode(mode);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/customodes/display-field")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("display")] = display;
    body[QStringLiteral("field")] = field;
    body[QStringLiteral("type")] = newType;
    body[QStringLiteral("confirm")] = true;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, mode] {
        reply->deleteLater();
        if (m_writingMode == mode)
            setWritingMode(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/customodes/display-field: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/customodes/display-field: %1").arg(
                    obj.value(QStringLiteral("error")).toString(QStringLiteral("write not confirmed"))));
        } else {
            setLastError(QString());
        }
        refresh();
    });
}
