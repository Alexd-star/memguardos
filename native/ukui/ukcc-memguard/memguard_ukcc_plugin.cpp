#include "memguard_ukcc_plugin.h"
#include "../dashboard_launcher.h"

#include <QAbstractItemView>
#include <QApplication>
#include <QDesktopServices>
#include <QDialog>
#include <QDir>
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
#include <QSizePolicy>
#include <QStandardPaths>
#include <QStyle>
#include <QTabWidget>
#include <QTextEdit>
#include <QTimer>
#include <QToolButton>
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QUrl>
#include <QVBoxLayout>

namespace {

QIcon themedIcon(
    const QString &name,
    QStyle::StandardPixmap standardPixmap = QStyle::SP_FileIcon)
{
    const QIcon icon = QIcon::fromTheme(name);
    return icon.isNull() ? QApplication::style()->standardIcon(standardPixmap) : icon;
}

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
        return QStringLiteral("需确认");
    return QStringLiteral("不可用");
}

QIcon levelIcon(const QString &level)
{
    if (level == QStringLiteral("green"))
        return themedIcon(QStringLiteral("dialog-ok-apply"), QStyle::SP_DialogApplyButton);
    if (level == QStringLiteral("yellow"))
        return themedIcon(QStringLiteral("dialog-warning"), QStyle::SP_MessageBoxWarning);
    return themedIcon(QStringLiteral("dialog-error"), QStyle::SP_MessageBoxCritical);
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
        return QStringLiteral("查看维护结果");
    return QStringLiteral("系统操作");
}

