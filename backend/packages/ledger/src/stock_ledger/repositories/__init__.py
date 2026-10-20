"""租户安全仓储层。

每个方法都以 `user_id` 作为首个业务参数，且所有 SQL 都带 `user_id` 过滤——
这是多租户隔离的第一道防线（第二道是 RLS）。跨账号 ID 一律按“不存在”处理，
抛 `NotFoundError` 而不是 403：不向调用方泄露“这个 ID 存在但不属于你”。
"""

from __future__ import annotations

from stock_ledger.repositories.imports import ImportBatchRepository, ImportPreviewRepository
from stock_ledger.repositories.quarantine import QuarantineRepository
from stock_ledger.repositories.reconciliation import ReconciliationRepository
from stock_ledger.repositories.rules import SummaryRuleRepository, seed_system_rules
from stock_ledger.repositories.trades import TradeRepository, TradeRow, decode_cursor, encode_cursor

__all__ = [
    "ImportBatchRepository",
    "ImportPreviewRepository",
    "QuarantineRepository",
    "ReconciliationRepository",
    "SummaryRuleRepository",
    "TradeRepository",
    "TradeRow",
    "decode_cursor",
    "encode_cursor",
    "seed_system_rules",
]
