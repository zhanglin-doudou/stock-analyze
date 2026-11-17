# 导入重构：券商无关的字段映射与账单类型映射（MVP）开发设计

> 状态：**v0.2 — 已实现并完成自动验收（2026-11-13）**（评审结论与 P0/P1 处置见[独立交叉评审记录](../reviews/2026-11-13-DevDesign-Review-import-mapping-refactor.md)；实际命令/结果、实现偏差与手工 SKIP 见附录 E）
> 日期：2026-11-13 ｜ 作者：AI（Team Lead）
> 需求来源：用户 2026-11-13 原始诉求（4 条 MVP + 2 条技术要求）
>
> 关联文档：
> - [账单解析与估值口径](2026-10-29-bill-parsing-and-valuation-caliber.md)（现有解析器语义：货基、债券、零金额信息行、可转债、省心投）
> - [API-Catalog v2](../architecture/v2/API-Catalog.md)（API-010~016 导入契约、§3 错误码、§6 验收矩阵）
> - [用户偏好 v2.1](2026-11-11-user-preferences.md)（本仓设计文档结构与门禁写法）
> - [独立交叉评审记录（2026-11-13）](../reviews/2026-11-13-DevDesign-Review-import-mapping-refactor.md)（v0.1 → v0.2 的 P0/P1 处置）
> - [前端重构选型 §02](../reviews/2026-10-28-frontend-refactor/02-design-research.md)（原「不引入 Tailwind / 全量 UI 库」决策，本设计提出**限定范围例外**）
> - 现有实现：`backend/packages/ledger/src/stock_ledger/parsers/{columns,huatai}.py`、`application/{preview,imports,classify}.py`、`backend/services/api/src/stock_api/routers/imports.py`、`frontend/apps/web/src/pages/Import.tsx`

---

## 0. 决策摘要（评审重点）

### A. 核心决策

| # | 决策点 | 推荐方案 | 备选 / 不采用 | 影响 |
|---|---|---|---|---|
| D-01 | 列的身份 | 用 **`(归一化表头, 同名出现序号)`** 作为列键（如 `成交日期#0`、`金额#1`），而不是当前的单表头字符串 | 纯列下标（列顺序一变全废）/ 纯表头（重复表头无法区分） | 支持重复表头、空表头、列顺序变化；签名稳定 |
| D-02 | 文件签名 | `sha256(sorted(列键))`，忽略列顺序；签名是模板的唯一绑定键 | 文件 `sha256`（同格式不同数据会不命中） | 同一券商同一导出格式 → 同一签名 → 增量导入免确认 |
| D-03 | 三层匹配 | **用户模板（强绑定）→ 券商系统规则（管理员维护）→ 通用别名规则**；每层都带 `source/confidence` | 只做系统规则（增量每次都要确认） | 首次确认后增量零确认；系统规则只做建议，不覆盖用户模板 |
| D-04 | 用户模板 | 独立表 `ledger.import_template`，`items` 存 JSONB；**首次成功 commit 时落库** | 复用 preview 的 `mapping` JSONB（无跨文件生命周期） | 模板与批次解耦，可撤销、可审计、可单独删除 |
| D-05 | 单项确认 | 系统/别名建议初始 **switch 关闭 = 未确认**（不是忽略）；用户逐项打开，或点「全部接受系统建议」（一次显式动作）；模板命中按 D-13 视为已确认，不再逐项问 | 默认打开（等于没确认） | 满足「系统匹配后也需用户单项确认」；未确认列仍走二次确认，忽略必须显式「忽略本列」 |
| D-06 | 必填口径 | **L1** 日期；**L1-trade** 代码/数量/价格（cash mode 可缺，但需 amount+非交易摘要）；**L2** 方向或类型、金额或量×价；**L3** 可选 | 「数据库所有列都必须映射」（不可行，可选列本来就允许缺） | 保留交易账单严格性，同时支持纯资金流水账单；缺 L1/L1-trade 阻断，缺 L3 只告警 |
| D-07 | 未映射列 | 允许忽略，但必须二次确认（后端 `acknowledge_unmapped=true`，前端 AlertDialog 列明未确认/忽略列）；`ignored[]` 只来自用户显式「忽略本列」，switch 关闭不算忽略 | 静默忽略（会吞掉真实费用列） | 防误配；忽略项写入模板，增量不再问 |
| D-08 | 多对一 | **只有 `other_fee` 允许多列 → 一列**，合并时按标度整数**求和**；其他字段重复映射一律 422 | 任意字段多对一（语义不可判定） | 满足「其他费多列合并」；求和失败/校验失败给专用错误码 |
| D-09 | 账单类型来源 | 优先映射到 `summary`（账单类型/业务名称/摘要）的列；未映射时按「方向 + 金额符号」派生类型（`derived:buy` 等）并告警 | 强制要求 summary 列（会挡掉无摘要券商） | 覆盖无摘要账单，同时保留类型步骤 |
| D-10 | 类型映射归属 | 用户级 **全局** 表 `ledger.import_bill_type`，键为规范化类型文本；记录首次出现的券商/批次 | 按券商隔离（跨券商同名业务要反复确认） | 增量导入已知类型自动匹配；同文本跨券商语义冲突由用户在设置页改（P1） |
| D-11 | 类型确认 | 首次出现的类型：列出「类型文本 / 行数 / 样例 / 建议 Category」，逐项 switch 确认；**历史已确认类型自动应用**，不再逐项确认 | 每次全部重确认（违反需求 3） | 首次严、增量快 |
| D-12 | 噪音/废弃 | 类型可标「废弃」→ 行落隔离区 `reason_code=BILL_TYPE_DISCARDED`，单独计数 `discarded_count`，不写 `trade` | 直接丢弃（不可审计）/ 计入 `quarantined`（口径混淆） | 噪音可追溯、可恢复；`discarded` 独立计数、不并入 `quarantined`；`parsed` 为解析器保留行数（不含解析隔离），结果页分别展示 parsed/inserted/skipped/quarantined/discarded，不宣称四者相加等于 parsed |
| D-13 | 增量导入 | 模板**精确命中** → 跳过列对应步骤；已知类型自动应用；只有**新类型**才进入类型步骤 | 每次重走两步（违反需求 3） | 满足「首次确定，后续不改变」；文件格式变化（签名不同）才重新确认 |
| D-14 | 撤销导入 | **任意批次**撤销都删除本批次写入（新增或修改）的账单类型映射；**只有 origin 批次**撤销才删除该签名的字段/列模板（增量撤销保留列模板）；删除动作进审计 | 任意撤销都删列模板（增量用户下次要重配）/ 只删流水（不满足需求 4） | 满足需求 4 的区分口径：增量撤销不破坏已确认的列对应；对话框按批次类型给不同提示 |
| D-15 | Category 枚举 | 把 `routers/rules.py::ALLOWED_CATEGORIES` 上提为 `stock_contracts.enums.BillCategory`（10 值），DB 仍用 `text` 存 | 新建 DB Enum 类型（新增值要迁移） | 单一真相；前端下拉、后端校验、文档同源 |
| D-16 | 解析器 | 把现有 `parse_bill` 改造成 **mapping plan 驱动 + profile 语义**：华泰的货基/可转债/省心投/合计行语义作为 `huatai` profile 保留；通用券商走 `generic` profile | 直接重写（回归风险极高）/ 只加适配层（多列费用无法合并） | 复用 80% 现有逻辑；华泰 golden 测试作为回归闸门 |
| D-17 | 前端 | 引入 **Tailwind v3.4 + shadcn（Radix primitives + CVA）**，但**只服务 `/import` 向导**：`preflight:false`、content 只扫 import 功能、CSS 随路由懒加载、颜色映射到既有 tokens | 全站 Tailwind（违反首屏 Budget 与既有 CSS 体系）/ 不用 shadcn（违反技术要求） | 满足技术要求且不污染现有页面；需评审批准例外 |
| D-18 | 提交一致性 | commit 时把 **mapping/type 快照写入 job params 与 batch**，worker 只用快照；PUT 映射用 `mapping_version` 乐观锁 | worker 重新读 preview 最新状态（提交后被改会错写） | 消除 TOCTOU；支持审计与重放 |
| D-19 | 旧预览/旧客户端兼容 | 迁移前的 preview 标记 `stage='legacy'`；**新请求不带 `X-Import-Flow: v2` 也走 legacy 单步流程**（旧 preview→commit 不变）；带 v2 的新预览必须 `stage='ready'` | 直接拒绝旧客户端（v1 破坏性变更） | v1 兼容；legacy 计划在 v2 稳定 90 天后下线，不是 24h |
| D-20 | 费用校验 | 两档：**算术错误**（非数字/负数/溢出）→ `IMPORT_FEE_MERGE_INVALID`；**合并后余额链校验失败** → `IMPORT_FEE_MERGE_VERIFY_FAILED`（带行号证据与「改单列」建议） | 只在导入后对账时报 warning（用户已写脏数据） | 在 commit 前拦住错误映射，符合「抛出指定错误让用户重新对应」 |

### B. 非目标（本期不做）

1. **不做 PDF / 图片 / OCR 账单**；只支持 `.xls/.xlsx/.csv` 平铺表格（单表头行）。
2. **不做多行表头、合并单元格、多 Sheet 选择**；检测到即报 `IMPORT_LAYOUT_UNSUPPORTED`，提示导出为平铺 CSV（P1 再做）。
3. **不做模糊/相似模板自动套用**（Jaccard 相似模板只提示、不自动应用；P1）。
4. **不做管理员前端规则编辑器**：MVP 提供 admin API + CLI seed；设置页的管理员表格为 P1。
5. **不做跨券商类型映射的按券商覆盖**（P1；MVP 全局类型文本映射 + 设置页可改）。
6. **不改 dedupe / replace_range / 对账补差 / 指标口径**；导入链路只换「列解析 + 分类入口」。
7. **不迁移既有 14 个页面到 shadcn**；只新增 `/import` 向导与少量 `components/ui`。

### C. 待人工确认（HITL，未确认按推荐实现）

| # | 问题 | 推荐 | 影响 |
|---|---|---|---|
| H-1 | 前端 Tailwind/shadcn 例外（原选型明确不引入 Tailwind） | **已批准（2026-11-13）**：仅 `/import` 子域引入，`preflight:false` + 懒加载 + tokens 映射 | 评审按批准执行；其他页面不迁移 |
| H-2 | 撤销时列模板与类型映射的删除范围 | **已批准（2026-11-13）**：列模板仅 origin 批次撤销删除；类型映射任意批次撤销都删「本批次写入（新增或修改）」的部分 | 增量撤销保留列对应；仅被复用的旧类型不删，避免破坏其他批次 |
| H-3 | 类型映射是否按券商隔离 | 否，用户级全局（同文本复用） | 跨券商同名异义时有误配风险；P1 设置页可改 |
| H-4 | Category 是否上提为 contracts 枚举 | 是 | 需要改 `rules.py`、前端下拉、文档；不改则继续用字符串白名单 |
| H-5 | 管理员规则编辑 UI 是否进 MVP | 否，API + seed；P1 设置页 | MVP 管理员需用 API/CLI 维护规则 |
| H-6 | 多列费用合并时，余额链校验失败是否硬阻断 | 是（422），提示改单列 | 真实账单有缺行时可能被挡；用户可只映射一列绕过 |
| H-7 | 未映射列二次确认的粒度 | 整个 mapping 一次确认（列清单） | 不逐列确认，避免疲劳 |

---

## 1. 背景、目标与验收

### 1.1 需求拆解（用户原文 → 可验收条目）

| # | 原文要点 | 设计落点 | 验收 |
|---|---|---|---|
| R1 | 表格列与数据库列必须人工对应；系统默认匹配；系统匹配也要 switch 单项确认；未匹配列列出数据库列手动指定；提交时数据库必填列必须有对应；有表格列未匹配要二次确认 | §3、§6.3、§8.3 | 首次导入走步骤 1；每个建议都有 switch；缺 L1 不能继续；忽略列弹二次确认 |
| R2 | `other_fee_units` 可多对一，合并求和；相加报错或后续验证/对账有问题要抛指定错误让用户重新对应 | §3.4、§7.2、§6.8 | 两列费用导入成功且金额=和；改一列为文本 → 422 指定错误；余额链不符 → 422 并高亮费用组 |
| R3 | 账单 Category 与账单类型手动对应；上一步确认后才做（因为要聚合类型）；系统建议也要确认；列出文件里的类型，必须匹配 Category 或废弃，排除噪音 | §4、§6.4、§8.3 | 步骤 2 只在 mapping 保存后出现；所有类型必须「分类或废弃」；废弃行进隔离区 |
| R4 | 首次导入后列对应确定，后续增量不变更，与用户绑定；增量有新类型才再次确认 | §3.6、§5.1、§6.3、§6.5 | 同格式二次导入跳过步骤 1；新类型只列新类型 |
| R5 | 撤销导入也要删除用户级字段对应关系 | §5.1、§7.4、§6.7 | origin 撤销删列模板；增量撤销保留列模板、删本批次写入类型；审计留痕 |
| T1 | 前端引入 shadcn | §8 | `components/ui` 使用 shadcn 源码组件；`components.json` 可升级 |
| T2 | 重构现有导入；华泰映射进系统对应关系；新账号导入华泰只需人工确认 | §3.5、§7.1、附录 B | 新账号上传华泰 fixture：12 列全有建议，只需逐项确认/全接受，无需手动选字段 |

### 1.2 现状与差距（基于 HEAD 代码事实）

| # | 现状 | 证据 | 问题 |
|---|---|---|---|
| G1 | 券商硬编码 `huatai` | `application/imports.py::commit_batch` 中 `batch.broker = "huatai"`；`routers/imports.py::commit_import` 创建 PENDING 批次时也写死 `broker="huatai"` | 无法支持其他券商；API-010 文档写了 `broker` 参数但代码未接收 |
| G2 | 列映射是 `表头 → 字段` 单射 | `parsers/columns.py::map_columns` 用 `used` 集合，重复表头第二个直接进 `unresolved` | 重复表头（真实账单常见「金额」「数量」两列）无法映射；不能多对一 |
| G3 | 无逐项确认 | `application/preview.py` 直接返回 `columns_detail`；`suggested_field` 由别名命中，`confidence` 硬编码 1.0 | 系统建议即事实，用户无法逐项确认/否决 |
| G4 | 无用户模板 | `import_preview.mapping` 只活 24h；`import_batch.columns` 只做留痕 | 每次导入都要重配；不满足增量零确认 |
| G5 | 分类靠摘要规则，无「账单类型」步骤 | `application/classify.py::match_rule`；`repositories/rules.py::SYSTEM_RULES` | 用户不能先看「文件里有哪些类型」再逐类决定；未知摘要整行隔离 |
| G6 | `Category` 不是真正枚举 | `routers/rules.py::ALLOWED_CATEGORIES` 是就地 frozenset；DB `trade.category` 为 `String(24)` | 前端/文档/校验三处易漂移 |
| G7 | 费用只支持单列 | `parsers/huatai.py` 的 `other_fee` 取单个 header | 华泰真实账单可能同时有「其他费」「过户费」；其他券商更常见 |
| G8 | 撤销无映射可删 | 当前没有用户级映射表 | 需求 4 无落点 |
| G9 | 前端无 shadcn/Tailwind | `frontend/apps/web/package.json` 仅 React/Router/TanStack Query/Nivo；`docs/reviews/.../02-design-research.md` 明确「不引入 Tailwind」 | 需限定范围例外 + 工程接入设计 |
| G10 | 预览样例只有 1 个 | `routers/imports.py::_preview_out` 的 `sample=([str(item["sample"])] ...)` | 用户难以判断重复列/可疑列 |

### 1.3 目标（可验收）

1. 任意**平铺表格**券商账单，都能通过「列对应 → 类型对应 → 确认导入」落库；华泰账单在新账号下只需确认，不需手动找字段。
2. 列映射、类型映射都可逐项确认、可撤销、可审计；用户级模板与用户绑定。
3. `other_fee` 多列合并正确（整数域求和），错误可定位到行/列并回到映射步骤。
4. 不改变既有 dedupe、撤销、对账补差、回合/净值/指标口径；同一 fixture 新旧链路结果 diff=0。
5. 新增契约、迁移、测试、前端页面全部过既有门禁。

### 1.4 术语

