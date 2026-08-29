#include "deviceservice.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkInformation>
#include <QNetworkReply>
#include <QSettings>
#include <QStandardPaths>
#include <QTextStream>
#include <QTimeZone>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

DeviceService::DeviceService(QObject *parent) : QObject(parent)
{
    // Restore the persisted "Ephemeris GPS only" choice on launch.
    m_ephemerisGpsOnly = QSettings().value(QStringLiteral("ephemeris/gpsOnly"), false).toBool();
    // Off by default - real decision, 2026-08-11 (see this property's own header comment).
    m_bleExperimentEnabled =
        QSettings().value(QStringLiteral("experimental/bluetooth"), false).toBool();
    // Off by default - see this property's own header comment. Opt-in write-back of the
    // watch's per-move synced flag.
    m_markSyncedEnabled =
        QSettings().value(QStringLiteral("experimental/markSynced"), false).toBool();
    m_intervalsEnabled =
        QSettings().value(QStringLiteral("experimental/intervals"), false).toBool();
    m_appZoneEnabled =
        QSettings().value(QStringLiteral("experimental/appZone"), false).toBool();
    m_smartSensorEnabled =
        QSettings().value(QStringLiteral("experimental/smartSensor"), false).toBool();
    // On by default since 2026-08-28 (UX audit item 3): the Coach was buried behind an
    // experimental toggle almost nobody found, yet it works offline for the basics (readiness
    // beacon + chat over local history). It now shows for everyone by default; the Settings
    // toggle still lets a user hide it, and an existing install keeps whatever it persisted.
    m_coachEnabled =
        QSettings().value(QStringLiteral("experimental/coach"), true).toBool();
    // Off by default - see this property's own header comment (built blind, never
    // hardware-confirmed).
    m_gpsTrackPodExperimentEnabled =
        QSettings().value(QStringLiteral("experimental/gpsTrackPod"), false).toBool();
    m_suuntoT6ExperimentEnabled =
        QSettings().value(QStringLiteral("experimental/suuntoT6"), false).toBool();
    m_pollTimer.setSingleShot(true);
    connect(&m_pollTimer, &QTimer::timeout, this, &DeviceService::refresh);

    m_heartbeatTimer.setSingleShot(true);
    connect(&m_heartbeatTimer, &QTimer::timeout, this, &DeviceService::refresh);

    m_blePollTimer.setSingleShot(true);
    connect(&m_blePollTimer, &QTimer::timeout, this, &DeviceService::pollBleStatus);

    // Whether we have a route to the internet, from Qt's own reachability backend. Asked
    // rather than probed: the clock and orbit features need this only to decide whether they
    // CAN run, and probing a server on every device poll would put real traffic on the wire
    // for something the OS already knows. If no backend loads (a stripped build, an unusual
    // platform), assume online - the update paths already report honestly when a download
    // fails, so a wrong "yes" costs one failed attempt while a wrong "no" would silently
    // disable a working feature.
    if (QNetworkInformation::loadDefaultBackend() && QNetworkInformation::instance()) {
        auto *info = QNetworkInformation::instance();
        auto apply = [this, info] {
            const bool up = info->reachability() == QNetworkInformation::Reachability::Online;
            if (up == m_online)
                return;
            m_online = up;
            emit onlineChanged();
        };
        connect(info, &QNetworkInformation::reachabilityChanged, this, apply);
        apply();
    } else {
        m_online = true;
    }
}

QUrl DeviceService::backendUrl(const QString &path)
{
    return QUrl(kBackendBase + path);
}

void DeviceService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void DeviceService::logToFile(const QString &line)
{
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir);
    QFile file(dir + QStringLiteral("/ambitapp.log"));
    if (!file.open(QIODevice::Append | QIODevice::Text))
        return;  // real but non-critical - never blocks the actual UI/retry flow on this
    QTextStream out(&file);
    out << QDateTime::currentDateTime().toString(Qt::ISODate) << ' ' << line << '\n';
}

void DeviceService::setLastError(const QString &friendlyMessage, const QString &technicalDetail)
{
    // Found via real testing, 2026-08-07: this used to show Qt's own raw network error text
    // ("Error transferring http://127.0.0.1:8766/api/... - server replied: Bad Gateway")
    // directly in the UI - technically accurate, not actually useful to look at. The real
    // detail isn't thrown away, just moved to a real log file instead of the user's face.
    if (!technicalDetail.isEmpty())
        logToFile(friendlyMessage + QStringLiteral(" | ") + technicalDetail);
    m_lastError = friendlyMessage;
    emit lastErrorChanged();
}

