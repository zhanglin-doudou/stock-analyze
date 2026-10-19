> ⚠️ **历史基线（v1，单机版）**。现行架构见 [Architecture-v2.md](../../Architecture-v2.md)（多用户云服务 + PG + 前后端分离 + API 优先验收）。本文不再更新，仅作演进依据。

# 个人交易复盘体系 · 软件架构设计文档

> 版本：**v1.0** ｜ 日期：2026-10-19 ｜ 需求基线：[PRD-v1.md](../../prd/PRD-v1.md)（v1.1，24 条 FR + 12 条 NFR）
> 配套专篇：[Reliability.md](Reliability.md)（高可用）｜[Testing.md](Testing.md)（测试）｜[Extensibility.md](Extensibility.md)（扩展）｜[ADR.md](ADR.md)（决策记录）

---

## 0. 导读

### 0.1 本文解决什么问题

把 PRD-v1 的 24 条功能需求与 12 条非功能需求，转译成**可施工、可测试、可演进**的软件结构：包边界、端口契约、数据模型、处理流水线、指标执行引擎、API、部署形态，以及三条质量主轴（高可用 / 高测试覆盖 / 高扩展性）的具体落地机制。

### 0.2 三个必须先纠正的期望

| 用户期望 | 本产品的真实约束 | 架构结论 |
|---|---|---|
| 高可用 | 单用户、本地运行、无集群、无运维 | **"高可用"= 崩溃安全 + 数据不丢 + 依赖失效可降级 + 可恢复**，不是多副本容灾。见 §2.1 与 Reliability.md |
| 高测试覆盖 | 计算口径密集、错误代价高（误导投资） | **"高覆盖"= 分层差异化覆盖 + 不变量测试 + golden 复算**，不是全局 100%（PRD NFR-7 已明确覆盖率≠正确性）。见 §2.2 与 Testing.md |
| 高扩展性 | 单人开发周期、单人维护 | **"高扩展"= 有限但语义清晰的扩展点 + 明确的反扩展清单**，不是万能插件框架。见 §2.3 与 Extensibility.md |

**架构师的第一职责是拒绝不适用于场景的架构。** 本文明确不引入：微服务、服务网格、Kubernetes、消息中间件、Redis、外部数据库服务、SPA 前端工程链、多租户。理由见 [ADR-0001](ADR.md) 与 §附录 A。

### 0.3 阅读路径

- 想评审架构决策 → §1 驱动力、§17 决策摘要、ADR.md
- 想开始写代码 → §5 包结构、§7 端口、§8 数据模型、§9 流水线
- 想评审质量属性 → §2 重新定义、§14 性能、§16 可观测性 + 三篇专篇
- 想验收需求覆盖 → §18 追溯矩阵

---

## 1. 架构驱动力

### 1.1 需求归纳：从 36 条需求到 9 个驱动力

| 驱动力 | 来源需求 | 架构含义 |
|---|---|---|
| **D1 口径正确性** | FR-R2/R3/R4/R5/R6/R7/R8、附录 A（A1–A27） | 计算逻辑必须集中、纯函数、可复算、可版本化 |
| **D2 数字可追溯** | G2、NFR-4、FR-D3 | 每个产物带 `definition_version / inputs_hash / data_as_of / n`，能反查输入行 |
| **D3 外部数据不可靠** | NFR-2、NFR-3、NFR-9、PRD §9 | 端口化数据源 + 能力协商 + 校验层 + 缓存 + 降级 |
| **D4 零安装可运行** | FR-L1、§4.2/4.3 | 单进程 + 嵌入式存储 + 静态资产本地化 + 一键启动 |
| **D5 幂等与可重放** | FR-L3、NFR-5、FR-R1 | 行级 bag 去重 + 批次撤销 + 脏窗口重算 + 作业续跑 |
| **D6 用户能看懂** | FR-D1/D3、NFR-12 | 结构化产物与渲染层分离；"三段式"由指标元数据自动生成 |
| **D7 隐私不出机** | FR-D7.3、NFR-1 | 金额本地计算、LLM 只组织语言、出站守卫断言 |
| **D8 单人可维护** | NFR-8、NFR-11 | 分层 + 契约同源 + 架构约束测试 + 口径集中在注册表 |
| **D9 性能可接受** | NFR-10 | 增量计算 + 物化结果 + 索引设计 + 模块化热路径 |

### 1.2 质量属性场景（ASR，可测）

| 编号 | 场景 | 度量 | 主要架构回应 |
|---|---|---|---|
| ASR-1 | 用户导入账单时进程被 kill -9 | 重启后 DB 一致，无半写入批次；已提交数据 RPO=0 | 导入事务原子 + WAL + 批次表 + 启动自愈（§9.3） |
| ASR-2 | 行情源全天不可用 | 净值/回合/费用类仍可用；超额/归因标"不可用"并说明 | 端口隔离 + 降级矩阵 + 缓存陈旧度（§12、Reliability §7） |
| ASR-3 | 账单某区间资金余额断链 | 资产类指标阻断、回合级标"可疑"、页面红字可见 | 对账闸门作为计算前置状态（§10.4） |
| ASR-4 | 口径修订（如卖飞率改 z 分数） | 无需改核心；旧结果自动失效；新旧结果并存可对比 | `definition_version` + 依赖 DAG 失效传播（§10.3） |
| ASR-5 | 10 年 5 万行流水全量重算 | ≤60s；首页 ≤3s；增量重算 ≤5s | 物化 + 脏窗口 + 索引 + 批处理（§14） |
| ASR-6 | 新增一个指标 | 只改 1 个文件、不触碰 core 外代码、契约测试自动生效 | 指标注册表 + 框架代算 n/CI/状态（§10、Extensibility EP-1） |
| ASR-7 | 修改 FIFO 实现 | 变异测试捕获；core 覆盖率仍 100%；golden 复算 pass | core 零 I/O 纯函数 + 分层覆盖门禁（§2.2） |
| ASR-8 | LLM 出站内容 | 断言不含金额与账号；断网时金额类问题仍可答 | 本地模板填充 + 出站守卫 + Provider 端口（§13） |
| ASR-9 | 用户换新电脑 | 备份目录指向新机即可恢复，指标 diff=0 | 数据目录自包含 + `.backup` API + 校验（§15.3） |

### 1.3 硬约束

| 编号 | 约束 | 影响 |
|---|---|---|
| C1 | 单用户、单机、无运维 | 禁止任何需要独立进程/服务的基础设施 |
| C2 | 零数据库安装（PRD §4.3） | SQLite 为默认；PostgreSQL 仅可选 profile |
| C3 | 必须离线可用（本地资产） | 前端库全部 vendored，禁 CDN；报告可离线打开 |
| C4 | 金融数据 Python 生态（akshare） | 主语言 Python 3.12 |
| C5 | 金融口径可复现 | 禁用 float 做金额/收益；禁用 `datetime.now()` 直接调用 |
| C6 | 使用者非程序员 | 错误必须含"发生了什么/为什么/怎么办"；不得暴露栈 |
| C7 | 合规：不构成投资建议 | 建议文案模板化 + 有测试兜底 |
| C8 | 单人开发 | 复杂度预算有限；优先"少而深"而非"多而全" |

---

## 2. 质量属性的重新定义（本架构最重要的三个判断）

### 2.1 「高可用」在本产品中的可测定义

| 通俗目标 | 本项目的可测定义 | 主要机制 | 详述 |
|---|---|---|---|
| 别崩 | **crash-free session rate ≥ 99.5%**；**启动成功率 ≥ 99.5%**（M0 ≥99%） | 单一职责进程、无阻塞长事务、作业可续跑、启动自检 | Reliability §1 |
| 别丢数据 | **已提交导入 RPO = 0**；备份 RPO ≤ 7 天（可配置） | WAL + 导入事务 `synchronous=FULL`、批次原子性、软删永不物理删 | Reliability §3 |
| 崩了能救回来 | **RTO ≤ 5 分钟**（从备份还原到可查询）；崩溃后自动恢复 ≤ 5s | 原子文件写、启动自愈、`.backup` API、restore drill | Reliability §6/§10 |
| 依赖挂了还能用 | **降级可用性**：行情源故障时 P0 指标仍可用 | 端口隔离 + 能力协商 + 缓存陈旧度 + 降级矩阵 | §12、Reliability §7 |

**可用性的乘法定义**：`可用性 = P(启动成功) × P(会话不崩) × P(数字可信)`。三者相乘，99.5% × 99.5% × 「口径可信」≈ 99%。**这解释了为什么"界面先行"与"口径门禁"必须并行**（Review R-C1）：渲染出错误数字等于不可用，只修启动可用性上限仍是 0。

**明确不做**：多副本、主从、故障转移、跨机高可用、99.99% 月度可用性承诺（单机无意义）。理由见 [ADR-0001](ADR.md)。

### 2.2 「高测试覆盖」在本产品中的可测定义

