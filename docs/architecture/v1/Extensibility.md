# 可扩展性设计（Extensibility Design）

> 版本 v1.0 ｜ 2026-10-19 ｜ 上游：`docs/PRD-v1.md`（FR-R4~R8、FR-D2/D3、附录 A、NFR-9/11）与 `docs/Review.md` §3 R-C3、§5 口径修订表
> 立场：扩展性不是抽象越多越好。本文定义 **11 个扩展点**、明确**拒绝 6 类扩展**（§10）。贯穿规则：**没有 contract test 的扩展点不算扩展点**。
## 1. 扩展性目标与判据
### 1.1 可度量验收（每条都能在 CI 或一次 PR 里判定）
| # | 判据 | 度量方式 | 阈值 | 验证手段 |
|---|---|---|---|---|
| X-1 | 新增一个指标**零改既有文件** | PR 的 `git diff --stat` | 新增 ≤2 文件（def+fixture），既有文件改动 **=0** | `tools/ci/change_scope.py` + 自动发现测试 |
| X-2 | 新增券商账单**不动解析主流程** | 改动 ∩ `ledger/app/import_*.py`、`ledger/domain/dedupe.py` | 交集 **=∅** | change_scope + parser 契约测试 |
| X-3 | 新增数据源**只实现 1 个 Protocol** | 实现文件数 | **=1**（+1 配置行），`review/domain`、`review/metrics` 改动 =0 | `tests/contract` 参数化 |
| X-4 | 新增报告区块**不改渲染框架** | `delivery/reports/renderer.py`、`delivery/web/render/` 改动 | **=0**；+1 block 文件 +1 模板片段 | 区块契约 + 快照测试 |
| X-5 | 任意两包**零循环依赖** | import-linter | violation **=0** | `lint-imports --include-type-checking` |
| X-6 | 口径变更后**全量重算 ≤60s**（5 万行） | bump `definition_version` 后重算 | ≤60s，旧结果 100% `stale`，报告出现口径断点 | 性能 job + golden |
| X-7 | 新增事件消费者 / 写错配置**不触核心** | `ledger/**`、`review/domain/**` 改动；非法配置启动行为 | 改动 **=0**；启动即失败并报字段路径 | fake consumer 集成测试（§9）+ `platform/config` 单测 |
### 1.2 改动成本预算表（超出即视为架构退化，可拒收）
| 变更类型 | 允许触碰的包 | 禁止触碰 | 需同步版本 | 工时 |
|---|---|---|---|---|
| 新增指标 | `review/metrics/defs/`（+`core/metrics/` 新公式） | `core/*` 既有文件、`review/app/*`、`delivery/*`、`ledger/*` | `definition_version=1.0`（新 id） | 0.5~1d |
| 修改指标口径 | 该 def 文件 + `docs/migrations/` 说明 | 其他 def、注册表、渲染层 | `definition_version` MAJOR/MINOR++ | 0.5d |
| 新增数据源 | `review/infra/marketdata/<src>.py` + `config/market.yaml` | `review/domain`、`review/app/metrics/*` | `extension_api_version` 兼容 | 1~2d |
| 新增券商解析器 | `ledger/infra/parsers/<broker>.py` + fixtures | `ledger/app/import_*.py`、`parsers/registry.py`（自动发现） | `parser.version` | 0.5~1d |
| 摘要分类 / 行为规则 | `config/ledger/summary_rules.yaml`、`config/review/behavior_rules.yaml` | **任何 Python 文件** | `config_version` / `rule_pack_version` | 0.5h |
| 新增报告区块 / 导出格式 | `delivery/reports/blocks/<id>.py`、`export/<fmt>.py` + 模板 + 配置顺序 | `delivery/reports/renderer.py`、`delivery/web/` 框架 | `report_schema_version` / `export_format_version` | 0.5~1d |
| 新增事件消费者 | `delivery/notify/<ch>.py` 或独立 adapter | `ledger/**`、`review/domain/**` | 事件 `schema_version` 不变 | 0.5d |
| 新增 API 端点 | `delivery/web/api/v1/<router>.py` + 用例 | `core/**`、domain | URL 保持 `/api/v1` | 0.5d |

