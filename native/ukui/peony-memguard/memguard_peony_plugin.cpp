#include "memguard_peony_plugin.h"

#include <QFileInfo>
#include <QIcon>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QFile>
#include <QTextStream>
#include <QUrl>

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

    auto *action = new QAction(QIcon::fromTheme(QStringLiteral("security-high")), QStringLiteral("发送到 MemGuardOS 记忆安全扫描"));
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

    QJsonObject event;
    event.insert(QStringLiteral("event_type"), QStringLiteral("peony_context_menu"));
    event.insert(QStringLiteral("path"), path);
    event.insert(QStringLiteral("process"), QStringLiteral("peony"));
    event.insert(QStringLiteral("action"), QStringLiteral("inspect"));
    const QString payload = QString::fromUtf8(QJsonDocument(event).toJson(QJsonDocument::Compact));

    if (QFileInfo::exists(QStringLiteral("/usr/bin/memctl"))) {
        QProcess::startDetached(QStringLiteral("/usr/bin/memctl"), QStringList()
            << QStringLiteral("ingest-os-event") << QStringLiteral("--json") << payload);
    } else {
        QProcess::startDetached(QStringLiteral("python3"), QStringList()
            << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl")
            << QStringLiteral("ingest-os-event") << QStringLiteral("--json") << payload);
    }
    QProcess::startDetached(QStringLiteral("notify-send"), QStringList()
        << QStringLiteral("MemGuardOS")
        << QStringLiteral("已将 Peony 选中文件发送到记忆安全扫描"));
}
