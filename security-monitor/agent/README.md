# 安全监控 Agent

推荐从管理端「服务器管理 → 安装Agent」下载**预配置安装包**（Token 已写入，小白直接下一步安装）。

## Windows（推荐）

1. 下载 Windows 安装包并解压  
2. 双击 `安装Agent.bat`  
3. UAC 点「是」→ 向导「下一步」→「安装」→「完成」  
无需安装 Python。

## Linux

1. 下载 Linux 安装包并解压  
2. `sudo bash install.sh`  
输入密码授权即可。

## 开发 / 手动运行

也可直接使用本目录脚本：

- Windows PowerShell Agent：`windows/agent.ps1`
- 跨平台 Python Agent：`agent.py` + `config.example.json`

管理端地址必须是目标机能访问的 IP/域名，不要填 `localhost`。
