# 用户偏好配置设计 · 独立交叉评审记录与修订处置（2026-11-13）

> 评审对象：[用户偏好配置 v2.0 设计稿](../architecture/2026-11-11-user-preferences.md)（评审时基线 `311c9e5`；修订后 v2.1 基线 `5419813`）
> 评审轮次：v2.0 → **v2.1（评审修订版）**
> 评审方式：3 名独立评审员（架构/数据/契约；API/并发；业务口径/测试/前端），只读代码/文档/迁移/门禁，**未修改跟踪文件、未跑 DB/网络**；证据落 `var/review-prefs/`（gitignored）。
> 结论：3 名评审均为 **REQUEST_CHANGES**；P0 全部修复、P1 全部采纳或按 HITL 推荐处置；**v2.1 可进入实现**，仅 §5 的 HITL-R1~R6 待人工确认（未确认按推荐实现）。
> **关键前提**：评审期间仓库已从 `311c9e5` 前进到 `5419813`（trade-flags 1.4 / 20 码、回合洞察算化、dashboard 新旗标系列、板块行 holdings），v2.0 的事实基线已过期；v2.1 已重新对齐。

---

## 1. 评审范围与判决

| 评审员 | 关注面 | 判决 | 主要 P0 |
|---|---|---|---|
| `reviewer-arch`（架构/数据/契约） | 迁移、RLS、分层、契约计数、门禁、OpenAPI | **REQUEST_CHANGES** | 基线漂移；additive schema 未登记；`off` 零变化与 ETag 矛盾；越权矩阵门禁必红 |
| `reviewer-api`（API/并发） | PATCH 语义、CAS、幂等、ETag、错误路径 | **REQUEST_CHANGES** | 基线漂移；越权矩阵不全；并发规范自相矛盾；幂等重放/no-op 缺陷 |
| `reviewer-domain`（业务口径/测试/前端） | 旗标口径、时段、概念、前端零口径、验收 | **REQUEST_CHANGES** | 旗标清单/阈值/版本全错；回合洞察第二套现算；月度聚合 hash 过滤副作用；概念 `exclude` 破坏 caveat/unclassified |

> 核对通过（评审未提出异议）：`user_setting` 6 列/RLS/懒创建、`/auth/me` 四字段、If-Match/ETag/304 既有范式、`require_write`、`StrictQueryRoute/StrictModel`、`errors[].field` 点号拼接、`SEED_ERROR_COUNT=47` 不变、Catalog 94/operation 62/JSON 56/`EXPECTED_PERIOD2=39`、迁移 0014 编号、CAS 写法可复用 `review/repositories/assets.py:67-80`。

---

## 2. P0 处置（全部接受并修订）

| # | 评审问题 | 处置 | v2.1 位置 |
|---|---|---|---|
| 1 | **基线漂移**：设计按旗标 1.1/17 码/5 独立阈值（含 instant）；HEAD 为 **1.4/20 码（买 8/卖 12）/4 个 bps + window**，`INSTANT_MARGIN_BPS` 已删；`TREND_FLAG_CODES` 9 码、`STACKED` 10 码；回合洞察已改计数口径；板块行新增 `holdings` | 全面重写 §2.3/§3.2.2/§4.4：4 个 bps 列 + window；flag_config/文案/索引/测试按 20 码重算；`bill_instant_margin_bps` 删除 | §2.3、§3.1-3.2、§4.4、§8.1 |
| 2 | **回合洞察第二套现算**：`insights/service.py:698-721` 的 `_trade_flag_statuses → evaluate_trade_flags` 生成 `status_counts`，与落库读取并存；改阈值后同响应两套口径 | 两者必须共用同一 `flag_config`/`flags_hash`；`insights/service.py` 进改动清单；API-092/093 增加 `flag_config.stale` | §3.5、§4.4、§6.2、§8.1 |
| 3 | **月度聚合 hash 过滤副作用**：`monthly_trade_flag_summary` 若整体按 hash 过滤，买卖笔数清零、月份落入 `months_without_data`；`stale_months` 未定义；CLI period 支持年/区间，delete 只覆盖区间 | 笔数查询**不带** hash；旗标/状态查询带当前 hash；`stale_months` 与 `months_without_data` 互斥并给算法；明确「区间内一套口径」 | §3.3、§4.4、§8.1 |
| 4 | **概念 `exclude` 破坏口径**：在 `concept_exposure` 前过滤会令 caveat/`SECTOR_CONCEPT_MULTI_MEMBERSHIP` 消失，被排除持仓落入 `unclassified` | v2.1 重定义为：行/TopN/others 排除；**聚合/caveat/未分类保持全量**；exclude 另给 `filtered_market_value`/`filtered_holding_count` | §4.6、§5.5、§8.1（**HITL-R2** 复核语义） |
| 5 | **additive 响应 schema 未登记**：`SectorMissingCode` Literal、`SectorConceptBlockOut`、`DashboardTradeFlagsOut`、`RoundInsight*Out` 不加字段会被 `response_model` 丢弃 | 新增 D-17 清单并写入 §5.5/§6.2/附录 D | §0.A D-17、§5.5、§6.2 |
| 6 | **`off` 零变化 vs ETag 矛盾**：v2.0 无论 off 与否都加 `concept_filter_hash`/`filters`/`assumptions`，与「逐字段一致」冲突 | `off` 严格零变化：不加字段、不进 `inputs_hash`/ETag、不加 assumptions；仅 `mode != off` 才变化 | §4.6、§1.2、§8.1 |
| 7 | **越权门禁必红**：`gate_tenant_coverage` 要求所有受保护 operation 进 `UNAUTHENTICATED_MATRIX`；v2.0 只提 API-096，且引用了不存在的 `SCOPES_MATRIX` | 三个新端点（GET/PATCH settings、rebuild）全进 `UNAUTHENTICATED_MATRIX`；`_unauth_kwargs` 补 PATCH（If-Match+body）/rebuild（Idempotency-Key+body）；删除 SCOPES_MATRIX 表述 | §5.7、§8.1、附录 D |