QGroupBox *makeStatusCard(const QString &title, const QString &description, QLabel **iconOut, QLabel **statusOut)
{
    auto *box = new QGroupBox(title);
    auto *layout = new QHBoxLayout(box);
    layout->setContentsMargins(14, 12, 14, 12);
    layout->setSpacing(12);
    auto *icon = new QLabel;
    icon->setFixedSize(30, 30);
    icon->setPixmap(themedIcon(QStringLiteral("emblem-system"), QStyle::SP_ComputerIcon).pixmap(30, 30));
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

void applyPageSpacing(QVBoxLayout *layout)
{
    if (!layout)
        return;
    layout->setContentsMargins(18, 16, 18, 18);
    layout->setSpacing(14);
}

void applyPanelSpacing(QLayout *layout)
{
    if (!layout)
        return;
    layout->setContentsMargins(16, 14, 16, 14);
    layout->setSpacing(12);
}

void applyGridRhythm(QGridLayout *layout)
{
    if (!layout)
        return;
    layout->setHorizontalSpacing(12);
    layout->setVerticalSpacing(12);
    layout->setColumnStretch(0, 1);
    layout->setColumnStretch(1, 1);
}

QLabel *makeMutedLabel(const QString &text);

QWidget *makeSectionHeader(const QString &iconName, const QString &title, const QString &description)
{
    auto *widget = new QWidget;
    auto *layout = new QHBoxLayout(widget);
    layout->setContentsMargins(0, 0, 0, 2);
    layout->setSpacing(12);

    auto *icon = new QLabel;
    icon->setFixedSize(28, 28);
    icon->setPixmap(themedIcon(iconName, QStyle::SP_FileDialogDetailedView).pixmap(28, 28));
    icon->setAccessibleName(title + QStringLiteral("图标"));

    auto *texts = new QVBoxLayout;
    texts->setContentsMargins(0, 0, 0, 0);
    texts->setSpacing(2);
    auto *titleLabel = new QLabel(title);
    QFont titleFont = titleLabel->font();
    titleFont.setBold(true);
    titleFont.setPointSizeF(titleFont.pointSizeF() * 1.08);
    titleLabel->setFont(titleFont);
    auto *body = makeMutedLabel(description);
    texts->addWidget(titleLabel);
    texts->addWidget(body);

    layout->addWidget(icon);
    layout->addLayout(texts, 1);
    return widget;
}

QLabel *makeMutedLabel(const QString &text)
{
    auto *label = new QLabel(text);
    label->setWordWrap(true);
    QPalette palette = label->palette();
    palette.setColor(QPalette::WindowText, palette.color(QPalette::Disabled, QPalette::WindowText));
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

QToolButton *makeDisclosureButton(const QString &text, const QString &accessibleName)
{
    auto *button = new QToolButton;
    button->setText(text);
    button->setCheckable(true);
    button->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    button->setArrowType(Qt::RightArrow);
    button->setAccessibleName(accessibleName);
    button->setMinimumHeight(36);
    return button;
}

QPushButton *makeOperationButton(const QString &iconName, const QString &text, const QString &accessibleName)
{
    auto *button = new QPushButton(themedIcon(iconName, QStyle::SP_FileDialogDetailedView), text);
    button->setMinimumHeight(44);
    button->setMaximumHeight(52);
    button->setMaximumWidth(220);
    button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
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

QString friendlyRecommendationText(const QString &text)
{
    if (containsAny(text, {QStringLiteral("SDK"), QStringLiteral("Kylin"), QStringLiteral("embedding"), QStringLiteral("vector"), QStringLiteral("向量")})) {
        if (containsAny(text, {QStringLiteral("按需检查"), QStringLiteral("端侧能力待确认"), QStringLiteral("端侧增强待确认")})) {
            return QStringLiteral("本机智能检索建议确认：核心记忆服务可正常使用；如需启用本机检索增强，请点击右侧按钮，系统会切换到【管理员】并完成检查。");
        }
        return QStringLiteral("本机智能检索需要确认：这不影响日常记忆检索；请点击右侧按钮检查本机智能检索，如果仍异常，可在【管理员】中点击【预热本机智能检索】后重新检查。");
    }
    return text;
}

QString formatBytesValue(const QString &raw)
{
    if (raw.isEmpty())
        return QStringLiteral("未采集");
    if (raw == QStringLiteral("max"))
        return QStringLiteral("未限制");

    bool ok = false;
    const qlonglong bytes = raw.toLongLong(&ok);
    if (!ok)
        return raw;
    if (bytes < 1024)
        return QStringLiteral("%1 B").arg(bytes);
    if (bytes < 1024 * 1024)
        return QStringLiteral("%1 KB").arg(QString::number(bytes / 1024.0, 'f', 1));
    if (bytes < 1024LL * 1024LL * 1024LL)
        return QStringLiteral("%1 MB").arg(QString::number(bytes / (1024.0 * 1024.0), 'f', 1));
    return QStringLiteral("%1 GB").arg(QString::number(bytes / (1024.0 * 1024.0 * 1024.0), 'f', 2));
}

QString formatCpuQuota(const QString &raw, const QJsonValue &quotaValue)
{
    if (!quotaValue.isUndefined() && !quotaValue.isNull()) {
        const QString quota = quotaValue.toVariant().toString();
        if (!quota.isEmpty() && quota != QStringLiteral("0") && quota != QStringLiteral("infinity"))
            return quota;
    }
    if (raw.isEmpty())
        return QStringLiteral("未采集");
    const QStringList parts = raw.split(QLatin1Char(' '), Qt::SkipEmptyParts);
    if (!parts.isEmpty() && parts.first() == QStringLiteral("max"))
        return QStringLiteral("未限制");
    if (parts.size() >= 2) {
        bool quotaOk = false;
        bool periodOk = false;
        const double quota = parts.at(0).toDouble(&quotaOk);
        const double period = parts.at(1).toDouble(&periodOk);
        if (quotaOk && periodOk && period > 0.0)
            return QStringLiteral("约 %1% 单核配额").arg(QString::number((quota / period) * 100.0, 'f', 0));
    }
    return raw;
}

QString formatCpuUsage(const QJsonObject &stat)
{
    if (stat.isEmpty())
        return QStringLiteral("未采集");
    const double usageSeconds = stat.value(QStringLiteral("usage_usec")).toDouble() / 1000000.0;
    const int throttled = stat.value(QStringLiteral("nr_throttled")).toInt();
    if (usageSeconds <= 0.0 && throttled <= 0)
        return QStringLiteral("已采集，暂无明显负载");
    if (throttled > 0)
        return QStringLiteral("累计使用 %1 秒，限流 %2 次").arg(QString::number(usageSeconds, 'f', 1)).arg(throttled);
    return QStringLiteral("累计使用 %1 秒").arg(QString::number(usageSeconds, 'f', 1));
}

QString takeNavigationTarget()
{
    const QString runtime = QStandardPaths::writableLocation(QStandardPaths::RuntimeLocation);
    QFile file(runtime + QStringLiteral("/memguardos-ukcc-target"));
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return {};
    const QString target = QString::fromUtf8(file.readAll()).trimmed();
    file.remove();
    return target;
}

QString formatResourceValue(const QString &key, const QJsonObject &resources)
{
    const QJsonObject memory = resources.value(QStringLiteral("memory")).toObject();
    const QJsonObject cpu = resources.value(QStringLiteral("cpu")).toObject();
    if (key == QStringLiteral("control_group"))
        return resources.value(QStringLiteral("control_group")).toString(QStringLiteral("未识别"));
    if (key == QStringLiteral("memory_current"))
        return formatBytesValue(memory.value(QStringLiteral("cgroup_memory_current")).toVariant().toString());
    if (key == QStringLiteral("memory_max"))
        return formatBytesValue(memory.value(QStringLiteral("cgroup_memory_max")).toVariant().toString());
    if (key == QStringLiteral("cpu_quota"))
        return formatCpuQuota(cpu.value(QStringLiteral("cpu_max")).toVariant().toString(), cpu.value(QStringLiteral("cpu_quota_per_sec_usec")));
    if (key == QStringLiteral("cpu_usage"))
        return formatCpuUsage(cpu.value(QStringLiteral("cpu_stat")).toObject());
    return QStringLiteral("未采集");
}

QString statusTextFromBool(bool ok)
{
    return ok ? QStringLiteral("通过") : QStringLiteral("需要处理");
}

bool looksLikeServiceLog(const QString &text)
{
    if (text.isEmpty())
        return false;
    if (containsAny(text, {QStringLiteral("journal"), QStringLiteral("memguardd"), QStringLiteral("systemd"), QStringLiteral("Traceback"), QStringLiteral("No entries")}))
        return true;
    const QRegularExpression syslogPattern(QStringLiteral("^\\w{3}\\s+\\d{1,2}\\s+\\d{2}:\\d{2}:\\d{2}"));
    for (const QString &line : text.split(QLatin1Char('\n'))) {
        if (syslogPattern.match(line.trimmed()).hasMatch())
            return true;
    }
    return false;
}

QString stripServiceLogPrefix(QString line)
{
    line = line.trimmed();
    line.remove(QRegularExpression(QStringLiteral("^--\\s+.*\\s+--$")));
    line.replace(QRegularExpression(QStringLiteral("^\\w{3}\\s+\\d{1,2}\\s+\\d{2}:\\d{2}:\\d{2}\\s+\\S+\\s+[^:]+:\\s*")), QString());
    line.replace(QRegularExpression(QStringLiteral("^\\d{4}-\\d{2}-\\d{2}[^ ]*\\s+\\S+\\s+[^:]+:\\s*")), QString());
    line.replace(QRegularExpression(QStringLiteral("\\s+")), QStringLiteral(" "));
    return line.trimmed();
}

QString formatServiceLogResult(const QString &text, const QString &summary)
{
    QStringList highlights;
    QStringList warnings;
    for (const QString &rawLine : text.split(QLatin1Char('\n'))) {
        QString line = stripServiceLogPrefix(rawLine);
        if (line.isEmpty() || line == QStringLiteral("--"))
            continue;
        if (line.contains(QStringLiteral("No entries"), Qt::CaseInsensitive)) {
            highlights << QStringLiteral("当前没有可显示的服务日志。");
            continue;
        }
        if (line.contains(QStringLiteral("Proxy init engine sessionId")))
            continue;
        const bool important = containsAny(line, {QStringLiteral("error"), QStringLiteral("failed"), QStringLiteral("denied"), QStringLiteral("timeout"), QStringLiteral("Traceback"), QStringLiteral("错误"), QStringLiteral("失败"), QStringLiteral("拒绝"), QStringLiteral("超时")});
        if (important)
            warnings << line.left(220);
        if (highlights.size() < 8)
            highlights << line.left(220);
    }

    QStringList lines;
    lines << QStringLiteral("最近服务日志");
    if (!summary.isEmpty())
        lines << QStringLiteral("操作说明：%1").arg(summary.simplified().left(160));
    lines << (warnings.isEmpty()
            ? QStringLiteral("摘要：未发现明显错误信息。")
            : QStringLiteral("摘要：发现 %1 条需要关注的信息。").arg(warnings.size()));
    if (!warnings.isEmpty()) {
        lines << QStringLiteral("需要关注：");
        for (const QString &line : warnings.mid(0, 5))
            lines << QStringLiteral("• %1").arg(line);
    } else if (!highlights.isEmpty()) {
        lines << QStringLiteral("最近信息：");
        for (const QString &line : highlights.mid(0, 6))
            lines << QStringLiteral("• %1").arg(line);
    }
    lines << QStringLiteral("下一步：如果服务状态仍异常，请点击【重新检查状态】；仍无法恢复时，导出系统报告交给管理员排查。");
    return lines.join(QLatin1Char('\n'));
}

QString formatMaintenanceResult(const QString &text, const QString &summary)
{
    const QJsonObject payload = parsedObject(text);
    if (payload.isEmpty()) {
        if (looksLikeServiceLog(text))
            return formatServiceLogResult(text, summary);
        if (text.isEmpty())
            return summary.isEmpty() ? QStringLiteral("本次操作没有返回可显示内容。") : summary;
        return QStringLiteral("维护结果\n%1").arg(text.left(4000));
    }

    QStringList lines;
    lines << QStringLiteral("结果摘要");

    if (payload.contains(QStringLiteral("summary"))) {
        const QJsonObject status = payload.value(QStringLiteral("summary")).toObject();
        const QString overall = status.value(QStringLiteral("overall")).toString();
        if (!overall.isEmpty()) {
            lines << QStringLiteral("本机状态：%1").arg(levelText(overall));
            lines << QStringLiteral("组件结果：正常 %1 项，建议确认 %2 项，不可用 %3 项。")
                         .arg(status.value(QStringLiteral("green")).toInt())
                         .arg(status.value(QStringLiteral("yellow")).toInt())
                         .arg(status.value(QStringLiteral("red")).toInt());
        }
    }

    if (payload.contains(QStringLiteral("embedding")) || payload.contains(QStringLiteral("vector_store"))) {
        const QJsonObject embedding = payload.value(QStringLiteral("embedding")).toObject();
        const QJsonObject model = embedding.value(QStringLiteral("model_info")).toObject();
        lines << QStringLiteral("本机智能检索：%1").arg(statusTextFromBool(embedding.value(QStringLiteral("available")).toBool()));
        if (!model.isEmpty()) {
            lines << QStringLiteral("模型：%1，维度：%2")
                         .arg(model.value(QStringLiteral("name")).toString(QStringLiteral("未识别")))
                         .arg(model.value(QStringLiteral("dim")).toVariant().toString());
        }
        const QJsonObject productState = embedding.value(QStringLiteral("product_state")).toObject();
        const QString message = embedding.value(QStringLiteral("user_message")).toString(productState.value(QStringLiteral("user_message")).toString());
        if (!message.isEmpty())
            lines << QStringLiteral("状态说明：%1").arg(message);
        const QString nextAction = embedding.value(QStringLiteral("next_action")).toString();
        if (!nextAction.isEmpty())
            lines << QStringLiteral("下一步：%1").arg(nextAction);

        const QJsonObject vector = payload.value(QStringLiteral("vector_store")).toObject();
        const QString vectorMessage = vector.value(QStringLiteral("user_message")).toString();
        if (!vectorMessage.isEmpty())
                lines << QStringLiteral("检索服务：%1").arg(vectorMessage);
    }

    if (payload.contains(QStringLiteral("sdk"))) {
        const QJsonObject embedding = payload.value(QStringLiteral("sdk")).toObject().value(QStringLiteral("embedding")).toObject();
        const QString message = embedding.value(QStringLiteral("user_message")).toString();
        if (!message.isEmpty())
            lines << QStringLiteral("本机 AI 能力：%1").arg(message);
        const QString nextAction = embedding.value(QStringLiteral("next_action")).toString();
        if (!nextAction.isEmpty())
            lines << QStringLiteral("下一步：%1").arg(nextAction);
    }

    if (payload.contains(QStringLiteral("resources"))) {
        const QJsonObject resources = payload.value(QStringLiteral("resources")).toObject();
        lines << QStringLiteral("资源组：%1").arg(formatResourceValue(QStringLiteral("control_group"), resources));
        lines << QStringLiteral("当前内存：%1").arg(formatResourceValue(QStringLiteral("memory_current"), resources));
        lines << QStringLiteral("内存上限：%1").arg(formatResourceValue(QStringLiteral("memory_max"), resources));
        lines << QStringLiteral("CPU 配额：%1").arg(formatResourceValue(QStringLiteral("cpu_quota"), resources));
        lines << QStringLiteral("CPU 使用：%1").arg(formatResourceValue(QStringLiteral("cpu_usage"), resources));
    }

    if (payload.contains(QStringLiteral("ok"))) {
        lines << QStringLiteral("执行结果：%1").arg(statusTextFromBool(payload.value(QStringLiteral("ok")).toBool()));
        const QString message = payload.value(QStringLiteral("message")).toString();
        if (!message.isEmpty())
            lines << QStringLiteral("说明：%1").arg(message);
    }
    if (payload.contains(QStringLiteral("output_dir")))
        lines << QStringLiteral("保存位置：%1").arg(payload.value(QStringLiteral("output_dir")).toString());
    if (payload.contains(QStringLiteral("file_count")))
        lines << QStringLiteral("证据文件：%1 个").arg(payload.value(QStringLiteral("file_count")).toVariant().toString());
    if (payload.contains(QStringLiteral("source_revision_status"))) {
        const QString revisionStatus = payload.value(QStringLiteral("source_revision_status")).toString();
        lines << QStringLiteral("版本记录：%1").arg(revisionStatus == QStringLiteral("not_available")
                ? QStringLiteral("当前运行环境未提供 Git 信息，最终提交建议在源码目录重新采集")
                : QStringLiteral("已记录"));
    }
    if (payload.contains(QStringLiteral("checked")))
        lines << QStringLiteral("操作记录检查数量：%1").arg(payload.value(QStringLiteral("checked")).toVariant().toString());
    if (payload.contains(QStringLiteral("failed_audit_id")))
        lines << QStringLiteral("需要处理的操作记录：%1").arg(payload.value(QStringLiteral("failed_audit_id")).toVariant().toString());

    const QJsonArray recommendations = payload.value(QStringLiteral("recommendations")).toArray();
    if (!recommendations.isEmpty()) {
        lines << QStringLiteral("处理建议：");
        for (const QJsonValue &value : recommendations)
            lines << QStringLiteral("• %1").arg(friendlyRecommendationText(value.toString()));
    }
    lines << QStringLiteral("需要保存完整诊断信息时，请使用【导出系统报告】。");
    return lines.join(QLatin1Char('\n'));
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
    applyPageSpacing(layout);
    layout->addWidget(makeSectionHeader(
        QStringLiteral("security-high"),
        QStringLiteral("MemGuardOS 记忆安全"),
        QStringLiteral("管理本机 Agent 记忆、操作记录完整性、本机智能检索和桌面入口。")));

    auto *summaryBox = new QGroupBox(QStringLiteral("当前状态"));
    auto *summaryLayout = new QHBoxLayout(summaryBox);
    applyPanelSpacing(summaryLayout);
    summaryIcon_ = new QLabel;
    summaryIcon_->setFixedSize(32, 32);
    summaryIcon_->setScaledContents(true);
    auto *summaryText = new QVBoxLayout;
    summaryTitle_ = new QLabel(QStringLiteral("正在检查…"));
    QFont summaryFont = summaryTitle_->font();
    summaryFont.setBold(true);
    summaryTitle_->setFont(summaryFont);
    summaryDescription_ = new QLabel(QStringLiteral("正在读取服务、操作记录、本机智能检索和桌面入口状态。"));
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

    resultSummary_ = new QLabel(QStringLiteral("操作结果：等待状态检查。"));
    resultSummary_->setWordWrap(true);
    resultSummary_->setAccessibleName(QStringLiteral("最近一次操作结果摘要"));
    layout->addWidget(resultSummary_);

    operationGuide_ = makeMutedLabel(QStringLiteral("暂无后台操作。状态会显示在本页；日志、操作记录、本机智能检索和报告结果会显示在【系统维护】>【维护结果】或保存为文件。"));
    operationGuide_->setAccessibleName(QStringLiteral("操作进度和结果位置说明"));
    layout->addWidget(operationGuide_);

    tabs_ = new QTabWidget;
    tabs_->setAccessibleName(QStringLiteral("MemGuardOS 设置分类"));
    overviewTabIndex_ = tabs_->addTab(buildOverviewTab(), themedIcon(QStringLiteral("user-home"), QStyle::SP_DirHomeIcon), QStringLiteral("日常使用"));
    serviceTabIndex_ = tabs_->addTab(buildServiceTab(), themedIcon(QStringLiteral("preferences-system"), QStyle::SP_ComputerIcon), QStringLiteral("管理员"));
    diagnosticsTabIndex_ = tabs_->addTab(buildDiagnosticsTab(), themedIcon(QStringLiteral("preferences-system"), QStyle::SP_FileDialogDetailedView), QStringLiteral("系统维护"));
    layout->addWidget(tabs_, 1);
    applyNavigationTarget(takeNavigationTarget());

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
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 日常使用滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    applyPageSpacing(layout);
    layout->addWidget(makeSectionHeader(
        QStringLiteral("user-home"),
        QStringLiteral("日常使用概览"),
        QStringLiteral("查看本机记忆安全状态，完成文件扫描、遗忘预览和常用入口操作。")));

    auto *healthBox = new QGroupBox(QStringLiteral("服务健康"));
    auto *healthLayout = new QGridLayout(healthBox);
    applyPanelSpacing(healthLayout);
    applyGridRhythm(healthLayout);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("服务运行"),
                                QStringLiteral("记忆后台服务和本地接口是否可用。"),
                                &serviceStatusIcon_,
                                &serviceStatusLabel_),
        0, 0);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("操作记录完整性"),
                                QStringLiteral("重要操作是否有完整记录。"),
                                &auditStatusIcon_,
                                &auditStatusLabel_),
        0, 1);
    healthLayout->addWidget(makeStatusCard(
                                QStringLiteral("本机智能检索"),
                                QStringLiteral("系统自带 AI 能力和备用检索是否可用。"),
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

    auto *behaviorBox = new QGroupBox(QStringLiteral("最近记忆行为"));
    auto *behaviorLayout = new QVBoxLayout(behaviorBox);
    applyPanelSpacing(behaviorLayout);
    behaviorSummary_ = new QLabel(QStringLiteral("正在读取最近新增记忆、偏好生效和风险处理结果。"));
    behaviorSummary_->setWordWrap(true);
    behaviorSummary_->setAccessibleName(QStringLiteral("最近记忆行为摘要"));
    behaviorLayout->addWidget(behaviorSummary_);

    auto *behaviorActions = new QGridLayout;
    applyGridRhythm(behaviorActions);
    recentMemoryButton_ = makeOperationButton(QStringLiteral("view-list-details"), QStringLiteral("最近新增记忆"), QStringLiteral("打开记忆中心查看最近新增内容"));
    recentPreferenceButton_ = makeOperationButton(QStringLiteral("preferences-system"), QStringLiteral("最近生效偏好"), QStringLiteral("打开偏好与策略查看当前生效偏好"));
    recentSensitiveButton_ = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("最近已保护内容"), QStringLiteral("打开敏感保护查看最近保护结果"));
    recentForgetButton_ = makeOperationButton(QStringLiteral("edit-delete"), QStringLiteral("最近遗忘验证"), QStringLiteral("打开精准遗忘查看最近验证结果"));
    behaviorActions->addWidget(recentMemoryButton_, 0, 0);
    behaviorActions->addWidget(recentPreferenceButton_, 0, 1);
    behaviorActions->addWidget(recentSensitiveButton_, 1, 0);
    behaviorActions->addWidget(recentForgetButton_, 1, 1);
    behaviorLayout->addLayout(behaviorActions);

    behaviorTimeline_ = new QTreeWidget;
    behaviorTimeline_->setColumnCount(3);
    behaviorTimeline_->setHeaderLabels({QStringLiteral("类型"), QStringLiteral("摘要"), QStringLiteral("状态")});
    behaviorTimeline_->setRootIsDecorated(false);
    behaviorTimeline_->setAlternatingRowColors(true);
    behaviorTimeline_->setSelectionMode(QAbstractItemView::SingleSelection);
    behaviorTimeline_->setMinimumHeight(128);
    behaviorTimeline_->setAccessibleName(QStringLiteral("最近记忆行为事件列表"));
    behaviorTimeline_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    behaviorTimeline_->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    behaviorTimeline_->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    behaviorLayout->addWidget(behaviorTimeline_);

    auto *refreshBehavior = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("查看最近变化"), QStringLiteral("查看最近新增记忆、Agent 偏好和风险处理结果"));
    behaviorLayout->addWidget(refreshBehavior);
    layout->addWidget(behaviorBox);

    auto *taskBox = new QGroupBox(QStringLiteral("常用操作"));
    taskBox->setAccessibleName(QStringLiteral("普通用户常用入口"));
    auto *taskLayout = new QGridLayout(taskBox);
    applyPanelSpacing(taskLayout);
    applyGridRhythm(taskLayout);
    auto *openConsole = makeOperationButton(QStringLiteral("applications-internet"), QStringLiteral("打开本地控制台"), QStringLiteral("打开 MemGuardOS 本地控制台"));
    auto *openAgent = makeOperationButton(QStringLiteral("system-run"), QStringLiteral("打开 Agent 工作台"), QStringLiteral("查看 Agent 交互、偏好生效和遗忘验证"));
    auto *runJourney = makeOperationButton(QStringLiteral("media-playback-start"), QStringLiteral("运行体验流程"), QStringLiteral("运行一次本地可复现的记忆写入、使用、保护和遗忘流程"));
    auto *openFiles = makeOperationButton(QStringLiteral("system-file-manager"), QStringLiteral("打开文件管理器"), QStringLiteral("打开文件管理器后可右键文件进行记忆扫描"));
    auto *forgetEntry = makeOperationButton(QStringLiteral("edit-delete"), QStringLiteral("查看遗忘入口"), QStringLiteral("打开本地控制台查看记忆遗忘入口"));
    auto *refreshNow = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重新检查状态"), QStringLiteral("重新检查 MemGuardOS 状态"));
    taskLayout->addWidget(openConsole, 0, 0);
    taskLayout->addWidget(openFiles, 0, 1);
    taskLayout->addWidget(forgetEntry, 1, 0);
    taskLayout->addWidget(refreshNow, 1, 1);
    taskLayout->addWidget(openAgent, 2, 0);
    taskLayout->addWidget(runJourney, 2, 1);
    taskLayout->addWidget(makeMutedLabel(QStringLiteral("日常操作只保留常用入口：查看服务状态、用文件管理器右键扫描文件、在本地控制台预览遗忘影响范围。日志、内核保护和系统授权放在【管理员】页。")), 3, 0, 1, 2);
    layout->addWidget(taskBox);

    auto *flowBox = new QGroupBox(QStringLiteral("记忆治理流程"));
    auto *flowLayout = new QVBoxLayout(flowBox);
    applyPanelSpacing(flowLayout);
    auto *flowTitle = new QLabel(QStringLiteral("保存 → 理解 → 使用 → 遗忘"));
    QFont flowFont = flowTitle->font();
    flowFont.setBold(true);
    flowFont.setPointSizeF(flowFont.pointSizeF() * 1.1);
    flowTitle->setFont(flowFont);
    flowTitle->setAlignment(Qt::AlignCenter);
    auto *flowHint = new QLabel(QStringLiteral("普通用户主要确认服务是否正常、查看文件是否安全、遗忘不再需要的记忆；管理员负责操作记录校验、系统授权、系统报告和高风险维护。"));
    flowHint->setWordWrap(true);
    flowLayout->addWidget(flowTitle);
    flowLayout->addWidget(flowHint);
    layout->addWidget(flowBox);

    auto *userBox = new QGroupBox(QStringLiteral("普通用户关注项"));
    auto *userLayout = new QGridLayout(userBox);
    applyPanelSpacing(userLayout);
    applyGridRhythm(userLayout);
    userLayout->addWidget(makeStatusCard(
                              QStringLiteral("记忆服务"),
                              QStringLiteral("确认 Agent 是否可以读写本机记忆。"),
                              nullptr,
                              nullptr),
        0, 0);
    userLayout->addWidget(makeStatusCard(
                              QStringLiteral("文件安全"),
                              QStringLiteral("通过 Peony 右键检查文件是否适合导入。"),
                              nullptr,
                              nullptr),
        0, 1);
    userLayout->addWidget(makeStatusCard(
                              QStringLiteral("已保护敏感内容"),
                              QStringLiteral("发现高风险内容时会跳过保存，并留下操作记录。"),
                              nullptr,
                              nullptr),
        1, 0);
    userLayout->addWidget(makeStatusCard(
                              QStringLiteral("遗忘指定记忆"),
                              QStringLiteral("在本地控制台先预览影响范围，再确认遗忘。"),
                              nullptr,
                              nullptr),
        1, 1);
    layout->addWidget(userBox);

    auto *recommendationBox = new QGroupBox(QStringLiteral("处理建议"));
    auto *recommendationLayout = new QVBoxLayout(recommendationBox);
    applyPanelSpacing(recommendationLayout);
    recommendations_ = new QListWidget;
    recommendations_->setWordWrap(true);
    recommendations_->setAlternatingRowColors(true);
    recommendations_->setSelectionMode(QAbstractItemView::NoSelection);
    recommendations_->setAccessibleName(QStringLiteral("MemGuardOS 当前处理建议"));
    recommendations_->setMinimumHeight(76);
    recommendationLayout->addWidget(recommendations_);
    recommendationLayout->addWidget(makeMutedLabel(QStringLiteral("点击每条建议右侧按钮，可跳转到具体处理区，或直接执行操作记录校验、本机智能检索检查等安全动作。")));
    layout->addWidget(recommendationBox);

    auto *moreBox = new QGroupBox(QStringLiteral("更多状态"));
    auto *moreLayout = new QVBoxLayout(moreBox);
    applyPanelSpacing(moreLayout);
    overviewDetailsToggle_ = makeDisclosureButton(QStringLiteral("查看组件、资源和操作历史"), QStringLiteral("展开或收起更多状态"));
    moreLayout->addWidget(overviewDetailsToggle_);
    overviewDetailsPanel_ = new QWidget;
    auto *detailsPanelLayout = new QVBoxLayout(overviewDetailsPanel_);
    detailsPanelLayout->setContentsMargins(0, 8, 0, 0);
    detailsPanelLayout->setSpacing(12);
    overviewDetailsPanel_->setVisible(false);

    auto *componentsBox = new QGroupBox(QStringLiteral("组件状态详情"));
    auto *componentsLayout = new QVBoxLayout(componentsBox);
    applyPanelSpacing(componentsLayout);
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
    detailsPanelLayout->addWidget(componentsBox);

    auto *detailGrid = new QGridLayout;
    applyGridRhythm(detailGrid);

    auto *resourceBox = new QGroupBox(QStringLiteral("资源快照"));
    auto *resourceLayout = new QVBoxLayout(resourceBox);
    applyPanelSpacing(resourceLayout);
    resourceSnapshot_ = new QTreeWidget;
    resourceSnapshot_->setColumnCount(2);
    resourceSnapshot_->setHeaderLabels({QStringLiteral("项目"), QStringLiteral("当前值")});
    resourceSnapshot_->setRootIsDecorated(false);
    resourceSnapshot_->setAlternatingRowColors(true);
    resourceSnapshot_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    resourceSnapshot_->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    resourceSnapshot_->setAccessibleName(QStringLiteral("记忆服务资源快照"));
    resourceLayout->addWidget(resourceSnapshot_);
    detailGrid->addWidget(resourceBox, 0, 0);

    auto *historyBox = new QGroupBox(QStringLiteral("操作历史"));
    auto *historyLayout = new QVBoxLayout(historyBox);
    applyPanelSpacing(historyLayout);
    operationHistory_ = new QTreeWidget;
    operationHistory_->setColumnCount(3);
    operationHistory_->setHeaderLabels({QStringLiteral("操作"), QStringLiteral("结果"), QStringLiteral("说明")});
    operationHistory_->setRootIsDecorated(false);
    operationHistory_->setAlternatingRowColors(true);
    operationHistory_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    operationHistory_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    operationHistory_->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    operationHistory_->setAccessibleName(QStringLiteral("MemGuardOS 最近操作历史"));
    historyLayout->addWidget(operationHistory_);
    detailGrid->addWidget(historyBox, 0, 1);
    detailsPanelLayout->addLayout(detailGrid);

    auto *hint = new QLabel(QStringLiteral("状态来自本机实时检查。选择【重新检查状态】可刷新服务、本机智能检索、操作记录和资源信息。管理员维护入口位于【管理员】和【系统维护】页。"));
    hint->setWordWrap(true);
    detailsPanelLayout->addWidget(hint);
    moreLayout->addWidget(overviewDetailsPanel_);
    layout->addWidget(moreBox);
    actionButtons_ << openConsole << openAgent << runJourney << openFiles << forgetEntry << refreshNow << refreshBehavior
                   << recentMemoryButton_ << recentPreferenceButton_ << recentSensitiveButton_ << recentForgetButton_;
    connect(overviewDetailsToggle_, &QToolButton::toggled, this, [this](bool checked) {
        overviewDetailsToggle_->setArrowType(checked ? Qt::DownArrow : Qt::RightArrow);
        overviewDetailsPanel_->setVisible(checked);
    });
    connect(openConsole, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
    connect(openAgent, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#agent")); });
    connect(runJourney, &QPushButton::clicked, this, &MemGuardUkccPlugin::runUserJourney);
    connect(openFiles, &QPushButton::clicked, this, [this]() {
        QDesktopServices::openUrl(QUrl::fromLocalFile(QDir::homePath()));
        appendHistory(QStringLiteral("打开文件管理器"), true, QStringLiteral("已打开文件管理器。选择文本文件后可通过 Peony 右键执行 MemGuardOS 记忆扫描。"));
    });
    connect(forgetEntry, &QPushButton::clicked, this, [this]() {
        appendHistory(QStringLiteral("查看遗忘入口"), true, QStringLiteral("已打开本地控制台。进入记忆列表后可先预览影响范围，再确认遗忘。"));
        openDashboard();
    });
    connect(refreshNow, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    connect(refreshBehavior, &QPushButton::clicked, this, [this]() {
        runMemctl({QStringLiteral("feedback-home"), QStringLiteral("--limit"), QStringLiteral("6")}, QStringLiteral("feedback"));
    });
    connect(recentMemoryButton_, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#memory")); });
    connect(recentPreferenceButton_, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#preferences")); });
    connect(recentSensitiveButton_, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#sensitive")); });
    connect(recentForgetButton_, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#forget")); });
    scroll->setWidget(tab);
    return scroll;
}

QWidget *MemGuardUkccPlugin::buildServiceTab()
{
    auto *scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 管理员滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    applyPageSpacing(layout);
    layout->addWidget(makeSectionHeader(
        QStringLiteral("preferences-system"),
        QStringLiteral("管理员控制台"),
        QStringLiteral("集中处理服务控制、本机智能检索、安全审计和系统授权。")));
    auto *adminGrid = new QGridLayout;
    applyGridRhythm(adminGrid);

    auto *serviceBox = new QGroupBox(QStringLiteral("管理员工具：服务控制"));
    auto *serviceLayout = new QVBoxLayout(serviceBox);
    applyPanelSpacing(serviceLayout);
    serviceLayout->addWidget(makeMutedLabel(QStringLiteral("管理记忆后台服务。日常使用时保持服务运行；维护前可查看日志，确认问题后再重启。")));
    serviceLayout->addWidget(makeSeparator());
    serviceState_ = new QLabel(QStringLiteral("等待检查"));
    serviceState_->setWordWrap(true);
    serviceLayout->addWidget(serviceState_);
    auto *serviceButtons = new QGridLayout;
    applyGridRhythm(serviceButtons);
    auto *start = makeOperationButton(QStringLiteral("media-playback-start"), QStringLiteral("启动服务"), QStringLiteral("启动 MemGuardOS 记忆服务"));
    auto *stop = makeOperationButton(QStringLiteral("media-playback-stop"), QStringLiteral("停止服务"), QStringLiteral("停止 MemGuardOS 记忆服务"));
    auto *restart = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重启服务"), QStringLiteral("重启 MemGuardOS 记忆服务"));
    auto *logs = makeOperationButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看记忆后台服务日志"));
    serviceButtons->addWidget(start, 0, 0);
    serviceButtons->addWidget(stop, 0, 1);
    serviceButtons->addWidget(restart, 0, 2);
    serviceButtons->addWidget(logs, 0, 3);
    serviceLayout->addLayout(serviceButtons);
    auto *serviceHint = makeMutedLabel(QStringLiteral("启动、停止和重启属于管理员授权操作，会通过 Polkit 显示系统授权窗口。"));
    serviceHint->setAccessibleName(QStringLiteral("管理员授权说明"));
    serviceLayout->addWidget(serviceHint);
    adminGrid->addWidget(serviceBox, 0, 0);

    auto *sdkBox = new QGroupBox(QStringLiteral("管理员工具：本机智能检索"));
    auto *sdkLayout = new QVBoxLayout(sdkBox);
    applyPanelSpacing(sdkLayout);
    sdkLayout->addWidget(makeMutedLabel(QStringLiteral("检查系统自带 AI 检索能力和本地模型状态。该能力未就绪时，系统会自动使用备用检索，不影响普通记忆检索。")));
    sdkLayout->addWidget(makeMutedLabel(QStringLiteral("该检查会调用本机模型；日常状态页只做轻量检查，避免拖慢设置界面。")));
    sdkLayout->addWidget(makeSeparator());
    sdkState_ = new QLabel(QStringLiteral("等待检查"));
    sdkState_->setWordWrap(true);
    auto *sdkButtons = new QGridLayout;
    applyGridRhythm(sdkButtons);
    auto *sdkCheck = makeOperationButton(QStringLiteral("system-search"), QStringLiteral("检查本机智能检索"), QStringLiteral("检查本机 AI 检索能力"));
    auto *warmup = makeOperationButton(QStringLiteral("media-playback-start"), QStringLiteral("预热本机智能检索"), QStringLiteral("预热本机智能检索能力"));
    sdkButtons->addWidget(sdkCheck, 0, 0);
    sdkButtons->addWidget(warmup, 0, 1);
    sdkLayout->addWidget(sdkState_);
    sdkLayout->addLayout(sdkButtons);
    adminGrid->addWidget(sdkBox, 0, 1);

    auto *securityBox = new QGroupBox(QStringLiteral("管理员工具：安全审计"));
    auto *securityLayout = new QVBoxLayout(securityBox);
    applyPanelSpacing(securityLayout);
    securityLayout->addWidget(makeMutedLabel(QStringLiteral("校验操作记录完整性、安装本机记录规则，并检查可选内核保护路径。高风险操作需要管理员授权。")));
    securityLayout->addWidget(makeSeparator());
    auditState_ = new QLabel(QStringLiteral("等待检查"));
    auditState_->setWordWrap(true);
    auto *securityButtons = new QGridLayout;
    applyGridRhythm(securityButtons);
    auto *verifyAudit = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("校验操作记录"), QStringLiteral("校验 MemGuardOS 操作记录完整性"));
    auto *installAudit = makeOperationButton(QStringLiteral("list-add"), QStringLiteral("安装审计规则"), QStringLiteral("安装 MemGuardOS 审计规则"));
    auto *bpfTest = makeOperationButton(QStringLiteral("dialog-warning"), QStringLiteral("检查内核保护状态"), QStringLiteral("检查 MemGuardOS 内核保护状态"));
    securityButtons->addWidget(verifyAudit, 0, 0);
    securityButtons->addWidget(installAudit, 0, 1);
    securityButtons->addWidget(bpfTest, 0, 2);
    securityLayout->addWidget(auditState_);
    securityLayout->addLayout(securityButtons);
    auto *securityHint = makeMutedLabel(QStringLiteral("安装审计规则和内核保护检查会触发管理员授权；未知动作会被白名单 helper 拒绝。"));
    securityLayout->addWidget(securityHint);
    adminGrid->addWidget(securityBox, 1, 0);

    auto *permissionBox = new QGroupBox(QStringLiteral("系统授权"));
    auto *permissionLayout = new QVBoxLayout(permissionBox);
    applyPanelSpacing(permissionLayout);
    permissionLayout->addWidget(makeMutedLabel(QStringLiteral("控制中心只暴露固定白名单操作：服务启停、审计规则安装、内核保护检查和状态诊断。普通记忆写入、检索和遗忘预览不需要提升权限。")));
    permissionLayout->addWidget(makeMutedLabel(QStringLiteral("如果授权弹窗未出现，请确认 Polkit policy 和 native helper 已安装，再重新打开控制中心。")));
    adminGrid->addWidget(permissionBox, 1, 1);
    layout->addLayout(adminGrid);
    layout->addStretch();

    actionButtons_ << start << stop << restart << logs << sdkCheck << warmup << verifyAudit << installAudit << bpfTest;
    connect(start, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("native-action"), QStringLiteral("start-service")}, QStringLiteral("action")); });
    connect(stop, &QPushButton::clicked, this, [this]() {
        if (QMessageBox::question(page_, QStringLiteral("停止记忆服务"), QStringLiteral("停止后 Agent 将暂时不能读写 MemGuardOS 记忆。确定停止吗？")) == QMessageBox::Yes)
            runMemctl({QStringLiteral("native-action"), QStringLiteral("stop-service")}, QStringLiteral("action"));
    });
    connect(restart, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("native-action"), QStringLiteral("restart-service")}, QStringLiteral("action")); });
    connect(logs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("50"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
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
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setAccessibleName(QStringLiteral("MemGuardOS 系统维护滚动区域"));
    auto *tab = new QWidget;
    auto *layout = new QVBoxLayout(tab);
    applyPageSpacing(layout);
    layout->addWidget(makeSectionHeader(
        QStringLiteral("document-save"),
        QStringLiteral("维护与报告"),
        QStringLiteral("导出系统报告、打开本地控制台、查看维护结果和本机证据。")));

    auto *introBox = new QGroupBox(QStringLiteral("系统维护说明"));
    auto *introLayout = new QVBoxLayout(introBox);
    applyPanelSpacing(introLayout);
    introLayout->addWidget(makeMutedLabel(QStringLiteral("这里用于导出系统报告、打开本地控制台、刷新状态和查看维护结果。报告默认不包含记忆正文，适合用于问题排查和提交前自检。")));
    introLayout->addWidget(makeSeparator());
    layout->addWidget(introBox);

    auto *maintenanceGrid = new QGridLayout;
    applyGridRhythm(maintenanceGrid);

    auto *reportBox = new QGroupBox(QStringLiteral("报告与控制台"));
    auto *reportLayout = new QVBoxLayout(reportBox);
    applyPanelSpacing(reportLayout);
    reportLayout->addWidget(makeMutedLabel(QStringLiteral("导出系统报告会聚合服务、本机智能检索、资源、授权和操作记录状态；本机证据会生成带 SHA256 清单的状态目录；本地控制台提供更完整的记忆写入、检索、上下文和遗忘预览流程。")));
    auto *reportButtons = new QGridLayout;
    applyGridRhythm(reportButtons);
    auto *diagnostics = makeOperationButton(QStringLiteral("document-save"), QStringLiteral("导出快速报告"), QStringLiteral("导出 MemGuardOS 系统报告"));
    auto *evidence = makeOperationButton(QStringLiteral("document-send"), QStringLiteral("导出本机证据"), QStringLiteral("导出 MemGuardOS openKylin 本机证据包"));
    auto *dashboard = makeOperationButton(QStringLiteral("applications-internet"), QStringLiteral("打开本地控制台"), QStringLiteral("打开 MemGuardOS 本地控制台"));
    reportButtons->addWidget(diagnostics, 0, 0);
    reportButtons->addWidget(evidence, 0, 1);
    reportButtons->addWidget(dashboard, 0, 2);
    reportLayout->addLayout(reportButtons);
    maintenanceGrid->addWidget(reportBox, 0, 0);

    auto *maintenanceBox = new QGroupBox(QStringLiteral("维护工具"));
    auto *maintenanceLayout = new QVBoxLayout(maintenanceBox);
    applyPanelSpacing(maintenanceLayout);
    maintenanceLayout->addWidget(makeMutedLabel(QStringLiteral("这些工具用于快速确认当前页面状态和后台服务状态。需要查看处理结果时再展开下方维护结果。")));
    auto *maintenanceButtons = new QGridLayout;
    applyGridRhythm(maintenanceButtons);
    auto *refresh = makeOperationButton(QStringLiteral("view-refresh"), QStringLiteral("重新检查状态"), QStringLiteral("重新检查 MemGuardOS 状态"));
    auto *verifyAudit = makeOperationButton(QStringLiteral("security-high"), QStringLiteral("校验操作记录"), QStringLiteral("校验 MemGuardOS 操作记录完整性"));
    auto *logs = makeOperationButton(QStringLiteral("text-x-log"), QStringLiteral("查看服务日志"), QStringLiteral("查看记忆后台服务日志"));
    maintenanceButtons->addWidget(refresh, 0, 0);
    maintenanceButtons->addWidget(verifyAudit, 0, 1);
    maintenanceButtons->addWidget(logs, 0, 2);
    maintenanceLayout->addLayout(maintenanceButtons);
    maintenanceGrid->addWidget(maintenanceBox, 0, 1);
    layout->addLayout(maintenanceGrid);

    auto *preflightBox = new QGroupBox(QStringLiteral("提交前复核"));
    auto *preflightLayout = new QVBoxLayout(preflightBox);
    applyPanelSpacing(preflightLayout);
    preflightLayout->addWidget(makeMutedLabel(QStringLiteral("报告默认不包含记忆正文，适合在交付、验收或问题排查前复核本机状态。更多复核项会在 Web 验收中心分项执行。")));
    auto *preflightButtons = new QGridLayout;
    applyGridRhythm(preflightButtons);
    auto *runAcceptance = makeOperationButton(QStringLiteral("system-run"), QStringLiteral("运行本机验收复核"), QStringLiteral("打开 Web 验收中心并运行分项复核"));
    auto *exportOpenKylin = makeOperationButton(QStringLiteral("document-send"), QStringLiteral("导出 openKylin 证据"), QStringLiteral("导出带校验清单的 openKylin 本机证据"));
    auto *exportSystemReport = makeOperationButton(QStringLiteral("document-save"), QStringLiteral("导出系统报告"), QStringLiteral("导出不包含记忆正文的系统状态报告"));
    auto *openAcceptance = makeOperationButton(QStringLiteral("applications-internet"), QStringLiteral("打开 Web 验收中心"), QStringLiteral("打开本地 Web 验收中心"));
    preflightButtons->addWidget(runAcceptance, 0, 0);
    preflightButtons->addWidget(exportOpenKylin, 0, 1);
    preflightButtons->addWidget(exportSystemReport, 1, 0);
    preflightButtons->addWidget(openAcceptance, 1, 1);
    preflightLayout->addLayout(preflightButtons);
    layout->addWidget(preflightBox);

    auto *detailsBox = new QGroupBox(QStringLiteral("维护结果"));
    auto *detailsLayout = new QVBoxLayout(detailsBox);
    applyPanelSpacing(detailsLayout);
    detailsToggle_ = new QToolButton;
    detailsToggle_->setText(QStringLiteral("查看维护结果"));
    detailsToggle_->setCheckable(true);
    detailsToggle_->setToolButtonStyle(Qt::ToolButtonTextBesideIcon);
    detailsToggle_->setArrowType(Qt::RightArrow);
    detailsToggle_->setAccessibleName(QStringLiteral("展开或收起维护结果"));
    details_ = new QTextEdit;
    details_->setReadOnly(true);
    details_->setVisible(false);
    details_->setAccessibleName(QStringLiteral("MemGuardOS 维护结果"));
    detailsLayout->addWidget(detailsToggle_);
    detailsLayout->addWidget(details_, 1);
    layout->addWidget(detailsBox, 1);

    actionButtons_ << diagnostics << evidence << dashboard << refresh << verifyAudit << logs
                   << runAcceptance << exportOpenKylin << exportSystemReport << openAcceptance;
    connect(detailsToggle_, &QToolButton::toggled, this, [this](bool checked) {
        detailsToggle_->setArrowType(checked ? Qt::DownArrow : Qt::RightArrow);
        details_->setVisible(checked);
    });
    connect(diagnostics, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics")}, QStringLiteral("export")); });
    connect(evidence, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("collect-openkylin-evidence")}, QStringLiteral("details")); });
    connect(dashboard, &QPushButton::clicked, this, &MemGuardUkccPlugin::openDashboard);
    connect(refresh, &QPushButton::clicked, this, &MemGuardUkccPlugin::refreshStatus);
    connect(verifyAudit, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("verify-audit")}, QStringLiteral("details")); });
    connect(logs, &QPushButton::clicked, this, [this]() { runProgram(QStringLiteral("journalctl"), {QStringLiteral("-u"), QStringLiteral("memguardd.service"), QStringLiteral("-n"), QStringLiteral("50"), QStringLiteral("--no-pager")}, QStringLiteral("details")); });
    connect(runAcceptance, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#acceptance")); });
    connect(exportOpenKylin, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("collect-openkylin-evidence")}, QStringLiteral("details")); });
    connect(exportSystemReport, &QPushButton::clicked, this, [this]() { runMemctl({QStringLiteral("diagnostics")}, QStringLiteral("export")); });
    connect(openAcceptance, &QPushButton::clicked, this, [this]() { openDashboardTarget(QStringLiteral("#acceptance")); });
    scroll->setWidget(tab);
    return scroll;
}

