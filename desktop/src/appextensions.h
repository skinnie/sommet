#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QString>
#include <QVariantList>
#include <QVariantMap>

// Generic extension seam. Lets an OPTIONAL, locally-compiled module contribute extra
// destinations (a nav entry + a QML page) without any tracked file naming it. The public app
// and CI builds contain nothing from it: the extension sources are compiled in only when the
// build is configured with -DSOMMET_PERSONAL=ON, which pulls in whatever *.cmake files exist
// under desktop/personal/ (a git-ignored, never-shipped directory). This class itself is
// always present but carries no feature specifics and no user data by design — `pages` is
// simply empty in a normal build.
//
// Registration happens from a hook (registerPersonalExtensions(), see main.cpp) that runs
// BEFORE the QML engine loads, so the singleton is already populated the first time the UI
// reads it — hence `pages` is CONSTANT (one registration pass at startup).
class AppExtensions : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // [{ id, label, glyph, iconSource, source }]
    //   id         - stable page id used for nav selection
    //   label      - nav row text
    //   glyph      - Material Symbols codepoint (optional; used if iconSource is empty)
    //   iconSource - URL of a QML component drawn as the nav icon (optional; overrides glyph)
    //   source     - URL of the QML page the content area loads
    Q_PROPERTY(QVariantList pages READ pages CONSTANT)

public:
    explicit AppExtensions(QObject *parent = nullptr);
    QVariantList pages() const { return m_pages; }

    // Called from an extension's registration hook, before the QML engine loads.
    static void addPage(const QVariantMap &page);

private:
    static QVariantList &registry();   // pending registrations, filled before the singleton exists
    QVariantList m_pages;
};
