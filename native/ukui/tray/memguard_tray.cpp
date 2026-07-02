#include <QAction>
#include <QApplication>
#include <QDesktopServices>
#include <QFile>
#include <QIcon>
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
        auto *openDashboardAction = menu->addAction(themedIcon(QStringLiteral("applications-internet"), QStyle::SP_DesktopIcon), QStringLiteral("打开本地控制台"));
        auto *refresh = menu->addAction(themedIcon(QStringLiteral("view-refresh"), QStyle::SP_BrowserReload), QStringLiteral("重新检查状态"));
        menu->addSeparator();
        auto *quit = menu->addAction(themedIcon(QStringLiteral("application-exit"), QStyle::SP_DialogCloseButton), QStringLiteral("退出托盘"));
        tray_.setContextMenu(menu);
        connect(openCenter, &QAction::triggered, this, []() { QProcess::startDetached(QStringLiteral("ukui-control-center"), {}); });
        connect(openDashboardAction, &QAction::triggered, this, &TrayController::openDashboard);
        connect(refresh, &QAction::triggered, this, &TrayController::refresh);
        connect(quit, &QAction::triggered, qApp, &QApplication::quit);
        connect(&tray_, &QSystemTrayIcon::activated, this, [this](QSystemTrayIcon::ActivationReason reason) {
            if (reason == QSystemTrayIcon::Trigger)
                openDashboard();
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

    void refresh()
    {
        if (current_)
            return;
        QStringList arguments{QStringLiteral("openkylin-status")};
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
                    const QString title = overall == QStringLiteral("green") ? QStringLiteral("运行正常") : QStringLiteral("需要处理");
                    const QString body = QStringLiteral("正常 %1 项，需要注意 %2 项，不可用 %3 项。")
                        .arg(summary.value(QStringLiteral("green")).toInt())
                        .arg(summary.value(QStringLiteral("yellow")).toInt())
                        .arg(summary.value(QStringLiteral("red")).toInt());
                    applyState(overall, title, body);
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

    void openDashboard()
    {
        QStringList arguments{QStringLiteral("web"), QStringLiteral("--host"), QStringLiteral("127.0.0.1"), QStringLiteral("--port"), QStringLiteral("8765")};
        const QString program = memctlProgram(&arguments);
        QProcess::startDetached(program, arguments);
        QTimer::singleShot(800, this, []() { QDesktopServices::openUrl(QUrl(QStringLiteral("http://127.0.0.1:8765"))); });
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
