# 接入 akshare 真实行情源（MARKET_MODE=live）

> 版本：v2（2026-10-23，按评审裁定修订）
> 评审报告：[2026-10-23-DevDesign-Review-live-market-data-source.md](../reviews/2026-10-23-DevDesign-Review-live-market-data-source.md)
> 人工裁定（2026-10-23）：D1=不复权估值；D2=不新增错误码、保持 degraded blocked 200；D3=区间并集覆盖 + 尾部刷新 + TTL；D4=接受推荐（范围限制 + MVP 偏离登记）。
>
> **2026-10-30 修订（读路径只读化）**：`MARKET_LIVE_CACHE_READ_ONLY=true`（默认）时，
> `CachedMarketDataSource.get_daily_bars` 只读本地 `market.daily_bar`，**不触发任何第三方请求**；
> 盘中不拉当天行情。行情更新改为收盘后显式 `make sync-market`（`sync_daily_bars`，只补本地
> 缺失区间；东八区 16:30 前自动截到昨天，留出收盘数据发布窗口）。`MARKET_LIVE_CACHE_READ_ONLY=false` 保留旧
> read-through（缺口/尾部即时拉取）仅用于调试。
> 实施状态：**已实现**（2026-10-23），记录见 [Period2-live-market-data-source-Implementation-Log.md](../reviews/Period2-live-market-data-source-Implementation-Log.md)。

## Context

**问题**：用户上传的真实账单（2021-10-29 ~ 2021-10-30，涉及 000651 格力电器、601688 华泰证券）在请求 `GET /nav?period=2021-10` 时被阻断。根因：当前唯一行情实现 `ReplayMarketDataSource` 只读 `fixtures/market/quotes.json`，覆盖 `000001/300750/600519/hs300_tr` 四个标的、2026-01-30 ~ 2026-09-30 九个月末 bar。账单的标的（000651/601688）与日期（2021-10）双重出界 → nav 层 `_load_prices` 批量失败、逐标重试也失败，2021-10-30/31 两日被判 `blocked`（`缺行情数据：000651,601688`）。

**预留扩展点**：`stock_ledger/market.py` 的 `create_market_source()` 已按 `settings.market_mode` 分发，`live` 分支当前 `raise NotImplementedError`。本次填入该分支。

**目标**：`MARKET_MODE=live` 时，账单 2021-10 的 NAV 查询能拉到 000651/601688 真实行情并正常返回；重复查询走缓存；连续月份只增量拉取。

**验收标准**

1. `uv sync --extra live` + `make migrate` + `.env` 设 `MARKET_MODE=live` 后，`GET /nav?period=2021-10` 的 `status` 不再因缺行情变 `blocked`，2021-10-29/30/31 均有 NAV 数据。
2. `market.pull_range` 出现 000651/601688 各 ≥1 行且 `bar_count > 0`；**已结束区间**（`end < 今天 − 尾部窗口`）的相同请求第二次不再调用 akshare；当月请求每次重拉尾部（预期）。
3. 连续查询 2021-10 → 2021-11：第二次只拉缺口/尾部，不整体重拉（日志 `fetch_range` 可证）。
4. 当月查询：尾部 N 个自然日每次重拉，新交易日不会因缓存而缺席。
5. 未安装 akshare 时 `MARKET_MODE=live` **启动即失败**（清晰提示 `uv sync --extra live`）；`replay` 模式不受影响。
6. `make verify-p1` / `make typecheck` / `make migrate-check` 全绿；错误码总数不变（47）。

**与需求基线的偏离（本期登记，后续期次补齐）**

| 基线 | 本期做法 | 后续 |
|---|---|---|
| PRD NFR-2：后复权 / as-of 因子快照 + 涨跌停/因子跳变校验 | v1 估值用**不复权**价（裁定 D1）；不做复权/因子校验 | 后续期次落 hfq + `factor_version` + as-of 因子快照（ADR-0025） |
| PRD NFR-9：可选灾备源 | 仅 akshare 单源 | 后续期次接灾备源 |
| Reliability §8：熔断 / 限流 / 独立线程池 | 仅：真超时 + 有上限重试 + 并发 semaphore（2） | 后续期次补熔断与统一出站治理 |
| Reliability §7：行情源故障时资产类 `stale` 可用 | 缓存命中优先；冷缓存 + 源故障 → blocked（带可重试原因） | 后续期次纳入降级矩阵 |
| Reliability §8：出站并发隔离 | v1 仅进程内 semaphore(2)；同 symbol/区间**不做 single-flight**（靠 upsert 幂等 + 低频请求） | 后续期次补 single-flight 与独立出站池 |
| NFR-2：证券主数据 PIT / 上市日期校验 | v1 不做上市日期校验（空结果统一 `no_data` + 负缓存短 TTL） | 接入证券主数据时补 |
| nav 事务边界 | nav 请求的外层事务在取数期间保持打开（缓存写用独立短事务） | 后续 NAV 异步化时把取数移出请求事务 |
| NAV `data_as_of` 语义 | 本期不改 `/nav`：`data_as_of` 仍为「计算时刻」 | 后续期次改取行情覆盖日 |

---

## 架构决策

### 1. port 不变，五个成员全部实现

`stock_platform/ports.py` 的 `MarketDataSource` Protocol 与 `Bar/Quote/MarketCapabilities` 不改。新增两个实现类，**五个成员全部实现**（否则 mypy strict 下 `AppState.market_data: MarketDataSource` 不成立）：

