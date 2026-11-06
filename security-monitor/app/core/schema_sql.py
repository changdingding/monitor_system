"""已有库启动时补齐门店健康监控相关表和字段。每条语句单独执行。"""

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sites (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        site_type VARCHAR(20) NOT NULL DEFAULT 'store',
        city VARCHAR(100),
        probe_token VARCHAR(128),
        status VARCHAR(20) DEFAULT 'offline',
        last_seen TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS host_metrics (
        id BIGSERIAL PRIMARY KEY,
        server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
        cpu DOUBLE PRECISION,
        memory DOUBLE PRECISION,
        disk DOUBLE PRECISION,
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS interface_status (
        id BIGSERIAL PRIMARY KEY,
        device_id INTEGER NOT NULL REFERENCES network_devices(id) ON DELETE CASCADE,
        if_index INTEGER,
        if_name VARCHAR(128) NOT NULL,
        admin_status VARCHAR(20),
        oper_status VARCHAR(20),
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
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
    )
    """,
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS site_id INTEGER",
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS cpu DOUBLE PRECISION",
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS memory DOUBLE PRECISION",
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS disk DOUBLE PRECISION",
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS site_id INTEGER",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS cpu DOUBLE PRECISION",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS memory DOUBLE PRECISION",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS disk DOUBLE PRECISION",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP",
    "ALTER TABLE network_devices ADD COLUMN IF NOT EXISTS vendor VARCHAR(32)",
    """
    ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS health_thresholds JSONB
        DEFAULT '{"cpu": 90, "memory": 90, "disk": 85, "offline_seconds": 180}'::jsonb
    """,
    """
    UPDATE system_settings
    SET health_thresholds = '{"cpu": 90, "memory": 90, "disk": 85, "offline_seconds": 180}'::jsonb
    WHERE health_thresholds IS NULL
    """,
    "ALTER TABLE alert_logs ADD COLUMN IF NOT EXISTS site_id INTEGER",
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'alert_logs' AND column_name = 'event_key'
        ) THEN
            ALTER TABLE alert_logs ALTER COLUMN event_key TYPE VARCHAR(255);
        ELSE
            ALTER TABLE alert_logs ADD COLUMN event_key VARCHAR(255);
        END IF;
    END $$
    """,
    """
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
    END $$
    """,
    # 接入 Token 加密存储后，唯一性改由摘要列保证
    "DROP INDEX IF EXISTS idx_sites_probe_token",
    "CREATE INDEX IF NOT EXISTS idx_servers_site ON servers(site_id)",
    "CREATE INDEX IF NOT EXISTS idx_network_devices_site ON network_devices(site_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_network_devices_site_ip ON network_devices(site_id, ip) WHERE site_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_host_metrics_server_time ON host_metrics(server_id, collected_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_interface_status_dev_name ON interface_status(device_id, if_name)",
    "CREATE INDEX IF NOT EXISTS idx_health_alert_state_site ON health_alert_state(site_id) WHERE active",
    "CREATE INDEX IF NOT EXISTS idx_alert_logs_site ON alert_logs(site_id)",
    # 凭据加密后密文较长，放宽列类型（重复执行安全）
    "ALTER TABLE servers ALTER COLUMN password TYPE TEXT",
    # 首次登录 / 管理员重置后必须修改密码
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE",
    # 登录失败锁定
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
    # SSH 主机密钥（首次信任后校验，防中间人）
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS ssh_host_key TEXT",
    "ALTER TABLE network_devices ALTER COLUMN snmp_community TYPE TEXT",
    # 接入 Token：密文 + SHA-256 摘要
    "ALTER TABLE servers ALTER COLUMN agent_token TYPE TEXT",
    "ALTER TABLE servers ADD COLUMN IF NOT EXISTS agent_token_hash VARCHAR(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_agent_token_hash ON servers(agent_token_hash) WHERE agent_token_hash IS NOT NULL",
    "ALTER TABLE sites ALTER COLUMN probe_token TYPE TEXT",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS probe_token_hash VARCHAR(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sites_probe_token_hash ON sites(probe_token_hash) WHERE probe_token_hash IS NOT NULL",
    # 删除服务器 / 网络设备时连带清理其日志（原外键无 ON DELETE，有日志的服务器删不掉）
    """
    DO $$
    DECLARE cname text;
    BEGIN
        SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'login_logs'::regclass AND contype = 'f' AND confrelid = 'servers'::regclass
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'login_logs'::regclass AND attname = 'server_id')]
          AND confdeltype <> 'c';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE login_logs DROP CONSTRAINT %I', cname);
            ALTER TABLE login_logs ADD CONSTRAINT login_logs_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE;
        END IF;
    END $$
    """,
    """
    DO $$
    DECLARE cname text;
    BEGIN
        SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'high_risk_operations'::regclass AND contype = 'f' AND confrelid = 'servers'::regclass
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'high_risk_operations'::regclass AND attname = 'server_id')]
          AND confdeltype <> 'c';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE high_risk_operations DROP CONSTRAINT %I', cname);
            ALTER TABLE high_risk_operations ADD CONSTRAINT high_risk_operations_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE;
        END IF;
    END $$
    """,
    """
    DO $$
    DECLARE cname text;
    BEGIN
        SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'snmp_metrics'::regclass AND contype = 'f' AND confrelid = 'network_devices'::regclass
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'snmp_metrics'::regclass AND attname = 'device_id')]
          AND confdeltype <> 'c';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE snmp_metrics DROP CONSTRAINT %I', cname);
            ALTER TABLE snmp_metrics ADD CONSTRAINT snmp_metrics_device_id_fkey FOREIGN KEY (device_id) REFERENCES network_devices(id) ON DELETE CASCADE;
        END IF;
    END $$
    """,
    """
    DO $$
    DECLARE cname text;
    BEGIN
        SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'alert_records'::regclass AND contype = 'f' AND confrelid = 'servers'::regclass
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'alert_records'::regclass AND attname = 'server_id')]
          AND confdeltype <> 'n';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE alert_records DROP CONSTRAINT %I', cname);
            ALTER TABLE alert_records ADD CONSTRAINT alert_records_server_id_fkey FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE SET NULL;
        END IF;
    END $$
    """,
    """
    DO $$
    DECLARE cname text;
    BEGIN
        SELECT conname INTO cname FROM pg_constraint
        WHERE conrelid = 'alert_records'::regclass AND contype = 'f' AND confrelid = 'network_devices'::regclass
          AND conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = 'alert_records'::regclass AND attname = 'device_id')]
          AND confdeltype <> 'n';
        IF cname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE alert_records DROP CONSTRAINT %I', cname);
            ALTER TABLE alert_records ADD CONSTRAINT alert_records_device_id_fkey FOREIGN KEY (device_id) REFERENCES network_devices(id) ON DELETE SET NULL;
        END IF;
    END $$
    """,
    # 操作审计
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(username, created_at DESC)",
    # AI 日志分析
    """
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
    )
    """,
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_log_analyses_created ON log_analyses(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_log_analyses_server ON log_analyses(server_id, created_at DESC)",
]
