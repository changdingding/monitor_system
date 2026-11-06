import { useState, useEffect } from 'react';
import { Table, Button, Modal, Form, Input, Select, Tag, Space, message, Popconfirm, Card, Radio, TreeSelect, Tooltip, Alert } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, KeyOutlined, UnlockOutlined } from '@ant-design/icons';
import { siteApi, userApi } from '../services/api';
import type { Site, User } from '../types';
import { siteTreeData } from '../utils/siteTree';

export default function Users() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [passwordModalVisible, setPasswordModalVisible] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [form] = Form.useForm();
  const [passwordForm] = Form.useForm();
  const [sites, setSites] = useState<Site[]>([]);
  const role = Form.useWatch('role', form);
  const dataScope = Form.useWatch('data_scope', form);

  useEffect(() => {
    fetchUsers();
    siteApi.list().then((res) => setSites(res.data || [])).catch(() => setSites([]));
  }, []);

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const res = await userApi.list();
      setUsers(res.data);
    } catch (error) {
      message.error('获取用户列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    setEditingUser(null);
    form.resetFields();
    form.setFieldsValue({ role: 'viewer', data_scope: 'sites', site_ids: [] });
    setModalVisible(true);
  };

  const handleEdit = (user: User) => {
    setEditingUser(user);
    form.setFieldsValue({ ...user, data_scope: user.data_scope || 'all', site_ids: user.site_ids || [] });
    setModalVisible(true);
  };

  const handleUnlock = async (id: number) => {
    try {
      await userApi.unlock(id);
      message.success('已解锁');
      fetchUsers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || '解锁失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await userApi.delete(id);
      message.success('删除成功');
      fetchUsers();
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSubmit = async (values: any) => {
    try {
      if (values.role === 'admin') values = { ...values, data_scope: 'all', site_ids: [] };
      if (editingUser) {
        await userApi.update(editingUser.id, values);
        message.success('更新成功');
      } else {
        await userApi.create(values);
        message.success('创建成功');
      }
      setModalVisible(false);
      fetchUsers();
    } catch (error: any) {
      message.error(error.response?.data?.detail || (editingUser ? '更新失败' : '创建失败'));
    }
  };

  const handleResetPassword = (user: User) => {
    setEditingUser(user);
    passwordForm.resetFields();
    setPasswordModalVisible(true);
  };

  const handlePasswordSubmit = async (values: { new_password: string }) => {
    if (!editingUser) return;
    try {
      await userApi.resetPassword(editingUser.id, values.new_password);
      message.success('密码重置成功');
      setPasswordModalVisible(false);
    } catch (error) {
      message.error('密码重置失败');
    }
  };

  const getRoleColor = (role: string) => {
    switch (role) {
      case 'admin': return 'red';
      case 'operator': return 'blue';
      case 'viewer': return 'green';
      default: return 'default';
    }
  };

  const getRoleText = (role: string) => {
    switch (role) {
      case 'admin': return '管理员';
      case 'operator': return '操作员';
      case 'viewer': return '只读';
      default: return role;
    }
  };

  const columns = [
    { title: '用户名', dataIndex: 'username', key: 'username', width: 110, ellipsis: true },
    { title: '昵称', dataIndex: 'nickname', key: 'nickname', width: 110, ellipsis: true },
    { title: '邮箱', dataIndex: 'email', key: 'email', width: 190, ellipsis: true },
    { title: '角色', dataIndex: 'role', key: 'role', width: 90,
      render: (role: string) => <Tag color={getRoleColor(role)}>{getRoleText(role)}</Tag> },
    { title: '数据范围', key: 'scope', width: 200, ellipsis: true,
      render: (_: unknown, record: User) => {
        if (record.role === 'admin' || (record.data_scope || 'all') === 'all') return <Tag color="gold">全部站点</Tag>;
        const names = record.site_names || [];
        return (
          <Tooltip title={`${names.join('、')}（含下级站点）`}>
            <Tag color="blue">{names.length ? `${names[0]}${names.length > 1 ? ` 等 ${names.length} 个` : ''}` : '未授权'}</Tag>
          </Tooltip>
        );
      } },
    { title: '状态', dataIndex: 'status', key: 'status', width: 130,
      render: (status: string, record: User) => (
        <Space size={4}>
          {status === 'active' ? <Tag color="green">正常</Tag> : <Tag color="red">禁用</Tag>}
          {record.locked_until && <Tag color="orange">已锁定</Tag>}
        </Space>
      ) },
    { title: '最后登录', dataIndex: 'last_login', key: 'last_login', width: 165,
      render: (text: string) => text ? new Date(text).toLocaleString('zh-CN') : '-' },
    { title: '操作', key: 'action', width: 300, fixed: 'right' as const,
      render: (_: any, record: User) => (
        <Space size={0} wrap>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Button type="link" size="small" icon={<KeyOutlined />} onClick={() => handleResetPassword(record)}>重置密码</Button>
          {record.locked_until && (
            <Popconfirm title="解除该用户的登录锁定？" onConfirm={() => handleUnlock(record.id)}>
              <Button type="link" size="small" icon={<UnlockOutlined />}>解锁</Button>
            </Popconfirm>
          )}
          {record.username !== 'admin' && (
            <Popconfirm title="确定删除此用户？" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="用户与门店权限"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={fetchUsers}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加用户</Button>
          </Space>
        }
      >
        <Table
          columns={columns}
          dataSource={users}
          rowKey="id"
          loading={loading}
          tableLayout="fixed"
          scroll={{ x: 1295 }}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个用户` }}
        />
      </Card>

      <Modal
        title={editingUser ? '编辑用户' : '添加用户'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={() => form.submit()}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input placeholder="登录用户名" disabled={!!editingUser} />
          </Form.Item>
          {!editingUser && (
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password placeholder="初始密码" />
            </Form.Item>
          )}
          <Form.Item name="nickname" label="昵称" rules={[{ required: true, message: '请输入昵称' }]}>
            <Input placeholder="用户昵称" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="用户邮箱" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: '请选择角色' }]}>
            <Select placeholder="选择角色">
              <Select.Option value="admin">管理员（全部功能）</Select.Option>
              <Select.Option value="operator">操作员（可配置设备、处理告警）</Select.Option>
              <Select.Option value="viewer">只读（仅查看）</Select.Option>
            </Select>
          </Form.Item>
          {role !== 'admin' && (
            <>
              <Form.Item name="data_scope" label="可查看的数据" initialValue="all">
                <Radio.Group>
                  <Radio value="all">全部站点（总部人员）</Radio>
                  <Radio value="sites">指定站点 / 分支</Radio>
                </Radio.Group>
              </Form.Item>
              {dataScope === 'sites' && (
                <Form.Item name="site_ids" label="授权站点" rules={[{ required: true, type: 'array', min: 1, message: '请至少选择一个站点' }]}
                  extra="授权分支即包含其下所有门店（以后新建的下级门店自动生效）。用户只能看到、操作这些站点的设备、服务器、告警和仪表盘数据。">
                  <TreeSelect multiple treeCheckable showCheckedStrategy={TreeSelect.SHOW_PARENT}
                    treeDefaultExpandAll placeholder="选择门店或分支" treeData={siteTreeData(sites)} />
                </Form.Item>
              )}
            </>
          )}
          {role === 'admin' && <Alert type="info" showIcon style={{ marginBottom: 16 }} message="管理员始终可以查看和管理全部站点。" />}
          <Form.Item name="status" label="状态" initialValue="active">
            <Select>
              <Select.Option value="active">正常</Select.Option>
              <Select.Option value="disabled">禁用</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重置密码 - ${editingUser?.username}`}
        open={passwordModalVisible}
        onCancel={() => setPasswordModalVisible(false)}
        onOk={() => passwordForm.submit()}
        destroyOnClose
      >
        <Form form={passwordForm} layout="vertical" onFinish={handlePasswordSubmit}>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 8, message: '密码至少8位' }]}>
            <Input.Password placeholder="输入新密码" />
          </Form.Item>
          <Form.Item name="confirm_password" label="确认密码" dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                  return Promise.reject(new Error('两次密码不一致'));
                },
              }),
            ]}
          >
            <Input.Password placeholder="再次输入新密码" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
