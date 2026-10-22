"""容差、标度与年化基数常量（Architecture §9.4；API-Catalog §4）。

金额/收益禁 float：所有比较一律以标度整数 + 显式容差进行。
"""

from decimal import Decimal
from typing import Final

# ── 容差（元 / 比率）───────────────────────────────────────
EPS_AMT: Final[Decimal] = Decimal("0.01")  # 金额容差
EPS_R: Final[Decimal] = Decimal("0.0001")  # 比率容差（1bp）
EPS_CUM: Final[Decimal] = Decimal("0.00000001")  # 标度整数最小单位

# ── 标度整数（Decimal 定点位数）─────────────────────────────
SCALE_AMOUNT: Final[int] = 2  # 金额 → 分
SCALE_PRICE: Final[int] = 6  # 价格 → 1e-6
SCALE_QTY: Final[int] = 4  # 数量 → 1e-4
SCALE_RATIO: Final[int] = 8  # 比率 → 1e-8

# ── 统计口径 ───────────────────────────────────────────────
A_SHUFFLE_DAYS: Final[int] = 244  # 年化基数（交易日）
ANNUALIZATION_BASE: Final[int] = A_SHUFFLE_DAYS
MIN_CONCLUSION_SAMPLE: Final[int] = 10  # n < 10 → insufficient，不出结论
MIN_CI_SAMPLE: Final[int] = 10  # bootstrap CI 最小样本

# ── 业务常量 ───────────────────────────────────────────────
IMPORT_PREVIEW_TTL_HOURS: Final[int] = 24
REPORT_SHARE_MAX_SECONDS: Final[int] = 2_592_000  # 30d
PAT_MAX_DAYS: Final[int] = 365
ACCESS_TOKEN_TTL_SECONDS: Final[int] = 900
DEFAULT_REFRESH_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 30
LOGIN_FAILURE_WINDOW_SECONDS: Final[int] = 300
LOGIN_FAILURE_MAX_ATTEMPTS: Final[int] = 5
FILE_MAX_BYTES: Final[int] = 20 * 1024 * 1024
CURSOR_DEFAULT_LIMIT: Final[int] = 50
CURSOR_MAX_LIMIT: Final[int] = 200