| 术语 | 含义 |
|---|---|
| 列键 `column_key` | `归一化表头#同名序号`；空表头用 `__col_{index}` |
| 签名 `signature` | 列键排序后的 SHA-256；模板绑定键 |
| 映射计划 `MappingPlan` | 一次导入的列键 → 目标字段 + 选项（金额是否带符号等） |
| 目标字段 | 逻辑字段名（如 `other_fee`），对应数据库列（`ledger.trade.other_fee_units`） |
| 账单类型 `type_key` | 规范化后的 `summary` 文本；无 summary 时用派生类型 |
| 模板 | 用户确认过的 `MappingPlan`，按签名绑定 |
| 废弃 | 用户判定该账单类型为噪音，不写流水 |

### 1.5 硬约束（沿用仓规，不另行豁免）

| 约束 | 本设计的落点 |
|---|---|
| 禁止 float；金额/数量/价格用标度整数 | 费用合并、类型金额合计、余额链校验全部在 int 域；API 边界才转定点字符串 |
| domain 禁止 wall-clock | 预览/提交时间来自注入 `Clock`；模板/类型时间戳由 DB `now()` |
| 仓储显式收 `user_id` | 新仓储 `ImportTemplateRepository/ImportBillTypeRepository/ImportColumnRuleRepository` 签名显式带 `user_id`（系统规则查询例外见 §3.5） |
| 错误码必须进 `ErrorCode` | 新增 13 个错误码 + `CATALOG` 中文人话；不用 `HTTPException` |
| 跨账号一律 404 | preview/template/type 跨用户 → 404；admin 规则不暴露用户数据 |
| 契约先行 | API-Catalog → `make api-snapshot` → `make api-check` → 代码 → API 测试 → `make sdk` |
| 分层 L1–L5 | 字段目录/枚举在 contracts；解析/仓储在 ledger；路由在 api；无反向依赖 |
| 前端零口径 | 计数/金额合计/预估全部后端返回；禁止 `Number/parseFloat/toFixed` |
| 半截写入回滚 | commit 仍用 savepoint；模板/类型与批次同事务；撤销同事务 |

---

## 2. 总体方案

### 2.1 三层匹配 + 用户确认

```
┌──────────────────────────────────────────────────────────────┐
│ 1. 用户模板（import_template，签名精确命中）                  │
│    source=template  confidence=1.0  已确认 → 不再逐项确认      │
├──────────────────────────────────────────────────────────────┤
│ 2. 券商系统规则（import_column_rule，管理员维护，broker 精确） │
│    source=system    confidence=0.95 需 switch 逐项确认         │
├──────────────────────────────────────────────────────────────┤
│ 3. 通用别名规则（broker='*'，从 FIELD_ALIASES seed）            │
│    source=alias     confidence=0.80 需 switch 逐项确认         │
└──────────────────────────────────────────────────────────────┘
```

规则只产生**建议**；模板是**已确认事实**。管理员改规则**不会**回写任何用户模板（避免历史导入被系统悄悄改口径）。

### 2.2 一次首次导入的完整时序

```text
用户               前端                     API                        ledger/worker
 |  上传文件         |                        |                           |
 |------------------>| POST /imports/preview  | 读文件、定位表头、算签名    |
 |                   |----------------------->| 查模板 / 系统规则           |
 |                   |<-----------------------| PreviewOut(stage=mapping,   |
 |                   |                        |  columns[], catalog[],     |
 |                   |                        |  template?, types? )       |
 |  步骤1：逐项开关   |                        |                           |
 |------------------>| PUT .../mapping        | 校验 L1/L2、多对一、金额语义 |
 |                   |----------------------->| 用 plan 重解析 → 聚合类型   |
 |                   |<-----------------------| stage=types, types[],       |
 |                   |                        |  verification, estimated    |
 |  步骤2：类型确认   |                        |                           |
 |------------------>| PUT .../types          | 校验所有类型已分类/废弃      |
 |                   |----------------------->| stage=ready                 |
 |                   |<-----------------------| preview(ready)              |
 |  确认导入          | POST /imports          | 校验 stage=ready            |
 |------------------>|----------------------->| 建 PENDING 批次 + 入队 job   |
 |                   |<-----------------------| 202 job_id                  |
 |                   |                        |         worker: 用快照解析   |
 |                   |                        |         分类/写 trade/对账   |
 |                   |                        |         落模板 + 类型映射    |
 |                   |<-----------------------| SSE 进度 / batch 结果        |
```

### 2.3 与现有两阶段导入的关系

保留「preview 不写业务行 / commit 异步 / 批次可撤销」的骨架；在 preview 内部增加**两个可反复编辑的草稿阶段**（mapping、types）。草稿只写 `import_preview`，仍然不写 `ledger.trade`。commit 之后才把用户确认结果固化成 `import_template` / `import_bill_type`。

---

## 3. 字段目录与映射模型

### 3.1 数据库字段目录（唯一真相）

字段目录放在 `stock_contracts/import_fields.py`（纯常量，无 I/O），API 在 `PreviewOut.catalog[]` 返回，前端不再硬编码字段列表。

| 逻辑字段 | 数据库列 | 级别 | 可多对一 | 值类型 | 说明 |
|---|---|---|---|---|---|
| `trade_date` | `ledger.trade.trade_date` | **L1** | 否 | date | 日期格式单独选择/探测 |
| `security_code` | `ledger.trade.security_code` | **L1-trade** | 否 | text | 保留前导零；Excel 数值去 `.0`；纯资金流水 cash mode 可缺 |
| `quantity` | `ledger.trade.quantity_units` | **L1-trade** | 否 | qty(4dp) | cash mode 可缺 |
| `price` | `ledger.trade.price_units` | **L1-trade** | 否 | price(6dp) | cash mode 可缺 |
| `security_name` | `ledger.trade.security_name` | L3 | 否 | text | 缺省时可用代码占位并告警 |
| `side` | `ledger.trade.side` | **L2** | 否 | buy/sell | 与 `summary` 二选一；可从摘要兜底 |
| `amount` | `ledger.trade.amount_units` | **L2** | 否 | money 带符号净额 | trade mode 缺省时按 `qty×price ± 费用` 推导；cash mode 必需 |
| `commission` | `ledger.trade.commission_units` | L3 | 否 | money | 缺省 0 |
| `stamp_tax` | `ledger.trade.stamp_tax_units` | L3 | 否 | money | 缺省 0 |
| `other_fee` | `ledger.trade.other_fee_units` | L3 | **是** | money | 多列求和；唯一允许多对一字段 |
| `cash_balance` | `ledger.trade.cash_balance_units` | L3 | 否 | money | 缺省则无法对账，告警 |
| `money_fund_balance` | `ledger.trade.money_fund_balance_units` | L3 | 否 | money | 货基余额（华泰 profile 用） |
| `summary` | `ledger.trade.summary` | **L2** | 否 | text | **账单类型**来源；与 `side` 二选一 |
| `note` | `ledger.trade.note` | L3 | 否 | text | 备注，不参与分类 |
| `account` | `ledger.trade.account_masked` | L3 | 否 | text | 入库前 MD5 脱敏，样例也脱敏 |

**L1**：`trade_date` 必须映射；缺 → 步骤 1 不能继续（`IMPORT_COLUMNS_UNRESOLVED`）。
**L1-trade**：`security_code`/`quantity`/`price` 在 **trade mode** 必须映射；若账单是纯资金流水（无代码/量价）且 `summary` 可判定为非交易（银行转存/利息/红利等），可走 **cash mode**：这三列可缺，但 `amount` 必须映射，行以 `security_code=CASH` 入库。
**L2**：`side` 或 `summary` 至少一个（否则 `IMPORT_CLASSIFICATION_SOURCE_MISSING`）；trade mode 下 `amount` 或（`quantity`+`price`）至少一组；cash mode 下 `amount` 必需。
**L3**：缺省只告警（`MISSING_CASH_BALANCE` 等），不影响继续。

> 判定顺序：先看 `security_code/quantity/price` 是否齐全 → trade mode；不齐时再看 `summary` 是否可判定非交易且 `amount` 已映射 → cash mode；两者都不满足 → `blocking[]` 列出缺项。这样既保留旧交易账单的严格性，又不把纯资金流水账单挡在门外。

> 需求原文「数据库必须列」按此解释：**不是所有 DB 列都必须有表格列**（可选列本就允许缺），而是 L1/L2 必须满足。UI 会同时展示 `数据库列名` 与「必填」徽标，避免歧义。

### 3.2 列键与文件签名

```python
# 纯函数，放 stock_ledger/parsers/mapping.py
def header_key(raw: str, occurrence: int, index: int) -> str:
    norm = normalize_header_v2(raw)       # NFKC + 去 BOM/空白/全角空格
    if not norm:
        return f"__col_{index}"           # 空表头按位置
    norm = norm.replace("#", "＃")         # 防止表头自带 # 与序号分隔符歧义
    return f"{norm}#{occurrence}"         # 同名第 n 个

def signature(keys: list[str]) -> str:
    return hashlib.sha256("\x1f".join(sorted(keys)).encode("utf-8")).hexdigest()
```

- 列顺序变化不影响签名（排序后哈希）。
- 新增/删除列一定改变签名 → 模板不命中 → 重新确认（符合「格式变了要重新确认」）。
- 同名表头按出现顺序编号；UI 用样例值区分 `金额#0` / `金额#1`。
- 空表头按位置编号；若列顺序变化会失配，这是可接受的边界。
- 归一化新增 **`normalize_header_v2`**：在现有 `normalize_header`（去 BOM/空白/全角空格）基础上加 NFKC（全角字母/数字→半角）。新链路的列键、系统规则、模板、类型键统一用它；legacy 解析器继续用 `normalize_header`，避免旧行为被静默改变。

### 3.3 映射计划（MappingPlan）

存储形态（`import_template.items` / `import_preview.mapping_items` / `import_batch.mapping_items`）：

```json
{
  "version": 1,
  "items": [
    {"column_key": "成交日期#0", "header": "成交日期", "target_field": "trade_date"},
    {"column_key": "发生金额#0", "header": "发生金额", "target_field": "amount",
     "options": {"amount_signed": true}},
    {"column_key": "其他费#0", "header": "其他费", "target_field": "other_fee"},
    {"column_key": "过户费#0", "header": "过户费", "target_field": "other_fee"}
  ],
  "ignored": ["备注#0"],
  "date_format": "YYYYMMDD"
}
```

规则：

1. `date_format` 在库里有独立列（`import_preview.date_format` / `import_template.date_format`），`items` 只存列对应；JSON 里的 `date_format` 是 plan 的逻辑字段，不塞进 `items`。
2. 每个 `column_key` 只能出现一次（重复 → `IMPORT_MAPPING_INVALID`）；`target_field` 必须属于字段目录（未知 → `IMPORT_MAPPING_INVALID`）。
3. 除 `other_fee` 外，同一 `target_field` 只能出现一次；重复 → `IMPORT_MAPPING_INVALID`（`errors[]` 指出两列）。
4. `other_fee` 可出现 2~8 次；出现 ≥2 次即进入合并组，按 `items` 顺序求和（顺序不影响结果）。
5. `ignored` 必须只包含未映射列（把已映射列放进 `ignored` → `IMPORT_MAPPING_INVALID`）；`ignored` 与其余未映射列必须被 `acknowledge_unmapped=true` 覆盖，否则 `IMPORT_UNMAPPED_COLUMNS_UNCONFIRMED`。
6. `amount` 的 `options.amount_signed`：`true` = 该列原生是「发生金额/净现金流」（已含费用、买卖带符号）；`false` = 毛额，需要按方向 ± 费用换算。系统规则对「发生金额/发生额」预置 `true`，其余 `false`；用户可改。
7. `date_format` 可选 `YYYYMMDD / YYYY-MM-DD / YYYY/M/D / YYYY-DD-MM`；不传则用探测结果，歧义时要求用户选。

### 3.4 `other_fee` 多对一（核心风险点）

**解析**：对每一行，把映射到 `other_fee` 的所有列分别按金额解析（`Decimal(str) → 标度整数`），在 **int 域求和**，得到 `other_fee_units`。禁止 float。

**算术校验（硬错误）**：

| 情况 | 错误码 | 行为 |
|---|---|---|
| 单元格非数字（如 `"手续费"`、`"--"` 以外的文本） | `IMPORT_FEE_MERGE_INVALID` | 422，`errors[].field=items.{column_key}`，`message` 含行号与原始值，最多 20 条（RFC 7807 `errors[]` 只有 field/code/message，不另造字段） |
| 空 / `-` / `--` | 视为 0 | v2 plan 读取层显式归一（legacy 的 `_decimal` 只认 `""/"-"/"+"`，不改旧行为） |
| 求和后为负 | `IMPORT_FEE_MERGE_INVALID` | 费用不应为负；提示检查列语义或改用单列 |
| 绝对值超过 `10^12` 元 | `IMPORT_FEE_MERGE_INVALID` | 防溢出/错列 |

**合并后余额链校验（硬错误，仅合并组触发）**：

- 触发条件：`other_fee` 列数 ≥2 **且** `cash_balance` 已映射（不论 `amount_signed`；后者用于区分是「费用合并错」还是「金额语义标错」）。
- **复用 `reconcile` 的排序/跳过口径**（日期 → `line_no` → `side=missing` 兜底 → `id`；`side=unknown` 与 `cash_balance is None` 的行跳过），逐行验证 `prev_balance + amount_net ≈ cash_balance`（容差沿用 `epsilon=0.01` 元）；不另写一套排序。
- **多账户**：现有 `reconcile` 本身不按 account 分链，因此合并费用校验也不分组；若账单出现 ≥2 个不同 `account_masked` 且存在合并费用 → 422 `IMPORT_FEE_MERGE_VERIFY_FAILED`（detail「多账户账单请拆分后再导，或费用只映射一列」），避免 preview 通过但 commit 对账断链。
- `amount_signed=false` 时任一失配 → `IMPORT_FEE_MERGE_VERIFY_FAILED`：`errors[]` 前 10 条 `field=items.{column_key}`、`message` 含行号/期望/实际/差额；`detail` 汇总；`what_to_do` 给「把费用改成单列 / 检查是否把非费用列映射到 other_fee」。
- `amount_signed=true` 时失配 → `IMPORT_MAPPING_INVALID`（detail「金额列标记为已含费用，但余额链不符；请检查金额语义或账单是否缺行」），防止把毛额误标成净额后绕过费用校验。
- 若 `cash_balance` 未映射：跳过链校验，返回 warning `FEE_MERGE_UNVERIFIED`（不阻断）。

**导入后二次对账**：即使通过上述校验，commit 收尾的现有对账仍会跑。若本批次使用了合并费用且 `filled_count > 0`：

- 批次 `remap_required=true`、`warnings` 追加 `FEE_MERGE_SUSPECT`；
- 结果页显示「费用列合并可能不对」横幅，提供「撤销并重新对应」按钮（撤销按 D-14：origin 批次删列模板；增量批次保留列模板、只删本批次写入类型）；
- 不自动改数据，不静默补差掩盖。

### 3.5 系统规则（管理员维护）

表 `ledger.import_column_rule`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | text PK | `icr_...` |
| `user_id` | uuid NULL | 全局规则为 NULL；预留用户覆盖，MVP 不写 |
| `broker` | text(16) | `huatai` / `*`（通用） |
| `header_norm` | text(128) | 归一化表头 |
| `match_type` | text(8) | `exact` / `prefix` / `contains`（默认 exact） |
| `target_field` | text(24) | 字段目录键 |
| `options` | jsonb | 字段选项，如 `{"amount_signed": true}`（仅 `amount` 用；默认 `{}`） |
| `priority` | int | 越小越优先 |
| `enabled` | bool | 管理员可禁用 |
| `is_system` | bool | seed 生成；不可删除，只能禁用 |
| `is_fingerprint` | bool | 是否参与券商自动识别打分 |
| `weight` | int | 识别权重（默认 1） |
| `note` | text | 给管理员的说明 |
| `version` | int | 乐观锁 |
| `created_at/updated_at` | timestamptz | |

