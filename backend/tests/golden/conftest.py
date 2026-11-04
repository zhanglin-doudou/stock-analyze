"""金标用例的夹具覆盖：指标三方比对不连库。

与 `backend/tests/unit/core` 同理：根 conftest 的 autouse `_clean_db` 会让纯计算用例
依赖 PostgreSQL，而变异测试在无库的 CI 环境只跑 `unit/core` + `golden`。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """空实现：覆盖根 conftest 的清库夹具（不连库、不清库）。"""