**落地节奏**：M0~M1 必做 EP-1/2（单源）/3（华泰）/4/5/7 + 契约测试套件；M2 做 EP-6/8/10 + fallback 第二源；M3 只落 EP-9/EP-11 的协议与事件位（见 §2 表"落地"列）。
## 2. 扩展点总览表（核心产出）
| 编号 | 扩展点 | 所在包 / 路径 | 注册机制 | 契约 | 版本化 | 稳定性 | 落地 |
|---|---|---|---|---|---|---|---|
| **EP-1** | 指标插件 | `review/metrics/defs/` | 包内 `pkgutil` 自动发现（**只扫自有包**） | `MetricDef`+`compute(ctx)->MetricValue` | `definition_version` | stable | M1 |
| **EP-2** | 数据源适配器 | `review/infra/marketdata/` | `config/market.yaml` 显式列表 + 工厂字典 | `MarketDataSource` | `capabilities` 协商 | stable | M0 单源 / M2 多源 |
| **EP-3** | 账单解析器 | `ledger/infra/parsers/` | 自动发现 + probe 打分 + 用户映射 profile | `BillParser`/`ParserSpec` | `parser.version` | stable | M0 华泰 / M2 第二家 |
| **EP-4** | 摘要分类规则 | `ledger/config/summary_rules.py`（数据在 `config/ledger/summary_rules.yaml`） | 配置加载 + 优先级 | `SummaryClassifier`（声明式，无代码） | `rule_pack_version` | stable | M0 |
| **EP-5** | 基准与提供方 | `review/metrics/benchmark/` | `config/review/benchmarks.yaml` 候选表 | `BenchmarkProvider` | `benchmark_id`+`benchmark_version` | stable | M1 |
| **EP-6** | 归因模型 | `core/attribution/` + `review/metrics/defs/attr_*.py` | `MetricGroup`，模型实现为组内 def | `AttributionModel` | `definition_version`+`group_version` | experimental | M2 |
| **EP-7** | 报告区块 | `delivery/reports/blocks/` | 自动发现 + 配置顺序/开关 | `ReportBlock` | `report_schema_version`+`block_id` | stable | M1 |
| **EP-8** | 行为规则卡 | `review/behavior/rules/`（数据在 `config/review/behavior_rules.yaml`） | 配置加载 | `BehaviorRule.evaluate`（纯函数） | `rule_pack_version` | experimental | M2 |
| **EP-9** | LLM Provider 与工具 | `delivery/chat/` | 工厂字典 + `tools/registry.py` | `LlmProvider`/`ToolGateway`/`Tool` | `extension_api_version` | experimental | M3 |
| **EP-10** | 导出格式 | `delivery/reports/export/` | 自动发现 | `Exporter` | `export_format_version` | experimental | M2 |
| **EP-11** | 通知通道（预留） | `delivery/notify/` | 事件消费者注册表 | `NotificationTarget` | `extension_api_version` | internal | M3 |
### 2.1 契约签名（每个扩展点一条主签名；完整方法集见各节）
```python
class MetricDef(Protocol):                    # EP-1（字段清单见 §3.1）
    def compute(self, ctx: "MetricContext") -> "MetricValue": ...
class MarketDataSource(Protocol):             # EP-2（完整 7 方法见 §4）
    def daily_bars(self, symbols, *, start, end, adjust="HFQ") -> "BarsFrame": ...
class BillParser(Protocol):                   # EP-3（完整定义见 §5）
    def parse(self, raw: "RawFile", m: "ColumnMapping") -> "ParseOutcome": ...   # 不写库
class SummaryClassifier(Protocol):            # EP-4
    def classify(self, summary: str, ctx: "RowContext") -> "Classification": ...
class BenchmarkProvider(Protocol):            # EP-5
    def series(self, *, start: date, end: date, total_return: bool = True) -> "BenchmarkSeries": ...
class AttributionModel(Protocol):             # EP-6（另有 factors()/reconcile() 返回残差）
    def decompose(self, ctx: "AttributionContext") -> "Decomposition": ...      # 纯函数
class ReportBlock(Protocol):                  # EP-7（block_id/order/requires 为类属性）
    def render(self, vm: "ReportViewModel", *, mode: Literal["html","text"]) -> str: ...
class BehaviorRule(Protocol):                 # EP-8
    def evaluate(self, snap: "BehaviorSnapshot") -> "RuleVerdict | None": ...
class LlmProvider(Protocol):                  # EP-9
    def complete(self, req: "ChatRequest", *, timeout_s: float) -> "ChatResponse": ...
class Tool(Protocol):
    def invoke(self, args: "BaseModel", deps: "ToolDeps") -> "BaseModel": ...    # 只回本地计算结果
class ToolGateway(Protocol):
    def call(self, name: str, args: "BaseModel", *, request_id: str) -> "ToolResult": ...
class Exporter(Protocol):                     # EP-10
    def export(self, report: "RenderedReport", target: "FileStorePath") -> "ExportArtifact": ...
class NotificationTarget(Protocol):           # EP-11（M3）
    def deliver(self, msg: "NotificationMessage") -> "DeliveryReceipt": ...
```

注册只允许两种：**自有包内 `pkgutil` 自动发现** + **配置显式列表**。禁止扫 `site-packages`、禁止 `importlib` 加载用户路径、禁止 entry-points 加载第三方包（见 §10「动态加载第三方 Python 包」）。
## 3. 指标注册表设计（最重要）
### 3.1 `MetricDef` 字段清单
| 字段 | 类型 | 说明与约束 | 消费者 |
|---|---|---|---|
| `id` | `str` | `^(selection\|timing\|behavior\|attribution\|perf)\.[a-z0-9_]+$`，全局唯一，**发布后永不复用** | 全局 |
| `definition_version` | `str` | `MAJOR.MINOR`；MAJOR=分母/口径方向变化，MINOR=精度/窗口/文案 | 失效判定、报告断点 |
| `domain` | `Domain` | `selection/timing/behavior/attribution/perf`（对应附录 A 归属） | 看板分卡、核心结论预算 |
| `scope` | `ScopeSpec` | `unit∈{round,position,day,trade}`、`cohort`（FR-R4：`buy_date`）、`period` 解析规则 | 引擎、drilldown |
| `params_schema` | `type[BaseModel]` | Pydantic v2，`extra="forbid"`；默认值即默认口径 | API 校验、`inputs_hash` |
| `dependencies` | `tuple[MetricDep\|DatasetDep]` | 数据集（`rounds/bars_hfq/positions_end/benchmark_series/calendar`）或指标；**深度 ≤3、必须无环** | DAG 拓扑、失效传播 |
| `compute` | `Callable[[MetricContext], MetricValue]` | 纯函数；**不得返回 ci/status/data_as_of** | 引擎 |
| `explain` | `ExplainSpec` | FR-D3 三段式：`line1` 结论模板、`line2` 算法模板（去术语） | 渲染层 |
| `drilldown` | `DrilldownSpec` | 第三行"涉及哪几笔"：dataset+过滤/排序/上限，禁止自由 SQL | 下钻抽屉 |
| `sample_policy` | `SamplePolicy` | `min_n=10`、`ci_min_n=30`、`ci∈{wilson,bootstrap}`、`min_trading_days=60`、`pending_rule` | 引擎（FR-R8） |
| `status_policy` | `tuple[StatusRule,...]` | 声明式：`("upstream=blocked",BLOCKED)`、`("pending_ratio>0.3",UNRELIABLE)` | 引擎 |
| `unit`/`precision` | `Unit`/`int` | `CNY/ratio/bp/days/count/z/score`；展示小数位 | 格式化、导出 |
| 附加必需 | `core_conclusion`/`frequency`/`benchmark_required`/`mvp` | `core_conclusion` 总数必须落在 **8~12**（A26 多重比较） | 看板、行动建议过滤 |
### 3.2 框架统一生成 `MetricResult`（指标自己不算 ci、不算 status）

