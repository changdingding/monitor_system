# 边缘采集器 安装 / 配置向导（Windows）
# 由「安装采集器.bat」或「修改配置.bat」以管理员权限启动。
# 已安装时自动读取安装目录中的配置，可修改总部地址 / Token 后保存并重启采集器。
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackageDir = Split-Path -Parent $ScriptDir
if (-not (Test-Path (Join-Path $PackageDir "probe.py"))) { $PackageDir = $ScriptDir }
$InstallDir = Join-Path $env:ProgramFiles "SecurityMonitorProbe"
$TaskName = "SecurityMonitorProbe"
$Installed = Test-Path (Join-Path $InstallDir "probe.py")

function Read-Json($path) {
  if (-not (Test-Path $path)) { return $null }
  try { return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Find-Python {
  $candidates = @()
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") { $candidates += $cmd.Source }
  }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    try {
      $exe = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
      if ($exe) { $candidates += $exe.Trim() }
    } catch {}
  }
  foreach ($pattern in @("$env:ProgramFiles\Python3*\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "C:\Python3*\python.exe")) {
    $candidates += (Get-ChildItem $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
  }
  foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) {
      try {
        $ver = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($ver -and [version]$ver -ge [version]"3.8") { return $c }
      } catch {}
    }
  }
  return $null
}

$cfg = Read-Json (Join-Path $InstallDir "config.json")
if (-not $cfg) { $cfg = Read-Json (Join-Path $PackageDir "config.json") }
if (-not $cfg) { $cfg = [pscustomobject]@{ server_url = "http://"; probe_token = ""; interval_seconds = 60; devices = @() } }
$Python = Find-Python

# ---------------- UI ----------------
$form = New-Object System.Windows.Forms.Form
$form.Text = if ($Installed) { "边缘采集器 - 修改配置" } else { "边缘采集器 - 安装向导" }
$form.Size = New-Object System.Drawing.Size(600, 470)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)

$title = New-Object System.Windows.Forms.Label
$title.Text = if ($Installed) { "采集器已安装，可修改参数后点「保存并重启」" } else { "把本机安装为站点边缘采集器（开机自动运行）" }
$title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(20, 15)
$title.Size = New-Object System.Drawing.Size(550, 28)
$form.Controls.Add($title)

function Add-Field($label, $value, $y, $password = $false) {
  $l = New-Object System.Windows.Forms.Label
  $l.Text = $label
  $l.Location = New-Object System.Drawing.Point(20, $y)
  $l.Size = New-Object System.Drawing.Size(120, 24)
  $form.Controls.Add($l)
  $t = New-Object System.Windows.Forms.TextBox
  $t.Text = [string]$value
  $t.Location = New-Object System.Drawing.Point(145, ($y - 3))
  $t.Size = New-Object System.Drawing.Size(420, 26)
  if ($password) { $t.UseSystemPasswordChar = $true }
  $form.Controls.Add($t)
  return $t
}

$txtUrl = Add-Field "总部地址" $cfg.server_url 60
$txtToken = Add-Field "站点 Token" $cfg.probe_token 100 $true
$chkShow = New-Object System.Windows.Forms.CheckBox
$chkShow.Text = "显示 Token"
$chkShow.Location = New-Object System.Drawing.Point(145, 128)
$chkShow.Size = New-Object System.Drawing.Size(200, 24)
$chkShow.Add_CheckedChanged({ $txtToken.UseSystemPasswordChar = -not $chkShow.Checked })
$form.Controls.Add($chkShow)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "设备清单、团体字、自动发现网段都在总部网页「站点 / 网络设备」中配置，采集器自动拉取，现场无需逐台填写。"
$hint.ForeColor = [System.Drawing.Color]::DimGray
$hint.Location = New-Object System.Drawing.Point(20, 160)
$hint.Size = New-Object System.Drawing.Size(550, 44)
$form.Controls.Add($hint)

$pyLabel = New-Object System.Windows.Forms.Label
$pyLabel.Location = New-Object System.Drawing.Point(20, 208)
$pyLabel.Size = New-Object System.Drawing.Size(550, 24)
if ($Python) {
  $pyLabel.Text = "Python：$Python"
  $pyLabel.ForeColor = [System.Drawing.Color]::DarkGreen
} else {
  $pyLabel.Text = "未找到 Python 3.8+，请先安装（安装时勾选 Add to PATH / 为所有用户安装）"
  $pyLabel.ForeColor = [System.Drawing.Color]::Firebrick
}
$form.Controls.Add($pyLabel)

$output = New-Object System.Windows.Forms.TextBox
$output.Multiline = $true
$output.ReadOnly = $true
$output.ScrollBars = "Vertical"
$output.Location = New-Object System.Drawing.Point(20, 238)
$output.Size = New-Object System.Drawing.Size(545, 120)
$form.Controls.Add($output)