| 层次 | 行 / 分支 | 门禁强度 | 理由 |
|---|---|---|---|
| `packages/core`（纯计算） | **100% / 100%**，变异分 ≥80% | **硬，CI 阻断，无豁免** | 口径错误的代价是误导投资决策 |
| `packages/contracts` | **97% / 95%** | 硬 | 契约漂移是跨层故障之源；下限见 ADR-0010 |
| `ledger.domain` / `review.domain` | **98% / 96%** | 硬 | 不变量所在层 |
| `ledger.app` / `review.app` | **95% / 90%** | 硬 | 失败路径（撤销/降级/阻断/锁冲突）是用户最常撞到的 |
| `*.infra` | **85% / 80%**（新增行 ≥90%） | 软告警 + 新增行阻断 | 以契约测试 + 回放为主 |
| `packages/platform` | **90% / 85%** | 硬 | 续跑/备份出错会静默毁数据，横切正确性 |
| `packages/delivery` | **80% / 70%** | 软告警 | 以快照 + 端到端为主 |
| `apps/server` | 70% / 60% | 软，用 e2e smoke 替代 | 组装根以接线为主 |
| **整体** | ≥90% / ≥85% | **告警**（不阻断） | 防"整体数字好看、新代码裸奔" |
| **新增/变更行（diff）** | **≥95%** | 硬 | 真正的防劣化门禁 |

**关键认知：覆盖率是回归信号，不是正确性信号。** 因此并列四道"覆盖率抓不到"的门：

| 门 | 载体 | 为什么不可省 |
|---|---|---|
| Golden 对拍 | `fixtures/expected/` + syrupy，期望值来自 **`tests/oracle/` 独立参考实现 + 人工手算表** 三方比对 | 覆盖率证明"跑过"，golden 证明"算对"；同源实现自证等于没验 |
| 边界矩阵 | `fixtures/bills/` 28 条（加仓/分批/同日多笔/分红/送转/配股/超卖/打新/停牌/退市/除权/区间替换/隔离区/首行非买入…） | 覆盖率凑不出"分红跨除权日"这类组合 |
| 变异测试 | mutmut on `core`，分数 ≥80% | 杀死"分支跑到但符号写反" |
| 架构约束 | import-linter + AST 扫描（禁 I/O、禁 wall-clock、禁 float） | 防止"口径对了但走错层"绕过闸门 |

**容差与常量的唯一来源**：`packages/contracts/constants.py`（`EPS_AMT / EPS_R / EPS_CUM / A / MIN_N_*`）与 `tests/support/tolerance.py`（`assert_amt / assert_ret / assert_cum`）。生产与测试同源 import——否则会出现"测试用 0.01、实现用 0.001"的双标。详见 [Testing.md](Testing.md)。

### 2.3 「高扩展性」在本产品中的可测定义

| 变更类型 | 允许触碰 | 禁止触碰 | 预算 |
|---|---|---|---|
| 新增指标 | `review/metrics/defs/*.py` 1 个文件 | core 引擎、API 层、模板 | ≤1 文件 / ≤0.5 人日 |
| 新增券商账单 | `ledger/parsers/*.py` + 注册 1 行 | 导入主流程 | ≤1 文件 / ≤1 人日 |
| 新增数据源 | 1 个 Protocol 实现 | 计算层 | ≤1 文件 / ≤2 人日 |
| 新增报告区块 | 1 个模板 macro + 1 个 `ReportBlock` | 渲染框架 | ≤2 文件 / ≤0.5 人日 |
| 口径修订 | metric def 的 `definition_version` +1 | 数据结构 | ≤1 文件 / 无迁移 |

**明确不做**：第三方动态加载 Python 包、通用表达式 DSL、多租户、前端插件体系。理由见 [Extensibility.md](Extensibility.md) 反扩展清单。

---

## 3. 系统上下文（C4 L1）

```
                    ┌──────────────────────────────┐
   券商导出文件 ────▶│                              │
   (xls/xlsx/csv)   │      个人交易复盘体系          │───▶ 本地页面 127.0.0.1:8787
                    │   (单进程 · 单机 · SQLite)     │───▶ 月度报告 HTML/PDF
   用户 ───────────▶│                              │───▶ 导出 Excel/JSON
                    └───────┬──────────┬───────────┘
                            │          │
                 ┌──────────▼──┐  ┌────▼──────────────┐
                 │ akshare 行情 │  │ LLM（可选，远程）   │
                 │ + AlphaFeed  │  │ GLM-4.5-Air 等     │
                 │ （可选灾备）  │  │ 仅收脱敏上下文      │
                 └─────────────┘  └───────────────────┘
                            │
                 ┌──────────▼──────────┐
                 │ 本地文件系统          │
                 │ 数据目录 / 备份盘     │
                 └─────────────────────┘
```

| 外部系统 | 方向 | 协议/形态 | 失败影响 | 应对 |
|---|---|---|---|---|
| 用户 | 双向 | HTTP (localhost) | — | 无鉴权，绑定 127.0.0.1 |
| 券商账单文件 | 入 | 文件上传 | 无数据 | 向导 + 自检 + 隔离区 |
| akshare | 出 | HTTP（库封装） | 行情类指标不可用 | 能力协商 + 缓存 + 降级 |
| AlphaFeed | 出（可选） | HTTP | 无（灾备） | 仅在主源失败时启用 |
| 远程 LLM | 出（可选） | HTTPS | 对话不可用，其余不受影响 | Provider 端口 + 出站守卫 |
| 本地文件系统 | 双向 | POSIX | 数据丢失 | 原子写 + 备份 + 校验 |

**关键性质**：所有出站依赖都是**可选的**。除"导入账单文件"外，没有任何功能在依赖失效时完全不可用（见 Reliability 降级矩阵）。

---

## 4. 容器视图（C4 L2）：为什么只有一个进程

```
┌─────────────────────────────────────────────────────────────────────┐
│ apps/server  (单进程, 127.0.0.1:8787)                                 │
│                                                                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │ presentation│  │ application  │  │ domain 引擎                   │  │
│  │ SSR + JSON  │─▶│ 用例编排      │─▶│ core: FIFO/NAV/metrics/...   │  │
│  │ (FastAPI)   │  │ (services)   │  │ (纯函数, 零 I/O)              │  │
│  └────────────┘  └──────┬───────┘  └──────────────────────────────┘  │
│                          │                                            │
│  ┌────────────┐  ┌───────▼──────┐  ┌──────────────┐                   │
│  │ 调度器      │  │ 作业执行器    │  │ 事件分发       │                  │
│  │ APScheduler│─▶│ JobRunner    │  │ Outbox+Bus    │                  │
│  └────────────┘  └──────┬───────┘  └──────────────┘                   │
│                          │                                            │
│  ┌───────────────────────▼──────────────────────────────────────┐    │
│  │ infrastructure: Repository(SQLAlchemy) · MarketDataAdapter    │    │
│  │ · BillParser · LlmProvider · FileStore · BackupTarget          │    │
│  └───────────────────────┬──────────────────────────────────────┘    │
└──────────────────────────┼───────────────────────────────────────────┘
                           │
        ┌──────────────────▼───────────────────┐
        │ 数据目录 ~/Documents/交易复盘/         │
        │  review.db (WAL) · backups/ · cache/  │
        │  archive/ · reports/ · logs/          │
        └──────────────────────────────────────┘
```

| 若拆成多容器 | 收益 | 代价 | 结论 |
|---|---|---|---|
| Web / Worker / DB 三个服务 | 隔离故障 | 用户需装 PG、开三个进程、管端口 | ❌ 违反 C1/C2，用户装不起来 |
| Web + 独立 Worker | 长任务不阻塞请求 | 需进程间调度与共享状态 | ❌ 用"单进程 + 线程池 + 可续跑作业"即可，长任务走 `202 + SSE` |

**结论**：单进程不是偷懒，而是唯一同时满足"零安装 + 崩溃安全 + 简单部署"的选择。为此付出的工程代价是"必须自己做作业续跑、单写者串行化、内存与 CPU 预算"——这三项已在 §9 与 Reliability 中正面处理。

---

## 5. 分层与包结构（C4 L3）

### 5.1 分层规则（铁律）

```
presentation ──▶ application ──▶ domain ◀── infrastructure
     │                 │            ▲              │
     └────── contracts ─┴────────────┴──────────────┘
                    platform（横切：db/jobs/events/config/obs/backup）
```

| 规则 | 内容 | 强制手段 |
|---|---|---|
| L1 | `domain` 零 I/O、零全局状态、零 `datetime.now()`、零随机 | import-linter + AST 检查测试 |
| L2 | `application` 只依赖 `domain` + 端口 + `contracts` | import-linter layers |
| L3 | `infrastructure` 实现端口，不得被 `application` 直接 import | import-linter independence |
| L4 | `presentation` 不得直接访问 ORM / SQL | import-linter + 测试 |
| L5 | 跨限界上下文只经 `contracts` 或事件，不共享 ORM 实体 | import-linter independence |
| L6 | `core` 不得 import 任何业务包 | import-linter |

