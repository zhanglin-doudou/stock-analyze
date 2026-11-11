"""oracle 用例的夹具覆盖：纯计算/静态校验不连库。

与 `backend/tests/unit/core`、`backend/tests/golden` 同理：根 conftest 的 autouse
`_clean_db` 会让纯计算用例依赖 PostgreSQL，而 pr-fast 的
`pytest backend/tests/golden backend/tests/oracle` 在无库环境下执行。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """空实现：覆盖根 conftest 的清库夹具（不连库、不清库）。"""
