"""stock_contracts.constants 单测：标度/容差/业务常量值须稳定。"""

from __future__ import annotations

from decimal import Decimal

from stock_contracts import constants as c


def test_tolerance_constants() -> None:
    assert Decimal("0.01") == c.EPS_AMT
    assert Decimal("0.0001") == c.EPS_R
    assert Decimal("0.00000001") == c.EPS_CUM


def test_scale_constants() -> None:
    assert c.SCALE_AMOUNT == 2
    assert c.SCALE_PRICE == 6
    assert c.SCALE_QTY == 4
    assert c.SCALE_RATIO == 8


def test_statistics_constants() -> None:
    assert c.A_SHUFFLE_DAYS == 244
    assert c.ANNUALIZATION_BASE == c.A_SHUFFLE_DAYS
    assert c.MIN_CONCLUSION_SAMPLE == 10
    assert c.MIN_CI_SAMPLE == 10


def test_auth_and_import_constants() -> None:
    assert c.ACCESS_TOKEN_TTL_SECONDS == 900
    assert c.DEFAULT_REFRESH_TTL_SECONDS == 2_592_000
    assert c.PAT_MAX_DAYS == 365
    assert c.LOGIN_FAILURE_WINDOW_SECONDS == 300
    assert c.LOGIN_FAILURE_MAX_ATTEMPTS == 5
    assert c.IMPORT_PREVIEW_TTL_HOURS == 24
    assert c.FILE_MAX_BYTES == 20 * 1024 * 1024


def test_pagination_and_share_constants() -> None:
    assert c.CURSOR_DEFAULT_LIMIT == 50
    assert c.CURSOR_MAX_LIMIT == 200
    assert c.REPORT_SHARE_MAX_SECONDS == 2_592_000
