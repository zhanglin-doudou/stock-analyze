"""API-090/091 逐笔理由 + `/trades` 过滤/内嵌契约（设计 2026-11-04 §6.1/§6.2/§6.6）。

覆盖 AC-R1~R6：
* 新建 201 / 更新 200 / If-Match 版本冲突 409 / 更新缺 If-Match 422；
* 非法 code/方向/标签/情绪 → 422；跨账号/非 buy|sell/已删除 → 404；admin → 403；
* `has_reason`/`reason_code` 过滤与 `TradeOut.reason` 内嵌；`/trades` ETag 含 reason.version；
* 审计只落 dedupe_key/reason_code/action，`reason_text` 不落库、不落日志。
"""

from __future__ import annotations

import logging
import re
from datetime import date

import pytest
from sqlalchemy import text
from stock_contracts.errors import ErrorCode
from test_api_028_031_rounds import _seed_trade

pytestmark = pytest.mark.api

_ETAG_RE = re.compile(r'^"(\d+):(\d+)"$')


async def _seed_via(engine, user_id, *, side: str = "buy", trade_date=None) -> str:
    return await _seed_trade(
        engine,
        user_id=user_id,
        trade_date=trade_date or date(2026, 6, 2),
        side=side,
        quantity_units=1_000_000,
        price_units=100_000_000,
        amount_units=-1_000_000 if side == "buy" else 1_000_000,
    )


async def _put_reason(client, api_prefix, headers, trade_id: str, *, body: dict, if_match=None):
    extra = {"If-Match": if_match} if if_match is not None else {}
    return await client.put(
        f"{api_prefix}/trades/{trade_id}/reason", headers={**headers, **extra}, json=body
    )


