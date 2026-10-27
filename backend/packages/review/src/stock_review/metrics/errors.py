"""指标注册期错误（Period2-05）。

这些错误只会在 `build_registry()` / 引擎执行期暴露，属于**代码配置问题**：
- `RegistryError`：def 缺字段、id/version 非法、dataset 不在白名单、estimator 未注册等；
- `CycleError`：MetricDep 成环（Kahn 拓扑失败）。

API 层不得把两者直接透给用户（会泄露内部实现）；路由装配期 fail-fast，
请求期由 `application/metrics.py` 转成 `ApiError(INTERNAL_ERROR)`。
"""

from __future__ import annotations


class RegistryError(RuntimeError):
    """指标注册表校验失败（def 配置错误，必须修代码）。"""


class CycleError(RegistryError):
    """指标依赖成环（A→B→A）。"""


__all__ = ["CycleError", "RegistryError"]
