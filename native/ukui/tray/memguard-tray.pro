QT += widgets
TEMPLATE = app
CONFIG += c++17
TARGET = memguardos-tray

SOURCES += memguard_tray.cpp

target.path = /usr/bin
INSTALLS += target