bool MemGuardUkccPlugin::isShowOnHomePage() const { return true; }
QIcon MemGuardUkccPlugin::icon() const { return themedIcon(QStringLiteral("security-high"), QStyle::SP_MessageBoxInformation); }
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
        const QString activePurpose = currentProcess_->property("purpose").toString();
        if (activePurpose == QStringLiteral("refresh") && purpose != QStringLiteral("refresh")) {
            currentProcess_->setProperty("handled", true);
            currentProcess_->kill();
            currentProcess_.clear();
            appendHistory(QStringLiteral("状态刷新"), true, QStringLiteral("后台刷新已让位给你刚选择的操作。"));
        } else {
            QMessageBox::information(page_, QStringLiteral("已有操作正在处理"), QStringLiteral("上一个操作尚未结束。可以等待完成，或点击【停止当前操作】后再执行新的操作。"));
            return;
        }
    }
    setBusy(true, operationStartMessage(arguments, purpose), purpose != QStringLiteral("refresh"));
    auto *process = new QProcess(this);
    currentProcess_ = process;
    QPointer<QProcess> guard(process);
    process->setProperty("purpose", purpose);
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
            bool exportSaved = true;
            if (purpose == QStringLiteral("refresh") && !payload.isEmpty()) {
                renderStatus(payload);
                QTimer::singleShot(0, this, [this]() {
                    runMemctl({QStringLiteral("feedback-home"), QStringLiteral("--limit"), QStringLiteral("6")}, QStringLiteral("feedback"));
                });
            }
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
            } else if (purpose == QStringLiteral("feedback") && exitCode == 0) {
                renderFeedbackSummary(output);
            } else if (exitCode != 0) {
                showDetailsOutput(output, summaryMessage);
                showError(QStringLiteral("操作失败"), output);
            } else {
                showDetailsOutput(output, summaryMessage);
            }
            QString completionMessage = operationCompleteMessage(arguments, purpose, exitCode);
            if (purpose == QStringLiteral("export") && exitCode == 0 && !exportSaved)
                completionMessage = QStringLiteral("报告保存已取消，系统状态未改变。");
            setBusy(false, completionMessage);
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
        setBusy(false, QStringLiteral("操作超时，已停止后台任务。"));
        appendHistory(purposeText(purpose), false, QStringLiteral("操作超时，已自动停止。"));
        showError(QStringLiteral("操作超时"), QStringLiteral("本次操作长时间没有完成，系统已自动停止。请查看服务日志或导出系统报告后重试。"));
    });
    process->start(program, arguments);
}

