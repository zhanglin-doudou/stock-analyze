# 可扩展性设计 v2（Extensibility Design）

> 版本 v2.0 ｜ 2026-10-19 ｜ 取代：[v1/Extensibility.md](../v1/Extensibility.md)（v1 单机版，仅作历史基线）
> 上游：[PRD-v2.md](../../prd/PRD-v2.md)（28 条 FR、§4.3 API 优先、§4.5 技术约束、NFR-8/11；附录 A1–A27 口径不变）
> 骨架（固定）：`backend/packages/{contracts,core,ledger,review,delivery,platform}` + `backend/services/{api,worker}`；`frontend/apps/web` + `frontend/packages/api-client`（OpenAPI 生成）；契约快照 `openapi/openapi.json`
> 立场：**扩展点是"加文件"而不是"改框架"**；没有 contract test 的扩展点不算扩展点；API 面新增端点必须显式登记（禁止自动暴露），指标/区块/解析器按约定自动发现。

## 1. 可度量扩展性判据

### 1.1 验收判据 X-1~X-8（每条可在 CI 或一次 PR 判定）

| # | 判据 | 度量方式 | 阈值 | 验证手段 |
|---|---|---|---|---|
| X-1 | 新增一个指标 **零改既有文件**，且 API 自动可查 | PR 的 `git diff --stat`；`GET /api/v1/metrics?ids=<new>` | 新增 ≤2 文件（def+fixture），既有文件改动 **=0**；端点无需改动 | `tools/ci/change_scope.py` + 自动发现测试 + API 验收 |
| X-2 | 新增券商账单 **不动 import 主流程** | 改动 ∩ `ledger/app/import_*.py`、`ledger/domain/dedupe.py` | 交集 **=∅**；preview/commit 两阶段行为不变 | change_scope + parser 契约测试 |
| X-3 | 新增数据源 **只实现 1 个 Protocol** | 实现文件数 | **=1**（+1 配置行），`review/domain`、`review/metrics` 改动 =0 | `backend/tests/contract/test_market_source.py` 参数化 |
| X-4 | 新增报告区块 **零改渲染框架、零改前端** | `delivery/reports/renderer.py`、`frontend/**` 改动 | **=0**；后端渲染 HTML 片段，前端只加载报告 URL | 区块契约 + 快照 + 前端 e2e |
| X-5 | 新增通知通道 **零改核心** | `ledger/**`、`review/domain/**`、`delivery/notify/bus.py` 改动 | **=0**；仅 +1 channel 文件 +1 配置 | outbox 重放测试 + 通道契约 |
| X-6 | API 演进 **破坏性变更 = 0（v1 内）** | `make api-contract-check` 的 OpenAPI diff | 仅允许 additive；破坏性未升 `/api/v2` → 非 0 退出 | oasdiff + `openapi/openapi.json` 快照 diff |
| X-7 | 新增端点/新增表 **租户隔离不回归** | 越权负向用例；DDL 检查 | 新端点 100% 有跨租户 404 用例；新表缺 `user_id`/RLS → CI 失败（首版列名 `user_id`，见 §4） | `backend/tests/api_acceptance/test_tenant_isolation.py` + `tools/ci/check_ddl.py` |
| X-8 | 新增页面 **只加文件 + 1 行注册** | `frontend/apps/web/src/routes/registry.ts` 变更 | 路由文件 1 个；registry **+1 行**；api-client 自动生成 | 路由契约测试 + Playwright 冒烟 |

### 1.2 改动成本预算表（超出预算即架构退化）

| 变更类型 | 允许触碰 | 禁止触碰 | 需同步版本 | 工时 |
|---|---|---|---|---|
| 新增指标 | `backend/packages/review/.../metrics/defs/`（+`core/metrics/` 新公式）+ fixture | `core/*` 既有文件、`review/app/*`、`delivery/*`、`services/*`、`frontend/*` | `definition_version=1.0`（新 id） | 0.5~1d |
| 修改指标口径 | 该 def 文件 + `docs/migrations/METRIC-*` | 其他 def、注册表、渲染层、API DTO | `definition_version` MAJOR/MINOR++（趋势断点） | 0.5d |
| 新增数据源 | `review/infra/marketdata/<src>.py` + `config/market.yaml` | `review/domain`、`app/metrics/*`、API 层 | `extension_api_version` 兼容 | 1~2d |
| 新增券商解析器 | `ledger/infra/parsers/<broker>.py` + fixtures | `ledger/app/import_*.py`、`parsers/registry.py` | `parser.version` | 0.5~1d |
| 摘要分类 / 行为规则 | `config/**/*.yaml` | **任何 Python/TS 文件** | `config_version`/`rule_pack_version` | 0.5h |
| 新增报告区块 | `delivery/reports/blocks/<id>.py` + 模板片段 + 配置顺序 | `reports/renderer.py`、`frontend/**` | `report_schema_version` MINOR++ | 0.5d |
| 新增导出格式 | `delivery/reports/export/<fmt>.py` | 报告渲染核心、API 层 | `export_format_version` | 1d |
| 新增通知通道 | `delivery/notify/<channel>.py` + 配置 | `platform/events/*`、`review/**` | `extension_api_version` | 0.5~1d |
| 新增 API 端点 | `services/api/routes/<router>.py` + `OperationSpec` + 验收用例 | `core/**`、domain 口径 | OpenAPI MINOR++，`operationId` 新增 | 0.5~1d |
| 新增前端页面 | `apps/web/src/routes/<page>.tsx` + registry 1 行 | `packages/{core,review,ledger}` 口径 | 随 API 版本 | 0.5d |

**节奏**：M0~M1 必做 EP-1/2（单源）/3（华泰）/4/5/7/11（in-app）+ 契约套件 + EP-12 骨架；M2 做 EP-6/8/9/10/11（email/webhook）+ fallback 第二源；M3 打磨与压测，**不新增扩展点**。

## 2. 扩展点总表 EP-1~EP-12