| 成员 | `AkshareMarketDataSource` | `CachedMarketDataSource`（装饰器） |
|---|---|---|
| `capabilities` | `mode="live"`、`symbols=()`、`supports_daily_bars=True`、`supports_latest_quote=True`、`supports_adjusted=False`、`max_lookback_days=None`。**`symbols=()` 表示「无静态标的清单」，调用方不得据此拒绝标的**（`supports_symbol` 对本源无意义） | 直接委托 `inner.capabilities` |
| `as_of` | 本进程**最近一次成功取数覆盖到的最后交易日**（内存记录）；尚无成功取数时返回注入 `Clock` 的 `today()`。仅为近似，权威覆盖由 `pull_range`/缓存判断 | **同步 property 不能查 DB**：维护内存 `_last_known_as_of`（每次缓存读/写后更新为已见到的最大 `trade_date`），未命中过时委托 `inner.as_of`；同属近似值 |
| `provenance` | `live:akshare@{version}`（version 取 `akshare.__version__`，import 后可得；uninstalled 情况由启动期拦截） | `f"{inner.provenance}#cache"` |
| `get_daily_bars` | 见 §5 | 见 §4 |
| `get_latest_quotes` | `get_daily_bars(symbols, clock.today() - 10d, clock.today())` 后取每 symbol 最后一条 bar；无 bar 的 symbol **不出现**在结果中（端口注释里写清） | 同上（走缓存路径） |

两个类都由构造注入 `Clock`（platform 惯例：`JobQueue`/`RateLimiter` 均构造收 Clock），不得内部 `SystemClock()`。

### 2. 估值口径：不复权（裁定 D1）

- **本期只落不复权日线**（akshare `adjust=""`），`market.daily_bar.adjust` 固定写 `''`；`capabilities.supports_adjusted=False`，与 replay 语义一致。
- 理由（详见评审 §2/D1）：
  1. NAV 公式 `现金 + Σ(数量 × 收盘价)` 需要**真实市价**；qfq 是相对最新价缩放的历史序列，会让总资产绝对值、MWR、多标的组合权重同时失真；qfq 还可能为负。
  2. 账单中股息/红利已作为 `category=dividend` 流水入现金，qfq 又把分红折入价格 → 除权日**重复计入**。
  3. akshare 官方确认 qfq 历史价格**时变**，与「永久缓存」不相容。
- `Settings.market_live_adjust` 类型为 `Literal[""]`：v1 只接受不复权，显式拒绝 `qfq/hfq`（避免误配）。hfq + 复权因子 + `factor_version` + as-of 留到后续期次，由 ADR-0025 记录。

### 3. 行情缓存表（公共数据，无 RLS）

新建 `market` schema 两表（不属于任何用户，所有租户共享；**不启用 RLS**，G7 门禁只扫 platform/ledger/review 且只查带 `user_id` 的表，不会被误伤）：

```sql
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE market.daily_bar (
    symbol        VARCHAR(16) NOT NULL,
    adjust        VARCHAR(8)  NOT NULL DEFAULT '',  -- v1 恒为 ''（不复权）
    trade_date    DATE        NOT NULL,
    close_units   BIGINT      NOT NULL,             -- scale=6，必须 > 0
    return_units  BIGINT      NOT NULL,             -- scale=8，由 close 序列派生（首根=0）
    source        VARCHAR(16) NOT NULL,             -- 'akshare'
    pulled_at     TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, adjust, trade_date)
);

CREATE TABLE market.pull_range (
    id              VARCHAR(40) PRIMARY KEY,  -- sha256(symbol|adjust|adjust_version|start|end)[:32]
    symbol          VARCHAR(16) NOT NULL,
    adjust          VARCHAR(8)  NOT NULL,
    adjust_version  VARCHAR(32) NOT NULL DEFAULT '',  -- 口径版本（为 hfq/factor 预留）
    range_start     DATE        NOT NULL,             -- 覆盖上界起点
    range_end       DATE        NOT NULL,             -- 覆盖上界终点（命中判定用，见下方语义）
    data_end        DATE        NULL,                 -- 上游实际返回的最后交易日（仅观测）
    requested_end   DATE        NOT NULL,             -- 发起该次拉取时的请求 end
    complete        BOOLEAN     NOT NULL DEFAULT TRUE,-- range_end >= requested_end
    source          VARCHAR(16) NOT NULL,
    pulled_at       TIMESTAMPTZ NOT NULL,
    bar_count       INTEGER     NOT NULL,             -- 0 = 该区间无行情（负缓存，短 TTL）
    checksum        VARCHAR(64) NOT NULL,             -- sha256(逐 bar 的 date:close_units)[:32]，留痕
    UNIQUE (symbol, adjust, adjust_version, range_start, range_end)
);

CREATE INDEX ix_pull_range_symbol_pulled ON market.pull_range (symbol, adjust, pulled_at);
```

**授权（必须，否则 `stock_app` 首次请求即 permission denied）**：

```sql
GRANT USAGE ON SCHEMA market TO stock_app, stock_backup;
GRANT SELECT, INSERT, UPDATE, DELETE ON market.daily_bar, market.pull_range TO stock_app;
GRANT SELECT ON market.daily_bar, market.pull_range TO stock_backup;
```

**语义补充**：

- `daily_bar` 是「按 (symbol, adjust, trade_date) 幂等 upsert」的缓存；主键顺序已服务「按标的 + 日期区间」扫描，不再另建索引；`pull_range` 只用于**覆盖判定**，不参与取数。
- **`range_end` 是覆盖判定上界，不是「上游最后交易日」**（避免周末/节假日导致同一历史区间每次重拉）：
  - `requested_end < clock.today()`（已结束区间）→ `range_end = requested_end`；非交易日无 bar 属正常，下次同区间直接命中。
  - `requested_end >= clock.today()`（含未来日期）→ `range_end = data_end`（只覆盖到实际数据）；尾部由 `tail_refetch_days` 刷新窗口兜底。
  - `complete = range_end >= requested_end`；`data_end` 仅记录上游实际最后交易日用于观测。
