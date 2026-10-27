# Period2-05/06 评审 #1 — 契约 / API / 架构一致性

- **裁决：REQUEST_CHANGES**（方向可行；4 个 P0 均在设计/契约层，改后即可放行，不需返工代码）
- 总评：DB 设计、四态 value 出口、常量与范围围栏与 07 冻结契约大体同构；但"下钻无 builder、`core/stats` 双主、stale 语义反转、`MetricContext` 缺 `positions_end`"会在 07 联调首日阻断，且 06 的契约份额/查询参数矩阵不完整。
- 核对计数：8/8 必核对项已逐项核对 → **P0 ×4、P1 ×6、P2 ×6**；"已核实一致"10 条。
- 基准：07 冻结契约 §4/§6.3/§8/§9 C-01~C-34/§13.4；Catalog §0.4/§0.8/§2.3/§4/§6；实读 `errors.py`/`trades.py`/`rounds.py`/`ports.py`/`constants.py`/`enums.py`。只读评审，未改设计/代码。

## 发现

**EC-P0-1｜下钻只有声明、没有 item 生成契约，API-035 无法由 07 实现**
- 问题/证据：05 §7.3（L260-265）只给声明式 `DrilldownSpec` + "decomposition + 回合/流水仓储查询"，无 `drilldown_items`/builder 接口；decomposition 是聚合标量（07:419），不含逐回合/逐日/逐事件明细；且明细源被限死为 `round/round_fill/trade`（05:265），而 chase/beta/drawdown/HHI/BHAR 下钻需 bars/asset_daily/benchmark/holdings（07:317-327）。`DrilldownSpec` 本身只有 endpoint+params（07:192-193），win_rate 下钻却是逐回合行且含 `security_code`（07:425），kit 只查 endpoint 可调（07:769）。
- 建议/影响：base.py 增 `MetricDef.drilldown_items(ctx, period, *, round_id, security_code, limit, cursor) -> (items, next_cursor)`（或 `DrilldownProvider` 注册），明细源扩到 5 视图，item 契约补 `security_code`（05:263 缺）；否则 07 的 18 指标下钻全部无法落地，API-035/P4 硬阻断。

**EC-P0-2｜`core/stats` 双主 + `ci_stat_id` 派发契约缺失（含 Wilson 输入）**
- 问题/证据：05 E-05（L19）交付 `numeric/wilson/bootstrap/estimators`；07 §5.1（L254-264）+ §10 S1（L905）交付"整个 `core/stats`"（含同 4 文件），归属未裁决。`ci_stat_id` 只在 C-19/07:195 提到"指向 estimators 注册表"，05 未定义注册方式、dispatch 签名、id 命名、未知 id 行为；07 新增 stat 必须改 05 的 `estimators.py`，与"零改既有文件"矛盾。Wilson 的 (success,n) 无来源：`MetricValue`（05:165）无成功计数，`value` 8 位舍入不可反推 k。
- 建议/影响：冻结 4 个文件归 05，07 只允许新增 `core/stats/estimators_extra.py` 或 `@estimator("id")` 注册块（05 预置空注册表 + pkgutil 发现），wilson 用 `(sum(ci_sample), len(ci_sample))` 或补 `ci_success/ci_n` 并进 C-19；否则两 PR 改同一文件冲突，07 的 DE/expectancy bootstrap CI 无法接入、P3 验收不可达。

**EC-P0-3｜stale 语义反转、不可达，且缓存指纹不覆盖 build/market 状态**
- 问题/证据：05 把 stale 定义成 read-through 的"短暂内部态"（05:216-217、L234），而 07 §6.3 把 `asset_build.state=stale/missing`（07:343-344）与 `definition_version` 漂移（07:352）定义为对外 `status=stale`（无 value）；05 gate 表却把"资产 build 非 current"一律映射 `blocked`（05:232）。E-02 下 bump 即插新版并重算，GET 永不返回 stale → 07 §8.7 "bump → stale 且无 value"（07:796）与"上游 stale→stale"（07:784）不可达。E-04 指纹=行数+`max(updated_at)`+`max(id)`（05:18），不含 `asset_build.state/last_status/data_as_of`、`benchmark.available`、`market.as_of`：build 转 stale 或 live 基准消失后仍可能命中缓存返回 ok。
- 建议/影响：明确 stale 可达路径（build/market/benchmark 触发 → 返回 stale 无 value、不重算旧版本），读缓存前先过 build/market 门禁，指纹补上述字段，优先级改 `blocked > stale > unreliable > ok` 或逐条列 §6.3；否则状态机与 07 断言互斥，API-079/033 必红。

