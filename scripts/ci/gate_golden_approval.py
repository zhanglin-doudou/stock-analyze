"""G8 金标审批门禁：schema + MANIFEST sha256 + 版本 + 人工审批 + 覆盖矩阵（§8.11 #8）。

两种模式：
- ``--mode structure``（PR-fast，无人工依赖）：schema（复用 ``golden.loader``）、
  definition_version、fixture sha256、golden 文件 sha256 与 MANIFEST 一致；
- ``--mode approval``（nightly/release，默认）：额外校验人工审批字段（approved/reviewer/
  approved_at、reviewer != author）、expected_source 类型，以及 ``COVERAGE.json`` 的
  required 场景全覆盖与状态/样本矩阵断言。

判据（任一失败即 exit 1）：
1. 每份 golden 通过 loader schema（缺键/未知键/float/状态矩阵矛盾一律失败）；
2. metric_id 在 MANIFEST.definition_versions 中且版本一致；
3. fixture.id 在 MANIFEST.fixtures 登记，夹具模块 sha256 一致；
4. golden 文件 sha256 与 MANIFEST.goldens 一致（审批绑定内容，改值必须重走审批）；
5. golden 覆盖 MANIFEST 的全部 metric_id（不多不少）；
6. approval 模式：review.approved=true、reviewer/approved_at 非空且 reviewer != author；
7. approval 模式：COVERAGE.json 的 required 场景均有对应 golden，且 expect_* 断言成立。

无 DB、无网络。``--golden-dir/--manifest/--tests-root/--coverage`` 供元测试注入临时目录。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_DIR = REPO_ROOT / "backend/tests/golden"
DEFAULT_TESTS_ROOT = REPO_ROOT / "backend/tests"
DEFAULT_MANIFEST = DEFAULT_GOLDEN_DIR / "MANIFEST.json"
DEFAULT_COVERAGE = DEFAULT_GOLDEN_DIR / "COVERAGE.json"

MODE_STRUCTURE = "structure"
MODE_APPROVAL = "approval"


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
    if not isinstance(payload, dict):
        raise SystemExit(f"MANIFEST 顶层必须是对象：{path}")
    if payload.get("schema_version") != 1:
        raise SystemExit(f"MANIFEST schema_version 必须为 1：{path}")
    for key in ("fixtures", "definition_versions", "goldens"):
        if not isinstance(payload.get(key), dict):
            raise SystemExit(f"MANIFEST 缺 {key}：{path}")
    return cast(dict[str, Any], payload)


def load_coverage(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"COVERAGE 顶层必须是对象：{path}")
    if payload.get("schema_version") != 1:
        raise SystemExit(f"COVERAGE schema_version 必须为 1：{path}")
    if not isinstance(payload.get("required"), list):
        raise SystemExit(f"COVERAGE 缺 required：{path}")
    return cast(dict[str, Any], payload)


def _loader_module() -> ModuleType:
    """复用 ``backend/tests/golden/loader.py``，避免 gate 与 pytest 的 schema 双写。"""
    path = DEFAULT_GOLDEN_DIR / "loader.py"
    spec = importlib.util.spec_from_file_location("golden_loader", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 loader：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def iter_golden(golden_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(golden_dir.rglob("golden*.json"))
        if path.name != "MANIFEST.json" and "_skeleton" not in path.parts
    ]


def _relative(path: Path, golden_dir: Path) -> str:
    return path.relative_to(golden_dir).as_posix()


def validate_structure(
    path: Path,
    case: Any,
    *,
    manifest: dict[str, Any],
    tests_root: Path,
    golden_dir: Path,
) -> list[str]:
    errors: list[str] = []
    rel = _relative(path, golden_dir)
    name = f"{path.parent.name}/{path.name}"

    pinned = manifest["definition_versions"].get(case.metric_id)
    if pinned is None:
        errors.append(
            f"{name}: metric_id {case.metric_id!r} 未在 MANIFEST.definition_versions 登记"
        )
    elif case.definition_version != pinned:
        errors.append(
            f"{name}: definition_version {case.definition_version!r} != MANIFEST {pinned!r}"
        )

    fixture = manifest["fixtures"].get(case.fixture_id)
    if fixture is None:
        errors.append(f"{name}: fixture {case.fixture_id!r} 未在 MANIFEST.fixtures 登记")
    else:
        module_path = tests_root / str(fixture["module"])
        if not module_path.exists():
            errors.append(f"{name}: 夹具模块不存在 {fixture['module']}")
        else:
            actual = sha256_file(module_path)
            if actual != fixture["sha256"]:
                errors.append(
                    f"{name}: fixture {case.fixture_id} sha256 不一致 "
                    f"（{actual[:12]}… != MANIFEST {str(fixture['sha256'])[:12]}…）"
                )

    pinned_hash = manifest["goldens"].get(rel)
    actual_hash = sha256_file(path)
    if pinned_hash is None:
        errors.append(
            f"{name}: golden 文件未在 MANIFEST.goldens 登记（新增/改值必须更新 MANIFEST）"
        )
    elif pinned_hash != actual_hash:
        errors.append(
            f"{name}: golden sha256 不一致（{actual_hash[:12]}… != MANIFEST {str(pinned_hash)[:12]}…；"
            "审批后改值必须重走人工签署）"
        )
    return errors


def validate_approval(case: Any, *, name: str) -> list[str]:
    errors: list[str] = []
    review = case.review
    if review.get("approved") is not True:
        errors.append(f"{name}: 未审批（review.approved != true，待人工 reviewer 签署）")
    if not review.get("reviewer"):
        errors.append(f"{name}: review.reviewer 为空")
    if not review.get("approved_at"):
        errors.append(f"{name}: review.approved_at 为空")
    elif isinstance(review.get("approved_at"), str):
        try:
            candidate = (
                review["approved_at"][:-1] + "+00:00"
                if review["approved_at"].endswith("Z")
                else review["approved_at"]
            )
            datetime.fromisoformat(candidate)
        except ValueError:
            errors.append(f"{name}: review.approved_at 不是 ISO8601")
    if review.get("reviewer") and review.get("reviewer") == review.get("author"):
        errors.append(f"{name}: reviewer 不能等于 author（禁止自签）")
    kind = case.expected_source.get("kind")
    if kind not in {"hand_calc", "oracle", "hand_calc+oracle"}:
        errors.append(f"{name}: expected_source.kind 非法 {kind!r}")
    return errors


def validate_coverage(
    coverage: dict[str, Any],
    *,
    cases_by_rel: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for item in coverage["required"]:
        if not isinstance(item, dict):
            errors.append("COVERAGE.required 条目必须是对象")
            continue
        scenario_id = item.get("id")
        metric_id = item.get("metric_id")
        rel = item.get("file")
        if not isinstance(scenario_id, str) or not scenario_id:
            errors.append("COVERAGE 条目缺 id")
            continue
        if scenario_id in seen_ids:
            errors.append(f"COVERAGE: 重复 scenario id {scenario_id}")
        seen_ids.add(scenario_id)
        if not isinstance(metric_id, str) or not metric_id:
            errors.append(f"COVERAGE.{scenario_id}: 缺 metric_id")
            continue
        if not isinstance(rel, str) or not rel:
            errors.append(f"COVERAGE.{scenario_id}: 缺 file")
            continue
        case = cases_by_rel.get(rel)
        if item.get("hitl"):
            errors.append(
                f"COVERAGE.{scenario_id}: HITL 未解决（{item['hitl']}），不得签署；"
                "人工拍板后删除 COVERAGE.hitl 标记并重出 golden"
            )
        if case is None:
            if not item.get("hitl"):
                errors.append(f"COVERAGE.{scenario_id}: 缺 golden 文件 {rel}")
            continue
        if case.metric_id != metric_id:
            errors.append(f"COVERAGE.{scenario_id}: metric_id {case.metric_id!r} != {metric_id!r}")
            continue
        if "expect_status" in item and case.status != item["expect_status"]:
            errors.append(
                f"COVERAGE.{scenario_id}: status={case.status!r} != 期望 {item['expect_status']!r}"
                f"（{rel}）"
            )
        if "expect_sample_status" in item and case.sample_status != item["expect_sample_status"]:
            errors.append(
                f"COVERAGE.{scenario_id}: sample_status={case.sample_status!r} "
                f"!= 期望 {item['expect_sample_status']!r}（{rel}）"
            )
        if "expect_value_present" in item:
            present = case.value_units(precision=8) is not None
            if present != bool(item["expect_value_present"]):
                errors.append(
                    f"COVERAGE.{scenario_id}: value_present={present} "
                    f"!= 期望 {item['expect_value_present']}（{rel}）"
                )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="金标审批门禁")
    parser.add_argument("--mode", choices=(MODE_STRUCTURE, MODE_APPROVAL), default=MODE_APPROVAL)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--tests-root", type=Path, default=DEFAULT_TESTS_ROOT)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    args = parser.parse_args(argv)

    mode = args.mode
    emit(f"== G8 金标门禁（{mode}）：schema + MANIFEST sha256 + version + 覆盖 ==")
    loader = _loader_module()
    try:
        manifest = load_manifest(args.manifest)
    except (OSError, SystemExit, json.JSONDecodeError) as exc:
        emit(f"❌ G8 金标门禁（{mode}）失败：MANIFEST 不可用：{exc}")
        return 1
    paths = iter_golden(args.golden_dir)
    emit(
        f"golden {len(paths)} 份 / MANIFEST 指标 {len(manifest['definition_versions'])} 个 / "
        f"夹具 {len(manifest['fixtures'])} 个"
    )

    failures: list[str] = []
    warnings: list[str] = []
    cases_by_rel: dict[str, Any] = {}
    seen: set[str] = set()
    for path in paths:
        name = f"{path.parent.name}/{path.name}"
        try:
            case = loader.load_case(path)
        except loader.GoldenFormatError as exc:
            failures.append(f"{name}: schema 非法：{exc}")
            emit(f"  [✗] {name} schema 非法：{exc}")
            continue
        cases_by_rel[_relative(path, args.golden_dir)] = case
        seen.add(case.metric_id)
        errors = validate_structure(
            path, case, manifest=manifest, tests_root=args.tests_root, golden_dir=args.golden_dir
        )
        if mode == MODE_APPROVAL:
            errors.extend(validate_approval(case, name=name))
        mark = "✓" if not errors else "✗"
        emit(
            f"  [{mark}] {name} {case.metric_id}@{case.definition_version} "
            f"status={case.status}/{case.sample_status} "
            f"approved={case.review.get('approved')} reviewer={case.review.get('reviewer')}"
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

    if mode == MODE_APPROVAL:
        try:
            coverage = load_coverage(args.coverage)
        except (OSError, SystemExit, json.JSONDecodeError) as exc:
            emit(f"❌ G8 金标门禁（approval）失败：COVERAGE 不可用：{exc}")
            return 1
        coverage_errors, coverage_warnings = validate_coverage(coverage, cases_by_rel=cases_by_rel)
        warnings.extend(coverage_warnings)
        failures.extend(coverage_errors)
        for error in coverage_errors:
            emit(f"  [✗] {error}")
        for warning in warnings:
            emit(f"  [warn] {warning}")

    if failures:
        emit(f"❌ G8 金标门禁（{mode}）失败：{len(failures)} 项")
        return 1
    emit(f"✅ G8 金标门禁（{mode}）通过：{len(paths)} 份 golden 结构/版本/sha256 一致")
    if mode == MODE_APPROVAL:
        emit(f"   覆盖矩阵 {len(coverage['required'])} 个场景全部有 golden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
