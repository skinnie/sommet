#pragma once
#include <QImage>
#include <QString>

// Pure barcode decode for the webcam scanner (BarcodeScanner) - no Qt Multimedia, no QObject, so
// it can be tested on still images. ZXing-C++ (Apache-2.0) for the grocery symbologies EAN-13/8
// and UPC-A/E; a frame that fails is tried once more sharpened, because fixed-focus laptop
// webcams blur the bars (tested 2026-09-27 on synthetic 720p frames: a tilted, noisy EAN-13 with
// 4.5 px bars under a 1.6 px blur only decodes after the unsharp pass).
namespace BarcodeDecode {
QString decode(const QImage &image);
QImage sharpen(const QImage &gray, int radius = 3, double amount = 2.5);   // unsharp mask, Grayscale8
}
