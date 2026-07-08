#include "memguard_peony_plugin.h"

#include <QAction>
#include <QAbstractItemView>
#include <QCheckBox>
#include <QDesktopServices>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFile>
#include <QFileInfo>
#include <QHeaderView>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMessageBox>
#include <QProcess>
#include <QProgressDialog>
#include <QPushButton>
#include <QTableWidget>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>
#include <algorithm>

namespace {

struct Command {
    QString program;
    QStringList arguments;
};

Command memctlCommand(const QStringList &arguments)
{
    if (QFile::exists(QStringLiteral("/usr/bin/memctl")))
        return {QStringLiteral("/usr/bin/memctl"), arguments};
    QStringList pythonArguments;
    pythonArguments << QStringLiteral("-m") << QStringLiteral("memguard.cli.memctl") << arguments;
    return {QStringLiteral("python3"), pythonArguments};
}

QJsonObject parseJsonOutput(const QByteArray &raw, QString *error)
{
    const int first = raw.indexOf('{');
    const int last = raw.lastIndexOf('}');
    if (first < 0 || last < first) {
        *error = QStringLiteral("没有收到有效的检查结果，请稍后重试；如仍失败，请在记忆安全中心导出诊断报告。");
        return {};
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(raw.mid(first, last - first + 1), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        *error = QStringLiteral("检查结果暂时无法读取，请重新检查或在记忆安全中心导出诊断报告。");
        return {};
    }
    return document.object();
}

QString actionLabel(const QString &action)
{
    if (action == QStringLiteral("import_text"))
        return QStringLiteral("可导入");
    if (action == QStringLiteral("block_sensitive"))
        return QStringLiteral("已保护");
    if (action == QStringLiteral("missing_path"))
        return QStringLiteral("文件不存在");
    return QStringLiteral("只记录");
}

QString candidateReason(const QJsonObject &item)
{
    const QString action = item.value(QStringLiteral("recommended_action")).toString();
    const QString level = item.value(QStringLiteral("sensitivity")).toObject().value(QStringLiteral("level")).toString();
    if (action == QStringLiteral("block_sensitive") || level == QStringLiteral("high"))
        return QStringLiteral("敏感风险");
    if (action == QStringLiteral("import_text"))
        return QStringLiteral("可保存内容，适合形成偏好或知识记忆");
    if (action == QStringLiteral("audit_only"))
        return QStringLiteral("只记录选择，不保存内容");
    if (action == QStringLiteral("missing_path"))
        return QStringLiteral("路径不可用");
    return QStringLiteral("不适合导入记忆");
}

QString riskLabel(const QJsonObject &item)
{
    const QString level = item.value(QStringLiteral("sensitivity")).toObject().value(QStringLiteral("level")).toString();
    if (level == QStringLiteral("high"))
        return QStringLiteral("高风险");
    if (level == QStringLiteral("medium"))
        return QStringLiteral("需要注意");
    return QStringLiteral("未发现高风险");
}

QString humanSize(qint64 bytes)
{
    if (bytes < 1024)
        return QStringLiteral("%1 B").arg(bytes);
    if (bytes < 1024 * 1024)
        return QStringLiteral("%1 KB").arg(QString::number(bytes / 1024.0, 'f', 1));
    return QStringLiteral("%1 MB").arg(QString::number(bytes / (1024.0 * 1024.0), 'f', 1));
}

void openLocalConsole(const QString &fragment = QString())
{
    const Command command = memctlCommand({QStringLiteral("web"), QStringLiteral("--host"), QStringLiteral("127.0.0.1"), QStringLiteral("--port"), QStringLiteral("8765")});
    QProcess::startDetached(command.program, command.arguments);
    QTimer::singleShot(800, [fragment]() {
        const QString suffix = fragment.isEmpty() ? QString() : fragment;
        QDesktopServices::openUrl(QUrl(QStringLiteral("http://127.0.0.1:8765%1").arg(suffix)));
    });
}

} // namespace

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
    return QStringLiteral("MemGuardOS 记忆内容检查");
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

QList<QAction *> MemGuardPeonyPlugin::menuActions(
    Peony::MenuPluginInterface::Types types,
    const QString &uri,
    const QStringList &selectionUris)
{
    QList<QAction *> actions;
    const bool supportedMenu = types.testFlag(Peony::MenuPluginInterface::DirectoryView)
        || types.testFlag(Peony::MenuPluginInterface::DesktopWindow) || !selectionUris.isEmpty();
    if (!enabled_ || !supportedMenu)
        return actions;

    lastSelection_ = selectionUris;
    primaryUri_ = uri;
    auto *action = new QAction(
        QIcon::fromTheme(QStringLiteral("security-high")),
        QStringLiteral("使用 MemGuardOS 检查记忆内容"));
    action->setObjectName(QStringLiteral("memguardos-peony-memory-scan"));
    action->setToolTip(QStringLiteral("预览文件风险，并在确认后导入安全内容"));
    QObject::connect(action, &QAction::triggered, this, &MemGuardPeonyPlugin::sendSelectionToMemGuard);
    actions << action;
    return actions;
}

void MemGuardPeonyPlugin::sendSelectionToMemGuard()
{
    QStringList uris = lastSelection_;
    if (uris.isEmpty() && !primaryUri_.isEmpty())
        uris << primaryUri_;
    QStringList paths;
    for (const QString &uri : uris) {
        const QUrl url(uri);
        const QString path = url.isLocalFile() ? url.toLocalFile() : uri;
        if (!path.isEmpty() && !paths.contains(path))
            paths << path;
    }
    if (paths.isEmpty()) {
        QMessageBox::information(nullptr, QStringLiteral("MemGuardOS"), QStringLiteral("没有可检查的本地文件。"));
        return;
    }
    if (paths.size() > 100) {
        QMessageBox::warning(nullptr, QStringLiteral("MemGuardOS"), QStringLiteral("一次最多检查 100 个文件，请减少选择数量。"));
        return;
    }

    QStringList args{QStringLiteral("desktop"), QStringLiteral("scan-files")};
    for (const QString &path : paths)
        args << QStringLiteral("--file") << path;
    runPreview(args);
}

void MemGuardPeonyPlugin::runPreview(const QStringList &arguments)
{
    const Command command = memctlCommand(arguments);
    auto *progress = new QProgressDialog(
        QStringLiteral("正在检查文件类型和敏感内容…"),
        QStringLiteral("取消"),
        0,
        0);
    progress->setWindowTitle(QStringLiteral("MemGuardOS 记忆内容检查"));
    progress->setWindowModality(Qt::ApplicationModal);
    progress->setMinimumDuration(0);
    progress->setAutoClose(false);
    progress->setAccessibleName(QStringLiteral("文件检查进度"));

    auto *process = new QProcess(this);
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(progress, &QProgressDialog::canceled, process, &QProcess::kill);
    connect(process, &QProcess::errorOccurred, this, [progress, process](QProcess::ProcessError) {
        progress->close();
        QMessageBox::critical(nullptr, QStringLiteral("MemGuardOS"), QStringLiteral("无法启动文件检查：%1").arg(process->errorString()));
        progress->deleteLater();
        process->deleteLater();
    });
    connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
        [this, progress, process](int exitCode, QProcess::ExitStatus) {
            progress->close();
            if (process->property("handled").toBool())
                return;
            process->setProperty("handled", true);
            const QByteArray output = process->readAll();
            QString error;
            const QJsonObject preview = parseJsonOutput(output, &error);
            if (exitCode != 0 || preview.isEmpty() || !preview.value(QStringLiteral("ok")).toBool()) {
                const QString message = !error.isEmpty() ? error : preview.value(QStringLiteral("message")).toString(QString::fromLocal8Bit(output));
                QMessageBox::critical(nullptr, QStringLiteral("文件检查失败"), message.left(1200));
            } else {
                showPreviewDialog(preview);
            }
            progress->deleteLater();
            process->deleteLater();
        });
    QTimer::singleShot(120000, process, [process, progress]() {
        if (process->state() == QProcess::NotRunning)
            return;
        process->setProperty("handled", true);
        process->kill();
        progress->close();
        QMessageBox::critical(nullptr, QStringLiteral("文件检查超时"), QStringLiteral("检查超过 120 秒，请减少文件数量后重试。"));
    });
    process->start(command.program, command.arguments);
    progress->show();
}

