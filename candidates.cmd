@echo off
chcp 65001 >nul
cd /d "%~dp0tool"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY (
  echo Python not found. Install Python 3 or edit this file.
  pause
  exit /b 1
)
%PY% build_library.py --candidates
pause