### 5.2 目录结构

```
stock-analyze/
├── pyproject.toml                 # uv workspace root
├── uv.lock
├── Makefile                       # dev 快捷入口（不含任何用户侧依赖）
├── apps/
│   └── server/
│       ├── src/app/
│       │   ├── main.py            # FastAPI 组装根
│       │   ├── cli.py             # review serve|import|recompute|doctor|backup
│       │   ├── wiring.py          # 依赖注入容器（手写，不引框架）
│       │   └── launcher.py        # 双击入口：探端口→迁移→自愈→开浏览器
│       └── pyproject.toml
├── packages/
│   ├── contracts/
│   │   └── src/contracts/{constants.py,dto,enums,errors,metric.py,report.py}   # constants.py = 容差/年化基数/最小样本唯一来源
│   ├── core/                      # ★ 纯领域计算内核（无业务上下文）
│   │   └── src/core/
│   │       ├── values/{money.py,ratio.py,period.py,qty.py}
│   │       ├── fifo/{engine.py,allocation.py}
│   │       ├── nav/{unit_nav.py,cashflow.py,mwr.py}
│   │       ├── metrics/{registry.py,defs/,context.py,pipeline.py}
│   │       ├── attribution/{five_factor.py,brinson.py}
│   │       ├── stats/{ci.py,bootstrap.py,sample_policy.py}
│   │       └── errors.py
│   ├── ledger/
│   │   └── src/ledger/{domain,application,infrastructure,api}
│   │       ├── domain/{trade.py,batch.py,summary.py,reconciliation.py}
│   │       ├── application/{import_bill.py,revoke_batch.py,repair_range.py,reconcile.py}
│   │       ├── infrastructure/{orm/,repositories/,parsers/}
│   │       └── api/routes.py
│   ├── review/
│   │   └── src/review/{domain,application,infrastructure,api}
│   │       ├── domain/{round.py,nav.py,metric_result.py,attribution.py}
│   │       ├── application/{build_rounds.py,build_nav.py,compute_metrics.py,compute_attribution.py}
│   │       ├── infrastructure/{orm/,repositories/,marketdata/}
│   │       └── api/routes.py
│   ├── delivery/
│   │   └── src/delivery/
│   │       ├── web/{pages,routes,partials,static/vendor/}
│   │       ├── reports/{blocks/,renderer.py,spec.py}
│   │       └── chat/{session.py,tools.py,guard.py}
│   └── platform/
│       └── src/platform/
│           ├── db/{engine.py,migrations/,unit_of_work.py,single_writer.py}
│           ├── jobs/{runner.py,state.py,checkpoint.py,locks.py}
│           ├── events/{outbox.py,bus.py,handlers.py}
│           ├── config/{settings.py,schema.py}
│           ├── obs/{logging.py,health.py,doctor.py,trace.py}
│           └── backup/{snapshot.py,restore.py,verify.py}
├── fixtures/                      # golden fixtures（见 Testing.md，含 MANIFEST）
│   ├── bills/  market/  expected/
├── tests/                         # 跨包集成与端到端
│   ├── oracle/                    # ★ 独立朴素参考实现：naive_fifo / naive_nav / naive_xirr（生产代码禁止 import）
│   ├── support/                   # tolerance.py（唯一容差入口）、builders、fixture loader
│   ├── integration/  e2e/  contract/  architecture/
└── docs/
```

### 5.3 依赖方向（禁止反向箭头）

```
apps/server ─▶ delivery ─▶ review ─▶ ledger ─▶ contracts
                 │            │         │         ▲
                 └────────────┴─────────┴─────────┘
                              │
                            core  （只被 ledger/review 依赖，不反向依赖任何人）
                              ▲
                          platform（被所有层依赖，但不依赖业务语义）
```

---

## 6. 限界上下文职责

### 6.1 Ledger（数据底座）

| 项 | 内容 |
|---|---|
| 职责 | 账单解析、去重、分类、软删、对账、批次撤销、导出 |
| 聚合根 | `ImportBatch`（批次）、`Trade`（流水行） |
| 不变量 | ① 同 `(dedupe_key, occurrence_idx)` 唯一；② 软删行不参与下游；③ 批次撤销只影响本批次；④ 股东账号原文本地加密可还原（非哈希） |
| 对外端口 | `TradeRepository`、`BillParser`、`FileStore` |
| 对外事件 | `BatchImported`、`TradesChanged`、`ReconciliationFailed` |
| 关键用例 | `ImportBill`、`RevokeBatch`、`RepairRange`、`Reconcile`、`PatchTrade`、`ExportAll` |

### 6.2 Review（度量层）

| 项 | 内容 |
|---|---|
| 职责 | 期初资产、FIFO 回合、净值（TWR/MWR）、三域指标、归因、统计可信度 |
| 聚合根 | `Round`、`NavSeries`、`MetricResult` |
| 不变量 | ① 桥接恒等式（Σ回合pnl+浮盈亏+股息+利息−费用 = 期末−期初−净入金）；② 回合买卖 trade_id 一经确定不可变；③ `MetricResult.inputs_hash` 变化即结果失效；④ `blocked` 状态不产出结论性文案 |
| 对外端口 | `RoundRepository`、`NavRepository`、`MetricRepository`、`MarketDataSource` |
| 对外事件 | `RoundsRebuilt`、`MetricsComputed`、`AnomalyDetected` |
| 关键用例 | `BuildRounds`、`BuildNav`、`ComputeMetrics`、`ComputeAttribution`、`GetMetricExplain` |

### 6.3 Delivery（交付层）

| 项 | 内容 |
|---|---|
| 职责 | SSR 页面、报告渲染、对话、人话层、下钻 |
| 关键约束 | 只读取结构化产物；**不得包含任何业务计算逻辑**（渲染层零口径） |
| 对外端口 | `ReportRepository`、`JournalRepository`、`LlmProvider`、`ToolGateway` |
| 关键用例 | `RenderDashboard`、`GenerateReport`、`AskQuestion`、`Drilldown` |

### 6.4 Platform（横切）

| 子系统 | 职责 | 关键设计 |
|---|---|---|
| `db` | 引擎、迁移、单写者、UoW | SQLite WAL / PG 可切；单写者队列避免 `SQLITE_BUSY` |
| `jobs` | 作业状态机、续跑、单飞锁 | `sys_job_run` + `checkpoint_json` |
| `events` | outbox + 进程内分发 | at-least-once，消费者幂等 |
| `config` | 设置 + schema 校验 + 版本 | 配置即数据，带版本与迁移 |
| `obs` | 日志、健康、doctor、trace | `request_id` 贯穿 |
| `backup` | 快照、还原、校验 | `Connection.backup()` + checksum + 指标 diff |

### 6.5 Core（计算内核）

`core` 不属于任何业务上下文，是被 `ledger`/`review` 复用的**纯计算库**：`values`（Money/Ratio/Qty 标度整数）、`fifo`、`nav`、`metrics`（注册表 + 执行管线）、`attribution`、`stats`。

> **它是"高测试覆盖"的落点**：无 I/O、无时钟、无随机、输入输出全是值对象 → 可 100% 覆盖、可属性测试、可变异测试。

---

## 7. 端口与适配器清单

| 端口 | 定义位置 | 生产实现 | 测试替身 | 用于需求 |
|---|---|---|---|---|
| `TradeRepository` | `ledger/domain/ports.py` | SQLAlchemy | `InMemoryTradeRepo` | FR-L3/L4/L8 |
| `BillParser` | `ledger/domain/ports.py` | 华泰解析器（注册表） | `FixtureParser` | FR-L3 |
| `FileStore` | `platform/ports.py` | 本地目录 | 临时目录 | FR-L3/L7 |
| `RoundRepository` | `review/domain/ports.py` | SQLAlchemy | InMemory | FR-R2 |
| `NavRepository` | `review/domain/ports.py` | SQLAlchemy | InMemory | FR-R3 |
| `MetricRepository` | `review/domain/ports.py` | SQLAlchemy | InMemory | FR-R5~R8 |
| `ReportRepository` | `delivery/domain/ports.py` | SQLAlchemy | InMemory | FR-D2 |
| `JournalRepository` | `delivery/domain/ports.py` | SQLAlchemy | InMemory | FR-D4 |
| `MarketDataSource` | `review/domain/ports.py` | akshare / AlphaFeed 适配器 | `RecordedSource`（回放） | FR-R4/R5、NFR-9 |
| `Clock` | `platform/ports.py` | SystemClock | `FrozenClock` | 全局（确定性） |
| `BackupTarget` | `platform/ports.py` | 本地/外置盘 | 临时目录 | FR-L7 |
| `LlmProvider` | `delivery/domain/ports.py` | GLM / 可选本地 | `EchoProvider` | FR-D7 |
| `ToolGateway` | `delivery/domain/ports.py` | 指标查询工具白名单 | 录制工具 | FR-D7 |
| `EventBus` | `platform/ports.py` | outbox + 进程内 | 同步收集器 | 全局 |
| `JobRunner` | `platform/ports.py` | 线程池 + DB 状态 | 同步执行器 | 全局 |

