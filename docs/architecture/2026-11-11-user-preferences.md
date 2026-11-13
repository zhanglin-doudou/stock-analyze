# 用户偏好配置 v2.1（评审修订版）：扩展 `platform.user_setting` + 通用 PATCH（外观与显示 / 账单旗标阈值与行情时段 / 概念板块过滤）

> 版本：**v2.1（评审修订版，2026-11-13）** ｜ 状态：**P0、P1-1、P1-2、P1-3、P2-1 已实现并完成门禁（2026-11-13）；代码已提交（后端 `e1aca2e`、前端 `9cb2915`）** ｜ 基线：`5419813`（schema `0013`；API-Catalog 94 端点 / 已实现 62 个 operation / 2xx 示例 56 / `SEED_ERROR_COUNT=47`；旗标 `DEFINITION_VERSION="1.4"`、20 码）
> **P0 实现记录（2026-11-13）**：仅交付 P0-1（数据与设置接口）+ P0-2（前端设置页）。实际端点计数为 **95 端点 / 64 operation / 58 个 2xx 示例 / `EXPECTED_PERIOD2=40`**（API-097 未登记，故与 D-16 的「96/65/59/41」相差 1，属 P0 增量）；迁移 `0014_user_setting_preferences`；阈值/窗口与默认值不同时设置页显示「需重建账单旗标」，重建端点与旗标接算留待 P1。
> **P1-1 实现记录（2026-11-13）**：`TradeFlagConfig` 参数化 `evaluate_trade_flags`；`definitions` 改模板 + `render_description(config)`；`build_trade_flags_for_user(config=)` 落 `flags_hash`，`load_trade_flags`/`monthly_trade_flag_summary` 按 hash 过滤并新增 `stale_months[]`；CLI `build-trade-flags` 逐用户读偏好；API-092/093/095 additive `flag_config`；`core.preferences.resolve` 增 `bill_bps`；`UserSettingRepository.get_effective`。API-097 与前端「立即重算」在 P1-2。
> **P1-2 实现记录（2026-11-13）**：worker handler `rebuild_trade_flags`（`stock_review.application.trade_flags.rebuild_trade_flags_job`：`scope=all` 按首末交易月、`scope=months` 按给定月，逐月构建、每月检查取消、最近 120 月截断）；API-097 `POST /settings/trade-flags/rebuild`（`Idempotency-Key` 必填、202 + `Location`/`Retry-After`、同 key 重放 `Idempotency-Replayed`、同 scope 单飞 `X-Job-Reused`、异参在途 409、months 格式/去重/上限 422）；计数升至 **96 端点 / 65 operation / 59 个 2xx 示例 / `EXPECTED_PERIOD2=41`**；前端设置页账单旗标卡片新增「立即重算」（`scope=all`，轮询 job 进度，成功后刷新设置以更新指纹）；P0 的「需重建账单旗标」提示即接到该入口。看板/回合详情页的 stale 横幅（§7.4）不在本轮范围，数据已由 P1-1 additive 就绪。
> **P1-2 补充（2026-11-13）**：看板 `/dashboard` 与回合洞察/回合详情页已接入「旗标已过期」横幅（共用 `ui/StaleFlagsNotice.tsx`，只渲染后端 `stale_months`/`flag_config.stale` 结论，动作指向设置页「立即重算」；无过期月份不显示）。
> **P1-3 实现记录（2026-11-13）**：`application/sectors.py` 的 `build_sector_overview(..., concept_filter=)` / `_concept_overview(..., concept_filter=)` 支持 `off/hide/exclude`（`ConceptFilter`/`ConceptFilterInfo`/`concept_filter_from_preferences`）；只裁剪行/TopN/others/`n_sectors`，coverage/overlap/multi_tag/hhi_tag/caveat/tagged/unclassified 保持全量；`exclude` 另给 `filtered_market_value_units`/`filtered_holding_count`；非 off 时输出 `concept.filters{mode,applied,excluded[{code,name,resolved}]}`、`assumptions.concept_filter_hash`，且 `_inputs_hash` 追加过滤指纹（ETag 随之变化）；新增 missing 码 `SECTOR_CONCEPT_FILTER_APPLIED`/`SECTOR_CONCEPT_FILTER_UNRESOLVED`；`routers/sectors.py` 仅在 `taxonomy=concept` 时读 `get_effective` 构造过滤参数，非 off 加 `Cache-Control: private, no-cache`；`off` 与旧响应逐字一致（无 filters/无 assumptions/ETag 不变）。前端 `Sectors.tsx` 渲染已排除概念 chips（`resolved=false` 标「已失效」）与 exclude 汇总；设置页概念多选与失效标记沿用 P0。
> **P2-1 收尾记录（2026-11-13）**：文档同步 `README`（Period2-19 状态/功能特性/未完成清单/文档索引）、`docs/DATABASE.md`（`concept_filter_prefs` 形状与读时生效说明）、`API-Catalog`（§1/§2 API-087 概念过滤、`SECTOR_*` 20→22 码、条件 ETag 与 `Cache-Control`、§6 验收；§2 详细契约计数修正 41→42）、`DEV-PLAN-v2`（Period2-19 已交付）；契约快照 `contracts/openapi.json` 与前端 SDK `schema.d.ts` 已重生成且 `api-snapshot --check` 无漂移。**门禁发现并修复一处 P0 遗留**：`review/models.py::TradeFlag` 缺少迁移 0014 已建的 `ix_trade_flag_user_hash` 索引声明，导致 `make migrate-check` 报 `remove_index` 漂移；补声明后通过（仅 ORM 元数据，无需新迁移）。手工验收（§8.2 第 3 条，运行中的 dev 实例 + 真实概念目录）：`hide` 时 68→63 行、被排除 code 不出现在行内、`coverage/overlap` 与全量一致、`Cache-Control: private, no-cache`、`If-None-Match`→304；切 `off` 后 68 行、`filters=null`、无 `concept_filter_hash`、ETag 变化；恢复原偏好后 ETag/`inputs_hash` 与基线逐字一致；行业视图不受影响（无 filters/无私有缓存头）。
> **v2.1 修订依据**：2026-11-13 三名独立评审（架构/数据/契约、API/并发、业务口径/测试）均 **REQUEST_CHANGES**；本版按评审 P0/P1 全部修复并**重新对齐 HEAD `5419813`**。评审记录：[2026-11-13-DevDesign-Review-user-preferences](../reviews/2026-11-13-DevDesign-Review-user-preferences.md)。
> **v2.0（2026-11-12）** 依据人工选型：① 扩展 `platform.user_setting`；② 通用 `PATCH /settings`（typed 列局部更新 + JSONB merge patch）；③ 移除账单字段映射。
> **2026-11-13 人工决策**：v2.0 的 H-01~H-10 按推荐执行；评审新增的再决策项见 §0.C（HITL-R1~R6，均给出推荐值，未确认前按推荐实现）。
> 关联设计：[板块数据](2026-10-31-sector-data.md)、[FIFO 回合洞察](2026-11-04-fifo-round-insights.md)、[账单解析与估值口径](2026-10-29-bill-parsing-and-valuation-caliber.md)、[Dashboard 设计](2026-10-28-dashboard.md)、[API-Catalog](v2/API-Catalog.md)。
> **本文是设计，不是实现**；默认值/单位/校验/PATCH 语义在此冻结，变更须 bump 本文版本并追加评审记录。

---

## 0. 决策摘要（评审重点）

> 状态列：**已定** = 人工选型/仓库铁律；**已批准** = 2026-11-13 人工按推荐执行；**评审修订** = v2.1 按评审意见收紧；**待确认** = 需人工对评审新增项拍板（HITL-R）。

### A. 核心决策

