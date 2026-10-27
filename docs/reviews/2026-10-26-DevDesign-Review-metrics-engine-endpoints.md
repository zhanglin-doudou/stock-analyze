# 指标引擎与端点（Period2-05/06）设计 · 交叉评审记录

> 审核对象：`docs/architecture/2026-10-26-metrics-engine-endpoints.md` **v1.0（383 行）**
> 修订结果：**v1.1（590 行）**（同路径）｜日期：2026-10-26｜组织：AI（Team Lead）
> 方式：两名独立评审员（fresh context、只读）+ Lead 自审；评审后由 Lead 直接修改设计文档
> 依据：已批准的 [三域指标设计 v1.0](2026-10-26-metrics-three-domain.md) §4/§6.3/§8/§9/§13.4、`docs/architecture/v2/API-Catalog.md`、`Extensibility.md`、`Architecture-v2.md` §11、`Testing.md` §12、迁移 0006、真实代码（`errors.py`/`router`/`models.py`/`conftest.py`/`Makefile`）

---

## 1. 审核元信息

| 项 | 内容 |
|---|---|
| 评审员 | `reviewer-2605-contract`（契约/API/架构一致性）、`reviewer-2605-engine`（DB/引擎/缓存/测试可执行性） |
| 评审材料 | `docs/reviews/_p2605/review_contract.md`（91 行）、`docs/reviews/_p2605/review_engine.md`（97 行） |
| 核对规模 | 契约 8/8 必核项；引擎 9/9 必核项；合计 17 项核对 + 数值/SQL/命令抽查 |
| 已执行 | 只读 grep/read、`make -n`、迁移/模型/conftest/Makefile 核对；未起服务/未写库/未改代码 |
| 结论 | 两名评审员均 **REQUEST_CHANGES**（可修后放行，无需重写设计）；Lead 裁决：全部 P0 接受并修复，P1 关键项修复，P2 记录/部分修复 |

### 1.1 裁决汇总

| 评审员 | 聚焦 | 裁决 | P0 | P1 | P2 | 关键阻塞项 |
|---|---|---|---|---|---|---|
| reviewer-2605-contract | 契约/API/架构 | REQUEST_CHANGES | 4 | 6 | 6 | 下钻无 item builder；`core/stats` 双主；stale 语义反转；`positions_end`/`rf_annual` 缺失 |
| reviewer-2605-engine | DB/引擎/缓存/测试 | REQUEST_CHANGES | 3 | 7 | 5 | 物化键缺 params；bars/benchmark 指纹不可执行；血缘表 PK 冲突；负缓存未定义；门禁必红 |

---

## 2. P0 处置（去重后 6 条，全部已修）