- 唯一键 `(user_id, broker, header_norm, target_field)`（`NULLS NOT DISTINCT`，沿用 `summary_rule` 的做法）。
- RLS 策略与 `summary_rule` 一致：`user_id IS NULL OR user_id = current_setting('app.user_id')`；全局行对所有租户可见，写入口只开在 admin API。
- 与 `summary_rule` 同款的已知边界：RLS `WITH CHECK` 对 `user_id IS NULL` 行放行，因此「租户会话能写全局规则」仅在 DB 层未被阻止，实际写路径由 API `require_admin()` 收口；本设计不引入独立 admin DB role（超出范围），在评审记录中登记为接受项。
- **匹配方式**：默认 `exact`；`prefix/contains` 由管理员显式创建（按 `priority` 排序，`exact` 先于 `prefix` 先于 `contains`）。不做正则，避免表头规则变成不可审计的代码。
- **歧义**：同一表头命中多条规则时，按 **broker 专属优先（非 `*`）→ `priority` → 匹配精度（exact>prefix>contains）→ `id`** 稳定排序取第一条作为建议；其余候选 P1 在 UI 展开。
- **seed 策略**：新表 `stock_contracts/import_fields.py::FIELD_ALIASES`（**与 legacy `parsers/columns.py::COLUMN_ALIASES` 分开**，避免改旧解析器语义；例如新表把「备注」映射到 `note` 而不是 `summary`）生成 `broker='*'` 规则；华泰 12 个精确表头生成 `broker='huatai'` 指纹规则。`seed_import_column_rules()` **只插缺、不覆盖**管理员改动；系统行不可删（422 提示改为禁用）。应用层在 preview/commit 前调用（自愈，与 `load_rules` 同款）。
- **券商识别**：对每个 `broker`，`score = Σ命中权重 / Σ该 broker 全部 is_fingerprint 规则权重`；`score ≥ 0.6` 且命中权重 ≥6，并严格大于第二名 → 判定该 broker；否则 `generic`。阈值刻意偏高：避免只命中「成交日期/证券代码」等通用列就被误判成华泰而套用可转债/货基专属语义。用户可在上传步骤手动改；generic 走通用语义。
- **未注册 profile 的券商**：识别只影响「用哪套系统建议 + 展示哪个券商」；语义 profile 仍回退 `generic`。新增华泰之外的专属语义（货基锚点/可转债手数等）需新增 profile 代码（P1）；MVP 的「覆盖所有券商」= 通用平铺表格能导入，不承诺每家专属口径。券商展示名来自 `stock_contracts/import_fields.py::BROKER_LABELS`（代码常量，P1 可改为表驱动）。
- 规则修改**只影响后续预览的建议**，不回溯模板、不重写历史 trade。

### 3.6 用户模板（用户级、与用户绑定）

表 `ledger.import_template`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | text PK | `imt_...` |
| `user_id` | uuid NOT NULL | FK `platform.user` ON DELETE CASCADE |
| `broker` | text(16) | 首次确认时的券商（展示用，不参与唯一） |
| `signature` | text(64) | 列键排序哈希 |
| `header_count` | int | 列数 |
| `date_format` | text(32) | 用户确认的日期格式；增量导入直接复用，避免再猜 |
| `items` | jsonb | `MappingPlan.items` |
| `ignored` | jsonb | 被用户确认忽略的列键 |
| `origin_batch_id` | text NULL | 首次创建模板的批次 |
| `last_batch_id` | text NULL | 最近使用批次 |
| `version` | int | 乐观锁 |
| `created_at/updated_at` | timestamptz | |

唯一键 `(user_id, signature)`；RLS 租户隔离。`import_template.version` 与 `import_preview.mapping_version` 是两套乐观锁（前者防并发覆盖模板，后者防草稿互改），不要混用。

**生命周期**：

1. 首次成功 commit：`upsert`（同签名并发用 `ON CONFLICT DO UPDATE`，`version` 只增；worker 内不返回 HTTP 409，冲突记录 warning 并以最后写入为准）。
2. 后续同签名 preview：精确命中 → 用模板生成 plan，`columns[].source=template`，步骤 1 只读；不改变模板。
3. 用户点「修改对应关系」→ 进入可编辑步骤；`PUT mapping` **只更新 preview 草稿**，不写 `import_template`/`import_bill_type`；**commit 成功后**才更新模板（`items/date_format/last_batch_id/version++`）。这样 `origin_batch_id` 永远有真实批次，撤销 origin 才能删到模板。
4. 撤销批次（D-14）：删除 `last_write_batch_id=本批次` 的类型映射（新增或修改）；**仅当本批次是模板 origin 时**才删除该用户该签名的列模板，增量批次撤销保留列模板。
5. 管理员改系统规则：模板不变。
6. `origin_batch_id` 是模板**首次创建**的批次，不随后续「修改对应关系」或增量使用变化；撤销 origin 会删模板（即使后续批次改过它），这是 H-2 的口径。
7. 模板命中但用模板 `date_format` 解析失败（券商改了日期格式）→ 不自动应用，`template.match="stale"/applied=false`，回退 `stage=mapping` 并提示「日期格式需确认」，同时给 warning `TEMPLATE_DATE_FORMAT_MISMATCH`；不改模板，等本次 commit 成功后更新。

---

## 4. 账单类型映射（Category）

### 4.1 类型来源与聚合

类型来源优先级：

1. 映射到 `summary` 的列值（推荐；账单里的「业务名称/摘要/交易类型」）。
2. 未映射 `summary` 时**派生类型**：
   - `derived:buy`（方向 buy）
   - `derived:sell`（方向 sell）
   - `derived:zero`（金额与余额均为 0）
   - `derived:unknown`（方向无法判定）
   预览给 warning `BILL_TYPE_DERIVED`，类型步骤仍要求用户给这些派生类型选 Category（或废弃）。

聚合（解析后用内存 dict，不额外查库；`summary` 由解析器按 `String(64)` 截断，原文保留在 `raw` 供展示）：

```text
for row in parsed_rows:
    key = normalize_type((row.summary or "")[:64])   # 与 trade.summary 列宽一致；NFKC + 去空白/零宽 + ASCII 小写
    if not key: key = derive_type_key(row)            # 空 summary → derived:buy/sell/unknown，绝不产生空串
    agg[key].rows += 1
    agg[key].amount_units += row.amount_units
    agg[key].samples = first 3 raw summaries
```

`types[]` 按 `rows` 降序返回。金额合计由后端算好（`amount_sum` 定点字符串），前端零口径。

**截断/空值规则**：解析器把 `summary` 截到 64、`note` 截到 200（DB 列宽），原文保留 `ParsedRow.raw`；`type_key` 用入库值同函数归一，保证 commit 后 `reclassify` 用同一键。空 `summary` 必须走派生键，`import_bill_type` 的 `CHECK(length>=1)` 不会因空串炸 job。

### 4.2 建议与确认

对每个 `type_key`：

| 来源 | 建议 Category | 是否自动确认 |
|---|---|---|
| 用户历史类型映射（`import_bill_type` 命中） | 历史 category | **是**（需求 3：已知类型不再问） |
| 历史映射是「废弃」 | 废弃 | **是** |
| 系统摘要规则 `SYSTEM_RULES` 命中（`match_rule`，`side="both"` 匹配） | 规则 category | 否（首次出现，switch 关闭） |
| 关键词启发式（如包含「红利」→ dividend） | 建议值 | 否 |
| 无 | 空 | 否，必须用户选 |

- 每个类型行 UI：`类型文本 | 行数 | 金额合计 | 样例 | Category 下拉 | 废弃 Switch | 确认 Switch`。
- 首次出现：确认 Switch 默认关闭；可点「全部接受建议」。
- 已确认历史类型：显示「已自动匹配（上次确认）」徽标，Switch 打开且只读；用户可展开修改。
- 类型映射**优先于**摘要规则：`import_bill_type` 命中即以它为准；摘要规则继续用于「首次建议」与 `reclassify` 兜底（§7.3）。
- **必须全部解决**（`category` 或 `discard=true` 二选一）才能进入 ready；否则 `IMPORT_BILL_TYPES_UNRESOLVED`。
- `category` 必须是 `BillCategory` 的 10 个值之一，否则 `IMPORT_CATEGORY_INVALID`。
- **不能既分类又废弃**（422）。

`BillCategory`（10 值，中文标签由 contracts 提供，前端不硬编码）：

| code | 中文标签 | 典型账单类型 |
|---|---|---|
| `buy` | 买入 | 证券买入、货基申购确认 |
| `sell` | 卖出 | 证券卖出、货基赎回确认 |
| `bonus` | 送转 | 送股、转增（当前规则仍落 `other`，枚举保留） |
| `dividend` | 分红 | 红利入账、红利发放、股息红利税（负数冲减分红收入） |
| `rights` | 配股 | 配股 |
| `interest` | 利息 | 利息归本 |
| `fee` | 费用 | 佣金、印花税、独立手续费行 |
| `transfer` | 资金划转 | 银行转证券/转银行、转托管、省心投申购 |
| `new_stock` | 新股 | 申购、中签、新股入账 |
| `other` | 其他 | 证券清理、送股（当前口径）、未知业务 |

> 注：枚举现为 11 值，另有 `broker_action`（券商行为）：申购中签/申购还款/新股入账/证券清理/转托管等登记动作，不建仓、不计成本。

> `trade.summary` 是 `String(64)`：类型键统一按**入库值**（≤64 字符）规范化，保证 commit 后 `reclassify` 用同一键；更长文本在预览中截断展示（`type_label` 最多 120）。

### 4.3 废弃 / 噪音

- `discard=true` → 该类型所有行在 commit 时写隔离区：
  - `reason_code=BILL_TYPE_DISCARDED`（新增枚举值，text 存储，无迁移）；
  - `raw` 保留原始行（账号仍脱敏）；
  - 计入 `batch.counts.discarded`，**不计入** `quarantined`；
  - `GET /imports/{batch_id}/quarantine?reason_code=BILL_TYPE_DISCARDED` 可查。
- 废弃是**用户级记忆**：后续同类型自动废弃，但结果页和类型步骤仍展示（可改）。
- 解析器层面的噪音（全零金额、未知布局等）继续走原有隔离原因，不受类型废弃影响。

### 4.4 增量导入的新类型

1. 同签名模板命中 → 解析 → 聚合类型。
2. 对每个 `type_key` 查 `import_bill_type`：
   - 命中 → 自动应用（分类或废弃）；
   - 未命中 → 进入 `types[]`，`needs_confirmation=true`。
3. 若 `types[]` 全部命中 → 服务端用历史映射填充 `type_resolutions` 并置 `stage=ready`，直接确认导入（步骤 2 只显示折叠的「已自动匹配 N 类」），不需要再调 API-099。
4. 若有未命中 → `stage=types`，只列未命中类型；已确认的折叠展示。
5. 用户确认后 commit，把新类型 upsert 进 `import_bill_type`。

---

## 5. 数据模型与迁移

### 5.1 新表（迁移 `0015_import_mapping`）

#### 5.1.1 `ledger.import_column_rule`（系统/管理员规则）

```sql
CREATE TABLE ledger.import_column_rule (
  id             text PRIMARY KEY,
  user_id        uuid NULL REFERENCES platform.user(id) ON DELETE CASCADE,
  broker         text NOT NULL,
  header_norm    text NOT NULL,
  match_type     text NOT NULL DEFAULT 'exact',
  target_field   text NOT NULL,
  options        jsonb NOT NULL DEFAULT '{}'::jsonb,
  priority       integer NOT NULL DEFAULT 100,
  enabled        boolean NOT NULL DEFAULT true,
  is_system      boolean NOT NULL DEFAULT false,
  is_fingerprint boolean NOT NULL DEFAULT false,
  weight         integer NOT NULL DEFAULT 1,
  note           text,
  version        integer NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_import_column_rule_broker CHECK (length(broker) BETWEEN 1 AND 16),
  CONSTRAINT ck_import_column_rule_weight CHECK (weight BETWEEN 1 AND 10),
  CONSTRAINT ck_import_column_rule_match CHECK (match_type IN ('exact','prefix','contains')),
  CONSTRAINT ck_import_column_rule_priority CHECK (priority BETWEEN 1 AND 999),
  CONSTRAINT uq_import_column_rule UNIQUE NULLS NOT DISTINCT
    (user_id, broker, header_norm, target_field)
);
CREATE INDEX ix_import_column_rule_lookup
  ON ledger.import_column_rule (broker, enabled, priority);
```

RLS：`USING (user_id IS NULL OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid)`，`WITH CHECK` 同式（与 `summary_rule` 一致）。

#### 5.1.2 `ledger.import_template`（用户字段模板）

```sql
CREATE TABLE ledger.import_template (
  id              text PRIMARY KEY,
  user_id         uuid NOT NULL REFERENCES platform.user(id) ON DELETE CASCADE,
  broker          text NOT NULL,
  signature       text NOT NULL,
  header_count    integer NOT NULL,
  date_format     text,
  items           jsonb NOT NULL,
  ignored         jsonb NOT NULL DEFAULT '[]'::jsonb,
  origin_batch_id text,
  last_batch_id   text,
  version         integer NOT NULL DEFAULT 1,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_import_template_user_signature UNIQUE (user_id, signature)
);
CREATE INDEX ix_import_template_user
  ON ledger.import_template (user_id, updated_at DESC);
```

RLS：标准 `tenant_isolation`。

#### 5.1.3 `ledger.import_bill_type`（用户类型映射）

```sql
CREATE TABLE ledger.import_bill_type (
  id                   text PRIMARY KEY,
  user_id              uuid NOT NULL REFERENCES platform.user(id) ON DELETE CASCADE,
  type_key             text NOT NULL,
  type_label           text NOT NULL,
  category             text,
  discarded            boolean NOT NULL DEFAULT false,
  broker               text,
  origin_batch_id      text,
  last_write_batch_id  text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_import_bill_type_user_key UNIQUE (user_id, type_key),
  CONSTRAINT ck_import_bill_type_key CHECK (length(type_key) BETWEEN 1 AND 64),
  CONSTRAINT ck_import_bill_type_resolution
    CHECK ((discarded AND category IS NULL) OR (NOT discarded AND category IS NOT NULL))
);
CREATE INDEX ix_import_bill_type_user ON ledger.import_bill_type (user_id, updated_at DESC);
CREATE INDEX ix_import_bill_type_write ON ledger.import_bill_type (user_id, last_write_batch_id);
```

RLS：标准 `tenant_isolation`。

### 5.2 存量表扩展

#### `ledger.import_preview`（新增列）

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `broker` | text(16) | `'generic'` | 识别/用户选择结果 |
| `signature` | text(64) | NULL | 列签名 |
| `header_row_index` | int | NULL | 表头行（0-based）；preview→commit 快照，避免重探测漂移 |
| `mapping_fingerprint` | text(64) | NULL | `sha256(mapping_items+ignored_columns+date_format)`；同文件改映射重导检测 |
| `stage` | text(16) | `'mapping'` | `mapping/types/ready/legacy` |
| `mapping_items` | jsonb | `[]` | MappingPlan items |
| `ignored_columns` | jsonb | `[]` | 用户确认忽略的列键 |
| `mapping_version` | int | 0 | 每次 PUT mapping 自增；API-099 不变；乐观锁 |
| `mapping_confirmed_at` | timestamptz | NULL | 步骤 1 完成时间 |
| `types_confirmed_at` | timestamptz | NULL | 步骤 2 完成时间 |
| `type_resolutions` | jsonb | `[]` | `[{type_key,label,category,discarded,source,rows}]` |
| `template_id` | text | NULL | 命中的模板 |
| `verification` | jsonb | `{}` | 费用合并/余额链校验结果 |
| `discarded_count` | int | 0 | 废弃类型行数 |

迁移时把**已存在**的 preview 行 `UPDATE stage='legacy'`；新行由 ORM 写 `mapping`。24h TTL 后 legacy 消失。

#### `ledger.import_batch`（新增列）

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `template_id` | text | NULL | 本次使用的模板 |
| `signature` | text(64) | NULL | 本次文件签名 |
| `header_row_index` | int | NULL | 表头行快照（与 preview 一致） |
| `mapping_fingerprint` | text(64) | NULL | 提交时的映射指纹 |
| `mapping_items` | jsonb | `[]` | 提交时的映射快照 |
| `type_resolutions` | jsonb | `[]` | 提交时的类型快照 |
| `mapping_source` | text(16) | `'legacy'` | `template/manual/legacy` |
| `discarded_count` | int | 0 | 废弃行数 |
| `remap_required` | boolean | false | 费用合并可疑，建议重新对应 |

`counts` 的 API 语义变为 `parsed / inserted / skipped / quarantined / discarded`；`discarded` 为新增字段（additive）。

新增索引（同迁移）：

```sql
CREATE INDEX ix_import_batch_user_file ON ledger.import_batch (user_id, file_sha256);
CREATE INDEX ix_import_batch_user_signature ON ledger.import_batch (user_id, signature, created_at DESC);
```

前者用于“同文件改映射重导”检测，后者用于撤销时重算 `last_batch_id` 与模板命中统计。

### 5.3 迁移、回滚与同步

