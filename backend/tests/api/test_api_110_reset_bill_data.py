"""API-110 验收：`POST /settings/reset-bill-data` 清空本人 ledger/review 数据。

覆盖：happy path（按表删除计数 + total）、跨用户隔离、幂等重放、缺幂等键 422、
admin 403、无数据时显式空计数。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.api

API_PREFIX = "/api/v1"
_RESET = f"{API_PREFIX}/settings/reset-bill-data"
_KEY = "reset-bill-data-key-0001"

_INSERT_TRADE_SQL = """
insert into ledger.trade (
    id, user_id, trade_date, security_code, security_name, side,
    quantity_units, price_units, amount_units, commission_units,
    stamp_tax_units, other_fee_units, cash_balance_available,
    dedupe_key, occurrence_idx, source, version
) values (
    :id, :uid, '2026-06-01', '600519', '贵州茅台', 'buy',
    1000000, 12340000, 123400, 0, 0, 0, false,
    :ddk, 0, 'import', 1
)
"""

_INSERT_BATCH_SQL = """
insert into ledger.import_batch (
    id, user_id, status, mode, broker, file_name, file_sha256, file_bytes,
    parsed_count, inserted_count, skipped_count, quarantined_count,
    columns, assumptions, warnings, ranges
) values (
    :id, :uid, 'committed', 'append', 'generic', 'demo.xlsx', :sha, 10,
    1, 1, 0, 0, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, '{}'::jsonb
)
"""

_INSERT_ROUND_SQL = """
insert into review.round (
    id, user_id, security_code, security_name, status, open_trade_id,
    open_date, quantity_units, cost_units, proceeds_units, dividend_units,
    pnl_units, return_rate_units, holding_days, data_error, oversell_units,
    fills_count, rebuilt_at
) values (
    :id, :uid, '600519', '贵州茅台', 'open', :trade_id,
    '2026-06-01', 1000000, 123400, 0, 0,
    0, 0, 0, false, 0, 1, now()
)
"""

_INSERT_ASSET_SQL = """
insert into review.asset_daily (
    id, user_id, trade_date, cash_units, market_value_units, total_assets_units,
    holding_cost_units, unrealized_pnl_units, holding_count, cashflow_units,
    prev_total_assets_units, daily_pnl_units, daily_return_units,
    status, status_reason, definition_version, rebuilt_at
) values (
    :id, :uid, '2026-06-01', 0, 100000, 100000, 0, 0, 1, 0,
    100000, 0, 0, 'ok', null, '1.9', now()
)
"""


async def _seed(engine: AsyncEngine, user_id: uuid.UUID) -> None:
    trade_id = f"trd_{uuid.uuid4().hex[:16]}"
    async with engine.begin() as conn:
        await conn.execute(
            text(_INSERT_TRADE_SQL),
            {"id": trade_id, "uid": user_id, "ddk": f"ddk_{uuid.uuid4().hex}"},
        )
        await conn.execute(
            text(_INSERT_BATCH_SQL),
            {"id": f"imp_{uuid.uuid4().hex[:16]}", "uid": user_id, "sha": "a" * 64},
        )
        await conn.execute(
            text(_INSERT_ROUND_SQL),
            {"id": f"rnd_{uuid.uuid4().hex[:16]}", "uid": user_id, "trade_id": trade_id},
        )
        await conn.execute(
            text(_INSERT_ASSET_SQL),
            {"id": f"ast_{uuid.uuid4().hex[:16]}", "uid": user_id},
        )


async def _count(engine: AsyncEngine, table: str, user_id: uuid.UUID) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(f"select count(*) from {table} where user_id = :uid"), {"uid": user_id}
        )
    return int(row.scalar_one())


async def test_api_110_reset_deletes_only_callers_data(
    client: httpx.AsyncClient, owner_engine: AsyncEngine, account_factory: Any
) -> None:
    alice = await account_factory()
    bob = await account_factory()
    await _seed(owner_engine, alice.user_id)
    await _seed(owner_engine, bob.user_id)

    resp = await client.post(_RESET, headers={**alice.headers, "Idempotency-Key": _KEY})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == {
        "round": 1,
        "asset_daily": 1,
        "trade": 1,
        "import_batch": 1,
    }
    assert body["total"] == 4

    for table in ("ledger.trade", "ledger.import_batch", "review.round"):
        assert await _count(owner_engine, table, alice.user_id) == 0
        assert await _count(owner_engine, table, bob.user_id) == 1

    # 幂等重放：返回首次结果，不重复删除。
    replay = await client.post(_RESET, headers={**alice.headers, "Idempotency-Key": _KEY})
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == body
    assert await _count(owner_engine, "review.round", bob.user_id) == 1


async def test_api_110_requires_idempotency_key(client: httpx.AsyncClient, user: Any) -> None:
    resp = await client.post(_RESET, headers=user.headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


async def test_api_110_admin_forbidden(client: httpx.AsyncClient, account_factory: Any) -> None:
    admin = await account_factory(role="admin")
    resp = await client.post(_RESET, headers={**admin.headers, "Idempotency-Key": _KEY})
    assert resp.status_code == 403
    assert resp.json()["code"] == "ADMIN_CANNOT_READ_TRADES"


async def test_api_110_empty_data_returns_zero(client: httpx.AsyncClient, user: Any) -> None:
    resp = await client.post(_RESET, headers={**user.headers, "Idempotency-Key": _KEY})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": {}, "total": 0}
