# 导入映射重构设计 · 独立交叉评审记录与修订处置（2026-11-13）

> 评审对象：[导入重构设计 v0.1](../architecture/2026-11-13-import-mapping-refactor.md)（评审基线 1264~1292 行；评审期间作者同步修订）
> 评审轮次：v0.1 → **v0.2（评审修订版）**
> 评审方式：2 名独立评审（后端/数据/契约；前端/交互/可测试性）+ 作者自检，只读代码/文档/迁移/门禁，**未修改跟踪文件、未跑 DB/网络**。
> 结论：2 名评审均为 **REQUEST_CHANGES**；P0 全部修复、P1 全部采纳或明确延后；**v0.2 可进入实现**。人工已批准 H-1/H-2（见 §5）。

---

## 1. 评审范围与判决

| 评审员 | 关注面 | 判决 | 主要 P0 |
|---|---|---|---|
| `reviewer-backend` | 数据模型/迁移/RLS、解析器、API 契约、撤销、门禁 | **REQUEST_CHANGES** | 模板写入时机；generic 静默丢华泰语义；v1 兼容不成立；真实华泰 13 列别名覆盖不足 |
| `reviewer-frontend` | shadcn/Tailwind 接入、向导状态机、a11y、测试/预算 | **REQUEST_CHANGES** | `preflight:false` reset/Portal 逃逸；CSS 变量全局污染；Switch 语义与“继续”矛盾；`types[]` 语义互斥 |
| 作者自检 | 文档内部一致性、计数、错误体形状 | — | RFC 7807 `errors[]` 形状、Category 标签来源、错误码计数、路由顺序 |

核对通过（评审未提出异议）：列键/签名设计、L1/L2/L3 方向、`mapping_items`/`type_resolutions` 快照、H-2 撤销矩阵、`other_fee` 双档错误、`reverted` 整数计数、现有 10 条现状断言（G1~G10）与代码基本一致。

---

## 2. P0 处置（全部接受并修订）

| # | 评审问题 | 处置 | v0.2 位置 |
|---|---|---|---|
| P0-1 | **模板写入时机矛盾**：§3.6 说“保存 mapping 后更新模板”，但 `origin_batch_id` 只有在 commit 才有真实批次；按原稿撤销 origin 永远删不到模板（需求 4 失效） | 明确 **`PUT mapping/types` 只写 preview 草稿**；`import_template`/`import_bill_type` **只在 commit 成功时写**；`origin_batch_id` 取批次 id；补“PUT 后模板/类型仍为 0”的验收 | §3.6、§7.2、§9.1 |
| P0-2 | **generic profile 静默丢失华泰语义**：`[省心投]` CASH 改写、货基推演被跳过，会复现 2026-10-29 D14 错账；且 API-010 `broker` 默认从 huatai 改 auto 违反既有契约 | generic **fail-closed**：货基无余额/省心投摘要 → `IMPORT_PROFILE_REQUIRED`（422）；`broker` 在 v2 默认 `auto`，**legacy 默认仍为 huatai**；可转债口径改回“review 层判定，与 profile 无关” | §6.2、§7.1 |
| P0-3 | **v1 兼容不成立**：新 preview 一律 `stage=mapping`，旧客户端/既有 30+ 导入测试无法一步 commit；§10.2“回滚 24h”自相矛盾 | 新增 **`X-Import-Flow: v2` 兼容开关**：不带该头 → `stage=legacy`，沿用旧严格解析/旧 commit/默认 huatai；带 v2 → 新流程。新前端显式带 v2；旧测试不改 | §6.2、§7.2、§10.2、§9.1 |
| P0-4 | **`FIELD_ALIASES` 覆盖不足**：真实华泰账单 13 列含 `日期/成交均价/股东账号`，合成 12 列 fixture 测不到；漏 `account` 会改变 `dedupe_key` → 重导重复 | 附录 A 改为 `COLUMN_ALIASES` 全量迁移（唯一有意差异：`备注→note`）；新增 `FIELD_ALIASES ⊇ COLUMN_ALIASES` 单测；验收加真实 13 列 fixture，断言 `account` 映射、`币种` ignored | 附录 A、§9.1 |
| P0-5 | **`preflight:false` + scoped reset 不足**：`border-style` 仍为 none；`button/input/select` 无 font inherit；Radix Portal 逃逸 `.import-wizard` | 补完整 scoped reset（`border-style:solid`、font/background/border 重置）；所有 Radix Portal 挂到 `#import-portal[data-import-ui]` 并传 `container`；增加视觉验收清单 | §8.2 |
| P0-6 | **CSS 全局污染**：`shadcn add` 默认写 `:root` 变量，`--muted/--border` 与 `tokens.css` 同名且路由 CSS 永不卸载 | `components.json` 显式 `cssVariables:false`；`import.css` 禁止 `:root`/全局元素选择器；只允许 `[data-import-ui]` 作用域；加“访问 /import 后再访问 /trades、/dashboard”视觉回归 | §8.2、§9.2 |
| P0-7 | **Switch 语义与“继续”矛盾**：一处说缺 L1 直接定位（按钮可点），测试说按钮禁用；若 off=忽略，默认全关时一次点击会“忽略全部列”，破坏 D-07 二次确认 | 明确 **off = 未确认，不是忽略**；`ignored[]` 只来自显式「忽略本列」；「继续」在 L1/L1-trade/L2 未满足时禁用并定位第一处；满足后才允许弹未确认列二次确认 | §0 D-05/D-07、§8.3、§8.5 |
| P0-8 | **`types[]` 语义互斥**：一处“只列未命中”，一处字段表含 `needs_confirmation=false` 的已知类型；N 无来源 | 统一为 **`types[]` 恒返回全量 + `needs_confirmation/confirmed`**；前端折叠已确认，`N = !needs_confirmation` 的数量；`save_mapping` 重聚合后自动套用历史类型，仅未命中需确认 | §4.4、§6.2、§6.3、§8.3 |