| # | 问题 | v2.1 口径 | 依据 / 代价 | 状态 |
|---|---|---|---|---|
| **D-01** | 偏好表形态 | **扩展既有 `platform.user_setting`**：新增 **7 列** = 4 个 bps 阈值列（`bill_near_equal/chase/stop_loss/short_term_*_bps`）+ `bill_window_trading_days smallint` + 2 个 JSONB（`market_session_prefs`、`concept_filter_prefs`）；启用既有 `display_prefs` JSONB 作为外观真值源；`version` 升级为 CAS 版本 | 复用既有 RLS/仓储/懒创建/清库夹具；零新表。**不新增独立 instant 阈值列**：现行 1.4 中即时类与短期类共用 `SHORT_TERM_MARGIN_BPS`（`core/trade_flags/constants.py:34`） | 已定（**HITL-R1** 复核） |
| **D-02** | 接口形态 | `GET /settings`（API-055）+ 通用 `PATCH /settings`（API-096）+ `POST /settings/trade-flags/rebuild`（API-097）；取消段级 PUT | 一个局部更新入口，契约面积最小 | 已定 |
| **D-03** | PATCH 语义 | 类型分流：typed 列**只更新出现字段**、`null`=恢复列默认；JSONB **JSON Merge Patch（RFC 7396）**：出现键覆盖/新增、`null` 删除键回落默认、**数组字段整体替换**；未知键（顶层/嵌套）422；**空 body `{}` 422**；**有字段但值全等于当前 → 200 不 bump** | RFC 7396 + 与 `rules.py` 的「无修改字段 422」对齐 | 评审修订 |
| **D-04** | typed vs JSONB | 「数值 + 范围 + 参与指纹」→ typed（4 bps + window）；「嵌套/列表/纯展示」→ JSONB（外观、时段、概念过滤） | DB CHECK 兜底 + 免频繁迁移 | 已批准（H-01） |
| **D-05** | 版本双轴 | `definition_version`（代码判定语义 **1.4**，不变）+ **`flags_hash`**（4 个 bps + window 的规范指纹，16 hex，**运行期派生、不落 `user_setting`**）；`review.trade_flag` 增 `flags_hash` 列并按「当前 `definition_version` + 当前 `flags_hash`」过滤。**不定义 `pref_fingerprint`**；概念过滤只用 `concept_filter_hash`（仅 `mode != off` 时计算） | `String(8)` 放不下指纹（`review/models.py:593`）；单一派生避免两套命名 | 评审修订 |
| **D-06** | 改配置是否重算 | **不自动重算**：阈值下次 build 或手动 API-097 生效；未重算月份显式 `stale_months[]`（与 `months_without_data` 区分）。概念/外观/时段读时生效。**build 的 period 支持 `YYYY-MM` / `YYYY` / `YYYY-MM-DD..YYYY-MM-DD`（`parse_period_label`），delete 只覆盖该区间** | 区间外月份保留旧 hash 行，因此只能说「该区间内一套口径」 | 评审修订 |
| **D-07** | 行情时段 | 用户级 `market_sessions` **仅展示/提醒**：设置页文案、盘后提醒开关。**不进入** `valuation_end`/`_session_notice` 判定/`data_as_of`/pending/同步窗口。**部署级 `MARKET_*` env 提升移出本期**（非目标，后续独立运维项），因此无需改 `config.py`/`infra/.env.example` | 仓库无盘中时段模型；用户可改「哪天算收盘」会破坏跨账号可复现性 | 已批准（H-02）+ 评审修订 |
| **D-08** | 概念过滤 | `off`（默认）**严格零变化**：不加 `filters` 字段、不进 `inputs_hash`/ETag、不加 `assumptions`。`hide`：行/TopN/others 排除，coverage/overlap/caveat/status 保持**全量**。`exclude`：同样排除，另给 `filtered_market_value`/`filtered_holding_count`；**聚合与 caveat 仍全量**、`unclassified` 不把被排除持仓误标为未分类。存 `code`+`name` 快照，读时按当前目录标 `resolved` | 修正 v2.0「exclude 在 `concept_exposure` 前过滤」导致的 caveat 消失/未分类误标（评审 P0） | 评审修订（**HITL-R2** 复核语义） |
| **D-09** | 账单字段映射 | **本期不做**（2026-11-12 决策）：不改 `parse_bill`/`map_columns`/`ImportCommitIn`/批次表/导入页；既有单次 mapping 保持原样 | 缩小风险面 | 已定 |
| **D-10** | 并发/幂等 | `If-Match: W/"<int>"` 必填（只接受整数版本；`*`/缺失/非法 422）；CAS 单条 `UPDATE ... WHERE user_id=:uid AND version=:expected`；首次缺行用 `INSERT ... ON CONFLICT DO NOTHING` 兜底；`Idempotency-Key` 可选，**request_hash 含 If-Match + body**；重放用缓存 body 的 `version` 重算 ETag；无变更也要 `complete` 幂等记录（防 IN_PROGRESS 卡死）；**不做 FOR UPDATE、不做二次 UPDATE** | 修正 v2.0「不做 FOR UPDATE vs get_or_create_for_update」自相矛盾、typed+JSONB 两条 UPDATE 会 +2 | 评审修订 |
| **D-11** | 审计 | 有实际变更才 `audit.record(action="settings.patch", meta={changed[],from_version,to_version,flags_hash?})`；不记全量 JSONB/令牌；无变更不审计 | 写操作三件套 | 已定 |
| **D-12** | 既有字段兼容 | `display_prefs` 原地启用；**`/auth/me` 的 `settings.display_prefs` 改为返回「生效值」**（形状不变：theme/density/trend），消除 raw vs effective 双口径；`benchmark_id`/`privacy_flags` 只读回显，PATCH 传入 → 422 | 评审 P1：raw 可观测会与 GET /settings 不一致 | 评审修订（**HITL-R4** 复核） |
| **D-13** | 分层落点 | `stock_contracts.preferences`：**仅声明式字段注册表**（组/键/存储/默认/只读/单位），**不调用 core**；`stock_core.preferences`：合并/校验/规范化/哈希（纯函数）；`stock_platform`：CAS 落库（显式 `user_id`）；review/ledger 只收显式参数 | 避免 contracts→core 违反 L1（评审 P1） | 评审修订 |
| **D-14** | 前端 | 所有表单值是字符串（百分比串、`HH:MM`、**窗口整数也用十进制字符串**，服务端解析）；PATCH 只发脏字段并**剔除只读字段**（`timezone/flags_hash/is_default/version/updated_at`）；数组发全量。窗口用预置 `<select>`（1/2/3/5/10/20），规避 `Number()/parseInt` 零口径门禁 | 评审 P1：`window_trading_days: int` 在 eslint 零口径下不可编辑 | 评审修订 |
| **D-15** | 错误码 | **不新增 ErrorCode/WarningCode**（`SEED_ERROR_COUNT=47` 不变）；`errors[].field` 为**点号路径**（如 `concept_filters.excluded` + 消息内下标，不写 `[0]`）；板块 `missing[]` 新增 2 个 code（响应字段、**无 severity 字段**） | 评审 P1/P2 | 评审修订 |
| **D-16** | 期次与编号 | 新开 **Period2-19**；API-055 移入；新增 **API-096/097**；端点 94→**96**、operation 62→**65**、2xx 示例 56→**59**、`EXPECTED_ALL_ENDPOINTS` 94→96、`EXPECTED_PERIOD2` 39→**41**、API-Catalog §2 详细契约 39→**42** | 契约计数联动 §5.7 | 已批准（H-05） |
| **D-17** | additive 响应字段清单 | 必须同 PR 加字段到 schema，否则 `response_model` 静默丢弃：`SectorConceptBlockOut.filters`、`SectorMissingCode` +2、`DashboardTradeFlagsOut.flag_config/stale_months`、`RoundInsightItemOut/PageOut/DetailOut.flag_config`、`SectorRowOut` 已有 `holdings` 不动 | 评审 P0：漏加字段 = 实现无效 | 评审修订 |

### B. 非目标（明确不做）

- **不做账单字段映射/模板**（D-09）。
- **不提升部署级 `MARKET_*` env**（D-07；运维独立项，后续另行评审）。
- 不做团队共享/跨用户预设；不做偏好导入导出；不做 per-section 版本/ETag。
- 不做盘中实时行情/自定义交易日历；用户级行情时段仅文案/提醒。
- 不改 18 指标、五因子归因、净值、回合引擎口径；`timing.chase/fly` 与旗标独立。
- 不开放 `benchmark_id`/`privacy_flags` 写入口（API-056/057 另行排期）。
- 不做偏好的历史版本回滚 UI（`audit_log` 可查，P2）。
- 不对概念「排除后的聚合」再算一套口径（HITL-R2：推荐保持全量聚合 + filtered 披露）。

### C. 决策记录与评审新增待确认项

**已决（2026-11-13 按推荐执行）**：H-01 混合 typed/JSONB、H-02 时段仅提示、H-03 概念默认 off + hide/exclude、H-04 不开放 benchmark/privacy、H-05 Period2-19 + API-096/097、H-06 数组整体替换、H-07 `null`=回落默认、H-08 阈值开放、H-09 只保留最新口径、H-10 映射后续期次。

**评审新增（实现前请确认；未确认按「推荐」实现）**：

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| HITL-R1 | 阈值集合按现行 1.4 的 4 个 bps（near/chase/stop_loss/short_term）+ window，**不恢复独立 instant 阈值**；若强行拆独立 instant 需改 core 判定与 `definition_version` | A 按现行 4+1（本文）；B 新增独立 instant（改判定语义，需 bump 1.5） | **A** |
| HITL-R2 | `exclude` 的统计语义 | A 行/TopN 排除 + 全量聚合 + `filtered_*` 披露（本文）；B 聚合也按过滤集重算 + 另给 `unfiltered` 全量块 | **A**（数据质量轴不被用户偏好污染） |
| HITL-R3 | 部署级 `MARKET_*` env 提升 | A 移出本期（本文）；B 本期一并做（需 config/env/CLI/资产链路全改） | **A** |
| HITL-R4 | `/auth/me.settings.display_prefs` | A 返回生效值（本文）；B 保持 raw（与 GET /settings 双口径） | **A** |
| HITL-R5 | 概念 code 存在性校验 | A 写时只告警（未知 code 可保存，list 标 `resolved=false`）；B 目录已同步时未知 code 422 | **A**（避免悬空 code 阻塞后续保存） |
| HITL-R6 | `window_trading_days` 输入 | A 预置下拉 + 字符串边界（本文）；B 自由输入（需前端数字解析，违反零口径） | **A** |

---

## 1. 背景、目标与验收总览

### 1.1 需求拆解（v2.1 范围）

| # | 需求 | 可配置内容 | 本期 |
|---|---|---|---|
| R1 | 外观与显示 | `theme`（light/dark/system）、`density`（cozy/compact）、`trend`（red-up/green-up） | ✅ P0 |
| R2 | 账单旗标阈值 | 4 个 bps 阈值 + 前后窗口行情日数（影响 20 码判定） | ✅ P0 配置 / P1 接算 |
| R3 | 行情时段 | 交易时段文案、盘后数据就绪时间、时段提示开关（仅展示/提醒） | ✅ P0 |
| R4 | 概念板块过滤 | `mode ∈ {off,hide,exclude}` + 被排除概念列表 | ✅ P0 配置 / P1 接算 |
| R5 | 账单字段对应 | Excel 列 → DB 字段映射 | ❌ 不做（D-09） |

### 1.2 目标（可验收）

1. **一个入口**：`GET /settings` 全量读取；**一个 `PATCH /settings`** 任意子集局部更新（typed 列 + JSONB 合并），`If-Match` 乐观锁。
2. **默认零变化**：默认偏好下既有页面数字/旗标结果/板块响应**逐字段一致**；`off` 时概念响应不新增字段、ETag 不变（回归锁定）。
3. **PATCH 可预测**：只改出现字段；JSONB 只合并出现键；`null` 回落默认；未知键 422；空 body 422；无变更 200 不 bump；重放不重复 bump。
4. **改阈值可解释**：响应同时给 `definition_version` 与 `flags_hash`；未重算月份 `stale_months[]`；API-092/093 的 `flag_config.stale=true` 时显式提示，不冒充新口径。
5. **概念过滤可撤销、可披露**：被排除概念不出现在行/TopN/others；`exclude` 显式 `filtered_market_value`；caveat/多归属/数据质量轴不被过滤掩盖。
6. **门禁全绿**：契约、越权矩阵、迁移、前端零口径、测试计数同 PR 更新；不新增错误码。

