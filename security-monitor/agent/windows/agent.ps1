# Security Monitor Agent (Windows, PowerShell)
# 本地采集登录/退出与高危操作，主动上报管理端
$ErrorActionPreference = "Continue"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $AgentDir "config.json"
$StatePath = Join-Path $AgentDir "state.json"
$LogPath = Join-Path $AgentDir "agent.log"
$SpoolDir = Join-Path $AgentDir "spool"
$AgentVersion = "1.1.0"

function Write-AgentLog($msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  try { Add-Content -Path $LogPath -Value $line -Encoding UTF8 } catch {}
  Write-Host $line
}

function Load-Config {
  if (-not (Test-Path $ConfigPath)) { throw "缺少 config.json" }
  $raw = Get-Content $ConfigPath -Raw -Encoding UTF8
  $cfg = $raw | ConvertFrom-Json
  if (-not $cfg.server_url -or -not $cfg.agent_token) { throw "config.json 缺少 server_url 或 agent_token" }
  if (-not $cfg.poll_interval_seconds) { $cfg | Add-Member -NotePropertyName poll_interval_seconds -NotePropertyValue 30 -Force }
  if (-not $cfg.heartbeat_interval_seconds) { $cfg | Add-Member -NotePropertyName heartbeat_interval_seconds -NotePropertyValue 60 -Force }
  $cfg.server_url = ([string]$cfg.server_url).Trim().TrimEnd("/")
  $cfg.agent_token = ([string]$cfg.agent_token).Trim()
  return $cfg
}

function Load-State {
  if (Test-Path $StatePath) {
    try { return Get-Content $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch {}
  }
  return [pscustomobject]@{ sessions = @{}; seen_high_risk = @(); last_heartbeat = 0 }
}

function Save-State($state) {
  $seen = @($state.seen_high_risk)
  if ($seen.Count -gt 5000) { $state.seen_high_risk = $seen[-3000..-1] }
  ($state | ConvertTo-Json -Depth 8) | Set-Content -Path $StatePath -Encoding UTF8
}

function Save-AgentSpool($path, $payload) {
  if (-not (Test-Path $SpoolDir)) { New-Item -ItemType Directory -Path $SpoolDir | Out-Null }
  $name = "{0}-{1}.json" -f [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds(), (Get-Random -Maximum 99999)
  $item = @{ path = $path; payload = $payload } | ConvertTo-Json -Depth 8 -Compress
  Set-Content -Path (Join-Path $SpoolDir $name) -Value $item -Encoding UTF8
  $files = @(Get-ChildItem $SpoolDir -Filter *.json | Sort-Object Name)
  if ($files.Count -gt 480) {
    $files | Select-Object -First ($files.Count - 480) | Remove-Item -Force -ErrorAction SilentlyContinue
  }
}

function Send-AgentApi($cfg, $path, $payload) {
  if (Test-Path $SpoolDir) {
    foreach ($file in @(Get-ChildItem $SpoolDir -Filter *.json | Sort-Object Name)) {
      try {
        $saved = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $savedPayload = $saved.payload
        Invoke-AgentApi $cfg $saved.path $savedPayload | Out-Null
        Remove-Item $file.FullName -Force
      } catch {
        $code = 0
        try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -ge 400 -and $code -lt 500 -and $code -ne 429) {
          Remove-Item $file.FullName -Force -ErrorAction SilentlyContinue
          continue
        }
        break
      }
    }
  }
  try {
    return Invoke-AgentApi $cfg $path $payload
  } catch {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -ge 400 -and $code -lt 500 -and $code -ne 429) { throw }
    Save-AgentSpool $path $payload
    Write-AgentLog ("链路中断，已暂存 " + $path)
    return $null
  }
}

function Invoke-AgentApi($cfg, $path, $payload) {
  $url = "$($cfg.server_url)$path"
  $json = $payload | ConvertTo-Json -Depth 8 -Compress
  # Windows PowerShell 默认可能用系统代码页，强制 UTF-8
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $headers = @{
    "X-Agent-Token" = $cfg.agent_token
    "User-Agent" = "security-monitor-agent/$AgentVersion"
  }
  $resp = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 20
  return $resp
}

