#include "memguard_ukcc_plugin.h"

#include <QAbstractItemView>
#include <QDesktopServices>
#include <QDialog>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMessageBox>
#include <QPalette>
#include <QPointer>
#include <QProcess>
#include <QPushButton>
#include <QRegularExpression>
#include <QTabWidget>
#include <QTextEdit>
#include <QTimer>
#include <QToolButton>
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QUrl>
#include <QVBoxLayout>

namespace {

QString cleanedOutput(const QByteArray &raw)
{
    QString text = QString::fromLocal8Bit(raw);
    text.remove(QRegularExpression(QStringLiteral("\\x1B\\[[0-?]*[ -/]*[@-~]")));
    QStringList kept;
    for (const QString &line : text.split(QLatin1Char('\n'))) {
        if (!line.contains(QStringLiteral("Proxy init engine sessionId")))
            kept << line;
    }
    return kept.join(QLatin1Char('\n')).trimmed();
}

QJsonObject parsedObject(const QString &text)
{
    const int first = text.indexOf(QLatin1Char('{'));
    const int last = text.lastIndexOf(QLatin1Char('}'));
    if (first < 0 || last < first)
        return {};
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(text.mid(first, last - first + 1).toUtf8(), &error);
    return error.error == QJsonParseError::NoError && document.isObject() ? document.object() : QJsonObject();
}

QString levelText(const QString &level)
{
    if (level == QStringLiteral("green"))
        return QStringLiteral("正常");
    if (level == QStringLiteral("yellow"))
        return QStringLiteral("需要注意");
    return QStringLiteral("不可用");
}

QIcon levelIcon(const QString &level)
{
    if (level == QStringLiteral("green"))
        return QIcon::fromTheme(QStringLiteral("dialog-ok-apply"));
    if (level == QStringLiteral("yellow"))
        return QIcon::fromTheme(QStringLiteral("dialog-warning"));
    return QIcon::fromTheme(QStringLiteral("dialog-error"));
}

QString boolText(bool value)
{
    return value ? QStringLiteral("正常") : QStringLiteral("需要检查");
}

} // namespace

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
    const QPalette palette = page_->palette();
    Q_UNUSED(palette);
    auto *layout = new QVBoxLayout(page_);
    auto *title = new QLabel(QStringLiteral("MemGuardOS 记忆安全"));
    QFont titleFont = title->font();
    titleFont.setPointSizeF(titleFont.pointSizeF() * 1.25);
    titleFont.setBold(true);
    title->setFont(titleFont);
    auto *description = new QLabel(QStringLiteral("管理本机 Agent 记忆服务、审计完整性、端侧向量能力与桌面集成。"));
    description->setWordWrap(true);
    layout->addWidget(title);
    layout->addWidget(description);

    auto *summaryBox = new QGroupBox(QStringLiteral("当前状态"));
    auto *summaryLayout = new QHBoxLayout(summaryBox);
    summaryIcon_ = new QLabel;
    summaryIcon_->setFixedSize(32, 32);
    summaryIcon_->setScaledContents(true);
    auto *summaryText = new QVBoxLayout;
    summaryTitle_ = new QLabel(QStringLiteral("正在检查…"));
    QFont summaryFont = summaryTitle_->font();
    summaryFont.setBold(true);
    summaryTitle_->setFont(summaryFont);
    summaryDescription_ = new QLabel(QStringLiteral("正在读取 systemd、审计、SDK 和桌面组件状态。"));
    summaryDescription_->setWordWrap(true);
    summaryText->addWidget(summaryTitle_);
    summaryText->addWidget(summaryDescription_);
    auto *refresh = new QPushButton(QIcon::fromTheme(QStringLiteral("view-refresh")), QStringLiteral("重新检查状态"));
    refresh->setAccessibleName(QStringLiteral("重新检查 MemGuardOS 状态"));
    summaryLayout->addWidget(summaryIcon_);
    summaryLayout->addLayout(summaryText, 1);
    summaryLayout->addWidget(refresh);
    layout->addWidget(summaryBox);

    auto *tabs = new QTabWidget;
    tabs->setAccessibleName(QStringLiteral("MemGuardOS 设置分类"));
    tabs->addTab(buildOverviewTab(), QIcon::fromTheme(QStringLiteral("view-list-details")), QStringLiteral("总览"));
    tabs->addTab(buildServiceTab(), QIcon::fromTheme(QStringLiteral("preferences-system")), QStringLiteral("服务与安全"));
    tabs->addTab(buildDiagnosticsTab(), QIcon::fromTheme(QStringLiteral("tools-report-bug")), QStringLiteral("诊断"));
    layout->addWidget(tabs, 1);

    connect(refresh, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    QTimer::singleShot(0, this, &MemGuardUkccPlugin::refreshStatus);
    return page_;
}

