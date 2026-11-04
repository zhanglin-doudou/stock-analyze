"""配置（NFR-14）：全部来自环境变量 / `.env`，生产由密钥管理注入。"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from stock_contracts.constants import (
    ACCESS_TOKEN_TTL_SECONDS,
    DEFAULT_REFRESH_TTL_SECONDS,
    RF_ANNUAL_DEFAULT,
)

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
    # 默认只读本地 market.daily_bar：读路径绝不触发第三方行情；收盘后用
    # `make sync-market` 显式补缺口/当日。调试可设 false 恢复旧 read-through。
    market_live_cache_read_only: bool = Field(True, alias="MARKET_LIVE_CACHE_READ_ONLY")
    market_live_cache_ttl_days: int = Field(30, alias="MARKET_LIVE_CACHE_TTL_DAYS")
    market_live_negative_ttl_days: int = Field(1, alias="MARKET_LIVE_NEGATIVE_TTL_DAYS")
    market_live_tail_refetch_days: int = Field(5, alias="MARKET_LIVE_TAIL_REFETCH_DAYS")
    market_live_max_concurrency: int = Field(2, alias="MARKET_LIVE_MAX_CONCURRENCY")
    market_live_retry_attempts: int = Field(2, alias="MARKET_LIVE_RETRY_ATTEMPTS")
    market_live_retry_backoff_seconds: float = Field(1.5, alias="MARKET_LIVE_RETRY_BACKOFF_SECONDS")
    # replay 日线能力声明（C-28）：显式配置日线夹具；未配置时 replay 仅月频序列。
    market_bars_file: Path | None = Field(None, alias="MARKET_BARS_FILE")
    # ── 板块数据（2026-10-31 sector-data 设计）──────────────
    # replay/live；live 才允许 `make sync-sectors` 外呼，读路径恒只读缓存。
    sector_mode: Literal["replay", "live"] = Field("replay", alias="SECTOR_MODE")
    sector_fixtures_file: Path | None = Field(None, alias="SECTOR_FIXTURES_FILE")
    sector_cache_read_only: bool = Field(True, alias="SECTOR_CACHE_READ_ONLY")
    sector_live_max_concurrency: int = Field(6, alias="SECTOR_LIVE_MAX_CONCURRENCY")
    sector_live_timeout_seconds: float = Field(30.0, alias="SECTOR_LIVE_TIMEOUT_SECONDS")
    sector_live_retry_attempts: int = Field(2, alias="SECTOR_LIVE_RETRY_ATTEMPTS")
    sector_industry_level_default: int = Field(1, alias="SECTOR_INDUSTRY_LEVEL_DEFAULT")
    sector_coverage_ok_min_units: int = Field(95_000_000, alias="SECTOR_COVERAGE_OK_MIN_UNITS")
    sector_coverage_degraded_min_units: int = Field(
        90_000_000, alias="SECTOR_COVERAGE_DEGRADED_MIN_UNITS"
    )
    sector_benchmark_id: str = Field("hs300", alias="SECTOR_BENCHMARK_ID")
    sector_benchmark_max_age_days: int = Field(45, alias="SECTOR_BENCHMARK_MAX_AGE_DAYS")
    sector_industry_stale_days: int = Field(35, alias="SECTOR_INDUSTRY_STALE_DAYS")
    sector_concept_stale_days: int = Field(7, alias="SECTOR_CONCEPT_STALE_DAYS")
    # P0 性能（2026-11-03）：同花顺出站统一走全局限速器；下面两个固定间隔字段
    # 仅保留以兼容旧 .env，同步路径已不再读取（改为 SECTOR_THS_RATE_LIMIT_PER_SECOND）。
    sector_ths_call_interval_seconds: float = Field(0.0, alias="SECTOR_THS_CALL_INTERVAL_SECONDS")
    sector_ths_page_interval_seconds: float = Field(0.0, alias="SECTOR_THS_PAGE_INTERVAL_SECONDS")
    # 全局限速（所有 THS 页面/日线请求与重试共用）：8 req/s 是实测安全区；
    # 撞 403 熔断后按需下调；0 表示不限速（仅单测/离线排障用）。
    sector_ths_rate_limit_per_second: float = Field(8.0, alias="SECTOR_THS_RATE_LIMIT_PER_SECOND")
    # 同花顺登录态：只从环境注入，禁止写入仓库/日志（`repr=False` 防止误打印）。
    # 缺失或失效时同步 fail-fast（整批不落库），不保留匿名半截路径。
    sector_ths_cookie: str | None = Field(None, alias="SECTOR_THS_COOKIE", repr=False)
    sector_ths_ua: str | None = Field(None, alias="SECTOR_THS_UA")
    sector_sync_retry_attempts: int = Field(3, alias="SECTOR_SYNC_RETRY_ATTEMPTS")
    sector_flat_active_units: int = Field(100_000, alias="SECTOR_FLAT_ACTIVE_UNITS")
    # 2026-11-02 五因子归因（§4.3）：classification_lookahead=allow 的部署开关
    # （默认关闭；开启后允许用最早可用分类快照并标 degraded，属人工批准的例外）。
    sector_lookahead_enabled: bool = Field(False, alias="SECTOR_LOOKAHEAD_ENABLED")

    # 指标引擎：无风险利率（C-24，D-14）
    rf_annual: Decimal = Field(RF_ANNUAL_DEFAULT, alias="RF_ANNUAL")
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
