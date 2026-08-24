#include "connectionsservice.h"

#include <QCryptographicHash>
#include <QDateTime>
#include <QDesktopServices>
#include <QHostAddress>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRandomGenerator>
#include <QTcpSocket>
#include <QTimer>
#include <QUrl>
#include <QUrlQuery>

const QString ConnectionsService::kDropbox = QStringLiteral("dropbox");
const QString ConnectionsService::kGoogleDrive = QStringLiteral("googledrive");
const QString ConnectionsService::kOneDrive = QStringLiteral("onedrive");

static const QString kIntervalsGroup = QStringLiteral("connections/intervals_icu");
static const QString kRunalyzeGroup = QStringLiteral("connections/runalyze");
static const QString kStravaGroup = QStringLiteral("connections/strava");

static const QString kStravaAuthUrl = QStringLiteral("https://www.strava.com/oauth/authorize");
static const QString kStravaTokenUrl = QStringLiteral("https://www.strava.com/oauth/token");
// Matches ApiStrava.ts's own STRAVA_SCOPES exactly - "read" for pulling athlete/activity
// data back down the line, "activity:write" for uploading (neither is wired to anything yet,
// this is the Connect step only - see this class's header comment).
static const QString kStravaScopes = QStringLiteral("activity:write,read");

ConnectionsService::ConnectionsService(QObject *parent) : QObject(parent)
{
    m_intervalsIcuAthleteId =
        m_settings.value(kIntervalsGroup + QStringLiteral("/athleteId")).toString();
    m_runalyzeConnected =
        !m_settings.value(kRunalyzeGroup + QStringLiteral("/apiKey")).toString().isEmpty();
    m_stravaClientId = m_settings.value(kStravaGroup + QStringLiteral("/clientId")).toString();
    m_stravaRefreshToken =
        m_settings.value(kStravaGroup + QStringLiteral("/refreshToken")).toString();

    for (const QString &provider : {kDropbox, kGoogleDrive, kOneDrive}) {
        const QString group = cloudGroup(provider);
        m_cloudClientId[provider] = m_settings.value(group + QStringLiteral("/clientId")).toString();
        m_cloudRefreshToken[provider] =
            m_settings.value(group + QStringLiteral("/refreshToken")).toString();
        m_cloudAccessToken[provider] =
            m_settings.value(group + QStringLiteral("/accessToken")).toString();
        m_cloudExpiresAt[provider] = m_settings.value(group + QStringLiteral("/expiresAt")).toLongLong();
    }
}

void ConnectionsService::saveIntervalsIcu(const QString &athleteId, const QString &apiKey)
{
    m_settings.setValue(kIntervalsGroup + QStringLiteral("/athleteId"), athleteId.trimmed());
    m_settings.setValue(kIntervalsGroup + QStringLiteral("/apiKey"), apiKey.trimmed());
    m_intervalsIcuAthleteId = athleteId.trimmed();
    emit intervalsIcuChanged();
}

void ConnectionsService::disconnectIntervalsIcu()
{
    m_settings.remove(kIntervalsGroup);
    m_intervalsIcuAthleteId.clear();
    emit intervalsIcuChanged();
}

QString ConnectionsService::intervalsIcuApiKey() const
{
    return m_settings.value(kIntervalsGroup + QStringLiteral("/apiKey")).toString();
}

bool ConnectionsService::syncFlag(const char *name, bool def) const
{
    return m_settings.value(kIntervalsGroup + QStringLiteral("/sync_") + QLatin1String(name),
                            def).toBool();
}

void ConnectionsService::setSyncFlag(const char *name, bool v)
{
    const QString key = kIntervalsGroup + QStringLiteral("/sync_") + QLatin1String(name);
    if (m_settings.value(key, v).toBool() == v && m_settings.contains(key))
        return;
    m_settings.setValue(key, v);
    emit syncFlagsChanged();
}

int ConnectionsService::syncImportDays() const
{
    return m_settings.value(kIntervalsGroup + QStringLiteral("/sync_importDays"), 90).toInt();
}

