QT += widgets network
TEMPLATE = app
CONFIG += c++17
TARGET = memguardos-tray

SOURCES += memguard_tray.cpp
HEADERS += ../dashboard_launcher.h

target.path = /usr/bin
INSTALLS += target