### 1.3 追踪

| 需求 | 设计 | 页面/API | 验收 |
|---|---|---|---|
| R1 | §3.2.1 / §4.7 / §7.2 | `GET/PATCH /settings`；设置页 | 默认值一致；切换即时生效并跨设备同步；首屏无闪烁 |
| R2 | §3.2.2 / §4.4 / §5.4 | `PATCH /settings`（`bill_flags`）、API-097、看板/回合 | 默认阈值结果与今日一致；改阈值手算一致；`stale_months` |
| R3 | §3.2.3 / §4.5 | `PATCH /settings`（`market_sessions`） | 默认提示不变；自定义只改文案/提醒，不改任何后端判定 |
| R4 | §3.2.4 / §4.6 / §5.5 | `PATCH /settings`（`concept_filters`）、API-087 | off 零变化；hide/exclude 后 TopN/others 重算、全量聚合与 caveat 保留 |
| R5 | §0.B | — | 不验收 |

### 1.4 术语

| 术语 | 含义 |
|---|---|
| 生效值 | 列值 + JSONB 默认合并后的完整、规范化、校验过的配置 |
| `flags_hash` | `{4 个 bps + window}` 规范 JSON 的 SHA-256 前 16 hex；派生值，落 `review.trade_flag` 行用于过滤 |
| `concept_filter_hash` | `{mode, sorted(excluded codes)}` 的规范哈希前 16 hex；**仅 `mode != off` 时非空并进 `inputs_hash`/ETag** |
| PATCH 注册表 | contracts 中声明「API 组 → 存储形态（typed 列 / JSONB 键）/ 默认值 / 只读」的单一表；校验/合并逻辑在 core |
| `stale_months` | 看板窗口内「有 `review.trade_flag` 行（当前 `definition_version`）但无当前 `flags_hash` 行」的月份 |
| `months_without_data` | 窗口内没有任何 `review.trade_flag` 行的月份（可能未 build 或当月无交易） |
| 账单旗标 | `review.trade_flag` 的 20 个行级旗标；**不是**回合旗标（`insights.flags`）或行为规则提醒（API-058） |

---

## 2. 现状与差距（HEAD `5419813` 证据）

### 2.1 `platform.user_setting`

| 项 | 现状 | 证据 |
|---|---|---|
| 表 | `user_id`(PK)/`benchmark_id`/`privacy_flags`/`display_prefs`/`version`/`updated_at` | `platform/models.py:253-266` |
| RLS | 已启用 `tenant_isolation` | `0002_platform_tenant_rls.py:61-76` |
| 仓储 | `get` 懒创建，无更新方法 | `platform/repositories/users.py:60-78` |
| 读写 | 仅 `GET /auth/me` 回显 4 字段（`display_prefs` 为 raw `{}`）；`version` 从不递增 | `routers/auth.py:269-281`；`schemas.py:67-69` |
| 业务消费 | `display_prefs`/`privacy_flags`/`benchmark_id` 无业务消费；外观存 localStorage `sa.theme/sa.density/sa.trend`（`Preferences.trend`） | `theme.ts:12-35`；`metrics/datasets.py:42` |

### 2.2 外观

| 项 | 现状 | 证据 |
|---|---|---|
| 首屏 | `index.html` 内联防闪 + `main.tsx` `initPreferences()` | `index.html:7-26`；`main.tsx:9-12` |
| 设置页 | 三组分段控件，切换即写本地；文案「偏好只存本地 / 存于浏览器」 | `pages/Settings.tsx:40,56-58,119-127,124-194` |
| 零口径 | eslint 禁用 `Number/parseInt/parseFloat/toFixed`；图表数值解析仅 `charts/geometry.ts` | `eslint.config.js:15-32` |
| 差距 | 跨设备不同步；`display_prefs` 死字段；`trend`（前端键）与设计 API 键需统一 | — |

### 2.3 账单旗标（**v2.1 重新对齐**）

| 项 | 现状 | 证据 |
|---|---|---|
| 旗标数量 | **20 个 code（买 8 / 卖 12）**；4 个 lookahead：`short_term_bottom`/`short_term_underwater`/`short_term_top`/`short_term_sold_early` | `core/trade_flags/definitions.py:23-319`；`constants.py:18` |
| 阈值 | **4 个 bps + 窗口**：`NEAR_EQUAL_TOLERANCE_BPS=100`、`CHASE_MARGIN_BPS=100`、`STOP_LOSS_MARGIN_BPS=100`、`SHORT_TERM_MARGIN_BPS=500`、`WINDOW_TRADING_DAYS=5`；`INSTANT_MARGIN_BPS` 已删除（即时类与短期类共用 500） | `constants.py:24-37`；`engine.py:255,265,291,311,321,325` |
| 判定语义 | `stop_loss` 需「破位且严格低于持仓均价」，破位但未低于均价归 `sell_drawdown`；零成本持仓止损恒不命中、回撤按破位 | `definitions.py:235-266`；`engine.py:278-325` |
| 显示分组 | `TREND_FLAG_CODES` 9 码（含 `short_term_underwater`/`sell_drawdown`/`short_term_sold_early`）；`STACKED_STATE_FLAG_CODES` 10 码 | `definitions.py:335-368` |
| 构建 | CLI `make build-trade-flags --month <period>`，`parse_period_label` 支持 `YYYY-MM/YYYY/YYYY-MM-DD..YYYY-MM-DD`；整段 delete+insert；owner 角色；无 worker/编排 | `cli.py:1297-1304`；`trade_flags/service.py:173,208-216`；`metrics/context.py:104-134` |
| 读路径 | 只按当前 `definition_version` 过滤；不感知阈值 | `service.py:284-318,437-442` |
| **第二套现算** | 回合洞察 `_trade_flag_statuses` 直接调 `evaluate_trade_flags` 生成 `status_counts`（抄底计数/卖飞计数），与落库 `trade_flags` 并存，**必须共用同一 config/hash** | `insights/service.py:698-721,895-930`；schema `status_counts` `schemas.py:1734,1897` |
| 看板 | 已有旗标趋势/柱状图（含 1.4 新系列） | `pages/dashboard/TradeFlagCharts*.tsx`；`routers/dashboard.py:380` |
| 差距 | 无用户级阈值；改阈值旧行被当新口径读；无自动重算；术语文案阈值写死 | — |

### 2.4 行情时段

| 项 | 现状 | 证据 |
|---|---|---|
| 时段模型 | **不存在**（无 09:30-11:30/13:00-15:00 判定） | 全仓 grep；仅演示时钟出现 |
| 收盘口径 | `MARKET_CLOSE_HOUR=15`；`valuation_end` 15:00 前回退前一工作日；`session_notice ∈ {market_open,sync_pending}` | `assets.py:598-630` |
| 发布窗口 | 16:30 前不拉当日 bar；18:00 后空结果可落负覆盖 | `market_live.py:76-77,790-798` |
| 前端文案 | **只有**「当日还未收盘（A 股 15:00 收盘）」「当日已收盘但行情尚未同步——请先…make sync-market」，**不存在「预计 16:30 后可同步」文案** | `pages/Assets.tsx:125-135` |
| 差距 | 无用户级时段；无「数据就绪时间」数据源 | — |

### 2.5 概念板块

| 项 | 现状 | 证据 |
|---|---|---|
| 读路径 | `GET /sectors/overview?taxonomy=concept` → `_concept_overview` | `routers/sectors.py:356-409`；`application/sectors.py:390,991` |
| 行与聚合 | 行 = `mv_by_concept`（可按 `limit` 截断/others）；`coverage/overlap/multi_tag/hhi_tag` 来自 `concept_exposure`（全量 memberships）；caveat = overlap>1 | `application/sectors.py:1009-1125`；`core/sector/concepts.py:29-72` |
| 多归属 | 全量 overlap>1 → `SECTOR_CONCEPT_MULTI_MEMBERSHIP` missing + caveat | `application/sectors.py:92,1075,1125` |
| 未分类 | `unclassified = equity - tagged`（`tagged` 来自全量 memberships） | `application/sectors.py:1127-1133` |
| 行内持仓 | 5a00410 新增 `holdings[]`（概念行专属） | `schemas.py:1085-1108`；`routers/sectors.py:158-167` |
| 目录 | `market.sector` PK `(taxonomy, sector_code)`；概念 taxonomy 仍 `concept_em`；`active_only` 默认 true + 游标分页 | `market_models.py:112-144`；`routers/sectors.py:423-426` |
| 失效 | 同步从不置 `is_active=false`；成分只 close 快照内 code | `sector_live.py:1490-1520` |
| missing schema | `SectorMissingCode` 为封闭 Literal；`SectorMissingOut` **无 severity 字段** | `schemas.py:1026-1057` |
| ETag | `as_of + sector digest + inputs_hash[:16]`；`inputs_hash` 不含偏好 | `routers/sectors.py:405-409`；`application/sectors.py:314-334` |
| 差距 | 无用户过滤；改设置不会使 ETag 变化 | — |

---

## 3. 数据模型

### 3.1 扩展 `platform.user_setting`（迁移 `0014`）

```sql
ALTER TABLE platform.user_setting
    ADD COLUMN bill_near_equal_tolerance_bps integer  NOT NULL DEFAULT 100,
    ADD COLUMN bill_chase_margin_bps         integer  NOT NULL DEFAULT 100,
    ADD COLUMN bill_stop_loss_margin_bps     integer  NOT NULL DEFAULT 100,
    ADD COLUMN bill_short_term_margin_bps    integer  NOT NULL DEFAULT 500,
    ADD COLUMN bill_window_trading_days      smallint NOT NULL DEFAULT 5,
    ADD COLUMN market_session_prefs          jsonb    NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN concept_filter_prefs          jsonb    NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE platform.user_setting
    ADD CONSTRAINT ck_user_setting_near_equal CHECK (bill_near_equal_tolerance_bps BETWEEN 10 AND 500),
    ADD CONSTRAINT ck_user_setting_chase      CHECK (bill_chase_margin_bps BETWEEN 10 AND 500),
    ADD CONSTRAINT ck_user_setting_stop_loss  CHECK (bill_stop_loss_margin_bps BETWEEN 10 AND 500),
    ADD CONSTRAINT ck_user_setting_short_term CHECK (bill_short_term_margin_bps BETWEEN 50 AND 2000),
    ADD CONSTRAINT ck_user_setting_window     CHECK (bill_window_trading_days BETWEEN 1 AND 20),
    ADD CONSTRAINT ck_user_setting_prefs_json CHECK (
        jsonb_typeof(market_session_prefs) = 'object' AND
        jsonb_typeof(concept_filter_prefs) = 'object'
    );
```

