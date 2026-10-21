# 二期硬依赖修复记录：G2 / G3 / G4

> 日期：2026-10-21 ｜ 基线：`4e2456f`（一期 P0 修复已提交）+ 本次工作区修改 ｜ 准入检查单：[P2-Entry-Checklist.md](P2-Entry-Checklist.md)
> 范围：只修 G2（队列语义）、G3（分类闭环）、G4（replay 行情）；G1/G5/G6/G7 未做。**未提交 git。**

## 0. 结论

| Gate | 状态 | 关键证据 |
|---|---|---|
| **G2** 心跳/租约/单飞/取消/异常收口 | ✅ | 19 条新用例；真实 HTTP 单飞同参复用 / 异参 409 / pending 取消终态 **7/7**；全量 API 套件绿 |
| **G3** 分类接入 commit + 未知摘要预检 + 规则词表 | ✅ | 真实账 category **177/177**、transfer 26、未知摘要 0、preview==commit、对账仍 **ok/175/0**；真实 HTTP **16/16** 中 G3 部分全 PASS |
| **G4** replay 适配器 + 确定性夹具 | ✅ | `generate_market_quotes()` 两次 SHA256 一致；tracked quotes == 生成器；replay 读取 hs300_tr 9 条、as_of 截断；25 条单测 |

最终集成门禁（独立库 `stock_p2_main`）：**`verify-p1` 15/15 全绿 + 勾选清单**；单测 174、API **108 passed**、越权/RLS/架构/契约全绿；前端 tsc/vitest(13)/build 全绿。真实 HTTP 回归：P0 全量 14/14、P0-2/3 14/14、§2.1 主链路 23/23。

---

## 1. G2：任务队列语义

### 1.1 改动

- `backend/packages/platform/src/stock_platform/jobs.py`
  - **单飞异参 409**：同 `(kind, scope_key)` 已有 pending/running 任务时，业务参数 digest 相同 → 复用；不同 → `ApiError(JOB_ALREADY_RUNNING, detail=旧 job_id)`（含 on_conflict 竞态分支）。
  - **内部参数不参与 digest**：`semantic_params()` 剔除 `batch_id`（每次请求新生成的 PENDING 批次 id）。否则同 preview 同 mode 的第二次提交会被误判异参而 409；修复后同参复用、异参（如 `full` vs `replace_range`）才 409。
  - `progress()` 续租；`request_cancel()`：PENDING 条件 UPDATE → `cancelled` + finished_at + 事件，RUNNING 仅置 flag；`fail_or_retry` 终态幂等守卫。
  - `reap_expired_leases`：`SELECT ... FOR UPDATE SKIP LOCKED` + 两段条件 UPDATE，避免双 worker 同时回收。
- `backend/services/worker/src/stock_worker/runner.py`
  - `_execute` 起独立 session 心跳协程（间隔 `HEARTBEAT_SECONDS`，可 monkeypatch），finally 取消；外层收口 ApiError→fail_or_retry、未知异常→failed+INTERNAL_ERROR；`_loop` 防御性兜底，异常不再打挂消费循环。
- `backend/services/api/src/stock_api/routers/jobs.py`：cancel 后 refresh，202 body 与 GET 一致（无 schema 变更）。

### 1.2 用例

- `backend/tests/unit/platform/`：12 + 5 例（心跳续租、progress 续租、pending/running 取消、单飞同参/异参、reaper 原子性、异常收口、循环继续、batch_id 内部参数）。
- `backend/tests/api/test_jobs_semantics.py`：pending 取消终态一致、running 仅 flag、同参复用同 job、异参 409。
- 旧 API-064 用例保留；新增用例补齐“终态”断言。

### 1.3 真实 HTTP（`var/review-p1/verify-g2-live.py`，worker 未启动）

```
[PASS] 单飞同参复用同一 job :: 202 reused=true
[PASS] 单飞异参 → 409 JOB_ALREADY_RUNNING :: 409 JOB_ALREADY_RUNNING 已有任务 job_... 正在执行
[PASS] 取消前 job=pending → 取消 202 status=cancelled → GET=cancelled → 列表=cancelled
== G2 live: 7/7 passed ==
```

### 1.4 风险/行为变更

- 单飞异参从“静默复用”改为 **409**：调用方需按 409 处理（`/imports` 正常重试走同参复用不受影响）。
- 心跳与 handler 长事务可能短暂等 job 行锁；reaper 用 SKIP LOCKED 保证不重复回收；高并发可后续加 `lock_timeout`。
- 改 worker 代码后必须重启 worker。

---

## 2. G3：分类链路闭环

### 2.1 改动

- `parsers/columns.py`：非交易摘要词表 `TRANSFER_SUMMARY_KEYWORDS` / `NON_TRADE_SUMMARY_SIDE` 与分类规则共用；真实账「银行转存/银行转取/转存管转入转出」归一。
- `repositories/rules.py`：系统规则扩到 19 条（code ≤16；股息红利税优先级；`sys_fee` 最低优先级；幂等 upsert + 废弃码清理 `sys_transfer_bank_*` 等），新增 `rule_summary_code`。
- `application/classify.py`：`load_rules` 幂等自愈（空库首次 preview 写 19 条系统规则；之后幂等）。
- `application/preview.py`：对可入库行用规则 dry-run；未命中 → `UNKNOWN_SUMMARY` warning + 计入 `estimated.quarantined`，与 commit 同口径。
- `application/imports.py`：commit 逐行分类；命中写 `category/summary_code` 并插入；未命中不写 trade、落隔离区 `UNKNOWN_SUMMARY`（含行号/摘要/建议规则）；批次 warnings/assumptions 对齐。

