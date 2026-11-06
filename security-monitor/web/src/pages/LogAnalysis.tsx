import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Col, Collapse, Empty, Form, Input, InputNumber, Modal, Popconfirm, Progress, Row,
  Segmented, Select, Space, Spin, Table, Tag, Typography, message,
} from 'antd';
import { DeleteOutlined, EyeOutlined, FileSearchOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { aiApi, logAnalysisApi, serverApi, LogAnalysisParams } from '../services/api';

const { Text, Paragraph } = Typography;

interface Issue {
  title: string;
  severity: string;
  category: string;
  evidence: string[];
  analysis: string;
  solution: string;
  commands: string[];
}

interface Analysis {
  id: number;
  server_id?: number;
  server_name?: string;
  source: 'ssh' | 'manual';
  log_type: string;
  log_label?: string;
  status: 'running' | 'success' | 'failed';
  summary?: string;
  health_score?: number;
  risk_level?: string;
  issues?: Issue[];
  issue_count?: number;
  recommendations?: string[];
  log_excerpt?: string;
  log_stats?: { raw_lines: number; unique_lines: number; chars: number; truncated: boolean };
  provider?: string;
  model?: string;
  usage?: { input_tokens?: number; output_tokens?: number };
  error?: string;
  created_by?: string;
  created_at: string;
  finished_at?: string;
}

const SEVERITY: Record<string, { color: string; label: string }> = {
  critical: { color: 'red', label: '严重' },
  high: { color: 'volcano', label: '高' },
  medium: { color: 'orange', label: '中' },
  low: { color: 'blue', label: '低' },
  info: { color: 'default', label: '提示' },
};

const SINCE_OPTIONS = [
  { value: 60, label: '最近 1 小时' },
  { value: 360, label: '最近 6 小时' },
  { value: 1440, label: '最近 24 小时' },
  { value: 4320, label: '最近 3 天' },
  { value: 10080, label: '最近 7 天' },
];

const severityTag = (s?: string) => {
  const v = SEVERITY[s || ''] || SEVERITY.info;
  return <Tag color={v.color}>{v.label}</Tag>;
};

const scoreColor = (n?: number) => (n === undefined || n === null ? '#999' : n >= 80 ? '#52c41a' : n >= 60 ? '#faad14' : '#ff4d4f');

// 从 JWT 中读取角色（仅用于隐藏按钮，权限以后端为准）
const currentRole = (): string => {
  try {
    const token = localStorage.getItem('token') || '';
    const body = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(body)))).role || 'viewer';
  } catch {
    return 'viewer';
  }
};

const preStyle: React.CSSProperties = {
  background: '#f6f8fa', padding: 12, borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
  fontSize: 12, maxHeight: 400, overflow: 'auto', margin: 0,
};

