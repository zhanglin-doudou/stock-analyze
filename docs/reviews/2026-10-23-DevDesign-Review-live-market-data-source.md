# DevDesign 评审：接入 akshare 真实行情源（MARKET_MODE=live）

| 项 | 内容 |
|---|---|
| 被评审文档 | [2026-10-22-live-market-data-source.md](../architecture/2026-10-22-live-market-data-source.md)（217 行） |
| 评审日期 | 2026-10-23 |
| 评审方式 | 逐节阅读 + 全仓代码对账（file:line）+ 架构基线比对 + akshare 上游事实核验（外网文档/源码/GitHub issue/东财接口实测）；只读评审，未改任何代码 |
| 结论 | **不建议按现稿直接实施**。方向正确（akshare / DB 缓存 / port 不变），但存在 **1 个口径级错误结论**（qfq 用于 NAV 估值）与 **11 个照做即门禁失败或功能不生效的阻断级缺口**；需先由人工裁定 §2 的 D1~D3，再按 §8 逐节勘误修订。 |

---

## 1. 总体评价

**做对的部分**（建议保留）：

- 数据源选型 akshare 与需求基线 [PRD-v2 NFR-9](../prd/PRD-v2.md)（`docs/prd/PRD-v2.md:494`）、[Architecture-v2.md:353](../Architecture-v2.md)（akshare 行情 / AlphaFeed 灾备 / LLM 为「外部可选依赖」）一致。
- 「公共行情数据放公共 schema、不启用 RLS、无 `user_id`」的隔离模型合理：G7 RLS 门禁只扫 platform/ledger/review 且只查带 `user_id` 的表（`scripts/ci/gate_rls_coverage.py:33,120-131`），market 不会被误伤。
- 「port 不变、新增两个实现类、装饰器做缓存」的分层意图正确；lazy import 保 replay 免装 akshare 的意图正确；测试分层（单测 mock / 集成标 live / e2e 手动）的骨架合理。
- 风险表识别到了 akshare 接口变动、首次拉取慢、同步阻塞事件循环等关键风险（但缓解手段不够，见 §4/§5）。

**核心问题**：现稿把「前复权 qfq 日线」当作「真实市价」写进 NAV 市值公式，并假设「历史日线不变」做永久缓存。前者会算错净值/MWR/组合收益，后者与 akshare 官方事实不符（qfq 历史价是时变的）。两项都已有明确的架构决策记录（v1 ADR-0009）与 PRD 条款（NFR-2），现稿未引用也未请求偏离授权。

---

## 2. 待人工决策（HITL 升级请求）

> 按 [AGENTS.md](../../AGENTS.md) HITL 协议：以下三项同时命中「歧义 / 影响不可逆（写入错误的净值历史）/ 策略例外（偏离 PRD+ADR）」，**暂停实施，请人工裁定后再修订设计**。

### D1（最高优先）复权与估值口径：qfq vs 不复权 / hfq + 因子

**触发条件**：策略例外 + 歧义 + 影响不可逆。

**已掌握信息**：

1. 现稿（line 9-14、§2、line 205、line 216）采用 `adjust="qfq"`，理由「TWR 标准做法、口径正确」「历史日线不变」「v1 不管复权因子变化」。
2. 需求与架构基线要求相反：
   - [PRD-v2 NFR-2](../prd/PRD-v2.md)（`docs/prd/PRD-v2.md:487`）：**后复权 / as-of 因子快照** + 涨跌停越界/复权因子跳变校验 + 留痕 `source/fetched_at/as_of/checksum`。
   - [v1 ADR-0009](../architecture/v1/ADR.md)（`docs/architecture/v1/ADR.md:177-190`）：采纳「后复权 + as-of 因子快照」，**被否方案明确包含「前复权实时计算（不可复现）」**；v2 ADR-0013~0024 未取代它。
   - [v2 Extensibility](../architecture/v2/Extensibility.md)（`:134`、`:121`）仍以 `bars_hfq` + `adjust=hfq` + `factor_version` 为数据版本口径。
   - [v1 Testing](../architecture/v1/Testing.md)（`:297,315`）：「前复权漂移会让历史回撤下周变另一个数」「qfq 不得顶替 hfq」。
3. 上游事实：akshare 官方文档明确 **前复权历史价格是时变的**（每次除权除息整体重算），**后复权历史价格不变**；长期持续分红公司的前复权价**可能为负**（来源见 §10）。
4. 技术层面，qfq 作为「市价」在 NAV 公式里至少四处出错：
   - `NAV = 现金 + Σ(数量 × 当日收盘价)`（`backend/packages/review/src/stock_review/application/nav.py:126-145`）需要**真实市价**。qfq 是相对最新价缩放的历史序列，2021 年的 qfq 收盘 ≠ 2021 年真实成交价 → **总资产绝对值失真**，而 `XIRR/MWR` 的期初/期末现金流用绝对值 → **MWR 错**。
   - 组合层面每个标的的复权因子 `f_i` 不同，权重变成 `qty·close·f_i / Σ` → **组合日收益（TWR）也会被扭曲**，不只是绝对值。
   - 股息重复计入：账单中股息/红利是流水（系统规则 `sys_dividend` 等，`backend/packages/ledger/src/stock_ledger/repositories/rules.py:49-52`，category=`dividend`），现金计入 NAV；qfq 已把分红折入价格连续性 → **除权日净值虚增**。（现稿 line 205 的「不影响」论证不成立。）
   - 永久缓存 + qfq 时变：同一历史区间在不同时间重拉会得到不同数值；`pull_range` 命中后不再重拉 → 结果取决于「首次缓存时间」，违反 NFR-5 可重放；分批拉取还会把**不同复权基准**的 bar 拼在同一条序列里，接缝处出现假跳变。
   - 额外：qfq 可能为负 → 违反 `Bar.close_units > 0` 的既有约定（`market.py:121-122` replay 强制校验）。
