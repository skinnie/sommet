#include "barcodescanner.h"

#include <QImage>
#include <QMetaObject>

#include "barcodedecode.h"

BarcodeScanner::BarcodeScanner(QObject *parent) : QObject(parent)
{
    m_pool.setMaxThreadCount(1);   // one decode at a time; frames arriving meanwhile are skipped
}

BarcodeScanner::~BarcodeScanner()
{
    setVideoSink(nullptr);
    m_pool.waitForDone();
}

void BarcodeScanner::setVideoSink(QVideoSink *sink)
{
    if (m_sink == sink) return;
    if (m_sink) disconnect(m_sink, nullptr, this, nullptr);
    m_sink = sink;
    if (m_sink) connect(m_sink, &QVideoSink::videoFrameChanged, this, &BarcodeScanner::onFrame);
    emit videoSinkChanged();
}

void BarcodeScanner::setActive(bool a)
{
    if (m_active == a) return;
    m_active = a;
    m_last.clear();
    emit activeChanged();
}

void BarcodeScanner::onFrame(const QVideoFrame &frame)
{
    if (!m_active || !frame.isValid()) return;
    if (!m_busy.testAndSetOrdered(0, 1)) return;   // still decoding the previous frame
    // toImage() on the GUI thread (it may use the render backend); the decode is the slow part.
    const QImage image = frame.toImage();
    m_pool.start([this, image]() {
        const QString code = BarcodeDecode::decode(image);
        QMetaObject::invokeMethod(this, [this, code]() { onDecoded(code); }, Qt::QueuedConnection);
        m_busy.storeRelease(0);
    });
}

void BarcodeScanner::onDecoded(const QString &code)
{
    if (!m_active) return;
    if (code.isEmpty()) return;          // a missed frame doesn't reset the agreement
    if (code == m_last) {
        m_last.clear();
        emit detected(code);
    } else {
        m_last = code;
    }
}
