#pragma once

#include <QList>
#include <QObject>
#include <QWidget>
#include <QTextEdit>
#include <ukcc/interface/interface.h>

class QLabel;
class QPushButton;

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
    QTextEdit *output_ = nullptr;
    QLabel *status_ = nullptr;
    QList<QPushButton *> actionButtons_;
    void runMemctl(const QStringList &args);
    void openDashboard();
    void setBusy(bool busy, const QString &message);
};
