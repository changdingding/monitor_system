// 服务器类型
export interface Server {
  id: number;
  name: string;
  ip: string;
  os_type: 'windows' | 'linux';
  port?: number;
  username?: string;
  status: 'online' | 'offline' | 'error';
  last_heartbeat?: string;
  site_id?: number | null;
  site_name?: string;
  cpu?: number | null;
  memory?: number | null;
  disk?: number | null;
  last_seen?: string;
  created_at: string;
  updated_at: string;
}

// 网络设备类型
export interface NetworkDevice {
  id: number;
  name: string;
  ip: string;
  device_type: 'switch' | 'router' | 'firewall' | 'other';
  snmp_version: 'v2c' | 'v3';
  snmp_community?: string;
  snmp_port: number;
  syslog_enabled: boolean;
  syslog_port?: number;
  status: 'online' | 'offline' | 'error';
  site_id?: number | null;
  site_name?: string;
  cpu?: number | null;
  memory?: number | null;
  last_seen?: string;
  vendor?: string;
  created_at: string;
  updated_at: string;
}

// 告警类型
export interface Alert {
  id: number;
  source_type: 'server' | 'network';
  source_id: number;
  source_name: string;
  level: 'critical' | 'high' | 'medium' | 'low';
  event_type: string;
  event_description: string;
  raw_data?: string;
  status: 'new' | 'acknowledged' | 'resolved' | 'ignored';
  notified: boolean;
  created_at: string;
  resolved_at?: string;
}

// 告警统计
export interface AlertStats {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  new_count: number;
  resolved_count: number;
}

// 飞书配置
export interface FeishuConfig {
  id: number;
  webhook_url: string;
  secret?: string;
  enabled: boolean;
  notify_levels: string[];
  debounce_seconds: number;
  created_at: string;
  updated_at: string;
}

// 用户类型
export interface User {
  id: number;
  username: string;
  nickname: string;
  email?: string;
  role: 'admin' | 'operator' | 'viewer';
  status: 'active' | 'disabled';
  last_login?: string;
  locked_until?: string | null;  // 非空表示当前处于登录锁定
  created_at: string;
  updated_at: string;
}

// 操作审计
export interface AuditLog {
  id: number;
  user_id?: number | null;
  username?: string | null;
  action: string;
  method: string;
  path: string;
  target_type?: string | null;
  target_id?: number | null;
  target_name?: string | null;
  status_code: number;
  success: boolean;
  ip?: string | null;
  forwarded_for?: string | null;
  detail?: Record<string, unknown> | null;
  created_at: string;
}

// 系统设置
export interface SystemSettings {
  log_retention_days: number;
  alert_debounce_seconds: number;
  collector_interval_seconds: number;
  monitor_interval_seconds: number;
  cpu_threshold?: number;
  memory_threshold?: number;
  disk_threshold?: number;
  offline_seconds?: number;
}

export interface Site {
  id: number;
  name: string;
  site_type: 'store' | 'cloud';
  city?: string;
  probe_token?: string;
  status: 'online' | 'offline';
  last_seen?: string;
  created_at: string;
  updated_at: string;
}

export interface DashboardSite {
  id: number;
  name: string;
  site_type: 'store' | 'cloud';
  city?: string;
  status: string;
  last_seen?: string;
  server_total: number;
  server_online: number;
  device_total: number;
  device_online: number;
  max_cpu?: number | null;
  max_disk?: number | null;
  active_alerts: number;
  health_score?: number;
  health_level?: 'good' | 'warn' | 'danger';
}

// 仪表盘概览
export interface DashboardOverview {
  total_servers: number;
  online_servers: number;
  total_devices: number;
  online_devices: number;
  total_alerts_today: number;
  critical_alerts_today: number;
  unresolved_alerts: number;
  total_sites?: number;
  store_sites?: number;
  online_probes?: number;
  active_health_alerts?: number;
}

// 告警趋势
export interface AlertTrend {
  date: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

// 登录响应
export interface LoginResponse {
  token: string;
  user: User;
  must_change_password?: boolean;
}
