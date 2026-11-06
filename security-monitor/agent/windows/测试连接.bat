@echo off
chcp 65001 >nul
title Agent 连接测试
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_connection.ps1"
if errorlevel 1 pause
