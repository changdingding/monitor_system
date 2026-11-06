# 多源异构安全监控与告警系统

企业级安全监控方案：服务器 **Agent 上报 / SSH 拉取**、网络设备 SNMP 巡检、**飞书告警**、Web 可视化管理。

---

## 文档入口（请先看这里）

| 文档 | 适合谁 | 内容 |
|------|--------|------|
| **[QUICKSTART.md](QUICKSTART.md)** | 部署 / 测试 | Docker 部署、部署后测试清单、从旧版本升级、备份 |
| **[docs/Windows部署指南-小白版.md](docs/Windows部署指南-小白版.md)** | 实施/运维小白 | 在 Windows / Windows Server 上从零部署管理端 + 安装 Agent |
| **[docs/项目说明书.md](docs/项目说明书.md)** | 全员 / 开发 | 技术栈、架构、模块、端口、API、运维说明 |

---

## 一分钟了解

- **管理端**：Docker 一键启动（Web + API + PostgreSQL + Redis）
- **被监控机**：安装 Agent（推荐）或使用 SSH 拉取
- **告警**：飞书机器人卡片推送（可自定义模板）

默认访问：

- 管理界面：http://服务器IP:3000  
- API：http://服务器IP:8000  
- 初始账号：`admin` / `admin123`（首次登录会强制修改密码）

---

## 技术栈（摘要）

**后端**：Python 3.10 · FastAPI · PostgreSQL 15 · Redis 7 · APScheduler · Paramiko  

**前端**：React 18 · TypeScript · Vite · Ant Design 5  

**客户端**：Windows PowerShell Agent · Linux Python Agent  

**部署**：Docker Compose · Nginx  

完整说明见 [docs/项目说明书.md](docs/项目说明书.md)。

---

## 快速启动（已熟悉 Docker）

```powershell
cd security-monitor
copy .env.example .env
# 编辑 .env：必须填写 DB_PASSWORD、JWT_SECRET、CREDENTIAL_KEY（生成方法与测试清单见 QUICKSTART.md）
docker compose up -d --build
```

浏览器打开 http://localhost:3000 。

Windows 小白请严格按 [部署指南](docs/Windows部署指南-小白版.md) 操作。

---

## 功能概览

- 服务器登录/退出告警（会话去重，不刷屏）
- 高危操作：创建用户、特权组、服务/计划任务、清日志等
- Agent 预配置安装包（双击下一步安装）
- SSH 拉取备选方案
- 飞书模板自定义、告警历史、用户权限
- 安全加固：凭据加密存储、登录失败锁定、SSH 主机密钥校验、操作审计
- **AI 日志分析**：SSH 拉取服务器系统日志（或直接粘贴日志）→ 自动脱敏 → 大模型分析 → 列出问题、原因、解决方案与排查命令
- **站点分支管理**：总部 / 分支（区域）/ 门店 / 云端 四级站点树，总部网络设备可由平台直接轮询或部署采集器
- **门店数据权限**：用户可授权到指定门店或分支（含全部下级），只能看到、操作范围内的设备、服务器、告警和大屏数据
- **采集器集中配置**：设备清单、团体字、自动发现网段在网页统一配置；采集器 Windows 图形安装向导 / Linux systemd 一键安装，现场只填总部地址和 Token
- **系统启用**：实施期可按类别清理测试数据；正式启用后不可清除、不可撤销，全部操作记入操作审计
- **态势大屏**：站点连接拓扑、综合健康指数、AI 智能研判、实时告警、健康排行等，全部来自实时数据，可点击下钻跳转

---

## 站点、权限与采集器

1. **建站点树**：「站点 / 分支」中先建总部，再建分支（区域），门店挂在分支下。总部机房与平台在同一内网时，采集方式选「总部平台直接轮询」，无需采集器。
2. **配置设备**：编辑站点设置默认 SNMP 团体字，开启「自动发现」并填写网段（如 `192.168.1.0/24`，单个不大于 /22）；或在「网络设备 → 批量导入」中粘贴整店清单（`名称,IP,类型,团体字,端口`，可直接从 Excel 复制）。
3. **部署采集器**：站点行点「采集器」下载安装包，解压到站内常开电脑（需 Python 3.8+）：
   - Windows：双击「安装采集器.bat」→ 向导里可修改总部地址 / Token、测试连接 → 安装（开机自启）；以后改参数运行安装目录的「修改配置.bat」
   - Linux：`sudo sh linux/install.sh`（改参数加 `--config`，卸载加 `--uninstall`）
   - 采集器每轮从 `GET /api/v1/probe/config` 拉取设备清单，新增设备无需到现场