void ConnectionsService::setSyncImportDays(int v)
{
    const QString key = kIntervalsGroup + QStringLiteral("/sync_importDays");
    if (m_settings.value(key, 90).toInt() == v && m_settings.contains(key))
        return;
    m_settings.setValue(key, v);
    emit syncFlagsChanged();
}

void ConnectionsService::saveRunalyze(const QString &apiKey)
{
    m_settings.setValue(kRunalyzeGroup + QStringLiteral("/apiKey"), apiKey.trimmed());
    m_runalyzeConnected = !apiKey.trimmed().isEmpty();
    emit runalyzeChanged();
}

void ConnectionsService::disconnectRunalyze()
{
    m_settings.remove(kRunalyzeGroup);
    m_runalyzeConnected = false;
    emit runalyzeChanged();
}

QString ConnectionsService::runalyzeApiKey() const
{
    return m_settings.value(kRunalyzeGroup + QStringLiteral("/apiKey")).toString();
}

void ConnectionsService::setStravaError(const QString &message)
{
    m_stravaError = message;
    emit stravaErrorChanged();
}

void ConnectionsService::stopStravaCallbackServer()
{
    if (m_stravaCallbackServer) {
        m_stravaCallbackServer->close();
        m_stravaCallbackServer->deleteLater();
        m_stravaCallbackServer = nullptr;
    }
}

void ConnectionsService::connectStrava(const QString &clientId, const QString &clientSecret)
{
    stopStravaCallbackServer();

    const QString trimmedId = clientId.trimmed();
    const QString trimmedSecret = clientSecret.trimmed();
    if (trimmedId.isEmpty() || trimmedSecret.isEmpty()) {
        setStravaError(QStringLiteral("Client ID and Client Secret are both required - "
                                       "register a real app at strava.com/settings/api first."));
        return;
    }

    m_settings.setValue(kStravaGroup + QStringLiteral("/clientId"), trimmedId);
    m_settings.setValue(kStravaGroup + QStringLiteral("/clientSecret"), trimmedSecret);
    m_stravaClientId = trimmedId;

    m_stravaCallbackServer = new QTcpServer(this);
    if (!m_stravaCallbackServer->listen(QHostAddress::LocalHost)) {
        setStravaError(QStringLiteral("Could not open a local port for the Strava login "
                                       "callback: %1")
                            .arg(m_stravaCallbackServer->errorString()));
        stopStravaCallbackServer();
        return;
    }
    const quint16 port = m_stravaCallbackServer->serverPort();

    m_stravaError.clear();
    emit stravaErrorChanged();
    m_stravaConnecting = true;
    emit stravaConnectingChanged();

    // Strava's own OAuth docs list "localhost" as a valid Authorization Callback Domain -
    // the standard desktop-app equivalent of the real Android app's custom URL scheme
    // redirect (see this class's header comment for why that path wasn't used here).
    const QString redirectUri = QStringLiteral("http://127.0.0.1:%1/callback").arg(port);

    QUrlQuery authQuery;
    authQuery.addQueryItem(QStringLiteral("client_id"), trimmedId);
    authQuery.addQueryItem(QStringLiteral("redirect_uri"), redirectUri);
    authQuery.addQueryItem(QStringLiteral("response_type"), QStringLiteral("code"));
    authQuery.addQueryItem(QStringLiteral("approval_prompt"), QStringLiteral("auto"));
    authQuery.addQueryItem(QStringLiteral("scope"), kStravaScopes);
    QUrl authUrl(kStravaAuthUrl);
    authUrl.setQuery(authQuery);

    connect(m_stravaCallbackServer, &QTcpServer::newConnection, this,
            [this, trimmedId, trimmedSecret] {
                QTcpSocket *socket = m_stravaCallbackServer->nextPendingConnection();
                connect(socket, &QTcpSocket::readyRead, this,
                        [this, socket, trimmedId, trimmedSecret] {
                            const QByteArray data = socket->readAll();
                            const QString requestLine =
                                QString::fromLatin1(data).split(QStringLiteral("\r\n")).value(0);

                            static const QString body = QStringLiteral(
                                "<html><body style=\"font-family:sans-serif;text-align:center;"
                                "margin-top:15%\"><h2>Sommet</h2><p>You can close this tab "
                                "and go back to Sommet.</p></body></html>");
                            const QByteArray bodyUtf8 = body.toUtf8();
                            const QByteArray response =
                                QStringLiteral("HTTP/1.1 200 OK\r\nContent-Type: "
                                               "text/html\r\nContent-Length: %1\r\nConnection: "
                                               "close\r\n\r\n")
                                    .arg(bodyUtf8.size())
                                    .toUtf8()
                                + bodyUtf8;
                            socket->write(response);
                            socket->flush();
                            socket->disconnectFromHost();

                            // requestLine looks like "GET /callback?code=... HTTP/1.1" - the
                            // path+query is the one space-separated field between the method
                            // and the HTTP version.
                            const QUrl requestUrl(QStringLiteral("http://127.0.0.1")
                                                   + requestLine.section(QLatin1Char(' '), 1, 1));
                            const QUrlQuery callbackQuery(requestUrl);
                            stopStravaCallbackServer();

                            if (callbackQuery.hasQueryItem(QStringLiteral("error"))) {
                                setStravaError(
                                    QStringLiteral("Strava login was cancelled or denied."));
                                m_stravaConnecting = false;
                                emit stravaConnectingChanged();
                                return;
                            }
                            const QString code =
                                callbackQuery.queryItemValue(QStringLiteral("code"));
                            if (code.isEmpty()) {
                                setStravaError(QStringLiteral(
                                    "Strava didn't send back an authorization code."));
                                m_stravaConnecting = false;
                                emit stravaConnectingChanged();
                                return;
                            }
                            exchangeStravaCode(trimmedId, trimmedSecret, code);
                        });
                connect(socket, &QTcpSocket::disconnected, socket, &QTcpSocket::deleteLater);
            });

    // Real people take longer than a few seconds to approve an OAuth prompt in a browser - 3
    // minutes, generous but not unbounded, so an abandoned/closed browser tab doesn't leave
    // the local port open (and "Connect" looking permanently stuck) forever.
    QTimer::singleShot(180000, this, [this] {
        if (m_stravaCallbackServer) {
            setStravaError(QStringLiteral("Timed out waiting for Strava login - try again."));
            m_stravaConnecting = false;
            emit stravaConnectingChanged();
            stopStravaCallbackServer();
        }
    });

    QDesktopServices::openUrl(authUrl);
}

