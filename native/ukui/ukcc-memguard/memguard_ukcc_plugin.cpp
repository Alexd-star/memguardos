#include "memguard_ukcc_plugin.h"

#include <QAbstractItemView>
#include <QColor>
#include <QDateTime>
#include <QDesktopServices>
#include <QDialog>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QFrame>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QListWidget>
#include <QListWidgetItem>
#include <QMessageBox>
#include <QPalette>
#include <QPointer>
#include <QProcess>
#include <QProgressBar>
#include <QPushButton>
#include <QRegularExpression>
#include <QScrollArea>
#include <QSize>
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

QColor levelColor(const QString &level)
{
    if (level == QStringLiteral("green"))
        return QColor(4, 120, 87);
    if (level == QStringLiteral("yellow"))
        return QColor(180, 83, 9);
    return QColor(180, 35, 24);
}

QLabel *makeMutedLabel(const QString &text)
{
    auto *label = new QLabel(text);
    label->setWordWrap(true);
    QPalette palette = label->palette();
    palette.setColor(QPalette::WindowText, QColor(102, 112, 133));
    label->setPalette(palette);
    return label;
}

QFrame *makeSeparator()
{
    auto *line = new QFrame;
    line->setFrameShape(QFrame::HLine);
    line->setFrameShadow(QFrame::Sunken);
    return line;
}

QPushButton *makeOperationButton(const QString &iconName, const QString &text, const QString &accessibleName)
{
    auto *button = new QPushButton(QIcon::fromTheme(iconName), text);
    button->setMinimumHeight(48);
    button->setIconSize(QSize(22, 22));
    button->setAccessibleName(accessibleName);
    return button;
}

