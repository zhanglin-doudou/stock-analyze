#!/bin/sh
# 同镜像多入口。所有分支都用 exec，确保 PID 1 是真实进程（信号/退出码正确）。
set -eu

case "${1:-api}" in
  api)
    shift
    exec uvicorn stock_api.app:create_app --factory \
      --host 0.0.0.0 --port "${PORT:-8000}" "$@"
    ;;
  worker)
    shift
    exec python -m stock_worker "$@"
    ;;
  migrate)
    shift
    exec alembic -c backend/alembic.ini upgrade head "$@"
    ;;
  cli)
    # 定时任务/运维命令：docker compose run --rm api cli sync-market ...
    shift
    exec python -m stock_api.cli "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
