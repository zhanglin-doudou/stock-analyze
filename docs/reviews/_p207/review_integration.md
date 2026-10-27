# Period2-07 三域指标设计 · 独立评审 #2（仓库集成 / 契约 / 可执行性）

**审核结论：REQUEST_CHANGES**
总评：口径推导与 §8.4 手算表质量高、§2 事实表基本属实，但存在 **2 个 P0**（demo 数据窗口与验收 `period=2026-09` 不匹配；主夹具超卖使期末持仓/浮盈亏自相矛盾）与 **8 条 P1**（契约不可实现/门禁与命令不可执行/契约修订清单漏项），须先修文档再进入实现。
**已核对断言数：66 条**（F1–F16=16、§6.1 十视图=10、§7 代码引用=8、C-01~C-26=26、§8 命令/门禁=6）；其中 11 条不成立或需修订。

## 发现

### I-P0-1 demo 数据日期与验收 period 错位，验收剧本按文档不可达
- 问题：D-19 只承诺「扩充 seed-demo 到 ≥12 个月」，未改账单生成器日期锚；§11/§8.7 仍用 `period=2026-09` 验收 n≥10。
- 证据：`_ANCHOR="2026-06-30"`（`backend/packages/ledger/src/stock_ledger/fixtures.py:49,68`）→ demo 流水窗口 2026-04-01~06-30；`DEMO_CLOCK=2026-10-01`（`backend/services/api/src/stock_api/cli.py:45`）、`months=3,count=120`（`cli.py:627`）；文档 `2026-10-26-metrics-three-domain.md:23`（D-19）、`:903,908`（§11）。即使扩到 12 个月（2025-07~2026-06），2026-09 仍无回合 → win_rate n=0。
- 建议：D-19 明确同批修改 `_ANCHOR`/行情 `as_of`/`DEMO_CLOCK` 对齐到 2026-09-30，并新增验收断言（period 内回合数、五卡非空）；否则改 DEV-PLAN §3.1 的 period。
- 影响：二期验收 §3.1 第 1/5 步与 §8.7 全部 API 用例失败。

### I-P0-2 fx_metrics_core 的超卖回合使「期末持仓/浮盈亏」自相矛盾
- 问题：C4 买 100/卖 150 超卖后，FIFO 引擎「绝不产生负持仓」，但 asset_daily 按流水数量累计为 -50→09-29 买入 200 后 150 股；文档同时对两种语义写死 golden。
- 证据：`fifo/engine.py:220,224-234`（oversell 未配对、lots 清空）；`review/application/assets.py:256-273`（`holdings+=delta` 允许负持仓、成本 `max(0,…)`）；`models.py:215`（超卖日允许负市值）。文档 `:681`（C4 超卖）、`:714`（C5 −2010.75 = 200×205−43010.75，按 FIFO「持仓归零」）、`:514`（「与 `asset_daily.unrealized_pnl` 同口径」）；按 asset_daily 同口径 C5 应为 150 股、浮亏 −12260.75；另 B5 付息 100 后 `:714` 的 −503.20 已含股息，与 `:514` 明示「不含已收股息」（应为 −603.20）冲突。
- 建议：二选一并冻结——① 主夹具去掉超卖（超卖移入独立 `fx_oversell`），主夹具 golden 与 D-13「0 分公差对账」才可成立；② 明确「超卖→持仓归零/负持仓」唯一语义并重算 B5/C5 与 §6.5 重建口径。
- 影响：behavior.de/win_rate_all 的 decomposition golden、behavior.hhi 与 D-13 对账不变量无法冻结。