void MemGuardPeonyPlugin::showPreviewDialog(const QJsonObject &preview)
{
    QDialog dialog;
    dialog.setWindowTitle(QStringLiteral("检查记忆内容"));
    dialog.setWindowIcon(QIcon::fromTheme(QStringLiteral("security-high")));
    dialog.resize(880, 480);
    dialog.setAccessibleName(QStringLiteral("MemGuardOS 文件检查结果"));
    auto *layout = new QVBoxLayout(&dialog);
    const QJsonArray items = preview.value(QStringLiteral("items")).toArray();
    int importable = 0;
    int blocked = 0;
    int auditOnly = 0;
    for (const QJsonValue &value : items) {
        const QString action = value.toObject().value(QStringLiteral("recommended_action")).toString();
        if (action == QStringLiteral("import_text"))
            ++importable;
        else if (action == QStringLiteral("block_sensitive"))
            ++blocked;
        else
            ++auditOnly;
    }
    auto *description = new QLabel(QStringLiteral("已检查 %1 个项目：可保存 %2 个，已保护 %3 个，只记录 %4 个。选择要保存给 Agent 使用的安全文本；敏感、超限或非文本内容不会被保存。")
            .arg(items.size())
            .arg(importable)
            .arg(blocked)
            .arg(auditOnly));
    description->setWordWrap(true);
    layout->addWidget(description);

    auto *table = new QTableWidget(items.size(), 7, &dialog);
    table->setHorizontalHeaderLabels({QStringLiteral("选择"), QStringLiteral("文件"), QStringLiteral("大小"), QStringLiteral("风险"), QStringLiteral("系统建议"), QStringLiteral("原因"), QStringLiteral("内容预览")});
    table->verticalHeader()->setVisible(false);
    table->setSelectionBehavior(QAbstractItemView::SelectRows);
    table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table->setAlternatingRowColors(true);
    table->setAccessibleName(QStringLiteral("文件风险检查列表"));
    table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(3, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(4, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(5, QHeaderView::ResizeToContents);
    table->horizontalHeader()->setSectionResizeMode(6, QHeaderView::Stretch);

    QList<QCheckBox *> checkBoxes;
    for (int row = 0; row < items.size(); ++row) {
        const QJsonObject item = items.at(row).toObject();
        const QString path = item.value(QStringLiteral("path")).toString();
        const bool selectable = item.value(QStringLiteral("selectable")).toBool();
        auto *check = new QCheckBox;
        check->setEnabled(selectable);
        check->setChecked(false);
        check->setProperty("path", path);
        check->setAccessibleName(QStringLiteral("选择 %1").arg(QFileInfo(path).fileName()));
        table->setCellWidget(row, 0, check);
        checkBoxes << check;
        auto *name = new QTableWidgetItem(QFileInfo(path).fileName());
        name->setToolTip(path);
        table->setItem(row, 1, name);
        table->setItem(row, 2, new QTableWidgetItem(humanSize(item.value(QStringLiteral("size_bytes")).toVariant().toLongLong())));
        table->setItem(row, 3, new QTableWidgetItem(riskLabel(item)));
        table->setItem(row, 4, new QTableWidgetItem(actionLabel(item.value(QStringLiteral("recommended_action")).toString())));
        table->setItem(row, 5, new QTableWidgetItem(candidateReason(item)));
        auto *content = new QTableWidgetItem(item.value(QStringLiteral("preview")).toString());
        content->setToolTip(item.value(QStringLiteral("preview")).toString());
        table->setItem(row, 6, content);
    }
    layout->addWidget(table, 1);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Cancel, &dialog);
    auto *apply = buttons->addButton(QStringLiteral("导入所选文件"), QDialogButtonBox::AcceptRole);
    apply->setIcon(QIcon::fromTheme(QStringLiteral("document-import")));
    apply->setEnabled(false);
    apply->setAccessibleName(QStringLiteral("导入所选安全文件"));
    auto updateApply = [checkBoxes, apply]() {
        apply->setEnabled(std::any_of(checkBoxes.cbegin(), checkBoxes.cend(), [](QCheckBox *box) { return box->isEnabled() && box->isChecked(); }));
    };
    for (QCheckBox *box : checkBoxes)
        connect(box, &QCheckBox::toggled, &dialog, updateApply);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    connect(apply, &QPushButton::clicked, &dialog, &QDialog::accept);
    layout->addWidget(buttons);

    if (dialog.exec() != QDialog::Accepted)
        return;
    QStringList selected;
    for (QCheckBox *box : checkBoxes) {
        if (box->isEnabled() && box->isChecked())
            selected << box->property("path").toString();
    }
    QStringList args{QStringLiteral("desktop"), QStringLiteral("apply-scan"), QStringLiteral("--manifest"), preview.value(QStringLiteral("manifest_path")).toString()};
    for (const QString &path : selected)
        args << QStringLiteral("--select") << path;
    args << QStringLiteral("--notify");
    runApply(args);
}

void MemGuardPeonyPlugin::runApply(const QStringList &arguments)
{
    const Command command = memctlCommand(arguments);
    auto *process = new QProcess(this);
    auto *progress = new QProgressDialog(QStringLiteral("正在保存所选内容并写入操作记录…"), QString(), 0, 0);
    progress->setWindowTitle(QStringLiteral("MemGuardOS"));
    progress->setWindowModality(Qt::ApplicationModal);
    progress->setMinimumDuration(0);
    progress->setCancelButton(nullptr);
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
        [this, progress, process](int, QProcess::ExitStatus) {
            progress->close();
            QString error;
            const QJsonObject result = parseJsonOutput(process->readAll(), &error);
            if (result.isEmpty()) {
                QMessageBox::critical(nullptr, QStringLiteral("导入失败"), error);
            } else {
                const QJsonObject summary = result.value(QStringLiteral("summary")).toObject();
                const QString message = QStringLiteral("已保存 %1 个安全文件；%2 个敏感或不适合保存的项目已被保护性跳过；%3 个未选择；%4 个未完成。\n操作记录：%5")
                    .arg(summary.value(QStringLiteral("stored")).toInt())
                    .arg(summary.value(QStringLiteral("blocked")).toInt())
                    .arg(summary.value(QStringLiteral("skipped")).toInt())
                    .arg(summary.value(QStringLiteral("failed")).toInt())
                    .arg(result.value(QStringLiteral("audit_chain")).toObject().value(QStringLiteral("ok")).toBool() ? QStringLiteral("完整") : QStringLiteral("需要检查"));
                QStringList storedIds;
                const QJsonArray results = result.value(QStringLiteral("results")).toArray();
                for (const QJsonValue &value : results) {
                    const QString memoryId = value.toObject().value(QStringLiteral("ingest")).toObject().value(QStringLiteral("memory_id")).toString();
                    if (!memoryId.isEmpty())
                        storedIds << memoryId;
                }
                QMessageBox box(result.value(QStringLiteral("ok")).toBool() ? QMessageBox::Information : QMessageBox::Warning,
                    result.value(QStringLiteral("ok")).toBool() ? QStringLiteral("导入完成") : QStringLiteral("部分内容未导入"),
                    message);
                auto *view = box.addButton(QStringLiteral("查看结果"), QMessageBox::ActionRole);
                auto *forget = box.addButton(QStringLiteral("撤销本次导入"), QMessageBox::ActionRole);
                forget->setEnabled(!storedIds.isEmpty());
                box.addButton(QStringLiteral("关闭"), QMessageBox::RejectRole);
                box.exec();
                if (box.clickedButton() == view)
                    openLocalConsole(QStringLiteral("#audit"));
                else if (box.clickedButton() == forget)
                    runForgetImport(storedIds);
            }
            progress->deleteLater();
            process->deleteLater();
        });
    process->start(command.program, command.arguments);
    progress->show();
}

