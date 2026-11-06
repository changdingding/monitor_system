import { useState, useEffect } from 'react';
import { Table, Button, Modal, Form, Input, Select, Tag, Space, message, Popconfirm, Card } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { networkDeviceApi, siteApi } from '../services/api';
import type { NetworkDevice, Site } from '../types';

export default function NetworkDevices() {
  const [devices, setDevices] = useState<NetworkDevice[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingDevice, setEditingDevice] = useState<NetworkDevice | null>(null);
  const [form] = Form.useForm();
  const [sites, setSites] = useState<Site[]>([]);

  useEffect(() => {
    fetchDevices();
    siteApi.list().then((res) => setSites(res.data || [])).catch(() => setSites([]));
  }, []);

  const fetchDevices = async () => {
    setLoading(true);
    try {
      const res = await networkDeviceApi.list();
      setDevices(res.data);
    } catch (error) {
      message.error('获取网络设备列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    setEditingDevice(null);
    form.resetFields();
    form.setFieldsValue({ snmp_version: 'v2c', snmp_port: 161, syslog_enabled: false, syslog_port: 514 });
    setModalVisible(true);
  };

  const handleEdit = (device: NetworkDevice) => {
    setEditingDevice(device);
    form.setFieldsValue(device);
    setModalVisible(true);
  };

  const handleDelete = async (id: number) => {
    try {
      await networkDeviceApi.delete(id);
      message.success('删除成功');
      fetchDevices();
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSubmit = async (values: any) => {
    try {
      if (editingDevice) {
        await networkDeviceApi.update(editingDevice.id, values);
        message.success('更新成功');
      } else {
        await networkDeviceApi.create(values);
        message.success('创建成功');
      }
      setModalVisible(false);
      fetchDevices();
    } catch (error) {
      message.error(editingDevice ? '更新失败' : '创建失败');
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'online': return 'green';
      case 'offline': return 'default';
      case 'error': return 'red';
      default: return 'default';
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'online': return '在线';
      case 'offline': return '离线';
      case 'error': return '异常';
      default: return status;
    }
  };

  const getDeviceTypeText = (type: string) => {
    switch (type) {
      case 'switch': return '交换机';
      case 'router': return '路由器';
      case 'firewall': return '防火墙';
      default: return '其他';
    }
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 150 },
    { title: '门店', dataIndex: 'site_name', key: 'site_name', width: 120, render: (v: string) => v || '总部直连' },
    { title: 'IP地址', dataIndex: 'ip', key: 'ip', width: 140 },
    { title: '设备类型', dataIndex: 'device_type', key: 'device_type', width: 100,
      render: (type: string) => getDeviceTypeText(type) },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 80,
      render: (v: string) => {
        const names: Record<string, string> = { huawei: '华为', h3c: '华三', cisco: '思科', ruijie: '锐捷', linux: 'Linux', generic: '通用' };
        return names[v] || v || '-';
      } },
    { title: 'SNMP版本', dataIndex: 'snmp_version', key: 'snmp_version', width: 100,
      render: (v: string) => <Tag>{v}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (status: string) => <Tag color={getStatusColor(status)}>{getStatusText(status)}</Tag> },
    { title: '操作', key: 'action', width: 120,
      render: (_: any, record: NetworkDevice) => (
        <Space>
          <Button type="link" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除此设备？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="网络设备管理"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchDevices}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加设备</Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={devices}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 台设备` }}
          scroll={{ x: 1100 }}
        />
      </Card>

      <Modal
        title={editingDevice ? '编辑设备' : '添加设备'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => form.submit()}
        destroyOnClose
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="site_id" label="所属门店" extra="选了门店后，由店内采集器上报，总部不再 SNMP 轮询。团体字写在采集器配置里。">
            <Select allowClear placeholder="不选则由总部直接 SNMP 轮询">
              {sites.filter((site) => site.site_type === 'store').map((site) => (
                <Select.Option key={site.id} value={site.id}>{site.name}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="name" label="设备名称" rules={[{ required: true, message: '请输入设备名称' }]}>
            <Input placeholder="例如：核心交换机-01" />
          </Form.Item>
          <Form.Item name="ip" label="IP地址" rules={[{ required: true, message: '请输入IP地址' }]}>
            <Input placeholder="例如：192.168.1.1" />
          </Form.Item>
          <Form.Item name="device_type" label="设备类型" rules={[{ required: true, message: '请选择设备类型' }]}>
            <Select placeholder="选择设备类型">
              <Select.Option value="switch">交换机</Select.Option>
              <Select.Option value="router">路由器</Select.Option>
              <Select.Option value="firewall">防火墙</Select.Option>
              <Select.Option value="other">其他</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="snmp_version" label="SNMP版本" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="v2c">SNMP v2c</Select.Option>
              <Select.Option value="v3">SNMP v3</Select.Option>
            </Select>
          </Form.Item>
          {/* antd 不支持在带 name 的 Form.Item 里用渲染函数（原写法导致输入框不显示）：外层监听版本，内层绑定字段 */}
          <Form.Item noStyle dependencies={['snmp_version']}>
            {({ getFieldValue }) => getFieldValue('snmp_version') === 'v2c' ? (
              <Form.Item name="snmp_community" label="SNMP Community">
                <Input.Password placeholder="例如：public" autoComplete="new-password" />
              </Form.Item>
            ) : null}
          </Form.Item>
          <Form.Item name="snmp_port" label="SNMP端口">
            <Input type="number" placeholder="默认 161" />
          </Form.Item>
          {/* Syslog 接收尚未实现，暂不提供开关（数据库字段保留） */}
        </Form>
      </Modal>
    </div>
  );
}