QString MemGuardUkccPlugin::operationStartMessage(const QStringList &arguments, const QString &purpose) const
{
    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return QStringLiteral("正在读取最近 50 条服务日志，结果会显示在【系统维护】。");
    if (joined.contains(QStringLiteral("verify-audit")))
        return QStringLiteral("正在校验操作记录完整性，完成后会打开结果。");
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")))
        return QStringLiteral("正在检查本机智能检索，窗口可保持打开，也可以随时停止。");
    if (joined.contains(QStringLiteral("diagnostics")))
        return QStringLiteral("正在生成系统报告，完成后请选择保存位置。");
    if (joined.contains(QStringLiteral("collect-openkylin-evidence")))
        return QStringLiteral("正在导出本机证据，完成后会显示保存位置。");
    if (joined.contains(QStringLiteral("native-action")))
        return QStringLiteral("正在等待系统授权并执行操作。");
    if (joined.contains(QStringLiteral("openkylin-status")) && joined.contains(QStringLiteral("--fast")))
        return QStringLiteral("正在快速刷新本机状态。");
    if (joined.contains(QStringLiteral("feedback-home")))
        return QStringLiteral("正在读取最近的记忆变化。");
    if (joined.contains(QStringLiteral(" demo")) || joined == QStringLiteral("demo"))
        return QStringLiteral("正在运行本地可复现体验流程，结果会显示在【系统维护】。");
    if (purpose == QStringLiteral("details"))
        return QStringLiteral("正在读取详情，结果会显示在【系统维护】。");
    return QStringLiteral("正在处理本次操作，窗口可保持打开。");
}

