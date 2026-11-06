import { useState } from 'react';
import { Form, Input, Button, Card, message, Typography } from 'antd';
import { UserOutlined, LockOutlined, SafetyOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../services/api';
import type { LoginResponse } from '../types';
import ChangePasswordModal from '../components/ChangePasswordModal';

const { Title, Text } = Typography;

interface LoginProps {
  onLogin: (token: string) => void;
}

export default function Login({ onLogin }: LoginProps) {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  // 需改密时暂存受限 token 与登录信息，改密成功后用新密码重新登录
  const [pending, setPending] = useState<{ token: string; username: string; password: string } | null>(null);

  const finishLogin = (token: string) => {
    onLogin(token);
    message.success('登录成功');
    navigate('/dashboard');
  };

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await authApi.login(values.username, values.password);
      const data = res.data as LoginResponse;
      if (data.must_change_password) {
        setPending({ token: data.token, username: values.username, password: values.password });
        return;
      }
      finishLogin(data.token);
    } catch (error: any) {
      message.error(error.response?.data?.detail || '登录失败，请检查用户名和密码');
    } finally {
      setLoading(false);
    }
  };

  const handlePasswordChanged = async (newPassword: string) => {
    if (!pending) return;
    const username = pending.username;
    setPending(null);
    form.setFieldsValue({ password: '' });
    try {
      const res = await authApi.login(username, newPassword);
      finishLogin((res.data as LoginResponse).token);
    } catch {
      message.info('密码已修改，请使用新密码登录');
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
    }}>
      <Card
        style={{
          width: 420,
          borderRadius: 12,
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        }}
        bordered={false}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <SafetyOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
          <Title level={3} style={{ marginBottom: 4 }}>安全监控管理平台</Title>
          <Text type="secondary">多源异构安全监控与告警系统</Text>
        </div>

        <Form
          form={form}
          onFinish={handleSubmit}
          size="large"
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登 录
            </Button>
          </Form.Item>
        </Form>

        <Text type="secondary" style={{ display: 'block', textAlign: 'center', fontSize: 12 }}>
          首次部署请使用部署文档中的初始账号登录，登录后需立即修改密码
        </Text>
      </Card>
      <ChangePasswordModal
        open={!!pending}
        forced
        token={pending?.token}
        initialOldPassword={pending?.password}
        onSuccess={handlePasswordChanged}
      />
    </div>
  );
}