async def test_api_090_create_requires_server_clock_and_returns_etag(
    client, api_prefix, user, owner_engine
):
    trade_id = await _seed_via(owner_engine, user.user_id)
    resp = await _put_reason(
        client,
        api_prefix,
        user.headers,
        trade_id,
        body={
            "reason_code": "bottom_fish",
            "reason_text": "跌到年线附近，按计划分批",
            "emotion_code": "anxious",
            "tags": ["年线", "分批"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reason_code"] == "bottom_fish"
    assert body["reason_label"] == "抄底"
    assert body["version"] == 1
    assert body["late_annotated"] is True  # d0=06-02，标注日 2026-11-05 已晚于 20 TD
    assert resp.headers["ETag"] == '"1"'
    assert "annotated_at" in body


async def test_api_090_update_requires_matching_if_match(client, api_prefix, user, owner_engine):
    trade_id = await _seed_via(owner_engine, user.user_id)
    first = await _put_reason(
        client, api_prefix, user.headers, trade_id, body={"reason_code": "plan_build"}
    )
    assert first.status_code == 201

    missing = await _put_reason(
        client, api_prefix, user.headers, trade_id, body={"reason_code": "chase"}
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == ErrorCode.VALIDATION_ERROR

    stale = await _put_reason(
        client,
        api_prefix,
        user.headers,
        trade_id,
        body={"reason_code": "chase"},
        if_match='"9"',
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == ErrorCode.VERSION_CONFLICT

    updated = await _put_reason(
        client,
        api_prefix,
        user.headers,
        trade_id,
        body={"reason_code": "chase", "reason_text": "突破年线追入"},
        if_match='"1"',
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["version"] == 2
    assert body["reason_code"] == "chase"
    assert updated.headers["ETag"] == '"2"'


@pytest.mark.parametrize(
    ("body", "expected_field"),
    [
        ({"reason_code": "take_profit"}, "reason_code"),  # 卖出码用于买入
        ({"reason_code": "plan_build", "emotion_code": "unknown"}, "emotion_code"),
        ({"reason_code": "plan_build", "tags": [str(i) for i in range(9)]}, "tags"),
        ({"reason_code": "plan_build", "reason_text": "x" * 501}, "reason_text"),
    ],
)
async def test_api_090_validation_errors_422(
    client, api_prefix, user, owner_engine, body, expected_field
):
    trade_id = await _seed_via(owner_engine, user.user_id)
    resp = await _put_reason(client, api_prefix, user.headers, trade_id, body=body)
    assert resp.status_code == 422, resp.text
    payload = resp.json()
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert any(err["field"] == expected_field for err in payload["errors"]), payload


async def test_api_090_404_for_cross_user_non_trade_and_deleted(
    client, api_prefix, user, account_factory, owner_engine
):
    other = await account_factory()
    trade_id = await _seed_via(owner_engine, user.user_id)
    body = {"reason_code": "plan_build"}

    cross = await _put_reason(client, api_prefix, other.headers, trade_id, body=body)
    assert cross.status_code == 404
    assert cross.json()["code"] == ErrorCode.NOT_FOUND

    transfer = await _seed_via(owner_engine, user.user_id, side="unknown")
    non_trade = await _put_reason(client, api_prefix, user.headers, transfer, body=body)
    assert non_trade.status_code == 404

    deleted = await client.delete(f"{api_prefix}/trades/{trade_id}", headers=user.headers)
    assert deleted.status_code == 204
    after_delete = await _put_reason(client, api_prefix, user.headers, trade_id, body=body)
    assert after_delete.status_code == 404


async def test_api_090_admin_forbidden(client, api_prefix, admin, owner_engine):
    trade_id = await _seed_via(owner_engine, admin.user_id)
    resp = await _put_reason(
        client, api_prefix, admin.headers, trade_id, body={"reason_code": "plan_build"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == ErrorCode.ADMIN_CANNOT_READ_TRADES


async def test_api_091_delete_is_idempotent_and_version_guarded(
    client, api_prefix, user, owner_engine
):
    trade_id = await _seed_via(owner_engine, user.user_id)
    created = await _put_reason(
        client, api_prefix, user.headers, trade_id, body={"reason_code": "plan_build"}
    )
    assert created.status_code == 201

    conflict = await client.delete(
        f"{api_prefix}/trades/{trade_id}/reason",
        headers={**user.headers, "If-Match": '"9"'},
    )
    assert conflict.status_code == 409

    deleted = await client.delete(
        f"{api_prefix}/trades/{trade_id}/reason",
        headers={**user.headers, "If-Match": '"1"'},
    )
    assert deleted.status_code == 204
    again = await client.delete(f"{api_prefix}/trades/{trade_id}/reason", headers=user.headers)
    assert again.status_code == 204


async def test_api_090_cross_user_delete_is_404(
    client, api_prefix, user, account_factory, owner_engine
):
    other = await account_factory()
    trade_id = await _seed_via(owner_engine, user.user_id)
    resp = await client.delete(f"{api_prefix}/trades/{trade_id}/reason", headers=other.headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == ErrorCode.NOT_FOUND


async def test_trades_list_embeds_reason_filters_and_etag(client, api_prefix, user, owner_engine):
    buy_id = await _seed_via(owner_engine, user.user_id)
    sell_id = await _seed_via(owner_engine, user.user_id, side="sell")
    created = await _put_reason(
        client, api_prefix, user.headers, buy_id, body={"reason_code": "bottom_fish"}
    )
    assert created.status_code == 201

    all_rows = await client.get(f"{api_prefix}/trades", headers=user.headers)
    assert all_rows.status_code == 200
    items = {item["trade_id"]: item for item in all_rows.json()["items"]}
    assert items[buy_id]["reason"]["reason_code"] == "bottom_fish"
    assert items[sell_id]["reason"] is None

    with_reason = await client.get(
        f"{api_prefix}/trades", headers=user.headers, params={"has_reason": "true"}
    )
    assert [item["trade_id"] for item in with_reason.json()["items"]] == [buy_id]

    without_reason = await client.get(
        f"{api_prefix}/trades", headers=user.headers, params={"has_reason": "false"}
    )
    assert [item["trade_id"] for item in without_reason.json()["items"]] == [sell_id]

    by_code = await client.get(
        f"{api_prefix}/trades", headers=user.headers, params={"reason_code": "bottom_fish"}
    )
    assert [item["trade_id"] for item in by_code.json()["items"]] == [buy_id]

    empty = await client.get(
        f"{api_prefix}/trades", headers=user.headers, params={"reason_code": "stop_loss"}
    )
    assert empty.json()["items"] == []

    detail = await client.get(f"{api_prefix}/trades/{buy_id}", headers=user.headers)
    match = _ETAG_RE.match(detail.headers["ETag"])
    assert match is not None
    assert match.group(2) == "1"  # 第二段是 reason.version（见设计 §6.6）


async def test_api_090_audit_and_logs_do_not_contain_reason_text(
    client, api_prefix, user, owner_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    trade_id = await _seed_via(owner_engine, user.user_id)
    secret = "秘密理由-不应落日志"
    resp = await _put_reason(
        client,
        api_prefix,
        user.headers,
        trade_id,
        body={"reason_code": "plan_build", "reason_text": secret},
    )
    assert resp.status_code == 201
    assert secret not in caplog.text

    async with owner_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "select action, target_id, meta from platform.audit_log "
                    "where action like 'trade_reason.%'"
                )
            )
        ).all()
    assert rows, "理由写操作必须留审计"
    for action, _target, meta in rows:
        assert action in {"trade_reason.write", "trade_reason.delete"}
        assert secret not in str(meta)
        assert meta.get("reason_code") == "plan_build"
        assert meta.get("trade_dedupe_key")