void DeviceService::refreshDemoMode()
{
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/demo"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError)
            return;
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool on = obj.value(QStringLiteral("enabled")).toBool();
        const QString variant = obj.value(QStringLiteral("variant")).toString();
        if (on == m_demoMode && variant == m_demoVariant)
            return;
        m_demoMode = on;
        m_demoVariant = variant;
        m_demoDeviceName = obj.value(QStringLiteral("deviceName")).toString();
        m_demoGarminRoot = obj.value(QStringLiteral("garminRoot")).toString();
        emit demoModeChanged();
    });
}

void DeviceService::setDemoMode(bool enabled, const QString &variant)
{
    QNetworkRequest request(backendUrl(QStringLiteral("/api/demo")));
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                      QStringLiteral("application/json"));
    QJsonObject payload;
    payload.insert(QStringLiteral("enabled"), enabled);
    if (!variant.isEmpty())
        payload.insert(QStringLiteral("variant"), variant);
    QNetworkReply *reply = m_network.post(request, QJsonDocument(payload).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_demoMode = obj.value(QStringLiteral("enabled")).toBool();
        m_demoVariant = obj.value(QStringLiteral("variant")).toString();
        m_demoDeviceName = obj.value(QStringLiteral("deviceName")).toString();
        m_demoGarminRoot = obj.value(QStringLiteral("garminRoot")).toString();
        emit demoModeChanged();
        // Switching either way changes what every page is looking at, so re-read now rather
        // than leaving the previous device's data on screen.
        m_autoSyncedThisConnection = true;   // never auto-write to a watch on a demo switch
        refresh();
    });
}

void DeviceService::refresh()
{
    m_pollTimer.stop();
    m_heartbeatTimer.stop();
    setLoading(true);

    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/health"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const bool reachable = (reply->error() == QNetworkReply::NoError);
        if (m_backendReachable != reachable) {
            m_backendReachable = reachable;
            emit backendReachableChanged();
        }
        if (!reachable) {
            setLoading(false);
            setLastError(QStringLiteral("Backend not running"),
                QStringLiteral("GET /api/health: %1").arg(reply->errorString()));
            m_pollTimer.start(kPollIntervalMs);
            return;
        }
        fetchDeviceInfo();
    });
}

void DeviceService::fetchDeviceInfo()
{
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/device"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_deviceInfoOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        if (m_deviceInfoOk) {
            m_model = obj.value(QStringLiteral("model")).toString();
            m_serial = obj.value(QStringLiteral("serial")).toString();
            m_firmwareVersion = obj.value(QStringLiteral("fw_version")).toString();
            m_hardwareVersion = obj.value(QStringLiteral("hw_version")).toString();
            m_batteryPercent = obj.value(QStringLiteral("battery_percent")).toInt(-1);
            setLastError(QString(), QString());
            // Connected - real request 2026-08-08 ("if watch is connected don't refresh"):
            // m_pollTimer (the fast 1s "searching" poll) stays stopped. But a real
            // disconnect must still eventually be noticed (found live, same day: "it is
            // blocked on ambit connected even if it disconnected" - the manual Refresh
            // button was removed in the same change, so with nothing polling there was
            // no way back). This slow heartbeat re-checks every 10s while connected -
            // enough to catch a real unplug within a bounded time without hammering the
            // USB link the way continuous 1s polling would.
            m_heartbeatTimer.start(kHeartbeatIntervalMs);

            // Real request, 2026-08-11 (Andre, G2/G3): "clock, sync upon connection of the
            // watch if connected to internet" and the same for the GPS orbit. This is a
            // deliberate exception to this app's own "explicit tap for any write" rule -
            // both are self-correcting, low-risk operations (set the clock to now; refresh
            // ephemeris that expires on its own), and having to remember to tap them is
            // exactly the busywork the rule exists to avoid elsewhere.
            //
            // Guarded so it happens once per CONNECTION, not once per poll: the heartbeat
            // re-reads this endpoint every 10s while connected, and syncing on each of those
            // would write to the watch continuously. Offline, nothing is attempted at all
            // and the UI keeps its existing tap-to-sync message.
            if (!m_autoSyncedThisConnection && m_online) {
                m_autoSyncedThisConnection = true;
                syncTime();
                updateGpsOrbit();
            }
        } else {
            const QString technical = reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/device: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/device: %1")
                    .arg(obj.value(QStringLiteral("stderr")).toString());
            setLastError(QStringLiteral("Watch not connected"), technical);
            // Disconnected: the next connection is a new one and syncs again.
            m_autoSyncedThisConnection = false;
            // Not connected - real request 2026-08-08 ("if not connected, refresh with a 1
            // second interval"): keep polling, uncapped, until it connects.
            m_pollTimer.start(kPollIntervalMs);
        }
        emit deviceInfoChanged();
    });
}