QWidget *MemGuardUkccPlugin::buildOverviewTab()
{
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    components_ = new QTreeWidget;
    components_->setColumnCount(3);
    components_->setHeaderLabels({QStringLiteral("组件"), QStringLiteral("状态"), QStringLiteral("说明")});
    components_->setRootIsDecorated(false);
    components_->setAlternatingRowColors(true);
    components_->setSelectionMode(QAbstractItemView::SingleSelection);
    components_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    components_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    components_->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    components_->setAccessibleName(QStringLiteral("本机组件状态列表"));
    layout->addWidget(components_);
    auto *hint = new QLabel(QStringLiteral("状态来自本机实时检查。选择【重新检查状态】可刷新服务、SDK、审计和资源信息。"));
    hint->setWordWrap(true);
    layout->addWidget(hint);
    return tab;
}

QWidget *MemGuardUkccPlugin::buildServiceTab()
{
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    auto *serviceBox = new QGroupBox(QStringLiteral("记忆服务"));
    auto *serviceLayout = new QVBoxLayout(serviceBox);
    serviceState_ = new QLabel(QStringLiteral("等待检查"));
    serviceState_->setWordWrap(true);
    serviceLayout->addWidget(serviceState_);
    auto *serviceButtons = new QHBoxLayout;
    auto *start = new QPushButton(QIcon::fromTheme(QStringLiteral("media-playback-start")), QStringLiteral("启动服务"));
    auto *stop = new QPushButton(QIcon::fromTheme(QStringLiteral("media-playback-stop")), QStringLiteral("停止服务"));
    auto *restart = new QPushButton(QIcon::fromTheme(QStringLiteral("view-refresh")), QStringLiteral("重启服务"));
    auto *logs = new QPushButton(QIcon::fromTheme(QStringLiteral("text-x-log")), QStringLiteral("查看服务日志"));
    serviceButtons->addWidget(start);
    serviceButtons->addWidget(stop);
    serviceButtons->addWidget(restart);
    serviceButtons->addWidget(logs);
    serviceButtons->addStretch();
    serviceLayout->addLayout(serviceButtons);
    layout->addWidget(serviceBox);

    auto *sdkBox = new QGroupBox(QStringLiteral("端侧向量能力"));
    auto *sdkLayout = new QVBoxLayout(sdkBox);
    sdkState_ = new QLabel(QStringLiteral("等待检查"));
    sdkState_->setWordWrap(true);
    auto *sdkButtons = new QHBoxLayout;
    auto *sdkCheck = new QPushButton(QStringLiteral("检查端侧向量能力"));
    auto *warmup = new QPushButton(QStringLiteral("预热 Kylin SDK"));
    sdkButtons->addWidget(sdkCheck);
    sdkButtons->addWidget(warmup);
    sdkButtons->addStretch();
    sdkLayout->addWidget(sdkState_);
    sdkLayout->addLayout(sdkButtons);
    layout->addWidget(sdkBox);

    auto *securityBox = new QGroupBox(QStringLiteral("审计与系统保护"));
    auto *securityLayout = new QVBoxLayout(securityBox);
    auditState_ = new QLabel(QStringLiteral("等待检查"));
    auditState_->setWordWrap(true);
    auto *securityButtons = new QHBoxLayout;
    auto *verifyAudit = new QPushButton(QStringLiteral("校验审计链"));
    auto *installAudit = new QPushButton(QStringLiteral("安装审计规则"));
    auto *bpfTest = new QPushButton(QStringLiteral("运行 BPF 安全验证"));
    securityButtons->addWidget(verifyAudit);
    securityButtons->addWidget(installAudit);
    securityButtons->addWidget(bpfTest);
    securityButtons->addStretch();
    securityLayout->addWidget(auditState_);
    securityLayout->addLayout(securityButtons);
    layout->addWidget(securityBox);
    layout->addStretch();

    actionButtons_ << start << stop << restart << logs << sdkCheck << warmup << verifyAudit << installAudit << bpfTest;
    connect(start, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("native-action"), QStringLiteral("start-service")}, QStringLiteral("action")); });
    connect(stop, &QPushButton::clicked, this, [this]() {
        if (QMessageBox::question(page_, QStringLiteral("停止记忆服务"), QStringLiteral("停止后 Agent 将暂时不能读写 MemGuardOS 记忆。确定停止吗？")) == QMessageBox::Yes)
            runMemctl({QStringLiteral("native-action"), QStringLiteral("stop-service")}, QStringLiteral("action"));
    });
    connect(restart, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("native-action"), QStringLiteral("restart-service")}, QStringLiteral("action")); });
    connect(logs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("100"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
    connect(sdkCheck, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("sdk-status"), QStringLiteral("--probe-embedding"), QStringLiteral("--probe-timeout"), QStringLiteral("90")}, QStringLiteral("details")); });
    connect(warmup, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("openkylin-status"), QStringLiteral("--probe-sdk"), QStringLiteral("--probe-timeout"), QStringLiteral("90")}, QStringLiteral("refresh")); });
    connect(verifyAudit, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("verify-audit")}, QStringLiteral("details")); });
    connect(installAudit, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("native-action"), QStringLiteral("install-auditd-rules")}, QStringLiteral("action")); });
    connect(bpfTest, &QPushButton::clicked, this, [this]() {
        if (QMessageBox::question(page_, QStringLiteral("运行 BPF 安全验证"), QStringLiteral("该操作需要管理员授权，只针对安全测试路径。是否继续？")) == QMessageBox::Yes)
            runMemctl({QStringLiteral("native-action"), QStringLiteral("run-bpf-lsm-test")}, QStringLiteral("action"));
    });
    return tab;
}

