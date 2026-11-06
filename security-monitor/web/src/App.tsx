import { Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import MainLayout from './layouts/MainLayout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Sites from './pages/Sites';
import Servers from './pages/Servers';
import NetworkDevices from './pages/NetworkDevices';
import AlertHistory from './pages/AlertHistory';
import FeishuConfig from './pages/FeishuConfig';
import Users from './pages/Users';
import SystemSettings from './pages/SystemSettings';
import AuditLogs from './pages/AuditLogs';
import LogAnalysis from './pages/LogAnalysis';
import AiConfig from './pages/AiConfig';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // 检查本地存储的 token
    const token = localStorage.getItem('token');
    if (token) {
      setIsAuthenticated(true);
    }
    setLoading(false);
  }, []);

  const handleLogin = (token: string) => {
    localStorage.setItem('token', token);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setIsAuthenticated(false);
  };

  if (loading) {
    return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      加载中...
    </div>;
  }

  return (
    <Routes>
      <Route path="/login" element={
        isAuthenticated ? <Navigate to="/dashboard" /> : <Login onLogin={handleLogin} />
      } />
      <Route path="/" element={
        isAuthenticated ? <MainLayout onLogout={handleLogout} /> : <Navigate to="/login" />
      }>
        <Route index element={<Navigate to="/dashboard" />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="sites" element={<Sites />} />
        <Route path="servers" element={<Servers />} />
        <Route path="network-devices" element={<NetworkDevices />} />
        <Route path="alerts" element={<AlertHistory />} />
        <Route path="log-analysis" element={<LogAnalysis />} />
        <Route path="ai-config" element={<AiConfig />} />
        <Route path="feishu" element={<FeishuConfig />} />
        <Route path="users" element={<Users />} />
        <Route path="settings" element={<SystemSettings />} />
        <Route path="audit-logs" element={<AuditLogs />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" />} />
    </Routes>
  );
}

export default App;