| 编号 | 问题（评审证据） | 处置 | v1.1 位置 |
|---|---|---|---|
| **EC-P0-1 / EE-P0-2（部分）** | 下钻只有声明式 `DrilldownSpec` + "decomposition + 仓储查询"，无法产出逐事件/逐日/逐证券明细；07 的 18 指标下钻与 API-035 硬阻断 | **接受**：新增 `DrilldownSpec.build_items(ctx, DrilldownFilters) -> Sequence[DrilldownItem]`（pure）；`DrilldownItem` 增 `security_code`；明细源扩到 rounds/fills/trades/asset_daily/bars/benchmark（由 ctx 提供）；引擎只负责排序/分页/格式化；上限 5000 + `truncated` | §4.3、§7.3、E-12、C-37 |
| **EC-P0-2** | `core/stats` 双主（05 交 numeric/wilson/bootstrap/estimators；07 交整个 stats）；`ci_stat_id` 注册/派发签名未定义；Wilson 的 success/n 无来源 | **接受**：冻结 **05 = numeric/wilson/bootstrap/estimators 框架 + `mean` estimator；07 = regression/concentration/drawdown/tstat + 新增 estimator 文件**；estimator 走 `core/stats/estimators/*.py` pkgutil 自动发现（07 加文件零改 05）；`ci="wilson"` 由引擎从 `ci_sample`（Bernoulli 0/1）求 `success/n`；`ci="bootstrap"` 要求 `len(ci_sample)==n` 且 `ci_stat_id` 已注册 | §4.4、E-05、E-15、C-19、§14 S-1/S-3 |
| **EC-P0-3** | stale 语义反转：05 把 stale 当内部短暂态，07 把 build stale/version 漂移当对外 stale；05 gate 又把 build 非 current 映射 blocked；指纹不含 build/market 状态 | **接受（对齐 07 并明确可达性）**：`stale` 仅在"上游数据状态过期"时对返回出（`asset_build.state=stale`、`market.as_of` 滞后）→ 无 value、不读缓存/不重算/不落库；**口径 bump 返回新版本 ok**（不返回 stale）；gate 优先级 `blocked > stale > unreliable > ok`；指纹补 `asset_build.state/last_status/data_as_of`、`benchmark.available`、`market.as_of`（§6.4） | E-13、§6.3、§6.4、C-41、§14 S-7 |
| **EC-P0-4** | `MetricContext` 缺 `positions_end`/`rf_annual`；07 冻结 def 依赖 `DatasetDep("positions_end")` → 注册即 CycleError | **接受（选择"07 内部重建"而非引擎加视图）**：`holdings_daily/positions_end` **不入视图层**，07 def 基于 `trades+bars` 重建（D-13）；`MetricContext` 显式含 `rf_annual`；registry 的 dataset 白名单不含这两个名字；07 §4.1/§7.1.1 需同步（§14 S-2/S-10） | E-06、§4.3、§14 S-2/S-10 |
| **EE-P0-1** | 物化唯一键/id 缺 `benchmark/params/rf/as_of`：切基准覆盖同版本行，E-02"旧行保留供趋势"失效 | **接受**：唯一键/确定性 id 改为 `(user_id, metric_id, period, definition_version, params_hash)`；`params_hash=sha256(canonical(benchmark, rf_annual, def params))`；`params` JSONB 留存原文；`as_of` 进 `inputs_hash`（同一 key 覆盖最新 as_of，符合"当前结果"语义）；切换 benchmark/rf 产生新行 | E-02、§3.1、§3.2、E-16 |
| **EE-P0-2** | bars/benchmark 是文件源（replay），无行数/updated_at/id；换夹具指纹恒不变 → read-through 返回旧（错）值 | **接受**：§6.4 逐视图指纹表；bars/benchmark = `provenance + file_sha256 + market.as_of`（replay）或 `market.daily_bar` 聚合 + `pull_range.checksum`（live）；`market.as_of` 进 `inputs_hash` | E-04、§6.4 |
| **EE-P0-3** | `metric_dependency.id` 未含 dep_id → 多依赖第二条 INSERT 撞 PK；无 `definition_version` 却按 (user,metric,period) 删 → 抹旧版本血缘 | **接受**：id 含 `dep_kind|dep_id`；表增 `definition_version` + `metric_result_id` FK；唯一键 `(user,metric,period,version,dep_kind,dep_id)`；删除按同版本 | E-03、§3.2 |

> 说明：EC-P0-1 与 EE-P0-2 的"明细/指纹"有重叠，合并处置；上表 P0 去重后 6 条。

---

## 3. P1 处置（关键项）

