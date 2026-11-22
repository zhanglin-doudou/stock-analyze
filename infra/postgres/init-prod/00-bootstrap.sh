#!/bin/sh
# 生产库角色初始化：在**云主机 PostgreSQL:5432** 上手工执行（不再由 Docker 触发）。
# 与 dev 的 00-bootstrap.sql 的区别：密码全部来自环境变量，严禁硬编码。
#
#   STOCK_DB_OWNER_PASSWORD=... STOCK_DB_APP_PASSWORD=... STOCK_DB_BACKUP_PASSWORD=... \
#   PGHOST=<云主机内网 IP> PGPORT=5432 STOCK_DB_SUPERUSER=postgres \
#   sh infra/postgres/init-prod/00-bootstrap.sh
#
# 幂等：重复执行只做 ALTER ROLE 同步密码；轮换密码后应用侧改 .env.prod 即可。
set -eu

: "${STOCK_DB_OWNER_PASSWORD:?STOCK_DB_OWNER_PASSWORD 未设置}"
: "${STOCK_DB_APP_PASSWORD:?STOCK_DB_APP_PASSWORD 未设置}"
: "${STOCK_DB_BACKUP_PASSWORD:?STOCK_DB_BACKUP_PASSWORD 未设置}"
DB_NAME="${STOCK_DB_NAME:-stock_analyze}"

# 第一步：装扩展 + 建三角色 + 建库（幂等）
psql -v ON_ERROR_STOP=1 --username "${STOCK_DB_SUPERUSER:-postgres}" --dbname postgres \
  -v owner_pw="$STOCK_DB_OWNER_PASSWORD" \
  -v app_pw="$STOCK_DB_APP_PASSWORD" \
  -v backup_pw="$STOCK_DB_BACKUP_PASSWORD" \
  -v dbname="$DB_NAME" <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

SELECT format(
  'CREATE ROLE stock_owner LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE',
  :'owner_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_owner') \gexec

SELECT format(
  'CREATE ROLE stock_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'app_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_app') \gexec

SELECT format(
  'CREATE ROLE stock_backup LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
  :'backup_pw'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stock_backup') \gexec

-- 已存在角色同步密码（幂等；卷复用但密码已轮换时保持一致）
SELECT format('ALTER ROLE stock_owner PASSWORD %L', :'owner_pw') \gexec
SELECT format('ALTER ROLE stock_app PASSWORD %L', :'app_pw') \gexec
SELECT format('ALTER ROLE stock_backup PASSWORD %L', :'backup_pw') \gexec

SELECT format('CREATE DATABASE %I OWNER stock_owner', :'dbname')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'dbname') \gexec
EOSQL

# 第二步：在目标库装扩展并授权连接（表级授权由 Alembic 迁移负责）
psql -v ON_ERROR_STOP=1 --username "${STOCK_DB_SUPERUSER:-postgres}" --dbname "$DB_NAME" \
  -v dbname="$DB_NAME" <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
SELECT format('GRANT CONNECT ON DATABASE %I TO stock_app, stock_backup', :'dbname') \gexec
EOSQL

echo "init-prod: roles/database ready (db=$DB_NAME)"
