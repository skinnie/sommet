#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QTimer>
#include <QUrl>
#include <QVariantList>

// AMBITAPP_SPEC.md's architecture: QML -> ViewModels -> Services -> Current Backend ->
// libambit. This is the first real "Services" class - a thin HTTP client against
// ambitapp-v2/backend/server.py (see that file and ../../README.md's "Architecture
// decision" section for why the backend is Python, not C++). It knows nothing about the
// watch protocol itself, only how to ask the local backend about it.
//
// QML_SINGLETON: one instance, shared app-wide - matches there being exactly one backend
// server process to talk to, not one client per page.
//
// 2026-08-07, real testing found this was slower to show "Connected" than the real Android
// app: refresh() used to also fetch /api/nav (a full read of the Waypoints+Routes flash
// regions, ~146KB over USB) just to use its success/failure as the connectivity signal -
// nothing in the UI ever showed navOk/navRawOutput otherwise (checked directly - zero other
// references). /api/device (a single ~40-byte 0x0000 command) is a real, much faster
// connectivity signal on its own; the nav fetch added real, unnecessary latency to
// something that never needed it. Removed entirely, not just deferred.
class DeviceService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool backendReachable READ backendReachable NOTIFY backendReachableChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

    // 2026-08-07: real, confirmed working against real hardware (tools/device_info.py) -
    // see device_info.py's own docstring for the source (commands this project already
    // knew about, reply layout from openambit's real code).
    Q_PROPERTY(bool deviceInfoOk READ deviceInfoOk NOTIFY deviceInfoChanged)
    Q_PROPERTY(QString model READ model NOTIFY deviceInfoChanged)
    Q_PROPERTY(QString serial READ serial NOTIFY deviceInfoChanged)
    Q_PROPERTY(QString firmwareVersion READ firmwareVersion NOTIFY deviceInfoChanged)
    Q_PROPERTY(QString hardwareVersion READ hardwareVersion NOTIFY deviceInfoChanged)
    Q_PROPERTY(int batteryPercent READ batteryPercent NOTIFY deviceInfoChanged)

    // Every Suunto watch currently on the USB bus, for the Home watch-switcher (2026-08-16,
    // porting the Android multi-watch picker). Each entry: {productId:int, name:str,
    // codename:str}. selectedProductId is the one pinned via selectWatch() (all backend tools
    // then target it), or -1 for "whichever is plugged".
    Q_PROPERTY(QVariantList connectedWatches READ connectedWatches NOTIFY connectedWatchesChanged)
    Q_PROPERTY(int selectedProductId READ selectedProductId NOTIFY connectedWatchesChanged)
    // The unified "active device" model (André, 2026-09-04): a Garmin Edge / Hammerhead Karoo can
    // be the active device instead of a watch. When activeBikeKind is set ("edge"/"karoo"), the
    // app is focused on that bike computer - Home shows it, watch-only sections hide. Empty means
    // a watch (or nothing) is active, the prior behaviour.
    Q_PROPERTY(QString activeBikeKind READ activeBikeKind NOTIFY activeDeviceChanged)
    Q_PROPERTY(bool bikeActive READ bikeActive NOTIFY activeDeviceChanged)

    // GPS orbit (AGPS/SGEE) update - real, 2026-08-07. The backend side
    // (POST /api/agps/update, sgee_andre.md) was already built and hardware-verified; only
    // this Service and HomePage.qml's "Not available yet" text were missing, found via real
    // testing ("check android version, it is working there" - opensportsync-main's
    // SgeeService.ts/HomeScreen.tsx do exactly this on one tap, matched here the same way
    // rather than adding a separate rehearse step this app doesn't otherwise expose for it).
    Q_PROPERTY(bool gpsOrbitBusy READ gpsOrbitBusy NOTIFY gpsOrbitChanged)
    Q_PROPERTY(QString gpsOrbitStatusText READ gpsOrbitStatusText NOTIFY gpsOrbitChanged)
    // Real, 2026-08-10 (sgee_andre.md's "GLONASS on the Kailash"). `glonassSupported` is
    // answered by the WATCH - sgee.py's glonass_status() reports whether this device
    // declares a GlonassSGEE region at all - never by a model list, which is exactly the
    // mistake Suunto's own Devices.xml makes (three models hardcoded; the Kailash, which
    // has both the receiver and the region, was left off and has therefore never received
    // GLONASS ephemeris from any Suunto software). The UI shows the option only when this
    // is true.
    Q_PROPERTY(bool glonassSupported READ glonassSupported NOTIFY gpsOrbitChanged)
    // App preference, NOT a field on the watch: when true we send GPS ephemeris only and
    // skip GLONASS. Persisted with QSettings, and passed to the backend as `gps_only` on
    // every update. Default false - the whole point is that we send both where the device
    // supports it.
    Q_PROPERTY(bool ephemerisGpsOnly READ ephemerisGpsOnly WRITE setEphemerisGpsOnly
               NOTIFY ephemerisGpsOnlyChanged)

    // Real, 2026-08-10 ("I connected the kailash via usb... it didn't sync time... is this
    // function implemented in our app? if not implement it"). Same one-tap, always-real-write
    // shape as updateGpsOrbit() above (tools/set_time.py's own docstring explains why no
    // rehearsal step is needed here, unlike settings/route writes) - POST /api/time/sync.
    Q_PROPERTY(bool timeSyncBusy READ timeSyncBusy NOTIFY timeSyncChanged)
    Q_PROPERTY(QString timeSyncStatusText READ timeSyncStatusText NOTIFY timeSyncChanged)
    // GET /api/time/zones (the real IANA names, offline - see set_time.py's own docstring) -
    // fetched lazily on first menu open, not on every Home load like deviceInfo/gpsOrbit,
    // since most syncTime() taps use "from device" and never need this list at all.
    Q_PROPERTY(QStringList timezones READ timezones NOTIFY timezonesChanged)
    // Whether this machine currently has a route to the internet. Read from Qt's own
    // network-reachability backend rather than by probing a server: the clock and GPS-orbit
    // features need it only to decide whether they CAN run, and probing on every device poll
    // would put real traffic on the wire for a question the OS already answers.
    Q_PROPERTY(bool online READ online NOTIFY onlineChanged)
    // Testing mode - the backend answers from fixtures instead of USB, so the app can be
    // explored with no watch attached. Every reply it produces is flagged, so the UI can say
    // plainly that nothing here is a real device.
    Q_PROPERTY(bool demoMode READ demoMode NOTIFY demoModeChanged)
    // Which device Testing mode is pretending to be (a codename like "Emu", or
    // "GarminEtrex"). Empty until the backend has answered.
    Q_PROPERTY(QString demoVariant READ demoVariant NOTIFY demoModeChanged)
    // The simulated device's product name, resolved by the backend from the same table the
    // picker lists - so the two can never disagree about what is being simulated.
    Q_PROPERTY(QString demoDeviceName READ demoDeviceName NOTIFY demoModeChanged)
    // Where the simulated Garmin's folder tree lives; empty unless the eTrex is selected.
    Q_PROPERTY(QString demoGarminRoot READ demoGarminRoot NOTIFY demoModeChanged)

    // Bluetooth (Linux only for now - see HANDOFF.md Milestone 7 items 14-17). Deliberately
    // separate from deviceInfoOk/model/etc above: /api/device already answers over BLE once
    // connected (server.py's own transport-agnostic design), so those existing properties
    // pick up a BLE-connected watch with no changes here. What's new is the STATE BEFORE
    // that - searching, a fresh pairing waiting on a passkey - which /api/device has no way
    // to express since it's a plain "is the watch there" query, not a connection process.
    Q_PROPERTY(bool bleAttempting READ bleAttempting NOTIFY bleStateChanged)
    Q_PROPERTY(bool bleSubscribed READ bleSubscribed NOTIFY bleStateChanged)
    Q_PROPERTY(bool bleHandshakeDone READ bleHandshakeDone NOTIFY bleStateChanged)
    // Non-empty while a fresh pairing needs a human to read the watch's own displayed
    // passkey and report it back (LE Legacy Passkey Entry - this watch family has no way to
    // confirm pairing any other way, see ble_server.py's Agent docstring). The UI shows a
    // passkey-entry dialog exactly when this is non-empty.
    Q_PROPERTY(QString blePendingPasskeyDevice READ blePendingPasskeyDevice NOTIFY bleStateChanged)
    Q_PROPERTY(QString bleError READ bleError NOTIFY bleStateChanged)
    Q_PROPERTY(int bleAttemptSeconds READ bleAttemptSeconds NOTIFY bleStateChanged)

    // Real decision, 2026-08-11 (André, after a live BLE session this same night hit real
    // reliability trouble - a route write that needed a mid-flight bug fix, and repeated
    // BlueZ connect/subscribe flakiness this project's own history already knew about but
    // hadn't fully solved): BLE stays real, but behind an explicit, OFF-by-default
    // Settings toggle - "experimental", not the default Home experience. Cable-only is
    // what most people get; this is for people who opt in knowing it's still being
    // hardened. Also the point where macOS/Windows BLE backends were explicitly dropped
    // from scope (HANDOFF.md) - this toggle and everything behind it is Linux/BlueZ only.
    // Persisted via QSettings, same mechanism as ephemerisGpsOnly above.
    Q_PROPERTY(bool bleExperimentEnabled READ bleExperimentEnabled
               WRITE setBleExperimentEnabled NOTIFY bleExperimentEnabledChanged)

    // "Mark synced workouts as synced for Suunto app and SuuntoLink" - experimental,
    // OFF by default (André, 2026-08-16). When on, after this app reads an activity off
    // the watch it writes the watch's own per-move synced flag (command 0x1201, the same
    // marker SuuntoLink writes over cable - entry 0x8b timestamp + synced=1 on Ambit3
    // GEN4 fw; see tools/mark_synced.py and the activity-sync-no-delete finding). Upside:
    // the official Suunto app / SuuntoLink then see the move as already synced and won't
    // duplicate it. Downside: the move can no longer be re-retrieved from the watch if the
    // Suunto app later fails to keep it - which is exactly why this is opt-in and default
    // off (nothing this app does to the watch is destructive unless the user asks). The
    // watch reclaims log space by circular-buffer wraparound regardless of this flag.
    // Persisted via QSettings, same mechanism as bleExperimentEnabled above.
    Q_PROPERTY(bool markSyncedEnabled READ markSyncedEnabled
               WRITE setMarkSyncedEnabled NOTIFY markSyncedEnabledChanged)
    // Experimental menu features (2026-08-17): each gates its own menu item (NavRail), the
    // same per-feature-toggle model the Android app uses. Persisted via QSettings.
    Q_PROPERTY(bool intervalsEnabled READ intervalsEnabled
               WRITE setIntervalsEnabled NOTIFY intervalsEnabledChanged)
    Q_PROPERTY(bool appZoneEnabled READ appZoneEnabled
               WRITE setAppZoneEnabled NOTIFY appZoneEnabledChanged)
    Q_PROPERTY(bool smartSensorEnabled READ smartSensorEnabled
               WRITE setSmartSensorEnabled NOTIFY smartSensorEnabledChanged)
    // Coach (v2 concept, 2026-08-21) - same per-feature-toggle model, gates the NavRail
    // entry. Was a compile-time FeatureFlags.coach: true; moved here (default OFF, runtime
    // toggle) at André's request so it ships hidden like every other new feature until it
    // earns its way out, not visible from first launch.
    Q_PROPERTY(bool coachEnabled READ coachEnabled
               WRITE setCoachEnabled NOTIFY coachEnabledChanged)

    // Suunto T6 family (2026-08-14, "implement Suunto t6 ... only as experimental") - a
    // different, older Suunto product from the watches this app targets (a 2004-2010
    // heart-rate training computer, no GPS - the track came from a separate GPS Track Pod).
    // Integrated built-blind exactly like the GPS Track Pod above; same "opt in knowing it
    // has never been hardware-confirmed" reasoning. Persisted via QSettings.
    Q_PROPERTY(bool suuntoT6ExperimentEnabled READ suuntoT6ExperimentEnabled
               WRITE setSuuntoT6ExperimentEnabled NOTIFY suuntoT6ExperimentEnabledChanged)
    // GPS Track Pod - a separate, standalone Suunto GPS logger (not a watch), integrated
    // built-blind and opt-in since none of it has ever been hardware-confirmed. Persisted
    // via QSettings. NavRail gates its item on this.
    Q_PROPERTY(bool gpsTrackPodExperimentEnabled READ gpsTrackPodExperimentEnabled
               WRITE setGpsTrackPodExperimentEnabled NOTIFY gpsTrackPodExperimentEnabledChanged)

