"""API-022~025 验收：摘要规则列表 / 新建 / 修改（If-Match）/ 删除（回退系统规则）。

覆盖 INTERFACE §4.1：
* API-022：系统默认规则存在且 code 唯一；跨用户列表隔离；admin 403；
* API-023：201 + `usr_`+8hex code；重复/非法 category → 422；新规则对 reclassify 生效；
* API-024：200 + `ETag: W/"<version>"`；旧 If-Match → 409 VERSION_CONFLICT；缺失 → 422；
  系统规则/他人规则 → 404；
* API-025：204；删除后列表不再返回（分类自然回退系统规则）；系统/他人规则 → 404。
"""

from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, SummaryRule, Trade
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_USR_CODE_RE = re.compile(r"^usr_[0-9a-f]{8}$")


async def _drain_worker(settings) -> None:
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def _seed_user_rule(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    rule_id = f"rul_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            SummaryRule(
                id=rule_id,
                user_id=user_id,
                code=f"usr_{uuid.uuid4().hex[:8]}",
                name="owner 私有规则",
                category="other",
                side="both",
                match_type="exact",
                pattern=f"owner-{uuid.uuid4().hex[:6]}",
                priority=100,
                enabled=True,
                is_system=False,
                version=1,
            )
        )
        await session.commit()
    return rule_id


async def _seed_batch(engine: AsyncEngine, *, user_id: uuid.UUID) -> str:
    batch_id = f"bat_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            ImportBatch(
                id=batch_id,
                user_id=user_id,
                preview_id=None,
                status="succeeded",
                mode="full",
                broker="huatai",
                file_name="bill.csv",
                file_sha256="0" * 64,
                file_bytes=128,
                parsed_count=1,
                inserted_count=1,
                skipped_count=0,
                quarantined_count=0,
                columns={"成交日期": "trade_date"},
                warnings=[],
                assumptions={},
                ranges={},
            )
        )
        await session.commit()
    return batch_id


async def _seed_trade(
    engine: AsyncEngine, *, user_id: uuid.UUID, batch_id: str, summary: str
) -> None:
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=f"trd_{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                batch_id=batch_id,
                trade_date=date(2026, 6, 15),
                security_code="600519",
                security_name="贵州茅台",
                side="buy",
                quantity_units=1_000_000,
                price_units=100_000_000,
                amount_units=-1_000_000,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=None,
                cash_balance_available=False,
                occurrence_idx=0,
                source="import",
                version=1,
                summary=summary,
                dedupe_key=f"600519:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()