5. 上游实测补充：东财 `fqt=1` 的「涨跌幅」确实按前复权序列计算，与 qfq 收盘自洽（除权日无矛盾）；但 qfq 收盘只有 2 位小数，由 close 反推收益有 ±0.5% 量化误差，且首根 bar 的涨跌幅相对区间外前一日（与本仓 nav 首点=0 的约定不同）。

**需要人决策**：在 A/B/C 中选一，并决定是否需要新建 ADR-0025 / 修订 ADR-0009。

| 选项 | 内容 | 代价 | 评价 |
|---|---|---|---|
| **A（推荐，v1 最小正确）** | 估值一律用**不复权** close（`adjust=""`）；`daily_bar.adjust` 列保留扩展位。收益/回测类指标后续按 NFR-2 再补 hfq | 本次改动最小，NAV 立刻正确（与 replay 的 `supports_adjusted=False` 语义也一致）；除权日靠账单送转/红利流水 + 数量调整保持价值连续 | 与 NFR-2「收益类用 hfq」的偏离只影响**尚未实现**的收益类指标，不阻塞本期目标；建议登记为分阶段落地 |
| B（一次到位） | 落 hfq + 复权因子 + `factor_version`，估值用 `raw = hfq / factor` | 工作量最大（多一列因子/一张事件表、失效逻辑）；东财后复权数据本身有质量 issue，需先验证 | 最符合 NFR-2/ADR-0009，但超出本次「让 2021-10 NAV 出数」的目标 |
| C（维持 qfq） | 按现稿实施 | 必须显式接受 NAV 绝对值/MWR/组合 TWR 不可靠、股息重复计入、结果随缓存时间漂移 | 若选此项：必须删掉「TWR 标准做法/口径正确」论证、把「复权因子失效」从 Out of Scope 移回范围、补 ADR 记录策略例外 |

**Agent 倾向**：A。它用最小改动保证本期目标（NAV 正确）且不把错误数据结构固化成永久缓存；B 作为后续期次；C 只有在需求人明确接受上述数值缺陷时才可接受。

### D2 `MARKET_DATA_UNAVAILABLE`/503 是否要暴露给 `GET /nav`

**触发条件**：歧义（同一份文档内自相矛盾）。

**已掌握信息**：

- 现稿 §5 设计了 500/503/404 四类映射，并新增 `ErrorCode.MARKET_DATA_UNAVAILABLE`（503 retryable）。
- 实际唯一消费方 `backend/packages/review/src/stock_review/application/nav.py:148-174` 的 `_load_prices` **捕获所有 `ApiError`**（降级在 `:164-172`）、逐标重试后跳过 → 任何 4xx/5xx 都被降级成「逐日 blocked、HTTP 200」；`test_api_032_nav.py:172+`、`test_nav_application.py:224` 已固化该语义。
- `getNav` 的契约 `x-error-codes` 不含 503（`backend/services/api/src/stock_api/openapi_meta.py:462-476`），`openapi.json` 的 getNav responses 只有 200/401/403/422；现稿 line 153「GET /nav 不动」与 line 98-100「新增错误码并登记 §3」冲突。
- 结论：**按现稿实施，503 是 HTTP 死码，实际用户只会看到 `blocked_reason=缺行情数据`**（且无法区分「标的真没数据」和「网络抖了一下」）。

**需要人决策**：

| 选项 | 内容 | 代价 | 评价 |
|---|---|---|---|
| **A（推荐 v1）** | 保持「降级 blocked 200」，**不新增错误码**；adapter 内做 2 次有上限重试 + 真超时；把 `blocked_reason` 区分为「行情源暂时不可用（可重试）/ 标的不在支持范围 / 标的当日无行情」三类 | 0 个契约改动、0 个现有测试改写 | 与现状消费方一致，不制造死码；用户能拿到可操作信息 |
| B（真正暴露 503） | 改 nav 降级策略 + `openapi_meta` getNav `x-error-codes` + API-Catalog §2/§3 + `make api-snapshot` + 新增 ≥3 条 API 测试（含 retryable 断言） | 推翻 line 153/155，改动面大、回归风险高 | 只有当产品明确要求「行情故障必须让前端区分 503 并重试」时才做 |

**Agent 倾向**：A（本期），B 留到 NAV 异步化/降级矩阵统一治理时再做。

### D3 缓存覆盖语义与失效策略（`pull_range` 的「永久覆盖」）

**触发条件**：影响不可逆（错误覆盖被持久化）+ 歧义。

**已掌握信息**（三个可复现的缺陷）：