`compute` 只能返回 `MetricValue{value, n, decomposition?, by_group?, n_pending?, meta?}`——它**拿不到 `status` 字段**，这是 FR-R8 不被绕过的结构性保证。

| 步骤 | 动作 | 代码位置 |
|---|---|---|
| 1 | 解析/校验 params，解析 period 与 `as_of`；按 DAG 取/算依赖，投影数据集视图 | `review/app/metrics/engine.py`、`datasets.py` |
| 2 | 前置对账闸门（FR-L6 / R-C4 分级阻断） | `review/app/metrics/gate.py` |
| 3 | 调 `compute(ctx)` → `MetricValue`（第 4~6 步全部由框架完成） | 各 def 文件 |
| 4 | 统一算 CI（Wilson/bootstrap，`Random` 由 Clock 注入）+ 求 status/门槛 | `core/stats/ci.py`、`core/metrics/status.py` |
| 5 | 统一收集 `assumptions`：`back_solved`/`benchmark_id`/`adjust=hfq`/`factor_version`/`rf`/`pit_rule` | `review/app/metrics/assumptions.py` |
| 6 | 统一算 `inputs_hash`、`data_as_of`，落库，发 `MetricsComputed` | `review/app/metrics/persist.py` |

`inputs_hash = sha256(canonical(metric_id, definition_version, engine_version, params, 各依赖 data_version_token, as_of))`。
**FR-R8 三重保障**：(a) compute 无法写 status；(b) 阈值常量单点定义在 `core/metrics/policy.py`，lint 测试禁止他处出现 `10/30/60` 魔数；(c) 渲染层只看 `result.status`——`insufficient` 只渲染原始值+「样本不足」，`line1` 取模板库 `insufficient_text`，`exploratory` 永不进入"下月行动"（`delivery/reports/action.py` 过滤）。
### 3.3 失效判定算法（脏标记 + 版本比对）
```python
# packages/review/src/review/app/metrics/cache.py
FRESH, STALE_VERSION, STALE_DATA, STALE_PARAMS, STALE_ENGINE = "fresh","stale_version","stale_data","stale_params","stale_engine"

def explain_staleness(c: "CachedResult|None", d: MetricDef, p: BaseModel, tokens: Mapping[str, str]) -> str:
    if c is None: return STALE_DATA
    if c.definition_version != d.definition_version: return STALE_VERSION   # 口径变更（NFR-11）
    if c.engine_version != ENGINE_VERSION:            return STALE_ENGINE    # 框架语义变更
    if c.params_hash != hash_params(p):               return STALE_PARAMS
    if any(c.tokens.get(k) != tokens[k] for k in c.tokens): return STALE_DATA
    return FRESH

def invalidate(data_token: str) -> frozenset[str]:          # 由 TradesChanged 等事件驱动
    dirty, todo = set(), list(REVERSE_DEPS.get(data_token, ()))
    while todo:
        mid = todo.pop()
        if mid in dirty or data_token not in ALL_DEPS[mid]: continue
        dirty.add(mid); todo.extend(REVERSE_DEPS.get(mid, ()))
    cache.mark_stale(dirty)                                 # 只标脏，不阻塞写路径
    return frozenset(dirty)
```

| 规则 | 要求 |
|---|---|
| 数据版本 | 每类数据集一行 `data_version`（`trades`=max(updated_at)+行数摘要；`bars_hfq` 追加 `factor_version`；`benchmark` 追加 `benchmark_version`）。**降级选项**：token 维护成本过高时退化为「任一数据集变更 → 相关域全量重算」，NFR-10 的 ≤60s 允许该简化，但仍保留 `definition_version` 比对 |
| 读路径 | 命中 stale 返回旧值 + `status=stale` 并投递重算 job；前端显示「口径更新中」，不把旧数字当现值 |
| 写路径 | 覆盖同一 `(metric_id, scope_key, period_key, definition_version)` 槽位；历史版本保留供报告对比 |
| 报告 | 存在 stale/blocked 拒绝出正式版；`--allow-stale` 时脚注「含 N 项旧口径」；传播只沿真实 token 依赖边，不做全表清空 |
### 3.4 依赖 DAG：拓扑执行与环检测
| 机制 | 规则 | 失败表现 |
|---|---|---|
| 构建 | 启动时 `registry.build_graph()`：Kahn 拓扑排序，level = `selection1/timing2/behavior2/perf3/attribution4` | 有环 → 启动失败并打印环路径 `a -> b -> c -> a` |
| 约束 | 只允许依赖**同级或更低 level**；跨级需显式 `cross_level=True` 并写理由；深度 ≤3 | 违规 → 启动失败 |
| 执行 | 同 level 无依赖可并行（`JobRunner` 分片）；单请求只算依赖子树；`MetricGroup(id,members,invariants)` 校验 `Σ因子 ≈ 总损益 (±ε_amt)` | 组不变量失败 → 组内指标 `unreliable`，报告列残差 |
### 3.5 三个示例（只示字段填法）

