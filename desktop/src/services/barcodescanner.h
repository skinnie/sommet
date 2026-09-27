#pragma once
#include <QAtomicInt>
#include <QObject>
#include <QPointer>
#include <QQmlEngine>
#include <QThreadPool>
#include <QVideoFrame>
#include <QVideoSink>

// Webcam barcode reader for Ember's meal search (André, 2026-09-27: "nobody uses a bar code
// scanner... everybody has a webcam. go"; issue #20). QML hands it the camera's VideoOutput
// videoSink; each new frame (while no decode is running) is decoded on a worker thread by
// ZXing-C++ (Apache-2.0, fetched at build time) for the grocery symbologies - EAN-13/8, UPC-A/E.
// detected(code) fires once the same code has been read on two frames, so a half-focused frame
// can't slip a wrong product in.
//
// Only compiled when Qt Multimedia is in the kit (CMake SOMMET_WEBCAM_SCAN); otherwise the QML
// never loads the scan view (EmberFoodService.webcamScanAvailable is false).
class BarcodeScanner : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QVideoSink *videoSink READ videoSink WRITE setVideoSink NOTIFY videoSinkChanged)
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
public:
    explicit BarcodeScanner(QObject *parent = nullptr);
    ~BarcodeScanner() override;

    QVideoSink *videoSink() const { return m_sink; }
    void setVideoSink(QVideoSink *sink);
    bool active() const { return m_active; }
    void setActive(bool a);

signals:
    void videoSinkChanged();
    void activeChanged();
    void detected(const QString &code);

private:
    void onFrame(const QVideoFrame &frame);
    void onDecoded(const QString &code);

    QPointer<QVideoSink> m_sink;
    bool m_active = true;
    QAtomicInt m_busy = 0;
    QThreadPool m_pool;
    QString m_last;      // previous frame's read, for the two-frame agreement
};
