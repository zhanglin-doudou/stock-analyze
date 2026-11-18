"""公开 ID 生成：同一毫秒内必须严格递增。

导入一张账单会在同一毫秒里生成上百个 ID；若随机位任意取值，`ORDER BY id` 就不再
等于文件行号顺序，同日流水会被打乱（2025-04-28 断链的根因）。
"""

from __future__ import annotations

import pytest
import stock_platform.ids as ids_mod


def test_ulid_monotonic_within_same_millisecond(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids_mod, "_last_ms", -1)
    monkeypatch.setattr(ids_mod, "_last_random", 0)
    monkeypatch.setattr(ids_mod.time, "time", lambda: 1_700_000_000.123)

    values = [ids_mod.new_ulid() for _ in range(500)]

    assert len(set(values)) == 500
    assert values == sorted(values)
    # 同一毫秒：时间前缀一致，顺序完全由递增的随机位决定。
    assert len({value[:10] for value in values}) == 1