**例 1：胜率（已平仓 + 全样本并列，A10/FR-R5.3）**
```python
# packages/review/src/review/metrics/defs/perf_win_rate.py
class WinRateParams(BaseModel):
    basis: Literal["closed", "all"] = "all"                  # A10 要求并列口径
def win_rate(ctx) -> MetricValue:                            # 纯函数：无 I/O、无 status
    closed = [r for r in ctx.rounds if r.closed]; wins = sum(1 for r in closed if r.pnl > 0)
    hold = ctx.positions_end.unrealized if ctx.params.basis == "all" else []
    num, den = wins + sum(1 for h in hold if h.pnl > 0), len(closed) + len(hold)
    return MetricValue(value=(num / den) if den else None, n=den, decomposition={"wins": num})
DEF = MetricDef(id="perf.win_rate", definition_version="1.2", domain=Domain.PERF,
    scope=ScopeSpec(unit="round", cohort="buy_date"), dependencies=(DatasetDep("rounds"), DatasetDep("positions_end")),
    explain=ExplainSpec("胜率 {value:.1%}（{basis_human}）", "{n} 笔里 {wins} 笔赚钱"),
    drilldown=DrilldownSpec(dataset="rounds", where="period", order_by="pnl desc"),
    sample_policy=SamplePolicy(min_n=10, ci="wilson"),
    status_policy=(StatusRule("upstream=blocked", Status.BLOCKED), StatusRule("recon_suspicious_ratio>0.05", Status.UNRELIABLE)),
    unit=Unit.RATIO, precision=4, core_conclusion=True, mvp="M1")
```
**例 2：卖飞率 z 分数（A16/FR-R5.2）**
```python
# packages/review/src/review/metrics/defs/timing_sold_too_early.py
class SoldTooEarlyParams(BaseModel):
    h: Literal[5, 10, 20] = 5
    z_threshold: float = 1.0                                 # A16，显式入参便于报告标注
def sold_too_early(ctx) -> MetricValue:
    legs, pending = ctx.rounds.sell_legs(h=ctx.params.h)     # 未走完窗口→pending，不进分母
    zs = []
    for l in legs:                                           # 停牌>50%/退市由数据集层剔除并计 pending
        fwd = ctx.bars_hfq.forward_return(l.symbol, l.date, h)               # 复权、PIT
        bench = ctx.benchmark_h.beta_adjusted_return(l.symbol, l.date, h)    # 前 250 日 OLS
        zs.append(((fwd - bench) / (ctx.bars_hfq.vol(l.symbol, l.date, 60) * sqrt(h)), l.sell_amount))
    denom = sum(w for _, w in zs)
    return MetricValue(value=(sum(w for z, w in zs if z > ctx.params.z_threshold) / denom) if denom else None,
                       n=len(zs), n_pending=pending)
DEF = MetricDef(id="timing.sold_too_early", definition_version="1.1", domain=Domain.TIMING,
    scope=ScopeSpec(unit="round", cohort="sell_date"), dependencies=(DatasetDep("rounds"), DatasetDep("bars_hfq"), DatasetDep("benchmark_series")),
    explain=ExplainSpec("你有 {value:.1%} 的卖出属于「卖飞」", "比该票 β 调整基准多涨了 {z_threshold} 个波动单位"),
    drilldown=DrilldownSpec(dataset="sell_legs", where="z > threshold", order_by="z desc"),
    sample_policy=SamplePolicy(min_n=10, ci="bootstrap", pending_rule="forward_bars"),
    status_policy=(StatusRule("pending_ratio>0.3", Status.INSUFFICIENT), StatusRule("capability=hfq_missing", Status.BLOCKED)),
    unit=Unit.RATIO, precision=4)
```
**例 3：五因子归因的"买点"因子（FR-R7.1 / Q0）**
```python
# packages/review/src/review/metrics/defs/attr_factor_chase.py
class ChaseParams(BaseModel):
    h: Literal[5, 10, 20] = 5
def factor_chase(ctx) -> MetricValue:
    buys = ctx.rounds.buy_events()                           # cohort = 买入日（FR-R4）
    rows = []
    for b in buys:
        q = ctx.metric("timing.chase_quantile", symbol=b.symbol, on=b.date)   # DAG 保证先算
        rows.append((float(b.cost), q.value, ctx.bars_hfq.forward_drawdown(b.symbol, b.date, h=h)))
    return MetricValue(value=decompose_chase(rows), unit=Unit.CNY, n=len(rows))  # core/attribution/chase.py
DEF = MetricDef(id="attribution.factor_chase", definition_version="0.9", domain=Domain.ATTRIBUTION,
    scope=ScopeSpec(unit="round", cohort="buy_date"), dependencies=(DatasetDep("rounds"), DatasetDep("bars_hfq"), MetricDep("timing.chase_quantile")),
    explain=ExplainSpec("本月亏损主要来自买点（{value:,.0f} 元，占 {share:.0%}）",
                        "按每笔买入的追涨分位与买入后 {h} 日回撤回归得到的贡献"),
    drilldown=DrilldownSpec(dataset="buy_events", where="period", order_by="contribution asc"),
    sample_policy=SamplePolicy(min_n=10, ci="bootstrap"),
    status_policy=(StatusRule("upstream=blocked", Status.BLOCKED), StatusRule("group_invariant_residual>0.01", Status.UNRELIABLE)),
    unit=Unit.CNY, precision=2, core_conclusion=True, mvp="M1")
# 组：MetricGroup("attribution.five_factor_v1", members=(5 个因子 def,), invariants=(SumToTotal(...),))
```
## 4. 数据源适配器能力协商（NFR-9 + akshare 不稳定）
```python
# packages/review/src/review/domain/ports.py
class MarketDataSource(Protocol):
    source_id: str; capabilities: "SourceCapabilities"
    def health(self) -> "SourceHealth": ...
    def trading_calendar(self, *, start: date, end: date) -> "Calendar": ...
    def daily_bars(self, symbols, *, start: date, end: date, adjust: "Adjust" = ...) -> "BarsFrame": ...
    def index_bars(self, code, *, start: date, end: date, total_return: bool = True) -> "IndexFrame": ...
    def security_master(self, *, as_of: date) -> "SecurityMaster": ...
    def fundamentals(self, symbols, fields, *, as_of: date) -> "PITFrame": ...
    def corporate_actions(self, symbols, *, start: date, end: date) -> "ActionFrame": ...
@dataclass(frozen=True)
class SourceCapabilities:                    # 声明式；contract test 验证"声明是否属实"
    supports_full_return_benchmark: bool     # 全收益指数（H00300）或可合成
    supports_pit_fundamentals: bool          # 带 announce_date 的财报
    supports_hfq: bool                       # 后复权（NFR-2 硬要求，False → 不可用于收益序列）
    supports_bulk_daily: bool                # 一次多票批量日线
    rate_limit: "RateLimit"                  # qps / daily_quota / max_symbols_per_call / burst
    max_lookback_days: int; max_forward_days: int; has_announce_date: bool
```

