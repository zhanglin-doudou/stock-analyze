# 测试策略与质量门禁（Testing Strategy & Quality Gates）

> 版本 **v1.0**｜日期 2026-10-19｜范围：`packages/{contracts,core,ledger,review,delivery,platform}` + `apps/server`
> 上游输入：[PRD-v1](../../prd/PRD-v1.md)（24 FR / 12 NFR；`ε_amt=0.01 元`、`ε_r=1bp`、`ε_cum=5bp`、`A=244`）、[Review](../../prd/Review.md) §1 R-A10（验收不可判定）、§5（指标口径修订表）。
> 本文**不定义任何新口径**：口径唯一事实源是 PRD 附录 A + Review §5。本文定义的是**如何证明口径没有被实现错**，以及什么条件下不允许合并/不允许发布。
> 技术栈（固定）：Python 3.12 + uv workspace；pytest / hypothesis / syrupy / pytest-cov / mutmut / import-linter / mypy --strict / ruff。

---

## 0. 一页结论
| # | 决策 | 内容 | 为什么 |
|---|---|---|---|
| D1 | 覆盖率分层，不设全局 100% | `core` 100%/100% 硬门禁；delivery/infra 用契约+回放替代行覆盖；整体 ≥90%/85% 仅作告警 | NFR-7（PRD L567）显式写「100% 覆盖率不作为正确性判据」；把预算从模板/适配器挪到领域计算才是风险最优 |
| D2 | 正确性由**独立 oracle** 证明，不由被测算代码自证 | golden 期望值来自 `tests/oracle/` 里的朴素参考实现 + 人工手算表，二者与生产实现三方比对 | R-A10 的病根是"接口调通即验收"；同源实现互相印证等于没验 |
| D3 | 确定性先于覆盖率 | 时间/随机/浮点/行情/网络/排序六类不确定性全部端口化或冻结，AST 扫描强制 | 不确定的测试无法作为门禁；也无法做变异与 golden diff |
| D4 | 口径常量单点 | `contracts/constants.py` 唯一的 `EPS_AMT/EPS_R/EPS_CUM/A/MIN_N_*`，测试与生产同源 import | PRD 容差散落会导致"测试用 0.01、实现用 0.001"，双标即失效 |
| D5 | 边界矩阵按"会让数字方向错"排序 | 28 条 fixture 边界矩阵，先覆盖口径翻转类（分红/送转/超卖/反推失败/DE 符号），再覆盖展示类 | 本产品伤害用户的不是崩溃，是**看起来对但方向错的数字** |
| D6 | 变异测试只对 `core` 下硬指标 | core mutation score ≥80%，`tolerance/fifo/nav` 单模块 ≥90%；nightly 跑全量、PR 只跑变更文件 | 变异是唯一能杀死"分支跑到但符号写反"的手段，但全仓跑代价过高 |
| D7 | 门禁失败分级：阻断 vs 告警 | 金额/收益/阻断态/golden diff/覆盖率 core 为 blocking；delivery 覆盖率、整体覆盖率、性能为 warning | 门禁太多等于没有门禁；只让"会误导用户"的问题阻断合并 |

---

## 1. 测试策略总纲
### 1.1 为什么本产品"高覆盖"≠"正确"
本产品的缺陷不是"代码没执行到"，而是**执行到了但口径错**。四类缺陷里，只有一类能被行覆盖捕获：

| 缺陷类别 | 实例 | 行覆盖能否发现 | 应交给的门禁 |
|---|---|---|---|
| 口径实现错 | 回合 pnl 在 `发生金额` 之外再减佣金（二次扣费） | **否**（分支全执行） | golden vs 独立实现、手算期望表 |
| 边界/符号错 | 胜率用 `>=0`；`DE = PGR − PLR` 写反；容差 `<` 写成 `<=` | **否** | 变异测试 + 边界矩阵精确值 |
| 接线/门禁错 | 页面读未过对账闸门的指标；LLM 出站带金额 | **否** | 集成测试 + 架构约束 + 出站断言 |
| 路径缺失 | 打新/送转/退市/停牌分支没写 | 是 | 覆盖率 + fixture 矩阵 |

**结论**：覆盖率是回归信号，`golden + 属性 + 变异` 才是正确性信号。四者并存，不可互相替代。
### 1.2 分层覆盖率目标与门禁强度
我采纳建议数字，但改三处并在末段论证。

| 层/包 | 行覆盖 | 分支覆盖 | 理由 | 门禁强度与不达标后果 |
|---|---|---|---|---|
| `packages/core` | **100%** | **100%** | 纯领域计算（entities/values/fifo/nav/metrics/attribution/stats/errors），零 I/O、零全局态，~4k 行，100% 可达；每行都对应一条口径 | 硬：`--cov-fail-under=100`，CI 直接失败，无豁免（见 §11.2） |
| `packages/contracts` | **97%** | **95%**（原建议 95%） | Pydantic 声明式代码由框架执行，未覆盖行多为 `model_validator` 防御分支；97% 才能保证**每个跨上下文 DTO 的校验器**都跑到 | 硬：按模块 `--cov-fail-under=97`；契约校验器单独 100% |
| `ledger/domain` + `review/domain` | **98%** | **96%** | 去重判定、对账分级、FIFO 归属、样本门槛判定；低于 core 的 2% 留给"平台相关防御 except"（明确登记在豁免清单） | 硬：98% |
| `ledger/app` + `review/app` | **95%** | **90%** | 用例编排，分支主要是失败路径（撤销、降级、阻断、单飞锁冲突）；失败路径恰是用户最常撞到的 | 硬：95% |
| `ledger/infra` + `review/infra` | **85%** | **80%** | SQLAlchemy 方言分支、akshare 解析异常族；**用契约测试+回放覆盖不必逐行**。新增代码单独 ≥90% | 软：低于 85% 告警；新增行 <90% 阻断 |
| `packages/platform`（db/jobs/events/config/obs/backup） | **90%** | **85%**（原建议并入 infra） | `sys_job_run` 单飞锁、checkpoint 续跑、备份完整性是**跨全部功能的横切正确性**，事故面比单个适配器大 | 硬：90%（单列的理由：备份/续跑出错会静默毁数据） |
| `packages/delivery`（web/templates/reports/chat） | **80%** | **70%** | Jinja 模板与 HTMX 片段做文本断言成本高；渲染逻辑抽到 view-model 后由单测覆盖，模板只测"分支是否出现" | 软：80% 告警；阻断态渲染正确性按 §6 事实断言，不计覆盖率 |
| `apps/server` | **70%** | **60%** | 组装根（app + CLI + launcher），大部分是接线，用 e2e smoke 覆盖更划算 | 软：不设 fail-under，用 e2e 名单替代 |
| **整体** | **≥90%** | **≥85%** | 与 PRD NFR-7 的"双指标"对齐，作为健康度趋势 | warning only：低于则 PR 需附说明，不阻断 |
| **新增/变更行（diff coverage）** | **≥95%** | — | 历史存量覆盖率不应成为新代码的挡箭牌；这是真正的防劣化门禁 | 硬：`tools/gate_diff_coverage.py` 失败即阻断 |

