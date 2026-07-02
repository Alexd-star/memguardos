#include "memguard_ukcc_plugin.h"

#include <QAbstractItemView>
#include <QDateTime>
#include <QDesktopServices>
#include <QDialog>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGridLayout>
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
#include <QProgressBar>
#include <QPushButton>
#include <QRegularExpression>
#include <QScrollArea>
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

QString purposeText(const QString &purpose)
{
    if (purpose == QStringLiteral("refresh"))
        return QStringLiteral("状态刷新");
    if (purpose == QStringLiteral("export"))
        return QStringLiteral("诊断导出");
    if (purpose == QStringLiteral("action"))
        return QStringLiteral("管理员授权操作");
    if (purpose == QStringLiteral("details"))
        return QStringLiteral("查看详情");
    return QStringLiteral("系统操作");
}

QGroupBox *makeStatusCard(const QString &title, const QString &description, QLabel **iconOut, QLabel **statusOut)
{
    auto *box = new QGroupBox(title);
    auto *layout = new QHBoxLayout(box);
    auto *icon = new QLabel;
    icon->setFixedSize(30, 30);
    icon->setPixmap(QIcon::fromTheme(QStringLiteral("emblem-system")).pixmap(30, 30));
    icon->setAccessibleName(title + QStringLiteral("状态图标"));

    auto *texts = new QVBoxLayout;
    auto *status = new QLabel(QStringLiteral("等待检查"));
    QFont statusFont = status->font();
    statusFont.setBold(true);
    status->setFont(statusFont);
    status->setAccessibleName(title + QStringLiteral("状态"));
    auto *body = new QLabel(description);
    body->setWordWrap(true);
    texts->addWidget(status);
    texts->addWidget(body);

    layout->addWidget(icon);
    layout->addLayout(texts, 1);
    if (iconOut)
        *iconOut = icon;
    if (statusOut)
        *statusOut = status;
    return box;
}