| 能力缺失 | 受影响指标 | 降级行为（必须显式） | 留痕 |
|---|---|---|---|
| `supports_hfq=False` | 全部收益/回撤/超额 | **该源直接不合格**，Composite 跳过；不得用 qfq 顶替（NFR-2） | `assumptions.adjust` |
| `supports_full_return_benchmark=False` | A23 基准超额 | 价格指数+股息率合成，`benchmark_kind="price+div"`，报告脚注 | `assumptions.benchmark` |
| `supports_pit_fundamentals=False` | FR-R4 选股域 | `status=blocked`、原因 `CAP_PIT_MISSING`，卡片显示「需更多数据」 | `MetricResult.status` |
| `supports_bulk_daily=False` | 全量日线刷新 | 逐票取数 + 限速排队，`JobRunner` 续跑，页面显示进度 | `market_fetch_log.rows` |
| `rate_limit` 紧张 | 全指标 | P0 优先；exploratory 延迟；永久缓存兜底 | job 优先级字段 |
| `has_announce_date=False` | PIT 过滤 | 法定披露截止日保守化（年报次年 4/30，季报按规则） | `assumptions.pit_rule="statutory"` |

**多源 fallback 链**（`review/infra/marketdata/composite.py`，M2）：`config/market.yaml` 顺序即优先级；逐个 `health()`+能力匹配+预算可用；连续 3 次失败（10 分钟内）熔断 30 分钟、半开只放 1 个探测；同一 `(dataset,symbol,range,adjust)` **固定同一 source_id，禁止一条序列跨源拼接**，跨源则该序列上的资产类指标 `unreliable` 并告警（R-C4/NFR-2）；`LocalCacheSource` 永远末端，缓存优先、无网可算已缓存区间。**来源留痕**：每次拉取写 `market_fetch_log(source_id, source_version, request_digest, fetched_at, rows, checksum, adjust, factor_version, validation_result)`，并折叠进 `MetricResult.assumptions.provenance`（G2 可追溯）。
## 5. 账单格式解析器插件（EP-3）
```python
# packages/ledger/src/ledger/domain/ports.py
class BillParser(Protocol):
    parser_id: str; version: str; formats: frozenset[str]     # {"xls","xlsx","csv"}
    def probe(self, sample: "FileSample") -> "Confidence": ...          # 0..1 + 理由，纯函数
    def describe(self) -> "ParserSpec": ...
    def build_mapping(self, header) -> "ColumnMapping": ...             # 自动映射，用户可改
    def parse(self, raw: "RawFile", m: "ColumnMapping") -> "ParseOutcome": ...  # 不写库！
@dataclass(frozen=True)
class ParserSpec:
    broker: str; signature: "SignatureRule"   # 必需列别名 + 前置标题行范围 + 账号/日期正则
    date_formats: tuple[str, ...]             # 20230103 / 2023-01-03 / 2023/1/3 / excel_serial
    header_preamble_rows: tuple[int, int]     # 华泰 2~5 行
    required_columns: frozenset[str]; optional_columns: frozenset[str]
    amount_sign: Literal["signed", "abs_by_summary"]   # 发生金额符号约定
    has_balance_column: bool                  # 缺失 → 对账降级（FR-L3.5），不静默
```

**识别与优先级**：`registry.detect(file)` 用各 parser `probe` 分 × 用户 profile 加成；最高 ≥0.6 且领先第二名 ≥0.2 → 直接采用，否则进向导确认（场景 A）。优先级：用户确认过的 `bill_mapping_profile`（按表头指纹 hash）> 具体券商 parser > 通用"13 列别名"兜底 parser；确认后写 `bill_mapping_profile(header_hash, parser_id, mapping_json, confirmed_at)`，同表头下次自动套用并可改回。`parse` 只返回 `ParseOutcome{rows, quarantined[], warnings[]}`，未知摘要进隔离区**带行号**（FR-L5），绝不静默丢；去重（附录 B/A27）、批次、对账、软删都在 `ledger/app/import_*.py`，parser 只做"字节 → 13 字段 DTO"。

| # | 新增一家券商（8 步） | 产出 |
|---|---|---|
| 1 | 取 3 份脱敏真实账单（正常/含分红送转/含断链） | `packages/ledger/tests/fixtures/bill/<broker>/` |
| 2 | 填 `ParserSpec`（别名、前置行、日期格式、符号约定、有无余额列） | `parsers/<broker>.py` |
| 3 | 实现 `probe`（正样本高分、负样本必须低分） | 同上 |
| 4 | 实现 `parse`：13 字段归一 + 保留行号 + 异常行进 quarantined | 同上 |
| 5 | 新摘要类型（若有）→ **只加** `config/ledger/summary_rules.yaml` 规则 | 配置，零代码 |
| 6 | 注册：文件放入 `parsers/` 即被发现（**不改任何既有文件**） | — |
| 7 | `tests/contract/test_bill_parser.py` 自动参数化（probe 区分度/空余额降级/未知摘要隔离/行号） | 测试 |
| 8 | 验收：重复上传 0 新增、断链 100% 检出、撤销后 diff=0、映射被记住 | 手工+golden |
## 6. 业务规则的可配置化边界

**判定三问**：改这句话会不会改变**数字的定义**？→ 代码。是否**用户自助修改**且改错也不会算错钱？→ 配置。是否需要**审计/回溯/按区间重跑**？→ 数据库。

