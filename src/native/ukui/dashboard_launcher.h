#pragma once

#include <QCoreApplication>
#include <QDesktopServices>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileDevice>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QObject>
#include <QProcess>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSaveFile>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>
#include <QUrlQuery>
#include <functional>

struct DashboardRuntimePaths
{
    QString directory;
    QString tokenFile;
    QString stateFile;
    QString lockFile;
};

inline QString dashboardRuntimeKey(const QString &nameSpace)
{
    QString key = nameSpace.trimmed();
    key.replace(QRegularExpression(QStringLiteral("[^A-Za-z0-9_.-]+")), QStringLiteral("_"));
    return key.isEmpty() ? QStringLiteral("default") : key.left(80);
}

inline DashboardRuntimePaths dashboardRuntimePaths(const QString &nameSpace)
{
    QString runtime = QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    if (runtime.isEmpty())
        runtime = QDir::home().filePath(QStringLiteral(".cache/memguardos/runtime"));
    const QString directory = QDir(runtime).filePath(QStringLiteral("memguardos"));
    const QString prefix = QStringLiteral("dashboard-") + dashboardRuntimeKey(nameSpace);
    return {
        directory,
        QDir(directory).filePath(prefix + QStringLiteral(".token")),
        QDir(directory).filePath(prefix + QStringLiteral(".json")),
        QDir(directory).filePath(prefix + QStringLiteral(".lock")),
    };
}

inline QByteArray createDashboardAuthToken()
{
    QByteArray token;
    token.reserve(64);
    for (int index = 0; index < 4; ++index) {
        token.append(
            QByteArray::number(QRandomGenerator::system()->generate64(), 16)
                .rightJustified(16, '0'));
    }
    return token;
}

inline bool dashboardTokenPermissionsAreOwnerOnly(const QFileInfo &info)
{
    const QFileDevice::Permissions required =
        QFileDevice::ReadOwner | QFileDevice::WriteOwner;
    const QFileDevice::Permissions disallowed =
        QFileDevice::ExeOwner | QFileDevice::ReadGroup
        | QFileDevice::WriteGroup | QFileDevice::ExeGroup
        | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther;
    const QFileDevice::Permissions permissions = info.permissions();
    return (permissions & required) == required && !(permissions & disallowed);
}

