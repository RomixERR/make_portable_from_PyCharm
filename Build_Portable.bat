@echo off
setlocal EnableExtensions
rem ==============================================================
rem  Build_Portable.bat  --  make a portable build of a PyCharm project
rem
rem  HOW TO USE
rem    1) just drag the project folder onto this .bat file, or
rem    2) double-click it and type the project folder path.
rem
rem  The result is created next to the project:  <ProjectName>_Portable
rem  Move that folder to any PC (no internet, no Python needed there)
rem  and run Start.bat inside it.
rem
rem  Extra options (run from a command line):
rem    Build_Portable.bat  C:\proj  --out  D:\portable_builds
rem    Build_Portable.bat  C:\proj  --overwrite
rem    Build_Portable.bat  C:\proj  --python 3.10 --entry main.py
rem    Build_Portable.bat  C:\proj  --lean          (smaller: only used packages)
rem    Build_Portable.bat  C:\proj  --runtime C:\Python310
rem    Build_Portable.bat  C:\proj  --no-relocate   (skip the relocation test)
rem ==============================================================

set "TOOL=%~dp0make_portable.py"
if not exist "%TOOL%" (
  echo [ERROR] make_portable.py not found next to this .bat file:
  echo         "%TOOL%"
  echo Keep Build_Portable.bat and make_portable.py in the same folder.
  pause
  exit /b 1
)

rem --- find a Python 3.6+ that is only used to RUN this builder tool ---
set "PYCMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 1>nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD python -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 1>nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
  echo [ERROR] This builder tool needs Python 3.6+ on THIS computer.
  echo         Install Python from python.org, or run the tool with a Python you have.
  pause
  exit /b 1
)

echo Builder will run with: %PYCMD%
echo.

%PYCMD% "%TOOL%" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [INFO] finished with code %RC% - read the messages above.
pause
exit /b %RC%