function AnalysisResult({ data }: { data: Analysis }) {
  if (data.status === 'running') {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
        <div style={{ marginTop: 16, color: '#666' }}>
          {data.source === 'ssh' ? '正在拉取日志并由 AI 分析' : 'AI 正在分析'}，通常需要 30 秒到几分钟，可以离开此页面稍后在历史记录中查看…
        </div>
      </div>
    );
  }
  if (data.status === 'failed') {
    return <Alert type="error" showIcon message="分析失败" description={data.error} />;
  }
  const issues = data.issues || [];
  return (
    <div>
      <Row gutter={24} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="140px" style={{ textAlign: 'center' }}>
          <Progress
            type="circle"
            width={110}
            percent={data.health_score ?? 0}
            strokeColor={scoreColor(data.health_score)}
            format={() => (
              <div>
                <div style={{ fontSize: 26, fontWeight: 600 }}>{data.health_score ?? '-'}</div>
                <div style={{ fontSize: 12, color: '#999' }}>健康分</div>
              </div>
            )}
          />
        </Col>
        <Col flex="auto">
          <Space wrap style={{ marginBottom: 8 }}>
            <span>整体风险：</span>{severityTag(data.risk_level)}
            <Text type="secondary">发现 {issues.length} 个问题</Text>
            {data.log_stats && (
              <Text type="secondary">
                日志 {data.log_stats.raw_lines} 行（折叠后 {data.log_stats.unique_lines} 行）
                {data.log_stats.truncated && '，过长已只保留最新部分'}
              </Text>
            )}
          </Space>
          <Paragraph style={{ fontSize: 15, marginBottom: 0 }}>{data.summary}</Paragraph>
        </Col>
      </Row>

      {issues.length === 0 ? (
        <Empty description="未发现明显问题" />
      ) : (
        <Collapse
          defaultActiveKey={issues.map((_, i) => i).filter((i) => ['critical', 'high'].includes(issues[i].severity))}
          items={issues.map((it, i) => ({
            key: i,
            label: (
              <Space>
                {severityTag(it.severity)}
                <Tag>{it.category}</Tag>
                <Text strong>{it.title}</Text>
              </Space>
            ),
            children: (
              <div>
                {it.evidence.length > 0 && (
                  <>
                    <Text type="secondary">关键日志</Text>
                    <pre style={{ ...preStyle, marginTop: 4, marginBottom: 12 }}>{it.evidence.join('\n')}</pre>
                  </>
                )}
                {it.analysis && (
                  <>
                    <Text type="secondary">原因分析</Text>
                    <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{it.analysis}</Paragraph>
                  </>
                )}
                <Text type="secondary">解决方案</Text>
                <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{it.solution}</Paragraph>
                {it.commands.length > 0 && (
                  <>
                    <Text type="secondary">参考命令（请确认后再执行）</Text>
                    {it.commands.map((c, j) => (
                      <Paragraph key={j} copyable={{ text: c }} style={{ marginBottom: 4 }}>
                        <Text code>{c}</Text>
                      </Paragraph>
                    ))}
                  </>
                )}
              </div>
            ),
          }))}
        />
      )}

      {(data.recommendations || []).length > 0 && (
        <Card size="small" title="优化建议" style={{ marginTop: 16 }}>
          <ul style={{ paddingLeft: 20, margin: 0 }}>
            {data.recommendations!.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </Card>
      )}

      <Collapse
        ghost
        style={{ marginTop: 8 }}
        items={[{
          key: 'raw',
          label: <Text type="secondary">查看发送给 AI 的日志（已脱敏）</Text>,
          children: <pre style={preStyle}>{data.log_excerpt || '（无）'}</pre>,
        }]}
      />
      <Text type="secondary" style={{ fontSize: 12 }}>
        模型：{data.model || '-'}
        {data.usage?.input_tokens !== undefined && ` · 输入 ${data.usage.input_tokens} / 输出 ${data.usage.output_tokens} tokens`}
        {' · '}AI 结论仅供参考，执行修复命令前请人工确认
      </Text>
    </div>
  );
}