- **7 列 / 6 CHECK**；不新建表、不改 RLS/授权（新列自动受既有 `tenant_isolation`）。
- 存量行由 `DEFAULT` 回填；`display_prefs` 既有 `{}` 表示「全默认」。
- **API 键 ↔ DB 存储**：

| API 组 | 键 | DB 存储 | 形态 |
|---|---|---|---|
| 外观与显示 | `appearance` | `display_prefs`（既有 JSONB，启用） | JSONB merge patch |
| 账单旗标阈值 | `bill_flags` | 4 个 `bill_*_bps` + `bill_window_trading_days` | typed 局部列更新 |
| 行情时段 | `market_sessions` | `market_session_prefs`（新 JSONB） | JSONB merge patch |
| 概念过滤 | `concept_filters` | `concept_filter_prefs`（新 JSONB） | JSONB merge patch |
| 只读回显 | `benchmark_id`/`privacy_flags` | 既有列 | 本期只读（PATCH 422） |
| 版本 | `version` | 既有列（启用 CAS） | int |

> **分工理由**：阈值是「数值 + 范围 + 参与 `flags_hash`」的标量 → typed 获得 DB CHECK；外观/时段/概念是枚举/列表/纯展示 → JSONB 免迁移。PATCH 的类型分流由单一注册表承担（§4.1）。

### 3.2 四个配置组

> 写入侧强校验（未知键 422；JSONB 内未知键也 422）；读取侧宽容归一（历史 JSONB 未知键忽略、回落默认）。默认值定义在 `stock_contracts.preferences`，DB `DEFAULT` 与代码默认由单测锁定。

#### 3.2.1 `appearance`（存 `display_prefs`）

| 字段 | 类型 | 默认 | 校验 |
|---|---|---|---|
| `theme` | `light｜dark｜system` | `light` | 枚举；前端解析 system |
| `density` | `cozy｜compact` | `cozy` | 枚举 |
| `trend` | `red-up｜green-up` | `red-up` | 枚举；与前端 `Preferences.trend`/`sa.trend` 同名 |

#### 3.2.2 `bill_flags`（4 个 bps 列 + 1 个窗口列）

| API 字段 | DB 列 | 默认（字符串/整数） | 校验 | 影响的旗标（1.4） |
|---|---|---|---|---|
| `near_equal_tolerance_pct` | `bill_near_equal_tolerance_bps` | `"1.00%"` | 10–500 bps，2 位小数 | 状态桶边界：`addon_average` vs `addon_dip`/`addon_chase`；`reduce_breakeven` vs profit/loss；`full_exit_breakeven` vs profit/loss |
| `chase_margin_pct` | `bill_chase_margin_bps` | `"1.00%"` | 10–500 bps | `chase` |
| `stop_loss_margin_pct` | `bill_stop_loss_margin_bps` | `"1.00%"` | 10–500 bps | `stop_loss`（且需净卖价 < 持仓均价）与 `sell_drawdown`（破位但未低于均价） |
| `short_term_margin_pct` | `bill_short_term_margin_bps` | `"5.00%"` | 50–2000 bps | `instant_bottom`、`short_term_bottom`、`short_term_underwater`、`instant_top`、`short_term_top`、`short_term_sold_early`（**即时类与短期类共用**） |
| `window_trading_days` | `bill_window_trading_days` | `"5"`（**字符串**） | 1–20 的十进制整数字符串 | 全部价格类 + `short_term_underwater` 的取窗（前/后各 N 个行情日，不含当日） |

- 边界：百分比串 → `Scaled/Ratio` 解析为 bps 整数落库；窗口串 → `int()`；禁 float。**窗口用字符串**以规避前端 `Number()/parseInt`（eslint 零口径）。
- `first_entry`/`full_exit` 只依赖持仓关系，不受阈值影响；`addon_*`/`reduce_*`/`full_exit_*` 受 near_equal 影响；20 码全量由 `definition_version="1.4"` 词表维护。
- `flags_hash` 规范：`sha256(json.dumps({"chase_margin_bps":int,"near_equal_tolerance_bps":int,"short_term_margin_bps":int,"stop_loss_margin_bps":int,"window_trading_days":int}, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode())[:16]`；只在**生效值**上算，不夹带派生字段。
- `definition_version` 不变（1.4）；`is_default` 由服务端比较生效值 == 默认值（只读）。

#### 3.2.3 `market_sessions`（存 `market_session_prefs`，仅展示/提醒）

| 字段 | 类型 | 默认 | 校验 | 说明 |
|---|---|---|---|---|
| `sessions` | array<{open,close}> | 上午/下午两段 | **1–2 段**（A 股实际两段）；`HH:MM`；升序不重叠；数组整体替换 | 仅设置页文案 |
| `data_ready_after` | `HH:MM` 字符串 | `"16:30"` | `HH:MM` 且 ≥ 合并后最后一段 close | 设置页/提醒文案；**后端不用于判定数据可用性** |
| `session_notice_enabled` | bool | `true` | — | 仅前端决定是否展示 `market_open`/`sync_pending` 提示；**后端响应字段不变** |
| `timezone` | string | `"Asia/Shanghai"` | 只读（PATCH 传 → 422） | 全仓固定东八区 |

> **边界**：`valuation_end`、`_session_notice`、`market_live` 16:30/18:00 窗口保持部署常量；`Assets.tsx` 现有 15:00/待同步文案不变（新「数据就绪时间」只出现在设置页/可选提醒，不改资产页既有提示）。

#### 3.2.4 `concept_filters`（存 `concept_filter_prefs`）

| 字段 | 类型 | 默认 | 校验 |
|---|---|---|---|
| `mode` | `off｜hide｜exclude` | `off` | 枚举 |
| `excluded` | array<{code,name}> | `[]` | ≤100；code 非空 ≤32；同 code 去重；name ≤64；**数组整体替换**；`off` 时允许保留但不生效 |

- 存在性：**写时只告警**（HITL-R5）：未知 code 允许保存；`GET /settings` 与 `/sectors/overview` 按当前目录标 `resolved=false`（API 层查 `market.sector`，core 不做 DB 校验）。`GET /settings` 的 `concept_filters.excluded[]` 返回 `{code,name,resolved}`；PATCH 请求只接受 `code/name`，传 `resolved` → 422。
- `concept_filter_hash`：`mode != off` 时 `sha256(json.dumps({"mode":mode,"excluded":sorted(codes)}, ...))[:16]`；`off` 时为空串，不进 `inputs_hash`/ETag。
- `off` 时**响应与现状逐字段一致**（不加 `filters`、不加 `assumptions`）。

### 3.3 `review.trade_flag` additive（同迁移 `0014`）

| 变更 | 说明 |
|---|---|
| `ADD COLUMN flags_hash varchar(16) NOT NULL DEFAULT '<DEFAULT_FLAGS_HASH>'`；回填后 **`ALTER COLUMN flags_hash DROP DEFAULT`** | 默认字面量由 `stock_contracts.preferences.DEFAULT_FLAGS_HASH` 提供（单测断言 == `flags_hash(默认配置)`）；DROP DEFAULT 让「build 漏传 hash」立即失败而不是被默认掩盖 |
| `CREATE INDEX ix_trade_flag_user_hash (user_id, flags_hash, definition_version)` | 供月度/看板按 hash 过滤；**按 dedupe_key 批量读**走既有唯一键 `(user_id, trade_dedupe_key, flag_code, definition_version)` 前缀，不依赖本索引 |
| 唯一键**不变** | 构建为「目标 period 内 delete+insert」；`flags_hash` 进唯一键会允许旧配置行残留，破坏「区间内一套口径」 |

- 存量行按默认配置构建，回填默认 hash 后与现口径一致。
- **读路径**：`load_trade_flags(..., flags_hash=当前)`；`monthly_trade_flag_summary` 中「买卖笔数」查询**不带 hash 过滤**（保证旧月份仍有笔数），「旗标/状态计数」查询带当前 hash；`stale_months` 与 `months_without_data` 分开算（§4.4）。

### 3.4 迁移 `0014`、回滚与同步

- 文件 `backend/migrations/versions/0014_user_setting_preferences.py`，`revision="0014"`，`down_revision="0013"`，`SCHEMA_VERSION="0014_user_setting_preferences"`，`PREVIOUS_SCHEMA_VERSION="0013_review_trade_flags"`。
- 步骤：① `ALTER user_setting` 加 7 列 + 6 CHECK；② `ALTER review.trade_flag` 加 `flags_hash`（默认字面量）+ 索引；③ `DROP DEFAULT`；④ 戳 `schema_version`。
- 同步：`stock_api/version.py`；`platform/models.py`/`review/models.py`；`docs/DATABASE.md` 补 **0007~0010（当前也缺）+ 0013 + 0014**；`conftest` 已含 `user_setting`；`test_platform_rls` 显式 INSERT 依赖新列默认值（无需改）。
- 回滚：删 `trade_flag` 索引/列、`user_setting` 7 列与 CHECK，版本回 0013；`display_prefs` 旧数据不丢。

### 3.5 仓储与分层

