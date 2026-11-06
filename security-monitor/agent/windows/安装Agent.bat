@echo off
chcp 65001 >nul
title 安全监控 Agent 安装
cd /d "%~dp0"

:: 请求管理员授权（UAC）
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo 正在请求管理员授权，请在弹出窗口点击「是」…
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wizard.ps1"
if errorlevel 1 (
  echo.
  echo 安装向导异常退出。可尝试右键本文件 → 以管理员身份运行。
  pause
)