**三处改动及论证**：① `contracts` 95→97，因为 `MetricResult` 是 14 个字段的跨上下文契约（`metric_id/definition_version/scope/period/value/unit/n/ci_low/ci_high/sample_status/assumptions/data_as_of/inputs_hash/computed_at/status`），一个校验器漏测就会让 `status=blocked` 的指标流到页面；② 增列 `platform` 并给 90% 硬门禁，`sys_job_run.checkpoint_json` 续跑若错会产出"半套指标"；③ 新增 diff coverage 95% 硬门禁，替代"整体数字好看但新代码裸奔"。（ADR-0010 记的 `contracts ≥95%` 是下限，本文 97% 更严，两者不冲突。）
**排除项**（`[tool.coverage.run] omit`）：Alembic 迁移脚本、`if TYPE_CHECKING:` 块、`__main__.py`、vendored ECharts JS、Jinja 模板文件本体（模板的断言走渲染测试，不走行覆盖）。
### 1.3 覆盖率之外的四道门（缺一不可）
| 门 | 载体 | 判据 | 为什么不可省 |
|---|---|---|---|
| Golden 对拍 | `fixtures/expected/` + syrupy `.ambr` | 同输入重复计算 diff = 0；与独立 oracle diff ≤ 容差 | 覆盖率证明"跑过"，golden 证明"算对" |
| 边界矩阵 | `fixtures/bills/` 28 条 + 参数化 | 每条至少一个断言命中的口径函数 | 覆盖率凑不出"分红跨除权日"这种组合 |
| 变异 | mutmut on `core` | mutation score ≥80% | 杀死"符号/边界写反"；覆盖率对它完全无感 |
| 架构约束 | import-linter + AST 扫描 | 合约 0 违反 | 防止"口径对了但走错层"（如页面直接读 DB 绕过闸门） |

---

## 2. 测试金字塔与套件分层
| 套件 | 目录 | 目标层 | 工具 | 规模预算 | 时长预算 | 触发 | 门禁 |
|---|---|---|---|---|---|---|---|
| 单元 | `packages/*/tests/unit/` | core / domain | pytest | ~1200 条 | ≤30s | pre-commit + PR fast | 硬（失败即阻断） |
| 属性 | `packages/core/tests/properties/`、`packages/ledger/tests/properties/` | core / ledger.domain | hypothesis | ~40 条 × 200 examples | ≤90s | PR full + nightly | 硬 |
| 契约（端口） | `tests/contract/` | 全部 15 个 Protocol 的每个实现 | pytest + 参数化 impl | 15 端口 × 6 断言 | ≤60s | PR full | 硬 |
| 契约（数据源） | `tests/contract/test_market_data_port.py` | `MarketDataSource` 适配器 | 回放快照（自建，不引 vcrpy） | ~120 条 | ≤120s | nightly | 硬 |
| Golden | `packages/review/tests/golden/`、`packages/delivery/tests/golden/` | review.metrics / delivery.reports | syrupy + `fixtures/expected` | ~35 快照 | ≤60s | PR full + release | 硬（diff ≠ 0 阻断） |
| 集成 | `packages/*/tests/integration/`、`tests/integration/` | app + infra + api（SQLite 文件库） | pytest + httpx ASGITransport | ~250 条 | ≤5min | PR full | 硬 |
| 端到端 | `tests/e2e/` | apps/server（真实启动 + 浏览器无关 HTTP） | pytest + launcher | ~15 条 | ≤8min | nightly + release | 硬 |
| 架构约束 | `tests/architecture/` | 全仓 | import-linter + AST | ~20 条 | ≤20s | PR fast | 硬 |
| 变异 | mutmut 作用于 `packages/core/src/core` | core | mutmut | 全量 ~600 变异体 | nightly ≤40min | nightly / 按需 | 硬（score 门槛） |
| 性能 | `tests/perf/` | 5 万行流水全链路 | pytest-benchmark 或自计时段 | 4 条 | ≤60s | nightly | warning（超预算 2× 才阻断 release） |
| 数据质量 | `tests/data/` | fixtures 自身（PII/哈希/链闭合） | pytest | ~30 条 | ≤20s | PR fast | 硬 |

**三层断言约定**（避免"只断言不报错"）：① 数值层用 `tests/support/tolerance.py::assert_amt/assert_ret/assert_cum`（唯一容差入口）；② 结构层断言 `MetricResult` 的 `status/sample_status/n/data_as_of/inputs_hash`；③ 文案层断言"阻断态不出现结论词"（`test_blocked_state_hides_return_cards`）。

---

## 3. 确定性工程
高覆盖率的前提是"同一输入永远同一结果"。本项目的不确定性来源与消除手段：

| # | 不确定性来源 | 典型污染点 | 消除手段 | 验证手段 |
|---|---|---|---|---|
| U1 | 系统墙钟 | `computed_at`、`data_as_of`、批次时间、报告日期 | `Clock` 端口注入；生产 `SystemClock`，测试 `FixedClock(2026-10-19T15:00+08:00)`；**禁止** `datetime.now()`（AST 扫描） | `tests/architecture/test_no_wall_clock.py` |
| U2 | 随机（bootstrap 10000 次、Wilson、抽样） | PGR/PLR CI、归因 CI | 所有随机入口显式 `seed:int`；测试传 `seed=20261019`；`numpy.random.Generator(PCG64(seed))` | `test_bootstrap_seed_deterministic` |
| U3 | float | 金额/收益率累积误差、`0.1+0.2` | 全链路 `Decimal(str(x))`；DB `DECIMAL(18,2)/(18,6)`；仅在展示层 `float()`；舍入统一 `ROUND_HALF_UP` | `test_no_float_in_core.py`（AST 禁 `float(` 于计算路径） |
| U4 | 时区 | 账单"本地日"与 DB `datetime`；自然日年化 365 | 统一 `ZoneInfo("Asia/Shanghai")`；日期字段用 `date`，时间戳一律 tz-aware；年化口径 `365/自然日` | `test_timezone_contract.py`（DTO 时间戳必须带 tzinfo） |
| U5 | 行情 | 价格/因子随时间漂移、前复权改写历史 | `fixtures/market/**` 冻结 parquet 快照 + `meta.json` 含 `sha256/factor_version/data_as_of`；测试只经 `ReplayMarketData` 端口 | `test_market_snapshot_hash` |
| U6 | 网络与等待 | akshare 请求、LLM 出站、备份/job/浏览器启动 | pytest 会话级 `--disable-socket`（自建 `tests/support/no_network.py`，免引入插件）；数据源测试走回放；禁止 `time.sleep`（AST 扫描），`JobRunner` 用 `ManualClock` + `job.tick()`，launcher 用 `wait_for_http` 显式轮询上限 | `test_suite_has_no_network`、`test_no_sleep.py` |
| U7 | 迭代/排序顺序 | `dict`/`set` 迭代进计算；FIFO 配对顺序 | 一切 `sorted()` 显式覆盖 A7 稳定序五元组 `(交易日, 文件行号, 摘要, 数量, 均价)`；set 只用于成员判断；JSON 输出 `sort_keys=True` | 属性 P-06（渲染顺序不敏感） |
| U8 | 文件系统/环境 | 读 `$HOME`、读 env、读用户真实数据目录 | `FileStore` 端口 + `tmp_path`；`Settings(_env_file=None, data_dir=tmp_path)` 显式构造；不读 `os.environ` | `test_no_ambient_env.py` |
| U9 | 编码/locale | 华泰账单 GBK / `utf-8-sig` / Excel 序列号日期 | 解码器显式传入 encoding 候选链；三种编码 fixture 各一份 | `test_import_encodings[gbk / utf8sig / utf8]` |
| U10 | 并发/WAL | SQLite 并发写、单飞锁 | 测试用 `tmp_path` 真实文件库（非 `:memory:`，因 WAL 与迁移语义）；并发用显式事务与 `(kind,scope)` 锁断言，不用线程竞速 | `test_job_singleflight_conflict` |
| U11 | 快照格式演进 | `.ambr`/fixture 版本升级后旧断言仍绿 | 快照首行写 `schema_version`；`syrupy` 未知版本即 fail；fixture 变更走 MANIFEST 审批（§11.3） | `test_snapshot_schema_version_present` |

