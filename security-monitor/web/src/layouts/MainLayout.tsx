import { Layout, Menu, Button, Dropdown, Avatar, Space, Tag, Tooltip } from 'antd';
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
import { MeProvider, useMe } from '../context/MeContext';

const { Header, Sider, Content } = Layout;

interface MainLayoutProps {
  onLogout: () => void;
}

export default function MainLayout(props: MainLayoutProps) {
  return (
    <MeProvider>
      <LayoutInner {...props} />
    </MeProvider>
  );
}

const ROLE_TEXT: Record<string, string> = { admin: '管理员', operator: '操作员', viewer: '只读' };

function LayoutInner({ onLogout }: MainLayoutProps) {
  const { me, isAdmin } = useMe();
  const [collapsed, setCollapsed] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const menuItems = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: '态势大屏' },
    { key: '/sites', icon: <ShopOutlined />, label: '站点 / 分支' },
    { key: '/servers', icon: <CloudServerOutlined />, label: '服务器管理' },
    { key: '/network-devices', icon: <GlobalOutlined />, label: '网络设备' },
    { key: '/alerts', icon: <AlertOutlined />, label: '告警历史' },
    { key: '/log-analysis', icon: <FileSearchOutlined />, label: 'AI 日志分析' },
    ...(isAdmin ? [
      { key: '/feishu', icon: <RobotOutlined />, label: '飞书配置' },
      { key: '/ai-config', icon: <ExperimentOutlined />, label: 'AI 配置' },
      { key: '/users', icon: <UserOutlined />, label: '用户与权限' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
      { key: '/audit-logs', icon: <AuditOutlined />, label: '操作审计' },
    ] : []),
  ];
  const isDashboard = location.pathname === '/dashboard';
  const scopeText = me?.data_scope === 'sites'
    ? `授权站点：${(me.scope_sites || []).map((s) => s.name).join('、') || '-'}（含下级共 ${me.scope_site_count ?? 0} 个）`
    : '全部站点';

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
          <Space size={12}>
            {me && !me.system_activated && (
              <Tooltip title="实施模式：管理员可在「系统设置 → 系统启用」清理测试数据，完成实施后正式启用">
                <Tag color="orange" style={{ cursor: isAdmin ? 'pointer' : 'default' }}
                  onClick={() => isAdmin && navigate('/settings')}>实施模式 · 未启用</Tag>
              </Tooltip>
            )}
            {me?.data_scope === 'sites' && (
              <Tooltip title={scopeText}><Tag color="blue">门店权限 {me.scope_site_count ?? 0}</Tag></Tooltip>
            )}
            <Dropdown menu={{ items: userMenuItems, onClick: handleUserMenuClick }} placement="bottomRight">
              <Space style={{ cursor: 'pointer' }}>
                <Avatar icon={<UserOutlined />} />
                <span>{me?.nickname || me?.username || '用户'}</span>
                {me && <Tag>{ROLE_TEXT[me.role] || me.role}</Tag>}
              </Space>
            </Dropdown>
          </Space>
        </Header>
        <Content style={isDashboard ? { margin: 0, minHeight: 280 } : {
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
