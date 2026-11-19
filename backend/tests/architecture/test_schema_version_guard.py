"""迁移 head 与代码 `SCHEMA_VERSION` 的静态守卫（不连数据库）。

背景：0019 把库升到 `0019_import_template_bill_types`，但 `stock_api.version`
仍写 `0018_trade_manual_revision`，导致 API 启动时的 schema 版本守卫直接退出、
前端看到所有接口 500。本用例不硬编码版本号，而是从迁移目录解析 revision 链算出
head，再与代码常量比对，避免下次迁移又漏改。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from stock_api.version import SCHEMA_VERSION

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS_DIR = REPO_ROOT / "backend" / "migrations" / "versions"

_ASSIGN_TEMPLATE = r"^{name}\s*(?::\s*[^=\n]+)?=\s*(.+?)\s*$"


@dataclass(frozen=True, slots=True)
class Migration:
    path: Path
    revision: str
    down_revision: str | tuple[str, ...] | None
    schema_version: str | None

    @property
    def down_revisions(self) -> set[str]:
        if self.down_revision is None:
            return set()
        if isinstance(self.down_revision, str):
            return {self.down_revision}
        return set(self.down_revision)


def _assignment(source: str, name: str) -> object:
    match = re.search(_ASSIGN_TEMPLATE.format(name=re.escape(name)), source, re.MULTILINE)
    assert match is not None, f"迁移文件缺少 {name} 声明"
    return ast.literal_eval(match.group(1))


def _load_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        revision = _assignment(source, "revision")
        assert isinstance(revision, str), f"{path.name}: revision 必须是字符串"
        down = _assignment(source, "down_revision")
        schema_match = re.search(
            _ASSIGN_TEMPLATE.format(name="SCHEMA_VERSION"), source, re.MULTILINE
        )
        schema_version = ast.literal_eval(schema_match.group(1)) if schema_match else None
        migrations.append(
            Migration(
                path=path,
                revision=revision,
                down_revision=down,  # type: ignore[arg-type]
                schema_version=schema_version if isinstance(schema_version, str) else None,
            )
        )
    assert migrations, "迁移目录为空"
    return migrations


def _heads(migrations: list[Migration]) -> list[Migration]:
    referenced = {down for item in migrations for down in item.down_revisions}
    return [item for item in migrations if item.revision not in referenced]


def test_migrations_head_matches_code_schema_version() -> None:
    migrations = _load_migrations()
    known = {item.revision for item in migrations}
    for item in migrations:
        missing = item.down_revisions - known
        assert not missing, f"{item.path.name}: down_revision 指向不存在的 {sorted(missing)}"

    heads = _heads(migrations)
    assert len(heads) == 1, (
        "迁移出现多个 head，请先合并分支：" + ", ".join(sorted(item.revision for item in heads))
    )
    head = heads[0]
    assert head.schema_version is not None, (
        f"迁移 {head.path.name} 没有声明 SCHEMA_VERSION，无法写入 platform.schema_version"
    )
    assert head.schema_version == SCHEMA_VERSION, (
        f"迁移已到 {head.schema_version}，代码里 SCHEMA_VERSION 还写着 {SCHEMA_VERSION}；"
        f"请同步 {Path('backend/services/api/src/stock_api/version.py')}"
    )
