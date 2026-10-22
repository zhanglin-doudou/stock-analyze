"""契约测试不依赖数据库：覆盖父级 conftest 的 autouse「清库」夹具。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_db() -> None:
    """backend/tests/conftest.py 的同名 autouse 夹具会连库；契约测试纯内存，覆盖为空操作。"""
    return None
