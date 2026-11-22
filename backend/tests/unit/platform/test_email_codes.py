"""邮箱验证码：生成、哈希与模板纯函数。"""

from __future__ import annotations

import re

import pytest
from stock_platform.email_templates import (
    render_password_reset_email,
    render_registration_email,
)
from stock_platform.email_verification import generate_code
from stock_platform.repositories.email_codes import throttle_key
from stock_platform.security import EmailCodeHasher


def test_generate_code_is_six_digits() -> None:
    for _ in range(20):
        code = generate_code()
        assert re.fullmatch(r"\d{6}", code)


def test_email_code_hasher_roundtrip_and_wrong_code() -> None:
    hasher = EmailCodeHasher("x" * 32, time_cost=1)
    digest = hasher.hash("123456")
    assert hasher.verify(digest, "123456")
    assert not hasher.verify(digest, "654321")


def test_email_code_hasher_rejects_short_pepper() -> None:
    with pytest.raises(ValueError):
        EmailCodeHasher("short")


def test_throttle_key_scope_and_purpose_isolated() -> None:
    a = throttle_key("email:register", "a@example.com")
    b = throttle_key("email:reset_password", "a@example.com")
    c = throttle_key("ip", "a@example.com")
    assert len(a) == 64
    assert a != b != c


def test_registration_email_contains_code_but_subject_does_not() -> None:
    content = render_registration_email(
        code="123456", ttl_seconds=600, brand="星星助手", site_host="xingst.site"
    )
    assert "123456" in content.html
    assert "123456" in content.text
    assert "123456" not in content.subject
    assert "10 分钟" in content.html
    assert "<img" not in content.html


def test_password_reset_email_copy_mentions_revocation() -> None:
    content = render_password_reset_email(
        code="654321", ttl_seconds=600, brand="星星助手", site_host="xingst.site"
    )
    assert "重置密码" in content.subject
    assert "654321" in content.html
    assert "API Token 将失效" in content.html
    assert "654321" not in content.subject
