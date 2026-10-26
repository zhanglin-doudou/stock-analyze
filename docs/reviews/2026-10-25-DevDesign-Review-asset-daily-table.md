# 资产表设计 · 交叉审核记录

> 审核对象：`docs/architecture/2026-10-25-asset-daily-table.md` **v0.1**（2026-10-25）
> 修订结果：设计文档已更新为 **v1.0**（同路径），本记录汇总三方独立审核意见与 Lead 裁决。
> 状态：**AI 交叉审核 + 第二轮独立验证完成；人工已批准；实现/门禁/验收完成（见 [Implementation Log](2026-10-25-asset-daily-table-Implementation-Log.md)）**

---

## 1. 审核元信息

| 项 | 内容 |
|---|---|
| 方式 | 三名独立审核员（fresh context，只读）并行审核；Lead 汇总裁决 |
| 审核员 | `data-model-reviewer`（数据模型/迁移/增量）、`api-contract-reviewer`（API/契约/安全）、`test-ux-reviewer`（测试/前端） |
| 只读约束 | 未修改任何文件、未实现代码、未写库；审核员仅返回结论文本 |
| 已执行基线命令 | `.venv/bin/python -m pytest backend/tests/contract/test_openapi_contract.py -q` → **20 passed in 0.78s**（api-contract-reviewer）；`scripts/ci/gate_rls_coverage.py` → `platform 4/4 / ledger 7/7 / review 4/4，✅ 15/15（豁免 2）`、`alembic check` 无漂移、`gate_endpoint_coverage.py` 30/30（data-model-reviewer） |
| 未执行 | 完整 `make test-p2`/`make api-check`/`make sdk`/迁移 upgrade-downgrade/RLS 新表计数/性能基准（沙箱与未实现限制） |
| 审核结论 | 三方均 **APPROVE_WITH_CHANGES**，第二轮独立验证 **APPROVED_WITH_FIXES**；无 BLOCK。P0 均为设计层可修，v1.0/v1.1 已逐条收敛 |

---

## 2. 裁决汇总

| 审核员 | 聚焦 | 裁决 | P0 | P1 | 关键阻塞项 |
|---|---|---|---|---|---|
| data-model-reviewer | 表/迁移/增量/口径 | APPROVE_WITH_CHANGES | 3 | 7 | 日期改晚漏算；full 不清理旧起点前数据；负市值与 CHECK 冲突 |
| api-contract-reviewer | API/契约/安全/门禁 | APPROVE_WITH_CHANGES | 3 | 9 | 非法 stage；job.result 无 API 出口；单飞 params 写死 |
| test-ux-reviewer | 测试/前端/可执行性 | APPROVE_WITH_CHANGES | 3 | 12 | job.result 不可观测；MONEY_FIELDS 冲突；IT-01 比较口径必失败 |
| verifier（第二轮） | v1.0 闭环/内部一致性/可执行性 | APPROVED_WITH_FIXES | 4 | 5 | 幽灵端点 `GET /assets.build`；IT-01 state 漏 `updated_at`；AT-26 stage 集合不可达；D9 陈述与算法相反 |

---

## 3. P0 处置（已全部在 v1.0 修复）