---

## 3. P1 处置（全部采纳）

| 主题 | 处置 | 位置 |
|---|---|---|
| 首次 PATCH / 行缺失 | `SELECT`（无锁）→ `INSERT ... ON CONFLICT DO NOTHING` → 单条 CAS；不会 PK 500、不会 version+2；删除 `get_or_create_for_update` 与「不做 FOR UPDATE」矛盾 | §4.3、§6.1 |
| 幂等缺陷 | `request_hash` 含 If-Match+body；重放用缓存 body.version 重算 ETag；**no-op 也 complete** 防 IN_PROGRESS 卡死；同 key 并发 409 明确 | §4.3 |
| 单条 UPDATE | typed 列 + JSONB 合并结果在**同一条 UPDATE** 落库，version 只 +1 | §3.5、§4.3 |
| `null` raw 可观测 | `/auth/me.settings.display_prefs` 返回**生效值**（形状不变），与 GET /settings 一致（HITL-R4） | §0.A D-12、§4.7 |
| 跨字段与单键 null | 只校验 patch 涉及的组；单键 null 与自定义 sessions 冲突需同一 PATCH 同时提交（示例写出） | §4.2 |
| 概念悬空 code 阻塞 | 写时**只告警**（未知 code 可保存），读时 `resolved=false`（HITL-R5） | §3.2.4、§4.6 |
| `off` 下 excluded 落库/hash | 允许保留列表但不生效；`off` 时 `concept_filter_hash` 为空、不进 ETag | §3.2.4、§4.6 |
| 错误路径格式 | `errors[].field` 为点号路径（数组下标写 message），删除 `[0]` 写法 | §4.2、附录 C |
| API-092/093 旗标消失 | 增加 `flag_config.stale`；前端提示「阈值已更新，待重算」，不展示旧 hash 结果 | §4.4、§7.4 |
| 窗口零口径 | `window_trading_days` 边界改**十进制字符串**；前端预置下拉（1/2/3/5/10/20），禁用 Number/parseInt（HITL-R6） | §3.2.2、§7.3 |
| 外观键名 | API `appearance.trend` 与前端 `Preferences.trend`/`sa.trend` 对齐；删除 `trend_color` | §2.2、§3.2.1 |
| Assets 文案不实 | 删除「已有预计 16:30 后可同步」；明确现有 15:00/待同步文案**不改**，新「数据就绪时间」只用于设置页/提醒（HITL-R3 移出 env 提升） | §2.4、§4.5、§7.1 |
| `session_notice_enabled` 消费点 | 仅前端隐藏提示；后端 `session_notice` 响应字段不变 | §4.5 |
| 前端缺展示 | 看板 `TradeFlagCharts` 增加 `stale_months`/`flag_config` 横幅；回合详情/洞察增加 stale 提示；设置页四组表单 | §7.1-7.4、§6.2 |
| 测试矩阵缺口 | 补：首写并发、空 body、只读字段、`null` 删键、数组替换/空数组、off 零变化逐项、exclude 聚合不变、stale 双集合、幂等重放 ETag、`_unauth_kwargs`、20 码默认回归 | §8.1 |
| 手工脚本不可执行 | 改为可执行步骤（真实概念 code 从 API-088 取；stale 月份用本地已有数据；`valuation_end` 用后端单测断言而非 API 透出） | §8.2 |
| R-12 data_as_of「统一修复」错误 | 删除该修复项（旗标 build 的 data_as_of=窗口内 bars 最大日是既有语义，不应在本期改） | §11 |
| contracts 校验器越层 | contracts 只做声明式注册表，校验/合并/哈希全部在 core（避免 contracts→core） | §3.5、§4.1 |
| flags_hash DEFAULT 掩盖漏传 | 回填后 `ALTER COLUMN flags_hash DROP DEFAULT` | §3.3 |
| 契约/验收行残留 | API-055 §6 行改为 4 组偏好 + version；§2 详细契约 39→42；DEV-PLAN 头部 88/88+5→96；DATABASE 补 0007~0014 | §5.7、附录 D |
| `pref_fingerprint` 命名冲突 | 删除 `pref_fingerprint`，只保留 `flags_hash` 与 `concept_filter_hash`，并给规范 JSON 定义 | §0.A D-05、§3.2.2、§3.2.4 |
| 术语表模板格式 | 定义为占位符模板（`{window}/{near_pct}/{chase_pct}/{stop_pct}/{short_pct}`），默认逐字相等测试 | §4.4 |
| admin 策略未定 | 明确 `/settings` 不调用 `forbid_admin_trades`（与 tokens 一致），admin 可管理自身偏好 | §5.1 |
| API-088 选择器 | 设置页多选取目录需注意 `active_only` 默认 true、游标分页 | §4.6、§7.1 |
| `SectorMissing` 无 severity | 删除「info 码/severity」表述；2 个码作为响应字段（无 severity） | §5.5、§5.6 |