| 编号 | 扩展点 | 所在位置 | 注册机制 | 契约 | 版本化 | 稳定性 | 里程碑 |
|---|---|---|---|---|---|---|---|
| **EP-1** | 指标插件 | `backend/packages/review/src/review/metrics/defs/` | 包内 `pkgutil` 自动发现（只扫自有包） | `MetricDef`（13 字段）+`compute(ctx)->MetricValue` | `definition_version` | stable | M1 |
| **EP-2** | 数据源适配器 | `review/infra/marketdata/` | `config/market.yaml` 显式列表 + 工厂字典 | `MarketDataSource` | `capabilities` 协商 | stable | M0 单源/M2 多源 |
| **EP-3** | 账单解析器 | `ledger/infra/parsers/` | 自动发现 + `probe` 打分 + 用户映射 profile | `BillParser`/`ParserSpec` | `parser.version` | stable | M0 华泰 |
| **EP-4** | 摘要分类规则 | `backend/packages/ledger/config/summary_rules.py`（数据 `config/ledger/summary_rules.yaml`） | 配置加载 + 优先级；CRUD API 只改数据 | `SummaryClassifier` | `rule_pack_version` | stable | M0 |
| **EP-5** | 基准与提供方 | `review/metrics/benchmark/` | `config/review/benchmarks.yaml` 候选表 | `BenchmarkProvider` | `benchmark_id`+`benchmark_version` | stable | M1 |
| **EP-6** | 归因模型 | `core/attribution/` + `review/metrics/defs/attr_*.py` | `MetricGroup`，模型=组内 def 集合 | `AttributionModel` | `definition_version`+`group_version` | experimental | M2 |
| **EP-7** | 报告区块 | `delivery/reports/blocks/` | 自动发现 + 配置顺序/开关 | `ReportBlock` | `report_schema_version`+`block_id` | stable | M1 |
| **EP-8** | 行为规则卡 | `review/behavior/rules/`（数据 `config/review/behavior_rules.yaml`） | 配置加载 + `PUT /settings/behavior-rules` | `BehaviorRule.evaluate`（纯函数） | `rule_pack_version` | experimental | M2 |
| **EP-9** | LLM Provider 与工具 | `delivery/chat/` | 工厂字典 + `tools/registry.py`（白名单） | `LlmProvider`/`Tool`/`ToolGateway` | `extension_api_version` | experimental | M2 |
| **EP-10** | 导出格式 | `delivery/reports/export/` | 自动发现 | `Exporter` | `export_format_version` | experimental | M2 |
| **EP-11** | 通知通道 | `delivery/notify/`（`bus.py` 固定，通道可加） | 通道注册表 + 配置启用 | `NotificationTarget` | `extension_api_version` + `template_version` | stable（in-app）/experimental（email/webhook） | M1 in-app / M2 其他 |
| **EP-12** | API DTO 与版本演进 | `backend/packages/contracts/src/contracts/dto/` + `backend/services/api/routes/` | **显式** `OperationSpec` 登记（禁止 glob 自动暴露） | `OperationSpec` + Pydantic DTO + OpenAPI 快照 | `/api/v{N}` + `schema_version` + `operationId` | stable（v1 内 additive） | M0 |

### 2.1 契约签名（每点一条主签名；完整定义见各节）

```python
class MetricDef(Protocol):                    # EP-1（字段见 §3.1）
    def compute(self, ctx: "MetricContext") -> "MetricValue": ...
class MarketDataSource(Protocol):             # EP-2（capabilities：full_return/pit/hfq/bulk/rate_limit）
    def daily_bars(self, symbols, *, start, end, adjust="HFQ") -> "BarsFrame": ...
class BillParser(Protocol):                   # EP-3（probe/describe/build_mapping/parse）
    def parse(self, raw: "RawFile", m: "ColumnMapping") -> "ParseOutcome": ...   # 纯函数，不写库
class SummaryClassifier(Protocol):            # EP-4
    def classify(self, summary: str, ctx: "RowContext") -> "Classification": ...
class BenchmarkProvider(Protocol):            # EP-5
    def series(self, *, start: date, end: date, total_return: bool = True) -> "BenchmarkSeries": ...
class AttributionModel(Protocol):             # EP-6
    def decompose(self, ctx: "AttributionContext") -> "Decomposition": ...      # 纯函数
class ReportBlock(Protocol):                  # EP-7
    def render(self, vm: "ReportViewModel", *, mode: Literal["html", "text"]) -> str: ...
class BehaviorRule(Protocol):                 # EP-8
    def evaluate(self, snap: "BehaviorSnapshot") -> "RuleVerdict | None": ...
class LlmProvider(Protocol):                  # EP-9
    def complete(self, req: "ChatRequest", *, timeout_s: float) -> "ChatResponse": ...
class Tool(Protocol):                         # EP-9 工具白名单
    def invoke(self, args: "BaseModel", deps: "ToolDeps") -> "BaseModel": ...    # 只回后端计算结果
class Exporter(Protocol):                     # EP-10
    def export(self, report: "RenderedReport", target: "ObjectPath") -> "ExportArtifact": ...
class NotificationTarget(Protocol):           # EP-11
    channel_id: str
    def deliver(self, msg: "NotificationMessage", ctx: "TenantContext") -> "DeliveryReceipt": ...
class OperationSpec(Protocol):                # EP-12（每个端点一条，驱动 OpenAPI/SDK/验收）
    operation_id: str; method: str; path: str; auth_scope: "Scope"
    request: type["BaseModel"] | None; response: type["BaseModel"]; errors: tuple[str, ...]
```

注册规则：**指标/解析器/区块/导出/通知走"自有包内自动发现"；API 端点与端口走"显式登记"**（API 面需要审查，不能因 import 了一个模块就多出端点）。禁止扫描 `site-packages`、禁止 `importlib` 加载用户路径、禁止 entry-points（见 §10「插件热加载」）。

## 3. 指标注册表 v2

### 3.1 `MetricDef` 13 字段（与 v1 一致，不新增字段）

| 字段 | 类型 | 约束（v2 增量） | 消费者 |
|---|---|---|---|
| `id` | `str` | `^(selection\|timing\|behavior\|attribution\|perf)\.[a-z0-9_]+$`；全局唯一，永不复用 | 注册表、`/metrics?ids=` |
| `definition_version` | `str` | `MAJOR.MINOR`；MAJOR=分母/方向变化（**趋势强制断点**），MINOR=精度/窗口/文案 | 失效判定、FR-D5 |
| `domain` | `Domain` | 五域枚举；`domain` 别名映射到 API query 参数（`domain=timing`） | 看板分卡、核心结论预算 |
| `scope` | `ScopeSpec` | `unit∈{round,position,day,trade}`、`cohort=buy_date`、`period` 解析；**租户由 TenantContext 注入，不在 params 里** | 引擎、scope_key |
| `params_schema` | `type[BaseModel]` | `extra="forbid"`；`benchmark`/`rf` 等显式入参 | API 校验、`inputs_hash` |
| `dependencies` | `tuple[MetricDep\|DatasetDep]` | 数据集或指标；深度 ≤3、无环；**跨租户依赖禁止** | DAG、失效传播 |
| `compute` | `Callable[[MetricContext], MetricValue]` | 纯函数；只返回 `MetricValue{value,n,decomposition?,n_pending?}`，**不得返回 ci/status/data_as_of**，不触网/不读时钟 | 引擎 |
| `explain` | `ExplainSpec` | FR-D3：`conclusion`/`method` 模板（人话，服务端渲染） | `/metrics/{id}/explain` |
| `drilldown` | `DrilldownSpec` | 第三行：`{endpoint, params}` 声明，供前端直接调用 | 下钻、可执行性断言 |
| `sample_policy` | `SamplePolicy` | `min_n=10`、`ci_min_n=30`、`ci∈{wilson,bootstrap}`、`min_trading_days=60` | 引擎（FR-R8） |
| `status_policy` | `tuple[StatusRule,...]` | 声明式：`upstream=blocked`、`recon_suspicious_ratio>0.05`、`capability=hfq_missing` | 引擎 |
| `unit`/`precision` | `Unit`/`int` | `CNY/ratio/bp/days/count/z/score`；API 传输按附录 B（金额定点字符串、比率小数） | 序列化、前端格式化 |
| 附加必需 | `core_conclusion`/`frequency`/`benchmark_required`/`mvp` | `core_conclusion` 总数 ∈[8,12]（A26）；`frequency` 决定异步/同步 | 行动建议过滤、调度 |