---

## 3. P1 处置（全部采纳或明确延后）

| # | 问题 | 处置 | 位置 |
|---|---|---|---|
| P1-1 | 费用合并余额链与 `reconcile` 口径不一致（unknown/空余额/多账户） | 复用 `reconcile` 排序/跳过规则；不按账户分组；多账户 + 合并费用 → 422 `IMPORT_FEE_MERGE_VERIFY_FAILED`，提示拆分 | §3.4 |
| P1-2 | `parsed+quarantined` 与 `inserted+skipped+discarded` 不守恒 | 删除“四者相加=parsed”表述；明确 `parsed` 不含解析隔离、`discarded` 独立计数，结果页分别展示 | §0 D-12 |
| P1-3 | 空 `summary` 导致 `type_key=''` 违反 CHECK；>64 字符入库失败 | 空 summary 走派生键；解析器把 summary 截 64、note 截 200，原文留 `raw`；type_key 用入库值同函数归一 | §4.1 |
| P1-4 | 表头探测依赖 plan/规则 → 同文件不同时间不同签名；合并单元格承诺不可实现 | 表头探测改为纯 headers 函数 + 确定性 tiebreak；`header_row_index` 持久化到 preview/batch，commit 只用快照；删除“合并单元格检测”承诺 | §5.2、§7.1 |
| P1-5 | 类型映射“首次引入”判定漏“后续批次修改” | 增加 `last_write_batch_id`；撤销删 `last_write_batch_id=batch` 的映射（新增或修改）；`origin_batch_id` 仅审计 | §5.1.3、§7.2、§7.4 |
| P1-6 | `GET /imports/templates` 会被 `GET /imports/{batch_id}` 吞 | 固化路由顺序：templates 静态段先注册；补路由顺序回归测试 | §6.1 |
| P1-7 | 无 `GET preview`，409/410/刷新无法恢复 | 新增 **API-106 `GET /imports/previews/{preview_id}`**（200 + ETag / 404 / 410，无副作用、不延长 TTL） | §6.1、§6.2.1 |
| P1-8 | 乐观锁偏离 API-Catalog §0.3（If-Match） | API-098/099 必填 `If-Match: "<mapping_version>"`，不匹配 → 409 `VERSION_CONFLICT`；删除 `IMPORT_MAPPING_STALE`；body `mapping_version` 可选且须与头一致 | §6.3、§6.4、§6.8 |
| P1-9 | `reverted.mapping_deleted: bool` 放进 `dict[str,int]` 会变 1/0 | 改为顶层 `mapping_revoked{mapping_deleted,types_deleted}`；`reverted{inserted,updated}` 保持整数 | §6.7、§7.4 |
| P1-10 | 新 DTO 字段只有散文，无法落 pydantic/SDK | 增加“新增 DTO 字段表”（PreviewOut/types/verification/blocking/BatchOut/RevokeOut） | §6.2 |
| P1-11 | 门禁连带遗漏：端点总数/DEV-PLAN/隔离矩阵/app.py/RLS | §6.9 增补 `EXPECTED_ALL_ENDPOINTS=105`、DEV-PLAN A.1、三类隔离矩阵、admin router 注册、`gate-rls`；删除不存在的 `operationId.lock.json` 引用 | §6.9 |
| P1-12 | 改映射重导只 warning，仍会产生重复行 | 新增 `mapping_fingerprint`（mapping+date_format 哈希）；同 `file_sha256` 不同指纹 → `requires_reimport_ack`；commit 必须 `replace_range` 或 `acknowledge_reimport=true`，否则 409 `IMPORT_REIMPORT_CONFLICT` | §5.2、§6.7、§11 R-10 |
| P1-13 | 撤销运行中批次后 worker 仍写模板/类型；`last_batch_id` 回退 origin 跳过中间批次 | 写记忆前重读 `revoked_at`，已撤销则跳过；`last_batch_id` 重算为同签名存活批次中 `created_at` 最大者，无则 origin | §7.2、§7.4 |
| P1-14 | L1 含代码/数量/价格，与“所有平铺账单”及 cash 行矛盾 | 引入 **L1-trade**：代码/数量/价格在 trade mode 必需；纯资金流水 cash mode 可缺，但 amount + 非交易摘要必需；L1 仅日期 | §0 D-06、§3.1 |

