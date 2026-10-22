# 统一命令入口（Architecture 附录 B / DEV-PLAN 附录 B）。本地可验证优先。
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV := uv run
PY := $(UV) python
ALEMBIC := $(UV) alembic -c backend/alembic.ini
COMPOSE := docker compose -f infra/compose/docker-compose.dev.yml
ENV_FILE := .env
SUPERUSER ?= $(shell whoami)
DB_NAME ?= stock_analyze
DB_PORT ?= 5433

.PHONY: help
help:
	@echo "── 环境 ──────────────────────────────────────────────"
	@echo "  make db-up          启动开发用 PostgreSQL（docker compose，5433）"
	@echo "  make db-bootstrap   在本机 PostgreSQL 上创建角色/库（无 Docker 时的替代）"
	@echo "  make db-down        停止 DB"
	@echo "  make db-reset       重建本地数据库（销毁并重新初始化）"
	@echo "  make migrate        执行 Alembic 迁移（建 schema + 表 + RLS）"
	@echo "── 运行 ──────────────────────────────────────────────"
	@echo "  make dev            起 api(热重载) + worker + web"
	@echo "  make api            仅起 FastAPI"
	@echo "  make worker         仅起任务消费者"
	@echo "  make web            仅起 Vite dev server"
	@echo "── 数据 ──────────────────────────────────────────────"
	@echo "  make seed-tenants   造 A/B/admin 三个账号与账单"
	@echo "  make seed-demo      造演示数据（含行情回放）"
	@echo "── 质量 ──────────────────────────────────────────────"
	@echo "  make lint           ruff + eslint"
	@echo "  make typecheck      mypy + tsc + import-linter"
	@echo "  make test           快速单测（无外部依赖）"
	@echo "  make test-core      core 内核单测 + 覆盖门禁"
	@echo "  make test-api       API 验收套件（一期端点）"
	@echo "  make test-rls       RLS 隔离集成测试"
	@echo "  make test-isolation 越权负向矩阵"
	@echo "  make api-check      契约完整性检查（示例/错误码/operationId）"
	@echo "  make gate-endpoint  一期 30 端点实现/测试引用覆盖门禁"
	@echo "  make gate-tenant    受保护端点越权（跨租户/401/admin）覆盖门禁"
	@echo "  make gate-rls       租户表 RLS 策略覆盖门禁"
	@echo "  make sdk            由 openapi.json 生成前端 SDK"
	@echo "  make verify-p1      一期全部断言（一条命令全绿）"
	@echo "  make demo-p1        5 分钟演示脚本"

# ── 环境 ────────────────────────────────────────────────────
.PHONY: env
env:
	@test -f $(ENV_FILE) || (cp infra/.env.example $(ENV_FILE) && echo "已生成 .env（本地默认值）")

.PHONY: db-up
db-up:
	@echo "→ 启动 docker compose PostgreSQL"
	@$(COMPOSE) up -d db
	@$(COMPOSE) exec -T db pg_isready -U postgres -d postgres -t 30

.PHONY: db-bootstrap
db-bootstrap:
	@echo "→ 在本机 PostgreSQL 创建角色与库（无 Docker 时的替代路径）"
	@psql -v ON_ERROR_STOP=1 -U $(SUPERUSER) -d postgres -v dbname=$(DB_NAME) -f scripts/db_bootstrap.sql

.PHONY: db-down
db-down:
	@$(COMPOSE) down

.PHONY: db-reset
db-reset:
	@$(COMPOSE) down -v
	@$(MAKE) db-up
	@$(MAKE) migrate

.PHONY: migrate
migrate:
	@$(ALEMBIC) upgrade head
	@$(ALEMBIC) current

.PHONY: migrate-check
migrate-check:
	@$(ALEMBIC) check

# ── 运行 ────────────────────────────────────────────────────
.PHONY: dev
dev:
	@echo "→ api:    http://127.0.0.1:8000/api/v1/openapi.json"
	@echo "→ web:    http://127.0.0.1:5173"
	@$(MAKE) -j3 api worker web

.PHONY: api
api:
	@$(PY) -m uvicorn stock_api.app:create_app --factory --host 127.0.0.1 --port 8000 --reload

.PHONY: worker
worker:
	@$(PY) -m stock_worker

.PHONY: web
web:
	@cd frontend && pnpm --filter web dev

# ── 数据 ────────────────────────────────────────────────────
.PHONY: seed-tenants
seed-tenants:
	@$(PY) -m stock_api.cli seed-tenants

.PHONY: seed-demo
seed-demo:
	@$(PY) -m stock_api.cli seed-demo

# ── 质量 ────────────────────────────────────────────────────
.PHONY: lint
lint:
	@$(UV) ruff check backend
	@$(UV) ruff format --check backend
	@cd frontend && pnpm -r lint

.PHONY: format
format:
	@$(UV) ruff check --fix backend
	@$(UV) ruff format backend

.PHONY: typecheck
typecheck:
	@$(UV) mypy backend/packages backend/services
	@$(UV) lint-imports
	@cd frontend && pnpm -r typecheck

.PHONY: test
test:
	@$(UV) pytest -m "not integration and not api" -q

.PHONY: test-core
test-core:
	@$(UV) pytest backend/tests/unit/core --cov=stock_core --cov-fail-under=100 -q

.PHONY: test-contracts
test-contracts:
	@$(UV) pytest backend/tests/unit/contracts -q

.PHONY: test-api
test-api:
	@$(UV) pytest backend/tests/api -q

.PHONY: test-integration
test-integration:
	@$(UV) pytest -m "integration" -q

.PHONY: test-rls
test-rls:
	@$(UV) pytest backend/tests/integration/test_rls_isolation.py -q

.PHONY: test-isolation
test-isolation:
	@$(UV) pytest -m "isolation" -q

.PHONY: test-architecture
test-architecture:
	@$(UV) pytest backend/tests/architecture -q

.PHONY: api-check
api-check:
	@$(PY) -m stock_api.cli api-check

.PHONY: api-snapshot
api-snapshot:
	@$(PY) -m stock_api.cli api-snapshot

.PHONY: sdk
sdk:
	@$(PY) -m stock_api.cli sdk

.PHONY: api-coverage
api-coverage:
	@$(PY) -m stock_api.cli api-coverage

.PHONY: gate-endpoint
gate-endpoint:
	@$(PY) scripts/ci/gate_endpoint_coverage.py

.PHONY: gate-tenant
gate-tenant:
	@$(PY) scripts/ci/gate_tenant_coverage.py

.PHONY: gate-rls
gate-rls:
	@$(PY) scripts/ci/gate_rls_coverage.py

.PHONY: verify-p1
verify-p1:
	@$(PY) -m stock_api.cli verify-p1

.PHONY: demo-p1
demo-p1:
	@$(PY) -m stock_api.cli demo-p1