### 3.2 框架代算与多租户 `scope_key`

| 步骤 | 动作 | 代码位置 |
|---|---|---|
| 1 | `TenantContext.require_user()` 取租户；解析 params、period、`as_of` | `services/api/deps/tenant.py`、`review/app/metrics/engine.py` |
| 2 | 按 DAG 取依赖并投影数据集视图（**仓储首参 TenantId，RLS 第二道**） | `review/app/metrics/datasets.py` |
| 3 | 前置对账闸门（分级阻断，A/FR-L6） | `review/app/metrics/gate.py` |
| 4 | 调 `compute(ctx)` → `MetricValue`（第 5~7 步全部框架完成） | 各 def 文件 |
| 5 | 统一算 CI（Wilson/bootstrap，`Random` 由 Clock 派生）+ status/sample_status | `core/stats/ci.py`、`core/metrics/status.py` |
| 6 | 统一收集 `assumptions`（`back_solved`/`benchmark_id`/`adjust=hfq`/`factor_version`/`rf`/`pit_rule`） | `review/app/metrics/assumptions.py` |
| 7 | 统一算 `inputs_hash`、`data_as_of`，落库 `MetricResult`，发 `MetricsComputed` | `review/app/metrics/persist.py` |

`scope_key = sha256(tenant_id, metric_id, params_canonical, period_key, as_of)`（首版 `tenant_id` 恒等于 `user_id`，见 §4.1）—— **租户必须进 key**，否则跨租户缓存污染（v2 最容易被忽略的越权漏洞）。
`inputs_hash = sha256(metric_id, definition_version, engine_version, params, 各依赖 data_version_token, as_of)`；`scope_key ≠ inputs_hash`：前者定位结果槽位，后者判失效。
**FR-R8 三重保障不变**：compute 写不了 status；阈值单点在 `core/metrics/policy.py`（lint 禁魔数 10/30/60）；渲染/API 只认 `sample_status`——`insufficient` 时 `conclusion=null` 且不返回结论字段。

### 3.3 `definition_version` 失效与趋势断点

| 场景 | 判定 | 行为 |
|---|---|---|
| 口径 MAJOR | `cached.definition_version ≠ def.definition_version` | 该租户对应 `period` 全量标 `stale` → 重算；`GET /metrics` 返回旧值 + `status=stale` 并投递 job；报告拒绝出正式版 |
| 口径 MINOR | 同上（同样失效） | 重算但不产生趋势断点（`breaks` 仅 MAJOR/benchmark 切换） |
| 依赖数据变更 | `data_version_token` 不匹配（trades/bars_hfq/benchmark） | 仅失效受影响指标（反依赖 BFS） |
| `benchmark_id` 切换 | params 变化 → `params_hash` 变化 | 强制新槽位；趋势 `breaks[]` 标注，`segment` 不跨段连线（FR-D5） |
| 引擎语义变更 | `engine_version` 变化 | 全量失效（发布时一次性） |
| 退租/注销 | 物理删除该租户数据 | `scope_key` 前缀 = 租户（首版 user_id），级联删除；缓存键同步清理 |

```python
# backend/packages/review/src/review/app/metrics/cache.py
STALE_VERSION, STALE_DATA, STALE_PARAMS, STALE_ENGINE = "version", "data", "params", "engine"

def staleness(c: "CachedResult | None", d: MetricDef, p: BaseModel,
              tokens: Mapping[str, str]) -> str | None:
    if c is None: return STALE_DATA
    if c.definition_version != d.definition_version: return STALE_VERSION
    if c.engine_version != ENGINE_VERSION: return STALE_ENGINE
    if c.params_hash != hash_params(p): return STALE_PARAMS
    if any(c.tokens.get(k) != tokens[k] for k in c.tokens): return STALE_DATA
    return None

def invalidate(tenant: TenantId, token: str) -> frozenset[str]:   # 事件驱动，按租户隔离
    dirty, todo = set(), list(REVERSE_DEPS.get(token, ()))
    while todo:
        mid = todo.pop()
        if mid in dirty or token not in ALL_DEPS[mid]: continue
        dirty.add(mid); todo.extend(REVERSE_DEPS.get(mid, ()))
    cache.mark_stale(tenant, dirty)                    # 只标脏，不阻塞写
    return frozenset(dirty)
```

**规模阈值与降级**（v2 新增，回答"多租户下还要不要细粒度失效"）：单租户 5 万行全量重算 ≤60s（NFR-10），因此——当 `受影响指标数 × 平均单指标耗时 > 单租户全量重算耗时` **或** 活跃租户 < 50 时，允许退化为"该租户该域全量重算"；只有跨租户批量重算（管理员作业/口径 MAJOR 发布）才必须走 token 传播，避免 100 租户 × 全表扫描的放大效应。

### 3.4 依赖 DAG 与环检测

| 机制 | 规则 | 失败表现 |
|---|---|---|
| 构建 | 启动时 `registry.build_graph()`：Kahn 拓扑，level=`selection1/timing2/behavior2/perf3/attribution4` | 有环 → 进程启动失败并打印环路径 |
| 约束 | 只允许依赖同级或更低 level；跨级需 `cross_level=True` 并写理由；深度 ≤3 | 违规 → 启动失败 |
| 执行 | 同 level 可并行；单请求只算依赖子树；Worker 侧按 `(tenant, metric_group)` 分片 | — |
| 组不变量 | `MetricGroup("attribution.five_factor_v1", invariants=SumToTotal(ε_amt, residual_channel="未归属"))` | 不变量失败 → 组内 `status=unreliable`，`/attribution` 返回 `residual` |

