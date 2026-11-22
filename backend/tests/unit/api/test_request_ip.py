"""客户端 IP 解析：只信受信代理，XFF 取最右值（DEPLOYMENT P0-2.4 / P0-2.6）。"""

from __future__ import annotations

import pytest
from starlette.requests import Request
from stock_api.request_ip import client_ip_hash
from stock_platform.config import Settings
from stock_platform.security import hash_ip

pytestmark = pytest.mark.no_db

TRUSTED_PEER = "172.20.0.5"  # 模拟同 Docker 网络内的反代网关（web 内 nginx）
UNTRUSTED_PEER = "198.51.100.9"


def _request(peer: str, headers: dict[str, str]) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw_headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        STOCK_ENV="test",
        STOCK_DB_HOST="127.0.0.1",
        TRUSTED_PROXY_CIDRS="172.16.0.0/12",
    )


def test_trusted_peer_prefers_x_real_ip() -> None:
    request = _request(
        TRUSTED_PEER,
        {"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "6.6.6.6, 203.0.113.7"},
    )
    assert client_ip_hash(request, _settings()) == hash_ip("203.0.113.7")


def test_trusted_peer_uses_rightmost_xff_when_real_ip_absent() -> None:
    request = _request(TRUSTED_PEER, {"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert client_ip_hash(request, _settings()) == hash_ip("203.0.113.7")


def test_trusted_peer_ignores_forged_leftmost_xff() -> None:
    # 客户端自带 "6.6.6.6" 伪造值；反代在末尾追加真实对端 203.0.113.7
    request = _request(TRUSTED_PEER, {"X-Forwarded-For": "6.6.6.6,203.0.113.7"})
    assert client_ip_hash(request, _settings()) == hash_ip("203.0.113.7")
    assert client_ip_hash(request, _settings()) != hash_ip("6.6.6.6")


def test_untrusted_peer_ignores_forwarded_headers() -> None:
    request = _request(
        UNTRUSTED_PEER,
        {"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7"},
    )
    assert client_ip_hash(request, _settings()) == hash_ip(UNTRUSTED_PEER)


def test_trusted_peer_with_invalid_forwarded_values_falls_back_to_peer() -> None:
    request = _request(TRUSTED_PEER, {"X-Forwarded-For": "not-an-ip, , 999.1.1.1"})
    assert client_ip_hash(request, _settings()) == hash_ip(TRUSTED_PEER)


def test_trusted_peer_supports_ipv6_xff() -> None:
    request = _request(TRUSTED_PEER, {"X-Forwarded-For": "2001:db8::1"})
    assert client_ip_hash(request, _settings()) == hash_ip("2001:db8::1")
