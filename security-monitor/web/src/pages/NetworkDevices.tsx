import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, TreeSelect,
  Typography, message,
} from 'antd';
import { DeleteOutlined, EditOutlined, ImportOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { networkDeviceApi, siteApi } from '../services/api';
import type { DeviceImportRow } from '../services/api';
import { useMe } from '../context/MeContext';
import type { NetworkDevice, Site } from '../types';
import { siteTreeData } from '../utils/siteTree';

const { Text } = Typography;

const TYPE_TEXT: Record<string, string> = { switch: '交换机', router: '路由器', firewall: '防火墙', ap: '无线AP', other: '其他' };
const TYPE_ALIASES: Record<string, string> = {
  交换机: 'switch', switch: 'switch', 路由器: 'router', router: 'router', 防火墙: 'firewall', firewall: 'firewall',
  ap: 'ap', 无线: 'ap', 无线ap: 'ap', 其他: 'other', other: 'other',
};
const VENDORS: Record<string, string> = { huawei: '华为', h3c: '华三', cisco: '思科', ruijie: '锐捷', linux: 'Linux', generic: '通用' };
const SOURCE_TEXT: Record<string, string> = { manual: '手工', import: '导入', discovered: '自动发现', probe: '采集器' };

/** 解析粘贴的设备清单：每行「名称,IP,类型,团体字,端口」，也支持 Tab 分隔（从 Excel 复制）和只有 IP 的行 */
function parseImport(text: string): { rows: DeviceImportRow[]; errors: string[] } {
  const rows: DeviceImportRow[] = [];
  const errors: string[] = [];
  text.split(/\r?\n/).forEach((raw, idx) => {
    const line = raw.trim();
    if (!line || line.startsWith('#')) return;
    const parts = line.split(/[,\t，]/).map((p) => p.trim());
    const ipIndex = parts.findIndex((p) => /^\d{1,3}(\.\d{1,3}){3}$/.test(p));
    if (ipIndex < 0) {
      if (idx === 0 && /ip/i.test(line)) return; // 表头
      errors.push(`第 ${idx + 1} 行没有 IP：${line}`);
      return;
    }
    const name = ipIndex === 0 ? undefined : parts[0];
    const rest = parts.slice(ipIndex + 1);
    const type = TYPE_ALIASES[(rest[0] || '').toLowerCase()] || (rest[0] ? 'other' : 'switch');
    rows.push({
      name: name || undefined,
      ip: parts[ipIndex],
      device_type: type,
      snmp_community: rest[1] || undefined,
      snmp_port: rest[2] ? Number(rest[2]) || 161 : 161,
    });
  });
  return { rows, errors };
}

export default function NetworkDevices() {
  const { canManage } = useMe();
  const [params, setParams] = useSearchParams();
  const [devices, setDevices] = useState<NetworkDevice[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [importVisible, setImportVisible] = useState(false);
  const [editingDevice, setEditingDevice] = useState<NetworkDevice | null>(null);
  const [form] = Form.useForm();
  const [sites, setSites] = useState<Site[]>([]);
  const [keyword, setKeyword] = useState('');
  const [importSite, setImportSite] = useState<number | undefined>();
  const [importText, setImportText] = useState('');
  const [importOverwrite, setImportOverwrite] = useState(false);
  const [importing, setImporting] = useState(false);

  const siteFilter = params.get('site_id') ? Number(params.get('site_id')) : undefined;
  const statusFilter = params.get('status') || undefined;

  const setFilter = (key: string, value?: string | number) => {
    const next = new URLSearchParams(params);
    if (value === undefined || value === null || value === '') next.delete(key);
    else next.set(key, String(value));
    setParams(next);
  };

  useEffect(() => {
    siteApi.list().then((res) => setSites(res.data || [])).catch(() => setSites([]));
  }, []);

  const fetchDevices = async () => {
    setLoading(true);
    try {
      const res = await networkDeviceApi.list({ site_id: siteFilter, status: statusFilter });
      setDevices(res.data);
    } catch {
      message.error('获取网络设备列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDevices();
  }, [siteFilter, statusFilter]);

  const collectSites = useMemo(() => sites.filter((s) => s.site_type !== 'cloud'), [sites]);
  const shown = useMemo(() => {
    const k = keyword.trim().toLowerCase();
    if (!k) return devices;
    return devices.filter((d) => [d.name, d.ip, d.site_name, d.vendor].some((v) => (v || '').toLowerCase().includes(k)));
  }, [devices, keyword]);

  const handleAdd = () => {
    setEditingDevice(null);
    form.resetFields();
    form.setFieldsValue({ snmp_version: 'v2c', snmp_port: 161, device_type: 'switch', site_id: siteFilter });
    setModalVisible(true);
  };

  const handleEdit = (device: NetworkDevice) => {
    setEditingDevice(device);
    form.setFieldsValue({ ...device, snmp_community: undefined });
    setModalVisible(true);
  };

  const handleDelete = async (id: number) => {
    try {
      await networkDeviceApi.delete(id);
      message.success('删除成功');
      fetchDevices();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '删除失败');
    }
  };

  const handleSubmit = async (values: any) => {
    const body = { ...values, snmp_port: Number(values.snmp_port) || 161, site_id: values.site_id ?? null };
    try {
      if (editingDevice) {
        await networkDeviceApi.update(editingDevice.id, body);
        message.success('更新成功');
      } else {
        await networkDeviceApi.create(body);
        message.success('创建成功');
      }
      setModalVisible(false);
      fetchDevices();
    } catch (error: any) {
      message.error(error.response?.data?.detail || (editingDevice ? '更新失败' : '创建失败'));
    }
  };

  const parsed = useMemo(() => parseImport(importText), [importText]);

  const openImport = () => {
    setImportSite(siteFilter);
    setImportText('');
    setImportOverwrite(false);
    setImportVisible(true);
  };

  const submitImport = async () => {
    if (!importSite) { message.error('请选择站点'); return; }
    if (!parsed.rows.length) { message.error('没有可导入的设备'); return; }
    setImporting(true);
    try {
      const res = await networkDeviceApi.import(importSite, parsed.rows, importOverwrite);
      const { created, updated, skipped, errors } = res.data;
      Modal.success({
        title: '导入完成',
        content: (
          <div>
            新增 {created} 台，更新 {updated} 台，跳过（已存在）{skipped} 台。
            {errors?.length > 0 && <div style={{ marginTop: 8, color: '#cf1322' }}>{errors.join('；')}</div>}
            <div style={{ marginTop: 8 }}>站点采集器下一轮会自动拉取新设备，无需到现场修改配置。</div>
          </div>
        ),
      });
      setImportVisible(false);
      setFilter('site_id', importSite);
      fetchDevices();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '导入失败');
    } finally {
      setImporting(false);
    }
  };

  const statusTag = (status: string) => {
    const map: Record<string, [string, string]> = {
      online: ['green', '在线'], offline: ['default', '离线'], error: ['red', 'SNMP 不通'],
    };
    const [color, text] = map[status] || ['default', status];
    return <Tag color={color}>{text}</Tag>;
  };

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
    {
      title: '站点', dataIndex: 'site_name', key: 'site_name', width: 130, ellipsis: true,
      render: (v: string) => v || <Text type="secondary">总部直连</Text>,
    },
    { title: 'IP地址', dataIndex: 'ip', key: 'ip', width: 130 },
    { title: '类型', dataIndex: 'device_type', key: 'device_type', width: 80, render: (t: string) => TYPE_TEXT[t] || '其他' },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 70, render: (v: string) => VENDORS[v] || v || '-' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: statusTag },
    {
      title: 'CPU / 内存', key: 'res', width: 110,
      render: (_: unknown, r: NetworkDevice) => r.cpu == null && r.memory == null ? '-'
        : `${r.cpu == null ? '-' : Math.round(r.cpu)}% / ${r.memory == null ? '-' : Math.round(r.memory)}%`,
    },
    {
      title: '端口 UP', key: 'if', width: 80,
      render: (_: unknown, r: NetworkDevice) => r.if_total ? `${r.if_up}/${r.if_total}` : '-',
    },
    { title: '来源', dataIndex: 'source', key: 'source', width: 80, render: (v: string) => <Tag>{SOURCE_TEXT[v] || '手工'}</Tag> },
    {
      title: '最后上报', dataIndex: 'last_seen', key: 'last_seen', width: 150,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    ...(canManage ? [{
      title: '操作', key: 'action', width: 140, fixed: 'right' as const,
      render: (_: any, record: NetworkDevice) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除此设备？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    }] : []),
  ];

  return (
    <div>
      <Card
        title="网络设备管理"
        extra={
          <Space wrap>
            <TreeSelect allowClear treeDefaultExpandAll placeholder="全部站点" style={{ width: 200 }}
              value={siteFilter} onChange={(v) => setFilter('site_id', v)} treeData={siteTreeData(collectSites)} />
            <Select allowClear placeholder="全部状态" style={{ width: 120 }} value={statusFilter}
              onChange={(v) => setFilter('status', v)}
              options={[{ value: 'online', label: '在线' }, { value: 'offline', label: '离线' }, { value: 'error', label: 'SNMP 不通' }]} />
            <Input.Search allowClear placeholder="名称 / IP" style={{ width: 160 }} onSearch={setKeyword}
              onChange={(e) => !e.target.value && setKeyword('')} />
            <Button icon={<ReloadOutlined />} onClick={fetchDevices}>刷新</Button>
            {canManage && <Button icon={<ImportOutlined />} onClick={openImport}>批量导入</Button>}
            {canManage && <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加设备</Button>}
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={shown}
          rowKey="id"
          loading={loading}
          size="middle"
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 台设备` }}
          scroll={{ x: 1350 }}
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
          <Form.Item name="site_id" label="所属站点"
            extra="选择站点后由该站点的采集器（或总部平台轮询）采集；团体字会自动下发给采集器。不选则由总部平台直接 SNMP 轮询。">
            <TreeSelect allowClear treeDefaultExpandAll placeholder="总部直连（不归属站点）" treeData={siteTreeData(collectSites)} />
          </Form.Item>
          <Form.Item name="name" label="设备名称" rules={[{ required: true, message: '请输入设备名称' }]}>
            <Input placeholder="例如：核心交换机-01" />
          </Form.Item>
          <Form.Item name="ip" label="IP地址" rules={[{ required: true, message: '请输入IP地址' }]}>
            <Input placeholder="例如：192.168.1.1" />
          </Form.Item>
          <Form.Item name="device_type" label="设备类型" rules={[{ required: true, message: '请选择设备类型' }]}>
            <Select options={Object.entries(TYPE_TEXT).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="snmp_version" label="SNMP版本" rules={[{ required: true }]}>
            <Select options={[{ value: 'v2c', label: 'SNMP v2c' }]} />
          </Form.Item>
          <Form.Item name="snmp_community" label="SNMP 团体字"
            extra={editingDevice ? '留空保持原值；站点设置了默认团体字时也可不填' : '留空则使用站点默认团体字'}>
            <Input.Password placeholder={editingDevice ? '******' : '例如：public'} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="snmp_port" label="SNMP端口">
            <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="默认 161" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="批量导入网络设备" open={importVisible} onCancel={() => setImportVisible(false)} onOk={submitImport}
        okText={`导入 ${parsed.rows.length} 台`} confirmLoading={importing} width={720} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message="每行一台：名称,IP,类型,团体字,端口（可直接从 Excel 复制粘贴）。只有 IP 也可以；类型可写 交换机/路由器/防火墙/AP；团体字留空则使用站点默认值。" />
        <Space direction="vertical" style={{ width: '100%' }}>
          <TreeSelect allowClear treeDefaultExpandAll placeholder="导入到哪个站点" style={{ width: '100%' }}
            value={importSite} onChange={setImportSite} treeData={siteTreeData(collectSites)} />
          <Input.TextArea rows={10} value={importText} onChange={(e) => setImportText(e.target.value)}
            placeholder={'核心交换机,192.168.1.1,交换机,public\n接入交换机-1,192.168.1.2,交换机\n出口路由器,192.168.1.254,路由器,private,161\n192.168.1.3'} />
          <Space>
            <Checkbox checked={importOverwrite} onChange={(e) => setImportOverwrite(e.target.checked)}>同 IP 已存在时覆盖名称 / 类型 / 团体字</Checkbox>
          </Space>
          <Text type="secondary">识别到 {parsed.rows.length} 台设备{parsed.errors.length ? `，${parsed.errors.length} 行无法识别` : ''}</Text>
          {parsed.errors.length > 0 && <Text type="danger" style={{ fontSize: 12 }}>{parsed.errors.slice(0, 5).join('；')}</Text>}
        </Space>
      </Modal>
    </div>
  );
}
