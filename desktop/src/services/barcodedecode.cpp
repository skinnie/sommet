#include "barcodedecode.h"

#include <QVector>
#include <algorithm>

#include "ReadBarcode.h"   // ZXing-C++ (build tree layout)

namespace BarcodeDecode {
namespace {
QString read(const QImage &gray)
{
    const ZXing::ImageView view(gray.constBits(), gray.width(), gray.height(),
                                ZXing::ImageFormat::Lum, int(gray.bytesPerLine()));
    ZXing::ReaderOptions opts;
    opts.setFormats(ZXing::BarcodeFormat::EAN13 | ZXing::BarcodeFormat::EAN8
                    | ZXing::BarcodeFormat::UPCA | ZXing::BarcodeFormat::UPCE);
    opts.setTryHarder(true);   // webcams are soft-focus and noisy
    opts.setTryRotate(true);   // a pack held sideways
    const auto result = ZXing::ReadBarcode(view, opts);
    return result.isValid() ? QString::fromStdString(result.text()) : QString();
}

// One horizontal + one vertical box-blur pass of the given radius (running sums, O(pixels)).
QVector<float> boxBlur(const QImage &g, int r)
{
    const int w = g.width(), h = g.height();
    QVector<float> tmp(w * h), out(w * h);
    for (int y = 0; y < h; ++y) {
        const uchar *row = g.constScanLine(y);
        float sum = 0; int n = 0;
        for (int x = 0; x <= std::min(r, w - 1); ++x) { sum += row[x]; ++n; }
        for (int x = 0; x < w; ++x) {
            tmp[y * w + x] = sum / n;
            if (x + r + 1 < w) { sum += row[x + r + 1]; ++n; }
            if (x - r >= 0) { sum -= row[x - r]; --n; }
        }
    }
    for (int x = 0; x < w; ++x) {
        float sum = 0; int n = 0;
        for (int y = 0; y <= std::min(r, h - 1); ++y) { sum += tmp[y * w + x]; ++n; }
        for (int y = 0; y < h; ++y) {
            out[y * w + x] = sum / n;
            if (y + r + 1 < h) { sum += tmp[(y + r + 1) * w + x]; ++n; }
            if (y - r >= 0) { sum -= tmp[(y - r) * w + x]; --n; }
        }
    }
    return out;
}
} // namespace

QImage sharpen(const QImage &gray, int radius, double amount)
{
    const QVector<float> blur = boxBlur(gray, radius);
    QImage out(gray.size(), QImage::Format_Grayscale8);
    const int w = gray.width();
    for (int y = 0; y < gray.height(); ++y) {
        const uchar *src = gray.constScanLine(y);
        uchar *dst = out.scanLine(y);
        for (int x = 0; x < w; ++x) {
            const double v = src[x] + amount * (src[x] - blur[y * w + x]);
            dst[x] = uchar(std::clamp(v, 0.0, 255.0));
        }
    }
    return out;
}

QString decode(const QImage &image)
{
    if (image.isNull()) return {};
    const QImage gray = image.convertToFormat(QImage::Format_Grayscale8);
    const QString plain = read(gray);
    return plain.isEmpty() ? read(sharpen(gray)) : plain;
}
} // namespace BarcodeDecode
