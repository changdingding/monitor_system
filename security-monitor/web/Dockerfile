# 构建阶段
FROM node:18-alpine AS builder

WORKDIR /app

# 复制依赖清单（含 lockfile，保证每次构建版本一致）
COPY package.json package-lock.json ./

# 按 lockfile 精确安装。装完确认 tsc 存在，避免 npm 异常退出后把空依赖层缓存下来。
RUN npm ci --no-audit --no-fund && node_modules/.bin/tsc --version

# 复制源代码
COPY . .

# 构建生产版本
RUN npm run build

# 生产阶段
FROM nginx:alpine

# 复制构建产物到 nginx
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf

# 暴露端口
EXPOSE 80

# 启动 nginx
CMD ["nginx", "-g", "daemon off;"]