| 层 | 改动 | 规则 |
|---|---|---|
| `stock_contracts.preferences`（新） | **声明式**组/键/存储/默认/只读/单位；`DEFAULT_FLAGS_HASH` 字面量；**不调用 core**（L1） | 零依赖 |
| `stock_core.preferences`（新） | `apply_patch(raw, patch) -> (new_raw, changed[])`（JSON Merge Patch + typed 映射 + 校验）、`resolve(raw) -> Effective`、`flags_hash()`、`concept_filter_hash()`、百分比/整数串解析（纯整数） | 纯函数、100% 覆盖 |
| `stock_platform` | `UserSettingRepository.update_patch(session, user_id, *, patch, expected_version)`：`SELECT`（无锁）→ core 合并 → 单条 CAS `UPDATE ... SET <出现列>, version=version+1, updated_at=now() WHERE user_id AND version=:expected`；0 行 → 409；行缺失先 `INSERT ON CONFLICT DO NOTHING` | 显式 user_id；CAS；RLS |
| `stock_review` | 旗标 build/read/summary **接收 `flag_config` 显式参数**；`insights/service.py` 的 `_trade_flag_statuses` 同参；`application/sectors.py` 接收 `concept_filter`；不 import 仓储 | 可确定性单测 |
| `stock_api`/`worker`/CLI | 组合：读行 → core 解析 → 传参；新增 `routers/settings.py`；worker `rebuild_trade_flags`；CLI build 逐用户读偏好 | 顶层可依赖所有层 |

> 合并/校验/哈希在 core；contracts 只做声明，避免 contracts→core 反向依赖。

---

## 4. 通用 PATCH 语义与配置口径

### 4.1 字段注册表（声明式）

| 组 | 键 | 存储 | 合并方式 | `null` | 只读 |
|---|---|---|---|---|---|
| `appearance` | `theme`/`density`/`trend` | `display_prefs` JSONB | RFC 7396 | 删除键→默认 | — |
| `bill_flags` | 4 个 `*_pct` + `window_trading_days` | 5 个 typed 列 | 只更新出现键 | 列恢复 DEFAULT | `flags_hash`/`is_default` |
| `market_sessions` | `sessions`/`data_ready_after`/`session_notice_enabled` | `market_session_prefs` JSONB | RFC 7396；`sessions` 数组整体替换 | 删除键→默认 | `timezone` |
| `concept_filters` | `mode`/`excluded` | `concept_filter_prefs` JSONB | RFC 7396；`excluded` 数组整体替换 | 删除键→默认 | — |
| 其他 | `benchmark_id`/`privacy_flags`/`version`/`updated_at` | — | — | — | 全部（PATCH 422） |

**顶层组**：`null` = 整组重置为默认；`{}` = 该组无变更。

### 4.2 合并算法（core 纯函数）

```python
def apply_patch(stored_raw: Mapping[str, Any], patch: Mapping[str, Any]) -> PatchResult:
    # 1) 顶层键必须 ∈ 注册表且可写；空 patch → 抛 EmptyPatch（API 映射 422）
    # 2) bill_flags：逐个出现键 → 百分比串/整数字符串解析 + 范围校验 → typed 列值；null → 列默认
    # 3) 其余 JSONB 组：merged = merge_patch(stored_raw[group] or {}, patch[group])（RFC 7396）
    #    嵌套键必须 ∈ 该组白名单；null 删键；数组整体替换并逐元素校验
    # 4) 用 merged 原始值解析生效值，做跨字段校验（sessions 排序/重叠、data_ready_after ≥ 最后 close）
    # 5) 对比 stored_raw 与 merged（规范化 JSON），无差异 → changed=[]
    # 6) 返回 new_raw、changed[]（点号路径，如 "appearance.theme"）
```

- **只校验请求涉及的组**；未出现组不重校验（避免历史悬空概念 code 阻塞无关更新）。
- 跨字段示例：只改 `sessions` 最后一档到 17:30 而 `data_ready_after` 仍 16:30 → 422；需同一 PATCH 同时提交。
- 错误路径：`bill_flags.chase_margin_pct`、`market_sessions.sessions`（数组下标写在 message 里，`errors[].field` 是点号路径）。

### 4.3 并发、幂等、缓存

- `If-Match` 必填，仅接受 `W/"<正整数>"`；`*`/缺失/非法 → 422；不匹配 → 409 `VERSION_CONFLICT`。
- 单事务：`SELECT`（无锁）→ 合并 → **单条** `UPDATE ... version=version+1 WHERE version=:expected`；0 行 → 409。首次缺行：`INSERT ... ON CONFLICT (user_id) DO NOTHING` 后再更新（保持 expected=1，最终 version=2，**不会 +2**）。
- `Idempotency-Key` 可选：`request_hash = sha256(method+path+If-Match+canonical_json(body))`；同 key 同 hash → 重放缓存 body/status + **由 `body.version` 重算 ETag** + `Idempotency-Replayed: true`；同 key 异 hash → 422 `IDEMPOTENCY_KEY_REUSED`；**无变更也要 `complete`**（否则 key 卡在 IN_PROGRESS）；同 key 并发第二次 → 409 `IDEMPOTENCY_IN_PROGRESS`。
- 无变更：200，`version`/ETag 不变，`X-Settings-Changed: false`；有变更 `true`。空 body `{}` → 422（对齐 `rules.py`）。
- 请求体：`application/json`；字段级上限（concept ≤100、字符串长度）足够；全局 body 上限沿用 nginx，不新增错误码。
- ETag：`W/"<version>"`（同 `rules.py:73-75`）。

### 4.4 旗标阈值：生效、重算与 stale

```
PATCH /settings {"bill_flags": {"chase_margin_pct": "2.00%"}}
  → 校验/合并 → 单条 UPDATE → version+1 → 审计 → flags_hash 变化
  → 读路径按新 hash：API-095 stale_months；API-092/093 flag_config.stale=true
（不自动重算）

make build-trade-flags ARGS='--month 2026-09'|'--month 2026'|'--month 2026-01-01..2026-12-31'
  → 逐用户读偏好 → evaluate_trade_flags(..., config) → 落 flags_hash
  → 仅覆盖该 period 区间（delete+insert）

POST /settings/trade-flags/rebuild (API-097)
  → 202；worker 逐月重算（同 CLI 函数）
```

- **读取口径**：
  - `load_trade_flags(session, user_id, dedupe_keys, *, flags_hash)`：只返回「当前 `definition_version` + 当前 `flags_hash`」行。
  - `monthly_trade_flag_summary`：买卖笔数查询**不加 hash 条件**；旗标/状态查询加当前 hash。
  - `stale_months`：窗口内存在当前 `definition_version` 行、但**没有任何当前 hash 行**的月份；`months_without_data`：窗口内完全没有行的月份；两者互斥。
  - API-092/093 `flag_config {thresholds..., flags_hash, is_default, stale}`：`stale=true` 表示「请求涉及的账单中存在**旧 `flags_hash` 行**」（即曾经构建过、但指纹不是当前配置）；从未构建过的账单不置 stale（否则每个未构建回合都会误报）。前端展示「阈值已更新，重算后更新」，不展示旧 hash 结果。
- **两套读取的一致性**：API-092/093 的 `status_counts` 为**现算**（始终当前配置）；`trade_flags` 为**落库**（按当前 hash 过滤）。`flag_config.stale=true` 只表示落库旗标待重算；此时前端提示「待重算」，不用落库旗标冒充当前口径（`status_counts` 仍可作为当前计数展示）。
- **术语表**：`definitions.py` 文案改「模板 + 服务端渲染」（`{window}`/`{span}`/`{window_ord}`/`{near_pct}`/`{chase_pct}`/`{stop_pct}`/`{short_pct}`，其中 `span=2×window`、`window_ord` 为英文序数），默认配置下渲染结果**逐字等于当前文案**（逐 code 黄金文本测试）；非默认时填入实际阈值（bps→紧凑百分比，如 250→`2.5`）。`trade_flag_glossary` 仅详情端点返回（列表端点现状无该字段），渲染用用户生效配置。
- **不联动**：`timing.chase/fly` 与回合洞察 `insights.flags` 保持独立。
- **regen**：只保留最新 hash（重建覆盖）；不保留旧口径（H-09）。

### 4.5 行情时段（仅展示/提醒）

- 用户级仅：`sessions`、`data_ready_after`、`session_notice_enabled`。
- 设置页展示「交易时段 09:30–11:30 / 13:00–15:00」与「盘后数据预计 16:30 后就绪（提醒时间）」；
- `session_notice_enabled=false` 时**前端**隐藏 `market_open`/`sync_pending` 提示；后端响应字段仍返回。
- **不得进入**：`valuation_end`/`_session_notice`/`data_as_of`/pending/同步窗口（H-02）。`Assets.tsx` 既有 15:00/待同步文案**不改**。
- 部署级 env 提升移出本期（HITL-R3）。

### 4.6 概念板块过滤（v2.1 修正语义）

| | `hide` | `exclude` |
|---|---|---|
| 行/TopN/others | 排除被选概念 | 排除被选概念 |
| `coverage/overlap/multi_tag/hhi_tag` | **全量**（不变） | **全量**（不变；数据质量轴不被用户偏好污染） |
| `caveat`（Σexposure>1） | 全量判定 | **全量判定**（多归属事实不被隐藏） |
| `unclassified` | 全量口径（不变） | 全量口径；**被排除持仓不计入未分类** |
| 新增披露 | `filters{mode,applied,excluded[]}` | `filters{...}` + `filtered_market_value` / `filtered_holding_count` |
| ETag | 含 `concept_filter_hash` | 含 `concept_filter_hash` |

- **`off` 严格零变化**：不加 `filters` 字段、不加 `assumptions`、`concept_filter_hash` 不参与 `inputs_hash`、ETag 不变。
- 实现落点：`_concept_overview` 行构造后、排序/截断前按 `excluded` 剔除行；`mv_by_concept` 的 `filtered_mv` 单独累计；`concept_exposure`（全量）保持不动 → coverage/overlap/caveat 天然不变；`unclassified = equity - tagged` 不变。
- ETag：`_inputs_hash` 增加 `concept_filter_hash` 仅当 `mode != off`；`routers/sectors.py:405-409` 自动变化；`Cache-Control: private, no-cache` 仅非 off 时加（避免默认行为变化）。
- 响应 schema：`SectorConceptBlockOut` 增 `filters`（`mode/applied/excluded[{code,name,resolved}]`）与 `filtered_market_value/filtered_holding_count`（仅 exclude 有值）；`SectorMissingCode` 增 `SECTOR_CONCEPT_FILTER_APPLIED`、`SECTOR_CONCEPT_FILTER_UNRESOLVED`（响应字段，**无 severity**）。
- 悬空 code：`resolved=false` + `SECTOR_CONCEPT_FILTER_UNRESOLVED`；不 5xx、不阻断。
- 目录不过滤（API-088 仍全量，`active_only` 默认 true、游标分页；设置页选择器需翻页或传 `active_only=false`）。
- 未来归因/月报若展示概念，必须复用同一过滤入参（R-09）。

