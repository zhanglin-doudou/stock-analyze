"""API-036 `GET /attribution` 验收（2026-11-02 五因子设计 §6/§7.3）。

覆盖：happy（空账户 → 200 blocked + 金额键缺省 + factors 固定 5 项）、admin 403、
未认证 401、422（period 缺失/非法、model=brinson_industry、lookahead 部署未开、
benchmark 非法）、400 UNKNOWN_QUERY_PARAM、跨账号各自可请求（列表型端点无资源 ID，
矩阵级隔离由 test_isolation_matrix 兜底）。

数据充足（ok/degraded）的黄金向量路径在 `backend/tests/unit/review/test_attribution_assembly.py`
与 `backend/tests/unit/core/test_attribution.py`（replay 注入行情/板块源，设计 §7.1）；
本文件只测协议层与降级短路径，不依赖板块夹具。
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import pytest_asyncio
from stock_api.app import create_app
from stock_contracts.clock import FrozenClock

pytestmark = pytest.mark.api

_CLOCK = "2026-07-01T09:00:00+00:00"
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
_RATIO_RE = re.compile(r"^-?\d+\.\d{8}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_FACTOR_CODES = ["selection", "sector", "buy_timing", "sell_timing", "fee"]
_ASSUMPTION_KEYS = {
    "total_pnl_basis",
    "beginning_value_source",
    "cashflow_basis",
    "fee_basis",
    "dividend_basis",
    "sector_basis",
    "classification_version",
    "classification_as_of",
    "classification_lookahead",
    "board_bar_as_of",
    "benchmark_id",
    "benchmark_total_return_proxy",
    "ratio_basis",
    "attributed_universe",
    "corporate_action_policy",
    "rounding",
    "adjust",
    "data_sources",
}


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(_CLOCK)


@pytest_asyncio.fixture
async def attr_client(settings: Any, frozen_clock: FrozenClock) -> Any:
    """frozen-clock 归因客户端（只影响业务时钟；token 验证走真实时间）。

    注意不要命名为 `client`——覆盖 conftest 的 client 会让 account_factory 的
    登录也走 frozen app，签出的 token 会被 PyJWT 的真实时间判为已过期。
    """
    app = create_app(settings, clock=frozen_clock)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ── happy（blocked 短路：空账户无资产窗口 → 200 + 键缺省，验收矩阵⑤）──


async def test_api_036_blocked_empty_account(attr_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution",
        headers=acc.headers,
        params={"period": "2026-06"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["model"] == "five_factor"
    assert body["ratio_basis"] == "amount/total_pnl"
    assert body["period"] == "2026-06"
    assert body["status"] == "blocked"
    assert body["definition_version"] == "1.0"
    assert body["total_pnl_basis"] == "asset_daily_mtm"
    assert body["ratio_na_reason"] is None
    # blocked 缺省语义（§6.3 冻结）：金额/覆盖整键省略，不输出 0 冒充
    for key in (
        "total_pnl",
        "base_capital",
        "residual",
        "residual_ratio",
        "residual_breakdown",
        "coverage",
    ):
        assert key not in body, key
    # factors 固定 5 项、固定顺序；code/name/rank 保留，amount 缺省、conclusion=null
    factors = body["factors"]
    assert [f["code"] for f in factors] == _FACTOR_CODES
    assert [f["rank"] for f in factors] == [1, 2, 3, 4, 5]
    for factor in factors:
        assert "amount" not in factor
        assert "ratio" not in factor
        assert "share" not in factor
        assert factor["conclusion"] is None
        assert factor["presentation"]["tone"] == "flat"
    # 200 降级披露：missing[] 非空、码表可读
    assert body["missing"], "blocked 必须带 missing[]"
    for item in body["missing"]:
        assert item["field"] and item["code"] and item["reason"] and item["what_to_do"]
    # 样本/口径自描述
    assert body["sample"]["min_n"] == 10
    assert body["sample"]["sample_status"] in ("ok", "insufficient")
    assert set(body["assumptions"]) >= _ASSUMPTION_KEYS
    assert body["assumptions"]["total_pnl_basis"] == "asset_daily_mtm"
    assert body["assumptions"]["classification_lookahead"] is False
    assert body["assumptions"]["corporate_action_policy"] == "exclude_symbol"
    assert body["inputs_hash"].startswith("sha256:")
    assert _HEX64_RE.match(body["inputs_hash"].removeprefix("sha256:"))
    assert body["links"] == {
        "dashboard": "/dashboard",
        "sectors": "/sectors",
        "rounds": "/rounds",
        "reports": "/reports",
    }


async def test_api_036_deterministic_inputs_hash(attr_client: Any, account_factory: Any) -> None:
    """验收矩阵⑫：同输入两跑响应逐字节相同（含 inputs_hash）。"""
    acc = await account_factory()
    resp1 = await attr_client.get(
        "/api/v1/attribution", headers=acc.headers, params={"period": "2026-06"}
    )
    resp2 = await attr_client.get(
        "/api/v1/attribution", headers=acc.headers, params={"period": "2026-06"}
    )
    assert resp1.status_code == resp2.status_code == 200
    assert resp1.json() == resp2.json()


# ── 越权 ─────────────────────────────────────────────────────


async def test_api_036_admin_forbidden(attr_client: Any, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await attr_client.get(
        "/api/v1/attribution", headers=admin.headers, params={"period": "2026-06"}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


async def test_api_036_unauthenticated(attr_client: Any) -> None:
    resp = await attr_client.get("/api/v1/attribution", params={"period": "2026-06"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "TOKEN_EXPIRED"


async def test_api_036_cross_account_isolation(attr_client: Any, account_factory: Any) -> None:
    """验收矩阵⑨：A/B 各自 200；列表型端点无资源 ID，不泄露彼此数据。"""
    a = await account_factory()
    b = await account_factory()
    for headers in (a.headers, b.headers):
        resp = await attr_client.get(
            "/api/v1/attribution", headers=headers, params={"period": "2026-06"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "blocked"


# ── 参数校验（验收矩阵⑪：422/422/422/422/400）────────────────


async def test_api_036_period_required(attr_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await attr_client.get("/api/v1/attribution", headers=acc.headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_api_036_invalid_period(attr_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    for bad in ("2026-13", "not-a-period", "2026-01-01..2026-01-02..2026-01-03"):
        resp = await attr_client.get(
            "/api/v1/attribution", headers=acc.headers, params={"period": bad}
        )
        assert resp.status_code == 422, f"{bad}: {resp.text}"
        assert resp.json()["code"] == "INVALID_PERIOD"


async def test_api_036_model_not_available(attr_client: Any, account_factory: Any) -> None:
    """M2 Brinson 未实现 → 422 MODEL_NOT_AVAILABLE（§6.3）。"""
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution",
        headers=acc.headers,
        params={"period": "2026-06", "model": "brinson_industry"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "MODEL_NOT_AVAILABLE"


async def test_api_036_lookahead_requires_deploy_flag(
    attr_client: Any, account_factory: Any
) -> None:
    """`classification_lookahead=allow` 但部署未开 SECTOR_LOOKAHEAD_ENABLED → 422（D-07）。"""
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution",
        headers=acc.headers,
        params={"period": "2026-06", "classification_lookahead": "allow"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "MODEL_NOT_AVAILABLE"


async def test_api_036_unknown_benchmark(attr_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution",
        headers=acc.headers,
        params={"period": "2026-06", "benchmark": "sp500_tr"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "UNKNOWN_BENCHMARK"


async def test_api_036_known_benchmark_accepted(attr_client: Any, account_factory: Any) -> None:
    """合法 benchmark 只回显/披露（D-11），不改变 blocked 短路。"""
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution",
        headers=acc.headers,
        params={"period": "2026-06", "benchmark": "csi1000_tr"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["assumptions"]["benchmark_id"] == "csi1000_tr"


async def test_api_036_unknown_query_param(attr_client: Any, account_factory: Any) -> None:
    acc = await account_factory()
    resp = await attr_client.get(
        "/api/v1/attribution", headers=acc.headers, params={"period": "2026-06", "foo": "bar"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "UNKNOWN_QUERY_PARAM"


async def test_api_036_accept_language_only_switches_copy(
    attr_client: Any, account_factory: Any
) -> None:
    """`Accept-Language: en-US` 不改变数值/结构（§5.5；本夹具空账户无文案可比）。"""
    acc = await account_factory()
    zh = await attr_client.get(
        "/api/v1/attribution", headers=acc.headers, params={"period": "2026-06"}
    )
    en = await attr_client.get(
        "/api/v1/attribution",
        headers={**acc.headers, "Accept-Language": "en-US"},
        params={"period": "2026-06"},
    )
    assert en.status_code == 200, en.text
    zh_body, en_body = zh.json(), en.json()
    # 数值/口径键完全相同（含 inputs_hash 确定性）。
    for key in ("status", "period", "total_pnl_basis", "definition_version", "inputs_hash"):
        assert zh_body[key] == en_body[key], key
    assert [f["code"] for f in en_body["factors"]] == _FACTOR_CODES
    assert [m["code"] for m in en_body["missing"]] == [m["code"] for m in zh_body["missing"]]
