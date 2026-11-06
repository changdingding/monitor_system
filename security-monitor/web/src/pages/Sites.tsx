import { useEffect, useState } from 'react';
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd';
import { DownloadOutlined, EditOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { siteApi } from '../services/api';
import type { Site } from '../types';

const { Paragraph, Text } = Typography;

export default function Sites() {
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [probeVisible, setProbeVisible] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [probeSite, setProbeSite] = useState<Site | null>(null);
  const [serverUrl, setServerUrl] = useState(`${window.location.protocol}//${window.location.hostname}:8000`);
  const [downloading, setDownloading] = useState(false);
  const [form] = Form.useForm();

  const load = async () => {
    setLoading(true);
    try {
      const res = await siteApi.list();
      setSites(res.data);
    } catch {
      message.error('获取门店列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ site_type: 'store' });
    setModalVisible(true);
  };

  const openEdit = (site: Site) => {
    setEditing(site);
    form.setFieldsValue(site);
    setModalVisible(true);
  };

  const openProbe = (site: Site) => {
    setProbeSite(site);
    setProbeVisible(true);
  };

  const submit = async (values: any) => {
    try {
      if (editing) {
        const res = await siteApi.update(editing.id, values);
        message.success('已更新');
        setModalVisible(false);
        await load();
        if (res.data?.site_type === 'store') {
          openProbe(res.data);
        }
        return;
      }
      const res = await siteApi.create(values);
      message.success('已创建');
      setModalVisible(false);
      await load();
      if (res.data?.site_type === 'store') {
        openProbe(res.data);
      }
    } catch (error: any) {
      message.error(error.response?.data?.detail || '保存失败');
    }
  };

  const remove = async (id: number) => {
    try {
      await siteApi.delete(id);
      message.success('已删除');
      load();
    } catch {
      message.error('删除失败');
    }
  };

  const resetToken = async () => {
    if (!probeSite) return;
    try {
      const res = await siteApi.resetToken(probeSite.id);
      setProbeSite({ ...probeSite, probe_token: res.data.probe_token });
      message.success('Token 已重置，请重新下载采集器');
      load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '重置失败');
    }
  };

  const copyToken = async () => {
    if (!probeSite?.probe_token) return;
    try {
      await navigator.clipboard.writeText(probeSite.probe_token);
      message.success('Token 已复制');
    } catch {
      message.error('复制失败，请手动选择 Token');
    }
  };

  const downloadPackage = async () => {
    if (!probeSite) return;
    const url = serverUrl.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      message.error('管理端地址要以 http:// 或 https:// 开头');
      return;
    }
    setDownloading(true);
    try {
      const res = await siteApi.downloadPackage(probeSite.id, url);
      const blob = new Blob([res.data], { type: 'application/zip' });
      const link = document.createElement('a');
      link.href = window.URL.createObjectURL(blob);
      link.download = `StoreProbe-${probeSite.name}.zip`;
      link.click();
      window.URL.revokeObjectURL(link.href);
      message.success('采集器已开始下载');
    } catch (error: any) {
      let detail = '下载失败';
      try {
        if (error.response?.data instanceof Blob) {
          const text = await error.response.data.text();
          detail = JSON.parse(text).detail || detail;
        }
      } catch { /* ignore */ }
      message.error(detail);
    } finally {
      setDownloading(false);
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '城市', dataIndex: 'city', key: 'city', render: (v: string) => v || '-' },
    {
      title: '类型', dataIndex: 'site_type', key: 'site_type', width: 100,
      render: (v: string) => v === 'cloud' ? <Tag color="blue">云端</Tag> : <Tag color="purple">门店</Tag>,
    },
    {
      title: '采集器', dataIndex: 'status', key: 'status', width: 120,
      render: (status: string, record: Site) => {
        if (record.site_type === 'cloud') return <Tag>无需采集器</Tag>;
        return <Tag color={status === 'online' ? 'green' : 'default'}>{status === 'online' ? '在线' : '离线'}</Tag>;
      },
    },
    {
      title: '最后心跳', dataIndex: 'last_seen', key: 'last_seen', width: 180,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作', key: 'action', width: 280,
      render: (_: unknown, record: Site) => (
        <Space>
          {record.site_type === 'store' && (
            <Button type="link" icon={<DownloadOutlined />} onClick={() => openProbe(record)}>采集器</Button>
          )}
          <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm title="删除后，名下服务器和设备会变为未归属" onConfirm={() => remove(record.id)}>
            <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="门店 / 云端"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>添加站点</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="门店在各自的局域网里，总部拨不进去。每家门店放一台边缘采集器，由它在店内读取交换机，再主动上报。云端服务器不用采集器，直接装 Agent。"
        />
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={sites}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个站点` }}
        />
      </Card>

      <Modal
        title={editing ? '编辑站点' : '添加站点'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={submit}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：上海南京路店 / 阿里云" />
          </Form.Item>
          <Form.Item name="site_type" label="类型" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="store">门店（需要边缘采集器）</Select.Option>
              <Select.Option value="cloud">云端（服务器直接上报）</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="city" label="城市">
            <Input placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`边缘采集器 - ${probeSite?.name || ''}`}
        open={probeVisible}
        onCancel={() => setProbeVisible(false)}
        footer={[
          <Button key="reset" onClick={resetToken}>重置 Token</Button>,
          <Button key="close" type="primary" onClick={() => setProbeVisible(false)}>关闭</Button>,
        ]}
        width={640}
      >
        {probeSite && (
          <div>
            <Paragraph>
              <Text strong>采集器 Token</Text>
            </Paragraph>
            <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
              <Input value={probeSite.probe_token} readOnly />
              <Button onClick={copyToken}>复制</Button>
            </Space.Compact>
            <Paragraph>
              <Text strong>管理端地址</Text>
              <Text type="secondary">（门店电脑必须能访问，不要填 localhost）</Text>
            </Paragraph>
            <Input
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="http://总部IP:8000"
              style={{ marginBottom: 16 }}
            />
            <Button type="primary" icon={<DownloadOutlined />} loading={downloading} onClick={downloadPackage}>
              下载采集器
            </Button>
            <Alert
              style={{ marginTop: 16 }}
              type="warning"
              showIcon
              message="解压后编辑 config.json 的 devices，填入店内交换机的 IP 和 SNMP 团体字。团体字只留在门店电脑上。Windows 双击「启动采集器.bat」，Linux 执行 bash run.sh。"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
