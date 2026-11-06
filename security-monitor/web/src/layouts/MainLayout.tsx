import { Layout, Menu, Button, Dropdown, Avatar, Space } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  ShopOutlined,
  CloudServerOutlined,
  GlobalOutlined,
  AlertOutlined,
  RobotOutlined,
  UserOutlined,
  SettingOutlined,
  LogoutOutlined,
  KeyOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  AuditOutlined,
  FileSearchOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import { useState } from 'react';
import ChangePasswordModal from '../components/ChangePasswordModal';

const { Header, Sider, Content } = Layout;

interface MainLayoutProps {
  onLogout: () => void;
}

export default function MainLayout({ onLogout }: MainLayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const menuItems = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
    { key: '/sites', icon: <ShopOutlined />, label: '门店 / 云端' },
    { key: '/servers', icon: <CloudServerOutlined />, label: '服务器管理' },
    { key: '/network-devices', icon: <GlobalOutlined />, label: '网络设备' },
    { key: '/alerts', icon: <AlertOutlined />, label: '告警历史' },
    { key: '/log-analysis', icon: <FileSearchOutlined />, label: 'AI 日志分析' },
    { key: '/feishu', icon: <RobotOutlined />, label: '飞书配置' },
    { key: '/ai-config', icon: <ExperimentOutlined />, label: 'AI 配置' },
    { key: '/users', icon: <UserOutlined />, label: '用户管理' },
    { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    { key: '/audit-logs', icon: <AuditOutlined />, label: '操作审计' },
  ];

  const userMenuItems = [
    { key: 'password', icon: <KeyOutlined />, label: '修改密码' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ];

  const handleMenuClick = (e: { key: string }) => {
    navigate(e.key);
  };

  const handleUserMenuClick = (e: { key: string }) => {
    if (e.key === 'password') {
      setPwdOpen(true);
    } else if (e.key === 'logout') {
      onLogout();
      navigate('/login');
    }
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider trigger={null} collapsible collapsed={collapsed}>
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: collapsed ? 16 : 20,
          fontWeight: 'bold',
        }}>
          {collapsed ? 'SM' : '安全监控'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
        />
      </Sider>
      <Layout>
        <Header style={{
          padding: '0 24px',
          background: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
        }}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ fontSize: 16 }}
          />
          <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenuClick }} placement="bottomRight">
            <Space style={{ cursor: 'pointer' }}>
              <Avatar icon={<UserOutlined />} />
              <span>管理员</span>
            </Space>
          </Dropdown>
        </Header>
        <Content style={{
          margin: 24,
          padding: 24,
          background: '#fff',
          borderRadius: 8,
          minHeight: 280,
        }}>
          <Outlet />
        </Content>
      </Layout>
      <ChangePasswordModal
        open={pwdOpen}
        onSuccess={(_pwd, newToken) => {
          if (newToken) localStorage.setItem('token', newToken);
          setPwdOpen(false);
        }}
        onCancel={() => setPwdOpen(false)}
      />
    </Layout>
  );
}