1. `upgrade`：建三张表 + 索引 + RLS；给 preview/batch 加列；`UPDATE import_preview SET broker='huatai', stage='legacy'`（历史 preview 均由华泰解析器产生，不能留 `generic`）；stamp `platform.schema_version='0015_import_mapping'`。
2. `downgrade`：drop 新表新列；版本回退 `0014_user_setting_preferences`。
3. `stock_api/version.py::SCHEMA_VERSION = "0015_import_mapping"`；`make migrate-check` 验证 ORM/迁移无漂移。
4. `LEDGER_TABLES` 加入三张新表；`backend/tests/conftest.py` 的硬编码 `_LEDGER_TABLES` 改为 `from stock_ledger.models import LEDGER_TABLES`，保证新表自动纳入 TRUNCATE/RLS 测试。
5. `stock_platform/ids.py` 新增前缀：`imt`（模板）、`ibt`（类型）、`icr`（规则）。

### 5.4 仓储与分层

| 层 | 新增/修改 | 约束 |
|---|---|---|
| `stock_contracts` | `import_fields.py`（字段目录）、`BillCategory`、`ImportStage`、`QuarantineReason.BILL_TYPE_DISCARDED`、错误码 | 不依赖其它包 |
| `stock_core` | 无（映射/签名是 ledger 领域纯函数） | 保持零 I/O |
| `stock_platform` | `ids.py` 前缀 | 不反向依赖 ledger |
| `stock_ledger` | 3 个模型、3 个仓储（template/type/rule）、`parsers/mapping.py`、`parsers/profiles.py`、`application/preview.py`、`imports.py`、`classify.py` | 仓储方法显式收 `user_id`；规则表例外见 §3.5 |
| `stock_api` | 导入路由扩展、admin 规则路由、schemas、openapi meta | `StrictQueryRoute`、`operation_id` |
| `worker` | `commit_batch` 使用快照；撤销按 origin/增量分别删模板/类型 | 不改任务协议 |
| `frontend` | shadcn 接入 + 向导 | 见 §8 |

---

## 6. API 契约

### 6.1 端点总览（新增 9 个：API-098~106；总数 96 → 105，API-089 预留不计数）

| API-ID | 方法 | 路径 | 用途 | 认证 | 幂等 | 同步/异步 |
|---|---|---|---|---|---|---|
| API-098 | PUT | `/imports/previews/{preview_id}/mapping` | 保存列对应、重解析、聚合类型 | write | PUT 幂等（version） | 同步 |
| API-099 | PUT | `/imports/previews/{preview_id}/types` | 保存类型分类/废弃 | write | PUT 幂等（version） | 同步 |
| API-100 | GET | `/imports/templates` | 我的字段模板列表 | read | — | 同步 |
| API-101 | DELETE | `/imports/templates/{template_id}` | 删除我的模板（重新对应） | write | — | 同步 |
| API-102 | GET | `/admin/import-column-rules` | 管理员查看系统规则 | admin | — | 同步 |
| API-103 | POST | `/admin/import-column-rules` | 新建规则 | admin | 支持 | 同步 |
| API-104 | PATCH | `/admin/import-column-rules/{rule_id}` | 修改/禁用规则（If-Match） | admin | 支持 | 同步 |
| API-105 | DELETE | `/admin/import-column-rules/{rule_id}` | 删除自定义规则 | admin | — | 同步 |
| API-106 | GET | `/imports/previews/{preview_id}` | 重新读取预览草稿（刷新/409 恢复） | read | — | 同步 |

> `GET /imports/field-catalog` 不单独开：字段目录作为 `PreviewOut.catalog[]` 返回，减少一次往返。
>
> **路由顺序**：`GET /imports/templates` 必须注册在 `GET /imports/{batch_id}` **之前**，否则会被当成 `batch_id`（与 `rounds/insights` 同款坑）；新路由统一放在 `routers/imports.py` 的静态段。

### 6.2 API-010 `POST /imports/preview`（扩展，additive）

请求 multipart：

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | `.xls/.xlsx/.csv`，≤20MB |
| 头 `X-Import-Flow` | 否 | `v2` = 新向导（必传）；**缺省 = legacy 单步兼容流程**，行为与旧版一致 |
| `broker` | 否 | v2：缺省 `auto`（自动识别），可传 `auto/huatai/generic/...`；legacy：缺省 `huatai`（保持 API-Catalog 既有默认） |
| `reuse_template` | 否 | 默认 `true` |
| `date_format` | 否 | 显式指定日期格式 |
| `mapping` | 否 | **仅兼容旧客户端**；新流程不传 |

响应 200（`PreviewOut` 扩展；旧字段全部保留）：

```json
{
  "preview_id": "prv_...",
  "file": {"name": "...", "sha256": "...", "bytes": 1234},
  "broker": {"code": "huatai", "label": "华泰证券", "detected": true},
  "signature": "hex...",
  "header_row_index": 0,
  "stage": "mapping",
  "mapping_version": 0,
  "template": null,
  "catalog": [
    {"field": "trade_date", "db_column": "ledger.trade.trade_date",
     "label": "成交日期", "level": "L1", "mergeable": false, "value_type": "date"}
  ],
  "categories": [
    {"code": "buy", "label": "买入"},
    {"code": "sell", "label": "卖出"},
    {"code": "transfer", "label": "资金划转"}
  ],
  "columns": [
    {"index": 0, "key": "成交日期#0", "name": "成交日期",
     "sample": ["20260401"], "suggested_field": "trade_date",
     "source": "system", "confidence": 0.95}
  ],
  "types": [],
  "verification": {"fee_merge": {"columns": ["其他费#0"], "status": "ok"}},
  "estimated": {"parsed": 121, "inserted": 120, "skipped": 0,
                "quarantined": 1, "discarded": 0},
  "can_commit": false,
  "blocking": [],
  "requires_reimport_ack": false,
  "estimated_available": true,
  "expires_at": "..."
}
```

- **流程判定**：请求头 `X-Import-Flow: v2` → 新流程；缺省 → `stage=legacy`，沿用旧 `parse_bill` 严格校验/旧 commit，保证既有客户端与测试不受影响。新前端必须显式带 `v2`。
- `stage=mapping`：需要步骤 1；此时 `types=[]`（类型要等 mapping 保存后才能聚合）。`stage=types`：模板命中或 mapping 已保存，但存在未确认类型；`stage=ready`：模板命中且类型全部已知，可直接 commit。
- **v2** 上传阶段不因缺 L1/L1-trade 报 422：返回 200 + `blocking[]{code,field,message}` + `can_commit=false`，让用户进入映射界面；硬校验在 API-098。**legacy** 上传仍保持旧行为（缺列直接 422 `IMPORT_COLUMNS_UNRESOLVED`）。
- `template`：`{id, match: "exact"|"stale"|"none", applied: bool, confirmed_at, origin_batch_id, ignored[]}`。
  - `exact+applied=true`：模板已应用，步骤 1 只读；`stale+applied=false`：签名命中但日期格式/布局解析失败，回到步骤 1 确认；`none`：无模板。
  - `ignored[]` 回传模板的忽略列，编辑对应关系时不得重新变成“未映射”而丢失。
- `can_commit` 只在 `stage=ready` 为 true。
- `mapping_version` 是后续 PUT mapping/types 的乐观锁版本；**每次 API-098 保存自增**（API-099 不变），响应必须回传，并带 `ETag: W/"<mapping_version>"`。
- `columns[].source ∈ template/system/alias/none`；`confidence` 由来源决定（1.0/0.95/0.8/0）。`columns[].sample` 从数据行取**前 2~3 个非空值**（不是 1 个），重复表头 `金额#0/金额#1` 靠它区分。
- `categories[]` 由后端从 `BillCategory` 标签表返回，前端**不硬编码**中文标签；`types[].suggested_category/category` 只给 code。
- `types[]{type_key,label,rows,amount_sum,samples[],suggested_category,source,needs_confirmation,category,discarded,confirmed}`：`source ∈ history/rule/heuristic/none`；`needs_confirmation=false` 表示历史已确认（自动应用）。
- `verification.fee_merge{columns,status,checked_rows,skipped_rows,mismatches[]}`：`status ∈ ok/skipped/failed`；`failed` 不会出现在 200（已转 422），200 里最多是 `ok/skipped`。

**新增 DTO 字段表（可直接落 pydantic / SDK）**：

| DTO | 字段 | 类型 | 可空 | 枚举/说明 |
|---|---|---|---|---|
| `PreviewOut` | `broker` | object | 否 | `{code,label,detected}` |
| | `signature` | string | 否 | 64 hex |
| | `stage` | string | 否 | `mapping/types/ready/legacy` |
| | `mapping_version` | int | 否 | 乐观锁版本 |
| | `header_row_index` | int | 是 | 0-based 表头行 |
| | `template` | object | 是 | `{id,match:exact/stale/none,applied,confirmed_at,origin_batch_id,ignored[]}` |
| | `catalog[]` | array | 否 | `{field,db_column,label,level:L1/L2/L3,mergeable,value_type}` |
| | `categories[]` | array | 否 | `{code,label}`，10 项 |
| | `types[]` | array | 否 | 见下 |
| | `verification` | object | 否 | `{fee_merge{columns,status:ok/skipped/failed,checked_rows,skipped_rows,mismatches[]}}` |
| | `blocking[]` | array | 否 | `{code,field,message}`；code ∈ `IMPORT_COLUMNS_UNRESOLVED/IMPORT_CLASSIFICATION_SOURCE_MISSING/IMPORT_PROFILE_REQUIRED` |
| | `requires_reimport_ack` | bool | 否 | 同文件改映射重导时为 true |
| | `estimated_available` | bool | 否 | 未解析时 false；UI 显示「待映射后计算」而不是 0 |
| `types[]` | `type_key` | string | 否 | 1~64 |
| | `label` | string | 否 | 原始展示文本 |
| | `rows` | int | 否 | |
| | `amount_sum` | string | 否 | 定点金额 |
| | `samples[]` | string[] | 否 | ≤3 |
| | `suggested_category` | string | 是 | `BillCategory` code |
| | `source` | string | 否 | `history/rule/heuristic/none` |
| | `needs_confirmation` | bool | 否 | |
| | `category` | string | 是 | |
| | `discarded` | bool | 否 | |
| | `confirmed` | bool | 否 | |
| `BatchOut` | `counts.discarded` | int | 否 | 新增 |
| | `mapping` | object | 是 | `{source,template_id,signature,remap_required,is_origin}` |
| `RevokeOut` | `mapping_revoked` | object | 否 | `{mapping_deleted,types_deleted}` |

### 6.2.1 API-106 `GET /imports/previews/{preview_id}`

- 用途：刷新页面、PUT 409/410 后恢复、`sessionStorage` 保存 `preview_id` 后重新读取草稿；**无副作用、不延长 TTL**。
- 认证 read；只返回本人；跨用户 404；过期 410。
- 响应 200：同一个 `PreviewOut`（含 `stage/mapping_version/columns/catalog/categories/types/verification/template/blocking`）+ `ETag: W/"<mapping_version>"`；客户端用 `If-Match: "<mapping_version>"` 写回。
- 错误：`IMPORT_PREVIEW_NOT_FOUND`(404)、`IMPORT_PREVIEW_EXPIRED`(410)。
- 验收：上传后 GET 与 POST 返回同 `preview_id/stage`；保存 mapping 后 GET 看到 `mapping_version+1` 与 `types`；跨用户 404。

### 6.3 API-098 `PUT /imports/previews/{preview_id}/mapping`

请求：

```json
{
  "mapping_version": 0,
  "items": [
    {"column_key": "成交日期#0", "target_field": "trade_date"},
    {"column_key": "发生金额#0", "target_field": "amount", "options": {"amount_signed": true}},
    {"column_key": "其他费#0", "target_field": "other_fee"},
    {"column_key": "过户费#0", "target_field": "other_fee"}
  ],
  "ignored": ["备注#0"],
  "acknowledge_unmapped": true,
  "date_format": "YYYYMMDD",
  "broker": "huatai"
}
```

- 乐观锁：**必填头 `If-Match: "<mapping_version>"`**（首次 `"0"`；响应 `ETag: W/"<mapping_version>"`）；body `mapping_version` 可选，若传必须与头一致（不一致 → 422 `VALIDATION_ERROR`）。
- `If-Match` 不匹配 → 409 `VERSION_CONFLICT`（遵循 API-Catalog §0.3，不新增版本错误码）。
- 服务端在事务内 `SELECT ... FOR UPDATE` 锁 preview，校验后覆盖 `mapping_items`，`mapping_version++`，清空 `types_confirmed_at`/`type_resolutions`，用新 plan 重解析，聚合类型，更新 `estimated/verification`。
- body 可带 `broker`（`auto/huatai/generic/...`）用于用户改选 profile；设置后重解析，解决 `blocking[].IMPORT_PROFILE_REQUIRED` 的死路。
- 重聚合后**立即按 `import_bill_type` 自动填充已知类型**（`needs_confirmation=false`），只有未命中类型需要用户确认；全部命中或 `types=[]` → `stage=ready`，否则 `stage=types`。因此“修改对应关系”不会强迫用户重确认已知类型（D-13）。
- 若该 `preview_id` 已存在 `pending/running/succeeded` 批次（即已提交且未失败/未撤销），直接 409 `IMPORT_STAGE_CONFLICT`：提交后不再允许改草稿（job 用快照，改了也不会生效，只会造成认知不一致）。
- 成功 200：`PreviewOut`（`stage=types` 或 `ready`）。
- 幂等：PUT 本身不承诺跨版本重放；`If-Match` 过期即 409 `VERSION_CONFLICT`，客户端用 `GET /imports/previews/{id}` 刷新后重试。相同版本重复提交同一内容 → 200（无变化）。
- `mapping_version` **只在 API-098 递增**；API-099 不递增。`内容一致` 定义为 `mapping_items + ignored_columns + date_format` 的 canonical JSON（键排序、无空白）逐字相等。

错误：

| 错误码 | 场景 |
|---|---|
| `IMPORT_PREVIEW_NOT_FOUND` / `IMPORT_PREVIEW_EXPIRED` | 404/410 |
| `IMPORT_MAPPING_INVALID` | 未知字段/重复字段（非 other_fee）/other_fee 超 8 列/列键不存在 |
| `IMPORT_COLUMNS_UNRESOLVED` | L1/L1-trade 缺失或 cash mode 条件不满足（`errors[]` 列字段） |
| `IMPORT_CLASSIFICATION_SOURCE_MISSING` | side 与 summary 都未映射 |
| `IMPORT_UNMAPPED_COLUMNS_UNCONFIRMED` | 有未映射列但 `acknowledge_unmapped=false` |
| `IMPORT_FEE_MERGE_INVALID` | 费用合并算术错误 |
| `IMPORT_FEE_MERGE_VERIFY_FAILED` | 合并后余额链不符 |
| `IMPORT_LAYOUT_UNSUPPORTED` | 多行表头/找不到唯一表头行（合并单元格不在 MVP 检测范围） |
| `IMPORT_PROFILE_REQUIRED` | generic profile 遇到货基无余额/省心投等专属语义 |
| `VALIDATION_ERROR` | 日期格式非法、列键格式非法、`options` 非法等 |
| `VERSION_CONFLICT` | `If-Match` 版本过期 |
| `IMPORT_STAGE_CONFLICT` | `stage=legacy`（旧预览不支持两步编辑） |

验收：所有建议列可保存；缺 L1/L1-trade 被拒；重复 `other_fee` 求和；非 other_fee 重复被拒；未确认忽略被拒；跨用户 404；保存后模板/类型仍为 0。

### 6.4 API-099 `PUT /imports/previews/{preview_id}/types`

请求：

```json
{
  "mapping_version": 3,
  "resolutions": [
    {"type_key": "证券买入", "category": "buy"},
    {"type_key": "未知业务XYZ", "discard": true}
  ]
}
```

- 必须覆盖 preview 当前**全部** `types[]`（每个恰好一次）；缺 → `IMPORT_BILL_TYPES_UNRESOLVED`；多/未知 → `IMPORT_MAPPING_INVALID`。
- `category` 与 `discard=true` 互斥；`category` 必须 ∈ `BillCategory`，否则 `IMPORT_CATEGORY_INVALID`。
- 成功 200：`stage=ready`，`types_confirmed_at` 更新，`estimated.discarded` 重算；`mapping_version` 不变。
- 乐观锁：**必填头 `If-Match: "<mapping_version>"`**；不匹配 → 409 `VERSION_CONFLICT`。
- 幂等：同 `mapping_version` + 同 `resolutions` 重放 → 200（types 保存不递增 `mapping_version`）；mapping 已被改则版本不匹配 → 409，重新走类型。
- 若 preview 已 `ready` 且 resolutions 与已存一致 → 200（幂等）；不一致 → 409 `IMPORT_STAGE_CONFLICT`（要改类型请先修改 mapping 重新进入类型步骤，避免静默改已确认类型）。
- 其他错误：预览不存在/过期 404/410；mapping 尚未保存（`stage=mapping`）→ 409 `IMPORT_STAGE_CONFLICT`；该 preview 已存在 `pending/running/succeeded` 批次 → 409 `IMPORT_STAGE_CONFLICT`；跨用户 404。