QWidget *MemGuardUkccPlugin::buildDiagnosticsTab()
{
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    auto *buttons = new QHBoxLayout;
    auto *diagnostics = new QPushButton(QIcon::fromTheme(QStringLiteral("document-save")), QStringLiteral("导出诊断报告"));
    auto *dashboard = new QPushButton(QIcon::fromTheme(QStringLiteral("applications-internet")), QStringLiteral("打开本地控制台"));
    buttons->addWidget(diagnostics);
    buttons->addWidget(dashboard);
    buttons->addStretch();
    layout->addLayout(buttons);
    auto *explanation = new QLabel(QStringLiteral("诊断报告包含系统、服务、SDK、资源和审计状态，不包含记忆正文。"));
    explanation->setWordWrap(true);
    layout->addWidget(explanation);
    detailsToggle_ = new QToolButton;
    detailsToggle_->setText(QStringLiteral("技术详情"));
    detailsToggle_->setCheckable(true);
    detailsToggle_->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    detailsToggle_->setArrowType(Qt::RightArrow);
    detailsToggle_->setAccessibleName(QStringLiteral("展开或收起技术详情"));
    details_ = new QTextEdit;
    details_->setReadOnly(true);
    details_->setVisible(false);
    details_->setAccessibleName(QStringLiteral("命令与诊断技术详情"));
    layout->addWidget(detailsToggle_);
    layout->addWidget(details_, 1);
    actionButtons_ << diagnostics << dashboard;
    connect(detailsToggle_, &QToolButton::toggled, this, [this](bool checked) {
        detailsToggle_->setArrowType(checked ? Qt::DownArrow : Qt::RightArrow);
        details_->setVisible(checked);
    });
    connect(diagnostics, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics"), QStringLiteral("--probe-sdk"), QStringLiteral("--probe-timeout"), QStringLiteral("90")}, QStringLiteral("export")); });
    connect(dashboard, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
    return tab;
}

bool MemGuardUkccPlugin::isShowOnHomePage() const { return true; }
QIcon MemGuardUkccPlugin::icon() const { return QIcon::fromTheme(QStringLiteral("security-high")); }
bool MemGuardUkccPlugin::isEnable() const { return true; }
const QString MemGuardUkccPlugin::name() const { return QStringLiteral("memguardos-memory-security"); }
void MemGuardUkccPlugin::plugin_leave() {}

void MemGuardUkccPlugin::refreshStatus()
{
    runMemctl({QStringLiteral("openkylin-status")}, QStringLiteral("refresh"));
}

void MemGuardUkccPlugin::runMemctl(const QStringList &arguments, const QString &purpose)
{
    QString program = QStringLiteral("python3");
    QStringList commandArguments{QStringLiteral("-m"), QStringLiteral("memguard.cli.memctl")};
    if (QFileInfo::exists(QStringLiteral("/usr/bin/memctl"))) {
        program = QStringLiteral("/usr/bin/memctl");
        commandArguments.clear();
    }
    commandArguments << arguments;
    runProgram(program, commandArguments, purpose);
}

void MemGuardUkccPlugin::runProgram(const QString &program, const QStringList &arguments, const QString &purpose)
{
    setBusy(true, QStringLiteral("正在执行，请稍候…"));
    auto *process = new QProcess(this);
    QPointer<QProcess> guard(process);
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError) {
        process->setProperty("handled", true);
        setBusy(false, QStringLiteral("无法启动操作"));
        showError(QStringLiteral("操作无法启动"), process->errorString());
        process->deleteLater();
    });
    connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
        [this, process, purpose](int exitCode, QProcess::ExitStatus) {
            if (process->property("handled").toBool()) {
                process->deleteLater();
                return;
            }
            const QString output = cleanedOutput(process->readAll());
            const QJsonObject payload = parsedObject(output);
            details_->setPlainText(output);
            if (purpose == QStringLiteral("refresh") && !payload.isEmpty())
                renderStatus(payload);
            else if (purpose == QStringLiteral("export") && exitCode == 0)
                exportDiagnostics(output);
            else if (purpose == QStringLiteral("action")) {
                if (exitCode == 0 && payload.value(QStringLiteral("ok")).toBool())
                    QMessageBox::information(page_, QStringLiteral("操作完成"), payload.value(QStringLiteral("message")).toString(QStringLiteral("系统操作已完成。")));
                else
                    showError(QStringLiteral("系统操作未完成"), payload.value(QStringLiteral("message")).toString(output));
                QTimer::singleShot(0, this, &MemGuardUkccPlugin::refreshStatus);
            } else if (exitCode != 0) {
                showError(QStringLiteral("操作失败"), output);
            } else {
                detailsToggle_->setChecked(true);
            }
            setBusy(false, exitCode == 0 ? QStringLiteral("检查完成") : QStringLiteral("需要处理"));
            process->deleteLater();
        });
    QTimer::singleShot(180000, this, [this, guard]() {
        if (!guard || guard->state() == QProcess::NotRunning)
            return;
        guard->setProperty("handled", true);
        guard->kill();
        setBusy(false, QStringLiteral("操作超时"));
        showError(QStringLiteral("操作超时"), QStringLiteral("操作超过 180 秒，请检查系统服务后重试。"));
    });
    process->start(program, arguments);
}

