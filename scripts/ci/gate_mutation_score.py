"""G-mutation 门禁：变异击杀率阈值 + 环比防退化（Period2-07 §8.9 / D-16）。

判据：
1. 读 `mutants/mutmut-cicd-stats.json`（`mutmut export-cicd-stats` 产物）；
2. 击杀率 = killed / (killed + survived + timeout + suspicious) × 100
   （timeout/suspicious 保守计入分母；no_tests/skipped 不计）；
3. 击杀率 < ``--min``（默认 80，§8.9 新增 metrics/stats 门槛）→ exit 1；
4. 存在基线文件且击杀率较基线下降 > ``--max-drop-pp``（默认 2pp，环比防退化）
   → exit 1；基线缺失时只告警不阻断（首轮用 ``--update-baseline`` 建立）；
5. stats 文件缺失/损坏、分母为 0（配置坏掉，未评估任何 mutant）→ exit 2。

用法::

    .venv/bin/python scripts/ci/gate_mutation_score.py
    .venv/bin/python scripts/ci/gate_mutation_score.py --min 95 --max-drop-pp 1.0
    .venv/bin/python scripts/ci/gate_mutation_score.py --update-baseline

退出码：0 = 通过；1 = 击杀率不达标或环比退化超阈；2 = stats 缺失/损坏/无评估。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATS = REPO_ROOT / "mutants" / "mutmut-cicd-stats.json"
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "ci" / "mutation_baseline.json"


def compute_score(stats: dict[str, int]) -> float | None:
    """击杀率百分数；分母为 0（未评估任何 mutant）返回 None。"""
    killed = int(stats.get("killed", 0))
    denominator = (
        killed
        + int(stats.get("survived", 0))
        + int(stats.get("timeout", 0))
        + int(stats.get("suspicious", 0))
    )
    if denominator == 0:
        return None
    return killed / denominator * 100


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    parser.add_argument("--min", dest="min_score", type=float, default=80.0)
    parser.add_argument("--max-drop-pp", type=float, default=2.0)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)

    try:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"stats 文件不可读：{args.stats}（{e}）——先跑 make mutation")
        return 2

    score = compute_score(stats)
    if score is None:
        print(f"分母为 0（killed+survived+timeout+suspicious），配置疑似坏掉：{args.stats}")
        return 2

    print(f"mutation score = {score:.2f}%（stats: {args.stats}）")

    if args.update_baseline:
        args.baseline.write_text(
            json.dumps({"score": round(score, 4)}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"基线已更新：{args.baseline} ← {score:.4f}")

    failed = False
    if score < args.min_score:
        print(f"击杀率 {score:.2f}% < 门槛 {args.min_score:.2f}%")
        failed = True

    if args.baseline.exists() and not args.update_baseline:
        try:
            baseline_score = float(json.loads(args.baseline.read_text(encoding="utf-8"))["score"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"基线文件损坏：{args.baseline}（{e}）")
            return 2
        drop = baseline_score - score
        if drop > args.max_drop_pp:
            print(f"环比退化 {drop:.2f}pp > 阈值 {args.max_drop_pp:.2f}pp（基线 {baseline_score:.2f}%）")
            failed = True
    elif not args.baseline.exists():
        print(f"提示：基线 {args.baseline} 不存在，跳过环比检查（--update-baseline 建立）")

    if failed:
        return 1
    print("mutation 门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