1. **连续月份永远不命中缓存**：nav 的取数区间是 `[period_start − 45 天, period_end]`（`nav.py:52` `_PRICE_LOOKBACK=timedelta(days=45)`，`nav.py:252-253`）。10 月请求 `[2021-08-17, 2021-10-31]`、11 月请求 `[2021-09-17, 2021-11-30]`，按现稿「`range_start <= start AND range_end >= end` 才算覆盖」永远互不覆盖 → 用户按月浏览时**每个月都整体重拉**，与「再点一次走纯缓存、避免重复拉取」的目标差距很大（只有完全相同的区间才命中）。
2. **当月区间会把「未来」标成已覆盖**：`period=当月` 时 `period_end=月末（未来日期）`；akshare 只返回截至今天的数据，但 `pull_range` 仍记录 `[.., 月末]` 已覆盖 → 本月内再请求永远命中旧缓存、拿不到新交易日的 bar → 持仓日被判 blocked，且**不会自愈**。
3. **永不失效**：一旦覆盖就不再重拉。叠加 D1 的 qfq 时变、停牌复牌、上游数据修正 → 错误数据被永久固化。现稿 line 216 把「复权因子变化失效」列为非目标，正是把最需要失效的场景排除了。

**需要人决策**（推荐 A+B 组合）：

- **A 覆盖判定改为区间并集/日期集合**（约 +40 行）：只要请求区间被若干已拉区间拼接覆盖即命中；尾部（最后 N 个交易日或 `trade_date >= today`)不缓存、每次重拉（stale-while-revalidate）；记录 `pull_range` 时 `range_end = min(end, 上游实际返回的最后交易日)`，并加 `complete` 标志表示是否包含未来日期。
- **B `pulled_at` TTL**：超过配置天数（如 30 天）或 `MARKET_LIVE_ADJUST`/版本变化时强制重拉。
- **C 失效键**（配合 D1-B）：`factor_version`/`base_date` 变化 → 整段失效。

### D4 范围确认（建议一并裁定，非阻塞）

- 只支持 A 股 6 位数字：ETF（51xxxx/159xxx）、可转债（11x/12x）、B 股（900/200）、北交所（4x/8x）会被当未知标的 → blocked。用户真实账单出现这些标的时 NAV 仍不可用。是否接受？至少应把 `blocked_reason` 写成「标的不在支持范围（仅 A 股）」，而不是笼统的「缺行情数据」。
- 本期不做熔断/限流/灾备源：与 [Reliability.md](../architecture/v2/Reliability.md) `:335-337`（3s/10s 超时、2 次重试、失败率熔断、并发隔离 8、令牌桶 5rps）、`docs/architecture/v2/Reliability.md:83`（SP-11 缓解措施含熔断）、NFR-9「可选灾备源」不符。是否作为 MVP 偏离登记后续期次？
- 是否接受 `GET /nav` 从「纯 DB 读」变成「请求内同步外网取数」：首次冷缓存 2 个标的约 1~6s，多标的线性叠加；NFR-10 写 p95 ≤500ms 不适用但读 p95 ≤300ms 会被打破。是否需要本期就改 job（202 + Location）？

---

## 3. 阻断级问题（P0，必须改设计）