**强制约束落地**：`tests/architecture/test_no_wall_clock.py` 扫描 `packages/core`、`packages/{ledger,review}/domain`、`{ledger,review}/app` 的 AST，命中 `datetime.now|datetime.utcnow|time.time|time.sleep|random.random|random.randint` 即 fail（allowlist 仅 `platform/obs/clock.py`、`tests/support/`）。`tests/support/tolerance.py` 是**唯一**容差入口，禁止在用例里写字面量 `0.01`。

```python
# tests/support/tolerance.py —— 唯一容差入口（供全仓 import）
def assert_amt(actual: Decimal, expected: Decimal) -> None:
    assert abs(actual - expected) <= EPS_AMT, f"|Δ|={abs(actual-expected)}"
def assert_ret(actual, expected) -> None: assert abs(actual - expected) <= EPS_R    # 1bp
def assert_cum(actual, expected) -> None: assert abs(actual - expected) <= EPS_CUM  # 5bp
```

---

## 4. Golden fixture 体系
### 4.1 目录与命名规范
```
fixtures/
├── MANIFEST.json                  # 每个 fixture：sha256 / owner / synthetic / reason / schema_version
├── bills/                         # 合成账单；NN 段位 = 分类（10s 交易 / 20s 公司行为 / 30s 资金 / 40s 导入 / 50s 统计）
│   ├── 10_add_position_two_lots.xlsx   11_partial_reduce_three_sells.csv
│   ├── 20_cash_dividend_single_lot.xlsx   40_balance_chain_break.csv
│   └── 53_attribution_buy_point_dominant.xlsx
├── market/mkt-akshare-cn_daily-20260101-v1/   # <source>-<dataset>-<yyyymmdd>-v<N>
│   ├── meta.json                  # source / fetched_at / factor_version / data_as_of / sha256
│   └── calendar.parquet, daily_hfq.parquet, factors.parquet   # 交易日历决定 A=244 边界
├── expected/                      # 人可审 oracle：rounds/*.yaml 手算、nav/*.json、golden/*.json（比率存 bp）
├── private/                       # .gitignore，真实脱敏账单仅本地跑
└── _pii_map.local.json            # .gitignore，合成映射表
```