void ConnectionsService::exchangeStravaCode(const QString &clientId, const QString &clientSecret,
                                             const QString &code)
{
    QUrlQuery body;
    body.addQueryItem(QStringLiteral("client_id"), clientId);
    body.addQueryItem(QStringLiteral("client_secret"), clientSecret);
    body.addQueryItem(QStringLiteral("code"), code);
    body.addQueryItem(QStringLiteral("grant_type"), QStringLiteral("authorization_code"));

    QNetworkRequest request{QUrl(kStravaTokenUrl)};
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                       QStringLiteral("application/x-www-form-urlencoded"));
    QNetworkReply *reply =
        m_network.post(request, body.query(QUrl::FullyEncoded).toUtf8());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        m_stravaConnecting = false;
        emit stravaConnectingChanged();

        if (reply->error() != QNetworkReply::NoError) {
            setStravaError(
                QStringLiteral("Strava token exchange failed: %1").arg(reply->errorString()));
            return;
        }
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const QString refreshToken = obj.value(QStringLiteral("refresh_token")).toString();
        if (refreshToken.isEmpty()) {
            setStravaError(QStringLiteral("Strava didn't return a refresh token."));
            return;
        }
        m_settings.setValue(kStravaGroup + QStringLiteral("/refreshToken"), refreshToken);
        m_settings.setValue(kStravaGroup + QStringLiteral("/accessToken"),
                             obj.value(QStringLiteral("access_token")).toString());
        m_settings.setValue(kStravaGroup + QStringLiteral("/expiresAt"),
                             obj.value(QStringLiteral("expires_at")).toVariant());
        m_stravaRefreshToken = refreshToken;
        emit stravaChanged();
    });
}

