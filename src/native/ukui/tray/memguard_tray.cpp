#include "../dashboard_launcher.h"

#include <QAction>
#include <QApplication>
#include <QDesktopServices>
#include <QDateTime>
#include <QFile>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLockFile>
#include <QMenu>
#include <QProcess>
#include <QStandardPaths>
#include <QSet>
#include <QStyle>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QUrl>

namespace {

QIcon themedIcon(const QString &name, QStyle::StandardPixmap defaultIcon)
{
    const QIcon icon = QIcon::fromTheme(name);
    return icon.isNull() ? QApplication::style()->standardIcon(defaultIcon) : icon;
}

QJsonObject parseObject(const QByteArray &raw)
{
    const int first = raw.indexOf('{');
    const int last = raw.lastIndexOf('}');
    if (first < 0 || last < first)
        return {};
    const QJsonDocument document = QJsonDocument::fromJson(raw.mid(first, last - first + 1));
    return document.isObject() ? document.object() : QJsonObject();
}

QJsonArray parseArray(const QByteArray &raw)
{
    const int first = raw.indexOf('[');
    const int last = raw.lastIndexOf(']');
    if (first < 0 || last < first)
        return {};
    const QJsonDocument document = QJsonDocument::fromJson(raw.mid(first, last - first + 1));
    return document.isArray() ? document.array() : QJsonArray();
}

QString firstRecommendation(const QJsonObject &payload)
{
    const QJsonArray recommendations = payload.value(QStringLiteral("recommendations")).toArray();
    for (const QJsonValue &value : recommendations) {
        const QString text = value.toString().simplified();
        if (!text.isEmpty())
            return text;
    }
    return {};
}

bool containsAny(const QString &text, const QStringList &keywords)
{
    for (const QString &keyword : keywords) {
        if (text.contains(keyword, Qt::CaseInsensitive))
            return true;
    }
    return false;
}

QString targetFromRecommendation(const QString &recommendation)
{
    if (containsAny(recommendation, {QStringLiteral("SDK"), QStringLiteral("Kylin"), QStringLiteral("embedding"), QStringLiteral("vector"), QStringLiteral("向量")}))
        return QStringLiteral("sdk");
    if (containsAny(recommendation, {QStringLiteral("audit"), QStringLiteral("审计")}))
        return QStringLiteral("audit");
    if (containsAny(recommendation, {QStringLiteral("敏感"), QStringLiteral("阻断"), QStringLiteral("Peony"), QStringLiteral("文件")}))
        return QStringLiteral("sensitive");
    if (containsAny(recommendation, {QStringLiteral("systemd"), QStringLiteral("服务")}))
        return QStringLiteral("service");
    if (containsAny(recommendation, {QStringLiteral("cgroup"), QStringLiteral("CPU"), QStringLiteral("内存"), QStringLiteral("资源")}))
        return QStringLiteral("resources");
    return QStringLiteral("overview");
}

void writeNavigationTarget(const QString &target)
{
    if (target.isEmpty())
        return;
    const QString runtime = QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    QFile file(runtime + QStringLiteral("/memguardos-ukcc-target"));
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text))
        file.write(target.toUtf8());
}

QString memctlProgram(QStringList *arguments)
{
    if (QFile::exists(QStringLiteral("/usr/bin/memctl")))
        return QStringLiteral("/usr/bin/memctl");
    arguments->prepend(QStringLiteral("memguard.cli.memctl"));
    arguments->prepend(QStringLiteral("-m"));
    return QStringLiteral("python3");
}

} // namespace

