"""
日志预处理：发送给 AI 之前清洗、脱敏、折叠重复行、控制长度

- 脱敏：密码 / Token / API Key / 私钥 / JWT 等替换为 ***，避免凭据被发送到第三方模型或落库
- 折叠：去掉时间戳和数字后内容相同的行只保留首次出现，并标注重复次数（节省 token，也便于 AI 看出高频问题）
- 截断：超过上限时保留最新的部分，并在 stats 中标明 truncated，界面和提示词里都会说明
"""
import re
from collections import OrderedDict

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MASKS = [
    # -----BEGIN ... PRIVATE KEY----- ... -----END ... PRIVATE KEY-----
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[私钥已隐藏]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "***JWT***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***"),
    # password=xxx / "token": "xxx" / api_key: xxx / 密码：xxx
    (re.compile(
        r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
        r"authorization|credential|密码|口令|密钥)[\"']?\s*[=:：]\s*[\"']?)[^\s\"',;&]+"
    ), r"\1***"),
    # URL 中的用户名:密码@
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+):[^\s/@]+@"), r"\1:***@"),
]

_TS_PREFIX = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"
    r"|\[\s*\d+\.\d+\])\s*"
)
_DIGITS = re.compile(r"\d+")
_MAX_LINE = 1000


def mask_secrets(text: str) -> str:
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text


def _signature(line: str) -> str:
    return _DIGITS.sub("#", _TS_PREFIX.sub("", line)).strip()


def preprocess(raw: str, max_chars: int = 60000) -> tuple:
    """返回 (处理后的文本, 统计信息)"""
    text = mask_secrets(_CTRL.sub("", _ANSI.sub("", (raw or "").replace("\r\n", "\n").replace("\r", "\n"))))
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln if len(ln) <= _MAX_LINE else ln[:_MAX_LINE] + " …" for ln in lines if ln.strip()]

    groups: "OrderedDict[str, list]" = OrderedDict()
    for ln in lines:
        # 分节标题（===== xxx =====）不折叠
        key = ln if ln.startswith("=====") else _signature(ln)
        if key in groups and not ln.startswith("====="):
            groups[key][1] += 1
        else:
            groups[key if key not in groups else f"{key}\x00{len(groups)}"] = [ln, 1]

    out = [ln if n == 1 else f"{ln}  [相似行共 {n} 条]" for ln, n in groups.values()]
    body = "\n".join(out)

    truncated = False
    max_chars = max(2000, int(max_chars or 60000))
    if len(body) > max_chars:
        truncated = True
        body = body[-max_chars:]
        body = body[body.find("\n") + 1:] if "\n" in body else body  # 不从半行开始

    stats = {
        "raw_lines": len(lines),
        "unique_lines": len(out),
        "chars": len(body),
        "truncated": truncated,
    }
    return body, stats
