-- 本机 PostgreSQL 引导脚本（无 Docker 时的替代路径）。
-- 用法：make db-bootstrap   （需超级用户，默认取当前登录用户）
--
-- 角色模型（Architecture §9.1 / §14.3）：
--   stock_owner ：schema/表 owner，Alembic 迁移使用
--   stock_app   ：应用运行时角色，非 owner、NOBYPASSRLS，仅 DML
\set ON_ERROR_STOP on

SELECT 'CREATE ROLE stock_owner LOGIN PASSWORD ''stock_owner_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_owner') \gexec

SELECT 'CREATE ROLE stock_app LOGIN PASSWORD ''stock_app_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_app') \gexec

-- 只读备份/校验角色（交付阶段 PITR 演练用）
SELECT 'CREATE ROLE stock_backup LOGIN PASSWORD ''stock_backup_pw'' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_backup') \gexec

\connect postgres
SELECT 'CREATE DATABASE ' || quote_ident(:'dbname') || ' OWNER stock_owner'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec

\connect :dbname
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
GRANT CONNECT ON DATABASE :"dbname" TO stock_app, stock_backup;