### 3.5 三个填法示例（口径沿用 A10/A16/A25，仅示字段）

**例 1：胜率（已平仓 + 全样本并列，A10）**
```python
# backend/packages/review/src/review/metrics/defs/perf_win_rate.py
class WinRateParams(BaseModel):
    basis: Literal["closed", "all"] = "all"                  # A10 并列口径
def win_rate(ctx) -> MetricValue:                            # 纯函数；租户已由 ctx 注入
    closed = [r for r in ctx.rounds if r.closed]; wins = sum(1 for r in closed if r.pnl > 0)
    hold = ctx.positions_end.unrealized if ctx.params.basis == "all" else []
    num, den = wins + sum(1 for h in hold if h.pnl > 0), len(closed) + len(hold)
    return MetricValue(value=(num / den) if den else None, n=den)
DEF = MetricDef(id="perf.win_rate", definition_version="1.2", domain=Domain.PERF,
    scope=ScopeSpec(unit="round", cohort="buy_date"),
    dependencies=(DatasetDep("rounds"), DatasetDep("positions_end")),
    explain=ExplainSpec("胜率 {value:.1%}（{basis_human}）", "{n} 笔里 {wins} 笔赚钱"),
    drilldown=DrilldownSpec(endpoint="/api/v1/rounds", params={"period": "{period}"}),
    sample_policy=SamplePolicy(min_n=10, ci="wilson"),       # ci_min_n=30 取全局默认
    status_policy=(StatusRule("upstream=blocked", Status.BLOCKED),
                   StatusRule("recon_suspicious_ratio>0.05", Status.UNRELIABLE)),
    unit=Unit.RATIO, precision=4, core_conclusion=True, mvp="M1")
# 组：MetricGroup("attribution.five_factor_v1", members=(5 个因子 def,), invariants=(...))
```
**例 2：卖飞率 z 分数（A16）**
```python
# backend/packages/review/src/review/metrics/defs/timing_sold_too_early.py
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
    scope=ScopeSpec(unit="round", cohort="sell_date"),
    dependencies=(DatasetDep("rounds"), DatasetDep("bars_hfq"), DatasetDep("benchmark_series")),
    explain=ExplainSpec("你有 {value:.1%} 的卖出属于「卖飞」", "比该票 β 调整基准多涨了 {z_threshold} 个波动单位"),
    drilldown=DrilldownSpec(endpoint="/api/v1/rounds", params={"error_type": "sold_too_early"}),
    sample_policy=SamplePolicy(min_n=10, ci="bootstrap", pending_rule="forward_bars"),
    status_policy=(StatusRule("pending_ratio>0.3", Status.INSUFFICIENT), StatusRule("capability=hfq_missing", Status.BLOCKED)),
    unit=Unit.RATIO, precision=4)
```
**例 3：五因子归因的"买点"因子（FR-R7.1 / Q0）**
```python
# backend/packages/review/src/review/metrics/defs/attr_factor_chase.py
class ChaseParams(BaseModel):
    h: Literal[5, 10, 20] = 5
def factor_chase(ctx) -> MetricValue:
    buys = ctx.rounds.buy_events()                           # cohort = 买入日（FR-R4）
    rows = [(b.trade_id, float(b.cost), ctx.metric("timing.chase_quantile", b).value,
             ctx.bars_hfq.forward_drawdown(b.symbol, b.date, h=ctx.params.h)) for b in buys]
    return MetricValue(value=decompose_chase(rows), unit=Unit.CNY, n=len(rows))
DEF = MetricDef(id="attribution.factor_chase", definition_version="0.9", domain=Domain.ATTRIBUTION,
    scope=ScopeSpec(unit="round", cohort="buy_date"),
    dependencies=(DatasetDep("rounds"), DatasetDep("bars_hfq"), MetricDep("timing.chase_quantile")),
    explain=ExplainSpec("本月亏损主要来自买点（{value:,.0f} 元，占 {share:.0%}）",
                        "按每笔买入的追涨分位与买入后 {h} 日回撤回归得到的贡献"),
    drilldown=DrilldownSpec(endpoint="/api/v1/trades", params={"trade_ids": "{top_contributors}"}),
    sample_policy=SamplePolicy(min_n=10, ci="bootstrap"),
    status_policy=(StatusRule("upstream=blocked", Status.BLOCKED), StatusRule("group_invariant_residual>0.01", Status.UNRELIABLE)),
    unit=Unit.CNY, precision=2, core_conclusion=True, mvp="M1")
```

## 4. 多租户演进路径（新增重点）

### 4.1 `TenantContext` 抽象（唯一入口）

```python
# backend/packages/platform/src/platform/domain/ports.py
class TenantContext(Protocol):
    def require_user(self) -> "UserId": ...                  # 无 token → 401
    def require_role(self, *roles: Role) -> None: ...        # 不足 → 403
    def scope(self) -> "TenantScope": ...                    # (tenant_id, user_id, actor, origin)
    def system(self, tenant: "TenantId") -> "TenantScope": ...  # worker 显式指定，禁止隐式全局
    def set_db_session(self, session: "Session") -> None: ... # SET LOCAL app.user_id（迁移后同名切 app.tenant_id）
```
铁律：**仓储方法首参必须是 `TenantScope`**（`TenantScope.tenant_id` 首版恒等于 `user_id`，端口实现内部落到 `user_id` 列）；`system()` 仅 `services/worker` 可注入；`admin` 也不得借此读交易明细（FR-L2）。**列名是 user_id，抽象名是 tenant_id**——这是未来"团队租户"唯一需要的前置条件。

### 4.2 `user_id → tenant_id` 迁移（5 步，可零停机）

| 步 | 动作 | 可回滚点 | 观测 |
|---|---|---|---|
| 1 | 加列 `tenant_id UUID NULL` + 索引 `(tenant_id, ...)` + `CHECK (tenant_id IS NOT NULL) NOT VALID` | 删列即回滚 | 无 |
| 2 | 分批回填 `tenant_id = user_id`（`WHERE tenant_id IS NULL LIMIT 5000` 循环） | 停止回填即回滚 | 剩余行数 → 0 |
| 3 | **双写**：新写入同时写 `user_id`/`tenant_id`；`VALIDATE CONSTRAINT` | 回退写入代码 | 双列不一致 = 0 |
| 4 | **双读**：读路径按 `tenant_id`，影子比对 `user_id` 结果；RLS 策略切换为 `app.tenant_id` | 切回旧策略（一条 DDL） | 比对差异 = 0 持续 7 天 |
| 5 | 切读完成 → 删除 `user_id` 列/索引与旧 RLS 策略；`openapi` 不变（**外部不暴露 tenant 语义**） | 需备份恢复（最后一步） | 越权负向用例全绿 |

