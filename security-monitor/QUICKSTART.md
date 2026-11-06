# Docker 部署指南

适用于 Linux / macOS / Windows（Docker Desktop）。Windows 图文版见 [docs/Windows部署指南-小白版.md](docs/Windows部署指南-小白版.md)。

## 系统要求

- Docker 20.10+，Docker Compose v2（`docker compose version` 能输出版本号）
- 至少 2 GB 内存、5 GB 磁盘
- 端口：**3000**（Web 管理端）、**8000**（API / Agent 上报）

## 一、全新部署

### 1. 准备配置文件

```bash
cd security-monitor
cp .env.example .env          # Windows PowerShell：copy .env.example .env
```

生成三串随机字符（分别用于数据库密码、JWT_SECRET、CREDENTIAL_KEY）：

```bash
# Linux / macOS
python3 -c "import secrets; [print(secrets.token_urlsafe(36)) for _ in range(3)]"
```

```powershell
# Windows PowerShell（执行三次）
$b = New-Object byte[] 36; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)
```

编辑 `.env`，**以下三项必填**：缺失时 `docker compose up` 直接报错并提示是哪一项；过短或使用默认弱密码时，app 容器会拒绝启动：

```ini
DB_PASSWORD=第一串随机字符        # 至少 8 位，不能是 postgres123 等默认密码
JWT_SECRET=第二串随机字符         # 至少 32 位
CREDENTIAL_KEY=第三串随机字符     # 至少 32 位；设置后不要更换、不要丢失
```

其余项保持默认即可：

| 变量 | 默认 | 说明 |
|------|------|------|
| `FEISHU_WEBHOOK_URL` / `FEISHU_SECRET` | 空 | 也可以稍后在网页「飞书配置」里填 |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCK_MINUTES` | 5 / 15 | 连续登录失败几次锁定、锁定分钟数 |
| `AUDIT_RETENTION_DAYS` | 180 | 操作审计保留天数 |
| `ALERT_RETENTION_DAYS` | 365 | 告警历史保留天数（登录日志等原始日志按网页「系统设置」里的“日志保留天数”） |
| `CORS_ORIGINS` | 空 | 经 3000 端口访问不需要设置 |

> `CREDENTIAL_KEY` 用来加密 SSH 密码、SNMP 团体字和 Agent Token。换了它，已保存的这些凭据都无法解密，需要重新录入、重装 Agent。建议另外备份一份。

### 2. 构建并启动

```bash
docker compose up -d --build
```

第一次需要下载镜像、安装依赖，大约 5～20 分钟。

### 3. 确认启动成功

```bash
docker compose ps
```

四个容器 `security-monitor-db / -redis / -app / -web` 都应是 `Up`，其中 db、redis、app 显示 `(healthy)`（app 启动后约 30 秒变为 healthy）。

```bash
curl http://localhost:8000/health     # 应返回 {"status":"healthy","database":"connected"}
docker compose logs app | tail -20    # 应看到 “安全监控系统已启动”
```

app 没起来时，先看 `docker compose logs app`。常见原因是 `.env` 里的必填值太短或用了默认弱密码，日志会直接写明是哪一项。

### 4. 首次登录

浏览器打开 `http://服务器IP:3000`，用 `admin` / `admin123` 登录，系统会**强制修改密码**，改完即可进入。

## 二、部署后测试清单

建议按顺序走一遍，每一项都是这一轮修复涉及的功能：

