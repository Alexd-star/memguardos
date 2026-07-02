#include "memguard_peony_plugin.h"

#include <QIcon>
#include <QProcess>
#include <QFile>
#include <QTextStream>
#include <QUrl>

static bool startMemctl(const QStringList &args)
{
    if (QFile::exists(QStringLiteral("/usr/bin/memctl"))) {
        return QProcess::startDetached(QStringLiteral("/usr/bin/memctl"), args);
    }
    QStringList pythonArgs;
    pythonArgs << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl") << args;
    return QProcess::startDetached(QStringLiteral("python3"), pythonArgs);
}

Peony::PluginInterface::PluginType MemGuardPeonyPlugin::pluginType()
{
    return Peony::PluginInterface::MenuPlugin;
}

const QString MemGuardPeonyPlugin::name()
{
    return QStringLiteral("memguardos-peony-menu-plugin");
}

const QString MemGuardPeonyPlugin::description()
{
    return QStringLiteral("MemGuardOS 记忆安全扫描右键菜单");
}

const QIcon MemGuardPeonyPlugin::icon()
{
    return QIcon::fromTheme(QStringLiteral("security-high"));
}

void MemGuardPeonyPlugin::setEnable(bool enable)
{
    enabled_ = enable;
}

bool MemGuardPeonyPlugin::isEnable()
{
    return enabled_;
}

QString MemGuardPeonyPlugin::testPlugin()
{
    return QStringLiteral("memguardos-peony-ok");
}

QList<QAction *> MemGuardPeonyPlugin::menuActions(Peony::MenuPluginInterface::Types types, const QString &uri, const QStringList &selectionUris)
{
    QList<QAction *> actions;
    QFile log(QStringLiteral("/tmp/memguardos_peony_plugin.log"));
    if (log.open(QIODevice::Append | QIODevice::Text)) {
        QTextStream out(&log);
        out << "menuActions enabled=" << enabled_
            << " types=" << static_cast<int>(types)
            << " uri=" << uri
            << " selections=" << selectionUris.join(QStringLiteral(","))
            << "\n";
    }

    const bool supportedMenu = types.testFlag(Peony::MenuPluginInterface::DirectoryView)
        || types.testFlag(Peony::MenuPluginInterface::DesktopWindow)
        || (!selectionUris.isEmpty());
    if (!enabled_ || !supportedMenu)
        return actions;

    lastSelection_ = selectionUris;
    fallbackUri_ = uri;

    auto *action = new QAction(QIcon::fromTheme(QStringLiteral("security-high")), QStringLiteral("用 MemGuardOS 扫描并导入记忆"));
    action->setObjectName(QStringLiteral("memguardos-peony-memory-scan"));
    QObject::connect(action, &QAction::triggered, this, &MemGuardPeonyPlugin::sendSelectionToMemGuard);
    actions << action;
    return actions;
}

void MemGuardPeonyPlugin::sendSelectionToMemGuard() const
{
    QString target = lastSelection_.isEmpty() ? fallbackUri_ : lastSelection_.first();
    QString path = QUrl(target).isLocalFile() ? QUrl(target).toLocalFile() : target;
    if (path.isEmpty())
        return;

    const bool started = startMemctl(QStringList()
        << QStringLiteral("desktop")
        << QStringLiteral("scan-file")
        << QStringLiteral("--file") << path
        << QStringLiteral("--ingest")
        << QStringLiteral("--notify"));

    if (!started) {
        QProcess::startDetached(QStringLiteral("notify-send"), QStringList()
            << QStringLiteral("MemGuardOS")
            << QStringLiteral("文件扫描命令启动失败，请确认 memctl 已安装。"));
    }
}