QString MemGuardUkccPlugin::operationCompleteMessage(const QStringList &arguments, const QString &purpose, int exitCode) const
{
    if (exitCode != 0)
        return QStringLiteral("操作未完成，已显示错误详情。");

    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return QStringLiteral("服务日志已显示在【系统维护】>【维护结果】。");
    if (joined.contains(QStringLiteral("verify-audit")))
        return QStringLiteral("操作记录校验已完成，结果已显示在【系统维护】。");
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")))
        return QStringLiteral("本机智能检索检查已完成，结果已显示在【系统维护】。");
    if (joined.contains(QStringLiteral("diagnostics")))
        return QStringLiteral("系统报告已生成。");
    if (joined.contains(QStringLiteral("collect-openkylin-evidence")))
        return QStringLiteral("本机证据已导出，保存位置已显示在【系统维护】。");
    if (purpose == QStringLiteral("refresh"))
        return QStringLiteral("状态刷新已完成，可以继续操作。");
    if (purpose == QStringLiteral("feedback"))
        return QStringLiteral("最近的记忆变化已更新，可以继续操作。");
    if (joined.contains(QStringLiteral(" demo")) || joined == QStringLiteral("demo"))
        return QStringLiteral("体验流程已完成，结果已显示在【系统维护】>【维护结果】。");
    if (purpose == QStringLiteral("action"))
        return QStringLiteral("系统操作已完成，正在刷新状态。");
    if (purpose == QStringLiteral("details"))
        return QStringLiteral("维护结果已显示在【系统维护】。");
    return QStringLiteral("操作已完成，可以继续使用。");
}

