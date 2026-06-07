#include "memguard_peony_plugin.h"

#include <QFileInfo>
#include <QIcon>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QFile>
#include <QTextStream>
#include <QUrl>

static bool isTextImportCandidate(const QString &path)
{
    QFileInfo info(path);
    if (!info.exists() || !info.isFile() || info.size() <= 0 || info.size() > 1024 * 1024)
        return false;
    const QString suffix = info.suffix().toLower();
    QStringList suffixes;
    suffixes << QStringLiteral("txt")
        << QStringLiteral("md")
        << QStringLiteral("log")
        << QStringLiteral("json")
        << QStringLiteral("yaml")
        << QStringLiteral("yml")
        << QStringLiteral("conf")
        << QStringLiteral("ini")
        << QStringLiteral("service")
        << QStringLiteral("desktop")
        << QStringLiteral("sh")
        << QStringLiteral("py")
        << QStringLiteral("cpp")
        << QStringLiteral("h")
        << QStringLiteral("c")
        << QStringLiteral("toml")
        << QStringLiteral("csv")
        << QStringLiteral("xml")
        << QStringLiteral("html")
        << QStringLiteral("css")
        << QStringLiteral("js")
        << QStringLiteral("ts")
        << QStringLiteral("sql")
        << QStringLiteral("env")
        << QStringLiteral("example");
    return suffixes.contains(suffix);
}

static bool startMemctl(const QStringList &args)
{
    if (QFileInfo::exists(QStringLiteral("/usr/bin/memctl"))) {
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

    QJsonObject event;
    event.insert(QStringLiteral("event_type"), QStringLiteral("peony_context_menu"));
    event.insert(QStringLiteral("path"), path);
    event.insert(QStringLiteral("process"), QStringLiteral("peony"));
    event.insert(QStringLiteral("action"), QStringLiteral("inspect"));
    const QString payload = QString::fromUtf8(QJsonDocument(event).toJson(QJsonDocument::Compact));

    startMemctl(QStringList() << QStringLiteral("ingest-os-event") << QStringLiteral("--json") << payload);

    bool imported = false;
    if (isTextImportCandidate(path)) {
        imported = startMemctl(QStringList()
            << QStringLiteral("ingest")
            << QStringLiteral("--file") << path
            << QStringLiteral("--source-kind") << QStringLiteral("peony_file"));
    }

    QProcess::startDetached(QStringLiteral("notify-send"), QStringList()
        << QStringLiteral("MemGuardOS")
        << (imported
            ? QStringLiteral("已发送 Peony 选中文件：OS 事件已审计，文本内容已进入记忆安全导入链路")
            : QStringLiteral("已发送 Peony 选中项：OS 事件已审计，目录/大文件/非文本文件未直接读取")));
}
