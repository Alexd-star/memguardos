#include "memguard_ukcc_plugin.h"

#include <QHBoxLayout>
#include <QIcon>
#include <QFileInfo>
#include <QLabel>
#include <QProcess>
#include <QRegularExpression>
#include <QPushButton>
#include <QVBoxLayout>

QString MemGuardUkccPlugin::plugini18nName()
{
    return QStringLiteral("MemGuardOS 记忆安全");
}

int MemGuardUkccPlugin::pluginTypes()
{
    return SECURITY;
}

QWidget *MemGuardUkccPlugin::pluginUi()
{
    if (page_)
        return page_;

    page_ = new QWidget;
    auto *layout = new QVBoxLayout(page_);
    auto *title = new QLabel(QStringLiteral("MemGuardOS 记忆安全"));
    title->setObjectName(QStringLiteral("memguardos-title"));
    auto *desc = new QLabel(QStringLiteral("查看本机 Agent 记忆服务状态、审计链和 openKylin 证据。"));
    desc->setWordWrap(true);

    auto *buttons = new QHBoxLayout;
    auto *doctor = new QPushButton(QStringLiteral("运行 doctor"));
    auto *audit = new QPushButton(QStringLiteral("验证审计链"));
    auto *sdk = new QPushButton(QStringLiteral("检查 Kylin SDK"));
    buttons->addWidget(doctor);
    buttons->addWidget(audit);
    buttons->addWidget(sdk);

    output_ = new QTextEdit;
    output_->setReadOnly(true);

    layout->addWidget(title);
    layout->addWidget(desc);
    layout->addLayout(buttons);
    layout->addWidget(output_);

    connect(doctor, &QPushButton::clicked, this, [this]() { runMemctl(QStringList() << QStringLiteral("doctor")); });
    connect(audit, &QPushButton::clicked, this, [this]() { runMemctl(QStringList() << QStringLiteral("verify-audit")); });
    connect(sdk, &QPushButton::clicked, this, [this]() {
        runMemctl(QStringList() << QStringLiteral("sdk-status") << QStringLiteral("--probe-embedding") << QStringLiteral("--probe-timeout") << QStringLiteral("90"));
    });
    return page_;
}

bool MemGuardUkccPlugin::isShowOnHomePage() const
{
    return true;
}

QIcon MemGuardUkccPlugin::icon() const
{
    return QIcon::fromTheme(QStringLiteral("security-high"));
}

bool MemGuardUkccPlugin::isEnable() const
{
    return true;
}

const QString MemGuardUkccPlugin::name() const
{
    return QStringLiteral("memguardos-memory-security");
}

void MemGuardUkccPlugin::plugin_leave()
{
}

void MemGuardUkccPlugin::runMemctl(const QStringList &args)
{
    if (!output_)
        return;
    QProcess process;
    process.setProcessChannelMode(QProcess::MergedChannels);
    QString program = QStringLiteral("python3");
    QStringList commandArgs;
    if (QFileInfo::exists(QStringLiteral("/usr/bin/memctl"))) {
        program = QStringLiteral("/usr/bin/memctl");
        commandArgs = args;
    } else {
        commandArgs << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl") << args;
    }
    process.start(program, commandArgs);
    if (!process.waitForFinished(120000)) {
        process.kill();
        process.waitForFinished(1000);
        output_->setPlainText(QStringLiteral("命令超时：") + program + QStringLiteral(" ") + commandArgs.join(QStringLiteral(" ")));
        return;
    }
    QString text = QString::fromLocal8Bit(process.readAll());
    text.remove(QRegularExpression(QStringLiteral("\\x1B\\[[0-?]*[ -/]*[@-~]")));
    QStringList kept;
    for (const QString &line : text.split(QLatin1Char('\n'))) {
        if (!line.contains(QStringLiteral("Proxy init engine sessionId")))
            kept << line;
    }
    if (process.exitCode() != 0)
        kept.prepend(QStringLiteral("命令退出码：%1").arg(process.exitCode()));
    output_->setPlainText(kept.join(QLatin1Char('\n')).trimmed());
}