export default function LogAnalysis() {
  const [form] = Form.useForm();
  const [source, setSource] = useState<'ssh' | 'manual'>('ssh');
  const [servers, setServers] = useState<any[]>([]);
  const [logTypes, setLogTypes] = useState<{ key: string; label: string; description: string }[]>([]);
  const [aiStatus, setAiStatus] = useState<{ enabled: boolean; model: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<{ content: string; stats: any } | null>(null);
  const [current, setCurrent] = useState<Analysis | null>(null);
  const [history, setHistory] = useState<Analysis[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const pollRef = useRef<number>();
  const canOperate = useMemo(() => ['admin', 'operator'].includes(currentRole()), []);

  const serverId = Form.useWatch('server_id', form);
  const logType = Form.useWatch('log_type', form);
  const selectedServer = servers.find((s) => s.id === serverId);

  useEffect(() => {
    serverApi.list().then((r) => setServers(r.data || [])).catch(() => setServers([]));
    aiApi.status().then((r) => setAiStatus(r.data)).catch(() => setAiStatus(null));
    form.setFieldsValue({ log_type: 'overview', lines: 300, since_minutes: 1440 });
    return () => window.clearInterval(pollRef.current);
  }, []);

  useEffect(() => {
    loadHistory(page);
  }, [page]);

  useEffect(() => {
    const os = selectedServer?.os_type || 'linux';
    logAnalysisApi.logTypes(os).then((r) => {
      setLogTypes(r.data);
      if (!r.data.some((t: any) => t.key === form.getFieldValue('log_type'))) {
        form.setFieldValue('log_type', 'overview');
      }
    });
  }, [selectedServer?.os_type]);

  const loadHistory = async (p = page) => {
    setHistoryLoading(true);
    try {
      const r = await logAnalysisApi.list({ page: p, size: 10 });
      setHistory(r.data.items);
      setTotal(r.data.total);
    } finally {
      setHistoryLoading(false);
    }
  };

  const poll = (id: number) => {
    window.clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await logAnalysisApi.get(id);
        setCurrent(r.data);
        if (r.data.status !== 'running') {
          window.clearInterval(pollRef.current);
          loadHistory(1);
          setPage(1);
        }
      } catch {
        window.clearInterval(pollRef.current);
      }
    };
    tick();
    pollRef.current = window.setInterval(tick, 2500);
  };

  const buildParams = (values: any): LogAnalysisParams => ({ ...values, source });

  const handlePreview = async () => {
    const values = await form.validateFields();
    setPreviewing(true);
    try {
      const r = await logAnalysisApi.fetch(buildParams(values));
      setPreview(r.data);
    } catch (e: any) {
      message.error(e.response?.data?.detail || '拉取日志失败');
    } finally {
      setPreviewing(false);
    }
  };

  const handleAnalyze = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      const r = await logAnalysisApi.create(buildParams(values));
      setCurrent({ id: r.data.id, status: 'running', source, log_type: values.log_type, created_at: '' } as Analysis);
      poll(r.data.id);
    } catch (e: any) {
      message.error(e.response?.data?.detail || '创建分析任务失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await logAnalysisApi.delete(id);
      message.success('已删除');
      if (current?.id === id) setCurrent(null);
      loadHistory();
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败');
    }
  };

  const columns = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss') },
    { title: '服务器', dataIndex: 'server_name', render: (v: string) => v || '-' },
    { title: '日志', dataIndex: 'log_label', width: 110 },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: (v: string) => ({ running: <Tag color="processing">分析中</Tag>, success: <Tag color="success">完成</Tag>,
        failed: <Tag color="error">失败</Tag> } as any)[v],
    },
    { title: '风险', dataIndex: 'risk_level', width: 70, render: (v: string, r: Analysis) => (r.status === 'success' ? severityTag(v) : '-') },
    {
      title: '健康分', dataIndex: 'health_score', width: 80,
      render: (v: number) => (v === null || v === undefined ? '-' : <span style={{ color: scoreColor(v), fontWeight: 600 }}>{v}</span>),
    },
    { title: '问题数', dataIndex: 'issue_count', width: 70 },
    { title: '发起人', dataIndex: 'created_by', width: 90 },
    {
      title: '操作', width: 130,
      render: (_: any, r: Analysis) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => poll(r.id)}>查看</Button>
          {canOperate && (
            <Popconfirm title="确定删除这条分析记录？" onConfirm={() => handleDelete(r.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      {aiStatus && !aiStatus.enabled && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="AI 分析尚未启用"
          description="请管理员在「AI 配置」中填写模型接口与 API Key 并启用。未启用时仍可拉取并预览日志。"
        />
      )}

      <Row gutter={16}>
        <Col xs={24} xl={10}>
          <Card
            title={<Space><RobotOutlined />AI 日志分析</Space>}
            extra={aiStatus?.enabled && <Text type="secondary">模型：{aiStatus.model}</Text>}
          >
            <Segmented
              block
              value={source}
              onChange={(v) => setSource(v as 'ssh' | 'manual')}
              options={[{ value: 'ssh', label: '拉取服务器日志' }, { value: 'manual', label: '粘贴日志' }]}
              style={{ marginBottom: 16 }}
            />
            <Form form={form} layout="vertical" disabled={!canOperate}>
              <Form.Item
                name="server_id"
                label="服务器"
                rules={source === 'ssh' ? [{ required: true, message: '请选择服务器' }] : []}
                extra={source === 'ssh' ? '通过 SSH 拉取，需在服务器管理中配置登录密码' : '可选，用于记录和给 AI 提供系统类型'}
              >
                <Select
                  allowClear={source === 'manual'}
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择服务器"
                  options={servers.map((s) => ({
                    value: s.id,
                    label: `${s.name}（${s.ip} · ${s.os_type === 'windows' ? 'Windows' : 'Linux'}）`,
                  }))}
                />
              </Form.Item>

              {source === 'ssh' ? (
                <>
                  <Form.Item name="log_type" label="日志类型" extra={logTypes.find((t) => t.key === logType)?.description}>
                    <Select options={logTypes.map((t) => ({ value: t.key, label: t.label }))} />
                  </Form.Item>
                  {logType === 'service' && (
                    <Form.Item name="unit" label="服务名" rules={[{ required: true, message: '请输入服务名' }]}>
                      <Input placeholder="如 nginx、docker、mysqld" />
                    </Form.Item>
                  )}
                  {logType === 'file' && (
                    <Form.Item
                      name="path"
                      label="日志文件路径"
                      rules={[{ required: true, message: '请输入日志文件路径' }]}
                      extra="仅允许 /var/log/ 下的文件，或以 .log/.out/.err/.txt 结尾的文件"
                    >
                      <Input placeholder="/var/log/nginx/error.log" />
                    </Form.Item>
                  )}
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="since_minutes" label="时间范围">
                        <Select options={SINCE_OPTIONS} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="lines" label="最多行数">
                        <InputNumber min={10} max={2000} step={100} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                  </Row>
                </>
              ) : (
                <Form.Item name="content" label="日志内容" rules={[{ required: true, message: '请粘贴日志内容' }]}>
                  <Input.TextArea rows={10} placeholder="把要分析的日志粘贴到这里（会自动隐藏密码、Token 等敏感信息）" />
                </Form.Item>
              )}

              <Form.Item name="note" label="补充说明（可选）">
                <Input.TextArea rows={2} placeholder="例如：昨晚 10 点后网站访问很慢" maxLength={1000} />
              </Form.Item>

              <Space>
                {source === 'ssh' && (
                  <Button icon={<FileSearchOutlined />} loading={previewing} onClick={handlePreview}>预览日志</Button>
                )}
                <Button
                  type="primary"
                  icon={<RobotOutlined />}
                  loading={submitting}
                  disabled={!canOperate || !aiStatus?.enabled || current?.status === 'running'}
                  onClick={handleAnalyze}
                >
                  AI 分析
                </Button>
              </Space>
              {!canOperate && <div style={{ marginTop: 8 }}><Text type="secondary">只读账号只能查看分析记录</Text></div>}
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={14}>
          <Card
            title={current ? `分析结果${current.server_name ? ` · ${current.server_name}` : ''}${current.log_label ? ` · ${current.log_label}` : ''}` : '分析结果'}
            style={{ minHeight: 300 }}
          >
            {current ? <AnalysisResult data={current} /> : <Empty description="发起分析或在下方历史记录中查看" />}
          </Card>
        </Col>
      </Row>

      <Card
        title="分析历史"
        style={{ marginTop: 16 }}
        extra={<Button icon={<ReloadOutlined />} onClick={() => loadHistory()}>刷新</Button>}
      >
        <Table
          rowKey="id"
          size="small"
          loading={historyLoading}
          columns={columns}
          dataSource={history}
          scroll={{ x: 900 }}
          pagination={{ current: page, pageSize: 10, total, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
        />
      </Card>

      <Modal
        title="日志预览（已脱敏，与发送给 AI 的内容一致）"
        open={!!preview}
        onCancel={() => setPreview(null)}
        width={900}
        footer={[
          <Button key="close" onClick={() => setPreview(null)}>关闭</Button>,
          <Button key="go" type="primary" disabled={!aiStatus?.enabled} onClick={() => { setPreview(null); handleAnalyze(); }}>
            开始 AI 分析
          </Button>,
        ]}
      >
        {preview && (
          <>
            <Text type="secondary">
              共 {preview.stats.raw_lines} 行，折叠相似行后 {preview.stats.unique_lines} 行，{preview.stats.chars} 字符
              {preview.stats.truncated && '（过长，已只保留最新部分）'}
            </Text>
            <pre style={{ ...preStyle, marginTop: 8, maxHeight: 520 }}>{preview.content || '（所选范围内没有日志）'}</pre>
          </>
        )}
      </Modal>
    </div>
  );
}