| # | 操作 | 预期 |
|---|------|------|
| 1 | 首次登录 admin | 弹出强制改密，改完进入仪表盘 |
| 2 | 用户管理 → 新增一个「操作员」，退出后用它登录 | 能新增服务器，看不到「用户管理」数据（403） |
| 3 | admin 把该操作员改为「只读」，**不要让对方重新登录**，对方刷新页面后再新增服务器 | 被拒绝（权限不足）；看不到 Agent Token / SNMP 团体字 |
| 4 | admin 停用该账号，对方刷新页面 | 立即退回登录页 |
| 5 | 用错误密码连续登录某个账号 5 次 | 第 5 次提示锁定；用户管理里显示「已锁定」，可点「解锁」 |
| 6 | 服务器管理 → 添加服务器 → 安装 Agent，在一台真实服务器上安装 | 「Agent推送」变为「推送中」，仪表盘出现资源数据 |
| 7 | 在装了 Agent 的服务器上登录一次（RDP / SSH） | 告警历史出现登录记录，飞书收到推送（已配置时） |
| 8 | 删除第 6 步那台服务器 | 删除成功（之前有日志的服务器删不掉，已修复） |
| 9 | 网络设备 → 添加设备，选 SNMP v2c | 能看到并填写 SNMP Community 输入框（之前不显示，已修复） |
| 10 | 操作审计 | 以上操作都有记录，含失败的登录与越权尝试 |
| 11 | 浏览器开发者工具 → 网络 → 任一页面的响应头 | 含 `Content-Security-Policy`、`X-Frame-Options` |

## 三、从旧版本升级

```bash
cd security-monitor
docker compose down                   # 只停容器，不要加 -v（-v 会删除数据库）
# 替换为新代码后：
docker compose up -d --build
```

启动时会自动完成数据迁移，无需手工执行 SQL：

- 历史明文的 SSH 密码、SNMP 团体字、Agent / 采集器 Token 改为加密存储，**已安装的 Agent 不用重装**
- 补齐新字段（登录锁定、token 版本、审计表等），修正日志表外键

**注意数据库密码**：旧版本默认使用 `postgres123`，新版本不再允许。PostgreSQL 的密码只在数据库**首次初始化**时由 `DB_PASSWORD` 设置，已有数据库需要先在库里改密，再改 `.env`：

```bash
docker compose up -d postgres
docker exec -it security-monitor-db psql -U postgres -c "ALTER USER postgres PASSWORD '新密码';"
# 然后把 .env 里的 DB_PASSWORD 改成同样的新密码，再 docker compose up -d --build
```

## 四、日常运维

```bash
docker compose ps                     # 状态
docker compose logs -f app            # 后端日志
docker compose restart app            # 重启后端
docker compose down                   # 停止（保留数据）
```

**备份**（建议每天定时执行，并定期试着恢复一次）：

```bash
docker exec security-monitor-db pg_dump -U postgres -Fc security_monitor > backup_$(date +%F).dump
# 恢复：
docker exec -i security-monitor-db pg_restore -U postgres -d security_monitor --clean < backup_xxxx.dump
```

同时备份 `.env`，其中的 `CREDENTIAL_KEY` 丢失后，备份里加密的凭据也无法使用。

## 五、上线前须知

- **HTTPS**：目前登录密码、登录凭证和 Agent Token 都以 HTTP 明文传输。只在内网使用可以暂缓；Agent 经公网或内网穿透接入时，必须在前面加一层 HTTPS（如 Nginx / Caddy 反向代理 3000 与 8000 端口），并把 Agent 安装包里的管理端地址改为 `https://`。
- **端口**：5432 / 6379 不对外暴露；只需开放 3000（管理员访问）与 8000（Agent 上报）。8000 端口建议用防火墙限制为服务器网段可访问。
- **Syslog 接收尚未实现**，514/udp 端口不再开放。
- **SSH 拉取**：现使用 paramiko 5，不再接受 SHA-1 签名。OpenSSH 7.2 以下的老系统（如 CentOS 6）可能无法 SSH 拉取，请改用 Agent 模式。

## 常见问题

**端口被占用**：修改 `docker-compose.yml` 里的端口映射左侧数字，例如 `"3001:80"`。

**app 一直 unhealthy / 反复重启**：`docker compose logs app` 查看原因，最常见是 `.env` 的必填项，以及升级后数据库密码与 `.env` 不一致（见“从旧版本升级”）。

**飞书收不到推送**：在「飞书配置」页面点「测试」，再看 `docker compose logs app` 中的错误信息；Webhook 只接受 `https://open.feishu.cn/` 或 `https://open.larksuite.com/` 开头的机器人地址。
