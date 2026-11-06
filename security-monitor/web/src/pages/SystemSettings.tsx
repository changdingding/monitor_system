import { useState, useEffect } from 'react';
import { Card, Form, InputNumber, Button, Space, message, Alert, Divider } from 'antd';
import { SaveOutlined, ReloadOutlined } from '@ant-design/icons';
import { settingsApi } from '../services/api';
import SystemActivation from '../components/SystemActivation';

export default function SystemSettings() {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const res = await settingsApi.get();
      form.setFieldsValue(res.data);
    } catch (error) {
      // 使用默认值
        form.setFieldsValue({
        log_retention_days: 30,
        alert_debounce_seconds: 60,
        collector_interval_seconds: 300,
        monitor_interval_seconds: 60,
        cpu_threshold: 90,
        memory_threshold: 90,
        disk_threshold: 85,
        offline_seconds: 180,
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (values: any) => {
    try {
      await settingsApi.update(values);
      message.success('保存成功');
    } catch (error) {
      message.error('保存失败');
    }
  };

  return (
    <div>
      <Card title="系统设置" loading={loading}>
        <Alert
          message="配置说明"
          description="以下配置影响系统的全局行为，修改后会自动生效。采集间隔和监控间隔不建议设置过小，以免增加服务器负载。"
          type="info"
          showIcon
          style={{ marginBottom: 24 }}
        />

        <Form
          form={form}
          layout="vertical"
          onFinish={handleSave}
          style={{ maxWidth: 600 }}
        >
          <Divider orientation="left">采集配置</Divider>

          <Form.Item name="collector_interval_seconds" label="日志采集间隔（秒）" rules={[{ required: true }]}>
            <InputNumber min={30} max={3600} style={{ width: '100%' }} placeholder="默认 300 秒（5分钟）" />
          </Form.Item>

          <Form.Item name="monitor_interval_seconds" label="网络设备监控间隔（秒）" rules={[{ required: true }]}>
            <InputNumber min={10} max={3600} style={{ width: '100%' }} placeholder="默认 60 秒" />
          </Form.Item>

          <Divider orientation="left">告警配置</Divider>

          <Form.Item name="alert_debounce_seconds" label="告警防抖窗口（秒）" rules={[{ required: true }]}>
            <InputNumber min={10} max={3600} style={{ width: '100%' }} placeholder="默认 60 秒，相同事件在此时间内只推送一次" />
          </Form.Item>

          <Divider orientation="left">健康阈值</Divider>

          <Form.Item name="cpu_threshold" label="CPU 告警阈值（%）" rules={[{ required: true }]}>
            <InputNumber min={1} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="memory_threshold" label="内存告警阈值（%）" rules={[{ required: true }]}>
            <InputNumber min={1} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="disk_threshold" label="磁盘告警阈值（%）" rules={[{ required: true }]}>
            <InputNumber min={1} max={100} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="offline_seconds" label="离线判定（秒）" extra="超过该时间没有心跳或上报，视为离线。默认 180 秒。" rules={[{ required: true }]}>
            <InputNumber min={60} max={3600} style={{ width: '100%' }} />
          </Form.Item>

          <Divider orientation="left">数据保留</Divider>

          <Form.Item name="log_retention_days" label="日志保留天数" rules={[{ required: true }]}>
            <InputNumber min={7} max={365} style={{ width: '100%' }} placeholder="默认 30 天" />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>保存设置</Button>
              <Button icon={<ReloadOutlined />} onClick={fetchSettings}>重新加载</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
      <div style={{ marginTop: 24 }}>
        <SystemActivation />
      </div>
    </div>
  );
}
