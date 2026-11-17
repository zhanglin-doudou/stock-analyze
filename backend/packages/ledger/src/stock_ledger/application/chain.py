"""导入对账链规划：选起点、沿余额链接受/隔离、首次导入头部重试。

数据库账单不变量：库内账单恒连续（允许缺首尾）。本模块只做**只读**规划、不写库：

- `walk_chain`：逐行核对 `上一行余额 + 本行带符号金额 == 本行余额`；对不上时从该行起
  连续隔离到链尾（锚点已不可信，继续接受会把缺口吞掉），首条断行留差额。
- `walk_chain_with_head_retry`：首次导入（库为空）头部最多试 5 个候选锚点，取接受行数
  最多者，并列取最早。
- `plan_chain`：按文件首末日期与库内首末账单选模式：

  多资金子账户账单（同一账单里有多个「资金账号」）先把文件行按账号拆成多条独立链，
  各自判定起点与连续性，再把结果合并回一份账单（`plan_chain_by_account`）；没有
  账号列的账单按单账户处理。

  | 场景 | 模式 | 起点 |
  | --- | --- | --- |
  | 库为空 | `empty` | 文件首行（头部重试） |
  | 文件首日在库中且库末覆盖文件末 | `covered` | 整文件 no-op |
  | 文件首日在库中但库末未覆盖 | `append` | 库内最后一条账单 |
  | 文件首日早于库首且文件末也早于库首 | `backfill` | 文件首行，末行须与库首闭合 |
  | 文件首日早于库首、但文件末已进库内（跨库首/两端） | `replace` | 文件首行，按文件覆盖区间 |
  | 文件首日晚于库末 | `append` | 库内最后一条账单 |
  | 文件区间落在库内（覆盖区段） | `replace` | 文件首行，按文件覆盖区间 |

边界口径：
- 同一日期中排在锚点之前的文件行直接跳过（已在库中，不重复导入）；
- 本行无资金余额 → 无法核对，接受、金额计入待核对合计但不推进锚点（与 reconcile 一致）；
- 向前补历史接不上 → `IMPORT_CHAIN_DISCONTINUOUS`（整批回滚，报断行行号与差额）；
- 增量追加的接缝接不上 → 同上（说明中间区间账单缺失/被撤销，不能静默隔离成 0 行）；
- 覆盖区段与库内前后剩余账单的接缝不闭合 → 同上（覆盖本身不再是拒绝理由）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from stock_contracts.constants import EPS_AMT
from stock_contracts.enums import QuarantineReason
from stock_contracts.errors import ApiError, ErrorCode, FieldError
from stock_core.values import Money

from stock_ledger.models import Trade
from stock_ledger.parsers.huatai import ParsedBill, ParsedRow, QuarantineCandidate
from stock_ledger.repositories.trades import TradeRepository

#: 首次导入头部最多尝试的候选锚点数量（口径：前 5 条候选，取接受行数最多者）。
HEAD_RETRY_CANDIDATES = 5


class ChainMode(StrEnum):
    EMPTY = "empty"
    COVERED = "covered"
    APPEND = "append"
    BACKFILL = "backfill"
    REPLACE = "replace"


@dataclass(slots=True)
class ChainWalk:
    """一次沿链行走的结果（纯值对象）。"""

    accepted: list[ParsedRow] = field(default_factory=list)
    quarantined: list[QuarantineCandidate] = field(default_factory=list)
    last_balance_units: int | None = None
    last_row: ParsedRow | None = None
    skipped: int = 0


@dataclass(slots=True)
class ChainPlan:
    """提交前的对账链规划；`accepted` 之外的行一律不参与分类/计数/记忆。"""

    mode: ChainMode
    accepted: list[ParsedRow]
    quarantined: list[QuarantineCandidate]
    skipped: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AccountChainPlan:
    """单个资金子账户的链规划。"""

    account: str | None
    plan: ChainPlan


@dataclass(slots=True)
class MultiChainPlan:
    """多子账户账单的整体规划：各子账户独立对账，结果按文件行序合并。"""

    groups: list[AccountChainPlan] = field(default_factory=list)

    @property
    def accepted(self) -> list[ParsedRow]:
        return sorted((row for group in self.groups for row in group.plan.accepted), key=_line_no)

    @property
    def quarantined(self) -> list[QuarantineCandidate]:
        return sorted(
            (item for group in self.groups for item in group.plan.quarantined),
            key=lambda item: item.line_no,
        )

    @property
    def skipped(self) -> int:
        return sum(group.plan.skipped for group in self.groups)

    @property
    def mode(self) -> ChainMode:
        """整体模式：全组 covered 才算 covered，否则取需要写库动作最强的一档。"""
        modes = {group.plan.mode for group in self.groups}
        if not modes:
            return ChainMode.EMPTY
        if modes == {ChainMode.COVERED}:
            return ChainMode.COVERED
        for mode in (ChainMode.REPLACE, ChainMode.BACKFILL, ChainMode.APPEND):
            if mode in modes:
                return mode
        return ChainMode.EMPTY

    @property
    def coverage(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "accounts_count": len(self.groups),
            "accounts": [
                {"account": group.account, **group.plan.coverage} for group in self.groups
            ],
        }

    def replace_ranges(self) -> list[tuple[str | None, date, date]]:
        """需要「以文件为准覆盖」的子账户区间（供写库前按账户软删）。"""
        ranges: list[tuple[str | None, date, date]] = []
        for group in self.groups:
            if group.plan.mode != ChainMode.REPLACE or not group.plan.accepted:
                continue
            raw_from = group.plan.coverage.get("from")
            raw_to = group.plan.coverage.get("to")
            if raw_from is None or raw_to is None:
                dates = [row.trade_date for row in group.plan.accepted]
                raw_from, raw_to = min(dates).isoformat(), max(dates).isoformat()
            ranges.append(
                (group.account, date.fromisoformat(str(raw_from)), date.fromisoformat(str(raw_to)))
            )
        return ranges


def _line_no(row: ParsedRow) -> int:
    return row.line_no


def group_rows_by_account(rows: list[ParsedRow]) -> list[tuple[str | None, list[ParsedRow]]]:
    """按资金账号把文件行拆成各自独立的余额链。

    空账号行继承上下文最近出现的账户（账单里利息/费用行常省略账号）；文件首部
    尚无账户时先挂起，遇到首个账户后并入。整份账单没有账号列 → 单组 `None`，
    即「默认按一个账户处理」。
    """
    groups: dict[str, list[ParsedRow]] = {}
    order: list[str] = []
    current: str | None = None
    pending: list[ParsedRow] = []
    for row in rows:
        account = row.account_masked
        if account:
            if account not in groups:
                groups[account] = []
                order.append(account)
            if pending:
                groups[account].extend(pending)
                pending = []
            current = account
            groups[account].append(row)
        elif current is None:
            pending.append(row)
        else:
            groups[current].append(row)
    if not order:
        return [(None, pending)]
    return [(account, groups[account]) for account in order]


def _break_candidate(
    row: ParsedRow,
    *,
    delta_units: int | None = None,
    expected_units: int | None = None,
    actual_units: int | None = None,
    counterpart: str | None = None,
) -> QuarantineCandidate:
    """链式核对不通过的行 → 隔离候选（`suggested_rule_id` 恒为空）。

    首条断行的差额/期望/实际写进 `raw` 的 `_chain_*` 保留键，让导入记录页与排查
    直接看到「断在哪、差多少」；后续连带隔离的行只保留原始内容。
    """
    raw = {**row.raw, "summary": row.summary or "", "line_no": str(row.line_no)}
    if delta_units is not None:
        raw.update(
            {
                "_chain_delta_units": str(delta_units),
                "_chain_delta": str(Money(delta_units)),
                "_chain_expected_units": str(expected_units),
                "_chain_actual_units": str(actual_units),
                "_chain_counterpart": counterpart or "",
            }
        )
    return QuarantineCandidate(
        line_no=row.line_no,
        raw=raw,
        reason_code=QuarantineReason.RECONCILIATION_BREAK.value,
        suggested_rule_id=None,
    )


def _skipped_before(row: ParsedRow, anchor: tuple[date, int | None] | None) -> bool:
    """同一日期中排在锚点之前的文件行：直接跳过（不得重复导入）。"""
    if anchor is None:
        return False
    anchor_date, anchor_line = anchor
    if row.trade_date < anchor_date:
        return True
    return row.trade_date == anchor_date and anchor_line is not None and row.line_no <= anchor_line


def walk_chain(
    rows: list[ParsedRow],
    *,
    start_balance_units: int | None,
    epsilon_units: int,
    skip_before: tuple[date, int | None] | None = None,
) -> ChainWalk:
    """沿文件行序核对余额链；断行起连续隔离到链尾。

    `start_balance_units is None` 表示还没有可信锚点：第一个带余额的行直接作为起点
    接受（它的余额不校验，因为没有上一行可比）。

    锚点只由**通过核对**的行推进：无余额行与方向未知行的金额先并入 `pending_units`，
    由下一条可核对行整体验证，绝不用它们自己的余额重设基准（否则缺口会被悄悄吸收）。
    一旦核对失败，锚点已被证明不可信，后续任何「接受」都可能吞掉缺口，因此从断行起
    **连续隔离到链尾**，保留已核对的前缀。
    """
    walk = ChainWalk(last_balance_units=start_balance_units)
    pending_units = 0
    # 最近一条**通过核对**的行：差额是指「相对它」的偏差；方向未知/无余额行不能当锚点，
    # 所以排查提示里的 counterpart 也不能指向它们。
    anchor_row: ParsedRow | None = None
    for index, row in enumerate(rows):
        if _skipped_before(row, skip_before):
            walk.skipped += 1
            continue
        balance = row.cash_balance_units
        if row.amount_units == 0 and balance is not None and pending_units == 0:
            # 零资金流的份额行（红利发放/非交易过户确认等）：不参与余额链核对。
            # 它们不改变现金，券商在这类行常把「资金余额」填 0.00，不能据此断链，
            # 也不推进锚点（`last_row` 留给最后一条真实资金行做接缝核对）。
            # 若前面还有待核对金额（pending_units != 0），仍需在此行验证缺口。
            walk.accepted.append(row)
            continue
        if balance is None:
            # 无余额无法核对：接受、金额记入待核对合计，不推进锚点。
            walk.accepted.append(row)
            walk.last_row = row
            pending_units += row.amount_units
            continue
        if walk.last_balance_units is None:
            walk.accepted.append(row)
            walk.last_balance_units = balance
            walk.last_row = row
            anchor_row = row
            pending_units = 0
            continue
        expected = walk.last_balance_units + pending_units + row.amount_units
        delta = balance - expected
        if abs(delta) <= epsilon_units:
            walk.accepted.append(row)
            walk.last_balance_units = balance
            walk.last_row = row
            anchor_row = row
            pending_units = 0
            continue
        counterpart = f"第 {anchor_row.line_no} 行" if anchor_row else "起始锚点"
        walk.quarantined.append(
            _break_candidate(
                row,
                delta_units=delta,
                expected_units=expected,
                actual_units=balance,
                counterpart=counterpart,
            )
        )
        for rest in rows[index + 1 :]:
            if _skipped_before(rest, skip_before):
                walk.skipped += 1
                continue
            walk.quarantined.append(_break_candidate(rest))
        break
    return walk


def walk_chain_with_head_retry(
    rows: list[ParsedRow],
    *,
    epsilon_units: int,
    max_candidates: int = HEAD_RETRY_CANDIDATES,
) -> ChainWalk:
    """首次导入：头部最多试 `max_candidates` 个锚点，取接受行数最多者，并列取最早。"""
    # 零资金流行的「资金余额」列不可信（常填 0.00），不能作为锚点候选。
    candidates = [
        index
        for index, row in enumerate(rows)
        if row.cash_balance_units is not None and row.amount_units != 0
    ][:max_candidates]
    if not candidates:
        return walk_chain(rows, start_balance_units=None, epsilon_units=epsilon_units)

    best: ChainWalk | None = None
    for index in candidates:
        anchor_row = rows[index]
        rest = walk_chain(
            rows[index + 1 :],
            start_balance_units=anchor_row.cash_balance_units,
            epsilon_units=epsilon_units,
        )
        # 锚点之前的零资金流行（零金额份额行等）不参与核对，直接接受，不当作断链。
        prefix_accepted = [
            row
            for row in rows[:index]
            if row.amount_units == 0 and row.cash_balance_units is not None
        ]
        prefix_quarantined = [
            _break_candidate(row)
            for row in rows[:index]
            if not (row.amount_units == 0 and row.cash_balance_units is not None)
        ]
        candidate = ChainWalk(
            accepted=[*prefix_accepted, anchor_row, *rest.accepted],
            quarantined=prefix_quarantined + rest.quarantined,
            last_balance_units=rest.last_balance_units,
            last_row=rest.last_row or anchor_row,
            skipped=rest.skipped,
        )
        if best is None or len(candidate.accepted) > len(best.accepted):
            best = candidate
    assert best is not None  # candidates 非空 → 至少一次
    return best


def _discontinuous(
    *, line_no: int | None, delta_units: int, counterpart: str, detail: str
) -> ApiError:
    """断链错误：带断行行号与差额（分），供前端直接定位。"""
    field = str(line_no if line_no is not None else "chain")
    return ApiError(
        ErrorCode.IMPORT_CHAIN_DISCONTINUOUS,
        errors=[
            FieldError(
                field,
                "CHAIN_DISCONTINUOUS",
                f"与{counterpart}相差 {Money(delta_units)}",
            )
        ],
        detail=detail,
        metadata={
            "line_no": line_no,
            "delta_units": delta_units,
            "delta": str(Money(delta_units)),
            "counterpart": counterpart,
        },
    )


def _verify_backfill(walk: ChainWalk, db_first: Trade, *, epsilon_units: int) -> None:
    """向前补历史：文件末条接受行必须与库内首条账单余额闭合。"""
    last = walk.last_row
    if last is None or last.cash_balance_units is None or db_first.cash_balance_units is None:
        return  # 无余额信息无法证明连续性（降级处理，不伪造结论）
    expected = last.cash_balance_units + db_first.amount_units
    delta = int(db_first.cash_balance_units) - expected
    if abs(delta) > epsilon_units:
        raise _discontinuous(
            line_no=last.line_no,
            delta_units=delta,
            counterpart=f"库内首条账单（{db_first.trade_date.isoformat()}）",
            detail=(
                f"向前补历史在文件第 {last.line_no} 行断开："
                f"上一行余额 + 本行金额与库内首条账单余额相差 {Money(delta)}"
            ),
        )


def _verify_append_seam(
    walk: ChainWalk,
    *,
    previous: Trade,
    rows: list[ParsedRow],
) -> None:
    """增量追加：文件首条可核对行必须与库内最后一条账单闭合。

    接不上说明库内与本文件之间存在缺口（中间区间账单未导入，或该区间的批次被撤销），
    此时 `walk_chain` 会把整份文件从第一行隔离到链尾，预览变成 0 行、类型步骤为空，
    提交还会“成功写入 0 行”。因此这里必须显式报断链，让用户先补导缺失区间。

    仅处理**接缝**断点：`_chain_counterpart == "起始锚点"` 表示第一条可核对行就对不上
    库内锚点；若断点在文件内部（已有多行通过核对），保持“断行起隔离到链尾”的既有语义。
    """
    if not walk.quarantined:
        return
    first_break = walk.quarantined[0]
    if first_break.raw.get("_chain_counterpart") != "起始锚点":
        return
    delta = int(first_break.raw.get("_chain_delta_units") or 0)
    expected = int(first_break.raw.get("_chain_expected_units") or 0)
    actual = int(first_break.raw.get("_chain_actual_units") or 0)
    db_balance = (
        str(Money(int(previous.cash_balance_units)))
        if previous.cash_balance_units is not None
        else "未知"
    )
    file_from = min(row.trade_date for row in rows) if rows else previous.trade_date
    gap_hint = (
        f"{previous.trade_date.isoformat()}（库内最后一条）与 "
        f"{file_from.isoformat()}（本文件首日）之间"
    )
    raise _discontinuous(
        line_no=first_break.line_no,
        delta_units=delta,
        counterpart=f"库内最后一条账单（{previous.trade_date.isoformat()}）",
        detail=(
            f"本文件第 {first_break.line_no} 行接不上库内最后一条账单"
            f"（{previous.trade_date.isoformat()}，余额 {db_balance}）："
            f"按库内余额推算应为 {Money(expected)}，账单实际为 {Money(actual)}，"
            f"相差 {Money(delta)}。{gap_hint}的账单可能尚未导入（或被撤销），"
            "请先补导该区间后再导入本文件。"
        ),
    )


def _verify_seam(
    *,
    previous: Trade | None,
    first: ParsedRow | None,
    last: ParsedRow | None,
    following: Trade | None,
    epsilon_units: int,
) -> None:
    """覆盖区段接缝：库内前一条 + 文件首行、文件末行 + 库内后一条都必须闭合。"""
    if (
        previous is not None
        and previous.cash_balance_units is not None
        and first is not None
        and first.cash_balance_units is not None
    ):
        expected = int(previous.cash_balance_units) + first.amount_units
        delta = int(first.cash_balance_units) - expected
        if abs(delta) > epsilon_units:
            raise _discontinuous(
                line_no=first.line_no,
                delta_units=delta,
                counterpart=f"库内前一条账单（{previous.trade_date.isoformat()}）",
                detail=(
                    f"覆盖区段起点第 {first.line_no} 行与库内前一条账单接不上，相差 {Money(delta)}"
                ),
            )
    if (
        following is not None
        and following.cash_balance_units is not None
        and last is not None
        and last.cash_balance_units is not None
    ):
        expected = int(last.cash_balance_units) + following.amount_units
        delta = int(following.cash_balance_units) - expected
        if abs(delta) > epsilon_units:
            raise _discontinuous(
                line_no=last.line_no,
                delta_units=delta,
                counterpart=f"库内后一条账单（{following.trade_date.isoformat()}）",
                detail=(
                    f"覆盖区段末行第 {last.line_no} 行与库内后一条账单接不上，相差 {Money(delta)}"
                ),
            )


def _covered_plan(rows: list[ParsedRow], *, db_first: Trade, db_last: Trade) -> ChainPlan:
    file_from = min(row.trade_date for row in rows)
    file_to = max(row.trade_date for row in rows)
    return ChainPlan(
        mode=ChainMode.COVERED,
        accepted=[],
        quarantined=[],
        skipped=len(rows),
        coverage={
            "mode": ChainMode.COVERED.value,
            "row_count": len(rows),
            "file_from": file_from.isoformat(),
            "file_to": file_to.isoformat(),
            "db_from": db_first.trade_date.isoformat(),
            "db_to": db_last.trade_date.isoformat(),
        },
    )


async def plan_chain(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    rows: list[ParsedRow],
    epsilon: Decimal = EPS_AMT,
    force_replace: bool = False,
    account: str | None = None,
) -> ChainPlan:
    """按库内首末账单与文件日期范围选择对账模式，并沿链给出接受/隔离结果。

    `force_replace`：调用方显式指定 `replace_range` 时置真——即便文件日期已被库内覆盖，
    也要按文件重写（不能用 covered 短路，否则「删而不插」）。
    `account`：只与该资金子账户的库内账单比对（多子账户账单各自判定起点与连续性）。
    """
    epsilon_units = Money.from_decimal(epsilon).units
    if not rows:
        return ChainPlan(mode=ChainMode.EMPTY, accepted=[], quarantined=[])

    file_from = min(row.trade_date for row in rows)
    file_to = max(row.trade_date for row in rows)
    repo = TradeRepository(session)
    first = await repo.first_bill(user_id, account=account)
    last = await repo.last_bill(user_id, account=account)
    if first is None or last is None:
        walk = walk_chain_with_head_retry(rows, epsilon_units=epsilon_units)
        return ChainPlan(
            mode=ChainMode.EMPTY,
            accepted=walk.accepted,
            quarantined=walk.quarantined,
            skipped=walk.skipped,
            coverage={"mode": ChainMode.EMPTY.value},
        )
    if force_replace:
        walk = walk_chain(rows, start_balance_units=None, epsilon_units=epsilon_units)
        return ChainPlan(
            mode=ChainMode.REPLACE,
            accepted=walk.accepted,
            quarantined=walk.quarantined,
            skipped=walk.skipped,
            coverage={
                "mode": ChainMode.REPLACE.value,
                "from": file_from.isoformat(),
                "to": file_to.isoformat(),
                "forced": True,
            },
        )

    same_day = await repo.bill_on_date(user_id, file_from, account=account)
    anchor = (last.trade_date, last.line_no)
    anchor_balance = last.cash_balance_units

    if same_day is not None:
        if last.trade_date >= file_to:
            return _covered_plan(rows, db_first=first, db_last=last)
        walk = walk_chain(
            rows,
            start_balance_units=anchor_balance,
            epsilon_units=epsilon_units,
            skip_before=anchor,
        )
        _verify_append_seam(walk, previous=last, rows=rows)
        return ChainPlan(
            mode=ChainMode.APPEND,
            accepted=walk.accepted,
            quarantined=walk.quarantined,
            skipped=walk.skipped,
            coverage={"mode": ChainMode.APPEND.value, "anchor": "db_last"},
        )

    if file_from < first.trade_date:
        if file_to < first.trade_date:
            # 纯向前补历史：不与库内区间重叠，末行必须与库首闭合。
            walk = walk_chain(rows, start_balance_units=None, epsilon_units=epsilon_units)
            _verify_backfill(walk, first, epsilon_units=epsilon_units)
            return ChainPlan(
                mode=ChainMode.BACKFILL,
                accepted=walk.accepted,
                quarantined=walk.quarantined,
                skipped=walk.skipped,
                coverage={"mode": ChainMode.BACKFILL.value, "anchor": "file_first"},
            )
        # 文件跨库首（可能也跨库末）：以文件为准覆盖 [file_from, file_to]，
        # 不再拒绝「跨库两端」，也不要求用户拆分文件。
        walk = walk_chain(rows, start_balance_units=None, epsilon_units=epsilon_units)
        accepted = walk.accepted
        following = (
            await repo.bill_after(user_id, file_to, account=account)
            if file_to < last.trade_date
            else None
        )
        if accepted:
            _verify_seam(
                previous=None,
                first=accepted[0],
                last=accepted[-1],
                following=following,
                epsilon_units=epsilon_units,
            )
        return ChainPlan(
            mode=ChainMode.REPLACE,
            accepted=accepted,
            quarantined=walk.quarantined,
            skipped=walk.skipped,
            coverage={
                "mode": ChainMode.REPLACE.value,
                "from": file_from.isoformat(),
                "to": file_to.isoformat(),
                "spans": True,
            },
        )

    if file_from > last.trade_date:
        walk = walk_chain(
            rows,
            start_balance_units=anchor_balance,
            epsilon_units=epsilon_units,
            skip_before=anchor,
        )
        _verify_append_seam(walk, previous=last, rows=rows)
        return ChainPlan(
            mode=ChainMode.APPEND,
            accepted=walk.accepted,
            quarantined=walk.quarantined,
            skipped=walk.skipped,
            coverage={"mode": ChainMode.APPEND.value, "anchor": "db_last"},
        )

    # 文件区间落在库内：以文件为准覆盖 [file_from, file_to]，重算受影响的配对/回合/资产。
    walk = walk_chain(rows, start_balance_units=None, epsilon_units=epsilon_units)
    previous = await repo.bill_before(user_id, file_from, account=account)
    following = await repo.bill_after(user_id, file_to, account=account)
    accepted = walk.accepted
    if accepted:
        _verify_seam(
            previous=previous,
            first=accepted[0],
            last=accepted[-1],
            following=following,
            epsilon_units=epsilon_units,
        )
    return ChainPlan(
        mode=ChainMode.REPLACE,
        accepted=accepted,
        quarantined=walk.quarantined,
        skipped=walk.skipped,
        coverage={
            "mode": ChainMode.REPLACE.value,
            "from": file_from.isoformat(),
            "to": file_to.isoformat(),
        },
    )


async def plan_chain_by_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    rows: list[ParsedRow],
    epsilon: Decimal = EPS_AMT,
    force_replace: bool = False,
) -> MultiChainPlan:
    """按资金子账户拆分后分别对账，再把接受/隔离结果按文件行序合并。

    单子账户或没有账号列（`account is None`）时退化为原来的单链规划。
    """
    groups = group_rows_by_account(rows)
    plans: list[AccountChainPlan] = []
    for account, group_rows in groups:
        plan = await plan_chain(
            session,
            user_id=user_id,
            rows=group_rows,
            epsilon=epsilon,
            force_replace=force_replace,
            account=account,
        )
        plans.append(AccountChainPlan(account=account, plan=plan))
    return MultiChainPlan(groups=plans)


def apply_chain_plan(bill: ParsedBill, plan: ChainPlan) -> None:
    """把规划结果落回解析结果：只保留接受行，断行进隔离区并留痕。"""
    bill.rows = plan.accepted
    bill.quarantined.extend(plan.quarantined)
    if plan.coverage:
        bill.assumptions["coverage"] = plan.coverage


def apply_multi_chain_plan(bill: ParsedBill, plan: MultiChainPlan) -> None:
    """多子账户规划落回解析结果（口径同 `apply_chain_plan`）。"""
    bill.rows = plan.accepted
    bill.quarantined.extend(plan.quarantined)
    if plan.coverage:
        bill.assumptions["coverage"] = plan.coverage


__all__ = [
    "HEAD_RETRY_CANDIDATES",
    "AccountChainPlan",
    "ChainMode",
    "ChainPlan",
    "ChainWalk",
    "MultiChainPlan",
    "apply_chain_plan",
    "apply_multi_chain_plan",
    "group_rows_by_account",
    "plan_chain",
    "plan_chain_by_account",
    "walk_chain",
    "walk_chain_with_head_retry",
]
