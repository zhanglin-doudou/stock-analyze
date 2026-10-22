"""本地对象存储（原始账单文件、导出包、备份）。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path


class LocalObjectStore:
    """单机卷实现；远程对象存储只需替换本类（端口见 ports.py）。"""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            raise ValueError(f"非法对象键: {key}")
        path = self._root / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def put_bytes(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._path(key).write_bytes, data)

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink)

    async def list_prefix(self, prefix: str) -> Sequence[str]:
        base = self._root
        if not base.exists():
            return ()

        def _walk() -> list[str]:
            return [
                str(p.relative_to(base))
                for p in sorted(base.rglob("*"))
                if p.is_file() and str(p.relative_to(base)).startswith(prefix)
            ]

        return await asyncio.to_thread(_walk)