### 4.7 外观与显示

- 首屏：localStorage 快路径不变（`sa.theme/sa.density/sa.trend`，`theme.ts`）；
- 登录后：`GET /settings` 的 `appearance`：
  - 若服务端 `display_prefs` 为 `{}`（从未设置）→ **保留本地值并向服务端播种**（一次 PATCH），避免存量用户被默认浅色覆盖；
  - 若服务端有显式键 → 服务端值覆盖本地并回写 localStorage。
- 切换：本地立即生效 → PATCH（只发变更键）；409 重取重试；失败保留本地并提示。
- `/auth/me.settings.display_prefs` 返回**生效值**（D-12），与 `GET /settings` 一致。

---

## 5. API 契约

### 5.1 端点总览

| API-ID | Method | Path | 用途 | scope | 幂等 | 同步/异步 | 优先级 |
|---|---|---|---|---|---|---|---|
| API-055 | GET | `/settings` | 设置总览（4 组生效值 + 只读回显 + `version` + ETag） | read | — | 同步 | P0 |
| API-096 | PATCH | `/settings` | 通用局部更新（typed + JSONB merge patch） | write | 支持 | 同步 | P0 |
| API-097 | POST | `/settings/trade-flags/rebuild` | 按当前阈值重算旗标 | write | **必需** | 异步 | P1 |
| API-087 | 修改 | `/sectors/overview` | additive：`concept.filters`/`filtered_*`/missing 码/条件 ETag | read | — | 同步 | P1 |
| API-095 | 修改 | `/dashboard/trade-flags` | additive：`flag_config` + `stale_months[]` | read | — | 同步 | P1 |
| API-092/093 | 修改 | `/rounds/insights`、`/rounds/{id}/insights` | additive：`flag_config`（含 `stale`） | read | — | 同步 | P1 |

- operationId：`getSettings`、`patchSettings`、`rebuildTradeFlags`。
- **总数**：端点 94→**96**（+API-096/097，API-055 已在册）；operation 62→**65**；2xx 示例 56→**59**；`EXPECTED_ALL_ENDPOINTS` 94→96；`EXPECTED_PERIOD2` 39→41；API-Catalog **§2 详细契约 39→42**；§6 API-055 行改为「200 含 4 组偏好 + version」。
- 新端点：`StrictQueryRoute`；错误 `ApiError`；无 query 参数。
- **admin 策略**：`/settings` 读写**不调用 `forbid_admin_trades`**（与 `/tokens` 一致，admin 可管理自己的偏好）；admin 访问 `/rounds`/`/sectors` 仍 403。文档与测试明确。

### 5.2 `GET /settings`（API-055）

- 请求：可选 `If-None-Match: W/"<version>"`；响应 200 `SettingsOut`，`ETag`。
- 行不存在：返回默认生效值 + `version=1`、`updated_at=null`（**不落库**）。
- 响应示例（节选）：

```json
{
  "version": 2,
  "updated_at": "2026-11-13T10:00:00Z",
  "appearance": {"theme": "dark", "density": "cozy", "trend": "red-up"},
  "bill_flags": {
    "near_equal_tolerance_pct": "1.00%",
    "chase_margin_pct": "1.00%",
    "stop_loss_margin_pct": "1.00%",
    "short_term_margin_pct": "5.00%",
    "window_trading_days": "5",
    "flags_hash": "9f2c1a7b3d4e5f60",
    "is_default": true
  },
  "market_sessions": {
    "sessions": [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}],
    "data_ready_after": "16:30",
    "session_notice_enabled": true,
    "timezone": "Asia/Shanghai"
  },
  "concept_filters": {"mode": "off", "excluded": []},
  "benchmark_id": "hs300_tr",
  "privacy_flags": {}
}
```

- `timezone/flags_hash/is_default` 为只读字段；PATCH 客户端必须剔除。

### 5.3 `PATCH /settings`（API-096，核心）

- 头：`If-Match: W/"<int>"` 必填；`Idempotency-Key` 可选；`Content-Type: application/json`。
- body 示例：

```json
{
  "appearance": {"theme": "dark", "density": null},
  "bill_flags": {"chase_margin_pct": "2.00%", "window_trading_days": "10"},
  "market_sessions": {"data_ready_after": "17:00"},
  "concept_filters": {"mode": "hide", "excluded": [{"code": "300900", "name": "低空经济"}]}
}
```

| 类别 | 规则 |
|---|---|
| typed（`bill_flags.*`） | 只更新出现键；`null` → 列默认；百分比串/整数字符串解析；`flags_hash` 变化 |
| JSONB 对象键 | RFC 7396 合并；`null` 删键回落默认；未出现键保持 |
| JSONB 数组键（`sessions`/`excluded`） | 整体替换，逐元素校验；`null` → 默认 |
| 顶层组 `null` | 整组重置默认 |
| 只读/未知键 | 422 `VALIDATION_ERROR`（点号路径） |
| 空 body `{}` | 422（无字段可改） |
| 值全等于当前 | 200，`version` 不变，`X-Settings-Changed: false` |

- 响应 200：完整 `SettingsOut`（与 GET 同构）+ 新 ETag；有变更 `X-Settings-Changed: true`。
- 错误：缺/非法 If-Match 422；旧版本 409；字段/跨字段 422；read scope 403；无凭证 401。

### 5.4 `POST /settings/trade-flags/rebuild`（API-097）

- 头 `Idempotency-Key` 必填；body `{"scope":"all"}` 或 `{"scope":"months","months":["2026-01"]}`；范围 ≤120 月。
- 202 + `Location`；worker kind `rebuild_trade_flags`（同 CLI 函数）；同参复用 job；错误 422/429/409。
- **实现口径（P1-2）**：`scope=all` 不接受 `months`（422）；`scope=months` 必须非空（422）；月份须 `YYYY-MM`、去重升序、≤120（422）。`scope=all` 取 `min/max(trade_date)` 的自然月区间并截断最近 120 月（`truncated=true`）；无交易 → job `succeeded` + `months=[]`（非失败）。job `result` 为 `{months[], months_count, trades, rows, flags_hash, truncated}`；逐月调用 `build_trade_flags_for_user(config=当前生效阈值)`，每月前检查取消。同 `scope_key=<user>:trade_flags` 且参数相同 → 复用在途 job（`X-Job-Reused: true`）；参数不同且任务在途 → 409 `JOB_ALREADY_RUNNING`。

### 5.5 additive 变更

- **GET /settings**：`concept_filters.excluded[]` 带 `{code,name,resolved}`（API 层查当前目录）；`resolved=false` 不报错。
- **API-087**（非 off 才出现）：`concept.filters{mode,applied,excluded[{code,name,resolved}]}`；`concept.filtered_market_value`/`filtered_holding_count`（exclude）；`missing[]` +2 码；`assumptions.concept_filter_hash`；ETag 条件变化；`Cache-Control` 条件设置。
- **API-095**：`flag_config {window_trading_days, near_equal_tolerance_pct, chase_margin_pct, stop_loss_margin_pct, short_term_margin_pct, flags_hash, is_default}` + `stale_months[]`。
- **API-092/093**：`flag_config`（含 `stale`）；详情 `trade_flag_glossary[]` 按生效阈值渲染；列表无 glossary 字段（现状）。
- **Schema 必改**：`SectorMissingCode` +2、`SectorConceptBlockOut`、`DashboardTradeFlagsOut`、`RoundInsightItemOut/PageOut/DetailOut`（否则 response_model 丢弃）。
- `GET /auth/me`：`display_prefs` 返回生效值，形状不变。

### 5.6 错误码 / 降级

| 类别 | 变更 |
|---|---|
| `ErrorCode` | 不新增（47 不变）；422/409/403/401 复用；field 为点号路径 |
| `WarningCode` | 不新增 |
| 板块 `missing[]` | +`SECTOR_CONCEPT_FILTER_APPLIED`、`SECTOR_CONCEPT_FILTER_UNRESOLVED`（响应字段、无 severity） |
| 200 降级 | `stale_months`/`filter_config.stale`/`filters` 均 200 语义 |

### 5.7 契约/门禁联动

| 项 | 现在 | 之后 | 位置 |
|---|---|---|---|
| API-Catalog §1 端点 | 94 | 96 | `API-Catalog.md:51` 标题+表格 |
| §2 详细契约 | 39 | **42** | `API-Catalog.md:154` |
| §6 验收矩阵 | 94 | 96 | `API-Catalog.md` §6 + API-055 行 |
| operation | 62 | 65 | `test_openapi_contract.py:88-89` |
| 2xx JSON 示例 | 56 | 59 | `test_openapi_contract.py:132` |
| `OPERATION_META` | 62 | +3 | `openapi_meta.py:376` |
| `EXPECTED_ALL_ENDPOINTS` / `EXPECTED_PERIOD2` | 94 / 39 | 96 / 41 | `gate_endpoint_coverage.py:28-29` |
| 越权矩阵 | — | `GET/PATCH /settings`、`POST rebuild` 全进 **`UNAUTHENTICATED_MATRIX`**；`_unauth_kwargs` 为 PATCH 补 `If-Match`+最小 body、为 rebuild 补 `Idempotency-Key`+body。**不存在 SCOPES_MATRIX**；无路径参数操作不需要 RESOURCE_MATRIX | `test_isolation_matrix.py:297,397` |
| SEED | 47 | 47 | `contracts/errors.py:313` |
| DEV-PLAN | — | Period2-19 + A.1 +2 行；头部计数 88/88+5 → 96 | `docs/DEV-PLAN-v2.md` |
| SDK/类型 | — | `make sdk`；`contract.ts` 字段断言 | `frontend/packages/api-client` |

