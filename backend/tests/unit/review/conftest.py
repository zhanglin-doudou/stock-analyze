"""review 单测的夹具覆盖：纯计算用例不连库。

与 `backend/tests/unit/core`、`backend/tests/golden` 同理：根 `backend/tests/conftest.py`
的 `_clean_db` 是 autouse（每个用例前 TRUNCATE），而本目录只测 `stock_review` 纯函数
（无 I/O、无 wall-clock），既不需要清库，也不该在无 PostgreSQL 的 CI（pr-fast）里失败。

同名夹具在本目录优先级更高，因此根 conftest 的 autouse 版本不会对这里生效。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """空实现：覆盖根 conftest 的清库夹具（不连库、不清库）。"""