**EC-P0-4｜`MetricContext` 缺 `positions_end`/`rf_annual`，07 冻结 def 注册即失败**
- 问题/证据：05 `views.py` 只声明 8 视图（L180-181），无 `positions_end`；而 07 冻结 def 骨架（07:183）与 win_rate 口径（07:421）以 `DatasetDep("positions_end")` 为输入，05 registry 对"未知依赖 → CycleError"（05:193），07 写真实 def 后注册即失败。context 未列 `rf_annual`（07:215），β/IR 不可算；05 R-2（L360）"按 §4.1 MetricContext 全集声明"与事实不符。
- 建议/影响：views 增 `positions_end`（或明确"07 注入、registry 不校验未知 DatasetDep"）+ `holdings_daily` 全字段占位，context.py 显式冻结 `user_id/period/as_of/rf_annual`，R-2 改为"本设计显式列出的字段+07 additive"；否则 07 的 #1 指标与 beta/IR 无法注册/计算，05→07 联调首日阻断。

**EC-P1-1｜API-033 `domain` 枚举擅自加 `perf`，无 C 项**
- 问题/证据：05:248 写 `domain=perf|timing|behavior|selection|all`，Catalog §2.3（L312）为 `selection|timing|behavior|all`；C-01（07:861）只改 dotted id/矩阵/ci_min_n，未含 domain 枚举扩充。另 05 的 domain 列允许 `attribution`（05:102）但 query 枚举未列。
- 建议/影响：C-01 增"domain 枚举加 perf（attribution 本期不收）"或由注册表推导白名单，同步 §4 与 SDK；否则契约 PR 漏项、perf 域无法按 domain 过滤。

**EC-P1-2｜`from/to` 在 M1（month scope）无映射，05 写"闭区间"与 C-34 冲突**
- 问题/证据：05:248 把 `period` 与 `from/to` 并列为"闭区间"，但 C-34（07:894）与 Catalog §0.4（L28）规定 `from/to` 半开 `[from,to)`；05 §8 表（L296）又写对了半开——文档内自相矛盾（07:331 也误写"也闭区间"，需 07 一并勘误）。E-07 只支持 `period=month`，`from/to` 跨月/非整月时 scope_key 与响应 `period` 未定义。
- 建议/影响：M1 明确 `from/to` → 422 `VALIDATION_ERROR`（推荐），或只接受同月并归一为 `period`；05:248 改"period 闭区间；from/to 按 §0.4 半开"。否则区间边界不可测。

**EC-P1-3｜`ids`/`benchmark` 参数边界与鉴权用例未定义**
- 问题/证据：E-09（05:23）只覆盖"未知 id 404""ids+domain 422"，未定义重复 id、空 `ids=`、两者都不传（默认全集？）、`benchmark` 缺省/未知值。05 §7.2/§7.3 错误码表未列 `ADMIN_CANNOT_READ_TRADES`（C-08 要求四端点全补），§7.4 又整体丢给 C-29；测试计划（05:314）缺无 token 401、read PAT 200（07:807 要求）。
- 建议/影响：补参数矩阵（空/重复/缺省/未知 benchmark→422 VALIDATION_ERROR，默认 `hs300_tr` 并进 assumptions），四端点错误码补 403、测试补 401/read PAT；否则 Catalog 验收无法逐条落地。

**EC-P1-4｜ETag 指纹不含查询身份；与 Catalog `W/"<version>"` 冲突；304/写库顺序未定义**
- 问题/证据：E-08（05:22）指纹仅 `(metric_id, definition_version, inputs_hash)`，不含 `ids/domain/period/benchmark/rf/include_exploratory`，与 07 §8.7（L798）"查询指纹含 6 项"冲突；空 items（`domain=attribution`、仅 exploratory）会得恒定 ETag。格式 `W/"sha16"` 与 Catalog §0.8（L48）`W/"<version>"` 不一致且无 C 项。管线先 UPSERT（⑥f）再算 ETag（⑧，05:216-218），命中 304 仍已写库，且未定义 304 是否允许副作用。
- 建议/影响：指纹加入全部查询参数（或对 canonical query 取 sha），空集纳入 `period/data_as_of`；C 项改 §0.8 为"ETag 可为内容 sha"或物化行加 `etag_version`；明确 304 判定在写库前（先读缓存算指纹）或声明"304 仍幂等写"。否则缓存命中错误、顶层字段可能过期。

