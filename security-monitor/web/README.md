# 安全监控系统 - 前端

基于 React 18 + TypeScript + Vite + Ant Design 5 的 Web 管理界面。

## 功能模块

- **仪表盘**：系统概览、告警趋势、最近告警
- **服务器管理**：添加/编辑/删除服务器
- **网络设备管理**：添加/编辑/删除网络设备
- **告警历史**：查看告警列表、详情、状态管理
- **飞书配置**：配置飞书 Webhook、测试推送
- **用户管理**：添加/编辑/删除用户、重置密码
- **系统设置**：配置采集间隔、告警防抖、日志保留

## 开发

### 安装依赖

```bash
npm install
```

### 启动开发服务器

```bash
npm run dev
```

访问：http://localhost:5173

### 构建生产版本

```bash
npm run build
```

构建产物在 `dist/` 目录。

### 代码检查

```bash
npm run lint
```

## 技术栈

- **React 18**：UI 框架
- **TypeScript**：类型安全
- **Vite**：构建工具
- **Ant Design 5**：UI 组件库
- **React Router 6**：路由管理
- **Axios**：HTTP 客户端
- **Recharts**：图表库

## 目录结构

```
web/
├── src/
│   ├── pages/           # 页面组件
│   ├── layouts/         # 布局组件
│   ├── services/        # API 服务
│   ├── types/           # 类型定义
│   ├── App.tsx          # 主应用组件
│   ├── main.tsx         # 入口文件
│   └── index.css        # 全局样式
├── public/              # 静态资源
├── Dockerfile           # Docker 镜像
├── nginx.conf           # Nginx 配置
├── package.json         # 依赖配置
├── vite.config.ts       # Vite 配置
└── tsconfig.json        # TypeScript 配置
```

## API 调用

所有 API 调用都通过 `src/services/api.ts` 封装，自动添加 JWT Token 到请求头。

```typescript
import { serverApi } from '../services/api';

// 获取服务器列表
const servers = await serverApi.list();

// 创建服务器
await serverApi.create({ name: 'Server 1', ip: '192.168.1.100', os_type: 'linux' });
```

## 路由配置

- `/login` - 登录页面
- `/dashboard` - 仪表盘
- `/servers` - 服务器管理
- `/network-devices` - 网络设备管理
- `/alerts` - 告警历史
- `/feishu` - 飞书配置
- `/users` - 用户管理
- `/settings` - 系统设置

## 认证

使用 JWT Token 进行认证：

1. 用户登录后获取 token
2. token 存储在 localStorage
3. 每次请求自动添加到 Authorization 头
4. token 过期自动跳转到登录页

## 部署

### Docker 部署

```bash
docker build -t security-monitor-web .
docker run -p 80:80 security-monitor-web
```

### 静态文件部署

```bash
npm run build
# 将 dist/ 目录部署到 Nginx/Apache
```

Nginx 配置参考 `nginx.conf`。