| 编号 | 问题（审核证据） | 处置 | v1.0 位置 |
|---|---|---|---|
| **DM-P0-1** | 脏流水 `trade_date` 改**更晚**时，旧日期行不会被任何重建覆盖 → 增量 ≠ 全量（`_MUTABLE_FIELDS` 含 trade_date：`ledger/repositories/trades.py:29-48`） | **接受（保守方案）**：脏集合含 `version>1`（改日期/软删/恢复/撤销）→ 全量重建；纯新增导入 version=1 才增量（D10）。补 IT-13/UT-21 | §6.2/§6.3-3/§6.4 |
| **DM-P0-2** | full 只 `delete_range(start..end)`，earliest 流水被删导致 `start` 后移时，`[旧 start, 新 start)` 旧行永久残留；`rows_count` 虚报 | **接受**：full 一律 `delete_all`；`start != state.start_date`（双向）列为独立 full 触发；写事务内 `count(*)` 复核 `rows_count`；补 IT-12 | §6.2/§6.4/§4.2 |
| **DM-P0-3** | `CHECK(market_value >= 0)` 与 nav 超卖推演（负持仓/负市值、degraded）冲突，构建必失败，且与 UT-12 自相矛盾 | **接受**：删该 CHECK；明确超卖日允许负市值、状态 degraded（D8） | §4.1/附录 B |
| **API-P0-1** | `loading/computing` 不在 `JobStage` 枚举（`contracts/enums.py:51-57`、Catalog §5.2），`JobQueue.progress` 原样落库不校验 | **接受**：改为 `queued → recomputing → writing → finalizing`；补 AT-26 SSE stage 断言 | §7.2 |
| **API-P0-2** | `job.result` 有列无 API 出口（`JobOut` 只有 result_ref），AT-11/16/18、FT-03、§8.3 增量提示不可实现 | **接受（方案 b）**：不扩 `JobOut`；构建摘要持久化到 `asset_build_state`（`last_mode/last_rebuild_from/last_rows_written/last_status`）经 `GET /assets` 的 `build` 对象暴露；job.result 仅运维/DB 断言。补 IT-15/AT-11/FT-03 | §4.2/§7.1/§7.2/§9 |
| **API-P0-3** | 单飞 params 写死 `{"mode":"auto"}`，`mode=full` 会被静默复用成 auto（`platform/jobs.py:79-89`） | **接受**：`params={"mode": <解析默认值后的真实值>}`；request_hash 同样规范化 | §6.3-8/§7.2 |
| **TEST-P0-1** | 同 API-P0-2（测试视角） | 同 API-P0-2 | §9.3/§9.5 |
| **TEST-P0-2** | 把 `total_assets/daily_pnl/prev_total_assets` 加入 `MONEY_FIELDS` 会让 nav 的 3 位示例被强制 2 位 → contract 测试红（`openapi_meta.py:44-79`、`test_openapi_contract.py:174-200`） | **接受**：只加资产独有字段 `cash/holding_cost/unrealized_pnl`；`daily_return` 加 RATIO_FIELDS；`daily_return_pct` 加 `_STRING_EXAMPLES`；`listAssets/buildAssets` 用 `EXAMPLE_OVERRIDES` 手写 2 位示例；**不动 nav** | §7.3/GT-07 |
| **TEST-P0-3** | IT-01「增量 == 全量」未排除 `rebuilt_at/created_at/updated_at`，按 v0.1 写法必失败 | **接受**：给出参与/排除字段清单 + canary（改坏 total 必须失败、只改 rebuilt_at 必须不失败） | §9.4 IT-01 |

---

## 3.1 第二轮独立验证（v1.1 修复）

第二轮验证员（fresh，只读）对 v1.0 给出 **APPROVED_WITH_FIXES**，发现 4 个 P0（均已在 v1.1 修复）：

| 编号 | 问题 | 修复 |
|---|---|---|
| V-P0-1 | 幽灵端点 `GET /assets.build`（契约只有 API-081/082，新增第三端点会打乱 82/45/矩阵计数） | 全文改为「`GET /assets` 响应中的 `build` 对象」；不新增第三端点 |
| V-P0-2 | IT-01 state 比较漏 `updated_at`（两次写 state 时间必然不同 → 等价性测试必红） | IT-01 的 state 排除清单补 `updated_at/created_at` |
| V-P0-3 | AT-26 断言 stage 集合不可达（平台 claim 置 `parsing`、finish 置 `done`） | 改为「handler 上报阶段 ∈ {recomputing,writing,finalizing}；终态 status=succeeded/stage=done 属平台行为」 |
| V-P0-4 | D9 批准陈述与 §6.3-10/IT-17 算法相反 | D9 重写为三档时机，§12/记录同步 |

验证员 P1 已同批处理：`state_reason`→`status_reason`、回填早于首个余额行→full、`rows_total` 只来自 job.result、missing/blocked 的 build 块可空性、示例 08-17/45 与窗口公式一致；另有 GT-01 验收计数（含表头 83）、DATABASE market 已存在、DEV-PLAN 4 处计数、AT-15 不同 scope、软删夹具约定等 P2 一并修正。

---

## 4. P1 处置（v1.0 已修，或明确降级/拒绝并说明）

