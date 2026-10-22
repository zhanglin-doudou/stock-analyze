"""公开 ID 生成：ULID 风格（时间有序、可排序、无碰撞）。"""

from __future__ import annotations

import os
import secrets
import time
from typing import Final

_CROCKFORD: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid(now_ms: int | None = None) -> str:
    """26 字符 ULID：10 位时间 + 16 位随机。"""
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return _encode(ms, 10) + _encode(secrets.randbits(80), 16)


def new_id(prefix: str, now_ms: int | None = None) -> str:
    """业务公开 ID，例如 `trd_01J9Z6…`、`job_01J9Z6…`。"""
    return f"{prefix}_{new_ulid(now_ms)}"


def new_ulid_for_user(prefix: str) -> str:
    return new_id(prefix)


def new_request_id() -> str:
    return os.urandom(8).hex()


PREFIX_TRADE = "trd"
PREFIX_BATCH = "imp"
PREFIX_PREVIEW = "prv"
PREFIX_JOB = "job"
PREFIX_RULE = "rul"
PREFIX_TOKEN = "tok"
PREFIX_REPORT = "rpt"
PREFIX_EXPORT = "exp"
PREFIX_SHARE = "shr"
PREFIX_RECON = "rec"
PREFIX_BACKUP = "bak"


def trade_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_TRADE, now_ms)


def batch_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_BATCH, now_ms)


def preview_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_PREVIEW, now_ms)


def job_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_JOB, now_ms)


def rule_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_RULE, now_ms)


def token_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_TOKEN, now_ms)


def backup_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_BACKUP, now_ms)
