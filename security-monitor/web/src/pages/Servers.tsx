import { useState, useEffect } from 'react';
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space, message, Popconfirm, Card, Alert, Typography,
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, ApiOutlined,
  CloudSyncOutlined, DownloadOutlined, KeyOutlined,
} from '@ant-design/icons';
import { serverApi, siteApi } from '../services/api';
import type { Server, Site } from '../types';

const { Paragraph, Text } = Typography;

type ServerRow = Server & {
  agent_token?: string;
  agent_online?: boolean;
  agent_last_seen?: string;
  collect_mode?: string;
  data_channel?: string;
  data_channel_label?: string;
  ssh_online?: boolean;
  ssh_host_key_known?: boolean;
  ssh_host_key_type?: string;
};

export default function Servers() {
  const [servers, setServers] = useState<ServerRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [agentModalVisible, setAgentModalVisible] = useState(false);
  const [editingServer, setEditingServer] = useState<ServerRow | null>(null);
  const [agentInfo, setAgentInfo] = useState<any>(null);
  const [actionLoadingId, setActionLoadingId] = useState<number | null>(null);
  const [downloadingOs, setDownloadingOs] = useState<'windows' | 'linux' | null>(null);
  const [form] = Form.useForm();
  const [sites, setSites] = useState<Site[]>([]);
  const osType = Form.useWatch('os_type', form);

  const defaultApiBase = `${window.location.protocol}//${window.location.hostname}:8000`;

  useEffect(() => {
    fetchServers();
    siteApi.list().then((res) => setSites(res.data || [])).catch(() => setSites([]));
  }, []);

  const fetchServers = async () => {
    setLoading(true);
    try {
      const res = await serverApi.list();
      setServers(res.data);
    } catch (error) {
      message.error('获取服务器列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    setEditingServer(null);
    form.resetFields();
    form.setFieldsValue({ port: 22, os_type: 'windows', collect_mode: 'agent' });
    setModalVisible(true);
  };

  const handleEdit = (server: ServerRow) => {
    setEditingServer(server);
    form.setFieldsValue({ ...server, password: undefined, collect_mode: server.collect_mode || 'agent' });
    setModalVisible(true);
  };

  const handleDelete = async (id: number) => {
    try {
      await serverApi.delete(id);
      message.success('删除成功');
      fetchServers();
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSubmit = async (values: any) => {
    try {
      if (editingServer) {
        await serverApi.update(editingServer.id, values);
        message.success('更新成功');
      } else {
        const res = await serverApi.create(values);
        message.success('创建成功，请安装 Agent 客户端');
        setModalVisible(false);
        await fetchServers();
        if (res.data?.id) {
          await openAgentInstall(res.data);
        }
        return;
      }
      setModalVisible(false);
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || (editingServer ? '更新失败' : '创建失败'));
    }
  };

  const openAgentInstall = async (server: ServerRow) => {
    setActionLoadingId(server.id);
    try {
      const res = await serverApi.agentInstall(server.id);
      setAgentInfo({
        ...res.data,
        server_url: defaultApiBase,
      });
      setAgentModalVisible(true);
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '获取安装信息失败');
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleResetHostKey = async (id: number) => {
    try {
      await serverApi.resetHostKey(id);
      message.success('主机密钥已清除，下次 SSH 连接时将重新记录');
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '重置失败');
    }
  };

  const handleResetToken = async () => {
    if (!agentInfo?.server_id) return;
    try {
      const res = await serverApi.resetAgentToken(agentInfo.server_id);
      setAgentInfo({ ...agentInfo, agent_token: res.data.agent_token });
      message.success('Token 已重置，请重新下载安装包');
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '重置失败');
    }
  };

  const downloadPackage = async (os: 'windows' | 'linux') => {
    if (!agentInfo?.server_id) return;
    const serverUrl = (agentInfo.server_url || '').trim();
    if (!serverUrl.startsWith('http://') && !serverUrl.startsWith('https://')) {
      message.error('请先填写正确的管理端地址（以 http:// 或 https:// 开头）');
      return;
    }
    if (serverUrl.includes('localhost') || serverUrl.includes('127.0.0.1')) {
      message.warning('管理端地址是 localhost，目标服务器通常访问不到。请改成管理机真实 IP 后再下载。');
    }
    setDownloadingOs(os);
    try {
      const res = await serverApi.downloadAgentPackage(agentInfo.server_id, os, serverUrl);
      const blob = new Blob([res.data], { type: 'application/zip' });
      const cd = res.headers['content-disposition'] || '';
      let filename = os === 'windows'
        ? `SecurityMonitorAgent-Windows.zip`
        : `SecurityMonitorAgent-Linux.zip`;
      const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd);
      if (m) {
        filename = decodeURIComponent(m[1] || m[2]);
      }
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success(`${os === 'windows' ? 'Windows' : 'Linux'} 安装包已开始下载`);
    } catch (error: any) {
      let detail = '下载失败';
      try {
        if (error.response?.data instanceof Blob) {
          const text = await error.response.data.text();
          const parsed = JSON.parse(text);
          detail = parsed.detail || detail;
        } else {
          detail = error.response?.data?.detail || detail;
        }
      } catch { /* ignore */ }
      message.error(detail);
    } finally {
      setDownloadingOs(null);
    }
  };

  const handleTest = async (server: ServerRow) => {
    setActionLoadingId(server.id);
    try {
      const res = await serverApi.test(server.id);
      if (res.data.success) message.success('SSH 连接成功');
      else message.error(res.data.message || '连接失败');
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '连接测试失败');
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleCollect = async (server: ServerRow) => {
    setActionLoadingId(server.id);
    try {
      const res = await serverApi.collect(server.id);
      message.success(
        `SSH 采集完成：登录 ${res.data.login_logs}，高危 ${res.data.high_risk_ops}，告警 ${res.data.alerted}`
      );
      fetchServers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '立即采集失败');
      fetchServers();
    } finally {
      setActionLoadingId(null);
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 140 },
    { title: '门店', dataIndex: 'site_name', key: 'site_name', width: 120, render: (v: string) => v || '未归属' },
    { title: 'IP地址', dataIndex: 'ip', key: 'ip', width: 130 },
    {
      title: '系统', dataIndex: 'os_type', key: 'os_type', width: 90,
      render: (type: string) => <Tag color={type === 'windows' ? 'blue' : 'green'}>{type === 'windows' ? 'Windows' : 'Linux'}</Tag>,
    },
    {
      title: '采集模式', dataIndex: 'collect_mode', key: 'collect_mode', width: 110,
      render: (mode: string) => {
        const m = mode || 'agent';
        return <Tag color={m === 'agent' ? 'purple' : m === 'ssh' ? 'default' : 'cyan'}>
          {m === 'agent' ? '仅Agent' : m === 'ssh' ? '仅SSH' : '混合'}
        </Tag>;
      },
    },
    {
      title: '当前通道', dataIndex: 'data_channel_label', key: 'data_channel', width: 160,
      render: (label: string, record: ServerRow) => {
        const ch = record.data_channel || 'none';
        const color = ch === 'agent' ? 'green' : ch === 'ssh' ? 'blue' : 'orange';
        return <Tag color={color}>{label || '未知'}</Tag>;
      },
    },
    {
      title: 'Agent推送', key: 'agent', width: 100,
      render: (_: any, record: ServerRow) => (
        record.agent_online
          ? <Tag color="green">推送中</Tag>
          : <Tag>未连接</Tag>
      ),
    },
    {
      title: 'SSH连通', dataIndex: 'status', key: 'status', width: 130,
      render: (status: string, record: ServerRow) => {
        const mode = record.collect_mode || 'agent';
        if (mode === 'agent') {
          return <Tag>不使用</Tag>;
        }
        return (
          <Space size={4}>
            <Tag color={status === 'online' ? 'green' : status === 'error' ? 'red' : 'default'}>
              {status === 'online' ? '可拉取' : status === 'error' ? '异常' : '离线'}
            </Tag>
            {record.ssh_host_key_known && (
              <Tag icon={<KeyOutlined />} color="blue" title={`已记录主机密钥（${record.ssh_host_key_type}），连接时严格校验`} />
            )}
          </Space>
        );
      },
    },
    {
      title: '资源', key: 'usage', width: 180,
      render: (_: unknown, record: ServerRow) => {
        const n = (v?: number | null) => (v === null || v === undefined ? '-' : `${Number(v).toFixed(0)}%`);
        return `CPU ${n(record.cpu)} / 内存 ${n(record.memory)} / 磁盘 ${n(record.disk)}`;
      },
    },
    {
      title: '最后活跃', key: 'last_active', width: 170,
      render: (_: any, record: ServerRow) => {
        const ch = record.data_channel;
        const t = ch === 'agent' ? record.agent_last_seen : record.last_heartbeat;
        return t ? new Date(t).toLocaleString('zh-CN') : '-';
      },
    },
    {
      title: '操作', key: 'action', width: 380,
      render: (_: any, record: ServerRow) => {
        const mode = record.collect_mode || 'agent';
        return (
          <Space wrap>
            <Button type="link" icon={<DownloadOutlined />} loading={actionLoadingId === record.id} onClick={() => openAgentInstall(record)}>
              安装Agent
            </Button>
            {(mode === 'ssh' || mode === 'both') && (
              <>
                <Button type="link" icon={<ApiOutlined />} loading={actionLoadingId === record.id} onClick={() => handleTest(record)}>SSH测试</Button>
                <Button type="link" icon={<CloudSyncOutlined />} loading={actionLoadingId === record.id} onClick={() => handleCollect(record)}>SSH拉取</Button>
                {record.ssh_host_key_known && (
                  <Popconfirm
                    title="重置主机密钥？"
                    description="仅在确认服务器已重装系统或更换 SSH 密钥时使用，下次连接将重新信任新密钥"
                    onConfirm={() => handleResetHostKey(record.id)}
                  >
                    <Button type="link" icon={<KeyOutlined />}>重置密钥</Button>
                  </Popconfirm>
                )}
              </>
            )}
            <Button type="link" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
            <Popconfirm title="确定删除此服务器？" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <Card
        title="服务器管理"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchServers}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加服务器</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="Agent 推送 vs SSH 拉取"
          description="推荐使用 Agent：客户端主动上报，飞书里会标注「Agent 客户端上报」。SSH 拉取是管理端定时/手动去连服务器取日志，飞书标注「SSH 服务端拉取」。看「当前通道」列可知此刻实际走哪条链路。"
        />
        <Table
          columns={columns}
          dataSource={servers}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1560 }}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 台服务器` }}
        />
      </Card>

      <Modal
        title={editingServer ? '编辑服务器' : '添加服务器'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="site_id" label="所属门店 / 云端">
            <Select allowClear placeholder="未归属">
              {sites.map((site) => (
                <Select.Option key={site.id} value={site.id}>
                  {site.name}{site.site_type === 'cloud' ? '（云端）' : ''}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="name" label="服务器名称" rules={[{ required: true, message: '请输入服务器名称' }]}>
            <Input placeholder="例如：生产服务器-01" />
          </Form.Item>
          <Form.Item name="ip" label="IP地址" rules={[{ required: true, message: '请输入IP地址' }]}>
            <Input placeholder="例如：192.168.1.100" />
          </Form.Item>
          <Form.Item name="os_type" label="操作系统" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="windows">Windows</Select.Option>
              <Select.Option value="linux">Linux</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="collect_mode" label="采集方式" initialValue="agent" rules={[{ required: true }]}
            extra="仅Agent=客户端推送；仅SSH=服务端拉取；混合=Agent优先，离线时才SSH"
          >
            <Select>
              <Select.Option value="agent">仅 Agent 客户端推送（推荐）</Select.Option>
              <Select.Option value="ssh">仅 SSH 服务端拉取</Select.Option>
              <Select.Option value="both">混合（Agent优先，离线时SSH）</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="port" label="SSH 端口（仅 SSH/混合模式需要）" initialValue={22}>
            <Input type="number" />
          </Form.Item>
          <Form.Item name="username" label="SSH 用户名（可选）">
            <Input placeholder={osType === 'windows' ? 'Administrator' : 'root'} />
          </Form.Item>
          <Form.Item name="password" label="SSH 密码（可选）" extra={editingServer ? '留空不修改' : 'Agent 模式可不填'}>
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`安装 Agent - ${agentInfo?.server_name || ''}`}
        open={agentModalVisible}
        onCancel={() => setAgentModalVisible(false)}
        footer={[
          <Button key="reset" onClick={handleResetToken}>重置 Token</Button>,
          <Button key="close" type="primary" onClick={() => setAgentModalVisible(false)}>关闭</Button>,
        ]}
        width={640}
      >
        {agentInfo && (
          <div>
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message="小白也能装：下载对应系统安装包 → 拷到服务器解压 → 双击安装（或运行安装脚本）→ 授权即可"
            />

            <Paragraph>
              <Text strong>① 填写管理端地址</Text>
              <Text type="secondary">（目标服务器必须能访问这个地址，不要用 localhost）</Text>
            </Paragraph>
            <Input
              value={agentInfo.server_url}
              onChange={(e) => setAgentInfo({ ...agentInfo, server_url: e.target.value })}
              placeholder="例如：http://192.168.1.10:8000"
              style={{ marginBottom: 16 }}
            />

            <Paragraph>
              <Text strong>② 下载安装包</Text>
            </Paragraph>
            <Space wrap style={{ marginBottom: 16 }}>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                size="large"
                loading={downloadingOs === 'windows'}
                onClick={() => downloadPackage('windows')}
              >
                下载 Windows 安装包
              </Button>
              <Button
                icon={<DownloadOutlined />}
                size="large"
                loading={downloadingOs === 'linux'}
                onClick={() => downloadPackage('linux')}
              >
                下载 Linux 安装包
              </Button>
            </Space>

            <Paragraph>
              <Text strong>③ 在目标机安装</Text>
            </Paragraph>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="Windows"
              description="解压后双击「安装Agent.bat」→ 弹出授权点「是」→ 向导里点「下一步」→「安装」→「完成」。无需安装 Python。"
            />
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="Linux"
              description="解压后执行：sudo bash install.sh → 输入密码授权即可。会自动安装为系统服务并开机自启。"
            />
            <Alert
              type="warning"
              showIcon
              message="安装成功后回到本页点「刷新」，Agent 列应显示「在线」。配置已写入安装包，操作人员无需再改 Token。"
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
