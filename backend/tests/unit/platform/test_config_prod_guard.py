"""生产密钥守卫：dev 默认值/过短密钥必须启动失败（DEPLOYMENT P0-3.3）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from stock_platform.config import DEFAULT_EMAIL_CODE_PEPPER, DEV_JWT_SECRET, Settings

pytestmark = pytest.mark.no_db

STRONG_SECRET = "s" * 40


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "STOCK_ENV": "prod",
        "STOCK_JWT_SECRET": STRONG_SECRET,
        "EMAIL_CODE_PEPPER": STRONG_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_prod_accepts_strong_secrets() -> None:
    settings = _settings()
    assert settings.env == "prod"


def test_non_prod_still_allows_dev_defaults() -> None:
    settings = _settings(
        STOCK_ENV="local",
        STOCK_JWT_SECRET=DEV_JWT_SECRET,
        EMAIL_CODE_PEPPER=DEFAULT_EMAIL_CODE_PEPPER,
    )
    assert settings.env == "local"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("STOCK_JWT_SECRET", DEV_JWT_SECRET),
        ("STOCK_JWT_SECRET", "too-short"),
        ("EMAIL_CODE_PEPPER", DEFAULT_EMAIL_CODE_PEPPER),
        ("EMAIL_CODE_PEPPER", "too-short"),
    ],
)
def test_prod_rejects_weak_secrets(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})