**EC-P1-5｜06 契约份额缺 C-06，且 kit 归属与 07 重叠**
- 问题/证据：05 §8（L279-298）自称"§9 中 05/06 份额"，但漏 C-06（Catalog §6：`nav_return`/flat id/`fx_round30` 与 n==12 矛盾，07:866）——正是 06 的验收矩阵；§9.1（05:313）声称交付 `tests/contract/test_metric_kit.py`，而 07 §8.1 L2a（07:582）把 `contract/kit.py`+`test_metric_plugin.py` 归 07，07 §4.3 P3 又要求 05 的 kit 全绿（循环依赖）。
- 建议/影响：§8 增 C-06（及 P2-4 的 Catalog §4 字段缺口）归 06；kit 明确"07 定义、05 只消费"或 05 先交纯 kit；否则 06 契约 PR 不完整、API-033 验收仍引用不存在指标。

**EC-P1-6｜def 导出名三套、测试 def 与 07 真实 id 冲突（E-10 vs R-6）**
- 问题/证据：05:192 接受 `def_instance`（或 `METRIC_DEF`），Extensibility §3.5（L185）用模块级 `DEF`，07:177 只给 class——三套发现约定。E-10（05:24）把测试 def 命名为 `perf.win_rate`（与 07 真实 def 同 id/version），R-6（05:364）却要求 `stub.` 前缀；测试 def 放生产 `defs/`（05:189）会进 `registry.all()`，破坏 07 的 golden 清单等值断言（07:640）与 core_conclusion 预算（07:764）。
- 建议/影响：冻结唯一导出名（建议 `DEF`），测试 def 移 `tests/fixtures/defs/` 由 `METRIC_DEFS_PATH` 注入、生产 registry 不扫，E-10/R-6 二选一；否则 07 的 18 def 可能全部发现不到、合并期 registry 污染。

**EC-P2-1｜07 侧残留两处需勘误（否则 05 按冻结契约实现仍被判红）**
- 问题/证据：07 §6.3 L351 写 `bar_density<0.8 → unreliable` 且 `value 有`，与 §4.2（07:230）/§8.6（07:777）"非 ok 无 value"冲突；07 §8.6 L768 的 kit 断言 `MetricValue` 字段名为 `value`，而 §4.1（07:195）/05:165 为 `value_units`。
- 建议/影响：07 改 L351 为"无 value"、L768 改 `value_units`，05 在 C-19 注明以 §4.1 为准；否则 05 抄 §6.3 会输出 unreliable 带 value。

**EC-P2-2｜越权用例断言错误**
- 问题/证据：05:321 写"A 用户 token 打 B 用户 `/metrics` → 404"，但接口无 user 路径参数、`metric_id` 是全局注册表键；07:806 明确隔离断言应为"响应不含他人数据 + 无数据时 items=[]"。
- 建议/影响：改为 A/B 同 period 各自期望值 + B 响应 0 命中 A 的 UUID/金额，admin 403、无 token 401。

**EC-P2-3｜`metric_dependency` 缺 `definition_version`，重算会删旧版本血缘**
- 问题/证据：父表唯一键含 version 且 E-02 保留旧行（05:123），但依赖表无 version 列（05:130-139），重算按 `(user_id,metric_id,period)` 先删后插（05:142）会连带删除旧版本血缘。
- 建议/影响：依赖表加 `definition_version`，删除条件带 version；否则 E-02 的版本保留在血缘层失真。