QPushButton *makeActionButton(const QString &iconName, const QString &text, const QString &accessibleName)
{
    auto *button = new QPushButton(QIcon::fromTheme(iconName), text);
    button->setMinimumHeight(48);
    button->setAccessibleName(accessibleName);
    return button;
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
    readinessBar_ = new QProgressBar;
    readinessBar_->setRange(0, 100);
    readinessBar_->setValue(0);
    readinessBar_->setTextVisible(true);
    readinessBar_->setFormat(QStringLiteral("健康度 %p%"));
    readinessBar_->setAccessibleName(QStringLiteral("MemGuardOS 本机健康度"));
    summaryText->addWidget(summaryTitle_);
    summaryText->addWidget(summaryDescription_);
    summaryText->addWidget(readinessBar_);
    auto *refresh = new QPushButton(QIcon::fromTheme(QStringLiteral("view-refresh")), QStringLiteral("重新检查状态"));
    refresh->setAccessibleName(QStringLiteral("重新检查 MemGuardOS 状态"));
    summaryLayout->addWidget(summaryIcon_);
    summaryLayout->addLayout(summaryText, 1);
    summaryLayout->addWidget(refresh);
    layout->addWidget(summaryBox);

    resultSummary_ = new QLabel(QStringLiteral("操作结果：等待首次检查。"));
    resultSummary_->setWordWrap(true);
    resultSummary_->setAccessibleName(QStringLiteral("最近一次操作结果摘要"));
    layout->addWidget(resultSummary_);

    auto *tabs = new QTabWidget;
    tabs->setAccessibleName(QStringLiteral("MemGuardOS 设置分类"));
    tabs->addTab(buildOverviewTab(), QIcon::fromTheme(QStringLiteral("view-list-details")), QStringLiteral("总览"));
    tabs->addTab(buildServiceTab(), QIcon::fromTheme(QStringLiteral("preferences-system")), QStringLiteral("服务与安全"));
    tabs->addTab(buildDiagnosticsTab(), QIcon::fromTheme(QStringLiteral("preferences-system")), QStringLiteral("系统维护"));
    layout->addWidget(tabs, 1);

    connect(refresh, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    QTimer::singleShot(0, this, &MemGuardUkccPlugin::refreshStatus);
    return page_;
}

QWidget *MemGuardUkccPlugin::buildOverviewTab()
{
    auto *scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 总览滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);

    auto *healthBox = new QGroupBox(QStringLiteral("服务健康"));
    auto *healthLayout = new QGridLayout(healthBox);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("服务运行"),
                                QStringLiteral("memguardd 后台服务和本地 API 是否可用。"),
                                &serviceStatusIcon_,
                                &serviceStatusLabel_),
        0, 0);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("审计完整性"),
                                QStringLiteral("审计规则和操作链路是否可校验。"),
                                &auditStatusIcon_,
                                &auditStatusLabel_),
        0, 1);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("端侧向量"),
                                QStringLiteral("Kylin embedding/vector SDK 与自动降级状态。"),
                                &sdkStatusIcon_,
                                &sdkStatusLabel_),
        1, 0);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("桌面集成"),
                                QStringLiteral("Peony 右键、控制中心和通知能力。"),
                                &desktopStatusIcon_,
                                &desktopStatusLabel_),
        1, 1);
    layout->addWidget(healthBox);

    auto *taskBox = new QGroupBox(QStringLiteral("任务入口"));
    auto *taskLayout = new QGridLayout(taskBox);
    auto *openConsole = makeActionButton(QStringLiteral("applications-internet"), QStringLiteral("打开本地控制台"), QStringLiteral("打开 MemGuardOS 本地控制台"));
    auto *verifyAudit = makeActionButton(QStringLiteral("security-high"), QStringLiteral("校验审计链"), QStringLiteral("校验 MemGuardOS 审计链"));
    auto *exportReport = makeActionButton(QStringLiteral("document-save"), QStringLiteral("导出系统报告"), QStringLiteral("导出 MemGuardOS 系统报告"));
    auto *serviceLogs = makeActionButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看 memguardd 服务日志"));
    taskLayout->addWidget(openConsole, 0, 0);
    taskLayout->addWidget(verifyAudit, 0, 1);
    taskLayout->addWidget(exportReport, 0, 2);
    taskLayout->addWidget(serviceLogs, 0, 3);
    layout->addWidget(taskBox);

    auto *flowBox = new QGroupBox(QStringLiteral("记忆治理流程"));
    auto *flowLayout = new QVBoxLayout(flowBox);
    auto *flowTitle = new QLabel(QStringLiteral("采集 → 抽取 → 上下文 → 遗忘"));
    QFont flowFont = flowTitle->font();
    flowFont.setBold(true);
    flowFont.setPointSizeF(flowFont.pointSizeF() * 1.1);
    flowTitle->setFont(flowFont);
    flowTitle->setAlignment(Qt::AlignCenter);
    auto *flowHint = new QLabel(QStringLiteral("普通用户主要完成记忆写入、检索和上下文预览；管理员负责审计校验、授权操作、系统报告和高风险遗忘确认。"));
    flowHint->setWordWrap(true);
    flowLayout->addWidget(flowTitle);
    flowLayout->addWidget(flowHint);
    layout->addWidget(flowBox);

    auto *componentsBox = new QGroupBox(QStringLiteral("组件状态详情"));
    auto *componentsLayout = new QVBoxLayout(componentsBox);
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
    componentsLayout->addWidget(components_);
    layout->addWidget(componentsBox);

    auto *detailGrid = new QGridLayout;

    auto *resourceBox = new QGroupBox(QStringLiteral("资源快照"));
    auto *resourceLayout = new QVBoxLayout(resourceBox);
    resourceSnapshot_ = new QTreeWidget;
    resourceSnapshot_->setColumnCount(2);
    resourceSnapshot_->setHeaderLabels({QStringLiteral("项目"), QStringLiteral("当前值")});
    resourceSnapshot_->setRootIsDecorated(false);
    resourceSnapshot_->setAlternatingRowColors(true);
    resourceSnapshot_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    resourceSnapshot_->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    resourceSnapshot_->setAccessibleName(QStringLiteral("memguardd 服务资源快照"));
    resourceLayout->addWidget(resourceSnapshot_);
    detailGrid->addWidget(resourceBox, 0, 0);

    auto *historyBox = new QGroupBox(QStringLiteral("操作历史"));
    auto *historyLayout = new QVBoxLayout(historyBox);
    operationHistory_ = new QTreeWidget;
    operationHistory_->setColumnCount(4);
    operationHistory_->setHeaderLabels({QStringLiteral("时间"), QStringLiteral("操作"), QStringLiteral("结果"), QStringLiteral("说明")});
    operationHistory_->setRootIsDecorated(false);
    operationHistory_->setAlternatingRowColors(true);
    operationHistory_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    operationHistory_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    operationHistory_->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    operationHistory_->header()->setSectionResizeMode(3, QHeaderView::Stretch);
    operationHistory_->setAccessibleName(QStringLiteral("MemGuardOS 最近操作历史"));
    historyLayout->addWidget(operationHistory_);
    detailGrid->addWidget(historyBox, 0, 1);
    layout->addLayout(detailGrid);

    auto *hint = new QLabel(QStringLiteral("状态来自本机实时检查。选择【重新检查状态】可刷新服务、SDK、审计和资源信息。"));
    hint->setWordWrap(true);
    layout->addWidget(hint);
    actionButtons_ << openConsole << verifyAudit << exportReport << serviceLogs;
    connect(openConsole, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
    connect(verifyAudit, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("verify-audit")}, QStringLiteral("details")); });
    connect(exportReport, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics"), QStringLiteral("--probe-sdk"), QStringLiteral("--probe-timeout"), QStringLiteral("90")}, QStringLiteral("export")); });
    connect(serviceLogs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("100"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
    scroll->setWidget(tab);
    return scroll;
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
    auto *serviceHint = new QLabel(QStringLiteral("启动、停止和重启属于管理员授权操作，会通过 Polkit 显示系统授权窗口。"));
    serviceHint->setWordWrap(true);
    serviceHint->setAccessibleName(QStringLiteral("管理员授权说明"));
    serviceLayout->addWidget(serviceHint);
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
    auto *bpfTest = new QPushButton(QStringLiteral("检查内核保护状态"));
    securityButtons->addWidget(verifyAudit);
    securityButtons->addWidget(installAudit);
    securityButtons->addWidget(bpfTest);
    securityButtons->addStretch();
    securityLayout->addWidget(auditState_);
    securityLayout->addLayout(securityButtons);
    auto *securityHint = new QLabel(QStringLiteral("安装审计规则和内核保护检查会触发管理员授权；未知动作会被白名单 helper 拒绝。"));
    securityHint->setWordWrap(true);
    securityLayout->addWidget(securityHint);
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
        if (QMessageBox::question(page_, QStringLiteral("检查内核保护状态"), QStringLiteral("该检查需要管理员授权，只访问安全检查路径，不会修改用户文件。是否继续？")) == QMessageBox::Yes)
            runMemctl({QStringLiteral("native-action"), QStringLiteral("run-bpf-lsm-test")}, QStringLiteral("action"));
    });
    return tab;
}

