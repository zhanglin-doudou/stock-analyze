"""stock_contracts.enums 单测：跨上下文枚举取值稳定，terminal 判定正确。"""

from __future__ import annotations

from stock_contracts.enums import (
    ImportBatchStatus,
    JobStage,
    JobStatus,
    JobType,
    MetricStatus,
    QuarantineReason,
    ReconciliationStatus,
    Role,
    SampleStatus,
    ScopeType,
    Side,
    TokenScope,
    TradeSource,
    WarningCode,
)


def test_role_and_scope() -> None:
    assert {r.value for r in Role} == {"user", "admin"}
    assert {s.value for s in TokenScope} == {"read", "write"}


def test_side_and_source() -> None:
    assert {s.value for s in Side} == {"buy", "sell", "unknown", "missing"}
    assert {s.value for s in TradeSource} == {"import", "manual", "system"}


def test_job_status_terminal() -> None:
    assert JobStatus.SUCCEEDED.is_terminal
    assert JobStatus.FAILED.is_terminal
    assert JobStatus.CANCELLED.is_terminal
    assert not JobStatus.PENDING.is_terminal
    assert not JobStatus.RUNNING.is_terminal


def test_import_batch_status_values() -> None:
    assert {s.value for s in ImportBatchStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "revoked",
    }


def test_other_enum_values() -> None:
    assert JobType.IMPORT_COMMIT.value == "import_commit"
    assert JobType.IMPORT_RECLASSIFY.value == "reclassify"
    assert MetricStatus.STALE.value == "stale"
    assert SampleStatus.INSUFFICIENT.value == "insufficient"
    assert ScopeType.SECURITY.value == "security"
    assert ReconciliationStatus.DEGRADED.value == "degraded"
    assert JobStage.WRITING.value == "writing"
    assert QuarantineReason.DUPLICATE_IN_FILE.value == "DUPLICATE_IN_FILE"
    assert WarningCode.MISSING_CASH_BALANCE.value == "MISSING_CASH_BALANCE"
