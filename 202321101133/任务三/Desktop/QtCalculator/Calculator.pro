QT       += widgets
CONFIG   += c++17

TARGET   = Calculator
TEMPLATE  = app

# 输出目录
DESTDIR   = $$PWD/bin

SOURCES += \
    main.cpp \
    mainwindow.cpp \
    calculatorengine.cpp

HEADERS += \
    mainwindow.h \
    calculatorengine.h

# Windows 下控制台输出关闭
win32 {
    CONFIG -= console
}
