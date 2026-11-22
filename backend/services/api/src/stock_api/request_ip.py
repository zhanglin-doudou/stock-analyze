"""客户端 IP 解析：只信任受信代理覆盖的转发头。

`TRUSTED_PROXY_CIDRS` 内的 peer 才采信转发头（否则客户端可任意伪造）：
- 优先 `X-Real-IP`（nginx 惯例）；
- 回退 `X-Forwarded-For` 的**最右合法值**（Traefik 在链尾追加它看到的对端地址；
  最左值可被客户端伪造，绝不能取）。

IP 只以哈希形式使用。
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


def _forwarded_for_ip(request: Request) -> str | None:
    """取 `X-Forwarded-For` 最右合法值（仅应在 peer 受信时调用）。

    代理在链尾追加它看到的对端地址；最左值可由客户端伪造，取最右值才是
    「离我们最近的一跳」。
    """
    header = request.headers.get("x-forwarded-for", "")
    for candidate in reversed(header.split(",")):
        value = candidate.strip()
        if value and _valid_ip(value):
            return value
    return None


def client_ip_hash(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else None
    if _is_trusted(peer, settings.trusted_proxy_cidrs):
        # 优先 X-Real-IP（nginx 惯例）；Traefik 默认只发 X-Forwarded-For，回退取最右值
        real_ip = request.headers.get("x-real-ip", "").strip()
        if not _valid_ip(real_ip):
            real_ip = _forwarded_for_ip(request) or ""
        if real_ip and _valid_ip(real_ip):
            return hash_ip(real_ip) or hash_ip("unknown") or "unknown"
    return hash_ip(peer) or hash_ip("unknown") or "unknown"


__all__ = ["client_ip_hash"]