bool containsAny(const QString &text, const QStringList &keywords)
{
    for (const QString &keyword : keywords) {
        if (text.contains(keyword, Qt::CaseInsensitive))
            return true;
    }
    return false;
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
    readinessBar_->setTextVisible(false);
    readinessBar_->setAccessibleName(QStringLiteral("MemGuardOS 本机健康度"));
    readinessValueLabel_ = new QLabel(QStringLiteral("0%"));
    readinessValueLabel_->setMinimumWidth(48);
    readinessValueLabel_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    readinessValueLabel_->setAccessibleName(QStringLiteral("MemGuardOS 本机健康度百分比"));
    auto *readinessRow = new QHBoxLayout;
    auto *readinessLabel = new QLabel(QStringLiteral("健康度"));
    readinessLabel->setMinimumWidth(52);
    readinessRow->addWidget(readinessLabel);
    readinessRow->addWidget(readinessBar_, 1);
    readinessRow->addWidget(readinessValueLabel_);
    summaryText->addWidget(summaryTitle_);
    summaryText->addWidget(summaryDescription_);
    summaryText->addLayout(readinessRow);
    auto *refresh = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重新检查状态"), QStringLiteral("重新检查 MemGuardOS 状态"));
    refresh->setAccessibleName(QStringLiteral("重新检查 MemGuardOS 状态"));
    cancelOperationButton_ = makeOperationButton(QStringLiteral("process-stop"), QStringLiteral("停止当前操作"), QStringLiteral("停止当前正在执行的 MemGuardOS 操作"));
    cancelOperationButton_->setVisible(false);
    cancelOperationButton_->setEnabled(false);
    summaryLayout->addWidget(summaryIcon_);
    summaryLayout->addLayout(summaryText, 1);
    summaryLayout->addWidget(refresh);
    summaryLayout->addWidget(cancelOperationButton_);
    layout->addWidget(summaryBox);

    resultSummary_ = new QLabel(QStringLiteral("操作结果：等待首次检查。"));
    resultSummary_->setWordWrap(true);
    resultSummary_->setAccessibleName(QStringLiteral("最近一次操作结果摘要"));
    layout->addWidget(resultSummary_);

    auto *guideBox = new QGroupBox(QStringLiteral("当前任务"));
    auto *guideLayout = new QVBoxLayout(guideBox);
    operationGuide_ = makeMutedLabel(QStringLiteral("当前任务：暂无后台操作。\n结果位置：快速状态显示在本页；日志、审计、SDK 和报告结果会显示在【系统维护】>【高级运行信息】或保存为文件。"));
    operationGuide_->setAccessibleName(QStringLiteral("当前任务和结果位置说明"));
    guideLayout->addWidget(operationGuide_);
    layout->addWidget(guideBox);

    tabs_ = new QTabWidget;
    tabs_->setAccessibleName(QStringLiteral("MemGuardOS 设置分类"));
    overviewTabIndex_ = tabs_->addTab(buildOverviewTab(), QIcon::fromTheme(QStringLiteral("view-list-details")), QStringLiteral("总览"));
    serviceTabIndex_ = tabs_->addTab(buildServiceTab(), QIcon::fromTheme(QStringLiteral("preferences-system")), QStringLiteral("服务与安全"));
    diagnosticsTabIndex_ = tabs_->addTab(buildDiagnosticsTab(), QIcon::fromTheme(QStringLiteral("preferences-system")), QStringLiteral("系统维护"));
    layout->addWidget(tabs_, 1);

    connect(refresh, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    connect(cancelOperationButton_, &QPushButton::clicked, this, [this]() {
        if (!currentProcess_ || currentProcess_->state() == QProcess::NotRunning)
            return;
        currentProcess_->setProperty("handled", true);
        currentProcess_->kill();
        setBusy(false, QStringLiteral("当前操作已取消，可以继续使用。"));
        appendHistory(QStringLiteral("取消操作"), true, QStringLiteral("用户取消了当前后台操作。"));
    });
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
    auto *openConsole = makeOperationButton(QStringLiteral("applications-internet"), QStringLiteral("打开本地控制台"), QStringLiteral("打开 MemGuardOS 本地控制台"));
    auto *verifyAudit = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("校验审计链"), QStringLiteral("校验 MemGuardOS 审计链"));
    auto *exportReport = makeOperationButton(QStringLiteral("document-save"), QStringLiteral("导出快速报告"), QStringLiteral("导出 MemGuardOS 系统报告"));
    auto *serviceLogs = makeOperationButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看 memguardd 服务日志"));
    taskLayout->addWidget(openConsole, 0, 0);
    taskLayout->addWidget(verifyAudit, 0, 1);
    taskLayout->addWidget(exportReport, 0, 2);
    taskLayout->addWidget(serviceLogs, 0, 3);
    taskLayout->addWidget(makeMutedLabel(QStringLiteral("轻量操作：快速刷新、日志、审计校验通常在数秒内完成；完整检查：端侧 SDK 完整检查和授权操作可能需要更久，可通过【停止当前操作】中断。")), 1, 0, 1, 4);
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

    auto *recommendationBox = new QGroupBox(QStringLiteral("处理建议"));
    auto *recommendationLayout = new QVBoxLayout(recommendationBox);
    recommendations_ = new QListWidget;
    recommendations_->setWordWrap(true);
    recommendations_->setAlternatingRowColors(true);
    recommendations_->setSelectionMode(QAbstractItemView::NoSelection);
    recommendations_->setAccessibleName(QStringLiteral("MemGuardOS 当前处理建议"));
    recommendations_->setMinimumHeight(76);
    recommendationLayout->addWidget(recommendations_);
    recommendationLayout->addWidget(makeMutedLabel(QStringLiteral("点击每条建议右侧按钮，可跳转到对应处理区，或直接执行校验、端侧能力检查等安全动作。")));
    layout->addWidget(recommendationBox);

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
    connect(exportReport, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics")}, QStringLiteral("export")); });
    connect(serviceLogs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("100"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
    scroll->setWidget(tab);
    return scroll;
}

QWidget *MemGuardUkccPlugin::buildServiceTab()
{
    auto *scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 服务与安全滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    auto *serviceBox = new QGroupBox(QStringLiteral("服务控制"));
    auto *serviceLayout = new QVBoxLayout(serviceBox);
    serviceLayout->addWidget(makeMutedLabel(QStringLiteral("管理 memguardd 后台服务。日常使用时保持服务运行；维护前可查看日志，确认问题后再重启。")));
    serviceLayout->addWidget(makeSeparator());
    serviceState_ = new QLabel(QStringLiteral("等待检查"));
    serviceState_->setWordWrap(true);
    serviceLayout->addWidget(serviceState_);
    auto *serviceButtons = new QGridLayout;
    auto *start = makeOperationButton(QStringLiteral("media-playback-start"), QStringLiteral("启动服务"), QStringLiteral("启动 MemGuardOS 记忆服务"));
    auto *stop = makeOperationButton(QStringLiteral("media-playback-stop"), QStringLiteral("停止服务"), QStringLiteral("停止 MemGuardOS 记忆服务"));
    auto *restart = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重启服务"), QStringLiteral("重启 MemGuardOS 记忆服务"));
    auto *logs = makeOperationButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看 memguardd 服务日志"));
    serviceButtons->addWidget(start, 0, 0);
    serviceButtons->addWidget(stop, 0, 1);
    serviceButtons->addWidget(restart, 0, 2);
    serviceButtons->addWidget(logs, 0, 3);
    serviceLayout->addLayout(serviceButtons);
    auto *serviceHint = makeMutedLabel(QStringLiteral("启动、停止和重启属于管理员授权操作，会通过 Polkit 显示系统授权窗口。"));
    serviceHint->setAccessibleName(QStringLiteral("管理员授权说明"));
    serviceLayout->addWidget(serviceHint);
    layout->addWidget(serviceBox);

    auto *sdkBox = new QGroupBox(QStringLiteral("端侧能力"));
    auto *sdkLayout = new QVBoxLayout(sdkBox);
    sdkLayout->addWidget(makeMutedLabel(QStringLiteral("检查 Kylin embedding/vector SDK 和本地模型状态。SDK 未就绪时，系统会保持稳定自动降级，不阻断普通记忆检索。")));
    sdkLayout->addWidget(makeMutedLabel(QStringLiteral("完整检查会触发端侧模型推理，首次执行可能较慢；日常状态页使用轻量检查，避免拖慢设置界面。")));
    sdkLayout->addWidget(makeSeparator());
    sdkState_ = new QLabel(QStringLiteral("等待检查"));
    sdkState_->setWordWrap(true);
    auto *sdkButtons = new QGridLayout;
    auto *sdkCheck = makeOperationButton(QStringLiteral("system-search"), QStringLiteral("检查端侧向量能力"), QStringLiteral("检查 Kylin SDK 与向量能力"));
    auto *warmup = makeOperationButton(QStringLiteral("media-playback-start"), QStringLiteral("预热 Kylin SDK"), QStringLiteral("预热 Kylin embedding SDK"));
    sdkButtons->addWidget(sdkCheck, 0, 0);
    sdkButtons->addWidget(warmup, 0, 1);
    sdkLayout->addWidget(sdkState_);
    sdkLayout->addLayout(sdkButtons);
    layout->addWidget(sdkBox);

    auto *securityBox = new QGroupBox(QStringLiteral("安全审计"));
    auto *securityLayout = new QVBoxLayout(securityBox);
    securityLayout->addWidget(makeMutedLabel(QStringLiteral("核验审计链、安装本机审计规则，并检查可选内核保护路径。高风险操作需要管理员授权。")));
    securityLayout->addWidget(makeSeparator());
    auditState_ = new QLabel(QStringLiteral("等待检查"));
    auditState_->setWordWrap(true);
    auto *securityButtons = new QGridLayout;
    auto *verifyAudit = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("校验审计链"), QStringLiteral("校验 MemGuardOS 审计链"));
    auto *installAudit = makeOperationButton(QStringLiteral("list-add"), QStringLiteral("安装审计规则"), QStringLiteral("安装 MemGuardOS 审计规则"));
    auto *bpfTest = makeOperationButton(QStringLiteral("dialog-warning"), QStringLiteral("检查内核保护状态"), QStringLiteral("检查 MemGuardOS 内核保护状态"));
    securityButtons->addWidget(verifyAudit, 0, 0);
    securityButtons->addWidget(installAudit, 0, 1);
    securityButtons->addWidget(bpfTest, 0, 2);
    securityLayout->addWidget(auditState_);
    securityLayout->addLayout(securityButtons);
    auto *securityHint = makeMutedLabel(QStringLiteral("安装审计规则和内核保护检查会触发管理员授权；未知动作会被白名单 helper 拒绝。"));
    securityLayout->addWidget(securityHint);
    layout->addWidget(securityBox);

    auto *permissionBox = new QGroupBox(QStringLiteral("授权边界"));
    auto *permissionLayout = new QVBoxLayout(permissionBox);
    permissionLayout->addWidget(makeMutedLabel(QStringLiteral("控制中心只暴露固定白名单操作：服务启停、审计规则安装、内核保护检查和状态诊断。普通记忆写入、检索和遗忘预览不需要提升权限。")));
    permissionLayout->addWidget(makeMutedLabel(QStringLiteral("如果授权弹窗未出现，请确认 Polkit policy 和 native helper 已安装，再重新打开控制中心。")));
    layout->addWidget(permissionBox);
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
    scroll->setWidget(tab);
    return scroll;
}

QWidget *MemGuardUkccPlugin::buildDiagnosticsTab()
{
    auto *scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 系统维护滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);

    auto *introBox = new QGroupBox(QStringLiteral("系统维护说明"));
    auto *introLayout = new QVBoxLayout(introBox);
    introLayout->addWidget(makeMutedLabel(QStringLiteral("这里用于导出系统报告、打开本地控制台、刷新状态和查看高级运行信息。报告默认不包含记忆正文，适合用于问题排查和提交前自检。")));
    introLayout->addWidget(makeSeparator());
    layout->addWidget(introBox);

    auto *reportBox = new QGroupBox(QStringLiteral("报告与控制台"));
    auto *reportLayout = new QVBoxLayout(reportBox);
    reportLayout->addWidget(makeMutedLabel(QStringLiteral("导出系统报告会聚合服务、SDK、资源、授权和审计状态；本地控制台提供更完整的记忆写入、检索、上下文和遗忘预览流程。")));
    auto *reportButtons = new QGridLayout;
    auto *diagnostics = makeOperationButton(QStringLiteral("document-save"), QStringLiteral("导出快速报告"), QStringLiteral("导出 MemGuardOS 系统报告"));
    auto *dashboard = makeOperationButton(QStringLiteral("applications-internet"), QStringLiteral("打开本地控制台"), QStringLiteral("打开 MemGuardOS 本地控制台"));
    reportButtons->addWidget(diagnostics, 0, 0);
    reportButtons->addWidget(dashboard, 0, 1);
    reportLayout->addLayout(reportButtons);
    layout->addWidget(reportBox);

    auto *maintenanceBox = new QGroupBox(QStringLiteral("维护工具"));
    auto *maintenanceLayout = new QVBoxLayout(maintenanceBox);
    maintenanceLayout->addWidget(makeMutedLabel(QStringLiteral("这些工具用于快速确认当前页面状态和后台服务状态。需要原始输出时再展开下方高级信息。")));
    auto *maintenanceButtons = new QGridLayout;
    auto *refresh = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重新检查状态"), QStringLiteral("重新检查 MemGuardOS 状态"));
    auto *verifyAudit = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("校验审计链"), QStringLiteral("校验 MemGuardOS 审计链"));
    auto *logs = makeOperationButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看 memguardd 服务日志"));
    maintenanceButtons->addWidget(refresh, 0, 0);
    maintenanceButtons->addWidget(verifyAudit, 0, 1);
    maintenanceButtons->addWidget(logs, 0, 2);
    maintenanceLayout->addLayout(maintenanceButtons);
    layout->addWidget(maintenanceBox);

    auto *detailsBox = new QGroupBox(QStringLiteral("高级运行信息"));
    auto *detailsLayout = new QVBoxLayout(detailsBox);
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
    detailsLayout->addWidget(detailsToggle_);
    detailsLayout->addWidget(details_, 1);
    layout->addWidget(detailsBox, 1);

    actionButtons_ << diagnostics << dashboard << refresh << verifyAudit << logs;
    connect(detailsToggle_, &QToolButton::toggled, this, [this](bool checked) {
        detailsToggle_->setArrowType(checked ? Qt::DownArrow : Qt::RightArrow);
        details_->setVisible(checked);
    });
    connect(diagnostics, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics")}, QStringLiteral("export")); });
    connect(dashboard, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
    connect(refresh, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    connect(verifyAudit, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("verify-audit")}, QStringLiteral("details")); });
    connect(logs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("100"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
    scroll->setWidget(tab);
    return scroll;
}

bool MemGuardUkccPlugin::isShowOnHomePage() const { return true; }
QIcon MemGuardUkccPlugin::icon() const { return QIcon::fromTheme(QStringLiteral("security-high")); }
bool MemGuardUkccPlugin::isEnable() const { return true; }
const QString MemGuardUkccPlugin::name() const { return QStringLiteral("memguardos-memory-security"); }
void MemGuardUkccPlugin::plugin_leave() {}

void MemGuardUkccPlugin::refreshStatus()
{
    runMemctl({QStringLiteral("openkylin-status"), QStringLiteral("--fast")}, QStringLiteral("refresh"));
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
    if (currentProcess_ && currentProcess_->state() != QProcess::NotRunning) {
        QMessageBox::information(page_, QStringLiteral("已有操作正在执行"), QStringLiteral("当前操作尚未结束。可以等待完成，或点击【停止当前操作】后再执行新的操作。"));
        return;
    }
    setBusy(true, operationStartMessage(arguments, purpose));
    auto *process = new QProcess(this);
    process->setProperty("startedAtMs", QDateTime::currentMSecsSinceEpoch());
    currentProcess_ = process;
    QPointer<QProcess> guard(process);
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError) {
        process->setProperty("handled", true);
        if (currentProcess_ == process)
            currentProcess_.clear();
        setBusy(false, QStringLiteral("无法启动操作"));
        showError(QStringLiteral("操作无法启动"), process->errorString());
        process->deleteLater();
    });
    connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
        [this, process, arguments, purpose](int exitCode, QProcess::ExitStatus) {
            if (process->property("handled").toBool()) {
                if (currentProcess_ == process)
                    currentProcess_.clear();
                process->deleteLater();
                return;
            }
            const QString output = cleanedOutput(process->readAll());
            const QJsonObject payload = parsedObject(output);
            if (currentProcess_ == process)
                currentProcess_.clear();
            QString summaryMessage = payload.value(QStringLiteral("message")).toString(output.left(240));
            const qint64 startedAtMs = process->property("startedAtMs").toLongLong();
            const qint64 elapsedMs = startedAtMs > 0 ? QDateTime::currentMSecsSinceEpoch() - startedAtMs : 0;
            const QString elapsedText = elapsedMs > 0 ? QStringLiteral("耗时 %1 秒").arg(QString::number(elapsedMs / 1000.0, 'f', 1)) : QString();
            bool exportSaved = true;
            if (purpose == QStringLiteral("refresh") && !payload.isEmpty())
                renderStatus(payload);
            else if (purpose == QStringLiteral("export") && exitCode == 0) {
                exportSaved = exportDiagnostics(output);
                if (!exportSaved)
                    summaryMessage = QStringLiteral("报告保存已取消，系统状态未改变");
            } else if (purpose == QStringLiteral("action")) {
                if (exitCode == 0 && payload.value(QStringLiteral("ok")).toBool())
                    QMessageBox::information(page_, QStringLiteral("操作完成"), payload.value(QStringLiteral("message")).toString(QStringLiteral("系统操作已完成。")));
                else
                    showError(QStringLiteral("系统操作未完成"), payload.value(QStringLiteral("message")).toString(output));
                QTimer::singleShot(0, this, &MemGuardUkccPlugin::refreshStatus);
            } else if (exitCode != 0) {
                showDetailsOutput(output, summaryMessage);
                showError(QStringLiteral("操作失败"), output);
            } else {
                showDetailsOutput(output, summaryMessage);
            }
            QString completionMessage = operationCompleteMessage(arguments, purpose, exitCode);
            if (purpose == QStringLiteral("export") && exitCode == 0 && !exportSaved)
                completionMessage = QStringLiteral("报告保存已取消，系统状态未改变。");
            if (!elapsedText.isEmpty())
                completionMessage = QStringLiteral("%1（%2）").arg(completionMessage, elapsedText);
            setBusy(false, completionMessage);
            if (!elapsedText.isEmpty() && !summaryMessage.isEmpty())
                summaryMessage = QStringLiteral("%1；%2").arg(summaryMessage, elapsedText);
            appendHistory(purposeText(purpose), exitCode == 0, summaryMessage);
            process->deleteLater();
        });
    QTimer::singleShot(operationTimeoutMs(arguments, purpose), this, [this, guard, arguments, purpose]() {
        if (!guard || guard->state() == QProcess::NotRunning)
            return;
        guard->setProperty("handled", true);
        guard->kill();
        if (currentProcess_ == guard)
            currentProcess_.clear();
        setBusy(false, QStringLiteral("操作超时，已停止后台命令。"));
        appendHistory(purposeText(purpose), false, QStringLiteral("操作超时，已自动停止。"));
        const int seconds = operationTimeoutMs(arguments, purpose) / 1000;
        showError(QStringLiteral("操作超时"), QStringLiteral("操作超过 %1 秒，已自动停止。请查看服务日志或导出系统报告后重试。").arg(seconds));
    });
    process->start(program, arguments);
}

