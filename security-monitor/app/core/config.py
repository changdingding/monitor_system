"""
应用配置
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置"""
    
    # 数据库配置
    DB_NAME: str = "security_monitor"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DB_HOST: str = "db"
    DB_PORT: int = 5432
    
    # Redis 配置
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    
    # 飞书配置
    FEISHU_WEBHOOK_URL: str = ""
    FEISHU_SECRET: str = ""
    
    # 应用配置
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    
    # 安全配置
    # JWT 签名密钥：必须通过环境变量设置，未设置时应用拒绝启动
    JWT_SECRET: str = ""
    JWT_EXPIRE_HOURS: int = 24
    # 凭据加密密钥（SSH 密码、SNMP 团体字）：必须通过环境变量设置，设置后不可随意更换
    CREDENTIAL_KEY: str = ""
    # 允许跨域的前端来源，逗号分隔；留空表示不允许跨域（同源经 nginx 代理访问不受影响）
    CORS_ORIGINS: str = ""
    # 登录防爆破：同一账号连续失败 N 次后锁定 M 分钟
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_MINUTES: int = 15
    # 操作审计日志保留天数
    AUDIT_RETENTION_DAYS: int = 180
    # 告警历史保留天数（其余日志按系统设置中的“日志保留天数”）
    ALERT_RETENTION_DAYS: int = 365

    # Syslog 配置
    SYSLOG_HOST: str = "0.0.0.0"
    SYSLOG_PORT: int = 514
    
    @property
    def DATABASE_URL(self) -> str:
        """数据库连接 URL"""
        # 密码 / 用户名做 URL 编码，否则含 @ / : # 等字符的强密码会让连接串解析错误
        from urllib.parse import quote_plus
        return (f"postgresql+asyncpg://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}")
    
    @property
    def CORS_ORIGIN_LIST(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def REDIS_URL(self) -> str:
        """Redis 连接 URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局配置实例
settings = Settings()
