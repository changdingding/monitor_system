import { useState, useEffect } from 'react';
import {
  Card, Form, Input, Switch, Button, Select, Space, message, Divider, Tag, Alert, Tabs, Typography,
} from 'antd';
import { SaveOutlined, SendOutlined, ReloadOutlined, BellOutlined } from '@ant-design/icons';
import { feishuApi } from '../services/api';

const { TextArea } = Input;
const { Paragraph, Text } = Typography;

const DEFAULT_LOGIN_TITLE = '🔔 {{action_text}}提醒 - {{server_name}}';
const DEFAULT_LOGIN_CONTENT = `**告警动作**: {{action_text}}
🖥️ **服务器**: {{server_name}} ({{server_host}})
👤 **账户**: {{account}}
🧾 **操作**: {{action_text}}
⏰ **发生时间**: {{login_time}}
🌐 **来源 IP**: {{source_ip}}
📌 **说明**: 按账号会话状态告警：登录推一次，保持登录不重复推，退出再推一次`;

const DEFAULT_HIGH_TITLE = '{{emoji}} 高危操作告警 - {{server_name}}';
const DEFAULT_HIGH_CONTENT = `**告警动作**: 检测到高危操作，已触发飞书推送
📡 **数据来源**: {{data_source}}
🖥️ **服务器**: {{server_name}} ({{server_host}})
⚠️ **风险等级**: {{risk_level}}
🔧 **操作类型**: {{operation_type}}
👤 **执行账户**: {{account}}
⏰ **操作时间**: {{operation_time}}
📝 **操作描述**: {{description}}
📌 **说明**: 同一高危事件仅推送一次`;

const DEFAULT_NET_TITLE = '🌐 网络设备告警 - {{device_name}}';
const DEFAULT_NET_CONTENT = `**告警动作**: 网络设备健康检查异常
🖥️ **设备**: {{device_name}} ({{device_host}})
⚠️ **告警类型**: {{alert_type}}
🚨 **严重程度**: {{severity}}
📝 **告警内容**: {{message}}`;

