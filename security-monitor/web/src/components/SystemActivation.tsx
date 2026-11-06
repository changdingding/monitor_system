import { useEffect, useState } from 'react';
import { Alert, Button, Card, Checkbox, Descriptions, Input, Modal, Space, Table, Tag, Typography, message } from 'antd';
import { CheckCircleOutlined, ClearOutlined, LockOutlined, RocketOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { systemApi } from '../services/api';
import { useMe } from '../context/MeContext';
import type { SystemState } from '../types';

const { Text, Paragraph } = Typography;

interface Category {
  key: string;
  label: string;
  total: number;
  tables: { table: string; label: string; count: number }[];
}

/** 系统启用：实施期可清理测试数据；正式启用后不可清除、不可撤销，所有操作留审计 */
export default function SystemActivation() {
  const navigate = useNavigate();
  const { reload: reloadMe } = useMe();
  const [state, setState] = useState<SystemState | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [chosen, setChosen] = useState<string[]>(['monitoring']);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [activateOpen, setActivateOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [s, p] = await Promise.all([systemApi.state(), systemApi.purgePreview()]);
      setState(s.data);
      setCategories(p.data.categories || []);
    } catch {
      message.error('获取系统状态失败');
    }
  };

  useEffect(() => { load(); }, []);

  const doPurge = async () => {
    setBusy(true);
    try {
      const res = await systemApi.purge(chosen, confirmText);
      const deleted = Object.entries(res.data.deleted || {}).filter(([, n]) => Number(n) > 0);
      Modal.success({
        title: '清理完成',
        content: deleted.length ? deleted.map(([k, n]) => `${k} ${n} 条`).join('；') : '没有需要清理的数据',
      });
      setPurgeOpen(false);
      setConfirmText('');
      load();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '清理失败');
    } finally {
      setBusy(false);
    }
  };

  const doActivate = async () => {
    setBusy(true);
    try {
      await systemApi.activate(confirmText, note);
      message.success('系统已正式启用');
      setActivateOpen(false);
      setConfirmText('');
      load();
      reloadMe();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '启用失败');
    } finally {
      setBusy(false);
    }
  };

  if (!state) return <Card title="系统启用" loading />;

  if (state.activated) {
    return (
      <Card title={<Space><LockOutlined />系统启用</Space>} extra={<Tag color="green" icon={<CheckCircleOutlined />}>已正式启用</Tag>}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="启用时间">{state.activated_at ? new Date(state.activated_at).toLocaleString('zh-CN') : '-'}</Descriptions.Item>
          <Descriptions.Item label="启用人">{state.activated_by || '-'}</Descriptions.Item>
          {state.note && <Descriptions.Item label="备注">{state.note}</Descriptions.Item>}
          <Descriptions.Item label="操作审计">
            已记录 {state.audit_log_count ?? 0} 条 <Button type="link" size="small" onClick={() => navigate('/audit-logs')}>查询</Button>
          </Descriptions.Item>
        </Descriptions>
        <Alert style={{ marginTop: 16 }} type="success" showIcon
          message="系统已正式运行：数据清理功能永久关闭，告警、日志、分析记录不可清除；所有配置变更都记录在操作审计中。" />
      </Card>
    );
  }

  const columns = [
    { title: '类别', dataIndex: 'label', key: 'label' },
    { title: '数据量', key: 'total', width: 100, render: (_: unknown, r: Category) => <Text strong>{r.total}</Text> },
    {
      title: '明细', key: 'tables', width: 340,
      render: (_: unknown, r: Category) => (
        <Space size={[4, 4]} wrap>
          {r.tables.map((t) => <Tag key={t.table}>{t.label} {t.count}</Tag>)}
        </Space>
      ),
    },
  ];

  return (
    <Card title={<Space><RocketOutlined />系统启用</Space>} extra={<Tag color="orange">实施模式（未启用）</Tag>}>
      <Alert type="warning" showIcon style={{ marginBottom: 16 }}
        message="当前为实施模式"
        description="实施、联调期间产生的测试数据（告警、日志、测试设备、测试账号等）可以在这里清理。确认一切就绪后点击「正式启用」：启用后数据不可清除，且无法撤销。清理和启用操作本身都会记录在操作审计中，操作审计任何时候都不会被清理。" />
      <Table rowKey="key" size="small" pagination={false} columns={columns} dataSource={categories}
        rowSelection={{ selectedRowKeys: chosen, onChange: (keys) => setChosen(keys as string[]) }} />
      <Space style={{ marginTop: 16 }}>
        <Button danger icon={<ClearOutlined />} disabled={!chosen.length} onClick={() => { setConfirmText(''); setPurgeOpen(true); }}>
          清理所选数据
        </Button>
        <Button type="primary" icon={<RocketOutlined />} onClick={() => { setConfirmText(''); setNote(''); setActivateOpen(true); }}>
          正式启用系统
        </Button>
      </Space>

      <Modal title="清理实施数据" open={purgeOpen} onCancel={() => setPurgeOpen(false)} onOk={doPurge}
        okText="清理" okButtonProps={{ danger: true, disabled: confirmText !== '确认清理' }} confirmLoading={busy}>
        <Paragraph>将清理以下类别的全部数据，<Text type="danger">不可恢复</Text>：</Paragraph>
        <ul>
          {categories.filter((c) => chosen.includes(c.key)).map((c) => <li key={c.key}>{c.label}（{c.total} 条）</li>)}
        </ul>
        {chosen.includes('assets') && !chosen.includes('monitoring') && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message="删除资产会连带删除其登录日志、指标等监控数据。" />
        )}
        <Paragraph>请输入 <Text code>确认清理</Text>：</Paragraph>
        <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder="确认清理" />
        <Checkbox checked disabled style={{ marginTop: 12 }}>操作审计保留，不会被清理</Checkbox>
      </Modal>

      <Modal title="正式启用系统" open={activateOpen} onCancel={() => setActivateOpen(false)} onOk={doActivate}
        okText="正式启用" okButtonProps={{ disabled: confirmText !== '确认启用' }} confirmLoading={busy}>
        <Alert type="error" showIcon style={{ marginBottom: 12 }}
          message="启用后无法撤销" description="启用后数据清理功能永久关闭，所有监控记录不可清除。请确认实施测试数据已清理完毕。" />
        <Paragraph>备注（可选，例如上线批次 / 验收单号）：</Paragraph>
        <Input.TextArea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        <Paragraph style={{ marginTop: 12 }}>请输入 <Text code>确认启用</Text>：</Paragraph>
        <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder="确认启用" />
      </Modal>
    </Card>
  );
}