| 内容 | 归属 | 载体与路径 | 变更流程 | 进 `inputs_hash` |
|---|---|---|---|---|
| 口径公式（A1~A27：TWR/MWR/DE/BHAR/费用占比）与 FIFO 配对算法 | **代码** | `core/nav.py`、`core/stats/`、`core/attribution/`、`core/fifo.py` | 改代码 + bump `definition_version` + 迁移说明 | 是（经版本） |
| 年化 `A=244`、容差 `ε_amt/ε_r/ε_cum`、门槛 10/30/60 | **代码** | `core/metrics/policy.py`（唯一定义点） | 改代码 + 全量重算 | 是（`engine_version`） |
| 摘要分类规则（打新/送转/红利税…） | **配置** | `config/ledger/summary_rules.yaml` → `ledger/config/summary_rules.py` | 设置页表格编辑；可按区间重跑（FR-L5） | 是（`rule_pack_version`） |
| 行为规则卡阈值与文案 | **配置** | `config/review/behavior_rules.yaml` | 用户可编辑/关闭（FR-D6） | 是（`rule_pack_version`） |
| 列名别名、日期格式、券商默认映射 | **配置**+DB 覆盖 | `config/ledger/parsers.yaml` + `bill_mapping_profile` | 向导确认即写 DB profile | 是 |
| 报告区块顺序与开关 | **配置** | `config/review/report_blocks.yaml` | 改顺序/关块无需发版 | 否（仅渲染） |
| 基准候选与默认基准 | **配置** | `config/review/benchmarks.yaml`（默认 `H00300` 不可为空，R-C2） | 加候选不改代码 | 是（`benchmark_id` 入 params） |
| `rf` 参数（禁止默认 0，A20） | **配置**+DB 快照 | `config/review/risk_free.yaml`，按 `as_of` 取 | 入库生效期 | 是 |
| 流水、指标结果、导入批次、配置发布记录 | **数据库** | `ledger_*`/`review_*`/`config_release` | 走 API/迁移 | 结果已由 hash 表达 |

**配置校验与版本化**：每个配置文件对应 `platform/config/schemas/<name>.py` 的 Pydantic 模型（`extra="forbid"`、字段带 `description`），启动时全量校验，**失败即拒绝启动并输出字段路径**；文件头带 `schema_version`+`config_version`，`platform/config/migrations/v{n}_to_v{n+1}.py` 提供迁移；每次生效写 `config_release(config_name, config_version, content_hash, effective_from, actor, diff_json)`，支持一键回滚。**配置不承载可执行表达式**（只有枚举/阈值/字符串/列名/正则白名单），这是 §10「通用 DSL 表达式引擎」的落地形态。
## 7. 分层与依赖治理

**端口归属规则**：R1 谁消费谁定义（端口由消费方定义，避免实现方绑架接口）；R2 跨上下文只经 `contracts`（DTO）或 `platform` 事件，端口留在各自 `domain`、**禁止 import 对方 infra/领域实体**；R3 单上下文 → `packages/<pkg>/src/<pkg>/domain/ports.py`；R4 实现永远在提供方 `infra/`，`domain` 不得 import `infra`；R5 组装只在根，只有 `apps/server` 可同时 import 多个包的 infra（composition root）。

| 端口 | 定义位置 | 实现位置 | 消费方 |
|---|---|---|---|
| `TradeRepository` | `ledger/domain/ports.py` | `ledger/infra/repo/trade_repo.py` | ledger app（写）；review 经同库只读视图（FR-R1） |
| `RoundRepository` / `NavRepository` / `MetricRepository` | `review/domain/ports.py` | `review/infra/repo/*_repo.py` | review app |
| `MarketDataSource` | `review/domain/ports.py` | `review/infra/marketdata/*.py` | review app metrics |
| `BillParser` | `ledger/domain/ports.py` | `ledger/infra/parsers/*.py` | ledger app import |
| `Clock`/`EventBus`/`JobRunner`/`FileStore`/`BackupTarget` | `platform/ports.py` | `platform/infra/*` | 所有包 |
| `ReportRepository`/`JournalRepository`/`LlmProvider`/`ToolGateway` | `delivery/domain/ports.py` | `delivery/{web,reports,chat}/*` | delivery |
```
                    apps/server  (composition root：唯一组装点)
             ┌──────────┬───────┴───────┬───────────┐
             ▼          ▼               ▼           ▼
        delivery    review          ledger      platform(infra)
             └──────────┴───────┬───────┴───────────┘
                                ▼
                    packages/contracts  (DTO / 枚举 / 跨包端口)
                                ▼
                      packages/core  (纯领域计算，零 I/O)
   横切：platform/ports 被所有包依赖；跨上下文：contracts 只读端口 或 platform EventBus 事件（异步、幂等）
```
```toml
# pyproject.toml（root）
[tool.importlinter]
root_package = "."
[[tool.importlinter.contracts]]              # core 是叶子：不依赖内部包或 I/O 库
type = "forbidden"
source_modules = ["core"]
forbidden_modules = ["ledger","review","delivery","platform","contracts","sqlalchemy","httpx","fastapi","jinja2","akshare"]
[[tool.importlinter.contracts]]              # 跨上下文只经 contracts / platform（另 3 条对称）
type = "forbidden"
source_modules = ["review"]
forbidden_modules = ["ledger"]
[[tool.importlinter.contracts]]              # domain 不依赖 app/infra/api（其他包同理）
type = "layers"
layers = ["review.api", "review.app", "review.domain"]
[[tool.importlinter.contracts]]              # 单一组装根
type = "forbidden"
source_modules = ["review.infra","ledger.infra","delivery"]
forbidden_modules = ["apps.server"]
```

运行参数固定加 `--include-type-checking`：`TYPE_CHECKING` 下的 import 也算依赖，否则循环依赖会在运行期才炸。
## 8. 版本化策略
| 对象 | 版本载体 | 兼容承诺 | 变更规则 |
|---|---|---|---|
| 数据库 schema | Alembic `revision` | 单向升级；升级前自动备份（FR-L7） | 破坏性变更须可逆脚本 + 数据回填测试 |
| 指标口径 | `MetricDef.definition_version` | MINOR 方向不变；MAJOR 分母/方向变化 | MAJOR 必须写 `docs/migrations/METRIC-<id>-<ver>.md`，旧结果标 stale |
| HTTP API | URL 前缀 `/api/v1` | v1 内只增不改；破坏性 → `/api/v2` | 弃用至少保留 1 个 minor + `Deprecation` 响应头 |
| 报告格式 | `report_schema_version` | MINOR 兼容；MAJOR 旧报告只读 | 口径/基准切换不得连线（FR-D5） |
| 导出包 | `bundle_format_version` | 向后可读（新版本读旧包） | 新增字段可选；语义变更升 MAJOR |
| 插件契约 | `extension_api_version`（`contracts/extension.py`） | stable 兼容；experimental 随时改；internal 不承诺 | stable 破坏性需 1 个 minor 的 deprecation 告警 |
| 事件 schema | `schema_version`（Pydantic `Literal`） | 只增可选字段 | 需要新必填 → 新类型 `XxxChanged.v2`，不原地改 |