QWidget *MemGuardUkccPlugin::buildDiagnosticsTab()
{
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    auto *buttons = new QHBoxLayout;
    auto *diagnostics = new QPushButton(QIcon::fromTheme(QStringLiteral("document-save")), QStringLiteral("导出系统报告"));
    auto *dashboard = new QPushButton(QIcon::fromTheme(QStringLiteral("applications-internet")), QStringLiteral("打开本地控制台"));
    buttons->addWidget(diagnostics);
    buttons->addWidget(dashboard);
    buttons->addStretch();
    layout->addLayout(buttons);
    auto *explanation = new QLabel(QStringLiteral("系统报告包含服务、SDK、资源、授权和审计状态，不包含记忆正文。"));
    explanation->setWordWrap(true);
    layout->addWidget(explanation);
    detailsToggle_ = new QToolButton;
    detailsToggle_->setText(QStringLiteral("高级信息"));
    detailsToggle_->setCheckable(true);
    detailsToggle_->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    detailsToggle_->setArrowType(Qt::RightArrow);
    detailsToggle_->setAccessibleName(QStringLiteral("展开或收起高级信息"));
    details_ = new QTextEdit;
    details_->setReadOnly(true);
    details_->setVisible(false);
    details_->setAccessibleName(QStringLiteral("高级运行信息"));
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
            const QString summaryMessage = payload.value(QStringLiteral("message")).toString(output.left(240));
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
            appendHistory(purposeText(purpose), exitCode == 0, summaryMessage);
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
    if (readinessBar_) {
        double readiness = summary.value(QStringLiteral("readiness_score")).toDouble();
        if (readiness <= 1.0)
            readiness *= 100.0;
        const int percent = qBound(0, static_cast<int>(readiness + 0.5), 100);
        readinessBar_->setValue(percent);
        readinessBar_->setFormat(QStringLiteral("健康度 %1%").arg(percent));
    }

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
    const bool serviceReady = memguardService.value(QStringLiteral("active")).toBool();
    setStatusBadge(
        serviceStatusIcon_,
        serviceStatusLabel_,
        serviceReady ? QStringLiteral("green") : QStringLiteral("red"),
        serviceReady ? QStringLiteral("运行中") : QStringLiteral("未运行"));
    serviceState_->setText(memguardService.value(QStringLiteral("active")).toBool()
        ? QStringLiteral("服务正在运行，并由 systemd 管理。")
        : QStringLiteral("服务当前未运行。启动、停止和重启操作会显示系统授权窗口。"));
    const QJsonObject embedding = payload.value(QStringLiteral("sdk")).toObject().value(QStringLiteral("embedding")).toObject();
    const bool embeddingAvailable = embedding.value(QStringLiteral("available")).toBool();
    const bool embeddingReady = embedding.value(QStringLiteral("inference_ready")).toBool();
    setStatusBadge(
        sdkStatusIcon_,
        sdkStatusLabel_,
        embeddingReady ? QStringLiteral("green") : (embeddingAvailable ? QStringLiteral("yellow") : QStringLiteral("red")),
        embeddingReady ? QStringLiteral("推理就绪") : (embeddingAvailable ? QStringLiteral("已安装，必要时自动降级") : QStringLiteral("未检测到")));
    sdkState_->setText(QStringLiteral("安装状态：%1；推理状态：%2；模型：%3")
        .arg(boolText(embedding.value(QStringLiteral("available")).toBool()))
        .arg(boolText(embedding.value(QStringLiteral("inference_ready")).toBool()))
        .arg(embedding.value(QStringLiteral("model_info")).toObject().value(QStringLiteral("name")).toString(QStringLiteral("未识别"))));
    const QJsonObject security = payload.value(QStringLiteral("security")).toObject();
    const bool auditReady = security.value(QStringLiteral("auditd")).toObject().value(QStringLiteral("rules_tool_available")).toBool();
    setStatusBadge(
        auditStatusIcon_,
        auditStatusLabel_,
        auditReady ? QStringLiteral("green") : QStringLiteral("red"),
        auditReady ? QStringLiteral("审计工具可用") : QStringLiteral("需要安装审计工具"));
    auditState_->setText(QStringLiteral("auditd：%1；Polkit 策略：%2；BPF LSM：可选安全验证路径")
        .arg(boolText(security.value(QStringLiteral("auditd")).toObject().value(QStringLiteral("available")).toBool()))
        .arg(boolText(security.value(QStringLiteral("polkit")).toObject().value(QStringLiteral("policy_installed")).toBool())));
    const QJsonObject desktop = payload.value(QStringLiteral("desktop")).toObject();
    const QJsonObject peony = desktop.value(QStringLiteral("peony")).toObject();
    const QJsonObject ukcc = desktop.value(QStringLiteral("ukui_control_center")).toObject();
    const QJsonObject notify = desktop.value(QStringLiteral("notify_send")).toObject();
    const bool desktopReady = peony.value(QStringLiteral("plugin_installed")).toBool() && ukcc.value(QStringLiteral("plugin_installed")).toBool();
    const bool desktopAvailable = peony.value(QStringLiteral("available")).toBool() || ukcc.value(QStringLiteral("available")).toBool() || notify.value(QStringLiteral("available")).toBool();
    setStatusBadge(
        desktopStatusIcon_,
        desktopStatusLabel_,
        desktopReady ? QStringLiteral("green") : (desktopAvailable ? QStringLiteral("yellow") : QStringLiteral("red")),
        desktopReady ? QStringLiteral("桌面入口就绪") : (desktopAvailable ? QStringLiteral("组件待安装或需重启桌面") : QStringLiteral("未检测到桌面能力")));
    renderResourceSnapshot(payload.value(QStringLiteral("resources")).toObject());
    appendHistory(QStringLiteral("状态刷新"), true, summaryDescription_->text());
}

