#include <QAction>
#include <QApplication>
#include <QDesktopServices>
#include <QFile>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLockFile>
#include <QMenu>
#include <QProcess>
#include <QStandardPaths>
#include <QStyle>
#include <QSystemTrayIcon>
#include <QTimer>
#include <QUrl>

namespace {

QIcon themedIcon(const QString &name, QStyle::StandardPixmap fallback)
{
    const QIcon icon = QIcon::fromTheme(name);
    return icon.isNull() ? QApplication::style()->standardIcon(fallback) : icon;
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
    if (containsAny(recommendation, {QStringLiteral("memguardd"), QStringLiteral("systemd"), QStringLiteral("服务")}))
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
        auto *openSdk = menu->addAction(themedIcon(QStringLiteral("system-search"), QStyle::SP_FileDialogContentsView), QStringLiteral("查看端侧向量能力"));
        auto *openAudit = menu->addAction(themedIcon(QStringLiteral("security-high"), QStyle::SP_DialogApplyButton), QStringLiteral("查看审计状态"));
        auto *openSensitive = menu->addAction(themedIcon(QStringLiteral("dialog-warning"), QStyle::SP_MessageBoxWarning), QStringLiteral("查看最近一次阻断记录"));
        auto *openDashboardAction = menu->addAction(themedIcon(QStringLiteral("applications-internet"), QStyle::SP_DesktopIcon), QStringLiteral("打开本地控制台"));
        auto *refresh = menu->addAction(themedIcon(QStringLiteral("view-refresh"), QStyle::SP_BrowserReload), QStringLiteral("重新检查状态"));
        menu->addSeparator();
        auto *quit = menu->addAction(themedIcon(QStringLiteral("application-exit"), QStyle::SP_DialogCloseButton), QStringLiteral("退出托盘"));
        tray_.setContextMenu(menu);
        connect(openCenter, &QAction::triggered, this, [this]() { openControlCenter(); });
        connect(openSdk, &QAction::triggered, this, [this]() { openControlCenter(QStringLiteral("sdk")); });
        connect(openAudit, &QAction::triggered, this, [this]() { openControlCenter(QStringLiteral("audit")); });
        connect(openSensitive, &QAction::triggered, this, [this]() { openDashboard(QStringLiteral("#audit")); });
        connect(openDashboardAction, &QAction::triggered, this, &TrayController::openDashboard);
        connect(refresh, &QAction::triggered, this, &TrayController::refresh);
        connect(quit, &QAction::triggered, qApp, &QApplication::quit);
        connect(&tray_, &QSystemTrayIcon::activated, this, [this](QSystemTrayIcon::ActivationReason reason) {
            if (reason == QSystemTrayIcon::Trigger) {
                if (lastTarget_ == QStringLiteral("sensitive"))
                    openDashboard(QStringLiteral("#audit"));
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
    }

private:
    QSystemTrayIcon tray_;
    QTimer timer_;
    QProcess *current_ = nullptr;
    QString lastState_;
    QString lastTarget_;

    void refresh()
    {
        if (current_)
            return;
        QStringList arguments{QStringLiteral("openkylin-status"), QStringLiteral("--fast")};
        const QString program = memctlProgram(&arguments);
        current_ = new QProcess(this);
        current_->setProcessChannelMode(QProcess::MergedChannels);
        connect(current_, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this](int exitCode, QProcess::ExitStatus) {
                QProcess *finished = current_;
                current_ = nullptr;
                const QJsonObject payload = parseObject(finished->readAll());
                if (exitCode != 0 || payload.isEmpty()) {
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
                finished->deleteLater();
            });
        current_->start(program, arguments);
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
        if (!lastState_.isEmpty() && lastState_ != state)
            tray_.showMessage(QStringLiteral("MemGuardOS：%1").arg(title), body, tray_.icon(), 6000);
        lastState_ = state;
    }

    void openControlCenter()
    {
        openControlCenter(QString());
    }

    void openControlCenter(const QString &target)
    {
        writeNavigationTarget(target.isEmpty() ? lastTarget_ : target);
        QProcess::startDetached(QStringLiteral("ukui-control-center"), {});
    }

    void openDashboard()
    {
        openDashboard(QString());
    }

    void openDashboard(const QString &fragment)
    {
        QStringList arguments{QStringLiteral("web"), QStringLiteral("--host"), QStringLiteral("127.0.0.1"), QStringLiteral("--port"), QStringLiteral("8765")};
        const QString program = memctlProgram(&arguments);
        QProcess::startDetached(program, arguments);
        QTimer::singleShot(800, this, [fragment]() {
            QDesktopServices::openUrl(QUrl(QStringLiteral("http://127.0.0.1:8765%1").arg(fragment)));
        });
    }
};

int main(int argc, char *argv[])
{
    QApplication application(argc, argv);
    application.setApplicationName(QStringLiteral("MemGuardOS"));
    application.setQuitOnLastWindowClosed(false);
    if (!QSystemTrayIcon::isSystemTrayAvailable())
        return 2;
    const QString runtime = QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    QLockFile lock(runtime + QStringLiteral("/memguardos-tray.lock"));
    lock.setStaleLockTime(0);
    if (!lock.tryLock(100))
        return 0;
    TrayController controller;
    controller.start();
    return application.exec();
}
