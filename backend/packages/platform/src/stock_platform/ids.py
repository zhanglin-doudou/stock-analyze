"""公开 ID 生成：ULID 风格（时间有序、可排序、无碰撞）。

同一毫秒内必须**严格递增**：导入时一张账单上百行会在同一毫秒里生成 ID，
若随机位任意取值，`ORDER BY id` 就不再等于写入（文件行号）顺序。ULID 规范允许
用「上一条 +1」的单调策略填充随机位，这里采用该策略；跨进程不保证单调，
但同一进程内的导入写入顺序与 ID 顺序一致。
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Final

_CROCKFORD: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MAX_RANDOM: Final[int] = 1 << 80

_lock = threading.Lock()
_last_ms: int = -1
_last_random: int = 0


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def _next_parts(ms: int) -> tuple[int, int]:
    """同一毫秒内递增随机位；时钟回拨时沿用上一次时间，保证 ID 单调。"""
    global _last_ms, _last_random
    with _lock:
        if ms > _last_ms:
            _last_ms = ms
            _last_random = secrets.randbits(80)
        elif _last_random + 1 >= _MAX_RANDOM:
            _last_ms += 1
            _last_random = 0
        else:
            _last_random += 1
        return _last_ms, _last_random


def new_ulid(now_ms: int | None = None) -> str:
    """26 字符 ULID：10 位时间 + 16 位随机（同毫秒单调递增）。"""
    if now_ms is not None:
        return _encode(now_ms, 10) + _encode(secrets.randbits(80), 16)
    ms, value = _next_parts(int(time.time() * 1000))
    return _encode(ms, 10) + _encode(value, 16)


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
# 导入映射 v2（2026-11-13）：用户字段模板 / 账单类型映射 / 系统列规则。
PREFIX_IMPORT_TEMPLATE = "imt"
PREFIX_IMPORT_BILL_TYPE = "ibt"
PREFIX_IMPORT_COLUMN_RULE = "icr"


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


def import_template_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_IMPORT_TEMPLATE, now_ms)


def import_bill_type_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_IMPORT_BILL_TYPE, now_ms)


def import_column_rule_id(now_ms: int | None = None) -> str:
    return new_id(PREFIX_IMPORT_COLUMN_RULE, now_ms)
