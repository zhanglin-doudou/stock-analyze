> ⚠️ **本索引对应 v1（单机版），已过时。现行版本见 [v2/README.md](../v2/README.md)（多用户云服务 + 前后端分离 + API 优先验收，基线 [PRD-v2.md](../../prd/PRD-v2.md)）。**
> 本目录保留 v1 文档作为**历史基线**，不再更新。

# 架构文档包 v1 · 索引（历史）

> 需求基线：[PRD-v1.md](../../prd/PRD-v1.md)（v1.1）｜评审依据：[Review.md](../../prd/Review.md)
> 文档包版本：**1.0** ｜ 日期：2026-10-19 ｜ 总计 5 份、约 2500 行

---

## 1. 文档清单

| 文档 | 行数 | 职责 | 读者 |
|---|---|---|---|
| [Architecture.md](Architecture.md) | 1001 | **主文档**：驱动力、容器、分层与包结构、数据模型、流水线、指标引擎、API、性能、安全、24 FR + 12 NFR 追溯矩阵、演进路线、风险 | 所有人 |
| [Reliability.md](Reliability.md) | 420 | 高可用：SLO 重新定义、19 行 FMEA、SQLite 崩溃一致性、作业续跑、启动自愈、**降级矩阵**、熔断、备份 RTO/RPO、可观测性、SOP | 实现、运维、评审 |
| [Testing.md](Testing.md) | 419 | 高测试覆盖：分层覆盖率门禁、11 类套件、确定性工程、**28 条 fixture 边界矩阵**、9 条属性不变量、验收→测试映射、变异、架构约束、flake/豁免政策、PII 脱敏 | 实现、评审 |
| [Extensibility.md](Extensibility.md) | 420 | 高扩展：11 个扩展点、`MetricDef` 13 字段、失效判定算法、数据源能力协商、代码/配置/DB 边界、版本化矩阵、**反扩展清单**、三个"新增 X"作业单 | 实现、长期维护者 |
| [ADR.md](ADR.md) | 254 | 12 条架构决策记录（含理由、后果、被否方案） | 评审、新人、未来自己 |

---

## 2. 三条质量主轴 → 答案位置速查

| 你问的问题 | 答案位置 |
|---|---|
| 这个单机产品凭什么谈"高可用"？ | Architecture §2.1（`可用性 = P(启动)×P(不崩)×P(数字可信)`）→ Reliability §1 |
| 崩溃/断电/磁盘满会不会丢数据？ | Reliability §3（WAL + 事务参数）、§4（原子文件写）、§5（作业续跑） |
| 行情源挂了产品还能用吗？ | Architecture §12 + Reliability §7（8 依赖 × 9 功能域降级矩阵） |
| 备份和恢复能到什么程度？ | Reliability §10、Architecture §15.3（RPO=0 / 备份 RPO≤7d / RTO≤5min） |
| "高测试覆盖"具体覆盖到什么程度？ | Architecture §2.2（分层表）→ Testing §1.2（精确数字与门禁强度） |
| 覆盖率抓不到的 bug 怎么防？ | Testing §1.3 与 §4.4（golden + 独立 oracle + 变异 + 架构约束四道门） |
| 口径错了怎么保证能被发现？ | Testing §4.3（28 条边界矩阵）、§5（9 条不变量）、§6（验收→用例） |
| 加一个指标要改几个文件？ | Architecture §2.3 + Extensibility §2（EP-1）与 §11（作业单） |
| 加一家券商账单要改哪里？ | Extensibility §5 与 §11 |
| 换行情源/换数据库要动什么？ | Extensibility §4、§8（版本化矩阵）；Architecture §14.3（换 PG 触发条件） |
| 这个决策为什么这么做？ | ADR.md（12 条，含被否方案） |

---

## 3. 单一事实源（Single Source of Truth）

文档间出现数字重复时，**以右列为准**，修改时只改主位并通知其余引用：

