# 板块数据底座与看板 实现记录（2026-10-31）

> 设计依据：[2026-10-31-sector-data.md](../architecture/2026-10-31-sector-data.md)（v0.2）。
> 所有决策点（D-01~D-24、H-01~H-09）按设计文档**推荐方案**落地。

## 1. 交付物

| 层 | 内容 |
|---|---|
| 迁移 | `backend/migrations/versions/0011_market_sectors.py`：`market.sector` / `sector_member`（SCD2 半开区间 + 行业当前部分唯一索引）/ `benchmark_member` / `sector_bar` / `sector_pull_range` / `sector_sync_state`；`GRANT`（`stock_app` DML / `stock_backup` 只读）；`platform.schema_version = 0011_market_sectors`；downgrade 按 FK 逆序删 6 表、不删 `market` schema |
| contracts | `ErrorCode` 未新增（缺失走响应字段 `missing[]`）；`SectorDataSource`/DTO 端口在 `stock_platform.ports` |
| core | `stock_core/sector/`：`weights.py`（`E_map` 分母、主动权重）、`concentration.py`（HHI / effective_n / TopK / deviation / coverage）、`concepts.py`（`exposure/overlap/hhi_tag`，不归一）、`preference.py`、`grouping.py`、`status.py`；`metrics/policy.py` 阈值 |
| platform | `market_models.py`（6 表 ORM）、`sector_source.py`（`create_sector_source` 注入）、`sector_live.py`（akshare + 本地缓存）、`sector_replay.py` / `sector_fixture.py`、`sector_validation.py`、`repositories/sectors.py`（公共查询显式不收 `user_id`）；`config.py` 全套 `SECTOR_*` |
| review | `application/sectors.py`（`build_sector_overview` / `list_sector_catalog`）、`application/portfolio.py`（从 dashboard 提取 `holdings_at`，行为不变） |
| api | `routers/sectors.py`（API-087 `GET /sectors/overview`、API-088 `GET /sectors`，只读 + ETag/304 + `missing[]`）、`schemas.py`、`deps.get_sector_data`、`app.py` 注册、`openapi_meta.py` 087/088 元数据 |
| CLI | `stock_api.cli sync-sectors`（仅 `live`；`--dry-run` 只做配置/表检查，不联网不写库）；`Makefile` `sync-sectors ARGS=...` |
| 夹具 | `fixtures/market/sectors.json`（确定性 replay 快照） |
| 前端 | `pages/Sectors.tsx`（三级下钻 / 概念暴露 / 深链）、`pages/dashboard/SectorBlock.tsx`（懒加载子块）、`charts/PreferenceBar.tsx`、`ui/Caveat.tsx`、`sectors.css`；`http.ts` / `contract.ts` / `queryClient.ts` / 路由与导航 |

## 2. 口径要点

- 行业（申万三级 SW2021）成员互斥，分母 `E_map`（已映射权益市值）：`Σweight≈1`、`Σactive≈0`，`coverage` 独立，未分类单列。
- 概念（东财）多归属**不可加总**：只输出 `exposure/overlap/multi_tag/hhi_tag`；`Σexposure>1` 响应带 `caveat`；`weight_mode=overlapping`。
- 基准缺失 → `active_*` 全 `null` + `missing[]` 含 `SECTOR_BENCHMARK_WEIGHTS_MISSING`，**禁止等权近似**。
- 未同步/覆盖不足 → 200 降级 + `missing[]`（`SECTOR_DATA_MISSING` 等）；不可用数值显式 `null`。
- 成分按 SCD2 时点区间读取，缺失 fail-closed（`not_covered`），禁止 assumed 近似。
- 不注册第 19 个指标、不做 API-089；五因子归因 `factors[]` 预留 `code="sector"`（Period2-08）。

## 3. 测试

