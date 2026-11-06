-- ============================================
-- 数据库初始化脚本
-- ============================================

-- 告警规则表
CREATE TABLE IF NOT EXISTS alert_rules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    rule_type VARCHAR(50) NOT NULL,  -- 'log', 'snmp', 'syslog'
    severity VARCHAR(20) NOT NULL DEFAULT 'medium',  -- 'low', 'medium', 'high', 'critical'
    enabled BOOLEAN DEFAULT TRUE,
    config JSONB,  -- 规则配置
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 服务器表
CREATE TABLE IF NOT EXISTS servers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    ip VARCHAR(255) NOT NULL,
    os_type VARCHAR(20) NOT NULL,  -- 'windows', 'linux'
    port INTEGER DEFAULT 22,
    username VARCHAR(100),
    password TEXT,  -- 加密存储（enc:v1:...）
    ssh_host_key TEXT,  -- 首次连接记录的 SSH 主机公钥（"类型 base64"）
    status VARCHAR(20) DEFAULT 'offline',  -- 'online', 'offline', 'error'
    last_heartbeat TIMESTAMP,
    agent_token TEXT,  -- 加密存储（enc:v1:...），仅用于管理端展示 / 打包
    agent_token_hash VARCHAR(64),  -- SHA-256，用于 Agent 鉴权查找
    agent_last_seen TIMESTAMP,
    collect_mode VARCHAR(20) DEFAULT 'agent',  -- agent | ssh | both
    site_id INTEGER,
    cpu DOUBLE PRECISION,
    memory DOUBLE PRECISION,
    disk DOUBLE PRECISION,
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 网络设备表
CREATE TABLE IF NOT EXISTS network_devices (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    ip VARCHAR(255) NOT NULL,
    device_type VARCHAR(50),  -- 'switch', 'router', 'firewall', 'other'
    snmp_version VARCHAR(10) DEFAULT 'v2c',  -- 'v2c', 'v3'
    snmp_community TEXT,  -- 加密存储（enc:v1:...）
    snmp_port INTEGER DEFAULT 161,
    syslog_enabled BOOLEAN DEFAULT FALSE,
    syslog_port INTEGER DEFAULT 514,
    status VARCHAR(20) DEFAULT 'offline',  -- 'online', 'offline', 'error'
    site_id INTEGER,
    cpu DOUBLE PRECISION,
    memory DOUBLE PRECISION,
    disk DOUBLE PRECISION,
    last_seen TIMESTAMP,
    vendor VARCHAR(32),
    source VARCHAR(20) DEFAULT 'manual',  -- manual | import | discovered | probe
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 登录日志表
CREATE TABLE IF NOT EXISTS login_logs (
    id BIGSERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    account VARCHAR(100) NOT NULL,
    login_time TIMESTAMP NOT NULL,
    source_ip VARCHAR(50),
    os_type VARCHAR(20) NOT NULL,
    raw_line TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 高危操作日志表
CREATE TABLE IF NOT EXISTS high_risk_operations (
    id BIGSERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    operation_type VARCHAR(50) NOT NULL,
    risk_level VARCHAR(20) NOT NULL DEFAULT 'medium',
    account VARCHAR(100) NOT NULL,
    operation_time TIMESTAMP NOT NULL,
    description TEXT NOT NULL,
    details TEXT,
    hostname VARCHAR(255),
    os_type VARCHAR(20) NOT NULL,
    raw_line TEXT,
    alerted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Syslog 日志表
CREATE TABLE IF NOT EXISTS syslog_logs (
    id BIGSERIAL PRIMARY KEY,
    source_ip VARCHAR(50) NOT NULL,
    facility VARCHAR(50),
    severity VARCHAR(20),
    message TEXT NOT NULL,
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- SNMP 监控数据表
CREATE TABLE IF NOT EXISTS snmp_metrics (
    id BIGSERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES network_devices(id) ON DELETE CASCADE,
    metric_type VARCHAR(50) NOT NULL,  -- 'cpu', 'memory', 'temperature', 'port_status'
    metric_value FLOAT NOT NULL,
    metric_unit VARCHAR(20),
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 告警记录表
CREATE TABLE IF NOT EXISTS alert_records (
    id BIGSERIAL PRIMARY KEY,
    rule_id INTEGER REFERENCES alert_rules(id),
    server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
    device_id INTEGER REFERENCES network_devices(id) ON DELETE SET NULL,
    severity VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    notified BOOLEAN DEFAULT FALSE,
    notified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX idx_login_logs_server ON login_logs(server_id);
CREATE INDEX idx_login_logs_time ON login_logs(login_time DESC);
CREATE INDEX idx_high_risk_ops_server ON high_risk_operations(server_id);
CREATE INDEX idx_high_risk_ops_time ON high_risk_operations(operation_time DESC);
CREATE INDEX idx_high_risk_ops_risk ON high_risk_operations(risk_level);
CREATE INDEX idx_syslog_source ON syslog_logs(source_ip);
CREATE INDEX idx_syslog_time ON syslog_logs(received_at DESC);
CREATE INDEX idx_snmp_metrics_device ON snmp_metrics(device_id);
CREATE INDEX idx_snmp_metrics_time ON snmp_metrics(collected_at DESC);
CREATE INDEX idx_alert_records_time ON alert_records(created_at DESC);
CREATE INDEX idx_alert_records_severity ON alert_records(severity);

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    nickname VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',  -- 'admin', 'operator', 'viewer'
    data_scope VARCHAR(10) NOT NULL DEFAULT 'all',  -- all 全部站点 | sites 仅 user_sites 中的站点及其下级
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- 'active', 'disabled'
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,  -- 首次登录 / 重置后需改密
    failed_login_count INTEGER NOT NULL DEFAULT 0,  -- 连续登录失败次数
    token_version INTEGER NOT NULL DEFAULT 0,  -- 改密 / 重置后递增，使已签发的 token 失效
    locked_until TIMESTAMP,  -- 登录锁定截止时间
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 飞书配置表
CREATE TABLE IF NOT EXISTS feishu_config (
    id SERIAL PRIMARY KEY,
    webhook_url TEXT NOT NULL,
    secret VARCHAR(255),
    enabled BOOLEAN DEFAULT FALSE,
    notify_levels JSONB DEFAULT '["critical", "high", "medium"]',
    debounce_seconds INTEGER DEFAULT 60,
    notify_login BOOLEAN DEFAULT TRUE,
    notify_high_risk BOOLEAN DEFAULT TRUE,
    notify_network BOOLEAN DEFAULT TRUE,
    append_details BOOLEAN DEFAULT FALSE,
    template_login_title TEXT,
    template_login_content TEXT,
    template_high_risk_title TEXT,
    template_high_risk_content TEXT,
    template_network_title TEXT,
    template_network_content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 系统设置表
CREATE TABLE IF NOT EXISTS system_settings (
    id SERIAL PRIMARY KEY,
    log_retention_days INTEGER DEFAULT 30,
    alert_debounce_seconds INTEGER DEFAULT 60,
    collector_interval_seconds INTEGER DEFAULT 300,
    monitor_interval_seconds INTEGER DEFAULT 60,
    health_thresholds JSONB DEFAULT '{"cpu": 90, "memory": 90, "disk": 85, "offline_seconds": 180}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 告警日志表（统一告警记录）
CREATE TABLE IF NOT EXISTS alert_logs (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(20) NOT NULL,  -- 'server', 'network'
    source_id INTEGER NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    level VARCHAR(20) NOT NULL,  -- 'critical', 'high', 'medium', 'low'
    event_type VARCHAR(100) NOT NULL,
    event_description TEXT NOT NULL,
    raw_data TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'new',  -- 'new', 'acknowledged', 'resolved', 'ignored'
    notified BOOLEAN DEFAULT FALSE,
    event_key VARCHAR(255),  -- 事件指纹，用于去重（同一事件只告警一次）
    site_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

-- 创建索引
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_alert_logs_time ON alert_logs(created_at DESC);
CREATE INDEX idx_alert_logs_level ON alert_logs(level);
CREATE INDEX idx_alert_logs_status ON alert_logs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_logs_event_key ON alert_logs(event_key) WHERE event_key IS NOT NULL;

-- 服务器账号会话状态（登录推一次 / 退出推一次，保持登录不重复推）
CREATE TABLE IF NOT EXISTS server_account_sessions (
    server_id INTEGER NOT NULL,
    account VARCHAR(100) NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'logged_out',  -- logged_in / logged_out
    last_event_time TIMESTAMP,
    last_source_ip VARCHAR(50),
    transition_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server_id, account)
);

-- 插入默认管理员用户 (密码: admin123)
INSERT INTO users (username, password_hash, nickname, email, role, status)
VALUES ('admin', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', '系统管理员', 'admin@example.com', 'admin', 'active')
ON CONFLICT (username) DO NOTHING;

-- 插入默认系统设置
INSERT INTO system_settings (log_retention_days, alert_debounce_seconds, collector_interval_seconds, monitor_interval_seconds)
VALUES (30, 60, 300, 60)
ON CONFLICT DO NOTHING;

-- ============================================
-- 门店 / 云端健康监控（已有库由应用启动时再补一遍）
-- ============================================

CREATE TABLE IF NOT EXISTS sites (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    site_type VARCHAR(20) NOT NULL DEFAULT 'store',  -- hq 总部 | branch 分支/区域 | store 门店 | cloud 云端
    parent_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,  -- 上级站点（分支管理）
    code VARCHAR(50),
    city VARCHAR(100),
    address VARCHAR(255),
    contact VARCHAR(100),
    phone VARCHAR(50),
    snmp_community TEXT,  -- 站点默认团体字（加密），下发给采集器
    scan_enabled BOOLEAN NOT NULL DEFAULT FALSE,  -- 采集器按网段自动发现设备
    scan_cidrs TEXT,
    probe_interval_seconds INTEGER NOT NULL DEFAULT 60,
    probe_version VARCHAR(32),
    probe_hostname VARCHAR(255),
    collect_mode VARCHAR(10) NOT NULL DEFAULT 'probe',  -- probe 站内采集器上报 | central 总部平台直接轮询
    probe_token TEXT,  -- 加密存储（enc:v1:...）
    probe_token_hash VARCHAR(64),  -- SHA-256，用于采集器鉴权查找
    status VARCHAR(20) DEFAULT 'offline',  -- online | offline
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS host_metrics (
    id BIGSERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    cpu DOUBLE PRECISION,
    memory DOUBLE PRECISION,
    disk DOUBLE PRECISION,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interface_status (
    id BIGSERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES network_devices(id) ON DELETE CASCADE,
    if_index INTEGER,
    if_name VARCHAR(128) NOT NULL,
    admin_status VARCHAR(20),
    oper_status VARCHAR(20),
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS health_alert_state (
    event_key VARCHAR(255) PRIMARY KEY,
    site_id INTEGER,
    source_type VARCHAR(20) NOT NULL,
    source_id INTEGER NOT NULL,
    alert_type VARCHAR(80) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    severity VARCHAR(20),
    message TEXT,
    alert_log_id BIGINT,
    opened_at TIMESTAMP,
    resolved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE servers ADD COLUMN IF NOT EXISTS site_id INTEGER;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS cpu DOUBLE PRECISION;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS memory DOUBLE PRECISION;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS disk DOUBLE PRECISION;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;

ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS site_id INTEGER;
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS cpu DOUBLE PRECISION;
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS memory DOUBLE PRECISION;
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS disk DOUBLE PRECISION;
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS vendor VARCHAR(32);

ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS health_thresholds JSONB
    DEFAULT '{"cpu": 90, "memory": 90, "disk": 85, "offline_seconds": 180}'::jsonb;
UPDATE system_settings
SET health_thresholds = '{"cpu": 90, "memory": 90, "disk": 85, "offline_seconds": 180}'::jsonb
WHERE health_thresholds IS NULL;

ALTER TABLE alert_logs ADD COLUMN IF NOT EXISTS site_id INTEGER;
ALTER TABLE alert_logs ALTER COLUMN event_key TYPE VARCHAR(255);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'servers_site_id_fkey') THEN
        ALTER TABLE servers
            ADD CONSTRAINT servers_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'network_devices_site_id_fkey') THEN
        ALTER TABLE network_devices
            ADD CONSTRAINT network_devices_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_probe_token_hash ON sites(probe_token_hash) WHERE probe_token_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_agent_token_hash ON servers(agent_token_hash) WHERE agent_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_servers_site ON servers(site_id);
CREATE INDEX IF NOT EXISTS idx_network_devices_site ON network_devices(site_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_network_devices_site_ip ON network_devices(site_id, ip) WHERE site_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_host_metrics_server_time ON host_metrics(server_id, collected_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_interface_status_dev_name ON interface_status(device_id, if_name);
CREATE INDEX IF NOT EXISTS idx_health_alert_state_site ON health_alert_state(site_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_alert_logs_site ON alert_logs(site_id);

-- 操作审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER,  -- 操作人（不设外键：删除用户后记录仍保留）
    username VARCHAR(100),
    action VARCHAR(100) NOT NULL,
    method VARCHAR(10) NOT NULL,
    path VARCHAR(255) NOT NULL,
    target_type VARCHAR(50),
    target_id BIGINT,
    target_name VARCHAR(255),
    status_code INTEGER NOT NULL,
    success BOOLEAN NOT NULL,
    ip VARCHAR(64),
    forwarded_for VARCHAR(255),  -- 仅供参考，可被客户端伪造
    detail JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(username, created_at DESC);

-- AI 配置（单行）
CREATE TABLE IF NOT EXISTS ai_config (
    id SERIAL PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    provider VARCHAR(20) NOT NULL DEFAULT 'anthropic',  -- anthropic | openai（OpenAI 兼容接口：DeepSeek / 通义千问 / Ollama 等）
    base_url TEXT,
    api_key TEXT,  -- 加密存储（enc:v1:）
    model VARCHAR(100) NOT NULL DEFAULT 'claude-opus-5-5',
    max_input_chars INTEGER NOT NULL DEFAULT 60000,
    timeout_seconds INTEGER NOT NULL DEFAULT 180,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- AI 日志分析记录
CREATE TABLE IF NOT EXISTS log_analyses (
    id BIGSERIAL PRIMARY KEY,
    server_id INTEGER REFERENCES servers(id) ON DELETE SET NULL,
    server_name VARCHAR(255),
    source VARCHAR(20) NOT NULL,  -- ssh | manual
    log_type VARCHAR(30) NOT NULL,
    params JSONB,
    log_excerpt TEXT,  -- 脱敏后实际发送给 AI 的日志
    log_stats JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'running',  -- running | success | failed
    summary TEXT,
    health_score INTEGER,
    risk_level VARCHAR(20),
    issues JSONB,
    recommendations JSONB,
    provider VARCHAR(20),
    model VARCHAR(100),
    usage JSONB,
    error TEXT,
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_log_analyses_created ON log_analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_log_analyses_server ON log_analyses(server_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sites_parent ON sites(parent_id);

-- 用户可访问的站点（data_scope = 'sites' 时生效，含下级站点）
CREATE TABLE IF NOT EXISTS user_sites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, site_id)
);

-- 系统启用状态（单行）：启用前可清理实施期数据，启用后不可清除
CREATE TABLE IF NOT EXISTS system_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    activated BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at TIMESTAMP,
    activated_by VARCHAR(100),
    note TEXT
);
INSERT INTO system_state (id, activated) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING;
