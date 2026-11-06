import { useState, useEffect } from 'react';
import { Table, Tag, Space, Button, Select, Card, Input, DatePicker, message, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import { auditApi } from '../services/api';
import type { AuditLog } from '../types';

const { RangePicker } = DatePicker;
const { Text } = Typography;

const TARGET_TEXT: Record<string, string> = {
  site: '站点', server: '服务器', network_device: '网络设备', user: '用户',
  alert: '告警', feishu: '飞书', settings: '系统设置', auth: '认证',
};

const ALERT_STATUS_TEXT: Record<string, string> = {
  new: '待处理', acknowledged: '已确认', resolved: '已解决', ignored: '已忽略',
};

const fmt = (d: Dayjs) => d.format('YYYY-MM-DDTHH:mm:ss');

export default function AuditLogs() {
  const [items, setItems] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [actions, setActions] = useState<string[]>([]);
  const [username, setUsername] = useState('');
  const [usernameInput, setUsernameInput] = useState('');
  const [action, setAction] = useState<string | undefined>();
  const [success, setSuccess] = useState<boolean | undefined>();
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);

  useEffect(() => {
    auditApi.actions().then((res) => setActions(res.data || [])).catch(() => setActions([]));
  }, []);

  useEffect(() => {
    fetchLogs();
  }, [page, pageSize, username, action, success, range]);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await auditApi.list({
        page, size: pageSize,
        username: username || undefined,
        action, success,
        start: range?.[0] ? fmt(range[0].startOf('day')) : undefined,
        end: range?.[1] ? fmt(range[1].endOf('day')) : undefined,
      });
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch (error: any) {
      message.error(error.response?.data?.detail || '获取审计日志失败');
    } finally {
      setLoading(false);
    }
  };

  const resetPage = <T,>(setter: (v: T) => void) => (v: T) => { setPage(1); setter(v); };

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (t: string) => (t ? new Date(t).toLocaleString('zh-CN') : '-') },
    { title: '操作人', dataIndex: 'username', key: 'username', width: 120,
      render: (u: string | null) => u || <Text type="secondary">匿名</Text> },
    { title: '动作', dataIndex: 'action', key: 'action', width: 160 },
    { title: '对象', key: 'target', width: 200,
      render: (_: unknown, r: AuditLog) => {
        if (!r.target_type || r.target_type === 'auth') return '-';
        const type = TARGET_TEXT[r.target_type] || r.target_type;
        const name = r.target_name || (r.target_id ? `#${r.target_id}` : '');
        return name ? `${type}：${name}` : type;
      } },
    { title: '结果', key: 'result', width: 100,
      render: (_: unknown, r: AuditLog) => (
        r.success ? <Tag color="green">成功</Tag> : <Tag color="red">失败 {r.status_code}</Tag>
      ) },
    { title: '说明', dataIndex: 'detail', key: 'detail',
      render: (d: Record<string, unknown> | null) => {
        if (!d || !Object.keys(d).length) return '-';
        return Object.entries(d).map(([k, v]) => {
          if (k === 'status') return `改为「${ALERT_STATUS_TEXT[String(v)] || v}」`;
          if (k === 'reason') return String(v);
          return `${k}=${v}`;
        }).join('；');
      } },
    { title: '来源 IP', key: 'ip', width: 140,
      render: (_: unknown, r: AuditLog) => (
        r.forwarded_for
          ? <Tooltip title={`X-Forwarded-For：${r.forwarded_for}（可被伪造，仅供参考）`}><span>{r.ip}</span></Tooltip>
          : r.ip || '-'
      ) },
  ];

  return (
    <Card
      title="操作审计"
      extra={<Button icon={<ReloadOutlined />} onClick={fetchLogs}>刷新</Button>}
    >
      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="操作人"
          allowClear
          style={{ width: 160 }}
          value={usernameInput}
          onChange={(e) => setUsernameInput(e.target.value)}
          onSearch={(v) => { setPage(1); setUsername(v.trim()); }}
        />
        <Select
          placeholder="动作"
          allowClear
          showSearch
          style={{ width: 180 }}
          value={action}
          onChange={resetPage(setAction)}
          options={actions.map((a) => ({ label: a, value: a }))}
        />
        <Select
          placeholder="结果"
          allowClear
          style={{ width: 110 }}
          value={success}
          onChange={resetPage(setSuccess)}
          options={[{ label: '成功', value: true }, { label: '失败', value: false }]}
        />
        <RangePicker value={range} onChange={(v) => { setPage(1); setRange(v as any); }} />
      </Space>
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={items}
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, s) => { setPage(p); setPageSize(s); },
        }}
      />
    </Card>
  );
}