int MemGuardUkccPlugin::operationTimeoutMs(const QStringList &arguments, const QString &purpose) const
{
    const QString joined = arguments.join(QLatin1Char(' '));
    if (joined.contains(QStringLiteral("journalctl")) || joined.contains(QStringLiteral("memguardd.service")))
        return 8000;
    if (joined.contains(QStringLiteral("openkylin-status")) && joined.contains(QStringLiteral("--fast")))
        return 10000;
    if (joined.contains(QStringLiteral("feedback-home")))
        return 15000;
    if (joined.contains(QStringLiteral("verify-audit")))
        return 20000;
    if (joined.contains(QStringLiteral("sdk-status")) || joined.contains(QStringLiteral("--probe-sdk")) || joined.contains(QStringLiteral("diagnostics")) || joined.contains(QStringLiteral("collect-openkylin-evidence")))
        return 120000;
    if (joined.contains(QStringLiteral("native-action")))
        return 90000;
    if (joined.contains(QStringLiteral(" demo")) || joined == QStringLiteral("demo"))
        return 60000;
    return purpose == QStringLiteral("details") ? 20000 : 60000;
}

void MemGuardUkccPlugin::showDetailsOutput(const QString &text, const QString &summary)
{
    if (tabs_)
        tabs_->setCurrentIndex(diagnosticsTabIndex_);
    if (detailsToggle_)
        detailsToggle_->setChecked(true);
    if (details_)
        details_->setPlainText(formatMaintenanceResult(text, summary));
    updateOperationGuide(QStringLiteral("已切换到【系统维护】>【维护结果】，可查看处理结果。"), false);
}