### 4.3 RLS 与角色模型演进

| 对象 | 首版策略 | 演进能力（必须保留，不实现） | 禁止 |
|---|---|---|---|
| RLS | `USING (user_id = current_setting('app.user_id')::bigint)`，策略名固定 `tenant_isolation`，`FORCE ROW LEVEL SECURITY`，应用连接非 owner 非 `BYPASSRLS` | 迁移 §4.2 第 4 步仅改策略表达式（`tenant_id = current_setting('app.tenant_id')`）；未来团队租户加 `OR tenant_id = ANY(...)`，表结构不变 | 业务代码拼 SQL 过滤当唯一防线 |
| 角色 | `role ∈ {user, admin}` 枚举 + 应用层 permission 检查；`/api/v1/admin/*` 独立前缀 | 未来 `tenant_admin` 只是枚举加值 + permission 表，**不改 DTO 结构** | 用角色当租户隔离手段 |
| 会话变量 | 每个请求/任务 `SET LOCAL app.user_id`；连接池归还前重置 | 迁移后同一行改 `app.tenant_id`；团队场景加额外变量 | 在连接级 `SET`（会串租户） |

### 4.4 影响面矩阵（改租户模型要动哪些）

| 层 | 影响 | 首版做法 |
|---|---|---|
| 表 | 首版所有业务表 `user_id` + 复合索引 + RLS；唯一约束以 `user_id` 前缀 | M0 建好；**`tenant_id` 列不预建**，按 §4.2 五步在需要团队租户时引入 |
| 端口 | 全部 Repository 首参 `TenantScope`；`JobQueue` 按 tenant 分区；缓存键含 tenant | M0 |
| 端点 | **URL 不含 tenant**（`/me` 语义）；跨租户一律 404；批量接口剔除他人 ID 不回显 | M0 |
| 前端 | 无租户概念：只持 token，所有请求同源 | M0 |
| 队列 | `platform.job(user_id, ...)` + 同租户串行（advisory lock）；迁移后列改名 | M1 |
| 审计 | `actor = user_id` + 租户边界；迁移时补 `tenant_id` 供团队审计 | M1 |
| 观测 | 日志/指标按租户（首版 user_id）低基数化（禁止高基数 tag） | M1 |

**只预留不实现**：独立 `tenant` 表（首版租户即 `user` 行，不建组织/成员表）、团队邀请、席位计费、跨租户聚合报表、SSO。

## 5. API 演进与兼容治理（新增重点）

| 承诺 | 内容 |
|---|---|
| 版本语义 | URL 主版本 `/api/v1`；`v1` 内**只允许 additive**；破坏性 → `/api/v2`，服务端同时提供 v1 与 v2 **≥1 个大版本且 ≥180 天** |
| OpenAPI | 后端生成 → 快照 `openapi/openapi.json` 提交；CI 用 `oasdiff` 判定 breaking；`/api/v1/openapi.json` 与 `/version` 的 `info.version` 一致（FR-A2） |
| operationId | **永久稳定**：重命名视为破坏性；确需改则保留旧 `operationId` 至 sunset，SDK 生成按 `operationId` 命名方法 |
| SDK | `frontend/packages/api-client` 100% 生成，禁止手写请求类型；SDK 与 API 同仓 lockstep 版本；前端不跨大版本调用 |
| 错误模型 | `code/user_message/what_to_do` 三字段固定；错误码只增不删、语义不改；新增错误码属 additive |
| 幂等 | 写接口支持 `Idempotency-Key`；语义变更（如超时后重放行为）视为破坏性 |

**破坏性变更判定（v1 内一律禁止，必须 `/api/v2`）**

| 变更 | 判定依据 | 检测方式 |
|---|---|---|
| 删除/重命名字段、端点、枚举值 | 旧客户端反序列化失败或语义丢失 | oasdiff `breaking` |
| 类型变化（定点字符串→number/float；enum→union） | 精度丢失或断言失败 | oasdiff `breaking` |
| 请求字段 `optional → required`（含新增必填） | 旧客户端请求 422 | oasdiff + 请求契约测试 |
| 默认值变更（分页大小、基准、排序、时间窗） | 同一请求结果变化 | 契约快照 + golden |
| `operationId` 变更 | 生成式 SDK 方法名变化 | `assert_operation_id_stable` |
| 鉴权 scope 收紧 / 匿名端点转鉴权 | 旧调用方 401/403 | scope 快照 diff |
| 错误码语义变化或复用旧码表新含义 | 客户端分支逻辑错乱 | 错误码表 diff |
| 分页游标格式、`ETag` 语义变更 | 翻页丢行/缓存击穿 | 分页一致性用例 |
| 越权返回码 404→403（或反向） | 泄露存在性 / 破坏客户端处理 | 跨租户负向用例快照 |

| Deprecation 阶段 | 动作 | 门禁 |
|---|---|---|
| T0 公告 | OpenAPI 标 `deprecated: true`；变更日志 + 站内通知；给出 `Sunset` 日期（≥180 天） | 公告缺失 → 发布检查失败 |
| 全期响应 | `Deprecation`/`Sunset` 头（RFC 8594）；文档标灰 | 头缺失 → 契约测试失败 |
| 全期观测 | 按 `operationId` 统计调用方（Token/UA） | 日活调用量报表 |
| T-30d | 调用量 >0 → 阻断发布并追调用方迁移 | CI 门禁 |
| T0 下线 | 返回 `410 Gone`（不静默 404），保留 1 个 minor 的说明页 | 下线后验收套件更新 |

**DTO 演进规则**：只加**可选**字段；服务端不得向旧客户端发送"新必填"；枚举未知值 → SDK 生成 `UNKNOWN` 兜底且前端可渲染；避免 `oneOf` 多态（改用 `type` 判别字段）；金额一律定点字符串（附录 B），禁用 float；ID 不透明、不得依赖格式；分页统一 `{items, next_cursor}`。

**expand-contract 与部署顺序**（与迁移同一套纪律）：DB expand（加列/加表/加索引，向后兼容）→ 后端发布（新代码双写、读旧列）→ 前端/SDK 发布（消费新字段）→ 后端切读新列 → contract（删旧列/旧端点/旧错误码）。**回滚顺序相反**；每个阶段都必须"旧代码 + 新 schema"可运行。前端先于后端删除是不可接受的（NFR-11 零停机）。

## 6. 前端可扩展性（薄客户端）

