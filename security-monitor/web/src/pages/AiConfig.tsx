import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, InputNumber, Radio, Select, Space, Switch, Tag, Typography, message } from 'antd';
import { ApiOutlined, SaveOutlined } from '@ant-design/icons';
import { aiApi } from '../services/api';

const { Text } = Typography;

interface Preset {
  key: string;
  label: string;
  provider: 'anthropic' | 'openai';
  base_url: string;
  model: string;
}

export default function AiConfig() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [keySet, setKeySet] = useState(false);
  const provider = Form.useWatch('provider', form);

  const apply = (data: any) => {
    setPresets(data.presets || []);
    setKeySet(!!data.api_key_set);
    form.setFieldsValue({ ...data, api_key: '' });
  };

  useEffect(() => {
    setLoading(true);
    aiApi.getConfig()
      .then((r) => apply(r.data))
      .catch((e) => message.error(e.response?.data?.detail || '加载 AI 配置失败'))
      .finally(() => setLoading(false));
  }, []);

  const save = async (extra: Record<string, unknown> = {}) => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const r = await aiApi.updateConfig({ ...values, ...extra });
      apply(r.data);
      message.success('已保存');
      return true;
    } catch (e: any) {
      message.error(e.response?.data?.detail || '保存失败');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!(await save())) return;
    setTesting(true);
    try {
      const r = await aiApi.test();
      message.success(`连接成功：${r.data.summary || 'AI 已返回分析结果'}`, 6);
    } catch (e: any) {
      message.error(e.response?.data?.detail || '测试失败', 8);
    } finally {
      setTesting(false);
    }
  };

  const applyPreset = (key: string) => {
    const p = presets.find((x) => x.key === key);
    if (p) form.setFieldsValue({ provider: p.provider, base_url: p.base_url, model: p.model });
  };

  return (
    <Card title="AI 配置" loading={loading}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 24 }}
        message="用于「AI 日志分析」"
        description={
          <div>
            日志在发送前会自动隐藏密码、Token、密钥等敏感信息，但仍包含主机名、IP、用户名等内容。
            如果日志不能离开内网，请选择「本地 Ollama」等私有部署的模型。
          </div>
        }
      />
      <Form form={form} layout="vertical" style={{ maxWidth: 640 }} onFinish={() => save()}>
        <Form.Item name="enabled" label="启用 AI 分析" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Form.Item label="快速选择">
          <Select
            placeholder="选择常用模型服务，自动填入下方配置"
            options={presets.map((p) => ({ value: p.key, label: p.label }))}
            onChange={applyPreset}
          />
        </Form.Item>

        <Form.Item name="provider" label="接口类型">
          <Radio.Group>
            <Radio.Button value="anthropic">Claude（Anthropic）</Radio.Button>
            <Radio.Button value="openai">OpenAI 兼容接口</Radio.Button>
          </Radio.Group>
        </Form.Item>

        <Form.Item
          name="base_url"
          label="接口地址"
          rules={provider === 'openai' ? [{ required: true, message: 'OpenAI 兼容接口需要填写地址' }] : []}
          extra={provider === 'openai'
            ? '填到 /v1 这一级，例如 https://api.deepseek.com/v1（系统会请求 {地址}/chat/completions）'
            : '留空使用官方地址 https://api.anthropic.com；使用代理或网关时填写其地址'}
        >
          <Input placeholder={provider === 'openai' ? 'https://api.deepseek.com/v1' : '留空使用官方地址'} />
        </Form.Item>

        <Form.Item
          name="api_key"
          label={<Space>API Key {keySet ? <Tag color="success">已设置</Tag> : <Tag>未设置</Tag>}</Space>}
          extra={keySet ? '留空表示不修改；加密保存，保存后不再显示' : provider === 'anthropic' ? '留空时读取服务端环境变量 ANTHROPIC_API_KEY' : '本地 Ollama 可留空'}
        >
          <Input.Password placeholder={keySet ? '••••••••（不修改请留空）' : '输入 API Key'} autoComplete="new-password" />
        </Form.Item>

        <Form.Item name="model" label="模型" rules={[{ required: true, message: '请填写模型名称' }]}>
          <Input placeholder="claude-opus-5-5" />
        </Form.Item>

        <Space size="large">
          <Form.Item name="max_input_chars" label="单次最多发送字符数" extra="超出时只保留最新的日志">
            <InputNumber min={2000} max={400000} step={10000} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="timeout_seconds" label="超时（秒）">
            <InputNumber min={10} max={900} style={{ width: 120 }} />
          </Form.Item>
        </Space>

        <Space>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>保存</Button>
          <Button icon={<ApiOutlined />} loading={testing} onClick={handleTest}>保存并测试</Button>
          {keySet && (
            <Button danger onClick={() => save({ clear_api_key: true })}>清除 API Key</Button>
          )}
        </Space>
        <div style={{ marginTop: 12 }}>
          <Text type="secondary">测试会用一段示例日志实际调用一次模型（会产生少量费用）。</Text>
        </div>
      </Form>
    </Card>
  );
}