---

## 6. 分层与改动清单

### 6.1 链路

```
GET /settings → require_user → 租户 session → repo.get_for_read（无行→默认视图）
  → core.resolve(raw) → API 定点/字符串化 → ETag
PATCH /settings → require_write → _parse_if_match → 租户 session
  → SELECT 行（无→INSERT ON CONFLICT DO NOTHING）
  → core.apply_patch → 无变更→complete idempotency→200（不 bump）
  → 单条 CAS UPDATE（typed 列 + JSONB + version+1 + updated_at）→ 审计 → 返回 SettingsOut
```

### 6.2 文件清单

| 层/文件 | 改动 |
|---|---|
| `contracts/preferences.py`（新） | 声明式注册表/枚举/默认值/`DEFAULT_FLAGS_HASH` |
| `core/preferences/`（新） | `patch.py`/`resolve.py`/`hash.py`/`units.py` |
| `core/trade_flags/constants.py` | 默认值 + `TradeFlagConfig`（不删现有常量语义） |
| `core/trade_flags/engine.py` | `evaluate_trade_flags(..., config)`（默认参数兼容） |
| `core/trade_flags/definitions.py` | 文案模板 + `render_description`（默认逐字一致）；修 `:279` 注释 |
| `platform/models.py` | `UserSetting` +7 列 |
| `platform/repositories/users.py` | `get_for_read` / `update_patch`（CAS） |
| `review/models.py` | `TradeFlag.flags_hash` |
| `review/trade_flags/service.py` | build/load/summary 接 config+hash；stale/counts 拆分 |
| `review/insights/service.py` | `_trade_flag_statuses` 接同一 config；`flag_config` 装配 |
| `review/application/sectors.py` | `build_sector_overview(..., concept_filter)`；行剔除/`filtered_*`/条件 inputs_hash |
| `review/application/assets.py` | **不改判定**；如接展示开关仅在前端 |
| `api/routers/settings.py`（新） | API-055/096/097 |
| `api/app.py` | 注册 settings router（**v2.0 漏项**） |
| `api/routers/sectors.py` | 读偏好→过滤；条件 ETag/`Cache-Control` |
| `api/routers/dashboard.py` / `rounds.py` | `flag_config`/`stale_months`/stale |
| `api/schemas.py` | `SettingsOut`/`SettingsPatchIn`；D-17 所有 additive 字段；`SectorMissingCode` +2 |
| `api/openapi_meta.py` | +3 operation 元数据/示例 |
| `api/cli.py` | build 逐用户读偏好；`--json` 摘要含 flags_hash |
| `worker/handlers.py` | `rebuild_trade_flags` |
| `migrations/0014_*` | 7 列+6 CHECK；trade_flag flags_hash+索引+DROP DEFAULT；版本戳 |
| `conftest.py`/`test_platform_rls.py` | 确认无需改（列有默认） |
| `docs/DATABASE.md` | 补 0007~0014 |
| `frontend/theme/theme.ts` + `App.tsx`/`main.tsx` | 服务端对齐（未设置时播种本地）、`trend` 键 |
| `frontend/pages/Settings.tsx` | 四组表单 + 脏字段 PATCH + 字符串/下拉 |
| `frontend/pages/dashboard/TradeFlagCharts*.tsx` | `stale_months`/`flag_config` 横幅/说明 |
| `frontend/pages/RoundDetail.tsx`/`RoundInsights.tsx` | `flag_config.stale` 提示 |
| `frontend/pages/Assets.tsx` | 可选：`session_notice_enabled` 隐藏提示（**不改文案**） |
| `frontend/api-client/http.ts`/`contract.ts` | 新端点/类型断言；304 不消费 body |
| `tests/*` | 见 §8；`test_api_095` 种子行接受默认 `flags_hash` |

> **不做**：`ledger` 解析器/预览/批次、导入页、`config.py`/`infra/.env.example`（env 提升移出本期）。

### 6.3 依赖与性能

- import-linter：contracts 零依赖；core→contracts；platform→contracts/core；review 不 import api；PATCH 合并 core，落库 platform。
- GET 1 次 PK SELECT；PATCH 1 次 SELECT + 1 次 UPDATE；`flags_hash`/`concept_filter_hash` 为微秒级哈希；rebuild 异步。

---

## 7. 前端设计（零口径）

### 7.1 设置页（`/settings`）

| 区块 | 内容 |
|---|---|
| 外观与显示 | 主题/密度/涨跌色（沿用现有分段控件；文案改为「已同步到云端」） |
| 账单旗标阈值 | 4 个百分比输入（字符串）+ 窗口下拉（1/2/3/5/10/20）+ 恢复默认 + 「改后需重算」与「立即重算」 |
| 行情时段 | 1–2 段时段行 + 盘后就绪时间 + 显示提示开关；注明「仅影响提示，不改变系统收盘口径」 |
| 概念板块过滤 | 模式（关闭/仅隐藏/不参与统计）+ 概念多选（API-088 目录，注意默认 `active_only=true` 与游标分页）+ 失效项标记（`resolved` 来自 GET /settings） |

### 7.2 外观首屏与同步

- localStorage 快路径不变；登录后 GET /settings：服务端 `{}` → 保留本地并播种；有值 → 覆盖本地。
- 本地立即生效 + 异步 PATCH（只发脏字段、剔除只读字段）；409 重取重试；失败保留本地提示。

### 7.3 PATCH 客户端

- 表单全部字符串；窗口用 `<select>`（不 `Number/parseInt`）；数组字段发全量；`null` 用于「恢复默认」。
- 提交前比较表单与初始值，只发变更字段；空变更禁用按钮。
- 成功后用响应回填并失效 `["sectors"]`（概念/时段变更时）。

### 7.4 看板/回合提示

- `stale_months[]` 非空：看板旗标图上方显示「以下月份按旧阈值计算：…」+ 重算入口；
- API-092/093 `flag_config.stale=true`：回合洞察显示「阈值刚更新，旗标待重算」，**不展示旧 hash 旗标**；
- 概念 `filters`/caveat 原样渲染（铁律 6）；`filtered_market_value` 的**数值由后端给出**，说明文案由前端固定拼接（后端不返回文案字段）。

### 7.5 测试锚点

`theme.test.ts:106-160`、`Settings.test.tsx:60-86`、`Sectors.test.tsx:213-217`、`Dashboard.test.tsx` 概念/旗标、`RoundInsights.test.tsx`、`Import` 不改；新增设置页四组、PATCH 脏字段、stale 横幅、首屏种子。

---

## 8. 测试与验收

### 8.1 测试矩阵

| 层 | 用例 |
|---|---|
| contracts | 注册表一致、默认值、`DEFAULT_FLAGS_HASH==flags_hash(默认)`、百分比/整数串解析、**不 import core**（import-linter） |
| core | `apply_patch`：typed 局部、JSONB 合并、`null` 删键/列默认、数组替换、未知键、空 patch、无变更 `changed=[]`、跨字段（sessions/data_ready）、`flags_hash`/`concept_filter_hash` 固定向量、百分比边界；100% 覆盖 |
| core 旗标 | 默认配置 20 码判定与现有测试一致；4 阈值各一组非默认手算；窗口变更对 pending 影响；1.4 新码（underwater/drawdown/sold_early）覆盖 |
| platform | `update_patch`：CAS 成功/冲突；首写缺行 `ON CONFLICT` 不 +2；typed+JSONB 单条 UPDATE 只 +1；RLS 直连；`migrate-check` |
| review 旗标 | build 落 hash；`load_trade_flags` 过滤；summary 笔数不带 hash、旗标带 hash；`stale_months`/`months_without_data` 互斥；insights `status_counts` 与 DB 行同 config；rebuild 幂等 |
| review 板块 | off 严格零变化（字段/ETag/assumptions 逐项比对）；hide 行/TopN/others 排除且聚合不变；exclude `filtered_*` 正确、unclassified 不含被排除、caveat 保留；悬空 code；非 off ETag 变化 |
| API | API-055/096/097 各 ≥3：默认、局部性、JSONB 合并、null、空 body 422、只读字段 422、未知键 422、If-Match 缺失/`*`/旧版本、304、幂等重放（ETag 由 body.version 重算、不重复 bump、no-op complete）、admin 可读写自身偏好、read PAT 403、rebuild 202/隔离 |
| 越权 | 三个新端点进 `UNAUTHENTICATED_MATRIX`；`_unauth_kwargs` 补 PATCH/rebuild 最小载荷；API-096/097 read-scope 403 |
| 前端 | 四组表单、脏字段 PATCH、窗口下拉、外观播种、stale 横幅、概念 `resolved`、「立即重算」（API-097 + 轮询成功刷新）；零口径 lint |
| 回归 | 默认偏好：指标/板块/旗标/资产/回合的 golden 与 API 断言零变化；`test_api_095` 种子行默认 hash 可读 |

### 8.2 手工验收（可执行版）

1. `/settings` 默认值；改主题 → 重登仍生效；`GET /settings` `version` 增加；`/auth/me.settings.display_prefs` 与 GET 一致。
2. 只改 `chase_margin_pct=2.00%` → 其他阈值/窗口不变、`flags_hash` 变；看板出现 `stale_months`（用本地已有旗标行的月份，如 `2026-09`，视本地数据）；跑 `make build-trade-flags ARGS='--month <该月>'` 后消失。
3. 概念过滤：先用 `GET /sectors?taxonomy=concept` 取两个真实 code → `mode=hide` 排除 → `/sectors` 概念页不出现且 TopN/覆盖与后端一致；切 `off` 恢复且 ETag 回到基线。
4. PATCH 语义：只发 `{"appearance":{"theme":"dark"}}` → 其他外观键不变；发 `{"appearance":{"density":null}}` → 回默认；空 body 422；只读字段 422。
5. 并发/幂等：两标签页同 If-Match → 一个 200、一个 409；同 key 同 body 重放 → version 只 +1 且 ETag 可重算。
6. 时段：只改 `data_ready_after` → 设置页文案变化；资产页 15:00/待同步文案与资产数字不变（后端单测断言 `valuation_end` 不读偏好）。

