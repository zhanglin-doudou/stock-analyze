"""G3 分类链路验收：commit 分类、未知摘要预检、预览/提交计数一致、规则词表。

覆盖：
1. 真实账单（175 行入库 + 2 行全零金额隔离）`category` 100% 非空、未知摘要 0、转账摘要命中 transfer；
2. 未知摘要 → preview 警告 + `estimated.quarantined` == commit 实际隔离；该行不写 trade；
3. 系统默认规则在任意入口幂等自愈（无 CLI seed 也不误隔离），旧词表系统码被同步清理；
4. P0 行为不回退：同文件重传 inserted=0、真实账对账仍 0 断链、全零金额行隔离 INVALID_AMOUNT。
"""

from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_ledger.models import SummaryRule
from stock_worker.runner import WorkerRunner

pytestmark = pytest.mark.api

_MOCK_BILL = Path(__file__).parent / "华泰账单_mock.xlsx"
# 货币基金账单（无「买卖标志」列，摘要为「申购确认/赎回确认/红利发放」）：回归 940018 赎回误隔离。
_FUND_BILL = Path(__file__).parent / "华泰账单_货基_mock.xlsx"
# 真实对账单节选：摘要折行、流水末尾「合计」+ 持仓区块、完全重复行（脱敏节选）。
_STATEMENT_BILL = Path(__file__).parent / "华泰账单_真实节选_mock.xlsx"
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 合成账单：1 条已知摘要（证券买入）+ 1 条未知摘要 → 预览与提交必须都隔离 1 行。
_UNKNOWN_BILL = (
    "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,佣金,印花税,其他费,资金余额,摘要\n"
    "2026-06-30,600519,贵州茅台,买入,100,1500.00,150000.00,5.00,0.00,0.10,100000.00,证券买入\n"
    "2026-06-30,000001,平安银行,买入,200,10.00,2000.00,0.50,0.00,0.01,95000.00,未知业务XYZ\n"
).encode()


async def _drain_worker(settings) -> None:
    """把队列里的任务全部领取并执行（最小复刻真实 worker 消费循环）。"""
    runner = WorkerRunner(settings)
    try:
        while True:
            claimed = await runner._claim_one()
            if claimed is None:
                break
            await runner._execute(claimed)
    finally:
        await runner.close()


