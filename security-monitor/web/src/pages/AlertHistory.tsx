import { useState, useEffect } from 'react';
import { Table, Tag, Space, Button, Select, Card, message, Modal, Descriptions } from 'antd';
import { ReloadOutlined, EyeOutlined, CheckOutlined } from '@ant-design/icons';
import { alertApi } from '../services/api';
import type { Alert } from '../types';

export default function AlertHistory() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [levelFilter, setLevelFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [detailVisible, setDetailVisible] = useState(false);
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);

  useEffect(() => {
    fetchAlerts();
  }, [page, pageSize, levelFilter, statusFilter]);

  const fetchAlerts = async () => {
    setLoading(true);
    try {
      const res = await alertApi.list({ page, size: pageSize, level: levelFilter, status: statusFilter });
      setAlerts(res.data.items || res.data);
      setTotal(res.data.total || res.data.length);
    } catch (error) {
      message.error('获取告警列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleViewDetail = (alert: Alert) => {
    setSelectedAlert(alert);
    setDetailVisible(true);
  };

  const handleResolve = async (id: number) => {
    try {
      await alertApi.updateStatus(id, 'resolved');
      message.success('已标记为已解决');
      fetchAlerts();
    } catch (error) {
      message.error('操作失败');
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

  const getLevelText = (level: string) => {
    switch (level) {
      case 'critical': return '严重';
      case 'high': return '高危';
      case 'medium': return '中危';
      case 'low': return '低危';
      default: return level;
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

  const columns = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (text: string) => new Date(text).toLocaleString('zh-CN'),
      sorter: (a: Alert, b: Alert) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime() },
    { title: '来源', dataIndex: 'source_name', key: 'source_name', width: 140, ellipsis: true },
    { title: '类型', dataIndex: 'source_type', key: 'source_type', width: 80,
      render: (type: string) => type === 'server' ? '服务器' : '网络设备' },
    { title: '级别', dataIndex: 'level', key: 'level', width: 80,
      render: (level: string) => <Tag color={getLevelColor(level)}>{getLevelText(level)}</Tag> },
    { title: '事件', dataIndex: 'event_description', key: 'event_description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag> },
    { title: '已通知', dataIndex: 'notified', key: 'notified', width: 80,
      render: (v: boolean) => v ? <Tag color="blue">是</Tag> : <Tag>否</Tag> },
    { title: '操作', key: 'action', width: 150,
      render: (_: any, record: Alert) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => handleViewDetail(record)}>详情</Button>
          {record.status === 'new' && (
            <Button type="link" size="small" icon={<CheckOutlined />} onClick={() => handleResolve(record.id)}>解决</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="告警历史"
        extra={
          <Space>
            <Select
              placeholder="告警级别"
              allowClear
              style={{ width: 120 }}
              onChange={(v) => { setLevelFilter(v); setPage(1); }}
            >
              <Select.Option value="critical">严重</Select.Option>
              <Select.Option value="high">高危</Select.Option>
              <Select.Option value="medium">中危</Select.Option>
              <Select.Option value="low">低危</Select.Option>
            </Select>
            <Select
              placeholder="状态"
              allowClear
              style={{ width: 120 }}
              onChange={(v) => { setStatusFilter(v); setPage(1); }}
            >
              <Select.Option value="new">待处理</Select.Option>
              <Select.Option value="acknowledged">已确认</Select.Option>
              <Select.Option value="resolved">已解决</Select.Option>
              <Select.Option value="ignored">已忽略</Select.Option>
            </Select>
            <Button icon={<ReloadOutlined />} onClick={fetchAlerts}>刷新</Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={alerts}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showTotal: (t) => `共 ${t} 条告警`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps); },
          }}
        />
      </Card>

      <Modal
        title="告警详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={null}
        width={700}
      >
        {selectedAlert && (
          <Descriptions bordered column={2}>
            <Descriptions.Item label="告警ID">{selectedAlert.id}</Descriptions.Item>
            <Descriptions.Item label="级别">
              <Tag color={getLevelColor(selectedAlert.level)}>{getLevelText(selectedAlert.level)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="来源">{selectedAlert.source_name}</Descriptions.Item>
            <Descriptions.Item label="类型">{selectedAlert.source_type === 'server' ? '服务器' : '网络设备'}</Descriptions.Item>
            <Descriptions.Item label="事件类型">{selectedAlert.event_type}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={getStatusColor(selectedAlert.status)}>{getStatusText(selectedAlert.status)}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="创建时间" span={2}>
              {new Date(selectedAlert.created_at).toLocaleString('zh-CN')}
            </Descriptions.Item>
            <Descriptions.Item label="事件描述" span={2}>{selectedAlert.event_description}</Descriptions.Item>
            {selectedAlert.raw_data && (
              <Descriptions.Item label="原始数据" span={2}>
                <pre style={{ maxHeight: 200, overflow: 'auto', fontSize: 12, background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                  {selectedAlert.raw_data}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
