"""确定性回合 ID 单测（INTERFACE §3.1 / §3.7）。

同一用户 + 证券 + FIFO 稳定键 → 同一 ID；不同任一输入 → 不同 ID；
长度受 DB 列 `String(40)` 约束；重复 rebuild 靠它保证幂等插入。
"""

from __future__ import annotations

import hashlib
import uuid

from stock_review.application.rebuild import build_round_id

USER_ID = uuid.UUID("00000000-0000-4000-8000-00000000000a")
OTHER_USER = uuid.UUID("00000000-0000-4000-8000-00000000000b")


def test_round_id_is_deterministic() -> None:
    key = "trd_buy_1:trd_sell_1"
    first = build_round_id(USER_ID, "600519", key)
    second = build_round_id(USER_ID, "600519", key)
    assert first == second


def test_round_id_matches_documented_formula() -> None:
    key = "trd_buy_1:trd_sell_1"
    expected_digest = hashlib.sha256(f"{USER_ID}|600519|{key}".encode()).hexdigest()[:32]
    assert build_round_id(USER_ID, "600519", key) == f"rnd_{expected_digest}"


def test_round_id_fits_column_width() -> None:
    round_id = build_round_id(USER_ID, "600519", "open")
    assert round_id.startswith("rnd_")
    assert len(round_id) == 36
    assert len(round_id) <= 40
    assert all(char in "0123456789abcdef" for char in round_id.removeprefix("rnd_"))


def test_round_id_varies_by_every_input_component() -> None:
    base = build_round_id(USER_ID, "600519", "k")
    assert base != build_round_id(OTHER_USER, "600519", "k")
    assert base != build_round_id(USER_ID, "000001", "k")
    assert base != build_round_id(USER_ID, "600519", "k2")


def test_open_and_closed_rounds_do_not_collide() -> None:
    assert build_round_id(USER_ID, "600519", "trd_buy_1:trd_sell_1") != build_round_id(
        USER_ID, "600519", "trd_buy_1:open"
    )