public:
    explicit DeviceService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool backendReachable() const { return m_backendReachable; }
    QString lastError() const { return m_lastError; }

    bool deviceInfoOk() const { return m_deviceInfoOk; }
    QString model() const { return m_model; }
    QString serial() const { return m_serial; }
    QString firmwareVersion() const { return m_firmwareVersion; }
    QString hardwareVersion() const { return m_hardwareVersion; }
    int batteryPercent() const { return m_batteryPercent; }
    QVariantList connectedWatches() const { return m_connectedWatches; }
    int selectedProductId() const { return m_selectedProductId; }
    QString activeBikeKind() const { return m_activeBikeKind; }
    bool bikeActive() const { return !m_activeBikeKind.isEmpty(); }

    // Checks /api/health, then /api/device (identity, battery). Read-only on the backend
    // side, safe to call any time.
    Q_INVOKABLE void refresh();

    // GET /api/devices - refresh the list of watches on the USB bus (the Home switcher).
    Q_INVOKABLE void refreshDevices();
    // POST /api/device/select then re-read: pin every backend tool to this one watch when
    // several share the bus (productId < 0 clears the pin). Mirrors Android's selectWatch().
    Q_INVOKABLE void selectWatch(int productId);
    // Make a bike computer the active device ("edge"/"karoo"), or "" to hand back to the watch.
    Q_INVOKABLE void selectBikeComputer(const QString &kind);

    bool gpsOrbitBusy() const { return m_gpsOrbitBusy; }
    QString gpsOrbitStatusText() const { return m_gpsOrbitStatusText; }
    bool glonassSupported() const { return m_glonassSupported; }
    bool ephemerisGpsOnly() const { return m_ephemerisGpsOnly; }
    void setEphemerisGpsOnly(bool value);

    // Downloads fresh orbital data and writes it to the watch in one call (POST
    // /api/agps/update, confirm:true) - a real watch write, not a rehearsal; the backend's
    // own sgee.py already only actually re-flashes if the generation date changed, so a
    // repeat tap when nothing's stale is cheap, matching the real Android app's own
    // no-separate-confirm-step UI.
    Q_INVOKABLE void updateGpsOrbit();

    // Read-only (GET /api/agps/status, a plain 0x0b15 query - no network fetch, nothing
    // written), safe to call on every Home load the same way refresh() is. Shows the watch's
    // own currently-stored orbit-data date even with no internet connection at all, which is
    // the whole point of it being separate from updateGpsOrbit() rather than folded into it.
    Q_INVOKABLE void checkGpsOrbitStatus();

    bool timeSyncBusy() const { return m_timeSyncBusy; }
    QString timeSyncStatusText() const { return m_timeSyncStatusText; }
    QStringList timezones() const { return m_timezones; }

    // Sets the watch's clock. Empty timezone (the default) means "this computer's own
    // current local time"; a real IANA name (from timezones() below) means "the current
    // time in that timezone instead" - the menu's own two options, real 2026-08-10
    // ("a button to sync time... 'from device' 'from different timezone'").
    Q_INVOKABLE void syncTime(const QString &timezone = QString());

    // Real, 2026-08-10 ("when you show the timezone can you show what time is there now?") -
    // a live preview of the real current time in a candidate zone, before committing to
    // sync to it. Pure client-side QDateTime/QTimeZone math (the same real IANA database
    // zoneinfo/set_time.py already uses) - no backend round trip needed for this one.
    Q_INVOKABLE QString currentTimeInZone(const QString &timezone) const;

    // Populates timezones() from the backend (GET /api/time/zones) - call once when the
    // "from a different timezone" option is opened, not eagerly.
    Q_INVOKABLE void fetchTimezones();

    bool online() const { return m_online; }
    bool demoMode() const { return m_demoMode; }

    // POST /api/demo. Refreshes straight after, so the switch is visible immediately.
    QString demoVariant() const { return m_demoVariant; }
    QString demoDeviceName() const { return m_demoDeviceName; }
    QString demoGarminRoot() const { return m_demoGarminRoot; }
    // variant empty = keep whatever is already selected.
    Q_INVOKABLE void setDemoMode(bool enabled, const QString &variant = QString());
    Q_INVOKABLE void refreshDemoMode();

    bool bleAttempting() const { return m_bleAttempting; }
    bool bleSubscribed() const { return m_bleSubscribed; }
    bool bleHandshakeDone() const { return m_bleHandshakeDone; }
    QString blePendingPasskeyDevice() const { return m_blePendingPasskeyDevice; }
    QString bleError() const { return m_bleError; }
    // Real request, 2026-08-13 (André, live testing: "it is still on 'connecting'...
    // maybe we should put a timer no?") - a plain "Connecting…" with no elapsed time gave
    // no way to tell "still genuinely searching" from "stuck". Ticks once per
    // pollBleStatus() poll (~1s, kBlePollIntervalMs) rather than a separate QElapsedTimer -
    // deliberately NOT a hard cutoff that cancels the attempt: a fresh pairing's passkey
    // wait can legitimately take longer than any fixed timeout should force it to give up
    // within (this file's own connectBle() comment already makes that case). The UI uses
    // this only to show a growing count and, past a threshold, a hint - never to stop
    // polling on its own.
    int bleAttemptSeconds() const { return m_bleAttemptSeconds; }

    // Starts ble_server.py (POST /api/ble/connect) and polls /api/ble/status until the
    // handshake completes, a passkey is needed (blePendingPasskeyDevice), or it fails.
    // `forget` mirrors ble_server.py's own --forget - NOT the default; PROJECT_RULES.md's
    // own pairing guidance is "always unpair, don't replace" as a WATCH-side menu action,
    // not something to do from this app on every connect tap, since a bond just
    // established is what lets a plain reconnect work next time.
    Q_INVOKABLE void connectBle(bool forget = false);
    // Tears the daemon down; leaves the watch's own bond alone.
    Q_INVOKABLE void disconnectBle();
    // Reports the passkey a human read off the watch's screen back to the pairing agent -
    // see blePendingPasskeyDevice's own comment for why this can't be automated.
    Q_INVOKABLE void submitBlePasskey(int passkey);
    // Real request, 2026-08-13 ("we need to add a button to forget the watch") - the Linux
    // side of the bond, reachable from the app instead of only a terminal command. Unlike
    // connectBle(true), which forgets as a side effect of a NEW pairing attempt, this drops
    // the bond immediately without also starting one - useful when the watch itself is the
    // one that needs a fresh "Pair Mobile App" tap next, not this app.
    Q_INVOKABLE void forgetBle();

    bool bleExperimentEnabled() const { return m_bleExperimentEnabled; }
    void setBleExperimentEnabled(bool value);
    bool markSyncedEnabled() const { return m_markSyncedEnabled; }
    bool intervalsEnabled() const { return m_intervalsEnabled; }
    bool appZoneEnabled() const { return m_appZoneEnabled; }
    bool smartSensorEnabled() const { return m_smartSensorEnabled; }
    bool coachEnabled() const { return m_coachEnabled; }
    void setMarkSyncedEnabled(bool value);
    void setIntervalsEnabled(bool value);
    void setAppZoneEnabled(bool value);
    void setSmartSensorEnabled(bool value);
    void setCoachEnabled(bool value);

    bool suuntoT6ExperimentEnabled() const { return m_suuntoT6ExperimentEnabled; }
    void setSuuntoT6ExperimentEnabled(bool value);

    bool gpsTrackPodExperimentEnabled() const { return m_gpsTrackPodExperimentEnabled; }
    void setGpsTrackPodExperimentEnabled(bool value);