| 编号 | 问题 | 处置 | v1.0 位置 |
|---|---|---|---|
| API-P1-1 / DM-P1-2 / TEST-P1-4 | `build.state` 判定链缺陷（blocked 被 missing 抢占、缺 definition_version、`data_as_of` 两轴混比、示例自相矛盾） | **接受**：重写 8 步判定链；`data_as_of` 定义为构建血缘；新增 `has_new_market_data`；示例改为 09-30 自洽 | §7.1/§7.2 |
| API-P1-2 | worker 没有行情端口，`build_assets` handler 拿不到 `MarketDataSource` | **接受**：WorkerRunner 懒装配 `create_market_source(..., session_factory=self.db.session, clock=self.clock)`；`JobHandler` 加第 5 参；同步改所有 handler 与 worker 单测；live 部署需 akshare（D11） | §7.2/§10.1/§11 |
| API-P1-3 / TEST-P1-1 / TEST-P1-10 / DM-P2-1/2 | 门禁同步清单不完整：contract `json_count 36→38`、`test_isolation_matrix.py`、DEV-PLAN A.1（期次=二）/80 计数、Catalog §4 Job.type、REVIEW_TABLES 4→6、DATABASE.md 缺 nav_daily/market | **接受**：§7.3 补全；GT 清单补 GT-03/07/10 | §7.3/§9.6/§10.1 |
| API-P1-4 / TEST-P1-2 | AT-06「跨用户 cursor → 400」与现有 cursor 实现（不含 user_id）冲突 | **接受（按现状）**：改为「A 的 cursor 在 B 名下只返回 B 的行，200」；篡改 → 400；user 绑定 cursor 列为既有债、本期不做 | §7.1/AT-06/§11 |
| API-P1-5 | AT-05 要求 `WWW-Authenticate`，全仓未实现 | **部分拒绝**：本期不扩平台错误中间件；AT-05 改为 401 `TOKEN_EXPIRED`，`WWW-Authenticate` 缺口单列既有债 | AT-05/§11 |
| API-P1-6 / TEST-P1-8 / DM-P2-13 | `rows_written < rows_total` 在历史 ≤45 天时必失败；IT-02 无判别力 | **接受**：改为 `rows_written == end - rebuild_from + 1`；最小写入用例要求历史 >45 天 + 两次构建间推进 FrozenClock | AT-16/IT-02/§9.7 |
| API-P1-7 | `IDEMPOTENCY_*` 无 AT 覆盖；无 build_assets SSE stage 用例 | **接受**：补 AT-25（同 key 异体 422）、AT-26（job/SSE stage 枚举 + 单调）；`IDEMPOTENCY_IN_PROGRESS` 注明平台层语义、HTTP 层难观测 | §9.3 |
| API-P1-8 | request_hash 未规范化，`{}` 与 `{"mode":"auto"}` 误报 422 | **接受**：基于解析默认值后的规范 body 计算；AT-12 增断言 | §7.2 |
| API-P1-9 / TEST-P1-9 | AT-19「24h 作用域」无实现支撑 | **接受**：拆为同 key 重放 / DB `expires_at≈+24h` / 跨用户互不影响；不断言 24h 后失效；AT-13 明确不同 key 才能测 409 | AT-13/AT-19 |
| DM-P1-1 | 水位线 `max(updated_at)`+`>` 对长事务晚提交会永久漏检（PG `now()`=事务开始时间） | **接受（改进方案）**：变更指纹 `(count,Σversion,max(updated_at))` 检测变化；仅指纹变化时做脏查询，下界取 `水位线 − 1h`；仍查不到 → 全量兜底；不依赖时钟单调性 | §6.2/§6.3-1 |
| DM-P1-3 | 「45 天安全窗吸收行情修正」无触发源（无行情水位线） | **部分接受**：v1 不建跨 schema 行情水位线；明确三档时机（end 前进窗口 / as_of 变化窗口 / 无 as_of 变化需 `mode=full`，D9）；删除过度承诺 | §6.3/§6.4/§11 |
| DM-P1-4 | `end < start` 无分支，会写负 `rows_count`/反向水位线 | **接受**：显式分支清空 + through/start=NULL；UT-19 | §6.2/§6.4 |
| DM-P1-5 / TEST-P1-3 | `assumption=statement` 永不可达（start 为全历史最早流水） | **接受**：v1 `assumption` 恒为 `back_solved`；移除示例中的 statement；API/UI 措辞同步 | §5.2/§7.1 |
| DM-P1-6 | 增量读取未规定稳定序，余额取值/反推不可复现 | **接受**：复用 `balances_ordered` ORDER BY 并在仓储 docstring 声明；UT-14 确定性断言 | §5.3/§6.2/§6.3-5 |
| DM-P1-7 | degraded 口径「与 nav 完全同口径」不成立（nav 全局 vs 设计逐日）且 data_error 起始日未定义 | **接受**：采用**全局 degraded**（与 nav 一致）；逐日仅 blocked/行情未覆盖单独标记；IT-07 只比 total/cashflow | §5.3/§5.4 |
| TEST-P1-7 | 软删夹具用 raw SQL 不刷 `updated_at`，IT-05/AT-17 会假绿 | **接受**：测试必须走 API-015/019/020 或 ORM `update(Trade)`；写入 §9.4 | §9.4/§9.6 |
| TEST-P1-11 / TEST-P1-12 | JobProgress 无轮询兜底；缺「重新进入页面接管在跑 job」 | **接受**：页面级轮询兜底（复用 Rounds 页思路）；进入页面查询 pending/running build_assets 并接管；补 FT-11/FT-12 | §8.3/§9.5 |
| TEST-P1-5 / TEST-P1-6 | `end < last_built_through` 有规则无用例；GET 过滤/排序/limit 缺正向用例 | **接受**：补 IT-11、AT-20/AT-21/AT-22/AT-23/AT-24 | §9.3/§9.4 |