void DeviceService::refreshDevices()
{
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/devices"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError)
            return; // a transient backend hiccup - keep the last list rather than blanking it
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (!obj.value(QStringLiteral("ok")).toBool())
            return;
        QVariantList watches;
        const auto arr = obj.value(QStringLiteral("watches")).toArray();
        for (const auto &v : arr) {
            const auto w = v.toObject();
            watches.append(QVariantMap{
                {QStringLiteral("productId"), w.value(QStringLiteral("productId")).toInt()},
                {QStringLiteral("name"), w.value(QStringLiteral("name")).toString()},
                {QStringLiteral("codename"), w.value(QStringLiteral("codename")).toString()},
            });
        }
        m_connectedWatches = watches;
        const auto sel = obj.value(QStringLiteral("selected"));
        m_selectedProductId = sel.isNull() ? -1 : sel.toInt();

        // If the pinned watch isn't actually on the bus - a stale selection carried over
        // from a previous session, or a different watch plugged in since - every tool would
        // keep targeting the absent one (write_nav defaults to the Ambit3 Peak when nothing
        // present matches) and the app would sit on "no watch" while one is clearly
        // connected. Fall back to a watch that IS present so it just works. selectWatch()
        // re-pins the backend, re-reads identity and re-fetches this list; once the pinned
        // id is in the list this branch is skipped, so it can't loop.
        bool selectedPresent = false;
        for (const auto &w : watches)
            if (w.toMap().value(QStringLiteral("productId")).toInt() == m_selectedProductId)
                selectedPresent = true;
        emit connectedWatchesChanged();
        if (!watches.isEmpty() && !selectedPresent)
            selectWatch(watches.first().toMap().value(QStringLiteral("productId")).toInt());
    });
}

void DeviceService::selectWatch(int productId)
{
    QNetworkRequest request(backendUrl(QStringLiteral("/api/device/select")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject payload;
    payload.insert(QStringLiteral("productId"),
                   productId < 0 ? QJsonValue() : QJsonValue(productId));
    QNetworkReply *reply = m_network.post(request, QJsonDocument(payload).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply, productId] {
        reply->deleteLater();
        m_selectedProductId = productId;
        emit connectedWatchesChanged();
        // A different watch is a new connection: re-read identity (which re-arms the
        // once-per-connection auto clock/orbit sync) and refresh the picker's own list.
        m_autoSyncedThisConnection = false;
        refresh();
        refreshDevices();
        // GLONASS support and orbit freshness are per-WATCH, read from the watch itself
        // (sgee.py glonass_status). Without this, switching e.g. Ambit3 -> Traverse kept the
        // previous watch's glonassSupported, so a Traverse (which HAS GlonassSGEE) wrongly
        // showed no GLONASS while an Ambit3 (which has none) could still show it. The
        // backend already targets the just-selected watch (the /api/device/select POST above
        // completed before this reply), so re-asking now reflects the new watch.
        checkGpsOrbitStatus();
    });
}

void DeviceService::setEphemerisGpsOnly(bool value)
{
    if (m_ephemerisGpsOnly == value)
        return;
    m_ephemerisGpsOnly = value;
    // Same QSettings mechanism ConnectionsService already uses for credentials - one bool
    // needs no new service class.
    QSettings().setValue(QStringLiteral("ephemeris/gpsOnly"), value);
    emit ephemerisGpsOnlyChanged();
}

void DeviceService::updateGpsOrbit()
{
    m_gpsOrbitBusy = true;
    m_gpsOrbitStatusText = QStringLiteral("Checking...");
    emit gpsOrbitChanged();

    QNetworkRequest request(backendUrl(QStringLiteral("/api/agps/update")));
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                       QStringLiteral("application/json"));
    QJsonObject bodyObj;
    bodyObj.insert(QStringLiteral("confirm"), true);
    bodyObj.insert(QStringLiteral("gps_only"), m_ephemerisGpsOnly);
    const QByteArray body = QJsonDocument(bodyObj).toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_network.post(request, body);
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        m_gpsOrbitBusy = false;

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError && obj.isEmpty()) {
            m_gpsOrbitStatusText =
                QStringLiteral("Couldn't reach the backend: %1").arg(reply->errorString());
            emit gpsOrbitChanged();
            return;
        }

        if (!obj.value(QStringLiteral("ok")).toBool()) {
            m_gpsOrbitStatusText = QStringLiteral("Failed: %1")
                .arg(obj.value(QStringLiteral("error")).toString(
                    obj.value(QStringLiteral("stderr")).toString()));
        } else if (obj.value(QStringLiteral("skipped")).toBool()) {
            // André, 2026-08-11: "If already synced and updated just say synced." The date
            // it is synced TO is already on this card, so repeating it here said nothing.
            m_gpsOrbitStatusText = QStringLiteral("Synced");
        } else if (obj.value(QStringLiteral("offline")).toBool()) {
            const QString watchDate = obj.value(QStringLiteral("watch_date")).toString();
            m_gpsOrbitStatusText = watchDate.isEmpty()
                ? QStringLiteral("No internet connection, and the watch has no orbit data yet")
                : QStringLiteral("No internet connection - watch's current data is from %1")
                    .arg(watchDate);
        } else if (obj.value(QStringLiteral("wrote")).toBool()) {
            m_gpsOrbitStatusText = QStringLiteral("Updated");
        } else {
            m_gpsOrbitStatusText = QStringLiteral("Synced");
        }
        emit gpsOrbitChanged();
    });
}

