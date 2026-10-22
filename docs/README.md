# 文档总索引

> 项目：个人交易复盘平台 ｜ 更新：2026-10-19
> **现行版本 = v2 系列**（多用户 + 云主机 + PostgreSQL + 前后端分离 + Monorepo + API 优先验收）。
> v1 系列为**历史基线**，记录演进过程，不再更新。

---

## 1. 先看哪个？

| 你想 | 打开 |
|---|---|
| 知道**要做什么**（需求） | [prd/PRD-v2.md](prd/PRD-v2.md) |
| 知道**怎么建**（架构） | [Architecture-v2.md](Architecture-v2.md) |
| **验收**某个功能（"完成"的判据） | [architecture/v2/API-Catalog.md](architecture/v2/API-Catalog.md) — 80 个端点 + 验收矩阵 |
| 知道**测试怎么做**、覆盖率门禁 | [architecture/v2/Testing.md](architecture/v2/Testing.md) |
| 知道**怎么上线/兜底/恢复** | [architecture/v2/Reliability.md](architecture/v2/Reliability.md) |
| 知道**加功能要改哪里** | [architecture/v2/Extensibility.md](architecture/v2/Extensibility.md) |
| 知道**先写什么、每期怎么本地验** | [DEV-PLAN-v2.md](DEV-PLAN-v2.md) — 三期开发清单（50 任务 / 93.5 人日） |
| 知道**为什么这么决策** | [architecture/v2/ADR.md](architecture/v2/ADR.md) |
| 看 v2 文档包导读 | [architecture/v2/README.md](architecture/v2/README.md) |

---

## 2. 现行（v2）

```
docs/
├── Architecture-v2.md              # 主架构（前置"可验收 API 清单"）
├── DEV-PLAN-v2.md                  # ★ 开发清单：一期/二期/三期 + 交付；每期本地可验证脚本
├── prd/
│   └── PRD-v2.md                   # 需求基线 v2.0（28 FR + 15 NFR，每条带 API 验收）
└── architecture/v2/
    ├── README.md                   # v2 文档包索引（单一事实源表、关键数字、阅读路径）
    ├── API-Catalog.md              # ★ 80 端点 / 47 错误码 / 验收矩阵 80/80
    ├── Reliability.md             # 8 条 SLO、20 行 FMEA、降级矩阵、PITR、SOP
    ├── Testing.md                 # 覆盖率门禁、越权矩阵、契约三件套、前端零口径
    ├── Extensibility.md           # 12 扩展点、租户演进、API 兼容治理、反扩展
    └── ADR.md                     # ADR-0013~0024（取代链）
```

**关键数字**：80 端点 ｜ 28/28 FR + 15/15 NFR 覆盖 ｜ 36 张表 ｜ 12 扩展点 ｜ 可用性 ≥99.9% ｜ RPO ≤5min / RTO ≤30min ｜ core 覆盖 100/100。

**一期验收（2026-10-20）**：[reviews/Period1-CodeReview-and-Acceptance.md](reviews/Period1-CodeReview-and-Acceptance.md) — 代码审查 + 一期验收报告，结论：**不通过**（5 项 P0 / 17 项 P1 待修，含真账单对账假断链、`replace_range` 数据丢失、admin PAT 越权、撤销入口缺失、verify-p1 空心）。
> **修复进展（2026-10-21）**：5 项 P0 已全部修复并复验 — [P0-2/P0-3](reviews/Period1-Fix-Log-P0-2-P0-3.md)（数据丢失 / admin PAT）、[P0-1/P0-4/P0-5](reviews/Period1-Fix-Log-P0-1-P0-4-P0-5.md)（真实账对账 / 撤销 UI / 门禁与契约）；`make verify-p1` 15/15 全绿。剩余 P1/P2 见审查报告。
> **二期准入（2026-10-21）**：[reviews/Period2-Entry-Checklist.md](reviews/Period2-Entry-Checklist.md)（G1~G7 准入检查单）｜[G2/G3/G4 修复记录](reviews/Period2-Hardening-Fix-Log-G2-G3-G4.md)｜[G5/G1 交付记录](reviews/Period2-Hardening-Fix-Log-G5-G1.md)｜[G6/G7 交付记录](reviews/Period2-Hardening-Fix-Log-G6-G7.md)（G1~G7 全部关闭；verify-p1 18/18）。

---

## 3. 历史（v1，只读）

| 目录/文件 | 说明 |
|---|---|
| [prd/PRD.md](prd/PRD.md) | 最初稿（225 行，单机、无界面） |
| [prd/Review.md](prd/Review.md) | **双视角评审报告**：金融专业 4.0/10、个人投资者 4/10；13 项 blocker + 8 处冲突裁决 |
| [reviews/financial-review.md](prd/reviews/financial-review.md) · [reviews/investor-review.md](prd/reviews/investor-review.md) | 两份独立评审原文 |
| [prd/PRD-v1.md](prd/PRD-v1.md) | v1.1（依据评审修订：单机 + 单页 UI + 月报） |
| [architecture/v1/](architecture/v1/README.md) | v1 架构包：Architecture / Reliability / Testing / Extensibility / ADR-0001~0012 |

---

## 4. 演进脉络（三句话）

1. **v1.0**：单机工具，只有 API、无可视化 → 双视角评审判「需重大修改」。
2. **v1.1**：补单页 UI、月度报告、归因、可信度层，口径全部公式化（A1–A27）。
3. **v2.0**：形态升级为**多用户云服务**；PostgreSQL + 前后端分离 + API 优先验收；口径（A1–A27）**保持不变**——金融正确性与部署形态无关。

---

## 5. 维护约定

- **单一事实源**：端点/错误码 → API-Catalog；SLO → Reliability §1；覆盖率 → Testing §2；扩展点 → Extensibility §2；数据模型/端口 → Architecture-v2 §8/§9；决策 → ADR。
- **数字冲突**：以 v2 包内所列权威位置为准，改主位后同步引用。
- **决策变更**：不修改已采纳 ADR 正文，新增 ADR 并标注取代（当前链：0001→0013、0006→0016、0007→0015、0008→0018）。
- **v1 只读**：如需修订历史结论，请新建文档，不改动 v1 包。

---

## 6. 术语与命名（Period vs Priority）

- **期次（Period）写全称**：`Period1` / `Period2` / `Period3`；期次任务 ID 写 `Period1-01`、`Period2-03`。文档、文件名、标题中不再用 `P1`/`P2` 表示期次。
- **优先级可简写 `P`**：`P0` / `P1` / `P2` / `P3`（严重度/优先级）；review 发现编号如 `P1-3` 表示「P1 级第 3 条」，与期次任务 `Period1-03` 不混淆。
- 历史文档中的 `P1-01` 是旧写法，等价于 `Period1-01`；新文档统一使用 Period 全称。