| # | 设计位置 | 问题 | 仓库证据 | 修正 |
|---|---|---|---|---|
| P0-1 | line 117-124、148 | **live 分支是死装配**：`build_state` 从未调用 `create_market_source`，一直直接 new `ReplayMarketDataSource` | `backend/services/api/src/stock_api/app.py:14,83`；`create_market_source` 仅被测试调用（`backend/tests/unit/ledger/test_market_replay.py:264,273`） | app.py 改为 `create_market_source(settings, session_factory=db.session)`（`db` 是 build_state 内局部变量，line 124 的 `state.db.session` 写法不成立）；replay/live 共走工厂 |
| P0-2 | §2 line 33-60、文件清单 line 140 | **迁移 0005 缺 GRANT**：`stock_app` 是 NOBYPASSRLS 非 owner，新 schema/表默认无权限 → 首次请求 `permission denied for schema market` | `backend/migrations/versions/0004_review_nav.py:92-96` 的 `_grant_roles` 模板；`backend/packages/platform/AGENTS.md` 三角色模型 | 加 `GRANT USAGE ON SCHEMA market TO stock_app, stock_backup` + 两表 `SELECT,INSERT,UPDATE,DELETE TO stock_app` / `SELECT TO stock_backup` |
| P0-3 | 文件清单 line 137-150 | **SCHEMA_VERSION 未 bump**：启动即校验不匹配 → API 拒启、`/readyz` 503 | `backend/services/api/src/stock_api/version.py:4`（`0004_period2_nav`）；`app.py:47-61`；`backend/migrations/versions/0004_review_nav.py:99-112` 的 `_stamp_schema_version` | 0005 写 `schema_version='0005_...'`、downgrade 回 `0004_period2_nav`、`version.py` 同步；`docs/DATABASE.md:116-117` 一并更新 |
| P0-4 | line 9-14、§2、line 205、216 | **qfq 口径**（见 D1）：数值错误 + 与 NFR-2/ADR-0009 冲突 + 缓存永不失效 | `docs/prd/PRD-v2.md:487`、`docs/architecture/v1/ADR.md:177-190`、`docs/architecture/v2/Extensibility.md:121,134`、`docs/architecture/v1/Testing.md:297,315` | 按 D1 裁定改口径；若偏离必须补 ADR + 修订 `capabilities.supports_adjusted` |
| P0-5 | §3 line 64-71 | **缓存覆盖语义三缺陷**（见 D3）：连续月份不命中 / 当月缓存未来 / 永不失效 | `backend/packages/review/src/stock_review/application/nav.py:52,252-253` | 按 D3 改覆盖判定 + 尾部刷新 + TTL/失效键 |
| P0-6 | line 155「所有现有测试不动」 | 至少三处现有测试/夹具必改：错误码计数、live 工厂用例、测试库清理 | `backend/tests/unit/contracts/test_errors.py:25`（硬断言 47）；`backend/tests/unit/ledger/test_market_replay.py:272-274`（断言 live 抛 `NotImplementedError`）；`backend/tests/conftest.py:33-73`（TRUNCATE 不含 `market.*`，autouse 清理每例执行） | 文件清单补：`test_errors.py`（47→48，建议顺带把断言改成 `len(ErrorCode)` 还历史债，见 `docs/reviews/Period1-CodeReview-and-Acceptance.md:300`）、`test_market_replay.py`、`backend/tests/conftest.py`（`_MARKET_TABLES` 并入 TRUNCATE；测试 settings 强制 `market_mode="replay"`） |
| P0-7 | line 176-178 | **`live` marker 未注册且未被门禁排除** → CI 收网络用例 | `pyproject.toml:52-56`（markers 无 live）；`Makefile:131` 与 `backend/services/api/src/stock_api/cli.py:448-449` 均为 `-m "not integration and not api"` | markers 加 `live`；Makefile + `cli.py` 两处加 `and not live`（或测试内 `importorskip`/`skipif`）；补集成测试文件路径到文件清单 |
| P0-8 | line 131、文件清单 | **mypy strict 必挂 akshare/pandas**；且 `market_live.py` 被 ledger 顶层 import 时不能在模块顶层 import akshare，否则无 extra 环境 import 即炸 | `pyproject.toml:80-89` strict=true，仅一条 parsers override；`uv.lock` 无 akshare/pandas | 加 `[[tool.mypy.overrides]] module=["akshare","akshare.*","pandas","pandas.*"] ignore_missing_imports=true`；`market_live.py` 顶层禁 import akshare，lazy import 放到 `to_thread`/启动期 |
| P0-9 | line 150 | **optional 依赖位置错 + 版本下限无效 + uv.lock 未更新** | 根 `pyproject.toml:18-19` `package=false`（workspace 聚合，非 akshare 导入方）；`backend/packages/platform/pyproject.toml` 才是导入方；PyPI 最早版本是 1.16.72（`akshare>=1.14` 无保护作用）；pandas 3.1.0 于 2026-10-22 发布，akshare 1.19.1 未验证 | `backend/packages/platform/pyproject.toml` 加 `[project.optional-dependencies] live = ["akshare>=1.16.72,<1.20"]`（建议实际 pin `==1.19.1` 并给 pandas 上限）；根 pyproject 加 `live = ["stock-platform[live]"]` 转发；`uv lock` 重新生成并在实环境验证 `uv sync --extra live` |
| P0-10 | line 183 e2e 第 3 步 | **`.env` 改 `MARKET_MODE=live` 会翻转整个测试套件**（conftest 读真实 `.env`） | `backend/tests/conftest.py:78-80,115-117`；`backend/tests/api/test_api_032_nav.py:172+` 依赖 replay 语义 | conftest 强制 replay（`settings.model_copy(update={"market_mode": "replay"})`）；e2e 步骤加「验证完改回 replay」 |
| P0-11 | line 24-27、§8 | **Port 5 个成员只设计了 2 个**：`capabilities`/`provenance`/`get_latest_quotes` 未定义 → `AppState.market_data: MarketDataSource` 在 mypy strict 下不成立 | `backend/packages/platform/src/stock_platform/ports.py:125-145`；`backend/services/api/src/stock_api/deps.py:39` | 设计补：两个类的 `capabilities`（`mode="live"`、`symbols`、`supports_adjusted` 取值语义）、`provenance`（`live:akshare@version#pull/pulled_at`）、`get_latest_quotes`（基于日线 last，且说明缓存/空结果语义） |

---

## 4. 高风险问题（P1）