void DeviceService::checkGpsOrbitStatus()
{
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/agps/status"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool()) {
            // Same "just don't show it" rule as WeatherService's own place-name lookup -
            // this is a passive background check, not something worth surfacing an error
            // for on its own; the explicit "Update" button's own errors still show.
            return;
        }
        // André, 2026-08-11 (item 14): "If already synced and updated just say synced."
        // Orbit data is dated and expires, so "current" means the watch's own date is
        // today's - anything older is worth an update and says so with the date, which is
        // the one case where the date is useful.
        {
            const QString watchDate = obj.value(QStringLiteral("date")).toString();
            const QString today =
                QDateTime::currentDateTime().toString(QStringLiteral("yyyy-MM-dd"));
            if (!obj.value(QStringLiteral("valid")).toBool())
                m_gpsOrbitStatusText = QStringLiteral("No data yet - tap to update");
            else if (watchDate == today)
                m_gpsOrbitStatusText = QStringLiteral("Synced");
            else
                m_gpsOrbitStatusText = QStringLiteral("%1 - tap to update").arg(watchDate);
        }
        // Asked of the watch, not assumed from its model - see the header's own comment.
        m_glonassSupported = obj.value(QStringLiteral("glonass")).toObject()
            .value(QStringLiteral("supported")).toBool();
        emit gpsOrbitChanged();
    });
}

void DeviceService::syncTime(const QString &timezone)
{
    m_timeSyncBusy = true;
    m_timeSyncStatusText = QStringLiteral("Syncing...");
    emit timeSyncChanged();

    QNetworkRequest request(backendUrl(QStringLiteral("/api/time/sync")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject bodyObj;
    if (!timezone.isEmpty()) {
        bodyObj.insert(QStringLiteral("timezone"), timezone);
    }
    QNetworkReply *reply = m_network.post(request, QJsonDocument(bodyObj).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        m_timeSyncBusy = false;

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError && obj.isEmpty()) {
            m_timeSyncStatusText =
                QStringLiteral("Couldn't reach the backend: %1").arg(reply->errorString());
            emit timeSyncChanged();
            return;
        }

        if (!obj.value(QStringLiteral("ok")).toBool()) {
            m_timeSyncStatusText = QStringLiteral("Failed: %1")
                .arg(obj.value(QStringLiteral("error")).toString());
        } else {
            m_timeSyncStatusText = QStringLiteral("Synced to %1")
                .arg(obj.value(QStringLiteral("time")).toString());
        }
        emit timeSyncChanged();
    });
}

