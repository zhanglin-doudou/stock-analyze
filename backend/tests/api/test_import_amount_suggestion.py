"""真实华泰文本型 XLS 账单：金额列建议回归（发生金额 vs 成交金额）。

用户账单（2026-11-12 导出）同时含「成交金额」毛额与「发生金额」带符号净额。
旧建议按别名序号取「成交金额」→「发生金额」落成未映射列，且非交易资金行
（银证转入/利息归本）的毛额为 0，现金流被静默丢弃。
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

V2 = {"X-Import-Flow": "v2"}

# 与真实账单同形的 22 列表头（含股份余额/委托编号/币种等噪音列）。
_HEADER = (
    '="发生日期"\t="业务名称"\t="证券代码"\t="证券名称"\t="成交均价"\t="成交数量"\t="成交金额"\t'
    '="股份余额"\t="手续费"\t="印花税"\t="过户费"\t="其他费"\t="发生金额"\t="资金余额"\t'
    '="委托编号"\t="成交编号"\t="委托价格"\t="委托数量"\t="股东代码"\t="资金帐号"\t="币种"\t="备注"\t'
)
_ROWS = (
    '="2019-08-15"\t="银证转入"\t\t\t0.0000\t0\t0.00\t0\t0.00\t0.00\t0.00\t0.000\t3000.00\t3000.19\t\t\t0.0000\t0\t\t="307000411838"\t="人民币"\t\t',
    '="2019-09-06"\t="证券买入清算"\t="000650"\t="仁和药业"\t7.2700\t400\t2908.00\t400\t5.00\t0.00\t0.00\t0.000\t-2913.00\t87.19\t="BAZUWDS2"\t="0103000009270973"\t7.2700\t400\t="0249753237"\t="307000411838"\t="人民币"\t\t',
    '="2019-09-23"\t="利息归本"\t\t\t0.0000\t0\t0.00\t0\t0.00\t0.00\t0.00\t0.000\t0.74\t87.93\t\t\t0.0000\t0\t\t="307000411838"\t="人民币"\t\t',
)


def _bill() -> bytes:
    return (_HEADER + "\r\n" + "\r\n".join(_ROWS) + "\r\n").encode("gbk")


async def _preview(client: AsyncClient, api_prefix: str, user: Any) -> dict[str, Any]:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers={**user.headers, **V2},
        files={"file": ("对账单查询06.xls", _bill(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_text_xls_amount_suggestion_prefers_signed_column(
    client: AsyncClient, api_prefix: str, user: Any
) -> None:
    body = await _preview(client, api_prefix, user)
    columns = {column["name"]: column for column in body["columns"]}
    assert columns["发生金额"]["suggested_field"] == "amount"
    assert columns["发生金额"]["suggested_options"] == {"amount_signed": True}
    # 毛额列转为未映射：毛额可由数量×价格推导，不再抢占 amount。
    assert columns["成交金额"]["suggested_field"] is None
    # 其它列建议保持不变（不得因本次修复变差）。
    assert {name: column["suggested_field"] for name, column in columns.items()} == {
        "发生日期": "trade_date",
        "业务名称": "summary",
        "证券代码": "security_code",
        "证券名称": "security_name",
        "成交均价": "price",
        "成交数量": "quantity",
        "成交金额": None,
        "股份余额": None,
        "手续费": "commission",
        "印花税": "stamp_tax",
        "过户费": "other_fee",
        "其他费": "other_fee",
        "发生金额": "amount",
        "资金余额": "cash_balance",
        "委托编号": None,
        "成交编号": None,
        "委托价格": None,
        "委托数量": None,
        "股东代码": None,
        "资金帐号": "account",
        "币种": None,
        "备注": "note",
    }
    # 试算按带符号发生金额解析：银证转入 3000.00（旧行为误取毛额 0.00）。
    sample = {row["line_no"]: row for row in body["sample"]}
    assert sample[2]["summary"] == "银证转入"
    assert sample[2]["amount"] == "3000.00"
    assert sample[3]["amount"] == "-2913.00"
    assert sample[4]["amount"] == "0.74"
