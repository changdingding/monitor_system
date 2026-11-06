import { useEffect, useState } from 'react';
import { Button, Card, Col, Drawer, Modal, Progress, Row, Space, Spin, Statistic, Table, Tag } from 'antd';
import { useNavigate } from 'react-router-dom';
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { dashboardApi } from '../../services/api';
import type { AlertTrend } from '../../types';
import { SITE_TYPE_LABELS } from '../../types';

interface Props {
  siteId: number | null;
  onClose: () => void;
  getContainer?: () => HTMLElement;
}

const pct = (v?: number | null) => (v === null || v === undefined ? '-' : `${Number(v).toFixed(1)}%`);
const VENDORS: Record<string, string> = { huawei: '华为', h3c: '华三', cisco: '思科', ruijie: '锐捷', linux: 'Linux', generic: '通用' };
const fmtTime = (v: string) => new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });

export function healthTag(level?: string, score?: number) {
  if (level === 'good') return <Tag color="green">{score ?? '-'} 健康</Tag>;
  if (level === 'warn') return <Tag color="orange">{score ?? '-'} 注意</Tag>;
  return <Tag color="red">{score ?? '-'} 异常</Tag>;
}

export default function SiteDetailDrawer({ siteId, onClose, getContainer }: Props) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [trend, setTrend] = useState<AlertTrend[]>([]);
  const [metric, setMetric] = useState<{ title: string; kind: 'server' | 'device'; data: any[] } | null>(null);

  useEffect(() => {
    if (!siteId) return;
    setLoading(true);
    setDetail(null);
    Promise.all([dashboardApi.siteDetail(siteId), dashboardApi.alertTrend(7, siteId)])
      .then(([d, t]) => { setDetail(d.data); setTrend(t.data || []); })
      .catch(() => { setDetail(null); setTrend([]); })
      .finally(() => setLoading(false));
  }, [siteId]);

  const openMetrics = async (kind: 'server' | 'device', record: { id: number; name: string }) => {
    setMetric({ title: record.name, kind, data: [] });
    try {
      const res = kind === 'server' ? await dashboardApi.serverMetrics(record.id) : await dashboardApi.deviceMetrics(record.id);
      setMetric({ title: record.name, kind, data: (res.data || []).map((i: any) => ({ ...i, time: fmtTime(i.collected_at) })) });
    } catch {
      setMetric({ title: record.name, kind, data: [] });
    }
  };

  const site = detail?.site;
  const servers = detail?.servers || [];
  const devices = detail?.devices || [];
  const ifaces = devices.flatMap((d: any) => d.interfaces || []);
  const ifUp = ifaces.filter((i: any) => i.oper_status === 'up').length;
  const go = (path: string) => { onClose(); navigate(path); };

  return (
    <Drawer title={site?.name || '站点详情'} width={900} open={!!siteId} onClose={onClose} getContainer={getContainer}
      extra={site && (
        <Space>
          <Button size="small" onClick={() => go(`/network-devices?site_id=${site.id}`)}>网络设备</Button>
          <Button size="small" onClick={() => go(`/servers?site_id=${site.id}`)}>服务器</Button>
          <Button size="small" onClick={() => go(`/alerts?site_id=${site.id}`)}>告警记录</Button>
        </Space>
      )}>
      {loading ? <Spin /> : site && (
        <div>
          <Space wrap style={{ marginBottom: 16 }}>
            <Tag color="blue">{SITE_TYPE_LABELS[site.site_type as keyof typeof SITE_TYPE_LABELS] || site.site_type}</Tag>
            {site.parent_name && <Tag>上级：{site.parent_name}</Tag>}
            {site.city && <Tag>{site.city}</Tag>}
            {site.site_type !== 'cloud' && (site.collect_mode === 'central'
              ? <Tag color="cyan">总部平台直接轮询</Tag>
              : <Tag color={site.status === 'online' ? 'green' : 'red'}>采集器{site.status === 'online' ? '在线' : '离线'}{site.probe_version ? ` v${site.probe_version}` : ''}</Tag>)}
            {healthTag(site.health_level, site.health_score)}
            {site.contact && <Tag>联系人：{site.contact} {site.phone || ''}</Tag>}
          </Space>
          <Row gutter={12} style={{ marginBottom: 16 }}>
            <Col span={6}><Card size="small"><Statistic title="健康分" value={site.health_score} suffix="/ 100" /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="服务器在线" value={servers.filter((s: any) => s.status === 'online').length} suffix={`/ ${servers.length}`} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="设备在线" value={devices.filter((d: any) => d.status === 'online').length} suffix={`/ ${devices.length}`} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="端口 UP" value={ifUp} suffix={`/ ${ifaces.length}`} /></Card></Col>
          </Row>
          {(detail.children || []).length > 0 && (
            <Card size="small" title="下级站点" style={{ marginBottom: 16 }}>
              <Space wrap>
                {detail.children.map((c: any) => (
                  <Tag key={c.id} color={c.status === 'online' ? 'green' : 'default'}>{c.name}</Tag>
                ))}
              </Space>
            </Card>
          )}
          <Card size="small" title="网络设备" style={{ marginBottom: 16 }}>
            <Table size="small" rowKey="id" pagination={false} dataSource={devices}
              locale={{ emptyText: '没有网络设备。可在「网络设备」中批量导入，或在站点上开启自动发现。' }}
              expandable={{
                expandedRowRender: (record: any) => (
                  <Space wrap>
                    {(record.interfaces || []).map((i: any) => (
                      <Tag key={i.if_name} color={i.oper_status === 'up' ? 'green' : i.oper_status === 'down' && i.admin_status !== 'down' ? 'red' : 'default'}>
                        {i.if_name}
                      </Tag>
                    ))}
                    {(record.interfaces || []).length === 0 && <span style={{ opacity: 0.6 }}>还没有端口数据</span>}
                  </Space>
                ),
              }}
              columns={[
                { title: '名称', dataIndex: 'name' },
                { title: 'IP', dataIndex: 'ip', width: 130 },
                { title: '厂商', dataIndex: 'vendor', width: 70, render: (v: string) => VENDORS[v] || v || '-' },
                { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <Tag color={v === 'online' ? 'green' : v === 'error' ? 'red' : 'default'}>{v === 'online' ? '在线' : v === 'error' ? 'SNMP不通' : '离线'}</Tag> },
                { title: 'CPU', dataIndex: 'cpu', width: 110, render: (v: number) => v == null ? '-' : <Progress percent={Math.round(v)} size="small" status={v >= 85 ? 'exception' : 'normal'} /> },
                { title: '内存', dataIndex: 'memory', width: 110, render: (v: number) => v == null ? '-' : <Progress percent={Math.round(v)} size="small" status={v >= 85 ? 'exception' : 'normal'} /> },
                { title: '曲线', width: 70, render: (_: unknown, r: any) => <Button type="link" size="small" onClick={() => openMetrics('device', r)}>24h</Button> },
              ]}
            />
          </Card>
          <Card size="small" title="服务器" style={{ marginBottom: 16 }}>
            <Table size="small" rowKey="id" pagination={false} dataSource={servers} locale={{ emptyText: '没有服务器' }}
              columns={[
                { title: '名称', dataIndex: 'name' },
                { title: 'IP', dataIndex: 'ip', width: 130 },
                { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'online' ? 'green' : 'default'}>{v === 'online' ? '在线' : v === 'error' ? '异常' : '离线'}</Tag> },
                { title: 'CPU', dataIndex: 'cpu', width: 80, render: pct },
                { title: '内存', dataIndex: 'memory', width: 80, render: pct },
                { title: '磁盘', dataIndex: 'disk', width: 80, render: pct },
                { title: '曲线', width: 70, render: (_: unknown, r: any) => <Button type="link" size="small" onClick={() => openMetrics('server', r)}>24h</Button> },
              ]}
            />
          </Card>
          <Card size="small" title="近 7 天告警">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                <XAxis dataKey="date" tick={{ fill: '#8fb3d9', fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fill: '#8fb3d9', fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Area type="monotone" dataKey="critical" name="严重" stackId="1" stroke="#ff4d6a" fill="#ff4d6a55" />
                <Area type="monotone" dataKey="high" name="高危" stackId="1" stroke="#ff8a3d" fill="#ff8a3d55" />
                <Area type="monotone" dataKey="medium" name="中危" stackId="1" stroke="#ffd447" fill="#ffd44755" />
                <Area type="monotone" dataKey="low" name="低危" stackId="1" stroke="#22e3a5" fill="#22e3a555" />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </div>
      )}

      <Modal title={`${metric?.title || ''} 近 24 小时`} open={!!metric} getContainer={getContainer} footer={null} width={760} onCancel={() => setMetric(null)}>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={metric?.data || []}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
            <XAxis dataKey="time" minTickGap={24} tick={{ fill: '#8fb3d9', fontSize: 11 }} />
            <YAxis domain={[0, 100]} unit="%" tick={{ fill: '#8fb3d9', fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Area type="monotone" dataKey="cpu" name="CPU" stroke="#20d4ff" fill="#20d4ff33" dot={false} />
            <Area type="monotone" dataKey="memory" name="内存" stroke="#ffb547" fill="#ffb54733" dot={false} />
            {metric?.kind === 'server' && <Area type="monotone" dataKey="disk" name="磁盘" stroke="#22e3a5" fill="#22e3a533" dot={false} />}
          </AreaChart>
        </ResponsiveContainer>
        {metric && metric.data.length === 0 && (
          <div style={{ textAlign: 'center', opacity: 0.6 }}>
            还没有采样数据。{metric.kind === 'server' ? '服务器安装 Agent 后约 1 分钟开始上报。' : '采集器上报后开始记录。'}
          </div>
        )}
      </Modal>
    </Drawer>
  );
}