| 编号 | 问题 | 处置 | 位置 |
|---|---|---|---|
| EC-P1-1 / EE-P2-1 | `domain` 枚举擅自加 `perf` 无 C 项；Catalog 现缺 perf | **接受**：C-01 增"domain 枚举补 `perf`（attribution 本期不收）"；API-033 参数矩阵同步 | C-01、§7.1 |
| EC-P1-2 / EE-P1-1 | `from/to` 写"闭区间"与 C-34 半开冲突；M1 未定义映射 | **接受**：`period` 闭区间；`from/to` 半开 `[from,to)`，M1 仅接受"月初→次月初"（等价单月），跨月/单端 422 INVALID_PERIOD；05 §8 与 C-34 一致 | §7.1、E-09、C-34/§14 S-9 |
| EC-P1-3 / EC-P2-2 | ids/benchmark 边界、四端点 403/401/read PAT 用例缺失；越权断言误写"打 B 用户→404" | **接受**：补校验矩阵（空/重复/缺省→domain=all/未知/51 个）；benchmark 未知→UNKNOWN_BENCHMARK 422；四端点补 403；测试改"A/B 同 period 各自值 + B 响应零泄露 + admin 403 + 无 token 401 + read PAT 200"；`/metrics/{id}` 在 `gate_tenant_coverage` 的 EXEMPT 写理由 | E-09、§7.1、§9.2 |
| EC-P1-4 | ETag 指纹不含查询身份；与 Catalog `W/"<version>"` 冲突；304/写库顺序未定义 | **接受**：指纹加 `ids/domain/period/from/to/benchmark/include_exploratory + registry_version + item hashes`；沿用弱 ETag（`_if_none_match_hit` 已忽略 `W/`）；**304 判定在写库前（304 不写库）**；C-36 明写 | E-08、§7.1、C-36 |
| EC-P1-5 | 06 漏 C-06；kit 归属与 07 重叠 | **接受**：C-06 补入 §8；**07 拥有 `tests/contract/kit.py`，05 只交付引擎侧最小断言**（避免双写） | §8 C-06、§9.1 |
| EC-P1-6 | def 导出名三套（`def_instance`/`METRIC_DEF`/`DEF`）；测试 def 与 07 同 id | **接受**：冻结 **`DEF`**（Extensibility §3.5 约定）；测试 def 放 `backend/tests/support/metric_defs/`，用 `build_registry(extra_paths=...)` 注入，生产 registry 不扫；测试 id `perf.win_rate_stub`/`perf.expectancy_stub` | E-10、§4.3、§5、§9.3 |
| EE-P1-1 | CHECK 只拦 status，拦不住 `pending_window` 带 value 等 | **接受**：补 `sample_status='pending_window' ⇒ value IS NULL`、`n>=0/n_pending>=0`、`ci_low<=ci_high`、period/version 正则、非 ok 必带 reason | §3.1 |
| EE-P1-2 | RLS 谓词漏 `NULLIF(...,true)` 与 0006 不一致 | **接受**：复刻 0006 的 `TENANT_PREDICATE`（`current_setting('app.user_id', true)` + NULLIF + WITH CHECK） | §3.3 |
| EE-P1-3 | `data_as_of NOT NULL` × 新用户无 asset_build 行 → 首次访问 500 | **接受**：无 build 行时退化 `min(clock.today, market.as_of)` + `assumptions.pit_rule="no_asset_build"`；依赖 build 的指标仍 blocked（不落库） | §3.1、§6.3 |
| EE-P1-4 | `DatasetDep 空/缺失→blocked` 与 07"n=0→insufficient"冲突；负缓存未定义 | **接受**：区分"视图不可用/未构建"（blocked）与"窗口 0 样本"（insufficient + value:null）；E-11 只物化 `status=ok`，blocked/unreliable 不落库、下次重算 | §6.3、E-11 |
| EE-P1-5 | `gate_tenant_coverage` 会因新增 4 端点变红；§9 缺缓存/指纹/并发用例 | **接受**：4 operation 进 `test_isolation_matrix.py` 三张矩阵 + EXEMPT 理由；§9.2 增 read-through/指纹矩阵/并发/负缓存用例 | §9.2 |
| EE-P1-6 | 无估时表 | **接受**：§10 逐步骤 M-1~M-8 + 合计；05≈6.5d、06≈3.0d；与已批准 5~7d 的差异列入 E-17 人工决策 | §10、E-17 |
| EE-P1-7 | `positions_end` 缺席 | 同 EC-P0-4：裁决为"07 内部重建"，05 不放视图；07 同步 | E-06、§14 S-2/S-10 |

---

## 4. P2 处置（记录/部分修复）

| 项 | 处置 |
|---|---|
| API-033 无 ETag/304 条目、Catalog `domain` 缺 `perf` | C-01/C-35/C-36 补；`make api-snapshot` 后回写 |
| `params` 若作 query 必须入 Catalog | 本期不从公共 query 暴露 def params；只暴露 `benchmark`；C-38 说明 |
| 夹具路径与 07 冲突 | 改为 `backend/tests/fixtures/metrics/fx_bars_mini/`（含 MANIFEST/owner/synthetic，`ReplayMarketDataSource` 注入） |
| `MetricValue` 放 review 而 `core/metrics` 在 core（L2） | §4.2 明写：core 纯函数返回 core 自有 dataclass/元组，def 层包装 `MetricValue`；AST 断言 core 禁 import review |
| stale 语义漂移 | E-13/C-41 + §14 S-7 统一；`/metrics` 的 stale 仅上游数据过期可达 |
| 迁移 up/down 无宿主、`test-engine` vs C-25 `test-metrics` 命名 | `make test-engine` 内含 `alembic downgrade 0006 && upgrade head` 幂等步骤；C-42 并入 C-25 的 target 清单 |
| `07 §6.3` bar_density 行仍有 value、`§8.6` kit 写 `value` 而非 `value_units` | §14 S-7/S-8 要求 07 文档勘误；实现前同步 |
| `as_of` 公式引用 §0（实为 07 §6.2） | 已改引用（本设计 §1.1/§6.1 直接定义） |
| 13 字段口径/枚举 | §4.3 显式列出 13 字段；与 Extensibility §3.1 对齐 |
| `Test def` 与 07 `perf.win_rate` 冲突 | E-10 隔离；测试 id `*_stub` |
| `make test-engine` 目前不存在 | M-6 新增（属新 target，不是误引） |
| `domain=attribution` 本期不收 | §7.1 枚举不含 attribution；DB `domain` 列仍允许（未来） |

---

## 5. Lead 自审补充（评审员未覆盖）

