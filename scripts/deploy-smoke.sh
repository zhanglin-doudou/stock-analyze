#!/usr/bin/env bash
# 部署后冒烟：路由 / 探针 / 公网不可达端点 / 安全头（DEPLOYMENT P0-1.6、P0-2.2、P0-2.3）。
#
#   ./scripts/deploy-smoke.sh                    # 本地预演（http://localhost）
#   BASE_URL=https://xingst.site ./scripts/deploy-smoke.sh
#
# 退出码 0 = 全绿；1 = 有失败项（逐行打印）。
set -uo pipefail

BASE="${BASE_URL:-http://localhost}"
fail=0

check_status() {
  local path="$1" expected="$2" code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$BASE$path" 2>/dev/null || echo "000")
  if [ "$code" = "$expected" ]; then
    printf 'ok   %-30s %s\n' "$path" "$code"
  else
    printf 'FAIL %-30s %s (expected %s)\n' "$path" "$code" "$expected"
    fail=1
  fi
}

check_status / 200
check_status /healthz 200
check_status /readyz 200
check_status /api/v1/openapi.json 200
# 网关不路由 + web 显式 404：运维端点不得以 SPA fallback 形式“假可达”
check_status /metrics 404
check_status /docs 404
check_status /redoc 404

headers=$(curl -sS -o /dev/null -D - --max-time 10 "$BASE/" 2>/dev/null || true)
lower=$(printf '%s' "$headers" | tr 'A-Z' 'a-z')

for h in x-content-type-options x-frame-options referrer-policy content-security-policy; do
  if printf '%s' "$lower" | grep -q "^$h:"; then
    printf 'ok   header %s\n' "$h"
  else
    printf 'FAIL header %s missing\n' "$h"
    fail=1
  fi
done

if [[ "$BASE" == https://* ]]; then
  if printf '%s' "$lower" | grep -q '^strict-transport-security:'; then
    echo 'ok   header strict-transport-security'
  else
    echo 'FAIL header strict-transport-security missing'
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  echo "✅ smoke passed: $BASE"
else
  echo "❌ smoke failed: $BASE"
fi
exit "$fail"