---

## 4. P2 处置 / 明确延后

| 项 | 处置 |
|---|---|
| API-Catalog §2 详细契约 39→42、DEV-PLAN A.1 头部、DATABASE 缺 0007~0010 | 纳入附录 D 同步清单 |
| `filters.present` v2.0 与 §3.2.4 不一致 | v2.1 统一为 `filters{mode,applied,excluded[]}`，删除 `present` |
| ORM smallint / 索引 | `bill_window_trading_days smallint`；索引 `(user_id, flags_hash, definition_version)` |
| `http.ts` 304 `data: undefined` | 前端设置页不发 `If-None-Match`（GET 直接 200）；304 仅契约支持 |
| `openapi_meta` 示例落「示例」 | 按 operation 注册真实示例（SettingsOut/202 JobAccepted） |
| 无 body 上限 / `If-Match:*` | 字段级上限 + nginx 全局限流；`*` 拒绝 422 |
| HITL-R3 env 提升 | **移出本期**（非目标）；不在文件清单/里程碑中 |
| HITL-R2 exclude 语义 | 按 A 实现（全量聚合 + filtered 披露），B 作为备选待确认 |
| HITL-R1 阈值集合 | 按现行 1.4 的 4+1；若需独立 instant 需另立设计并 bump `definition_version` |

---

## 5. 评审新增 HITL（未确认按推荐实现）

| # | 问题 | 推荐 | 影响 |
|---|---|---|---|
| HITL-R1 | 阈值按现行 4 个 bps + window，不恢复独立 instant | A（现行） | B 需改 core 判定 + bump 1.5 |
| HITL-R2 | `exclude` 聚合语义：全量聚合 + `filtered_*` 披露 vs 过滤聚合 + `unfiltered` 全量块 | A | 影响 §4.6/API-087 字段与测试 |
| HITL-R3 | 部署级 `MARKET_*` env 提升是否本期 | A 移出本期 | B 需 config/env/CLI/资产链路全改，+1~2 天 |
| HITL-R4 | `/auth/me.display_prefs` 返回生效值 | A 是 | B 保持 raw 但与 GET 双口径 |
| HITL-R5 | 概念未知 code 写时告警 vs 422 | A 告警 | B 会让历史悬空 code 阻塞保存 |
| HITL-R6 | 窗口输入下拉+字符串边界 | A 是 | B 需前端数字解析，违反零口径 |

---

## 6. 结论

- **v2.0 不可实现**（基线过期 + 7 项 P0）；**v2.1 已修复全部 P0/P1**，并重新对齐 `5419813`。
- **待实现**；实现前需确认 HITL-R1~R6（默认按推荐，不阻塞开工）。
- 残余风险：见设计 §11（R-01~R-13），其中 R-04/R-05 由 v2.1 的单测与「off 严格零变化」锁定。
- 评审证据：`var/review-prefs/`（gitignored）；本记录随设计同版本入库，后续改动阈值/表结构/概念语义须追加修订并 bump 设计版本。