| # | 问题 | 修复 |
|---|---|---|
| L-1 | E-07 初稿把 `ScopeSpec.unit`（round/trade/day）当"scope 粒度"拒绝，会误拒 07 全部指标 | 改为"period 粒度=月；unit 作为统计单元元数据透传" |
| L-2 | operationId 用 `metrics_batch` 等 camelCase 片段，与 Catalog §1.1 snake_case 规范不符 | 改为 `metrics_get_batch/metrics_get_explain/metrics_get_drilldown/metrics_get_detail`，C-43 登记 openapi_meta |
| L-3 | `MetricResult`/API-079/035 无 `benchmark` 参数，无法从参数维度选择物化行 | 079/035 加 `benchmark?`；033 已有 |
| L-4 | `data_as_of` 新用户无 asset_build 行会 NOT NULL 失败 | 退化公式 + assumptions（同 EE-P1-3） |
| L-5 | `metric_result.updated_at` 在 `ON CONFLICT DO UPDATE` 不会自动更新 | UPSERT 显式 `updated_at=now()` |
| L-6 | 迁移未写授权 | 复刻 0006 `_grant_roles()` |
| L-7 | `reason` 无白名单 | §6.3 增机读码白名单 |
| L-8 | `ci_sample` 未约束与 `n` 关系 | §4.2 增约束：wilson=Bernoulli；bootstrap `len==n`；`ci=none` 必须 None |
| L-9 | `ETag` 弱/强与既有 helper | 保持弱 ETag（helper 已忽略 `W/`）；304 先判定、不写库 |
| L-10 | 初稿"API-038 handler → 07"与 07 D-17（06/07b）不一致 | E-18 明确归 **06 收尾** |

---

## 6. 修改后的验收性自查

| 项 | 结论 | 证据（v1.1） |
|---|---|---|
| 07 冻结契约逐字段 | PASS | §4.2/§4.3（MetricValue/Context/MetricDef/Dataset 白名单/DEF 导出） |
| DB 四态矩阵可被 CHECK 拦住 | PASS | §3.1 六条 CHECK |
| 缓存正确性（键/指纹/负缓存） | PASS | E-02/E-04/E-11、§6.2、§6.4、§7.1 |
| stale 可达路径与 07 一致 | PASS（待 07 文档同步 S-7） | E-13、§6.3 |
| drilldown 可实现 | PASS（07 def 需实现 build_items） | E-12、§7.3、§14 S-4 |
| 端点校验/错误码/鉴权 | PASS | §7.1~7.5、§9.2 |
| 测试可执行（门禁/migration/夹具） | PASS | §9.1~9.3 |
| 估时与已批准 5~7d | **CONDITIONAL（E-17 待人工）** | §10：05+06≈9.5d；备选 8.0/7.0d |
| 07 文档同步项 | 10 项待同步 | §14 S-1~S-10 |

---

## 7. 剩余开放项

| # | 项 | 状态 |
|---|---|---|
| 1 | **E-17 估时**：05+06 9.5d vs 已批准 5~7d，选"接受/挪 07b/砍验收" | **待人工拍板（阻塞排期，不阻塞设计）** |
| 2 | E-01/E-02/E-05/E-10/E-11/E-12/E-13/E-15/E-18 工程决策 | 建议按推荐，人工抽查 |
| 3 | 07 文档 S-1~S-10 同步 | 实现前由 Lead 一次性提交文档 PR |
| 4 | C-35~C-43 契约条目并入 07 的总包 | 随契约 PR |
| 5 | `metric_result` 的 `data_as_of` 在 as_of 前进时覆盖最新行（不保留历史 as_of） | 已按"当前结果"语义记录；若未来需 as_of 历史，另加维度（YAGNI） |

---

## 8. 评审结论

- 设计骨架（包结构、DB、read-through、端点、测试）方向正确，与 07 冻结契约大体同构；初稿的 4 个契约级缺口（下钻 builder、stats 双主、stale 语义、positions_end）会直接阻断 07 联调，已在 v1.1 修复。
- 两名评审员均 **REQUEST_CHANGES**，无 **BLOCK**；v1.1 修完 P0 与关键 P1，剩余为**人工估时决策（E-17）**与 **07 文档同步**。
- **放行条件**：E-17 拍板 + 07 同步 PR（§14 S-1~S-10）+ 契约 PR（C-01~C-06/C-16~C-19/C-22/C-24/C-26/C-28/C-29/C-31/C-34~C-43）合并后，进入 M-1…M-8 实现。
- **修改落地状态**：v1.1 已写入本记录 §2~§5 的全部处置；评审员报告与修改后文档同批提交（本轮仅文档，无生产代码改动）。