**原则**：端口由**消费方**（domain/application）定义，由 infrastructure 实现——依赖倒置，保证计算层可脱离数据库测试。

---

## 8. 数据架构

### 8.1 存储选型与逻辑分库

| 项 | 决策 | 理由 |
|---|---|---|
| 默认引擎 | SQLite（WAL） | 零安装、单文件、事务完整、够用（5 万行量级） |
| 可选引擎 | PostgreSQL（profile: pg） | 未来云端/多用户；同一套 SQLAlchemy 元数据 |
| 逻辑分库 | 默认单文件 + 表前缀 `ledger_` / `review_` / `sys_`；PG 下映射为双 schema | 满足 PRD 的分库语义而不引入双连接 |
| 数值表示 | **标度整数**（金额×100 分、价格×1e6、数量×1e4、比率×1e8） | 精确十进制、跨库可移植、避免 float 与 DECIMAL 方言差异 |
| 时间 | UTC 存储 + 业务日按 Asia/Shanghai 计算 | 避免跨时区与夏令时歧义 |
| 软删 | `deleted_at`（NULL=有效）+ 部分唯一索引 | 满足"删除历史永不物理清除" |

### 8.2 表清单

**Ledger 域**

| 表 | 主键/唯一 | 关键列 | 说明 |
|---|---|---|---|
| `ledger_user` | id | name, created_at | seed 单行 `root/100000` |
| `ledger_import_batch` | id | filename_hash, file_sha256, parser_id, mode, total/inserted/skipped/quarantined, mapping_json, created_at, revoked_at | 支撑 FR-L3 批次与撤销 |
| `ledger_trade` | id；唯一 `(user_id, dedupe_key, occurrence_idx)` | batch_id, seq_in_file, trade_date, security_code, summary_raw, summary_type, side, qty_e4, price_e6, commission_c, stamp_tax_c, other_fee_c, amount_c, cash_balance_c, account_ref_enc, deleted_at | 唯一事实源 |
| `ledger_trade_history` | id | trade_id, before_json, after_json, actor, at | PATCH 审计 |
| `ledger_quarantine_row` | id | batch_id, row_no, raw_json, reason_code | 隔离区，绝不静默丢弃 |
| `ledger_summary_rule` | id | pattern, match_type, summary_type, side, priority, enabled, version | 配置化分类 |
| `ledger_reconciliation` | id | period_start, period_end, status, break_count, first_break_trade_id, expected_c, actual_c | 余额链结果 |
| `ledger_provenance` | id | source_id, dataset, symbol, fetched_at, as_of, row_count, checksum, quality_flags | NFR-2 数据留痕 |
| `ledger_export_log` | id | kind, path, checksum, at | FR-L7 |

**Review 域**

| 表 | 主键/唯一 | 关键列 | 说明 |
|---|---|---|---|
| `review_round` | id | security_code, account_ref_enc, open_date, close_date, qty_e4, cost_c, proceeds_c, dividend_net_c, pnl_c, return_e8, hold_days, definition_version, deleted_at | 核心实体 |
| `review_round_fill` | id | round_id, buy_trade_id, sell_trade_id, qty_e4, cost_c, proceeds_c | FIFO 配对明细（可追溯） |
| `review_nav_daily` | `(user_id, date)` | market_value_c, cash_c, total_assets_c, unit_nav_e8, shares_e8, cashflow_c, return_daily_e8, stale_flags | 净值序列 |
| `review_cashflow` | id | date, amount_c, kind, source_trade_id | 外部现金流 |
| `review_metric_result` | id；唯一 `(metric_id, definition_version, scope_type, scope_key, period_start, period_end)` | value_json, unit, n, ci_low_e8, ci_high_e8, sample_status, status, assumptions_json, data_as_of, inputs_hash, computed_at | 物化 + 血缘 |
| `review_metric_dependency` | `(metric_id, depends_on)` | — | DAG 审计 |
| `review_attribution` | id | period, model, factor, value_c, ratio_e8, rank | 五因子 / Brinson |
| `review_report` | id | period, report_schema_version, status, html_path, pdf_path, checksum, data_as_of, generated_at | 月报 |
| `review_journal_entry` | id | round_id, phase, thesis_tags, thesis_text, plan_json, emotion_score, review_conclusion, realized | FR-D4 |
| `review_behavior_rule` | id | code, enabled, threshold_json, version | FR-D6 |
| `review_alert` | id | rule_code, triggered_at, payload_json, acknowledged, channel, sent_at | 提醒 |

**Platform/运维域**

| 表 | 用途 |
|---|---|
| `sys_job_run` | 作业状态机 + checkpoint（可续跑） |
| `sys_job_lock` | 单飞锁（`(kind, scope_key)` 行锁 + 过期时间） |
| `sys_outbox_event` | 事件 at-least-once 投递 |
| `sys_config` | 配置（含 `config_release` 版本） |
| `sys_llm_call` | 对话调用留痕（含是否脱敏） |
| `sys_audit_log` | 人工改单、撤销、修复的操作审计 |
| `sys_backup_log` | 备份与校验结果（含 5 项校验明细） |
| `sys_breaker` | 数据源熔断状态 `(scope, state, opened_at, failures)`，重启后不立刻重试已熔断源 |
| `sys_metric_counter` | 进程内可观测指标定期快照（导入行数/隔离率/重算耗时/降级次数/缓存命中） |
| `sys_startup_log` | 启动记录与脏标记（判定崩溃会话、计算启动成功率 SLO-1） |
| `sys_schema_version` | 应用级契约版本快照（与 `alembic_version` 配合，迁移前一致性校验） |
| `alembic_version` | 数据库 schema 版本（Alembic 自带） |

### 8.3 关键索引

| 索引 | 目的 |
|---|---|
| `idx_trade_date (user_id, trade_date, id)` | 区间扫描、净值构建 |
| `idx_trade_code (user_id, security_code, trade_date)` | 单标的重放、回合配对 |
| `uidx_trade_dedupe (user_id, dedupe_key, occurrence_idx)` | 幂等导入 |
| `idx_quarantine_batch (batch_id)` | 导入结果页 |
| `idx_metric_lookup (metric_id, scope_type, scope_key, period_start, period_end)` | 指标读取 |
| `idx_metric_stale (status, definition_version)` | 失效扫描 |
| `idx_nav_date (date)` | 时序查询 |
| `idx_job_status (status, heartbeat_at)` | 崩溃恢复扫描 / 续跑 |

### 8.4 幂等与去重（对应 PRD 附录 B 与 R-B8 修复）

```
dedupe_key = hash(user_id ‖ normalize(account_ref) ‖ trade_date ‖ security_code
                  ‖ summary_raw ‖ qty ‖ price ‖ amount)     # 不含资金余额
occurrence_idx = 该 dedupe_key 在“同一批次内”的出现序号（1,2,3...）
唯一约束：(user_id, dedupe_key, occurrence_idx)
```

- **bag 去重**：同键多笔按出现次数比对，只插入差额（解决"同日同价同量多笔"误吞并）。
- 空股东账号归一为哨兵 `UNKNOWN`（解决 v1.0 的空值哈希碰撞缺陷）。
- 不含 `资金余额`：交割单无该列时仍可去重。

### 8.5 数据量估算

| 数据 | 10 年估算 | 增长 |
|---|---|---|
| `ledger_trade` | ≤ 5 万行 | ~5000 行/年 |
| `review_round` | ≤ 1 万 | ~1000/年 |
| `review_nav_daily` | ≤ 2500 行 | 244/年 |
| `review_metric_result` | 8~12 核心 × 12 月 × 3 年 + 派生 ≈ 1~3 万 | 有界 |
| DB 总大小 | < 300 MB | 有界 |

> 规模判断：**SQLite 在此量级有 1~2 个数量级余量**。切换到 PostgreSQL 的触发条件见 §14.3。

---

## 9. 核心处理流水线

### 9.1 导入流水线（Ledger）

```
文件 ─▶ [S0 探测签名] ─▶ [S1 解析+列映射] ─▶ [S2 规范化/校验] ─▶ [S3 计算 dedupe_key]
     ─▶ [S4 bag 去重] ─▶ [S5 摘要分类] ─▶ [S6 隔离区归档] ─▶ [S7 事务落库]
     ─▶ [S8 余额链对账] ─▶ [S9 发 BatchImported] ─▶ [S10 触发下游重算]
```