---

## 4. P2 处置 / 明确延后

| # | 问题 | 处置 |
|---|---|---|
| P2-1 | `--` 费用归一位置 | v2 plan 读取层归一为 0；legacy `_decimal` 不改 |
| P2-2 | CHECK/默认值漏洞 | `discarded` 与 `category` 互斥 CHECK；`priority` CHECK；`is_system` 默认 false（seed 显式 true）；`user_id` 加 FK |
| P2-3 | 可转债 profile 事实错误 | 删除“generic 手/张偏差”；×10 在 review 层按代码判定 |
| P2-4 | conftest 清库表清单不同源 | conftest 改 import `LEDGER_TABLES` |
| P2-5 | legacy preview broker 回填 generic | 迁移 `broker='huatai'` + `stage='legacy'` |
| P2-6 | 规则排序缺 broker 特异性；权重阈值口径 | broker 专属优先；阈值改 `Σ命中权重/Σ全部指纹权重 ≥0.6` |
| P2-7 | `mapping_version` 递增/内容一致口径 | 仅 API-098 递增；canonical JSON 逐字比较 |
| P2-8 | `estimated=0` 误导；样例只有 1 条 | 加 `estimated_available`；`columns[].sample` 2~3 条 |
| P2-9 | 前端 Progress 双进度条 | 只用 `JobProgress`；不引入 shadcn Progress |
| P2-10 | API-100 无分页；API-014 枚举；API-010 `mode` | 加 `limit/cursor`；§6.9 登记 API-014 枚举与 API-010 参数修正 |
| P2-11 | review 层 category 白名单 | 改用 `BillCategory` 或加一致性单测 |
| P2-12 | worker 内 409 不成立 | 模板 upsert 用 `ON CONFLICT DO UPDATE` + warning；HTTP 409 只发生在 API 同步阶段 |
| P2-13 | 类型行“分类+废弃”并存 | UI 行状态机：选分类清废弃、开废弃清分类、切换重置确认 |
| P2-14 | `Import.test.tsx` 无 QueryClientProvider / mock 不全；`Imports.test` 锁计数；`contract.test` 锁 30 条 | §8.5 存量测试迁移：QueryClient 包裹、mock 补齐、计数改新口径、独立 `IMPORT_V2_ENDPOINTS` |
| P2-15 | jsdom 能力不足 | 补 `PointerEvent/ResizeObserver/matchMedia/scrollIntoView/setPointerCapture` 等；Select 用原生替身，真实 Radix 交互留手工/P1 Playwright |
| P2-16 | 工程接入失败：`@types/node`、tsconfig.node include、ESM `require` | 加 `@types/node`；include 配置；`import animate from "tailwindcss-animate"` |
| P2-17 | import chunk 预算不可执行 | 开 `build.manifest`，按 Import 可达 chunk 求和；先做空向导 spike 再定预算（当前≤80KB JS/20KB CSS 为上限） |
| P2-18 | 颜色/圆角/字体映射错位；alpha 修饰符对 var() 无效 | `background:var(--bg)`、radius 用 `--r-*`、font 映射；去掉 `/90`，遮罩用 rgba |
| P2-19 | 通用类名冲突 `.table` | Tailwind `prefix:"tw-"`，shadcn CLI 同步前缀 |
| P2-20 | legacy stage 前端无分支 | 未知/legacy stage → 提示重新上传；409 给专门文案 |

---

## 5. HITL 决策（已人工批准，2026-11-13）

| # | 问题 | 批准结果 | 落点 |
|---|---|---|---|
| H-1 | 是否允许 `/import` 子域引入 Tailwind/shadcn | **批准**：仅 `/import`，`preflight:false` + 懒加载 + tokens 映射；其他页面不迁移 | §0.C、§8 |
| H-2 | 撤销时列模板/类型映射删除范围 | **批准**：列模板**仅 origin 批次**撤销删除；类型映射**任意批次**撤销删除「本批次写入（新增或修改）」的部分 | §0 D-14、§7.4 |

> 类型映射的“本批次写入”用 `last_write_batch_id` 判定；仅被复用的旧类型不删；本批次改过的类型删除后不回滚旧值（无历史表，P1 可加）。

---

## 6. 结论

- v0.2 已修复全部 P0；P1 全部落到文档；P2 中影响实现的项已处理，纯优化项明确延后。
- **可以进入实现**，但必须按 §12 里程碑先冻结 v0.2 文档，再按 M1→M5 推进；M2 的 legacy vs plan `ParsedBill` 逐字段相等是硬退出条件。
- 残余风险（实现期盯）：华泰真实 13 列 fixture 的 `account`/`price` 覆盖；generic 的 fail-closed 触发条件；费用合并多账户/缺行；shadcn 的 Portal reset 与 CSS 污染；`mapping_fingerprint` 重导拦截的误报。
