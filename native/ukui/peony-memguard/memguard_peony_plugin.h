#pragma once

#include <QObject>
#include <QAction>
#include <QJsonObject>
#include <QStringList>
#include <peony-qt/menu-plugin-iface.h>

class MemGuardPeonyPlugin : public QObject, public Peony::MenuPluginInterface
{
    Q_OBJECT
    Q_PLUGIN_METADATA(IID MenuPluginInterface_iid FILE "common.json")
    Q_INTERFACES(Peony::MenuPluginInterface)

public:
    Peony::PluginInterface::PluginType pluginType() override;
    const QString name() override;
    const QString description() override;
    const QIcon icon() override;
    void setEnable(bool enable) override;
    bool isEnable() override;
    QString testPlugin() override;
    QList<QAction *> menuActions(Peony::MenuPluginInterface::Types types, const QString &uri, const QStringList &selectionUris) override;

private:
    bool enabled_ = true;
    QStringList lastSelection_;
    QString primaryUri_;
    void sendSelectionToMemGuard();
    void runPreview(const QStringList &arguments);
    void showPreviewDialog(const QJsonObject &preview);
    void runApply(const QStringList &arguments);
    void runForgetImport(const QStringList &memoryIds);
};
