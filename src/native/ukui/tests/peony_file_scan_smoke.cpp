#include <QAction>
#include <QApplication>
#include <QDialog>
#include <QFileInfo>
#include <QPluginLoader>
#include <QTableWidget>
#include <QTimer>
#include <QUrl>

#include <peony-qt/menu-plugin-iface.h>

#include <cstdio>

int main(int argc, char *argv[])
{
    QApplication application(argc, argv);
    if (argc != 3) {
        std::fprintf(stderr, "usage: %s PLUGIN_SO SAMPLE_FILE\n", argv[0]);
        return 2;
    }

    const QString pluginPath = QFileInfo(QString::fromLocal8Bit(argv[1])).absoluteFilePath();
    const QString samplePath = QFileInfo(QString::fromLocal8Bit(argv[2])).absoluteFilePath();
    QPluginLoader loader(pluginPath);
    QObject *pluginObject = loader.instance();
    if (!pluginObject) {
        std::fprintf(stderr, "plugin_load_error=%s\n", loader.errorString().toLocal8Bit().constData());
        return 3;
    }
    auto *plugin = qobject_cast<Peony::MenuPluginInterface *>(pluginObject);
    if (!plugin) {
        std::fprintf(stderr, "plugin_interface_error=true\n");
        return 4;
    }

    const QString uri = QUrl::fromLocalFile(samplePath).toString();
    const QList<QAction *> actions = plugin->menuActions(
        Peony::MenuPluginInterface::DesktopWindow,
        uri,
        QStringList{uri});
    if (actions.isEmpty()) {
        std::fprintf(stderr, "menu_action_missing=true\n");
        return 5;
    }

    bool resultSeen = false;
    QTimer observer;
    QObject::connect(&observer, &QTimer::timeout, &application, [&]() {
        for (QWidget *widget : QApplication::topLevelWidgets()) {
            auto *dialog = qobject_cast<QDialog *>(widget);
            if (!dialog || dialog->windowTitle() != QStringLiteral("检查记忆内容"))
                continue;
            auto *table = dialog->findChild<QTableWidget *>();
            if (!dialog->isVisible() || !table || table->rowCount() < 1) {
                std::fprintf(stderr, "preview_dialog_invalid=true\n");
                application.exit(6);
                return;
            }
            resultSeen = true;
            std::printf("peony_file_scan_dialog=visible\npreview_rows=%d\n", table->rowCount());
            dialog->reject();
            application.quit();
            return;
        }
    });
    observer.start(100);

    QTimer::singleShot(0, actions.first(), &QAction::trigger);
    QTimer::singleShot(20000, &application, [&]() {
        if (!resultSeen) {
            std::fprintf(stderr, "preview_dialog_timeout=true\n");
            application.exit(7);
        }
    });
    return application.exec();
}
