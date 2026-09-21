#include "appextensions.h"

// Pending registrations live in a function-local static so an extension can register before
// the QML singleton is ever constructed (the hook runs before engine load).
QVariantList &AppExtensions::registry()
{
    static QVariantList r;
    return r;
}

void AppExtensions::addPage(const QVariantMap &page)
{
    registry().append(page);
}

AppExtensions::AppExtensions(QObject *parent) : QObject(parent)
{
    m_pages = registry();
}