void ConnectionsService::disconnectStrava()
{
    stopStravaCallbackServer();
    m_settings.remove(kStravaGroup);
    m_stravaClientId.clear();
    m_stravaRefreshToken.clear();
    m_stravaConnecting = false;
    emit stravaChanged();
    emit stravaConnectingChanged();
}

QString ConnectionsService::stravaClientSecret() const
{
    return m_settings.value(kStravaGroup + QStringLiteral("/clientSecret")).toString();
}

// ─── Dropbox / Google Drive / OneDrive - generic loopback OAuth2 engine ───────────────────
//
// One implementation shared by all three (and callable for more providers later) instead of
// pasting the Strava block above three more times - see this class's header comment for why
// each provider differs only in a handful of URLs/scopes/whether it needs PKCE, not in the
// actual callback-server/token-exchange mechanics.

QString ConnectionsService::cloudGroup(const QString &provider)
{
    return QStringLiteral("connections/") + provider;
}

ConnectionsService::CloudOAuthConfig ConnectionsService::cloudConfig(const QString &provider)
{
    CloudOAuthConfig cfg;
    if (provider == kDropbox) {
        cfg.authUrl = QStringLiteral("https://www.dropbox.com/oauth2/authorize");
        cfg.tokenUrl = QStringLiteral("https://api.dropboxapi.com/oauth2/token");
        // Matches the "files.content.write"/"files.content.read" permissions the user needs
        // to tick in the Dropbox App Console's Permissions tab for their own app.
        cfg.scope = QStringLiteral("files.content.write files.content.read");
        cfg.pkce = false;
        // Without this, Dropbox only ever returns a short-lived access token and no
        // refresh_token - "offline" is what asks for the durable one this app stores.
        cfg.extraAuthParams = {{QStringLiteral("token_access_type"), QStringLiteral("offline")}};
    } else if (provider == kGoogleDrive) {
        cfg.authUrl = QStringLiteral("https://accounts.google.com/o/oauth2/v2/auth");
        cfg.tokenUrl = QStringLiteral("https://oauth2.googleapis.com/token");
        // drive.file, not full Drive access - this app only ever sees files/folders it
        // created itself (least privilege; also avoids Google's stricter verification tier).
        cfg.scope = QStringLiteral("https://www.googleapis.com/auth/drive.file");
        cfg.pkce = false;
        // access_type=offline asks for a refresh_token; prompt=consent forces Google to hand
        // one back on every connect (not just the very first authorization ever), which
        // matters for reconnecting after a Disconnect.
        cfg.extraAuthParams = {{QStringLiteral("access_type"), QStringLiteral("offline")},
                                {QStringLiteral("prompt"), QStringLiteral("consent")}};
    } else if (provider == kOneDrive) {
        cfg.authUrl =
            QStringLiteral("https://login.microsoftonline.com/common/oauth2/v2.0/authorize");
        cfg.tokenUrl = QStringLiteral("https://login.microsoftonline.com/common/oauth2/v2.0/token");
        // AppFolder, not full OneDrive access - same least-privilege reasoning as Dropbox/
        // Google above. offline_access is what makes Microsoft hand back a refresh_token.
        cfg.scope = QStringLiteral("Files.ReadWrite.AppFolder offline_access");
        // PKCE, no client secret - Microsoft's own recommended flow for a native/public
        // client (Azure app registrations under "Mobile and desktop applications" don't even
        // issue a usable secret for this flow), and personal Microsoft accounts can't do
        // app-only auth anyway, so there's no real security loss versus Dropbox/Google here.
        cfg.pkce = true;
    }
    return cfg;
}

void ConnectionsService::setCloudError(const QString &provider, const QString &message)
{
    m_cloudError[provider] = message;
    if (provider == kDropbox)
        emit dropboxErrorChanged();
    else if (provider == kGoogleDrive)
        emit googleDriveErrorChanged();
    else if (provider == kOneDrive)
        emit oneDriveErrorChanged();
}

