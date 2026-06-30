@echo off
set PATH=D:\QT\Tools\mingw1310_64\bin;D:\QT\6.11.0\mingw_64\bin;%PATH%
cd /d "E:\github\东亚重工\2026-06-29-16-01-01\QtCalculator"
D:\QT\6.11.0\mingw_64\bin\qmake.exe Calculator.pro
if %errorlevel% neq 0 (
    echo QMAKE FAILED
    exit /b 1
)
echo === qmake OK ===
mingw32-make.exe -j4
