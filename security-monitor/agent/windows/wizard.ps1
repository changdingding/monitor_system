# Windows Agent 图形安装向导（下一步 / 授权 / 完成）
# 由「安装Agent.bat」以管理员权限启动
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = Join-Path $env:ProgramFiles "SecurityMonitorAgent"
$TaskName = "SecurityMonitorAgent"
$ConfigSrc = Join-Path $ScriptDir "config.json"
$AgentSrc = Join-Path $ScriptDir "agent.ps1"

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-ConfigPreview {
  if (-not (Test-Path $ConfigSrc)) { return $null }
  return Get-Content $ConfigSrc -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Install-Agent {
  if (-not (Test-Path $ConfigSrc)) { throw "安装包缺少 config.json" }
  if (-not (Test-Path $AgentSrc)) { throw "安装包缺少 agent.ps1" }

  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Copy-Item $AgentSrc (Join-Path $InstallDir "agent.ps1") -Force
  Copy-Item $ConfigSrc (Join-Path $InstallDir "config.json") -Force
  foreach ($extra in @("test_connection.ps1", "测试连接.bat")) {
    $p = Join-Path $ScriptDir $extra
    if (Test-Path $p) { Copy-Item $p (Join-Path $InstallDir $extra) -Force }
  }

  # 停掉旧任务，避免残留进程占着旧配置
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

  $ps = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
  $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$InstallDir\agent.ps1`""
  $action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $InstallDir
  $triggerStartup = New-ScheduledTaskTrigger -AtStartup
  # 立即再跑一次，避免必须重启才生效
  $triggerNow = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(3))
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($triggerStartup, $triggerNow) -Principal $principal -Settings $settings -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName

  # 安装后立刻测连通（失败不回滚，但提示用户）
  $cfg = Read-ConfigPreview
  if ($cfg) {
    $base = ([string]$cfg.server_url).Trim().TrimEnd("/")
    try {
      $null = Invoke-RestMethod -Uri "$base/health" -Method Get -TimeoutSec 15
      $headers = @{ "Content-Type" = "application/json"; "X-Agent-Token" = [string]$cfg.agent_token }
      $body = '{"hostname":"' + $env:COMPUTERNAME + '","os_type":"windows","agent_version":"install-check"}'
      $null = Invoke-RestMethod -Uri "$base/api/v1/agent/heartbeat" -Method Post -Headers $headers -Body $body -TimeoutSec 20
      $script:installHint = "连通测试通过：管理端已收到心跳。"
    } catch {
      $script:installHint = "文件已安装，但连通测试失败：$($_.Exception.Message)`r`n请双击安装目录中的「测试连接.bat」查看详情。"
    }
  } else {
    $script:installHint = "已安装。请运行「测试连接.bat」确认。"
  }
}

# ---- UI ----
$form = New-Object System.Windows.Forms.Form
$form.Text = "安全监控 Agent 安装向导"
$form.Size = New-Object System.Drawing.Size(560, 420)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)

$page = 0
$cfg = Read-ConfigPreview

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Location = New-Object System.Drawing.Point(24, 20)
$lblTitle.Size = New-Object System.Drawing.Size(500, 28)
$lblTitle.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 14, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($lblTitle)

$lblBody = New-Object System.Windows.Forms.Label
$lblBody.Location = New-Object System.Drawing.Point(24, 60)
$lblBody.Size = New-Object System.Drawing.Size(500, 240)
$form.Controls.Add($lblBody)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Location = New-Object System.Drawing.Point(24, 300)
$progress.Size = New-Object System.Drawing.Size(500, 18)
$progress.Style = "Marquee"
$progress.Visible = $false
$form.Controls.Add($progress)

$btnBack = New-Object System.Windows.Forms.Button
$btnBack.Text = "上一步"
$btnBack.Location = New-Object System.Drawing.Point(220, 340)
$btnBack.Size = New-Object System.Drawing.Size(90, 32)
$form.Controls.Add($btnBack)

$btnNext = New-Object System.Windows.Forms.Button
$btnNext.Text = "下一步"
$btnNext.Location = New-Object System.Drawing.Point(320, 340)
$btnNext.Size = New-Object System.Drawing.Size(90, 32)
$form.Controls.Add($btnNext)

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = "取消"
$btnCancel.Location = New-Object System.Drawing.Point(420, 340)
$btnCancel.Size = New-Object System.Drawing.Size(90, 32)
$form.Controls.Add($btnCancel)

function Show-Page {
  switch ($page) {
    0 {
      $lblTitle.Text = "欢迎使用安全监控 Agent"
      $lblBody.Text = @"
本向导将在本机安装安全监控客户端。

安装后会：
• 自动采集本机登录 / 退出与高危操作
• 主动上报到管理端，并由管理端推送飞书

点击「下一步」继续。
"@
      $btnBack.Enabled = $false
      $btnNext.Text = "下一步"
      $btnNext.Enabled = $true
      $progress.Visible = $false
    }
    1 {
      $serverUrl = if ($cfg) { $cfg.server_url } else { "(缺失)" }
      $tokenHint = if ($cfg -and $cfg.agent_token) { ($cfg.agent_token.Substring(0, [Math]::Min(8, $cfg.agent_token.Length)) + "…") } else { "(缺失)" }
      $lblTitle.Text = "确认安装信息"
      $lblBody.Text = @"
安装目录：
$InstallDir

管理端地址：
$serverUrl

Agent Token：
$tokenHint

计划任务名：
$TaskName

请确认以上信息正确。下一步将请求管理员授权并开始安装。
"@
      $btnBack.Enabled = $true
      $btnNext.Text = "安装"
      $btnNext.Enabled = $true
      $progress.Visible = $false
    }
    2 {
      $lblTitle.Text = "正在安装…"
      $lblBody.Text = "正在复制文件并注册开机自启任务，请稍候。"
      $btnBack.Enabled = $false
      $btnNext.Enabled = $false
      $btnCancel.Enabled = $false
      $progress.Visible = $true
      $form.Refresh()
      try {
        if (-not (Test-IsAdmin)) { throw "当前没有管理员权限，请关闭后右键「以管理员身份运行」安装Agent.bat" }
        Install-Agent
        $script:page = 3
        Show-Page
      } catch {
        $script:page = 4
        $script:errMsg = $_.Exception.Message
        Show-Page
      }
    }
    3 {
      $hint = if ($installHint) { $installHint } else { "" }
      $lblTitle.Text = "安装完成"
      $lblBody.Text = @"
Agent 已安装并尝试启动。

• 安装目录：$InstallDir
• 开机自动运行
• $hint

也可双击安装目录里的「测试连接.bat」自检。
回到管理端点「刷新」，Agent 应变为「在线」。
"@
      $progress.Visible = $false
      $btnBack.Enabled = $false
      $btnCancel.Visible = $false
      $btnNext.Text = "完成"
      $btnNext.Enabled = $true
    }
    4 {
      $lblTitle.Text = "安装失败"
      $lblBody.Text = "错误信息：`n$errMsg`n`n请确认以管理员身份运行，或联系管理员。"
      $progress.Visible = $false
      $btnBack.Enabled = $true
      $btnCancel.Enabled = $true
      $btnNext.Text = "重试"
      $btnNext.Enabled = $true
    }
  }
}

$btnCancel.Add_Click({ $form.Close() })
$btnBack.Add_Click({
  if ($page -eq 4) { $script:page = 1 } else { $script:page = [Math]::Max(0, $page - 1) }
  Show-Page
})
$btnNext.Add_Click({
  if ($page -eq 3) { $form.Close(); return }
  if ($page -eq 4) { $script:page = 2; Show-Page; return }
  if ($page -eq 1) { $script:page = 2; Show-Page; return }
  $script:page = $page + 1
  Show-Page
})

Show-Page
[void]$form.ShowDialog()