| 阶段 | 纯函数？ | 幂等 | 失败策略 |
|---|---|---|---|
| S0~S2 | 是 | 是 | 整批失败，返回"哪列/哪行/怎么办"（NFR-12） |
| S3~S5 | 是 | 是 | 单行失败 → 进隔离区，不阻塞整批 |
| S7 | 否 | 是（唯一约束兜底） | 事务回滚，批次标记 FAILED |
| S8 | 是（输入=落库行） | 是 | 不阻断入库；写 `ledger_reconciliation` |
| S10 | 否 | 是（作业幂等） | 可重跑；失败不影响导入结果 |

**关键设计**：S1–S6 在**内存中对纯数据结构**执行，S7 一次性事务提交 → 实现"批次原子性"（ASR-1）。解析器的输出是 `ParsedBillRow[]`（不接触 DB），因此可用 fixture 做纯单元测试。

### 9.2 计算流水线（Review）

```
BatchImported / TradesChanged / 定时
        │
        ▼
[P1 确定脏窗口] ──▶ [P2 重放持仓] ──▶ [P3 FIFO 回合] ──▶ [P4 日终资产+现金]
        │                                                        │
        │                                                        ▼
        │                                                  [P5 净值 TWR/MWR]
        ▼                                                        │
[P6 指标 DAG 拓扑执行] ◀──────────────────────────────────────────┘
        │
        ├─▶ [P7 归因（五因子/Brinson）]
        ├─▶ [P8 统计可信度闸门（n/CI/门槛）]
        └─▶ [P9 物化 MetricResult] ──▶ [P10 发 MetricsComputed] ──▶ 页面/报告缓存失效
```

**脏窗口算法（脏窗口是"增量 + 正确"的交点）：**

```
def dirty_window(changed_dates, triggers) -> Window:
    # 1) 交易变动：从最早变动日开始（FIFO 会向后传导）
    start = min(changed_dates)
    # 2) 行情修正/复权因子变更：从最早受影响日
    start = min(start, earliest_affected_market_date)
    # 3) 指标 definition_version 变更：全量（该指标及其下游）
    if metric_version_changed: return FULL
    # 4) 数据源 as_of 前移（新财报/新行情）：从 as_of 生效日开始
    start = min(start, as_of_effective_date)
    # 5) 保守回退：start 前推 max(20 交易日, 最长持仓期) 以覆盖"卖飞率窗口/pending"
    return Window(start = start - safety_lookback, end = TODAY)
```

`safety_lookback` 是**正确性换性能的保险丝**：宁可多算，不可漏算。它必须在配置中显式声明并写入 `MetricResult.assumptions`。

### 9.3 作业状态机与可续跑

```
        ┌─────────┐  claim   ┌─────────┐  progress  ┌───────────┐
        │ PENDING │────────▶ │ RUNNING │──────────▶ │ SUCCEEDED │
        └─────────┘          └────┬────┘            └───────────┘
                                  │ 抛错
                                  ▼
                             ┌─────────┐  retryable   ┌──────────┐
                             │ FAILED  │─────────────▶ │ PENDING  │
                             └─────────┘               └──────────┘
                                  │ 进程崩溃（心跳超时）
                                  ▼
                         启动恢复扫描：RUNNING 且心跳超时
                                  │
                      ┌───────────┴────────────┐
                      ▼                        ▼
              checkpoint 可续          checkpoint 不可续
                      │                        │
                      ▼                        ▼
                  → PENDING              → FAILED（标记中断）
```

| 机制 | 实现 |
|---|---|
| 续跑 | `checkpoint_json` 记录"已完成的窗口/已处理的 last_id" |
| 幂等 | 每个 stage 以 `(window, definition_version)` 为写键，重跑覆盖同键结果 |
| 单飞 | `sys_job_lock(kind, scope_key)` 行锁 + 过期时间；双击启动只允许一个实例（文件锁） |
| 心跳 | `heartbeat_at` 每 5s 更新；超 60s 判定崩溃 |

调度触发矩阵见 §9.5。

### 9.4 触发矩阵

| 事件 | 触发作业 | 去抖 | 优先级 |
|---|---|---|---|
| `BatchImported` | `BuildRounds` → `BuildNav` → `ComputeMetrics` | 合并 2s 内多次 | 高 |
| `TradesChanged`（PATCH/软删/撤销） | 同上（脏窗口从最早变动日） | 2s | 高 |
| `ReconciliationFailed` | 标记区间 `blocked`；重算资产类指标为 `blocked` | 立即 | 高 |
| 每日定时（本地时间 07:00） | `RefreshMarketData` → `BuildNav` → `ComputeMetrics` | — | 中 |
| `definition_version` 变更（启动时检测） | 全量重算受影响指标 | 启动后 10s | 中 |
| `ReportRequested` | `GenerateReport` | — | 中 |
| 每周定时 | `Backup` + `VerifyBackup` | — | 低 |
| `AnomalyDetected` | 评估行为规则 → 生成 `review_alert` | — | 低 |

---

## 10. 指标执行引擎（架构核心）

### 10.1 为什么需要"引擎"而不是一堆函数

因为 FR-R8（统计可信度）、FR-D3（人话三段式）、G2（可追溯）、NFR-11（口径版本化）这四条需求是**横切的**：如果每个指标自己算 CI、自己拼文案、自己处理 `blocked`，必然出现"有的指标带 n、有的不带"的不一致——这正是 v1.0 评审中被点名的形态。

**设计**：指标声明式注册（`MetricDef`），框架统一代算 `n / CI / assumptions / inputs_hash / status`。

### 10.2 契约（架构视角；`MetricDef` 完整 13 字段见 [Extensibility EP-1](Extensibility.md)）

**关键约束**：指标的 `compute()` 只返回 `MetricValue`，**拿不到也无法设置 `status`**；`n / ci / assumptions / inputs_hash / sample_status / status` 全部由框架计算。这是 FR-R8 与 FR-L6 在结构上不可被绕过的前提（见 ADR-0004/ADR-0011）。

```python
# core/metrics/registry.py（概念签名）
MetricDef = {
    "id": "win_rate",                    # 稳定标识
    "definition_version": "3.0",         # MAJOR.MINOR：MAJOR=口径方向变化(不可比) / MINOR=精度·窗口·文案
    "domain": "perf",                    # selection|timing|behavior|attribution|perf
    "scope": Scope.PORTFOLIO,            # PORTFOLIO|ROUND|SECURITY|PERIOD
    "params_schema": WinRateParams,      # 参数 schema
    "dependencies": ["portfolio_rounds", "nav_series"],
    "compute": compute_win_rate,         # (MetricContext) -> raw value
    "sample_policy": "ratio_n30",         # 框架据此决定 n<10 不出结论
    "status_policy": "blocked_if_nav_unreliable",
    "unit": "ratio",
    "explain": ("本月已平仓 {n} 轮里有 {k} 轮赚钱，胜率 {v}。",
                "胜率 = 赚钱的轮数 ÷ 已平仓总轮数（不含还拿着的）。"),
    "drilldown": "rounds_list_filter(winner=True)",   # 第三行“涉及哪几笔”
}
```

### 10.3 执行与失效

| 环节 | 机制 |
|---|---|
| 拓扑排序 | 由 `dependencies` 构 DAG，检测环，按层并行（受单写者约束串行落库） |
| 缓存键 | `(metric_id, definition_version, scope_key, period, params_hash)` |
| 血缘 | `inputs_hash = hash(输入行 id 集合 + 行情快照版本 + 参数 + definition_version)` |
| 失效 | `inputs_hash` 或 `definition_version` 变化 → 旧结果标 `stale`，进入重算队列 |
| 可比性 | `definition_version` 的 **MAJOR** 变化表示口径方向改变（结果不可比）：趋势线与报告必须打**口径断点**、禁止跨版本连线（FR-D5）；**MINOR** 变化（精度/窗口/文案）仍可比 |
| 失败隔离 | 单指标异常不阻塞其他指标；标 `status=error` 并写 `support_id` |

### 10.4 门禁：把"结论的安全性"做进引擎

这是本架构对 PRD FR-L6 / FR-R8 的**结构性回应**——不是靠自觉，而是靠状态机：

```
输入就绪？
 ├─ 对账断链（资产类） ─────────▶ status=blocked   → 不渲染收益结论（页面红字）
 ├─ 样本不足（n<10 / 日频<60）──▶ sample_status=insufficient → 只出原始值
 ├─ 观察窗口未走完 ────────────▶ sample_status=pending_window → 不进分母
 ├─ 行情缺失/陈旧 ─────────────▶ status=stale → 显示“数据截至 X，可能滞后”
 └─ 全部通过 ──────────────────▶ status=ok
```

**渲染层规则**：`status != ok` 时，模板只能渲染"占位 + 原因 + 怎么办"，**物理上无法**渲染结论句（由模板守卫 + 快照测试保证）。

---

## 11. API 契约

### 11.1 设计原则