**命名规范**：`bills/<NN>_<snake_case_scene>.<ext>`，NN 段位=类别（`10s` 交易结构、`20s` 公司行为、`30s` 资金/打新、`40s` 导入与对账、`50s` 统计门槛/归因），空号留给插入不重排。`expected/<domain>/<NN>_*.{yaml,json}` 与账单同名，测试用 `pytest.mark.parametrize` + `glob` 自动配对——**新增 fixture 不需要改测试代码**。
**必存在字段**：`meta.json` 必须含 `schema_version/source/fetched_at/factor_version/data_as_of/sha256`；`MANIFEST.json` 缺条目即 fail（`tests/data/test_fixture_manifest.py`）。
**与包内 fixture 的关系**：跨包共享的账单/行情/golden 放顶层 `fixtures/`（本节）；仅单包使用的可放 `packages/<pkg>/tests/fixtures|golden/`（如 Extensibility §5 的券商账单样例），但**两类都必须登记同一个 `MANIFEST.json`**，并同样受 §11.3/§11.4 审批约束。
### 4.2 必须存在的 fixture
| 必备组 | 原因 |
|---|---|
| 6 类抽样结构（加仓/分批减/同日多笔/分红/送转/超卖，各 ≥5） | 直接对应 G1 的"30 回合分层抽样，6 类各 ≥5" |
| 公司行为边界（配股/红利税/除权一致性/停牌/退市） | FR-R2.3 的 pnl 增量项全在这里；错一项所有收益率都偏 |
| 3 类资金流（转账/利息/打新） | 决定 TWR 与 MWR 是否分得清（FR-R3.2） |
| 6 类导入异常（未知摘要/首行非买/无余额/断链/缺列/区间替换） | FR-L3/L5/L6 的阻断与降级是产品可信度前提 |
| 4 类统计门槛（N=5/40 交易日/DE 有无效/单因子归因） | FR-R8、FR-R6、FR-R7 的"不下结论"能力 |
### 4.3 边界矩阵（28 条）
| # | fixture | 构造要点 | 断言什么 | 对应 FR |
|---|---|---|---|---|
| 1 | `10_add_position_two_lots` | 两笔买入不同价/量，一次卖出跨两批 | FIFO 按稳定序跨批配对；pnl/成本/`trade_id` 集合 `{B1,B2}` 精确相等 | FR-R2.3, G1 |
| 2 | `11_partial_reduce_three_sells` | 1 买 3 卖（部分数量） | 3 个回合；剩余持仓数量=0；`Σ回合pnl` 与现金流闭合 | FR-R2.3 |
| 3 | `12_same_day_multi_fill` | 同日同价同量 3 笔 + 一笔不同量 | bag 去重计数（不误判）；FIFO 用**文件行号**而非日期破平局 | FR-L3.4, 附录 A27 |
| 4 | `15_oversell_negative_qty` | 卖出数量 > FIFO 可配数量 | 不生成负持仓；标 `data_error` 并按隔离/对账规则处置，绝不静默配平 | FR-R2.3, FR-L6 |
| 5 | `20_cash_dividend_single_lot` | 除息日持股、股息入账、红利税单列 | 回合 pnl 含**税后**股息净额；股息不折份额（内部收益） | FR-R2.3, 附录 A3/A8 |
| 6 | `22_bonus_share_10_for_3` | 10 送 3，公告日/除权日分列 | 数量 ×1.3、**总成本不变**、单价下调；卖出后收益率与手算表一致 | FR-R2.3, A9 |
| 7 | `23_rights_issue_payment` | 配股缴款 + 股份到账 | 缴款计入买入成本；数量增加但不新增回合；摊薄成本上升 | FR-R2.3, A9 |
| 8 | `24_ex_div_consistency` | 跨除权日的完整回合 | 恒等式 `买入成本 + 股息 − 卖出净额 = 账单现金流差额`（≤`ε_amt`） | FR-R2.3 验收 |
| 9 | `25_dividend_tax_tiers` | 持股 20 天 / 200 天 / 400 天三组 | 差别化税率 20%/10%/0% 的档位边界（30、365 天）精确命中 | FR-R2.3 |
| 10 | `26_suspended_valuation` | 个股连续停牌 10 个交易日 | 停牌日取前收 + 标 `stale`；当日收益不计；连续停牌 >50% 剔除卖飞率并计 `pending_window` | FR-R2.2, NFR-2, A16 |
| 11 | `27_delisted_holding` | 期末仍持股且标的退市 | 退市股不从样本消失；期末浮盈亏用最后收盘价；计入全样本胜率分母 | NFR-2, A10 |
| 12 | `30_ipo_three_flows` | 申购中签 + 缴款 + 新股卖出（无代码段） | 不参与回合配对；占用计入资金利用率；净值不断链 | FR-L5.2 |
| 13 | `31_interest_vs_transfer` | 同月利息归本 + 银证转入 + 转出 | 利息=内部收益（不折份额）；转账=外部现金流（折份额）；TWR 只受行情影响 | FR-R3.2, A2/A3 |
| 14 | `32_large_deposit_uptrend` | 月中入金 30% 且当月上涨 | `MWR > TWR`（资金在盈利期投入），差值方向断言 + CI；页面文案含解释 | FR-R3.3 |
| 15 | `40_unknown_summary_quarantine` | 摘要「可转债转股」不在规则表 | 100% 进隔离区、响应含**行号**、不静默丢弃；指标不出该行 | FR-L5.1/5.3 |
| 16 | `41_first_row_not_buy` | 首行是「利息归本」 | `period_initial_cash = 首行余额 − 首行发生金额`；`assumption=back_solved`；首行入库不丢弃 | FR-R2.1, A1 |
| 17 | `42_first_row_no_balance` | 首行无资金余额 | 反推失败 → `NAV_UNRELIABLE`；只出回合级指标；页面红字 + 人话 | FR-R2.1, FR-L6.2 |
| 18 | `43_balance_chain_break` | 人为抽掉一笔转账（3 月 12 日） | 检出率 100%；断点 `trade_id`+期望/实际值（≤`ε_amt`）；区间内资产类 hard-block、区间外标「含 N 笔可疑」 | FR-L6, G2 |
| 19 | `44_no_cash_balance_column` | 交割单格式（无资金余额列） | 自检提示「对账不可用、收益仍可算」；降级而非静默失败 | FR-L3.5 |
| 20 | `45_duplicate_upload` | 同一文件连续导入两次 | 第二次 0 新增；bag 级同键多笔不误判；撤销后行数/回合/指标回到导入前 | FR-L3.4, NFR-5 |
| 21 | `46_interval_replace` | 区间内新账单替换旧数据 | 旧行软删可查、新行生效；复算结果与"直接导入新版"一致 | FR-L3.3 |
| 22 | `47_gbk_excel_serial` | GBK 编码 + Excel 序列号日期 + 前置 4 行标题 + 别名列名（成交数量/成交日期） | 表头识别、列映射记住、日期归一；与 UTF-8 版本解析结果 diff=0 | FR-L3.1/3.2 |
| 23 | `48_dirty_row_isolation` | 数量 0 / 均价 0 / 金额与数量×均价不一致 | 脏行进隔离区并给行号，不阻断整批；未匹配摘要不并入计算 | NFR-2, FR-L5 |
| 24 | `50_n5_insufficient` | 5 个回合、有效指标 | `sample_status=insufficient`；渲染无结论文案；仅原始值 + `n` | FR-R8, A26 |
| 25 | `51_intraday_lt60_days` | 40 个交易日 | 不输出夏普/MDD/IR，标 `pending_window` 且不进分母 | FR-R8, A26 |
| 26 | `52_disposition_only_winners` | 只卖盈、死扛亏（期末浮亏） | `DE>0` 且 bootstrap CI 不含 0；PGR/PLR 与手算一致 | FR-R6.1 |
| 27 | `53_no_disposition_effect` | 对称卖出盈/亏 | `DE≈0` 且 CI 含 0（防"任何数据都报处置效应"） | FR-R6.1 |
| 28 | `54_attribution_buy_point` | 单因子主导（追高买入、其余因子≈0） | 五因子金额和与区间损益差 ≤`ε_amt`（或显式残差）；排序第一 = 买点 | FR-R7.1 |

> 补充两条同表管理但不入主矩阵的 fixture：`55_attribution_residual`（含未归属残差，断言显式列出而非硬凑）、`56_high_beta_bull`（高 β 牛市，断言未标 `benchmark_id+β` 时不得出现"选股能力强"文案，FR-R5.1）。
### 4.4 oracle 策略（防"用被测代码生成期望值"）
| oracle 类型 | 位置 | 覆盖 | 为什么可信 |
|---|---|---|---|
| 人工手算表 | `fixtures/expected/rounds/*.yaml`，由财务口径负责人按附录 A 逐笔手算 | 28 条矩阵中的回合 pnl/收益率 | 与实现零共享代码 |
| 朴素参考实现 | `tests/oracle/naive_fifo.py`、`naive_nav.py`（可读性优先，不考虑性能，独立作者） | 净值、TWR、FIFO | 独立实现路径；生产代码不得 import `tests/` |
| 快照 + 交叉校验 | `**/__snapshots__/*.ambr`；桥接恒等式、数量守恒 | 展示层文案；全量 fixture | 快照只用于**人工审过的**稳定输出（更新需审批 §11.4）；交叉校验不依赖外部期望值，能抓"期望值本身抄错" |

---

## 5. 属性测试（hypothesis）
生成器统一放在 `tests/support/strategies.py`：金额一律 `st.decimals(places=2, min_value=..., max_value=...)`（**不生成 float**）；日期从行情快照的交易日历中取索引，保证生成的流水落在有行情的区间。

