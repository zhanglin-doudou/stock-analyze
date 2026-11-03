"""core 单测的夹具覆盖：纯计算用例不连库。

根 `backend/tests/conftest.py` 的 `_clean_db` 是 autouse（每个用例前 TRUNCATE），
用于 API/集成/越权用例。而本目录只测 `stock_core` 纯函数，既不需要清库，也不该
在无数据库环境下失败——变异测试（`make mutation-full`）正是只选
`backend/tests/unit/core` + `backend/tests/golden`，在 CI 里没有 PostgreSQL 服务。

同名夹具在本目录优先级更高，因此根 conftest 的 autouse 版本不会对这里生效。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """空实现：覆盖根 conftest 的清库夹具（不连库、不清库）。"""
