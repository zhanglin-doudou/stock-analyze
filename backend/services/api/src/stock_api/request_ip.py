"""客户端 IP 解析：只信任受信代理覆盖的 X-Real-IP。

现有登录/注册散落取 X-Forwarded-For 最左值，客户端可伪造；本模块统一为
`client_ip_hash`，并供登录失败计数与新验证码限流共用。IP 只以哈希形式使用。
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request
from stock_platform.config import Settings
from stock_platform.security import hash_ip


@lru_cache(maxsize=32)
def _networks(cidrs: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    values: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in cidrs.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(values)


def _is_trusted(peer: str | None, cidrs: str) -> bool:
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _networks(cidrs))


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip_hash(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else None
    if _is_trusted(peer, settings.trusted_proxy_cidrs):
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip and _valid_ip(real_ip):
            return hash_ip(real_ip) or hash_ip("unknown") or "unknown"
    return hash_ip(peer) or hash_ip("unknown") or "unknown"


__all__ = ["client_ip_hash"]
