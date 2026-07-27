QT += widgets network
CONFIG += c++17 console link_pkgconfig
CONFIG -= app_bundle
TEMPLATE = app
TARGET = peony-file-scan-smoke
PKGCONFIG += peony
SOURCES += peony_file_scan_smoke.cpp
