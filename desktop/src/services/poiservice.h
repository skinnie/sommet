#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>

// Step 9. Deliberately thinner than DeviceService/RouteService: on-watch POIs come back as
// raw text only (see backend/server.py's own comment on why - the schema-driven field names
// aren't something this project can confirm ahead of a real watch check, unlike routes'
// fixed print format). addPoi() is a REAL write since 2026-08-11: the backend runs
// write_nav.py's `addpoi`, the Android app's hardware-confirmed (2026-08-06) native
// algorithm ported back into this repo's tools - the honest-501 era this class's comments
// used to describe is over.
class PoiService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString rawOutput READ rawOutput NOTIFY dataChanged)
    Q_PROPERTY(bool ok READ ok NOTIFY dataChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(QString addResultText READ addResultText NOTIFY addResultChanged)
    // Whether the last addPoi() succeeded - the page colors addResultText by it (success
    // green vs error red) instead of assuming every result is a failure, which was true
    // in the 501 era and isn't anymore.
    Q_PROPERTY(bool addOk READ addOk NOTIFY addResultChanged)
    // GPX <wpt> import - real, confirmed-working on the Android app (opensportsync-main's
    // "POI import (GPX file and manual coordinates)"), missing here until 2026-08-07. Each
    // entry: {name, lat, lon}; submitting one goes through the same real addPoi() write as
    // manual entry - see this class's own note above.
    Q_PROPERTY(QVariantList importedPois READ importedPois NOTIFY importedPoisChanged)
    Q_PROPERTY(QString importError READ importError NOTIFY importedPoisChanged)
    // Real request 2026-08-08 ("POIs on the watch, please do like for the routes") - real
    // per-POI {name, lat, lon}, parsed from the same raw_output already fetched by
    // refresh(), matching this class's own header comment's caveat: those field names
    // (Name=/Location.Latitude=/Location.Longitude=) are now confirmed directly from real
    // hardware output, not guessed at ahead of a real watch check the way that comment
    // originally worried about.
    Q_PROPERTY(QVariantList onWatchPois READ onWatchPois NOTIFY dataChanged)
    // Removing one on-watch POI (Ambit3 family, 2026-10-25): the backend saves a copy of the
    // list, then writes it back without that POI (POST /api/pois/remove). The Android twin is
    // PoiService.removePoisFromWatch().
    Q_PROPERTY(bool removing READ removing NOTIFY removeResultChanged)
    Q_PROPERTY(QString removeResultText READ removeResultText NOTIFY removeResultChanged)
    Q_PROPERTY(bool removeOk READ removeOk NOTIFY removeResultChanged)

public:
    explicit PoiService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString rawOutput() const { return m_rawOutput; }
    bool ok() const { return m_ok; }
    QString lastError() const { return m_lastError; }
    QString addResultText() const { return m_addResultText; }
    bool addOk() const { return m_addOk; }
    QVariantList importedPois() const { return m_importedPois; }
    QString importError() const { return m_importError; }
    QVariantList onWatchPois() const { return m_onWatchPois; }
    bool removing() const { return m_removing; }
    QString removeResultText() const { return m_removeResultText; }
    bool removeOk() const { return m_removeOk; }

    Q_INVOKABLE void refresh();
    // `type` is the Ambit POI type byte 0-17 (the icon the watch shows); default 17
    // ("Waypoint"). The default keeps the GPX-reimport callers (which have no Ambit type)
    // working unchanged - only the manual add-POI form passes a chosen type.
    // `type` is the POI type byte in the connected watch's own list (DeviceCapabilities.qml).
    // `ele` is the elevation in metres of a POI imported from a GPX, NaN when there is none:
    // a Traverse / Traverse Alpha keeps it, an Ambit3 POI has no altitude.
    Q_INVOKABLE void addPoi(const QString &name, double lat, double lon, int type = 17,
                            double ele = qQNaN());
    Q_INVOKABLE void importGpxFile(const QUrl &fileUrl);
    // `lat`/`lon` exactly as onWatchPois lists them: the backend matches the stored values.
    Q_INVOKABLE void removePoi(const QString &name, double lat, double lon);
    // A single real <wpt> GPX, for LocalFileService.saveText() - built locally (no backend
    // round trip) since a one-point GPX needs nothing the watch/network has to supply beyond
    // the name/lat/lon this page already has in memory.
    Q_INVOKABLE QString buildWaypointGpx(const QString &name, double lat, double lon) const;

signals:
    void loadingChanged();
    void dataChanged();
    void lastErrorChanged();
    void addResultChanged();
    void importedPoisChanged();
    void removeResultChanged();

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QString m_rawOutput;
    bool m_ok = false;
    QString m_lastError;
    QString m_addResultText;
    bool m_addOk = false;
    QVariantList m_importedPois;
    QString m_importError;
    QVariantList m_onWatchPois;
    bool m_removing = false;
    QString m_removeResultText;
    bool m_removeOk = false;

    void setLoading(bool value);
    static QVariantList parseOnWatchPois(const QString &rawOutput);
};