### 6.5 API-100 / API-101 模板管理

`GET /imports/templates` → `items[]{template_id,broker,signature,header_count,date_format,items[],ignored[],origin_batch_id,last_batch_id,created_at,updated_at}` + `next_cursor/has_more`；`limit`(1~200，默认 50)/`cursor`；按 `updated_at DESC`；只返回本人；不含任何行数据。

`DELETE /imports/templates/{template_id}` → 204；删除模板，不影响已导入 trade；审计 `import.template.deleted`；跨用户 404 `IMPORT_TEMPLATE_NOT_FOUND`。用于「重新对应」入口。

### 6.6 API-102~105 管理员规则

- 全部 `require_admin()`；**不能**访问用户模板/预览（管理员越界读业务数据仍 403 `ADMIN_CANNOT_READ_TRADES`）。
- `GET` 支持 `broker/enabled/q/limit/cursor`；返回 `rule_id,broker,header_norm,match_type,target_field,options,priority,enabled,is_system,is_fingerprint,weight,version,created_at`。
- `POST` body：`broker`(1~16) / `header_norm`(1~128) / `match_type`(exact/prefix/contains) / `target_field`∈目录 / `options`(仅 `amount_signed` 合法) / `priority`(1~999) / `enabled` / `is_fingerprint` / `weight`(1~10) / `note`；重复 → 409 `IMPORT_RULE_CONFLICT`。
- `PATCH` 必填 `If-Match: "<version>"`（旧版本 → 409 `VERSION_CONFLICT`）；系统行只允许改 `enabled/priority/note/weight/is_fingerprint/options/match_type`（改 `target_field` → 422，提示禁用后新建）。
- `DELETE` 系统行 → 422 `VALIDATION_ERROR`（detail「系统内置规则不可删除，请禁用」）；自定义行 204。
- 审计：`import_rule.created/updated/deleted`，仅记录 id/broker/header_norm/target_field/version，不记录用户数据。

### 6.7 存量端点 additive 变更

| 端点 | 变更 |
|---|---|
| API-010 `POST /imports/preview` | 请求新增 `broker?/reuse_template?`；响应新增 `broker/signature/stage/mapping_version/template/catalog/types/verification/blocking`；**缺 L1 不再 422**（返回 200 + blocking）；`mapping` 仅 legacy |
| API-011 `POST /imports` | 请求新增 `mapping_version?`、`acknowledge_reimport?`；`mapping_version` 不一致 → 409 `VERSION_CONFLICT`；`mapping` 仅用于 `stage=legacy`；v2 预览必须 `stage=ready`，否则 409 `IMPORT_STAGE_CONFLICT`；`requires_reimport_ack && mode!=replace_range && !acknowledge_reimport` → 409 `IMPORT_REIMPORT_CONFLICT`；快照写入 job params |
| API-013 `GET /imports/{batch_id}` | `counts` 新增 `discarded`；新增 `mapping{source,template_id,signature,remap_required,is_origin}`（`is_origin` 决定撤销对话框文案）；`assumptions` 保留 |
| API-014 `GET .../quarantine` | `reason_code` 白名单新增 `BILL_TYPE_DISCARDED`；`raw` 支持 `note/money_fund_balance` 映射 |
| API-015 `POST .../revoke` | 响应新增**顶层** `mapping_revoked{mapping_deleted: bool, types_deleted: int}`；`reverted{inserted,updated}` 保持 int 不变（避免 bool 被 pydantic 变成 1/0）；行为见 §7.4 |
| API-012 `GET /imports` | 列表项新增 `discarded`、`mapping_source`（additive） |

### 6.8 错误码 / 警告码

新增 `ErrorCode`（同时进 `CATALOG` 与 API-Catalog §3）：

| 错误码 | HTTP | user_message（中文人话） | what_to_do |
|---|---|---|---|
| `IMPORT_MAPPING_INVALID` | 422 | 列对应不合法。 | 检查是否有重复字段或未知字段；只有其他费可以多列。 |
| `IMPORT_UNMAPPED_COLUMNS_UNCONFIRMED` | 422 | 还有表格列没有对应。 | 确认忽略这些列，或为它们指定字段。 |
| `IMPORT_FEE_MERGE_INVALID` | 422 | 其他费多列合并失败。 | 检查费用列是否都是金额；修正后重新对应。 |
| `IMPORT_FEE_MERGE_VERIFY_FAILED` | 422 | 费用合并后与资金余额对不上。 | 改为只映射一列费用，或检查是否把非费用列当成了费用。 |
| `IMPORT_BILL_TYPES_UNRESOLVED` | 422 | 还有账单类型没有分类。 | 为每个类型选择 Category，或标记为废弃。 |
| `IMPORT_CATEGORY_INVALID` | 422 | 账单分类不合法。 | 从系统提供的 10 个分类中选择。 |
| `IMPORT_CLASSIFICATION_SOURCE_MISSING` | 422 | 无法判断买卖方向或账单类型。 | 映射「买卖标志」或「摘要/业务名称」列。 |
| `IMPORT_PROFILE_REQUIRED` | 422 | 这张账单需要对应券商的能力才能正确处理。 | 选择正确的券商 profile，或映射缺失的货基余额列。 |
| `IMPORT_REIMPORT_CONFLICT` | 409 | 同一文件已用不同的对应关系导入过。 | 用区间替换重导，或确认「重新导入」后再提交。 |
| `IMPORT_STAGE_CONFLICT` | 409 | 当前导入阶段不允许该操作。 | 先完成列对应/类型确认。 |
| `IMPORT_LAYOUT_UNSUPPORTED` | 422 | 这个表格布局暂不支持。 | 导出为单行表头的 CSV 后重试。 |
| `IMPORT_TEMPLATE_NOT_FOUND` | 404 | 找不到这个对应关系。 | 刷新列表。 |
| `IMPORT_RULE_CONFLICT` | 409 | 规则已存在。 | 编辑已有规则或换一个表头。 |

新增警告码：`BILL_TYPE_DERIVED`、`FEE_MERGE_APPLIED`、`FEE_MERGE_UNVERIFIED`、`FEE_MERGE_SUSPECT`、`TEMPLATE_REUSED`、`TEMPLATE_DATE_FORMAT_MISMATCH`、`MAPPING_UNMAPPED_COLUMNS`、`MAPPING_CHANGED_REIMPORT`。

### 6.9 契约联动清单（实现 PR 必须同批）

1. `docs/architecture/v2/API-Catalog.md`：§1 加 9 行、§2 加 9 段、§3 加 13 个错误码、§6 验收矩阵、§8 FR 追溯（L3/L5）；§0.1 登记 `X-Import-Flow: v2` 兼容开关，§2 API-014 `reason_code` 加 `BILL_TYPE_DISCARDED`、API-010 参数表补 `broker/reuse_template` 并删未实现的 `mode`。
2. `stock_contracts/errors.py::SEED_ERROR_COUNT` 47 → **60**（+13），同步 `test_errors_catalog`/API-Catalog 计数断言。
3. `make api-snapshot` → `contracts/openapi.json`；`make api-check`。
4. 契约锁：`backend/tests/contract/test_openapi_contract.py` 的 `EXPECTED_ALL_ENDPOINTS` 96→**105**，新增 9 个 operation 的示例/错误码断言；仓库**没有** `openapi/operationId.lock.json`，不要引用。
5. `make sdk` → `frontend/packages/api-client/src/api/generated/schema.d.ts`（勿手改）。
6. `frontend/packages/api-client/src/http.ts` 加 `getPreview/saveMapping/saveTypes/listTemplates/deleteTemplate`；admin 规则客户端（可同批或 P1）。
7. `frontend/packages/api-client/src/contract.test.ts` 新增独立 `IMPORT_V2_ENDPOINTS` 名单（9 条），**不改**一期 30 条硬编码与 docstring。
8. `frontend/AGENTS.md` 记录 shadcn/Tailwind 例外与边界。
9. `docs/DATABASE.md` §2 `ledger` 表清单加入 `import_template / import_bill_type / import_column_rule`。
10. `docs/prd/PRD-v2.md` FR-L3 增补：列/类型人工映射、用户模板、撤销删映射（不新增 FR 编号）。
11. 门禁：`scripts/ci/gate_endpoint_coverage.py` 的 `EXPECTED_ALL_ENDPOINTS=105`；`docs/DEV-PLAN-v2.md` 附录 A.1 登记 API-098~106；`test_isolation_matrix.py` 的 RESOURCE/UNAUTHENTICATED/ADMIN_FORBIDDEN 三类矩阵补新端点；`app.py` 注册 `admin_import_rules` router；`gate-rls` 覆盖三张新表。
12. review 侧 category 白名单（`rebuild._KIND_BY_CATEGORY`、behavior/attribution 的 category 集合）改用 `BillCategory` 或加一致性单测，避免 D-15 上提后再次漂移。

---

## 7. 后端实现

### 7.1 解析器重构（不改变华泰口径）

目标：把「怎么读表 / 哪列是什么」与「业务语义（货基、可转债、省心投、合计行）」解耦。

| 模块 | 职责 |
|---|---|
| `parsers/mapping.py`（新） | `header_key`、`signature`、`MappingPlan`、`validate_plan`、`merge_fee_units`、`normalize_type_key`、`derive_type_key` |
| `parsers/table.py`（新） | 读 xls/xlsx/csv（复用现有 `_read_rows`）、**纯 headers 表头探测**、列键生成、按 plan 取列下标、按字段聚值；返回 `header_row_index` |
| 表头探测算法 | 扫描前 30 行，按「非空单元格比例 + 文本占比 + **静态别名命中数（`FIELD_ALIASES`，不是 admin 规则/plan）**」打分；并列时 `line_no` 最小确定性 tiebreak；仍无法唯一/疑似多行表头 → `IMPORT_LAYOUT_UNSUPPORTED`。探测**不依赖 plan/管理员规则**，并在 preview/batch 持久化 `header_row_index`；commit 只用快照，不重探测。合并单元格不在 MVP 检测范围（读取层只给字符串矩阵），不承诺识别 |
| `parsers/profiles.py`（新） | `Profile` dataclass + 注册表：`huatai`（`supports_money_fund/cbond/sxt`）、`generic`；`detect_broker(headers, rules)` |
| `parsers/huatai.py`（改） | 保留 `_build_candidate` 与业务分支；入参从 `mapping: dict[str,str]` 改为 `plan: MappingPlan`；`field_value(field)` 对 `other_fee` 返回多列和；金额带符号由 `options.amount_signed` 决定；其他逻辑不动 |
| `parsers/columns.py`（改） | `COLUMN_ALIASES` 保留给 legacy 路径（不改语义）；新 seed 源在 `stock_contracts/import_fields.py::FIELD_ALIASES`；`map_columns` 标记 legacy（仅旧路径/兼容） |

**关键改动点**：

1. `_build_candidate` 现在用 `field_headers[field]` 取单个表头；改为 `field_columns[field]`（列下标元组），`other_fee` 用求和包装。
2. `amount_signed` 从 `is_signed_amount_header(header)` 改为 plan 的 `options.amount_signed`，系统规则预置。
3. `parse_bill` 增加 `profile` 参数；`huatai` 分支保留 `_money_fund_*`、`_CBOND_LOT_SIZE`、省心投；`generic` 只保留通用规则（合计停表、零金额信息行、金额/方向推导、隔离），检测到货基行无余额/省心投摘要等专属构造时 **fail-closed** `IMPORT_PROFILE_REQUIRED`，不静默按普通交易入库。
4. `parse_bill` 的 `mapping` 参数保留为 legacy 兼容（内部转成 plan），新路径走 `plan`。
5. `ParsedBill` 增加 `signature`、`profile`、`mapping_items` 快照（可选），其余字段不变。

**generic profile 能力边界（必须在前端/结果页可见）**：

- 支持：平铺表头、日期格式、代码/名称、方向、量/价、带符号或毛额、佣金/印花税/其他费（含多列合并）、资金余额、摘要类型、去重、隔离、**合计行停表、零金额信息行隔离**（这些是通用规则，不依赖 profile）。
- 货基：若映射了 `money_fund_balance` 列，按货基行处理；**没有该列但检测到货基行**（名称含「货币」或价格=1 且费用全 0）→ **fail-closed** `IMPORT_PROFILE_REQUIRED`（422，提示映射货基余额列或选择对应券商 profile），不按普通交易静默入库（否则会复现 2026-10-29 D14 的资产错账）。
- 省心投：检测到 `[省心投]申购确认`/`[省心投]红利发放` 等华泰专属摘要 → `IMPORT_PROFILE_REQUIRED`（提示选择 `huatai` profile），generic 不猜测语义。
- 可转债：×10/手口径在 **review 层**按 `is_convertible_bond` 判定，与 profile 无关；解析器里的 `_CBOND_LOT_SIZE` 只用于 WARN，不是 generic 的差异化能力（删除原「generic 会手/张偏差」的错误表述）。

**回归闸门**：用现有 `fixtures/market/bill_huatai.xlsx` + 旧 mapping 生成 legacy plan，新旧两条路径的 `ParsedBill` 必须逐字段相等（行数、金额、费用、余额、警告、假设、隔离行）。这是 M2 的退出条件。

### 7.2 应用层

`application/preview.py`：

- **legacy 分支**（无 `X-Import-Flow: v2`）：调用旧 `parse_bill` 严格校验，缺列仍 422；`stage=legacy`，`can_commit=true`（依据旧 `estimated`），行为与发布前一致。
- `create_preview(...)`（v2）：读文件 → `inspect_table` 只取表头/样例/列键/签名/`header_row_index`（**不要求 L1 齐全**）→ 识别 broker → 查模板（`reuse_template`）→ 生成建议（template/system/alias）→ 仅当 plan 完整时尝试解析（模板命中或全部 L1/L2 建议齐全）→ 写 preview（stage=mapping/types/ready）。
  - v2 还要查「同 `file_sha256` 且 `mapping_fingerprint` 不同的历史成功批次」：命中 → warning `MAPPING_CHANGED_REIMPORT` + `requires_reimport_ack=true`，提示改用 `replace_range` 或显式确认重导。
  - 注意：**不能**把建议 plan 当成已确认；`columns[].source` 标明来源，`can_commit=false` 除非模板命中且类型全已知。
  - 非模板命中时，即使建议 plan 完整、解析成功，也只用于 `estimated/verification`，**不返回 `types`**（`stage=mapping`）；类型聚合只在 `save_mapping` 成功后暴露，保证「先列对应、后类型」的顺序。
  - 建议 plan 不完整/解析失败**不阻断上传**：返回 `stage=mapping`、`types=[]`、`blocking[]`（缺哪些 L1/L2）、`estimated` 置 0 且 `estimated_available=false`（UI 显示「待映射后计算」，不能把 0 当空文件）；用户手工补全后再走 `save_mapping` 的硬校验。上传即 422 会让「未被规则识别的表头」永远无法进入映射界面——这正是当前实现的最大可用性缺口。
  - **profile 专属语义**（货基无余额/省心投）在 `create_preview` 只转成 `blocking[]` 的 `IMPORT_PROFILE_REQUIRED` + 建议 broker（让用户改选 profile），**不直接 422**；`save_mapping`/commit 才 fail-closed 422。否则用户会被卡在“还没机会改券商”的死路。
- `save_mapping(...)`：锁 preview → 校验 → 重解析 → 聚合类型 → 重算 `estimated`（含已存在 dedupe 的 skipped）→ 重算 `mapping_fingerprint`（含 `mapping_items+ignored_columns+date_format`）与 `requires_reimport_ack` → 写 `mapping_items/mapping_version/mapping_confirmed_at/types/verification/estimated` → 清空旧类型确认。**不写任何用户级持久记忆**（`import_template`/`import_bill_type` 只在 commit 成功时写）。
- `save_types(...)`：锁 preview → 校验覆盖率 → 写 `type_resolutions/types_confirmed_at` → stage=ready。
- `preview_out(...)`：组装 `catalog/columns/types/verification`；金额一律 `Money` 字符串。

