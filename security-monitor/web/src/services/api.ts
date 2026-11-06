import axios from 'axios';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

// 请求拦截器 - 添加 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 后端参数校验失败（422）时 detail 是数组，统一转成可读字符串，页面可直接 message.error(detail)
const normalizeDetail = (detail: unknown): unknown => {
  if (!Array.isArray(detail)) return detail;
  return detail
    .map((item: { msg?: string }) => String(item?.msg ?? '').replace(/^Value error, /, ''))
    .filter(Boolean)
    .join('；') || '提交的数据格式不正确';
};

// 响应拦截器 - 处理 401 / 403 / 422
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.data?.detail !== undefined) {
      error.response.data.detail = normalizeDetail(error.response.data.detail);
    }
    if (error.response?.status === 403 && !error.response.data?.detail) {
      error.response.data = { ...(error.response.data || {}), detail: '权限不足' };
    }
    // 登录接口的 401 是“用户名或密码错误”，交给登录页提示，不要整页跳转
    if (error.response?.status === 401 && !String(error.config?.url || '').includes('/auth/login')) {
      localStorage.removeItem('token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// 认证 API
export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
  // token 可选：强制改密时使用登录返回的受限 token（尚未写入 localStorage）
  changePassword: (oldPassword: string, newPassword: string, token?: string) =>
    api.post(
      '/auth/change-password',
      { old_password: oldPassword, new_password: newPassword },
      token ? { headers: { Authorization: `Bearer ${token}` } } : undefined,
    ),
};

// 服务器 API
export const serverApi = {
  list: () => api.get('/servers'),
  get: (id: number) => api.get(`/servers/${id}`),
  create: (data: any) => api.post('/servers', data),
  update: (id: number, data: any) => api.put(`/servers/${id}`, data),
  delete: (id: number) => api.delete(`/servers/${id}`),
  test: (id: number) => api.post(`/servers/${id}/test`),
  collect: (id: number) => api.post(`/servers/${id}/collect`),
  agentInstall: (id: number) => api.get(`/servers/${id}/agent-install`),
  resetAgentToken: (id: number) => api.post(`/servers/${id}/reset-agent-token`),
  resetHostKey: (id: number) => api.post(`/servers/${id}/reset-host-key`),
  downloadAgentPackage: (id: number, os: 'windows' | 'linux', serverUrl: string) =>
    api.get(`/servers/${id}/agent-package`, {
      params: { os, server_url: serverUrl },
      responseType: 'blob',
    }),
};

// 网络设备 API
export const siteApi = {
  list: () => api.get('/sites'),
  create: (data: any) => api.post('/sites', data),
  update: (id: number, data: any) => api.put(`/sites/${id}`, data),
  delete: (id: number) => api.delete(`/sites/${id}`),
  resetToken: (id: number) => api.post(`/sites/${id}/reset-probe-token`),
  downloadPackage: (id: number, serverUrl: string) =>
    api.get(`/sites/${id}/probe-package`, {
      params: { server_url: serverUrl },
      responseType: 'blob',
    }),
};

export const networkDeviceApi = {
  list: () => api.get('/network-devices'),
  get: (id: number) => api.get(`/network-devices/${id}`),
  create: (data: any) => api.post('/network-devices', data),
  update: (id: number, data: any) => api.put(`/network-devices/${id}`, data),
  delete: (id: number) => api.delete(`/network-devices/${id}`),
};

// 告警 API
export const alertApi = {
  list: (params?: { page?: number; size?: number; level?: string; status?: string }) =>
    api.get('/alerts', { params }),
  get: (id: number) => api.get(`/alerts/${id}`),
  stats: () => api.get('/alerts/stats'),
  updateStatus: (id: number, status: string) => api.patch(`/alerts/${id}/status`, { status }),
};

// 飞书配置 API
export const feishuApi = {
  getConfig: () => api.get('/feishu/config'),
  updateConfig: (data: any) => api.put('/feishu/config', data),
  testWebhook: () => api.post('/feishu/test'),
};

// 用户 API
export const userApi = {
  list: () => api.get('/users'),
  get: (id: number) => api.get(`/users/${id}`),
  create: (data: any) => api.post('/users', data),
  update: (id: number, data: any) => api.put(`/users/${id}`, data),
  delete: (id: number) => api.delete(`/users/${id}`),
  resetPassword: (id: number, newPassword: string) =>
    api.post(`/users/${id}/reset-password`, { new_password: newPassword }),
  unlock: (id: number) => api.post(`/users/${id}/unlock`),
};

// 操作审计 API（仅管理员）
export const auditApi = {
  list: (params: {
    page?: number; size?: number; username?: string; action?: string;
    success?: boolean; start?: string; end?: string;
  }) => api.get('/audit-logs', { params }),
  actions: () => api.get('/audit-logs/actions'),
};

// 系统设置 API
export const settingsApi = {
  get: () => api.get('/settings'),
  update: (data: any) => api.put('/settings', data),
};

// AI 配置 API（config / test 仅管理员；status 所有登录用户）
export const aiApi = {
  getConfig: () => api.get('/ai/config'),
  updateConfig: (data: any) => api.put('/ai/config', data),
  // AI 调用可能较慢，单独放宽超时
  test: () => api.post('/ai/test', undefined, { timeout: 600000 }),
  status: () => api.get('/ai/status'),
};

// AI 日志分析 API
export interface LogAnalysisParams {
  source: 'ssh' | 'manual';
  server_id?: number;
  log_type?: string;
  lines?: number;
  since_minutes?: number;
  unit?: string;
  path?: string;
  content?: string;
  note?: string;
}

export const logAnalysisApi = {
  logTypes: (osType: string) => api.get('/log-analysis/log-types', { params: { os_type: osType } }),
  fetch: (data: LogAnalysisParams) => api.post('/log-analysis/fetch', data, { timeout: 120000 }),
  create: (data: LogAnalysisParams) => api.post('/log-analysis', data),
  list: (params?: { page?: number; size?: number; server_id?: number }) => api.get('/log-analysis', { params }),
  get: (id: number) => api.get(`/log-analysis/${id}`),
  delete: (id: number) => api.delete(`/log-analysis/${id}`),
};

// 仪表盘 API
export const dashboardApi = {
  overview: () => api.get('/dashboard/overview'),
  alertTrend: (days: number = 7, siteId?: number) =>
    api.get('/dashboard/alert-trend', { params: { days, site_id: siteId } }),
  recentAlerts: (limit: number = 10) => api.get('/dashboard/recent-alerts', { params: { limit } }),
  sites: () => api.get('/dashboard/sites'),
  siteDetail: (id: number) => api.get(`/dashboard/sites/${id}`),
  serverMetrics: (id: number, hours: number = 24) =>
    api.get(`/dashboard/servers/${id}/metrics`, { params: { hours } }),
};

export default api;
