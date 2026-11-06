import { useState, useEffect } from 'react';
import { Row, Col, Card, Statistic, Table, Tag, Progress, Spin, Button, Drawer, Modal } from 'antd';
import {
  CloudServerOutlined,
  GlobalOutlined,
  AlertOutlined,
  WarningOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ShopOutlined,
} from '@ant-design/icons';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { dashboardApi } from '../services/api';
import type { DashboardOverview, AlertTrend, Alert, DashboardSite } from '../types';

export default function Dashboard() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [trend, setTrend] = useState<AlertTrend[]>([]);
  const [recentAlerts, setRecentAlerts] = useState<Alert[]>([]);
  const [sites, setSites] = useState<DashboardSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [siteTrend, setSiteTrend] = useState<AlertTrend[]>([]);
  const [metricOpen, setMetricOpen] = useState(false);
  const [metricTitle, setMetricTitle] = useState('');
  const [metricSeries, setMetricSeries] = useState<any[]>([]);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [overviewRes, trendRes, alertsRes, sitesRes] = await Promise.all([
        dashboardApi.overview(),
        dashboardApi.alertTrend(7),
        dashboardApi.recentAlerts(10),
        dashboardApi.sites(),
      ]);
      setOverview(overviewRes.data);
      setTrend(trendRes.data);
      setRecentAlerts(alertsRes.data);
      setSites(sitesRes.data || []);
    } catch (error) {
      // 如果 API 不可用，使用模拟数据展示
      setOverview({
        total_servers: 0,
        online_servers: 0,
        total_devices: 0,
        online_devices: 0,
        total_alerts_today: 0,
        critical_alerts_today: 0,
        unresolved_alerts: 0,
      });
      setTrend([]);
      setRecentAlerts([]);
      setSites([]);
    } finally {
      setLoading(false);
    }
  };

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'critical': return '#f5222d';
      case 'high': return '#fa8c16';
      case 'medium': return '#fadb14';
      case 'low': return '#52c41a';
      default: return '#d9d9d9';
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'new': return 'red';
      case 'acknowledged': return 'orange';
      case 'resolved': return 'green';
      case 'ignored': return 'default';
      default: return 'default';
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'new': return '待处理';
      case 'acknowledged': return '已确认';
      case 'resolved': return '已解决';
      case 'ignored': return '已忽略';
      default: return status;
    }
  };

  const pct = (value?: number | null) => (value === null || value === undefined ? '-' : `${Number(value).toFixed(1)}%`);

  const openSite = async (site: DashboardSite) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      const [detailRes, trendRes] = await Promise.all([
        dashboardApi.siteDetail(site.id),
        dashboardApi.alertTrend(7, site.id),
      ]);
      setDetail(detailRes.data);
      setSiteTrend(trendRes.data || []);
    } catch {
      setDetail(null);
      setSiteTrend([]);
    } finally {
      setDetailLoading(false);
    }
  };

  const openMetrics = async (server: { id: number; name: string }) => {
    setMetricTitle(server.name);
    setMetricOpen(true);
    setMetricSeries([]);
    try {
      const res = await dashboardApi.serverMetrics(server.id, 24);
      setMetricSeries((res.data || []).map((item: any) => ({
        ...item,
        time: new Date(item.collected_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', month: '2-digit', day: '2-digit' }),
      })));
    } catch {
      setMetricSeries([]);
    }
  };

  const healthTag = (level?: string, score?: number) => {
    if (level === 'good') return <Tag color="green">{score ?? '-'} 健康</Tag>;
    if (level === 'warn') return <Tag color="orange">{score ?? '-'} 注意</Tag>;
    return <Tag color="red">{score ?? '-'} 异常</Tag>;
  };

  const vendorText = (vendor?: string) => {
    switch (vendor) {
      case 'huawei': return '华为';
      case 'h3c': return '华三';
      case 'cisco': return '思科';
      case 'ruijie': return '锐捷';
      case 'linux': return 'Linux';
      case 'generic': return '通用';
      default: return vendor || '-';
    }
  };

  const siteColumns = [
    { title: '门店', dataIndex: 'name', key: 'name', width: 140 },
    {
      title: '健康分', key: 'health', width: 110,
      render: (_: unknown, record: DashboardSite) => healthTag(record.health_level, record.health_score),
    },
    { title: '城市', dataIndex: 'city', key: 'city', width: 100, render: (v: string) => v || '-' },
    {
      title: '类型', dataIndex: 'site_type', key: 'site_type', width: 80,
      render: (v: string) => v === 'cloud' ? <Tag color="blue">云端</Tag> : <Tag>门店</Tag>,
    },
    {
      title: '采集器', key: 'probe', width: 100,
      render: (_: unknown, record: DashboardSite) => {
        if (record.site_type === 'cloud') return <Tag>不需要</Tag>;
        return <Tag color={record.status === 'online' ? 'green' : 'default'}>{record.status === 'online' ? '在线' : '离线'}</Tag>;
      },
    },
    {
      title: '服务器', key: 'servers', width: 110,
      render: (_: unknown, record: DashboardSite) => `${record.server_online || 0} / ${record.server_total || 0}`,
    },
    {
      title: '网络设备', key: 'devices', width: 110,
      render: (_: unknown, record: DashboardSite) => `${record.device_online || 0} / ${record.device_total || 0}`,
    },
    { title: '最高 CPU', dataIndex: 'max_cpu', key: 'max_cpu', width: 100, render: (v: number) => pct(v) },
    { title: '最高磁盘', dataIndex: 'max_disk', key: 'max_disk', width: 100, render: (v: number) => pct(v) },
    {
      title: '未恢复', dataIndex: 'active_alerts', key: 'active_alerts', width: 90,
      render: (v: number) => <Tag color={v > 0 ? 'red' : 'green'}>{v || 0}</Tag>,
    },
    {
      title: '操作', key: 'action', width: 80,
      render: (_: unknown, record: DashboardSite) => <Button type="link" onClick={() => openSite(record)}>查看</Button>,
    },
  ];

  const alertColumns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (text: string) => new Date(text).toLocaleString('zh-CN') },
    { title: '来源', dataIndex: 'source_name', key: 'source_name', width: 120 },
    { title: '级别', dataIndex: 'level', key: 'level', width: 80,
      render: (level: string) => <Tag color={getLevelColor(level)}>{level.toUpperCase()}</Tag> },
    { title: '事件', dataIndex: 'event_description', key: 'event_description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag> },
  ];

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>;
  }

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="服务器总数"
              value={overview?.total_servers || 0}
              prefix={<CloudServerOutlined />}
              suffix={`/ ${overview?.online_servers || 0} 在线`}
            />
            <Progress
              percent={overview?.total_servers ? Math.round((overview.online_servers / overview.total_servers) * 100) : 0}
              size="small"
              status="active"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="网络设备"
              value={overview?.total_devices || 0}
              prefix={<GlobalOutlined />}
              suffix={`/ ${overview?.online_devices || 0} 在线`}
            />
            <Progress
              percent={overview?.total_devices ? Math.round((overview.online_devices / overview.total_devices) * 100) : 0}
              size="small"
              status="active"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="今日告警"
              value={overview?.total_alerts_today || 0}
              prefix={<AlertOutlined />}
              valueStyle={{ color: (overview?.total_alerts_today || 0) > 0 ? '#fa8c16' : undefined }}
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              严重: {overview?.critical_alerts_today || 0}
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待处理告警"
              value={overview?.unresolved_alerts || 0}
              prefix={<WarningOutlined />}
              valueStyle={{ color: (overview?.unresolved_alerts || 0) > 0 ? '#f5222d' : '#52c41a' }}
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              <CheckCircleOutlined style={{ color: '#52c41a' }} /> 需要关注
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <Statistic title="门店 / 云端" value={overview?.total_sites || 0} prefix={<ShopOutlined />} suffix={`门店 ${overview?.store_sites || 0}`} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <Statistic
              title="采集器在线"
              value={overview?.online_probes || 0}
              suffix={`/ ${overview?.store_sites || 0}`}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={8}>
          <Card>
            <Statistic
              title="未恢复健康告警"
              value={overview?.active_health_alerts || 0}
              prefix={<WarningOutlined />}
              valueStyle={{ color: (overview?.active_health_alerts || 0) > 0 ? '#f5222d' : '#52c41a' }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="门店概览" style={{ marginTop: 16 }}>
        <Table
          columns={siteColumns}
          dataSource={sites}
          rowKey="id"
          pagination={false}
          size="small"
          scroll={{ x: 1180 }}
          locale={{ emptyText: '还没有门店。请先在「门店 / 云端」里建档。' }}
        />
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={16}>
          <Card title="告警趋势（近7天）" extra={<ClockCircleOutlined />}>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="critical" stroke="#f5222d" name="严重" />
                <Line type="monotone" dataKey="high" stroke="#fa8c16" name="高危" />
                <Line type="monotone" dataKey="medium" stroke="#fadb14" name="中危" />
                <Line type="monotone" dataKey="low" stroke="#52c41a" name="低危" />
              </LineChart>
            </ResponsiveContainer>
            {trend.length === 0 && (
              <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>
                暂无告警趋势数据
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="系统状态">
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>服务器在线率</span>
                <span style={{ fontWeight: 'bold' }}>
                  {overview?.total_servers ? Math.round((overview.online_servers / overview.total_servers) * 100) : 0}%
                </span>
              </div>
              <Progress
                percent={overview?.total_servers ? Math.round((overview.online_servers / overview.total_servers) * 100) : 0}
                strokeColor="#1890ff"
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>设备在线率</span>
                <span style={{ fontWeight: 'bold' }}>
                  {overview?.total_devices ? Math.round((overview.online_devices / overview.total_devices) * 100) : 0}%
                </span>
              </div>
              <Progress
                percent={overview?.total_devices ? Math.round((overview.online_devices / overview.total_devices) * 100) : 0}
                strokeColor="#52c41a"
              />
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span>告警处理率</span>
                <span style={{ fontWeight: 'bold' }}>
                  {overview?.total_alerts_today ? Math.round(((overview.total_alerts_today - overview.unresolved_alerts) / overview.total_alerts_today) * 100) : 100}%
                </span>
              </div>
              <Progress
                percent={overview?.total_alerts_today ? Math.round(((overview.total_alerts_today - overview.unresolved_alerts) / overview.total_alerts_today) * 100) : 100}
                strokeColor="#52c41a"
              />
            </div>
          </Card>
        </Col>
      </Row>

      <Card title="最近告警" style={{ marginTop: 16 }}>
        <Table
          columns={alertColumns}
          dataSource={recentAlerts}
          rowKey="id"
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无告警记录' }}
        />
      </Card>

      <Drawer
        title={detail?.site?.name || '门店详情'}
        width={860}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detailLoading ? <Spin /> : detail && (
          <div>
            <p>
              {detail.site?.site_type === 'cloud' ? '云端' : '门店'}
              {detail.site?.city ? ` · ${detail.site.city}` : ''}
              {' · '}
              采集器 {detail.site?.site_type === 'cloud' ? '不需要' : (detail.site?.status === 'online' ? '在线' : '离线')}
              {' · '}
              健康分 {healthTag(detail.site?.health_level, detail.site?.health_score)}
            </p>
            <Card size="small" title="服务器" style={{ marginBottom: 16 }}>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={detail.servers || []}
                locale={{ emptyText: '没有服务器' }}
                columns={[
                  { title: '名称', dataIndex: 'name' },
                  { title: 'IP', dataIndex: 'ip', width: 130 },
                  { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'online' ? 'green' : 'default'}>{v === 'online' ? '在线' : v === 'error' ? '异常' : '离线'}</Tag> },
                  { title: 'CPU', dataIndex: 'cpu', width: 80, render: (v: number) => pct(v) },
                  { title: '内存', dataIndex: 'memory', width: 80, render: (v: number) => pct(v) },
                  { title: '磁盘', dataIndex: 'disk', width: 80, render: (v: number) => pct(v) },
                  { title: '曲线', width: 80, render: (_: unknown, record: any) => <Button type="link" onClick={() => openMetrics(record)}>24小时</Button> },
                ]}
              />
            </Card>
            <Card size="small" title="网络设备" style={{ marginBottom: 16 }}>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={detail.devices || []}
                locale={{ emptyText: '没有网络设备' }}
                expandable={{
                  expandedRowRender: (record: any) => (
                    <Table
                      size="small"
                      rowKey="if_name"
                      pagination={false}
                      dataSource={record.interfaces || []}
                      locale={{ emptyText: '还没有端口数据' }}
                      columns={[
                        { title: '端口', dataIndex: 'if_name' },
                        { title: '管理状态', dataIndex: 'admin_status' },
                        { title: '运行状态', dataIndex: 'oper_status', render: (v: string) => <Tag color={v === 'up' ? 'green' : v === 'down' ? 'red' : 'default'}>{v || '-'}</Tag> },
                      ]}
                    />
                  ),
                }}
                columns={[
                  { title: '名称', dataIndex: 'name' },
                  { title: 'IP', dataIndex: 'ip', width: 130 },
                  { title: '厂商', dataIndex: 'vendor', width: 80, render: (v: string) => vendorText(v) },
                  { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'online' ? 'green' : v === 'error' ? 'red' : 'default'}>{v === 'online' ? '在线' : v === 'error' ? 'SNMP不通' : '离线'}</Tag> },
                  { title: 'CPU', dataIndex: 'cpu', width: 80, render: (v: number) => pct(v) },
                  { title: '内存', dataIndex: 'memory', width: 80, render: (v: number) => pct(v) },
                ]}
              />
            </Card>
            <Card size="small" title="该门店告警趋势（近7天）">
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={siteTrend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Line type="monotone" dataKey="critical" stroke="#f5222d" name="严重" />
                  <Line type="monotone" dataKey="high" stroke="#fa8c16" name="高" />
                  <Line type="monotone" dataKey="medium" stroke="#fadb14" name="中" />
                  <Line type="monotone" dataKey="low" stroke="#52c41a" name="低" />
                </LineChart>
              </ResponsiveContainer>
              {siteTrend.length === 0 && <div style={{ textAlign: 'center', color: '#999' }}>暂无告警趋势</div>}
            </Card>
          </div>
        )}
      </Drawer>

      <Modal title={`${metricTitle} 近 24 小时`} open={metricOpen} footer={null} width={760} onCancel={() => setMetricOpen(false)}>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={metricSeries}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="time" minTickGap={24} />
            <YAxis domain={[0, 100]} unit="%" />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="cpu" name="CPU" stroke="#1677ff" dot={false} />
            <Line type="monotone" dataKey="memory" name="内存" stroke="#fa8c16" dot={false} />
            <Line type="monotone" dataKey="disk" name="磁盘" stroke="#52c41a" dot={false} />
          </LineChart>
        </ResponsiveContainer>
        {metricSeries.length === 0 && <div style={{ textAlign: 'center', color: '#999' }}>还没有资源采样。服务器安装 Agent 后约 1 分钟开始上报。</div>}
      </Modal>
    </div>
  );
}