1. **双通道**：SSR 页面（人用）+ JSON API（页面自身/未来脚本用），同一 application 层，禁止两套逻辑。
2. **长任务不阻塞**：一切重计算返回 `202 + job_id`，通过 SSE 推送进度。
3. **幂等**：写操作支持 `Idempotency-Key`；导入天然以文件哈希 + bag 去重幂等。
4. **错误可操作**：RFC 7807 + 扩展字段（NFR-12）。
5. **版本前缀**：`/api/v1`；破坏性变更才升 v2。

### 11.2 端点清单（节选，完整见 OpenAPI）

| Method | Path | 用途 | 需求 | 幂等 |
|---|---|---|---|---|
| POST | `/api/v1/imports` | 上传账单（multipart） | FR-L3 | 文件哈希幂等 |
| GET | `/api/v1/imports/{id}` | 批次结果（含隔离区） | FR-L3 | 读 |
| POST | `/api/v1/imports/{id}/revoke` | 撤销批次 | FR-L3 | 是 |
| GET | `/api/v1/trades` | 流水分页 | FR-L8 | 读 |
| PATCH | `/api/v1/trades/{id}` | 人工修正 | FR-L4 | `If-Match` |
| DELETE | `/api/v1/trades/{id}` | 软删 | FR-L4 | 是 |
| POST | `/api/v1/trades/{id}/restore` | 恢复 | FR-L4 | 是 |
| GET | `/api/v1/reconciliation` | 对账状态与断点 | FR-L6 | 读 |
| GET | `/api/v1/rounds` | 回合列表 | FR-R2 | 读 |
| GET | `/api/v1/nav` | 净值序列（含基准） | FR-R3/R5 | 读 |
| GET | `/api/v1/metrics` | 批量取指标结果 | FR-R5~R8 | 读 |
| GET | `/api/v1/metrics/{id}/explain` | 三段式解释 | FR-D3 | 读 |
| GET | `/api/v1/metrics/{id}/drilldown` | 涉及哪几笔 | FR-D3 | 读 |
| GET | `/api/v1/attribution` | 归因结果 | FR-R7 | 读 |
| GET | `/api/v1/trends` | 纵向趋势 | FR-D5 | 读 |
| POST | `/api/v1/jobs/{kind}` | 触发重算 | — | `Idempotency-Key` |
| GET | `/api/v1/jobs/{id}/events` | SSE 进度 | — | 读 |
| POST | `/api/v1/reports` | 生成月报 | FR-D2 | 期间幂等 |
| GET | `/reports/{id}.html` | 报告静态页 | FR-D2 | 读 |
| GET/POST | `/api/v1/journal` | 交易日志 | FR-D4 | 读/写 |
| GET/PUT | `/api/v1/settings` | 设置（隐私开关/基准/规则） | FR-D7.3、FR-L5 | 写 |
| POST | `/api/v1/export` | 全量导出 | FR-L7 | `Idempotency-Key` |
| POST | `/api/v1/backups` | 立即备份 | FR-L7 | 是 |
| POST | `/api/v1/chat/messages` | 对话 | FR-D7 | 会话内 |
| GET | `/api/v1/health` | 健康 | NFR | 读 |
| GET | `/api/v1/doctor` | 自诊断 | Reliability | 读 |
| GET | `/metrics` | 本地 Prometheus 文本（进程内指标快照） | NFR-10、Reliability §11 | 读 |

### 11.3 错误模型

```json
{
  "type": "https://app.local/errors/import-missing-cash-balance",
  "title": "缺少“资金余额”列",
  "status": 422,
  "code": "IMPORT_MISSING_CASH_BALANCE",
  "user_message": "你导入的文件里没有“资金余额”列，可能是交割单。",
  "what_to_do": ["改用“对账单”导出", "或继续导入：对账不可用但收益仍可计算"],
  "retryable": false,
  "support_id": "req_01H...",
  "detail": {"missing_columns": ["资金余额"]}
}
```

> `user_message / what_to_do` 是**必填**字段（NFR-12 的架构级保证）。技术栈只出现在 `detail` 与日志。

---

## 12. 交付层架构

### 12.1 为什么是 SSR + HTMX 而不是 SPA

| 维度 | SSR + HTMX | SPA (React/Vue) |
|---|---|---|
| 构建步骤 | 无 | 需要 node/pnpm，违反"零安装"精神 |
| 离线 | 天然 | 需要额外配置 |
| 首屏 | 服务端渲染，直达 | 空壳 + 请求瀑布 |
| 复杂度 | 单进程、单语言 | 前后端两套栈、两套构建 |
| 交互丰富度 | 足够（局部刷新 + SSE + ECharts） | 更强（本产品用不上） |

**结论**：ADR-0006 采纳 SSR + HTMX。ECharts 以 **vendored 静态文件** 引入（满足 C3 离线约束）。

### 12.2 渲染结构

```
delivery/web/
├── pages/            # 整页：dashboard, rounds, reports, journal, settings, doctor
├── partials/         # HTMX 片段：卡片、表格、进度条、下钻抽屉
├── guards.py         # ★ 模板守卫：status != ok 时禁止渲染结论（§10.4）
└── static/vendor/    # htmx.min.js, echarts.min.js, pico.css（全部本地）
```

### 12.3 报告生成（FR-D2）

| 环节 | 设计 |
|---|---|
| 规格 | `ReportSpec`（period + 区块开关 + 基准 + 口径版本快照） |
| 区块 | `ReportBlock` 插件：概览 / 归因 / 最赚最亏 / 行为 / 重复错误 / 一条行动 / 口径脚注 |
| 渲染 | Jinja2 → 自包含 HTML（内联 CSS + 内联图表 SVG 或本地 ECharts 快照） |
| 落盘 | `tmp → fsync → rename` 原子写 + `checksum` 落库（Reliability §4） |
| 幂等 | 同 `(period, spec_hash)` 复用已有报告 |
| 可离线 | HTML 自包含，可在未安装本应用的设备打开 |
| 降级 | 区间内存在 `blocked` 指标时，报告降级为**"数据质量说明单"**（不输出收益结论，只输出可信度与修复指引），与 [Reliability §7](Reliability.md) 降级矩阵一致 |

### 12.4 "三段式"的架构支撑

FR-D3 要求"结论 / 怎么算 / 涉及哪几笔"。这不是文案工作，而是**元数据驱动**：

```
MetricDef.explain  → 渲染第一、二行（带参数插值，模板化）
MetricDef.drilldown → 生成第三行的查询声明 → 由同一 API 执行
```

好处：新增指标自动获得合规的解释与下钻，不需要改渲染层——这是 §2.3 "新增指标 ≤1 文件"得以成立的关键。

---

## 13. 对话子系统（P1）

### 13.1 关键约束：金额不出本机

```
用户提问
  │
  ▼
[ToolGateway] 白名单工具（只读指标 API）──▶ 本地计算结果（含金额）
  │
  ▼
[ResponseComposer] 本地模板填充：{"本月收益": 1234.56, ...}
  │
  ▼
[EgressGuard] 断言：出站 payload 不含金额/账号/股东代码 → 违规即拦截
  │
  ▼
[LlmProvider] 只收到：指标名、比例、分位、样本量、以及“请组织语言”指令
  │
  ▼
回答 = 本地数值 + LLM 措辞 + 【看流水】下钻链接
```

| 组件 | 职责 | 保障的需求 |
|---|---|---|
| `ToolGateway` | 工具白名单 + 参数 schema 校验 + 调用留痕 | 防幻觉（强制取数） |
| `ResponseComposer` | 本地填充数值模板 | 金额可见、断网可答 |
| `EgressGuard` | 出站前正则/JSON schema 断言 | NFR-1、ASR-8 |
| `LlmProvider` | 多 provider 适配 + 超时/降级 | 可用性 |
| `sys_llm_call` | 调用留痕（含是否脱敏） | 可观测、可审计 |

**降级路径**：LLM 不可用 → 直接返回"结构化结论 + 模板文案"（用户仍能得到答案，只是措辞不灵活）。这是"LLM 是旁路而非主路"的架构体现。

---

## 14. 性能架构

### 14.1 预算（来自 NFR-10）

| 场景 | 预算 | 主要手段 |
|---|---|---|
| 首页（5 万行 / 3 年） | ≤ 3s | 物化指标 + 索引 + 单次查询聚合 |
| 指标全量重算 | ≤ 60s | 批量读 + 纯计算 + 批量写 |
| 增量重算（单批导入后） | ≤ 5s | 脏窗口 + 物化复用 |
| 单文件导入解析（5000 行） | ≤ 3s | 流式解析 + 一次事务 |
| 月报生成 | ≤ 5s | 报告复用 + SVG 预生成 |

### 14.2 实现路径

