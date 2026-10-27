"""G8 金标审批门禁：MANIFEST sha256 + definition_version + 人工审批（§8.11 #8）。

判据（全部失败即 exit 1）：
1. 每个 ``golden*.json`` 的 ``metric_id`` 都在 ``MANIFEST.definition_versions`` 中，
   且 ``definition_version`` 与 MANIFEST 钉住版本一致；
2. ``fixture.id`` 在 ``MANIFEST.fixtures`` 中登记，且对应模块文件的 sha256 与
   MANIFEST 记录一致（夹具被改动必须同时更新 MANIFEST，属人工审批动作）；
3. ``review.author`` 非空；``review.approved`` 为 true、``reviewer`` 与
   ``approved_at`` 非空（人工审批不可由 Agent 自签，见 AGENTS.md HITL）；
4. golden 覆盖全部 18 个 metric_id（不多不少）。

无 DB、无网络。``--golden-dir/--manifest/--tests-root`` 供元测试注入临时目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_DIR = REPO_ROOT / "backend/tests/golden"
DEFAULT_TESTS_ROOT = REPO_ROOT / "backend/tests"
DEFAULT_MANIFEST = DEFAULT_GOLDEN_DIR / "MANIFEST.json"


def emit(*parts: object) -> None:
    """统一输出（ruff T20 禁止散落的 print）。"""
    sys.stdout.write(" ".join(str(part) for part in parts) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise SystemExit(f"MANIFEST schema_version 必须为 1：{path}")
    if not isinstance(payload.get("fixtures"), dict):
        raise SystemExit(f"MANIFEST 缺 fixtures：{path}")
    if not isinstance(payload.get("definition_versions"), dict):
        raise SystemExit(f"MANIFEST 缺 definition_versions：{path}")
    return payload


def iter_golden(golden_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    cases: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(golden_dir.rglob("golden*.json")):
        if path.name == "MANIFEST.json":
            continue
        cases.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return cases


def validate_case(
    path: Path,
    payload: dict[str, Any],
    *,
    manifest: dict[str, Any],
    tests_root: Path,
) -> list[str]:
    """校验单份 golden；返回错误清单（空 = 通过）。"""
    errors: list[str] = []
    name = path.parent.name if path.parent.name else str(path)
    metric_id = payload.get("metric_id")
    version = payload.get("definition_version")

    pinned = manifest["definition_versions"].get(metric_id)
    if pinned is None:
        errors.append(f"{name}: metric_id {metric_id!r} 未在 MANIFEST.definition_versions 登记")
    elif version != pinned:
        errors.append(f"{name}: definition_version {version!r} != MANIFEST {pinned!r}")

    fixture_id = (payload.get("fixture") or {}).get("id")
    fixture = manifest["fixtures"].get(fixture_id)
    if fixture is None:
        errors.append(f"{name}: fixture {fixture_id!r} 未在 MANIFEST.fixtures 登记")
    else:
        module_path = tests_root / str(fixture["module"])
        if not module_path.exists():
            errors.append(f"{name}: 夹具模块不存在 {fixture['module']}")
        else:
            actual = sha256_file(module_path)
            if actual != fixture["sha256"]:
                errors.append(
                    f"{name}: fixture {fixture_id} sha256 不一致 "
                    f"（{actual[:12]}… != MANIFEST {str(fixture['sha256'])[:12]}…）"
                )

    review = payload.get("review") or {}
    if not review.get("author"):
        errors.append(f"{name}: review.author 为空")
    if review.get("approved") is not True:
        errors.append(f"{name}: 未审批（review.approved != true，待人工 reviewer 签署）")
    if not review.get("reviewer"):
        errors.append(f"{name}: review.reviewer 为空")
    if not review.get("approved_at"):
        errors.append(f"{name}: review.approved_at 为空")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="金标审批门禁")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--tests-root", type=Path, default=DEFAULT_TESTS_ROOT)
    args = parser.parse_args(argv)

    emit("== G8 金标审批门禁：MANIFEST sha256 + version + 人工审批 ==")
    manifest = load_manifest(args.manifest)
    cases = iter_golden(args.golden_dir)
    emit(
        f"golden {len(cases)} 份 / MANIFEST 指标 {len(manifest['definition_versions'])} 个 / "
        f"夹具 {len(manifest['fixtures'])} 个"
    )

    failures: list[str] = []
    seen: set[str] = set()
    for path, payload in cases:
        metric_id = str(payload.get("metric_id"))
        seen.add(metric_id)
        errors = validate_case(path, payload, manifest=manifest, tests_root=args.tests_root)
        review = payload.get("review") or {}
        mark = "✓" if not errors else "✗"
        emit(
            f"  [{mark}] {path.parent.name}/{path.name} "
            f"{metric_id}@{payload.get('definition_version')} "
            f"approved={review.get('approved')} reviewer={review.get('reviewer')}"
        )
        for error in errors:
            emit(f"      - {error}")
        failures.extend(errors)

    registered = set(manifest["definition_versions"])
    if seen != registered:
        missing = sorted(registered - seen)
        extra = sorted(seen - registered)
        if missing:
            failures.append("golden 未覆盖指标：" + "、".join(missing))
        if extra:
            failures.append("golden 含 MANIFEST 未登记指标：" + "、".join(extra))

    if failures:
        emit(f"❌ G8 金标审批门禁失败：{len(failures)} 项")
        return 1
    emit(f"✅ G8 金标审批门禁通过：{len(cases)} 份 golden 全部已审批且版本/sha256 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
