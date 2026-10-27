"""规范化 JSON / 指纹 / seed 派生（Period2-05，E-04/E-08/D-07）。

约定：
- 键排序、无空格、UTF-8；Decimal → 定点字符串；**拒绝 float/NaN**；
- `params_hash` = 64 hex（`String(64)`）；`inputs_hash` / 指纹 = `sha256:<64hex>`
  （71 字符，落 `String(80)`）；两者都不含 user_id / period 之外的请求上下文；
- bootstrap seed 由 `(metric_id, definition_version, inputs_hash)` 派生，
  同一输入的 CI 逐位可复现（D-07）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

_SHA_PREFIX = "sha256:"


class CanonicalJsonError(TypeError):
    """输入含 float / 非字符串键 / 不可序列化对象（架构禁止 float）。"""


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        raise CanonicalJsonError("canonical json 禁止 float")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalJsonError("canonical json 禁止 NaN/Infinity")
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError(f"canonical json 的键必须是字符串: {key!r}")
            normalized[key] = _canonical(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    raise CanonicalJsonError(f"canonical json 不支持的类型: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    """确定性 JSON 文本；同一对象跨进程字节一致。"""
    return json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def params_hash(params: Mapping[str, Any]) -> str:
    """参数原文（不含 user/period）的 64 hex 指纹（E-02）。"""
    return sha256_hex(canonical_json(params))


def inputs_hash(payload: Mapping[str, Any]) -> str:
    """引擎输入指纹（E-04/E-16）：`sha256:<64hex>`。"""
    return _SHA_PREFIX + sha256_hex(canonical_json(payload))


def object_id(prefix: str, *parts: object) -> str:
    """确定性主键：`<prefix>_` + sha256(canonical(parts))[:32]。"""
    return f"{prefix}_{sha256_hex(canonical_json(list(parts)))[:32]}"


def derive_seed(*parts: object) -> int:
    """从稳定文本派生 64-bit seed（bootstrap 用）；同输入逐位一致。"""
    digest = sha256_hex("|".join(str(part) for part in parts))
    return int(digest[:16], 16)


__all__ = [
    "CanonicalJsonError",
    "canonical_json",
    "derive_seed",
    "inputs_hash",
    "object_id",
    "params_hash",
    "sha256_hex",
]