| 手段 | 说明 |
|---|---|
| 物化 | `review_metric_result` 即缓存，避免每次现算 |
| 增量 | 脏窗口 + `inputs_hash` 精确失效，避免全量重算 |
| 索引 | §8.3 的 8 个索引覆盖全部高频路径 |
| 批量 | 导入单事务、指标单事务分批（每 500 个结果一次 commit） |
| 避免 N+1 | Repository 提供 `get_many` 批量接口（契约层强制） |
| 向量化边界 | **pandas 只允许出现在 `infrastructure/read_models`**，用于批量聚合；其结果必须由 core 的标度整数实现做 golden 对账（[Testing §4.4 oracle 策略](Testing.md)） |
| PRAGMA | `journal_mode=WAL`、`cache_size`、`temp_store=MEMORY`、`mmap_size`（见 Reliability 参数表） |
| 渲染 | 卡片查询合并为一次往返；图表数据走单一 JSON 端点 |

### 14.3 何时该换 PostgreSQL

触发条件（满足任一即需评估，见 ADR-0001）：

| 指标 | 阈值 |
|---|---|
| `ledger_trade` 行数 | > 500 万 |
| DB 大小 | > 5 GB |
| 并发写者 | > 1（多用户/多设备） |
| 需要跨机访问 | 出现即触发 |

**架构准备**：SQLAlchemy 元数据与 Alembic 迁移保持方言无关（禁用 SQLite 专有 SQL 出现在 domain；`batch_alter_table` 兼容）；切换只需换连接串 + 运行迁移。

---

## 15. 安全、隐私与合规

### 15.1 威胁模型（本地应用的真实威胁）

| 威胁 | 现实性 | 控制 |
|---|---|---|
| 同机其他进程/他人读取 DB | 中 | 数据目录权限 0700；可选 DB 加密（P2） |
| 浏览器侧 CSRF（本机页面） | 中 | SameSite=Strict + CSRF token（写操作） |
| 误将报告分享出去 | 高 | 报告脱敏选项（账号/金额遮罩）、脚注声明 |
| LLM 出站泄露 | 高 | `EgressGuard` 断言 + 默认不含金额（§13） |
| 依赖供应链 | 中 | `uv.lock` 锁定 + 无 postinstall 脚本 + 依赖白名单审查 |
| 端口被局域网访问 | 中 | 仅绑定 `127.0.0.1`（不绑 `0.0.0.0`），启动时断言 |

### 15.2 控制项

| 控制 | 实现位置 | 对应 NFR |
|---|---|---|
| 绑定回环 + 启动断言 | `apps/server/launcher.py` | NFR-6 |
| 无遥测、无外呼（除显式配置的数据源/LLM） | `platform/config` + 出站守卫 | NFR-1 |
| 金额/账号脱敏日志 | `platform/obs/logging.py` 过滤器 | NFR-1 |
| 报告水印与脚注 | `delivery/reports/spec.py` | 合规 |
| 建议文案模板白名单 | `delivery/reports/blocks/action.py` | C7 |
| 密钥（LLM key）存 OS keychain 或环境变量 | `platform/config/secrets.py` | NFR-1 |

### 15.3 数据主权（FR-L7）

```
snapshot: sqlite3.Connection.backup()  → backups/review-YYYYMMDD-HHMMSS.db
verify:   打开副本 → PRAGMA integrity_check → 行数比对 → 关键指标 diff
restore:  停写 → 备份当前 → 替换 → 迁移 → doctor → 指标 diff 报告
export:   Excel（人看）+ JSON（机读，含 meta：definition_version/data_as_of）
```

---

## 16. 可观测性架构

三条线，全部**本地落盘**（不上报）：

| 线 | 内容 | 位置 | 用户可见性 |
|---|---|---|---|
| 结构化日志 | JSON，字段规范见 Reliability §11；`request_id`/`job_id` 贯穿 | `logs/app-*.jsonl` | 诊断包 |
| 进程内指标 | 导入行数/失败率/重算耗时/降级次数/缓存命中 | `sys_*` 计数 + `/api/v1/doctor` | 自诊断页 |
| 本地 trace | 关键链路 span（可选 OTel → 本地文件） | `logs/trace-*.jsonl` | 诊断包 |

**关键设计**：面向**非程序员用户**的可观测性 = "一键生成诊断包"（zip：doctor 结果 + 最近日志 + 配置快照 + 版本 + DB 统计），用户发给开发者即可定位。这比仪表盘更符合 C6。

---

## 17. 关键架构决策摘要

| ADR | 决策 | 状态 | 一句话理由 | 被否方案 |
|---|---|---|---|---|
| [0001](ADR.md) | 单进程 + SQLite 单文件 | 采纳 | 唯一满足零安装/崩溃安全/低维护的组合 | 微服务 + PG + 多容器 |
| [0002](ADR.md) | 六边形分层 + 消费方定义端口 | 采纳 | 让计算层可脱离 I/O 测试（覆盖率前提） | 传统三层 + 直接 ORM |
| [0003](ADR.md) | 标度整数（非 float / 非 DECIMAL） | 采纳 | 金融金额精确 + 跨库可移植 | float、SQLAlchemy Numeric |
| [0004](ADR.md) | 指标注册表 + 框架代算 n/CI/状态 | 采纳 | 横切需求集中实现，新增指标 1 文件 | 各指标自算、通用表达式 DSL |
| [0005](ADR.md) | 结构化产物与渲染层分离 | 采纳 | 满足"结构化可追溯 + 人话可读"双重要求 | 直接把文案存库 |
| [0006](ADR.md) | SSR + HTMX + vendored 资产 | 采纳 | 无构建、离线、单语言 | SPA + 构建链 |
| [0007](ADR.md) | 事件经 outbox，不引消息中间件 | 采纳 | 保留扩展点（提醒/推送）且零运维 | Kafka/RabbitMQ/Redis |
| [0008](ADR.md) | 派生数据弱持久化（`synchronous=NORMAL`），事实数据强持久化 | 采纳 | 派生可重建，事实不可重建 | 全库 FULL（慢）、全库 NORMAL（险） |
| [0009](ADR.md) | 后复权 + as-of 因子快照 | 采纳 | 口径可复现（NFR-3/NFR-5） | 前复权实时计算 |
| [0010](ADR.md) | 分层差异化覆盖率 + 变异测试 | 采纳 | 覆盖率≠正确性 | 全局 100% |
| [0011](ADR.md) | 降级矩阵 + 引擎级门禁状态机 | 采纳 | 把"结论安全性"做成结构约束 | 靠 UI 文案/开发者自觉 |
| [0012](ADR.md) | LLM 为旁路，金额本地填充 | 采纳 | 隐私与可用性同时满足 | LLM 直连数据库 |

---

## 18. 需求追溯矩阵

> 列含义：**包** = 主要承载模块；**架构机制** = 本架构给出的关键支撑；**测试** = 主要测试类型（细化见 Testing.md）。

### 18.1 功能需求

| 需求 | 包 | 架构机制 | 测试 |
|---|---|---|---|
| FR-L1 安装启动部署 | apps/server、platform/db | launcher 探端口→迁移→自愈→开浏览器；单文件 DB | e2e（干净环境启动）、doctor 自检 |
| FR-L2 用户数据分区 | ledger | seed 单行 + `user_id` 贯穿；无权限层 | 单元（seed 幂等） |
| FR-L3 账单导入 | ledger、core/fifo?（否） | 解析器注册表 + 列映射 + 多日期格式 + bag 去重 + 批次撤销 + 向导 | golden fixture（多格式/边界）、属性测试（幂等）、集成 |
| FR-L4 数据维护 | ledger | 软删 + `history` 审计 + 恢复重算 | 单元 + 集成 |
| FR-L5 摘要分类与隔离 | ledger | 规则表配置化 + 隔离区表 + 打新类型 | 单元（规则优先级）+ fixture（未知摘要） |
| FR-L6 对账自证 | ledger、review | `ledger_reconciliation` + 引擎门禁状态机（blocked） | fixture（断链）+ 快照（阻断态不渲染结论） |
| FR-L7 数据主权 | platform/backup、ledger | `.backup` API + 校验 + 原子写 + 导出 | 集成（备份→恢复→diff=0）、故障注入 |
| FR-L8 查询 API | ledger/api | 批量 Repository + 分页游标 | 契约测试（OpenAPI 快照） |
| FR-R1 数据同步 | review | 同库只读视图/函数调用，无镜像 | 集成（一致性） |
| FR-R2 期初资产与回合 | core/fifo、review | 期初反推 `back_solved` + FIFO 稳定序 + 股息归属 + 桥接恒等式 | 属性（数量/成本守恒）、golden（除权/送转）、单元 |
| FR-R3 净值与收益 | core/nav、review | 份额法 + 外部现金流白名单 + XIRR | 属性（TWR 对现金流不变）、golden（TWR vs MWR 方向） |
| FR-R4 选股能力 | core/metrics、review/marketdata | PIT 数据门 + cohort 归属 + BHAR + `data_as_of` | golden（前视偏差断言）、契约（数据源） |
| FR-R5 择时与绩效 | core/metrics、stats | 基准锚定 + β/α + 回撤族 + 费用口径 + z 分数 | golden（口径复算）、单元（容差） |
| FR-R6 行为偏差 | core/metrics、stats | PGR/PLR + bootstrap CI + HHI | 单元 + 属性（只卖盈样例） |
| FR-R7 归因 | core/attribution | 五因子（P0）+ Brinson/Carino（P1），DAG 节点 | golden（单因子主导排序）、单元（恒等式） |
| FR-R8 统计可信度 | core/stats、metrics 引擎 | `sample_policy` 由框架代算；状态机 | 单元（门槛边界）、快照（N<10 无结论） |
| FR-D1 只读看板 | delivery/web、guards | SSR + HTMX + 卡片聚合 + 模板守卫 | 快照（阻断态）、e2e（首屏≤3s） |
| FR-D2 月度报告 | delivery/reports | ReportSpec + 区块插件 + 自包含 HTML + 原子写 | 快照（报告结构）、e2e（离线打开）、diff（数字一致） |
| FR-D3 人话与下钻 | delivery、core/metrics | `explain`/`drilldown` 元数据驱动 | 快照 + 术语扫描测试（无裸术语） |
| FR-D4 交易日志闭环 | delivery、review | 一等实体 + 卖出后事件触发对照 | 集成（事件→提示） |
| FR-D5 纵向趋势 | delivery、review | 从 `metric_result` 派生趋势点；口径断点标记 | 单元（断点逻辑） |
| FR-D6 行为规则卡 | delivery、platform/events | 规则表 + `AnomalyDetected` 评估器 | 单元（阈值）+ 集成（回放） |
| FR-D7 对话复盘 | delivery/chat、platform | ToolGateway 白名单 + 本地填充 + EgressGuard + Provider 降级 | 断网 e2e、出站断言测试、录制回放 |
| FR-D8 通知与移动端 | delivery、platform/events | 静态报告页 + outbox 通知通道（P2） | 单元 + e2e（手机浏览器） |

