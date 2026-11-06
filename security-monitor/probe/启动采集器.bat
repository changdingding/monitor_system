@echo off
cd /d "%~dp0"
python probe.py
if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装 Python 3，并且 config.json 已填写。
  pause
)