function Format-EventTime($value) {
  if (-not $value) { return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") }
  try { return ([datetime]$value).ToString("yyyy-MM-dd HH:mm:ss") }
  catch { return "$value" }
}

function Collect-SessionEvents {
  $start = (Get-Date).AddMinutes(-3)
  $events = @()
  try {
    $raw = Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 4624, 4634, 4647; StartTime = $start } -ErrorAction SilentlyContinue
  } catch { $raw = @() }
  foreach ($e in @($raw | Sort-Object TimeCreated)) {
    $account = ""
    $ip = ""
    $logonType = 0
    $action = ""
    $actionText = ""
    if ($e.Id -eq 4624) {
      $account = [string]$e.Properties[5].Value
      $logonType = [int]$e.Properties[8].Value
      $ip = [string]$e.Properties[18].Value
      if (@(2, 7, 10, 11) -notcontains $logonType) { continue }
      $action = "login"; $actionText = "账号登录"
    } elseif ($e.Id -in 4634, 4647) {
      $account = [string]$e.Properties[1].Value
      $action = "logout"; $actionText = "账号退出"
    } else { continue }
    if (-not $account -or $account.EndsWith("$")) { continue }
    $events += @{
      category = "session"
      action = $action
      action_text = $actionText
      account = $account
      event_time = Format-EventTime $e.TimeCreated
      source_ip = $ip
      os_type = "windows"
      hostname = $env:COMPUTERNAME
    }
  }
  return $events
}

function Get-WinEventDataMap($ev) {
  $map = @{}
  try {
    $xml = [xml]$ev.ToXml()
    foreach ($d in @($xml.Event.EventData.Data)) {
      $name = [string]$d.Name
      if ($name) { $map[$name] = [string]$d.'#text' }
    }
  } catch {}
  return $map
}