### 2.2 实测（真实 HTTP + 独立库）

| 项 | 结果 |
|---|---|
| 真实账 preview==commit counts | `{parsed:177, inserted:177, skipped:0, quarantined:0}` |
| 真实账 category 非空 | **177/177**；分布 buy83 / sell62 / **transfer26** / dividend3 / interest2 / fee1 |
| 未知摘要账单 | preview==commit `{2,1,0,1}`；warning `UNKNOWN_SUMMARY`；隔离行 line_no=3、reason=`UNKNOWN_SUMMARY` |
| 真实账对账（P0 回归） | **ok / checked=175 / breaks=0** |
| 真实 HTTP 复验 | `verify-g3-g4.py` **16/16** |

### 2.3 设计决策/风险

1. **系统规则应用层自愈**：preview 首次会写 19 条 `user_id IS NULL` 配置行（不写 trade）。一期原设计只在 `seed-tenants` 写规则；不 auto-seed 会让真实账整片误隔离。若要求 preview 严格零写入，P2-02 可改为启动/迁移 seed。
2. `summary_code` = 命中规则短码；买卖类规则 `side=both`（分类由摘要决定，避免合成夹具摘要/买卖标志矛盾误隔离）。P2-02 规则 CRUD 规格需对齐。
3. 未知摘要行号目前只在 `assumptions.unknown_summary_rows`；`warnings[].row` 需改 `routers/imports.py`（未在本次范围，P2-12 如需行内定位再放开）。
4. 旧库 `category` NULL 数据需重导或 CLI reclassify；G1 清库重 seed 可覆盖。
5. 未知摘要重传会在新批次再留一条隔离（inserted=0 不回退）。

---

## 3. G4：行情 replay 适配器与确定性夹具

### 3.1 改动

- `platform/ports.py` 新增 `MarketCapabilities` / `Bar` / `Quote` / `MarketDataSource` Protocol（标度整数、禁 float）。
- 新增 `stock_ledger/market.py`：`ReplayMarketDataSource`（懒加载、闭区间过滤、as_of 截断、fail-closed：缺文件/坏 JSON/非法定点/未来数据 → INTERNAL_ERROR；未知 symbol → NOT_FOUND；参数错 → VALIDATION_ERROR）+ `create_market_source(settings)`（replay/live；live 现为 `NotImplementedError`，P2-04 再实现）。
- `fixtures.py`：`generate_market_quotes()` 纯确定性（固定 9 个交易日 2026-01-30~09-30、4 标的、固定首日+漂移、sort_keys、无时刻戳）。
- `cli.write_market_fixtures()` 复用该生成器；`fixtures/market/quotes.json` 重新生成（4318 B）。
- 新增 `backend/tests/unit/ledger/test_market_replay.py` 25 例。

### 3.2 证据

- 生成器两次 + tracked 文件 SHA256 均 `40ed258d65a9ca71…`；replay 读回 hs300_tr 9 条，首 `2026-01-30`、末 `2026-09-30`，不返回 as_of 之后数据；`provenance=replay:deterministic-fixtures/v1#sha256:…`。
- 真实 HTTP 脚本中 G4 纯函数部分全部 PASS（16/16 的一部分）。

### 3.3 风险

- 尚未接线消费方：P2-04 在 AppState 装配 `create_market_source(settings)`；建议 `MARKET_AS_OF` 与 FrozenClock 基准日对齐。
- live 模式装配即抛 `NotImplementedError`（fail-closed，不静默空数据）。
- 既有 `fixtures/market/bill_huatai.xlsx` 与生成器当前输出仍不一致（seed-demo 会重写一次 diff）——属 G5/夹具治理，本次未改 bill（避免影响 G3 对账用例）。

---

## 4. 集成验证

| 项 | 结果 |
|---|---|
| `verify-p1`（15 步，独立库 `stock_p2_main`） | ✅ 15/15，exit 0，勾选清单完整 |
| ruff / format / mypy | ✅ 96 files formatted / 71 source files 0 error |
| 单测 / core 覆盖 | 174 passed / 31 passed 100% |
| API 验收 | **108 passed** |
| 越权 / RLS / 架构 / 契约测试 | 18 / 4 / 1 / 20 passed |
| 前端 tsc / vitest / build | ✅ 13 tests / build 0 |
| 真实 HTTP：G2 / G3+G4 / P0 全量 / P0-2·3 / §2.1 | 7/7 / 16/16 / 14/14 / 14/14 / 23/23 |

> 说明：集成/真实 HTTP 用独立库 `stock_p2_main`、`stock_p2_live`，避免与用户本机 `make dev` 的旧 worker 抢任务；验收后已清理临时库。

## 5. 后续（本次未做）

- G1 清库重 seed + tag（P2 第一天）
- G5 seed/demo 契约（mode=append、合法邮箱/口令）
- G6 前端接 SDK + 端点覆盖/越权门禁
- G7 review schema/platform 租户表 RLS
- P2-01 FIFO 纯核心 + oracle 可立即开始

## 6. 操作提示

1. 代码改动后**必须重启 worker**（无热重载）；用户当前 8000 栈的 worker 是旧代码，请 `make dev`/`make worker` 重启。
2. 本地库若沿用旧数据，先做 G1（清库重 seed）；G3 的分类规则会在首次 preview 自动补齐。
3. G4 quotes 夹具已确定化；bill xlsx 仍会在 seed-demo 时被重写（G5 处理）。