### 18.2 非功能需求

| 需求 | 架构机制 | 验证 |
|---|---|---|
| NFR-1 隐私 | 回环绑定、LLM 旁路、EgressGuard、日志脱敏 | 出站断言测试、日志扫描 |
| NFR-2 数据校验层 | `ledger_provenance` + 适配器校验器（涨跌停/复权跳变/一致性） | 恶意 fixture、契约测试 |
| NFR-3 时点一致 | PIT 门 + `data_as_of` + `pending_window` | golden（前视偏差）、单元 |
| NFR-4 数据可信 | 对账闸门 + `MetricResult` 血缘字段 + 三段式 | 集成 + 快照 |
| NFR-5 幂等可重放 | 唯一约束 + 批次撤销 + 脏窗口 + 作业续跑 | 属性测试 + 故障注入 |
| NFR-6 无鉴权 | 绑定回环 + 启动断言 + CSRF | e2e（外部访问失败） |
| NFR-7 测试与验收口径 | 分层覆盖门禁 + golden + 变异 + 架构测试 | CI 自身（Testing.md） |
| NFR-8 工程一致性 | uv workspace + contracts 同源 + import-linter | 架构约束测试 |
| NFR-9 依赖与灾备源 | `MarketDataSource` 能力协商 + 适配器 + 缓存 | 契约测试 + 熔断测试 |
| NFR-10 性能 | 物化 + 增量 + 索引 + 批量 | 基准测试（benchmark 套件） |
| NFR-11 可维护性 | `definition_version` + 注册表集中 + 迁移策略 | 版本变更演练测试 |
| NFR-12 可用性 | 错误模型必填 `user_message/what_to_do` | 契约测试（错误 schema 快照） |

---

## 19. 演进路线（与 PRD M0–M3 对齐）

| 阶段 | 架构增量 | 退出条件 |
|---|---|---|
| **M0 可用底座** | 包骨架、SQLite + 迁移、单写者、导入流水线、对账、作业框架、launcher、doctor、SSR 骨架 | 干净环境一键启动；三模式导入；断链检出；架构约束测试全绿 |
| **M1 四问 + 月报** | core 全套口径、指标引擎、门禁状态机、看板五卡、报告生成、可信度层 | core 100% 覆盖 + 变异 ≥80%；golden 全过；月报可离线打开 |
| **M2 归因与闭环** | 归因 DAG、趋势、日志实体、规则卡、outbox 消费者 | 五因子恒等式；Brinson 三效应 ≤1bp；事件回放可用 |
| **M3 对话** | ToolGateway、EgressGuard、Provider 适配、降级 | 出站断言通过；断网可答；10 固定问题全通过 |

**演进原则**：每个阶段只新增端口，不改已定的分层与契约；`definition_version` 与 `report_schema_version` 独立演进。

---

## 20. 架构风险登记

| 风险 | 触发条件 | 影响 | 缓解 / 决策点 |
|---|---|---|---|
| 单进程下长任务阻塞页面 | 全量重算 60s 与用户操作并发 | 首页超时 | 线程池隔离 + 单写者队列 + 页面读缓存；M1 压测验证 |
| SQLite 写放大 | 高频增量重算 | 导入变慢 | WAL + 批量提交 + 只重算脏窗口；监控 `wal` 大小 |
| core 追求 100% 覆盖导致过度抽象 | 开发进度压力 | 可读性下降 | 只对"口径函数"强制 100%；I/O 编排不强制 |
| 指标注册表过度设计 | 指标数量增长 | 抽象税 | 字段冻结在 v1 的最小集（见 Extensibility EP-1），新增字段需 ADR |
| 数据源能力差异 | 换源/降级 | 指标不可比 | 能力协商 + `benchmark=price+div` 等标注 + golden 对账 |
| pandas 与 core 双实现漂移 | 性能优化引入并行实现 | 数字不一致 | 强制 golden 对账（[Testing §4.4](Testing.md)），CI 阻断 |
| 报告与看板数字不一致 | 两条渲染路径 | 信任崩塌 | 同一 `MetricResult` 出处 + e2e diff 测试 |
| LLM 输出越界 | 模型幻觉 | 合规风险 | 本地数值填充 + 文案白名单 + 出站断言 + 关闭模型仍可用 |

---

## 附录 A：技术选型与理由（含被否方案）

| 领域 | 选型 | 理由 | 被否 |
|---|---|---|---|
| 语言 | Python 3.12 | akshare 生态、单人开发效率 | Go/Rust（数据生态成本）、Node（数据分析弱） |
| 包管理 | uv workspace | PRD NFR-8 指定；锁文件 + 快 | pip/poetry（无 workspace 语义） |
| Web | FastAPI + Pydantic v2 | 契约同源、OpenAPI 自动、SSR 兼容 Jinja2 | Django（重）、Flask（契约弱） |
| ORM | SQLAlchemy 2.0 + Alembic | 方言无关、迁移成熟 | 裸 SQL（迁移与可测性差）、SQLModel（抽象不足） |
| 存储 | SQLite(WAL) 默认 / PG 可选 | 零安装 vs 未来扩展 | 仅 PG（违反 C2）、DuckDB（并发写弱） |
| 模板 | Jinja2 + HTMX | 无构建、离线 | React/Vue（构建链 + 双语言） |
| 图表 | ECharts vendored | 金融图表成熟、离线可用 | Chart.js（能力弱）、服务端图表（交互差） |
| 调度 | APScheduler 进程内 | 零外部依赖 | Celery（需 broker）、系统 cron（不可移植） |
| 日志 | structlog | 结构化 + 易脱敏 | logging（结构弱） |
| 测试 | pytest + hypothesis + syrupy + mutmut + import-linter | 覆盖"正确性 + 不变量 + 快照 + 变异 + 架构" | 仅 pytest（漏不变量与架构腐化） |
| 打包 | uv + `.command`/`.bat`；PyInstaller（可选） | 零安装入口 | 仅 Docker（需 Docker 安装） |
| 数值 | 标度整数 + Decimal 边界 | 精确、快、可移植 | float（不可接受）、DECIMAL（方言差异） |

## 附录 B：关键约定（团队规范）

1. 所有金额/价格/数量/比率类型必须用 `core/values` 的标度整数类型，禁止 `float`。
2. 所有时间必须来自注入的 `Clock`，禁止直接 `datetime.now()`。
3. 所有指标必须注册进 `core/metrics/registry`，禁止在 application 层直接算指标。
4. 所有面向用户的文案必须来自模板或 `MetricDef.explain`，禁止在 HTML 内写死业务结论。
5. 所有写操作必须带幂等键或天然幂等（唯一约束兜底）。
6. 所有新增外部依赖必须更新 ADR 与附录 A。
7. 文件命名：`*_repo.py`（仓储实现）、`*_service.py`（用例）、`*_def.py`（指标定义）、`*_block.py`（报告区块）。
