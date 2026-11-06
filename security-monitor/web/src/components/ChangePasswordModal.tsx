import { useState } from 'react';
import { Modal, Form, Input, Alert, message } from 'antd';
import { LockOutlined } from '@ant-design/icons';
import { authApi } from '../services/api';

interface ChangePasswordModalProps {
  open: boolean;
  /** 强制改密：不可关闭，原密码已知时可预填 */
  forced?: boolean;
  /** 用于调用接口的 token（强制改密时是登录返回的受限 token，尚未写入本地存储） */
  token?: string;
  initialOldPassword?: string;
  /** newToken：改密后旧 token 全部失效，接口返回当前会话的新 token */
  onSuccess: (newPassword: string, newToken?: string) => void;
  onCancel?: () => void;
}

export default function ChangePasswordModal({
  open, forced = false, token, initialOldPassword, onSuccess, onCancel,
}: ChangePasswordModalProps) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const handleOk = async () => {
    const values = await form.validateFields();
    setLoading(true);
    try {
      const res = await authApi.changePassword(values.old_password, values.new_password, token);
      message.success('密码已修改，其他设备上的登录已失效');
      form.resetFields();
      onSuccess(values.new_password, res.data?.token);
    } catch (error: any) {
      message.error(error.response?.data?.detail || '修改密码失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="修改密码"
      open={open}
      onOk={handleOk}
      onCancel={forced ? undefined : onCancel}
      closable={!forced}
      maskClosable={!forced}
      keyboard={!forced}
      cancelButtonProps={forced ? { style: { display: 'none' } } : undefined}
      confirmLoading={loading}
      okText="确认修改"
      destroyOnClose
    >
      {forced && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前密码为初始密码或已被管理员重置，请先修改密码后再使用系统。"
        />
      )}
      <Form form={form} layout="vertical" preserve={false} initialValues={{ old_password: initialOldPassword }}>
        <Form.Item name="old_password" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
          <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="新密码"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 8, message: '密码至少8位' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || value !== getFieldValue('old_password')) return Promise.resolve();
                return Promise.reject(new Error('新密码不能与原密码相同'));
              },
            }),
          ]}
        >
          <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认新密码"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                return Promise.reject(new Error('两次输入的密码不一致'));
              },
            }),
          ]}
        >
          <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