void MemGuardUkccPlugin::renderResourceSnapshot(const QJsonObject &resources)
{
    if (!resourceSnapshot_)
        return;
    resourceSnapshot_->clear();
    const QJsonObject memory = resources.value(QStringLiteral("memory")).toObject();
    const QJsonObject cpu = resources.value(QStringLiteral("cpu")).toObject();
    const QList<QPair<QString, QString>> rows = {
        {QStringLiteral("服务 cgroup"), resources.value(QStringLiteral("control_group")).toString(QStringLiteral("未识别"))},
        {QStringLiteral("内存当前值"), memory.value(QStringLiteral("cgroup_memory_current")).toVariant().toString()},
        {QStringLiteral("内存上限"), memory.value(QStringLiteral("cgroup_memory_max")).toVariant().toString()},
        {QStringLiteral("CPU 配额"), cpu.value(QStringLiteral("cpu_max")).toVariant().toString()},
        {QStringLiteral("CPU 统计"), QString(QJsonDocument(cpu.value(QStringLiteral("cpu_stat")).toObject()).toJson(QJsonDocument::Compact))},
    };
    for (const auto &row : rows) {
        auto *item = new QTreeWidgetItem(resourceSnapshot_);
        item->setText(0, row.first);
        item->setText(1, row.second.isEmpty() ? QStringLiteral("未采集") : row.second);
    }
}

