#include "memguard_peony_plugin.h"

#include <QAction>
#include <QAbstractItemView>
#include <QCheckBox>
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
        *error = QStringLiteral("命令没有返回可识别的 JSON 结果。");
        return {};
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(raw.mid(first, last - first + 1), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        *error = QStringLiteral("结果解析失败：%1").arg(parseError.errorString());
        return {};
    }
    return document.object();
}

QString actionLabel(const QString &action)
{
    if (action == QStringLiteral("import_text"))
        return QStringLiteral("可以导入");
    if (action == QStringLiteral("block_sensitive"))
        return QStringLiteral("敏感内容，已阻断");
    if (action == QStringLiteral("missing_path"))
        return QStringLiteral("文件不存在");
    return QStringLiteral("仅记录，不导入");
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
    fallbackUri_ = uri;
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
    if (uris.isEmpty() && !fallbackUri_.isEmpty())
        uris << fallbackUri_;
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
    auto *description = new QLabel(QStringLiteral("选择要写入 Agent 记忆的安全文本。敏感、超限或非文本内容不会被导入。"));
    description->setWordWrap(true);
    layout->addWidget(description);

    const QJsonArray items = preview.value(QStringLiteral("items")).toArray();
    auto *table = new QTableWidget(items.size(), 6, &dialog);
    table->setHorizontalHeaderLabels({QStringLiteral("选择"), QStringLiteral("文件"), QStringLiteral("大小"), QStringLiteral("风险"), QStringLiteral("处理建议"), QStringLiteral("内容预览")});
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
    table->horizontalHeader()->setSectionResizeMode(5, QHeaderView::Stretch);

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
        auto *content = new QTableWidgetItem(item.value(QStringLiteral("preview")).toString());
        content->setToolTip(item.value(QStringLiteral("preview")).toString());
        table->setItem(row, 5, content);
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
    auto *progress = new QProgressDialog(QStringLiteral("正在导入所选内容并写入审计记录…"), QString(), 0, 0);
    progress->setWindowTitle(QStringLiteral("MemGuardOS"));
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
                QMessageBox::critical(nullptr, QStringLiteral("导入失败"), error);
            } else {
                const QJsonObject summary = result.value(QStringLiteral("summary")).toObject();
                const QString message = QStringLiteral("已导入 %1 个，阻断 %2 个，跳过 %3 个，失败 %4 个。\n审计链：%5")
                    .arg(summary.value(QStringLiteral("stored")).toInt())
                    .arg(summary.value(QStringLiteral("blocked")).toInt())
                    .arg(summary.value(QStringLiteral("skipped")).toInt())
                    .arg(summary.value(QStringLiteral("failed")).toInt())
                    .arg(result.value(QStringLiteral("audit_chain")).toObject().value(QStringLiteral("ok")).toBool() ? QStringLiteral("完整") : QStringLiteral("需要检查"));
                if (result.value(QStringLiteral("ok")).toBool())
                    QMessageBox::information(nullptr, QStringLiteral("导入完成"), message);
                else
                    QMessageBox::warning(nullptr, QStringLiteral("部分内容未导入"), message);
            }
            progress->deleteLater();
            process->deleteLater();
        });
    process->start(command.program, command.arguments);
    progress->show();
}
