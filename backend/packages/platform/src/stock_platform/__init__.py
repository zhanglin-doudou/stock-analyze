"""横切能力（platform）：配置、DB/租户、认证、队列、outbox、观测。

`platform` 不被 `domain`/`core` 依赖；它只依赖 `contracts`。
"""

from stock_platform.config import Settings, build_dsn, get_settings
from stock_platform.db import Base, Database

__all__ = ["Base", "Database", "Settings", "build_dsn", "get_settings"]
