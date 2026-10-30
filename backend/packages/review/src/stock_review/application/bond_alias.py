"""可转债发债代码 → 上市代码的名称归并（纯函数，无 I/O、无 wall-clock）。

背景（设计 `2026-10-29-subscription-isolation-and-cbond-rounds.md` D3）：
可转债**申购（发债）代码**（如 754019「曙26发债」）与**上市代码**（如 113xxx
「曙26转债」）不同；按 `security_code` 分组的 FIFO 回合/持仓/估值永远配不成对。
本模块用名称 stem（去尾部「发债」/「转债」）把发债代码归并到上市代码：

- 仅当同一 stem 对应的上市转债代码**唯一**时归并（`alias[发债代码] = 上市代码`）；
- 冲突（同名多只转债）或无对应（未上市/已强赎无交易）时不归并，进 `unresolved`
  由调用方留痕（日志/监控），发债代码按原样参与计算；
- 归并表从 trades 派生，不落库；回合 ID / holdings / 行情请求统一用 canonical 代码。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from stock_platform.symbols import is_convertible_bond

_BOND_NAME_SUFFIXES: tuple[str, ...] = ("发债", "转债")


def extract_bond_stem(name: str) -> str | None:
    """「曙26发债」/「曙26转债」→「曙26」；不以发债/转债结尾 → None。"""
    text = "".join(name.split())
    for suffix in _BOND_NAME_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return None


def build_bond_alias(
    pairs: Iterable[tuple[str, str]],
) -> tuple[dict[str, str], list[str]]:
    """`(security_code, security_name)` 序列 → (别名映射, 未归并发债代码列表)。

    - 发债代码：任一名称以「发债」结尾；
    - 上市代码：任一名称以「转债」结尾且 `is_convertible_bond(code)`；
    - 发债代码的 stem 对应**唯一**上市代码（排除自身）时归并，否则进 unresolved。
    """
    subscription_stem: dict[str, str] = {}
    listed_by_stem: dict[str, set[str]] = {}
    for code, name in pairs:
        if not code or not name:
            continue
        stem = extract_bond_stem(name)
        if stem is None:
            continue
        compact = "".join(name.split())
        if compact.endswith("发债"):
            subscription_stem.setdefault(code, stem)
        elif is_convertible_bond(code):
            listed_by_stem.setdefault(stem, set()).add(code)
    alias: dict[str, str] = {}
    unresolved: list[str] = []
    for code, stem in subscription_stem.items():
        candidates = listed_by_stem.get(stem, set()) - {code}
        if len(candidates) == 1:
            alias[code] = next(iter(candidates))
        else:
            unresolved.append(code)
    return alias, sorted(unresolved)


def canonical_code(alias: Mapping[str, str], code: str) -> str:
    """发债代码 → 上市代码；无映射原样返回。"""
    return alias.get(code, code)


__all__ = ["build_bond_alias", "canonical_code", "extract_bond_stem"]
