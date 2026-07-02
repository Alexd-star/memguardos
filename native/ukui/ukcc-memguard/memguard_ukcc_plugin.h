#pragma once

#include <QList>
#include <QObject>
#include <QStringList>
#include <QWidget>
#include <ukcc/interface/interface.h>

class QLabel;
class QJsonObject;
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
    void appendHistory(const QString &operation, bool ok, const QString &message);
    void exportDiagnostics(const QString &text);
    void openDashboard();
    void setBusy(bool busy, const QString &message);
    void showError(const QString &title, const QString &message);
};