function Resolve-AccountName($value) {
  $v = ([string]$value).Trim()
  if (-not $v) { return "" }
  if ($v -notmatch '^S-1-') { return $v }
  try {
    $sid = New-Object System.Security.Principal.SecurityIdentifier($v)
    $name = $sid.Translate([System.Security.Principal.NTAccount]).Value
    if ($name -match '\\') { return ($name.Split('\')[-1]) }
    return $name
  } catch {
    return $v
  }
}

function Test-SensitiveGroup($groupName) {
  $g = ([string]$groupName).Trim().ToLower()
  if (-not $g) { return $false }
  $sensitive = @(
    'administrators', '管理员', 'admin',
    'remote desktop users', '远程桌面用户',
    'backup operators', '备份操作员',
    'power users', 'power users',
    'domain admins', 'enterprise admins',
    'account operators', '账户操作员',
    'schema admins'
  )
  foreach ($s in $sensitive) {
    if ($g -eq $s -or $g.EndsWith("\$s") -or $g.Contains($s)) { return $true }
  }
  return $false
}

function Collect-HighRiskEvents {
  $start = (Get-Date).AddMinutes(-3)
  $map = @{
    4697 = @{ op = "service_install"; level = "high"; title = "安装系统服务" }
    4698 = @{ op = "scheduled_task"; level = "high"; title = "创建计划任务" }
    4720 = @{ op = "user_create"; level = "critical"; title = "创建用户账户" }
    4732 = @{ op = "group_member_change"; level = "high"; title = "修改特权组成员" }
    1102 = @{ op = "log_clear"; level = "critical"; title = "清空安全日志" }
  }
  $events = @()
  try {
    $raw = Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 4697, 4698, 4720, 4732, 1102; StartTime = $start } -ErrorAction SilentlyContinue
  } catch { $raw = @() }

  $createdUsers = New-Object System.Collections.Generic.HashSet[string]
  $ordered = @($raw | Sort-Object TimeCreated)

  # 先收集本轮所有新建账户，用于抑制「创建用户→加入Users组」的连带告警
  foreach ($e in $ordered) {
    if ([int]$e.Id -ne 4720) { continue }
    $data0 = Get-WinEventDataMap $e
    $nu = Resolve-AccountName ($data0['TargetUserName'])
    if (-not $nu) { $nu = Resolve-AccountName ($data0['SamAccountName']) }
    if ($nu) { [void]$createdUsers.Add($nu.ToLower()) }
  }

  foreach ($e in $ordered) {
    $eid = [int]$e.Id
    if (-not $map.ContainsKey($eid)) { continue }
    $m = $map[$eid]
    $data = Get-WinEventDataMap $e

    $operator = Resolve-AccountName ($data['SubjectUserName'])
    if (-not $operator) { $operator = Resolve-AccountName ($data['SubjectUserSid']) }
    # 计算机账户名（带或不带 $）不当作操作者展示
    if ($operator -and (
        $operator -eq $env:COMPUTERNAME -or
        $operator.Equals($env:COMPUTERNAME, [System.StringComparison]::OrdinalIgnoreCase) -or
        $operator.EndsWith('$')
      )) {
      $operator = ""
    }
    # SubjectDomainName 有时会被旧逻辑误当成账户；XML 路径下若操作者仍空，勿用域名

    $target = ""
    $group = ""
    $desc = ""
    $account = ""

    switch ($eid) {
      4720 {
        $target = Resolve-AccountName ($data['TargetUserName'])
        if (-not $target) { $target = Resolve-AccountName ($data['SamAccountName']) }
        $who = if ($operator) { $operator } else { "未知账户" }
        $newUser = if ($target) { $target } else { "未知" }
        $desc = "账户「$who」创建了新用户「$newUser」"
        $account = $who
      }
      4732 {
        $group = Resolve-AccountName ($data['TargetUserName'])
        if (-not $group) { $group = Resolve-AccountName ($data['TargetSid']) }
        # 创建用户时会自动加入 Users 等普通组，不单独告警
        if (-not (Test-SensitiveGroup $group)) { continue }
        $member = Resolve-AccountName ($data['MemberName'])
        if (-not $member) { $member = Resolve-AccountName ($data['MemberSid']) }
        # 刚创建用户附带的特权组变更，合并进创建用户语义，避免刷屏
        if ($member -and $createdUsers.Contains($member.ToLower())) { continue }
        $who = if ($operator) { $operator } else { "未知账户" }
        $desc = "账户「$who」将「$member」加入了特权组「$group」"
        $account = $who
        $target = $member
      }
      4697 {
        $svc = $data['ServiceName']
        if (-not $svc) { $svc = $data['ServiceFileName'] }
        if (-not $svc) { $svc = "未知服务" }
        $who = if ($operator) { $operator } else { "未知账户" }
        $desc = "账户「$who」安装了系统服务「$svc」"
        $account = $who
      }
      4698 {
        $task = $data['TaskName']
        if (-not $task) { $task = "未知任务" }
        $who = if ($operator) { $operator } else { "未知账户" }
        $desc = "账户「$who」创建了计划任务「$task」"
        $account = $who
      }
      1102 {
        $who = if ($operator) { $operator } else { "未知账户" }
        $desc = "账户「$who」清空了 Windows 安全日志"
        $account = $who
      }
      default {
        $account = if ($operator) { $operator } else { "未知账户" }
        $desc = "$($m.title)（操作者：$account）"
      }
    }

    if ($account -and $account.EndsWith("$")) { continue }
    if (-not $desc) { continue }

    $item = @{
      category = "high_risk"
      operation_type = $m.op
      risk_level = $m.level
      account = $account
      event_time = Format-EventTime $e.TimeCreated
      description = $desc
      os_type = "windows"
      hostname = $env:COMPUTERNAME
    }
    if ($target) { $item["target_account"] = $target }
    if ($group) { $item["group_name"] = $group }
    $events += $item
  }
  return $events
}

function Filter-SessionByState($events, $state) {
  if (-not $state.sessions) { $state.sessions = @{} }
  $sessions = @{}
  foreach ($p in $state.sessions.PSObject.Properties) { $sessions[$p.Name] = $p.Value }
  $out = @()
  foreach ($ev in ($events | Sort-Object { $_.event_time })) {
    $account = [string]$ev.account
    if (-not $account) { continue }
    $cur = if ($sessions.ContainsKey($account)) { $sessions[$account] } else { "logged_out" }
    if ($ev.action -eq "login") {
      if ($cur -eq "logged_in") { continue }
      $sessions[$account] = "logged_in"
      $out += $ev
    } elseif ($ev.action -eq "logout") {
      if ($cur -ne "logged_in") { continue }
      $sessions[$account] = "logged_out"
      $out += $ev
    }
  }
  $state.sessions = $sessions
  return $out
}

function Filter-HighRisk($events, $state) {
  $seen = New-Object System.Collections.Generic.HashSet[string]
  foreach ($x in @($state.seen_high_risk)) { [void]$seen.Add([string]$x) }
  $out = @()
  foreach ($ev in $events) {
    # 按「操作类型 + 目标账户 + 分钟」去重，避免同一创建用户刷出多条
    $target = [string]$ev.target_account
    if (-not $target) { $target = [string]$ev.account }
    $minute = ""
    try { $minute = ([datetime]$ev.event_time).ToString("yyyy-MM-dd HH:mm") } catch { $minute = [string]$ev.event_time }
    $raw = "$($ev.operation_type)|$target|$minute"
    $sha = [System.BitConverter]::ToString(
      [System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($raw))
    ).Replace("-", "").ToLower()
    if ($seen.Contains($sha)) { continue }
    [void]$seen.Add($sha)
    $out += $ev
  }
  $state.seen_high_risk = @($seen)
  return $out
}

# ---- main loop ----
try {
  # 只保留一个 Agent 进程，避免旧版本+新版本同时跑导致重复推送
  try {
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
      Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        ($_.CommandLine -like '*SecurityMonitorAgent*agent.ps1*' -or $_.CommandLine -like '*\agent.ps1*')
      } |
      ForEach-Object {
        Write-AgentLog ("停止重复 Agent 进程 PID=$($_.ProcessId)")
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      }
  } catch {}

  $cfg = Load-Config
} catch {
  Write-AgentLog ("启动失败: " + $_.Exception.Message)
  throw
}
function Get-HostMetrics {
  $cpu = $null
  $mem = $null
  $disk = $null
  try {
    $cpu = [math]::Round((Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average, 1)
  } catch {}
  try {
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.TotalVisibleMemorySize -gt 0) {
      $mem = [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
    }
  } catch {}
  try {
    $logical = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    if ($logical -and $logical.Size -gt 0) {
      $disk = [math]::Round((($logical.Size - $logical.FreeSpace) / $logical.Size) * 100, 1)
    }
  } catch {}
  return @{ cpu = $cpu; memory = $mem; disk = $disk }
}

$state = Load-State
Write-AgentLog ("Security Monitor Agent $AgentVersion started on $env:COMPUTERNAME")
Write-AgentLog ("Reporting to $($cfg.server_url)")

while ($true) {
  try {
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $lastHb = 0
    try { $lastHb = [double]$state.last_heartbeat } catch {}
    if (($now - $lastHb) -ge [int]$cfg.heartbeat_interval_seconds) {
      $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
      Send-AgentApi $cfg "/api/v1/agent/heartbeat" @{
        hostname = $env:COMPUTERNAME
        os_type = "windows"
        agent_version = $AgentVersion
        collected_at = $stamp
      } | Out-Null
      $state.last_heartbeat = $now
      Write-AgentLog "heartbeat ok"
      try {
        $metrics = Get-HostMetrics
        $metrics.collected_at = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Send-AgentApi $cfg "/api/v1/agent/metrics" $metrics | Out-Null
        Write-AgentLog ("metrics cpu=$($metrics.cpu) mem=$($metrics.memory) disk=$($metrics.disk)")
      } catch {
        Write-AgentLog ("metrics error: " + $_.Exception.Message)
      }
    }

    $sessionEvents = Filter-SessionByState (Collect-SessionEvents) $state
    $riskEvents = Filter-HighRisk (Collect-HighRiskEvents) $state
    $all = @($sessionEvents) + @($riskEvents)
    if ($all.Count -gt 0) {
      $resp = Send-AgentApi $cfg "/api/v1/agent/events" @{ events = $all; collected_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") }
      Write-AgentLog ("reported $($all.Count) events, alerted=$($resp.alerted) skipped=$($resp.skipped)")
    }
    Save-State $state
  } catch {
    Write-AgentLog ("loop error: " + $_.Exception.Message)
  }
  Start-Sleep -Seconds ([int]$cfg.poll_interval_seconds)
}