- 后端：`test_sector_core.py`（golden/oracle 对拍）、`test_sector_golden.py`、`test_sector_repository.py`、`test_sector_validation.py`、`test_sector_catalog.py`、`test_sector_overview.py`、`test_api_087_sectors_overview.py`、`test_api_088_sectors_catalog.py`；隔离矩阵加入 `/sectors/overview`、`/sectors`。
- 前端：`Dashboard.test.tsx`（子块 / `[data-board]` 恰 2 个 / links）、`Sectors.test.tsx`（深链回填 / 概念 caveat / 状态）、`zero-caliber.test.ts`。

## 4. 门禁结果（2026-10-31）

| 命令 | 结果 |
|---|---|
| `make lint` | 通过（ruff + eslint + tsc） |
| `make typecheck` | 通过（mypy strict 172 files + import-linter L1–L5 KEPT） |
| `make migrate-check` | 通过（No new upgrade operations detected） |
| `make test-p2` | 通过（含 G6b 越权 46/46、api-check、契约快照无漂移） |
| `make test-metrics` | 通过（含 G8 金标结构 25 份） |
| `make test-isolation` | 通过（99 passed） |
| `make api-check` | 通过（88/88 登记、55 操作、错误码一致） |
| `make api-coverage` | 通过（API-087/088 ✓ 二期） |
| `make gate-endpoint` / `make gate-golden-structure` | 通过 |
| `make sync-sectors ARGS='--dry-run'` | 通过（replay 模式提示无需同步） |
| `make sdk` | 通过（`schema.d.ts` 已同步） |
| `pnpm -r test` | 通过（25 files / 115 tests） |
| `pnpm -r build` + `pnpm budget` | 通过（首屏 JS gzip 85.16KB ≤ 220KB、CSS 4.66KB ≤ 30KB） |

## 5. SKIP 与已知限制

- `make verify-p2`：仓库尚未提供（Period2-14 暂停）；二期验收以上表 `test-p2` + `test-metrics` + 各 gate 为准。
- `live` 模式 akshare 联网同步仅手测（本地默认 `SECTOR_MODE=replay`）；`sync-sectors --dry-run` 已覆盖配置/表检查。
- 归因（API-036）、月报、导出、设置增强、报告页仍为占位，不伪造数据。

## 6. 数据源兜底与换源（2026-11-01）

东财（EM）`push2*.eastmoney.com` 被 WAF 封禁（RemoteDisconnected），legulegu.com（申万行业目录/成分）间歇性 504。为保证 `sync-sectors` 可用，新增多源兜底：

### 概念板块：EM → 新浪（SINA）链式兜底
- `AkshareSectorSource._fetch_concept_catalog` 按 `(EM, SINA)` 顺序尝试目录，命中首个可用源后缓存 `self._concept_source`。
- `_fetch_concept_members` 复用同源，按板块逐个 `_call`（每板独立超时+重试），单板失败跳过并 `warning`，不拖垮整批。
- 持久化时 `market.sector.source` / `sector_member.source` / `sector_sync_state.source` 写入实际命中的源（`em` 或 `sina`），不再硬编码 `em`。

### 行业成分：legulegu → 申万官方 API
- `_fetch_industry_members` 由 `sw_index_third_cons`（legulegu，不稳定）切换为 `index_component_sw`（swsresearch.com 官方 API），需去 `.SI` 后缀，列为 `证券代码`。

### 校验修复
- `validate_catalog`：L1（申万一级行业）为顶层、`parent_code=None` 合法；原逻辑误将 L1 当作需父级的层，已改为 `level <= 1` 无父级。

### 测试夹具
- `tests/conftest.py` 的 `settings` fixture 同时强制 `market_mode=replay` 与 `sector_mode=replay`，避免 `.env` 中 `SECTOR_MODE=live` 导致 API 测试走空缓存。

### 已知外部依赖
- legulegu.com 504 时行业目录（L1/L2/L3 树 + 父级关系）无法拉取，`sync-sectors` 会在 `_fetch_industry_catalog` 失败；待站点恢复后即可全量同步。申万官方 API 暂不提供三级行业目录接口。