### I-P1-1 验收/门禁命令与路径不可执行，verify-p2 清单不完整
- 问题：§11 沿用 DEV-PLAN 的 `make rebuild`、`make demo-broken`，两者在 Makefile/cli 中都不存在；§8.11 的 11 步用 `pytest tests/...`（缺 `backend/` 前缀，pyproject `testpaths=["backend/tests"]`），且缺 mypy/ruff/import-linter/既有 P2 全量套件与前端 tsc（§11 却声称含 tsc）。
- 证据：`Makefile` 无 `rebuild`/`demo-broken` 目标（全文 212 行）；`stock_api/cli.py:913-930` 仅 seed-tenants/seed-demo/api-check/api-snapshot/sdk/api-coverage/verify-p1/demo-p1；`DEV-PLAN-v2.md:405` 把 `make demo-broken` 列「二」。文档 `:899-903`、`:818-833`。
- 建议：C-25 扩为「补 rebuild/demo-broken 目标 + verify-p2 明确复用 verify-p1 静态检查 + 现有 `test-p2` 步骤 + `backend/` 前缀路径」。
- 影响：验收与 PR 门禁无法照文档执行。

### I-P1-2 `gate_endpoint_coverage` 不覆盖指标端点，「门禁自动纳入」不成立
- 问题：§8.8/§8.11 声称该门禁自动保证 API-033/034/035/038/079「每端点 ≥3 用例」。
- 证据：`scripts/ci/gate_endpoint_coverage.py:1-15,100-133` 只校验 DEV-PLAN A.1「期次=一」的 30 个 API-ID，且只查「被引用」不计数；`DEV-PLAN-v2.md:332-355` 中这些 API 期次均为「二」。
- 建议：新增 `gate_metrics_coverage.py` 或扩展现有门禁（all-endpoints + ≥3 用例计数），并在 C 清单立项。
- 影响：L3 API 覆盖无强制，05/06/07 三任务互推时容易漏测。

### I-P1-3 §8.9 mutmut 配置键与产物均按 mutmut 2 写，pin ≥3.2 后不可用
- 问题：`paths_to_mutate`/`tests_dir`/`runner` 是 mutmut 2 键；mutmut 3 用 `source_paths`/`pytest_add_cli_args_test_selection`/`also_copy`，分数需 `mutmut export-cicd-stats`，不存在 `mutation.json` 产物。
- 证据：文档 `:798-806`；mutmut 3 官方 README（PyPI 3.8.0）：`[tool.mutmut] source_paths = [...]`、`pytest_add_cli_args_test_selection = [...]`（<https://pypi.org/project/mutmut/>）。
- 建议：改写配置键、gate 改读 `mutants/mutmut-cicd-stats.json`；「PR diff-only」需 git diff→`source_paths`/通配 wrapper 的实现说明。
- 影响：`make mutation` 跑不起来或分数恒 0，D-16 验收不可达。

### I-P1-4 D-07 的 `ci_sample` 契约不足以让框架复算 DE 的 bootstrap CI
- 问题：DE 的统计量是 `PGR−PLR`（计数比），框架按 `ci_sample` 通用计算时必须知道统计量；冻结的 `MetricDef` 13 字段无 stat 钩子，`MetricValue` 也只有样本值。
- 证据：文档 `:33`（D-07）、`:195-196`、`:284`；`Extensibility.md:107`（步骤 5：CI 由框架统一算、`Random` 由 Clock 派生）、`:94-110`（13 字段）、`:104`（compute 只返 4 字段）。文档已自认「无法由框架通用均值型 CI 复现」，但方案未补统计量传递。
- 建议：C-19 扩为 `ci_stat: Callable[[Sequence[int]], int]`（或 `ci_replicates`），并同步 Extensibility §3.2；否则 DE 只能「口径双写」。
- 影响：FR-R6「de 含 ci_low/ci_high 且 CI 不含 0」验收与 05 引擎契约互斥。

### I-P1-5 stale 的 value 语义三处冲突，§4.2/§6.3 自身也不一致
- 问题：§4.2 规定 `stale → 无 value 键`；§6.3 却写「无（或旧值+stale）」；Extensibility §3.3 规定 `stale` 返回旧值。
- 证据：文档 `:230`、`:343,351`；`docs/architecture/v2/Extensibility.md:132`；`docs/Architecture-v2.md:631`（非 ok 不返回 value）。
- 建议：新增 C 项统一为 Architecture 口径（stale 无 value），并补 API-033 stale 断言；§6.3 删「或旧值」。
- 影响：05/06/07 与 API 契约测试互斥，前端 stale 取值行为不明。

