"""回合洞察纯计算（Period2-09，设计 2026-11-04 §3）。

零 I/O、零时钟：日历、成交、行情、基准全部由调用方注入；所有价格为标度整数，
比率为 `Ratio` 同标度整数（1e-8）。本包只做纯函数，禁止读库/取系统时间。
"""

from __future__ import annotations

DEFINITION_VERSION = "1.0"