void DeviceService::fetchTimezones()
{
    if (!m_timezones.isEmpty()) {
        return;  // already fetched this session - zoneinfo's own list never changes at runtime
    }
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/time/zones"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool()) {
            return;
        }
        QStringList zones;
        for (const auto &v : obj.value(QStringLiteral("zones")).toArray()) {
            zones << v.toString();
        }
        m_timezones = zones;
        emit timezonesChanged();
    });
}

QString DeviceService::currentTimeInZone(const QString &timezone) const
{
    const QTimeZone tz(timezone.toUtf8());
    if (!tz.isValid()) {
        return QString();
    }
    // Real, 2026-08-10 ("it shows the date and that makes the hour no visible") - this is
    // shown inline next to a long zone name in a fixed-width dropdown row (HomePage.qml's
    // own tzCombo delegate); the full date+seconds this originally returned pushed the
    // actually-useful hour:minute off the visible edge. Just the time - picking a timezone
    // to compare "what hour is it there" doesn't need today's date repeated 599 times.
    return QDateTime::currentDateTime(tz).toString(QStringLiteral("HH:mm"));
}

void DeviceService::connectBle(bool forget)
{
    m_bleAttempting = true;
    m_bleSubscribed = false;
    m_bleHandshakeDone = false;
    m_blePendingPasskeyDevice.clear();
    m_bleError.clear();
    m_bleAttemptSeconds = 0;
    emit bleStateChanged();

    QJsonObject body;
    body[QStringLiteral("forget")] = forget;
    QNetworkRequest request(backendUrl(QStringLiteral("/api/ble/connect")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool()) {
            m_bleAttempting = false;
            m_bleError = obj.value(QStringLiteral("error")).toString(reply->errorString());
            emit bleStateChanged();
            return;
        }
        // The daemon is up; now poll /api/ble/status for the states that unfold after this
        // (subscribed, a passkey request, the handshake completing) - see connectBle()'s
        // own header comment for why this request itself doesn't wait for any of that.
        pollBleStatus();
    });
}

void DeviceService::pollBleStatus()
{
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/ble/status"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError) {
            m_bleAttempting = false;
            m_bleError = reply->errorString();
            emit bleStateChanged();
            return;
        }
        m_bleAttemptSeconds += 1;
        m_bleSubscribed = obj.value(QStringLiteral("subscribed")).toBool();
        m_bleHandshakeDone = obj.value(QStringLiteral("handshake_done")).toBool();
        const auto pendingVal = obj.value(QStringLiteral("pending_passkey_device"));
        m_blePendingPasskeyDevice = pendingVal.isNull() ? QString() : pendingVal.toString();
        emit bleStateChanged();

        if (m_bleHandshakeDone) {
            // Real device data (model/serial/fw/hw/battery) comes from /api/device, which
            // already answers over BLE transparently once a watch is subscribed - no
            // separate fetch needed here, just kick the existing poll so it picks this up
            // right away instead of waiting for its own next scheduled tick.
            m_bleAttempting = false;
            emit bleStateChanged();
            refresh();
            // Real bug, found live 2026-08-11: GPS orbit status is only ever checked once,
            // from HomePage.qml's own Component.onCompleted - if that ran before a BLE
            // connection existed (the normal case: the page loads with nothing connected,
            // the user connects afterward), the "couldn't read orbit status" error just sat
            // there forever with nothing to retry it. checkGpsOrbitStatus() is cheap and
            // read-only (a single 0x0b15 query), so re-running it here the moment a BLE
            // watch becomes available is the same "connection just changed, re-ask" logic
            // refresh() already gets, applied to the one other query that had been missing
            // it.
            checkGpsOrbitStatus();
            return;
        }
        if (!m_bleAttempting) {
            return;  // disconnectBle() was called while this request was in flight
        }
        // Real request, 2026-08-13 (André, live testing: "it is still on 'connecting'...
        // I was thinking like a timer to stop the connecting...so we can try something
        // also"). Cuts off only the NOT-YET-FOUND case (never subscribed at all) - once a
        // real device has subscribed and this app is just waiting on a passkey or the
        // handshake, that wait can legitimately run long (the person has to notice the
        // watch's screen, read six digits, and relay it back) and is left alone. Doesn't
        // tear the daemon down - it keeps scanning in the background regardless, so a late
        // connection still completes; this only frees the UI to let the person retry or
        // use Forget instead of staring at a stuck button.
        if (!m_bleSubscribed && m_bleAttemptSeconds >= kBleSearchTimeoutS) {
            m_bleAttempting = false;
            m_bleError = tr("No watch found within %1s - check it's on the Bluetooth pairing "
                             "screen and try again").arg(kBleSearchTimeoutS);
            emit bleStateChanged();
            return;
        }
        // Keep polling - a fresh pairing's passkey wait can take longer than any single
        // fixed timeout should force it to give up within (the person has to notice the
        // watch's screen, read six digits, and tell this app).
        m_blePollTimer.start(kBlePollIntervalMs);
    });
}