- 空数据负缓存：`data_end = NULL`、`range_end = max(range_start, min(requested_end, clock.today() - 1 day))`、`complete = false`，用短 TTL 自动过期（见 §4.7）。
- `adjust_version` 为后续 hfq/因子失效预留；v1 恒为 `''`，命中条件必须相等。
- `checksum`/`bar_count`/`pulled_at` 满足 NFR-2 的「来源留痕」最小要求（`source/fetched_at/as_of/checksum`）。
- `market` schema 是**可再生缓存**，不进备份/恢复演练的正确性断言（丢了可重拉）。
- **downgrade**：`DROP TABLE market.daily_bar` / `market.pull_range` → `DROP SCHEMA market` → 回 stamp `0004_period2_nav`。

### 4. 缓存查询策略（裁定 D3：区间并集 + 尾部刷新 + TTL）

`CachedMarketDataSource.get_daily_bars(symbols, start, end)`：

1. 参数校验（与 replay 一致）：`symbols` 非空、`start <= end`。
2. 对每个 symbol 独立执行 `_plan_fetch`（纯函数，可单测）：

```
reads = select pull_range where symbol=? and adjust='' and adjust_version=''
        and ( (bar_count > 0 and pulled_at >= clock.now() - ttl_days)          # 正缓存 TTL
           or (bar_count = 0 and pulled_at >= clock.now() - negative_ttl_days) )  # 负缓存短 TTL
merged = merge_intervals([(r.range_start, r.range_end) for r in reads])
tail_start = max(start, clock.today() - tail_refetch_days)   # 尾部保护区起点
# 只允许「尾部之外」的已覆盖区间参与命中；尾部 [tail_start, end] 永远算缺口
head = [(s, min(e, tail_start - 1 day)) for (s, e) in merged if s < tail_start]
gaps = subtract([start, end], head)         # 未覆盖缺口（末尾必然包含尾部窗口）
if not gaps:  → 只读缓存（无网络）
else:         → fetch_start = gaps[0].start；fetch_end = gaps[-1].end
               一次性拉 [fetch_start, fetch_end]（跨过已覆盖段，upsert 幂等）
```

3. 拉取成功后（短事务，见本节第 10 条）：`upsert daily_bar` + `upsert pull_range`，`pull_range` 按 §3 的覆盖语义写入：`range_start = fetch_start`、`requested_end = fetch_end`、`range_end`/`complete` 按「已结束区间 vs 含未来日期」分别取值、`data_end = 上游实际最后交易日`、`checksum = sha256(逐 bar 的 date:close_units)[:32]`。
4. 读缓存：`select ... where symbol in (...) and adjust='' and start <= trade_date <= end order by trade_date, symbol`，返回 `Bar` 序列（与 replay 同序）。
5. 未来 `end`（如当月月末）：不报错；只返回已有 bar。尾部保护区保证新交易日下次请求必被重拉。
6. 多 symbol：独立 plan；并发拉取受 `market_live_max_concurrency` semaphore 限制。
7. 空结果：
   - **支持范围内**的 symbol、区间内上游返回空 → 读缓存为空即可，**记录负缓存**（`bar_count=0`、`data_end=NULL`、`range_end=max(range_start, min(requested_end, today-1))`、`complete=false`）；负缓存 TTL 用 `market_live_negative_ttl_days`（默认 1 天）单独判定，避免把「未上市/停牌」永久固化成无数据，也避免每次请求都打上游。
   - 读缓存路径命中「负缓存已过期」→ 重新拉取。
   - **v1 不做上市日期校验**：无法区分「区间完全早于上市」与「停牌/上游临时空」，统一按 `no_data` + 负缓存短 TTL 处理（后续接证券主数据时补上市日期校验）。
8. **未来 start**：`start > clock.today()`（如 `period=2027-01`，`routers/nav.py` 不校验未来月）时不发起拉取、直接返回空序列（nav 判 `no_data`），避免生成 `range_end < range_start` 的非法负缓存区间。
9. 不做跨区间合并的**取数优化**（gaps 合并成一次请求即可），不引入增量列/分区。
10. **事务边界**：adapter **自身不持有 DB 事务**——网络调用不借用调用方会话，缓存读写各自用 `session_factory` 打开独立短事务（upsert 两表后立即提交）。注意：nav 请求的**外层事务**在取数期间仍然打开（已知偏离，受 `market_live_max_concurrency=2` 及低频请求约束，后续 NAV 异步化时把取数移出请求事务，见「与需求基线的偏离」）。

`pull_range` 写入必须 `ON CONFLICT (symbol, adjust, adjust_version, range_start, range_end) DO UPDATE SET pulled_at, requested_end, complete, bar_count, checksum, data_end`，并发双击不会 `IntegrityError`。负缓存（`bar_count=0`）的 `range_end = max(range_start, min(requested_end, clock.today() - 1 day))`、`complete = false`、`data_end = NULL`，短 TTL 过期后自动重拉（`max(range_start, …)` 保证区间合法）。

### 5. akshare 调用约定

**调用方式**（lazy import + 真超时 + 线程隔离）：

```python
def _fetch_units(self, symbol: str, start: date, end: date) -> list[tuple[date, int, int]]:
    ak = _import_akshare()          # 线程内的 lazy import（模块顶层不 import akshare/pandas）
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        adjust="", timeout=self._timeout_seconds,   # akshare 1.16.72+ 原生支持 timeout
    )
    return _parse_rows(df.to_dict("records"))  # 纯函数：行字典 → [(trade_date, close_units, return_units)]

units = await asyncio.to_thread(self._fetch_units, symbol, start, end)
```

