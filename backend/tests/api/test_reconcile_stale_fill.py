"""对账陈旧补差清理的回归测试（API-026 自愈语义的边界场景）。

背景缺陷：缺口愈合后（被删原行恢复 / 缺失行随增量导入回链），残留的 side=missing 补差行
自身会成为新的断链点，下一次对账把它当断链行再补差，产出第二笔反向补差（级联），且补差
归属本次导入批次——表现为「先导旧账单、再增量导入新账单时系统在历史日期触发补差，而新建
账号直接导入新账单不触发」。

修复：reconcile 发现补差行与前驱不平衡时，若移除该段连续 missing 行后前后行直接自洽
（缺口已愈合），判定其为陈旧补差并软删，链条绕过它继续，不再级联补差。

 fixture：华泰账单_增量基线_mock.xlsx（2021-10-29~2021-11-11，9 行）与
华泰账单_增量更新_mock.xlsx（同区间 + 2021-11-16~2021-12-30 新增 31 行）；
后者完全包含前者的 dedupe_key，可稳定复现增量导入路径。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_BASE = Path(__file__).parent / "华泰账单_增量基线_mock.xlsx"
_INCR = Path(__file__).parent / "华泰账单_增量更新_mock.xlsx"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _import(client, api_prefix, user, settings, path: Path, name: str) -> dict:
    """preview → commit(mode=full) → 手动 drain worker → 批次详情。"""
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": (name, path.read_bytes(), _XLSX)},
    )
    assert resp.status_code == 200, resp.text
    preview_id = resp.json()["preview_id"]
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["batch_id"]
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()
    resp = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fills(client, api_prefix, user) -> list[dict]:
    resp = await client.get(
        f"{api_prefix}/trades", headers=user.headers, params={"side": "missing", "limit": 100}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_incremental_import_does_not_fill_on_healed_gap(
    client, api_prefix, account_factory, settings
) -> None:
    """增量导入收尾对账不得对「已愈合缺口上的残留补差行」级联补差，且应清理陈旧补差行。"""
    user = await account_factory()
    batch1 = await _import(client, api_prefix, user, settings, _BASE, "base.xlsx")
    assert batch1["filled_count"] == 0

    # 用户删除 2021-11-01 的银行转存 3200（制造真实缺口）→ 对账自愈生成补差行 F1。
    resp = await client.get(
        f"{api_prefix}/trades",
        headers=user.headers,
        params={"trade_date_from": "2021-11-01", "trade_date_to": "2021-11-01", "limit": 50},
    )
    assert resp.status_code == 200, resp.text
    deleted = next(t for t in resp.json()["items"] if t["amount"] == "3200.00")
    resp = await client.delete(
        f"{api_prefix}/trades/{deleted['trade_id']}",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
    )
    assert resp.status_code in (200, 204), resp.text
    resp = await client.get(
        f"{api_prefix}/reconciliation", headers=user.headers, params={"period": "2021-11"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["filled_count"] == 1
    assert len(await _fills(client, api_prefix, user)) == 1

    # 恢复被删原行：缺口愈合，F1 成为陈旧补差残留。
    resp = await client.post(
        f"{api_prefix}/trades/{deleted['trade_id']}/restore",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
    )
    assert resp.status_code == 200, resp.text

    # 增量导入新账单：收尾对账应软删 F1，而不是对 F1 再补差。
    batch2 = await _import(client, api_prefix, user, settings, _INCR, "incr.xlsx")
    assert batch2["filled_count"] == 0, batch2.get("fills")
    assert await _fills(client, api_prefix, user) == []

    # 月度对账复查：链完全自洽，无断链、无补差残留（状态 ok 而非 degraded）。
    resp = await client.get(
        f"{api_prefix}/reconciliation", headers=user.headers, params={"period": "2021-11"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body
    assert body["filled_count"] == 0


async def test_fresh_account_direct_import_has_no_fill(
    client, api_prefix, account_factory, settings
) -> None:
    """对照组：新账号直接导入增量文件本就不触发补差（修复不改变该基线路径）。"""
    user = await account_factory()
    batch = await _import(client, api_prefix, user, settings, _INCR, "incr.xlsx")
    assert batch["filled_count"] == 0
    assert await _fills(client, api_prefix, user) == []