| 机制 | 设计 |
|---|---|
| 契约驱动 | 唯一数据入口是 `frontend/packages/api-client`（生成式），页面禁止 `fetch`/手写 DTO（ESLint + dependency-cruiser 断言） |
| 页面/路由注册 | `apps/web/src/routes/registry.ts`：`{path, element, flag, operations: [operationId]}`；新增页面 = 1 个 page 文件 + registry 1 行；启动时校验 manifest 里的 `operationId` 均存在于 SDK（缺则构建失败） |
| 服务端 feature flag | `GET /api/v1/settings/features` 返回本租户开关；前端只做**展示裁剪**，权限/计费/口径一律后端判定（前端 flag 不是安全边界） |
| 状态与缓存 | TanStack Query，queryKey 由 `[operationId, params]` 生成；不做业务缓存合并（口径在后端） |
| 明确禁止 | 微前端、自研组件库（用 headless 小组件）、SSR/同构、前端口径计算（金额/比率只做格式化） |

**口径隔离的硬手段**：金额/比率在 SDK 中是 **branded type**（`MoneyString`/`RatioString`），TS 层不允许 `+ - * /` 运算，只能走 `formatMoney()/formatRatio()`；任何"前端算出 0.4321"的写法编译失败——这比 code review 可靠。

| 前后端版本组合 | 支持 | 保障 |
|---|---|---|
| Web(N) + API(N) | ✅ | 同仓 lockstep |
| Web(N-1) + API(N) | ✅（N-1 大版本内） | API additive；e2e 用旧 SDK 快照跑冒烟 |
| Web(N) + API(N-1) | ✅ | 前端不调用未发布字段；flag 降级 |
| Web(N-2) + API(N) | ❌ | 部署顺序保证前端跟随；`X-Client-Version` 埋点观测 |

## 7. 代码 / 配置 / 数据库三分

| 内容 | 归属 | 载体 | 变更流程 | 进 `inputs_hash` |
|---|---|---|---|---|
| 口径公式（A1~A27）、FIFO、年化/容差常量 | **代码** | `backend/packages/core/**` | 改代码 + bump `definition_version` + 迁移说明 | 是（经版本） |
| 摘要分类规则、行为规则阈值、列映射 | **配置** | `config/**/*.yaml`（schema 见 `platform/config/schemas/`） | 设置页/CRUD API 改数据；可按区间重跑 | 是（`rule_pack_version`） |
| 报告区块顺序/开关、基准候选、rf、feature flag | **配置** | `config/review/*.yaml`、`platform.config_release` | 发布即生效，可回滚 | 部分（benchmark/rf） |
| 租户数据、指标结果、审计、任务、Token | **数据库** | `ledger.*`/`review.*`/`platform.*`（均含租户列，首版 `user_id`） | 迁移/API | 结果由 hash 表达 |
| 前端文案模板、路由 manifest | **前端代码**（非配置） | `apps/web/src/**` | 随前端发布 | 否（展示层） |

**配置版本化**：每份配置有 `schema_version` + `config_version`；加载即校验（`extra="forbid"`），失败拒绝启动并报字段路径；生效写 `platform.config_release(config_name, version, content_hash, effective_from, actor, diff)`；回滚 = 恢复上一哈希。配置里**不承载可执行表达式**（只有枚举/阈值/字符串/列名/正则白名单）。

## 8. 依赖治理

| 规则 | 内容 |
|---|---|
| 端口归属 | 端口由消费方定义、实现放 `infra`；跨上下文只经 `contracts` DTO 或 `platform` 事件；`services/{api,worker}` 是唯一组装根 |
| 包依赖 | `services/api`、`services/worker` 可 import 所有 packages；packages 不得 import services；`core` 是叶子 |
| 前端依赖 | `apps/web` 只能 import `packages/api-client` 与本地 `features/*`；features 之间不互相 import |
| 契约单源 | 仅 `contracts` 定义 DTO；`api-client` 由 OpenAPI 生成，不得手改（CI 校验生成物 diff=0） |

| 端口 | 定义位置 | 实现 | 消费方 |
|---|---|---|---|
| `TradeRepository` | `ledger/domain/ports.py` | `ledger/infra/repo/` | ledger app、worker |
| `RoundRepository`/`NavRepository`/`MetricRepository` | `review/domain/ports.py` | `review/infra/repo/` | review app、worker |
| `ReportRepository`/`JournalRepository` | `delivery/domain/ports.py` | `delivery/infra/repo/` | delivery、api |
| `UserRepository`/`TokenRepository`/`AuditRepository` | `platform/domain/ports.py` | `platform/infra/repo/` | platform、api |
| `TenantContext`/`PasswordHasher`/`RateLimiter` | `platform/domain/ports.py` | `platform/infra/security/`、`platform/infra/limiter/` | 所有请求路径 |
| `MarketDataSource` | `review/domain/ports.py` | `review/infra/marketdata/` | review app metrics |
| `BillParser` | `ledger/domain/ports.py` | `ledger/infra/parsers/` | ledger app import |
| `LlmProvider`/`ToolGateway` | `delivery/domain/ports.py` | `delivery/chat/` | delivery chat |
| `ObjectStore`/`BackupTarget` | `platform/domain/ports.py` | `platform/infra/objstore/` | report、export、backup |
| `JobQueue`/`EventBus` | `platform/domain/ports.py` | `platform/infra/jobs/`（PG `SKIP LOCKED`）、`platform/infra/events/`（outbox） | 所有长任务、api |
| `Clock` | `platform/domain/ports.py` | `platform/infra/clock.py` | 全局（确定性） |

```toml
# backend/pyproject.toml
[tool.importlinter]
root_package = "backend"
[[tool.importlinter.contracts]]              # core 是叶子
type = "forbidden"
source_modules = ["packages.core"]
forbidden_modules = ["packages.ledger","packages.review","packages.delivery","packages.platform","packages.contracts","sqlalchemy","fastapi","httpx","redis","akshare"]
[[tool.importlinter.contracts]]              # 跨上下文只经 contracts / platform 事件
type = "forbidden"
source_modules = ["packages.review"]
forbidden_modules = ["packages.ledger"]
[[tool.importlinter.contracts]]              # domain 不依赖 app/infra/api
type = "layers"
layers = ["review.api", "review.app", "review.domain"]
[[tool.importlinter.contracts]]              # 业务包之间互相独立（不得共享 ORM 实体）
type = "independence"
modules = ["packages.ledger", "packages.review", "packages.delivery"]
[[tool.importlinter.contracts]]              # services 是唯一组装根，packages 不得反向依赖
type = "forbidden"
source_modules = ["packages"]
forbidden_modules = ["services"]
```