- **模块顶层禁止 `import akshare`/`import pandas`**：`stock_ledger.market` 会 import `stock_platform.market_live`，无 extra 环境下 replay 模式必须仍可 import；重 import 放在线程内（import 秒级，不能阻塞事件循环）。
- **启动期 fail-fast**：`create_market_source` 的 live 分支用 `importlib.util.find_spec("akshare")` 检查（不触发重 import），缺失则 `RuntimeError` 提示 `uv sync --extra live`；不做运行期 500。
- **超时**：`market_live_timeout_seconds`（默认 10s）**传给 akshare**（`requests` 的 connect/read 超时，非总时长）；`asyncio.wait_for` 不作为取消手段（线程无法取消），只用于外层观测/告警日志。
- **重试**：总尝试次数 `market_live_retry_attempts`（默认 2），间隔 `market_live_retry_backoff_seconds * 2**attempt`，仅对瞬时类异常；不要对「明确不支持/空结果」重试。东财按并发封 IP（issue #6100/#7005），重试必须克制。
- **并发**：`asyncio.Semaphore(market_live_max_concurrency)`（默认 2）包住每次 `to_thread`；同 symbol/区间不做单飞（v1 靠 upsert 幂等 + 低频请求）。
- **列名与类型**（akshare 1.19.1 实测）：中文列名 `日期/股票代码/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率`；`日期` 是 `datetime.date`；数值列是 **float64**（成交量文档写 int64，实际 `to_numeric` 后为 float）。只取 `日期`、`收盘` 两列，其余忽略。`_parse_rows` 只接收 `Sequence[Mapping[str, Any]]`（线程内 `df.to_dict("records")` 转换），因此单测不需要 pandas/akshare。
- **定点转换（禁止 float 进入内部）**：`close_units = int((Decimal(str(x)) * 10**6).to_integral_value(ROUND_HALF_UP))`；`NaN/None/非有限值/close<=0/超出 BigInteger` 一律 fail-closed（`INTERNAL_ERROR`）。akshare 用 `pd.to_numeric(errors="coerce")` 把坏值变 NaN 且不抛，**适配器必须显式校验**。
- **`return_units` 由 close 序列派生**：`ret = close_i/close_{i-1} - 1`，scale=8、`ROUND_HALF_UP`；区间首根 bar 的 `return_units=0`。理由：与 `close_units` 严格自洽；当前 NAV 不使用该字段（`review/application/nav.py` 只用 `close_units`，收益由 NAV 序列自算）。
- **标的白名单（替换 `\d{6}`）**：仅 A 股主板/科创板/创业板：

  ```python
  _A_SHARE_PREFIXES = ("600", "601", "603", "605", "688", "689",   # 沪
                       "000", "001", "002", "003", "300", "301")   # 深
  # 必须 6 位数字且前 3 位在白名单；其余 → ApiError(NOT_FOUND, metadata={"market_failure": "not_supported"})
  ```

  明确不支持（v1）：ETF/LOF（51x/56x/58x/159x）、可转债（11x/12x）、B 股（900/200）、北交所（4x/8x/920）、港股/美股。akshare 的 `market_code = 1 if symbol.startswith("6") else 0` **没有白名单**，深市非 A 股会静默返回对应品种数据，必须由本适配器拦截。v1 **不做上市日期校验**（见 §4.7）。
- **`as_of`**：`self._last_bar_date or clock.today()`；每次成功取数后更新 `_last_bar_date = max(上游 bar 日期)`（同步 property，不查 DB/网络）。
- **可观测性**：结构化日志 `market.fetch`（`symbol/range/bars/latency_ms/attempt/cache_hit/result`，不含敏感数据）；Prometheus 指标（platform 已依赖 `prometheus-client`，风格同 `obs.py` 的 `HTTP_*`）：
  - `sa_market_fetch_total{result="ok|error|empty"}`、`sa_market_fetch_seconds`（Histogram）
  - `sa_market_cache_hits_total{cache="hit|miss"}`、`sa_market_fetch_errors_total{reason="unavailable|invalid_data"}`
  - 对应 Reliability FM-17 的 `market_fetch_errors` 指标。

### 6. 错误映射（裁定 D2：不新增 ErrorCode，保持 degraded blocked）

**不新增 `MARKET_DATA_UNAVAILABLE`**，不改 `ErrorCode`/API-Catalog §3 错误码表。适配器抛既有错误码，用 `ApiError.metadata["market_failure"]` 携带失败类别，供 nav 生成可读的 blocked 原因：

| 场景 | 抛出 | `metadata.market_failure` | 说明 |
|---|---|---|---|
| akshare 未安装 | 启动期 `RuntimeError`（部署错误） | — | 运行期兜底 `INTERNAL_ERROR` |
| 标的不在支持范围 / 非法格式 | `NOT_FOUND` 404 | `not_supported` | detail 写明范围（仅 A 股主板/科创/创业） |
| 网络超时 / 连接失败 / HTTP 5xx / JSON 解析失败 / 缺 `data` key | `INTERNAL_ERROR` 500（retryable） | `unavailable` | 重试耗尽后才抛 |
| 合法 A 股 + 区间内空数据 | 返回空序列（不抛），写负缓存 | —（无异常） | nav 侧通过「请求了但无 bar」判定为 `no_data` |
| 缺列 / NaN / 非数值 / close<=0 / 超标度 | `INTERNAL_ERROR` 500 | `invalid_data` | fail-closed，不静默修数据 |
| 缓存 DB 写失败 | 向上抛原始异常（非 `ApiError`） | — | DB 故障不掩盖；请求 500 |

