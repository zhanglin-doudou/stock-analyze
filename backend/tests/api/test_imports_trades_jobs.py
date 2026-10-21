"""一期端点验收：导入（API-010~016）、流水（API-017~021、080）、任务（API-061~064）、
对账（API-026）。

数据准备走 owner 直插（绕过 RLS，用于铺场景）；运行时一律经 API 走 stock_app 角色。
每个 API-ID 至少三条：happy / 越权 / 关键错误码。
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_contracts.clock import SystemClock
from stock_contracts.errors import ErrorCode
from stock_ledger.models import ImportBatch, QuarantineRow, Trade
from stock_ledger.parsers.huatai import parse_bill
from stock_platform.models import Job, JobEvent
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_BATCH_HEADER = "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,佣金,印花税,其他费,资金余额,摘要"
# G3：commit 会按摘要分类——测试账单必须带可命中系统默认规则的摘要（证券买入）。
_BATCH_ROWS = [
    "2026-06-30,600519,贵州茅台,买入,100,1500.00,150000.00,5.00,0.00,0.10,100000.00,证券买入",
    "2026-06-30,000001,平安银行,买入,200,10.00,2000.00,0.50,0.00,0.01,95000.00,证券买入",
]


def _bill_bytes(rows: list[str] | None = None) -> bytes:
    body = "\n".join([_BATCH_HEADER, *(rows if rows is not None else _BATCH_ROWS)])
    return (body + "\n").encode("utf-8")


# 真实形态的对账单（华泰「普通账户电子对账单」）：没有「买卖标志」列，方向靠摘要识别。
_MOCK_BILL = Path(__file__).parent / "华泰账单_mock.xlsx"
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── 夹具铺场景（owner 直插）────────────────────────────────


async def _seed_trade(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    trade_date: date = date(2026, 6, 15),
    code: str = "600519",
    side: str = "buy",
    quantity_units: int = 1_000_000,
    price_units: int = 1_000_000,
    amount_units: int = 1_000_000,
    cash_balance: int | None = None,
    version: int = 1,
    batch_id: str | None = None,
    line_no: int | None = None,
    dedupe_key: str | None = None,
) -> str:
    trade_id = f"trd_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Trade(
                id=trade_id,
                user_id=user_id,
                batch_id=batch_id,
                trade_date=trade_date,
                security_code=code,
                security_name=code,
                side=side,
                quantity_units=quantity_units,
                price_units=price_units,
                amount_units=amount_units,
                commission_units=0,
                stamp_tax_units=0,
                other_fee_units=0,
                cash_balance_units=cash_balance,
                cash_balance_available=cash_balance is not None,
                occurrence_idx=0,
                source="import",
                version=version,
                line_no=line_no,
                dedupe_key=dedupe_key or f"{code}:{uuid.uuid4().hex[:10]}",
            )
        )
        await session.commit()
    return trade_id


async def _seed_batch(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    status: str = "succeeded",
    counts: tuple[int, int, int, int] = (2, 2, 0, 0),
    columns: dict[str, str] | None = None,
) -> str:
    batch_id = f"bat_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            ImportBatch(
                id=batch_id,
                user_id=user_id,
                preview_id=None,
                status=status,
                mode="full",
                broker="huatai",
                file_name="bill.csv",
                file_sha256="0" * 64,
                file_bytes=128,
                parsed_count=counts[0],
                inserted_count=counts[1],
                skipped_count=counts[2],
                quarantined_count=counts[3],
                columns=columns or {"成交日期": "trade_date"},
                warnings=[],
                assumptions={},
                ranges={},
            )
        )
        await session.commit()
    return batch_id


async def _seed_quarantine(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    batch_id: str,
    line_no: int,
    raw: dict[str, str],
    reason_code: str = "INVALID_ROW",
) -> None:
    async with AsyncSession(engine) as session:
        session.add(
            QuarantineRow(
                user_id=user_id,
                batch_id=batch_id,
                line_no=line_no,
                raw=raw,
                reason_code=reason_code,
                resolved=False,
            )
        )
        await session.commit()


async def _seed_job(
    engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    status: str = "pending",
    kind: str = "import_commit",
    stage: str = "queued",
    progress: int = 0,
    events: list[tuple[int, str, dict]] | None = None,
) -> str:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    async with AsyncSession(engine) as session:
        session.add(
            Job(
                id=job_id,
                user_id=user_id,
                kind=kind,
                scope_key=f"{user_id}:{uuid.uuid4().hex[:6]}",
                dedupe_key=f"{kind}:{uuid.uuid4().hex[:8]}",
                canonical_params="{}",
                params={},
                status=status,
                priority=100,
                attempt=1,
                max_attempts=3,
                progress=progress,
                stage=stage,
            )
        )
        if events:
            await session.flush()
            for seq, etype, payload in events:
                session.add(JobEvent(job_id=job_id, seq=seq, type=etype, payload=payload))
        await session.commit()
    return job_id


async def _count_trades(engine: AsyncEngine) -> int:
    from sqlalchemy import text

    async with AsyncSession(engine) as session:
        return int(await session.scalar(text("select count(*) from ledger.trade")) or 0)


# ══ API-010 预览 ═══════════════════════════════════════════


async def test_api_010_preview_writes_no_trades(client, api_prefix, user, owner_engine) -> None:
    before = await _count_trades(owner_engine)
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.csv", _bill_bytes(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estimated"]["parsed"] == 2
    assert body["estimated"]["quarantined"] == 0
    assert body["can_commit"] is True
    assert await _count_trades(owner_engine) == before == 0


async def test_api_010_preview_same_file_same_sha256(client, api_prefix, user) -> None:
    files = {"file": ("bill.csv", _bill_bytes(), "text/csv")}
    first = await client.post(f"{api_prefix}/imports/preview", headers=user.headers, files=files)
    second = await client.post(f"{api_prefix}/imports/preview", headers=user.headers, files=files)
    assert first.json()["file"]["sha256"] == second.json()["file"]["sha256"]


async def test_api_010_preview_estimated_inserted_zero_when_all_duplicate(
    client, api_prefix, user, owner_engine
) -> None:
    """同一份文件的所有行在库中已存在 → estimated.inserted == 0。"""
    data = _bill_bytes()
    bill = parse_bill(data, "bill.csv", mapping=None, date_format=None, clock=SystemClock())
    for row in bill.rows:
        await _seed_trade(
            owner_engine, user_id=user.user_id, code=row.security_code, dedupe_key=row.dedupe_key
        )
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.csv", data, "text/csv")},
    )
    body = resp.json()
    assert body["estimated"]["inserted"] == 0
    assert body["estimated"]["skipped"] == body["estimated"]["parsed"]


async def test_api_010_preview_rejects_admin(client, api_prefix, admin) -> None:
    """admin 不可触碰业务数据（§14.2）。"""
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=admin.headers,
        files={"file": ("bill.csv", _bill_bytes(), "text/csv")},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


async def test_api_010_preview_unsupported_file(client, api_prefix, user) -> None:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.txt", b"whatever", "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["code"] == ErrorCode.IMPORT_UNSUPPORTED_FILE.value


# ══ API-011 提交 ═══════════════════════════════════════════


async def _preview(client, api_prefix, user) -> str:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.csv", _bill_bytes(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["preview_id"]


async def _preview_with_rows(client, api_prefix, user, rows: list[str]) -> str:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": ("bill.csv", _bill_bytes(rows), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["preview_id"]


async def _commit_import(
    client,
    api_prefix,
    user,
    preview_id: str,
    *,
    mode: str = "full",
    range_body: dict[str, str] | None = None,
) -> dict:
    payload: dict[str, object] = {"preview_id": preview_id, "mode": mode}
    if range_body is not None:
        payload["range"] = range_body
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json=payload,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _drain_worker(settings) -> None:
    """把队列里的任务全部领取并执行（最小复刻真实 worker 的消费循环）。

    用真实 `WorkerRunner`（而非直连 `commit_batch`），从而覆盖 worker 侧会话必须
    带租户上下文这一点：ledger 业务表都启用了 RLS，会话没 `SET LOCAL app.user_id`
    时仓储查询会一行都看不到。
    """
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def test_api_011_commit_accepted(client, api_prefix, user) -> None:
    preview_id = await _preview(client, api_prefix, user)
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["batch_id"]
    assert body["status"] == "pending"
    assert resp.headers["Retry-After"] == "1"


async def test_api_011_commit_requires_idempotency_key(client, api_prefix, user) -> None:
    preview_id = await _preview(client, api_prefix, user)
    resp = await client.post(
        f"{api_prefix}/imports",
        headers=user.headers,
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_api_011_commit_cross_user_preview_404(
    client, api_prefix, user, account_factory
) -> None:
    other = await account_factory()
    preview_id = await _preview(client, api_prefix, other)
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.IMPORT_PREVIEW_NOT_FOUND.value


async def test_api_010_preview_real_bill_resolves_side_from_summary(
    client, api_prefix, user
) -> None:
    """真实对账单无「买卖标志」列：方向从摘要识别，全部解析成功、零隔离。

    含非交易类业务（银行转存/转取、股息入帐、利息归本等），代码空行用 CASH 占位导入。
    """
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": (_MOCK_BILL.name, _MOCK_BILL.read_bytes(), _XLSX_CONTENT_TYPE)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "买卖标志" not in {col["name"] for col in body["columns"]}
    summary_col = next(col for col in body["columns"] if col["name"] == "摘要")
    assert summary_col["suggested_field"] == "summary"
    assert body["estimated"] == {"parsed": 177, "inserted": 177, "skipped": 0, "quarantined": 0}
    assert body["sample"]
    # 非交易行按资金流向分配 side（sell=流入，buy=流出），交易行正常买卖。
    assert all(sample["side"] in {"buy", "sell", "unknown"} for sample in body["sample"])
    # 预览的 columns 里也不能回显明文账号。
    account_detail = next((c for c in body["columns"] if c["name"] == "股东账号"), None)
    assert account_detail is not None
    assert account_detail["suggested_field"] == "account"
    # API 边界把原始单个 sample 值包成 list（PreviewColumn.sample: list[str]）。
    # mock 数据中股东账号为 A00000000 / A10000000，只落 MD5 不落明文。
    assert account_detail["sample"] == ["4cda045a46c54164471b5311607f9ab8"]
    assert account_detail["sample"] != ["A00000000"]


async def test_api_011_commit_real_bill_worker_end_to_end(
    client, api_prefix, user, settings
) -> None:
    """真实对账单提交后由 worker 执行成功（回归：worker 会话缺 RLS 租户上下文）。

    缺少租户上下文时 `commit_batch` 里的预览/批次查询会被 RLS 过滤成空，
    表现为 job 失败 + `IMPORT_PREVIEW_NOT_FOUND`（假 404），本用例专门守这一条。
    """
    preview = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": (_MOCK_BILL.name, _MOCK_BILL.read_bytes(), _XLSX_CONTENT_TYPE)},
    )
    assert preview.status_code == 200, preview.text

    accepted = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview.json()["preview_id"], "mode": "full"},
    )
    assert accepted.status_code == 202, accepted.text
    job_id, batch_id = accepted.json()["job_id"], accepted.json()["batch_id"]

    await _drain_worker(settings)

    job = await client.get(f"{api_prefix}/jobs/{job_id}", headers=user.headers)
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "succeeded", job.text
    assert job.json()["error"] is None

    batch = (await client.get(f"{api_prefix}/imports", headers=user.headers)).json()["items"][0]
    assert batch["batch_id"] == batch_id
    assert batch["status"] == "succeeded"
    assert batch["counts"] == {"parsed": 177, "inserted": 177, "skipped": 0, "quarantined": 0}

    trades = await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
    items = trades.json()["items"]
    assert len(items) == 177
    # 非交易行按资金流向分配 side（sell=流入，buy=流出），交易行正常买卖，零金额转存管为 unknown。
    assert {item["side"] for item in items} <= {"buy", "sell", "unknown"}
    # 股东账号只落 MD5 摘要（32 位 hex），不落明文；非交易行账号为空时 account_masked 为 None。
    for item in items:
        if item["account_masked"] is not None:
            assert len(item["account_masked"]) == 32
            assert item["account_masked"] != "A00000000"


async def test_idempotency_key_same_body_replays(client, api_prefix, user) -> None:
    preview_id = await _preview(client, api_prefix, user)
    key = "idem-" + uuid.uuid4().hex + "!"
    payload = {"preview_id": preview_id, "mode": "full"}
    first = await client.post(
        f"{api_prefix}/imports", headers={**user.headers, "Idempotency-Key": key}, json=payload
    )
    assert first.status_code == 202
    replay = await client.post(
        f"{api_prefix}/imports", headers={**user.headers, "Idempotency-Key": key}, json=payload
    )
    assert replay.status_code == 202
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["job_id"] == first.json()["job_id"]


async def test_idempotency_key_same_key_different_body_conflicts(client, api_prefix, user) -> None:
    preview_id = await _preview(client, api_prefix, user)
    key = "idem-" + uuid.uuid4().hex + "!"
    first = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": key},
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert first.status_code == 202
    conflict = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": key},
        json={"preview_id": preview_id, "mode": "full", "note": "different-body"},
    )
    assert conflict.status_code == 422
    assert conflict.json()["code"] == ErrorCode.IDEMPOTENCY_KEY_REUSED.value


async def test_api_011_replace_range_reinserts_without_data_loss(
    client, api_prefix, user, settings
) -> None:
    """P0 回归：replace_range 清掉区间旧行后必须插回本批行，不能“删而不插”。"""
    first = await _commit_import(client, api_prefix, user, await _preview(client, api_prefix, user))
    await _drain_worker(settings)
    before = (await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)).json()[
        "items"
    ]
    assert len(before) == 2

    second = await _commit_import(
        client,
        api_prefix,
        user,
        await _preview(client, api_prefix, user),
        mode="replace_range",
        range_body={"from": "2026-06-01", "to": "2026-06-30"},
    )
    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{second['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text
    batch = await client.get(f"{api_prefix}/imports/{second['batch_id']}", headers=user.headers)
    assert batch.json()["counts"] == {"parsed": 2, "inserted": 2, "skipped": 0, "quarantined": 0}

    after = (await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)).json()[
        "items"
    ]
    assert len(after) == 2
    assert {item["trade_id"] for item in after}.isdisjoint({item["trade_id"] for item in before})
    assert {item["batch_id"] for item in after} == {second["batch_id"]}
    assert first["batch_id"] != second["batch_id"]


async def test_api_011_replace_range_invalid_range_422(client, api_prefix, user) -> None:
    """倒置区间必须在同步阶段 422，而不是丢给 worker 变成失败任务。"""
    preview_id = await _preview(client, api_prefix, user)
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={
            "preview_id": preview_id,
            "mode": "replace_range",
            "range": {"from": "2026-06-30", "to": "2026-06-01"},
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value
    assert resp.json()["errors"][0]["field"] == "range"


async def test_api_011_replace_range_only_touches_requested_range(
    client, api_prefix, user, settings
) -> None:
    """replace_range 必须用请求体 range，而不是账单 min/max（否则误删区间外流水）。"""
    rows = [
        "2026-06-01,600519,贵州茅台,买入,100,1500.00,150000.00,5.00,0.00,0.10,100000.00,证券买入",
        "2026-06-30,000001,平安银行,买入,200,10.00,2000.00,0.50,0.00,0.01,95000.00,证券买入",
    ]
    first = await _commit_import(
        client, api_prefix, user, await _preview_with_rows(client, api_prefix, user, rows)
    )
    await _drain_worker(settings)
    before = {
        item["trade_date"]: item
        for item in (
            await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
        ).json()["items"]
    }
    assert set(before) == {"2026-06-01", "2026-06-30"}

    replacement = [
        "2026-06-01,600519,贵州茅台,买入,100,1600.00,160000.00,5.00,0.00,0.10,100000.00,证券买入"
    ]
    second = await _commit_import(
        client,
        api_prefix,
        user,
        await _preview_with_rows(client, api_prefix, user, replacement),
        mode="replace_range",
        range_body={"from": "2026-06-01", "to": "2026-06-01"},
    )
    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{second['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text

    after = {
        item["trade_date"]: item
        for item in (
            await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
        ).json()["items"]
    }
    assert set(after) == {"2026-06-01", "2026-06-30"}
    # amount 为带符号净现金流：买入 160000.00 毛额 + 费用 5.10 → -160005.10。
    assert after["2026-06-01"]["amount"] == "-160005.10"
    assert after["2026-06-01"]["batch_id"] == second["batch_id"]
    # 区间外（06-30）行不受影响，仍属第一批次。
    assert after["2026-06-30"]["trade_id"] == before["2026-06-30"]["trade_id"]
    assert after["2026-06-30"]["batch_id"] == first["batch_id"]


# ══ API-012 批次列表 ═══════════════════════════════════════


async def test_api_012_list_only_own_batches(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    await _seed_batch(owner_engine, user_id=user.user_id)
    await _seed_batch(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/imports", headers=user.headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["batch_id"].startswith("bat_")


async def test_api_012_list_is_cursor_paged(client, api_prefix, user, owner_engine) -> None:
    for _ in range(3):
        await _seed_batch(owner_engine, user_id=user.user_id)
    first = await client.get(f"{api_prefix}/imports?limit=2", headers=user.headers)
    body = first.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"]
    second = await client.get(
        f"{api_prefix}/imports?limit=2&cursor={body['next_cursor']}", headers=user.headers
    )
    assert len(second.json()["items"]) == 1


async def test_api_012_list_requires_credential(client, api_prefix) -> None:
    assert (await client.get(f"{api_prefix}/imports")).status_code == 401


# ══ API-013 批次详情 ═══════════════════════════════════════


async def test_api_013_detail_counts(client, api_prefix, user, owner_engine) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id, counts=(5, 4, 1, 2))
    resp = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"parsed": 5, "inserted": 4, "skipped": 1, "quarantined": 2}
    assert body["can_revoke"] is True


async def test_api_013_detail_unknown_404(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/imports/bat_missing", headers=user.headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


async def test_api_013_detail_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    batch_id = await _seed_batch(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert resp.status_code == 404


# ══ API-014 隔离区 ═════════════════════════════════════════


async def test_api_014_quarantine_line_no_and_raw(client, api_prefix, user, owner_engine) -> None:
    columns = {"成交日期": "trade_date", "证券代码": "security_code", "成交金额": "amount"}
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id, columns=columns)
    raw = {"成交日期": "2026-06-30", "证券代码": "600519", "成交金额": "abc"}
    await _seed_quarantine(
        owner_engine, user_id=user.user_id, batch_id=batch_id, line_no=42, raw=raw
    )
    resp = await client.get(f"{api_prefix}/imports/{batch_id}/quarantine", headers=user.headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["line_no"] == 42
    assert items[0]["raw"]["security_code"] == "600519"
    assert items[0]["raw"]["amount"] == "abc"
    assert items[0]["reason_code"] == "INVALID_ROW"


async def test_api_014_quarantine_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    batch_id = await _seed_batch(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/imports/{batch_id}/quarantine", headers=user.headers)
    assert resp.status_code == 404


async def test_api_014_quarantine_unknown_batch_404(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/imports/bat_missing/quarantine", headers=user.headers)
    assert resp.status_code == 404


# ══ API-015 撤销 ═══════════════════════════════════════════


async def test_api_015_revoke_reverts_rows(client, api_prefix, user, owner_engine) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id, counts=(2, 2, 0, 0))
    await _seed_trade(owner_engine, user_id=user.user_id, batch_id=batch_id, code="600519")
    await _seed_trade(owner_engine, user_id=user.user_id, batch_id=batch_id, code="000001")
    assert await _count_trades(owner_engine) == 2
    resp = await client.post(f"{api_prefix}/imports/{batch_id}/revoke", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "revoked"
    assert body["reverted"]["inserted"] == 2
    # 软删（行仍在，但已带 deleted_at），故物理计数不变
    detail = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert detail.json()["status"] == "revoked"
    assert detail.json()["can_revoke"] is False


async def test_api_015_revoke_twice_conflicts(client, api_prefix, user, owner_engine) -> None:
    batch_id = await _seed_batch(owner_engine, user_id=user.user_id)
    assert (
        await client.post(f"{api_prefix}/imports/{batch_id}/revoke", headers=user.headers)
    ).status_code == 200
    again = await client.post(f"{api_prefix}/imports/{batch_id}/revoke", headers=user.headers)
    assert again.status_code == 409
    assert again.json()["code"] == ErrorCode.IMPORT_ALREADY_REVOKED.value


async def test_api_015_revoke_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    batch_id = await _seed_batch(owner_engine, user_id=other.user_id)
    resp = await client.post(f"{api_prefix}/imports/{batch_id}/revoke", headers=user.headers)
    assert resp.status_code == 404


async def test_api_015_reimport_after_revoke_restores_rows(
    client, api_prefix, user, settings
) -> None:
    """P0 回归：撤销（软删）不能永久占住去重键，否则重导会 inserted=0 静默丢数据。"""
    first = await _commit_import(client, api_prefix, user, await _preview(client, api_prefix, user))
    await _drain_worker(settings)
    before = await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
    assert len(before.json()["items"]) == 2

    revoked = await client.post(
        f"{api_prefix}/imports/{first['batch_id']}/revoke", headers=user.headers
    )
    assert revoked.status_code == 200, revoked.text
    assert (await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)).json()[
        "items"
    ] == []

    second = await _commit_import(
        client, api_prefix, user, await _preview(client, api_prefix, user)
    )
    await _drain_worker(settings)
    job = await client.get(f"{api_prefix}/jobs/{second['job_id']}", headers=user.headers)
    assert job.json()["status"] == "succeeded", job.text
    batch = await client.get(f"{api_prefix}/imports/{second['batch_id']}", headers=user.headers)
    assert batch.json()["counts"] == {"parsed": 2, "inserted": 2, "skipped": 0, "quarantined": 0}
    restored = await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
    assert len(restored.json()["items"]) == 2


# ══ API-017 流水列表 ═══════════════════════════════════════


async def test_api_017_list_cursor_paging(client, api_prefix, user, owner_engine) -> None:
    for day in (10, 11, 12):
        await _seed_trade(owner_engine, user_id=user.user_id, trade_date=date(2026, 6, day))
    first = await client.get(f"{api_prefix}/trades?limit=2", headers=user.headers)
    body = first.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"]
    second = await client.get(
        f"{api_prefix}/trades?limit=2&cursor={body['next_cursor']}", headers=user.headers
    )
    assert len(second.json()["items"]) == 1
    assert second.json()["has_more"] is False


async def test_api_017_money_is_fixed_point_string(client, api_prefix, user, owner_engine) -> None:
    await _seed_trade(owner_engine, user_id=user.user_id)
    resp = await client.get(f"{api_prefix}/trades", headers=user.headers)
    item = resp.json()["items"][0]
    assert item["quantity"] == "100.0000"
    assert item["price"] == "1.000000"
    assert item["amount"] == "10000.00"


async def test_api_017_etag_and_if_none_match(client, api_prefix, user, owner_engine) -> None:
    await _seed_trade(owner_engine, user_id=user.user_id)
    first = await client.get(f"{api_prefix}/trades", headers=user.headers)
    etag = first.headers["ETag"]
    second = await client.get(
        f"{api_prefix}/trades", headers={**user.headers, "If-None-Match": etag}
    )
    assert second.status_code == 304
    assert second.content == b""


async def test_api_017_invalid_sort_and_fields(client, api_prefix, user) -> None:
    bad_sort = await client.get(f"{api_prefix}/trades?sort=bogus", headers=user.headers)
    assert bad_sort.status_code == 400
    assert bad_sort.json()["code"] == ErrorCode.INVALID_SORT_FIELD.value
    bad_field = await client.get(f"{api_prefix}/trades?fields=bogus", headers=user.headers)
    assert bad_field.status_code == 400
    assert bad_field.json()["code"] == ErrorCode.INVALID_FIELD_SELECTION.value


async def test_api_017_admin_cannot_read(client, api_prefix, admin) -> None:
    resp = await client.get(f"{api_prefix}/trades", headers=admin.headers)
    assert resp.status_code == 403


# ══ API-080 流水详情 ═══════════════════════════════════════


async def test_api_080_detail_happy_path(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    resp = await client.get(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert resp.status_code == 200
    assert resp.json()["trade_id"] == trade_id
    assert resp.headers["ETag"] == '"1"'


async def test_api_080_detail_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    trade_id = await _seed_trade(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert resp.status_code == 404


async def test_api_080_detail_unknown_404(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/trades/trd_missing", headers=user.headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND.value


# ══ API-018 修正（乐观锁）══════════════════════════════════


async def test_api_018_patch_bumps_version(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    resp = await client.patch(
        f"{api_prefix}/trades/{trade_id}",
        headers={**user.headers, "If-Match": "1"},
        json={"note": "人工修正"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["trade"]
    assert body["version"] == 2
    assert body["note"] == "人工修正"


async def test_api_018_patch_stale_version_conflicts(
    client, api_prefix, user, owner_engine
) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    await client.patch(
        f"{api_prefix}/trades/{trade_id}",
        headers={**user.headers, "If-Match": "1"},
        json={"note": "first"},
    )
    stale = await client.patch(
        f"{api_prefix}/trades/{trade_id}",
        headers={**user.headers, "If-Match": "1"},
        json={"note": "second"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == ErrorCode.VERSION_CONFLICT.value


async def test_api_018_patch_missing_version_and_cross_user(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    no_version = await client.patch(
        f"{api_prefix}/trades/{trade_id}", headers=user.headers, json={"note": "x"}
    )
    assert no_version.status_code == 422

    other = await account_factory()
    other_trade = await _seed_trade(owner_engine, user_id=other.user_id)
    cross = await client.patch(
        f"{api_prefix}/trades/{other_trade}",
        headers={**user.headers, "If-Match": "1"},
        json={"note": "x"},
    )
    assert cross.status_code == 404


# ══ API-019 软删 ═══════════════════════════════════════════


async def test_api_019_delete_then_hidden(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    resp = await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert resp.status_code == 204
    hidden = await client.get(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert hidden.status_code == 404
    visible = await client.get(
        f"{api_prefix}/trades/{trade_id}?include_deleted=true", headers=user.headers
    )
    assert visible.status_code == 200
    assert visible.json()["deleted_at"] is not None


async def test_api_019_delete_is_idempotent(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    assert (
        await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    ).status_code == 204
    assert (
        await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    ).status_code == 204


async def test_api_019_delete_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    trade_id = await _seed_trade(owner_engine, user_id=other.user_id)
    resp = await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert resp.status_code == 404


# ══ API-020 恢复 ═══════════════════════════════════════════


async def test_api_020_restore_undeletes(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    resp = await client.post(f"{api_prefix}/trades/{trade_id}/restore", headers=user.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["trade"]["deleted_at"] is None
    assert resp.json()["status"] == "restored"


async def test_api_020_restore_not_deleted_422(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    resp = await client.post(f"{api_prefix}/trades/{trade_id}/restore", headers=user.headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_api_020_restore_after_reimport_conflicts_422(
    client, api_prefix, user, settings
) -> None:
    """撤销后重新导入会生成同去重键的有效行；此时恢复旧行必须 422，而不是 500。"""
    first = await _commit_import(client, api_prefix, user, await _preview(client, api_prefix, user))
    await _drain_worker(settings)
    old_items = (await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)).json()[
        "items"
    ]
    assert len(old_items) == 2
    old_id = old_items[0]["trade_id"]

    assert (
        await client.post(f"{api_prefix}/imports/{first['batch_id']}/revoke", headers=user.headers)
    ).status_code == 200
    second = await _commit_import(
        client, api_prefix, user, await _preview(client, api_prefix, user)
    )
    await _drain_worker(settings)
    assert (
        len(
            (await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)).json()[
                "items"
            ]
        )
        == 2
    )
    assert second["batch_id"] != first["batch_id"]

    resp = await client.post(f"{api_prefix}/trades/{old_id}/restore", headers=user.headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value
    assert "同去重键" in resp.json()["detail"]


async def test_api_020_restore_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    trade_id = await _seed_trade(owner_engine, user_id=other.user_id)
    resp = await client.post(f"{api_prefix}/trades/{trade_id}/restore", headers=user.headers)
    assert resp.status_code == 404


# ══ API-021 留痕 ═══════════════════════════════════════════


async def test_api_021_history_records_ops(client, api_prefix, user, owner_engine) -> None:
    trade_id = await _seed_trade(owner_engine, user_id=user.user_id)
    await client.patch(
        f"{api_prefix}/trades/{trade_id}",
        headers={**user.headers, "If-Match": "1"},
        json={"note": "h"},
    )
    await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    await client.post(f"{api_prefix}/trades/{trade_id}/restore", headers=user.headers)
    resp = await client.get(f"{api_prefix}/trades/{trade_id}/history", headers=user.headers)
    assert resp.status_code == 200
    ops = [item["op"] for item in resp.json()["items"]]
    assert {"update", "delete", "restore"}.issubset(set(ops))


async def test_api_021_history_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    trade_id = await _seed_trade(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/trades/{trade_id}/history", headers=user.headers)
    assert resp.status_code == 404


async def test_api_021_history_unknown_404(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/trades/trd_missing/history", headers=user.headers)
    assert resp.status_code == 404


# ══ API-026 对账 ═══════════════════════════════════════════


async def test_api_026_break_located_to_line(client, api_prefix, user, owner_engine) -> None:
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 1),
        amount_units=-100_000,
        cash_balance=500_000,
        line_no=2,
        code="600519",
    )
    second = await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        amount_units=-20_000,
        cash_balance=470_000,  # 期望 500000-20000=480000，实际 470000 → 断链
        line_no=3,
        code="000001",
    )
    resp = await client.get(f"{api_prefix}/reconciliation?period=2026-06", headers=user.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["break_count"] == 1
    assert body["breaks"][0]["line_no"] == 3
    assert body["breaks"][0]["trade_id"] == second
    assert body["breaks"][0]["actual"] == "4700.00"


async def test_api_026_ok_and_degraded(client, api_prefix, user, owner_engine) -> None:
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 1),
        amount_units=-100_000,
        cash_balance=500_000,
    )
    await _seed_trade(
        owner_engine,
        user_id=user.user_id,
        trade_date=date(2026, 6, 2),
        amount_units=-20_000,  # 带符号净现金流：买入为负 → 余额减少 200.00
        cash_balance=480_000,
    )
    ok = await client.get(f"{api_prefix}/reconciliation?period=2026-06", headers=user.headers)
    assert ok.json()["status"] == "ok"

    # 无资金余额列 → degraded（200 语义）
    await _seed_trade(
        owner_engine, user_id=user.user_id, trade_date=date(2026, 7, 1), code="300750"
    )
    degraded = await client.get(f"{api_prefix}/reconciliation?period=2026-07", headers=user.headers)
    assert degraded.json()["status"] == "degraded"
    assert degraded.json()["break_count"] == 0


async def test_api_026_real_bill_reconciles_with_zero_breaks(
    client, api_prefix, user, settings
) -> None:
    """P0-1 回归：真实账单（「发生金额」带符号净额）导入后余额链必须 0 断链。

    177 行明细产生 176 次相邻转移；「转存管转出/转入」两行方向 unknown（金额 0），
    其中只有 1 行位于链中段会被跳过核对 → checked_rows = 175，break_count = 0。
    旧实现按 side 反号并再扣费用（真实账单已含费用），实测 156/175 假断链。
    """
    preview = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": (_MOCK_BILL.name, _MOCK_BILL.read_bytes(), _XLSX_CONTENT_TYPE)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimated"]["parsed"] == 177

    accepted = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview.json()["preview_id"], "mode": "full"},
    )
    assert accepted.status_code == 202, accepted.text
    await _drain_worker(settings)

    resp = await client.get(
        f"{api_prefix}/reconciliation?from=2021-10-29&to=2022-06-07", headers=user.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok", body["breaks"][:3]
    assert body["checked_rows"] == 175
    assert body["break_count"] == 0


async def test_api_026_invalid_period_422(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/reconciliation?period=2026-13", headers=user.headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.INVALID_PERIOD.value


# ══ API-061~064 任务 ═══════════════════════════════════════


async def test_api_061_list_with_backlog(client, api_prefix, user, owner_engine) -> None:
    await _seed_job(owner_engine, user_id=user.user_id, status="pending")
    await _seed_job(owner_engine, user_id=user.user_id, status="succeeded", stage="done")
    resp = await client.get(f"{api_prefix}/jobs", headers=user.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["backlog"]["pending"] == 1
    assert body["backlog"]["succeeded"] == 1


async def test_api_061_list_status_filter_and_isolation(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    await _seed_job(owner_engine, user_id=user.user_id, status="pending")
    await _seed_job(owner_engine, user_id=user.user_id, status="succeeded", stage="done")
    filtered = await client.get(f"{api_prefix}/jobs?status=pending", headers=user.headers)
    assert len(filtered.json()["items"]) == 1

    other = await account_factory()
    await _seed_job(owner_engine, user_id=other.user_id, status="pending")
    isolated = await client.get(f"{api_prefix}/jobs", headers=user.headers)
    assert len(isolated.json()["items"]) == 2


async def test_api_061_list_invalid_status_422(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/jobs?status=nope", headers=user.headers)
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.VALIDATION_ERROR.value


async def test_api_062_detail_happy_and_cross_user(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    job_id = await _seed_job(owner_engine, user_id=user.user_id, progress=35, stage="writing")
    resp = await client.get(f"{api_prefix}/jobs/{job_id}", headers=user.headers)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id
    assert resp.json()["progress"] == 35

    other = await account_factory()
    other_job = await _seed_job(owner_engine, user_id=other.user_id)
    cross = await client.get(f"{api_prefix}/jobs/{other_job}", headers=user.headers)
    assert cross.status_code == 404
    assert cross.json()["code"] == ErrorCode.NOT_FOUND.value


async def test_api_063_sse_event_stream(client, api_prefix, user, owner_engine) -> None:
    job_id = await _seed_job(
        owner_engine,
        user_id=user.user_id,
        status="succeeded",
        stage="done",
        progress=100,
        events=[(1, "queued", {"kind": "import_commit"}), (2, "succeeded", {"progress": 100})],
    )
    lines: list[str] = []
    async with client.stream(
        "GET", f"{api_prefix}/jobs/{job_id}/events", headers=user.headers
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            lines.append(line)
            if len(lines) >= 6:
                break
    assert any(line.startswith("event: ") for line in lines)
    assert any(line.startswith("id: ") for line in lines)


async def test_api_063_sse_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    job_id = await _seed_job(owner_engine, user_id=other.user_id)
    resp = await client.get(f"{api_prefix}/jobs/{job_id}/events", headers=user.headers)
    assert resp.status_code == 404


async def test_api_063_sse_unknown_404(client, api_prefix, user) -> None:
    resp = await client.get(f"{api_prefix}/jobs/job_missing/events", headers=user.headers)
    assert resp.status_code == 404


async def test_api_064_cancel_pending(client, api_prefix, user, owner_engine) -> None:
    job_id = await _seed_job(owner_engine, user_id=user.user_id, status="pending")
    resp = await client.post(f"{api_prefix}/jobs/{job_id}/cancel", headers=user.headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"] == job_id


async def test_api_064_cancel_terminal_conflicts(client, api_prefix, user, owner_engine) -> None:
    job_id = await _seed_job(owner_engine, user_id=user.user_id, status="succeeded", stage="done")
    resp = await client.post(f"{api_prefix}/jobs/{job_id}/cancel", headers=user.headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.JOB_NOT_CANCELLABLE.value


async def test_api_064_cancel_cross_user_404(
    client, api_prefix, user, account_factory, owner_engine
) -> None:
    other = await account_factory()
    job_id = await _seed_job(owner_engine, user_id=other.user_id, status="pending")
    resp = await client.post(f"{api_prefix}/jobs/{job_id}/cancel", headers=user.headers)
    assert resp.status_code == 404