**前端架构断言**（CI 两条）：① dependency-cruiser：`apps/web` 不得 import `api-client` 之外的 packages、不得 `fetch`/`axios` 裸调；② AST/ESLint 规则：禁止对 branded `MoneyString`/`RatioString` 做算术、禁止在 `apps/web/src` 出现 `definition_version` 口径分支与指标公式常量。

## 9. 版本化矩阵（7 类）

| 对象 | 兼容承诺 | 变更流程 | 谁需行动 |
|---|---|---|---|
| DB schema | 只做 expand-contract；迁移可回滚 | Alembic 三阶段；`/readyz` 校验 schema_version；升级前快照 | 部署方（执行 migrate）、回滚脚本、API/worker 同版本 |
| 指标口径 | MINOR 兼容展示；MAJOR 方向可能变 | `definition_version`++，写 `docs/migrations/METRIC-*`；MAJOR 触发趋势断点与全量失效 | 前端无需改（字段不变）；报告需重生成；用户看到口径说明 |
| REST API | v1 内 additive；N-1 大版本 ≥180 天 | oasdiff 门禁 + deprecation 头 + sunset | 前端/SDK 重新生成；脚本用户按 `Sunset` 迁移；CI 验收套件更新 |
| 报告 schema | `report_schema_version` MINOR 兼容；MAJOR 旧报告只读 | 区块增删走 MINOR；字段语义变更升 MAJOR | 区块作者、分享链接只读兼容、导出包校验 |
| 导出格式 | `bundle_format_version` 向后可读（新读旧） | 新增可选字段；语义变更升 MAJOR | 迁移工具、离线分析脚本 |
| 事件 schema | 只增可选字段；新增必填 → 新事件类型 `.v2` | Pydantic `Literal` schema_version；消费者忽略未知字段 | worker 消费者幂等改造；outbox 重放测试 |
| OpenAPI | 快照即契约；breaking 必须升 major | `make api-contract-check`；生成物 diff=0；`operationId` 稳定 | 前端重建 SDK、验收套件、文档、第三方集成方 |

## 10. 反扩展清单（明确拒绝）

| 拒绝项 | 理由 | 重新评估触发条件 |
|---|---|---|
| Kubernetes / 多集群 | 首版 100 用户单云主机 + Compose 足够；K8s 引入运维复杂度与成本，收益为 0（NFR-13 用 2 副本 + 滚动升级即可） | 单机容量打满（CPU/连接数持续 >70%）或需要多可用区 SLA 时，先评估托管 PG + 2 台主机 |
| 微服务拆分 | 单仓单进程内已按包分层；拆分引入网络/分布式事务/契约漂移，与"单人维护双栈"直接冲突 | 团队 ≥3 人且某包独立发布节奏成为瓶颈时，只优先拆 `delivery`（无状态、只读） |
| 跨地域多活 | 单 PG 单写；多活需要冲突解决与合规评估，个人产品无此场景 | 付费用户跨大洲且 RTO 要求 <5 分钟时，先做热备再谈多活 |
| 插件热加载（运行时装载第三方代码） | 任意代码执行 = 越权读全租户数据 + 结果不确定；与多租户隔离、`inputs_hash` 可复现、golden 测试直接冲突 | 不评估。生态需求走"内置扩展点 + 配置"，由我们发版 |
| 通用 DSL 表达式引擎 | 口径/规则 DSL 让非专业用户写错、无法静态校验、`definition_version` 不可追踪，并形成第二套语言 | 拒绝。确有自定义指标时只给受限公式：白名单函数 + 声明式参数，编译期校验 |
| 计费/席位/组织管理 | 首版"一用户即一租户"，Non-goals 明确；引入计费会污染租户模型与权限模型 | 商业化被验证后单独立项；届时再加 `plan` 表，不动业务表 |
| 可插拔数据库驱动 | C1 固定 PostgreSQL（RLS、`SKIP LOCKED`、部分索引、JSONB 都依赖它）；抽象层只会把方言差异拖到更晚 | 不评估。C1 变更属产品级重写 |
| 微前端 | 前后端分离已满足"薄前端"；微前端带来构建链、依赖重复、通信约定与部署编排，个人产品无收益 | 团队有多条前端产品线且独立发布时再评估 |
| GraphQL/BFF 网关 | 页面数据需求由《API-Catalog》的少量聚合端点满足；GraphQL 让权限（按租户/字段）与限流治理复杂化，收益不抵成本 | 客户端形态 >3 种（Web/移动/第三方）且读模型差异显著时再评估 |

**配套原则**：同一模式出现第 3 次才抽接口；抽象必须先有 2 个真实实现或 1 个契约测试；为"未来可能性"预留的代码不得进入 M0/M1 验收范围。

## 11. 四个作业单

### 11.1 新增指标
| # | 动作 | 文件 | 测试 | 验收 |
|---|---|---|---|---|
| 1 | 定 `id`/`domain`/`definition_version=1.0`，查重 | — | registry 启动校验 | 无重复 id |
| 2 | 新公式先落纯函数（否则跳过） | `backend/packages/core/src/core/<area>.py` | 纯函数单测 + golden | 手算一致 |
| 3 | 写 `params_schema` + `compute`（只返 `MetricValue`，零 I/O/时钟/随机） | `review/.../metrics/defs/<domain>_<name>.py` | purity + determinism | 两次 `inputs_hash` 同 |
| 4 | 声明 `dependencies`（≤3 深、level 合法、无环） | 同上 | 启动 DAG 校验 | 可拓扑 |
| 5 | 填 `sample_policy`/`status_policy`（模板，不写魔数） | 同上 | contract kit n=0/5/9/10/29/30 | n<10 无 conclusion |
| 6 | `explain` 两行 + `drilldown{endpoint,params}` | 同上 | 术语扫描 + endpoint 可调用 | 下钻非空 |
| 7 | 放文件即注册（不碰 registry/`__init__`） | — | 自动发现 + `GET /metrics?ids=` | API 自动可见 |
| 8 | golden fixture + 多租户隔离用例 | `backend/tests/golden/<id>/` | 容差 ε + 跨租户 404 | CI 全绿 |
| 9 | OpenAPI 是否变？新增 query 枚举值属 additive | — | `make api-contract-check` | 无 breaking |

