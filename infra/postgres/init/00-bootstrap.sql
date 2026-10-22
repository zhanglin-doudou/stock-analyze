-- 由 docker-entrypoint-initdb.d 在首次初始化时执行（仅当使用 Docker PG 时）。
-- 若使用本机 PostgreSQL，请改用 scripts/db_bootstrap.sql（make db-bootstrap）。
--
-- 角色模型（Architecture §9.1 / §14.3）：
--   stock_owner  ：schema/表 owner，用于 Alembic 迁移（DDL）
--   stock_app    ：应用运行时角色，非 owner、无 BYPASSRLS，仅 DML 权限

-- 第一步：在 postgres 库装扩展 + 建角色 + 建目标库
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SELECT 'CREATE ROLE stock_owner LOGIN PASSWORD ''stock_owner_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_owner') \gexec

SELECT 'CREATE ROLE stock_app LOGIN PASSWORD ''stock_app_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_app') \gexec

SELECT 'CREATE ROLE stock_backup LOGIN PASSWORD ''stock_backup_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_backup') \gexec

SELECT 'CREATE DATABASE stock_analyze OWNER stock_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'stock_analyze') \gexec

-- 第二步：连接目标库，在那里也装一份扩展（Alembic 迁移连的是这个库）
\connect stock_analyze
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
GRANT CONNECT ON DATABASE stock_analyze TO stock_app;
