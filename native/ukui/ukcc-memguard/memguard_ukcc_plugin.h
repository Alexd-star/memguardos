#pragma once

#include <QList>
#include <QObject>
#include <QStringList>
#include <QWidget>
#include <ukcc/interface/interface.h>

class QLabel;
class QJsonObject;
class QListWidget;
class QProgressBar;
class QPushButton;
class QTextEdit;
class QToolButton;
class QTreeWidget;

class MemGuardUkccPlugin : public QObject, public CommonInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID CommonInterface_iid FILE "ukcc-memguard.json")
    Q_INTERFACES(CommonInterface)

public:
    QString plugini18nName() override;
    int pluginTypes() override;
    QWidget *pluginUi() override;
    bool isShowOnHomePage() const override;
    QIcon icon() const override;
    bool isEnable() const override;
    const QString name() const override;
    void plugin_leave() override;

private:
    QWidget *page_ = nullptr;
    QLabel *summaryIcon_ = nullptr;
    QLabel *summaryTitle_ = nullptr;
    QLabel *summaryDescription_ = nullptr;
    QLabel *serviceState_ = nullptr;
    QLabel *sdkState_ = nullptr;
    QLabel *auditState_ = nullptr;
    QLabel *resultSummary_ = nullptr;
    QLabel *serviceStatusIcon_ = nullptr;
    QLabel *serviceStatusLabel_ = nullptr;
    QLabel *auditStatusIcon_ = nullptr;
    QLabel *auditStatusLabel_ = nullptr;
    QLabel *sdkStatusIcon_ = nullptr;
    QLabel *sdkStatusLabel_ = nullptr;
    QLabel *desktopStatusIcon_ = nullptr;
    QLabel *desktopStatusLabel_ = nullptr;
    QLabel *readinessValueLabel_ = nullptr;
    QProgressBar *readinessBar_ = nullptr;
    QListWidget *recommendations_ = nullptr;
    QTreeWidget *components_ = nullptr;
    QTreeWidget *resourceSnapshot_ = nullptr;
    QTreeWidget *operationHistory_ = nullptr;
    QTextEdit *details_ = nullptr;
    QToolButton *detailsToggle_ = nullptr;
    QList<QPushButton *> actionButtons_;

    QWidget *buildOverviewTab();
    QWidget *buildServiceTab();
    QWidget *buildDiagnosticsTab();
    void refreshStatus();
    void runMemctl(const QStringList &arguments, const QString &purpose);
    void runProgram(const QString &program, const QStringList &arguments, const QString &purpose);
    void renderStatus(const QJsonObject &payload);
    void renderResourceSnapshot(const QJsonObject &resources);
    void setStatusBadge(QLabel *icon, QLabel *label, const QString &level, const QString &text);
    void appendHistory(const QString &operation, bool ok, const QString &message);
    void exportDiagnostics(const QString &text);
    void openDashboard();
    void setBusy(bool busy, const QString &message);
    void showError(const QString &title, const QString &message);
};