QString MemGuardUkccPlugin::operationStartMessage(const QStringList &arguments, const QString &purpose) const
{
    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return QStringLiteral("正在读取最近 100 条服务日志，结果会显示在【系统维护】。");
    if (joined.contains(QStringLiteral("verify-audit")))
        return QStringLiteral("正在校验审计链，完成后会打开校验结果。");
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")))
        return QStringLiteral("正在检查端侧向量能力，首次 SDK 推理可能需要更久，可随时停止。");
    if (joined.contains(QStringLiteral("diagnostics")))
        return QStringLiteral("正在生成系统报告，完成后请选择保存位置。");
    if (joined.contains(QStringLiteral("native-action")))
        return QStringLiteral("正在等待系统授权并执行操作。");
    if (joined.contains(QStringLiteral("openkylin-status")) && joined.contains(QStringLiteral("--fast")))
        return QStringLiteral("正在快速刷新本机状态。");
    if (purpose == QStringLiteral("details"))
        return QStringLiteral("正在读取详情，结果会显示在【系统维护】。");
    return QStringLiteral("正在执行操作，请稍候。");
}

QString MemGuardUkccPlugin::operationCompleteMessage(const QStringList &arguments, const QString &purpose, int exitCode) const
{
    if (exitCode != 0)
        return QStringLiteral("操作未完成，已显示错误详情。");

    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return QStringLiteral("服务日志已显示在【系统维护】>【高级运行信息】。");
    if (joined.contains(QStringLiteral("verify-audit")))
        return QStringLiteral("审计链校验已完成，结果已显示在【系统维护】。");
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")))
        return QStringLiteral("端侧向量能力检查已完成，结果已显示在【系统维护】。");
    if (joined.contains(QStringLiteral("diagnostics")))
        return QStringLiteral("系统报告已生成。");
    if (purpose == QStringLiteral("refresh"))
        return QStringLiteral("状态刷新已完成，可以继续操作。");
    if (purpose == QStringLiteral("action"))
        return QStringLiteral("系统操作已完成，正在刷新状态。");
    if (purpose == QStringLiteral("details"))
        return QStringLiteral("详情已显示在【系统维护】>【高级运行信息】。");
    return QStringLiteral("操作已完成，可以继续使用。");
}

