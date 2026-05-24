#include "memguard_ukcc_plugin.h"

#include <QDesktopServices>
#include <QFileInfo>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QProcess>
#include <QRegularExpression>
#include <QPushButton>
#include <QUrl>
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
    auto *desc = new QLabel(QStringLiteral("管理本机 Agent 记忆服务的运行状态、审计完整性、端侧向量能力和诊断信息。"));
    desc->setWordWrap(true);

    auto *statusBox = new QGroupBox(QStringLiteral("运行状态"));
    auto *statusGrid = new QGridLayout(statusBox);
    statusGrid->addWidget(new QLabel(QStringLiteral("服务健康")), 0, 0);
    statusGrid->addWidget(new QLabel(QStringLiteral("检查 systemd/cgroup、存储路径、Python 运行时和本机能力。")), 0, 1);
    statusGrid->addWidget(new QLabel(QStringLiteral("审计完整性")), 1, 0);
    statusGrid->addWidget(new QLabel(QStringLiteral("校验追加式审计哈希链，发现历史篡改或链路分叉时明确报错。")), 1, 1);
    statusGrid->addWidget(new QLabel(QStringLiteral("端侧向量能力")), 2, 0);
    statusGrid->addWidget(new QLabel(QStringLiteral("检查 Kylin embedding/vector SDK，真实探测可用时用于本地检索增强。")), 2, 1);

    auto *opsBox = new QGroupBox(QStringLiteral("常用操作"));
    auto *buttons = new QGridLayout(opsBox);
    auto *health = new QPushButton(QStringLiteral("运行环境检查"));
    auto *audit = new QPushButton(QStringLiteral("校验审计链"));
    auto *sdk = new QPushButton(QStringLiteral("检查端侧向量能力"));
    auto *diagnostics = new QPushButton(QStringLiteral("导出诊断报告"));
    auto *dashboard = new QPushButton(QStringLiteral("打开本地控制台"));
    buttons->addWidget(health, 0, 0);
    buttons->addWidget(audit, 0, 1);
    buttons->addWidget(sdk, 0, 2);
    buttons->addWidget(diagnostics, 1, 0);
    buttons->addWidget(dashboard, 1, 1);

    output_ = new QTextEdit;
    output_->setReadOnly(true);

    layout->addWidget(title);
    layout->addWidget(desc);
    layout->addWidget(statusBox);
    layout->addWidget(opsBox);
    layout->addWidget(output_);

    connect(health, &QPushButton::clicked, this, [this]() { runMemctl(QStringList() << QStringLiteral("doctor")); });
    connect(audit, &QPushButton::clicked, this, [this]() { runMemctl(QStringList() << QStringLiteral("verify-audit")); });
    connect(sdk, &QPushButton::clicked, this, [this]() {
        runMemctl(QStringList() << QStringLiteral("sdk-status") << QStringLiteral("--probe-embedding") << QStringLiteral("--probe-timeout") << QStringLiteral("90"));
    });
    connect(diagnostics, &QPushButton::clicked, this, [this]() {
        runMemctl(QStringList() << QStringLiteral("diagnostics") << QStringLiteral("--probe-sdk") << QStringLiteral("--probe-timeout") << QStringLiteral("90"));
    });
    connect(dashboard, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
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
    if (!process.waitForFinished(180000)) {
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

void MemGuardUkccPlugin::openDashboard()
{
    QString program = QStringLiteral("python3");
    QStringList commandArgs;
    if (QFileInfo::exists(QStringLiteral("/usr/bin/memctl"))) {
        program = QStringLiteral("/usr/bin/memctl");
        commandArgs << QStringLiteral("web") << QStringLiteral("--host") << QStringLiteral("127.0.0.1") << QStringLiteral("--port") << QStringLiteral("8765");
    } else {
        commandArgs << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl")
                    << QStringLiteral("web") << QStringLiteral("--host") << QStringLiteral("127.0.0.1") << QStringLiteral("--port") << QStringLiteral("8765");
    }
    QProcess::startDetached(program, commandArgs);
    QDesktopServices::openUrl(QUrl(QStringLiteral("http://127.0.0.1:8765")));
    if (output_) {
        output_->setPlainText(QStringLiteral("已尝试启动并打开本地控制台：http://127.0.0.1:8765\n如果端口已被占用，请直接刷新浏览器页面或稍后重试。"));
    }
}
