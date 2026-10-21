# 一期 P0 修复记录：P0-2（replace_range 数据丢失）/ P0-3（admin PAT 越权）

> 日期：2026-10-21 ｜ 基线：`b61d441` + 本次工作区修改 ｜ 关联报告：[Period1-CodeReview-and-Acceptance.md](Period1-CodeReview-and-Acceptance.md)
> 范围：仅修 P0-2 / P0-3 及其直接耦合缺陷（撤销后重导、restore 冲突），未动 P0-1 / P0-4 / P0-5。
> 未提交 git；所有改动在工作区，供审阅后自行提交。

## 0. 结论

| 项 | 修复前 | 修复后 | 证据 |
|---|---|---|---|
| P0-2 `replace_range` | 先软删区间旧行、再被"软删行仍占去重键"全部 skip → `inserted=0`、流水 120→0 | 只按**有效行**去重 + 部分唯一索引；`replace_range` 使用请求体 `range` → `inserted=120`、流水保持 120 | 单测 3 条 + 真实 HTTP 复验 |
| P0-3 admin PAT | PAT 一律解析为 `Role.USER` → admin 用 PAT 读 `/trades` 返回 200 | PAT 回查所属用户真实角色 → admin PAT 返回 403 `ADMIN_CANNOT_READ_TRADES`；普通用户 PAT 仍 200 | 单测 2 条 + 真实 HTTP 复验 |
| 连带 P1-3 撤销后重导 | 撤销（软删）后同文件重导 `inserted=0`，用户无法恢复 | 软删行不占去重键 → 重导 `inserted=120`、行数恢复 | 单测 1 条 + 真实 HTTP 复验 |
| 连带 restore 冲突 | 重导后恢复旧行会撞唯一索引 → 500 | 恢复前检查同键有效行，显式返回 422 `VALIDATION_ERROR` | 单测 1 条 |

**质量门禁（全量、串行执行）：** ruff ✅、ruff format ✅、mypy strict ✅（69 文件 0 error）、快速单测 86 passed、core 覆盖 100%、API 套件 **97 passed**（原 90 + 新增 7）、越权 18 passed、RLS 4 passed、架构约束 1 passed。**真实 HTTP 复验 14/14**；原有 §2.1 端到端回归 **23/23**。

## 1. 变更清单

### 1.1 去重键：软删行不再占键（P0-2 / P1-3 的根因）

- `backend/packages/ledger/src/stock_ledger/repositories/trades.py`
  - `existing_dedupe_keys()` 增加 `Trade.deleted_at.is_(None)`：只统计有效行。
  - `restore()` 增加"同 dedupe_key + occurrence_idx 的有效行已存在"检查，冲突时抛 `ApiError(VALIDATION_ERROR)`（422），避免 DB 唯一索引异常变成 500。
- `backend/packages/ledger/src/stock_ledger/models.py`
  - `Trade.__table_args__`：`UniqueConstraint(user_id, dedupe_key)` → **部分唯一索引**
    `Index("uq_trade_user_dedupe", user_id, dedupe_key, occurrence_idx, unique=True, postgresql_where=text("deleted_at IS NULL"))`。
    与 `docs/Architecture-v2.md` §9.3「`unique (user_id, dedupe_key, occurrence_idx) where deleted_at is null`」对齐。
- `backend/migrations/versions/0001_phase1_schema.py`
  - 删除表级 `UniqueConstraint`，改为同名部分唯一索引（一期未发布，直接修正 0001，注释已说明）。
  - **已存在的本地库**需 `make db-reset`，或执行等价 DDL（本次已在开发库执行）：
    ```sql
    ALTER TABLE ledger.trade DROP CONSTRAINT uq_trade_user_dedupe;
    CREATE UNIQUE INDEX uq_trade_user_dedupe
      ON ledger.trade (user_id, dedupe_key, occurrence_idx)
      WHERE deleted_at IS NULL;
    ```

### 1.2 `replace_range` 使用请求体区间，并校验区间

- `backend/services/api/src/stock_api/routers/imports.py`
  - 入队 `job.params.range = payload.range.model_dump(by_alias=True)`（此前 `range` 被完全丢弃）。
- `backend/packages/ledger/src/stock_ledger/application/imports.py`
  - 新增 `_requested_range(params)`：解析 `{"from","to"}`，非法/倒置区间 → `VALIDATION_ERROR` 422。
  - `commit_import` 同步校验倒置区间（`from > to` → 422），避免把参数错误丢给 worker 变成失败任务。
  - `commit_batch`：
    - `replace_range` 用请求区间做 `soft_delete_range`；兼容未携带 range 的旧调用（CLI/seed）回退账单日期范围；
    - 批次 `ranges` 记录实际替换区间 + `replaced_trades`；
    - 去重查询发生在软删之后（保持原顺序），配合 1.1 后不再跳过本批行。
- 说明：`mapping` 透传（P2-10）与 `replaced_batch_id` 血缘（P2-12）仍属未修项，不在本次范围。

### 1.3 PAT 继承真实角色（P0-3）

- `backend/services/api/src/stock_api/deps.py`
  - `_resolve_pat()` 在查 `api_token` 后回查 `platform.user`：
    - 用户不存在 / 非 active → `TOKEN_REVOKED`；
    - `role = Role(owner.role)`，不再硬编码 `Role.USER`。
  - 影响：admin 的 PAT 与 JWT 行为一致（业务明细 403）；普通用户 PAT 不受影响。
  - 代价：PAT 请求多一次用户主键查询（后续可用 join 优化）。

## 2. 新增回归用例（7 条）