void ConnectionsService::setCloudConnecting(const QString &provider, bool value)
{
    m_cloudConnecting[provider] = value;
    if (provider == kDropbox)
        emit dropboxConnectingChanged();
    else if (provider == kGoogleDrive)
        emit googleDriveConnectingChanged();
    else if (provider == kOneDrive)
        emit oneDriveConnectingChanged();
}

void ConnectionsService::emitCloudChanged(const QString &provider)
{
    if (provider == kDropbox)
        emit dropboxChanged();
    else if (provider == kGoogleDrive)
        emit googleDriveChanged();
    else if (provider == kOneDrive)
        emit oneDriveChanged();
}

void ConnectionsService::stopCloudCallbackServer(const QString &provider)
{
    QTcpServer *server = m_cloudCallbackServers.value(provider);
    if (server) {
        server->close();
        server->deleteLater();
        m_cloudCallbackServers.remove(provider);
    }
}

void ConnectionsService::startCloudOAuth(const QString &provider, const QString &clientId,
                                          const QString &clientSecret)
{
    stopCloudCallbackServer(provider);

    const QString trimmedId = clientId.trimmed();
    const QString trimmedSecret = clientSecret.trimmed();
    const CloudOAuthConfig cfg = cloudConfig(provider);

    if (trimmedId.isEmpty() || (!cfg.pkce && trimmedSecret.isEmpty())) {
        setCloudError(provider,
                      cfg.pkce ? QStringLiteral("Client ID is required - register your own app "
                                                 "first, see the field above for where.")
                               : QStringLiteral("Client ID and Client Secret are both required - "
                                                 "register your own app first, see the field "
                                                 "above for where."));
        return;
    }

    const QString group = cloudGroup(provider);
    m_settings.setValue(group + QStringLiteral("/clientId"), trimmedId);
    if (!cfg.pkce)
        m_settings.setValue(group + QStringLiteral("/clientSecret"), trimmedSecret);
    m_cloudClientId[provider] = trimmedId;

    QTcpServer *server = new QTcpServer(this);
    if (!server->listen(QHostAddress::LocalHost)) {
        setCloudError(provider, QStringLiteral("Could not open a local port for the login "
                                                "callback: %1")
                                     .arg(server->errorString()));
        server->deleteLater();
        return;
    }
    m_cloudCallbackServers[provider] = server;
    const quint16 port = server->serverPort();

    setCloudError(provider, QString()); // clears any old error text and emits the signal for it
    setCloudConnecting(provider, true);

    const QString redirectUri = QStringLiteral("http://127.0.0.1:%1/callback").arg(port);

    // PKCE verifier/challenge - only actually used when cfg.pkce (OneDrive), computed
    // unconditionally since it's cheap and keeps the lambda capture list uniform below.
    QByteArray randomBytes(32, Qt::Uninitialized);
    for (int i = 0; i < randomBytes.size(); ++i)
        randomBytes[i] = static_cast<char>(QRandomGenerator::global()->bounded(256));
    const QString codeVerifier = QString::fromLatin1(
        randomBytes.toBase64(QByteArray::Base64UrlEncoding | QByteArray::OmitTrailingEquals));
    const QByteArray challengeHash =
        QCryptographicHash::hash(codeVerifier.toUtf8(), QCryptographicHash::Sha256);
    const QString codeChallenge = QString::fromLatin1(
        challengeHash.toBase64(QByteArray::Base64UrlEncoding | QByteArray::OmitTrailingEquals));

    QUrlQuery authQuery;
    authQuery.addQueryItem(QStringLiteral("client_id"), trimmedId);
    authQuery.addQueryItem(QStringLiteral("redirect_uri"), redirectUri);
    authQuery.addQueryItem(QStringLiteral("response_type"), QStringLiteral("code"));
    authQuery.addQueryItem(QStringLiteral("scope"), cfg.scope);
    for (const auto &kv : cfg.extraAuthParams)
        authQuery.addQueryItem(kv.first, kv.second);
    if (cfg.pkce) {
        authQuery.addQueryItem(QStringLiteral("code_challenge"), codeChallenge);
        authQuery.addQueryItem(QStringLiteral("code_challenge_method"), QStringLiteral("S256"));
    }
    QUrl authUrl(cfg.authUrl);
    authUrl.setQuery(authQuery);

    connect(server, &QTcpServer::newConnection, this,
            [this, server, provider, trimmedId, trimmedSecret, redirectUri, codeVerifier] {
                QTcpSocket *socket = server->nextPendingConnection();
                connect(socket, &QTcpSocket::readyRead, this,
                        [this, socket, provider, trimmedId, trimmedSecret, redirectUri, codeVerifier] {
                            const QByteArray data = socket->readAll();
                            const QString requestLine =
                                QString::fromLatin1(data).split(QStringLiteral("\r\n")).value(0);

                            static const QString body = QStringLiteral(
                                "<html><body style=\"font-family:sans-serif;text-align:center;"
                                "margin-top:15%\"><h2>Sommet</h2><p>You can close this tab "
                                "and go back to Sommet.</p></body></html>");
                            const QByteArray bodyUtf8 = body.toUtf8();
                            const QByteArray response =
                                QStringLiteral("HTTP/1.1 200 OK\r\nContent-Type: "
                                               "text/html\r\nContent-Length: %1\r\nConnection: "
                                               "close\r\n\r\n")
                                    .arg(bodyUtf8.size())
                                    .toUtf8()
                                + bodyUtf8;
                            socket->write(response);
                            socket->flush();
                            socket->disconnectFromHost();

                            // Same request-line parsing as handleStravaCallback() above.
                            const QUrl requestUrl(QStringLiteral("http://127.0.0.1")
                                                   + requestLine.section(QLatin1Char(' '), 1, 1));
                            const QUrlQuery callbackQuery(requestUrl);
                            stopCloudCallbackServer(provider);

                            if (callbackQuery.hasQueryItem(QStringLiteral("error"))) {
                                setCloudError(provider,
                                              QStringLiteral("Login was cancelled or denied."));
                                setCloudConnecting(provider, false);
                                return;
                            }
                            const QString code =
                                callbackQuery.queryItemValue(QStringLiteral("code"));
                            if (code.isEmpty()) {
                                setCloudError(provider,
                                              QStringLiteral("No authorization code came back."));
                                setCloudConnecting(provider, false);
                                return;
                            }
                            exchangeCloudCode(provider, trimmedId, trimmedSecret, code,
                                               redirectUri, codeVerifier);
                        });
                connect(socket, &QTcpSocket::disconnected, socket, &QTcpSocket::deleteLater);
            });

    // Same generous-but-bounded timeout as connectStrava() above.
    QTimer::singleShot(180000, this, [this, provider] {
        if (m_cloudCallbackServers.value(provider)) {
            setCloudError(provider, QStringLiteral("Timed out waiting for login - try again."));
            setCloudConnecting(provider, false);
            stopCloudCallbackServer(provider);
        }
    });

    QDesktopServices::openUrl(authUrl);
}