int MemGuardUkccPlugin::operationTimeoutMs(const QStringList &arguments, const QString &purpose) const
{
    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return 8000;
    if (joined.contains(QStringLiteral("openkylin-status")) && joined.contains(QStringLiteral("--fast")))
        return 10000;
    if (joined.contains(QStringLiteral("verify-audit")))
        return 20000;
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")) || joined.contains(QStringLiteral("diagnostics")))
        return 120000;
    if (joined.contains(QStringLiteral("native-action")))
        return 90000;
    return purpose == QStringLiteral("details") ? 20000 : 60000;
}

void MemGuardUkccPlugin::showDetailsOutput(const QString &text, const QString &summary)
{
    if (tabs_)
        tabs_->setCurrentIndex(diagnosticsTabIndex_);
    if (detailsToggle_)
        detailsToggle_->setChecked(true);
    if (details_)
        details_->setPlainText(text.isEmpty() ? summary : text);
    updateOperationGuide(QStringLiteral("结果位置：已切换到【系统维护】>【高级运行信息】，可直接查看原始输出。"), false);
}

void MemGuardUkccPlugin::updateOperationGuide(const QString &message, bool busy)
{
    if (!operationGuide_)
        return;
    if (busy) {
        operationGuide_->setText(QStringLiteral("当前任务：%1\n结果位置：轻量状态会更新在本页；日志、审计、SDK 和报告输出会进入【系统维护】>【高级运行信息】。可点击【停止当前操作】取消。")
            .arg(message));
        return;
    }
    if (message.isEmpty()) {
        operationGuide_->setText(QStringLiteral("当前任务：暂无后台操作。\n结果位置：快速状态显示在本页；日志、审计、SDK 和报告结果会显示在【系统维护】>【高级运行信息】或保存为文件。"));
        return;
    }
    operationGuide_->setText(QStringLiteral("当前任务：暂无后台操作。\n结果位置：%1").arg(message));
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
        if (readinessValueLabel_)
            readinessValueLabel_->setText(QStringLiteral("%1%").arg(percent));
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
    const QJsonValue inferenceValue = embedding.value(QStringLiteral("inference_ready"));
    const bool inferenceKnown = !inferenceValue.isUndefined() && !inferenceValue.isNull();
    const bool embeddingReady = inferenceKnown && inferenceValue.toBool();
    const bool fastSdkStatus = embedding.value(QStringLiteral("status_mode")).toString() == QStringLiteral("fast");
    setStatusBadge(
        sdkStatusIcon_,
        sdkStatusLabel_,
        embeddingReady ? QStringLiteral("green") : (embeddingAvailable ? QStringLiteral("yellow") : QStringLiteral("red")),
        embeddingReady ? QStringLiteral("推理就绪") : (embeddingAvailable ? QStringLiteral("SDK 可检测，完整推理按需检查") : QStringLiteral("未检测到")));
    sdkState_->setText(QStringLiteral("安装状态：%1；推理状态：%2；模型：%3")
        .arg(boolText(embedding.value(QStringLiteral("available")).toBool()))
        .arg(inferenceKnown ? boolText(embeddingReady) : (fastSdkStatus ? QStringLiteral("未执行快速启动时的重探针") : QStringLiteral("未执行")))
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
    if (recommendations_) {
        recommendations_->clear();
        const QJsonArray recommendations = payload.value(QStringLiteral("recommendations")).toArray();
        for (const QJsonValue &value : recommendations) {
            addRecommendationRow(value.toString(), overall);
        }
        if (recommendations_->count() == 0)
            new QListWidgetItem(levelIcon(QStringLiteral("green")), QStringLiteral("当前没有需要处理的建议。"), recommendations_);
    }
    renderResourceSnapshot(payload.value(QStringLiteral("resources")).toObject());
    appendHistory(QStringLiteral("状态刷新"), true, summaryDescription_->text());
}

void MemGuardUkccPlugin::addRecommendationRow(const QString &text, const QString &overall)
{
    if (!recommendations_)
        return;

    QString actionText = QStringLiteral("查看处理位置");
    QString actionIcon = QStringLiteral("go-next");
    if (containsAny(text, {QStringLiteral("memguardd"), QStringLiteral("systemd"), QStringLiteral("服务")})) {
        actionText = QStringLiteral("去服务控制");
        actionIcon = QStringLiteral("preferences-system");
    } else if (containsAny(text, {QStringLiteral("SDK"), QStringLiteral("Kylin"), QStringLiteral("embedding"), QStringLiteral("vector"), QStringLiteral("向量")})) {
        actionText = QStringLiteral("检查端侧能力");
        actionIcon = QStringLiteral("system-search");
    } else if (containsAny(text, {QStringLiteral("audit"), QStringLiteral("审计")})) {
        actionText = QStringLiteral("校验审计链");
        actionIcon = QStringLiteral("security-high");
    } else if (containsAny(text, {QStringLiteral("Polkit"), QStringLiteral("授权"), QStringLiteral("权限")})) {
        actionText = QStringLiteral("查看授权边界");
        actionIcon = QStringLiteral("changes-prevent");
    } else if (containsAny(text, {QStringLiteral("Peony"), QStringLiteral("UKUI"), QStringLiteral("桌面"), QStringLiteral("插件"), QStringLiteral("托盘"), QStringLiteral("通知")})) {
        actionText = QStringLiteral("查看桌面集成");
        actionIcon = QStringLiteral("preferences-desktop");
    } else if (containsAny(text, {QStringLiteral("cgroup"), QStringLiteral("CPU"), QStringLiteral("内存"), QStringLiteral("资源")})) {
        actionText = QStringLiteral("查看资源快照");
        actionIcon = QStringLiteral("utilities-system-monitor");
    }

    auto *item = new QListWidgetItem(recommendations_);
    item->setToolTip(text);
    auto *row = new QWidget(recommendations_);
    auto *rowLayout = new QHBoxLayout(row);
    rowLayout->setContentsMargins(8, 6, 8, 6);
    rowLayout->setSpacing(10);

    auto *icon = new QLabel;
    icon->setFixedSize(22, 22);
    icon->setPixmap(levelIcon(overall).pixmap(22, 22));
    icon->setAccessibleName(QStringLiteral("建议状态图标"));

    auto *message = new QLabel(text);
    message->setWordWrap(true);
    message->setAccessibleName(QStringLiteral("处理建议内容"));

    auto *action = new QPushButton(QIcon::fromTheme(actionIcon), actionText);
    action->setMinimumHeight(36);
    action->setIconSize(QSize(18, 18));
    action->setAccessibleName(QStringLiteral("处理建议操作：%1").arg(actionText));
    connect(action, &QPushButton::clicked, this, [this, text]() { handleRecommendationAction(text); });

    rowLayout->addWidget(icon);
    rowLayout->addWidget(message, 1);
    rowLayout->addWidget(action);
    item->setSizeHint(row->sizeHint());
    recommendations_->setItemWidget(item, row);
}

void MemGuardUkccPlugin::handleRecommendationAction(const QString &text)
{
    if (containsAny(text, {QStringLiteral("memguardd"), QStringLiteral("systemd"), QStringLiteral("服务")})) {
        if (tabs_)
            tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【服务与安全】>【服务控制】，可启动、重启或查看 memguardd 日志。"));
        return;
    }

    if (containsAny(text, {QStringLiteral("SDK"), QStringLiteral("Kylin"), QStringLiteral("embedding"), QStringLiteral("vector"), QStringLiteral("向量")})) {
        if (tabs_)
            tabs_->setCurrentIndex(serviceTabIndex_);
        runMemctl({QStringLiteral("sdk-status"), QStringLiteral("--probe-embedding"), QStringLiteral("--probe-timeout"), QStringLiteral("90")}, QStringLiteral("details"));
        return;
    }

    if (containsAny(text, {QStringLiteral("audit"), QStringLiteral("审计")})) {
        if (tabs_)
            tabs_->setCurrentIndex(serviceTabIndex_);
        runMemctl({QStringLiteral("verify-audit")}, QStringLiteral("details"));
        return;
    }

    if (containsAny(text, {QStringLiteral("Polkit"), QStringLiteral("授权"), QStringLiteral("权限")})) {
        if (tabs_)
            tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【服务与安全】>【授权边界】，请检查 Polkit policy 和 native helper。"));
        return;
    }

    if (containsAny(text, {QStringLiteral("Peony"), QStringLiteral("UKUI"), QStringLiteral("桌面"), QStringLiteral("插件"), QStringLiteral("托盘"), QStringLiteral("通知")})) {
        if (tabs_)
            tabs_->setCurrentIndex(diagnosticsTabIndex_);
        if (detailsToggle_)
            detailsToggle_->setChecked(true);
        if (details_)
            details_->setPlainText(QStringLiteral("请在【系统维护】中重新检查状态；如果桌面入口仍不可见，请重新打开设置、重启 Peony，或复跑 scripts/verify_ukui_plugins.sh。"));
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【系统维护】，可查看桌面集成和插件状态。"));
        return;
    }

    if (containsAny(text, {QStringLiteral("cgroup"), QStringLiteral("CPU"), QStringLiteral("内存"), QStringLiteral("资源")})) {
        if (tabs_)
            tabs_->setCurrentIndex(overviewTabIndex_);
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【总览】>【资源快照】，可查看 memguardd 服务 cgroup、内存和 CPU 配额。"));
        return;
    }

    if (tabs_)
        tabs_->setCurrentIndex(diagnosticsTabIndex_);
    if (detailsToggle_)
        detailsToggle_->setChecked(true);
    if (details_)
        details_->setPlainText(QStringLiteral("建议先在【系统维护】中重新检查状态；仍异常时导出系统报告，再查看服务日志。"));
    appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【系统维护】，可重新检查、查看日志或导出系统报告。"));
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
    if (label) {
        QPalette palette = label->palette();
        palette.setColor(QPalette::WindowText, levelColor(level));
        label->setPalette(palette);
        label->setText(QStringLiteral("%1：%2").arg(levelText(level), text));
    }
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

bool MemGuardUkccPlugin::exportDiagnostics(const QString &text)
{
    const QString path = QFileDialog::getSaveFileName(page_, QStringLiteral("保存系统报告"), QStringLiteral("memguardos-system-report.json"), QStringLiteral("JSON 文件 (*.json)"));
    if (path.isEmpty()) {
        updateOperationGuide(QStringLiteral("报告保存已取消，系统状态未改变。"), false);
        return false;
    }
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
        showError(QStringLiteral("无法保存系统报告"), file.errorString());
        return false;
    }
    file.write(text.toUtf8());
    file.close();
    updateOperationGuide(QStringLiteral("系统报告已保存到 %1。").arg(path), false);
    QMessageBox::information(page_, QStringLiteral("系统报告已保存"), QStringLiteral("报告已保存到：\n%1").arg(path));
    return true;
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
    if (!message.isEmpty())
        summaryDescription_->setText(message);
    updateOperationGuide(message, busy);
    if (cancelOperationButton_) {
        cancelOperationButton_->setVisible(busy);
        cancelOperationButton_->setEnabled(busy);
    }
    for (QPushButton *button : actionButtons_)
        button->setEnabled(!busy);
}

void MemGuardUkccPlugin::showError(const QString &title, const QString &message)
{
    QMessageBox::critical(page_, title, message.left(1600));
}
