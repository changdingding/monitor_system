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
  device_type: 'switch' | 'router' | 'firewall' | 'ap' | 'other';
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
  source?: 'manual' | 'import' | 'discovered' | 'probe';
  site_type?: SiteType;
  if_up?: number;
  if_total?: number;
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
  data_scope?: 'all' | 'sites';
  site_ids?: number[];
  site_names?: string[];
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

export type SiteType = 'hq' | 'branch' | 'store' | 'cloud';

export const SITE_TYPE_LABELS: Record<SiteType, string> = {
  hq: '总部', branch: '分支', store: '门店', cloud: '云端',
};

export interface Site {
  id: number;
  name: string;
  site_type: SiteType;
  parent_id?: number | null;
  parent_name?: string | null;
  code?: string | null;
  city?: string;
  address?: string | null;
  contact?: string | null;
  phone?: string | null;
  probe_token?: string;
  status: 'online' | 'offline';
  last_seen?: string;
  probe_version?: string | null;
  probe_hostname?: string | null;
  collect_mode?: 'probe' | 'central';
  scan_enabled?: boolean;
  scan_cidrs?: string | null;
  probe_interval_seconds?: number;
  has_community?: boolean;
  device_count?: number;
  server_count?: number;
  created_at: string;
  updated_at: string;
}

export interface DashboardSite {
  id: number;
  name: string;
  site_type: SiteType;
  parent_id?: number | null;
  code?: string | null;
  collect_mode?: 'probe' | 'central';
  probe_down?: boolean;
  probe_version?: string | null;
  if_down?: number;
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
  error_devices?: number;
  interfaces_total?: number;
  interfaces_up?: number;
  interfaces_down?: number;
  alerts_24h?: number;
  alerts_prev_24h?: number;
  hq_sites?: number;
  branch_sites?: number;
  cloud_sites?: number;
  probe_sites?: number;
}

export interface Insight {
  level: 'danger' | 'warn' | 'info' | 'good';
  title: string;
  detail: string;
  link?: string | null;
}

export interface Insights {
  score: number;
  level: 'good' | 'warn' | 'danger';
  summary: string;
  items: Insight[];
}

export interface NameValue {
  name: string;
  value: number;
  online?: number;
}

export interface Distribution {
  alert_levels: NameValue[];
  event_types: NameValue[];
  device_types: NameValue[];
  vendors: NameValue[];
  top_servers: { id: number; name: string; cpu?: number; memory?: number; disk?: number; site_name?: string }[];
  top_devices: { id: number; name: string; ip: string; cpu?: number; memory?: number; site_name?: string }[];
}

export interface Me {
  id: number;
  username: string;
  nickname: string;
  role: 'admin' | 'operator' | 'viewer';
  data_scope: 'all' | 'sites';
  scope_sites: { id: number; name: string; site_type: SiteType }[];
  scope_site_count: number | null;
  system_activated: boolean;
}

export interface SystemState {
  activated: boolean;
  activated_at?: string | null;
  activated_by?: string | null;
  note?: string | null;
  audit_log_count?: number;
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
