"""标度整数值对象（Architecture §9.4）。

金额 → 分（2 位）、价格 → 1e-6、数量 → 1e-4、比率 → 1e-8。
**禁止 float**：内部一律 int 标度单位，外部一律定点字符串。
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Self

from stock_contracts.constants import EPS_R, SCALE_AMOUNT, SCALE_PRICE, SCALE_QTY, SCALE_RATIO

_QUANT = {
    0: Decimal(1),
    2: Decimal("0.01"),
    4: Decimal("0.0001"),
    6: Decimal("0.000001"),
    8: Decimal("0.00000001"),
}


class Scaled:
    """定点标度整数基类。不可变、可比较、可加减。"""

    __slots__ = ("units",)
    # 显式类级注解：`__slots__` 里声明的实例属性 mypy 默认看不到，补注解后可见（运行时等价）。
    units: int
    scale: int = 0

    def __init__(self, units: int) -> None:
        if isinstance(units, bool) or not isinstance(units, int):
            raise TypeError("units 必须是 int（标度整数）")
        object.__setattr__(self, "units", units)

    # ── 构造 ────────────────────────────────────────────
    @classmethod
    def from_decimal(cls, value: Decimal) -> Self:
        # mypy：Decimal 与 float 是「不相交基类」，直接 isinstance(value, float) 会被判不可达；
        # 经 object 中转只为保留运行时防御分支（未标注的调用方仍可能误传 float），行为不变。
        raw: object = value
        if isinstance(raw, float):  # 防御：误传 float 直接拒绝
            raise TypeError("禁止使用 float 构造标度整数")
        try:
            d = Decimal(value)
        except (InvalidOperation, TypeError) as exc:  # pragma: no cover - 防御分支
            raise ValueError(f"无法解析为定点数: {value!r}") from exc
        return cls(int(d.scaleb(cls.scale).to_integral_value(rounding=ROUND_HALF_UP)))

    @classmethod
    def from_str(cls, raw: str) -> Self:
        return cls.from_decimal(Decimal(raw))

    @classmethod
    def zero(cls) -> Self:
        return cls(0)

    # ── 输出 ────────────────────────────────────────────
    def to_decimal(self) -> Decimal:
        return Decimal(self.units).scaleb(-self.scale)

    def quantize(self) -> Decimal:
        return self.to_decimal().quantize(_QUANT[self.scale])

    def __str__(self) -> str:
        return f"{self.quantize():f}"

    def as_api(self) -> str:
        """API 边界输出：定点字符串（金额 2 位）。"""
        return str(self)

    def to_float(self) -> float:
        """仅用于展示/统计输出，不参与金额计算。"""
        return float(self.to_decimal())

    # ── 运算 ────────────────────────────────────────────
    def _check(self, other: Scaled) -> None:
        if not isinstance(other, type(self)):
            raise TypeError(f"类型不匹配: {type(self).__name__} vs {type(other).__name__}")

    def __add__(self, other: Self) -> Self:
        self._check(other)
        return type(self)(self.units + other.units)

    def __sub__(self, other: Self) -> Self:
        self._check(other)
        return type(self)(self.units - other.units)

    def __neg__(self) -> Self:
        return type(self)(-self.units)

    def __abs__(self) -> Self:
        return type(self)(abs(self.units))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, type(self)):
            return self.units == other.units
        return NotImplemented

    def __lt__(self, other: Self) -> bool:
        self._check(other)
        return self.units < other.units

    def __le__(self, other: Self) -> bool:
        self._check(other)
        return self.units <= other.units

    def __gt__(self, other: Self) -> bool:
        self._check(other)
        return self.units > other.units

    def __ge__(self, other: Self) -> bool:
        self._check(other)
        return self.units >= other.units

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.units))

    def __bool__(self) -> bool:
        return self.units != 0

    def __repr__(self) -> str:  # pragma: no cover - 调试
        return f"{type(self).__name__}({self.units})[{self}]"

    def is_zero(self) -> bool:
        return self.units == 0

    def is_negative(self) -> bool:
        return self.units < 0

    def is_close(self, other: Self, tolerance: Decimal = EPS_R) -> bool:
        return abs(self.to_decimal() - other.to_decimal()) <= tolerance


class Money(Scaled):
    """金额（分）。"""

    scale = SCALE_AMOUNT


class Price(Scaled):
    """价格（1e-6）。"""

    scale = SCALE_PRICE


class Qty(Scaled):
    """数量（1e-4）。"""

    scale = SCALE_QTY


class Ratio(Scaled):
    """比率（1e-8）。"""

    scale = SCALE_RATIO

    def as_pct(self, digits: int = 2) -> str:
        """展示用百分比字符串（不参与计算）。"""
        pct = (self.to_decimal() * Decimal(100)).quantize(
            Decimal(1).scaleb(-digits), rounding=ROUND_HALF_EVEN
        )
        return f"{pct:f}%"


def money_of(value: str | Decimal | int) -> Money:
    if isinstance(value, int) and not isinstance(value, bool):
        return Money.from_decimal(Decimal(value))
    if isinstance(value, str):
        return Money.from_str(value)
    if isinstance(value, Decimal):
        return Money.from_decimal(value)
    raise TypeError("money_of 只接受 str/Decimal/int")


def price_of(value: str | Decimal | int) -> Price:
    # `Price` 因基类 `Scaled` 定义 `__slots__` 被 mypy 视作不相交基类，直接对联合类型做
    # isinstance 会被判不可达；经 object 中转保留「已传入 Price 则原样返回」的运行时语义。
    raw: object = value
    if isinstance(raw, Price):
        return raw
    return Price.from_decimal(Decimal(value))


def qty_of(value: str | Decimal | int) -> Qty:
    return Qty.from_decimal(Decimal(value))


def ratio_of(value: str | Decimal | int) -> Ratio:
    return Ratio.from_decimal(Decimal(value))


def mul_ratio(base: Money, ratio: Ratio) -> Money:
    """金额 × 比率 → 金额（银行家舍入到分）。"""
    return Money.from_decimal(base.to_decimal() * ratio.to_decimal())


def money_mul(qty: Qty, price: Price) -> Money:
    """数量 × 价格 → 金额（出现金额唯一入口）。"""
    return Money.from_decimal(qty.to_decimal() * price.to_decimal())


def ratio_between(part: Money, whole: Money) -> Ratio:
    """比率 = 部分 / 整体；整体为 0 时返回 0（调用方负责语义）。"""
    if whole.is_zero():
        return Ratio.zero()
    return Ratio.from_decimal(part.to_decimal() / whole.to_decimal())


def ratio_div(numer: Ratio, denom: Ratio) -> Ratio:
    if denom.is_zero():
        raise ZeroDivisionError("比率分母为 0")
    return Ratio.from_decimal(numer.to_decimal() / denom.to_decimal())


def sum_money(items: Any) -> Money:
    total = Money.zero()
    for item in items:
        if not isinstance(item, Money):
            raise TypeError("sum_money 只接受 Money")
        total = total + item
    return total
