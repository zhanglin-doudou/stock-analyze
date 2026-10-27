"""golden 容差唯一入口（§8.2/§8.5：容差断言只允许走这里，禁字面量）。

kind → 判定规则（全部整数域，eps 字符串用 Decimal 解析，禁止 float 中转）：
- ``exact``：精确相等（计数、状态、确定性 CI）；
- ``ratio_bp``：比率绝对容差，``eps`` 是比率值（如 "0.0001"=1bp），按 metric precision 折算成 units；
- ``cny`` / ``days`` / ``z``：绝对容差，``eps`` 按 precision 折算；
- ``fee``：``eps`` 绝对 + ``rel`` 相对（千分比字符串，如 "0.005"=0.5%）双口径取大者。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class Tolerance:
    kind: str
    abs_units: int
    rel_numerator: int = 0
    rel_denominator: int = 1

    def allows(self, actual: int, expected: int) -> bool:
        diff = abs(actual - expected)
        limit = self.abs_units
        if self.rel_denominator > 1 or self.rel_numerator > 0:
            rel_limit = abs(expected) * self.rel_numerator // self.rel_denominator
            limit = max(limit, rel_limit)
        return diff <= limit


def _eps_to_units(eps: str, precision: int) -> int:
    try:
        value = Decimal(eps)
    except InvalidOperation as exc:  # pragma: no cover - 配置错误
        raise ValueError(f"非法 eps: {eps!r}") from exc
    return int(value * (Decimal(10) ** precision))


def parse_tolerance(raw: dict[str, str], *, precision: int) -> Tolerance:
    """从 golden JSON 的 ``tolerance`` 段解析；``rel`` 缺省为 0。"""
    kind = raw.get("kind", "exact")
    eps = raw.get("eps", "0")
    rel = raw.get("rel", "0")
    if kind == "exact":
        return Tolerance(kind=kind, abs_units=0)
    abs_units = _eps_to_units(eps, precision)
    rel_dec = Decimal(rel)
    # rel 以小数字符串给出（"0.005"），转分数避免 float
    rel_scaled = rel_dec * (Decimal(10) ** 6)
    return Tolerance(
        kind=kind,
        abs_units=abs_units,
        rel_numerator=int(rel_scaled),
        rel_denominator=10**6,
    )


def assert_within_tolerance(
    actual: int | None,
    expected: int | None,
    *,
    tolerance: Tolerance,
    path: str,
) -> None:
    """None 只能对 None；否则按容差判定，失败信息带路径与双方值。"""
    if expected is None or actual is None:
        assert actual is None and expected is None, f"{path}: actual={actual!r} expected={expected!r}"
        return
    assert tolerance.allows(actual, expected), (
        f"{path}: |{actual} - {expected}| = {abs(actual - expected)} 超容差 "
        f"(abs≤{tolerance.abs_units}, rel={tolerance.rel_numerator}/{tolerance.rel_denominator})"
    )


__all__ = ["Tolerance", "assert_within_tolerance", "parse_tolerance"]