async def _list_rules(client, api_prefix, headers) -> dict:
    resp = await client.get(f"{api_prefix}/summary-rules", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _create_rule(client, api_prefix, user) -> dict:
    resp = await client.post(
        f"{api_prefix}/summary-rules",
        headers=user.headers,
        json={
            "name": "我的自定义规则",
            "category": "transfer",
            "side": "both",
            "match_type": "contains",
            "pattern": f"自定义-{uuid.uuid4().hex[:6]}",
            "priority": 30,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_api_022_default_rules_present_and_codes_unique(client, api_prefix, user) -> None:
    body = await _list_rules(client, api_prefix, user.headers)
    items = body["items"]
    assert body["total"] == len(items)
    assert len(items) >= 5, "系统默认规则缺失"
    codes = [item["code"] for item in items]
    assert len(codes) == len(set(codes)), "规则 code 不唯一"
    assert sum(1 for item in items if item["is_system"]) >= 5
    assert {"sys_buy_stock", "sys_sell_stock"} <= set(codes)


async def test_api_022_cross_user_list_isolated(
    client, api_prefix, user, account_factory, owner_engine: AsyncEngine
) -> None:
    owner_rule = await _seed_user_rule(owner_engine, user_id=user.user_id)
    owner_ids = {
        item["rule_id"] for item in (await _list_rules(client, api_prefix, user.headers))["items"]
    }
    assert owner_rule in owner_ids

    intruder = await account_factory()
    intruder_ids = {
        item["rule_id"]
        for item in (await _list_rules(client, api_prefix, intruder.headers))["items"]
    }
    assert owner_rule not in intruder_ids, "B 的规则列表泄露了 A 的私有规则"


async def test_api_022_admin_forbidden(client, api_prefix, admin) -> None:
    resp = await client.get(f"{api_prefix}/summary-rules", headers=admin.headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_api_023_create_rule(client, api_prefix, user) -> None:
    created = await _create_rule(client, api_prefix, user)
    assert _USR_CODE_RE.match(created["code"]), created
    assert created["is_system"] is False
    assert created["version"] == 1
    assert created["category"] == "transfer"
    listed = {
        item["rule_id"]: item
        for item in (await _list_rules(client, api_prefix, user.headers))["items"]
    }
    assert created["rule_id"] in listed


async def test_api_023_duplicate_and_invalid_category(client, api_prefix, user) -> None:
    payload = {
        "name": "重复规则",
        "category": "other",
        "side": "both",
        "match_type": "exact",
        "pattern": "重复摘要ABC",
        "priority": 100,
    }
    assert (
        await client.post(f"{api_prefix}/summary-rules", headers=user.headers, json=payload)
    ).status_code == 201
    duplicate = await client.post(f"{api_prefix}/summary-rules", headers=user.headers, json=payload)
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == ErrorCode.VALIDATION_ERROR.value

    payload["pattern"] = "另一个摘要"
    payload["category"] = "not_a_category"
    invalid = await client.post(f"{api_prefix}/summary-rules", headers=user.headers, json=payload)
    assert invalid.status_code == 422

    payload["category"] = "other"
    payload["match_type"] = "regex"
    payload["pattern"] = "([未闭合"
    bad_regex = await client.post(f"{api_prefix}/summary-rules", headers=user.headers, json=payload)
    assert bad_regex.status_code == 422


async def test_api_023_rule_takes_effect_on_reclassify(
    client, api_prefix, user, owner_engine: AsyncEngine, settings
) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    await _seed_trade(
        owner_engine, user_id=user.user_id, batch_id=batch_id, summary="新规则生效摘要ZZZ"
    )
    payload = {
        "name": "生效验证",
        "category": "transfer",
        "side": "both",
        "match_type": "contains",
        "pattern": "新规则生效摘要ZZZ",
        "priority": 10,
    }
    created = await client.post(f"{api_prefix}/summary-rules", headers=user.headers, json=payload)
    assert created.status_code == 201, created.text

    resp = await client.post(
        f"{api_prefix}/imports/{batch_id}/reclassify",
        headers={**user.headers, "Idempotency-Key": "idem-" + uuid.uuid4().hex},
        json={},
    )
    assert resp.status_code == 202, resp.text
    await _drain_worker(settings)

    trades = await client.get(f"{api_prefix}/trades?batch_id={batch_id}", headers=user.headers)
    assert trades.status_code == 200, trades.text
    assert {item["category"] for item in trades.json()["items"]} == {"transfer"}


async def test_api_024_patch_version_and_conflict(client, api_prefix, user) -> None:
    created = await _create_rule(client, api_prefix, user)
    rule_id = created["rule_id"]
    resp = await client.patch(
        f"{api_prefix}/summary-rules/{rule_id}",
        headers={**user.headers, "If-Match": 'W/"1"'},
        json={"name": "改名后", "priority": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["name"] == "改名后"
    assert resp.headers["ETag"] == 'W/"2"'

    stale = await client.patch(
        f"{api_prefix}/summary-rules/{rule_id}",
        headers={**user.headers, "If-Match": 'W/"1"'},
        json={"name": "并发覆盖"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == ErrorCode.VERSION_CONFLICT.value

    missing = await client.patch(
        f"{api_prefix}/summary-rules/{rule_id}", headers=user.headers, json={"name": "缺版本"}
    )
    assert missing.status_code == 422


async def test_api_024_patch_system_and_foreign_rule_404(
    client, api_prefix, user, account_factory, owner_engine: AsyncEngine
) -> None:
    items = (await _list_rules(client, api_prefix, user.headers))["items"]
    system_rule = next(item for item in items if item["is_system"])
    resp = await client.patch(
        f"{api_prefix}/summary-rules/{system_rule['rule_id']}",
        headers={**user.headers, "If-Match": 'W/"1"'},
        json={"name": "试图改系统规则"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value

    foreign_rule = await _seed_user_rule(owner_engine, user_id=user.user_id)
    intruder = await account_factory()
    foreign = await client.patch(
        f"{api_prefix}/summary-rules/{foreign_rule}",
        headers={**intruder.headers, "If-Match": 'W/"1"'},
        json={"name": "跨租户改"},
    )
    assert foreign.status_code == 404


async def test_api_025_delete_rule_falls_back_to_system(client, api_prefix, user) -> None:
    created = await _create_rule(client, api_prefix, user)
    rule_id = created["rule_id"]
    deleted = await client.delete(f"{api_prefix}/summary-rules/{rule_id}", headers=user.headers)
    assert deleted.status_code == 204

    listed = {
        item["rule_id"] for item in (await _list_rules(client, api_prefix, user.headers))["items"]
    }
    assert rule_id not in listed
    # 系统规则仍在：删除用户规则后分类自然回退内置规则。
    codes = {
        item["code"] for item in (await _list_rules(client, api_prefix, user.headers))["items"]
    }
    assert {"sys_buy_stock", "sys_sell_stock"} <= codes

    again = await client.delete(f"{api_prefix}/summary-rules/{rule_id}", headers=user.headers)
    assert again.status_code == 404


async def test_api_025_delete_system_and_foreign_rule_404(
    client, api_prefix, user, account_factory, owner_engine: AsyncEngine
) -> None:
    items = (await _list_rules(client, api_prefix, user.headers))["items"]
    system_rule = next(item for item in items if item["is_system"])
    resp = await client.delete(
        f"{api_prefix}/summary-rules/{system_rule['rule_id']}", headers=user.headers
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value

    foreign_rule = await _seed_user_rule(owner_engine, user_id=user.user_id)
    intruder = await account_factory()
    foreign = await client.delete(
        f"{api_prefix}/summary-rules/{foreign_rule}", headers=intruder.headers
    )
    assert foreign.status_code == 404