`application/imports.py::commit_batch`：

1. 读 preview（存在性/过期）与 job params 快照（`mapping_items/type_resolutions/mapping_version/signature`）。
2. 用快照 plan 解析（与预览同一函数 → 计数一致）；generic profile 命中货基无余额/省心投等专属构造时，在写任何 trade 之前失败 `IMPORT_PROFILE_REQUIRED`（job error），不留半截数据。
3. 逐行：
   - `type_key` 命中 `resolution`：
     - `discard` → 隔离 `BILL_TYPE_DISCARDED`，`discarded_count++`；
     - 否则 `category=resolution.category`；`summary_code` 取建议规则码（有则写，无则 NULL）。
   - 未命中 → 防御性失败 `IMPORT_BILL_TYPES_UNRESOLVED`（理论上不可能，stage=ready 保证）。
4. dedupe/skip、写 `trade`（含 `other_fee_units` 合并值）。
5. 收尾对账（现有）；若 `merged_fee && filled_count>0` → `remap_required=true` + warning `FEE_MERGE_SUSPECT`。
6. 写记忆前**重读 `import_batch.revoked_at`**：若已撤销 → 跳过模板/类型写入（避免「撤销后 worker 又写回孤儿记忆」），批次收口为 revoked/cancelled。
7. 同事务 upsert `import_template`（`origin_batch_id` 首次、`last_batch_id` 最新）与 `import_bill_type`：
   - 新类型 → `origin_batch_id=last_write_batch_id=batch.id`；
   - 已存在且 category/discarded 未变 → 不写（仅复用）；
   - 已存在但被用户改过 → 更新 category/discarded，`last_write_batch_id=batch.id`，`origin_batch_id` 保留首次（审计）。
8. 批次写 `mapping_items/type_resolutions/signature/template_id/mapping_source/discarded_count`；同时沿用 `columns`（`header → field`，重复列取首个）供隔离区 `raw` 展示兼容，权威快照仍是 `mapping_items`。

`commit_import`（API）在创建批次/入队前：

- 校验 `preview.stage in ('ready','legacy')`；legacy 走旧逻辑（D-19）。
- 把快照写进 `job.params`（JSON 可序列化）。
- 创建 PENDING 批次时使用 `preview.broker`、`preview.signature`、`mapping_items`（不再硬编码 huatai/空 columns）。

### 7.3 分类口径（与现有规则共存）

新增 `application/classify.py::resolve_type_category(...)`：

```text
1. 规范化 type_key
2. import_bill_type 命中（用户级）→ category / discarded
3. 否则 match_rule（现有系统+用户摘要规则）→ rule.category
4. 否则 None → 隔离 UNKNOWN_SUMMARY
```

- 导入 commit 用 `preview.type_resolutions`（快照）优先，保证与用户确认一致；`import_bill_type` 是下次导入的建议来源。
- `reclassify_user`（API-016）：对每行先查 `import_bill_type`：
  - 命中且未废弃 → 保持/写入该 category；
  - 命中但 `discarded=true` → **跳过**（不删历史行、不改 category；废弃只在导入时生效）；
  - 未命中 → 走原 `match_rule`。
  **不**因类型映射删除而反向改历史行。
- `trade.summary_code`：来自规则的短码；用户手选类型无规则时写 NULL（不伪造 `sys_*`）。
- Category 仍是 10 值；`bonus` 继续保留（当前送股写 `other`，不改变行为）。

### 7.4 撤销（D-14）

`revoke_batch_now(...)` 在现有「软删本批次 trade + 补差行」之外，按 H-2 批准口径执行：

| 撤销对象 | 列/字段模板（`import_template`） | 账单类型映射（`import_bill_type`） | 流水/补差 |
|---|---|---|---|
| **origin 批次**（`template.found && template.origin_batch_id == batch.id`） | **删除**（该用户该 `signature`） | 删除 `last_write_batch_id == batch.id` 的本批次写入项（新增或修改） | 软删本批次 |
| **增量批次**（同签名但不是 origin） | **保留**（列对应不破坏） | 删除 `last_write_batch_id == batch.id` 的本批次写入项（新增或修改） | 软删本批次 |
| 无模板（legacy/手工删除） | 无操作 | 同上 | 软删本批次 |

实现顺序：

1. 现有：软删本批次 `trade` + 归属本批次的 `side=missing` 补差行。
2. `DELETE FROM import_bill_type WHERE user_id=? AND last_write_batch_id=?`（只删本批次**新增或修改**的类型；仅被本批次复用的旧类型不删，避免破坏其他批次）。
3. `SELECT import_template WHERE user_id=? AND signature=?`；若存在且 `origin_batch_id == batch.id` → `DELETE`，`mapping_deleted=true`；否则 `mapping_deleted=false`。
4. 若保留模板且 `last_batch_id == batch.id` → 重算为「同 `signature`、未撤销批次中 `created_at` 最大者」（排除本批次）；没有则回退 `origin_batch_id`。不要直接回退 origin，否则会跳过中间仍有效的增量批次（B1→B2→B3 撤销 B3 应回到 B2，而不是 B1）。
5. 批次写 `assumptions["mapping_revoked"]=true`；审计 `import.mapping_revoked`（`signature` 哈希、`mapping_deleted`、`types_deleted`）。
6. 响应顶层 `mapping_revoked{mapping_deleted, types_deleted}`；`reverted{inserted,updated}` 保持原有整数计数。
7. 删除类型记忆**不回写**历史 trade：其他批次已写入的 `category` 保留；后续 `reclassify` 若找不到类型记忆再回退摘要规则。
8. 前端撤销对话框按类型给不同文案：
   - origin：「撤销会删除本次写入的流水、补差，以及**该格式的列对应关系**；下次导入需要重新确认。」
   - 增量：「撤销会删除本次写入的流水、补差，以及**本批次新增或修改的账单类型记忆**；已确认的列对应关系保留。」

> 边界：若 origin 批次先撤销、模板已删，后续增量批次再撤销时只删本批次类型（无模板可删）；若模板被 `DELETE /imports/templates` 手工删除，origin 撤销时 `mapping_deleted=false`。增量撤销**不**删除仅被复用的旧类型，这是为了保护其他批次；本批次改过的类型会随撤销删除（**不回滚旧值**，`import_bill_type` 不存历史版本；P1 可加历史表）；需要彻底清空类型记忆时走设置页（P1）。

### 7.5 性能、并发、可观测

- **并发**：mapping/types 保存对 preview 行 `FOR UPDATE`；worker 内模板 upsert 用 `ON CONFLICT DO UPDATE`，`version` 只增，不返回 HTTP 409（job 内记录 warning）；同签名两文件并发以最后成功 commit 为准，不静默丢但可审计。
- **性能**：预览/保存各解析一次；5k 行目标 <2s，50k 行 <10s；类型聚合 O(n)；签名 O(列数)。超时/大文件沿用现有 20MB 限制。
- **可观测**：日志 `import.preview.created`（broker/signature/columns/stage）、`import.mapping.saved`（fields/merged/ignored/version）、`import.types.saved`（types/discarded）、`import.template.hit/miss`、`import.fee.merge_failed`（line/columns）；指标计数与耗时。
- **审计**：`import.mapping.saved`、`import.types.saved`、`import.template.created/deleted`、`import_rule.*`；审计不含原始行内容（表头可以，账号列已脱敏）。
- **隐私**：类型标签来自 `summary`（业务类型），系统建议优先映射业务类型列而非备注列；若用户把备注映射为 `summary`，标签会落 `import_bill_type`，撤销导入可删；模板只存表头，不存行数据。

