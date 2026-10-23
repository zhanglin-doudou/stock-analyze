"""配置（NFR-14）：全部来自环境变量 / `.env`，生产由密钥管理注入。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from stock_contracts.constants import ACCESS_TOKEN_TTL_SECONDS, DEFAULT_REFRESH_TTL_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[5]


def build_dsn(user: str, password: str, host: str, port: int, database: str) -> str:
    """统一 DSN 构造（asyncpg 方言）。"""
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    env: Literal["local", "test", "prod"] = Field("local", alias="STOCK_ENV")
    log_level: str = Field("INFO", alias="STOCK_LOG_LEVEL")

    # ── DB ────────────────────────────────────────────────
    db_host: str = Field("127.0.0.1", alias="STOCK_DB_HOST")
    db_port: int = Field(5433, alias="STOCK_DB_PORT")
    db_name: str = Field("stock_analyze", alias="STOCK_DB_NAME")
    db_owner_user: str = Field("stock_owner", alias="STOCK_DB_OWNER_USER")
    db_owner_password: str = Field("stock_owner_pw", alias="STOCK_DB_OWNER_PASSWORD")
    db_app_user: str = Field("stock_app", alias="STOCK_DB_APP_USER")
    db_app_password: str = Field("stock_app_pw", alias="STOCK_DB_APP_PASSWORD")
    db_owner_dsn_override: str | None = Field(None, alias="STOCK_DB_OWNER_DSN")
    db_app_dsn_override: str | None = Field(None, alias="STOCK_DB_APP_DSN")
    db_pool_size: int = Field(10, alias="STOCK_DB_POOL_SIZE")
    db_max_overflow: int = Field(10, alias="STOCK_DB_MAX_OVERFLOW")
    db_echo: bool = Field(False, alias="STOCK_DB_ECHO")

    # ── 认证 ──────────────────────────────────────────────
    jwt_secret: str = Field("dev-only-insecure-secret-change-me", alias="STOCK_JWT_SECRET")
    jwt_algorithm: str = "HS256"
    access_ttl_seconds: int = Field(ACCESS_TOKEN_TTL_SECONDS, alias="STOCK_ACCESS_TTL_SECONDS")
    refresh_ttl_seconds: int = Field(DEFAULT_REFRESH_TTL_SECONDS, alias="STOCK_REFRESH_TTL_SECONDS")
    argon2_time_cost: int = Field(2, alias="STOCK_ARGON2_TIME_COST")

    # ── 外部依赖 ──────────────────────────────────────────
    market_mode: Literal["replay", "live"] = Field("replay", alias="MARKET_MODE")
    # 行情 live 模式（设计见 docs/architecture/2026-10-22-live-market-data-source.md §7）
    market_live_source: Literal["akshare"] = Field("akshare", alias="MARKET_LIVE_SOURCE")
    # v1 只落不复权日线（真实市价）：估值口径需要真实成交价，qfq 时变且会重复计入分红。
    market_live_adjust: Literal[""] = Field("", alias="MARKET_LIVE_ADJUST")
    market_live_timeout_seconds: float = Field(10.0, alias="MARKET_LIVE_TIMEOUT_SECONDS")
    market_live_cache_enabled: bool = Field(True, alias="MARKET_LIVE_CACHE_ENABLED")
    market_live_cache_ttl_days: int = Field(30, alias="MARKET_LIVE_CACHE_TTL_DAYS")
    market_live_negative_ttl_days: int = Field(1, alias="MARKET_LIVE_NEGATIVE_TTL_DAYS")
    market_live_tail_refetch_days: int = Field(5, alias="MARKET_LIVE_TAIL_REFETCH_DAYS")
    market_live_max_concurrency: int = Field(2, alias="MARKET_LIVE_MAX_CONCURRENCY")
    market_live_retry_attempts: int = Field(2, alias="MARKET_LIVE_RETRY_ATTEMPTS")
    market_live_retry_backoff_seconds: float = Field(1.5, alias="MARKET_LIVE_RETRY_BACKOFF_SECONDS")
    llm_provider: Literal["none", "glm", "local"] = Field("none", alias="LLM_PROVIDER")

    # ── 运维 ──────────────────────────────────────────────
    readyz_timeout_seconds: float = Field(2.0, alias="STOCK_READYZ_TIMEOUT_SECONDS")
    data_dir: Path = Field(REPO_ROOT / "var", alias="STOCK_DATA_DIR")
    fixtures_dir: Path = Field(REPO_ROOT / "fixtures", alias="STOCK_FIXTURES_DIR")

    # ── 计算得到 ──────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_local(self) -> bool:
        return self.env in ("local", "test")

    def app_dsn(self) -> str:
        """应用运行时 DSN（stock_app：非 owner、无 BYPASSRLS）。"""
        if self.db_app_dsn_override:
            return self.db_app_dsn_override
        return build_dsn(
            self.db_app_user, self.db_app_password, self.db_host, self.db_port, self.db_name
        )

    def owner_dsn(self) -> str:
        """迁移/DDL DSN（stock_owner）。"""
        if self.db_owner_dsn_override:
            return self.db_owner_dsn_override
        return build_dsn(
            self.db_owner_user, self.db_owner_password, self.db_host, self.db_port, self.db_name
        )

    def sync_owner_dsn(self) -> str:
        """Alembic 离线/同步场景（psycopg 方言）。"""
        return self.owner_dsn().replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
