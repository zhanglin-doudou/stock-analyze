"""物化写回（Period2-05，E-01/E-11/C-41）。

- 只物化 `status=ok`（含 insufficient/pending_window 的 ok）；blocked/stale/unreliable 不落库；
- 主键确定性：`mtr_` + sha256(user|metric|period|version|params_hash)[:32]；
- 血缘只删当前 `(user, metric, period, definition_version)` 的行，**不跨版本删**；
- `is_stale` 是缓存命中判定的唯一入口（C-41），测试直接断言。
"""

from __future__ import annotations

import uuid
from typing import Any

from stock_review.metrics.engine import MetricOutcome
from stock_review.metrics.hash import object_id
from stock_review.repositories.metrics import MetricResultRepository


def metric_result_id(
    user_id: uuid.UUID,
    *,
    metric_id: str,
    period: str,
    definition_version: str,
    params_hash_value: str,
) -> str:
    return object_id("mtr", str(user_id), metric_id, period, definition_version, params_hash_value)


def dependency_id(
    user_id: uuid.UUID,
    *,
    metric_id: str,
    period: str,
    definition_version: str,
    dep_kind: str,
    dep_id: str,
) -> str:
    return object_id("mtd", str(user_id), metric_id, period, definition_version, dep_kind, dep_id)


def is_stale(row: Any, *, inputs_hash: str) -> bool:
    """缓存行是否过期：指纹不同或不是 ok（blocked/unreliable 不读旧行）。"""
    return getattr(row, "inputs_hash", None) != inputs_hash or getattr(row, "status", None) != "ok"


def result_values(user_id: uuid.UUID, outcome: MetricOutcome) -> dict[str, Any]:
    return {
        "id": metric_result_id(
            user_id,
            metric_id=outcome.metric_id,
            period=outcome.period,
            definition_version=outcome.definition_version,
            params_hash_value=outcome.params_hash,
        ),
        "metric_id": outcome.metric_id,
        "period": outcome.period,
        "params_hash": outcome.params_hash,
        "params": dict(outcome.params),
        "domain": outcome.domain,
        "scope": dict(outcome.scope),
        "status": outcome.status,
        "sample_status": outcome.sample_status,
        "value_units": outcome.value_units,
        "unit": outcome.unit,
        "precision": outcome.precision,
        "n": outcome.n,
        "n_pending": outcome.n_pending,
        "ci_low_units": outcome.ci_low_units,
        "ci_high_units": outcome.ci_high_units,
        "decomposition": dict(outcome.decomposition),
        "assumptions": dict(outcome.assumptions),
        "reason": outcome.reason,
        "inputs_hash": outcome.inputs_hash,
        "definition_version": outcome.definition_version,
        "data_as_of": outcome.data_as_of,
        "computed_at": outcome.computed_at,
    }


def dependency_rows(user_id: uuid.UUID, outcome: MetricOutcome) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    result_id = metric_result_id(
        user_id,
        metric_id=outcome.metric_id,
        period=outcome.period,
        definition_version=outcome.definition_version,
        params_hash_value=outcome.params_hash,
    )
    for item in outcome.lineage:
        dep_kind = str(item["dep_kind"])
        dep_id = str(item["dep_id"])
        rows.append(
            {
                "id": dependency_id(
                    user_id,
                    metric_id=outcome.metric_id,
                    period=outcome.period,
                    definition_version=outcome.definition_version,
                    dep_kind=dep_kind,
                    dep_id=dep_id,
                ),
                "metric_id": outcome.metric_id,
                "period": outcome.period,
                "definition_version": outcome.definition_version,
                "dep_kind": dep_kind,
                "dep_id": dep_id,
                "dep_hash": item.get("dep_hash"),
                "metric_result_id": result_id,
            }
        )
    return rows


async def persist_outcome(
    repository: MetricResultRepository,
    user_id: uuid.UUID,
    outcome: MetricOutcome,
    *,
    force: bool = False,
) -> bool:
    """ok 结果 UPSERT + 同批替换血缘；返回是否写入。

    `force=True`（API-083 构建编排路径）时非 ok 的 blocked/stale/unreliable 状态行
    也落库——构建场景需要把状态矩阵显式物化供指标页展示；读路径（E-01）保持只写
    ok，且缓存命中仍由 `is_stale` 把非 ok 行视为过期，两条路径互不污染。
    """
    if not force and not outcome.persistable:
        return False
    await repository.upsert(user_id, result_values(user_id, outcome))
    await repository.replace_dependencies(
        user_id,
        metric_id=outcome.metric_id,
        period=outcome.period,
        definition_version=outcome.definition_version,
        rows=dependency_rows(user_id, outcome),
    )
    return True


__all__ = [
    "dependency_id",
    "dependency_rows",
    "is_stale",
    "metric_result_id",
    "persist_outcome",
    "result_values",
]
