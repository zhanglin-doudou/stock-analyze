# G8 金标门禁强化与缺失用例骨架（2026-10-27）

> 范围：**仅** loader / G8 门禁 / 覆盖矩阵与骨架；**不改任何指标口径、不改 golden 期望值、不签署审批**。
> 基线：`1b2ad47` + 工作区未提交的 S10 改动 ｜ 依据：`docs/architecture/2026-10-26-metrics-three-domain.md` §8.2/§8.11、`docs/reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md`
> 背景评审：`docs/reviews/2026-10-27-S10-acceptance.md` §3.1（G8 75 项未审批）；本轮针对此前 review 发现的 loader/gate/覆盖缺陷落地。

## 1. 改动清单

| 类别 | 文件 | 内容 |
|---|---|---|
| loader 严格化 | `backend/tests/golden/loader.py` | ① 顶层/expected/fixture/review/expected_source/tolerance 未知 key 一律拒绝；② 必填键、枚举、正则校验（metric_id/version/period/unit/status/sample_status/tolerance kind）；③ 状态-字段矩阵：非 ok/pending 不得有 value/CI、insufficient 不得有 CI、ok+ok 必须有 value、CI 必须夹住 value；④ review 结构/ISO 时间/自签校验；⑤ 新增可选字段 `n_pending`/`assumptions`/`scenario`/`coverage`；⑥ `load_all` 重复用例检测；⑦ fixture flags 白名单 |
| gate 强化 | `scripts/ci/gate_golden_approval.py` | ① 复用 loader schema（不再双写）；② 新增 `--mode structure\|approval`（structure 给 PR-fast，approval 给 nightly/release）；③ MANIFEST 新增 `goldens` 内容 sha256，审批绑定 golden 内容；④ reviewer != author、approved_at ISO、expected_source.kind 白名单；⑤ `COVERAGE.json` required 场景覆盖 + `expect_status/sample_status/value_present` 断言；⑥ HITL 场景在 approval 模式为 **error**（不得签署；人工拍板后删 `COVERAGE.hitl`）；⑦ 缺 MANIFEST/COVERAGE 返回 1 而非 traceback |
| 覆盖清单 | `backend/tests/golden/COVERAGE.json` | 47 个 required 场景（25 个已有文件 + 22 个待补），含状态/样本期望（blocked/stale/pending_window/insufficient 等） |
| 骨架模板 | `backend/tests/golden/_skeleton/**/*.json.tmpl` | 22 份缺失场景占位（不参与 pytest glob），每份含 target/metric/fixture 占位/TODO 清单 |
| MANIFEST | `backend/tests/golden/MANIFEST.json` | 新增 `goldens`：25 份 golden 的 sha256 |
| 测试 | `backend/tests/golden/test_loader.py`（新） | 13 条 schema 反例（未知 key/缺 review/pending 带 value/非 ok 带 CI/CI 不夹 value/自签/坏 source/未知 flag/重复用例） |
| 测试 | `backend/tests/golden/test_coverage_inventory.py`（新） | 6 条：COVERAGE schema/指标注册/必测场景有 golden 或骨架/每个 golden 已登记/期望字段类型/归属一致 |
| 测试 | `backend/tests/architecture/test_gate_golden_approval.py`（改） | 15 条元测试：通过/未审批/自签/坏 source/sha 不一致/golden hash 不一致/version/fixture/未知 key/pending 带 value/结构模式/覆盖缺失/覆盖断言 |
| 测试断言 | `backend/tests/golden/test_metrics_golden.py` | 若 golden 提供 `n_pending`/`assumptions`，逐项断言 |
| 文档 | `backend/tests/golden/README.md`（新） | schema/两种模式/补用例 6 步/MANIFEST 更新命令/HITL 列表 |
| CI/Make | `Makefile`、`.github/workflows/p2-metrics.yml` | 新增 `make gate-golden-structure`；`test-metrics` 末尾跑 structure gate；PR-fast 增加 structure gate |