void MemGuardUkccPlugin::renderStatus(const QJsonObject &payload)
{
    const QJsonObject summary = payload.value(QStringLiteral("summary")).toObject();
    const QString overall = summary.value(QStringLiteral("overall")).toString(QStringLiteral("red"));
    summaryIcon_->setPixmap(levelIcon(overall).pixmap(32, 32));
    summaryTitle_->setText(overall == QStringLiteral("green") ? QStringLiteral("本机记忆安全状态正常") : QStringLiteral("有项目需要处理"));
    summaryDescription_->setText(QStringLiteral("正常 %1 项，需要注意 %2 项，不可用 %3 项。")
        .arg(summary.value(QStringLiteral("green")).toInt())
        .arg(summary.value(QStringLiteral("yellow")).toInt())
        .arg(summary.value(QStringLiteral("red")).toInt()));

    components_->clear();
    const QJsonArray components = payload.value(QStringLiteral("components")).toArray();
    for (const QJsonValue &value : components) {
        const QJsonObject component = value.toObject();
        const QString level = component.value(QStringLiteral("level")).toString();
        auto *item = new QTreeWidgetItem(components_);
        item->setIcon(0, levelIcon(level));
        item->setText(0, component.value(QStringLiteral("label")).toString());
        item->setText(1, levelText(level));
        item->setText(2, component.value(QStringLiteral("ready")).toBool() ? QStringLiteral("已就绪") : QStringLiteral("查看诊断建议"));
    }
    const QJsonObject services = payload.value(QStringLiteral("services")).toObject();
    const QJsonObject memguardService = services.value(QStringLiteral("memguardd.service")).toObject();
    serviceState_->setText(memguardService.value(QStringLiteral("active")).toBool()
        ? QStringLiteral("服务正在运行，并由 systemd 管理。")
        : QStringLiteral("服务当前未运行。启动、停止和重启操作会显示系统授权窗口。"));
    const QJsonObject embedding = payload.value(QStringLiteral("sdk")).toObject().value(QStringLiteral("embedding")).toObject();
    sdkState_->setText(QStringLiteral("安装状态：%1；推理状态：%2；模型：%3")
        .arg(boolText(embedding.value(QStringLiteral("available")).toBool()))
        .arg(boolText(embedding.value(QStringLiteral("inference_ready")).toBool()))
        .arg(embedding.value(QStringLiteral("model_info")).toObject().value(QStringLiteral("name")).toString(QStringLiteral("未识别"))));
    const QJsonObject security = payload.value(QStringLiteral("security")).toObject();
    auditState_->setText(QStringLiteral("auditd：%1；Polkit 策略：%2；BPF LSM：可选安全验证路径")
        .arg(boolText(security.value(QStringLiteral("auditd")).toObject().value(QStringLiteral("available")).toBool()))
        .arg(boolText(security.value(QStringLiteral("polkit")).toObject().value(QStringLiteral("policy_installed")).toBool())));
}

