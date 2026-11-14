"""多资金子账户账单：各子账户独立对账，结果合并归属同一账号。

真实华泰账单里「资金账号」可能有多个（主账户 + 子账户/币种户），每个子账户
各有独立的余额链。旧实现按全表单链核对，跨账户切换必然断链并被判成「多账户」；
现在按子账户拆链，各自判定起点与连续性，入库后仍归属同一账号。
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from stock_worker.runner import WorkerRunner

V2 = {"X-Import-Flow": "v2"}

_HEADER = (
    "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
    "佣金,印花税,其他费,资金余额,资金账号,摘要\n"
)

# 两个子账户的余额链各自连续，但按全表单链看会在第二行断掉：
#   A: 9000 → 10100（+1100）  B: 900 → 1010（+110）
_MULTI_BILL = (
    _HEADER
    + "2026-04-01,600001,测试甲,买入,100,10.00,1000.00,0,0,0,9000.00,ACT_A,证券买入\n"
    + "2026-04-01,600002,测试乙,买入,10,10.00,100.00,0,0,0,900.00,ACT_B,证券买入\n"
    + "2026-04-02,600001,测试甲,卖出,100,11.00,1100.00,0,0,0,10100.00,ACT_A,证券卖出\n"
    + "2026-04-02,600002,测试乙,卖出,10,11.00,110.00,0,0,0,1010.00,ACT_B,证券卖出\n"
).encode()

# 没有资金账号列 → 默认单账户。
_SINGLE_BILL = (
    "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
    "佣金,印花税,其他费,资金余额,摘要\n"
    "2026-04-01,600001,测试,买入,100,10.00,1000.00,0,0,0,9000.00,证券买入\n"
    "2026-04-02,600001,测试,卖出,100,11.00,1100.00,0,0,0,10100.00,证券卖出\n"
).encode()


async def _preview(client: AsyncClient, api_prefix: str, user: Any, data: bytes) -> dict[str, Any]:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers={**user.headers, **V2},
        files={"file": ("bill.csv", data, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _to_ready(
    client: AsyncClient, api_prefix: str, user: Any, preview: dict[str, Any]
) -> dict[str, Any]:
    items = [
        {
            "column_key": column["key"],
            "target_field": column["suggested_field"],
            "options": column["suggested_options"],
        }
        for column in preview["columns"]
        if column["suggested_field"]
    ]
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json={"items": items, "ignored": [], "acknowledge_unmapped": True},
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json={
            "resolutions": [
                {
                    "type_key": item["type_key"],
                    "category": item["category"] or item["suggested_category"] or "other",
                    "discard": False,
                }
                for item in body["types"]
            ]
        },
    )
    assert typed.status_code == 200, typed.text
    return typed.json()


async def _commit_and_drain(
    client: AsyncClient, api_prefix: str, user: Any, preview: dict[str, Any], settings: Any
) -> str:
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={
            "preview_id": preview["preview_id"],
            "mode": "full",
            "mapping_version": preview["mapping_version"],
        },
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["batch_id"]
    await _drain_worker(settings)
    return batch_id


async def _drain_worker(settings: Any) -> None:
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def test_sub_accounts_reconcile_independently_and_merge(
    client: AsyncClient, api_prefix: str, user: Any, settings: Any
) -> None:
    preview = await _preview(client, api_prefix, user, _MULTI_BILL)
    coverage = preview["assumptions"]["coverage"]
    assert coverage["accounts_count"] == 2
    assert coverage["mode"] == "empty"
    # 跨账户切换不再被当成断链：两段链各自连续，无隔离、无挡单。
    assert preview["blocking"] == []
    assert preview["estimated"]["quarantined"] == 0

    batch_id = await _commit_and_drain(
        client, api_prefix, user, await _to_ready(client, api_prefix, user, preview), settings
    )
    detail = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert detail.json()["counts"] == {
        "parsed": 4,
        "inserted": 4,
        "skipped": 0,
        "quarantined": 0,
        "discarded": 0,
    }
    items = (await client.get(f"{api_prefix}/trades?limit=20", headers=user.headers)).json()[
        "items"
    ]
    assert len(items) == 4
    # 归属口径 A：明细保留各子账户的 account_masked 原值（去重键要素/可追溯），
    # 统计与资产层没有账户维度，天然合并到同一账号。
    assert len({item["account_masked"] for item in items}) == 2
    assert all(item["account_masked"] for item in items)
    quarantine = (
        await client.get(f"{api_prefix}/imports/{batch_id}/quarantine", headers=user.headers)
    ).json()["items"]
    assert quarantine == []


async def test_bill_without_account_column_defaults_to_single_account(
    client: AsyncClient, api_prefix: str, user: Any
) -> None:
    preview = await _preview(client, api_prefix, user, _SINGLE_BILL)
    coverage = preview["assumptions"]["coverage"]
    assert coverage["accounts_count"] == 1
    assert coverage["accounts"][0]["account"] is None
    assert preview["estimated"]["parsed"] == 2
    assert preview["blocking"] == []