### I-P1-6 D-13 的构建器位置违反分层铁律，X-2/§7.3.1 引用无法落地
- 问题：X-2 把持仓重建放 `core/metrics/holdings.py` 并「复用 `valuation.closing_price_ladder/close_on_or_before/holding_delta`」，但 core 只可依赖 contracts；这些原语在 `stock_review.application.valuation`（依赖 ledger/platform）。
- 证据：`pyproject.toml:115-119`（L2 合约）；`valuation.py:1-40`；`:563`（X-2）、`:39`（D-13）、`:512`（`build_rounds(trades_to_events(...))` 为 review+core 混合）。
- 建议：明确拆两层——core 纯函数（价格阶梯/权重/HHI）+ `review/metrics/holdings.py` 装配器（I/O），并同步 X-2、§8.9 变异范围与「core 100% 覆盖」边界。
- 影响：实现即触发 import-linter 失败；D-13 的「零依赖/不落库」结论失真。

### I-P1-7 §9 契约修订清单漏 8 项（会直接导致实现期返工/门禁互斥）
- 问题：未覆盖 Extensibility §3.2「Random 由 Clock 派生→sha256 seed」；Testing §8 U2 `seed=20261019/np.random.PCG64`；Testing §9.1/§9.2 fixture 目录/命名/MANIFEST 与 §8.3 冲突（C-20 只管 golden 与变异路径）；Testing §12.1「变异不阻断合并」vs §8.11 gate exit 1；API-079 只有 §1/§6 两行、无 §2.3 详约（§8.7 却设计 5 类用例）；C-08 只补 API-033（034/035/079/038 同样要 403）；C-05 未含 `metric_result.decomposition` 列与 OpenAPI/SDK 同步；C-17 未含 `backend/tests/unit/contracts/test_constants.py:27`（断言 MIN_CI_SAMPLE==10）。
- 证据：`Extensibility.md:107,132`；`Testing.md:327,347,355,387,486-497`；`API-Catalog.md:132,310-326,606`；`constants.py:24`；`test_constants.py:26-27`。
- 建议：补 C-27~C-34，逐条标注文件/位置/落地任务。
- 影响：05/06 与 07 三任务对同一契约各改一版。

### I-P1-8 D-02 的日线夹具没有装配路径，且与「nav/资产零回归」互斥
- 问题：新增 `daily_bars.json` 后无人加载：`create_market_source()` 硬编码 `fixtures/market/quotes.json`；若改全局源则 nav/asset 价格点变化（违反 D-02「零回归」），若双源共存则缺配置项与装配点，且 seed-demo 未说明同时写两文件。
- 证据：`stock_ledger/market.py:288-307`；文档 `:20`（D-02）、`:357`（§6.4）、`:880`（S2 仅写「适配器」）。
- 建议：C 项新增 `Settings.market_replay_path`（或 metrics 专用 `daily_bars` 源）+ 装配点 + `capabilities` 基准可用性；明确 seed-demo 双写。
- 影响：§1.2 目标 6 与 §11 timing 冒烟不可达（即便 I-P0-1 修好）。

### I-P2-1 `nav_daily`「按月稀疏/月频净值」表述错误（结论不受影响）
- 证据：`nav.py:190-195,222-241`（点=流水日+期初基线+期末），非按月；`models.py:158-205`。改为「仅流水日+期末稀疏」即可（D-12 结论仍成立）。

### I-P2-2 §7.1.3 `mean_natural_days=(close−open).days+1` 与手算表冲突
- 证据：`:433`（+1 公式）vs `:709-712,722`（68÷12=5.6667，按表逐对日期求和 68 恰为不含 +1 的 `.days`）；照 §7.1.3 实现 golden 必差 12 天。

### I-P2-3 多处事实引用/名目不实（不影响结论）
- `ports.py:132-160` 越界（文件 145 行，`F9`:101）；`F12`:104「fx_n5 有 value」非 Catalog 原文（`API-Catalog.md:315` 只写 insufficient 且无 ci）；`F13`:105 引用的 Makefile 行号是 help 行；§8.1 L2b「`engine` 既有夹具」不存在（conftest 只有 `owner_engine/app_engine`，`tests/conftest.py:96,109`）；§8.9 `ci.py/tolerance.py` 不在 §5.1 模块表（且 tolerance 在 `tests/support/`）。