async def _preview(
    client,
    api_prefix,
    user,
    data: bytes,
    file_name: str = "bill.csv",
    content_type: str = "text/csv",
) -> dict:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers=user.headers,
        files={"file": (file_name, data, content_type)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _commit(client, api_prefix, user, preview_id: str) -> dict:
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview_id, "mode": "full"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _batch(client, api_prefix, user, batch_id: str) -> dict:
    resp = await client.get(f"{api_prefix}/imports/{batch_id}", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _trades(client, api_prefix, user) -> list[dict]:
    resp = await client.get(f"{api_prefix}/trades?limit=200", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def _quarantine(client, api_prefix, user, batch_id: str) -> list[dict]:
    resp = await client.get(f"{api_prefix}/imports/{batch_id}/quarantine", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def _seed_rule(engine: AsyncEngine, *, code: str, pattern: str = "旧词表") -> None:
    """owner 直插一条「旧词表」系统规则，模拟历史库残留（系统规则 user_id 为 NULL）。"""
    async with AsyncSession(engine) as session:
        session.add(
            SummaryRule(
                id=f"rule_{uuid.uuid4().hex[:12]}",
                user_id=None,
                code=code,
                name="历史系统规则",
                category="transfer",
                side="both",
                match_type="exact",
                pattern=pattern,
                priority=30,
                enabled=True,
                is_system=True,
                version=1,
            )
        )
        await session.commit()


async def _rule_count(engine: AsyncEngine) -> int:
    async with AsyncSession(engine) as session:
        return int(await session.scalar(select(func.count()).select_from(SummaryRule)) or 0)


async def test_real_bill_all_rows_classified_and_zero_unknown(
    client, api_prefix, user, settings
) -> None:
    """真实账单：175/175 category 非空、未知摘要 0、preview 计数 == commit 计数。"""
    preview = await _preview(
        client, api_prefix, user, _MOCK_BILL.read_bytes(), _MOCK_BILL.name, _XLSX_CONTENT_TYPE
    )
    assert preview["estimated"] == {"parsed": 175, "inserted": 175, "skipped": 0, "quarantined": 2}
    assert "UNKNOWN_SUMMARY" not in {warning["code"] for warning in preview["warnings"]}

    accepted = await _commit(client, api_prefix, user, preview["preview_id"])
    await _drain_worker(settings)

    batch = await _batch(client, api_prefix, user, accepted["batch_id"])
    assert batch["counts"] == {"parsed": 175, "inserted": 175, "skipped": 0, "quarantined": 2}
    assert "UNKNOWN_SUMMARY" not in batch["warnings"]

    trades = await _trades(client, api_prefix, user)
    assert len(trades) == 175
    assert all(item["category"] for item in trades), "真实账单必须 100% 分类"
    # 分类分布：买入 83 / 卖出 62 / 资金划转 24（转存管 2 行全零被隔离）/ 分红 3 / 利息 2 / 费用 1。
    assert Counter(item["category"] for item in trades) == {
        "buy": 83,
        "sell": 62,
        "transfer": 24,
        "dividend": 3,
        "interest": 2,
        "fee": 1,
    }
    # 非交易摘要命中资金划转，而不是被「费」等宽泛规则吞掉或被隔离。
    transfer_summaries = {item["summary"] for item in trades if item["category"] == "transfer"}
    assert transfer_summaries == {"银行转存", "银行转取"}
    # 规则短码落 summary_code（≤16 字符列宽），且与分类自洽。
    assert all(item["summary_code"] for item in trades)
    assert {item["summary_code"] for item in trades if item["summary"] == "银行转存"} == {
        "sys_transfer_in"
    }
    assert {item["summary_code"] for item in trades if item["summary"] == "股息红利税\n补缴"} == {
        "sys_div_tax"
    }

    # 仅有的 2 条隔离 = 五金额全 0 的「转存管转入/转出」。
    quarantine = await _quarantine(client, api_prefix, user, accepted["batch_id"])
    assert len(quarantine) == 2
    assert {item["reason_code"] for item in quarantine} == {"INVALID_AMOUNT"}
    assert {item["raw"]["summary"] for item in quarantine} == {"转存管转入", "转存管转出"}

    # P0 回归：分类不影响余额链，真实账对账仍 0 断链。
    reco = await client.get(
        f"{api_prefix}/reconciliation?from=2021-10-29&to=2022-06-07", headers=user.headers
    )
    body = reco.json()
    assert body["status"] == "ok", body["breaks"][:3]
    assert body["checked_rows"] == 174
    assert body["break_count"] == 0
    assert body["filled_count"] == 0


async def test_money_fund_bill_imports_with_zero_amount_quarantine(
    client, api_prefix, user, settings
) -> None:
    """货币基金账单：申赎确认命中规则；全零「红利发放」按 INVALID_AMOUNT 隔离。"""
    preview = await _preview(
        client, api_prefix, user, _FUND_BILL.read_bytes(), _FUND_BILL.name, _XLSX_CONTENT_TYPE
    )
    assert preview["estimated"] == {"parsed": 6, "inserted": 6, "skipped": 0, "quarantined": 1}
    assert "UNKNOWN_SUMMARY" not in {warning["code"] for warning in preview["warnings"]}

    accepted = await _commit(client, api_prefix, user, preview["preview_id"])
    await _drain_worker(settings)

    batch = await _batch(client, api_prefix, user, accepted["batch_id"])
    assert batch["counts"] == {"parsed": 6, "inserted": 6, "skipped": 0, "quarantined": 1}
    assert "UNKNOWN_SUMMARY" not in set(batch["warnings"])

    items = await _quarantine(client, api_prefix, user, accepted["batch_id"])
    assert len(items) == 1
    assert items[0]["reason_code"] == "INVALID_AMOUNT"
    assert items[0]["line_no"] == 19

    trades = await _trades(client, api_prefix, user)
    assert len(trades) == 6
    assert all(item["category"] for item in trades)
    by_summary = {(item["summary"], item["side"]): item for item in trades}

    redeem = by_summary[("赎回确认", "sell")]
    assert redeem["category"] == "sell"
    assert redeem["summary_code"] == "sys_redeem"
    assert redeem["amount"] == "116295.38"
    # 货基行带余额（份额 × 1.0000）；无持仓锚点且有净赎回 → 期初按 -min(累计净额)
    # 反推下界（116307.65），余额全程 ≥ 0：赎回后 = 12.27，申购后 = 106720.80。
    assert redeem["money_fund_balance"] == "12.27"

    subscribe = by_summary[("申购确认", "buy")]
    assert subscribe["category"] == "buy"
    assert subscribe["summary_code"] == "sys_fund_buy"
    assert subscribe["amount"] == "-106708.53"
    assert subscribe["money_fund_balance"] == "106720.80"

    assert {item["category"] for item in trades} == {"buy", "sell", "transfer"}


async def test_real_statement_excerpt_quarantine_only_zero_amount(
    client, api_prefix, user, settings
) -> None:
    """真实对账单节选：折行摘要全部命中、持仓区不入隔离，全零金额行隔离 INVALID_AMOUNT。"""
    preview = await _preview(
        client,
        api_prefix,
        user,
        _STATEMENT_BILL.read_bytes(),
        _STATEMENT_BILL.name,
        _XLSX_CONTENT_TYPE,
    )
    assert preview["estimated"] == {"parsed": 5, "inserted": 5, "skipped": 0, "quarantined": 5}
    assert "UNKNOWN_SUMMARY" not in {warning["code"] for warning in preview["warnings"]}

    accepted = await _commit(client, api_prefix, user, preview["preview_id"])
    await _drain_worker(settings)

    batch = await _batch(client, api_prefix, user, accepted["batch_id"])
    assert batch["counts"] == {"parsed": 5, "inserted": 5, "skipped": 0, "quarantined": 5}
    assert "UNKNOWN_SUMMARY" not in set(batch["warnings"])
    # 节选夹具余额链自洽 → 导入对账不产生补差。
    assert batch["filled_count"] == 0
    assert batch["fills"] == []

    items = await _quarantine(client, api_prefix, user, accepted["batch_id"])
    assert len(items) == 5
    assert {item["reason_code"] for item in items} == {"INVALID_AMOUNT"}
    assert {item["line_no"] for item in items} == {17, 18, 19, 20, 25}
    # 持仓/声明区块（夹具第 27 行起）不应出现在隔离区。
    assert all(item["line_no"] < 27 for item in items)

    trades = await _trades(client, api_prefix, user)
    assert len(trades) == 5
    assert all(item["category"] for item in trades)
    by_summary = {item["summary"]: item for item in trades}
    assert by_summary["[省心投]申\n购确认"]["category"] == "buy"
    assert by_summary["新股申购确\n认缴款"]["category"] == "new_stock"
    assert by_summary["[省心投]申\n购确认"]["side"] == "buy"
    assert {item["category"] for item in trades} == {
        "buy",
        "sell",
        "transfer",
        "new_stock",
    }
    # 基金持仓区块是期末锚点：940018 赎回后余额 = 参考市值 51859.86；202308 期初 0。
    balances = {item["money_fund_balance"] for item in trades if item["money_fund_balance"]}
    assert balances == {"51859.86", "10000.00"}


async def test_unknown_summary_preview_equals_commit_quarantine(
    client, api_prefix, user, settings
) -> None:
    """未知摘要：预览警告（含行号/摘要）+ 预估隔离 == 提交实际隔离；该行不写 trade。"""
    preview = await _preview(client, api_prefix, user, _UNKNOWN_BILL)
    assert preview["estimated"] == {"parsed": 2, "inserted": 1, "skipped": 0, "quarantined": 1}
    assert "UNKNOWN_SUMMARY" in {warning["code"] for warning in preview["warnings"]}
    assert preview["assumptions"]["unknown_summary_rows"] == [
        {"line_no": 3, "summary": "未知业务XYZ", "side": "buy"}
    ]

    accepted = await _commit(client, api_prefix, user, preview["preview_id"])
    await _drain_worker(settings)

    batch = await _batch(client, api_prefix, user, accepted["batch_id"])
    assert batch["counts"] == {"parsed": 2, "inserted": 1, "skipped": 0, "quarantined": 1}
    assert "UNKNOWN_SUMMARY" in batch["warnings"]
    assert batch["assumptions"]["unknown_summary_rows"] == [
        {"line_no": 3, "summary": "未知业务XYZ", "side": "buy"}
    ]

    trades = await _trades(client, api_prefix, user)
    assert len(trades) == 1
    assert trades[0]["summary"] == "证券买入"
    assert trades[0]["category"] == "buy"
    assert trades[0]["summary_code"] == "sys_buy_stock"

    items = await _quarantine(client, api_prefix, user, accepted["batch_id"])
    assert len(items) == 1
    item = items[0]
    assert item["reason_code"] == "UNKNOWN_SUMMARY"
    assert item["line_no"] == 3
    assert item["raw"]["summary"] == "未知业务XYZ"
    assert item["resolved"] is False


async def test_unknown_summary_reimport_keeps_dedupe_skipped(
    client, api_prefix, user, settings
) -> None:
    """重传同一文件：已插入行 skipped（不重复插入），未知摘要行仍稳定隔离且计数一致。"""
    first = await _preview(client, api_prefix, user, _UNKNOWN_BILL)
    first_job = await _commit(client, api_prefix, user, first["preview_id"])
    await _drain_worker(settings)
    assert (await _batch(client, api_prefix, user, first_job["batch_id"]))["counts"] == {
        "parsed": 2,
        "inserted": 1,
        "skipped": 0,
        "quarantined": 1,
    }

    second = await _preview(client, api_prefix, user, _UNKNOWN_BILL)
    assert second["estimated"] == {"parsed": 2, "inserted": 0, "skipped": 1, "quarantined": 1}
    second_job = await _commit(client, api_prefix, user, second["preview_id"])
    await _drain_worker(settings)
    assert (await _batch(client, api_prefix, user, second_job["batch_id"]))["counts"] == {
        "parsed": 2,
        "inserted": 0,
        "skipped": 1,
        "quarantined": 1,
    }
    assert len(await _trades(client, api_prefix, user)) == 1


async def test_system_rules_seed_idempotent_and_old_vocabulary_pruned(
    client, api_prefix, user, settings, owner_engine
) -> None:
    """任意入口（preview）自愈系统规则：幂等、无重复，且旧词表系统码被同步清理。"""
    from stock_ledger.repositories.rules import SYSTEM_RULES

    await _seed_rule(owner_engine, code="sys_transfer_bank_in")
    before = await _rule_count(owner_engine)
    assert before == 1

    await _preview(client, api_prefix, user, _UNKNOWN_BILL)
    after_first = await _rule_count(owner_engine)
    assert after_first == len(SYSTEM_RULES)

    await _preview(client, api_prefix, user, _UNKNOWN_BILL)
    assert await _rule_count(owner_engine) == after_first  # 幂等：无重复写入

    async with AsyncSession(owner_engine) as session:
        codes = set(await session.scalars(select(SummaryRule.code)))
    assert "sys_transfer_bank_in" not in codes  # 废弃系统码已清理
    assert {"sys_transfer_in", "sys_transfer_out", "sys_custody_in", "sys_custody_out"} <= codes


async def test_synthetic_fixture_preview_equals_commit(client, api_prefix, user, settings) -> None:
    """合成夹具（fixtures/market/bill_huatai.xlsx）：预览计数 == 提交计数，且插入行 100% 分类。

    不固化夹具行数（G4 可能调整生成器），只断言「预览=提交=实际」「已插入行都有分类」，
    以及隔离区条数与批次计数一致——这正是 G3 要求的 seed 数据分类一致性。
    """
    fixture = Path(__file__).parents[3] / "fixtures" / "market" / "bill_huatai.xlsx"
    preview = await _preview(
        client, api_prefix, user, fixture.read_bytes(), fixture.name, _XLSX_CONTENT_TYPE
    )
    accepted = await _commit(client, api_prefix, user, preview["preview_id"])
    await _drain_worker(settings)

    batch = await _batch(client, api_prefix, user, accepted["batch_id"])
    assert preview["estimated"] == batch["counts"]

    fills = batch["filled_count"]
    trades = await _trades(client, api_prefix, user)
    # 插入行 + 导入收尾对账补差行 = 实际流水总数；未知摘要行被隔离后会形成缺口并被补差。
    assert len(trades) == batch["counts"]["inserted"] + fills
    assert trades and all(item["category"] for item in trades)
    if fills:
        assert "RECONCILIATION_AUTOFILLED" in batch["warnings"]
        assert len(batch["fills"]) == fills
        assert all(item["summary"] == "对账自动补差" for item in batch["fills"])

    quarantine = await _quarantine(client, api_prefix, user, accepted["batch_id"])
    assert len(quarantine) == batch["counts"]["quarantined"]
    assert all(
        item["reason_code"] in {"UNKNOWN_SUMMARY", "DUPLICATE_IN_FILE"} for item in quarantine
    )
    # 分类隔离只应来自「未知业务XYZ」演示行；重复行隔离的 reason 是 DUPLICATE_IN_FILE。
    unknown_reason = [item for item in quarantine if item["reason_code"] == "UNKNOWN_SUMMARY"]
    assert {item["raw"]["summary"] for item in unknown_reason} <= {"未知业务XYZ"}