void MemGuardPeonyPlugin::runForgetImport(const QStringList &memoryIds)
{
    if (memoryIds.isEmpty()) {
        QMessageBox::information(nullptr, QStringLiteral("MemGuardOS"), QStringLiteral("本次没有已导入的记忆可撤销。"));
        return;
    }
    QStringList args{QStringLiteral("desktop"), QStringLiteral("forget-import"), QStringLiteral("--compact"), QStringLiteral("--notify")};
    for (const QString &memoryId : memoryIds)
        args << QStringLiteral("--memory-id") << memoryId;

    const Command command = memctlCommand(args);
    auto *process = new QProcess(this);
    auto *progress = new QProgressDialog(QStringLiteral("正在遗忘本次导入内容…"), QString(), 0, 0);
    progress->setWindowTitle(QStringLiteral("遗忘本次导入内容"));
    progress->setWindowModality(Qt::ApplicationModal);
    progress->setMinimumDuration(0);
    progress->setCancelButton(nullptr);
    process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
        [progress, process](int, QProcess::ExitStatus) {
            progress->close();
            QString error;
            const QJsonObject result = parseJsonOutput(process->readAll(), &error);
            if (result.isEmpty()) {
                QMessageBox::critical(nullptr, QStringLiteral("遗忘失败"), error);
            } else {
                const QJsonObject summary = result.value(QStringLiteral("summary")).toObject();
                const QJsonObject audit = result.value(QStringLiteral("audit_chain")).toObject();
                const bool ok = result.value(QStringLiteral("ok")).toBool();
                const QString message = QStringLiteral("已遗忘 %1 条本次导入记忆；%2 条未找到；%3 条此前已遗忘。\n操作记录：%4")
                    .arg(summary.value(QStringLiteral("forgotten")).toInt())
                    .arg(summary.value(QStringLiteral("missing")).toInt())
                    .arg(summary.value(QStringLiteral("already_deleted")).toInt())
                    .arg(audit.value(QStringLiteral("ok")).toBool() ? QStringLiteral("完整") : QStringLiteral("需要检查"));
                QMessageBox box(ok ? QMessageBox::Information : QMessageBox::Warning,
                    ok ? QStringLiteral("遗忘完成") : QStringLiteral("遗忘结果需要确认"),
                    message);
                auto *view = box.addButton(QStringLiteral("查看操作记录"), QMessageBox::ActionRole);
                box.addButton(QStringLiteral("关闭"), QMessageBox::RejectRole);
                box.exec();
                if (box.clickedButton() == view)
                    openLocalConsole(QStringLiteral("#audit"));
            }
            progress->deleteLater();
            process->deleteLater();
        });
    process->start(command.program, command.arguments);
    progress->show();
}