| # | 问题 | 影响 | 证据/依据 | 修正 |
|---|---|---|---|---|
| P1-1 | `MARKET_DATA_UNAVAILABLE` 是死码（见 D2） | 白加错误码、文档漂移 | `nav.py:148-174`、`openapi_meta.py:462-476` | D2 裁定；选 A 则删除该错误码 |
| P1-2 | `as_of` 语义错 & 生产不读：设计让 live 返回 `clock.now().date()`（周末/节假日报未覆盖日），且 Clock 未注入；nav 实际用 `state.clock.now()` 作为 `data_as_of` | 「数据覆盖到哪」不可信；`data_as_of` 假新鲜（拉 2021 数据也报今天） | `ports.py:136-138`（as_of 定义）、设计 line 86、`backend/services/api/src/stock_api/routers/nav.py:150` | `as_of` 改为「已缓存/已拉取数据的最大交易日」或注入 Clock 计算；nav 的 `data_as_of` 改取 market/cache 覆盖日（至少写清语义与后续期次） |
| P1-3 | `capabilities` 对 live 失效：`supports_symbol = symbol in symbols`，live 无静态清单时全 False | 任何按 capabilities 预检的调用方会全拒 | `ports.py:88-102` | 明确 live 语义：`symbols=()` 且调用方不得据此拒绝；`supports_adjusted` 按 D1 取值 |
| P1-4 | 超时设置形同虚设：`to_thread` + `wait_for` 不能取消正在执行的线程；设计未把 timeout 传给 akshare | 慢请求堆积线程池、`MARKET_LIVE_TIMEOUT_SECONDS` 无效 | Python `Future.cancel` 语义；akshare 1.19.1 `stock_zh_a_hist(..., timeout=None)` 原生支持 | 直接传 `timeout=`；加 semaphore（1~2）+ 同 symbol/区间单飞；`wait_for` 只做观测告警；东财会按并发封 IP 数小时（见 §10） |
| P1-5 | **symbol 白名单是 `\d{6}`，会静默返回非 A 股/错误标的数据** | 数据污染（最危险的一类） | akshare 源码 `market_code = 1 if symbol.startswith("6") else 0`，无白名单；深市 159xxx/12xxxx/200xxx 会正常返回对应品种，900xxx 等市场码错配 | 改为显式交易所前缀白名单（60/68/00/30/43/8x…，剔除 5/1/2/9 非 A 股段）+ 上市日期校验；不支持品种给明确 `blocked_reason` |
| P1-6 | 空 DataFrame → `NOT_FOUND` + 永久 `bar_count=0` 不可靠 | 有效 A 股也会因上游临时问题返回空（issue #5751），被永久固化成「无数据」；反过来又让「标的不存在」和「上游异常」共用 404 | 设计 line 62、93；akshare 源码 `data is null` 时返回空 DF | 合法 A 股且区间在上市后 → 视为上游异常；仅确认非法代码/区间完全早于上市才 NOT_FOUND；负缓存必须带 TTL；不得写永久 `bar_count=0` |
| P1-7 | float→定点转换会截断/off-by-one | 价格/收益差 1 个最小单位，且违反 AGENTS.md 禁 float 约定 | 设计 line 85 `int(Decimal(pct).scaleb(6))`；akshare 数值列 float64（且 `pd.to_numeric(errors="coerce")` 会把坏值变 NaN 不抛） | 统一 `Decimal(str(x))` + `ROUND_HALF_UP` + 超范围/NaN 拒绝（参考 `market.py:69-82`、`stock_core/values.py`）；`return_units` 建议由 close 序列派生，并注明当前生产无消费方 |
| P1-8 | 网络调用在 DB 事务内 + 连接池占用 | 单请求占 2 条连接（nav 事务 + 缓存事务），network 15s 期间压住连接；pool 10+10 时 10 并发即打满 | `backend/packages/platform/src/stock_platform/db.py:69-83`、`config.py:43-44`、`backend/services/api/src/stock_api/routers/nav.py:80-88` | 先取数（不持事务）→ 再短事务 upsert；缓存 session 只在写时打开；必要时单飞/预热 |
| P1-9 | `pull_range` 并发 insert 无冲突处理 | 双击/并发请求 → `IntegrityError` 500 | 设计 line 69（`insert pull_range`） | `ON CONFLICT DO NOTHING`（或 DO UPDATE），与 `daily_bar` upsert 同事务 |
| P1-10 | 无留痕/日志/指标 | 违反 NFR-2 留痕（`source/fetched_at/as_of/checksum`）与 Reliability FM-17 的 `market_fetch_errors` 指标 | `docs/prd/PRD-v2.md:487`、`docs/architecture/v2/Reliability.md:314` | `pull_range` 加 `checksum`/`rows`/`provenance`；拉取/命中/失败打结构化日志 + 指标（可后置到可观测期次，但设计要写明） |
| P1-11 | 无「陈旧缓存降级读取」路径 | 架构 §7 要求行情源故障时资产类 `stale` 可用，而非一律 blocked | `docs/architecture/v2/Reliability.md:324`、`docs/Architecture-v2.md:74` | 明确：缓存命中优先于网络（现稿已有）；冷缓存 + 源故障 → blocked 可接受，但要在设计里显式写「与 §7 的差异」 |
| P1-12 | 若 D1 选 hfq，需先验证上游数据质量 | 东财后复权数据存在质量问题（issue #7250）；直接永久缓存会把脏数据固化 | §10 引用 | 抽样验证 `hfq_close / raw_close` 为分段常数且阶跃日与公告一致，再决定 |

---

## 5. 中低风险与改进建议（P2）

