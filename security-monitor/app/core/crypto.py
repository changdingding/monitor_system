"""
敏感凭据加密（SSH 密码、SNMP 团体字、Agent / 采集器 Token）

- 使用 Fernet（AES-128-CBC + HMAC-SHA256），密钥来自环境变量 CREDENTIAL_KEY
- 密文格式：enc:v1:<fernet token>；无前缀的值视为历史明文，读取时原样返回
- 启动时 migrate_plaintext_credentials() 会把历史明文就地加密

接入 Token 另存 SHA-256 摘要（*_token_hash）用于鉴权查找：
Token 是 256 位随机数，无需加盐；密文仅用于在管理端重新展示 / 打包。
"""
import base64
import hashlib
import logging
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key = settings.CREDENTIAL_KEY
    if not key:
        raise RuntimeError("CREDENTIAL_KEY 未设置")
    # 接受任意长度的口令，统一派生为 32 字节 Fernet 密钥
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: Optional[str]) -> Optional[str]:
    """加密；空值与已加密值原样返回"""
    if not value or is_encrypted(value):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: Optional[str]) -> Optional[str]:
    """解密；历史明文原样返回；密钥不匹配时返回 None 并记录错误"""
    if not value or not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("凭据解密失败：CREDENTIAL_KEY 与加密时不一致")
        return None


def token_hash(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_access_token() -> tuple[str, str, str]:
    """生成接入 Token，返回 (明文, 密文, 摘要)；明文只用于本次响应"""
    plain = secrets.token_urlsafe(32)
    return plain, encrypt(plain), token_hash(plain)


async def migrate_access_tokens(db) -> int:
    """历史明文 Token → 密文 + 摘要；只有密文缺摘要的补算摘要。已安装的 Agent / 采集器无需改动"""
    migrated = 0
    for table, column in (("servers", "agent_token"), ("sites", "probe_token")):
        rows = await db.fetch_all(
            f"SELECT id, {column} AS v FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' "
            f"AND ({column} NOT LIKE 'enc:v1:%' OR {column}_hash IS NULL)"
        )
        for row in rows:
            plain = decrypt(row["v"])
            if not plain:
                logger.error(f"{table}#{row['id']} 的 {column} 无法解密，跳过（请在管理端重置该 Token）")
                continue
            await db.execute(
                f"UPDATE {table} SET {column}=$1, {column}_hash=$2 WHERE id=$3",
                encrypt(plain), token_hash(plain), row["id"],
            )
            migrated += 1
    if migrated:
        logger.info(f"已迁移 {migrated} 个接入 Token（加密存储 + 摘要鉴权）")
    return migrated


async def migrate_plaintext_credentials(db) -> int:
    """把 servers.password / network_devices.snmp_community 中的历史明文加密"""
    migrated = 0
    for table, column in (("servers", "password"), ("network_devices", "snmp_community")):
        rows = await db.fetch_all(
            f"SELECT id, {column} AS v FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' "
            f"AND {column} NOT LIKE 'enc:v1:%'"
        )
        for row in rows:
            await db.execute(f"UPDATE {table} SET {column}=$1 WHERE id=$2", encrypt(row["v"]), row["id"])
            migrated += 1
    if migrated:
        logger.info(f"已加密 {migrated} 条历史明文凭据")
    return migrated
