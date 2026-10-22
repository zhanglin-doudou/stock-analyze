"""stock_core.values 标度整数单测（Architecture §9.4）。

目标：把 values.py 的语句/分支覆盖率打到 100%。所有金额类断言一律走标度整数与
定点字符串，绝不用 float 参与比较。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from stock_contracts.constants import SCALE_AMOUNT, SCALE_PRICE, SCALE_QTY, SCALE_RATIO
from stock_core.values import (
    Money,
    Price,
    Qty,
    Ratio,
    Scaled,
    money_mul,
    money_of,
    mul_ratio,
    price_of,
    qty_of,
    ratio_between,
    ratio_div,
    ratio_of,
    sum_money,
)

# ── 构造 ────────────────────────────────────────────────────


def test_scales_match_contracts() -> None:
    assert Money.scale == SCALE_AMOUNT == 2
    assert Price.scale == SCALE_PRICE == 6
    assert Qty.scale == SCALE_QTY == 4
    assert Ratio.scale == SCALE_RATIO == 8


def test_construct_from_int_units() -> None:
    assert Money(124).units == 124
    assert Qty(1).units == 1


@pytest.mark.parametrize("bad", [True, False, "5", 5.0, None])
def test_construct_rejects_non_int_units(bad: object) -> None:
    with pytest.raises(TypeError):
        Money(bad)  # type: ignore[arg-type]


def test_from_decimal_half_up() -> None:
    # 0.005 元 → 半分，ROUND_HALF_UP → 1 分
    assert Money.from_decimal(Decimal("0.005")).units == 1
    assert Money.from_decimal(Decimal("1.235")).units == 124
    assert Price.from_decimal(Decimal("1.2345678")).units == 1234568
    assert Qty.from_decimal(Decimal("0.00005")).units == 1
    assert Ratio.from_decimal(Decimal("0.000000005")).units == 1


def test_from_decimal_rejects_float() -> None:
    with pytest.raises(TypeError):
        Money.from_decimal(3.14)  # type: ignore[arg-type]


def test_from_str_roundtrip() -> None:
    assert Money.from_str("2.00").units == 200
    assert Qty.from_str("100").units == 1_000_000


def test_zero() -> None:
    assert Money.zero().units == 0
    assert Money.zero().is_zero()


def test_scaled_base_scale_zero() -> None:
    base = Scaled(5)
    assert base.scale == 0
    assert Scaled.from_decimal(Decimal("1.5")).units == 2  # HALF_UP


# ── 输出 ────────────────────────────────────────────────────


def test_to_decimal_quantize_and_str() -> None:
    m = Money(124)
    assert m.to_decimal() == Decimal("1.24")
    assert m.quantize() == Decimal("1.24")
    assert str(m) == "1.24"
    assert m.as_api() == "1.24"


def test_to_float_is_display_only() -> None:
    assert Money(124).to_float() == 1.24


def test_repr() -> None:
    assert "Money(124)" in repr(Money(124))


# ── 运算 ────────────────────────────────────────────────────


def test_add_sub_neg_abs() -> None:
    assert (Money(100) + Money(24)).units == 124
    assert (Money(100) - Money(24)).units == 76
    assert (-Money(100)).units == -100
    assert abs(Money(-100)).units == 100


def test_type_mismatch_raises() -> None:
    with pytest.raises(TypeError):
        _ = Money(1) + Qty(1)
    with pytest.raises(TypeError):
        _ = Money(1) < Qty(1)


def test_equality_and_notimplemented() -> None:
    assert Money(1) == Money(1)
    assert Money(1) != Money(2)
    # 与异类型比较 → __eq__ 返回 NotImplemented，Python 回退为 False
    assert (Money(1) == Qty(1)) is False
    assert (Money(1) == 1) is False


def test_ordering() -> None:
    assert Money(1) < Money(2)
    assert Money(2) <= Money(2)
    assert Money(3) > Money(2)
    assert Money(2) >= Money(2)


def test_hash_and_bool() -> None:
    assert hash(Money(1)) == hash(Money(1))
    assert bool(Money(5)) is True
    assert bool(Money(0)) is False


def test_sign_helpers_and_close() -> None:
    assert Money(0).is_zero() is True
    assert Money(-1).is_negative() is True
    assert Money(1).is_negative() is False
    assert Money(100).is_close(Money(100)) is True
    assert Money(100).is_close(Money(101)) is False


def test_ratio_as_pct() -> None:
    assert Ratio.from_decimal(Decimal("0.1234")).as_pct() == "12.34%"


# ── 工厂函数 ────────────────────────────────────────────────


def test_money_of_accepts_int_str_decimal() -> None:
    assert money_of(500).units == 50_000
    assert money_of("12.34").units == 1234
    assert money_of(Decimal("1.00")).units == 100


def test_money_of_rejects_float_and_others() -> None:
    with pytest.raises(TypeError):
        money_of(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        money_of(None)  # type: ignore[arg-type]


def test_price_qty_ratio_of() -> None:
    existing = Price(1_000_000)
    assert price_of(existing) is existing
    assert price_of("1.5").units == 1_500_000
    assert qty_of(100).units == 1_000_000
    assert ratio_of("0.5").units == 50_000_000


# ── 组合运算 ────────────────────────────────────────────────


def test_money_mul() -> None:
    assert money_mul(qty_of(100), price_of("1.5")).units == 15_000


def test_mul_ratio() -> None:
    assert mul_ratio(Money(100), ratio_of("0.25")).units == 25


def test_ratio_between() -> None:
    assert ratio_between(Money(50), Money(100)).units == 50_000_000
    # whole 为 0 → 返回 0 而非除零
    assert ratio_between(Money(1), Money.zero()).is_zero()


def test_ratio_div() -> None:
    assert ratio_div(ratio_of("0.5"), ratio_of("0.25")).units == 200_000_000
    with pytest.raises(ZeroDivisionError):
        ratio_div(ratio_of("0.5"), Ratio.zero())


def test_sum_money() -> None:
    assert sum_money([Money(1), Money(2), Money(3)]).units == 6
    assert sum_money([]).units == 0
    with pytest.raises(TypeError):
        sum_money([Money(1), "x"])  # type: ignore[list-item]