export default function FeishuConfig() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [testLoading, setTestLoading] = useState(false);

  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const res = await feishuApi.getConfig();
      form.setFieldsValue({
        ...res.data,
        template_login_title: res.data.template_login_title || DEFAULT_LOGIN_TITLE,
        template_login_content: res.data.template_login_content || DEFAULT_LOGIN_CONTENT,
        template_high_risk_title: res.data.template_high_risk_title || DEFAULT_HIGH_TITLE,
        template_high_risk_content: res.data.template_high_risk_content || DEFAULT_HIGH_CONTENT,
        template_network_title: res.data.template_network_title || DEFAULT_NET_TITLE,
        template_network_content: res.data.template_network_content || DEFAULT_NET_CONTENT,
        notify_login: res.data.notify_login ?? true,
        notify_high_risk: res.data.notify_high_risk ?? true,
        notify_network: res.data.notify_network ?? true,
        append_details: res.data.append_details ?? false,
      });
    } catch (error) {
      form.setFieldsValue({
        enabled: false,
        notify_levels: ['critical', 'high', 'medium'],
        debounce_seconds: 60,
        notify_login: true,
        notify_high_risk: true,
        notify_network: true,
        append_details: false,
        template_login_title: DEFAULT_LOGIN_TITLE,
        template_login_content: DEFAULT_LOGIN_CONTENT,
        template_high_risk_title: DEFAULT_HIGH_TITLE,
        template_high_risk_content: DEFAULT_HIGH_CONTENT,
        template_network_title: DEFAULT_NET_TITLE,
        template_network_content: DEFAULT_NET_CONTENT,
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      await feishuApi.updateConfig(values);
      message.success('保存成功');
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.response?.data?.detail || '保存失败');
    }
  };

  const handleTest = async () => {
    setTestLoading(true);
    try {
      // 先保存当前表单，确保测试用的是最新模板
      const values = await form.validateFields();
      await feishuApi.updateConfig(values);
      await feishuApi.testWebhook();
      message.success('测试消息已发送（已应用当前模板），请检查飞书群');
    } catch (error: any) {
      if (error?.errorFields) {
        message.error('请先完善必填配置');
      } else {
        message.error(error.response?.data?.detail || '测试发送失败，请检查 Webhook 配置');
      }
    } finally {
      setTestLoading(false);
    }
  };

  const resetTemplates = () => {
    form.setFieldsValue({
      template_login_title: DEFAULT_LOGIN_TITLE,
      template_login_content: DEFAULT_LOGIN_CONTENT,
      template_high_risk_title: DEFAULT_HIGH_TITLE,
      template_high_risk_content: DEFAULT_HIGH_CONTENT,
      template_network_title: DEFAULT_NET_TITLE,
      template_network_content: DEFAULT_NET_CONTENT,
    });
    message.info('已恢复默认模板，请点击保存生效');
  };

  return (
    <div>
      <Card title="飞书告警配置" loading={loading}>
        <Alert
          message="飞书告警动作说明"
          description={
            <div>
              <Paragraph style={{ marginBottom: 8 }}>
                系统在采集到对应事件后，会执行明确的告警动作：<Text strong>写入告警历史 + 按模板推送飞书卡片</Text>。
              </Paragraph>
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                <li><Tag color="blue">服务器登录</Tag> 检测到登录日志 → 推送「登录告警」</li>
                <li><Tag color="orange">高危操作</Tag> 检测到 high/critical 操作 → 推送「高危操作告警」</li>
                <li><Tag color="purple">网络设备</Tag> 健康检查异常 → 推送「网络设备告警」</li>
              </ul>
            </div>
          }
          type="info"
          showIcon
          icon={<BellOutlined />}
          style={{ marginBottom: 24 }}
        />

        <Form form={form} layout="vertical">
          <Tabs
            items={[
              {
                key: 'basic',
                label: '基础配置',
                children: (
                  <div style={{ maxWidth: 720 }}>
                    <Form.Item name="webhook_url" label="Webhook 地址" rules={[{ required: true, message: '请输入 Webhook 地址' }]}>
                      <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx" />
                    </Form.Item>

                    <Form.Item name="secret" label="签名校验密钥（可选）">
                      <Input.Password placeholder="飞书机器人安全设置中的签名校验密钥" />
                    </Form.Item>

                    <Form.Item name="enabled" label="启用飞书告警推送" valuePropName="checked">
                      <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>

                    <Divider orientation="left">告警动作开关</Divider>
                    <Space size="large" wrap>
                      <Form.Item name="notify_login" label="登录告警" valuePropName="checked" style={{ marginBottom: 12 }}>
                        <Switch checkedChildren="推送" unCheckedChildren="关闭" />
                      </Form.Item>
                      <Form.Item name="notify_high_risk" label="高危操作告警" valuePropName="checked" style={{ marginBottom: 12 }}>
                        <Switch checkedChildren="推送" unCheckedChildren="关闭" />
                      </Form.Item>
                      <Form.Item name="notify_network" label="网络设备告警" valuePropName="checked" style={{ marginBottom: 12 }}>
                        <Switch checkedChildren="推送" unCheckedChildren="关闭" />
                      </Form.Item>
                    </Space>

                    <Form.Item name="notify_levels" label="推送告警级别（高危/网络告警生效）">
                      <Select mode="multiple" placeholder="选择需要推送的告警级别">
                        <Select.Option value="critical"><Tag color="#f5222d">严重</Tag></Select.Option>
                        <Select.Option value="high"><Tag color="#fa8c16">高危</Tag></Select.Option>
                        <Select.Option value="medium"><Tag color="#fadb14">中危</Tag></Select.Option>
                        <Select.Option value="low"><Tag color="#52c41a">低危</Tag></Select.Option>
                      </Select>
                    </Form.Item>

                    <Form.Item name="debounce_seconds" label="防抖时间窗口（秒）" initialValue={60}>
                      <Input type="number" placeholder="相同事件在此时间窗口内只推送一次" />
                    </Form.Item>

                    <Form.Item
                      name="append_details"
                      label="附加详情区块"
                      valuePropName="checked"
                      extra="开启后会在卡片底部再追加一份键值详情（可能导致内容重复）"
                    >
                      <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>
                  </div>
                ),
              },
              {
                key: 'templates',
                label: '告警模板自定义',
                children: (
                  <div style={{ maxWidth: 860 }}>
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="模板变量"
                      description={
                        <div>
                          <div>使用 <Text code>{'{{变量名}}'}</Text> 占位，发送时自动替换。</div>
                          <div style={{ marginTop: 8 }}>
                            <Text strong>登录/退出告警：</Text> action, action_text, server_name, server_host, account, login_time, source_ip
                          </div>
                          <div>
                            <Text strong>高危操作：</Text> emoji, server_name, server_host, risk_level, operation_type, account, operation_time, description
                          </div>
                          <div>
                            <Text strong>网络告警：</Text> device_name, device_host, alert_type, severity, message
                          </div>
                        </div>
                      }
                    />

                    <Divider orientation="left">登录告警模板</Divider>
                    <Form.Item name="template_login_title" label="标题模板" rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="template_login_content" label="正文模板（支持 Markdown）" rules={[{ required: true }]}>
                      <TextArea rows={7} />
                    </Form.Item>

                    <Divider orientation="left">高危操作告警模板</Divider>
                    <Form.Item name="template_high_risk_title" label="标题模板" rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="template_high_risk_content" label="正文模板（支持 Markdown）" rules={[{ required: true }]}>
                      <TextArea rows={8} />
                    </Form.Item>

                    <Divider orientation="left">网络设备告警模板</Divider>
                    <Form.Item name="template_network_title" label="标题模板" rules={[{ required: true }]}>
                      <Input />
                    </Form.Item>
                    <Form.Item name="template_network_content" label="正文模板（支持 Markdown）" rules={[{ required: true }]}>
                      <TextArea rows={7} />
                    </Form.Item>

                    <Button onClick={resetTemplates}>恢复默认模板</Button>
                  </div>
                ),
              },
            ]}
          />

          <Divider />
          <Space>
            <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>保存配置</Button>
            <Button icon={<SendOutlined />} loading={testLoading} onClick={handleTest}>发送测试消息</Button>
            <Button icon={<ReloadOutlined />} onClick={fetchConfig}>重新加载</Button>
          </Space>
        </Form>
      </Card>
    </div>
  );
}