---

## 5. P2 处置（选取关键项；其余在实现阶段随 PR 收口）

| 审核项 | 处置 |
|---|---|
| blocked 数值显式 `null` 与 Catalog §0.8「value 缺省」的关系 | **接受**：API-081 §2 明写「本端点 blocked 行数值为显式 null」，与 MetricResult.value 缺省不是同一语义 |
| `daily_return` 用 floor 与 core ROUND_HALF_UP 不一致 | **接受**：改用 Decimal + ROUND_HALF_UP；pct 用 `Ratio.as_pct(2)`（HALF_EVEN，与 nav 一致） |
| `(user_id,trade_date)` 唯一已能范围扫描，普通索引冗余 | **接受**：不建重复索引（只保留 UNIQUE） |
| `asset_build_state.version` 伪乐观锁 | **接受**：改为 `UPDATE ... WHERE version=:expected`，0 行 → `VERSION_CONFLICT` |
| no-op 是否更新 `last_built_at` 矛盾 | **接受（改为可观测）**：no-op 不写数据/水位线，但写入 `last_mode=noop`、`last_built_at`、`last_rows_written=0`、`last_rebuild_from=NULL`，让 `GET /assets` 的 `build` 对象 可区分 no-op（否则前端无法显示「已是最新」）；IT-03 按此断言 |
| 无数据/全局 blocked 状态字段残留 | **接受**：`reset_state` 显式置空 start/through/data_as_of/assumption/status_reason；UT-10 |
| `overall_status` 未定义 / blocked 横幅与逐日 blocked 冲突 | **接受**：`last_status=blocked` 仅全局无锚点；逐日 blocked 只影响行/`last_status=degraded`，不触发空表横幅 |
| 市值公式应按逐标的 floor | **接受**：§4.1/§5.3 改为逐标的 `qty*close//1e8` 后求和 |
| `market_data.as_of` 进程级可变、作为 end 会振荡 | **接受**：`end=clock.today()`；as_of 只用于 `data_as_of` 与「>as_of 且持仓 → degraded」 |
| 短历史最小写入不可演示 | **接受**：IT-02/AT-16 改用 >45 天夹具并推进时钟 |
| 余额行覆盖丢弃其后 amount | **接受为已知限制**：文档化 + UT-07 固定行为；修正需 nav 同步改（人工决策，v1 不做） |
| 只有 blocked_reason 无 degraded 原因 | **接受**：统一为 `status_reason`（blocked 必填、degraded 可填） |
| `REVIEW_TABLES` 4→6、isolation 测试缺 nav_daily、DATABASE.md 缺 0004/0005 | **接受**：已写入 §4.4/§7.3 |
| `_STRING_EXAMPLES`/`_DICT_EXAMPLES`/`EXAMPLE_OVERRIDES` 缺项 | **接受**：写入 §7.3 |
| `POST /assets/build` 复用 `JobAcceptedOut` 多 `batch_id:null` | **接受**：新建 `AssetBuildAccepted`（示例即契约） |
| `make api-snapshot --check` 不是合法命令 | **接受**：改为 `python -m stock_api.cli api-snapshot --check` |
| 前端 `contract.ts` 类型别名漏列、`Layout` 未导出、`Cursor` 文案、`signedClass` 对 `0.00%` 误色、零口径 ESLint 尚不存在 | **接受**：文件清单补 `contract.ts`、导出 `Layout`；文案「下一页」；本页单独处理零值着色；FT-07 改为源码扫描 |
| Table 组件无 caption；筛选卡片无定义 | **接受**：v1 不扩 `Table.tsx`（去掉 caption 断言）；资产页 v1 无筛选控件 |
| `JobType` 枚举未加 `build_assets` | **接受（不阻塞）**：补 `JobType.BUILD_ASSETS` + 单测 |
| operationId 与 Catalog §7.3 snake_case 冲突 | **接受**：沿用仓库实际 camelCase；Catalog §7.3 注明 |
| 日期过滤半开 vs 既有闭区间 | **接受**：新端点与既有实现一致用**闭区间**，文档显式说明 |
| 性能目标无承载 | **接受**：§9.7 标注人工走查/基准记录，不设自动门禁 |