1. **模型风格**：`market_models.py` 复用 `TimestampMixin`/`DateTime(timezone=True)`，主键用 `String(40)`（仓内惯例，`review/models.py:173`）；DDL 与模型逐列一致。
2. **迁移环境**：`backend/migrations/env.py:34` 的 `OWNED_SCHEMAS` 需加 `"market"`，并在 env.py import `stock_platform.market_models`，否则 `make migrate-check` 看不到 market 漂移。
3. **downgrade**：0005 需写 drop 表 + drop schema + 回 stamp 0004（参考 `0004_review_nav.py:114-120`）。
4. **文档同步**（现稿只列了 API-Catalog §3）：`docs/DATABASE.md`（schema 布局表、TRUNCATE/备份清单）、`docs/architecture/v2/README.md`（「三 schema」「47 个错误码」关键数字）、`docs/Architecture-v2.md:474`（`MarketDataSource` 在 `review/domain/ports.py` 的旧描述）、`docs/architecture/v2/Reliability.md`（若接受偏离需记录）、`infra/.env.example`（4 个新环境变量）。
5. **ADR**：新增外部依赖 + 公共 schema + 错误码 + 复权口径偏离，建议补 ADR-0025（若 D1 选 C 则必须补）。
6. **`market_live_source: Literal["akshare"]`** 单值 Literal 无扩展价值，建议按 Extensibility EP-2 的 `config/market.yaml` 显式列表 + 工厂字典（或先保留 str 校验，写明后续多源）。
7. **`market` schema 是缓存而非事实源**：备份/恢复策略中应显式排除（丢了可重拉），避免 restore 演练把它当业务数据校验；RPO 相关文档注明。
8. **e2e 前置条件**：本仓 `.venv` 无 akshare；`uv sync --extra live` 需要网络与可写 uv cache（本评审环境 sandbox 下 uv cache 只读，未能实测）；东财对服务器 IP 有封禁风险，e2e 要限流、失败可重试。
9. **`Bar.return_units` 首根 bar 语义**：上游首根涨跌幅相对区间外前一日，与本仓 nav 首点=0 的约定不同；若保留按涨跌幅转换，需在设计里写明首根处理。
10. **命名/异常**：`AkshareMarketDataSource._ensure_akshare()` 建议用 `importlib.util.find_spec` 提前判缺失（启动期 fail-fast 比首个用户请求报 500 更好）。

---

## 6. 与既有架构基线的偏离清单（需在设计里显式声明）

| 基线 | 要求 | 现稿 | 处理 |
|---|---|---|---|
| PRD-v2 NFR-2 | 后复权/as-of 因子快照、涨跌停/因子跳变校验、`source/fetched_at/as_of/checksum` 留痕 | qfq、无校验、仅 `source/pulled_at` | D1 裁定 + 留痕字段补齐或登记偏离 |
| PRD-v2 NFR-5 | 派生数据可全量重建、同输入 diff=0 | qfq + 永久缓存 → 结果随缓存时间变化 | D1/D3 |
| PRD-v2 NFR-9 | 主源 akshare（经校验层）+ 可选灾备源 + 关键数据本地缓存 | 有 akshare + 缓存；无校验层、无灾备 | 本期可缩范围，但需登记 |
| v1 ADR-0009 | 后复权 + as-of 因子快照；前复权被明确否决 | qfq | D1（若维持 qfq 必须新建 ADR 取代 0009） |
| v2 Extensibility X-3 / EP-2 | 新增源只实现 1 个 Protocol；路径 `review/infra/marketdata/<src>.py` + `config/market.yaml` + `backend/tests/contract/test_market_source.py` 参数化 | platform 实现 + env 配置 + 无参数化契约测试 | 至少补参数化契约测试；路径偏离记录 |
| v2 Reliability §8 | 行情 3s/10s 超时、2 次重试、失败率熔断、并发隔离、限流 5rps | 1 个 15s 设置、无重试策略/熔断/隔离 | MVP 偏离登记 + 至少真超时/重试/并发上限 |
| v2 Reliability §7 | 行情源故障 → 资产类 `stale` 可用（缓存 ≤T-1） | 冷缓存失败 → blocked | 设计写明差异与后续期次 |
| API-Catalog §7 契约治理 | 新增错误码：§3 + `x-error-codes` + `problem+json` 示例 + 快照 | 只列 §3 | 按 D2 决定是否新增，并同步契约 |
| DATABASE.md / README | 「三 schema」「47 个错误码」等关键数字 | 未列更新 | 补文件清单 |

---

## 7. 建议的测试补充

1. **Port 契约参数化测试**（Extensibility X-3 明确要求）：`backend/tests/contract/test_market_source.py` 对 Replay/Akshare/Cached 三个实现跑同一套断言（能力声明、空区间、非法 symbol、as_of、provenance、`get_latest_quotes`），保证「换实现不改调用方」。
2. **缓存用例**（在现稿 5 条基础上补）：连续月份命中（10 月 → 11 月只拉增量/不重拉）、当月尾部刷新、并发双请求（`ON CONFLICT`）、`adjust`/版本切换失效、TTL 过期重拉、DB 写失败上抛。
3. **转换边界用例**：NaN/None/负价/超过 2 位小数/涨跌幅缺失 → 明确 `INTERNAL_ERROR`；`Decimal(str(x))` 不做二进制截断。
4. **错误映射用例**：`Timeout`/`ConnectionError`/`JSONDecodeError`/缺 `data` key/空 DF（合法代码 vs 非法代码）分别断言。
5. **无网络约束**：单测必须走 mock（Testing.md 的 `no_network` 约定），`live` 用例只在显式环境变量 + `uv sync --extra live` 下运行，且从 `make verify-p1`/CI 排除。
6. **门禁用例自检**：新增 0005 后跑 `make migrate` + `make migrate-check` + `make verify-p1`；`stock_app` 角色单独跑一条读写用例（不要只用 owner 夹具，否则 GRANT 缺失测不出来——`backend/tests/unit/platform/conftest.py:24-38` 用的是 owner）。
7. **e2e**：验证后把 `.env` 改回 replay；断言 conftest 强制 replay。

---

## 8. 逐节勘误表（可直接采纳）

