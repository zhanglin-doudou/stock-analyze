"""严格查询参数校验（API-Catalog §0.2 / 用例 UNKNOWN_QUERY_PARAM）。

未登记的 query 参数一律 400，避免“静默忽略”带来的错误报表。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from stock_contracts.errors import ApiError, ErrorCode


def _collect_allowed(dependant: Any, into: set[str]) -> None:
    for param in dependant.query_params:
        name = getattr(param, "name", None) or getattr(param, "_name", None)
        if name:
            into.add(name)
        alias = getattr(param, "alias", None)
        if alias:
            into.add(alias)
    for sub in dependant.dependencies:
        _collect_allowed(sub, into)


class StrictQueryRoute(APIRoute):
    """把未知 query 参数变成显式错误。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        allowed: set[str] = set()
        _collect_allowed(self.dependant, allowed)
        self._allowed_query_params = allowed

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Any]]:
        handler = super().get_route_handler()

        async def strict_handler(request: Request) -> Any:
            unknown = [key for key in request.query_params if key not in self._allowed_query_params]
            if unknown:
                raise ApiError(
                    ErrorCode.UNKNOWN_QUERY_PARAM,
                    detail=f"未知查询参数: {', '.join(sorted(unknown))}",
                )
            return await handler(request)

        return strict_handler