### 11.2 新增券商账单
| # | 动作 | 文件/产物 | 验收 |
|---|---|---|---|
| 1 | 3 份脱敏账单（正常/含分红送转/含断链） | `ledger/tests/fixtures/bill/<broker>/` | fixture 入库 |
| 2 | `ParserSpec`：别名、前置行、日期格式、符号约定、有无余额列 | `ledger/infra/parsers/<broker>.py` | 字段齐全 |
| 3 | `probe`（正样本高分、负样本低分） | 同上 | ≥0.8 / ≤0.2 |
| 4 | `parse`：13 字段归一 + 保留行号 + 异常进 quarantined | 同上 | 不写库（纯函数） |
| 5 | 新摘要类型 → **只加** `config/ledger/summary_rules.yaml` | 配置 | 零代码 |
| 6 | 注册：文件放入 `parsers/` 即被发现（不改既有文件） | — | 自动发现 |
| 7 | 契约测试参数化（probe/空余额降级/隔离/幂等） | `backend/tests/contract/test_bill_parser.py` | 全绿 |
| 8 | 全链路 API 验收：preview→commit→revoke | `backend/tests/api_acceptance/test_imports.py` | 新增=0、断链 100%、撤销 diff=0 |

### 11.3 新增报告区块
| # | 动作 | 文件 | 测试 | 验收 |
|---|---|---|---|---|
| 1 | 定 `block_id`/`order`/`requires(metric_id)` | `delivery/reports/blocks/<id>.py` | 快照 | — |
| 2 | `render(vm, mode)`：只读 ViewModel，禁 Repository | 同上 | purity（禁 I/O） | 无 repo import |
| 3 | `fallback`：`missing/stale/insufficient/blocked` 四态文案 | 同上 | 四态快照 | 不抛异常、不含结论 |
| 4 | HTML/纯文本模板（报告 HTML 后端渲染，前端零改） | `delivery/templates/reports/blocks/<id>.html.j2` | 渲染快照 | 可打印 |
| 5 | 注册 + 配置顺序/开关（`report_schema_version` MINOR++） | `config/review/report_blocks.yaml` | 开关测试 | 关闭后不出现 |
| 6 | 合规/术语扫描 + 与 `/metrics` 数字一致 | — | 词表 + parity | 0 命中、diff=0 |

### 11.4 新增通知通道
| # | 动作 | 文件 | 测试 | 验收 |
|---|---|---|---|---|
| 1 | 定 `channel_id`、能力（模板/重试/幂等键） | `delivery/notify/<channel>.py` | 快照 | — |
| 2 | 实现 `NotificationTarget.deliver`（含 tenant 上下文、超时） | 同上 | 契约测试 | 不触业务表 |
| 3 | 模板（版本化，`template_version` 入审计） | `delivery/templates/notify/<channel>/` | 渲染快照 | 含退订/降级文案 |
| 4 | 配置启用 + 租户开关 | `config/review/notify.yaml` | 开关测试 | 关闭即不投递 |
| 5 | 消费 outbox（`AlertsTriggered`/`ReportGenerated`），幂等 | `delivery/notify/handlers.py`（既有文件，只加注册） | 重放 2 次副作用 1 次 | 无重复提醒 |
| 6 | 限流与失败降级（不阻塞其他通道） | 同 channel | fake clock 限流测试 | 429 可见、可重试 |
| 7 | API 验收：`/alerts` 出现记录、ack 后可查 | `backend/tests/api_acceptance/test_alerts.py` | 全绿 | 非站内通道仅审计可见 |

## 12. 契约测试套件与"不改核心"证明

```
backend/tests/
  contract/            # 端口/插件契约（参数化 impl）
    kit.py             # assert_* 断言库
    test_metric_plugin.py / test_bill_parser.py / test_market_source.py
    test_report_block.py / test_notifier.py / test_exporter.py
  api_acceptance/      # 100% 端点（FR-A2），含跨租户负向用例
  golden/<metric_id>/  # 固定行情快照 + 期望值（容差 ε）
frontend/tests/
  arch/                # dependency-cruiser + branded-type 算术断言
  e2e/                 # Playwright：登录→看板→下钻→月报
```

| 套件 | 关键断言 | 抓什么 bug | 新插件自动覆盖 |
|---|---|---|---|
| 指标 EP-1 | `assert_metric_def_valid`；`assert_purity(compute)`（禁 socket/open/sqlalchemy/session）；`assert_deterministic`；`assert_sample_gate`（n=0/5/9/10/29/30/59/60）；`assert_no_status_leak`；`assert_drilldown_callable`（endpoint 存在且带租户返回非空）；`assert_tenant_scoped`（两租户同 fixture 结果互不污染、cache 不串） | 绕过 FR-R8、非确定性、偷 I/O、越权下钻、跨租户缓存污染 | 是（参数化 `registry.all()`） |
| 数据源 EP-2 | `assert_capabilities_truthful`；`assert_rate_limit_respected`；`assert_no_account_data`（出站无租户/账号）；`assert_provenance` | 能力虚标、限频打爆、隐私出站 | 是（`SOURCES`） |
| 账单 EP-3 | `assert_probe_discrimination`；`assert_quarantine`；`assert_purity`；`assert_balance_optional`；`assert_batch_idempotent` | 误识别、静默丢数、重复导入 | 是（`PARSERS`） |
| 区块 EP-7 | `assert_block_fallback`（四态）；`assert_block_readonly`；`assert_no_bare_terms`；`assert_toggle_off` | 缺数据白屏、越权取数、术语泄漏 | 是（`BLOCKS`） |
| 通知 EP-11 | `assert_idempotent_delivery`；`assert_channel_isolation`（一通道失败不影响其他）；`assert_tenant_scope`；`assert_template_versioned` | 重复推送、串租户、跨通道雪崩 | 是（`CHANNELS`） |
| API EP-12 | `assert_operation_spec`（示例/错误码/scope 齐全）；`assert_openapi_snapshot`（diff 仅 additive）；`assert_tenant_404`（每端点自动生成越权用例）；`assert_operation_id_stable` | 契约漂移、破坏性变更、越权回归 | 是（遍历 `OperationSpec` 注册表） |

**"新增指标不需改核心"的三重证明**：①**自动发现** `test_metric_discovery_is_decoupled`——在测试专用 defs 路径放一个 def，断言 registry 发现、contract kit 跑到它、`GET /metrics?ids=` 可查；②**改动范围守门** `tools/ci/change_scope.py` 按 §1.2 预算表映射"变更类型→允许路径"，越界即失败（如新增指标却改了 `engine.py`/API DTO）；③**架构测试**——import-linter（§8）+ 扫描 `core/**`、`*.domain` 的 import 断言无 I/O 库与 `datetime.now()`，证明扩展没往核心里塞分支。**反模式测试**：`test_no_wildcard_plugin_loading`（禁 `importlib` 外部路径）、`test_core_conclusion_budget`（∈[8,12]）、`test_metric_depth_cap`（≤3）、`test_config_schema_strict`、`test_frontend_no_metric_arithmetic`。
