"""华泰证券对账单解析器（纯函数、无 I/O 直读）。

输入 bytes + file_name，输出结构化 `ParsedBill`。之所以做成纯函数：解析结果要能被
预览（不落库）与提交（落库）两个阶段复用，并可被属性测试反复回放。
所有金额/数量/价格都经 `Decimal(str) → 标度整数`，绝不出现 float。

金额口径（全仓统一，对账直接使用）：`ParsedRow.amount_units` 一律是**带符号的发生金额
（净现金流，已含费用）**——买入为负、卖出为正；非交易资金业务按资金方向（转入为卖出方向
正、转出为买入方向负）；方向不可判定且没有金额流时为 0。账单里原生的「发生金额/发生额」
列已满足该口径，直接采用；「成交金额」毛额（及按量×价推导出的毛额）则按方向换算：
买入 `-(毛额+费用)`、卖出 `毛额-费用`，费用 = 佣金 + 印花税 + 其他费。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_contracts.clock import Clock
from stock_contracts.enums import QuarantineReason, Side, WarningCode
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.values import Money, Price, Qty
from stock_platform.symbols import is_convertible_bond

from stock_ledger.parsers.columns import (
    SENSITIVE_ACCOUNT_ALIASES,
    detect_date_format,
    is_non_trade_summary,
    is_signed_amount_header,
    map_columns,
    normalize_cell,
    normalize_header,
    normalize_summary,
    parse_date_text,
    resolve_side,
)
from stock_ledger.parsers.dedupe import core_signature, dedupe_key
from stock_ledger.parsers.mapping import MappingPlan, column_keys, merge_fee_units, validate_plan
from stock_ledger.parsers.profiles import profile_for
from stock_ledger.parsers.table import SUPPORTED_SUFFIXES, read_rows, validate_file

_HEADER_SCAN_ROWS = 30
_CURRENCY_NOISE = ("¥", "￥", "元", " ", ",", "\u3000")


@dataclass(slots=True)
class QuarantineCandidate:
    """无法进入明细的行：保留原始内容 + 原因码，供隔离区回填。"""

    line_no: int
    raw: dict[str, str]
    reason_code: str
    suggested_rule_id: str | None = None


@dataclass(slots=True)
class ParsedRow:
    """一条可入明细的交易（金额/数量/价格均为标度整数）。"""

    line_no: int
    trade_date: date
    security_code: str
    security_name: str
    side: str
    quantity_units: int
    price_units: int
    # 带符号发生金额（净现金流，含费用）：买入为负、卖出为正；对账直接用 prev+amount。
    amount_units: int
    commission_units: int
    stamp_tax_units: int
    other_fee_units: int
    cash_balance_units: int | None
    cash_balance_available: bool
    summary: str | None
    summary_code: str | None
    account_masked: str | None
    occurrence_idx: int
    dedupe_key: str
    raw: dict[str, str]
    # 货币基金余额（该行发生后该标的的份额余额，金额 scale=2）。None = 非货基行。
    # 货基单位净值恒 1.0000，没有可估值净值，资产口径按余额计（设计 §5.2）。
    money_fund_balance_units: int | None = None


# 货币基金识别：券商全称含「货币」（归一化折行/空白后），fixtures 3/3 命中。
_MONEY_FUND_KEYWORD = "货币"
# 货基单位净值恒 1.0000；价格列缺失时按此兜底（不伪造份额）。
_ONE_PRICE_UNITS = Price.from_decimal(Decimal(1)).units


def _is_money_fund_name(name: str) -> bool:
    return _MONEY_FUND_KEYWORD in "".join(name.split())


def _is_money_fund_row(row: ParsedRow) -> bool:
    """货基行识别：名称含「货币」，或「买/卖价格 = 1 且佣金/印花税/其他费全 0」。

    后者是对账口径的兜底（部分券商货基全称不含「货币」）；`quantity_units != 0`
    排除转托管确认等零份额噪音行，避免整只标的被误判成货基。
    """
    if _is_money_fund_name(row.security_name):
        return True
    return (
        row.side in (Side.BUY.value, Side.SELL.value)
        and row.price_units == _ONE_PRICE_UNITS
        and row.quantity_units != 0
        and row.commission_units == 0
        and row.stamp_tax_units == 0
        and row.other_fee_units == 0
    )


def _money_fund_codes(rows: list[ParsedRow]) -> set[str]:
    """账单中所有货基标的代码（名称 + 价格 1 零费用两条口径的并集）。"""
    return {row.security_code for row in rows if _is_money_fund_row(row)}


# 零金额信息行（纯通知，无资金意义）：申购中签/新股入账（帐）/托管转出/托管转入。
_ZERO_AMOUNT_INFO_KEYWORDS: tuple[str, ...] = (
    "申购中签",
    "新股入账",
    "新股入帐",
    "托管转出",
    "托管转入",
)
# 省心投（华泰基金投顾）摘要（归一化后精确匹配，2026-10-30 用户确认口径，设计 D14）：
# 资金在主账户与省心投投顾账户之间划转，但**转出动作不在账单留记录**——账单里只有
# 「[省心投]申购确认」（主账户资金流出买省心投货基）与「[省心投]红利发放」（省心投
# 账户内部的份额红利通知，金额恒 0，与主账户资金无关）。
_SXT_SUBSCRIBE_SUMMARY = "[省心投]申购确认"
_SXT_DIVIDEND_SUMMARY = "[省心投]红利发放"
# 可转债：数量单位是手（1 手 = 10 张），成交/行情价格是单张金额。
_CBOND_LOT_SIZE = 10
# 「qty × 10 × 单张价 ≈ |amount|」容差（amount 含费用，对齐估值反推的 2% 口径）。
_CBOND_AMOUNT_TOLERANCE_PERCENT = 2


def _is_zero_amount_info_summary(summary: str) -> bool:
    """摘要归一化后命中零金额信息关键词（申购中签/新股入账/托管转出入）。"""
    if not summary:
        return False
    normalized = normalize_summary(summary)
    return any(keyword in normalized for keyword in _ZERO_AMOUNT_INFO_KEYWORDS)


def _is_cbond_subscription_name(name: str) -> bool:
    """可转债申购（发债）行：名称去空白后以「发债」结尾（如「曙26发债」）。"""
    return "".join(name.split()).endswith("发债")


def _is_cbond_amount_inconsistent(row: ParsedRow) -> bool:
    """可转债行「数量(手) × 10 × 单张价」与 |发生金额| 偏差超容差（留痕不阻断）。

    只校验有明确买卖方向、量价额俱全的行；申购代码（754/780 等名称以发债结尾）
    与上市转债代码（is_convertible_bond）都按同一口径校验。
    """
    if (
        row.side not in (Side.BUY.value, Side.SELL.value)
        or row.quantity_units <= 0
        or row.price_units <= 0
        or row.amount_units == 0
    ):
        return False
    if not (
        is_convertible_bond(row.security_code) or _is_cbond_subscription_name(row.security_name)
    ):
        return False
    expected = Money.from_decimal(
        Qty(row.quantity_units).to_decimal() * _CBOND_LOT_SIZE * Price(row.price_units).to_decimal()
    ).units
    if expected <= 0:
        return False
    return abs(expected - abs(row.amount_units)) * 100 > _CBOND_AMOUNT_TOLERANCE_PERCENT * expected


def _share_value_units(shares_units: int, price_units: int) -> int:
    """份额 × 单位净值 → 金额标度整数（Money scale=2，ROUND_HALF_UP）。"""
    return Money.from_decimal(
        Qty(shares_units).to_decimal() * Price(price_units).to_decimal()
    ).units


def _read_fund_holding_anchors(rows: list[list[str]], codes: set[str]) -> dict[str, int]:
    """解析「基金持仓」区块，返回 `{货基代码: 期末参考市值(分)}`。

    区块在流水「合计」之后（真实对账单实测表头：基金代码/基金名称/基金份额/单位净值/
    参考市值/摊薄成本价/持仓盈亏），无独立日期列，锚点日按账单最大流水日。缺失
    `参考市值` 时用 `份额 × 单位净值` 兜底；非货基代码忽略。
    """
    header_idx: int | None = None
    for idx, row in enumerate(rows):
        normalized = [normalize_header(cell) for cell in row]
        if "基金代码" in normalized and "基金份额" in normalized:
            header_idx = idx
            break
    if header_idx is None:
        return {}
    header = [normalize_header(cell) for cell in rows[header_idx]]
    code_idx = header.index("基金代码")
    share_idx = header.index("基金份额")
    nav_idx = header.index("单位净值") if "单位净值" in header else None
    value_idx = header.index("参考市值") if "参考市值" in header else None
    anchors: dict[str, int] = {}
    for row in rows[header_idx + 1 :]:
        if not any(row):
            continue
        code = normalize_cell(row[code_idx]) if code_idx < len(row) else ""
        if not code:
            continue
        if not code.isdigit():
            # 区块以「合计/声明」等非代码行收尾。
            break
        if code not in codes:
            continue
        value_units: int | None = None
        if value_idx is not None and value_idx < len(row) and row[value_idx]:
            try:
                value_units = _units(row[value_idx], Money)
            except _RowError:
                value_units = None
        if value_units is None and share_idx < len(row) and row[share_idx]:
            try:
                shares = _units(row[share_idx], Qty)
                nav_units = _units(row[nav_idx], Price) if nav_idx is not None else 0
                if nav_units:
                    value_units = _share_value_units(shares, nav_units)
            except _RowError:
                value_units = None
        if value_units is not None:
            anchors[code] = value_units
    return anchors


# 货基「零金额但带份额」的行：红利发放/非交易过户确认。它们不产生现金流，
# 但会改变货基份额余额；若直接按 INVALID_AMOUNT 隔离，会让后续赎回被误判为超卖。
_MONEY_FUND_SHARE_ONLY_SUMMARIES: frozenset[str] = frozenset({"红利发放", "非交易过户确认"})


def _share_only_money_fund_row(
    raw: dict[str, str],
    values: Callable[[str], Sequence[str]],
    date_format_code: str,
    line_no: int,
) -> ParsedRow | None:
    """识别「零金额、带份额」的货基行，仅供余额推演，不进入流水。"""

    def value(name: str) -> str:
        items = values(name)
        return items[0] if items else ""

    code = value("security_code")
    name = value("security_name")
    summary = normalize_summary(value("summary"))
    if not code or not _is_money_fund_name(name):
        return None
    if summary not in _MONEY_FUND_SHARE_ONLY_SUMMARIES:
        return None
    try:
        quantity_units = _units(value("quantity"), Qty)
        price_units = _units(value("price"), Price) or _ONE_PRICE_UNITS
        year, month, day = parse_date_text(value("trade_date"), date_format_code)
        trade_date = date(year, month, day)
    except (_RowError, ValueError):
        return None
    if quantity_units == 0:
        return None
    # 「非交易过户确认」当前实测为货基份额转出（同日另有「资管转让资金上账」资金行；
    # 与资金行配对时在 D16 改写中被剔除，防份额重复扣减）；
    # 「红利发放」为份额转入。份额符号不可靠，按摘要语义定方向。
    if summary == "非交易过户确认":
        side = Side.SELL.value
    else:
        side = Side.BUY.value if quantity_units > 0 else Side.SELL.value
    return ParsedRow(
        line_no=line_no,
        trade_date=trade_date,
        security_code=code,
        security_name=name,
        side=side,
        quantity_units=abs(quantity_units),
        price_units=price_units,
        amount_units=0,
        commission_units=0,
        stamp_tax_units=0,
        other_fee_units=0,
        cash_balance_units=0,
        cash_balance_available=True,
        summary=value("summary") or None,
        summary_code=None,
        account_masked=value("account") or None,
        occurrence_idx=0,
        dedupe_key="",
        raw=raw,
    )


def _apply_money_fund_balances(
    rows: list[ParsedRow],
    anchors: dict[str, int],
    *,
    extra_rows: list[ParsedRow] | None = None,
) -> tuple[dict[str, int], set[str], set[str], set[str]]:
    """按余额累计（+ 账单「基金持仓」期末锚点反推期初）逐行写货币基金余额。

    全部在**金额标度（分）**上运行：份额增量先按「份额 × 单位净值(1.0000)」换算成金额，
    避免 Qty(scale=4) 与 Money(scale=2) 混算。返回 ``(inferred, zero, clamped, interest)``：

    - ``inferred``：无锚点且期初按赎回行自证为正（下界 ``-min(累计净额)``）的代码 → 留痕。
    - ``zero``：无锚点且期初为 0 的代码 → 留痕即可。
    - ``clamped``：有锚点但账单**缺赎回/转出流水**（单侧流水缺陷），按期末锚点反推的
      中间余额会为负 → 写 0 兜底留痕（设计 D12）。
    - ``interest``：**账单内建立持仓**（该代码首行即买入）的代码出现**部分超卖**（赎回把
      已跟踪持仓卖穿）→ 超出部分 = 累计利息，在赎回当日实现（设计 D15）。期初不得因此
      抬高（否则利息被算成初始资产，当次超卖的盈利被抹掉）。
      （[省心投] 申购自 D14 起转 CASH 资金行，不再进入货基推演。）

    期初口径：
    - 首行即买入（持仓在本账单内建立）→ 期初 0；后续任何负余额都是利息/缺失流水。
    - 首行即赎回（账单期前已持有）→ 有锚点按 ``锚点 − 累计净额`` 反推；无锚点按
      ``-min(累计净额)`` 下界（D10）。这两种情况下负数都是「期初估值不足」，不是利息。
    """
    extra = extra_rows or []
    all_rows = [*rows, *extra]
    codes = _money_fund_codes(all_rows)
    if not codes:
        return {}, set(), set(), set()
    balance_only = {row.line_no for row in extra}
    # 严格 Excel 行序（line_no 即原始表格行号）：同日多笔买卖绝不按 side/日期二次排序。
    ordered = sorted(all_rows, key=lambda row: row.line_no)
    # 第一遍：期初 0 推演，记录各代码的累计净额与最小余额。
    running: dict[str, int] = dict.fromkeys(codes, 0)
    min_running: dict[str, int] = dict.fromkeys(codes, 0)
    first_row: dict[str, ParsedRow] = {}
    for row in ordered:
        if row.security_code not in codes:
            continue
        if row.line_no not in balance_only:
            first_row.setdefault(row.security_code, row)
        running[row.security_code] += _money_fund_row_delta(row)
        min_running[row.security_code] = min(
            min_running[row.security_code], running[row.security_code]
        )
    # 首行即买入 → 持仓在本账单内建立，期初必为 0（不能把利息当初始资产）。
    built_in_bill = {code for code, row in first_row.items() if _money_fund_row_delta(row) > 0}
    opening: dict[str, int] = {}
    for code in codes:
        if code in built_in_bill:
            opening[code] = 0
        elif code in anchors:
            opening[code] = anchors[code] - running[code]
        else:
            opening[code] = -min_running[code] if min_running[code] < 0 else 0
    missing = {code for code in codes if code not in anchors}
    inferred = {code: opening[code] for code in missing if opening[code] > 0}
    zero = {code for code in missing if opening[code] <= 0}
    balance = dict(opening)
    clamped: set[str] = set()
    interest: set[str] = set()
    last_row: dict[str, ParsedRow] = {}
    for row in ordered:
        code = row.security_code
        if code not in codes:
            continue
        balance[code] += _money_fund_row_delta(row)
        if row.line_no in balance_only:
            # 零金额份额行只调整余额，不产出流水行；负数兜底为 0（非现金流事件）。
            if balance[code] < 0:
                balance[code] = 0
            continue
        stored = balance[code]
        if stored < 0:
            if code in built_in_bill:
                # 部分超卖：赎回把已跟踪持仓卖穿 → 超出部分是利息，当日实现；累计归零。
                interest.add(code)
                balance[code] = 0
            else:
                # 缺流水导致的反推负余额：写 0（不修改累计值，期末仍落在锚点上）。
                clamped.add(code)
            stored = 0
        row.money_fund_balance_units = stored
        last_row[code] = row
    # 锚点校准：末行余额对齐「基金持仓」期末参考市值（差额记在末行当日，不进期初）。
    for code, row in last_row.items():
        anchor = anchors.get(code)
        if anchor is None or int(row.money_fund_balance_units or 0) == anchor:
            continue
        row.money_fund_balance_units = max(0, anchor)
    return inferred, zero, clamped, interest


def _money_fund_row_delta(row: ParsedRow) -> int:
    """货基行的余额增量（金额分）：卖出取负，其余（含红利再投）取正。"""
    shares = -row.quantity_units if row.side == Side.SELL.value else row.quantity_units
    return _share_value_units(shares, row.price_units or _ONE_PRICE_UNITS)


# 「资管转让资金上账」（归一化后精确匹配，设计 D16）：华泰把货基（天天发）赎回拆成两行——
# 资金腿「资管转让资金上账」（无代码 CASH 资金行，+金额）与份额腿「非交易过户确认」
# （零金额份额转出，仅供余额推演）。资金腿若按银证转入（transfer）入账：桥接外部现金流
# 虚增 → 累计收益少算；FIFO 没有对应赎回 → 幽灵 lot 虚增持仓成本；资产表在资金到账日
# 现金与货基重复计。还原为货基赎回行后，分类/FIFO/桥接/资产表全部走既有口径自闭合。
_ASSET_TRANSFER_IN_SUMMARY = "资管转让资金上账"


def _rewrite_asset_transfer_as_fund_redemption(
    rows: list[ParsedRow], share_only_rows: list[ParsedRow]
) -> list[int]:
    """把「资管转让资金上账」CASH 资金行原地改写为货基赎回行（设计 D16）。

    - 仅当账单内可唯一识别一只货基（含份额行在内 `_money_fund_codes` 恰好 1 个代码）
      才改写；无法唯一归属时保持 CASH 划转口径（安全兜底，不猜代码）；
    - 改写：code/name 取货基，qty = 金额（单位净值恒 1.0000，1 元 = 1 份），price = 1.0，
      摘要改写为「赎回确认」（分类规则据此落 sell；原始摘要与代码留 `raw` 可溯源）；
    - 配对的「非交易过户确认」份额腿（份额价值 == 某改写行金额）从余额推演中剔除，
      避免份额被重复扣减（改写行的份额增量已承担该扣减）。

    返回被改写行的 line_no 列表（调用方写 assumptions 留痕）。
    """
    fund_codes = _money_fund_codes([*rows, *share_only_rows])
    if len(fund_codes) != 1:
        return []
    code = next(iter(fund_codes))
    name = next(
        (row.security_name for row in [*rows, *share_only_rows] if row.security_code == code),
        code,
    )
    rewritten: list[int] = []
    redeemed_amounts: list[int] = []  # 改写行金额（分），用于配对份额腿
    for row in rows:
        if row.security_code != "CASH" or row.amount_units <= 0:
            continue
        if normalize_summary(row.summary or "") != _ASSET_TRANSFER_IN_SUMMARY:
            continue
        row.security_code = code
        row.security_name = name
        # 货基单位净值恒 1.0000：1 元 = 1 份；Money(scale=2) × 100 = Qty(scale=4)。
        row.quantity_units = row.amount_units * 100
        row.price_units = _ONE_PRICE_UNITS
        row.summary = "赎回确认"
        rewritten.append(row.line_no)
        redeemed_amounts.append(row.amount_units)
    if not rewritten:
        return []
    kept_share_only: list[ParsedRow] = []
    for share_row in share_only_rows:
        share_value = _share_value_units(share_row.quantity_units, share_row.price_units)
        if (
            share_row.security_code == code
            and normalize_summary(share_row.summary or "") == "非交易过户确认"
            and share_value in redeemed_amounts
        ):
            redeemed_amounts.remove(share_value)
            continue
        kept_share_only.append(share_row)
    share_only_rows[:] = kept_share_only
    return rewritten


@dataclass(slots=True)
class ParsedBill:
    """一次解析的完整结果（预览与提交共用）。"""

    rows: list[ParsedRow] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    date_format: str | None = None
    warnings: list[WarningCode] = field(default_factory=list)
    quarantined: list[QuarantineCandidate] = field(default_factory=list)
    assumptions: dict[str, Any] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows) + len(self.quarantined)


class _RowError(Exception):
    """单行解析失败（内部信号，外部统一转隔离）。"""

    __slots__ = ("reason",)

    def __init__(self, reason: QuarantineReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(slots=True)
class _Candidate:
    parsed: ParsedRow
    raw_values: tuple[str, ...]


def _locate_header(
    rows: list[list[str]], mapping: dict[str, str] | None
) -> tuple[int, list[str], list[list[str]]]:
    """在前若干行里挑“识别到最多列”的一行作表头（账单常有抬头/说明行）。"""
    best_idx = 0
    best_count = 0
    best_headers: list[str] = rows[0] if rows else []
    for idx in range(min(len(rows), _HEADER_SCAN_ROWS)):
        found = map_columns(rows[idx], mapping=mapping)
        count = len(found.columns)
        if count > best_count:
            best_idx, best_count, best_headers = idx, count, rows[idx]
    return best_idx, best_headers, rows[best_idx + 1 :]


def _decimal(raw: str) -> Decimal:
    """单元格文本 → Decimal（去千分位/货币符号）；空值按 0。"""
    cleaned = raw
    for noise in _CURRENCY_NOISE:
        cleaned = cleaned.replace(noise, "")
    if cleaned in ("", "-", "+"):
        return Decimal(0)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise _RowError(QuarantineReason.INVALID_ROW) from exc


def _units(raw: str, kind: type[Money] | type[Price] | type[Qty]) -> int:
    return kind.from_decimal(_decimal(raw)).units


def _gross_to_net_units(gross_units: int, side: str, fees_units: int) -> int:
    """毛成交额 → 带符号净现金流（分）：买入 `-(毛额+费用)`，卖出 `毛额-费用`。"""
    if side == Side.SELL.value:
        return gross_units - fees_units
    return -(gross_units + fees_units)


def _net_from_gross_units(gross_units: int, side: str, fees_units: int) -> tuple[int, bool]:
    """毛额 → 净现金流；方向未知时无法定号（置 0 并回报），否则按买卖方向换算。"""
    if side == Side.UNKNOWN.value:
        return 0, gross_units != 0
    return _gross_to_net_units(gross_units, side, fees_units), False


def _net_amount_units(
    raw_text: str, *, signed: bool, side: str, fees_units: int
) -> tuple[int, bool]:
    """金额列原文 → 带符号净现金流；返回 `(units, 是否因方向未知而无法定号)`。

    - 「发生金额/发生额」原生带符号 → 直接采用（费用已含在列内，绝不重复扣减）；
    - 毛额列里出现负数 → 只可能是已带符号的净额（毛额不可能为负），按净额采用；
    - 其余按方向换算；方向 unknown 时资金流向不可判定，置 0 并回报调用方留痕。
    """
    raw_units = _units(raw_text, Money)
    if signed or raw_units < 0:
        return raw_units, False
    return _net_from_gross_units(raw_units, side, fees_units)


class _ProfileRequiredError(Exception):
    """通用画像遇到券商特有语义（省心投等）→ 调用方转 `IMPORT_PROFILE_REQUIRED`。"""


def _build_candidate(
    raw: dict[str, str],
    field_headers: dict[str, str],
    date_format_code: str,
    line_no: int,
    *,
    amount_signed: bool,
) -> tuple[ParsedRow, bool, bool]:
    """legacy 入口：表头 → 字段的取值闭包，再走统一的 plan 口径构建。"""

    def values(name: str) -> tuple[str, ...]:
        header = field_headers.get(name)
        return (raw.get(header, ""),) if header is not None else ()

    return _build_candidate_from_values(
        raw,
        values,
        date_format_code,
        line_no,
        amount_signed=amount_signed,
        apply_sxt=True,
    )


def _build_candidate_from_values(
    raw: dict[str, str],
    values: Callable[[str], Sequence[str]],
    date_format_code: str,
    line_no: int,
    *,
    amount_signed: bool,
    apply_sxt: bool,
) -> tuple[ParsedRow, bool, bool]:
    """单行 → ParsedRow；失败抛 _RowError 由调用方隔离。

    返回 `(行, 金额是否按量×价派生, 金额是否因方向未知而无法定号)`。
    """

    def value(name: str) -> str:
        items = values(name)
        return items[0] if items else ""

    code = value("security_code")
    if not code:
        # 非交易类业务（银行转存/转取、股息入帐等）证券代码可空，用 CASH 占位导入。
        if is_non_trade_summary(value("summary")):
            code = "CASH"
        else:
            raise _RowError(QuarantineReason.INVALID_ROW)
    # 方向：买卖标志列可缺失/为空，此时回退摘要识别，仍无则 unknown（不隔离该行）。
    side = resolve_side(value("side"), value("summary"))
    try:
        year, month, day = parse_date_text(value("trade_date"), date_format_code)
        trade_date = date(year, month, day)
    except ValueError as exc:
        raise _RowError(QuarantineReason.INVALID_ROW) from exc

    quantity_units = _units(value("quantity"), Qty)
    price_units = _units(value("price"), Price)

    commission_units = merge_fee_units([_units(item, Money) for item in values("commission")])
    stamp_tax_units = merge_fee_units([_units(item, Money) for item in values("stamp_tax")])
    # other_fee 支持多列合并（D-08）：每列先转标度整数，再在 int 域求和。
    other_fee_units = merge_fee_units([_units(item, Money) for item in values("other_fee")])
    fees_units = commission_units + stamp_tax_units + other_fee_units

    amount_text = value("amount")
    if amount_text:
        amount_units, amount_unsigned = _net_amount_units(
            amount_text, signed=amount_signed, side=side, fees_units=fees_units
        )
        amount_derived = False
    else:
        # 无金额列：先按量×价推导毛额，再按方向换算成净现金流（不能用原始毛额入库）。
        gross_units = Money.from_decimal(
            Qty(quantity_units).to_decimal() * Price(price_units).to_decimal()
        ).units
        amount_units, amount_unsigned = _net_from_gross_units(gross_units, side, fees_units)
        amount_derived = True

    balance_text = value("cash_balance")
    cash_balance_units: int | None = None
    if balance_text:
        cash_balance_units = _units(balance_text, Money)

    # 「[省心投]红利发放」：省心投账户内部的份额红利通知（金额恒 0），与主账户资金
    # 无关——不论金额字段形态一律直接隔离（设计 D14），不进明细。
    normalized_summary = normalize_summary(value("summary"))
    if normalized_summary == _SXT_DIVIDEND_SUMMARY:
        if not apply_sxt:
            raise _ProfileRequiredError(_SXT_DIVIDEND_SUMMARY)
        raise _RowError(QuarantineReason.ZERO_AMOUNT_INFO)

    # 「申购中签/新股入账/托管转出」等纯通知行：无发生金额且无资金余额 → 隔离为
    # ZERO_AMOUNT_INFO，不进明细（否则会形成无意义持仓/回合噪音，如中签通知行使
    # 持仓形成日早于真实缴款日）。同类摘要但有金额的行（真实缴款）不受影响。
    # 打新各分支在新口径下的落点：真正扣款的「新股/认购/申购」行 → new_stock 建仓；
    # 申购中签/申购还款/新股入账/证券清理/托管等登记动作 → broker_action（不影响持仓与成本）；
    # 申购冻结/解冻/退款等资金腿 → transfer（外部现金流，不进收益）。
    if (
        amount_units == 0
        and cash_balance_units is None
        and _is_zero_amount_info_summary(value("summary"))
    ):
        raise _RowError(QuarantineReason.ZERO_AMOUNT_INFO)

    # 五个金额字段（佣金/印花税/其他费/发生金额/资金余额）全为 0：没有资金流语义
    # （典型是货基份额再投、转托管确认等噪音行），不入明细，隔离为 INVALID_AMOUNT。
    # 资金余额列缺失（None）不算 0，交后续对账按 MISSING_CASH_BALANCE 降级处理。
    if (
        commission_units == 0
        and stamp_tax_units == 0
        and other_fee_units == 0
        and amount_units == 0
        and cash_balance_units == 0
    ):
        raise _RowError(QuarantineReason.INVALID_AMOUNT)

    summary = value("summary") or None
    # 账号明文已在 parse_bill 入口统一抹成 MD5（空账号留空串 → 这里转 None）。
    # 此值同时作为 ParsedRow.account_masked（落库列）与下游去重键的业务要素。
    account_masked = value("account") or None
    # 非交易行证券名称为空时，用摘要填充（如「银行转存」），避免默认成 CASH 无法辨识业务。
    security_name = value("security_name") or (summary or "资金业务" if code == "CASH" else code)
    # 「[省心投]申购确认」视同银行转出（设计 D14）：主账户资金流出到省心投投顾账户，
    # 但转出动作不在账单留记录，申购确认行本身就是转出证据。转 CASH 资金行（与
    # 「银行转取」同构）：无份额语义（qty/price 清零，原始值留 raw 可溯源）、不进持仓、
    # 不打货基余额；发生金额（负值）即主账户流出额，分类走 transfer（sys_sxt_out）。
    if summary is not None and normalize_summary(summary) == _SXT_SUBSCRIBE_SUMMARY:
        if not apply_sxt:
            raise _ProfileRequiredError(_SXT_SUBSCRIBE_SUMMARY)
        code = "CASH"
        security_name = summary
        quantity_units = 0
        price_units = 0

    row = ParsedRow(
        line_no=line_no,
        trade_date=trade_date,
        security_code=code,
        security_name=security_name,
        side=side,
        quantity_units=quantity_units,
        price_units=price_units,
        amount_units=amount_units,
        commission_units=commission_units,
        stamp_tax_units=stamp_tax_units,
        other_fee_units=other_fee_units,
        cash_balance_units=cash_balance_units,
        cash_balance_available=cash_balance_units is not None,
        summary=summary,
        summary_code=None,
        account_masked=account_masked,
        occurrence_idx=0,
        dedupe_key="",
        raw=raw,
    )
    return row, amount_derived, amount_unsigned


@dataclass(slots=True)
class _ParseContext:
    """一次解析的列上下文（legacy 与 plan 两条入口归一到这里）。"""

    headers: list[str]
    raw_headers: list[str]
    field_indices: dict[str, list[int]]
    columns: dict[str, str]
    unresolved: list[str]
    amount_signed: bool
    apply_sxt: bool
    require_money_fund_balance: bool
    broker: str


def _legacy_context(headers: list[str], mapping: dict[str, str] | None) -> _ParseContext:
    found = map_columns(headers, mapping=mapping)
    missing_required = found.missing_required()
    if missing_required:
        raise ApiError(
            ErrorCode.IMPORT_COLUMNS_UNRESOLVED,
            errors=[
                FieldError("columns", "missing_required_column", f"缺少必需列: {name}")
                for name in missing_required
            ],
            detail="账单缺少必需列",
        )
    field_headers = found.field_headers()
    return _ParseContext(
        headers=headers,
        raw_headers=headers,
        field_indices={name: [headers.index(header)] for name, header in field_headers.items()},
        columns=found.columns,
        unresolved=found.unresolved,
        amount_signed=is_signed_amount_header(field_headers.get("amount")),
        apply_sxt=True,
        require_money_fund_balance=False,
        broker="huatai",
    )


def _plan_context(headers: list[str], plan: MappingPlan, broker: str) -> _ParseContext:
    keys = column_keys(headers)
    key_to_idx = {key: idx for idx, key in enumerate(keys)}
    unknown = [item.column_key for item in plan.items if item.column_key not in key_to_idx]
    if unknown:
        raise ApiError(
            ErrorCode.IMPORT_MAPPING_INVALID,
            errors=[
                FieldError("mapping", "unknown_column", f"列键不在当前文件中: {key}")
                for key in unknown
            ],
            detail="映射引用了不存在的列",
        )
    field_indices: dict[str, list[int]] = {}
    columns: dict[str, str] = {}
    for item in plan.items:
        idx = key_to_idx[item.column_key]
        field_indices.setdefault(item.field, []).append(idx)
        columns[headers[idx]] = item.field
    mapped_keys = {item.column_key for item in plan.items}
    profile = profile_for(broker)
    return _ParseContext(
        headers=headers,
        raw_headers=headers,
        field_indices=field_indices,
        columns=columns,
        unresolved=[header for idx, header in enumerate(headers) if keys[idx] not in mapped_keys],
        amount_signed=plan.amount_signed(),
        apply_sxt=profile.apply_sxt,
        require_money_fund_balance=profile.require_money_fund_balance,
        broker=profile.code,
    )


def _parse_bill_impl(
    data: bytes,
    file_name: str,
    *,
    mapping: dict[str, str] | None = None,
    plan: MappingPlan | None = None,
    date_format: str | None,
    clock: Clock,
    broker: str = "huatai",
) -> ParsedBill:
    """解析实现：legacy（表头→字段字典）与 v2（MappingPlan）共用全部业务口径。"""
    suffix = validate_file(data, file_name)
    raw_rows = read_rows(data, suffix)
    if plan is None:
        header_idx, headers, data_rows = _locate_header(raw_rows, mapping)
        context = _legacy_context(headers, mapping)
    else:
        validate_plan(plan)
        if plan.header_row_index >= len(raw_rows):
            raise ApiError(
                ErrorCode.IMPORT_MAPPING_INVALID,
                errors=[
                    FieldError(
                        "header_row_index",
                        "header_row_out_of_range",
                        f"表头行 {plan.header_row_index} 超出文件范围",
                    )
                ],
                detail="表头行超出文件范围",
            )
        if "trade_date" not in plan.mapped_fields():
            raise ApiError(
                ErrorCode.IMPORT_MAPPING_INVALID,
                errors=[FieldError("mapping", "missing_trade_date", "必须映射成交日期")],
                detail="缺少成交日期映射",
            )
        header_idx = plan.header_row_index
        headers = [normalize_header(cell) for cell in raw_rows[header_idx]]
        data_rows = raw_rows[header_idx + 1 :]
        context = _plan_context(headers, plan, broker)

    warnings: list[WarningCode] = []
    assumptions: dict[str, Any] = {}

    trade_date_idx = (
        context.field_indices["trade_date"][0] if context.field_indices.get("trade_date") else None
    )
    samples: list[str] = []
    if trade_date_idx is not None:
        for row in data_rows:
            if trade_date_idx < len(row):
                cell = row[trade_date_idx]
                # 「合计」之后不是交易行：样本遇到它即停，避免把「合计」当日期样本。
                if normalize_header(cell) == "合计":
                    break
                if cell:
                    samples.append(cell)
            if len(samples) >= 20:
                break

    detected_format, detected_warnings = detect_date_format(samples)
    if date_format is not None:
        effective_format = date_format
    else:
        effective_format = detected_format or "YYYY-MM-DD"
        warnings.extend(detected_warnings)
        if (
            detected_format is None and samples
        ) or WarningCode.DATE_FORMAT_AMBIGUOUS in detected_warnings:
            assumptions["date_format"] = effective_format

    def make_values(row: list[str]) -> Callable[[str], Sequence[str]]:
        def values(name: str) -> Sequence[str]:
            cells = tuple(
                row[idx] if idx < len(row) else "" for idx in context.field_indices.get(name, ())
            )
            if plan is not None and name == "other_fee":
                return tuple("" if cell in {"--", "—"} else cell for cell in cells)
            return cells

        return values

    account_indices = context.field_indices.get("account", [])
    # 脱敏口径独立于字段映射：股东代码/股东账号不再当作 account，但仍是敏感列，
    # 明文不得进入隔离区 raw 或预览 sample。
    sensitive = {
        index
        for index, header in enumerate(context.headers)
        if normalize_header(header) in SENSITIVE_ACCOUNT_ALIASES
    }
    account_indices = sorted({*account_indices, *sensitive})
    candidates: list[_Candidate] = []
    share_only_rows: list[ParsedRow] = []
    quarantined: list[QuarantineCandidate] = []
    derived_count = 0
    unsigned_amount_count = 0
    # 流水明细以「合计」行收尾；其后的持仓区块用于货币基金余额锚点（见 §5.3）。
    stop_row_index: int | None = None

    for offset, original_row in enumerate(data_rows):
        line_no = header_idx + offset + 2
        if not any(cell for cell in original_row):
            continue
        # 流水明细以「合计」行收尾：其后是股票/基金持仓、声明等非交易区块。
        # 不在这里停止的话，上百行持仓会被当成坏行写进隔离区，污染“隔离数据”。
        if (
            trade_date_idx is not None
            and trade_date_idx < len(original_row)
            and normalize_header(original_row[trade_date_idx]) == "合计"
        ):
            stop_row_index = header_idx + offset + 1
            break
        row = list(original_row)
        # 把账号列明文替换成 MD5 摘要：隔离区 raw 和预览 columns_detail.sample
        # 都从这里拿原始行字典，源头抹一次即可，下游都安全。
        for idx in account_indices:
            if idx < len(row) and row[idx]:
                row[idx] = hashlib.md5(row[idx].encode("utf-8")).hexdigest()
        raw = {
            header: (row[i] if i < len(row) else "") for i, header in enumerate(context.raw_headers)
        }
        values = make_values(row)
        try:
            parsed, amount_derived, amount_unsigned = _build_candidate_from_values(
                raw,
                values,
                effective_format,
                line_no,
                amount_signed=context.amount_signed,
                apply_sxt=context.apply_sxt,
            )
        except _ProfileRequiredError as exc:
            raise ApiError(
                ErrorCode.IMPORT_PROFILE_REQUIRED,
                detail=f"账单包含券商特有语义（{exc}），请选择对应券商画像",
            ) from exc
        except _RowError as err:
            share_only = (
                _share_only_money_fund_row(raw, values, effective_format, line_no)
                if err.reason == QuarantineReason.INVALID_AMOUNT
                else None
            )
            if share_only is not None:
                share_only_rows.append(share_only)
                continue
            quarantined.append(
                QuarantineCandidate(line_no=line_no, raw=raw, reason_code=err.reason.value)
            )
            continue
        if amount_derived:
            derived_count += 1
        if amount_unsigned:
            unsigned_amount_count += 1
        raw_values = tuple(raw.get(header, "") for header in context.headers)
        candidates.append(_Candidate(parsed=parsed, raw_values=raw_values))

    kept: list[_Candidate] = []
    seen_full: set[tuple[str, ...]] = set()
    for cand in candidates:
        if cand.raw_values in seen_full:
            quarantined.append(
                QuarantineCandidate(
                    line_no=cand.parsed.line_no,
                    raw=cand.parsed.raw,
                    reason_code=QuarantineReason.DUPLICATE_IN_FILE.value,
                )
            )
            continue
        seen_full.add(cand.raw_values)
        kept.append(cand)

    # 「资管转让资金上账」还原为货基赎回行（设计 D16）：必须在 occurrence/dedupe_key
    # 计算之前改写，标的/数量/价格变化才会进入去重键；配对份额腿同步剔除。
    asset_transfer_rewritten = _rewrite_asset_transfer_as_fund_redemption(
        [cand.parsed for cand in kept], share_only_rows
    )

    counts: dict[str, int] = {}
    rows: list[ParsedRow] = []
    for cand in kept:
        parsed_row = cand.parsed
        signature = core_signature(parsed_row)
        parsed_row.occurrence_idx = counts.get(signature, 0)
        counts[signature] = parsed_row.occurrence_idx + 1
        parsed_row.dedupe_key = dedupe_key(parsed_row)
        rows.append(parsed_row)

    # 严格 Excel 行序：line_no 即原表行号；同日多笔买卖保持表格自上而下顺序。
    rows.sort(key=lambda row: row.line_no)

    if "cash_balance" not in context.field_indices:
        warnings.append(WarningCode.MISSING_CASH_BALANCE)
    # 货币基金余额：按份额累计推演；若账单含「基金持仓」区块，用其参考市值作期末锚点
    # 反推期初（设计 §5.3）。无锚点且账单内有净赎回的代码按「-min(累计净额)」反推期初下界
    # （赎回行自证期初持仓），其余无锚点代码期初按 0；均写 assumptions 留痕，不伪造余额。
    fund_rows = raw_rows[stop_row_index + 1 :] if stop_row_index is not None else []
    fund_anchors = _read_fund_holding_anchors(fund_rows, _money_fund_codes(rows))
    # 通用画像：货基行没有「货基余额」映射 → fail-closed（设计 D-09），不猜期初。
    if (
        context.require_money_fund_balance
        and "money_fund_balance" not in context.field_indices
        and any(_is_money_fund_name(row.security_name) for row in rows + share_only_rows)
    ):
        raise ApiError(
            ErrorCode.IMPORT_PROFILE_REQUIRED,
            detail="账单包含货币基金行，请选择券商画像或映射「货基余额」列",
        )
    mf_inferred, mf_zero, mf_clamped, mf_interest = _apply_money_fund_balances(
        rows, fund_anchors, extra_rows=share_only_rows
    )
    if mf_zero:
        assumptions["money_fund_opening_zero"] = sorted(mf_zero)
    if mf_inferred:
        assumptions["money_fund_opening_inferred"] = {
            code: mf_inferred[code] for code in sorted(mf_inferred)
        }
    if mf_clamped:
        assumptions["money_fund_balance_clamped"] = sorted(mf_clamped)
    if mf_interest:
        # 部分超卖 = 累计利息在赎回当日实现（设计 D15）：不抬高期初，避免利息被算成初始资产。
        assumptions["money_fund_oversell_interest"] = sorted(mf_interest)
    if share_only_rows:
        assumptions["money_fund_balance_only_rows"] = sorted(row.line_no for row in share_only_rows)
    if asset_transfer_rewritten:
        # 设计 D16：被还原为货基赎回行的「资管转让资金上账」行号留痕（原始摘要留 raw）。
        assumptions["asset_transfer_fund_redemption_rows"] = asset_transfer_rewritten
    if rows and rows[0].side != "buy":
        warnings.append(WarningCode.FIRST_ROW_NOT_BUY)
    if any(q.reason_code == QuarantineReason.DUPLICATE_IN_FILE.value for q in quarantined):
        warnings.append(WarningCode.DUPLICATE_ROWS)
    if derived_count:
        assumptions["amount_derived_rows"] = derived_count
    if unsigned_amount_count:
        # 毛额列 + 方向 unknown：资金流向不可判定，金额保守置 0，不伪造正负号。
        assumptions["amount_unsigned_rows"] = unsigned_amount_count
    # 可转债口径留痕：数量单位是手（1 手 = 10 张）、价格是单张金额，「qty × 10 ×
    # 单张价」应与 |发生金额| 大致相等；偏差超容差说明券商口径异常，WARN 不阻断。
    cbond_inconsistent = sum(1 for row in rows if _is_cbond_amount_inconsistent(row))
    if cbond_inconsistent:
        warnings.append(WarningCode.CBOND_AMOUNT_INCONSISTENT)
        assumptions["cbond_amount_inconsistent_rows"] = cbond_inconsistent

    return ParsedBill(
        rows=rows,
        columns=context.columns,
        unresolved=context.unresolved,
        date_format=effective_format,
        warnings=warnings,
        quarantined=quarantined,
        assumptions=assumptions,
    )


def parse_bill(
    data: bytes,
    file_name: str,
    *,
    mapping: dict[str, str] | None,
    date_format: str | None,
    clock: Clock,
) -> ParsedBill:
    """解析华泰账单（legacy 入口）；业务失败以 ApiError/隔离行表达，不抛裸异常。"""
    return _parse_bill_impl(data, file_name, mapping=mapping, date_format=date_format, clock=clock)


def parse_bill_with_plan(
    data: bytes,
    file_name: str,
    *,
    plan: MappingPlan,
    date_format: str | None,
    clock: Clock,
    broker: str = "generic",
) -> ParsedBill:
    """按映射计划解析（v2 入口）；与 legacy 共用业务口径，差异只在列取值与画像。"""
    return _parse_bill_impl(
        data, file_name, plan=plan, date_format=date_format, clock=clock, broker=broker
    )


__all__ = [
    "SUPPORTED_SUFFIXES",
    "ParsedBill",
    "ParsedRow",
    "QuarantineCandidate",
    "parse_bill",
    "parse_bill_with_plan",
]