function Log($msg) { $output.AppendText(("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $msg)) }

function Get-Inputs {
  $url = $txtUrl.Text.Trim().TrimEnd("/")
  $token = $txtToken.Text.Trim()
  if (-not ($url -match "^https?://[^/]+")) { throw "总部地址要以 http:// 或 https:// 开头，例如 http://10.0.0.8:8000" }
  if ($url -match "localhost|127\.0\.0\.1") { throw "不要填 localhost，请填写本机能访问到的总部 IP 或域名" }
  if ($token.Length -lt 16) { throw "请填写站点 Token（在总部「站点」页面点「采集器」查看）" }
  return @{ url = $url; token = $token }
}

function Test-Server($inputs) {
  $headers = @{ "X-Probe-Token" = $inputs.token; "Content-Type" = "application/json" }
  $body = '{"hostname":"' + $env:COMPUTERNAME + '","probe_version":"wizard-check"}'
  $hb = Invoke-RestMethod -Uri "$($inputs.url)/api/v1/probe/heartbeat" -Method Post -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 15
  $conf = Invoke-RestMethod -Uri "$($inputs.url)/api/v1/probe/config" -Method Get -Headers $headers -TimeoutSec 15
  $count = @($conf.devices).Count
  $scan = if ($conf.scan.enabled) { "，自动发现网段：" + (@($conf.scan.cidrs) -join ", ") } else { "" }
  return "连接成功：站点「$($hb.site_name)」，总部已配置 $count 台设备$scan"
}

function Save-Config($dir, $inputs) {
  $data = [ordered]@{
    server_url = $inputs.url
    probe_token = $inputs.token
    interval_seconds = if ($cfg.interval_seconds) { [int]$cfg.interval_seconds } else { 60 }
    devices = if ($cfg.devices) { @($cfg.devices) } else { @() }
  }
  $json = $data | ConvertTo-Json -Depth 6
  [IO.File]::WriteAllText((Join-Path $dir "config.json"), $json, (New-Object Text.UTF8Encoding($false)))
}

function Stop-ProbeProcess {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*SecurityMonitorProbe*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Install-Probe($inputs) {
  if (-not $Python) { throw "未找到 Python 3.8+，无法安装。请安装 Python 后重新运行本向导。" }
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  foreach ($f in @("probe.py", "snmp_client.py", "请先阅读.txt")) {
    $src = Join-Path $PackageDir $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $InstallDir $f) -Force }
  }
  $winDir = Join-Path $InstallDir "windows"
  New-Item -ItemType Directory -Force -Path $winDir | Out-Null
  foreach ($f in @("wizard.ps1")) {
    $src = Join-Path $ScriptDir $f
    if ((Test-Path $src) -and ((Resolve-Path $src).Path -ne (Join-Path $winDir $f))) { Copy-Item $src (Join-Path $winDir $f) -Force }
  }
  # 安装目录里放一个「修改配置」入口
  $bat = "@echo off`r`ncd /d `"%~dp0`"`r`nnet session >nul 2>&1`r`nif %errorlevel% neq 0 (powershell -NoProfile -Command `"Start-Process -FilePath '%~f0' -Verb RunAs`" & exit /b)`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0windows\wizard.ps1`"`r`n"
  [IO.File]::WriteAllText((Join-Path $InstallDir "修改配置.bat"), $bat, (New-Object Text.UTF8Encoding($true)))
  Save-Config $InstallDir $inputs

  Stop-ProbeProcess
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$InstallDir\probe.py`" --service" -WorkingDirectory $InstallDir
  $triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -Once -At ((Get-Date).AddSeconds(3))))
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
  Start-ScheduledTask -TaskName $TaskName
}

$btnTest = New-Object System.Windows.Forms.Button
$btnTest.Text = "测试连接"
$btnTest.Location = New-Object System.Drawing.Point(20, 375)
$btnTest.Size = New-Object System.Drawing.Size(110, 34)
$btnTest.Add_Click({
  try { $i = Get-Inputs; Log "正在连接 $($i.url) …"; Log (Test-Server $i) }
  catch { Log ("失败：" + $_.Exception.Message) }
})
$form.Controls.Add($btnTest)

$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = if ($Installed) { "保存并重启" } else { "安装" }
$btnInstall.Location = New-Object System.Drawing.Point(140, 375)
$btnInstall.Size = New-Object System.Drawing.Size(120, 34)
$btnInstall.Add_Click({
  try {
    $i = Get-Inputs
    try { Log (Test-Server $i) } catch { Log ("警告：连接测试失败（" + $_.Exception.Message + "），仍继续安装，网络恢复后会自动上报") }
    Install-Probe $i
    Log "已安装到 $InstallDir，并注册开机自启任务「$TaskName」，采集器已启动。"
    Log "以后修改参数：运行安装目录中的「修改配置.bat」。运行日志：$InstallDir\probe.log"
    $btnInstall.Text = "保存并重启"
    $script:Installed = $true
  } catch { Log ("安装失败：" + $_.Exception.Message) }
})
$form.Controls.Add($btnInstall)

$btnUninstall = New-Object System.Windows.Forms.Button
$btnUninstall.Text = "卸载"
$btnUninstall.Location = New-Object System.Drawing.Point(270, 375)
$btnUninstall.Size = New-Object System.Drawing.Size(90, 34)
$btnUninstall.Enabled = $Installed
$btnUninstall.Add_Click({
  $ok = [System.Windows.Forms.MessageBox]::Show("确定卸载采集器？总部将显示该站点采集器离线。", "确认", "YesNo", "Warning")
  if ($ok -ne "Yes") { return }
  try {
    Stop-ProbeProcess
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Log "已删除开机任务。安装目录 $InstallDir 保留（含日志），可手动删除。"
    $btnUninstall.Enabled = $false
  } catch { Log ("卸载失败：" + $_.Exception.Message) }
})
$form.Controls.Add($btnUninstall)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = "关闭"
$btnClose.Location = New-Object System.Drawing.Point(465, 375)
$btnClose.Size = New-Object System.Drawing.Size(100, 34)
$btnClose.Add_Click({ $form.Close() })
$form.Controls.Add($btnClose)

if ($Installed) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) { Log "开机任务状态：$($task.State)" } else { Log "未找到开机任务，点「保存并重启」重新注册" }
}

[void]$form.ShowDialog()