| 用例 | 文件 | 断言 |
|---|---|---|
| `test_api_011_replace_range_reinserts_without_data_loss` | `backend/tests/api/test_imports_trades_jobs.py` | 导入 2 行 → 同文件 `replace_range` → job succeeded、`inserted=2/skipped=0`、流水仍 2 行、旧行 ID 全部被替换 |
| `test_api_011_replace_range_invalid_range_422` | 同上 | 请求 `range.from > range.to` → 同步 422 `VALIDATION_ERROR`（不丢给 worker） |
| `test_api_011_replace_range_only_touches_requested_range` | 同上 | 账单含 06-01/06-30 两行；仅替换 06-01 区间 → 06-01 更新为新批次、06-30 行 ID/批次不变 |
| `test_api_015_reimport_after_revoke_restores_rows` | 同上 | 导入 → revoke（0 行）→ 同文件重导 → `inserted=2`、流水恢复 2 行 |
| `test_api_020_restore_after_reimport_conflicts_422` | 同上 | 撤销+重导后恢复旧行 → 422 `VALIDATION_ERROR`（不再是 500） |
| `test_admin_pat_inherits_admin_role_and_cannot_read_trades` | `backend/tests/api/test_auth_tokens_ops.py` | admin JWT 403、admin PAT（`Authorization: Bearer pat_...`）403 `ADMIN_CANNOT_READ_TRADES` |
| `test_user_pat_inherits_user_role_and_can_read_own_trades` | 同上 | 普通用户 PAT 调 `/auth/me` role=user；`/trades` 200 |

## 3. 验证命令与结果

```bash
# 1) 静态门禁
.venv/bin/ruff check backend                 # All checks passed
.venv/bin/ruff format --check backend        # 84 files already formatted
.venv/bin/mypy backend/packages backend/services  # Success: no issues found in 69 source files

# 2) 测试（串行，避免与后台 worker 抢队列）
.venv/bin/python -m pytest -m "not integration and not api" -q   # 86 passed
.venv/bin/python -m pytest backend/tests/unit/core --cov=stock_core --cov-fail-under=100 -q  # 100%
.venv/bin/python -m pytest backend/tests/api -q                  # 97 passed
.venv/bin/python -m pytest -m isolation -q                       # 18 passed
.venv/bin/python -m pytest backend/tests/integration/test_rls_isolation.py -q  # 4 passed
.venv/bin/python -m pytest backend/tests/architecture -q         # 1 passed

# 3) 全新库迁移（验证 0001 改动可重建）
# 临时库 stock_p1_fixcheck → alembic upgrade head ×2 + alembic check 全通过；
# 索引：CREATE UNIQUE INDEX uq_trade_user_dedupe ON ledger.trade
#       USING btree (user_id, dedupe_key, occurrence_idx) WHERE (deleted_at IS NULL)

# 4) 真实 HTTP 复验（uvicorn:8010 + worker + PG）
.venv/bin/python var/review-p1/verify-p0-fixes.py   # 14/14 passed
.venv/bin/python var/review-p1/live_e2e.py          # 23/23 passed（原有主链路回归）
```

## 4. 真实 HTTP 复验摘要（`var/review-p1/verify-p0-fixes-final.log`）

```
[PASS] replace_range 重新插入 120 行（不再删而不插） :: {"parsed":120,"inserted":120,"skipped":0,"quarantined":1}
[PASS] replace_range 后流水仍 120 行
[PASS] replace_range 记录请求区间 :: 2026-04-01..2026-06-30
[PASS] 撤销成功且流水清零 :: 200
[PASS] 撤销后重导 inserted=120（修复前为 0）
[PASS] admin JWT 读交易 → 403 ADMIN_CANNOT_READ_TRADES
[PASS] admin PAT 读交易 → 403（修复前 200）
[PASS] 普通用户 PAT 读交易 → 200
== P0 fix verification: 14/14 passed ==
```

## 5. 环境注意事项（重要）

1. **开发库 schema 已手工更新**：当前本地库已执行 1.1 的等价 DDL；其他人若已有旧库，请 `make db-reset` 或执行同一 DDL，否则旧唯一约束仍会让重导/替换失败。
2. **改完代码必须重启 worker**：本次复验曾出现 replace_range 偶发失败，根因是上一轮遗留的**旧代码 worker**（无热重载）仍在消费同一开发库、与新 worker 抢任务。已停止该陈旧进程；后续改 worker 代码后务必重启 `make worker`。
3. 复验期间在开发库新增了若干 `fix-*` 测试账号与批次（dev 库，非生产），按需清理。
4. `make migrate` 在当前库仍会因历史 `public.alembic_version` 属主（`sa_owner`）报 permission denied；这是既有环境问题（见审查报告 §2），与本次修复无关。

## 6. 仍未修复（本次范围外，按审查报告优先级）

- **P0-1**：真实账单金额口径未归一 → 对账 156/175 假断链（数据正确性最高优先级）。
- **P0-4**：前端无导入批次/撤销入口（§2.1 第 4 步不可完成）。
- **P0-5**：`verify-p1` 门禁空心、契约快照漂移（password 12→8）、api-check 缺示例/错误模型校验。
- **P1**：心跳缺失、pending 取消不终态、单飞异参复用、RLS platform 表缺口、`summary_rule` WITH CHECK、分类未接入 commit、真实账单词汇漂移、seed/demo 契约、trades 过滤参数空转、token localStorage、无 eslint、上传防护等（详见审查报告 §5.2）。
- **P2**：`mapping` 透传、`replaced_batch_id` 血缘、幂等 TTL、Clock 收口、游标签名、JWT aud/iss、SSE 头等（详见审查报告 §5.3）。