### I-P2-4 §4.1 WinRateDef 依赖漏 `positions_end`，DAG 失效传播会漏行情变化
- 证据：`:183` 仅 `DatasetDep("rounds")`，而 `:419`/`:514` 需要期末浮盈亏；`Extensibility.md:186` 示例含 `DatasetDep("positions_end")`。

### I-P2-5 规模/预算数字前后不一致
- 证据：asset_daily「≤3e3 日」（`:321`）vs 10 年 3650 自然日（§6.5 `:376`「2500 日/50 标的」）；§8.10 记忆测试 750 日×200 标的（`:812`）；p95 预算 200ms（`:39`）与 300ms（`:811`）。HHI 10 年实测量级应为 D=3650×S，建议按 730k 次重估 <200ms 预算。

### I-P2-6 变异/性能门禁的产物与阻塞级别需再声明
- `gate_mutation_score.py` 读 `mutation.json`（`:806`）无对应产物；Testing §12.1 规定变异「不阻断合并、阻断 release」，§8.9/§8.11 却 gate exit 1；`.github/workflows/` 当前整目录不存在（`:833` 计划新建）。

## 已核实为真的关键事实（抽查）
1. 迁移止于 0006，无 `review/metrics`、无 `metric_result` 表；`REVIEW_TABLES` 仅 6 张（`review/models.py:301-308`；`migrations/versions/0001~0006`）。
2. 回合字段齐备：`status/data_error/oversell_units/holding_days/fills_count`（`review/models.py:49-98`）。
3. `holding_days` 为周一~周五近似（`core/fifo/calendar.py:12-22`；`engine.py:264` 调 `business_days_between`）。
4. `nav_daily` 稀疏：点=流水日+期初基线+期末，非逐日（`application/nav.py:190-195,222-241`）。
5. `asset_daily` 逐自然日（`while cursor_date <= end` +1 天，`assets.py:245-246`）、`daily_return=(total−cashflow−prev_total)/prev_total` 现金流调整（`assets.py:320-329`）、`unrealized=mv−holding_cost`（`assets.py:303`）。
6. `amount_units` 已含费、禁二次扣费（`review/application/valuation.py:33-35`，注释明确禁止再叠加费用列）。
7. replay 夹具 9 个月频点、4 序列含 `hs300_tr`（`ledger/fixtures.py:190-215`；`fixtures/market/quotes.json`）。
8. live 只支持 6 位 A 股、只落不复权；replay `supports_adjusted=False`（`platform/market_live.py:62-73,171-186`；`market.py:199-201`）。
9. 常量 `MIN_CONCLUSION_SAMPLE=MIN_CI_SAMPLE=10`、`A_SHUFFLE_DAYS=244`（`contracts/constants.py:21-24`）。
10. `RoundRepository.page()` 用 `COALESCE(close_date, open_date)`（`repositories/rounds.py:159-166`）。
11. demo `months=3,count=120`、`DEMO_CLOCK=2026-10-01`，且账单确有超卖（`cli.py:45,627`；本地复跑 seed=7/13/42 分别 12/13/27 次卖出早于买入）。
12. §8.4 手算表复算一致：win_rate 0.66666667、pnl_ratio 1.63381463、CR1 0.31427381、Top3 0.81170984、Gini 0.49793802、expectancy 1042.16、fee 0.00048123、Wilson(8,12) [0.39062209,0.86187991]、MDD 0.07829824、Ulcer 0.04165262；fx_metrics_core 余额链闭合、最低余额 24344.34（Decimal 独立复算）。
13. `behavior.de` 方向矛盾属实：PRD A15「PLR−PGR」（`PRD-v2.md:579`）vs FR-R6「只卖盈不卖亏→DE>0」（`PRD-v2.md:411`）。
14. 无 mutmut 配置/依赖、无 `.github/workflows`、无 `verify-p2`（`pyproject.toml`、`Makefile`）。

> 复核方式：只读 grep/read + `.venv/bin/python` 独立复算；未改设计/生产代码、未起服务、未写库。本文仅为评审材料。