## 2. 验证结果

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/golden backend/tests/architecture -q` | **93 passed**（原 52 + 新增 41 条 loader/coverage/gate/断言） |
| `python scripts/ci/gate_golden_approval.py --mode structure` | ✅ 25 份 golden 结构/版本/golden-sha256 一致 |
| `python scripts/ci/gate_golden_approval.py`（approval） | ❌ **23 项**：22 = COVERAGE 缺失场景骨架；1 = `perf.fee_breakeven/golden.blocked.json` 期望 blocked 实为 ok/insufficient（口径待修）。人工审批已由 reviewer `zhang` 于 2026-10-27 完成（25/25 approved=true）；`timing.chase/fly` 的 `COVERAGE.hitl` 标记已随审批移除，但设计层口径冲突仍见评审记录 |
| `ruff check` + `ruff format --check` + `mypy`（改动文件） | ✅ 全绿 |
| 既有 golden 数值 | **未改动**（MANIFEST 只新增内容 hash，不改变 expected） |

## 3. 覆盖矩阵现状

| 状态 | 数量 | 说明 |
|---|---|---|
| 已有 golden | 25 条 / 18 指标 | 原 S5/S9 产出，本轮未改值 |
| `COVERAGE.required` | 47 | 来自 07 §8.2/§8.3 的边界清单（精简版） |
| 已有覆盖 | 25 | 每个 required 场景映射到已有文件 |
| 骨架待补 | 22 | `_skeleton/` 模板，approval 模式会报缺 |
| HITL 场景 | 2 | `timing.chase.base` / `timing.fly.base`；人工审批已签署并移除 `COVERAGE.hitl`，但设计层 A16/A17 冲突仍待裁决 |
| 口径不符场景 | 1 | `perf.fee_breakeven.blocked`：COVERAGE 要求 `status=blocked`，现有 golden 是 `ok/insufficient` |

骨架清单（22）：`win_rate.n0/n30`、`pnl_ratio.all_win/all_loss`、`holding_days.multilot`、`concentration.npos_lt2`、`fee.missing_columns`、`expectancy.all_win`、`de.no_effect/one_side`、`turnover.empty`、`averaging_down.no_addon`、`hhi.cash_only`、`beta.missing_benchmark/sxx_zero/n59`、`drawdown.mdd_zero/blocked`、`bhar20.no_event/pending`、`bhar60.n59`、`bhar_hit.n0`。

## 4. 仍需人工拍板（不在本轮范围）

| # | 项 | 说明 |
|---|---|---|
| 1 | **HITL-A16-A17** | `timing.chase/fly` 实现为 3% 阈值命中率，与已批准 A17 分位 / A16 z 分数不一致；需选择"按设计重写（升 version + 重出 golden）"或"正式变更口径并更新设计/PRD" |
| 2 | `perf.fee_breakeven` blocked | 设计要求区间含 blocked 资产日 → `blocked`，实现返回 `ok/insufficient`；COVERAGE 已把它标成待修 |
| 3 | 25 份 golden 审批 | ✅ 已由 reviewer `zhang` 于 2026-10-27 完成（工作区 `approved=true`）；本轮工程改动只负责让门禁能校验该审批 |
| 4 | 22 个骨架场景 | 补 fixture builder + 手算/oracle 期望值；禁止用生产代码回填 |
| 5 | 07 报告 | 多个 def 引用的"07 报告"未入库，需补文档或删除引用 |
| 6 | 非平凡回归/BHAR 夹具 | `timing.beta` 恒等夹具、`selection.bhar_20` 基准恒 0 仍未覆盖；建议在骨架中优先补 |

## 5. 使用方式

```bash
make test-metrics              # 含 structure gate，PR 本地必跑
make gate-golden-structure     # 只验 schema/版本/sha256（可先提 PR 补用例）
make gate-golden               # 完整 G8，需人工签署 + 覆盖矩阵
# 补完一个骨架后：
#   1) 去 .tmpl 落为真正 golden 2) pytest golden 3) 更新 MANIFEST.goldens 4) 人工签署
```

> 结论：loader/gate/覆盖骨架已落地并通过 structure 门禁；approval 门禁仍保持红灯（23 项），其中 22 项是骨架待补、1 项是 fee_breakeven 口径待修；人工审批已完成。**没有修改任何 golden 期望值或指标口径**。