### 8.3 门禁

`make lint / typecheck / test-p2 / test-metrics / test-isolation / api-snapshot / api-check / sdk / migrate-check / gate-endpoint / gate-tenant / gate-rls / gate-golden-structure`。

---

## 9. 迁移、发布与回滚

1. 合并 0014 → `make migrate`（5433）→ `make migrate-check`。
2. 只加列/索引，旧列语义不变；`display_prefs` 由死字段转为外观真值源。
3. 默认偏好 = 现行为（DB 默认 + off），回归套件证明；无需功能开关。
4. 回滚：`downgrade` 删 7 列/CHECK/`flags_hash`；外观数据在 `display_prefs` 保留；前端无状态。
5. 备份：`user_setting` 纳入既有备份；`trade_flag` 可重建。

---

## 10. 里程碑（评审后）

| 里程碑 | 内容 | 估时 |
|---|---|---|
| P0-1 数据+PATCH 引擎 | 0014（7 列/6 CHECK/trade_flag hash）、ORM/仓储 CAS、contracts/core、GET/PATCH、审计、门禁 | 2.0 |
| P0-2 前端设置页 | 四组表单、PATCH 客户端、外观播种、测试 | 1.0 |
| P1-1 旗标接算 | engine/definitions、build/read/summary hash+stale、**insights status_counts 同 config**、CLI、API-092/093/095 additive、看板/回合 UI、手算回归 | 2.0 |
| P1-2 重算端点 | worker + API-097 + 202/幂等/进度 | 0.5 |
| P1-3 概念过滤 | 行剔除/filtered/条件 ETag/schema/missing、前端概念表单、测试 | 1.0 |
| P2 收尾 | docs/契约/SDK/手工验收 | 0.5 |
| **合计** | | **7.0 人日**（P0 3.0 / P1 3.5 / P2 0.5） |

---

## 11. 风险

| # | 风险 | 级别 | 缓解 |
|---|---|---|---|
| R-01 | 改阈值后旧结果被当新口径 | 高 | `flags_hash` 过滤 + `stale_months`/`flag_config.stale`；不展示旧 hash |
| R-02 | JSONB PATCH 并发丢失更新 | 高 | 单条 CAS；409 重试；幂等 key |
| R-03 | 术语文案与阈值漂移 | 高 | 模板渲染 + 默认逐字黄金测试 |
| R-04 | 概念 `exclude` 误伤 caveat/unclassified | 高 | v2.1 语义：全量聚合+caveat、`filtered_*` 披露、unclassified 不变；单测锁定 |
| R-05 | `off` 零变化被新字段/ETag 破坏 | 高 | off 不输出 filters/assumptions、不参与 hash；回归比对 |
| R-06 | 漏加 response schema 字段被静默丢弃 | 高 | D-17 清单 + 契约测试断言字段存在 |
| R-07 | 门禁必红（越权矩阵/计数） | 中 | §5.7 清单；`_unauth_kwargs` |
| R-08 | build 漏传 hash 被默认值掩盖 | 中 | 回填后 DROP DEFAULT |
| R-09 | 未来归因/月报绕过概念过滤 | 低 | 复用 `_concept_overview` 同一入参 |
| R-10 | 本地/服务端外观漂移/播种覆盖 | 中 | 「服务端 `{}` 才播种」+ 409 重取 |
| R-11 | 窗口/百分比前端转换违反零口径 | 中 | 字符串边界 + 下拉；lint |
| R-12 | 概念 code 悬空 | 中 | 写时告警 + 读时 `resolved=false`（HITL-R5） |
| R-13 | 文档/代码再漂移 | 中 | 本版已对齐 `5419813`；实现前重跑 `git log` 复核 |

---

## 12. 评审新增 HITL（未确认按推荐实现）

见 §0.C 的 HITL-R1~R6：阈值集合（4+1 vs 独立 instant）、`exclude` 聚合语义、env 提升移出本期、`/auth/me` 生效值、概念未知 code 告警不拒绝、窗口下拉+字符串。**其余评审问题均已在 v2.1 直接修复，无需人工决策**。

---

## 附录 A：默认配置（API 生效值）

```json
{
  "version": 1,
  "updated_at": null,
  "appearance": {"theme": "light", "density": "cozy", "trend": "red-up"},
  "bill_flags": {
    "near_equal_tolerance_pct": "1.00%",
    "chase_margin_pct": "1.00%",
    "stop_loss_margin_pct": "1.00%",
    "short_term_margin_pct": "5.00%",
    "window_trading_days": "5",
    "flags_hash": "<DEFAULT_FLAGS_HASH>",
    "is_default": true
  },
  "market_sessions": {
    "sessions": [{"open": "09:30", "close": "11:30"}, {"open": "13:00", "close": "15:00"}],
    "data_ready_after": "16:30",
    "session_notice_enabled": true,
    "timezone": "Asia/Shanghai"
  },
  "concept_filters": {"mode": "off", "excluded": []},
  "benchmark_id": "hs300_tr",
  "privacy_flags": {}
}
```

## 附录 B：PATCH 语义速查

| 请求键 | 存储 | `null` | 未提供 | 只读/未知 |
|---|---|---|---|---|
| `appearance.theme/density/trend` | `display_prefs` | 删键→默认 | 保持 | 未知 422 |
| `bill_flags.*_pct` | typed 列 | 列默认 | 保持 | 只读字段 422 |
| `bill_flags.window_trading_days` | typed 列（字符串边界） | 列默认 | 保持 | 非 1–20 整数字符串 422 |
| `market_sessions.sessions` | JSONB 数组 | 删键→默认 | 保持 | 元素非法 422 |
| `market_sessions.data_ready_after` | JSONB 键 | 删键→默认 | 保持 | `< 最后 close` 422 |
| `market_sessions.session_notice_enabled` | JSONB 键 | 删键→默认 | 保持 | — |
| `concept_filters.mode` | JSONB 键 | 删键→off | 保持 | 未知枚举 422 |
| `concept_filters.excluded` | JSONB 数组 | 删键→[] | 保持 | 元素非法 422（未知 code 仅告警） |
| 顶层组 `null` | 该组 | 整组重置 | 保持 | — |
| `benchmark_id/privacy_flags/version/updated_at` | — | — | — | 422 |
| `timezone/flags_hash/is_default` | — | — | — | 422（只读） |

## 附录 C：校验规则

| # | 规则 |
|---|---|
| V-01 | `extra="forbid"`；组/键 ∈ 注册表且可写；空 patch → 422 |
| V-02 | 百分比串 `^\d{1,2}\.\d{2}%$`；范围 10–500 / 50–2000 bps |
| V-03 | `window_trading_days` 十进制整数字符串，1–20 |
| V-04 | `sessions` 1–2 段、`HH:MM`、升序不重叠；`data_ready_after ≥ 最后 close` |
| V-05 | `timezone` 只读 `Asia/Shanghai`（PATCH 422） |
| V-06 | `concept_filters.mode` 枚举；`excluded` ≤100、code ≤32、去重、name ≤64；未知 code 仅告警 |
| V-07 | 跨字段校验基于合并后生效值，且只校验涉及组 |
| V-08 | 只读字段/未知键 422；点号路径 |
| V-09 | DB CHECK 兜底（4 bps + window/JSONB object） |

## 附录 D：契约与文档同步清单

- [ ] API-Catalog §1 计数 94→96 +2 行；§2 详细契约 39→42；§6 API-055 行改为 4 组偏好 + version；§7 计数；
- [ ] DEV-PLAN Period2-19 + A.1 +2 行 + 头部 88/88+5→96；
- [ ] `version.py`；`docs/DATABASE.md` 0007~0014；`conftest`/`test_platform_rls` 复核；
- [ ] `gate_endpoint_coverage.py` 96/41；`test_openapi_contract.py` 65/59；
- [ ] `openapi_meta.py` +3；`make api-snapshot`/`sdk`；`contract.ts`/`http.ts`；
- [ ] `test_isolation_matrix.py` 三个新端点 + `_unauth_kwargs`；不存在 SCOPES_MATRIX；
- [ ] D-17 所有 additive schema 字段；
- [ ] 顺手修 `core/trade_flags/definitions.py:279` 历史注释（若仍过时）与 sector 文档阈值笔误。

## 附录 E：证据索引（HEAD `5419813`）

- 旗标：`core/trade_flags/constants.py:18-37`、`definitions.py:23-368`、`engine.py:143-337`；`review/trade_flags/service.py:46,69,173,208-216,284-318,409-442`；`review/models.py:558-620`；`cli.py:1297-1304`；`metrics/context.py:104-134`；`insights/service.py:698-721,895-930`；`schemas.py:993-1011,1675-1701,1860-1901`；`dashboard.py:380`。
- 时段：`assets.py:598-630`；`market_live.py:76-77,790-798`；`pages/Assets.tsx:117-135`。
- 概念：`application/sectors.py:314-334,390,991-1145`；`routers/sectors.py:356-409,423-426`；`core/sector/concepts.py:29-72`；`schemas.py:1026-1057,1085-1108,1171-1191`；`market_models.py:112-144`；`sector_live.py:1490-1520`。
- 偏好/前端：`platform/models.py:253-266`；`platform/repositories/users.py:60-78`；`routers/auth.py:269-281`；`0002_platform_tenant_rls.py:61-76`；`eslint.config.js:15-32`；`theme.ts:12-35`；`index.html:7-26`；`pages/Settings.tsx:40-194`。
- 契约/门禁：`API-Catalog.md:51,108-111,154,705,736-743`；`openapi_meta.py:376`；`test_openapi_contract.py:88-132`；`gate_endpoint_coverage.py:28-29`；`test_isolation_matrix.py:297-422`；`errors.py:313`；`version.py:4`。
- 幂等/并发：`platform/idempotency.py:29-120`；`platform/models.py:188-208`；`review/repositories/assets.py:67-80`（CAS 范式）。
- 评审证据（临时）：`var/review-prefs/`（gitignored）。
