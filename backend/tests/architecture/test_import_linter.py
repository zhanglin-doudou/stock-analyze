"""架构铁律：import-linter L1~L5 必须零违规。

规则声明在根 pyproject.toml 的 [tool.importlinter]，此处只断言 `lint-imports` 退出码为 0。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _lint_imports_executable() -> str:
    """优先用与解释器同目录的 lint-imports，否则回退 PATH。"""
    sibling = Path(sys.executable).parent / "lint-imports"
    if sibling.exists():
        return str(sibling)
    found = shutil.which("lint-imports")
    assert found, "未找到 lint-imports（应在 dev 依赖组 import-linter 中）"
    return found


def test_import_linter_contracts_pass() -> None:
    result = subprocess.run(
        [_lint_imports_executable()],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