void ConnectionsService::exchangeCloudCode(const QString &provider, const QString &clientId,
                                            const QString &clientSecret, const QString &code,
                                            const QString &redirectUri, const QString &codeVerifier)
{
    const CloudOAuthConfig cfg = cloudConfig(provider);

    QUrlQuery body;
    body.addQueryItem(QStringLiteral("client_id"), clientId);
    body.addQueryItem(QStringLiteral("code"), code);
    body.addQueryItem(QStringLiteral("grant_type"), QStringLiteral("authorization_code"));
    body.addQueryItem(QStringLiteral("redirect_uri"), redirectUri);
    if (cfg.pkce)
        body.addQueryItem(QStringLiteral("code_verifier"), codeVerifier);
    else
        body.addQueryItem(QStringLiteral("client_secret"), clientSecret);

    QNetworkRequest request{QUrl(cfg.tokenUrl)};
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                       QStringLiteral("application/x-www-form-urlencoded"));
    QNetworkReply *reply = m_network.post(request, body.query(QUrl::FullyEncoded).toUtf8());
    connect(reply, &QNetworkReply::finished, this, [this, reply, provider] {
        reply->deleteLater();
        setCloudConnecting(provider, false);

        if (reply->error() != QNetworkReply::NoError) {
            setCloudError(provider,
                          QStringLiteral("Token exchange failed: %1").arg(reply->errorString()));
            return;
        }
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const QString refreshToken = obj.value(QStringLiteral("refresh_token")).toString();
        if (refreshToken.isEmpty()) {
            setCloudError(provider, QStringLiteral("Didn't get back a refresh token."));
            return;
        }
        const QString accessToken = obj.value(QStringLiteral("access_token")).toString();
        const qint64 expiresIn = obj.value(QStringLiteral("expires_in")).toVariant().toLongLong();
        const qint64 expiresAt =
            QDateTime::currentSecsSinceEpoch() + (expiresIn > 0 ? expiresIn : 3600);

        const QString group = cloudGroup(provider);
        m_settings.setValue(group + QStringLiteral("/refreshToken"), refreshToken);
        m_settings.setValue(group + QStringLiteral("/accessToken"), accessToken);
        m_settings.setValue(group + QStringLiteral("/expiresAt"), expiresAt);

        m_cloudRefreshToken[provider] = refreshToken;
        m_cloudAccessToken[provider] = accessToken;
        m_cloudExpiresAt[provider] = expiresAt;
        emitCloudChanged(provider);
    });
}