class TrayController : public QObject
{
public:
    explicit TrayController(QObject *parent = nullptr)
        : QObject(parent)
    {
        tray_.setToolTip(QStringLiteral("MemGuardOS 正在检查状态"));
        tray_.setIcon(themedIcon(QStringLiteral("security-high"), QStyle::SP_MessageBoxInformation));
        auto *menu = new QMenu;
        auto *openCenter = menu->addAction(themedIcon(QStringLiteral("preferences-system"), QStyle::SP_FileDialogDetailedView), QStringLiteral("打开记忆安全中心"));
        auto *recentMenu = menu->addMenu(themedIcon(QStringLiteral("document-open-recent"), QStyle::SP_FileDialogInfoView), QStringLiteral("最近变化"));
        auto *openRecentMemory = recentMenu->addAction(themedIcon(QStringLiteral("document-save"), QStyle::SP_FileDialogInfoView), QStringLiteral("最近学到的记忆"));
        auto *openRecentPreference = recentMenu->addAction(themedIcon(QStringLiteral("preferences-desktop"), QStyle::SP_FileDialogInfoView), QStringLiteral("最近生效的偏好"));
        auto *openRecentAgent = recentMenu->addAction(themedIcon(QStringLiteral("system-run"), QStyle::SP_ComputerIcon), QStringLiteral("最近 Agent 任务"));
        auto *openRecentForget = recentMenu->addAction(themedIcon(QStringLiteral("edit-delete"), QStyle::SP_TrashIcon), QStringLiteral("最近遗忘验证"));
        auto *openSensitive = recentMenu->addAction(themedIcon(QStringLiteral("dialog-warning"), QStyle::SP_MessageBoxWarning), QStringLiteral("查看已保护内容"));
        auto *systemMenu = menu->addMenu(themedIcon(QStringLiteral("utilities-system-monitor"), QStyle::SP_ComputerIcon), QStringLiteral("系统状态"));
        auto *openSdk = systemMenu->addAction(themedIcon(QStringLiteral("system-search"), QStyle::SP_FileDialogContentsView), QStringLiteral("查看本机智能检索"));
        auto *openAudit = systemMenu->addAction(themedIcon(QStringLiteral("security-high"), QStyle::SP_DialogApplyButton), QStringLiteral("查看操作记录"));
        auto *openDashboardAction = menu->addAction(themedIcon(QStringLiteral("applications-internet"), QStyle::SP_DesktopIcon), QStringLiteral("打开本地控制台"));
        auto *refresh = menu->addAction(themedIcon(QStringLiteral("view-refresh"), QStyle::SP_BrowserReload), QStringLiteral("重新检查状态"));
        auto *pauseNotifications = menu->addAction(themedIcon(QStringLiteral("notifications-disabled"), QStyle::SP_MessageBoxInformation), QStringLiteral("暂停通知 1 小时"));
        pauseNotifications->setCheckable(true);
        menu->addSeparator();
        auto *quit = menu->addAction(themedIcon(QStringLiteral("application-exit"), QStyle::SP_DialogCloseButton), QStringLiteral("退出托盘"));
        tray_.setContextMenu(menu);
        connect(openCenter, &QAction::triggered, this, [this]() { openControlCenter(); });
        connect(openSdk, &QAction::triggered, this, [this]() { openControlCenter(QStringLiteral("sdk")); });
        connect(openAudit, &QAction::triggered, this, [this]() { openControlCenter(QStringLiteral("audit")); });
        connect(openSensitive, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#sensitive")); });
        connect(openRecentMemory, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#memory")); });
        connect(openRecentPreference, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#preferences")); });
        connect(openRecentAgent, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#agent")); });
        connect(openRecentForget, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#forget")); });
        connect(openDashboardAction, &QAction::triggered, this, [this]() { openDashboard(); });
        connect(refresh, &QAction::triggered, this, &TrayController::refresh);
        connect(pauseNotifications, &QAction::toggled, this, [this, pauseNotifications](bool checked) {
            notificationsPausedUntil_ = checked ? QDateTime::currentDateTime().addSecs(3600) : QDateTime();
            if (checked) {
                QTimer::singleShot(3600000, this, [this, pauseNotifications]() {
                    notificationsPausedUntil_ = QDateTime();
                    pauseNotifications->setChecked(false);
                });
            }
        });
        connect(quit, &QAction::triggered, qApp, &QApplication::quit);
        connect(&tray_, &QSystemTrayIcon::activated, this, [this](QSystemTrayIcon::ActivationReason reason) {
            if (reason == QSystemTrayIcon::Trigger) {
                if (lastTarget_ == QStringLiteral("sensitive"))
                    openDashboard(QStringLiteral("#sensitive"));
                else
                    openControlCenter(lastTarget_);
            }
        });
        timer_.setInterval(60000);
        connect(&timer_, &QTimer::timeout, this, &TrayController::refresh);
    }

    void start()
    {
        tray_.show();
        timer_.start();
        refresh();
        refreshNotifications();
    }

private:
    QSystemTrayIcon tray_;
    QTimer timer_;
    QProcess *current_ = nullptr;
    QProcess *notificationProcess_ = nullptr;
    QString lastState_;
    QString lastTarget_;
    QDateTime notificationsPausedUntil_;
    QSet<QString> lastNotificationIds_;
    bool notificationSeeded_ = false;

    void refresh()
    {
        if (current_)
            return;
        QStringList arguments{QStringLiteral("openkylin-status"), QStringLiteral("--fast")};
        const QString program = memctlProgram(&arguments);
        current_ = new QProcess(this);
        QProcess *process = current_;
        process->setProcessChannelMode(QProcess::MergedChannels);
        connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError) {
            if (process->property("handled").toBool() || process->property("timedOut").toBool())
                return;
            process->setProperty("handled", true);
            if (current_ == process)
                current_ = nullptr;
            applyState(
                QStringLiteral("error"),
                QStringLiteral("状态检查失败"),
                QStringLiteral("无法启动 MemGuardOS 状态检查，请打开记忆安全中心查看。"));
            process->deleteLater();
        });
        connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this, process](int exitCode, QProcess::ExitStatus exitStatus) {
                if (process->property("handled").toBool()) {
                    if (current_ == process)
                        current_ = nullptr;
                    process->deleteLater();
                    return;
                }
                process->setProperty("handled", true);
                if (current_ == process)
                    current_ = nullptr;
                const QJsonObject payload = parseObject(process->readAll());
                if (process->property("timedOut").toBool()) {
                    applyState(QStringLiteral("error"), QStringLiteral("状态检查超时"), QStringLiteral("状态检查等待时间过长，请稍后重新检查。"));
                } else if (exitStatus != QProcess::NormalExit || exitCode != 0 || payload.isEmpty()) {
                    applyState(QStringLiteral("error"), QStringLiteral("状态检查失败"), QStringLiteral("无法读取 MemGuardOS 状态，请打开控制中心查看。"));
                } else {
                    const QJsonObject summary = payload.value(QStringLiteral("summary")).toObject();
                    const QString overall = summary.value(QStringLiteral("overall")).toString(QStringLiteral("red"));
                    const QString title = overall == QStringLiteral("green")
                        ? QStringLiteral("运行正常")
                        : (overall == QStringLiteral("yellow") ? QStringLiteral("建议确认") : QStringLiteral("需要处理"));
                    const QString body = QStringLiteral("正常 %1 项，建议确认 %2 项，不可用 %3 项。")
                        .arg(summary.value(QStringLiteral("green")).toInt())
                        .arg(summary.value(QStringLiteral("yellow")).toInt())
                        .arg(summary.value(QStringLiteral("red")).toInt());
                    const QString recommendation = firstRecommendation(payload);
                    lastTarget_ = targetFromRecommendation(recommendation);
                    applyState(
                        overall,
                        title,
                        recommendation.isEmpty() ? body : QStringLiteral("%1\n下一步：%2").arg(body, recommendation));
                }
                process->deleteLater();
                refreshNotifications();
            });
        QTimer::singleShot(15000, process, [process]() {
            if (process->state() == QProcess::NotRunning || process->property("handled").toBool())
                return;
            process->setProperty("timedOut", true);
            process->kill();
        });
        process->start(program, arguments);
    }

    void refreshNotifications()
    {
        if (notificationProcess_)
            return;
        QStringList arguments{QStringLiteral("notifications"), QStringLiteral("--limit"), QStringLiteral("20")};
        const QString program = memctlProgram(&arguments);
        notificationProcess_ = new QProcess(this);
        QProcess *process = notificationProcess_;
        process->setProcessChannelMode(QProcess::MergedChannels);
        connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError) {
            if (process->property("handled").toBool() || process->property("timedOut").toBool())
                return;
            process->setProperty("handled", true);
            if (notificationProcess_ == process)
                notificationProcess_ = nullptr;
            process->deleteLater();
        });
        connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this, process](int exitCode, QProcess::ExitStatus exitStatus) {
                if (process->property("handled").toBool()) {
                    if (notificationProcess_ == process)
                        notificationProcess_ = nullptr;
                    process->deleteLater();
                    return;
                }
                process->setProperty("handled", true);
                if (notificationProcess_ == process)
                    notificationProcess_ = nullptr;
                const QJsonArray notifications = parseArray(process->readAll());
                if (!process->property("timedOut").toBool()
                    && exitStatus == QProcess::NormalExit
                    && exitCode == 0
                    && !notifications.isEmpty())
                    consumeNotifications(notifications);
                process->deleteLater();
            });
        QTimer::singleShot(10000, process, [process]() {
            if (process->state() == QProcess::NotRunning || process->property("handled").toBool())
                return;
            process->setProperty("timedOut", true);
            process->kill();
        });
        process->start(program, arguments);
    }

    bool isUserNotification(const QJsonObject &notification) const
    {
        const QString level = notification.value(QStringLiteral("level")).toString().toLower();
        const QString kind = notification.value(QStringLiteral("kind")).toString().toLower();
        if (level == QStringLiteral("critical"))
            return true;
        return kind == QStringLiteral("sensitive_block")
            || kind == QStringLiteral("tool_block")
            || kind == QStringLiteral("forget")
            || kind == QStringLiteral("agent_activity")
            || kind == QStringLiteral("audit_error")
            || kind == QStringLiteral("service_error");
    }

    void consumeNotifications(const QJsonArray &notifications)
    {
        QSet<QString> currentNotificationIds;
        for (const QJsonValue &value : notifications) {
            const QString id = value.toObject().value(QStringLiteral("notification_id")).toString();
            if (!id.isEmpty())
                currentNotificationIds.insert(id);
        }

        if (!notificationSeeded_) {
            lastNotificationIds_ = currentNotificationIds;
            notificationSeeded_ = true;
            return;
        }

        int shown = 0;
        for (int index = notifications.size() - 1; index >= 0 && shown < 3; --index) {
            const QJsonObject notification = notifications.at(index).toObject();
            const QString id = notification.value(QStringLiteral("notification_id")).toString();
            if (id.isEmpty() || lastNotificationIds_.contains(id))
                continue;
            lastNotificationIds_.insert(id);
            if (!isUserNotification(notification) || !notificationsPausedUntil_.isNull())
                continue;
            const QString title = notification.value(QStringLiteral("title")).toString(QStringLiteral("MemGuardOS 有新的重要变化"));
            const QString summary = notification.value(QStringLiteral("summary")).toString(QStringLiteral("请打开记忆安全中心查看详情。"));
            tray_.showMessage(QStringLiteral("MemGuardOS：%1").arg(title), summary, tray_.icon(), 6000);
            ++shown;
        }
        lastNotificationIds_ = currentNotificationIds;
    }

    void applyState(const QString &state, const QString &title, const QString &body)
    {
        if (state == QStringLiteral("green"))
            tray_.setIcon(themedIcon(QStringLiteral("security-high"), QStyle::SP_DialogApplyButton));
        else if (state == QStringLiteral("yellow"))
            tray_.setIcon(themedIcon(QStringLiteral("dialog-warning"), QStyle::SP_MessageBoxWarning));
        else
            tray_.setIcon(themedIcon(QStringLiteral("dialog-error"), QStyle::SP_MessageBoxCritical));
        tray_.setToolTip(QStringLiteral("MemGuardOS：%1\n%2").arg(title, body));
        if (!lastState_.isEmpty() && lastState_ != state && state == QStringLiteral("red") && notificationsPausedUntil_.isNull())
            tray_.showMessage(QStringLiteral("MemGuardOS：%1").arg(title), body, tray_.icon(), 6000);
        lastState_ = state;
    }

    void openControlCenter()
    {
        openControlCenter(QStringLiteral("overview"));
    }

    void openControlCenter(const QString &target)
    {
        const QString destination = target.isEmpty()
            ? (lastTarget_.isEmpty() ? QStringLiteral("overview") : lastTarget_)
            : target;
        writeNavigationTarget(destination);
        QProcess::startDetached(
            QStringLiteral("ukui-control-center"),
            {QStringLiteral("-m"), QStringLiteral("memguardos-memory-security")});
        // 控制中心冷启动时第一次 -m 可能在插件注册完成前被消费，窗口停在设置首页；
        // 延迟后向已运行的单实例重发一次模块跳转，并补写导航目标供插件轮询。
        QTimer::singleShot(2200, this, [destination]() {
            writeNavigationTarget(destination);
            QProcess::startDetached(
                QStringLiteral("ukui-control-center"),
                {QStringLiteral("-m"), QStringLiteral("memguardos-memory-security")});
        });
    }

    void openDashboard()
    {
        openDashboard(QString());
    }

    void openDashboard(const QString &fragment)
    {
        const QString nameSpace = QStringLiteral("default");
        const DashboardRuntimePaths runtimePaths = dashboardRuntimePaths(nameSpace);
        QString authToken;
        QString preparationError;
        if (!ensureDashboardAuthToken(runtimePaths, &authToken, &preparationError)) {
            tray_.showMessage(
                QStringLiteral("MemGuardOS：本地控制台启动失败"),
                QStringLiteral("%1 请重新登录后重试；仍失败时请从记忆安全中心导出系统报告。")
                    .arg(preparationError),
                tray_.icon(),
                6000);
            return;
        }

        QStringList arguments = dashboardProcessArguments(runtimePaths, nameSpace);
        const QString program = memctlProgram(&arguments);
        qint64 launchedPid = 0;
        const bool started = QProcess::startDetached(
            program,
            arguments,
            QString(),
            &launchedPid);
        if (!started) {
            tray_.showMessage(
                QStringLiteral("MemGuardOS：本地控制台启动失败"),
                QStringLiteral("请确认 MemGuardOS 和 memctl 已安装且可以运行，然后从托盘重新打开。"),
                tray_.icon(),
                6000);
            return;
        }
        openDashboardWhenReady(this, runtimePaths, nameSpace, authToken, fragment, launchedPid, [this]() {
            tray_.showMessage(
                QStringLiteral("MemGuardOS：本地控制台暂时无法打开"),
                QStringLiteral("控制台服务未通过身份校验或启动进程已退出。请重新打开；仍失败时请从记忆安全中心导出系统报告。"),
                tray_.icon(),
                6000);
        });
    }
};

int main(int argc, char *argv[])
{
    QApplication application(argc, argv);
    application.setApplicationName(QStringLiteral("MemGuardOS"));
    application.setQuitOnLastWindowClosed(false);
    const QString runtime = QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    QLockFile lock(runtime + QStringLiteral("/memguardos-tray.lock"));
    lock.setStaleLockTime(0);
    if (!lock.tryLock(100))
        return 0;
    TrayController controller;
    controller.start();
    return application.exec();
}