void MemGuardUkccPlugin::setStatusBadge(QLabel *icon, QLabel *label, const QString &level, const QString &text)
{
    if (icon)
        icon->setPixmap(levelIcon(level).pixmap(30, 30));
    if (label)
        label->setText(QStringLiteral("%1：%2").arg(levelText(level), text));
}

void MemGuardUkccPlugin::appendHistory(const QString &operation, bool ok, const QString &message)
{
    if (resultSummary_)
        resultSummary_->setText(QStringLiteral("操作结果：%1，%2。").arg(operation, ok ? QStringLiteral("已完成") : QStringLiteral("需要处理")));
    if (!operationHistory_)
        return;
    auto *item = new QTreeWidgetItem(operationHistory_);
    item->setIcon(2, ok ? levelIcon(QStringLiteral("green")) : levelIcon(QStringLiteral("red")));
    item->setText(0, QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss")));
    item->setText(1, operation);
    item->setText(2, ok ? QStringLiteral("完成") : QStringLiteral("失败"));
    item->setText(3, message.simplified().left(280));
    operationHistory_->insertTopLevelItem(0, operationHistory_->takeTopLevelItem(operationHistory_->indexOfTopLevelItem(item)));
    while (operationHistory_->topLevelItemCount() > 12)
        delete operationHistory_->takeTopLevelItem(operationHistory_->topLevelItemCount() - 1);
}

void MemGuardUkccPlugin::exportDiagnostics(const QString &text)
{
    const QString path = QFileDialog::getSaveFileName(page_, QStringLiteral("保存系统报告"), QStringLiteral("memguardos-system-report.json"), QStringLiteral("JSON 文件 (*.json)"));
    if (path.isEmpty())
        return;
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
        showError(QStringLiteral("无法保存系统报告"), file.errorString());
        return;
    }
    file.write(text.toUtf8());
    file.close();
    QMessageBox::information(page_, QStringLiteral("系统报告已保存"), QStringLiteral("报告已保存到：\n%1").arg(path));
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
