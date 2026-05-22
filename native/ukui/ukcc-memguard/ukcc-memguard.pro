QT += widgets
TEMPLATE = lib
CONFIG += plugin c++17
TARGET = memguardos-ukcc-plugin

SOURCES += memguard_ukcc_plugin.cpp
HEADERS += memguard_ukcc_plugin.h
DISTFILES += ukcc-memguard.json

INCLUDEPATH += /usr/include
LIBS += -lukcc

target.path = /usr/lib/x86_64-linux-gnu/ukui-control-center
INSTALLS += target