**nav 侧最小改动（降级语义不变；唯一契约改动是下方「用户可见性」的可选响应字段）**：`review/application/nav.py` 的 `_load_prices` 返回 `(ladder, causes)`：
- 异常路径：从 `ApiError.metadata["market_failure"]` 取类别；
- 成功但该标的区间内没有任何 bar：记为 `no_data`（不需要适配器造 404）。

`blocked_reason` 在保留 `缺行情数据：<symbols>` 前缀的前提下追加类别：

- `unavailable` → `（行情源暂时不可用，可重试）`
- `not_supported` → `（标的不在支持范围，仅支持 A 股主板/科创/创业）`
- `no_data` → `（该区间无行情，可能未上市/停牌）`

**用户可见性（非破坏新增字段）**：`NavPointOut` 增加可选字段 `blocked_reason: str | None = None`（仅 blocked 日有值，取自 `review.nav_daily.blocked_reason`）。按 API-Catalog §7 规则「新增可选响应字段」属**非破坏**变更：

- 需 `make api-snapshot` 重生成 `contracts/openapi.json`、`make sdk`（前端由生成类型承接，无手写改动）、API-Catalog §2.2 补字段说明；
- **不新增错误码**、不改 HTTP 状态、不改 `x-error-codes`、不改 `blocked_ranges` 语义；
- 现有 API 测试只断言 `blocked`/`blocked_ranges`，新增字段默认 `null`，不破坏既有断言。

### 7. Settings 新增字段

```python
# 外部依赖 ── 行情 live 模式
market_live_source: Literal["akshare"] = Field("akshare", alias="MARKET_LIVE_SOURCE")
market_live_adjust: Literal[""] = Field("", alias="MARKET_LIVE_ADJUST")   # v1 仅不复权
market_live_timeout_seconds: float = Field(10.0, alias="MARKET_LIVE_TIMEOUT_SECONDS")
market_live_cache_enabled: bool = Field(True, alias="MARKET_LIVE_CACHE_ENABLED")
market_live_cache_ttl_days: int = Field(30, alias="MARKET_LIVE_CACHE_TTL_DAYS")
market_live_cache_read_only: bool = Field(True, alias="MARKET_LIVE_CACHE_READ_ONLY")
market_live_negative_ttl_days: int = Field(1, alias="MARKET_LIVE_NEGATIVE_TTL_DAYS")
market_live_tail_refetch_days: int = Field(5, alias="MARKET_LIVE_TAIL_REFETCH_DAYS")
market_live_max_concurrency: int = Field(2, alias="MARKET_LIVE_MAX_CONCURRENCY")
market_live_retry_attempts: int = Field(2, alias="MARKET_LIVE_RETRY_ATTEMPTS")
market_live_retry_backoff_seconds: float = Field(1.5, alias="MARKET_LIVE_RETRY_BACKOFF_SECONDS")
```

- `MARKET_MODE` 已有的 `Literal["replay", "live"]` 不动。
- `market_live_source` 单值 Literal 是「本期仅 akshare」的显式声明；多源（Extensibility EP-2 的 `config/market.yaml` + 工厂字典）留后续期次。
- 缓存开关默认开；关闭则 `CachedMarketDataSource` 不装配，退化为纯 akshare（调试用）。
- 以上 **11 个**新变量需同步 `infra/.env.example`。

### 8. 装配改造

`stock_ledger/market.py` 的 `create_market_source` 签名改为：

```python
def create_market_source(
    settings: Settings,
    *,
    session_factory: SessionFactory | None = None,
    clock: Clock | None = None,          # None → SystemClock()（仅装配层允许）
) -> MarketDataSource: ...
```

`SessionFactory` 定义在 `stock_platform/market_live.py`（Protocol：`Callable[..., AbstractAsyncContextManager[AsyncSession]]`，即 `Database.session` 的形状）。

| 组合 | 行为 |
|---|---|
| `replay` | `ReplayMarketDataSource(fixtures_dir/market/quotes.json)`，忽略 `session_factory`（保持现有惰性加载） |
| `live` + `cache_enabled=True` | 必须传 `session_factory`，否则 `ValueError`；返回 `CachedMarketDataSource(inner=AkshareMarketDataSource(settings, clock), session_factory=session_factory, clock=clock)` |
| `live` + `cache_enabled=False` | 返回 `AkshareMarketDataSource(settings, clock)`；`session_factory` 可为 None |
| `live` 且 akshare 未安装 | 启动期 `RuntimeError`（`uv sync --extra live`） |

**工厂留在 ledger 的原因**：`ReplayMarketDataSource` 在 ledger（读 fixtures 属于该层现有职责），而 platform 不能依赖 ledger（L3）；因此装配函数只能放在 ledger（L4 允许依赖 platform）或 api 顶层。为最小改动保留在 ledger，live 实现放 platform；**ledger 不直接读写缓存表**，只把 `session_factory` 透传。

`stock_api/app.py` 的 `build_state` **改为走工厂**（当前直接 new `ReplayMarketDataSource`，live 分支永远不会生效）：

```python
db = Database(settings, role="app")
market_data = create_market_source(settings, session_factory=db.session, clock=resolved_clock)
return AppState(..., db=db, market_data=market_data, ...)
```

- 用 `build_state` 内的局部 `db`，不要写 `state.db.session`（构造期间 `state` 尚不存在）。
- 删除 `app.py` 顶层的 `from stock_ledger.market import ReplayMarketDataSource`。
- `clock` 从 `build_state` 的 `resolved_clock` 传入（不要在生产路径用 `SystemClock()`）。
- worker 不消费行情（grep 无引用），**不动**。

### 9. 分层与文件位置

