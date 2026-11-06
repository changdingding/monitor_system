# 在目标 Windows 服务器以管理员 PowerShell 执行
# 用法: .\install_windows.ps1

$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $Python) {
  Write-Host "未找到 Python，请先安装 Python 3.8+ 并勾选 Add to PATH"
  exit 1
}

$config = Join-Path $AgentDir "config.json"
if (-not (Test-Path $config)) {
  Copy-Item (Join-Path $AgentDir "config.example.json") $config
  Write-Host "已创建 config.json，请填写 server_url 和 agent_token 后重新执行本脚本"
  exit 1
}

$taskName = "SecurityMonitorAgent"
$action = New-ScheduledTaskAction -Execute $Python.Source -Argument "`"$AgentDir\agent.py`"" -WorkingDirectory $AgentDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "已安装并启动计划任务: $taskName"
Write-Host "查看状态: Get-ScheduledTask -TaskName $taskName"
