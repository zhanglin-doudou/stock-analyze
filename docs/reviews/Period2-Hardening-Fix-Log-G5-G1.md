# 二期准入 G5 + G1 交付记录：seed/demo 契约与一期数据基线

> 日期：2026-10-21 ｜ 基线：`Period1` 修复后的工作区 ｜ 准入检查单：[Period2-Entry-Checklist.md](Period2-Entry-Checklist.md)
> 范围：G5（seed/demo 契约收口、确定性夹具）+ G1（清库重 seed、迁移基线、打 tag）。未改 Period2-01 之后的功能。

## 0. 结论

| Gate | 状态 | 关键证据 |
|---|---|---|
| **G5** seed/demo 契约 | ✅ | seed 批次 `mode=full`；账号 `a@demo.example.com`/`b@demo.example.com`/`admin@demo.example.com` 均可经 API 登录；`make seed-tenants` 幂等；`make seed-demo` 可重复执行；`make demo-p1 --run` 完整跑通（注册→登录→预览→提交→job succeeded→流水） |
| **G1** 数据基线 | ✅ | 主库清库重建 → `alembic upgrade head`（幂等）→ `seed-tenants` ×2 → `seed-demo`；3 用户 / 238 流水 / 2 批次；fixtures 重写后字节不变；tag `period1-baseline` |

验证：`verify-p1` **15/15 全绿**（G5 改动后、独立库 `stock_g5_check`）；主库 seed 后演示账号登录 200、`GET /imports` 200、批次全为 `full`、category 全为非空（interest/transfer/sell/buy/dividend）。

## 1. G5：seed/demo 契约收口

### 1.1 改动

- **系统夹具确定性**（`stock_ledger/fixtures.py`）
  - `_to_xlsx_bytes`：固定 `docProps` 的 created/modified + zip 条目时间戳（1980-01-01），并对 `docProps/core.xml` 的 `dcterms:modified` 做最终钉死（openpyxl 保存时会覆盖）。
  - 效果：`generate_huatai_bill`/`generate_broken_bill` 跨进程、跨秒字节完全一致（bill `73aae45c…`，12781 B）；`generate_market_quotes` 仍 `40ed258d…`。
  - `seed-demo`/`demo-p1` 重写 `fixtures/market/*` 不再产生 git diff。
- **seed/demo 契约**（`stock_api/cli.py`）
  - `DEMO_ACCOUNTS` 改为合法邮箱：`a@demo.example.com` / `b@demo.example.com` / `admin@demo.example.com`；`DEMO_EMAIL=demo@demo.example.com`。
  - `_import_bill` 的 job params 改 `mode="full"`（原 `append` 与 `BatchOut` Literal 冲突，`GET /imports` 会 500）。
  - seed 不再额外 enqueue 一个不会被消费的 `reclassify` job（commit 已在 G3 中完成分类），避免孤儿 pending job。
  - `demo-p1`：
    - 注册补 `accept_tos=true`，使用合法邮箱；
    - commit 使用 `mode="full"` + `Idempotency-Key`（32+）；
    - 轮询 job 到终态，要求 `succeeded` 且导入后流水非空，失败明确报错；
    - 文案/步骤与 API 实际一致（预览 200、full 模式）。
- **文档**
  - `README.md` 演示账号表、`docs/DEV-PLAN-v2.md` §2.1 演练脚本与夹具路径同步为实际值（`a@demo.example.com` / `Demo!Passw0rd2026` / `fixtures/market/bill_huatai.xlsx`）。
- **测试**
  - 新增 `backend/tests/unit/ledger/test_fixtures_deterministic.py`：bill/broken/quotes 两次生成字节一致。

### 1.2 实测

```text
seed-tenants #1: A/B 各 inserted=119 quarantined=3（1 重复行 + 1 未知摘要 + parser 隔离）
seed-tenants #2: 账号/批次存在 → 跳过导入（幂等）
seed-demo:       幂等跳过；fixtures 重写后 sha256 不变
demo-p1 --run:   register 201 → login 200 → preview 200 → commit 202 → job succeeded → trades items=5
```

> 说明：合成夹具 120 行中 1 行未知摘要 + 1 行重复，因此 `inserted=119/quarantined=3` 是预期值（分类口径在 G3 中定义）。

## 2. G1：清库重 seed 与基线

### 2.1 动作

1. 停止本机旧 `make dev` 栈（uvicorn/worker/vite），避免旧 worker 写库/抢任务。
2. `stock_analyze` 库 `drop ... with (force)` → 以 `stock_owner` 重建 → 安装 `citext/pgcrypto` → `grant connect to stock_app`。
3. `alembic upgrade head`（首次成功建 21 表/7 条 RLS；再跑一次无操作，`alembic check` 无 drift）。
4. `seed-tenants` ×2（第二次幂等）→ `seed-demo`。
5. `fixtures/market/*` 由确定性生成器写入，重复执行字节一致。
6. 主库启动 API+worker 验证：三个账号登录 200；`GET /imports` 200；批次全 `full`；`demo-p1 --run` 全链路成功。
7. 最终再重置/seed 一次，保证基线只含 seed 数据（不含 demo-p1 账号）→ 提交并打 tag。

### 2.2 基线状态

| 项 | 值 |
|---|---|
| 用户 | 3（A/B/admin） |
| 流水 | 238（A/B 各 119） |
| 导入批次 | 2（mode=full） |
| 分类 | buy/sell/transfer/dividend/interest 均有，category 非空 |
| 迁移 | `alembic current = 0001`，`check` 无 drift |
| fixtures | bill `73aae45c…` / quotes `40ed258d…`，重跑不变 |
| tag | `period1-baseline`（提交后创建） |

## 3. 命名约定（Period vs Priority）

- 期次写全称：`Period1` / `Period2` / `Period3`；期次任务 ID `Period1-01`。
- 优先级可简写：`P0/P1/P2/P3`；review 发现编号如 `P1-3` 是「P1 级第 3 条」。
- 本次已把文档/报告/检查单文件与标题统一为 `PeriodN-*`；`DEV-PLAN-v2.md` 的任务 ID 改为 `PeriodN-xx`；历史报告保留旧写法并加说明。`docs/README.md` §6 已收录该约定。

## 4. 验证与遗留

- `verify-p1` 15/15（独立库）：单测 175、API 108、越权 18、RLS 4、架构 1、契约 20、前端 13 tests + build。
- 遗留：G6（前端接 SDK + 端点覆盖/越权门禁）、G7（review schema/platform 租户表 RLS）；Period2-01 FIFO 可立即开始。
- 旧 `make dev` 栈已停止，请重新 `make dev`/`make worker` 启动（务必重启 worker 以加载 G2/G3 新代码）。