### 7.6 改动文件清单（实现 PR 对照）

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/packages/contracts/src/stock_contracts/enums.py` | 改 | `BillCategory`、`ImportStage`、`QuarantineReason.BILL_TYPE_DISCARDED`、新 `WarningCode` |
| `backend/packages/contracts/src/stock_contracts/errors.py` | 改 | 13 个新 `ErrorCode` + `CATALOG` 人话；`SEED_ERROR_COUNT` 47→60 |
| `backend/packages/contracts/src/stock_contracts/import_fields.py` | 新 | 字段目录、`FIELD_ALIASES`、`BROKER_LABELS` |
| `backend/packages/ledger/src/stock_ledger/models.py` | 改 | 3 个新模型 + preview/batch 新列 + `LEDGER_TABLES` |
| `backend/packages/ledger/src/stock_ledger/parsers/mapping.py` | 新 | 列键/签名/plan 校验/费用合并/类型键 |
| `backend/packages/ledger/src/stock_ledger/parsers/table.py` | 新 | 读表、表头定位、列取值 |
| `backend/packages/ledger/src/stock_ledger/parsers/profiles.py` | 新 | broker 识别、huatai/generic profile |
| `backend/packages/ledger/src/stock_ledger/parsers/huatai.py` | 改 | plan 驱动；保留华泰语义 |
| `backend/packages/ledger/src/stock_ledger/parsers/columns.py` | 改 | legacy 标记；`normalize_header_v2`（或放 mapping.py） |
| `backend/packages/ledger/src/stock_ledger/repositories/import_templates.py` | 新 | 模板 CRUD/upsert |
| `backend/packages/ledger/src/stock_ledger/repositories/import_types.py` | 新 | 类型映射 CRUD |
| `backend/packages/ledger/src/stock_ledger/repositories/import_rules.py` | 新 | 系统规则 CRUD + seed |
| `backend/packages/ledger/src/stock_ledger/repositories/imports.py` | 改 | preview/batch 新字段读写 |
| `backend/packages/ledger/src/stock_ledger/application/preview.py` | 改 | inspect、save_mapping、save_types |
| `backend/packages/ledger/src/stock_ledger/application/imports.py` | 改 | snapshot commit、模板/类型落库、撤销删除 |
| `backend/packages/ledger/src/stock_ledger/application/classify.py` | 改 | 类型映射优先 |
| `backend/packages/platform/src/stock_platform/ids.py` | 改 | `imt/ibt/icr` 前缀 |
| `backend/services/api/src/stock_api/routers/imports.py` | 改 | API-010/011/013/014/015 + 098~101 |
| `backend/services/api/src/stock_api/routers/admin_import_rules.py` | 新 | API-102~105 |
| `backend/services/api/src/stock_api/schemas.py` | 改 | 新请求/响应模型 |
| `backend/services/api/src/stock_api/openapi_meta.py` | 改 | 示例/错误码/operationId |
| `backend/services/api/src/stock_api/version.py` | 改 | `0015_import_mapping` |
| `backend/migrations/versions/0015_import_mapping.py` | 新 | 3 表 + 2 表扩展 + RLS + backfill |
| `backend/tests/...` | 新/改 | §9.1 矩阵；fixture 三条 |
| `frontend/apps/web/package.json` / `tailwind.config.ts` / `postcss.config.cjs` / `components.json` | 新/改 | shadcn/Tailwind 接入 |
| `frontend/apps/web/src/components/ui/*` / `src/lib/utils.ts` | 新 | shadcn 组件 + `cn` |
| `frontend/apps/web/src/features/import/*` | 新 | 向导步骤/状态/校验/测试 |
| `frontend/apps/web/src/pages/Import.tsx` | 改 | 承载 wizard；保留 welcome |
| `frontend/apps/web/src/test/setup.ts` | 新 | jsdom polyfill |
| `frontend/packages/api-client/src/http.ts` / `contract.test.ts` | 改 | `getPreview/saveMapping/saveTypes` 等新方法；独立 v2 端点名单 |
| `frontend/scripts/check-budget.mjs` | 改 | import chunk 预算 |


---

## 8. 前端设计（shadcn）

### 8.1 技术选型与既有决策冲突（H-1）

原前端选型（[02-design-research.md §208-209](../reviews/2026-10-28-frontend-refactor/02-design-research.md)）明确：

> 「不引入：Tailwind、ECharts/Recharts/Chart.js、AG Grid、任何全量 UI 库。」

本次用户明确要求「前端引入 shadcn 库」。shadcn 不是运行时 npm 包，而是 **Radix primitives + CVA + Tailwind 的源码组件**；要真正使用 shadcn，必须引入 Tailwind。因此本设计提出**限定范围例外**：

- 只在 `/import` 功能域引入 Tailwind + shadcn；其他页面继续用现有 tokens + 手写 CSS。
- `preflight: false`（不重置全局样式）、`content` 只扫 import 与 `components/ui`、颜色映射到既有 CSS 变量、CSS 随路由懒加载。
- 首屏预算（JS ≤220KB / CSS ≤30KB gzip）只检查入口 chunk；import 页面是 lazy chunk，不占首屏。另设 import chunk 预算（JS ≤80KB gzip、CSS ≤20KB gzip），在 `frontend/scripts/check-budget.mjs` 里开 `build.manifest`，按 Import 可达 chunk 集合求和（不能只匹配 `Import-*` 文件名，会漏共享 chunk/CSS）；M4 第一天先做“空向导 spike”实测，再按“实测+20% 余量”定最终预算。超预算时裁剪顺序：Tooltip/Progress（已不引入）→ Select 换原生/手写 → 拆步骤懒加载。
- 若评审否决 Tailwind，则退化为「Radix primitives + 手写 CSS」，不满足「shadcn」字面要求，需用户确认。

### 8.2 工程接入

依赖（`frontend/apps/web/package.json`）：

```json
{
  "dependencies": {
    "@radix-ui/react-alert-dialog": "^1.x",
    "@radix-ui/react-dialog": "^1.x",
    "@radix-ui/react-label": "^2.x",
    "@radix-ui/react-select": "^2.x",
    "@radix-ui/react-slot": "^1.x",
    "@radix-ui/react-switch": "^1.x",
    "class-variance-authority": "^0.7.x",
    "clsx": "^2.x",
    "lucide-react": "^0.468.0",
    "tailwind-merge": "^2.x"
  },
  "devDependencies": {
    "@types/node": "^22.x",
    "tailwindcss": "^3.4.x",
    "postcss": "^8.x",
    "autoprefixer": "^10.x",
    "tailwindcss-animate": "^1.x",
    "@testing-library/user-event": "^14.x"
  }
}
```

工程文件：

| 文件 | 作用 |
|---|---|
| `frontend/apps/web/tailwind.config.ts` | `content` 只含 `src/features/import/**/*.{ts,tsx}`、`src/components/ui/**/*.{ts,tsx}`、`src/pages/Import.tsx`；`preflight:false`；`prefix:"tw-"`（避免 `.table` 等通用类名与既有 CSS 冲突）；`darkMode:["selector",'[data-theme="dark"]']`；`import animate from "tailwindcss-animate"; plugins:[animate]`；颜色映射见下 |
| `frontend/apps/web/tsconfig.node.json` | `types:["node"]`，include 加 `tailwind.config.ts`/`postcss.config.cjs`（否则配置文件零类型检查、`node:path` 无类型） |
| `frontend/apps/web/postcss.config.cjs` | tailwind + autoprefixer |
| `frontend/apps/web/components.json` | shadcn CLI 配置：`"style":"new-york"`、`"cssVariables":false`（**禁止写 `:root` shadcn 变量，覆盖 `--muted/--border`**）、`"tailwind":{"config":"tailwind.config.ts","css":"src/features/import/import.css","baseColor":"neutral","prefix":"tw-"}`、`aliases.components=@/components`、`aliases.utils=@/lib/utils` |
| `frontend/apps/web/tsconfig.json` / `vite.config.ts` | 增加 `@/* → src/*` 别名（shadcn 默认导入路径） |
| `src/lib/utils.ts` | `cn()`（clsx + tailwind-merge） |
| `src/features/import/import.css` | `@tailwind base; @tailwind components; @tailwind utilities;` + **完整 scoped reset**（见下）；**只在 Import 页面 import**，保证 CSS 进 lazy chunk；禁止 `:root`/全局元素选择器 |
| `src/components/ui/*` | shadcn 源码组件：`button/card/table/switch/select/dialog/alert-dialog/badge/label`；**不引入 `progress`**（复用现有 `JobProgress`，避免双进度条）；`tooltip` 按预算可选 |
| `src/test/setup.ts` | jsdom polyfill：`PointerEvent` 桩、`ResizeObserver`、`matchMedia`、`scrollIntoView`、`scrollTo`、`setPointerCapture/hasPointerCapture/releasePointerCapture`、`requestAnimationFrame`；`@testing-library/jest-dom`（若引入） |

**Scoped reset（必须完整，P0）**：

```css
/* 页面根与 Portal 根都带 data-import-ui */
[data-import-ui], [data-import-ui] *, [data-import-ui] ::before, [data-import-ui] ::after {
  box-sizing: border-box;
  border-style: solid;      /* 只补 border-color 不够：preflight 关掉后 border-style 仍是 none */
  border-width: 0;
  border-color: var(--line);
}
[data-import-ui] button, [data-import-ui] input,
[data-import-ui] select, [data-import-ui] textarea {
  font: inherit;
  background: none;
  border: 0;
}
[data-import-ui] { font-family: var(--font-ui); }
```

- Radix `Dialog/AlertDialog/Select` 的 Portal **必须**挂到 `<div id="import-portal" data-import-ui>`（页面挂载时创建，卸载时移除），并给 `Dialog.Portal/AlertDialog.Portal/Select.Portal` 传 `container`；否则弹层落在 `body` 上，scoped reset 失效。
- `import.css` 禁止 `:root` 与全局元素选择器；`components.json` 的 `cssVariables:false` 防止 shadcn CLI 写入覆盖 `--muted/--border` 的变量块。
- Tailwind 配 `prefix:"tw-"`，shadcn CLI 生成的组件类与手写页面类都必须带 `tw-`（`cn()` 不会自动补前缀）；这是为了避免 `.table`/`.flex` 与既有 CSS 同名。

**alpha 修饰符**：Tailwind v3 对 `var(--accent)` 这类颜色不能生成 `bg-primary/90` 的 alpha；shadcn 模板里 `hover:bg-primary/90`、`bg-black/80` 必须改成实体变量/`rgba()`（例如 `hover:bg-accent`、遮罩 `rgba(0,0,0,.5)`），否则 hover 态“看起来没反应”。

**颜色映射（不新增色值）**：

```ts
// tailwind.config.ts
theme: {
  extend: {
    colors: {
      background: "var(--bg)",
      foreground: "var(--text)",
      card: "var(--surface-raised)",
      "card-foreground": "var(--text)",
      popover: "var(--surface-raised)",
      "popover-foreground": "var(--text)",
      primary: "var(--accent)",
      "primary-foreground": "var(--accent-contrast)",
      secondary: "var(--surface-2)",
      "secondary-foreground": "var(--text-2)",
      muted: "var(--surface-2)",
      "muted-foreground": "var(--muted)",
      accent: "var(--accent-soft)",
      "accent-foreground": "var(--text)",
      destructive: "var(--danger)",
      "destructive-foreground": "var(--accent-contrast)",
      border: "var(--line)",
      input: "var(--line-strong)",
      ring: "var(--accent)",
    },
    borderRadius: { lg: "var(--r-lg)", md: "var(--r-md)", sm: "var(--r-sm)" },
    fontFamily: { ui: "var(--font-ui)", display: "var(--font-display)", mono: "var(--font-mono)" },
  },
}
```

- 不定义 shadcn 默认的 `:root { --background: ... }`，避免与 `tokens.css` 的 `--accent` 等冲突；组件类直接引用 Tailwind color key。
- 深色主题由现有 `[data-theme="dark"]` 变量覆盖；Tailwind `dark:` 仅在需要时用。

**测试**：在 `vite.config.ts` 的 `test` 块加 `setupFiles: ["./src/test/setup.ts"]`（本仓没有独立 `vitest.config`）；Radix Select 在 jsdom 下首选**测试替身**（`vi.mock("../components/ui/select")` → 原生 `<select>` + 同 `aria-label`），真实 pointer 交互留给手工验收/P1 Playwright，避免 M4 被 Select polyfill 卡住。

### 8.3 页面与交互（`/import`）

页面仍由 `frontend/apps/web/src/pages/Import.tsx` 导出，内部换成 wizard；路由保持 lazy；保留 `?welcome=1` 首跑引导（`OnboardingGate` 依赖）。步骤：

1. **上传**（`Uploader` + shadcn `Card`/`Button`）
   - 文件选择、券商选择（默认自动识别，展示 `broker.label` + 「改」）、文件大小/格式提示。
   - 上传后展示文件摘要（名称/大小/哈希前 8 位）。
2. **列对应**（核心）
   - 行草稿模型：每列 `{column_key, target_field: string|null, accepted: boolean, explicitly_ignored: boolean}`。
     - `accepted=false` = **未确认**，不是忽略；提交时按未映射处理。
     - `explicitly_ignored=true` 只来自「忽略本列」按钮；`ignored[]` 只收这些列。
   - 顶部：`模板命中` 横幅（仅 `template.applied=true` 时显示「已复用你 2026-11-01 确认的对应关系」；`applied=false` 显示「格式有变化，请确认日期格式/新增列」+「修改对应关系」）；日期格式 `Select`（探测值可改，带歧义警告）。
   - 表头行：`表格列（样例 2~3 条） | 数据库列（必填徽标） | 系统建议 | 采用（Switch） | 忽略本列`。
   - 未映射/未确认行：`Select` 列出 `catalog`，按「必填 / 常用 / 其他」分组；已用字段禁用（`other_fee` 除外）；`Switch` 关闭时该行不进入 `items`。
   - 重复 `other_fee`：显示「合并 2 列，求和」徽标 + 组内列顺序；允许移除。
   - 底部校验栏：缺 L1/L2 红字；未确认/未映射列数；「全部接受系统建议」；「继续」。
   - 「继续」启用条件：L1 全确认 + L2 满足；**不满足时按钮禁用并定位到第一个缺失行**（不提供“点了再弹”的另一条路径）。
   - 点「继续」（仅在上面的条件满足后可点）：
     - 仍有未确认/未映射/显式忽略列 → `AlertDialog` 列出列名 +「这些列不会导入」+ 确认按钮；确认后带 `acknowledge_unmapped=true` 调 API；
     - 费用合并算术/链路错误 → 顶部错误卡 + 高亮 `other_fee` 组，`what_to_do` 展示。
   - 成功后进入步骤 3；`save_mapping` 聚合出的 `types=[]` 时直接进入 ready（跳过步骤 3）。
3. **账单类型**
   - `types[]` **恒返回全部类型**（含历史已匹配）；前端按 `needs_confirmation` 折叠：`N = types.filter(t => !t.needs_confirmation).length`。
   - 表头：`类型 | 行数 | 金额合计 | 样例 | Category | 废弃 | 确认`。
   - 行状态机：`unresolved → resolved(category | discard) → confirmed`；选择 Category 自动关闭废弃，打开废弃自动清空 Category；切换任一选项时确认开关重置为关。
   - 已自动匹配：折叠区「已自动匹配 N 类（上次确认）」；可展开改；展开修改前提示「这会改变你以后所有『X』类型账单的归类」。
   - 新类型：Category `Select`（后端 `categories[]` 的 10 个中文标签）+ 废弃 `Switch` + 确认 `Switch`（默认关）；「全部接受建议」。
   - 未解决行禁用「继续」，底部提示「还有 N 个类型未处理」；`types=[]` 时本步骤不出现。
   - **不提供**“分类但确认关”的提交：确认开关只对已 resolved 行可开；提交时 `resolved && confirmed` 才算完成。
4. **确认导入**
   - 摘要卡：文件、券商、映射来源（模板/手动）、列数/忽略列、类型数/废弃数、预估 parsed/inserted/skipped/quarantined/discarded、警告。
   - 若 `requires_reimport_ack=true`：显示「同一文件曾用不同的列对应导入过，直接重导可能产生重复行」横幅；提供「改用区间替换」/「确认重新导入」两个动作，后者带 `acknowledge_reimport=true`。
   - 「确认导入」按钮 → `POST /imports`；**每个 preview 只生成一次幂等键**（存 state，不每次点击 `newIdempotencyKey()`），pending 时按钮禁用 → 进度只用现有 `JobProgress`（不再叠加 shadcn Progress，避免双进度条）。
5. **结果**
   - 计数、模板「已保存，下次同格式将复用」、`remap_required` 横幅、去隔离区/流水链接。
   - 撤销按钮 → `AlertDialog` 按 `batch.mapping.is_origin` 给文案：origin 明示「同时删除列对应关系」；增量明示「保留列对应，只删本批次写入的账单类型记忆」。
   - `/imports` 批次列表与详情增加 `discarded` 计数与 `remap_required` 徽标，便于定位需要重配的批次。

**模板命中分支**：

- `stage=types`：直接进入步骤 3，顶部显示「列对应已复用」+「修改对应关系」。
- `stage=ready`：步骤 2 折叠为只读摘要，直接到确认导入；「修改对应关系」回到步骤 1（保存后重聚合；已知类型自动套用，仅新类型需确认）。
- `stage=mapping`：正常两步。
- `stage=legacy`/未知：不渲染新向导，提示「这次预览由旧版本创建，请重新上传」；`IMPORT_STAGE_CONFLICT` 给专门文案，避免用户看到裸 409。

### 8.4 状态、错误与零口径

- 服务端状态用 TanStack Query（项目已引入）：`useMutation` 保存 mapping/types/commit；成功后 `setQueryData` 更新 preview，避免重复拉取。
- 客户端只保存「待确认草稿」（哪些 switch 打开、选了哪个字段），不计算金额/类型统计。
- 所有计数、金额合计、预估都来自后端；前端禁止 `Number()/parseFloat/toFixed`（现有 ESLint 零口径规则继续生效）。
- 错误：统一 `ApiProblemError`；`problem.errors[]` 的 `field` 约定：
  - `items.{column_key}` → 该列行内错误（费用合并错误同时高亮整个 `other_fee` 组）；
  - `items.{column_key}.target_field` → 字段选择错误；
  - `types.{type_key}.category` → 类型行内错误；
  - `verification.fee_merge` → 顶部错误卡 + 费用组高亮。
- 网络失败可重试；PUT 用 `mapping_version` 防覆盖；409 时提示「内容已变化，已为你刷新」。
- 预览 TTL 24h：任意 PUT 返回 410 时提示「预览已过期，请重新上传」；已保存的模板/类型仍在，重新上传会自动命中。
- 刷新恢复：MVP 允许刷新后重新上传（预览 24h 内的 `preview_id` 也可放 `sessionStorage`，P1）；不做草稿本地持久化。

### 8.5 可访问性与测试锚点

- 每个 Switch 有 `<Label>` 与 `aria-describedby`（样例/字段名）；表格用真实 `<table>` 语义（shadcn Table）。
- AlertDialog 焦点陷阱、Esc 关闭、`aria-live` 播报校验结果。
- 颜色不是唯一信号：必填/错误同时有文字/图标。
- 测试锚点：`role="switch"` + `aria-label="采用 成交日期 → trade_date"`；按钮文案「继续」「确认导入」「全部接受系统建议」「修改对应关系」；错误卡 `role="alert"`。

**前端测试**（vitest + RTL）：

| 用例 | 断言 |
|---|---|
| 首次上传 huatai | 12 行建议；switch 默认关；「继续」禁用直到必填项（L1/L1-trade/L2）满足 |
| 全接受 + 继续 | 调 `saveMapping`；进入类型步骤 |
| 未映射列继续 | 弹二次确认；取消不调 API；确认后 `acknowledge_unmapped=true` |
| 多列 other_fee | 显示合并徽标；请求含两列 |
| 费用错误 | 显示指定错误 + 费用组高亮；不前进 |
| 类型未全选 | 「继续」禁用；选完/废弃后可继续 |
| 模板命中 | 跳过步骤 1；显示「已复用」；「修改对应关系」可回到步骤 1 |
| 增量新类型 | 只显示新类型；已知类型折叠 |
| 撤销 | origin 对话框含「删除列对应关系」；增量对话框含「保留列对应」；确认后展示对应提示 |
| 错误映射 | `errors[].field` 落到对应行 |
**存量测试迁移（P1，M4 必做）**：

- `Import.test.tsx`：`render` 外包 `QueryClientProvider`（`makeTestClient()`，`retry:false`）；`vi.mock("../client")` 工厂补齐 `preview/getPreview/saveMapping/saveTypes/commit/getBatch/revoke`；新增 `getPreview` 恢复用例。
- `Imports.test.tsx`：`countSummary`/精确文案断言改为包含 `discarded` 的新计数（或正则），不要锁死旧字符串。
- `contract.test.ts`：新增独立 `IMPORT_V2_ENDPOINTS` 9 条，**不改**一期 30 条硬编码与 docstring；否则会误报“一期破坏”。
- Radix Select 用 `vi.mock("../components/ui/select")` 替换为原生 `<select aria-label=...>`；Dialog/Switch 用真实组件即可，polyfill 见 §8.2。


---

## 9. 测试与验收

### 9.1 后端测试矩阵

| 层 | 用例 | 期望 |
|---|---|---|
| unit `mapping.py` | 列键/签名：顺序变化同签名；重复表头不同键；空表头 | 稳定/可区分 |
| unit `mapping.py` | 费用求和：`1.23+4.56=5.79`（分域）；空值=0；非数字/负数/溢出 | 结果正确/指定错误 |
| unit `mapping.py` | 类型键：NFKC/空白/大小写；派生类型 | 归一稳定 |
| unit `huatai` | legacy mapping vs plan 的 `ParsedBill` 逐字段相等 | 零回归 |
| unit `classify` | 类型映射优先于摘要规则；废弃；规则兜底 | category 正确 |
| API-098 | happy：12 列保存、类型聚合；缺 L1/L1-trade；重复字段；other_fee 多列；未确认忽略；版本冲突；跨用户 | 200/422/422/200/422/409/404 |
| API-098 | 保存 mapping 后查 `import_template`/`import_bill_type` | 均为 0（PUT 不写用户级记忆，commit 才写） |
| API-098 | 两列费用 + 余额链不符 | 422 `IMPORT_FEE_MERGE_VERIFY_FAILED`，含行号 |
| API-099 | 全覆盖；漏类型；非法 category；既分类又废弃；版本变化 | 200/422/422/422/409 |
| API-011 | `stage=mapping` commit → 409；`ready` → 202；快照后改 preview 不影响 job | 409/202/diff=0 |
| API-011 | 首次 commit 落模板/类型；二次同签名跳过步骤 1；新类型需确认 | 模板 1 条、类型 N 条 |
| API-015 | origin 撤销删模板 + 本批次写入类型；增量撤销保留模板、只删本批次写入类型；审计；重复撤销 409 | origin 模板 0；增量模板仍在、types 删除数正确 |
| API-102~105 | 非 admin 403；系统行删除 422；版本冲突 409；规则不影响模板 | 权限/不可变 |
| API-010 | 华泰新账号：12 列建议 + 类型建议；无表头布局 422；重复表头可映射 | 建议完整/错误清晰 |
| 真实 13 列 | `日期/币种/股东账号/.../发生金额/资金余额` 华泰账单 | 12 字段有建议、`币种` ignored、`account` 映射；`FIELD_ALIASES ⊇ COLUMN_ALIASES` |
| 兼容 | **不带** `X-Import-Flow` 的旧 preview→commit 全链路 | 与旧版行为一致（`stage=legacy`、缺列仍 422、默认 huatai）；既有 30+ 处导入测试不改 |
| v2 | 带 `X-Import-Flow: v2` 的 preview→mapping→types→commit | 新流程；旧客户端永不走 v2 |
| integration | 旧 fixture 新流程：inserted/quarantined 与 golden 一致；回合/净值/指标 diff=0 | 零口径回归 |
| isolation | 跨用户 preview/template/type 404；admin 规则不含用户数据 | 隔离 |
| 契约 | `make api-check`/`api-snapshot`/`sdk` | 全绿 |

新增 fixture（`fixtures/market/`）：

- `bill_generic_2fee.csv`：两列费用，可验证求和与余额链。
- `bill_generic_nosummary.csv`：无摘要，验证派生类型。
- `bill_huatai_newtype.xlsx`：在现有 fixture 基础上加一行新摘要，验证增量新类型。

### 9.2 手工验收（用固定账号 `1234@dev.com / 1234`）

1. 新账号首次导入 `bill_huatai.xlsx`：12 列建议全有；switch 默认关；点「全部接受」→ 类型列表出现；全部分类/废弃 → 确认导入；计数与旧链路一致；结果页提示模板已保存。
2. 再次导入同文件：不出现列对应步骤；类型全部自动匹配；直接确认；`inserted=0/skipped>0`（去重）。
3. 导入 `bill_huatai_newtype.xlsx`：只出现新类型；确认后成功；再导入同文件不再问。
4. 导入 `bill_generic_2fee.csv`：两列费用合并；金额=两列和；把一列改成文本 → 422 指定错误；修正后成功。
5. 未映射列：忽略 → 二次确认；取消 → 留在步骤 1；确认 → 成功。
6. 缺 L1-trade：删掉证券代码/数量/价格映射 → 「继续」禁用/422。
7. 撤销：首次（origin）批次撤销 → 对话框提示删列对应，`GET /imports/templates` 为空，再次导入需重配；增量批次撤销 → 对话框提示保留列对应，模板仍在，仅本批次新增/修改的类型被删。
8. 管理员：非 admin 调 API → 403；admin 改规则后新账号建议变化；已有模板不变。
9. 跨用户：用 B 账号访问 A 的 preview/template → 404。
10. 前端：`make lint`、`pnpm test`、`pnpm budget`；无 `parseFloat/Number/toFixed` 新增。

### 9.3 门禁

```bash
make lint
make typecheck
make test-p2          # 含新增 API/集成/隔离/契约
make test-isolation
make api-snapshot
make api-check
make sdk
make gate-golden-structure
cd frontend && pnpm -r test && pnpm -r build && pnpm budget
```

---

## 10. 迁移、发布与回滚

### 10.1 发布顺序

1. 合入迁移 `0015` + 契约 + 后端（preview/commit 新逻辑，legacy 兼容）。
2. `make api-snapshot && make api-check && make sdk`，前端类型更新。
3. 部署后端 → 冒烟：旧 preview（stage=legacy）仍可 commit；新 preview stage=mapping。
4. 部署前端（同一发布窗口；本仓单机部署，前后端同批）。
5. 跑手工验收 1~10；观察 `import.mapping.saved` / `import.template.hit` 日志。
6. 24h 后旧 preview 全部过期；下个迭代删除 legacy 分支。

### 10.2 回滚

- 前端回滚：旧页面**不带** `X-Import-Flow: v2` 上传 → 后端走 `stage=legacy` 旧单步流程，行为与发布前一致；因此回滚不受 24h 窗口限制。已用 v2 创建但未提交的 preview 在旧页面无法完成两步，会让用户重新上传（可接受）。
- 后端回滚：迁移是 additive（新表/新列），旧代码可忽略；`downgrade` 会 drop 映射数据（模板/类型）——只有在确认可丢失映射时才执行。
- 数据修复：撤销问题批次（origin 会删模板；增量保留模板）后重新导入；或 `DELETE /imports/templates/{id}` 强制重新对应。

### 10.3 兼容性

- API-010/011 旧字段保留；新字段 additive。
- **唯一行为分支**：`X-Import-Flow: v2` 才启用新流程；缺省走 legacy（旧校验/旧 commit/默认 huatai）。这是 v1 兼容开关，不改变旧请求语义；计划在 v2 稳定 90 天后下线 legacy。
- `counts.discarded` 新增；旧客户端忽略。
- `trade` 表结构不变；`other_fee_units` 仍是单列。
- 旧 CLI/seed 调用 `commit_batch` 时保持签名兼容（`mapping` 可选）。

---

## 11. 风险

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R-1 | 解析器重构引入华泰回归 | 中/高 | legacy vs plan 的 `ParsedBill` 逐字段 golden；先合解析器再合流程；fixture 全跑 |
| R-2 | shadcn/Tailwind 污染现有样式或超预算 | 中/中 | `preflight:false`、content 限定、CSS 懒加载、import chunk 独立预算、视觉回归 |
| R-3 | 多列费用映射错误但通过算术校验 | 中/高 | 余额链校验（合并组硬门）+ 导入后对账 `remap_required` + 撤销重配 |
| R-4 | 模板签名过严（列增删即失效） | 高/低 | 签名按列键集合；P1 相似模板提示；UI 显示「格式变了，请确认新列」 |
| R-5 | 用户级全局类型映射跨券商误配 | 低/中 | 类型文本 + 首次确认；设置页可改（P1）；批次快照可审计 |
| R-6 | 撤销删模板/类型造成增量用户困扰 | 中/低 | H-2 已批准：增量保留列模板、只删本批次写入类型；对话框分类型明示；审计 |
| R-7 | 映射保存同步解析大文件慢 | 中/低 | 20MB 上限；耗时日志；P1 异步化 |
| R-8 | 管理员规则误改影响新用户 | 中/中 | 规则只做建议；版本/审计；禁用而非删除；模板不受影响 |
| R-9 | 旧前端与新后端发布不同步 | 中/中 | 同批发布；legacy 24h；409 文案引导刷新 |
| R-10 | 改映射后重导同一文件，`dedupe_key`（含 amount/cash_balance）变化 → 重复行 | 中/中 | `mapping_fingerprint` 前移到 preview：同 `file_sha256` + 不同指纹 → warning `MAPPING_CHANGED_REIMPORT` + `requires_reimport_ack`；commit 必须 `replace_range` 或 `acknowledge_reimport=true`，否则 409 `IMPORT_REIMPORT_CONFLICT` |

---

## 12. 里程碑（评审后）

| 阶段 | 内容 | 退出条件 | 估时 |
|---|---|---|---|
| M1 契约与模型 | 字段目录、BillCategory、错误码、3 表 + 2 表扩展、迁移 0015、ids | `make migrate-check`、模型单测 | 1d |
| M2 解析器 | mapping/table/profiles；huatai 改造；legacy golden | 新旧 `ParsedBill` 相等；现有 parser 测试全绿 | 2d |
| M3 应用/API | preview 两阶段、commit/revoke、admin 规则、模板/类型仓储 | API-098~106 + 导入回归测试 | 2d |
| M4 前端 | Tailwind/shadcn 接入、向导 5 步、错误/确认/撤销 | 前端测试 + 手工验收 1~7 | 2d |
| M5 门禁/发布 | 契约/SDK/隔离/预算/文档同步、灰度 | 全部 gate 绿；手工验收 1~10 | 1d |

> 合计约 8 个开发日（不含评审与等待）。

---

## 13. HITL（待人工确认）

H-1（Tailwind/shadcn 例外）与 H-2（撤销删除范围）已于 2026-11-13 人工批准，见 §0.C；其余按推荐实现。

---

## 附录 A：华泰系统规则种子（示例）

| 表头（归一化） | target_field | is_fingerprint |
|---|---|---|
| 成交日期 / 交易日期 / 发生日期 / 清算日期 / 日期 | `trade_date` | ✅（成交日期） |
| 证券代码 / 股票代码 / 证券编码 | `security_code` | ✅（证券代码） |
| 证券名称 / 股票名称 / 证券简称 | `security_name` | ✅（证券名称） |
| 买卖标志 / 操作 / 交易类别 / 交易类型 / 买卖方向 / 买卖 | `side` | ✅（买卖标志） |
| 成交数量 / 发生数量 / 成交股数 / 发生股数 | `quantity` | ✅（成交数量） |
| 成交价格 / 发生价格 / 成交均价 | `price` | ✅（成交价格） |
| 成交金额 / 发生金额 / 发生额 | `amount`（`成交金额`=`amount_signed:false`；`发生金额/发生额`=`true`） | ✅（成交金额） |
| 佣金 | `commission` | ✅ |
| 印花税 / 交易印花税 | `stamp_tax` | ✅（印花税） |
| 其他费 / 过户费 / 其他费用 | `other_fee` | ✅（其他费） |
| 资金余额 / 余额 | `cash_balance` | ✅（资金余额） |
| 摘要 / 业务名称 / 业务类型 | `summary` | ✅（摘要） |
| 手续费 | `commission` | ❌ |
| 备注 | `note`（**唯一有意与 legacy 不同**：legacy 是 `summary`） | ❌ |
| 资金账号 / 股东代码 / 客户号 / 资金账户 / 股东账号 | `account` | ❌ |
| 币种（真实华泰 13 列之一） | 不映射，列入 ignored | ❌ |

通用 `broker='*'` 规则由 `stock_contracts/import_fields.py::FIELD_ALIASES` 全量生成（不含 fingerprint），保证未知券商也有基础建议；legacy `COLUMN_ALIASES` 不改。

**覆盖闸门**：

- 单测 `FIELD_ALIASES ⊇ COLUMN_ALIASES`（按字段比较别名集合），唯一允许的差异是 `备注`：legacy→`summary`，v2→`note`；不允许漏掉 `日期/成交均价/股东账号`。
- 验收 fixture 必须包含**真实 13 列华泰账单**（`日期/币种/股东账号/证券代码/证券名称/摘要/成交数量/成交均价/佣金/印花税/其他费/发生金额/资金余额`）：断言 12 个字段有建议、`币种` 进 ignored；尤其 `account` 必须映射，否则 `account_masked=NULL` 会改变 `dedupe_key`（重复导入产生新行）。
- 合成 12 列 fixture 继续用于 golden，但不能替代真实 13 列覆盖。

## 附录 B：一次华泰新账号导入的预期结果

- `broker=huatai`（自动识别，score=12）；`signature` 由 12 个列键生成。
- `columns[]` 12 行，全部 `source=system`、`confidence=0.95`，switch 关闭。
- `types[]` 形如：`证券买入`（buy）、`证券卖出`（sell）、`红利入账`（dividend）、`利息归本`（interest）、`银行转证券`（transfer）、`未知业务XYZ`（无建议）。
- 用户「全部接受」→ 保存 mapping → 类型步骤确认 6 类（`未知业务XYZ` 选 `other` 或废弃）→ ready → commit。
- 批次 `mapping_source=manual`、`template_id=imt_...`、`counts.discarded=0`；模板/类型落库。
- 第二次同文件：`stage=ready`，直接 commit；`inserted=0/skipped>0`。
- 撤销：origin 批次撤销 → 列模板删除、本批次写入类型删除，第三次导入重新走两步；增量批次撤销 → 列模板保留，只删本批次写入类型。

## 附录 C：验收清单（Definition of Done）

- [ ] 需求 1：switch 单项确认、L1/L1-trade 校验、未映射二次确认、`other_fee` 多列求和与专用错误。
- [ ] 需求 2：mapping 保存后才出现类型步骤；所有类型分类/废弃；建议可确认；废弃进隔离区。
- [ ] 需求 3：首次 commit 落模板；同签名增量跳过列步骤；新类型才再确认。
- [ ] 需求 4：origin 撤销删列模板；增量撤销保留列模板、只删本批次写入类型；前端分类型明示；审计。
- [ ] 技术要求 1：shadcn 源码组件 + `components.json`；仅 `/import` 域；不污染全局。
- [ ] 技术要求 2：华泰映射进系统 seed；新账号导入华泰只需人工确认；旧 fixture 零回归。
- [ ] 契约：API-Catalog/OpenAPI/SDK/operationId 同步；9 个新端点验收矩阵。
- [ ] 迁移：0015 可升可降；`migrate-check` 绿；legacy preview 兼容。
- [ ] 门禁：lint/typecheck/test-p2/test-isolation/api-check/sdk/frontend test/build/budget 全绿。
- [ ] 文档：`frontend/AGENTS.md` 记录 Tailwind 例外；本设计评审记录归档。

## 附录 D：新增券商/账单格式的接入清单（运营可执行）

目标：**不写代码**也能让一种新券商账单进入「系统建议 + 用户确认」的流程；只有专属语义（货基锚点/可转债手数等）才需要 profile 代码。

1. 收集 2~3 份脱敏样例（不同月份、含费用/转账/分红）。
2. 在管理员 API 新增 `broker` 的指纹规则：把该券商稳定出现的表头（如「成交日期/证券代码/买卖标志/成交数量/成交价格/成交金额/佣金/印花税/其他费/资金余额/摘要」）设为 `is_fingerprint=true`、`weight=1`；阈值按 §3.5。
3. 补充非指纹别名（`is_fingerprint=false`）：如「发生金额」→ `amount` + `options.amount_signed=true`；「过户费」→ `other_fee`；「备注」→ `note`。
4. 用新账号上传样例，走一遍「列对应 → 类型 → 导入」，确认 `signature` 与类型聚合符合预期。
5. 把这组样例放进 `fixtures/market/`，在 `test_api_010_import_mapping.py` 加一条「建议覆盖 + 计数」回归；若新增专属语义，再按 §7.1 增加 profile 并补 golden。
6. 记录到 `docs/architecture/v2/API-Catalog.md` §6 验收矩阵，标注 broker 与 fixture 名。

---

## 附录 E：实现与验收记录（2026-11-13）

### E.1 已实现范围

- 迁移 `0015_import_mapping_v2`：新增 `import_template/import_bill_type/import_column_rule`，扩展 preview/batch 两阶段草稿与提交快照；历史 preview 回填 `broker='huatai', stage='legacy'`。
- API-098~106、两阶段 preview、commit 快照、origin/增量撤销差异、管理员列规则与 `/import` 限定域 Tailwind + shadcn 向导均已实现；旧请求不带 `X-Import-Flow: v2` 时仍走 legacy。
- 新增 `bill_generic_2fee.csv`、`bill_generic_nosummary.csv`、`bill_huatai_newtype.xlsx`；175 行金标 fixture 的新旧链路已对账流水、导入计数、回合、净值与指标，结果一致。
- 三张新表已补 RLS 集成测试；`import_column_rule` 的全局行按已批准设计跨租户可见，写边界由 admin API 守卫，不在本次收紧数据库写策略。

### E.2 已执行的自动验收

| 命令 / 检查 | 实际结果 |
|---|---|
| `STOCK_DB_PORT=5433 make test-p2` | **1714 passed**（595 + 441 + 410 + 144 + 124；另有 4 + 2037 deselected）；core 覆盖率 100%；二期端点 50/50、受保护端点 65/65、契约快照无漂移 |
| `STOCK_DB_PORT=5433 make test-metrics` | **1121 passed**（595 + 364 + 70 + 92）；core 覆盖率 100%；25 份 golden 结构门禁通过 |
| `STOCK_DB_PORT=5433 make test-isolation` | **144 passed**，2037 deselected |
| `STOCK_DB_PORT=5433 uv run pytest backend/tests/api/test_import_mapping_v2.py backend/tests/unit/ledger/test_import_mapping.py backend/tests/api/test_imports_trades_jobs.py -q` | **148 passed**（99.10s） |
| 一次性临时库迁移 `0014→0015→0014→0015` | 通过；预置 legacy 行的回填值、schema version、列/表恢复断言均通过，临时库已删除 |
| `STOCK_DB_PORT=5433 uv run pytest backend/tests/integration/test_rls_isolation.py -q` | **12 passed** |
| `STOCK_DB_PORT=5433 make gate-rls` | **25/25 通过** |
| `make lint` | 通过（ruff check、ruff format check、前端 eslint + tsc） |
| `make typecheck` | 通过（mypy 218 个源码文件；import-linter 5/5 KEPT；前端 tsc） |
| `make api-check` | 通过：64 paths / 74 operations，105 个 API-ID 与验收行，62 个错误码一致 |
| `make gate-golden-structure` | 通过：25 份 golden 的结构、版本与 sha256 一致 |
| `cd frontend && pnpm -r test && pnpm -r build && pnpm budget` | **273 passed**（api-client 21 + web 252）；构建通过；首屏 JS **90.54KB gzip** / CSS **6.59KB gzip**，均在预算内 |

### E.3 与 v0.2 设计的已确认实现偏差

| 项目 | 实际行为 | 影响 |
|---|---|---|
| 摘要规则命中类型的首次确认 | `SYSTEM_RULES` 命中的已知类型会自动确认，未知/新类型仍需人工确认；因此不再严格执行 §4「系统摘要规则命中也需首次逐项 switch 确认」 | 保留既有摘要规则的自动分类体验；自动验收已覆盖该口径，不能宣称所有首次系统建议都需逐项确认 |
| API-099 ready 状态重放 | preview 尚未提交且 `If-Match` 匹配时，ready 状态下提交不同 resolutions 也会重算并返回 200；§6.4「不一致 → 409、须先修改 mapping」未实现 | 已提交批次仍返回 409；客户端不能在未提交 ready 状态依赖“不同类型写必被拒” |
| 区间替换界面 | 本期 `/import` 未提供 `replace_range` 的区间输入 UI | 需要区间替换时仍走既有 API/CLI 能力；不宣称本期新增 range UI |

### E.4 SKIP 与未执行项

- §9.2 手工验收 1~10 **全部 SKIP**：本轮未进行真实浏览器点击、固定账号手工上传或管理员手工操作；以上结果仅来自自动化测试、迁移/RLS 检查与构建门禁。
- 新向导读取到 `stage='legacy'` 的预览时提示重新上传；旧客户端不带 v2 头的 legacy preview→commit 兼容链路不变，不能把这理解为后端强制旧用户重传。
- 未执行生产发布、灰度、live 外部数据源或真实券商网站验证；未创建 git commit。
