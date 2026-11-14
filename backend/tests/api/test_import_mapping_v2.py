"""导入映射重构 v2：API-010/098/099/106/100/101 + 管理员规则 102~105 验收。

覆盖 happy / 越权 / 关键错误码；v2 通过 `X-Import-Flow: v2` 显式开启，
legacy 行为由 `test_imports_trades_jobs.py` 回归。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from stock_contracts.errors import ErrorCode
from stock_worker.runner import WorkerRunner

_HEADER = (
    "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
    "佣金,印花税,其他费,资金余额,摘要\n"
)
V2 = {"X-Import-Flow": "v2"}
_MARKET_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "market"
_HUATAI_FIXTURE = _MARKET_FIXTURES / "bill_huatai.xlsx"
_HUATAI_NEWTYPE_FIXTURE = _MARKET_FIXTURES / "bill_huatai_newtype.xlsx"
_TWO_FEE_FIXTURE = _MARKET_FIXTURES / "bill_generic_2fee.csv"
_NO_SUMMARY_FIXTURE = _MARKET_FIXTURES / "bill_generic_nosummary.csv"
_LEGACY_GOLDEN_FIXTURE = Path(__file__).with_name("华泰账单_mock.xlsx")
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _row(index: int, summary: str = "证券买入") -> str:
    # 买入 100@10 + 佣金 5 → 净额 -1005；余额必须逐行递减，才能通过连续对账。
    balance = 9000 - 1005 * index
    return (
        f"2026-04-{index:02d},60000{index},测试{index},买入,100,10.00,1000.00,"
        f"5.00,0.00,0.00,{balance:.2f},{summary}\n"
    )


def _bill(rows: int = 2, summary: str = "证券买入") -> bytes:
    return ("\ufeff" + _HEADER + "".join(_row(i + 1, summary) for i in range(rows))).encode("utf-8")


def _two_fee_bill(*, fee_a: str = "1.00", second_balance: str = "10098.00") -> bytes:
    return (
        "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
        "佣金,印花税,费用甲,费用乙,资金余额,摘要\n"
        f"2026-04-01,600001,测试,买入,100,10.00,1000.00,0,0,{fee_a},0.50,8998.50,证券买入\n"
        f"2026-04-02,600001,测试,卖出,100,11.00,1100.00,0,0,0.25,0.25,{second_balance},证券卖出\n"
    ).encode()


def _two_fee_mapping(preview: dict[str, Any]) -> dict[str, Any]:
    payload = _mapping_payload(preview)
    payload["items"].extend(
        {
            "column_key": next(
                column["key"] for column in preview["columns"] if column["name"] == name
            ),
            "target_field": "other_fee",
            "options": {},
        }
        for name in ("费用甲", "费用乙")
    )
    return payload


async def _preview(
    client: AsyncClient,
    api_prefix: str,
    user: Any,
    *,
    data: bytes | None = None,
    filename: str = "huatai.csv",
    content_type: str = "text/csv",
) -> dict[str, Any]:
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers={**user.headers, **V2},
        files={"file": (filename, data or _bill(), content_type)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mapping_payload(preview: dict[str, Any]) -> dict[str, Any]:
    items = [
        {
            "column_key": column["key"],
            "target_field": column["suggested_field"],
            "options": column["suggested_options"],
        }
        for column in preview["columns"]
        if column["suggested_field"]
    ]
    return {"items": items, "ignored": [], "acknowledge_unmapped": False}


def _types_payload(preview: dict[str, Any], *, discard: bool = False) -> dict[str, Any]:
    return {
        "resolutions": [
            {
                "type_key": item["type_key"],
                "category": None
                if discard
                else (item["category"] or item["suggested_category"] or "other"),
                "discard": discard,
            }
            for item in preview["types"]
        ]
    }


async def _ready_preview(
    client: AsyncClient,
    api_prefix: str,
    user: Any,
    *,
    data: bytes | None = None,
    filename: str = "huatai.csv",
    content_type: str = "text/csv",
) -> dict[str, Any]:
    preview = await _preview(
        client,
        api_prefix,
        user,
        data=data,
        filename=filename,
        content_type=content_type,
    )
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_mapping_payload(preview),
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body),
    )
    assert typed.status_code == 200, typed.text
    return typed.json()


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


async def _commit(
    client: AsyncClient, api_prefix: str, user: Any, preview: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={
            "preview_id": preview["preview_id"],
            "mode": "full",
            "mapping_version": preview["mapping_version"],
            **extra,
        },
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def test_api_010_v2_stage_mapping_or_types(client, api_prefix, user) -> None:
    body = await _preview(client, api_prefix, user)
    assert body["stage"] in ("mapping", "types", "ready")
    assert body["broker"] == "huatai"
    assert body["signature"]
    assert body["mapping_version"] == 1
    assert body["catalog"]
    assert body["categories"]
    assert body["estimated"]["estimated_available"] is True
    assert all("key" in column for column in body["columns"])
    # 摘要规则命中的已知类型无需人工确认 → 可直接 ready。
    assert body["can_commit"] is (body["stage"] == "ready")


async def test_api_010_v2_huatai_fixture_has_complete_suggestions(client, api_prefix, user) -> None:
    body = await _preview(
        client,
        api_prefix,
        user,
        data=_HUATAI_FIXTURE.read_bytes(),
        filename=_HUATAI_FIXTURE.name,
        content_type=_XLSX_CONTENT_TYPE,
    )
    assert body["broker"] == "huatai"
    assert len(body["columns"]) == 12
    assert all(column["suggested_field"] for column in body["columns"])
    assert {column["name"] for column in body["columns"]} == {
        "成交日期",
        "证券代码",
        "证券名称",
        "买卖标志",
        "成交数量",
        "成交价格",
        "成交金额",
        "佣金",
        "印花税",
        "其他费",
        "资金余额",
        "摘要",
    }
    assert any(item["suggested_category"] for item in body["types"])
    assert {item["type_key"] for item in body["types"] if item["needs_confirmation"]} == {
        "未知业务xyz"
    }


async def test_api_010_v2_rejects_headerless_data_layout(client, api_prefix, user) -> None:
    data = _bill().decode("utf-8-sig").split("\n", 1)[1].encode()
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers={**user.headers, **V2},
        files={"file": ("headerless.csv", data, "text/csv")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.IMPORT_LAYOUT_UNSUPPORTED.value


async def test_api_010_v2_unknown_text_headers_remain_mappable(client, api_prefix, user) -> None:
    body = await _preview(
        client,
        api_prefix,
        user,
        data="自定义甲,自定义乙\nfoo,bar\n".encode(),
        filename="custom.csv",
    )
    assert body["stage"] == "mapping"
    assert [column["name"] for column in body["columns"]] == ["自定义甲", "自定义乙"]
    assert all(column["suggested_field"] is None for column in body["columns"])


async def test_api_098_duplicate_fee_headers_map_independently(client, api_prefix, user) -> None:
    data = (
        "成交日期,证券代码,证券名称,买卖标志,成交数量,成交价格,成交金额,"
        "佣金,印花税,其他费,其他费,资金余额,摘要\n"
        "2026-04-01,600001,测试,买入,100,10.00,1000.00,0,0,1.00,0.50,8998.50,证券买入\n"
        "2026-04-02,600001,测试,卖出,100,11.00,1100.00,0,0,0.25,0.25,10098.00,证券卖出\n"
    ).encode()
    preview = await _preview(client, api_prefix, user, data=data, filename="duplicate.csv")
    fee_columns = [column for column in preview["columns"] if column["name"] == "其他费"]
    assert len(fee_columns) == 2
    assert len({column["key"] for column in fee_columns}) == 2
    assert {column["suggested_field"] for column in fee_columns} == {"other_fee"}

    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_mapping_payload(preview),
    )
    assert resp.status_code == 200, resp.text
    fee_items = [item for item in resp.json()["mapping_items"] if item["field"] == "other_fee"]
    assert {item["column_key"] for item in fee_items} == {column["key"] for column in fee_columns}
    assert resp.json()["verification"]["fee_merge"]["status"] == "ok"


async def test_api_010_shareholder_codes_are_not_accounts(client, api_prefix, user) -> None:
    """沪/深股东代码不再是资金账号：不得据此判定多账户、不得挡单。"""
    data = (
        "成交日期,证券代码,成交数量,成交价格,成交金额,其他费,过户费,资金余额,股东代码,摘要\n"
        "2026-04-01,600001,100,10.00,1000.00,1.00,0.50,8998.50,A,证券买入\n"
        "2026-04-02,600001,100,11.00,1100.00,0.25,0.25,10098.00,B,证券卖出\n"
    ).encode()
    preview = await _preview(client, api_prefix, user, data=data, filename="accounts.csv")
    assert preview["blocking"] == []
    by_name = {column["name"]: column for column in preview["columns"]}
    assert by_name["股东代码"]["suggested_field"] is None

    # 两列费用合并后余额链校验通过，映射可直接通过（股东代码确认忽略）。
    payload = _mapping_payload(preview)
    payload["ignored"] = [by_name["股东代码"]["key"]]
    payload["acknowledge_unmapped"] = True
    assert len([item for item in payload["items"] if item["target_field"] == "other_fee"]) == 2
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=payload,
    )
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["verification"]["fee_merge"]["status"] == "ok"


async def test_api_099_no_summary_fixture_uses_derived_types(client, api_prefix, user) -> None:
    body = await _preview(
        client,
        api_prefix,
        user,
        data=_NO_SUMMARY_FIXTURE.read_bytes(),
        filename=_NO_SUMMARY_FIXTURE.name,
    )
    assert body["stage"] == "types"
    assert {item["type_key"] for item in body["types"]} == {"derived:buy", "derived:sell"}
    assert all(item["needs_confirmation"] for item in body["types"])


async def test_api_098_mapping_version_conflict(client, api_prefix, user) -> None:
    preview = await _preview(client, api_prefix, user)
    stale = int(preview["mapping_version"]) + 10
    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(stale)},
        json=_mapping_payload(preview),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.VERSION_CONFLICT.value


async def test_api_098_acknowledge_unmapped_required(client, api_prefix, user) -> None:
    preview = await _preview(client, api_prefix, user)
    payload = _mapping_payload(preview)
    # 去掉一个可选字段（佣金）且不忽略 → 未映射列必须显式确认。
    payload["items"] = [item for item in payload["items"] if item["target_field"] != "commission"]
    version = str(preview["mapping_version"])
    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": version},
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.IMPORT_UNMAPPED_COLUMNS_UNCONFIRMED.value

    commission_key = next(
        column["key"] for column in preview["columns"] if column["name"] == "佣金"
    )
    payload["ignored"] = [commission_key]
    payload["acknowledge_unmapped"] = True
    ok = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": version},
        json=payload,
    )
    assert ok.status_code == 200, ok.text


async def test_api_099_ready_then_commit_and_snapshot(client, api_prefix, user, settings) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    assert ready["stage"] == "ready"
    assert ready["can_commit"] is True

    accepted = await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "succeeded"
    assert body["counts"]["parsed"] == 2
    assert body["counts"]["inserted"] == 2
    assert body["counts"]["discarded"] == 0
    assert body["mapping_source"] == "manual"
    assert body["mapping"]["source"] == "manual"
    assert body["mapping"]["is_origin"] is True
    assert body["mapping"]["signature"] == ready["signature"]


async def test_api_011_stage_conflict_before_ready(client, api_prefix, user) -> None:
    # 未知摘要类型 → 需人工确认，stage 停在 types，不能直接提交。
    preview = await _preview(client, api_prefix, user, data=_bill(summary="基金转换"))
    assert preview["stage"] != "ready"
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": preview["preview_id"], "mode": "full"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.IMPORT_STAGE_CONFLICT.value


async def test_api_011_stale_mapping_version(client, api_prefix, user) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": ready["preview_id"], "mode": "full", "mapping_version": 0},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.VERSION_CONFLICT.value


async def test_api_099_discard_rows_go_to_quarantine(client, api_prefix, user, settings) -> None:
    preview = await _preview(client, api_prefix, user)
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_mapping_payload(preview),
    )
    body = mapped.json()
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body, discard=True),
    )
    assert typed.status_code == 200, typed.text
    accepted = await _commit(client, api_prefix, user, typed.json())
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    counts = detail.json()["counts"]
    assert counts["discarded"] == 2
    assert counts["quarantined"] == 0
    assert counts["inserted"] == 0

    quarantine = await client.get(
        f"{api_prefix}/imports/{accepted['batch_id']}/quarantine", headers=user.headers
    )
    assert quarantine.status_code == 200
    reasons = {item["reason_code"] for item in quarantine.json()["items"]}
    assert "BILL_TYPE_DISCARDED" in reasons


async def test_api_106_get_preview(client, api_prefix, user) -> None:
    preview = await _preview(client, api_prefix, user)
    resp = await client.get(
        f"{api_prefix}/imports/previews/{preview['preview_id']}", headers=user.headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["preview_id"] == preview["preview_id"]
    assert resp.json()["stage"] == preview["stage"]


async def test_api_106_cross_account_404(client, api_prefix, user, account_factory) -> None:
    other = await account_factory()
    preview = await _preview(client, api_prefix, user)
    resp = await client.get(
        f"{api_prefix}/imports/previews/{preview['preview_id']}", headers=other.headers
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.IMPORT_PREVIEW_NOT_FOUND.value


async def test_api_100_101_template_reuse_and_delete(client, api_prefix, user, settings) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)

    listed = await client.get(f"{api_prefix}/imports/templates", headers=user.headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    template_id = items[0]["template_id"]
    assert items[0]["signature"] == ready["signature"]
    assert items[0]["items"]

    # 同文件再传：按签名命中模板，自动带出映射。
    again = await _preview(client, api_prefix, user)
    assert again["template"] is not None
    assert again["template"]["template_id"] == template_id
    assert again["stage"] == "ready"
    assert again["can_commit"] is True

    deleted = await client.delete(
        f"{api_prefix}/imports/templates/{template_id}", headers=user.headers
    )
    assert deleted.status_code == 204
    missing = await client.delete(
        f"{api_prefix}/imports/templates/{template_id}", headers=user.headers
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == ErrorCode.IMPORT_TEMPLATE_NOT_FOUND.value


async def test_api_101_cross_account_template_404(
    client, api_prefix, user, settings, account_factory
) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)
    listed = await client.get(f"{api_prefix}/imports/templates", headers=user.headers)
    template_id = listed.json()["items"][0]["template_id"]

    other = await account_factory()
    resp = await client.delete(
        f"{api_prefix}/imports/templates/{template_id}", headers=other.headers
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.IMPORT_TEMPLATE_NOT_FOUND.value


async def test_api_011_reimport_conflict_requires_ack(client, api_prefix, user, settings) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)

    # 同文件、不同映射（把佣金列改成 other_fee）→ 重导冲突提示。
    preview = await _preview(client, api_prefix, user)
    payload = _mapping_payload(preview)
    for item in payload["items"]:
        if item["target_field"] == "commission":
            item["target_field"] = "note"
    payload["ignored"] = []
    payload["acknowledge_unmapped"] = True
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=payload,
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["mapping_fingerprint"] != ready["mapping_fingerprint"]
    assert body["requires_reimport_ack"] is True
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body),
    )
    assert typed.status_code == 200, typed.text
    ready2 = typed.json()

    resp = await client.post(
        f"{api_prefix}/imports",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={
            "preview_id": ready2["preview_id"],
            "mode": "full",
            "mapping_version": ready2["mapping_version"],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.IMPORT_REIMPORT_CONFLICT.value

    accepted = await _commit(client, api_prefix, user, ready2, acknowledge_reimport=True)
    await _drain_worker(settings)
    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["status"] == "succeeded"


async def test_api_015_revoke_rolls_back_template_and_types(
    client, api_prefix, user, settings, owner_engine: AsyncEngine
) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    accepted = await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)
    assert (await client.get(f"{api_prefix}/imports/templates", headers=user.headers)).json()[
        "items"
    ]

    revoked = await client.post(
        f"{api_prefix}/imports/{accepted['batch_id']}/revoke",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"reason": "user_request"},
    )
    assert revoked.status_code == 200, revoked.text
    mapping = revoked.json()["mapping_revoked"]
    assert mapping["mapping_deleted"] is True
    assert mapping["types_deleted"] >= 1
    assert (await client.get(f"{api_prefix}/imports/templates", headers=user.headers)).json()[
        "items"
    ] == []
    async with owner_engine.connect() as conn:
        audit_count = await conn.scalar(
            text(
                "SELECT count(*) FROM platform.audit_log "
                "WHERE action='import.mapping_revoked' AND target_id=:batch_id"
            ),
            {"batch_id": accepted["batch_id"]},
        )
    assert audit_count == 1

    repeated = await client.post(
        f"{api_prefix}/imports/{accepted['batch_id']}/revoke",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"reason": "user_request"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == ErrorCode.IMPORT_ALREADY_REVOKED.value


async def test_api_102_105_admin_rules(client, api_prefix, user, admin) -> None:
    await _preview(client, api_prefix, user)  # 触发系统规则自愈 seed

    forbidden = await client.get(f"{api_prefix}/admin/import-column-rules", headers=user.headers)
    assert forbidden.status_code == 403

    listed = await client.get(f"{api_prefix}/admin/import-column-rules", headers=admin.headers)
    assert listed.status_code == 200
    assert any(item["is_system"] for item in listed.json()["items"])

    created = await client.post(
        f"{api_prefix}/admin/import-column-rules",
        headers=admin.headers,
        json={
            "broker": "generic",
            "header_norm": "自定义备注列",
            "match_type": "exact",
            "target_field": "note",
            "priority": 100,
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["is_system"] is False
    assert rule["version"] == 1

    duplicate = await client.post(
        f"{api_prefix}/admin/import-column-rules",
        headers=admin.headers,
        json={
            "broker": "generic",
            "header_norm": "自定义备注列",
            "match_type": "exact",
            "target_field": "note",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == ErrorCode.IMPORT_RULE_CONFLICT.value

    patched = await client.patch(
        f"{api_prefix}/admin/import-column-rules/{rule['rule_id']}",
        headers={**admin.headers, "If-Match": "1"},
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    stale = await client.patch(
        f"{api_prefix}/admin/import-column-rules/{rule['rule_id']}",
        headers={**admin.headers, "If-Match": "1"},
        json={"enabled": True},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == ErrorCode.VERSION_CONFLICT.value

    system_rule = next(item for item in listed.json()["items"] if item["is_system"])
    denied = await client.delete(
        f"{api_prefix}/admin/import-column-rules/{system_rule['rule_id']}", headers=admin.headers
    )
    assert denied.status_code == 422

    removed = await client.delete(
        f"{api_prefix}/admin/import-column-rules/{rule['rule_id']}", headers=admin.headers
    )
    assert removed.status_code == 204


async def test_api_103_admin_rules_patch_requires_if_match(client, api_prefix, admin) -> None:
    created = await client.post(
        f"{api_prefix}/admin/import-column-rules",
        headers=admin.headers,
        json={
            "broker": "generic",
            "header_norm": "无版本列",
            "target_field": "note",
        },
    )
    assert created.status_code == 201
    resp = await client.patch(
        f"{api_prefix}/admin/import-column-rules/{created.json()['rule_id']}",
        headers=admin.headers,
        json={"enabled": False},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/import-column-rules"),
        ("POST", "/admin/import-column-rules"),
        ("PATCH", "/admin/import-column-rules/rul_probe"),
        ("DELETE", "/admin/import-column-rules/rul_probe"),
    ],
)
async def test_api_102_105_non_admin_forbidden(client, api_prefix, user, method, path) -> None:
    headers = dict(user.headers)
    kwargs: dict[str, Any] = {}
    if method == "POST":
        kwargs["json"] = {"broker": "generic", "header_norm": "probe", "target_field": "note"}
    elif method == "PATCH":
        headers["If-Match"] = "1"
        kwargs["json"] = {"enabled": False}
    resp = await client.request(method, f"{api_prefix}{path}", headers=headers, **kwargs)
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.FORBIDDEN.value


@pytest.mark.parametrize("endpoint", ["/admin/import-column-rules"])
async def test_api_102_forbidden_for_admin_reading_trades(
    client, api_prefix, admin, endpoint
) -> None:
    # admin 读业务数据仍禁止；规则表是显式例外，这里确认对照没有被放宽。
    resp = await client.get(f"{api_prefix}/trades", headers=admin.headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES.value


@pytest.mark.parametrize("field", ["trade_date", "security_code", "quantity", "price"])
async def test_api_098_rejects_incomplete_required_mapping(
    client, api_prefix, user, field: str
) -> None:
    preview = await _preview(client, api_prefix, user)
    payload = _mapping_payload(preview)
    payload["items"] = [item for item in payload["items"] if item["target_field"] != field]
    payload["ignored"] = [
        column["key"] for column in preview["columns"] if column["suggested_field"] == field
    ]
    payload["acknowledge_unmapped"] = True
    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.IMPORT_COLUMNS_UNRESOLVED.value
    assert any(error["field"] == field for error in resp.json()["errors"])


async def test_api_098_rejects_duplicate_non_fee_field(client, api_prefix, user) -> None:
    preview = await _preview(client, api_prefix, user)
    payload = _mapping_payload(preview)
    targets = [
        item for item in payload["items"] if item["target_field"] in {"commission", "stamp_tax"}
    ]
    assert len(targets) == 2
    for item in targets:
        item["target_field"] = "note"
    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == ErrorCode.IMPORT_MAPPING_INVALID.value


async def test_api_098_011_two_fee_columns_merge_and_commit(
    client, api_prefix, user, settings, owner_engine: AsyncEngine
) -> None:
    preview = await _preview(
        client,
        api_prefix,
        user,
        data=_TWO_FEE_FIXTURE.read_bytes(),
        filename=_TWO_FEE_FIXTURE.name,
    )
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_two_fee_mapping(preview),
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["verification"]["fee_merge"]["status"] == "ok"
    assert body["verification"]["fee_merge"]["checked_rows"] == 1
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body),
    )
    assert typed.status_code == 200, typed.text
    accepted = await _commit(client, api_prefix, user, typed.json())
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["counts"]["inserted"] == 2
    async with owner_engine.connect() as conn:
        rows = list(
            await conn.execute(
                text(
                    "SELECT other_fee_units, amount_units FROM ledger.trade "
                    "WHERE batch_id=:batch_id AND line_no IS NOT NULL AND deleted_at IS NULL "
                    "ORDER BY line_no"
                ),
                {"batch_id": accepted["batch_id"]},
            )
        )
    assert [(row.other_fee_units, row.amount_units) for row in rows] == [
        (150, -100150),
        (50, 109950),
    ]


@pytest.mark.parametrize(
    ("bill", "expected_code", "line_text"),
    [
        (_two_fee_bill(fee_a="abc"), ErrorCode.IMPORT_FEE_MERGE_INVALID.value, "第 2 行"),
    ],
)
async def test_api_098_fee_arithmetic_errors(
    client, api_prefix, user, bill: bytes, expected_code: str, line_text: str
) -> None:
    preview = await _preview(client, api_prefix, user, data=bill)
    resp = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_two_fee_mapping(preview),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == expected_code
    assert any(line_text in error["message"] for error in resp.json()["errors"])


async def test_api_098_balance_chain_break_is_quarantined_not_rejected(
    client, api_prefix, user, settings
) -> None:
    """余额接不上不再整批失败：该行进隔离区（RECONCILIATION_BREAK），其余行正常入库。"""
    bill = _two_fee_bill(second_balance="10000.00")
    preview = await _preview(client, api_prefix, user, data=bill)
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_two_fee_mapping(preview),
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["estimated"]["inserted"] == 1
    assert body["estimated"]["quarantined"] == 1

    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body),
    )
    assert typed.status_code == 200, typed.text
    accepted = await _commit(client, api_prefix, user, typed.json())
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["counts"] == {
        "parsed": 1,
        "inserted": 1,
        "skipped": 0,
        "quarantined": 1,
        "discarded": 0,
    }


async def _mapped_unknown_type_preview(
    client: AsyncClient, api_prefix: str, user: Any
) -> dict[str, Any]:
    preview = await _preview(client, api_prefix, user, data=_bill(summary="基金转换"))
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=_mapping_payload(preview),
    )
    assert mapped.status_code == 200, mapped.text
    assert mapped.json()["stage"] == "types"
    return mapped.json()


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing", ErrorCode.IMPORT_BILL_TYPES_UNRESOLVED.value),
        ("unknown", ErrorCode.IMPORT_MAPPING_INVALID.value),
        ("duplicate", ErrorCode.IMPORT_MAPPING_INVALID.value),
        ("illegal", ErrorCode.IMPORT_CATEGORY_INVALID.value),
        ("category_and_discard", ErrorCode.IMPORT_CATEGORY_INVALID.value),
    ],
)
async def test_api_099_rejects_invalid_type_resolutions(
    client, api_prefix, user, case: str, expected_code: str
) -> None:
    body = await _mapped_unknown_type_preview(client, api_prefix, user)
    payload = _types_payload(body)
    if case == "missing":
        payload["resolutions"] = []
    elif case == "unknown":
        payload["resolutions"][0]["type_key"] = "not-present"
    elif case == "duplicate":
        payload["resolutions"].append(dict(payload["resolutions"][0]))
    elif case == "illegal":
        payload["resolutions"][0]["category"] = "not-a-category"
    elif case == "category_and_discard":
        payload["resolutions"][0]["discard"] = True

    resp = await client.put(
        f"{api_prefix}/imports/previews/{body['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"])},
        json=payload,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == expected_code


async def test_api_099_stale_mapping_version(client, api_prefix, user) -> None:
    body = await _mapped_unknown_type_preview(client, api_prefix, user)
    resp = await client.put(
        f"{api_prefix}/imports/previews/{body['preview_id']}/types",
        headers={**user.headers, "If-Match": str(body["mapping_version"] + 1)},
        json=_types_payload(body),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.VERSION_CONFLICT.value


async def test_api_098_099_cross_account_404(client, api_prefix, user, account_factory) -> None:
    other = await account_factory()
    body = await _mapped_unknown_type_preview(client, api_prefix, user)
    mapping = await client.put(
        f"{api_prefix}/imports/previews/{body['preview_id']}/mapping",
        headers={**other.headers, "If-Match": str(body["mapping_version"])},
        json=_mapping_payload(body),
    )
    types = await client.put(
        f"{api_prefix}/imports/previews/{body['preview_id']}/types",
        headers={**other.headers, "If-Match": str(body["mapping_version"])},
        json=_types_payload(body),
    )
    for resp in (mapping, types):
        assert resp.status_code == 404
        assert resp.json()["code"] == ErrorCode.IMPORT_PREVIEW_NOT_FOUND.value


@pytest.mark.parametrize("endpoint", ["mapping", "types"])
async def test_api_098_099_submitted_preview_is_immutable(
    client, api_prefix, user, endpoint: str
) -> None:
    ready = await _ready_preview(client, api_prefix, user)
    await _commit(client, api_prefix, user, ready)
    payload = _mapping_payload(ready) if endpoint == "mapping" else _types_payload(ready)
    resp = await client.put(
        f"{api_prefix}/imports/previews/{ready['preview_id']}/{endpoint}",
        headers={**user.headers, "If-Match": str(ready["mapping_version"])},
        json=payload,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == ErrorCode.IMPORT_STAGE_CONFLICT.value


async def test_api_011_job_uses_immutable_snapshot_and_persists_memories(
    client, api_prefix, user, settings, owner_engine: AsyncEngine
) -> None:
    ready = await _ready_preview(client, api_prefix, user, data=_bill(summary="基金转换"))
    async with owner_engine.connect() as conn:
        before_counts = (
            await conn.scalar(
                text("SELECT count(*) FROM ledger.import_template WHERE user_id=:user_id"),
                {"user_id": user.user_id},
            ),
            await conn.scalar(
                text("SELECT count(*) FROM ledger.import_bill_type WHERE user_id=:user_id"),
                {"user_id": user.user_id},
            ),
        )
    assert before_counts == (0, 0)

    accepted = await _commit(client, api_prefix, user, ready)
    async with owner_engine.connect() as conn:
        params = await conn.scalar(
            text("SELECT params FROM platform.job WHERE id=:job_id"),
            {"job_id": accepted["job_id"]},
        )
        batch = (
            (
                await conn.execute(
                    text(
                        "SELECT signature, header_row_index, mapping_fingerprint, mapping_items, "
                        "type_resolutions, mapping_source FROM ledger.import_batch WHERE id=:batch_id"
                    ),
                    {"batch_id": accepted["batch_id"]},
                )
            )
            .mappings()
            .one()
        )
    assert params["mapping_version"] == ready["mapping_version"]
    assert params["signature"] == ready["signature"] == batch["signature"]
    assert (
        params["mapping_fingerprint"]
        == ready["mapping_fingerprint"]
        == batch["mapping_fingerprint"]
    )
    assert params["mapping_items"] == ready["mapping_items"] == batch["mapping_items"]
    assert params["type_resolutions"] == ready["types"] == batch["type_resolutions"]
    assert params["ignored_columns"] == ready["ignored_columns"]
    assert params["mapping_source"] == batch["mapping_source"] == "manual"

    # 直接篡改草稿，模拟排队期间另一写入方绕过应用层修改 preview。
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ledger.import_preview SET stage='mapping', mapping_items=CAST(:items AS jsonb), "
                "type_resolutions=CAST(:types AS jsonb), verification=CAST(:verification AS jsonb), "
                "mapping_version=mapping_version+10 WHERE id=:preview_id"
            ),
            {
                "items": json.dumps([]),
                "types": json.dumps([]),
                "verification": json.dumps({"tampered": True}),
                "preview_id": ready["preview_id"],
            },
        )
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["counts"]["inserted"] == 2
    async with owner_engine.connect() as conn:
        template_items = await conn.scalar(
            text("SELECT items FROM ledger.import_template WHERE origin_batch_id=:batch_id"),
            {"batch_id": accepted["batch_id"]},
        )
        type_rows = list(
            await conn.execute(
                text(
                    "SELECT type_key, category FROM ledger.import_bill_type "
                    "WHERE origin_batch_id=:batch_id"
                ),
                {"batch_id": accepted["batch_id"]},
            )
        )
        unchanged = await conn.scalar(
            text("SELECT mapping_items FROM ledger.import_batch WHERE id=:batch_id"),
            {"batch_id": accepted["batch_id"]},
        )
    assert template_items == ready["mapping_items"]
    assert [(row.type_key, row.category) for row in type_rows] == [("基金转换", "other")]
    assert unchanged == ready["mapping_items"]


async def test_api_099_import_same_file_twice_is_covered_noop(
    client, api_prefix, user, settings, owner_engine: AsyncEngine
) -> None:
    """文件首日在库中且库末覆盖文件末：整文件 no-op，返回 covered 状态。"""
    first = await _ready_preview(client, api_prefix, user, data=_bill())
    await _commit(client, api_prefix, user, first)
    await _drain_worker(settings)

    second = await _ready_preview(client, api_prefix, user, data=_bill())
    assert second["assumptions"]["coverage"]["mode"] == "covered"
    assert second["estimated"]["inserted"] == 0
    accepted = await _commit(client, api_prefix, user, second)
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["assumptions"]["coverage"]["mode"] == "covered"
    assert detail.json()["counts"] == {
        "parsed": 2,
        "inserted": 0,
        "skipped": 2,
        "quarantined": 0,
        "discarded": 0,
    }
    async with owner_engine.connect() as conn:
        total = await conn.scalar(
            text("SELECT count(*) FROM ledger.trade WHERE user_id=:user_id AND deleted_at IS NULL"),
            {"user_id": user.user_id},
        )
    assert total == 2


async def test_api_099_backfill_requires_chain_closure(client, api_prefix, user, settings) -> None:
    """向前补历史接不上 → 422 IMPORT_CHAIN_DISCONTINUOUS，带断行行号与差额。"""
    later = (
        _HEADER + "2026-04-10,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,8995.00,证券买入\n"
    ).encode()
    later_preview = await _ready_preview(client, api_prefix, user, data=later)
    await _commit(client, api_prefix, user, later_preview)
    await _drain_worker(settings)

    # 末行余额 9000 + 库内首条 -1005 = 7995 ≠ 8995 → 断开 1000 分。
    broken = (
        _HEADER + "2026-04-01,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,9000.00,证券买入\n"
    ).encode()
    # 预览阶段即按连续链预检：接不上直接 422，不进入提交。
    resp = await client.post(
        f"{api_prefix}/imports/preview",
        headers={**user.headers, "X-Import-Flow": "v2"},
        files={"file": ("broken.csv", broken, "text/csv")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == ErrorCode.IMPORT_CHAIN_DISCONTINUOUS.value
    assert any("相差 1000.00" in error["message"] for error in resp.json()["errors"])

    # 闭合的补历史（末行余额 10000）则可以正常入库。
    closed = (
        _HEADER + "2026-04-01,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,10000.00,证券买入\n"
    ).encode()
    ok_preview = await _ready_preview(client, api_prefix, user, data=closed)
    assert ok_preview["assumptions"]["coverage"]["mode"] == "backfill"
    accepted = await _commit(client, api_prefix, user, ok_preview)
    await _drain_worker(settings)
    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["counts"]["inserted"] == 1


async def test_api_099_span_both_ends_replaces_overlap_with_file(
    client, api_prefix, user, settings
) -> None:
    """文件同时早于库首、晚于库末：以文件为准覆盖区段，不再 422、不要求拆分。"""
    base = (
        _HEADER
        + "2026-06-01,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,7995.00,证券买入\n"
        + "2026-06-02,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,6990.00,证券买入\n"
    ).encode()
    first = await _ready_preview(client, api_prefix, user, data=base)
    await _commit(client, api_prefix, user, first)
    await _drain_worker(settings)

    spanning = (
        _HEADER
        + "2026-05-01,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,8995.00,证券买入\n"
        + "2026-06-01,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,7990.00,证券买入\n"
        + "2026-06-02,600001,测试,买入,100,10.00,1000.00,5.00,0.00,0.00,6985.00,证券买入\n"
    ).encode()
    preview = await _ready_preview(client, api_prefix, user, data=spanning)
    assert preview["assumptions"]["coverage"]["mode"] == "replace"
    assert preview["assumptions"]["coverage"]["accounts"][0]["spans"] is True

    accepted = await _commit(client, api_prefix, user, preview)
    await _drain_worker(settings)
    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.json()["counts"] == {
        "parsed": 3,
        "inserted": 3,
        "skipped": 0,
        "quarantined": 0,
        "discarded": 0,
    }

    trades = (await client.get(f"{api_prefix}/trades", headers=user.headers)).json()["items"]
    assert sorted(item["trade_date"] for item in trades) == [
        "2026-05-01",
        "2026-06-01",
        "2026-06-02",
    ]


async def test_api_011_015_incremental_type_and_revoke_preserve_template(
    client, api_prefix, user, settings, owner_engine: AsyncEngine
) -> None:
    first = await _ready_preview(client, api_prefix, user)
    first_accepted = await _commit(client, api_prefix, user, first)
    await _drain_worker(settings)

    second_data = _bill() + _row(3, "基金转换").encode()
    second_preview = await _preview(client, api_prefix, user, data=second_data)
    assert second_preview["template"] is not None
    assert second_preview["stage"] == "types"
    assert {item["type_key"] for item in second_preview["types"] if item["needs_confirmation"]} == {
        "基金转换"
    }
    second = await _ready_preview(client, api_prefix, user, data=second_data)
    second_accepted = await _commit(client, api_prefix, user, second)
    await _drain_worker(settings)

    async with owner_engine.connect() as conn:
        type_rows = list(
            await conn.execute(
                text(
                    "SELECT type_key, origin_batch_id, last_write_batch_id "
                    "FROM ledger.import_bill_type WHERE user_id=:user_id ORDER BY type_key"
                ),
                {"user_id": user.user_id},
            )
        )
        template = (
            (
                await conn.execute(
                    text(
                        "SELECT id, origin_batch_id, last_batch_id FROM ledger.import_template "
                        "WHERE user_id=:user_id"
                    ),
                    {"user_id": user.user_id},
                )
            )
            .mappings()
            .one()
        )
    assert len(type_rows) == 2
    old_type = next(row for row in type_rows if row.type_key == "证券买入")
    new_type = next(row for row in type_rows if row.type_key == "基金转换")
    assert old_type.last_write_batch_id == first_accepted["batch_id"]
    assert new_type.last_write_batch_id == second_accepted["batch_id"]
    assert template["origin_batch_id"] == first_accepted["batch_id"]
    assert template["last_batch_id"] == second_accepted["batch_id"]

    revoked = await client.post(
        f"{api_prefix}/imports/{second_accepted['batch_id']}/revoke",
        headers={**user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"reason": "user_request"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["mapping_revoked"] == {
        "mapping_deleted": False,
        "types_deleted": 1,
    }
    async with owner_engine.connect() as conn:
        remaining_types = list(
            await conn.execute(
                text("SELECT type_key FROM ledger.import_bill_type WHERE user_id=:user_id"),
                {"user_id": user.user_id},
            )
        )
        last_batch_id = await conn.scalar(
            text("SELECT last_batch_id FROM ledger.import_template WHERE id=:template_id"),
            {"template_id": template["id"]},
        )
    assert [row.type_key for row in remaining_types] == ["证券买入"]
    assert last_batch_id == first_accepted["batch_id"]


async def test_api_010_011_huatai_newtype_fixture_only_confirms_new_type(
    client, api_prefix, user, settings
) -> None:
    first = await _ready_preview(
        client,
        api_prefix,
        user,
        data=_HUATAI_FIXTURE.read_bytes(),
        filename=_HUATAI_FIXTURE.name,
        content_type=_XLSX_CONTENT_TYPE,
    )
    await _commit(client, api_prefix, user, first)
    await _drain_worker(settings)

    second = await _preview(
        client,
        api_prefix,
        user,
        data=_HUATAI_NEWTYPE_FIXTURE.read_bytes(),
        filename=_HUATAI_NEWTYPE_FIXTURE.name,
        content_type=_XLSX_CONTENT_TYPE,
    )
    assert second["template"] is not None
    assert second["stage"] == "types"
    assert {item["type_key"] for item in second["types"] if item["needs_confirmation"]} == {
        "基金转换"
    }

    typed = await client.put(
        f"{api_prefix}/imports/previews/{second['preview_id']}/types",
        headers={**user.headers, "If-Match": str(second["mapping_version"])},
        json=_types_payload(second),
    )
    assert typed.status_code == 200, typed.text
    assert typed.json()["stage"] == "ready"
    accepted = await _commit(client, api_prefix, user, typed.json())
    await _drain_worker(settings)

    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["counts"] == {
        "parsed": 121,
        "inserted": 1,
        "skipped": 120,
        "quarantined": 1,
        "discarded": 0,
    }


async def test_legacy_and_v2_real_fixture_have_zero_downstream_diff(
    client, api_prefix, user, settings, account_factory, owner_engine: AsyncEngine
) -> None:
    """同一真实 fixture 走旧/新链路，流水、回合、净值与指标语义必须一致。"""
    legacy_user = await account_factory()
    data = _LEGACY_GOLDEN_FIXTURE.read_bytes()
    legacy_preview = await client.post(
        f"{api_prefix}/imports/preview",
        headers=legacy_user.headers,
        files={"file": (_LEGACY_GOLDEN_FIXTURE.name, data, _XLSX_CONTENT_TYPE)},
    )
    assert legacy_preview.status_code == 200, legacy_preview.text
    assert legacy_preview.json()["estimated"] == {
        "parsed": 175,
        "inserted": 175,
        "skipped": 0,
        "quarantined": 2,
        "discarded": 0,
        "estimated_available": True,
    }
    legacy_accepted = await client.post(
        f"{api_prefix}/imports",
        headers={**legacy_user.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
        json={"preview_id": legacy_preview.json()["preview_id"], "mode": "full"},
    )
    assert legacy_accepted.status_code == 202, legacy_accepted.text

    v2_preview = await _preview(
        client,
        api_prefix,
        user,
        data=data,
        filename=_LEGACY_GOLDEN_FIXTURE.name,
        content_type=_XLSX_CONTENT_TYPE,
    )
    # 股东账号不再是资金账号（不再映射），故比旧口径少一列。
    assert len(v2_preview["mapping_items"]) == 11
    currency_key = next(
        column["key"] for column in v2_preview["columns"] if column["name"] == "币种"
    )
    mapping_payload = _mapping_payload(v2_preview)
    mapping_payload["ignored"] = [currency_key]
    mapping_payload["acknowledge_unmapped"] = True
    mapped = await client.put(
        f"{api_prefix}/imports/previews/{v2_preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(v2_preview["mapping_version"])},
        json=mapping_payload,
    )
    assert mapped.status_code == 200, mapped.text
    typed = await client.put(
        f"{api_prefix}/imports/previews/{v2_preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(mapped.json()["mapping_version"])},
        json=_types_payload(mapped.json()),
    )
    assert typed.status_code == 200, typed.text
    v2_accepted = await _commit(client, api_prefix, user, typed.json())
    await _drain_worker(settings)

    expected_counts = {
        "parsed": 175,
        "inserted": 175,
        "skipped": 0,
        "quarantined": 2,
        "discarded": 0,
    }
    for account, accepted in ((legacy_user, legacy_accepted.json()), (user, v2_accepted)):
        detail = await client.get(
            f"{api_prefix}/imports/{accepted['batch_id']}", headers=account.headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["counts"] == expected_counts

    rebuild_ids: dict[Any, str] = {}
    for account in (legacy_user, user):
        rebuilt = await client.post(
            f"{api_prefix}/jobs/rebuild-period",
            headers={**account.headers, "Idempotency-Key": "key-" + uuid.uuid4().hex},
            json={"period": "2022-06", "include_exploratory": True},
        )
        assert rebuilt.status_code == 202, rebuilt.text
        rebuild_ids[account.user_id] = rebuilt.json()["job_id"]
    await _drain_worker(settings)
    for account in (legacy_user, user):
        job = await client.get(
            f"{api_prefix}/jobs/{rebuild_ids[account.user_id]}", headers=account.headers
        )
        assert job.status_code == 200, job.text
        assert job.json()["status"] == "succeeded", job.text

    queries = {
        "trades": """
            SELECT trade_date, security_code, security_name, side, quantity_units, price_units,
                   amount_units, commission_units, stamp_tax_units, other_fee_units,
                   cash_balance_units, cash_balance_available, money_fund_balance_units,
                   occurrence_idx, category, summary, line_no, dedupe_key
            FROM ledger.trade
            WHERE user_id=:user_id AND deleted_at IS NULL
            ORDER BY trade_date, line_no, security_code, occurrence_idx
        """,
        "rounds": """
            SELECT security_code, security_name, status, open_date, close_date, quantity_units,
                   cost_units, proceeds_units, dividend_units, pnl_units, return_rate_units,
                   holding_days, data_error, oversell_units, fills_count
            FROM review.round WHERE user_id=:user_id
            ORDER BY security_code, open_date, close_date, status, cost_units, proceeds_units
        """,
        "nav": """
            SELECT trade_date, nav_units, return_units, total_value_units, cashflow_units,
                   shares_units, status, blocked_reason
            FROM review.nav_daily WHERE user_id=:user_id ORDER BY trade_date
        """,
        "metrics": """
            SELECT metric_id, definition_version, params_hash, domain, period, scope, status,
                   sample_status, value_units, unit, precision, n, n_pending, ci_low_units,
                   ci_high_units, decomposition, assumptions, reason, data_as_of
            FROM review.metric_result WHERE user_id=:user_id AND period='2022-06'
            ORDER BY metric_id
        """,
    }
    async with owner_engine.connect() as conn:
        for name, query in queries.items():
            legacy_rows = [
                dict(row)
                for row in (
                    await conn.execute(text(query), {"user_id": legacy_user.user_id})
                ).mappings()
            ]
            v2_rows = [
                dict(row)
                for row in (await conn.execute(text(query), {"user_id": user.user_id})).mappings()
            ]
            assert v2_rows == legacy_rows, name
            if name in {"trades", "metrics"}:
                assert v2_rows, name


@pytest.mark.skipif(not os.environ.get("REAL_XLS"), reason="仅在 REAL_XLS 指定原文件时执行")
async def test_real_xls_preview_to_commit(client, api_prefix, user, settings) -> None:
    """用户提供的文本型 XLS：只在隔离库显式运行，不输出账号或原始账单行。"""
    path = Path(os.environ["REAL_XLS"])
    preview = await _preview(
        client,
        api_prefix,
        user,
        data=path.read_bytes(),
        filename="statement.xls",
        content_type="application/vnd.ms-excel",
    )
    assert len(preview["columns"]) == 22
    columns = {column["name"]: column for column in preview["columns"]}
    # 这份真实账单必须靠系统建议本身落位：发生金额 → amount（带符号），成交金额不抢占。
    assert columns["发生金额"]["suggested_field"] == "amount"
    assert columns["发生金额"]["suggested_options"] == {"amount_signed": True}
    assert columns["成交金额"]["suggested_field"] is None
    # 资金帐号（异体字）才是账户；沪/深股东代码不得被识别为账户。
    assert columns["资金帐号"]["suggested_field"] == "account"
    assert columns["股东代码"]["suggested_field"] is None
    assert preview["blocking"] == []
    payload = _mapping_payload(preview)
    # 原文件含多个资金子账户，按既定保护规则只映射一列其他费。
    payload["items"] = [
        item
        for item in payload["items"]
        if item["target_field"] != "other_fee" or item["column_key"] == columns["过户费"]["key"]
    ]
    mapped_keys = {item["column_key"] for item in payload["items"]}
    payload["ignored"] = [
        column["key"] for column in preview["columns"] if column["key"] not in mapped_keys
    ]
    payload["acknowledge_unmapped"] = True

    mapped = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/mapping",
        headers={**user.headers, "If-Match": str(preview["mapping_version"])},
        json=payload,
    )
    assert mapped.status_code == 200, mapped.text
    mapped_body = mapped.json()
    assert mapped_body["estimated"]["parsed"] == 42

    categories = {
        "证券买入清算": "buy",
        "证券卖出清算": "sell",
        "银证转入": "transfer",
        "跨行转入": "transfer",
        "跨行转出": "transfer",
        "利息归本": "interest",
        "红股派息": "dividend",
        "深圳市场股息红利个人所得税扣款": "fee",
        "资产账户币种开户": "other",
        "带资金补登": "other",
    }
    resolutions = [
        {
            "type_key": item["type_key"],
            "category": categories[item["type_key"]],
            "discard": False,
        }
        for item in mapped_body["types"]
    ]
    typed = await client.put(
        f"{api_prefix}/imports/previews/{preview['preview_id']}/types",
        headers={**user.headers, "If-Match": str(mapped_body["mapping_version"])},
        json={"resolutions": resolutions},
    )
    assert typed.status_code == 200, typed.text
    ready = typed.json()
    assert ready["stage"] == "ready"
    assert ready["can_commit"] is True
    assert ready["estimated"]["parsed"] + ready["estimated"]["quarantined"] == 47

    accepted = await _commit(client, api_prefix, user, ready)
    await _drain_worker(settings)
    detail = await client.get(f"{api_prefix}/imports/{accepted['batch_id']}", headers=user.headers)
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["status"] == "succeeded", detail.text
    counts = detail_body["counts"]
    assert counts["parsed"] + counts["quarantined"] == 47
    assert counts["inserted"] + counts["skipped"] + counts["discarded"] == counts["parsed"]

    quarantine = await client.get(
        f"{api_prefix}/imports/{accepted['batch_id']}/quarantine", headers=user.headers
    )
    assert quarantine.status_code == 200, quarantine.text
    assert len(quarantine.json()["items"]) == counts["quarantined"] + counts["discarded"]
    sys.stdout.write(
        "REAL_XLS_RESULT "
        + json.dumps(
            {
                "columns": len(preview["columns"]),
                "rows": 47,
                "types": len(mapped_body["types"]),
                "status": detail_body["status"],
                "counts": counts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