| 内容 | 权威位置 |
|---|---|
| SLO 与可用性目标 | Reliability §1 |
| 分层覆盖率数字与门禁强度 | Testing §1.2 |
| 扩展点清单与 `MetricDef` 字段 | Extensibility §2、§3 |
| 数据表清单与索引 | Architecture §8.2、§8.3 |
| 端口契约 | Architecture §7 |
| 架构决策 | ADR.md |
| 指标口径公式（A1–A27） | [PRD-v1 附录 A](../../prd/PRD-v1.md) |
| 容差常量（`EPS_AMT/EPS_R/EPS_CUM/A`） | 代码 `packages/contracts/constants.py`；测试侧 `tests/support/tolerance.py` |

---

## 4. 关键数字速查

| 项 | 值 |
|---|---|
| 部署形态 | 单进程 + SQLite 单文件（WAL），`127.0.0.1:8787` |
| 分层 | `presentation → application → domain ← infrastructure`（六边形） |
| 包 | `contracts / core / ledger / review / delivery / platform` + `apps/server` |
| 端口 | 15 个（Repository × 6、MarketDataSource、BillParser、Clock、FileStore、BackupTarget、LlmProvider、ToolGateway、EventBus、JobRunner） |
| 数据表 | 31 张（ledger 9 / review 11 / sys 11，另有 Alembic 自带 `alembic_version`） |
| 架构决策 | 12 条 ADR |
| 扩展点 | 11 个（EP-1 ~ EP-11） |
| 降级矩阵 | 8 故障 × 9 功能域 |
| FMEA | 19 行 |
| 测试边界 fixture | 28 条 |
| 属性不变量 | 9 条 |
| 性能预算 | 首屏 ≤3s / 全量重算 ≤60s / 增量 ≤5s / 月报 ≤5s |
| 需求覆盖 | 24 FR + 12 NFR 全部有承载模块与测试类型（Architecture §18） |

---

## 5. 建议阅读顺序

| 角色 | 顺序 |
|---|---|
| 首次接触 | Architecture §0–§2（20 分钟）→ ADR 摘要表 → 本索引 §2 |
| 准备动手实现 | Architecture §5/§7/§8/§9 → Extensibility §11 作业单 → Testing §1/§4 |
| 架构评审 | Architecture §1 驱动力、§2 质量属性、§17 决策、§20 风险 → Reliability §1/§7 → Testing §1.2 |
| 长期维护/接手 | ADR 全文 → Extensibility §8 版本化、§10 反扩展 → Reliability §12 Runbook |

---

## 6. 文档维护规则

1. **决策不可悄悄改**：任何改变分层、持久化形态、外部依赖、指标口径方向的变更，必须新增 ADR（ADR-0001 起编号连续，不重用，不改写已采纳正文）。
2. **数字只改主位**：§3 表中的权威位置变更后，须同步检查其余文档的引用（用 `grep` 搜关键数字）。
3. **新增文档**：必须在本索引 §1 登记，并在相关专篇加交叉链接。
4. **与 PRD 的关系**：PRD 是"要什么"，本包是"怎么建"；若架构发现 PRD 需求不可实现，回到 PRD 走修订流程（如 PRD-v1 附录 B 的去重规则即由架构反馈修订）。
5. **版本对齐**：PRD 大版本升级时，Architecture 必须同步复核 §18 追溯矩阵。

---

## 7. 已知的架构未决项（沿用 PRD 开放问题）

| 编号 | 未决项 | 当前架构承载方式 |
|---|---|---|
| OQ-1 | 默认基准是否允许永久切换 | 支持切换 + `benchmark_id` 强制标注 + 趋势线断点（Architecture §10.3、Extensibility EP-5） |
| OQ-2 | 融资账户是否纳入本期 | 不纳入；`NAV = 资产 − 负债` 已在公式中预留（PRD 附录 A4） |
| OQ-3 | 月报是否原生 PDF | 采用 HTML + 浏览器打印，避免 PDF 引擎依赖（Architecture §12.3） |
| OQ-4 | 行动建议的合规边界 | 文案模板白名单 + 测试兜底（Architecture §15.2，ADR-0005） |
| OQ-5 | 打新收益是否单列 | 已作为独立资金占用流进净值；是否单列统计留 M2（PRD FR-L5.2） |

---

## 8. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-19 | 1.0 | 首版：主文档 + 高可用 + 测试 + 扩展 + ADR，覆盖 PRD-v1 全部 24 FR / 12 NFR |