- 新增实现：`backend/packages/platform/src/stock_platform/market_live.py`（`AkshareMarketDataSource` + `CachedMarketDataSource` + `SessionFactory` + 纯函数 `_parse_rows`/`_plan_fetch`）、`market_models.py`（SQLAlchemy 模型，复用 `TimestampMixin`；`String(40)` 主键、`String(n)` 字符串列与 §3 DDL 的 `VARCHAR(n)` **逐列一致**，并在 `__table_args__` 中声明 §3 的两条索引 `ix_daily_bar_symbol_date` / `ix_pull_range_symbol_pulled`，确保 `alembic check` 无索引/类型漂移）。
- 分层：platform 只依赖 contracts/core ✅；ledger → platform ✅；api 顶层装配 ✅（import-linter L1–L5 不新增边）。
- 行情是跨用户公共基础设施，放 platform 语义最准；与 Extensibility EP-2 的 `review/infra/marketdata/` 路径偏离，记录在 ADR-0025。

---

## 文件清单

### 新增

| 文件 | 内容 |
|---|---|
| `backend/packages/platform/src/stock_platform/market_models.py` | `DailyBar` / `PullRange`（schema=`market`，复用 `TimestampMixin`） |
| `backend/packages/platform/src/stock_platform/market_live.py` | `AkshareMarketDataSource`、`CachedMarketDataSource`、`SessionFactory`、`validate_symbols`、`plan_fetch`、`_parse_rows` |
| `backend/migrations/versions/0005_market_cache.py` | 建 `market` schema + 两表 + 索引 + **GRANT** + `schema_version='0005_market_cache'`；downgrade 回 `0004_period2_nav` |
| `backend/tests/unit/platform/test_market_live.py` | 适配器单测（mock `_import_akshare`，不联网） |
| `backend/tests/unit/platform/test_market_cache.py` | 覆盖/尾部/TTL/并发/负缓存单测（纯函数 + 真实 DB）；`plan_fetch` 纯函数单测 |
| `backend/tests/contract/test_market_source.py` | Port 契约参数化测试（Replay/Akshare/Cached 同一套断言） |
| `backend/tests/integration/test_market_live_e2e.py` | `@pytest.mark.live` 集成用例（联网 + `uv sync --extra live`，不进默认门禁） |

### 修改

| 文件 | 改动 |
|---|---|
| `backend/packages/platform/src/stock_platform/config.py` | 加 10 个 `market_live_*` 字段（§7） |
| `backend/packages/platform/src/stock_platform/obs.py` | 行情指标：`sa_market_fetch_total`/`_seconds`/`_errors_total`、`sa_market_cache_hits_total`（Reliability FM-17） |
| `backend/packages/review/src/stock_review/models.py` | 修历史漂移：`NavDaily` 改为 `UniqueConstraint + 普通索引`，与迁移 0004 一致（使 `make migrate-check` 通过） |
| `backend/packages/ledger/src/stock_ledger/market.py` | `create_market_source` 填 live 分支 + `session_factory` 参数 + 启动期 `find_spec` 检查 |
| `backend/services/api/src/stock_api/app.py` | `build_state` 改走工厂，传 `db.session` 与 `resolved_clock` |
| `backend/services/api/src/stock_api/version.py` | `SCHEMA_VERSION = "0005_market_cache"` |
| `backend/migrations/env.py` | `OWNED_SCHEMAS` 加 `"market"`；import `stock_platform.market_models` |
| `backend/packages/review/src/stock_review/application/nav.py` | `_load_prices` 返回 `(ladder, causes)`；`blocked_reason` 追加类别后缀（§6） |
| `backend/services/api/src/stock_api/schemas.py` | `NavPointOut` 增可选字段 `blocked_reason: str \| None = None`（§6） |
| `backend/services/api/src/stock_api/routers/nav.py` | 从 `nav_daily` 行填充 `blocked_reason` |
| `contracts/openapi.json` | `make api-snapshot` 重生成（新增可选响应字段） |
| `docs/architecture/v2/API-Catalog.md` | §2.2 nav 契约补 `blocked_reason` 可选字段说明（§3 错误码表不动） |
| `backend/tests/conftest.py` | `_TRUNCATE_SQL` 加 `market.daily_bar`/`market.pull_range`；`settings` 夹具强制 `market_mode="replay"`（防止 `.env` 改 live 污染测试） |
| `backend/tests/unit/ledger/test_market_replay.py` | 改写 live 工厂用例（`ValueError`/`cache_enabled=False`/replay 惰性） |
| `backend/tests/unit/review/test_nav_application.py` | 加「blocked 原因带类别」用例 |
| `backend/tests/api/test_api_032_nav.py` | 加 `NavPointOut.blocked_reason` 契约用例（blocked 日有值 / 非 blocked 为 null） |
| `frontend/packages/api-client/src/api/generated/schema.d.ts` | `make sdk` 自动生成（新增可选字段，不手改） |
| `backend/packages/platform/pyproject.toml` | `[project.optional-dependencies] live = ["akshare>=1.16.72,<1.20"]`（建议 pin `==1.19.1`；给 pandas 上限） |
| 根 `pyproject.toml` | `[project.optional-dependencies] live = ["stock-platform[live]"]`；pytest markers 加 `"live"`；mypy override `module=["akshare","akshare.*","pandas","pandas.*"] ignore_missing_imports=true` |
| `uv.lock` | `uv lock` 重新生成（新增 akshare/pandas 等） |
| `Makefile` + `backend/services/api/src/stock_api/cli.py` | 所有 pytest 门禁统一排除 live：`-m "not integration and not api and not live"`，且 **按路径收集 `backend/tests/integration` 的目标（如 `test-p2`）也加 `-m "not live"`** |
| `infra/.env.example` | 新增 10 个 `MARKET_LIVE_*` |
| `docs/DATABASE.md` | schema 布局表加 `market`（公共缓存）+ 迁移记录 + TRUNCATE 清单 |
| `docs/architecture/v2/README.md` | 「三 schema」→「三业务 schema + 1 公共缓存 schema」；「36 张表」→「36 业务表 + 2 公共缓存表」；「ADR 12 条」→「13 条」 |
| `docs/Architecture-v2.md` | §9 数据表清单加 `market` 两表；修正 §8 端口位置（line 474 仍写 `review/domain/ports.py`，实际 `stock_platform/ports.py`） |
| `docs/architecture/v2/ADR.md` | 追加 ADR-0025：行情估值口径（不复权）+ 缓存失效策略 + NFR-2 分阶段偏离；同步索引表 |
| `docs/reviews/2026-10-23-DevDesign-Review-live-market-data-source.md` | 追加 §12 裁定结果（已存在，本次已更新） |