| 版本变更 | 已有 DB | 前端/模板 | LLM prompt | 备份包 | 测试 fixture | 用户 |
|---|---|---|---|---|---|---|
| schema 迁移 | Alembic 自动升级 | 无感 | 无感 | 旧包可迁移导入 | golden 需重建 | 重启（双击即可） |
| 指标 MAJOR | 旧结果标 stale 并重算 | 报告加口径断点 | 模板槽位不变 | — | 期望值必须更新 | 看到「口径已更新」说明 |
| API 破坏性 | — | 改 `delivery/web/api` | 工具 schema 同步 | — | 契约测试更新 | 无感（本地同进程） |
| 报告模板 | — | 快照测试更新 | — | 旧报告只读 | snapshot 更新 | 新报告新样式 |
| 导出格式 | — | 导出实现更新 | — | 新版本仍可读旧包 | roundtrip 测试 | 迁移到新机器可恢复 |
| 插件契约 stable | — | — | — | — | contract kit 更新 | 无感 |
## 9. 事件驱动的扩展（outbox）
| 环节 | 设计 |
|---|---|
| 写 | 业务事务内**同库同事务**写 `platform_outbox(event_id, type, schema_version, aggregate_type, aggregate_id, payload, occurred_at, published_at, attempts)`（`platform/events/outbox.py`） |
| 投 | `outbox_publisher` 由 `JobRunner` 驱动（`platform/jobs/`），按 `published_at is null` 拉批 → `EventBus.publish` → 标记；失败退避重试；按 `aggregate_id` 分区有序，跨聚合并发 |
| 消 | `outbox_consumer_cursor(consumer_id, last_event_id)`；幂等表 `processed_event(consumer_id, event_id)` 主键去重；处理必须幂等（重放安全） |
| 扩展 | 新增消费者 = 新增一个 handler 注册，**不改 ledger/review**；事件是唯一跨层异步契约 |

| 未来需求 | 消费者（M2/M3） | 是否改核心 |
|---|---|---|
| FR-D6 行为提醒 | `review/behavior/reminder_handler.py`：消费 `MetricsComputed`+`AnomalyDetected`，用 EP-8 规则求值 | 否 |
| FR-D8 推送 / webhook / 备份告警 | `delivery/notify/*`（EP-11）消费 `ReportGenerated`、`delivery/notify/webhook_target.py`、`platform/obs/backup_handler.py` 消费 `BackupCompleted` | 否 |

**事件 schema 演进**：只增可选字段（`Field(default=None)`）；消费者**忽略未知字段**、对缺失可选字段有默认行为；同一 `type` 内不改字段语义；新增必填 → `TradesChanged.v2` 新类型 + 双写过渡。事件 DTO 在 `contracts/events/` 与端口同源发布。
## 10. 反扩展清单（明确拒绝）
| 拒绝项 | 理由（个人单机 + 5 万行 + 非程序员用户） | 若真需要：触发条件与代价 |
|---|---|---|
| 多租户 | 唯一用户 `user_id=100000` 只是分区标签（FR-L2）。多租户污染每个查询、scope、缓存键，还带来鉴权/隔离/越权测试成本，收益为 0 | 上线 C 端才做：中间件 + `user_id` 全链路（表结构已预留），2~3 周且需重写缓存键 |
| 微服务拆分 | 单进程+SQLite 单文件是**产品需求**（FR-L1 双击可用、NFR-10 ≤3s）。拆分引入网络、序列化、分布式事务与运维，直接违反零安装约束 | 仅多用户云端化且 QPS 撑不住；届时先拆 delivery 只读侧，ledger/review 保持同库 |
| 通用 DSL 表达式引擎 | 公式/归因/规则都用 DSL → 非程序员写不对、无法静态校验、无法 diff、`inputs_hash` 不稳定，并形成第二套语言 | 拒绝。确有自定义指标需求时只给**受限公式**：白名单函数+声明式参数，编译期校验，禁循环与 I/O |
| 动态加载第三方 Python 包 | 任意代码执行 = 数据泄露 + 结果不确定；`NFR-5 diff=0` 与 golden 失效；用户也装不上、debug 不了 | 拒绝。生态需求走"内置插件+配置"，新增插件由我们发版 |
| 可插拔数据库驱动 | SQLAlchemy 已覆盖 SQLite/PostgreSQL 双 profile；再抽象一层只会让方言差异泄漏更晚、更难测 | 拒绝。换库时改 `platform/db/` 一处 engine 装配即可 |
| 前端插件体系 | HTMX+SSR 是形态硬约束（PRD §4.3-4）；前端插件必然引入构建链、版本协商与 CSP 问题 | 拒绝。区块扩展一律在服务端（EP-7），前端只做模板片段 |