**EC-P2-4｜`MetricResult` 新字段与顶层 `definition_version` 未进 C 项；CHECK 未覆盖 pending_window**
- 问题/证据：05:249-250 在 item 内加 `unit`/`assumptions` 并要求顶层 `definition_version`，Catalog §2.3（L313）/§4 无 `unit`、`assumptions` 只在顶层，C-05 只加 `decomposition`；多版本时顶层 version 取值未定义。05:107 要求 pending_window 时 value NULL，但 05:124 的 CHECK 只写 `status='ok' OR value IS NULL`。
- 建议/影响：C-05 扩为"MetricResult 增 unit/assumptions/decomposition + 顶层 version 规则"，CHECK 加 `sample_status<>'pending_window' OR value_units IS NULL`。

**EC-P2-5｜夹具与测试落点双源**
- 问题/证据：05:320 `tests/fixtures/bars_mini.json` 与 07 §8.3 的 `backend/tests/fixtures/metrics/<id>/`+MANIFEST/`fx_bars_min`（07:686）两套；05:314 `tests/api/test_metrics.py` 与 07 §8.1 L3 的每端点 `test_api_03x_*.py`+`mark.api`（07:584）不一致，`gate_endpoint_coverage` 依赖标记。
- 建议/影响：05 采用 07 的 `fixtures/metrics/` 布局与 `test_api_033_metrics.py` 等命名，仅新增 `make test-engine`。

**EC-P2-6｜引用/事实小错**
- 问题/证据：05:204 称 as_of 公式"三域设计 §0 已定"，实际在 07 §6.2（L334）；05:309 用 §5.5（07 的 regression/tstat）数字验 05 的 stats 子集，超出 E-05 范围；05:192 "13 字段"未枚举，Extensibility §3.1（L98-110）实为 12 行+4 附加，与 07:764 "13 字段齐全"口径需统一。
- 建议/影响：逐条订正并给 13 字段清单（id/version/domain/scope/params_schema/dependencies/compute/explain/drilldown/sample_policy/status_policy/unit/precision）。

## 已核实一致

1. 五个错误码全在 `errors.py`（24/29/52/53/59），映射正确（METRIC_NOT_FOUND 404:154、INVALID_CURSOR 400:258）；05:83 不新增码。
2. `MetricValue` 六字段与 07:195 一致（`value_units/n/decomposition/n_pending/ci_sample/ci_stat_id`；ci_sample 升序/不落库/≤2000，05:165）。
3. 四态 value 出口（insufficient 有值无 CI 无 conclusion；pending_window 无 value；blocked/stale/unreliable 无 value）与 07:230/§8.6 门槛表一致（05:236-240）。
4. 常量 `Z_95=1.959963985`/`MIN_TRADING_DAYS=60`/`RF_ANNUAL_DEFAULT=0.015` 与 C-17/A26/D-14 一致（05:158-160），`MIN_CI_SAMPLE=10` 保持（constants.py:24）。
5. period 归属 `close_date` + 新增 `in_period` 与 C-22/D-18 一致（05:52/290），`page()` 语义不动（rounds.py:148-166）。
6. ETag/304 复用可行：`trades.py:86-93` 已忽略 `W/` 前缀并剥引号，`If-None-Match` 模式可移植（05:22/251）。
7. registry 仅 `pkgutil` 扫自有包（05:192），符合 Extensibility §2"禁 site-packages/entry-points/用户路径"。
8. 分层：`stock_core/stats` → contracts、`stock_review/metrics` → core/platform/contracts（05:84），符合 AGENTS 分层铁律。
9. 契约先行顺序与验收 period：Catalog→snapshot→api-check→router→tests→sdk（05:275/§9.3），冒烟 `period=2026-06` 符合 D-19/C-33（05:331）。
10. 迁移配套：0007 两表 + RLS + `conftest._REVIEW_TABLES`（conftest.py:60）+ `test_rls_isolation.py` + DATABASE.md，与 C-26/07 §8.8 一致（05:147-150）。

## 放行条件

1. M-1 契约 PR 前修完 **EC-P0-1~4**（先落 05 v1.1，并与 07 同步订正 §8.6 L768、§6.3 L351）。
2. **EC-P1-1~6** 进同一契约 PR（domain 枚举、from/to、参数矩阵、ETag/C-06、def 装载）。
3. P2 可与实现并行；EC-P2-1 必须在 07 写 defs/kit 前落到 07 文档，否则 05 按 §4.1 实现会被 07 kit 判红。
