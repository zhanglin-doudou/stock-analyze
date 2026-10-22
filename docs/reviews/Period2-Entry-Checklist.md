# 二期（Period2）准入检查单

> 版本：2026-10-22 ｜ 依据：[DEV-PLAN-v2.md](../DEV-PLAN-v2.md) §3、[API-Catalog.md](../architecture/v2/API-Catalog.md)、[Period1-CodeReview-and-Acceptance.md](Period1-CodeReview-and-Acceptance.md)、[Period1-Fix-Log-P0-2-P0-3.md](Period1-Fix-Log-P0-2-P0-3.md)、[Period1-Fix-Log-P0-1-P0-4-P0-5.md](Period1-Fix-Log-P0-1-P0-4-P0-5.md)
> 用途：判断"一期是否可退出、二期是否可进入"，以及二期启动前必须补齐的依赖项。**每次状态变化请更新本表。**

## 0. 当前结论

**有条件通过（Conditional Go，G2/G3/G4 已关闭）；Period2-01/02 已完成（2026-10-22）**：

- 5 项 P0 已全部修复并复验（数据口径/去重/权限/撤销 UI/验收门禁与契约）；
- **G1~G7 全部关闭**：G1 数据基线、G2 队列语义、G3 分类闭环、G4 replay 行情、G5 seed/demo、G6 SDK/覆盖门禁、G7 platform RLS 均已完成并验证（见 [G2-G4](Period2-Hardening-Fix-Log-G2-G3-G4.md)、[G5-G1](Period2-Hardening-Fix-Log-G5-G1.md)、[G6-G7](Period2-Hardening-Fix-Log-G6-G7.md)）。
- **Period2-01（core/fifo 纯引擎 + 独立 oracle）与 Period2-02（review.round/round_fill、rebuild-rounds job、脏窗口重建、GET /rounds/*
、规则 CRUD、reclassify、bridge）已完成**：后端全量 `pytest` 590 passed；`stock_core` 覆盖率 100%；受保护端点 33/33、跨租户资源 17/17、RLS 14/14（review 3/3）全绿。证据见 [Period2-01/02 实施日志](Period2-01-02-Implementation-Log.md)。
- `verify-p1` 已扩为 18 步全绿（含 API 验收、越权、RLS、契约快照、前端 tsc/vitest/build）；
- 剩余 P1 中构成后续二期任务硬依赖的项已逐条在矩阵标注；下一批可启动 Period2-03（NAV）与 Period2-04（行情）。

**Go / No-Go 矩阵：**

| 二期任务 | 现在能否开始 | 前置条件 |
|---|---|---|
| Period2-01 FIFO 回合引擎（core 纯函数 + oracle） | ✅ **已完成（2026-10-22）** | 无（P0-1 口径已修） |
| Period2-02 回合落库 / rebuild-rounds / 规则 CRUD | ✅ **已完成（2026-10-22）** | G2 ✅、G7 ✅ |
| Period2-03 净值与收益（NAV/TWR/XIRR） | ⚠️ Period2-02 后 | G3 ✅（分类/现金流识别）、Period2-02 |
| Period2-04 行情适配与校验（replay/live） | ✅ 可开始 | G4 ✅（replay 适配器 + 确定性夹具；live 留 Period2-04） |
| Period2-05 指标注册表与执行引擎 | ⚠️ Period2-03/04 后 | G6 ✅；P2-03/04 完成 |
| Period2-06~Period2-13 端点/前端/报告/导出 | ⚠️ 随 Period2-05 | G6、前端 token/eslint 收口 |
| Period2-14 二期越权全量 / `make verify-p2` | ⚠️ 随端点增长 | G6 ✅（门禁已有，新端点自动纳入） |
| Period2-15 前端零口径门禁 | ⚠️ 先有 eslint | eslint 基础设施（P1-15 残余） |

---

## 1. 一期退出检查（P0/P1 状态）

### 1.1 P0（全部已关闭）

| P0 | 状态 | 证据 |
|---|---|---|
| P0-1 真实账对账假断链 | ✅ 已修复 | 真实账单 checked=175 / breaks=0；真实 HTTP 复验 14/14 |
| P0-2 `replace_range` 数据丢失 | ✅ 已修复 | Fix Log P0-2/3；API 回归 + 真实 HTTP |
| P0-3 admin PAT 越权 | ✅ 已修复 | admin JWT/PAT 均 403；普通用户 PAT 200 |
| P0-4 前端撤销入口缺失 | ✅ 已修复 | `/imports` 批次/隔离区/撤销；vitest 13 passed + build |
| P0-5 验收门禁/契约失效 | ✅ 已修复 | `verify-p1` 15/15；快照/SDK 一致；示例/problem+json/x-* 全覆盖 |

### 1.2 P1 未关闭项（二期期间按优先级抵扣）

| P1 | 影响 | 二期处置 |
|---|---|---|
| P1-1 心跳/租约缺失 | 长任务重复执行（回合/净值/报表） | **G2 本次修复** |
| P1-2 pending 取消不终态 | 队列幽灵任务/占配额 | **G2 本次修复** |
| P1-4 单飞异参静默复用 | rebuild 参数语义错 | **G2 本次修复** |
| P1-7 分类未接入 commit / 未知摘要预检 | NAV 现金流/行为指标错误 | **G3 本次修复** |
| P1-8 默认规则与解析器词汇漂移 | 真实账 26/177 误隔离 | **G3 本次修复** |
| P1-14 replay 适配器缺失 | Period2-04 / NAV 基准无法复算 | **G4 本次修复** |
| P1-10 seed/demo 契约 + 账号邮箱 | `make seed-demo && make rebuild` 不可用 | **G5 已完成（2026-10-21）** |
| P1-14/P1-06 SDK 未接入、tools/ci 缺位 | 35 端点契约/越权无法机器保证 | **G6 待做（Period2-05 前）** |
| P1-5 RLS 仅覆盖 ledger | review 新表容易漏策略 | **G7 待做（Period2-02 前）** |
| P1-11 `sort`/`batch_id`/`include_total` 空转 | 前端筛选/排序错误 | 二期前端 Sprint 修 |
| P1-13 token localStorage | XSS 风险 | Period2-12 前修 |
| P1-15 无 eslint | Period2-15 零口径无挂载基础 | Period2-15 前建 |
| P1-16 SSE 无轮询兜底 / 角色 403 页 | 进度/权限体验 | 二期前端修 |
| P1-17 上传体积/解压炸弹 | 认证用户内存 DoS | 二期安全收口 |
| P1-9 损坏文件/Excel 日期 | 真实账单兼容 | 二期导入增强 |
| P1-12 幂等 TTL / P2-4 时间源 / P2-5 游标等 | 长周期/可复现性 | 二期按需 |

---

## 2. 二期准入 Gate（G1~G7）

> 状态：✅ 已完成 / 🚧 进行中 / ⬜ 待做。每项完成后补证据链接。

### G1 数据基线：清库重 seed + 打 tag

- **为什么**：P0-1 将 `amount_units` 改为带符号发生金额，旧库/旧 seed 是旧口径，混用会污染 FIFO/净值/指标。
- **动作**：主库 `drop ... with(force)` → 重建 owner=stock_owner → `alembic upgrade head` ×2 → `seed-tenants` ×2 → `seed-demo`；打 tag `period1-baseline`。
- **验收**：3 用户 / 238 流水 / 2 批次（mode=full）；迁移幂等无 drift；fixtures 重跑字节不变；tag 存在。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：见 [G5-G1 Fix Log](Period2-Hardening-Fix-Log-G5-G1.md)
- **估时**：0.25d

### G2 任务队列语义（心跳/租约/单飞/取消/异常收口）

- **为什么**：DEV-PLAN Period2-02 显式依赖 P1-11；`rebuild-rounds` 是长任务，租约过期会被 reaper 重投 → 重复写回合；单飞异参复用会执行错误参数。
- **动作**：
  1. worker 执行期间心跳续租（间隔 < lease/3），任务结束/异常停止；
  2. `reap_expired_leases` 加行锁 + 条件更新，避免双 worker 同时回收；
  3. `enqueue` 同 `(kind, scope_key)` 参数不同 → 409 `JOB_ALREADY_RUNNING`（带旧 job_id）；参数相同才复用；
  4. pending 取消 → 直接终态 `cancelled` + finished_at + 事件；running 取消检查点收口；
  5. `_execute` 未知异常 → fail/retry 或终态 failed，不让消费循环退出。
- **验收**：心跳/回收/取消/单飞/异常 5 组回归用例；`pytest backend/tests/api backend/tests/unit/platform -q` 绿；真实 HTTP 取消 pending 终态；长任务不被重投。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：19 条新用例；真实 HTTP 单飞同参复用/异参 409/pending 取消终态 7/7；`pytest backend/tests/api` 108 passed；详见 [Fix Log](Period2-Hardening-Fix-Log-G2-G3-G4.md)
- **估时**：1d

### G3 分类链路闭环（commit 分类 + 未知摘要预检 + 规则词汇）

- **为什么**：Period2-03 外部现金流白名单依赖摘要分类（银证转账/转存管）；Period2-07 行为/费用指标依赖 category；当前 commit 后 category 全 NULL，真实账 26/177 误隔离。
- **动作**：
  1. 解析/预览阶段用默认+用户规则做 dry-run，未知摘要计入 preview `estimated.quarantined` + warning（含行号），与 commit 实际一致；
  2. commit 时对每行匹配规则：命中写 `category/summary_code` 并落库；未命中不写 trade，改落隔离区 `UNKNOWN_SUMMARY`（含建议规则/行号）；
  3. 默认规则覆盖真实账非交易词汇（银行转存/银行转取/转存管转入转出），与解析器共用词表，消除 26/177 误隔离；
  4. 保持 dedupe/撤销/对账行为不变；规则 CRUD/API-016 留给 Period2-02。
- **验收**：真实账 preview `quarantined` 与实际 commit `quarantined` 一致且为 0（全部已知摘要命中）；`GET /trades` category 非空率 100%（真实账）；合成夹具分类一致；现有 API/unit 测试全绿；新增分类回归用例。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：真实账 category 177/177、transfer 26、未知摘要 0、preview==commit、对账仍 0 断链；真实 HTTP G3/G4 16/16；详见 [Fix Log](Period2-Hardening-Fix-Log-G2-G3-G4.md)
- **估时**：1.5d

### G4 行情 replay 适配器 + 确定性夹具

- **为什么**：DEV-PLAN Period2-04 依赖 P1-14；当前 `quotes.json` 只写不读，`MARKET_MODE=replay` 是空壳，Period2-03 基准/Period2-04 校验无法复算。
- **动作**：
  1. `MarketDataSource` 端口（能力协商/`get_daily_bars(symbols, start, end)`/`provenance`/`as_of`）；
  2. Replay 实现：读 `fixtures/market/quotes.json`，校验日期/定点字符串/不返回未来数据；live 模式显式 `NotImplementedError`（Period2-04 再实现）；
  3. 工厂按 `MARKET_MODE` 装配；确定性夹具生成（固定日期/排序/无时间戳，重复生成字节一致）；
  4. 夹具通过 `fixtures.py` 统一生成，`seed-demo`/`demo-p1` 复用；`quotes.json` 可被读回。
- **验收**：单测覆盖 replay 正常读取/未来数据拒绝/非法定点拒绝/live 未实现；同参数重复生成字节一致；真实 HTTP 无需新端点（阶段 2 消费）。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：quotes 两次生成 SHA256 一致；replay 读取 9 条/as_of 截断；25 条单测；详见 [Fix Log](Period2-Hardening-Fix-Log-G2-G3-G4.md)
- **估时**：1d

### G5 seed/demo 契约收口

- **为什么**：二期验收剧本第一条 `make seed-demo && make rebuild`；现在 seed 批次 `mode="append"` 会让 `GET /imports` 500，seed 账号 `a@local` 无法登录。
- **动作**：seed/demo 改 `mode="full"`；seed 账号改合法邮箱（如 `a@demo.example.com`）或放宽 EmailStr 并同步文档口令；`make seed-demo` 产出可登录 + 可 rebuild 的演示数据。
- **验收**：seed 后 `a@demo.example.com` 可登录；`GET /imports` 200；`make seed-tenants` 幂等；文档 §2.1 同步。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：seed 批次 mode=full；三账号可登录；`seed-tenants` 幂等；`seed-demo` 可重复；`demo-p1 --run` 跑通；bill/quotes 字节确定；见 [G5-G1 Fix Log](Period2-Hardening-Fix-Log-G5-G1.md)
- **估时**：0.5d

### G6 契约链路与端点覆盖/越权门禁

- **为什么**：二期一次新增 ~35 个端点；Period2-14 要求"受保护端点越权 100%"，Period2-15 要求前端零口径；当前前端仍手写 DTO、`tools/ci` 缺位。
- **动作**：前端类型改用生成 SDK；新增 `tools/ci/gate_endpoint_coverage.py` 与 `gate_tenant_coverage.py`（或 pytest 等价门禁）；`verify-p1`/`verify-p2` 纳入；API-075/072 等缺口用例补齐。
- **验收**：新增端点未加验收/越权用例即门禁失败；`make verify-p2` 可跑。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：`contract.ts` 编译期契约断言 + 7 条 contract.test；`gate_endpoint_coverage` 30/30；`gate_tenant_coverage` 23/23、401 23/23、列表隔离 4/4；`verify-p1` 18/18。详见 [G6-G7 Fix Log](Period2-Hardening-Fix-Log-G6-G7.md)
- **估时**：1d

### G7 review schema 与 platform 租户表 RLS

- **为什么**：Period2-02 起新增 `review.round/round_fill/nav_daily` 等表；当前 RLS 只覆盖 ledger，platform 的 job/api_token/idempotency 等无策略。
- **动作**：新表迁移同批创建 `tenant_isolation` 策略 + WITH CHECK；为 platform 租户表补策略（认证前置表单独处理）；RLS 测试补 Testing §4.3 八类场景。
- **验收**：`information_schema` 枚举租户表策略 100%；未设 `app.user_id` 读 review/platform 租户表 0 行；跨租户写被拒。
- **状态**：✅ 已完成（2026-10-21）
- **证据**：0002 迁移启用 platform 4 表 RLS；worker 双池；`gate_rls_coverage` 11/11；RLS 测试 20 条；详见 [G6-G7 Fix Log](Period2-Hardening-Fix-Log-G6-G7.md)
- **估时**：0.5–1d

---

## 3. 二期启动排期（建议）

```
Day 0        G1 已完成（清库重 seed + tag `period1-baseline`）
Sprint 1     ✅ Period2-01 已完成（2026-10-22）：稳定序/分批减仓/股息/送转/配股/桥接；独立 oracle 对拍 + 属性测试
Sprint 2     ✅ Period2-02 已完成（2026-10-22）：review.round/round_fill + rebuild-rounds job + 脏窗口 + /rounds/* + 规则 CRUD + reclassify + bridge
Sprint 3     Period2-03 NAV（依赖 G3/Period2-02 ✅）；Period2-04 行情（依赖 G4）
Sprint 4     Period2-05 指标引擎 + Period2-06 端点；新增端点由 G6 门禁强制覆盖
Sprint 5+    Period2-07~Period2-13；Period2-14 二期越权全量；Period2-15 前端零口径
```

> 估时参考：二期总量 33.5 人日（DEV-PLAN §3）；G1~G7 合计约 5–6 人日，其中 G2/G3/G4 与 Period2-01 可并行。

---

## 4. 风险与不进入条件

| 风险 | 触发 | 后果 | 规避 |
|---|---|---|---|
| 旧口径数据混入 | 未清库直接算指标 | FIFO/净值系统性偏差 | G1 强制先做 |
| rebuild 重复执行 | 心跳缺失 + 长任务 | 回合重复/半截写 | G2 先于 Period2-02 |
| NAV 现金流识别错 | 分类未接入 | 外部现金流漏计/错计 | G3 先于 Period2-03 |
| 行情回放空壳 | replay 未实现 | 基准/净值无法复算 | G4 先于 Period2-04 |
| 演示数据不可用 | seed 契约未收口 | 二期验收剧本跑不通 | G5 先于二期验收 |
| 端点/越权漏测 | 门禁未建 | 35 端点回归失控 | G6 与端点同步 |
| review 表越权 | RLS 未覆盖 | 跨租户泄漏（最严重） | G7 先于 Period2-02 |

**不进入二期条件（必须停）：**

- P0 回归失败；
- G1 未做（旧口径数据仍在；已于 2026-10-21 完成）；
- Period2-02 前 G2 未完成（G2 已于 2026-10-21 完成）；
- Period2-03 前 G3 未完成（G3 已于 2026-10-21 完成）；
- Period2-04 前 G4 未完成（G4 已于 2026-10-21 完成）。

---

## 5. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-21 | 初版：5 项 P0 已修复；G2/G3/G4 进入修复 |
| 2026-10-21 | G2/G3/G4 已完成并集成验证（verify-p1 15/15；真实 HTTP G2 7/7、G3/G4 16/16） |
| 2026-10-21 | G5 seed/demo + G1 清库基线完成（3 用户/238 流水/2 批次，tag `period1-baseline`） |
| 2026-10-21 | G6（SDK/端点/越权门禁）+ G7（platform RLS/review 门禁）完成；verify-p1 18/18；G1~G7 全部关闭 |
| 2026-10-22 | **Period2-01 完成**：`core/fifo`（稳定序/分批减仓/股息除息日归属/送转/配股/超卖 data_error/open 回合/桥接恒等式）+ 独立 oracle + 28 边界对拍 + hypothesis 属性测试；core 覆盖率 100%（161 passed）。 |
| 2026-10-22 | **Period2-02 完成**：`review.round/round_fill/round_build_state`（迁移 0003 + RLS 3/3）、`rebuild_rounds` job（full/period 脏窗口 + 400 天安全回看 + 自动入队）、API-016/022~025/027~031、规则 CRUD、桥接恒等式；后端全量 590 passed，契约快照/SDK 同步。证据：[实施日志](Period2-01-02-Implementation-Log.md)。 |
| 2026-10-22 | **增量（Period2-02 产品化）**：导入提交/分类/撤销成功后自动建立 FIFO 回合；Web 新增「回合」列表（筛选/分页/重建）与回合详情（FIFO 配对明细）；`seed-demo` 直接建回合；后端全量 591 passed、前端 31 passed。证据：[实施日志 §8](Period2-01-02-Implementation-Log.md)。 |
