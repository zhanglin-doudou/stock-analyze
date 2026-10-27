"""Period2-05/06 物化集成测试（设计 §9：UPSERT 幂等/版本+参数保留/负缓存/写失败不阻断读/并发同键/血缘/DB CHECK）。

真实 PostgreSQL（owner 角色绕过 RLS 直写）；每个用例自造数据，不依赖执行顺序。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from stock_review.application.metrics import persist_safely
from stock_review.metrics.engine import MetricOutcome
from stock_review.metrics.persist import persist_outcome
from stock_review.repositories.metrics import MetricResultRepository

pytestmark = pytest.mark.integration

PERIOD = "2026-06"
NOW = datetime(2026, 6, 30, 15, 0, tzinfo=UTC)


def _outcome(
    *,
    metric_id: str = "perf.win_rate_stub",
    version: str = "1.0",
    params_hash: str = "a" * 64,
    status: str = "ok",
    value_units: int | None = 58_333_333,
    inputs_hash: str | None = None,
    lineage: tuple[dict[str, Any], ...] = (),
) -> MetricOutcome:
    return MetricOutcome(
        metric_id=metric_id,
        definition_version=version,
        domain="perf",
        scope={"unit": "round", "cohort": "close_date", "period": "month"},
        period=PERIOD,
        params={},
        params_hash=params_hash,
        inputs_hash=inputs_hash or ("sha256:" + "c" * 64),
        status=status,
        sample_status="ok",
        reason=None,
        value_units=value_units if status == "ok" else None,
        unit="ratio",
        precision=8,
        n=12,
        n_pending=0,
        ci_low_units=31_951_131 if status == "ok" else None,
        ci_high_units=80_673_969 if status == "ok" else None,
        decomposition={"wins": 7, "losses": 5},
        assumptions={"rf_rate": "0.015"},
        data_as_of=date(2026, 6, 10),
        computed_at=NOW,
        lineage=lineage,
    )


async def _count_results(engine: AsyncEngine, user_id: uuid.UUID) -> int:
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text("select count(*) from review.metric_result where user_id = :uid"),
                    {"uid": user_id},
                )
            ).scalar_one()
        )


async def _count_deps(engine: AsyncEngine, user_id: uuid.UUID, version: str) -> int:
    async with engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "select count(*) from review.metric_dependency "
                        "where user_id = :uid and definition_version = :version"
                    ),
                    {"uid": user_id, "version": version},
                )
            ).scalar_one()
        )


async def _new_user(engine: AsyncEngine) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                'insert into platform."user" (id, email, password_hash, role, status) '
                "values (:id, :email, 'x', 'user', 'active')"
            ),
            {"id": user_id, "email": f"u-{user_id.hex[:8]}@test.dev"},
        )
    return user_id


@pytest.mark.asyncio
async def test_upsert_idempotent_same_unique_key(owner_engine: AsyncEngine):
    """同唯一键两次 UPSERT：行数不变、value 以最后写入为准。"""
    user_id = await _new_user(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        await persist_outcome(repo, user_id, _outcome(value_units=50_000_000))
        await session.commit()
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        await persist_outcome(repo, user_id, _outcome(value_units=58_333_333))
        await session.commit()

    assert await _count_results(owner_engine, user_id) == 1
    async with owner_engine.begin() as conn:
        value = (
            await conn.execute(
                text("select value_units from review.metric_result where user_id = :uid"),
                {"uid": user_id},
            )
        ).scalar_one()
    assert int(value) == 58_333_333


@pytest.mark.asyncio
async def test_version_and_params_rows_coexist(owner_engine: AsyncEngine):
    """口径 bump / 参数不同 → 旧行保留、新行插入（趋势断点可读历史段）。"""
    user_id = await _new_user(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        await persist_outcome(repo, user_id, _outcome(version="1.0"))
        await persist_outcome(repo, user_id, _outcome(version="2.0"))
        await persist_outcome(repo, user_id, _outcome(version="1.0", params_hash="b" * 64))
        await session.commit()
    assert await _count_results(owner_engine, user_id) == 3


@pytest.mark.asyncio
async def test_negative_cache_blocked_not_persisted(owner_engine: AsyncEngine):
    """负缓存：blocked/stale/unreliable 不落库。"""
    user_id = await _new_user(owner_engine)
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        for status in ("blocked", "stale", "unreliable"):
            written = await persist_outcome(repo, user_id, _outcome(status=status))
            assert written is False
        await session.commit()
    assert await _count_results(owner_engine, user_id) == 0


@pytest.mark.asyncio
async def test_persist_safely_swallows_write_failure(owner_engine: AsyncEngine):
    """写失败不阻断读：坏行只记日志，好行仍写入，返回计数不含失败项。"""
    user_id = await _new_user(owner_engine)
    good = _outcome(metric_id="perf.good_stub")
    bad = _outcome(metric_id="perf.bad_stub", inputs_hash="x" * 200)  # String(80) 溢出
    async with AsyncSession(owner_engine) as session:
        written = await persist_safely(session, user_id, [good, bad])
        await session.commit()
    assert written == 1
    assert await _count_results(owner_engine, user_id) == 1


@pytest.mark.asyncio
async def test_concurrent_same_key_upsert(owner_engine: AsyncEngine):
    """并发同键 UPSERT：无唯一冲突异常，最终 1 行。"""
    user_id = await _new_user(owner_engine)

    async def write(value: int) -> None:
        async with AsyncSession(owner_engine) as session:
            repo = MetricResultRepository(session)
            await persist_outcome(repo, user_id, _outcome(value_units=value))
            await session.commit()

    await asyncio.gather(write(50_000_000), write(58_333_333), write(60_000_000))
    assert await _count_results(owner_engine, user_id) == 1


@pytest.mark.asyncio
async def test_dependency_replaces_only_current_version(owner_engine: AsyncEngine):
    """血缘替换只清当前 `(user, metric, period, version)`；旧版本血缘保留。"""
    user_id = await _new_user(owner_engine)
    lineage = ({"dep_kind": "dataset", "dep_id": "rounds", "dep_hash": "sha256:" + "d" * 64},)
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        await persist_outcome(repo, user_id, _outcome(version="1.0", lineage=lineage))
        await persist_outcome(repo, user_id, _outcome(version="2.0", lineage=lineage))
        await session.commit()
    assert await _count_deps(owner_engine, user_id, "1.0") == 1
    assert await _count_deps(owner_engine, user_id, "2.0") == 1

    # 重新物化 1.0：1.0 血缘被替换（仍 1 行），2.0 不受影响
    async with AsyncSession(owner_engine) as session:
        repo = MetricResultRepository(session)
        await persist_outcome(repo, user_id, _outcome(version="1.0", lineage=lineage))
        await session.commit()
    assert await _count_deps(owner_engine, user_id, "1.0") == 1
    assert await _count_deps(owner_engine, user_id, "2.0") == 1


@pytest.mark.asyncio
async def test_db_check_constraints(owner_engine: AsyncEngine):
    """DB CHECK：非 ok 不得带 value；ci 必须成对。"""
    user_id = await _new_user(owner_engine)
    base = {
        "id": f"mtr_{uuid.uuid4().hex[:32]}",
        "user_id": user_id,
        "metric_id": "perf.check_stub",
        "period": PERIOD,
        "params_hash": "a" * 64,
        "params": "{}",
        "domain": "perf",
        "scope": "{}",
        "unit": "ratio",
        "precision": 8,
        "n": 0,
        "n_pending": 0,
        "inputs_hash": "sha256:" + "c" * 64,
        "definition_version": "1.0",
        "data_as_of": date(2026, 6, 10),
        "computed_at": NOW,
    }

    async def try_insert(**overrides: Any) -> bool:
        row = {**base, "id": f"mtr_{uuid.uuid4().hex[:32]}", **overrides}
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        try:
            async with owner_engine.begin() as conn:
                await conn.execute(
                    text(f"insert into review.metric_result ({columns}) values ({placeholders})"),
                    row,
                )
            return True
        except Exception:
            return False

    # 合法 ok 行可插入
    assert await try_insert(
        status="ok",
        sample_status="ok",
        value_units=1,
        ci_low_units=0,
        ci_high_units=2,
    )
    # 非 ok 带 value → CHECK 拒绝
    assert not await try_insert(status="blocked", sample_status="blocked", value_units=1)
    # ci 不成对 → CHECK 拒绝
    assert not await try_insert(
        status="ok", sample_status="ok", value_units=1, ci_low_units=0, ci_high_units=None
    )