### 不动

- `GET /nav` 的 HTTP 状态、`x-error-codes`、`blocked_ranges` 语义（仅新增可选响应字段 `blocked_reason`，按 §7 属非破坏变更）。
- `backend/packages/contracts/src/stock_contracts/errors.py`、`API-Catalog §3` 错误码表、`SEED_ERROR_COUNT`（不新增错误码）。
- `ReplayMarketDataSource`、`fixtures/market/quotes.json`、worker、`market_mode` 字段。
- `frontend` 手写代码（新增字段为可选，SDK 由 `make sdk` 生成，无需手写改动）。

---

## 测试策略

### 单元测试（pytest，必须全绿，不联网）

`test_market_live.py`：

- `_parse_rows`：正常 DataFrame → 标度整数；NaN/None/非有限/close<=0/超标度 → `INTERNAL_ERROR`；`return_units` 由 close 派生（首根=0）+ ROUND_HALF_UP。
- symbol 白名单：600/601/603/605/688/689/000/001/002/003/300/301 通过；51x/159x/11x/12x/900/200/4x/8x/920/非 6 位 → `NOT_FOUND` + `market_failure="not_supported"`。
- akshare 未安装：`_import_akshare` 抛 `RuntimeError`/`ImportError` → `INTERNAL_ERROR`（启动期另有 `find_spec` 用例）。
- 网络异常：mock `_fetch_units` 抛 `Timeout`/`ConnectionError` → 重试 N 次后 `INTERNAL_ERROR` + `unavailable`；断言重试次数与 backoff。
- 空结果 → 返回空序列（不抛），并触发负缓存写入（cache 层用例断言）。
- `timeout=` 被传入 akshare（mock 断言 kwargs）。
- `as_of` 取最近成功 bar 日期（同步 property，不触发网络/DB）；`get_latest_quotes` 取最后 bar、无 bar 的 symbol 不出现。
- 可观测性：拉取成功/失败/空结果各产生一条结构化日志与对应指标（用 `capsys`/`prometheus_client` 读取断言）。

`test_market_cache.py`：

- `_plan_fetch` 纯函数：全命中（无网络）；连续月区间（只有缺口）；**尾部窗口恒为缺口且 `fetch_end == 请求 end`**（构造 `today/tail_refetch_days` 断言，防止伪代码回归）；已结束区间（`end < today − tail`）二次请求无网络；覆盖上界用 `requested_end`（周末/非交易日不再重拉）；TTL 过期；负缓存 TTL；多 symbol 独立。
- `CachedMarketDataSource.as_of`：内存近似值随读/写更新，未命中时委托 inner（同步、不查 DB）。
- 真实 DB（`Database(role="app")`）：upsert 幂等、`pull_range` `ON CONFLICT`、并发双请求不炸、`bar_count=0` 负缓存、`checksum` 落库。
- `cache_enabled=False` → 纯透传不写缓存（装配层用例）。
- 至少一条用例用 **app 角色** 验证 GRANT（owner 夹具会掩盖权限缺失）。

`test_market_source.py`（契约参数化）：三个实现跑同一套断言（capabilities 字段、`symbols` 语义、`start>end` → `VALIDATION_ERROR`、空 symbols → `VALIDATION_ERROR`、`as_of` 类型、`provenance` 非空、`get_latest_quotes` 返回 `Quote`）。

`test_market_replay.py`：live 无 `session_factory` → `ValueError`；`cache_enabled=False` → `AkshareMarketDataSource`；replay 仍惰性、忽略 `session_factory`。

`test_nav_application.py` / `test_api_032_nav.py`：

- market source 抛带 `metadata.market_failure` 的 `ApiError` 时，blocked 原因含对应后缀，状态仍 200 blocked。
- `NavPointOut.blocked_reason`：blocked 日有值、非 blocked 日为 `null`（API 契约用例）。

### 集成测试（手动，不进 CI 门禁）

`@pytest.mark.live`（marker 已注册、门禁已排除），需 `uv sync --extra live` + 网络：

- 用例**自建** `Settings(market_mode="live")`（不依赖根 `.env`；conftest 的 `settings` 夹具固定 replay），`pytest.importorskip("akshare")` + 显式环境变量开关双重跳过。
- 拉真实 000651 在 2021-10 的日线：返回 Bar 数 >0、首日日期、`close_units` 落合理区间、`return_units` 与 close 序列自洽。
- 东财封禁在服务器 IP 上可能出现 → 用例失败只告警，不阻塞门禁（手动执行）。

### 端到端验证（手动）

