import { useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Divider, Form, Input, InputNumber, Modal, Popconfirm, Radio, Row, Select, Space, Switch,
  Table, Tag, TreeSelect, Typography, message,
} from 'antd';
import {
  ApartmentOutlined, CloudOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, HomeOutlined, PlusOutlined,
  ReloadOutlined, ShopOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { siteApi } from '../services/api';
import { useMe } from '../context/MeContext';
import type { Site, SiteType } from '../types';
import { buildSiteTree, siteTreeData } from '../utils/siteTree';
import { SITE_TYPE_LABELS } from '../types';

const { Paragraph, Text } = Typography;

const TYPE_COLOR: Record<SiteType, string> = { hq: 'magenta', branch: 'geekblue', store: 'purple', cloud: 'blue' };
const TYPE_ICON: Record<SiteType, JSX.Element> = {
  hq: <HomeOutlined />, branch: <ApartmentOutlined />, store: <ShopOutlined />, cloud: <CloudOutlined />,
};

export default function Sites() {
  const navigate = useNavigate();
  const { canManage } = useMe();
  const [sites, setSites] = useState<Site[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [probeVisible, setProbeVisible] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [probeSite, setProbeSite] = useState<Site | null>(null);
  const [serverUrl, setServerUrl] = useState(`${window.location.protocol}//${window.location.hostname}:8000`);
  const [downloading, setDownloading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [expanded, setExpanded] = useState<number[]>([]);
  const [form] = Form.useForm();
  const siteType = Form.useWatch('site_type', form) as SiteType | undefined;
  const collectMode = Form.useWatch('collect_mode', form);

  const load = async () => {
    setLoading(true);
    try {
      const res = await siteApi.list();
      setSites(res.data);
      // 默认展开全部有下级的站点
      const parents = new Set<number>((res.data || []).map((s: Site) => s.parent_id).filter(Boolean));
      setExpanded(Array.from(parents));
    } catch {
      message.error('获取站点列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const tree = useMemo(() => {
    if (!keyword.trim()) return buildSiteTree(sites);
    const k = keyword.trim().toLowerCase();
    return sites
      .filter((s) => [s.name, s.city, s.code].some((v) => (v || '').toLowerCase().includes(k)))
      .map((s) => ({ ...s }));
  }, [sites, keyword]);

  // 编辑时上级不能选自己和自己的下级
  const blockedParents = useMemo(() => {
    if (!editing) return new Set<number>();
    const blocked = new Set<number>([editing.id]);
    let grew = true;
    while (grew) {
      grew = false;
      sites.forEach((s) => {
        if (s.parent_id && blocked.has(s.parent_id) && !blocked.has(s.id)) { blocked.add(s.id); grew = true; }
      });
    }
    return blocked;
  }, [editing, sites]);

  const openCreate = (parent?: Site) => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      site_type: parent ? 'store' : (sites.some((s) => s.site_type === 'hq') ? 'store' : 'hq'),
      parent_id: parent?.id ?? sites.find((s) => s.site_type === 'hq')?.id,
      probe_interval_seconds: 60,
      scan_enabled: false,
    });
    setModalVisible(true);
  };

  const openEdit = (site: Site) => {
    setEditing(site);
    form.resetFields();
    form.setFieldsValue({ ...site, snmp_community: undefined });
    setModalVisible(true);
  };

  const openProbe = (site: Site) => {
    setProbeSite(site);
    setProbeVisible(true);
  };

  const submit = async (values: any) => {
    const body = { ...values, parent_id: values.parent_id ?? null };
    try {
      const res = editing ? await siteApi.update(editing.id, body) : await siteApi.create(body);
      message.success(editing ? '已更新' : '已创建');
      setModalVisible(false);
      await load();
      if (!editing && res.data?.probe_token && res.data?.collect_mode !== 'central') {
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
    } catch (error: any) {
      message.error(error.response?.data?.detail || '删除失败');
    }
  };

  const clearCommunity = async () => {
    if (!editing) return;
    try {
      await siteApi.clearCommunity(editing.id);
      message.success('已清除默认团体字');
      setEditing({ ...editing, has_community: false });
      load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '操作失败');
    }
  };

  const resetToken = async () => {
    if (!probeSite) return;
    try {
      const res = await siteApi.resetToken(probeSite.id);
      setProbeSite({ ...probeSite, probe_token: res.data.probe_token });
      message.success('Token 已重置，旧采集器将无法上报，请在采集电脑上运行「修改配置」填写新 Token');
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
      link.download = `EdgeProbe-${probeSite.name}.zip`;
      link.click();
      window.URL.revokeObjectURL(link.href);
      message.success('安装包已开始下载');
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

  const collectTag = (record: Site) => {
    if (record.site_type === 'cloud') return <Tag>Agent 直连</Tag>;
    if (record.collect_mode === 'central') return <Tag color="cyan">平台直接轮询</Tag>;
    if (record.site_type !== 'store' && !record.last_seen) return <Tag>未部署采集器</Tag>;
    return (
      <Space size={2}>
        <Tag color={record.status === 'online' ? 'green' : 'default'}>{record.status === 'online' ? '采集器在线' : '采集器离线'}</Tag>
        {record.probe_version && <Text type="secondary" style={{ fontSize: 12 }}>v{record.probe_version}</Text>}
      </Space>
    );
  };

  const columns = [
    {
      title: '名称', dataIndex: 'name', key: 'name',
      render: (v: string, r: Site) => <Space>{TYPE_ICON[r.site_type]}<span>{v}</span>{r.code && <Text type="secondary">#{r.code}</Text>}</Space>,
    },
    {
      title: '类型', dataIndex: 'site_type', key: 'site_type', width: 80,
      render: (v: SiteType) => <Tag color={TYPE_COLOR[v]}>{SITE_TYPE_LABELS[v] || v}</Tag>,
    },
    { title: '城市', dataIndex: 'city', key: 'city', width: 90, render: (v: string) => v || '-' },
    { title: '采集方式', key: 'collect', width: 170, render: (_: unknown, r: Site) => collectTag(r) },
    {
      title: '资产', key: 'assets', width: 130,
      render: (_: unknown, r: Site) => (
        <Space size={4}>
          <a onClick={() => navigate(`/network-devices?site_id=${r.id}`)}>设备 {r.device_count ?? 0}</a>
          <a onClick={() => navigate(`/servers?site_id=${r.id}`)}>服务器 {r.server_count ?? 0}</a>
        </Space>
      ),
    },
    {
      title: '自动发现', key: 'scan', width: 150,
      render: (_: unknown, r: Site) => r.site_type === 'cloud' ? '-' : r.scan_enabled
        ? <Text style={{ fontSize: 12 }}>{(r.scan_cidrs || '').split(',').join('、')}</Text>
        : <Text type="secondary">未开启</Text>,
    },
    {
      title: '最后心跳', dataIndex: 'last_seen', key: 'last_seen', width: 160,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
    ...(canManage ? [{
      title: '操作', key: 'action', width: 290,
      render: (_: unknown, record: Site) => (
        <Space size={0}>
          {record.site_type !== 'cloud' && (
            <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => openProbe(record)}>采集器</Button>
          )}
          {record.site_type !== 'cloud' && (
            <Button type="link" size="small" icon={<PlusOutlined />} onClick={() => openCreate(record)}>下级</Button>
          )}
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
          <Popconfirm title="删除后，名下服务器和设备会变为未归属" onConfirm={() => remove(record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    }] : []),
  ];

  const counts = (t: SiteType) => sites.filter((s) => s.site_type === t).length;

  return (
    <div>
      <Card
        title={<Space>站点 / 分支管理
          <Text type="secondary" style={{ fontSize: 13, fontWeight: 400 }}>
            总部 {counts('hq')} · 分支 {counts('branch')} · 门店 {counts('store')} · 云端 {counts('cloud')}
          </Text></Space>}
        extra={
          <Space>
            <Input.Search allowClear placeholder="名称 / 城市 / 编码" onSearch={setKeyword} onChange={(e) => !e.target.value && setKeyword('')} style={{ width: 200 }} />
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            {canManage && <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>添加站点</Button>}
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="按「总部 → 分支（区域）→ 门店」建立层级。门店和分支通常放一台边缘采集器主动上报；总部机房与平台在同一内网时可选「平台直接轮询」，无需采集器。网络设备、团体字、自动发现网段都在总部统一配置，采集器自动拉取。"
        />
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={tree}
          size="middle"
          expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: (keys) => setExpanded(keys as number[]) }}
          pagination={false}
          scroll={{ x: 1200 }}
        />
      </Card>

      <Modal
        title={editing ? '编辑站点' : '添加站点'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => form.submit()}
        destroyOnClose
        width={720}
      >
        <Form form={form} layout="vertical" onFinish={submit}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
                <Input placeholder="例如：上海南京路店 / 华东区 / 总部" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="site_type" label="类型" rules={[{ required: true }]}>
                <Select onChange={(v) => form.setFieldsValue({ collect_mode: v === 'hq' ? 'central' : 'probe' })}>
                  <Select.Option value="hq">总部</Select.Option>
                  <Select.Option value="branch">分支 / 区域</Select.Option>
                  <Select.Option value="store">门店</Select.Option>
                  <Select.Option value="cloud">云端（服务器直接装 Agent）</Select.Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="parent_id" label="上级站点" extra="门店挂在分支或总部下；授权某个分支的用户可看到其下全部门店">
                <TreeSelect allowClear treeDefaultExpandAll placeholder="不选为顶级"
                  treeData={siteTreeData(sites.filter((s) => s.site_type !== 'cloud'), (s) => blockedParents.has(s.id))} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="code" label="编码"><Input placeholder="可选" /></Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="city" label="城市"><Input placeholder="可选" /></Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="address" label="地址"><Input placeholder="可选" /></Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="contact" label="联系人"><Input placeholder="可选" /></Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="phone" label="电话"><Input placeholder="可选" /></Form.Item>
            </Col>
          </Row>
          {siteType !== 'cloud' && (
            <>
              <Divider orientation="left" plain>网络设备采集（集中下发给采集器）</Divider>
              <Form.Item name="collect_mode" label="采集方式" initialValue="probe">
                <Radio.Group>
                  <Radio value="probe">站内边缘采集器上报</Radio>
                  <Radio value="central">总部平台直接 SNMP 轮询（平台与设备同一内网）</Radio>
                </Radio.Group>
              </Form.Item>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item name="snmp_community" label="默认 SNMP 团体字"
                    extra={editing?.has_community ? <span>已设置（留空保持不变）<a onClick={clearCommunity} style={{ marginLeft: 8 }}>清除</a></span> : '设备未单独填写团体字时使用'}>
                    <Input.Password autoComplete="new-password" placeholder={editing?.has_community ? '******' : '例如：public'} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="probe_interval_seconds" label="采集间隔（秒）">
                    <InputNumber min={30} max={3600} style={{ width: '100%' }} disabled={collectMode === 'central'} />
                  </Form.Item>
                </Col>
              </Row>
              {collectMode !== 'central' && (
                <>
                  <Form.Item name="scan_enabled" label="自动发现设备" valuePropName="checked"
                    extra="开启后采集器每小时扫描下列网段，SNMP 有响应的设备自动建档，无需逐台录入">
                    <Switch />
                  </Form.Item>
                  <Form.Item noStyle dependencies={['scan_enabled']}>
                    {({ getFieldValue }) => getFieldValue('scan_enabled') ? (
                      <Form.Item name="scan_cidrs" label="扫描网段" rules={[{ required: true, message: '请填写网段' }]}
                        extra="逗号或换行分隔，最多 8 个，单个不大于 /22，仅限内网地址">
                        <Input.TextArea rows={2} placeholder="192.168.1.0/24, 192.168.2.0/24" />
                      </Form.Item>
                    ) : null}
                  </Form.Item>
                </>
              )}
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title={`边缘采集器 - ${probeSite?.name || ''}`}
        open={probeVisible}
        onCancel={() => setProbeVisible(false)}
        footer={[
          <Popconfirm key="reset" title="重置后旧 Token 立即失效，确定？" onConfirm={resetToken}>
            <Button>重置 Token</Button>
          </Popconfirm>,
          <Button key="close" type="primary" onClick={() => setProbeVisible(false)}>关闭</Button>,
        ]}
        width={660}
      >
        {probeSite && (
          <div>
            {probeSite.collect_mode === 'central' && (
              <Alert type="info" showIcon style={{ marginBottom: 16 }}
                message="该站点当前为「平台直接轮询」，不需要采集器。如改为采集器上报，请先编辑站点修改采集方式。" />
            )}
            <Paragraph><Text strong>站点 Token</Text></Paragraph>
            <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
              <Input.Password value={probeSite.probe_token} readOnly />
              <Button onClick={copyToken}>复制</Button>
            </Space.Compact>
            <Paragraph>
              <Text strong>管理端地址</Text>
              <Text type="secondary">（站点电脑必须能访问，不要填 localhost）</Text>
            </Paragraph>
            <Input value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="http://总部IP:8000" style={{ marginBottom: 16 }} />
            <Button type="primary" icon={<DownloadOutlined />} loading={downloading} onClick={downloadPackage}>
              下载安装包
            </Button>
            <Alert
              style={{ marginTop: 16 }}
              type="success"
              showIcon
              message="安装步骤"
              description={
                <ol style={{ paddingLeft: 18, margin: 0 }}>
                  <li>解压到站点内一台常开、能访问交换机的电脑（需 Python 3.8+）。</li>
                  <li>Windows：双击「安装采集器.bat」→ 授权 → 向导中可修改总部地址 / Token → 测试连接 → 安装（开机自启）。</li>
                  <li>Linux：<Text code>sudo sh linux/install.sh</Text>（systemd 服务，开机自启）。</li>
                  <li>设备在总部配置：<a onClick={() => navigate(`/network-devices?site_id=${probeSite.id}`)}>批量导入设备</a>，或编辑站点开启自动发现。采集器自动拉取，无需改本地文件。</li>
                  <li>以后改参数：运行安装目录的「修改配置.bat」，或 <Text code>install.sh --config</Text>。</li>
                </ol>
              }
            />
          </div>
        )}
      </Modal>
    </div>
  );
}