| ID | 不变量 | 签名思路 | 生成器策略 | 落地文件 |
|---|---|---|---|---|
| P-01 | 份额法下 TWR 与外部现金流**无关** | `test_twr_invariant_to_external_cashflow(path, flows)`：同一价格路径，插入任意日末 CF，`assert_ret(twr_with, twr_without)` | `price_path()` 2~60 日 Decimal 序列；`flows` 随机日索引 + `±1e6` 金额，同日可空 | `packages/core/tests/properties/test_nav_twr_cashflow_neutral.py` |
| P-02 | FIFO 数量守恒 + 成本守恒 | `test_fifo_conserves_qty_and_cost(bills)`：`Σbuy_qty == Σsell_qty + end_qty`；`Σabs(买入发生金额) == Σ回合买入成本 + 未平仓成本` | `bill_sequence()`：买入/卖出/送转随机组合，卖出量受可配量约束（超卖单独策略） | `packages/core/tests/properties/test_fifo_conservation.py` |
| P-03 | 回合一阶恒等式（桥接） | `test_bridge_identity(run)`：`Σround_pnl + 浮盈亏 + 股息 + 利息 − 未分摊费用 == 期末 − 期初 − 净入金`（`assert_amt`） | 组合 `bill_sequence()` + `price_path()` + 现金余额链自洽生成 | `packages/review/tests/properties/test_bridge_identity.py` |
| P-04 | 导入幂等 | `test_import_idempotent(bills)`：`state(import(X)) == state(import(import(X)))`（行数/回合/`inputs_hash`） | 任意账单序列；含同日多笔与空股东账号 | `packages/ledger/tests/properties/test_import_idempotent.py` |
| P-05 | bag 去重计数正确 | `test_bag_dedupe_counts(bills, k)`：导入 X 后再导入 X 的 `k` 倍副本 → 新增 0；导入 X 加一行同键 → 新增 1 | `k ∈ 1..3`；重复使用 `st.lists(..., min_size=0)` 生成副本 | `packages/ledger/tests/properties/test_bag_dedupe.py` |
| P-06 | 报告渲染对输入顺序不敏感 | `test_report_render_order_invariant(bills)`：`shuffle(bills)` 后渲染 HTML 的规范文本相同 | 对同一 `bills` 施加 `st.permutations`（≤8 行样本） | `packages/delivery/tests/properties/test_render_order.py` |
| P-07 | 金额等比缩放不改比率指标 | `test_ratio_scale_invariance(bills, k)`：所有金额 ×k 后 `TWR/胜率/DE` 不变（`assert_ret`）；金额类指标 ×k | `k ∈ {Decimal("0.37"), Decimal("1"), Decimal("3")}` | `packages/review/tests/properties/test_scale_invariance.py` |
| P-08 | TWR 时间可加 | `test_twr_subinterval_composition(path)`：`1+TWR(0..n) == Π(1+TWR(子区间))` | 随机切分点（保持交易日序） | `packages/core/tests/properties/test_twr_additivity.py` |
| P-09 | 容差/门槛自身的元性质 | `test_tolerance_meta(a,b)`：对称（`Δ` 与 `−Δ` 同判）、边界（恰 `EPS_AMT` 通过、`EPS_AMT+1e-4` 失败）、传递性 | `st.decimals(places=6)` 围绕 `EPS_AMT` 邻域 | `packages/core/tests/properties/test_tolerance_meta.py` |

```python
# P-01 示意（12 行）：这条不变量是份额法定义的同义反复，因此它能抓住"把 CF 当收益"这类实现错
@given(path=price_path(), flows=external_cashflows(), data=st.data())
def test_twr_invariant_to_external_cashflow(path, flows):
    base = compute_nav(path=path, external_cf=[])
    moved = compute_nav(path=path, external_cf=[f for f in flows if f.day in path.days])
    assert_ret(moved.twr, base.twr)             # 出入金不影响 TWR，只影响 MWR
    assert_amt(moved.end_asset, moved.units[-1] * moved.nav_u[-1])
```

**hypothesis 配置**（`pyproject.toml`）：`max_examples=200`（PR full）/`1000`（nightly）；`deadline=None` 仅对纯计算套件；启用 `hypothesis` 的 `derandomize=False` 但在失败时打印 `@seed` 便于复现；对慢套件设 `suppress_health_check=[HealthCheck.too_slow]` 需行内注释理由。

---

## 6. 验收标准的可执行化（A-G*/A-FR 映射）
| 验收编号 | 判据（PRD/Review） | 测试函数 | 断言 | fixture | 门禁 |
|---|---|---|---|---|---|
| A-G1-01 | 30 回合分层抽样 pnl 差 ≤0.01 元 | `test_stratified_30_rounds_pnl[case]`（6 类 ×5 参数化） | `assert_amt(round_pnl, expected)`；样本集合用 `rounding_sample()` 断言分层齐全 | `1*/2*` 账单 6 份（每类 ≥1）+ `expected/rounds/*.yaml` | 硬 |
| A-G1-02 | 回合收益率差 ≤1bp | `test_stratified_30_rounds_return[case]` | `assert_ret`；收益率分母=Σ买入成本（含费） | 同上 | 硬 |
| A-G1-03 | FIFO `trade_id` 集合 100% 一致 | `test_fifo_trade_id_set_equal[case]` | `set(actual) == set(expected)`（非子集） | 同上 | 硬 |
| A-G2-01 | 断链检出率 100%（0.01 元阈值） | `test_balance_chain_break_detection_all[break_case]` | 6 种断链变体全部命中；断点 `trade_id` 精确；`abs(期望−实际) ≤ ε_amt` | `43_balance_chain_break` ×6 变体 | 硬 |
| A-G2-02 | 净值日收益 ≤1bp / 累计 ≤5bp | `test_nav_daily_diff_vs_reference`、`test_nav_cum_diff_vs_reference` | `assert_ret` / `assert_cum` vs `tests/oracle/naive_nav.py` | `10_*` + `market/mkt-*` | 硬 |
| A-G2-03 | golden 同输入 diff=0 | `test_golden_metrics_snapshot`（syrupy） | `assert result == snapshot`；**先剔除** `computed_at` | `fixtures/expected/golden/*.json` | 硬 |
| A-G2-04 | 重复计算 diff=0 | `test_metric_result_recompute_stable` | 两个 `MetricResult` 除 `computed_at` 外全等，含 `inputs_hash` | 全 fixture | 硬 |
| A-L3-01 | 重复上传 0 新增 | `test_import_idempotent_second_upload` | `batch2.inserted == 0 and batch2.skipped == n` | `45_duplicate_upload` | 硬 |
| A-L3-02 | 撤销后回到导入前 | `test_revoke_batch_restores_state` | 行数/回合数/`Σpnl`/净值序列 diff=0 | `45_*` | 硬 |
| A-L5-01 | 未知摘要 100% 隔离 + 行号 | `test_unknown_summary_quarantined_with_row_no` | 隔离数=未知行数；响应行号集合相等；不进指标 | `40_*` | 硬 |
| A-L6-01 | 阻断态页面不渲染收益结论 | `test_blocked_state_hides_return_cards`（delivery） | HTML 不含「年化/超额/夏普」结论词；含 `NAV_UNRELIABLE` 人话 + 断点链接 | `41/42/43` | 硬 |
| A-R2-01 | 期初反推成功/失败双分支 | `test_back_solved_initial_cash`、`test_nav_unreliable_when_backsolve_fails` | `assumption=="back_solved"`；失败分支 `status=="blocked"` 且只出回合级 | `41/42_*` | 硬 |
| A-R3-01 | TWR/MWR 方向与容差 | `test_mwr_vs_twr_direction[deposit_up / withdraw_down / flat]` | 方向断言 + `assert_ret` 对独立 XIRR 实现 | `32_*` | 硬 |
| A-R6-01 | DE>0 且 CI 不含 0；无效应对照 | `test_disposition_effect_detected`、`test_no_disposition_effect_ci_contains_zero` | `DE>0`、`ci_low>0`；对照组 `ci_low<0<ci_high` | `52/53_*`（`seed` 固定） | 硬 |
| A-R7-01 | 五因子和 ≤0.01 元 + 单因子主导排序 | `test_attribution_factor_sum_residual`、`test_attribution_rank_single_factor` | `assert_amt(Σfactors, period_pnl)` 或残差显式非空；`argmax(abs(factor))=="buy_point"` | `54_*` | 硬 |
| A-R8-01 | N<10 不出结论 | `test_insufficient_sample_no_narrative` | `sample_status=="insufficient"`；渲染无「结论/风格/建议」词 | `50_*` | 硬 |
| A-R8-02 | 日频 <60 交易日不出风险指标 | `test_intraday_pending_window` | 夏普/MDD/IR 为 `None`；`sample_status=="pending_window"`；不进分母 | `51_*` | 硬 |
| A-R5-01 | 高 β 不得未标注即断言选股能力 | `test_high_beta_requires_benchmark_annotation` | 文案必须含 `benchmark_id` 与 `β`；否则 fail | `56_high_beta_bull` | 硬 |
| A-NFR2-01 | hfq/qfq 收益率一致性 + 涨跌停越界 | `test_factor_consistency_and_limit_bounds` | 一致性 <1bp；越界行告警且**不入计算** | `market/mkt-*` + 恶意见证数据 | 硬 |