void MemGuardUkccPlugin::exportDiagnostics(const QString &text)
{
    const QString path = QFileDialog::getSaveFileName(page_, QStringLiteral("保存诊断报告"), QStringLiteral("memguardos-diagnostics.json"), QStringLiteral("JSON 文件 (*.json)"));
    if (path.isEmpty())
        return;
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
        showError(QStringLiteral("无法保存诊断报告"), file.errorString());
        return;
    }
    file.write(text.toUtf8());
    file.close();
    QMessageBox::information(page_, QStringLiteral("诊断报告已保存"), QStringLiteral("报告已保存到：\n%1").arg(path));
}

void MemGuardUkccPlugin::openDashboard()
{
    QString program = QFileInfo::exists(QStringLiteral("/usr/bin/memctl")) ? QStringLiteral("/usr/bin/memctl") : QStringLiteral("python3");
    QStringList arguments;
    if (program == QStringLiteral("python3"))
        arguments << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl");
    arguments << QStringLiteral("web") << QStringLiteral("--host") << QStringLiteral("127.0.0.1") << QStringLiteral("--port") << QStringLiteral("8765");
    const bool started = QProcess::startDetached(program, arguments);
    if (started) {
        QTimer::singleShot(800, this, []() { QDesktopServices::openUrl(QUrl(QStringLiteral("http://127.0.0.1:8765"))); });
    } else {
        showError(QStringLiteral("本地控制台启动失败"), QStringLiteral("请检查 memctl 是否已经安装。"));
    }
}

void MemGuardUkccPlugin::setBusy(bool busy, const QString &message)
{
    if (busy)
        summaryDescription_->setText(message);
    for (QPushButton *button : actionButtons_)
        button->setEnabled(!busy);
}

void MemGuardUkccPlugin::showError(const QString &title, const QString &message)
{
    QMessageBox::critical(page_, title, message.left(1600));
}
