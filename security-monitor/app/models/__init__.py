"""
数据模型
"""
from app.core.database import Base
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Float, JSON
from sqlalchemy.sql import func


# 告警规则表
class AlertRule(Base):
    __tablename__ = "alert_rules"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    rule_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False, default="medium")
    enabled = Column(Boolean, default=True)
    config = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# 服务器表
class Server(Base):
    __tablename__ = "servers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    os_type = Column(String(20), nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_username = Column(String(100))
    ssh_password = Column(String(255))
    ssh_key = Column(Text)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# 网络设备表
class NetworkDevice(Base):
    __tablename__ = "network_devices"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    device_type = Column(String(50))
    snmp_version = Column(String(10), default="v2c")
    snmp_community = Column(String(100))
    snmp_username = Column(String(100))
    snmp_password = Column(String(255))
    snmp_auth_protocol = Column(String(20))
    snmp_priv_protocol = Column(String(20))
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# 登录日志表
class LoginLog(Base):
    __tablename__ = "login_logs"
    
    id = Column(BigInteger, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False)
    account = Column(String(100), nullable=False)
    login_time = Column(DateTime, nullable=False)
    source_ip = Column(String(50))
    os_type = Column(String(20), nullable=False)
    raw_line = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


# 高危操作日志表
class HighRiskOperation(Base):
    __tablename__ = "high_risk_operations"
    
    id = Column(BigInteger, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False)
    operation_type = Column(String(50), nullable=False)
    risk_level = Column(String(20), nullable=False, default="medium")
    account = Column(String(100), nullable=False)
    operation_time = Column(DateTime, nullable=False)
    description = Column(Text, nullable=False)
    details = Column(Text)
    hostname = Column(String(255))
    os_type = Column(String(20), nullable=False)
    raw_line = Column(Text)
    alerted = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


# Syslog 日志表
class SyslogLog(Base):
    __tablename__ = "syslog_logs"
    
    id = Column(BigInteger, primary_key=True, index=True)
    source_ip = Column(String(50), nullable=False)
    facility = Column(String(50))
    severity = Column(String(20))
    message = Column(Text, nullable=False)
    received_at = Column(DateTime, server_default=func.now())


# SNMP 监控数据表
class SnmpMetric(Base):
    __tablename__ = "snmp_metrics"
    
    id = Column(BigInteger, primary_key=True, index=True)
    device_id = Column(Integer, nullable=False)
    metric_type = Column(String(50), nullable=False)
    metric_value = Column(Float, nullable=False)
    metric_unit = Column(String(20))
    collected_at = Column(DateTime, server_default=func.now())


# 告警记录表
class AlertRecord(Base):
    __tablename__ = "alert_records"
    
    id = Column(BigInteger, primary_key=True, index=True)
    rule_id = Column(Integer)
    server_id = Column(Integer)
    device_id = Column(Integer)
    severity = Column(String(20), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    notified = Column(Boolean, default=False)
    notified_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())


# 兼容旧式表对象导入
alert_rules = AlertRule.__table__
servers = Server.__table__
network_devices = NetworkDevice.__table__
login_logs = LoginLog.__table__
high_risk_operations = HighRiskOperation.__table__
syslog_logs = SyslogLog.__table__
snmp_metrics = SnmpMetric.__table__
alert_records = AlertRecord.__table__
alerts = AlertRecord.__table__