---

## 7. 架构约束测试
### 7.1 import-linter 合约（`pyproject.toml` `[tool.importlinter]`）
以 C2 为代表（其余同构）：
```toml
root_packages = ["contracts", "core", "ledger", "review", "delivery", "platform", "server"]
[[contracts]]                          # 写法示意：C2 core 纯领域
name = "C2 core 不得 import 框架与上下文"
type = "forbidden"
source_modules = ["core"]
forbidden_modules = ["ledger", "review", "delivery", "platform", "server",
                     "fastapi", "sqlalchemy", "alembic", "jinja2", "httpx", "structlog"]
```

| 合约 | 类型 | 源 → 禁止 | 为什么必须存在 |
|---|---|---|---|
| C1 contracts 无反向依赖 | forbidden | `contracts` → 其余全部包 | 契约是跨上下文唯一共享语言，反向依赖会形成环 |
| C2 core 纯领域 | forbidden | `core` → 上下文 + `fastapi/sqlalchemy/alembic/jinja2/httpx/structlog` | core 是口径唯一实现处，掺入 I/O 即不可测、不可重放 |
| C3 domain 隔离 | forbidden | `{ledger,review}.domain` → 各自 `infra/api` + `sqlalchemy/fastapi` | 六边形核心：domain 只定义端口，实现由 infra 提供 |
| C4 跨上下文只经 ports/contracts | forbidden | `review.{app,domain}` → `ledger.domain/ledger.infra` | 度量层只能经 `ledger.api` 门面与 `contracts` DTO 取数，禁止穿透 |
| C5 presentation 不碰 DB | forbidden | `delivery.{web,reports,chat}` → `sqlalchemy/{ledger,review}.infra/platform.db` | 页面绕过 app 层即绕过对账闸门，会渲染不可信收益 |
| C6 上下文内分层 | layers | `ledger.api` → `ledger.app` → `ledger.domain`；`ledger.infra` 不得 import `ledger.api` | 单向依赖；适配器同层互不依赖 |

**为什么 layers + forbidden 组合**：六边形里 `api` 与 `infra` 同属适配器层，纯 `layers` 表达不了"同层互不依赖"，必须补 forbidden。
### 7.2 其它防腐化测试
| 测试文件 | 断言 | 为什么 |
|---|---|---|
| `tests/architecture/test_import_contracts.py::test_lint_imports_passes` | `subprocess.run(["uv","run","lint-imports"])` 返回 0，且**逐合约名字**断言输出含 C1~C6 | 防止合约被静默删除（删了也返回 0） |
| `tests/architecture/test_core_purity.py::test_core_has_no_io` | AST 扫描 `open/socket/requests/httpx/sqlite3` 与 `os.environ` 在 `packages/core` 零命中 | core 一旦读环境就不再可测、不可重放 |
| `tests/architecture/test_ports_fakes.py::test_every_port_has_fake` | 枚举 15 个 Protocol（`TradeRepository/RoundRepository/NavRepository/MetricRepository/ReportRepository/JournalRepository/MarketDataSource/BillParser/Clock/FileStore/BackupTarget/LlmProvider/ToolGateway/EventBus/JobRunner`），每个在 `tests/fakes/` 有 fake 且 `isinstance(prod_adapter, Protocol)` 成立（`runtime_checkable`） | 端口加方法而 fake 未更新 → 立即红，避免适配器漂移 |
| `packages/platform/tests/test_job_singleflight.py` | 同 `(kind, scope)` 第二次 run 返回 `skipped`；`checkpoint_json` 中断后续跑结果 == 一次跑完 | 作业模型是横切正确性，续跑错了会产出半套指标 |
| `tests/architecture/test_public_api_frozen.py` / `test_delivery_no_repo.py` | 每包 `__all__` 的 syrupy 快照；加载 `delivery.web` 后 `sys.modules` 不含 `sqlalchemy` | 新增导出需人工确认；与 C5 双保险（静态合约 + 动态加载） |

---

## 8. 变异测试
| 项 | 约定 | 为什么 |
|---|---|---|
| 作用范围 | 仅 `packages/core/src/core`（`fifo.py/nav.py/metrics/*/attribution.py/values/*`） | core 是口径唯一实现处；对 infra/模板做变异收益极低 |
| 目标分数 | 全局 **≥80%**；单模块硬门槛：`values/decimal_math.py`、`metrics/tolerance.py` **≥95%**；`fifo.py`、`nav.py` **≥90%**；`attribution.py` **≥85%** | 容差与舍入错一个 ULP 就会全站偏；归因分支多，85% 是可达且有意义的下限 |
| 等价变异体 | 登记在 `tools/mutmut_equivalent.json`（含理由与人工复核人），不计入分母 | 避免为凑分数写无意义测试 |
| 运行方式 | `mutmut run`（nightly 全量）；PR 只跑 `git diff` 涉及的 core 文件（`mutmut run --paths-to-mutate "$(changed)"`） | 全量 ~40min，PR 反馈需 ≤10min |
| 失败策略 | nightly score 下降 >2pp 或跌破门槛 → 建 issue 并标 `quality-gate`，不阻断当次合并但阻断 release | 变异修复成本高，需预算而非阻塞日常 |
| 缓存 | `.mutmut-cache` 入库到 CI cache（key=core 源码 hash） | 重复变异体可跳过 |

**必须杀死（kill）的算子清单**——这些是"分支跑到但结果错"的高发点：

| 变异算子 | 高发位置 | 为什么必须杀死 | 杀手测试 |
|---|---|---|---|
| `<` ↔ `<=` | `metrics/win_rate.py`（`pnl > 0`）、`tolerance.py` | 胜率边界、容差边界直接决定验收是否通过 | `test_win_rate_excludes_zero_pnl`、P-09 元性质 |
| 符号翻转 `+`/`−` | `fifo.py`（买入负/卖出正）、`behavior.py`（`DE = PLR − PGR`） | DE 写反会把"死扛亏损"说成"跑得快" | `test_disposition_effect_detected`（方向断言） |
| 容差方向/常量替换 | `tolerance.py`（`EPS_AMT/EPS_R/EPS_CUM`）、`A=244` | 用 252 或 1% 会系统性偏差 | `test_constants_match_prd_appendix`、`test_sharpe_uses_244` |
| 分母 `n`/`n−1`/分母选错；舍入模式 | `stats/wilson.py`、`metrics/sharpe.py`、费用占比；`values/decimal_math.py::quantize_money` | 分母错是"数字对但量级错"；0.005 元舍入差异会破坏 `ε_amt` 对拍 | golden 快照 + 手算表；`test_quantize_money_boundaries`（0.005/0.015/0.025） |
| `and` ↔ `or`（短路） | `ledger/domain/reconcile.py`（分级阻断条件）、`sample_gate.py` | 阻断条件写松会渲染不可信收益 | `test_blocked_state_hides_return_cards` |
| `None` ↔ 默认值 | `timing.py::chase_percentile`（高低相等返回 NULL） | 除零后返回 0 会被当成"买在最低点" | `test_chase_percentile_flat_window_returns_none` |
| 循环边界 `range(n)` ↔ `range(n-1)` | `fifo.py` 配对循环、`attribution.py` 因子累加 | 漏最后一笔买入/最后一个因子 | P-02 守恒属性 |