void DeviceService::disconnectBle()
{
    m_blePollTimer.stop();
    m_bleAttempting = false;
    m_bleSubscribed = false;
    m_bleHandshakeDone = false;
    m_blePendingPasskeyDevice.clear();
    emit bleStateChanged();

    QNetworkReply *reply =
        m_network.post(QNetworkRequest(backendUrl(QStringLiteral("/api/ble/disconnect"))),
                       QByteArray());
    connect(reply, &QNetworkReply::finished, reply, &QNetworkReply::deleteLater);
}

void DeviceService::forgetBle()
{
    m_blePollTimer.stop();
    m_bleAttempting = false;
    m_bleSubscribed = false;
    m_bleHandshakeDone = false;
    m_blePendingPasskeyDevice.clear();
    m_bleError.clear();
    emit bleStateChanged();

    QNetworkReply *reply =
        m_network.post(QNetworkRequest(backendUrl(QStringLiteral("/api/ble/forget"))),
                       QByteArray());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool()) {
            m_bleError = obj.value(QStringLiteral("error")).toString(reply->errorString());
            emit bleStateChanged();
        }
    });
}

void DeviceService::submitBlePasskey(int passkey)
{
    QJsonObject body;
    body[QStringLiteral("passkey")] = passkey;
    QNetworkRequest request(backendUrl(QStringLiteral("/api/ble/passkey")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError || !obj.value(QStringLiteral("ok")).toBool()) {
            m_bleError = obj.value(QStringLiteral("error")).toString(reply->errorString());
            emit bleStateChanged();
        }
        // Success just clears the pending prompt on the next pollBleStatus() tick - no
        // separate handling needed here.
    });
}

void DeviceService::setBleExperimentEnabled(bool value)
{
    if (m_bleExperimentEnabled == value)
        return;
    m_bleExperimentEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/bluetooth"), value);
    // Turning it off mid-session tears down whatever BLE state was live, rather than
    // leaving a daemon running (and a "Connecting..."/passkey dialog possibly still
    // showing) behind a toggle the user just switched off.
    if (!value) {
        disconnectBle();
    }
    emit bleExperimentEnabledChanged();
}

void DeviceService::setMarkSyncedEnabled(bool value)
{
    if (m_markSyncedEnabled == value)
        return;
    m_markSyncedEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/markSynced"), value);
    emit markSyncedEnabledChanged();
}

void DeviceService::setIntervalsEnabled(bool value)
{
    if (m_intervalsEnabled == value)
        return;
    m_intervalsEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/intervals"), value);
    emit intervalsEnabledChanged();
}

void DeviceService::setAppZoneEnabled(bool value)
{
    if (m_appZoneEnabled == value)
        return;
    m_appZoneEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/appZone"), value);
    emit appZoneEnabledChanged();
}

void DeviceService::setSmartSensorEnabled(bool value)
{
    if (m_smartSensorEnabled == value)
        return;
    m_smartSensorEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/smartSensor"), value);
    emit smartSensorEnabledChanged();
}

void DeviceService::setCoachEnabled(bool value)
{
    if (m_coachEnabled == value)
        return;
    m_coachEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/coach"), value);
    emit coachEnabledChanged();
}

void DeviceService::setSuuntoT6ExperimentEnabled(bool value)
{
    if (m_suuntoT6ExperimentEnabled == value)
        return;
    m_suuntoT6ExperimentEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/suuntoT6"), value);
    emit suuntoT6ExperimentEnabledChanged();
}

void DeviceService::setGpsTrackPodExperimentEnabled(bool value)
{
    if (m_gpsTrackPodExperimentEnabled == value)
        return;
    m_gpsTrackPodExperimentEnabled = value;
    QSettings().setValue(QStringLiteral("experimental/gpsTrackPod"), value);
    emit gpsTrackPodExperimentEnabledChanged();
}
