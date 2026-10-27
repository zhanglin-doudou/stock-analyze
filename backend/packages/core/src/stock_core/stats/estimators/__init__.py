"""统计量注册表（Period2-05，E-05）：包内自动发现，放文件即注册。

def 通过 `ci_stat_id` 引用统计量；新增统计量只需在本包内新增一个模块（文件名不以
`_` 开头）并在函数上使用 `@register(...)`，引擎与端点零改动（X-1 判据）。

统计量签名：`(samples: Sequence[tuple[int, ...]]) -> int`，返回标度整数。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Sequence

Estimator = Callable[[Sequence[tuple[int, ...]]], int]

_REGISTRY: dict[str, Estimator] = {}


def register(stat_id: str) -> Callable[[Estimator], Estimator]:
    """装饰器：把统计量登记到 `stat_id`（重复注册视为配置错误）。"""

    def decorator(func: Estimator) -> Estimator:
        if stat_id in _REGISTRY:
            raise ValueError(f"stat already registered: {stat_id}")
        _REGISTRY[stat_id] = func
        return func

    return decorator


def stats_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get_stats(stat_id: str) -> Estimator:
    try:
        return _REGISTRY[stat_id]
    except KeyError as exc:
        raise KeyError(f"unknown stat: {stat_id}") from exc


def _discover() -> None:
    """导入本包内所有非私有模块，触发其 `@register` 副作用。"""
    public_names = [
        module.name for module in pkgutil.iter_modules(__path__) if not module.name.startswith("_")
    ]
    for name in public_names:
        importlib.import_module(f"{__name__}.{name}")


_discover()
_BUILTIN: dict[str, Estimator] = dict(_REGISTRY)


def reset_registry_for_tests() -> None:
    """仅测试使用：清空注册表并恢复内置统计量（便于验证重复注册/未知统计量分支）。"""
    _REGISTRY.clear()
    _REGISTRY.update(_BUILTIN)


from stock_core.stats.estimators.mean import mean_stat  # noqa: E402  （发现后再导出）

__all__ = [
    "Estimator",
    "get_stats",
    "mean_stat",
    "register",
    "reset_registry_for_tests",
    "stats_ids",
]
