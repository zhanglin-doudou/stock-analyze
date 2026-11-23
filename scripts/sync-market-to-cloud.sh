#!/usr/bin/env bash
# 本地 market 公共缓存 → 云主机数据库 一键同步（2026-11-23）。
#
#   make sync-market-to-cloud                       # 默认目标（本机 .env → 云主机）
#   ./scripts/sync-market-to-cloud.sh               # 等价
#   REMOTE=ubuntu@1.15.38.201 SSH_KEY=~/.ssh/local_macbp.pem \
#   REMOTE_DIR=~/projects/stock-analyze ./scripts/sync-market-to-cloud.sh
#
# 语义：market.* 是可再生公共缓存（无 RLS、无序列，见 AGENTS.md 板块铁律）。
# 本脚本在**单事务**里 TRUNCATE + 全量 COPY 替换（幂等，可反复执行），
# 完成后自动核对两端 8 张表行数，不一致即退出码 1。
#
# 连接：
#   本地 —— 根 .env 的 STOCK_DB_OWNER_*（默认 127.0.0.1:5432 / stock_owner）
#   云侧 —— SSH 到 REMOTE，读远端 infra/.env.prod 的 owner 口令，连远端 127.0.0.1:5432
set -euo pipefail

REMOTE="${REMOTE:-ubuntu@1.15.38.201}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/local_macbp.pem}"
REMOTE_DIR="${REMOTE_DIR:-~/projects/stock-analyze}"
LOCAL_ENV="${LOCAL_ENV:-.env}"
L_HOST="${STOCK_DB_HOST:-127.0.0.1}"
L_PORT="${STOCK_DB_PORT:-5432}"

# ── 前置检查 ──────────────────────────────────────────────
for bin in psql pg_dump gzip ssh sed; do
  command -v "$bin" >/dev/null || { echo "❌ 缺少命令：$bin"; exit 2; }
done
[ -f "$LOCAL_ENV" ] || { echo "❌ 缺少 $LOCAL_ENV（本地连接配置）"; exit 2; }
[ -f "${SSH_KEY/#\~/$HOME}" ] || { echo "❌ 缺少 SSH 密钥：$SSH_KEY"; exit 2; }

env_val() { sed -n "s/^$2=//p" "$1" | tail -1; }
L_USER="$(env_val "$LOCAL_ENV" STOCK_DB_OWNER_USER)"; L_USER="${L_USER:-stock_owner}"
L_PASS="$(env_val "$LOCAL_ENV" STOCK_DB_OWNER_PASSWORD)"; L_PASS="${L_PASS:-stock_owner_pw}"
L_NAME="$(env_val "$LOCAL_ENV" STOCK_DB_NAME)"; L_NAME="${L_NAME:-stock_analyze}"

# 远端连接信息（远端脚本内自取 .env.prod 口令，不经本地、不落日志）
REMOTE_SQL() {  # $1 = psql 前缀参数后的 SQL（-tAc）
  ssh -i "${SSH_KEY/#\~/$HOME}" -o ConnectTimeout=20 "$REMOTE" \
    "cd $REMOTE_DIR && PGPASSWORD=\"\$(sed -n 's/^STOCK_DB_OWNER_PASSWORD=//p' infra/.env.prod | tail -1)\" psql -h 127.0.0.1 -p 5432 -U stock_owner -d stock_analyze $1"
}

COUNT_SQL="select table_name || '=' || (xpath('/row/c/text()', query_to_xml('select count(*) as c from market.'||quote_ident(table_name), false,true,'')))[1]::text::int from information_schema.tables where table_schema='market' order by table_name"

# ── 本地基线（行数 + TRUNCATE 语句）────────────────────────
local_counts="$(PGPASSWORD="$L_PASS" psql -h "$L_HOST" -p "$L_PORT" -U "$L_USER" -d "$L_NAME" -tAc "$COUNT_SQL")"
[ -n "$local_counts" ] || { echo "❌ 本地 market schema 无表（$L_HOST:$L_PORT/$L_NAME）"; exit 2; }
echo "== 本地 market 行数 =="
echo "$local_counts"

truncate_sql="$(PGPASSWORD="$L_PASS" psql -h "$L_HOST" -p "$L_PORT" -U "$L_USER" -d "$L_NAME" -tAc \
  "select 'TRUNCATE ' || string_agg('market.'||quote_ident(table_name), ', ') || ' CASCADE;' from information_schema.tables where table_schema='market'")"

# ── 传输：TRUNCATE + data-only dump，单事务原子替换 ─────────
echo "== 传输中（本地 pg_dump → gzip → SSH → 云 psql，单事务）…"
{
  printf '%s\n' "$truncate_sql"
  PGPASSWORD="$L_PASS" pg_dump -h "$L_HOST" -p "$L_PORT" -U "$L_USER" -d "$L_NAME" \
    --schema=market --data-only --no-owner --no-privileges \
    | sed '/transaction_timeout/d'   # PG17+ 客户端兼容 PG16 云库
} | gzip | ssh -i "${SSH_KEY/#\~/$HOME}" -o ConnectTimeout=20 "$REMOTE" \
    "cd $REMOTE_DIR && gunzip | PGPASSWORD=\"\$(sed -n 's/^STOCK_DB_OWNER_PASSWORD=//p' infra/.env.prod | tail -1)\" psql -h 127.0.0.1 -p 5432 -U stock_owner -d stock_analyze --single-transaction -v ON_ERROR_STOP=1 -q -o /dev/null"

# ── 核对：两端行数必须一致 ─────────────────────────────────
remote_counts="$(REMOTE_SQL "-tAc \"$COUNT_SQL\"")"
echo "== 云库 market 行数（同步后）=="
echo "$remote_counts"
if [ "$local_counts" != "$remote_counts" ]; then
  echo "❌ 两端行数不一致，同步未达成（本地为准，可重跑本脚本）"
  exit 1
fi
echo "✅ market 缓存同步完成，两端行数一致"
echo "提示：若页面仍显示无数据（行情负缓存），在云主机重启 api：docker compose ... restart api"