---

## 9. 数据质量与数据源契约测试
### 9.1 `MarketDataSource` 端口契约（离线可测）
| 断言 | 内容 | 为什么 |
|---|---|---|
| 结构与复权 | 列名/类型/dtype 固定；日期单调不重复；价格 >0；`factor_version` 非空、因子 >0 且累积单调；`daily_hfq`/`daily_qfq` 同日内收益率一致 | 下游指标假设列语义，漂移即静默错；前复权漂移会让历史回撤下周变另一个数（PRD §9） |
| 时点 | `meta.data_as_of` 存在且 ≤ 请求 `as_of`；财报行带 `announce_date` | NFR-3 |
| 停牌/退市 | 停牌是"缺行"还是"前收填充"必须显式声明并被测试覆盖；退市股不消失 | NFR-2 |
| 错误语义 | 限频/超时/字段缺失抛领域异常（非 `KeyError`），错误含 source/fetched_at | 适配器不能把网络问题伪装成空数据 |
| 回放一致性 | 录制快照回放结果 == 录制时结果（`sha256`） | 离线可测的前提 |

**录制回放**：不自建 VCR 依赖，用 `tests/support/recording.py::find_or_replay`：默认只读 `tests/cassettes/<source>/<endpoint>/<yyyymmdd>-v1.json`；录制必须显式 `MARKET_RECORD=1` 且 CI 中该变量被禁止（`test_ci_forbids_recording`）。`ReplayMarketData` 是 `MarketDataSource` 的正式实现之一，与 `AkshareAdapter`、`AlphaFeedAdapter` 一起跑同一套参数化契约测试：

```python
@pytest.mark.parametrize("impl", [AkshareAdapter, AlphaFeedAdapter, ReplayMarketData])
def test_market_data_port_contract(impl, market_snapshot):
    src = impl(cassette=market_snapshot)          # 同一断言集，三个实现
    bars = src.daily("300308", "2023-01-01", "2023-06-30", adjust="hfq")
    assert_market_bars_contract(bars)              # 列/单调/正价/因子/factor_version
```
### 9.2 校验层恶意见证数据（`fixtures/market/adversarial/`）
| 见证数据 | 构造 | 期望行为与理由 | 断言 |
|---|---|---|---|
| `adv_factor_mismatch` | 除权日 hfq 与 qfq 收益率差 30bp | 告警并**拒算**该标的当日收益，不入指标 | `status=="blocked"`、`metric` 的 `data_as_of` 不变 |
| `adv_limit_up_break` | 主板单日 +10.01% / −10.01%；ST ±5.01%；创业板 ±20.01%；北交所 ±30.01% | 越界行告警隔离；恰 ±10.00% 必须**通过** | 边界精确：`10.00%` ok、`10.01%` warn |
| `adv_new_listing` | 新股首日无涨跌停、上市日 < 首根 K 线 | 不误判越界；上市日之前无持仓 | 上市日前持仓为空 |
| `adv_factor_jump` | 单日复权因子变化 >50% 且无公司行为公告 | 告警 + 缓存失效 + 重拉标记 | 告警事件落库（`source/fetched_at/行数/校验结果`） |
| `adv_delisted_vanished` | 退市日前有持仓，退市后数据源返回空 | 持仓不从样本消失，用最后价 + `stale` | 全样本胜率分母包含该持仓 |
| `adv_timezone_shift` | 日期写成 UTC 前一日 | 归一为 UTC+8 本地日，不产生跨日错位 | 与基准日历对齐、无重复日 |

---

## 10. CI 流水线设计
| 通道 | 触发 | 内容 | 超时 | 失败策略 |
|---|---|---|---|---|
| **pre-commit** | 本地提交 | ruff format/check、`lint-imports`、mypy（增量）、相关单测（`pytest -x -q packages/core/tests/unit`） | 60s | 阻断提交 |
| **PR fast lane** | PR 打开/推送 | ruff、mypy --strict（changed packages）、`tests/architecture/`、`tests/data/`、core+domain 单测、diff coverage | **4min** | blocking |
| **PR full lane** | PR 加 `full-ci` label / ready-for-review | fast + 全部集成 + 端口契约 + golden + 属性（200 examples）+ 按包覆盖率门禁 | **18min** | blocking |
| **nightly** | 每日 03:00 | full + e2e + 性能 + 数据源回放契约 + 属性 1000 examples + 变异全量 + PG profile（`docker compose --profile pg` 集成） | **75min** | 失败建 issue（`quality-gate`），不阻断他人 PR；连续 2 天失败升级为 blocker |
| **release** | tag | nightly 全量 + golden 冻结校验 + 迁移 upgrade/downgrade 往返 + 备份/恢复演练 | **120min** | blocking（任何一项失败禁止发布） |

**覆盖率按包分别判定**（不合并成一个大报告）：

```yaml
# .github/workflows/ci.yml（片段，非完整文件）
- run: |                       # 硬门禁：按包分别 --cov-fail-under，不合并成一个大报告
    uv run pytest packages/core/tests -q --cov=core --cov-branch --cov-fail-under=100
    uv run pytest packages/ledger/tests/unit packages/review/tests/unit -q --cov=ledger.domain --cov=review.domain --cov-branch --cov-fail-under=98
    uv run pytest packages/platform/tests -q --cov=platform --cov-branch --cov-fail-under=90
    uv run pytest packages/contracts/tests -q --cov=contracts --cov-fail-under=97
- run: uv run pytest packages/delivery/tests -q --cov=delivery --cov-fail-under=80 || echo "::warning::delivery cov"   # 软门禁
- run: uv run python tools/gate_diff_coverage.py --xml cov-core.xml --min=95    # 新增行 ≥95%，硬
```

**为什么 fast lane 要跑架构约束与数据质量**：这两类测试总耗时 <25s，却能挡住"最贵的错误"（越层访问、fixture 带 PII、缺口径常量）；放到 full lane 会让坏设计活到 reviewer 面前。
**为什么 golden 只在 full/release 跑**：它是语义门禁，diff 需要人工判读（`golden-approved`），不适合每次 push 自动通过；但 release 前必须冻结。

---

