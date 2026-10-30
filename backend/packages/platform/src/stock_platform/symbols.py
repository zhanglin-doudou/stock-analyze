"""6 位证券代码的品种分类（单一真相来源）。

`market_live`（platform 层）与 `stock_review` 的估值/兜底逻辑共用本模块，避免
「行情源支持哪些品种」与「估值时哪些品种可以按买入价兜底」两处口径漂移。

设计见 `docs/architecture/2026-10-29-money-fund-and-bond-market-data.md` §6.1：

- `"stock"`：A 股主板/科创板/创业板（沪 600/601/603/605/688/689，深 000/001/002/
  003/300/301）。东风东财接口的 market_code 只看首位是否为 6，深市非 A 股会静默
  返回对应品种数据，必须先分类再路由。
- `"fund"`：场内基金（沪 5 开头 ETF/LOF/REITs 全系；深 15/16/18 开头）。
- `"bond"`：沪深交易所债券（国债/企业债/公司债/可转债）。
- `None`：不支持（B 股/北交所/港美股/未知前缀），不猜测。

债券代码区段（6 位）：

- 可转债/可交换债：沪 110/111/113/118（科创板可转债）/132，深 123/125/126/127/128；
- 国债/企业债/公司债：沪 010/019/122/124/136/188，深 100/101/102/112。

注意深 11x/12x 与沪 5x 的冲突已在基金/债券区段里显式区分（深 11x 是公司债、
深 12x 是可转债；沪 5 开头是基金）。
"""

from __future__ import annotations

# A 股主板/科创板/创业板（沪 600/601/603/605/688/689，深 000/001/002/003/300/301）。
_A_SHARE_PREFIXES: frozenset[str] = frozenset(
    {"600", "601", "603", "605", "688", "689", "000", "001", "002", "003", "300", "301"}
)
# 场内基金：沪 5 开头全系；深 15/16/18 开头。
_FUND_PREFIXES: frozenset[str] = frozenset({"15", "16", "18"})

# 可转债/可交换债（走 akshare `bond_zh_hs_cov_daily`）。
_CONVERTIBLE_BOND_PREFIXES: frozenset[str] = frozenset(
    {"110", "111", "113", "118", "123", "125", "126", "127", "128", "132"}
)
# 国债/企业债/公司债（走 akshare `bond_zh_hs_daily`）。
_OTHER_BOND_PREFIXES: frozenset[str] = frozenset(
    {"010", "019", "100", "101", "102", "112", "122", "124", "136", "188"}
)
_BOND_PREFIXES: frozenset[str] = _CONVERTIBLE_BOND_PREFIXES | _OTHER_BOND_PREFIXES
# 沪市债券前缀（其余债券前缀一律按深市处理，与交易所代码区段一致）。
_SH_BOND_PREFIXES: frozenset[str] = frozenset(
    {"010", "019", "110", "111", "113", "118", "122", "124", "132", "136", "188"}
)

CLASS_STOCK = "stock"
CLASS_FUND = "fund"
CLASS_BOND = "bond"


def classify_symbol(symbol: str) -> str | None:
    """6 位代码 → `"stock" | "fund" | "bond" | None`（不支持的品种返回 None）。"""
    prefix = symbol[:3]
    if prefix in _A_SHARE_PREFIXES:
        return CLASS_STOCK
    if symbol.startswith("5") or symbol[:2] in _FUND_PREFIXES:
        return CLASS_FUND
    if prefix in _BOND_PREFIXES:
        return CLASS_BOND
    return None


def is_bond(symbol: str) -> bool:
    """是否沪深交易所债券（估值时允许「按买入价」兜底的品种）。"""
    return symbol[:3] in _BOND_PREFIXES


def is_convertible_bond(symbol: str) -> bool:
    """是否可转债/可交换债（akshare 走 `bond_zh_hs_cov_daily`）。"""
    return symbol[:3] in _CONVERTIBLE_BOND_PREFIXES


def bond_exchange(symbol: str) -> str:
    """债券代码 → 交易所前缀（`sh` / `sz`）。非债券代码调用无意义，由调用方保证。"""
    return "sh" if symbol[:3] in _SH_BOND_PREFIXES else "sz"


def bond_symbol(symbol: str) -> str:
    """`019547` → `sh019547`（akshare 债券接口要求交易所前缀）。"""
    return f"{bond_exchange(symbol)}{symbol}"