1. `uv sync --extra live`（会更新 `uv.lock`）
2. `make migrate`（执行 0005）→ `make migrate-check` 无漂移；`alembic current` 为 `0005_market_cache`
3. `.env` 改 `MARKET_MODE=live`（10 个新变量可选）
4. 重启 API：`cd backend && uv run python -m stock_api.main`
5. 浏览器登录 demo 账号，导入用户的 xlsx 账单
6. 前端「净值」页选 2021-10 → 点「构建净值与收益」
7. **预期**：不再出现 `缺行情数据：000651,601688`；2021-10-29/30/31 均有 NAV 数据；`review.nav_daily` 落库；若仍 blocked，`blocked_reason` 带三类后缀之一（可用 `SELECT blocked_reason FROM review.nav_daily WHERE status='blocked'` 核对）
8. 数据库验证：`SELECT symbol, range_start, range_end, bar_count, complete FROM market.pull_range;` → 000651/601688 各 ≥1 行且 `bar_count>0`
9. 再点一次「构建」（已结束区间）→ 走纯缓存（日志无 akshare 拉取）
10. 切到 2021-11 构建 → 日志只出现缺口/尾部拉取，不整体重拉
11. 切到当月（2026-10）构建两次 → 第二次仍重拉尾部 5 天（预期行为）
12. **验证完把 `.env` 改回 `MARKET_MODE=replay`**（否则后续 `make verify-p1` 的 API 测试会走 live；conftest 已强制 replay 兜底）
13. `market.pull_range.checksum` 非空、`pulled_at` 递增

### 门禁

- `make verify-p1`：ruff + pytest（`not live`）+ api-check + 架构约束 + 快照一致（**已按新增可选字段重生成**）+ 前端 tsc
- `make typecheck`：mypy strict + import-linter
- `make migrate-check`：alembic 无漂移（market 已纳入 `OWNED_SCHEMAS`；DDL 与模型逐列一致）
- `make sdk`：生成物包含 `NavPointOut.blocked_reason`，无手写前端改动

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 东财按并发/频率封 IP 数小时（issue #6100/#7005，服务器 IP 易被识别） | 每进程并发上限 2 + semaphore；重试 ≤2 次有退避；`timeout=10s`；失败时缓存命中优先；后续期次补熔断 |
| 首次拉历史区间慢（跨 5 年） | DB 缓存 + 覆盖并集：连续月份只拉缺口；单次请求区间合并为 1 次拉取 |
| akshare 接口变动（东财改版） | lazy import + 失败 `INTERNAL_ERROR`（fail-closed）；`market_live_source` 预留多源；版本 pin `1.16.72~1.20` |
| qfq 时变/负价 | **本期不用复权价**（D1=A）；`market_live_adjust` 只接受 `""` |
| 上游返回脏数据（NaN/坏列/负价） | `_parse_rows` 显式校验并 fail-closed；`checksum` 留痕 |
| 上游临时返回空被永久固化 | 负缓存 + 短 TTL（默认 1 天）+ `complete` 标记 |
| 缓存对含未来日期的请求过度覆盖 | 覆盖上界只取 `data_end`（不含未来日期）+ 尾部保护区每次重拉 + `complete` 标记；TTL 30 天兜底刷新 |
| 同步 akshare 阻塞事件循环 | `asyncio.to_thread` 隔离 + timeout 传上游 + 并发 semaphore；import 在子线程 |
| `GET /nav` 首次冷缓存延迟（NFR-10 读 p95 ≤300ms 被打破） | 已知偏离，登记后续期次（NAV 异步化）；单请求内多 symbol 并发拉取 + 缓存命中优先 |
| 缓存表无 RLS 被越权读 | 行情是公开数据；写路径只允许内部装配（不开放 HTTP 端点）；`stock_app` 仅经迁移 GRANT DML |
| 缓存 DB 写失败 | 向上抛（请求 500），不静默降级；缓存可在下次请求重建 |
| nav 外层事务在取数期间保持打开 | 缓存写用独立短事务；进程并发上限 2；登记后续 NAV 异步化解决（见偏离表） |

---

## 非目标（Out of Scope）

- hfq/qfq 复权序列、复权因子/as-of 快照、`factor_version` 失效（后续期次，ADR-0025 记录）
- 港股/美股/基金/ETF/可转债/B 股/北交所行情（v1 白名单拦截并给明确原因）
- 分钟级/实时行情（`get_latest_quotes` 基于日线 last）
- 熔断 / 限流 / 灾备源 / 独立出站线程池的完整治理（后续期次）
- `GET /nav` 异步化与 `data_as_of` 语义修正（后续期次）
- 跨区间取数合并优化、行情数据分区/清理策略
- 上市日期校验/证券主数据 PIT、同 symbol/区间 single-flight（后续期次）
- worker 侧使用 live 行情（worker 当前无 NAV 计算场景）

---

## 决策记录

| 日期 | 决策 | 依据 |
|---|---|---|
| 2026-10-23 | D1=A：v1 估值用不复权；hfq/因子留后续期次 | NAV 市值需真实市价；股息重复计入；qfq 时变与永久缓存不相容；与 PRD NFR-2 的偏离由 ADR-0025 记录 |
| 2026-10-23 | D2=A：不新增错误码，保持 degraded blocked 200；`blocked_reason` 分三类，经 `NavPointOut.blocked_reason`（新增可选字段，非破坏）暴露 | `/nav` 消费方吞 ApiError，503 是死码；唯一契约改动是可选响应字段 |
| 2026-10-23 | D3=A+B：区间并集覆盖 + 尾部刷新 + TTL + 负缓存短 TTL | 连续月份可命中、当月不漏新 bar、上游修正可自愈 |
| 2026-10-23 | D4：接受 v1 范围限制与 MVP 偏离（无灾备/熔断，`GET /nav` 延迟） | 目标聚焦「2021-10 NAV 出数」；偏离登记后后续期次补齐 |
