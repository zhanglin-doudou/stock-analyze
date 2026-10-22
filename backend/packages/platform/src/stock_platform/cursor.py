"""不透明游标编解码（API-Catalog §0.2 游标分页）。

排序键 → URL 安全 base64（无填充）；任何损坏一律 INVALID_CURSOR，
避免把内部排序键暴露成可猜的 offset。
"""

from __future__ import annotations

import base64
import binascii
import json

from stock_contracts.errors import ApiError, ErrorCode


def encode_cursor(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标格式错误") from exc
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ApiError(ErrorCode.INVALID_CURSOR, detail="游标内容非法")
    return data


__all__ = ["decode_cursor", "encode_cursor"]
