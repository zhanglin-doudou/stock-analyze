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
    normalize_header,
    normalize_name,
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
# 「[省心投]申购确认」（主账户资金流出到省心投投顾账户）。「[省心投]红利发放」按账单
# 分类正常入账（dividend，金额恒 0），不再单独隔离。
_SXT_SUBSCRIBE_SUMMARY = "[省心投]申购确认"
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
    # （份额再投、转托管确认、红利发放等登记/通知行），一律不入明细，隔离为
    # INVALID_AMOUNT。这里**不做任何摘要豁免**：这类行的「资金余额」列常是券商占位
    # 0.00（真实案例：2026-10-27 940018 红利发放），放进账本会直接成为期末余额锚点；
    # 需要份额/红利留痕时从隔离区人工处理。
    # 资金余额列缺失（None）不算 0，交后续对账按 MISSING_CASH_BALANCE 降级处理。
    if (
        commission_units == 0
        and stamp_tax_units == 0
        and other_fee_units == 0
        and amount_units == 0
        and cash_balance_units == 0
    ):
        raise _RowError(QuarantineReason.INVALID_AMOUNT)

    # 摘要在这里（解析边界）统一归一：删除折行/空格/零宽字符后再落 ParsedRow。
    # 落库摘要、类型 key、规则匹配、模板比对因此共用同一文本，避免各自再归一分叉。
    summary = normalize_summary(value("summary")) or None
    # 账号明文已在 parse_bill 入口统一抹成 MD5（空账号留空串 → 这里转 None）。
    # 此值同时作为 ParsedRow.account_masked（落库列）与下游去重键的业务要素。
    account_masked = value("account") or None
    # 非交易行证券名称为空时，用摘要填充（如「银行转存」），避免默认成 CASH 无法辨识业务。
    # 名称与摘要同口径归一并落库：券商折行会写成「华泰紫金天\n天发货币」。
    security_name = normalize_name(value("security_name")) or (
        summary or "资金业务" if code == "CASH" else code
    )
    # 「[省心投]申购确认」视同银行转出（设计 D14）：主账户资金流出到省心投投顾账户，
    # 但转出动作不在账单留记录，申购确认行本身就是转出证据。转 CASH 资金行（与
    # 「银行转取」同构）：无份额语义（qty/price 清零，原始值留 raw 可溯源）、不进持仓；
    # 发生金额（负值）即主账户流出额，分类走 transfer（sys_sxt_out）。
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
    quarantined: list[QuarantineCandidate] = []
    derived_count = 0
    unsigned_amount_count = 0

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