| 设计位置 | 现文 | 建议改为 |
|---|---|---|
| 头部「已确认的决策·复权口径」 | 前复权 qfq（TWR 标准做法） | 按 D1 裁定，并注明与 NFR-2/ADR-0009 的关系；删除「口径正确」未经证明的论证 |
| line 12「历史日线不变」 | 缓存理由 | 改为「**不复权/hfq 的日线历史不变**；qfq 时变，见 §10 事实」 |
| §1 line 24-27 | 两个实现类 | 补全 5 个 Port 成员（capabilities/provenance/get_latest_quotes/as_of）与各自语义 |
| §2 line 29-60 | 表结构 | 按 D1 决定是否加 `factor_units`/`base_date`/`checksum`；`pull_range` 加 `complete`、`pulled_at` 索引；DDL 与模型逐列一致 |
| §3 line 64-71 | 覆盖判定 | 按 D3 改「区间并集 + 尾部刷新 + TTL」；补 `ON CONFLICT` |
| §4 line 73-80 | akshare 调用 | 补 `timeout=`；补「列名中文、日期是 `date`、数值 float64、NaN 可能」；补 `to_thread` 内 lazy import |
| §4 line 84 | 仅 `\d{6}` | 改交易所前缀白名单 + 上市日期校验；不支持品种明确 `blocked_reason` |
| §4 line 85 | `int(Decimal(pct).scaleb(6))` | `Decimal(str(x))` + `ROUND_HALF_UP` + 超范围/NaN 拒绝；或由 close 派生 |
| §4 line 86 | `as_of = clock.now().date()` | 按 P1-2 改；Clock 构造注入 |
| §5 错误映射 | 四类 500/503/404 | 按 D2 裁定；若选 A 删除 `MARKET_DATA_UNAVAILABLE`，把「源不可用」写进 `blocked_reason` 分类 |
| §6 Settings | 4 个字段 | `market_live_timeout_seconds` 需真正传给 akshare；补并发上限/重试次数/TTL 字段 |
| §7 装配 | `session_factory` 方案 | 明确 app.py 改走工厂；`create_market_source` 建议上移到 api/platform（ledger 工厂不应知道 DB）；`db.session` 用局部变量 |
| 文件清单 | 缺项 | 补 `version.py`、`migrations/env.py`、`backend/tests/conftest.py`、`test_errors.py`、`test_market_replay.py`、`backend/packages/platform/pyproject.toml`、根 `pyproject.toml`、`uv.lock`、`infra/.env.example`、`docs/DATABASE.md`、`docs/architecture/v2/README.md`、ADR |
| 测试策略 line 176-178 | `@pytest.mark.live` | 补 marker 注册与门禁排除；补集成测试文件路径 |
| 风险表 line 205 | 「qfq 口径正确」 | 删除/改写；补「东财封 IP」「akshare import 秒级」「GET /nav 延迟」风险 |
| 非目标 line 216 | 复权因子失效 v1 不管 | 与永久缓存不兼容，必须移回范围（至少 TTL）或随 D1 一并解决 |
| e2e line 181-190 | 改 `.env` 为 live | 加「验证后改回 replay」；补 `uv lock`/网络/限流前置；第 8 步断言 `pull_range` 时同时断言 `bar_count>0` |

---

## 9. 修订后建议实施顺序与工作量

1. **人工裁定 D1~D3（+D4）** → 修订设计文档 + 必要时 ADR-0025（0.5d）。
2. **契约/装配/迁移骨架**：port 全成员 + capabilities/as_of/provenance、app.py 走工厂、0005（GRANT/SCHEMA_VERSION/env.py/downgrade）、conftest 清理 + 强制 replay（1~1.5d）。
3. **adapter**：白名单、真超时、重试上限、并发 semaphore、NaN/定点转换、错误映射（1d）。
4. **缓存**：覆盖并集/尾部刷新/TTL、并发 upsert、短事务（1d）。
5. **测试与门禁**：参数化契约测试、缓存/转换/错误用例、marker/mypy/extras、`make verify-p1`、`make typecheck`（1~1.5d）。
6. **e2e**：`uv sync --extra live` → migrate → live 验证 2021-10 → 恢复 replay → 文档同步（0.5d）。

合计约 **3.5~5 人日**（不含 D1 选 B 的工作量）。

---

## 10. 附：akshare 事实核验（2026-10-23，akshare-sme）

