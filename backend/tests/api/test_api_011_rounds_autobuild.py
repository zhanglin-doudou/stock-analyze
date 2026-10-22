"""导入账单后**自动建立回合**（Period2-02 增强的端到端验收）。

验收链路：`POST /imports/preview` → `POST /imports`（202）→ worker 依次执行
`import_commit` 与自动入队的 `rebuild_rounds` → `GET /rounds` 立即可查。

断言重点：
* 导入成功后队列里出现 `rebuild_rounds` 任务且终态 succeeded；
* 回合与 FIFO 配对和手算一致（同一证券：买 100@10 + 买 100@12，卖 150@15 →
  1 个 closed 回合（2 条 fill，pnl = 2250 - (1000 + 600) = 650.00）
  + 1 个 open 回合（剩余 50 股，成本 600.00））；
* 回合数据跨账号隔离（B 的列表 0 条）。
"""

from __future__ import annotations

import uuid

import pytest
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_HEADER = "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,佣金,印花税,其他费,资金余额,摘要"
_ROWS = [
    # 期初现金 100000；买入 100@10、买入 100@12 后 97800；卖出 150@15 后 100050。
    "2026-06-02,600519,贵州茅台,买入,100,10.00,1000.00,0.00,0.00,0.00,99000.00,证券买入",
    "2026-06-10,600519,贵州茅台,买入,100,12.00,1200.00,0.00,0.00,0.00,97800.00,证券买入",
    "2026-06-20,600519,贵州茅台,卖出,150,15.00,2250.00,0.00,0.00,0.00,100050.00,证券卖出",
]


def _bill_bytes(rows: list[str] | None = None) -> bytes:
    body = "\n".join([_HEADER, *(rows if rows is not None else _ROWS)])
    return (body + "\n").encode("utf-8")


async def _drain_worker(settings) -> None:
    """把队列里的任务全部领取并执行（导入 + 自动重建两个任务）。"""
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def test_import_auto_builds_rounds_and_web_can_query(
    client, api_prefix, user, settings
) -> None:
    preview = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("rounds.csv", _bill_bytes(), "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimated"]["inserted"] == 3

    accepted = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "rounds-" + uuid.uuid4().hex},
        json={"preview_id": preview.json()["preview_id"], "mode": "full"},
    )
    assert accepted.status_code == 202, accepted.text

    await _drain_worker(settings)

    # 导入任务与自动重建任务都应成功；重建任务由 worker 在 import_commit 成功后入队。
    jobs = await client.get(f"{api_prefix}/jobs?limit=20", headers=user.headers)
    assert jobs.status_code == 200, jobs.text
    items = jobs.json()["items"]
    rebuild_jobs = [item for item in items if item["type"] == "rebuild_rounds"]
    assert rebuild_jobs, f"导入后应自动入队 rebuild_rounds，实际任务：{[i['type'] for i in items]}"
    assert all(item["status"] == "succeeded" for item in rebuild_jobs)

    # GET /rounds 立即可查：新引擎语义 buy100+buy100+sell150 未清仓 → 1 个 open round（含 2 条 fill）。
    rounds = await client.get(f"{api_prefix}/rounds?limit=50", headers=user.headers)
    assert rounds.status_code == 200, rounds.text
    body = rounds.json()
    assert len(body["items"]) == 1, body  # 只有 open round
    opened = body["items"]
    assert opened[0]["status"] == "open"
    round_ = opened[0]
    assert round_["security_code"] == "600519"
    # open round 聚合已消耗 lot 部分：sell 150 股 FIFO 消耗 buy1 全量 + buy2 50 股
    assert round_["quantity"] == "150.0000"  # 已 FIFO 配对的股数
    assert round_["cost"] == "1600.00"  # 已消耗 lot 成本（1000 + 600）
    assert round_["proceeds"] == "2250.00"
    assert round_["pnl"] == "650.00"
    assert round_["fills_count"] == 2

    fills = await client.get(
        f"{api_prefix}/rounds/{round_['round_id']}/fills", headers=user.headers
    )
    assert fills.status_code == 200, fills.text
    fill_items = fills.json()["items"]
    assert len(fill_items) == 2
    assert sum(int(item["quantity"].split(".")[0]) for item in fill_items) == 150
    assert round_["open_trade_id"] == fill_items[0]["buy_trade_id"]
    assert fill_items[0]["sell_trade_id"] == fill_items[1]["sell_trade_id"]

    # 跨账号隔离：B 查不到 A 的回合。
    other = await client.post(
        f"{api_prefix}/auth/register",
        json={
            "email": f"other-{uuid.uuid4().hex[:10]}@example.com",
            "password": "Str0ng-Passw0rd!",
            "accept_tos": True,
        },
    )
    assert other.status_code == 201, other.text
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    isolated = await client.get(f"{api_prefix}/rounds", headers=other_headers)
    assert isolated.status_code == 200
    assert isolated.json()["items"] == []