void MemGuardUkccPlugin::renderFeedbackSummary(const QString &text)
{
    if (!behaviorSummary_)
        return;
    const QJsonObject payload = parsedObject(text);
    if (payload.isEmpty()) {
        behaviorSummary_->setText(QStringLiteral("最近变化暂时无法读取。可以打开本地控制台查看记忆详情。"));
        return;
    }
    QStringList lines;
    const QJsonArray memories = payload.value(QStringLiteral("recent_memories")).toArray();
    if (memories.isEmpty()) {
        lines << QStringLiteral("最近没有新增可展示的记忆。");
    } else {
        lines << QStringLiteral("最近学到：%1 条").arg(memories.size());
        for (int index = 0; index < memories.size() && index < 2; ++index) {
            const QJsonObject item = memories.at(index).toObject();
            lines << QStringLiteral("• %1").arg(item.value(QStringLiteral("summary_for_user")).toString(QStringLiteral("已形成一条受控记忆。")));
        }
    }
    const QJsonArray preferences = payload.value(QStringLiteral("current_preferences")).toArray();
    lines << QStringLiteral("当前生效偏好：%1 条").arg(preferences.size());
    const QJsonArray sensitive = payload.value(QStringLiteral("recent_sensitive_blocks")).toArray();
    if (!sensitive.isEmpty())
        lines << QStringLiteral("最近已保护敏感内容：%1 条，可在通知中心查看。 ").arg(sensitive.size());
    const QJsonArray forgetting = payload.value(QStringLiteral("recent_forgetting")).toArray();
    if (!forgetting.isEmpty())
        lines << QStringLiteral("最近完成遗忘：%1 次，可查看验证结果。 ").arg(forgetting.size());
    behaviorSummary_->setText(lines.join(QLatin1Char('\n')));

    if (recentMemoryButton_)
        recentMemoryButton_->setText(QStringLiteral("最近新增记忆 · %1").arg(memories.size()));
    if (recentPreferenceButton_)
        recentPreferenceButton_->setText(QStringLiteral("最近生效偏好 · %1").arg(preferences.size()));
    if (recentSensitiveButton_)
        recentSensitiveButton_->setText(QStringLiteral("最近已保护内容 · %1").arg(sensitive.size()));
    if (recentForgetButton_)
        recentForgetButton_->setText(QStringLiteral("最近遗忘验证 · %1").arg(forgetting.size()));

    if (!behaviorTimeline_)
        return;
    behaviorTimeline_->clear();
    const QJsonArray notifications = payload.value(QStringLiteral("notifications")).toArray();
    for (int index = 0; index < notifications.size() && index < 6; ++index) {
        const QJsonObject item = notifications.at(index).toObject();
        const QString level = item.value(QStringLiteral("level")).toString();
        const QString status = level == QStringLiteral("critical")
            ? QStringLiteral("需要处理")
            : (level == QStringLiteral("warning") ? QStringLiteral("需要确认") : QStringLiteral("已完成"));
        auto *row = new QTreeWidgetItem(behaviorTimeline_);
        row->setText(0, item.value(QStringLiteral("title")).toString(QStringLiteral("记忆变化")));
        row->setText(1, item.value(QStringLiteral("summary")).toString(QStringLiteral("状态已经更新。")));
        row->setText(2, status);
        row->setIcon(2, levelIcon(level == QStringLiteral("critical") ? QStringLiteral("red") : (level == QStringLiteral("warning") ? QStringLiteral("yellow") : QStringLiteral("green"))));
    }
    if (behaviorTimeline_->topLevelItemCount() == 0) {
        auto *empty = new QTreeWidgetItem(behaviorTimeline_);
        empty->setText(0, QStringLiteral("暂无事件"));
        empty->setText(1, QStringLiteral("完成一次记忆写入、Agent 任务或遗忘后，这里会显示结果。"));
        empty->setText(2, QStringLiteral("正常"));
        empty->setIcon(2, levelIcon(QStringLiteral("green")));
    }
}

void MemGuardUkccPlugin::updateOperationGuide(const QString &message, bool busy)
{
    if (!operationGuide_)
        return;
    if (busy) {
        operationGuide_->setText(QStringLiteral("处理进度：%1\n结果位置：状态会更新在本页；日志、操作记录、本机智能检索和报告结果会进入【系统维护】>【维护结果】。可点击【停止当前操作】取消。")
            .arg(message));
        return;
    }
    if (message.isEmpty()) {
        operationGuide_->setText(QStringLiteral("处理进度：暂无后台操作。\n结果位置：状态显示在本页；日志、操作记录、本机智能检索和报告结果会显示在【系统维护】>【维护结果】或保存为文件。"));
        return;
    }
    operationGuide_->setText(QStringLiteral("处理进度：暂无后台操作。\n结果位置：%1").arg(message));
}