---

## 6. 新增决策（v0.1 未覆盖，v1.0 采用；需人工确认）

| 编号 | 决策 | 理由 |
|---|---|---|
| D8 | 超卖/历史缺买入导致**负市值**，状态 degraded，不建非负 CHECK | 与 nav 逐日推演一致；改判 blocked 会与 IT-07 冲突 |
| D9 | 行情修正三档：end 前进窗口 / as_of 变化窗口（跨度>45 天→full）/ 无 as_of 变化需 `mode=full`；不建行情水位线 | 避免 review→market 跨 schema 耦合；修复入口保留 |
| D10 | 任何修正/软删/恢复（version>1）→ 全量重建；纯新增（version=1）→ 增量 | 修复「日期改晚」漏算；个人数据量级可接受 |
| D11 | WorkerRunner 懒装配行情端口，`JobHandler` 加第 5 参；live 部署 worker 需 akshare | 异步构建必须能取行情；替代「同步 200」违背长任务约定 |
| D12 | 强制全量 `mode=full` 仅 API 暴露，UI 不暴露 | 避免误操作；行情 >45 天修正走 API/运维 |

---

## 7. 明确拒绝 / 降级的建议

1. **不扩 `JobOut.result`**：改由 `asset_build_state` + `GET /assets` 的 `build` 对象暴露构建摘要（P0 方案 b）。原因：改 `/jobs` 契约影响面更大，且前端刷新列表即可拿到摘要。
2. **不实现 user-bound cursor / 24h 失效**：与既有 `/trades`、`/rounds` 游标实现保持一致；Catalog §0.4 的 user 绑定/24h TTL 属既有契约债，不在本期扩大范围。
3. **不补 `WWW-Authenticate`**：平台级 401 头缺失是既有债，所有端点一致；本期只让 AT-05 断言现状（401 + TOKEN_EXPIRED），另立跟进。
4. **不建行情水位线**（D9）：接受「无 as_of 变化的历史 bar 修正需 `mode=full`」的残留风险。
5. **不改 `GET /nav`**：资产表用共享原语 + IT-07 同值回归；nav 的 3 位金额、GET 副作用等历史债不在本期修。
6. **不做同步 200 构建**：保留 202 + job，换取进度/幂等/单飞与未来扩展性。

---

## 7.1 Lead 自检追加修正（v0.1 审核未覆盖 / 审核后新发现）

在把审核意见落入 v1.0 时，Lead 额外发现并修复以下问题（均属于同一设计范围，提交人工审核前已写入正文）：

| # | 问题 | 修正 |
|---|---|---|
| S-1 | 伪代码在「指纹变化且 version>1」分支内把 `full=True` 后，`rebuild_from` 会沿外层 `else` 跳过而**未赋值** | 重构为 `rebuild_from = start` 默认值 + 内层 `if full: … else: …`；全量/增量分支均有定义 |
| S-2 | no-op 若完全不写 state，前端无法从 `GET /assets` 的 `build` 对象 区分「已是最新」 | no-op 写 `last_mode=noop/last_built_at/last_rows_written=0/last_rebuild_from=NULL`，数据/水位线/指纹不动（见 §6.3-4） |
| S-3 | 全局 FIFO degraded 下，增量若只改窗口，旧日期行仍是 ok → 增量 ≠ 全量 | state 增 `last_fifo_error`；该标志变化 → 全量；补齐 UT-26 |
| S-4 | `market_data.as_of` 前进但 `end` 未变时，旧 `date>as_of` 的 degraded 行永远修不回 | 将 `market_changed` 纳入重建触发；as_of 跨度 >45 天 → 全量；补 IT-17 |
| S-5 | 增量窗口前一日若无落库行，`prev_total` 无来源 | 缺前置行 → 全量兜底；且必须在拉行情前修正窗口（否则行情区间不足） |
| S-6 | 版本 bump 后旧行仍可能被列表读出，顶层却写新版本 | `GET /assets` 按 `state.definition_version` 过滤行并回显该值；补 AT-22 |
| S-7 | `definition_version`/`data_as_of`/`build.state` 之间口径未写死 | §7.1 判定链改为 8 步（missing/blocked/missing/stale×4/current），`data_as_of` 只表示构建血缘 |
| S-8 | S-3/S-4 修正引入的 `last_fifo_error` 未进迁移字段清单 | 已补 §4.2/附录 B |

