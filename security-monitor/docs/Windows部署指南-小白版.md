# Windows 部署指南（小白版）

本文教你把 **安全监控系统（管理端）** 装到 Windows / Windows Server 上，并安装被监控机器上的 **Agent 客户端**。

适合：几乎没接触过 Docker / 服务器的同事。

---

## 一、先搞清楚两件事

本系统分两块，不要混淆：

| 名称 | 装在哪里 | 干什么 |
|------|----------|--------|
| **管理端** | 一台 Windows 电脑或 Windows Server | 网页控制台、存数据、推飞书 |
| **Agent 客户端** | 每台要监控的 Windows / Linux 服务器 | 本地采集登录/高危操作，主动上报管理端 |

推荐架构：

```
[被监控服务器] --Agent上报--> [管理端 Windows Server] --Webhook--> [飞书群]
```

如果被监控机器在公网云上，而管理端在公司内网，云机器访问不到内网时，需要给管理端做 **内网穿透**（如 cpolar / frp），或把管理端也放到有公网 IP 的云主机上。

---

## 二、管理端部署（Windows / Windows Server）

### 1. 准备环境

1. 电脑建议：Windows 10/11 或 Windows Server 2019/2022，内存 **≥ 4GB**（推荐 8GB）。
2. 安装 **Docker Desktop**（必须）：
   - 官网：https://www.docker.com/products/docker-desktop/
   - 安装时勾选使用 WSL 2（按安装向导默认即可）。
   - 安装完重启电脑。
   - 打开 Docker Desktop，等到左下角显示 **Running / 引擎正在运行**。
3. 确认 Docker 可用：打开 **PowerShell**，输入：

```powershell
docker version
docker compose version
```

能看到版本号即可。若提示不是内部命令，说明 Docker 没装好或没启动。

### 2. 拿到项目文件

把整个 `security-monitor` 项目文件夹拷到服务器，例如：

```text
D:\apps\security-monitor
```

文件夹里应能看到：`docker-compose.yml`、`Dockerfile`、`app`、`web`、`agent`、`init.sql` 等。

### 3. 配置环境变量（必做）

1. 复制 `.env.example` 为 `.env`（若已有 `.env` 可跳过）。
2. 生成三串随机字符：在 PowerShell 中执行下面命令**三次**，得到三串不同的随机字符：

```powershell
$b = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

3. 用记事本打开 `.env`，填写 / 检查：

```ini
DB_PASSWORD=第一串随机字符
JWT_SECRET=第二串随机字符
CREDENTIAL_KEY=第三串随机字符
FEISHU_WEBHOOK_URL=
FEISHU_SECRET=
```

- **`DB_PASSWORD`、`JWT_SECRET`、`CREDENTIAL_KEY` 三项必填**，不填或使用 `postgres123` 这类默认密码，系统会拒绝启动。
- `CREDENTIAL_KEY` 用于加密保存服务器 SSH 密码和 SNMP 团体字，**填好后不要再改、不要丢**（建议另行备份），否则已保存的密码无法解密，需要重新录入。
- 数据库密码只在**第一次启动**时生效。从旧版本升级、原来用的是 `postgres123` 的，请按 `QUICKSTART.md`「从旧版本升级」一节先改库内密码。
- 飞书 Webhook 也可先空着，稍后在网页里配置（只接受 `https://open.feishu.cn/...` 或 `https://open.larksuite.com/...` 机器人地址）。

### 4. 启动系统

1. 在项目目录打开 PowerShell：

```powershell
cd D:\apps\security-monitor
docker compose up -d --build
```

第一次会下载镜像、构建，可能要 **5～20 分钟**，请耐心等待。

2. 查看是否启动成功：

```powershell
docker compose ps
```

应看到类似：

| 容器名 | 状态 |
|--------|------|
| security-monitor-web | Up |
| security-monitor-app | Up (healthy) |
| security-monitor-db | Up (healthy) |
| security-monitor-redis | Up (healthy) |

3. 浏览器打开：

- 管理界面：http://本机IP:3000  
  本机也可：http://localhost:3000  
- 后端健康检查：http://本机IP:8000/health  

4. 默认登录账号：

| 项目 | 值 |
|------|----|
| 用户名 | `admin` |
| 密码 | `admin123` |

**首次登录系统会强制弹出“修改密码”窗口，改完才能进入系统。** 之后可在右上角用户菜单 →「修改密码」随时修改。

### 5. 防火墙放行（很重要）

若别的机器要访问本管理端，或云服务器上的 Agent 要连过来，需要在 Windows 防火墙放行：

| 端口 | 用途 |
|------|------|
| **3000** | 网页管理界面 |
| **8000** | API / Agent 上报（必须） |
| 5432 | 数据库（一般只本机用，可不对外开放） |
| 6379 | Redis（一般不对外开放） |

PowerShell（管理员）示例：

```powershell
New-NetFirewallRule -DisplayName "SecurityMonitor-Web-3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
New-NetFirewallRule -DisplayName "SecurityMonitor-API-8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

云服务器（阿里云/腾讯云等）还要在 **安全组** 放行同样端口。

### 6. 常用运维命令

在项目目录执行：

```powershell
# 查看状态
docker compose ps

# 查看后端日志
docker compose logs -f app

# 停止
docker compose stop