void MemGuardUkccPlugin::renderStatus(const QJsonObject &payload)
{
    const QJsonObject summary = payload.value(QStringLiteral("summary")).toObject();
    const QString overall = summary.value(QStringLiteral("overall")).toString(QStringLiteral("red"));
    summaryIcon_->setPixmap(levelIcon(overall).pixmap(32, 32));
    summaryTitle_->setText(overall == QStringLiteral("green")
        ? QStringLiteral("本机记忆安全状态正常")
        : (overall == QStringLiteral("yellow") ? QStringLiteral("有项目建议确认") : QStringLiteral("有项目需要处理")));
    summaryDescription_->setText(QStringLiteral("正常 %1 项，建议确认 %2 项，不可用 %3 项。")
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
        const QString userMessage = component.value(QStringLiteral("user_message")).toString();
        const QString nextAction = component.value(QStringLiteral("next_action")).toString();
        item->setText(2, userMessage.isEmpty()
            ? (component.value(QStringLiteral("ready")).toBool() ? QStringLiteral("已就绪") : QStringLiteral("查看诊断建议"))
            : (nextAction.isEmpty() ? userMessage : QStringLiteral("%1；%2").arg(userMessage, nextAction)));
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
        ? QStringLiteral("记忆服务正在运行，并由系统服务管理。")
        : QStringLiteral("服务当前未运行。启动、停止和重启操作会显示系统授权窗口。"));
    const QJsonObject embedding = payload.value(QStringLiteral("sdk")).toObject().value(QStringLiteral("embedding")).toObject();
    const bool embeddingAvailable = embedding.value(QStringLiteral("available")).toBool();
    const QJsonValue inferenceValue = embedding.value(QStringLiteral("inference_ready"));
    const bool inferenceKnown = !inferenceValue.isUndefined() && !inferenceValue.isNull();
    const bool inferenceReady = inferenceKnown && inferenceValue.toBool();
    const QString capabilityState = embedding.value(QStringLiteral("capability_state")).toString();
    const QString userMessage = embedding.value(QStringLiteral("user_message")).toString();
    const QString nextAction = embedding.value(QStringLiteral("next_action")).toString();
    const QJsonObject productState = embedding.value(QStringLiteral("product_state")).toObject();
    const bool installed_unwarmed = capabilityState == QStringLiteral("installed_unwarmed");
    const bool recently_ready = capabilityState == QStringLiteral("recently_ready");
    const bool installedNotProbed = capabilityState == QStringLiteral("installed_not_probed");
    const bool sdkUsable = installed_unwarmed || recently_ready || inferenceReady || installedNotProbed;
    const QString sdkLevel = sdkUsable
        ? QStringLiteral("green")
        : (embeddingAvailable ? QStringLiteral("yellow") : QStringLiteral("red"));
    const QString sdkLabel = userMessage.isEmpty()
        ? (inferenceReady ? QStringLiteral("已准备好") : (embeddingAvailable ? QStringLiteral("按需检查") : QStringLiteral("未检测到")))
        : userMessage;
    setStatusBadge(
        sdkStatusIcon_,
        sdkStatusLabel_,
        sdkLevel,
        sdkLabel);
    QString sdkHumanState;
    if (inferenceReady || recently_ready) {
        sdkHumanState = QStringLiteral("本机智能检索最近验证可用，可作为检索增强。");
    } else if (installed_unwarmed || installedNotProbed) {
        sdkHumanState = QStringLiteral("本机 AI 能力已安装，当前未提前准备；这不是故障，首次使用可能稍慢。");
    } else if (embeddingAvailable) {
        sdkHumanState = QStringLiteral("已检测到本机 AI 能力，但需要进一步检查模型或运行环境。");
    } else {
        sdkHumanState = QStringLiteral("未检测到本机 AI 能力，系统会继续使用备用检索，不影响日常记忆检索。");
    }
    QString warmupLine = QStringLiteral("最近一次提前准备：未记录。");
    if (productState.value(QStringLiteral("last_probe_success")).toBool()) {
        warmupLine = QStringLiteral("最近一次提前准备：已成功记录。");
    } else if (!productState.value(QStringLiteral("last_failure_reason")).toString().isEmpty()) {
        warmupLine = QStringLiteral("最近一次提前准备：未通过；%1").arg(productState.value(QStringLiteral("last_failure_reason")).toString());
    }
    sdkState_->setText(QStringLiteral("%1\n模型：%2\n%3\n建议：%4")
        .arg(sdkHumanState)
        .arg(embedding.value(QStringLiteral("model_info")).toObject().value(QStringLiteral("name")).toString(QStringLiteral("未识别")))
        .arg(warmupLine)
        .arg(nextAction.isEmpty() ? QStringLiteral("日常使用会自动保持稳定检索；需要本机检索增强时点击【检查本机智能检索】或【预热本机智能检索】。") : nextAction));
    const QJsonObject security = payload.value(QStringLiteral("security")).toObject();
    const bool auditReady = security.value(QStringLiteral("auditd")).toObject().value(QStringLiteral("rules_tool_available")).toBool();
    setStatusBadge(
        auditStatusIcon_,
        auditStatusLabel_,
        auditReady ? QStringLiteral("green") : QStringLiteral("red"),
        auditReady ? QStringLiteral("操作记录工具可用") : QStringLiteral("需要安装操作记录工具"));
    auditState_->setText(QStringLiteral("操作记录工具：%1；系统授权策略：%2；内核保护：可选安全验证路径")
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
        actionText = QStringLiteral("检查本机智能检索");
        actionIcon = QStringLiteral("system-search");
    } else if (containsAny(text, {QStringLiteral("audit"), QStringLiteral("审计")})) {
        actionText = QStringLiteral("校验操作记录");
        actionIcon = QStringLiteral("security-high");
    } else if (containsAny(text, {QStringLiteral("Polkit"), QStringLiteral("授权"), QStringLiteral("权限")})) {
        actionText = QStringLiteral("查看系统授权");
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

    auto *message = new QLabel(friendlyRecommendationText(text));
    message->setWordWrap(true);
    message->setAccessibleName(QStringLiteral("处理建议内容"));

    auto *action = new QPushButton(themedIcon(actionIcon, QStyle::SP_ArrowForward), actionText);
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
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【管理员】>【服务控制】，可启动、重启或查看服务日志。"));
        return;
    }

    if (containsAny(text, {QStringLiteral("SDK"), QStringLiteral("Kylin"), QStringLiteral("embedding"), QStringLiteral("vector"), QStringLiteral("向量")})) {
        if (tabs_)
            tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已切换到【管理员】并开始【检查本机智能检索】；完成后结果会显示在【系统维护】>【维护结果】。"));
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
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【管理员】>【系统授权】，请检查授权策略和受控维护工具。"));
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
        appendHistory(QStringLiteral("处理建议"), true, QStringLiteral("已定位到【日常使用】>【资源快照】，可查看记忆服务的内存和 CPU 限制。"));
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

void MemGuardUkccPlugin::applyNavigationTarget(const QString &target)
{
    if (target.isEmpty() || !tabs_)
        return;
    if (target == QStringLiteral("sdk")) {
        tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("托盘跳转"), true, QStringLiteral("已定位到【管理员】>【本机智能检索】，可检查或预热本机智能检索。"));
        return;
    }
    if (target == QStringLiteral("audit")) {
        tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("托盘跳转"), true, QStringLiteral("已定位到【管理员】>【安全审计】，可校验操作记录或查看记录规则。"));
        return;
    }
    if (target == QStringLiteral("service")) {
        tabs_->setCurrentIndex(serviceTabIndex_);
        appendHistory(QStringLiteral("托盘跳转"), true, QStringLiteral("已定位到【管理员】>【服务控制】，可查看服务状态和日志。"));
        return;
    }
    if (target == QStringLiteral("resources")) {
        tabs_->setCurrentIndex(overviewTabIndex_);
        appendHistory(QStringLiteral("托盘跳转"), true, QStringLiteral("已定位到【日常使用】>【资源快照】，可查看服务资源占用。"));
        return;
    }
    if (target == QStringLiteral("sensitive")) {
        tabs_->setCurrentIndex(diagnosticsTabIndex_);
        if (detailsToggle_)
            detailsToggle_->setChecked(true);
        if (details_)
            details_->setPlainText(QStringLiteral("最近受保护的敏感内容和文件扫描结果可在本地控制台的操作记录页查看；也可以在文件管理器中重新选择文件并查看扫描报告。"));
        appendHistory(QStringLiteral("托盘跳转"), true, QStringLiteral("已定位到【系统维护】，可打开本地控制台查看已保护内容。"));
        return;
    }
    tabs_->setCurrentIndex(overviewTabIndex_);
}

void MemGuardUkccPlugin::renderResourceSnapshot(const QJsonObject &resources)
{
    if (!resourceSnapshot_)
        return;
    resourceSnapshot_->clear();
    const QList<QPair<QString, QString>> rows = {
        {QStringLiteral("服务资源组"), formatResourceValue(QStringLiteral("control_group"), resources)},
        {QStringLiteral("当前内存"), formatResourceValue(QStringLiteral("memory_current"), resources)},
        {QStringLiteral("内存上限"), formatResourceValue(QStringLiteral("memory_max"), resources)},
        {QStringLiteral("CPU 配额"), formatResourceValue(QStringLiteral("cpu_quota"), resources)},
        {QStringLiteral("CPU 使用"), formatResourceValue(QStringLiteral("cpu_usage"), resources)},
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
        label->setText(QStringLiteral("%1：%2").arg(levelText(level), text));
        label->setAccessibleDescription(QStringLiteral("%1，%2").arg(levelText(level), text));
    }
}

void MemGuardUkccPlugin::appendHistory(const QString &operation, bool ok, const QString &message)
{
    if (resultSummary_)
        resultSummary_->setText(QStringLiteral("操作结果：%1，%2。").arg(operation, ok ? QStringLiteral("已完成") : QStringLiteral("需要处理")));
    if (!operationHistory_)
        return;
    auto *item = new QTreeWidgetItem(operationHistory_);
    item->setIcon(1, ok ? levelIcon(QStringLiteral("green")) : levelIcon(QStringLiteral("red")));
    item->setText(0, operation);
    item->setText(1, ok ? QStringLiteral("完成") : QStringLiteral("失败"));
    item->setText(2, message.simplified().left(280));
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
    openDashboardTarget(QString());
}

void MemGuardUkccPlugin::openDashboardTarget(const QString &fragment)
{
    const QString nameSpace = QStringLiteral("default");
    const DashboardRuntimePaths runtimePaths = dashboardRuntimePaths(nameSpace);
    QString authToken;
    QString preparationError;
    if (!ensureDashboardAuthToken(runtimePaths, &authToken, &preparationError)) {
        showError(
            QStringLiteral("本地控制台启动失败"),
            QStringLiteral("%1 请重新登录后重试；仍失败时请导出系统报告并检查用户运行时目录。")
                .arg(preparationError));
        return;
    }

    QString program = QFileInfo::exists(QStringLiteral("/usr/bin/memctl")) ? QStringLiteral("/usr/bin/memctl") : QStringLiteral("python3");
    QStringList arguments = dashboardProcessArguments(runtimePaths, nameSpace);
    if (program == QStringLiteral("python3")) {
        arguments.prepend(QStringLiteral("memguard.cli.memctl"));
        arguments.prepend(QStringLiteral("-m"));
    }
    qint64 launchedPid = 0;
    const bool started = QProcess::startDetached(
        program,
        arguments,
        QString(),
        &launchedPid);
    if (!started) {
        showError(
            QStringLiteral("本地控制台启动失败"),
            QStringLiteral("请确认 memctl 已安装且可以运行，然后重新打开；仍失败时请导出系统报告检查安装状态。"));
        return;
    }
    openDashboardWhenReady(this, runtimePaths, nameSpace, authToken, fragment, launchedPid, [this]() {
        showError(
            QStringLiteral("本地控制台暂时无法打开"),
            QStringLiteral("控制台服务未通过身份校验或启动进程已退出。请重新打开；仍失败时请导出系统报告检查安装与用户运行时目录。"));
    });
}

void MemGuardUkccPlugin::runUserJourney()
{
    QString program = QFileInfo::exists(QStringLiteral("/usr/bin/memguard")) ? QStringLiteral("/usr/bin/memguard") : QStringLiteral("python3");
    QStringList arguments;
    if (program == QStringLiteral("python3"))
        arguments << QStringLiteral("-m") << QStringLiteral("memguard.cli.memguard");
    arguments << QStringLiteral("demo");
    runProgram(program, arguments, QStringLiteral("demo"));
}

void MemGuardUkccPlugin::setBusy(bool busy, const QString &message, bool lockActions)
{
    if (!message.isEmpty())
        summaryDescription_->setText(message);
    updateOperationGuide(message, busy);
    if (cancelOperationButton_) {
        cancelOperationButton_->setVisible(busy);
        cancelOperationButton_->setEnabled(busy);
    }
    for (QPushButton *button : actionButtons_)
        button->setEnabled(!(busy && lockActions));
}

void MemGuardUkccPlugin::showError(const QString &title, const QString &message)
{
    QMessageBox::critical(page_, title, message.left(1600));
}

