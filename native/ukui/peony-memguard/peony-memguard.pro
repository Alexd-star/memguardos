QT += widgets
TEMPLATE = lib
CONFIG += plugin c++17
TARGET = peony-menu-plugin-memguardos

SOURCES += memguard_peony_plugin.cpp
HEADERS += memguard_peony_plugin.h
DISTFILES += common.json

CONFIG += link_pkgconfig
PKGCONFIG += peony

target.path = /usr/lib/x86_64-linux-gnu/peony-extensions
INSTALLS += target