| 事实 | 结论 | 来源 |
|---|---|---|
| 签名/列名 | `stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="", timeout=None)`；中文列名与现稿一致；`日期` 是 `date`、数值列 float64（成交量文档写 int64 但实际 `to_numeric` 为 float） | [akshare 1.19.1 源码](https://github.com/akfamily/akshare/blob/release-v1.19.1/akshare/stock_feature/stock_hist_em.py#L952)、[官方文档](https://akshare.akfamily.xyz/data/stock/stock.html#id23) |
| 涨跌幅与 qfq 收盘 | **自洽**：东财 `fqt=1` 的涨跌幅按前复权序列计算；实测 000651 2021 全年 243 bars 偏差 >0.02pp 为 0（含 2021-08-23 除权日）。但 qfq 收盘仅 2 位小数，由 close 反推收益有 ±0.5% 量化误差；首根 bar 涨跌幅相对区间外前一日 | 同源接口实测 + [issue #7250](https://github.com/akfamily/akshare/issues/7250) |
| qfq 时变 | **官方确认**：前复权「每次除权除息都重新调整历史价格，历史价格是时变的」，持续分红公司可能出现负值；后复权「保证历史价格不变」 | [官方文档](https://akshare.akfamily.xyz/data/stock/stock.html#id23) |
| 非 A 股/未知代码 | 源码 `market_code = 1 if symbol.startswith("6") else 0`，**无白名单**；深市 159xxx/12xxxx/200xxx 会返回对应非 A 股数据（静默），900xxx 等市场码错配；不存在代码通常返回空 DF；网络异常为 `requests.Timeout/ConnectionError`（含 `RemoteDisconnected`）、`JSONDecodeError`、缺 `data` key 的 `KeyError` | 源码 + [issue #5751](https://github.com/akfamily/akshare/issues/5751) |
| 超时 | 1.16.72+ 原生 `timeout=` 传 `requests.get`（connect/read 超时，非总时长）；`to_thread` 的运行中线程**无法取消** | [requests 超时文档](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)、[Future.cancel](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel) |
| 封禁风险 | 东财按并发/频率封 IP 数小时（#6100），服务器部署易被识别（#7005 `RemoteDisconnected`） | [#6100](https://github.com/akfamily/akshare/issues/6100)、[#7005](https://github.com/akfamily/akshare/issues/7005) |
| 依赖 | akshare 1.19.1：`requires-python>=3.11`，依赖 pandas>=2.0.0、curl_cffi、lxml、html5lib、xlrd、openpyxl、mini-racer 等；下载量约 43MB（akshare 1.27 + pandas 11.2 + lxml 8.6 + curl_cffi 2.8 + mini-racer 18.5）；**无 py.typed**；PyPI 最早版本 1.16.72（`>=1.14` 无意义）；pandas 3.1.0 于 2026-10-22 发布，与 akshare 1.19.1 兼容未验证 | [PyPI 1.19.1](https://pypi.org/project/akshare/1.19.1/)、wheel 解包核验 |
| import 成本 | akshare 顶层 344 个 import（含 pandas），首次 import 秒级 → 不要在事件循环里直接 import | 源码 |
| 未确认项 | 不存在/退市/ETF/转债/B 股/北交所逐代码实测（评审期间东财限流 `RemoteDisconnected`）；pandas 3.1.0 × akshare 兼容；hfq 数据质量（#7250）。验证方法见任务记录 | — |

> 结论：现稿 §4 的调用方式（中文列名、qfq）在 API 层面可用，但必须在**超时、白名单、NaN 校验、空结果语义、并发限流**五处补强；§4 的「涨跌幅换算」数学正确，但「qfq 可做永久缓存」不成立。

---

## 11. 已核验为正确的设计点（避免误伤）

- `app.py`/worker 现状：worker 无任何行情依赖，设计「worker 不动」成立。
- `ErrorCode` 是运行时枚举，**不进入 OpenAPI schema**（`openapi.json` 的 `Error.code` 是正则字符串），仅新增错误码不会导致 `make api-snapshot --check` 漂移；但若改 `getNav` 的 `x-error-codes`/responses 则会漂移。
- 新增错误码需要三处同步（枚举 + CATALOG `_p` + §3 表）是正确结论，只是还漏了 `SEED_ERROR_COUNT` 与测试。
- 「不启用 RLS」不会被 G7 门禁拦截（门禁只扫 3 个 schema 且只查带 `user_id` 的表）。
- port 不变、复用 `Bar`/`Quote`、装饰器做缓存、lazy import 的大方向正确。

---

## 12. 人工裁定结果与后续（2026-10-23）

| 编号 | 裁定 | 落点（修订版 DevDesign） |
|---|---|---|
| D1 | **A**：v1 估值用不复权价；hfq/因子快照留后续期次 | [修订版 §2](../architecture/2026-10-22-live-market-data-source.md)（估值口径）+ ADR-0025（NFR-2 分阶段偏离） |
| D2 | **A**：不新增错误码，保持 degraded blocked 200；`blocked_reason` 分三类，并经 `NavPointOut.blocked_reason`（新增可选响应字段，非破坏）暴露 | 修订版 §6（错误映射）+ nav/schema 最小改动 |
| D3 | **A+B**：区间并集覆盖 + 尾部刷新 + TTL + 负缓存短 TTL | 修订版 §4（缓存查询策略） |
| D4 | **接受推荐**：白名单范围限制 + MVP 偏离登记 + `GET /nav` 保持同步 | 修订版「与需求基线的偏离」「非目标」 |

**后续期次待办（已在修订版登记）**：hfq + 复权因子 + `factor_version` + as-of 快照（NFR-2）；灾备源与熔断/限流（NFR-9、Reliability §8）；`GET /nav` 异步化与 `data_as_of` 语义修正（NFR-10、Reliability §7）；多源配置化（Extensibility EP-2）。

**本次落地补充**（评审未展开但实施时必须做）：`app.py` 改走工厂（原实现绕过工厂）、0005 迁移 GRANT + `SCHEMA_VERSION` bump、`migrations/env.py` 纳入 `market` schema、pytest `live` marker 注册并从门禁排除、mypy override、optional 依赖放 platform 包并在根转发、conftest 清 `market.*` 并强制 replay。