4. **分配权限**：「用户与权限」中角色决定能做什么（管理员 / 操作员 / 只读），数据范围决定能看哪些站点（全部，或指定门店 / 分支）。
5. **正式启用**：实施完成后在「系统设置 → 系统启用」清理测试数据，再点「正式启用」。启用后清理功能永久关闭，AI 分析记录也不能删除；清理与启用操作本身都记录在「操作审计」中，审计日志任何时候都不会被清理。

---

## AI 日志分析

1. 管理员进入 **AI 配置**：选择模型服务（Claude 官方 / DeepSeek / 通义千问 / Kimi / 智谱 / 本地 Ollama），填写 API Key，点「保存并测试」，打开「启用」。
2. 进入 **AI 日志分析**：选服务器和日志类型 → 可先「预览日志」→ 点「AI 分析」，结果自动出现在右侧，并保存在分析历史中。

| 日志类型 | Linux | Windows |
|----------|-------|---------|
| 综合巡检（推荐） | 磁盘 / 内存 / Top 进程 / 失败服务 + 系统与内核警告 | 磁盘 / 内存 / 未运行的自动服务 + System、Application 错误 |
| 系统日志 | journalctl 警告以上 / syslog / messages | System 日志错误与警告 |
| 认证 / 安全日志 | auth.log / secure | 登录失败、账户锁定、新建用户、清空日志等 |
| 内核日志 | journalctl -k / dmesg | — |
| 指定服务 / 指定文件 | journalctl -u 服务名；/var/log 下或 .log 结尾的文件 | — |
| 应用程序日志 | — | Application 日志错误与警告 |

说明：

- 远程拉取走 SSH，需要在服务器管理里配置登录密码；仅装了 Agent 的服务器请用「粘贴日志」。
- 发送前会隐藏密码、Token、API Key、私钥、JWT 等，并把相似日志行折叠计数；超过「单次最多发送字符数」时只保留最新部分并告知 AI。
- 服务名 / 文件路径做白名单校验，不能借此读取 `/etc/shadow` 等任意文件或注入命令。
- API Key 加密存储（与 SSH 密码同一把 `CREDENTIAL_KEY`），接口不返回明文；分析记录按「日志保留天数」自动清理。
- 权限：管理员配置 AI；管理员 / 运维可发起和删除分析；只读用户可查看结果。
- 日志不能出内网时，选择「本地 Ollama」：在宿主机运行 Ollama，地址填 `http://host.docker.internal:11434/v1`。

---

## 开发自检

```bash
# 需要本地 PostgreSQL（默认 postgres/testpass@localhost:5432，见 tests/conftest.py）
pip install -r requirements-dev.txt
bash scripts/check.sh            # 后端 pytest + 前端 tsc/构建；也可只跑 backend / frontend
```

推送到 GitHub 后，`.github/workflows/ci.yml` 会在每次 push / PR 自动执行同样的检查。

## 目录结构（简）

```text
app/          后端 API、采集、规则引擎、飞书
web/          前端管理台
agent/        客户端安装包源文件
probe/        边缘采集器（含 Windows 安装向导、Linux 安装脚本）
docs/         部署与说明书
docker-compose.yml
```

---

## 许可证与注意

请在生产环境修改默认密码，并为公网访问配置 HTTPS/防火墙策略。

安全相关说明：

- `DB_PASSWORD`、`JWT_SECRET`、`CREDENTIAL_KEY` 为必填环境变量；`CREDENTIAL_KEY` 设置后不要更换（用于加密 SSH 密码 / SNMP 团体字 / Agent Token）。
- 用户角色：`admin` 全部权限；`operator` 可管理服务器/设备/门店与处理告警；`viewer` 只读。非管理员可再限定数据范围到指定门店 / 分支。
- 采集器集中配置会把团体字下发给站点采集器（凭站点 Token 鉴权），公网部署请为管理端配置 HTTPS。
- PostgreSQL / Redis 默认不再映射到宿主机端口。