inline bool ensureDashboardAuthToken(
    const DashboardRuntimePaths &paths,
    QString *token,
    QString *errorMessage)
{
    if (!token)
        return false;
    QDir directory;
    if (!directory.mkpath(paths.directory)) {
        if (errorMessage)
            *errorMessage = QStringLiteral("无法创建用户运行时目录。");
        return false;
    }
    QFile::setPermissions(
        paths.directory,
        QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);

    QFileInfo existingInfo(paths.tokenFile);
    if (existingInfo.exists()) {
        if (existingInfo.isSymLink() || !existingInfo.isFile()
            || !dashboardTokenPermissionsAreOwnerOnly(existingInfo)) {
            if (errorMessage)
                *errorMessage = QStringLiteral("本地控制台凭据文件权限不安全。");
            return false;
        }
        QFile existing(paths.tokenFile);
        if (!existing.open(QIODevice::ReadOnly | QIODevice::Text)) {
            if (errorMessage)
                *errorMessage = QStringLiteral("无法读取本地控制台凭据。");
            return false;
        }
        const QByteArray raw = existing.readAll().trimmed();
        if (raw.size() >= 40) {
            *token = QString::fromLatin1(raw);
            return true;
        }
        existing.close();
    }

    const QByteArray raw = createDashboardAuthToken();
    QSaveFile output(paths.tokenFile);
    if (!output.open(QIODevice::WriteOnly | QIODevice::Text)) {
        if (errorMessage)
            *errorMessage = QStringLiteral("无法创建本地控制台凭据。");
        return false;
    }
    output.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    if (output.write(raw + '\n') != raw.size() + 1 || !output.commit()) {
        if (errorMessage)
            *errorMessage = QStringLiteral("无法安全保存本地控制台凭据。");
        return false;
    }
    QFile::setPermissions(
        paths.tokenFile,
        QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    *token = QString::fromLatin1(raw);
    return true;
}

inline QStringList dashboardProcessArguments(
    const DashboardRuntimePaths &paths,
    const QString &nameSpace)
{
    return {
        QStringLiteral("--namespace"),
        nameSpace,
        QStringLiteral("web"),
        QStringLiteral("--host"),
        QStringLiteral("127.0.0.1"),
        QStringLiteral("--port"),
        QStringLiteral("0"),
        QStringLiteral("--auth-token-file"),
        paths.tokenFile,
        QStringLiteral("--state-file"),
        paths.stateFile,
        QStringLiteral("--lock-file"),
        paths.lockFile,
        QStringLiteral("--idle-timeout"),
        QStringLiteral("900"),
    };
}

namespace memguard_dashboard_detail {

struct DashboardRuntimeState
{
    quint16 port = 0;
    qint64 pid = 0;
    QString nameSpace;
    bool valid = false;
};

inline DashboardRuntimeState readDashboardState(
    const QString &stateFile,
    const QString &expectedNamespace)
{
    QFile file(stateFile);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return {};
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject())
        return {};
    const QJsonObject payload = document.object();
    const int port = payload.value(QStringLiteral("port")).toInt();
    const qint64 pid = payload.value(QStringLiteral("pid")).toVariant().toLongLong();
    const QString nameSpace = payload.value(QStringLiteral("namespace")).toString();
    const bool valid =
        payload.value(QStringLiteral("schema")).toString()
            == QStringLiteral("memguardos-dashboard-runtime-v1")
        && nameSpace == expectedNamespace
        && port > 0 && port <= 65535
        && pid > 0;
    return {
        valid ? static_cast<quint16>(port) : static_cast<quint16>(0),
        valid ? pid : 0,
        nameSpace,
        valid,
    };
}

inline bool processIsAlive(qint64 pid)
{
    if (pid <= 0)
        return false;
#ifdef Q_OS_LINUX
    return QFileInfo::exists(QStringLiteral("/proc/%1").arg(pid));
#else
    return true;
#endif
}

class DashboardReadyPoller final : public QObject
{
public:
    DashboardReadyPoller(
        QObject *parent,
        const DashboardRuntimePaths &paths,
        const QString &expectedNamespace,
        const QString &authToken,
        const QString &targetTab,
        qint64 launchedPid,
        const std::function<void()> &onFailure)
        : QObject(parent)
        , paths_(paths)
        , expectedNamespace_(expectedNamespace)
        , authToken_(authToken)
        , targetTab_(targetTab)
        , launchedPid_(launchedPid)
        , onFailure_(onFailure)
        , manager_(new QNetworkAccessManager(this))
        , timer_(new QTimer(this))
    {
        timer_->setSingleShot(true);
        QObject::connect(timer_, &QTimer::timeout, this, [this]() { pollState(); });
    }

    void start()
    {
        elapsed_.start();
        scheduleNext(0);
    }

private:
    static constexpr qint64 timeoutMs_ = 30000;

    DashboardRuntimePaths paths_;
    QString expectedNamespace_;
    QString authToken_;
    QString targetTab_;
    qint64 launchedPid_ = 0;
    std::function<void()> onFailure_;
    QNetworkAccessManager *manager_;
    QTimer *timer_;
    QElapsedTimer elapsed_;
    int attempt_ = 0;
    bool finished_ = false;

    void scheduleNext(int delayMs)
    {
        if (finished_)
            return;
        const qint64 remainingMs = timeoutMs_ - elapsed_.elapsed();
        if (remainingMs <= 0) {
            fail();
            return;
        }
        timer_->start(static_cast<int>(qMin<qint64>(delayMs, remainingMs)));
    }

    void pollState()
    {
        if (finished_)
            return;
        const DashboardRuntimeState state =
            readDashboardState(paths_.stateFile, expectedNamespace_);
        if (!state.valid || !processIsAlive(state.pid)) {
            ++attempt_;
            if (elapsed_.elapsed() >= 1200 && !processIsAlive(launchedPid_)) {
                fail();
                return;
            }
            scheduleNext(qMin(1000, 100 * (1 << qMin(attempt_, 3))));
            return;
        }
        pollReady(state.port);
    }

    void pollReady(quint16 port)
    {
        const qint64 remainingMs = timeoutMs_ - elapsed_.elapsed();
        if (remainingMs <= 0) {
            fail();
            return;
        }

        QNetworkRequest request{
            QUrl(QStringLiteral("http://127.0.0.1:%1/readyz").arg(port))};
        request.setRawHeader(
            QByteArrayLiteral("Authorization"),
            QByteArrayLiteral("Bearer ") + authToken_.toUtf8());
        QNetworkReply *reply = manager_->get(request);
        const int replyTimeoutMs = static_cast<int>(qMin<qint64>(1500, remainingMs));
        QTimer::singleShot(replyTimeoutMs, reply, [reply]() {
            if (!reply->isFinished())
                reply->abort();
        });
        QObject::connect(reply, &QNetworkReply::finished, this, [this, reply, port]() {
            const int httpStatus =
                reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            const QByteArray body = reply->readAll();
            const bool transportReady =
                reply->error() == QNetworkReply::NoError && httpStatus == 200;
            reply->deleteLater();

            QJsonParseError parseError;
            const QJsonDocument document = QJsonDocument::fromJson(body, &parseError);
            const QJsonObject payload =
                parseError.error == QJsonParseError::NoError && document.isObject()
                ? document.object()
                : QJsonObject();
            const bool identityReady =
                payload.value(QStringLiteral("status")).toString() == QStringLiteral("ready")
                && payload.value(QStringLiteral("namespace")).toString() == expectedNamespace_;

            if (transportReady && identityReady) {
                openBrowser(port);
                return;
            }
            if (httpStatus == 401 || httpStatus == 403
                || (transportReady && !identityReady)) {
                fail();
                return;
            }

            ++attempt_;
            if (elapsed_.elapsed() >= 1200 && !processIsAlive(launchedPid_)) {
                const DashboardRuntimeState current =
                    readDashboardState(paths_.stateFile, expectedNamespace_);
                if (!current.valid) {
                    fail();
                    return;
                }
            }
            scheduleNext(qMin(2000, 100 * (1 << qMin(attempt_, 4))));
        });
    }

    void openBrowser(quint16 port)
    {
        if (finished_)
            return;
        finished_ = true;
        timer_->stop();

        QString tab = targetTab_.trimmed();
        while (tab.startsWith(QLatin1Char('#')))
            tab.remove(0, 1);
        QUrlQuery fragment;
        fragment.addQueryItem(QStringLiteral("token"), authToken_);
        if (!tab.isEmpty())
            fragment.addQueryItem(QStringLiteral("tab"), tab);
        QUrl url(QStringLiteral("http://127.0.0.1:%1/").arg(port));
        url.setFragment(fragment.toString(QUrl::FullyEncoded));

        if (!QDesktopServices::openUrl(url) && onFailure_)
            onFailure_();
        deleteLater();
    }

    void fail()
    {
        if (finished_)
            return;
        finished_ = true;
        timer_->stop();
        if (onFailure_)
            onFailure_();
        deleteLater();
    }
};

} // namespace memguard_dashboard_detail

inline void openDashboardWhenReady(
    QObject *context,
    const DashboardRuntimePaths &paths,
    const QString &expectedNamespace,
    const QString &authToken,
    const QString &targetTab,
    qint64 launchedPid,
    const std::function<void()> &onFailure = std::function<void()>())
{
    QObject *owner = context ? context : QCoreApplication::instance();
    if (!owner || authToken.isEmpty() || paths.stateFile.isEmpty()) {
        if (onFailure)
            onFailure();
        return;
    }
    auto *poller = new memguard_dashboard_detail::DashboardReadyPoller(
        owner,
        paths,
        expectedNamespace,
        authToken,
        targetTab,
        launchedPid,
        onFailure);
    poller->start();
}
