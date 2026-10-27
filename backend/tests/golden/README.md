# Golden 金标用例（G8）

> 设计依据：`docs/architecture/2026-10-26-metrics-three-domain.md` §8.2/§8.3/§8.11、
> `docs/reviews/2026-10-26-DevDesign-Review-metrics-three-domain.md`。
> 门禁：`scripts/ci/gate_golden_approval.py`；清单：`MANIFEST.json` + `COVERAGE.json`。

## 1. 目录与文件

```
backend/tests/golden/
├── loader.py                 # 唯一 schema 入口（pytest 与 gate 共用）
├── MANIFEST.json             # definition_versions / fixtures sha256 / goldens sha256
├── COVERAGE.json             # 必测场景矩阵（required 场景 → golden 文件）
├── <metric_id>/golden*.json  # 实际 golden（会被 pytest 参数化加载）
└── _skeleton/**/*.json.tmpl  # 缺失场景骨架（不加载，按 COVERAGE 补齐后改名去 .tmpl）
```

## 2. golden JSON 必填字段（loader 严格校验）

```json
{
  "schema_version": 1,
  "metric_id": "perf.win_rate",
  "definition_version": "1.0",
  "fixture": {"id": "fx_metrics_core", "flags": {}},
  "period": "2026-09",
  "scope": "round",
  "params": {},
  "expected": {
    "value": "0.66666667",          // null 或展示字符串（按 def precision 折算）
    "unit": "ratio",
    "n": 12,
    "n_pending": 0,                  // 可选；存在时测试断言
    "ci_low": "0.39062209",
    "ci_high": "0.86187991",
    "sample_status": "ok",
    "status": "ok",
    "decomposition": {"wins": 8},    // 值：标度整数字符串/int/文本/null
    "assumptions": {"calendar": "weekday_proxy"}  // 可选；存在时测试断言子集
  },
  "tolerance": {"kind": "ratio_bp", "eps": "0.0001"},
  "expected_source": {"kind": "hand_calc", "ref": "手算表位置", "oracle": null},
  "review": {"author": "test-designer", "reviewer": "zhang", "approved": true, "approved_at": "2026-10-27"},
  "boundaries": ["n=12 ≥ min_n：出 Wilson CI"],
  "scenario": "perf.win_rate.base",   // 可选
  "coverage": ["perf.win_rate.base"]  // 可选
}
```

硬规则（不满足直接 `GoldenFormatError`）：
- 未知 key 一律拒绝（顶层/expected/fixture/review/expected_source/tolerance）；
- `status != ok` 或 `sample_status == pending_window` → `value` 必须 `null`、CI 必须 `null`；
- `sample_status == insufficient` → 不得有 CI；
- `status=ok 且 sample_status=ok` → `value` 不得为 null；
- CI 必须成对、`ci_low <= value <= ci_high`，且只在 `ok/ok` 出现；
- `review.approved=true` 时 `reviewer/approved_at` 必填且 **reviewer != author**；
- `expected_source.kind ∈ {hand_calc, oracle, hand_calc+oracle}`。

## 3. 门禁两种模式

```bash
make gate-golden-structure   # PR-fast：schema + version + fixture/golden sha256
make gate-golden             # nightly/release：再加人工审批 + COVERAGE 场景矩阵
```

- **structure**：不要求 `approved=true`（新人可先提 PR 补用例），但 golden 内容必须与
  `MANIFEST.goldens` 一致；新增/改 golden 后需更新 MANIFEST（见 §5）。
- **approval**：25 份全部 `approved=true` 且 reviewer 非作者、`COVERAGE.json` 的 required 场景
  全部有 golden 且 `expect_status/sample_status/value_present` 断言成立，才会绿。
- 当前 22 个缺失场景在 `_skeleton/`，因此 approval 模式会继续红（预期，不阻断 PR-fast）；人工审批已由 reviewer `zhang` 完成（25/25）。

## 4. 补一个缺失用例

1. 在 `COVERAGE.json` 找到 `required` 条目；在 `_skeleton/<metric>/<file>.tmpl` 看待补项；
2. 在 `backend/tests/support/fx_metrics_*.py` 增加/复用 fixture builder；
3. 手算或独立 oracle 得到 `expected`（**禁止用生产代码生成后回填**）；
4. 把 `.json.tmpl` 复制为 `golden.<name>.json`（去掉 `.tmpl`），补全所有字段；
5. `pytest backend/tests/golden -q` 全绿；更新 MANIFEST（§5）；
6. 人工 reviewer 复核后把 `review` 写成 `approved=true / reviewer / approved_at`；
   `make gate-golden` 才会转绿。

## 5. 更新 MANIFEST / 重新计算 sha256

```bash
.venv/bin/python - <<'PY'
import hashlib, json, pathlib
root = pathlib.Path("backend/tests/golden")
m = json.loads((root / "MANIFEST.json").read_text())
m["goldens"] = {
    p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(root.rglob("golden*.json"))
    if p.name != "MANIFEST.json" and "_skeleton" not in p.parts
}
(root / "MANIFEST.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
PY
```

> 改 golden 期望值 = 改审批对象：必须更新 MANIFEST + 重新人工签署，否则 approval 门禁红。

## 6. 已知待人工拍板（HITL，不得 Agent 自签）

| 项 | 说明 |
|---|---|
| `timing.chase` / `timing.fly` | 当前实现为"20 根内 >3% 命中率"，与已批准 A17/A16 分位/z 口径不一致；已随人工审批签署，设计层冲突仍需单独裁决 |
| `perf.fee_breakeven` blocked | 现有 `golden.blocked.json` 实为 `ok/insufficient`；`COVERAGE` 要求 `status=blocked`，approval 模式会报错，待修口径/门禁 |
| 其他缺失场景 | `_skeleton/` 22 份模板 + `COVERAGE.required`，按 §4 补齐后再签 |
| HITL 阻断 | `COVERAGE.hitl` 在 approval 模式下是 **error**；本次人工审批后标记已移除，后续新增 HITL 仍会阻断 |