**配套原则**：同一模式出现第 3 次才抽接口（rule of three）；抽象必须先有 2 个真实实现或 1 个契约测试；为"未来可能性"预留的代码不得进入 M0/M1 验收范围。
## 11. 三个"新增 X"实操作业单
### 11.1 新增一个指标
| # | 动作 | 涉及文件 | 测试 | 验收 |
|---|---|---|---|---|
| 1 | 定 `id`/`domain`/`definition_version=1.0`，查重 | — | `registry` 启动校验 | 无重复 id |
| 2 | 新公式先落纯函数（否则跳过） | `packages/core/src/core/<area>.py` | 纯函数单测 + golden | 手算一致 |
| 3 | 写 `params_schema`（`extra=forbid`，默认值=默认口径） | `review/metrics/defs/<domain>_<name>.py` | 默认参数快照 | 非法参数被拒 |
| 4 | 写 `compute`：只返回 `MetricValue`，零 I/O/随机/`datetime.now()` | 同上 | purity + determinism | 两次 `inputs_hash` 相同 |
| 5 | 声明 `dependencies`（深度 ≤3、level 合法、无环） | 同上 | 启动 DAG 校验 | 无环、可拓扑 |
| 6 | 填 `sample_policy`/`status_policy`（模板化，不写魔数） | 同上 | contract kit 覆盖 n=0/5/9/10/29/30 | N<10 不出结论 |
| 7 | 写 `explain` 两行 + `drilldown` | 同上 | 术语扫描（PRD 附录 C）+ 渲染快照 | 页面无裸术语 |
| 8 | 放文件即注册（不碰 `registry.py`/`__init__.py`） | — | 自动发现测试 | 首页/报告出现该指标 |
| 9 | 加 golden fixture | `packages/review/tests/golden/<id>/expected.json` | 容差 ε 比对 | CI 全绿 |
### 11.2 新增一家券商账单
见 §5 的 8 步表。补充验收口径：① 同文件重复上传新增=0；② 未知摘要 100% 进隔离区且带行号；③ 断链样例检出 100%（≤0.01 元）；④ 撤销批次后行数/指标 diff=0；⑤ 二次导入自动套用记住的映射；⑥ 缺「资金余额」列时明确降级提示（FR-L3.5）。
### 11.3 新增一个报告区块
| # | 动作 | 涉及文件 | 测试 | 验收 |
|---|---|---|---|---|
| 1 | 定 `block_id`、默认 `order`、`requires`（metric_id 集合） | `delivery/reports/blocks/<id>.py` | 快照 | — |
| 2 | 实现 `render(vm, mode)`：**只读 ViewModel，禁止直连 Repository** | 同上 | purity（禁 I/O） | 无 repo import |
| 3 | 实现 `fallback(reason)`：`missing/stale/insufficient/blocked` 四态都有文案 | 同上 | 四态快照 | 不抛异常、不渲染误导数字 |
| 4 | 写 Jinja 片段（`html`）+ 纯文本版（导出/推送复用） | `delivery/templates/reports/blocks/<id>.html.j2` | 渲染快照 | HTML 可打印 |
| 5 | 注册（自动发现）+ 配置排顺序/开关 | `config/review/report_blocks.yaml` | 开关测试 | 关闭后不出现 |
| 6 | 术语与合规扫描：无裸技术词、无标的级买卖建议 | — | PRD 附录 C 扫描 | 0 命中 |
| 7 | 月报一致性：与看板同源同值 | — | `test_report_dashboard_parity` | diff=0 |
## 12. 可扩展性的测试保障（contract test kit）
```
tests/contract/            # 根级测试项目（非发布包，不污染 packages/）
  kit.py                   # assert_* 断言库
  conftest.py              # 参数化：registry.all() / PARSERS / BLOCKS / SOURCES / EXPORTERS
  fixtures.py              # 合成 MetricContext、固定行情快照、断链账单
  golden/<metric_id>/expected.json
```

| 套件 | 关键断言 | 抓什么 bug | 新插件自动覆盖 |
|---|---|---|---|
| 指标 EP-1 | `assert_metric_def_valid`；`assert_purity(compute)`（monkeypatch socket/open/sqlalchemy，禁全局状态）；`assert_deterministic`；`assert_sample_gate`（n=0/5/9/10/29/30/59/60 全枚举）；`assert_no_status_leak`（`MetricValue` 无 status/ci 键）；`assert_drilldown_resolves`（行数 ≤n 且只碰声明数据集）；`assert_explain_renderable`（两行可渲染、无裸术语） | 绕过 FR-R8、非确定性、偷 I/O、下钻越权、文案带术语 | 是（`registry.all()` 参数化） |
| 数据源 EP-2 | `assert_capabilities_truthful`（声明 hfq 则序列必须=原始价×因子；声明 bulk 则单/批一致）；`assert_rate_limit_respected`（fake clock）；`assert_no_account_data`；`assert_provenance` | 能力虚标、限频打爆、隐私出站、来源不可追 | 是（`SOURCES`） |
| 账单 EP-3 | `assert_probe_discrimination`（正样本 ≥0.8、负样本 ≤0.2）；`assert_quarantine`；`assert_purity`（不写库）；`assert_balance_optional`（无余额列降级不失败）；`assert_batch_idempotent` | 误识别、静默丢数、脏写、去重回归 | 是（`PARSERS`） |
| 区块 EP-7 | `assert_block_fallback`（四态）；`assert_block_readonly`（无 repo import）；`assert_block_no_bare_terms`；`assert_toggle_off` | 缺数据白屏、越权取数、术语泄漏 | 是（`BLOCKS`） |
| 导出 EP-10 / 事件 §9 | `assert_roundtrip`（导出→导入 diff=0，FR-L7）；`assert_metadata`（含 `definition_version`/`data_as_of`）；`assert_idempotent_consumer`（同 event_id 重放两次，副作用 1 次）；`assert_outbox_atomic` | 导出丢口径、不可恢复、重复提醒、丢事件 | 是（`EXPORTERS`/消费者注册表） |

**"新增指标不需改核心"的三重证明**：①**自动发现测试** `test_metric_discovery_is_decoupled`——在测试专用 defs 路径放一个 def 模块，断言 registry 能发现且 contract kit 自动跑到它，证明注册不是手工登记；②**改动范围守门** `tools/ci/change_scope.py`——读 `git diff --name-only` 按 §1.2 预算表映射"变更类型→允许路径"，越界即 CI 失败（如新增指标却改了 `engine.py`）；③**架构测试**——import-linter（§7）+ 扫描 `core/**` 与 `*.domain` 的 import，断言无 `sqlalchemy/httpx/fastapi/random/datetime.now`，证明扩展没往核心里塞分支。
**反模式测试**：`test_no_wildcard_plugin_loading`（禁止 `importlib` 加载外部路径）、`test_core_conclusion_budget`（`core_conclusion=True` 指标数 ∈[8,12]，A26）、`test_metric_depth_cap`（依赖深度 ≤3）、`test_config_schema_strict`（配置出现未知键即失败）。
