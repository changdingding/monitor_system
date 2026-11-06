# 一键测试：管理端是否可达 + Token 是否有效
Add-Type -AssemblyName System.Windows.Forms
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ScriptDir "config.json"
# 若从安装目录旁运行，也尝试 Program Files
if (-not (Test-Path $ConfigPath)) {
  $alt = Join-Path $env:ProgramFiles "SecurityMonitorAgent\config.json"
  if (Test-Path $alt) {
    $ConfigPath = $alt
    $ScriptDir = Split-Path -Parent $alt
  }
}

function Show-Result($title, $text) {
  [System.Windows.Forms.MessageBox]::Show($text, $title, "OK", "Information") | Out-Null
}

try {
  if (-not (Test-Path $ConfigPath)) { throw "找不到 config.json：$ConfigPath" }
  $cfg = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $base = ([string]$cfg.server_url).Trim().TrimEnd("/")
  $token = [string]$cfg.agent_token
  if (-not $base) { throw "config.json 中 server_url 为空" }
  if (-not $token) { throw "config.json 中 agent_token 为空" }

  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add("配置文件: $ConfigPath")
  $lines.Add("管理端地址: $base")
  $lines.Add("Token 前缀: $($token.Substring(0, [Math]::Min(6, $token.Length)))...")
  $lines.Add("")

  # 1) health
  try {
    $h = Invoke-RestMethod -Uri "$base/health" -Method Get -TimeoutSec 15
    $lines.Add("[1] 健康检查: 成功  status=$($h.status)")
  } catch {
    $lines.Add("[1] 健康检查: 失败")
    $lines.Add("    $($_.Exception.Message)")
    $lines.Add("")
    $lines.Add("说明: 云服务器访问不到管理端。请确认：")
    $lines.Add("1) cpolar 隧道仍在线")
    $lines.Add("2) server_url 填的是 http://xxx.cpolar.top （不要加 :8000）")
    $lines.Add("3) 本机 Docker 管理端正在运行")
    Show-Result "Agent 连接测试 - 失败" ($lines -join "`r`n")
    exit 1
  }

  # 2) heartbeat
  try {
    $headers = @{
      "Content-Type" = "application/json"
      "X-Agent-Token" = $token
    }
    $body = '{"hostname":"' + $env:COMPUTERNAME + '","os_type":"windows","agent_version":"test"}'
    $resp = Invoke-RestMethod -Uri "$base/api/v1/agent/heartbeat" -Method Post -Headers $headers -Body $body -TimeoutSec 20
    $lines.Add("[2] 心跳上报: 成功  server=$($resp.server_name)")
    $lines.Add("")
    $lines.Add("请回到管理端点「刷新」，Agent 应显示「在线」。")
    Show-Result "Agent 连接测试 - 成功" ($lines -join "`r`n")
  } catch {
    $msg = $_.Exception.Message
    $detail = ""
    try {
      $respStream = $_.Exception.Response.GetResponseStream()
      if ($respStream) {
        $reader = New-Object System.IO.StreamReader($respStream)
        $detail = $reader.ReadToEnd()
      }
    } catch {}
    $lines.Add("[2] 心跳上报: 失败")
    $lines.Add("    $msg")
    if ($detail) { $lines.Add("    $detail") }
    $lines.Add("")
    $lines.Add("若提示无效 Token：请在管理端「重置 Token」后重新下载安装包。")
    Show-Result "Agent 连接测试 - 失败" ($lines -join "`r`n")
    exit 1
  }
} catch {
  Show-Result "Agent 连接测试 - 失败" $_.Exception.Message
  exit 1
}
