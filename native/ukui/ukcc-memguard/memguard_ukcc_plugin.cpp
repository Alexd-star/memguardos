#include "memguard_ukcc_plugin.h"

#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QProcess>
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
    connect(sdk, &QPushButton::clicked, this, [this]() { runMemctl(QStringList() << QStringLiteral("sdk-status")); });
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
    process.start(QStringLiteral("python3"), QStringList() << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl") << args);
    process.waitForFinished(10000);
    output_->setPlainText(QString::fromLocal8Bit(process.readAllStandardOutput()) + QString::fromLocal8Bit(process.readAllStandardError()));
}