## 8. 未验证 / 残余风险

| 项 | 说明 | 后续 |
|---|---|---|
| 完整门禁未实跑 | 沙箱内 `uv` 缓存不可写；未跑 `make test-p2`/`make api-check`/`make sdk`/迁移升级降级/新表 RLS 6/6/性能 | 实现后按 GT-01~GT-10 实跑 |
| 并发长事务水位线竞态 | 用「指纹 + 1h 重叠 + 全量兜底」覆盖；未做双连接端到端复现 | 实现阶段补 IT-14 或人工压测 |
| 无 as_of 变化的历史 bar 修正 | 只能 `mode=full`（D9） | 人工确认接受；后续可加行情水位线 |
| live 行情限流/成本 | 未联网验证 10 年 × 20 标的；增量已限定窗口取数 | 实现后 `make test-market`/live 手测 |
| 性能目标 | 10 年/5000 笔 <3s 等未实测 | 人工走查 + 基准记录 |
| worker 时钟确定性 | WorkerRunner 硬编码 `SystemClock`；确定性测试优先直接调 application（FrozenClock） | 实现时按现有 `test_rounds_rebuild` 写法 |
| 第二轮验证已做 | 验证员对 v1.0 给出 APPROVED_WITH_FIXES，4 个 P0 已在 v1.1 修复（见 §3.1）；未再发起第三轮 | 实现后由测试/门禁兜底 |
| 历史文档债 | nav 3 位金额、`data_as_of` 类型不一致、WWW-Authenticate、游标绑定、`openapi/` 路径等 | 单独立项，不在本期 |

---

## 9. 人工批准清单

请人工逐项确认（设计文档 §12 同）：

- [ ] **D1** 每日总资产一行（非明细行）
- [ ] **D2** 新建 `review.asset_daily`，不改 `nav_daily`
- [ ] **D4+D11** 异步 job + worker 装配行情源（需改 handler 签名；live worker 需 akshare）
- [ ] **D6** blocked 行数值显式 `null`
- [ ] **D8** 超卖日允许负市值、degraded
- [ ] **D9** 行情修正三档（end 前进 / as_of 变化窗口，跨度>45 天转 full / 无 as_of 变化需 `mode=full`）
- [ ] **D7** `end=clock.today()`；as_of 后有持仓的日期标 degraded（纯现金不降级）
- [ ] **D10** 修正/软删/恢复 → 全量重建
- [ ] **D12** `mode=full` 仅 API 暴露，UI 不暴露
- [ ] **范围** 本期只做资产表 + 数据契约；回撤指标后续做
- [ ] **交互** `/assets` 新页、按钮文案、构建摘要来自 `GET /assets` 的 `build` 对象（不新增第三端点）

批准后按设计 §10 顺序实现；实现完成后补 `docs/reviews/2026-10-25-asset-daily-table-Implementation-Log.md` 与验收报告。

---

## 10. 签署

| 角色 | 名称 | 结论 | 时间 |
|---|---|---|---|
| 独立审核（数据） | data-model-reviewer | APPROVE_WITH_CHANGES（P0×3/P1×7 已收敛） | 2026-10-25 |
| 独立审核（API/契约） | api-contract-reviewer | APPROVE_WITH_CHANGES（P0×3/P1×9 已收敛） | 2026-10-25 |
| 独立审核（测试/前端） | test-ux-reviewer | APPROVE_WITH_CHANGES（P0×3/P1×12 已收敛） | 2026-10-25 |
| 第二轮独立验证 | verifier | APPROVED_WITH_FIXES（4 P0 已收敛） | 2026-10-25 |
| 汇总/裁决 | Lead（AI） | v1.0 已逐条处置；无 BLOCK | 2026-10-25 |
| **人工审核** | **（待填）** | **待定** | — |