## 11. 缺陷预防与豁免政策
### 11.1 flaky test
| 项 | 规则 | 为什么 |
|---|---|---|
| 判定 | 同一 commit 连续 3 次结果不稳，或 nightly 同类失败出现 ≥2 次但本地不可复现 | 单次失败可能是真 bug，不能一律归为 flaky |
| 处置 | 立即移动到 `tests/quarantine/`（默认 `--ignore`），PR 里同时开 issue，SLA **5 个工作日**：修复或删除；超期自动 fail nightly | 隔离而不删除，避免掩盖真实缺陷 |
| 禁止 | **禁止** `pytest-rerunfailures`、禁止用 shell 短路忽略失败、禁止在 CI 里重跑整条流水线来"让它绿" | 重跑掩盖是把偶发当正常，系统性问题会累积 |
| 度量 | nightly 输出 flaky 率；门槛 **<1%**，超过则下个迭代优先治理 | 可观测才可治理 |
### 11.2 `# pragma: no cover` 使用条件
| 允许场景 | 附加要求 | 为什么 |
|---|---|---|
| 仅单平台可达分支（Windows/macOS 启动器） | 同行注释 `# pragma: no cover - macos-only launcher, see CI matrix` | 跨平台分支无法在单 CI 机器全覆盖 |
| `if TYPE_CHECKING:` / import 循环防御；Alembic 迁移脚本 | 后者文件级 omit 不进包覆盖率统计 | 前者运行期不执行；后者由 upgrade/downgrade 往返测试覆盖 |
| `except BaseException: raise` 重抛 | 需 `issue: #NNN` 且 reviewer 确认不可构造 | 防御代码，但必须留痕 |

**审计**：`tools/check_pragma.py` 统计 `# pragma: no cover` 数量，>10 处或比上次增加即 fail fast lane；每处必须能映射到上表场景，无法映射即视为覆盖率规避。
### 11.3 fixture 漂移审批
| 变更类型 | 审批 | 理由 |
|---|---|---|
| 新增 fixture | 1 名 reviewer + 更新 `MANIFEST.json`（sha256/owner/synthetic） | 防止"改 fixture 让测试变绿" |
| 修改既有 fixture 数据 | **≥2 名** reviewer（含口径负责人），PR 描述必须写"为什么旧数据是错的" | fixture 是验收基准；改它等于改合同 |
| 删除 fixture | 需在 `MANIFEST.json` 保留 tombstone + 理由 | 防止覆盖回退 |
| `fixtures/private/` | 永不入库；`.gitignore` + CI 扫文件名 | 真实资金数据 |
### 11.4 golden / syrupy 更新规则
- 更新命令固定为 `uv run pytest --snapshot-update`，且**只允许**出现在单独 PR；混在业务 PR 里直接 review 拒绝。
- PR 必须贴出快照 diff（金额变化精确到分、比率变化精确到 bp），并标注 `golden-approved` label（由口径负责人加）。
- `tools/golden_diff_guard.py`：若 diff 同时含 `packages/core|review` 源码与 `*.ambr`，要求 `golden-approved` 否则 fail。
- `definition_version` 变更必须伴随快照更新与迁移说明（NFR-11）；版本号未升而快照变了 → fail。

---

## 12. 测试数据 PII 与隐私
真实账单含股东账号与真实资金，**fixture 必须 100% 合成或脱敏**；真实数据只允许放 `fixtures/private/`（`.gitignore`），并仅在本地 `--private-fixtures` 层跑。

| 字段 | 规则 | 为什么 |
|---|---|---|
| 股东账号 | 映射为 10 位合成串 `0000000001..`（保留长度与空值语义）；映射表 `_pii_map.local.json` 不入库 | `dedupe_key` 依赖账号，但真实账号是可识别金融标识 |
| 资金账号/客户姓名 | 一律替换为 `88888888` / `张三`；文件前部标题行保留结构但改内容 | 华泰账单前 2~5 行常含姓名账号，正是导入解析要测的地方 |
| 金额 | **保序缩放**后重算：选定 factor（如 0.37），所有原始金额取整到分后重算「资金余额」列，而非逐行缩放 | 逐行缩放会让 `round` 误差破坏余额链，导致 fixture 本身触发断链——余额必须由缩放后的流水重算才自洽 |
| 证券代码/名称 | **保持真实**（如 `300308` 中际旭创） | 行情快照按代码对齐，改代码会让复权/基准测试失真 |
| 日期 | 保持真实交易日（与行情快照对齐） | 自然日年化、交易日计数依赖真实日历 |
| 备注/操作理由、文件名 | 备注清空或合成固定短语；文件名不含姓名/账号/券商客户号 | 自由文本与文件名都会进 git 历史、日志与 CI artifact |
| 备份/导出产物 | e2e 只使用合成 fixture；真实目录不参与测试 | 导出测试最容易把真实数据写进 CI artifact |

**自动化执行**：

| 测试 | 断言 | 为什么必须自动化 |
|---|---|---|
| `tests/data/test_fixtures_no_pii.py`、`test_fixture_manifest.py` | 正则扫描：无 18 位身份证、无 11 位手机号、无真实股东账号；每个 fixture 有 MANIFEST 条目、sha256 匹配、`synthetic: true` | 人眼无法覆盖 5 万行 fixture 的敏感串 |
| `tests/data/test_pii_amount_chain_closed.py` | 脱敏后 fixture 的余额链**仍闭合**（"缩放不破坏链"的回归测试） | 脱敏本身会引入断链，必须防 |
| `tests/data/test_fixture_amounts_disjoint.py` | fixture 金额集合与 `private/` 真实金额集合**零交集**（本地层运行时生效） | 人工审查看不出金额是否来自真实账单 |
| `test_privacy_outbound.py::test_llm_payload_has_no_amount_no_account`（M3，`delivery/chat`） | 出站 payload 正则扫描：无 10 位账号、无 fixture 金额集合命中 | NFR-1 是合规红线，必须机器可证 |

---

## 附录 A：测试文件索引（约定落地位置）
| 文件 | 职责 |
|---|---|
| `tests/conftest.py` | 全局 fixture：`FixedClock`、`tmp_path` 文件库、`market_snapshot`、`replay_market_data`、`no_network` |
| `tests/support/tolerance.py` | `assert_amt/assert_ret/assert_cum`（唯一容差入口） |
| `tests/support/strategies.py` | hypothesis 生成器（账单序列、价格路径、现金流） |
| `tests/oracle/naive_fifo.py`、`naive_nav.py`、`naive_xirr.py` | 独立参考实现（生产代码禁止 import `tests/`） |
| `tests/fakes/` | 15 个端口的 fake（`tests/architecture/test_ports_fakes.py` 强制齐全） |
| `tests/architecture/test_import_contracts.py` | 运行 import-linter 并逐合约名断言 |
| `tests/architecture/test_no_wall_clock.py`、`test_no_sleep.py`、`test_core_purity.py` | 确定性 AST 扫描 |
| `tests/contract/` | 端口契约：`test_market_data_port.py`（数据源回放）+ 各 Repository 契约 |
| `tests/data/test_fixtures_no_pii.py`、`test_fixture_manifest.py` | fixture 治理 |
| `packages/review/tests/golden/test_golden_metrics.py` | golden 快照与 `fixtures/expected/golden/` 对拍 |
| `tests/e2e/test_first_run_to_report.py` | FR-L1 + D1 + D2 全流程（合成数据，无需网络） |
