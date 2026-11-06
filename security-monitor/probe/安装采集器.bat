@echo off
chcp 65001 >nul
title 边缘采集器 安装 / 配置
cd /d "%~dp0"

:: 请求管理员授权（UAC），注册开机自启任务需要管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo 正在请求管理员授权，请在弹出窗口点击「是」…
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\wizard.ps1"
if errorlevel 1 (
  echo.
  echo 安装向导异常退出。可尝试右键本文件 → 以管理员身份运行。
  pause
)