# 启动
docker compose start

# 重新构建并启动
docker compose up -d --build

# 完全停止并删除容器（数据卷默认保留）
docker compose down
```

数据（PostgreSQL）存在 Docker 卷里，一般 `down` 不会丢库；不要随便删 volume。

---

## 三、配置飞书告警

1. 打开飞书群 → 添加机器人 → 自定义机器人 → 复制 Webhook。
2. 登录管理端 → **飞书配置**。
3. 填入 Webhook（如有签名校验再填 Secret）。
4. 打开需要的告警开关（登录/退出、高危操作等）。
5. 点 **发送测试**，群里能收到即成功。
6. 建议点一次 **恢复默认模板** 再保存，确保含「数据来源」等新字段。

---

## 四、添加服务器并安装 Agent（推荐）

### 1. 在管理端添加服务器

1. 打开 **服务器管理** → **添加服务器**。
2. 填写名称、IP、操作系统。
3. 采集方式选：**仅 Agent 客户端推送（推荐）**。
4. Agent 模式可不填 SSH 密码。
5. 保存后点 **安装Agent**。

### 2. 下载安装包

1. **管理端地址** 填被监控机能访问到的地址，例如：

```text
http://192.168.1.10:8000
```

或公网/穿透地址：

```text
http://xxxx.cpolar.top
```

注意：

- **不要**填 `localhost`（云服务器上的 Agent 访问不到你的本机 localhost）。
- **不要**在 cpolar 地址后再加 `:8000`（穿透已映射到 8000）。

2. 点 **下载 Windows 安装包**（Linux 服务器则下 Linux 包）。
3. 把 zip 拷到目标服务器解压。

### 3. Windows 目标机安装 Agent

1. 解压后双击 **`安装Agent.bat`**。
2. 弹出「用户账户控制」点 **是**。
3. 向导里：**下一步 → 安装 → 完成**。
4. 回到管理端点 **刷新**，「当前通道」应显示 **Agent 正在推送**。

若任务没装上（提示找不到计划任务），用**管理员 PowerShell**执行（路径按实际解压目录改）：

```powershell
$src = "D:\安装包解压目录\agent.ps1"
Stop-ScheduledTask -TaskName SecurityMonitorAgent -ErrorAction SilentlyContinue
Copy-Item $src "C:\Program Files\SecurityMonitorAgent\agent.ps1" -Force
# 然后重新运行解压目录里的 安装Agent.bat
```

### 4. Linux 目标机安装 Agent

```bash
unzip SecurityMonitorAgent-Linux-xxx.zip
cd SecurityMonitorAgent-Linux-xxx
sudo bash install.sh
```

---

## 五、云服务器连内网管理端（可选）

场景：管理端在公司电脑，Agent 在公网云主机。

1. 本机安装 cpolar / ngrok / frp，把本机 `8000` 映射成公网 URL。
2. 安装 Agent 时，管理端地址填该公网 URL。
3. 在云服务器测试：

```powershell
Invoke-RestMethod "http://你的穿透地址/health"
```

应返回 `healthy`。

---

## 六、不用 Agent、改用 SSH 拉取（备选）

适合暂时装不了 Agent、但管理端能 SSH 连到目标机时：

1. 编辑服务器 → 采集方式选 **仅 SSH** 或 **混合**。
2. 填写 SSH 端口、用户名、密码。
3. Windows 目标机需开启 OpenSSH Server。
4. 点 **SSH测试** / **SSH拉取**。

飞书告警里会标注数据来源是「SSH 服务端拉取」还是「Agent 客户端上报」。

---

## 七、验收清单（装完请打勾）

- [ ] Docker Desktop 状态为 Running  
- [ ] `docker compose ps` 四个服务都是 Up  
- [ ] 浏览器能打开 http://服务器IP:3000 并登录  
- [ ] 飞书测试消息能收到  
- [ ] 至少一台机器 Agent「推送中」或 SSH「可拉取」  
- [ ] 测试登录或创建用户后，飞书有对应告警且描述可读  

---

## 八、常见问题

### 1. 网页打不开

- Docker 是否在跑？  
- 端口是不是 **3000**（不是 80）？  
- 防火墙/安全组是否放行 3000？

### 2. Agent 一直「未连接」

- `config.json` 里的 `server_url` 是否从目标机能访问？  
- 管理端 8000 是否放行？  
- 计划任务 `SecurityMonitorAgent` 是否 Running？  
- 看 `C:\Program Files\SecurityMonitorAgent\agent.log`

### 3. 创建用户推了两条 / 描述乱码

- 更新到最新 Agent 安装包并重装。  
- 服务端已做去重与中文描述；SSH 拉取也已改为结构化中文。  
- 飞书配置页点「恢复默认模板」并保存。

### 4. 忘记管理员密码

进入数据库容器重置（高级操作，可找运维）：

```powershell
docker exec -it security-monitor-db psql -U postgres -d security_monitor
```

---

## 九、端口一览

| 端口 | 协议 | 说明 |
|------|------|------|
| 3000 | TCP | Web 管理界面 |
| 8000 | TCP | 后端 API、Agent 上报 |
| 5432 | TCP | PostgreSQL |
| 6379 | TCP | Redis |

---

部署完成后，日常使用请看同目录的《项目说明书》。