void ConnectionsService::connectDropbox(const QString &clientId, const QString &clientSecret)
{
    startCloudOAuth(kDropbox, clientId, clientSecret);
}

void ConnectionsService::disconnectDropbox()
{
    stopCloudCallbackServer(kDropbox);
    m_settings.remove(cloudGroup(kDropbox));
    m_cloudClientId.remove(kDropbox);
    m_cloudRefreshToken.remove(kDropbox);
    m_cloudAccessToken.remove(kDropbox);
    m_cloudExpiresAt.remove(kDropbox);
    setCloudConnecting(kDropbox, false);
    emit dropboxChanged();
}

QString ConnectionsService::dropboxClientSecret() const
{
    return m_settings.value(cloudGroup(kDropbox) + QStringLiteral("/clientSecret")).toString();
}

void ConnectionsService::connectGoogleDrive(const QString &clientId, const QString &clientSecret)
{
    startCloudOAuth(kGoogleDrive, clientId, clientSecret);
}

void ConnectionsService::disconnectGoogleDrive()
{
    stopCloudCallbackServer(kGoogleDrive);
    m_settings.remove(cloudGroup(kGoogleDrive));
    m_cloudClientId.remove(kGoogleDrive);
    m_cloudRefreshToken.remove(kGoogleDrive);
    m_cloudAccessToken.remove(kGoogleDrive);
    m_cloudExpiresAt.remove(kGoogleDrive);
    setCloudConnecting(kGoogleDrive, false);
    emit googleDriveChanged();
}

QString ConnectionsService::googleDriveClientSecret() const
{
    return m_settings.value(cloudGroup(kGoogleDrive) + QStringLiteral("/clientSecret")).toString();
}

void ConnectionsService::connectOneDrive(const QString &clientId)
{
    // No secret - see cloudConfig()'s kOneDrive branch for why PKCE is used instead.
    startCloudOAuth(kOneDrive, clientId, QString());
}

void ConnectionsService::disconnectOneDrive()
{
    stopCloudCallbackServer(kOneDrive);
    m_settings.remove(cloudGroup(kOneDrive));
    m_cloudClientId.remove(kOneDrive);
    m_cloudRefreshToken.remove(kOneDrive);
    m_cloudAccessToken.remove(kOneDrive);
    m_cloudExpiresAt.remove(kOneDrive);
    setCloudConnecting(kOneDrive, false);
    emit oneDriveChanged();
}