signals:
    void loadingChanged();
    void backendReachableChanged();
    void lastErrorChanged();
    void deviceInfoChanged();
    void gpsOrbitChanged();
    void ephemerisGpsOnlyChanged();
    void timeSyncChanged();
    void timezonesChanged();
    void onlineChanged();
    void gpsTrackPodExperimentEnabledChanged();
    void suuntoT6ExperimentEnabledChanged();
    void demoModeChanged();
    void bleStateChanged();
    void bleExperimentEnabledChanged();
    void markSyncedEnabledChanged();
    void intervalsEnabledChanged();
    void appZoneEnabledChanged();
    void smartSensorEnabledChanged();
    void coachEnabledChanged();
    void connectedWatchesChanged();
    void activeDeviceChanged();

private:
    QNetworkAccessManager m_network;
    bool m_online = false;
    bool m_demoMode = false;
    QString m_demoVariant;
    QString m_demoDeviceName;
    QString m_demoGarminRoot;
    // Auto-sync fires once per connection, not once per poll: the device endpoint is
    // re-read every 10s by the heartbeat, and syncing the clock and re-checking the orbit
    // on each of those would write to the watch continuously.
    bool m_autoSyncedThisConnection = false;
    bool m_loading = false;
    bool m_backendReachable = false;
    QString m_lastError;

    bool m_deviceInfoOk = false;
    QString m_model;
    QString m_serial;
    QString m_firmwareVersion;
    QString m_hardwareVersion;
    int m_batteryPercent = -1;
    QVariantList m_connectedWatches;
    int m_selectedProductId = -1;
    QString m_activeBikeKind;   // "" = watch active; "edge"/"karoo" = that bike computer active

    bool m_gpsOrbitBusy = false;
    bool m_glonassSupported = false;
    bool m_ephemerisGpsOnly = false;
    QString m_gpsOrbitStatusText;

    bool m_timeSyncBusy = false;
    QString m_timeSyncStatusText;
    QStringList m_timezones;

    // Auto-refresh, real request 2026-08-08 ("if watch is connected don't refresh, if not
    // connected refresh with a 1 second interval, remove the refresh button"). Superseded
    // an earlier 5s-always-polling design (see V3_CHANGELOG.md).
    QTimer m_pollTimer;
    static constexpr int kPollIntervalMs = 1000;

    // Real bug, found live 2026-08-08 ("it is blocked on ambit connected even if it
    // disconnected"): taking "don't refresh once connected" completely literally - stopping
    // m_pollTimer outright and also removing the manual Refresh button in the same round -
    // meant a real disconnection was *never* noticed once deviceInfoOk had gone true: with
    // nothing polling and no button, refresh() would simply never run again. This slower
    // heartbeat is the real fix - not a return to fast polling, just a low-cost "is it still
    // there" check every 10s while connected, so a real unplug gets noticed within a bounded
    // time instead of being stuck forever. m_pollTimer (1s) stays reserved for the "actively
    // searching" case; the two are never running at the same time.
    QTimer m_heartbeatTimer;
    static constexpr int kHeartbeatIntervalMs = 10000;

    bool m_bleAttempting = false;
    bool m_bleSubscribed = false;
    bool m_bleHandshakeDone = false;
    QString m_blePendingPasskeyDevice;
    QString m_bleError;
    int m_bleAttemptSeconds = 0;
    bool m_bleExperimentEnabled = false;
    bool m_intervalsEnabled = false;
    bool m_appZoneEnabled = false;
    bool m_smartSensorEnabled = false;
    bool m_coachEnabled = false;
    bool m_markSyncedEnabled = false;
    bool m_gpsTrackPodExperimentEnabled = false;
    bool m_suuntoT6ExperimentEnabled = false;
    // Polls /api/ble/status while connectBle() is in progress - separate from
    // m_pollTimer/m_heartbeatTimer (those poll /api/device, which only starts answering
    // once a BLE watch has actually subscribed; this tracks getting there, including a
    // fresh pairing's own passkey wait).
    QTimer m_blePollTimer;
    static constexpr int kBlePollIntervalMs = 1000;
    // Real request, 2026-08-13 - see pollBleStatus()'s own comment for why this only cuts
    // off the never-subscribed case, not a passkey wait already in progress.
    static constexpr int kBleSearchTimeoutS = 30;
    void pollBleStatus();

    void setLoading(bool value);
    void setLastError(const QString &friendlyMessage, const QString &technicalDetail);
    void fetchDeviceInfo();
    void logToFile(const QString &line);

    static QUrl backendUrl(const QString &path);
};
