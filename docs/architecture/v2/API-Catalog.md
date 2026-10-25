# 个人交易复盘平台 · API 目录（v2.0，可验收基线）

> 状态：已冻结待评审 ｜ 日期：2026-10-19 ｜ 需求基线：[PRD-v2.md](../../prd/PRD-v2.md)（28 条 FR：L1-L8 / A1-A4 / R1-R8 / D1-D8）
> 定位：**端点清单 + 验收判据的权威位置**；契约单源为 FastAPI 生成的 OpenAPI 3.1，快照提交 `openapi/openapi.json`。
> 骨架（不可改名）：nginx（TLS/静态 SPA/`/api` 反代，同源）+ FastAPI api（≥2 副本、无状态）+ worker（`platform.job` 队列、`FOR UPDATE SKIP LOCKED`）+ PostgreSQL 16（`ledger`/`review`/`platform` + RLS）。

## §0 总则

### 0.1 Base URL 与版本
- 生产 `https://{host}/api/v1`；nginx 同源反代，**无 CORS、无跨域 Cookie**。运维端点根路径：`/healthz`、`/readyz`、`/version`、`/metrics`；契约元数据：`/api/v1/openapi.json`、`/api/v1/errors`。
- URL 只承载 API major；破坏性变更升 `/api/v2`，v1 **保留 N-1 至少一个大版本**（≥90 天弃用窗口）；新增可选字段/端点/枚举值不升版本，客户端必须忽略未知字段。`definition_version`（口径 `MAJOR.MINOR`）与 API 版本正交。
### 0.2 认证（3 种，互斥）
| 方式 | 凭证形态 | 传递位置 | 有效期 | 适用 |
|---|---|---|---|---|
| 会话 access JWT | HS256（`sub/role/aud=web/jti`） | `Authorization: Bearer <jwt>` | 15min（`exp-iat=900`） | SPA |
| Refresh token | opaque，DB 存 SHA-256，**轮换 + 复用检测** | `POST /auth/refresh` body | 30d 滑动 | 续期 |
| PAT | `pat_` + 43 字符，仅创建时返回一次，DB 存哈希 | `Authorization: Bearer pat_...` | 默认 90d，最长 365d | 脚本/CI |
- 同一请求只允许一种凭证，出现两种 → `401 MULTIPLE_CREDENTIALS`；所有 401 必须带 `WWW-Authenticate: Bearer realm="api"`。
- PAT scope `read`/`write`：read 仅可 `GET/HEAD`，写端点必须 write；PAT 继承用户角色。管理端点仅 `role=admin`，且 **admin 不可读交易明细**（`403 ADMIN_CANNOT_READ_TRADES`）。
- 越权统一：单资源跨用户 → **404**（不泄露存在性）；批量/混合参数接口混入他人 ID → **403** `FOREIGN_RESOURCE_IN_BATCH`，不回显他人字段。
### 0.3 请求头
- `Authorization` 受保护端点必填；`X-Request-Id`：UUIDv7/ULID，可选，缺失服务端生成，贯穿日志/审计/错误体。
- `Idempotency-Key`：16~128 可见字符，作用域 `(user_id,key)`，见 0.6；`If-Match`：PATCH/PUT 必填，值=资源 ETag `"<version>"`，不匹配 → 409 `VERSION_CONFLICT`。
- `If-None-Match`：条件 GET，命中 → 304 空 body；`Accept-Language`：`zh-CN`（默认）/`en-US`，仅影响 `user_message`/`conclusion` 文案；`Last-Event-ID`：SSE 续传（§5）。
- 响应侧：`ETag`、`X-RateLimit-Limit/Remaining/Reset`、`Retry-After`，见 0.8。
### 0.4 分页 / 过滤 / 排序 / 字段裁剪
- **游标分页**：`limit`（1~200，默认 50）+ `cursor`；响应 `{items[], next_cursor, has_more}`，`next_cursor=null` 结束；禁止 `offset`；游标绑定 `(user_id, 过滤/排序指纹)`，篡改/跨用户/超 24h → `400 INVALID_CURSOR`。
- **过滤**：白名单参数；未知参数 → `400 UNKNOWN_QUERY_PARAM`；区间半开 `[from,to)`。**排序**：`sort=field`/`-field` 白名单，非法 → `400 INVALID_SORT_FIELD`，追加 `id` 稳定 tiebreaker。
- **字段裁剪**：`fields=id,trade_date` 只返所列字段+恒返 `id`；未知字段 → `400 INVALID_FIELD_SELECTION`；敏感字段不可裁剪返回。总数仅 `include_total=true` 给近似 `total_estimate`。
### 0.5 统一错误模型（RFC 7807 + 扩展）
`Content-Type: application/problem+json`；字段 `type/title/status/detail/instance` + `code/user_message/what_to_do/retryable/request_id/errors[]`：
```json
{"type":"https://api.example.com/problems/v1/version-conflict","title":"Version conflict","status":409,"instance":"/api/v1/trades/trd_01J9Z6",
 "code":"VERSION_CONFLICT","user_message":"这条数据刚被改过。","what_to_do":"刷新后重新提交。","retryable":false,"request_id":"01J9Z6..."}
```
- 状态码语义：400 参数/游标/排序；401 未认证；403 权限/scope/admin 禁区；404 不存在或跨用户；409 状态/版本/幂等冲突；410 过期/已撤销；413/415 文件；422 语义校验；429 限流；500 未预期；503 依赖不可用。
- 200 级「降级码」（`SAMPLE_INSUFFICIENT`、`NAV_UNRELIABLE`、`IMPORT_MISSING_CASH_BALANCE`）不是失败：以 `sample_status/status/warnings[]` 表达，无错误体、无结论文案。
### 0.6 幂等
- **必需**（所有 202 异步写 + 改变资产的 POST）：同 key + 同 body → 重放首次响应（**同一 job_id/同一 body**，`Idempotency-Replayed: true`），副作用只发生一次；同 key 不同 body → `422 IDEMPOTENCY_KEY_REUSED`；首次仍在执行 → `409 IDEMPOTENCY_IN_PROGRESS`（附 `job_id`）。
- **支持**（PATCH/PUT/撤销/恢复/分享/建规则）：同 key 重放首次结果，不带 key 时按 HTTP 语义。**不适用**（`POST /tokens`、register/login、匿名分享）：token 明文只出现一次，超时用 `GET /tokens` 对齐。
- 存储 `platform.idempotency`（唯一索引 `(user_id,key)`），保留 24h，只缓存 2xx/409/422，5xx 不缓存。重复 DELETE → 204；重复 restore → 200 同状态；重复 revoke → 409 `IMPORT_ALREADY_REVOKED`。
- **自愈例外**：`GET /reconciliation`（API-026）首次调用会写入 `side=missing`、`source=system` 的补差流水，使断链闭合；该写入只发生一次（重复调用幂等）。客户端/网关不得假设该 GET 无副作用或长期缓存其响应。导入提交（API-011）也会在收尾自动跑一次对账：补差行归属该批次（`batch_id`），撤销批次时一并软删；其结果通过 API-013 的 `filled_count/fills[]` 暴露。
### 0.7 异步任务模式
- 长任务返回 `202` + `Location: /api/v1/jobs/{id}` + `Retry-After: 1`，body 含 `job_id/type/status/links{self,events}`；轮询 `GET /jobs/{id}` 至终态取 `result_ref`，或订阅 SSE（§5）；SSE 是优化，轮询为兜底。
- 同类任务去重 `dedupe_key=hash(user_id,type,canonical_params)`：pending/running 已存在 → 返回同一 `job_id`（202 + `X-Job-Reused: true`）；参数不同且超并发 → `429 QUOTA_EXCEEDED`。
### 0.8 限流 / ETag / 格式 / 软删
- 限流按 `user_id` + PAT 双维度令牌桶（PG 原子计数）；响应 `X-RateLimit-Limit/Remaining/Reset`（epoch 秒）；超限 `429 RATE_LIMITED` + 正整数 `Retry-After`；登录失败 5 次/5min → `429 LOGIN_RATE_LIMITED`；背压异步写 `429 QUOTA_EXCEEDED` + `Retry-After: 60`。
- ETag `W/"<version>"`（列表含查询指纹），写响应返新 ETag；`If-Match` 不匹配 → 409 `VERSION_CONFLICT`（遵循 PRD FR-L4，不采用 412）。
- 格式：金额**定点字符串 2 位** `"1234.56"`；价格 6 位、数量 4 位；比率十进制小数；时间 ISO 8601 UTC；业务日 `YYYY-MM-DD`（Asia/Shanghai）；`blocked` 时 `value` **字段缺省**。软删默认过滤 `deleted_at NULL`，`include_deleted=true` 仅本人；注销物理级联删除，审计留事件元数据。

## §1 端点总表（82 个；Path 省略公共前缀 `/api/v1`，标注「根」者不在前缀下）
| API-ID | Method | Path | 用途 | FR | 认证/scope | 幂等 | 同步/异步 | 优先级 | 主要成功码 |
|---|---|---|---|---|---|---|---|---|---|
| API-001 | POST | `/auth/register` | 注册账号 | L2 | 公开 | 支持 | 同步 | P0 | 201 |
| API-002 | POST | `/auth/login` | 登录换取令牌 | L2 | 公开 | — | 同步 | P0 | 200 |
| API-003 | POST | `/auth/refresh` | 轮换刷新 access | L2 | 公开(refresh) | — | 同步 | P0 | 200 |
| API-004 | POST | `/auth/logout` | 登出并撤销会话 | L2 | Bearer | 支持 | 同步 | P0 | 204 |
| API-005 | GET | `/auth/me` | 当前用户与角色 | L2/D1 | Bearer | — | 同步 | P0 | 200 |
| API-006 | DELETE | `/account` | 注销并物理删除数据 | L2/L7 | write | 必需 | 同步 | P0 | 204 |
| API-007 | POST | `/tokens` | 创建 PAT（明文一次） | A1 | Bearer | 不适用 | 同步 | P0 | 201 |
| API-008 | GET | `/tokens` | PAT 列表（无明文） | A1 | read | — | 同步 | P0 | 200 |
| API-009 | DELETE | `/tokens/{token_id}` | 撤销 PAT | A1 | write | 幂等 | 同步 | P0 | 204 |
| API-010 | POST | `/imports/preview` | 导入预览（不落库） | L3 | write | 支持 | 同步 | P0 | 200 |
| API-011 | POST | `/imports` | 提交导入（commit） | L3 | write | 必需 | 异步 | P0 | 202 |
| API-012 | GET | `/imports` | 导入批次列表 | L3 | read | — | 同步 | P0 | 200 |
| API-013 | GET | `/imports/{batch_id}` | 批次详情、计数与系统补差 | L3 | read | — | 同步 | P0 | 200 |
| API-014 | GET | `/imports/{batch_id}/quarantine` | 隔离区行 | L5 | read | — | 同步 | P0 | 200 |
| API-015 | POST | `/imports/{batch_id}/revoke` | 撤销该批次 | L3 | write | 支持 | 同步 | P0 | 200 |
| API-016 | POST | `/imports/{batch_id}/reclassify` | 按新规则重跑分类 | L5 | write | 必需 | 异步 | P1 | 202 |
| API-017 | GET | `/trades` | 流水查询（游标/裁剪/ETag） | L8/L4 | read | — | 同步 | P0 | 200 |
| API-018 | PATCH | `/trades/{trade_id}` | 单行修正（If-Match） | L4 | write | 支持 | 同步 | P0 | 200 |
| API-019 | DELETE | `/trades/{trade_id}` | 软删单行 | L4 | write | 幂等 | 同步 | P1 | 204 |
| API-020 | POST | `/trades/{trade_id}/restore` | 恢复软删行 | L4 | write | 幂等 | 同步 | P1 | 200 |
| API-021 | GET | `/trades/{trade_id}/history` | 修改历史 | L4 | read | — | 同步 | P1 | 200 |
| API-022 | GET | `/summary-rules` | 摘要规则列表 | L5 | read | — | 同步 | P0 | 200 |
| API-023 | POST | `/summary-rules` | 新建摘要规则 | L5 | write | 支持 | 同步 | P0 | 201 |
| API-024 | PATCH | `/summary-rules/{rule_id}` | 修改规则 | L5 | write | 支持 | 同步 | P0 | 200 |
| API-025 | DELETE | `/summary-rules/{rule_id}` | 删除规则 | L5 | write | 幂等 | 同步 | P1 | 204 |
| API-026 | GET | `/reconciliation` | 对账、断链定位与自动补差 | L6 | read | — | 同步 | P0 | 200 |
| API-027 | GET | `/reconciliation/bridge` | 桥接恒等式校验 | R2 | read | — | 同步 | P0 | 200 |
| API-028 | POST | `/jobs/rebuild-rounds` | 重建回合与净值 | R2 | write | 必需 | 异步 | P0 | 202 |
| API-029 | GET | `/rounds` | 回合列表 | R1/R2 | read | — | 同步 | P0 | 200 |
| API-030 | GET | `/rounds/{round_id}` | 回合详情 | R2/D1 | read | — | 同步 | P0 | 200 |
| API-031 | GET | `/rounds/{round_id}/fills` | FIFO 配对明细 | R2 | read | — | 同步 | P0 | 200 |
| API-032 | GET | `/nav` | 净值序列与 TWR/MWR | R3 | read | — | 同步 | P0 | 200 |
| API-033 | GET | `/metrics` | 指标批量查询 | R5/R6/R8 | read | — | 同步 | P0 | 200 |
| API-034 | GET | `/metrics/{metric_id}/explain` | 三段式解释 | D3 | read | — | 同步 | P0 | 200 |
| API-035 | GET | `/metrics/{metric_id}/drilldown` | 指标下钻明细 | D3 | read | — | 同步 | P0 | 200 |
| API-036 | GET | `/attribution` | 五因子归因 | R7 | read | — | 同步 | P0 | 200 |
| API-037 | GET | `/trends` | 纵向趋势与口径断点 | D5 | read | — | 同步 | P1 | 200 |
| API-038 | POST | `/diagnostics/selection` | 选股能力诊断 | R4 | write | 必需 | 异步 | P0 | 202 |
| API-039 | POST | `/reports` | 生成月报 | D2 | write | 必需 | 异步 | P0 | 202 |
| API-040 | GET | `/reports` | 报告列表 | D2 | read | — | 同步 | P1 | 200 |
| API-041 | GET | `/reports/{report_id}` | 报告详情/状态 | D2 | read | — | 同步 | P0 | 200 |
| API-042 | GET | `/reports/{report_id}/download` | 下载 HTML/PDF | D2 | read | — | 同步 | P0 | 200 |
| API-043 | POST | `/reports/{report_id}/shares` | 创建分享链接 | D8 | write | 支持 | 同步 | P1 | 201 |
| API-044 | GET | `/shares/{share_token}` | 匿名读取分享（签名） | D8 | 公开(签名) | — | 同步 | P1 | 200 |
| API-045 | DELETE | `/shares/{share_id}` | 撤销分享 | D8 | write | 幂等 | 同步 | P1 | 204 |
| API-046 | POST | `/journal` | 新建交易日志 | D4 | write | 支持 | 同步 | P0 | 201 |
| API-047 | GET | `/journal` | 日志列表（可按回合） | D4 | read | — | 同步 | P0 | 200 |
| API-048 | PATCH | `/journal/{entry_id}` | 编辑日志 | D4 | write | 支持 | 同步 | P1 | 200 |
| API-049 | DELETE | `/journal/{entry_id}` | 删除日志 | D4 | write | 幂等 | 同步 | P1 | 204 |
| API-050 | GET | `/journal/prompts` | 卖出后待对照提示 | D4 | read | — | 同步 | P1 | 200 |
| API-051 | GET | `/journal/summary` | 季度兑现率/理由统计 | D4 | read | — | 同步 | P1 | 200 |
| API-052 | GET | `/alerts` | 行为规则触发记录 | D6 | read | — | 同步 | P1 | 200 |
| API-053 | POST | `/alerts/{alert_id}/ack` | 确认提醒 | D6 | write | 幂等 | 同步 | P1 | 200 |
| API-054 | GET | `/notifications` | 站内通知 | D8 | read | — | 同步 | P2 | 200 |
| API-055 | GET | `/settings` | 设置总览 | D1 | read | — | 同步 | P0 | 200 |
| API-056 | PUT | `/settings/benchmark` | 切换基准（历史分段） | R5 | write | 幂等 | 同步 | P1 | 200 |
| API-057 | PUT | `/settings/privacy` | 隐私与 LLM 开关 | D6 | write | 幂等 | 同步 | P1 | 200 |
| API-058 | PUT | `/settings/behavior-rules` | 行为规则阈值 | D6 | write | 幂等 | 同步 | P1 | 200 |
| API-059 | POST | `/exports` | 创建全量导出 | L7 | write | 必需 | 异步 | P0 | 202 |
| API-060 | GET | `/exports/{export_id}/download` | 下载导出包 | L7 | read | — | 同步 | P0 | 200 |
| API-061 | GET | `/jobs` | 任务列表（含积压） | A3/D2 | read | — | 同步 | P1 | 200 |
| API-062 | GET | `/jobs/{job_id}` | 任务详情/进度 | R2 | read | — | 同步 | P0 | 200 |
| API-063 | GET | `/jobs/{job_id}/events` | SSE 进度流 | R2 | read | — | 流 | P0 | 200 |
| API-064 | POST | `/jobs/{job_id}/cancel` | 取消任务 | R2 | write | 幂等 | 同步 | P1 | 202 |
| API-065 | GET | `/audit-logs` | 本人审计 | A4 | read | — | 同步 | P0 | 200 |
| API-066 | GET | `/admin/audit-logs` | 系统级审计（无明细） | A4 | admin | — | 同步 | P1 | 200 |
| API-067 | GET | `/admin/system/status` | 队列积压/系统状态 | L1/A3 | admin | — | 同步 | P1 | 200 |
| API-068 | POST | `/admin/backups` | 触发全量备份 | L7 | admin | 必需 | 异步 | P1 | 202 |
| API-069 | GET | `/admin/backups` | 备份列表与校验 | L7 | admin | — | 同步 | P1 | 200 |
| API-070 | POST | `/admin/restore-drills` | 恢复演练 | L7 | admin | 必需 | 异步 | P1 | 202 |
| API-071 | GET | `/healthz`（根） | 存活探针 | L1 | 公开 | — | 同步 | P0 | 200 |
| API-072 | GET | `/readyz`（根） | 就绪探针（DB/迁移） | L1 | 公开 | — | 同步 | P0 | 200 |
| API-073 | GET | `/version`（根） | 版本信息 | L1/A2 | 公开 | — | 同步 | P0 | 200 |
| API-074 | GET | `/metrics`（根） | Prometheus 指标 | NFR-15 | 公开(内网) | — | 同步 | P1 | 200 |
| API-075 | GET | `/api/v1/openapi.json` | 契约快照 | A2 | 公开 | — | 同步 | P0 | 200 |
| API-076 | GET | `/api/v1/errors` | 机器可读错误码表 | A2 | 公开 | — | 同步 | P1 | 200 |
| API-077 | POST | `/chat/sessions` | 创建对话会话 | D7 | write | 支持 | 同步 | P1 | 201 |
| API-078 | POST | `/chat/sessions/{session_id}/messages` | 提问（金额后端填充） | D7 | write | 支持 | 同步 | P1 | 200 |
| API-079 | GET | `/metrics/{metric_id}` | 指标详情（单指标完整 MetricResult） | R5/R8/D1 | read | — | 同步 | P0 | 200 |
| API-080 | GET | `/trades/{trade_id}` | 流水详情（含血缘与版本） | L4/L8/D1 | read | — | 同步 | P0 | 200 |
| API-081 | GET | `/assets` | 每日资产快照列表（现金/持仓市值/成本/浮盈/日收益） | D1 | read | — | 同步 | P0 | 200 |
| API-082 | POST | `/assets/build` | 构建/增量更新资产表 | D1 | write | 必需 | 异步 | P0 | 202 |

## §2 分域详细契约
> 下列 32 个端点为**详细契约**；其余 50 个的请求/响应字段以 `openapi/openapi.json` 为准（§1 已登记、§6 已配验收用例）。断言均为可脚本执行：状态码 + 字段值 + 状态迁移 + 副作用。
### 2.1 认证、导入与流水
#### API-001 POST /auth/register
- 用途：注册账号；FR-L2；认证：公开；幂等：支持；同步。
- 请求：body `email`(string,必填,≤254) / `password`(string,必填,12~128,≥3 类字符) / `display_name`(可选,≤40) / `accept_tos`(bool,必填=true)。
- 响应 201：`user{id,email,role:"user",email_verified,created_at}` / `access_token` / `refresh_token` / `token_type:"Bearer"` / `expires_in:900`。
- 错误码：EMAIL_TAKEN(409)、WEAK_PASSWORD(422)、VALIDATION_ERROR(422)、RATE_LIMITED(429)。
- 验收断言：① 201 且 user.role=="user"、expires_in==900；② 同邮箱再注册 → 409 EMAIL_TAKEN；③ password="123456" → 422 且用户未创建；④ 缺 accept_tos → 422 且 errors[0].field=="accept_tos"；⑤ 返回 access_token 调 GET /auth/me → 200 且 email 一致，且审计出现 action=="auth.register"。
#### API-002 POST /auth/login
- 用途：登录签发令牌；FR-L2；认证：公开；幂等：不支持；同步。
- 请求：body `email`(必填) / `password`(必填) / `device_name`(可选,≤64)。
- 响应 200：`access_token` / `refresh_token` / `expires_in:900` / `token_type:"Bearer"` / `user{id,email,role}`。
- 错误码：INVALID_CREDENTIALS(401)、LOGIN_RATE_LIMITED(429)、VALIDATION_ERROR(422)。
- 验收断言：① 200 且 access_token 为三段式 JWT、`exp-iat==900`；② 错密码 1~4 次 401，第 5 次 429 且 Retry-After 为正整数；③ 无 token 调 GET /trades → 401 且 WWW-Authenticate 含 Bearer；④ admin 登录后调 GET /trades → 403 ADMIN_CANNOT_READ_TRADES；⑤ user 调 GET /admin/system/status → 403。
#### API-003 POST /auth/refresh
- 用途：refresh 换新 access（轮换+复用检测）；FR-L2；认证：refresh token；同步。
- 请求：body `refresh_token`(必填)；每次成功返回**新** refresh，旧值立即失效。
- 响应 200：`access_token` / `refresh_token`(新) / `expires_in:900`。
- 错误码：TOKEN_EXPIRED(401)、TOKEN_REVOKED(401)、TOKEN_REUSED(401)、VALIDATION_ERROR(422)。
- 验收断言：① R0 换得 R1 且 R1≠R0；② R0 再用 → 401 TOKEN_REUSED 且 R1 同时失效（令牌族撤销）；③ 新 access 调 /auth/me → 200；④ 登出后原 refresh → 401 TOKEN_REVOKED；⑤ 响应不含密码/哈希字段。
#### API-007 POST /tokens
- 用途：创建 PAT；FR-A1；认证：会话 Bearer；幂等：**不适用**（明文只出现一次，超时用 GET /tokens 对齐）；同步。
- 请求：body `name`(必填,1~64) / `scopes`(必填,枚举数组 ⊆["read","write"]) / `expires_at`(可选,默认 now+90d,≤365d)。
- 响应 201：`token{token_id,name,prefix,token,scopes,expires_at,created_at}`；`token` 以 `pat_` 开头，此后不再返回。
- 错误码：VALIDATION_ERROR(422)、INSUFFICIENT_SCOPE(403)、RATE_LIMITED(429)。
- 验收断言：① 201 且 token 匹配 `^pat_[A-Za-z0-9]{43}$`；② GET /tokens 200 响应体不含明文字段，仅 prefix（前 8 位）；③ 该 PAT 调 GET /metrics → 200；④ read PAT POST /imports → 403 INSUFFICIENT_SCOPE；⑤ expires_at 超 365d → 422，撤销事件进审计（action=="token.revoke"）。
#### API-010 POST /imports/preview
- 用途：解析账单、列映射建议、质量预检（不落业务数据）；FR-L3；认证：write；幂等：支持；同步。
- 请求：multipart `file`(必填,.xls/.xlsx/.csv,≤20MB) / `broker`(默认 huatai) / `mode`(full|replace_range) / `mapping`(可选 JSON)。
- 响应 200：`preview_id`(24h) / `file{name,sha256,bytes}` / `columns[]{index,name,sample[],suggested_field,confidence}` / `date_format` / `warnings[]{code,row,column,message}` / `estimated{parsed,inserted,skipped,quarantined}` / `cash_balance_available` / `can_commit`。
- 错误码：IMPORT_UNSUPPORTED_FILE(415)、IMPORT_FILE_TOO_LARGE(413)、VALIDATION_ERROR(422)、RATE_LIMITED(429)。
- 验收断言：① 200 且 estimated.inserted 等于 fixture 期望；② 缺资金余额列 → warnings 含 MISSING_CASH_BALANCE 且 cash_balance_available==false；③ 预览前后 GET /trades?limit=1 的 total_estimate 与首行不变；④ 未知摘要行计入 estimated.quarantined；⑤ 同一文件二次 preview → sha256 相同、inserted 预估为 0。
#### API-011 POST /imports
- 用途：按确认映射提交导入（bag 去重 + 批次）；FR-L3；认证：write；幂等：**必需**；异步。
- 请求：body `preview_id`(必填) / `mode`(full|replace_range) / `range{from,to}`(replace_range 必填) / `mapping`(可选)；头 `Idempotency-Key`(必填)。
- 响应 202：`job_id` / `type:"import_commit"` / `batch_id` / `status:"pending"` / `links{self,events}`；`Location: /api/v1/jobs/{job_id}`、`Retry-After: 1`。
- 错误码：IDEMPOTENCY_KEY_REUSED(422)、IDEMPOTENCY_IN_PROGRESS(409)、IMPORT_PREVIEW_EXPIRED(410)、IMPORT_COLUMNS_UNRESOLVED(422)、JOB_ALREADY_RUNNING(409)、QUOTA_EXCEEDED(429)。
- 验收断言：① 202 且 job.type=="import_commit"、batch_id 非空；② 轮询至 succeeded 后 GET /imports/{batch_id}.counts.inserted == preview.estimated.inserted；③ 同文件再 commit → inserted==0 且 skipped>0；④ 同 Idempotency-Key 并发两次 → 同一 job_id 且 trades 只增一次；⑤ 首行非买入 → commit 成功且 batch.assumptions 含 back_solved 或 warnings 含 NAV_UNRELIABLE；⑥ 断链账单 commit 收尾自动对账：批次 `filled_count≥1`、`warnings` 含 `RECONCILIATION_AUTOFILLED`、`fills[]` 行号/金额/日期精确，且补差行 `side=missing/source=system/batch_id=本批次`。
#### API-013 GET /imports/{batch_id}
- 用途：批次详情、计数与**导入收尾对账的系统补差清单**；FR-L3；认证：read；同步。
- 请求：path `batch_id`。
- 响应 200：`batch_id` / `status(pending…revoked)` / `mode` / `broker` / `file_name` / `counts{parsed,inserted,skipped,quarantined}` / `filled_count` / `fills[]{trade_id,trade_date,line_no,amount,cash_balance,summary}` / `warnings[]` / `assumptions{reconciliation{status,filled_count}}` / `created_at` / `finished_at` / `revoked_at` / `can_revoke`。`filled_count` 只统计未软删的 `side=missing` 补差行；撤销后归零、`fills=[]`。
- 错误码：NOT_FOUND(404)。
- 验收断言：① 200 且 counts.inserted==412、file_sha256 匹配；② 用户 B 访问 A 的 batch → 404；③ 断链账单导入后 `filled_count==len(fills)≥1` 且每项 `summary=="对账自动补差"`、金额为分转元的定点字符串；④ 正常账单 `filled_count==0`、`fills==[]` 且 warnings 不含 `RECONCILIATION_AUTOFILLED`；⑤ 撤销后 `filled_count==0`、`fills==[]`。
#### API-014 GET /imports/{batch_id}/quarantine
- 用途：隔离区（未知摘要、全零金额等未入库行）；FR-L5；认证：read；同步；游标分页。
- 请求：path `batch_id`；query `limit`(默认 50) / `cursor` / `reason_code`(可选，∈`UNKNOWN_SUMMARY/UNMAPPED_COLUMN/INVALID_ROW/DUPLICATE_IN_FILE/INVALID_AMOUNT`)。
- 响应 200：`items[]{line_no,raw{date,security_code,summary,quantity,price,amount,cash_balance},reason_code,suggested_rule_id}` / `next_cursor` / `total_estimate`。
- 错误码：NOT_FOUND(404)、INVALID_CURSOR(400)、VALIDATION_ERROR(422)。
- 验收断言：① 200 且 fx_quarantine 第 7 行被隔离（line_no==7）；② 每项 raw.summary 与原始文件逐字一致；③ 用户 B 访问 A 的 batch → 404 且响应不含 A 任何字段；④ 新增匹配规则并 reclassify 后隔离区条目减少；⑤ 「佣金/印花税/其他费/发生金额/资金余额」五字段全为 0 的行 → `reason_code=="INVALID_AMOUNT"` 且 `line_no` 精确、该行不在 `GET /trades`；同内容重复的全零行也只记 `INVALID_AMOUNT`（判定先于 `DUPLICATE_IN_FILE`）。
#### API-015 POST /imports/{batch_id}/revoke
- 用途：撤销本次导入（软删该批次写入行 + 归属该批次的系统补差行）；FR-L3；认证：write；幂等：支持；同步。
- 请求：path `batch_id`；body `reason`(可选,≤200)；重复调用 → 409。
- 响应 200：`batch_id` / `status:"revoked"` / `reverted{inserted,updated}` / `revoked_at` / `affected_metrics[]{metric_id,status:"stale"}`。
- 错误码：NOT_FOUND(404)、IMPORT_ALREADY_REVOKED(409)、VALIDATION_ERROR(422)。
- 验收断言：① 200 后 GET /trades 行数 == 导入前行数（含补差行一起回退）；② batch.status=="revoked"；③ 再次 revoke → 409 IMPORT_ALREADY_REVOKED；④ GET /trades?include_deleted=true 可见被软删行且 batch_id 匹配；⑤ 审计出现 action=="import.revoke"；⑥ 本批次补差行被一并软删（`GET /trades?side=missing&batch_id=…` 为空，`include_deleted=true` 可见），`reverted.inserted` 包含补差行数。
#### API-017 GET /trades
- 用途：交易流水查询（完整 REST）；FR-L8/L4；认证：read；同步；游标分页。
- 请求：query `limit`(1~200) / `cursor` / `sort`(白名单 trade_date,-trade_date,amount,-amount,created_at) / `trade_date_from`、`trade_date_to` / `security_code` / `side`(∈{buy,sell,unknown,missing}) / `batch_id` / `include_deleted`(默认 false) / `fields` / `include_total`。
- 响应 200：`items[]{trade_id,trade_date,security_code,security_name,side,quantity,price,amount,commission,stamp_tax,other_fee,cash_balance,cash_balance_available,account_masked,dedupe_key,version,source,deleted_at}` / `next_cursor` / `has_more`；响应头 `ETag`。
- 错误码：INVALID_SORT_FIELD(400)、INVALID_CURSOR(400)、INVALID_FIELD_SELECTION(400)、UNKNOWN_QUERY_PARAM(400)、VALIDATION_ERROR(422)。
- 验收断言：① limit=100 时 next_cursor 非空，游标翻全集无重复无遗漏（trade_id 集合与服务端总数一致）；② sort=foo → 400 INVALID_SORT_FIELD；③ fields=id,trade_date → 响应键集合恰为这两个；④ 带上次 ETag 的 If-None-Match → 304 且空 body；⑤ 用户 B 的结果集为 0 条；⑥ `side=missing` → 仅返回对账补差行，且每项 `source=="system"`、`batch_id==null`、`summary=="对账自动补差"`。
#### API-018 PATCH /trades/{trade_id}
- 用途：单行修正（乐观并发）；FR-L4；认证：write；幂等：支持；同步。
- 请求：path `trade_id`；头 `If-Match`(**必填** `"<version>"`)；body 可改 `summary_code/trade_date/quantity/price/commission/stamp_tax/other_fee/amount/cash_balance/note`，禁改 `trade_id/dedupe_key`。
- 响应 200：完整 Trade（version 递增 1）/ `affected_metrics[]{metric_id,status:"stale"}`；响应头新 `ETag`。
- 错误码：VERSION_CONFLICT(409)、NOT_FOUND(404)、VALIDATION_ERROR(422)、IDEMPOTENCY_KEY_REUSED(422)。
- 验收断言：① 200 且 version==旧 version+1；② 用旧 version 再 PATCH → 409 VERSION_CONFLICT；③ 改 dedupe_key → 422；④ 修改后 GET /trades/{id}/history 多一条版本记录；⑤ 跨用户 PATCH → 404 且目标行未被修改。
#### API-020 POST /trades/{trade_id}/restore
- 用途：恢复软删行并触发派生复算；FR-L4；认证：write；幂等：幂等；同步。
- 请求：path `trade_id`；可选 body `reason`；响应头新 ETag。
- 响应 200：`trade`(deleted_at=null,version+1) / `recompute_job_id` / `status:"restored"`。
- 错误码：NOT_FOUND(404)、VERSION_CONFLICT(409)、VALIDATION_ERROR(422)。
- 验收断言：① restore 后默认查询可见该行；② 等 recompute_job_id succeeded 后 GET /metrics?ids=win_rate&period=… 与删除前 diff≤0.01/1bp；③ 对未删除行 restore → 422 VALIDATION_ERROR；④ 重复 restore → 200 且状态不变。
### 2.2 规则、对账、回合与净值
#### API-026 GET /reconciliation
- 用途：余额链对账、断链精确定位与**自动补差**；FR-L6；认证：read；同步。
- 请求：query `period`(YYYY-MM) 或 `from`/`to`；`include_ok_rows`(默认 false)。
- 响应 200：`period` / `status(ok|degraded|blocked)` / `break_count` / `filled_count`（本次调用新增的补差流水数）/ `breaks[]{trade_id,line_no,trade_date,expected,actual,diff,filled,filled_trade_id}`（`filled=true` 表示该断链已由补差流水修复；`filled_trade_id` 为补差流水 ID，未补差为 null）/ `checked_rows` / `epsilon:"0.01"` / `data_as_of` / `generated_at`。
- 补差语义：检出断链时，对**带账单行号的导入行**逐条写入补差流水 `side=missing`、`source=system`、`security_code=CASH`、`security_name=对账补差`、`summary=对账自动补差`、`category=other`（导入收尾对账写入的补差 `batch_id`=该导入批次；对账 GET 触发的补差 `batch_id=null`）；`trade_date`=断链行日期、`amount`=实际余额−(上一行余额+本行发生金额)、`cash_balance`=本行余额−本行发生金额、`line_no`=断链行行号，排序保证补差行在断链行之前。落库一次后链即闭合，重复调用不再新增（幂等）。
- 状态语义：本次补差成功或区间内存在 `side=missing` 行 → `status=degraded`（不再 `blocked`）；导入收尾已补差的批次可经 API-013 的 `filled_count/fills` 与 `RECONCILIATION_AUTOFILLED` 警告获知；存在**无法补差**的断链（如手工录入行 `line_no=null`）→ `blocked` 且对应 `breaks[].filled=false`；无断链且无补差行 → `ok`；无资金余额列 → `degraded`（200 降级语义，对应 `IMPORT_MISSING_CASH_BALANCE`）。
- **副作用**：该 GET 是自愈写端点——首次调用会写入补差流水；调用方/网关不得假设它只读、也不得长期缓存响应。
- 错误码：INVALID_PERIOD(422)、VALIDATION_ERROR(422)。
- 验收断言：① fx_base → status=="ok"、break_count==0、filled_count==0；② fx_broken 首次 → status=="degraded"（不再 blocked）、break_count==1、filled_count==1、breaks[0].filled==true 且 breaks[0].filled_trade_id 非空、line_no 与 fixture 一致、|diff|≤0.01；③ 同一请求再次调用 → break_count==0、filled_count==0、status=="degraded"（区间内存在 missing 行），补差行仍只有 1 条；④ GET /trades?side=missing → 可查到该补差行（source=="system"、summary=="对账自动补差"、batch_id==null）；⑤ 缺资金余额列 → status=="degraded"、break_count==0（降级而非错误体）。
#### API-027 GET /reconciliation/bridge
- 用途：桥接恒等式（期初 + 现金流 + 损益 = 期末）；FR-R2；认证：read；同步。
- 请求：query `period`(必填) / `as_of`(可选业务日，只用该业务日及以前的数据)。
- 响应 200：`period` / `balanced`(bool) / `identity{initial_cash,initial_open_cost,cashflows,realized_pnl,dividends,interest,fees,other,open_cost,ending_value,residual}` / `tolerance:"0.01"` / `status(ok|degraded|blocked)` / `data_as_of` / `inputs_hash`。
- 恒等式（Period2-02 实现口径，金额均为定点字符串；`realized_pnl` 为**不含股息**的交易损益，股息单列，避免二次计入）：
  ```
  residual = initial_cash + initial_open_cost + cashflows + realized_pnl
             + dividends + interest - fees + other - ending_value
  ending_value = 期末现金 + open_cost（期末未平仓成本；P2-03 换为市值后同一恒等式仍成立）
  ```
  `cashflows` 为净外部资金流（银证转账等 transfer 净额）；`fees` 为未分摊费用（fee 类行，正=费用）；`other` 为无法归类的现金流（打新/补差/未知），非零时 `status=degraded` 并自然体现在 `residual`。期初/期末现金取账单 `cash_balance` 锚点，锚点缺失时按现金流推导并降级；完全无 `cash_balance` 时 `status=blocked`。
- 错误码：INVALID_PERIOD(422)、VALIDATION_ERROR(422)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① balanced==true；② |identity.residual|≤0.01；③ 断链 fixture（cash_balance 不连续）→ balanced==false 且 residual≠0；④ 相同输入重复调用 balanced/residual/inputs_hash 完全相同。
#### API-028 POST /jobs/rebuild-rounds
- 用途：异步重建回合/净值/派生指标；FR-R2；认证：write；幂等：必需；异步。
- 请求：body `scope`(full|period,默认 full) / `period`(scope=period 必填) / `reason`(可选)；头 `Idempotency-Key`。
- 响应 202：`job_id` / `type:"rebuild_rounds"` / `status:"pending"` / `links{self,events}`；`Location: /api/v1/jobs/{job_id}`；同参复用（G2 语义）时 `X-Job-Reused: true`。
- 错误码：IDEMPOTENCY_KEY_REUSED(422)、JOB_ALREADY_RUNNING(409)、QUOTA_EXCEEDED(429)、VALIDATION_ERROR(422)、INVALID_PERIOD(422)。
- 验收断言：① 202 + Location；② 同参数并发 3 次 → 同一 job_id（第 2/3 次 `X-Job-Reused: true`）；③ 单飞范围内**异参**并发第 2 个 → 409 `JOB_ALREADY_RUNNING`（G2 收口：静默复用会跑错参数）；并发任务数超用户配额 → 429 QUOTA_EXCEEDED + Retry-After；④ 终态 succeeded 后 GET /rounds 立即可见新结果；⑤ 运行中 POST /jobs/{id}/cancel → 终态 cancelled。
#### API-016 POST /imports/{batch_id}/reclassify
- 用途：用户新增/修改摘要规则后，对指定批次重跑分类（未知摘要重新命中、隔离区条目减少）；FR-L5；认证：write；幂等：必需；异步。
- 请求：path `batch_id`；body `{}` 或 `{reason?}`；头 `Idempotency-Key`（缺失 → 422 VALIDATION_ERROR）。
- 响应 202：`job_id` / `type:"reclassify"` / `status:"pending"` / `batch_id` / `links{self,events}`；`Location: /api/v1/jobs/{job_id}`。
- 错误码：NOT_FOUND(404)、IMPORT_ALREADY_REVOKED(409)、VALIDATION_ERROR(422)、JOB_ALREADY_RUNNING(409)、QUOTA_EXCEEDED(429)。
- 验收断言：① 202 且 job.type=="reclassify"；② 新增用户规则命中后重跑 → 隔离区未解决条目减少且 trade.category/summary_code 更新；③ 跨用户 batch_id → 404 且不产生 job；④ 已撤销批次 → 409 IMPORT_ALREADY_REVOKED。
#### API-022 GET /summary-rules
- 用途：摘要分类规则列表（系统默认 + 本人规则）；FR-L5；认证：read；同步。
- 请求：query 无（可选 `include_system=true`，默认 true）。
- 响应 200：`items[]{rule_id,code,name,category,side,match_type,pattern,priority,enabled,is_system,version,created_at}` / `total`；系统规则 `user_id IS NULL` 对所有人生效。
- 错误码：VALIDATION_ERROR(422)。
- 验收断言：① 200 且系统默认规则覆盖 buy/sell/dividend/transfer/fee/interest/rights/new_stock 等类别；② 同用户 `code` 唯一（系统规则 code 也唯一）；③ 跨用户列表仅含系统规则 + 自己的规则（0 条他人规则）。
#### API-023 POST /summary-rules
- 用途：新建用户摘要规则；FR-L5；认证：write；幂等：支持；同步。
- 请求：body `name/category/side/match_type/pattern/priority/enabled`；`code` 由服务端生成（`usr_`+8hex，≤16）。
- 响应 201：`SummaryRuleOut`；响应头 `ETag: W/"1"`。
- 错误码：VALIDATION_ERROR(422)。
- 验收断言：① 201 且 `is_system=false`、`version=1`；② 下一次分类/API-016 重跑即生效；③ 非法 category/side/match_type/regex → 422。
#### API-024 PATCH /summary-rules/{rule_id}
- 用途：修改本人规则；FR-L5；认证：write；幂等：支持；同步。
- 请求：path `rule_id`；头 `If-Match` 必填（`"<version>"`）；body 可改 `name/category/side/match_type/pattern/priority/enabled`（`code`/`is_system` 不可改）。
- 响应 200：`SummaryRuleOut`（version+1）+ 新 `ETag`。
- 错误码：NOT_FOUND(404)、VERSION_CONFLICT(409)、VALIDATION_ERROR(422)。
- 验收断言：① version 递增 1；② 旧 If-Match → 409 VERSION_CONFLICT；③ 系统规则/他人规则 → 404（不可改）；④ `code` 字段被改 → 422。
#### API-025 DELETE /summary-rules/{rule_id}
- 用途：删除本人规则（删除后系统默认规则自然回退）；FR-L5；认证：write；幂等：幂等；同步。
- 请求：path `rule_id`；无 body。
- 响应 204；重复删除 → 204。
- 错误码：NOT_FOUND(404)、VALIDATION_ERROR(422)。
- 验收断言：① 204；② 删除后该摘要回到系统规则命中/隔离行为；③ 系统规则/他人规则 → 404。
#### API-029 GET /rounds
- 用途：回合列表（FIFO 配对结果，按平仓日倒序）；FR-R1/R2；认证：read；同步；游标分页。
- 请求：query `security_code?` / `status?(open|closed|data_error)` / `from?`/`to?`（按 close_date 半开）/ `limit`(1~200,默认 50) / `cursor?`。
- 响应 200：`items[]Round{round_id,security_code,security_name,status,open_date,close_date,quantity,cost,proceeds,dividend,pnl,return_rate,holding_days,open_trade_id,close_trade_id,data_error,oversell_quantity,fills_count,rebuilt_at}` / `next_cursor` / `has_more`。
- 错误码：INVALID_CURSOR(400)、INVALID_SORT_FIELD(400)、VALIDATION_ERROR(422)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① 200；rebuild job succeeded 后立即可见；② 跨用户 0 条；③ `pnl` 与 golden 差 ≤0.01；④ 游标翻页无重无漏；非法游标 400 INVALID_CURSOR。
#### API-030 GET /rounds/{round_id}
- 用途：回合详情；FR-R2/D1；认证：read；同步。
- 请求：path `round_id`。
- 响应 200：`Round`（同上，含 `fills_count`）；可选 `fills[]`（`include_fills=true` 时内联）。
- 错误码：NOT_FOUND(404)、VALIDATION_ERROR(422)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① 200 且 pnl/return_rate/holding_days 与列表一致；② 跨用户/不存在 → 404 且 body 不含他人 `security_code/pnl`；③ admin → 403 ADMIN_CANNOT_READ_TRADES。
#### API-031 GET /rounds/{round_id}/fills
- 用途：FIFO 配对明细（可追溯到买卖流水）；FR-R2；认证：read；同步。
- 请求：path `round_id`。
- 响应 200：`items[]{fill_id,sequence,buy_trade_id,sell_trade_id,quantity,open_date,close_date,holding_days}` / `total`。
- 错误码：NOT_FOUND(404)、VALIDATION_ERROR(422)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① `buy_trade_id/sell_trade_id` 集合与 golden/FIFO 期望 100% 一致；② 跨用户 → 404。
#### API-032 GET /nav
- 用途：单位净值序列 + TWR/MWR 与基准指标；FR-R3；认证：read；同步。
- 请求：query `from`/`to`(与 `period`=YYYY-MM 二选一) / `benchmark`(默认 hs300_tr) / `granularity`(本期仅 daily)。
- 口径（Period2-03 实现）：`twr` 为区间累计份额法 TWR（小数），`annualized_twr` 按自然日年化；`mwr` 为 XIRR（ACT/365，CF 含期初投入负/期末市值正）；`mwr_minus_twr = mwr − annualized_twr`（两端均年化，方能对齐比较，PRD 附录 A6）。期初基准点落在区间首日；非交易日按「当日或之前最近收盘价」估值；持仓标的缺行情 → 该日 `blocked` 且不输出资产值（不整段报错）。期初现金锚点：`period_start` 前最后一条余额行直取（`assumption="statement"`）；否则取 `period_start` 当日或之后第一条余额行反推（`assumption="back_solved"`，PRD 附录 A1）；全历史无余额行 → 整段 `blocked`。本期不含基准对比，`beta/alpha_annual/t_value/tracking_error/ir` 返回 null。
- 响应 200：`points[]{date,unit_nav,twr_daily,twr_daily_pct,cashflow,blocked,blocked_reason,total_assets,prev_total_assets,daily_pnl,holdings[]}` / `summary{mwr,twr,annualized_twr,mwr_minus_twr,beta,alpha_annual,t_value,tracking_error,ir,n_days,assumption}` / `status(ok|blocked|degraded)` / `blocked_ranges[]{from,to,reason}` / `benchmark_id` / `data_as_of` / `definition_version`。`summary.assumption ∈ statement|back_solved`，blocked 时为 null。`twr_daily_pct` 为两位小数的百分比字符串（如 `"0.62%"`）；`total_assets/prev_total_assets/daily_pnl` 为三位小数定点字符串，`daily_pnl = total_assets − cashflow − prev_total_assets`，首个净值点的 `prev_total_assets` 取「期初现金 + 期初持仓按上一交易日收盘价」估值（缺行情或前一点 blocked 时为 null，首点 `twr_daily_pct` 按该日盈亏/昨日资产计算）；`holdings[]` 的 `quantity/avg_cost/close_price/market_value` 为最多三位小数、去掉尾零的字符串（如 `"9.1"`）；`avg_cost` 为**含费买入均价**（累计成本 ÷ 持仓数量，成本取净现金流出，已含佣金/印花税/其他费）。
- 错误码：INVALID_PERIOD(422)、UNKNOWN_BENCHMARK(422)、VALIDATION_ERROR(422)。
- 验收断言：① points 按 date 严格升序、twr_daily 为十进制小数；② 与独立 oracle 逐日收益差 ≤1bp；③ 被阻断区间 status=="blocked" 且 summary 中资产类字段缺省；④ 未传 benchmark 时 benchmark_id=="hs300_tr" 并出现在 assumptions；⑤ 首行非余额行、区间内含余额行的月份 → 构建成功且 `summary.assumption=="back_solved"`，期初现金 = 余额行余额 − 该行发生额 − 区间内该行之前流水发生额之和。
#### API-081 GET /assets
- 用途：每日资产快照列表（“资产变化”展示 + 后续回撤族数据源）；FR-D1；认证：read；同步；游标分页。
- 请求：query `limit`(1~200，默认 50) / `cursor` / `from`/`to`(业务日**闭区间** `[from,to]`) / `status`(∈`ok|blocked|degraded`) / `sort`(白名单 `trade_date`、`-trade_date`，默认 `-trade_date`)。
- 响应 200：`items[]{date,cash,market_value,total_assets,holding_cost,unrealized_pnl,holding_count,cashflow,prev_total_assets,daily_pnl,daily_return,daily_return_pct,status,status_reason}` / `next_cursor` / `has_more` / `build{state,last_status,last_built_at,last_built_through,last_mode,last_rebuild_from,last_rows_written,rows_count,assumption,status_reason,has_new_market_data,definition_version}` / `definition_version` / `data_as_of`。金额为 2 位定点字符串；`daily_return` 为 8 位比率串、`daily_return_pct` 为两位百分比串（后端算，前端零口径）；**blocked 行数值字段一律显式 `null`**（非 `"0.00"`、非省略键）、`holding_count=0`、`status_reason` 非空。`build.state ∈ missing|current|stale|blocked`：无 state → `missing`（`rows_count=0`、`last_mode=null`）；全局无现金锚点 → `blocked`（`last_status="blocked"`、`last_built_through=null`、`status_reason` 非空）；指纹/行情 as_of/自然日前进 → `stale`。行只返回与 `state.definition_version` 同版本的口径（版本 bump 后不混排）。
- 错误码：INVALID_CURSOR(400)、INVALID_SORT_FIELD(400)、UNKNOWN_QUERY_PARAM(400)、INVALID_PERIOD(422)、VALIDATION_ERROR(422)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① 200 且 `items` 按 date 倒序、06-30 行与手算 oracle 一致（`cash="-70000.00"`、`market_value="170620.00"`、`total_assets="100620.00"`、`daily_pnl="620.00"`、`daily_return="0.00620000"`、`daily_return_pct="0.62%"`）、`build.state=="current"`、`definition_version=="1.0"`；② 未构建 → `items==[]`、`build.state=="missing"`、owner 直查两表无新增行；③ 跨用户 0 条泄露、admin → 403 `ADMIN_CANNOT_READ_TRADES`、无 token → 401；④ blocked 行数值字段全 `null` 且 `status_reason` 非空；⑤ 游标翻全集无重无漏、篡改 cursor → 400 INVALID_CURSOR；⑥ `limit=1/200` 可用、`limit=0/201` → 422、`from>to` → 422 INVALID_PERIOD、`status=foo` → 422、`sort=foo` → 400、未知 query → 400；⑦ `from/to` 闭区间含端点、`status` 过滤与 `sort=trade_date` 升序（与默认倒序互逆）；⑧ 构建后插流水/推进行情/时钟跨日 → `build.state=="stale"`，重建后回到 `current`。
#### API-082 POST /assets/build
- 用途：构建/增量更新每日资产表（长任务）；FR-D1；认证：write；幂等：**必需**；异步。
- 请求：头 `Idempotency-Key`(必填 16~128)；body 可选 `{mode:"auto"|"full"}`（默认 `auto`；`auto`=首次全量/后续按指纹与水位线增量）。`request_hash` 基于**解析默认值后**的规范 body（缺省 body 与 `{"mode":"auto"}` 视为同一请求）。
- 响应 202：`job_id` / `type:"build_assets"` / `status:"pending"` / `links{self,events}`；`Location: /api/v1/jobs/{job_id}`、`Retry-After: 1`；同参复用 `X-Job-Reused: true`；幂等重放 `Idempotency-Replayed: true`（重放路径同样带 Location/Retry-After）。
- Job：`kind=build_assets`、单飞 `scope_key={user_id}:assets`、`params={"mode":<解析默认值后的真实值>}`；handler 阶段 `recomputing → writing → finalizing`（progress 单调不减、粒度 ≥5）；结果写入 `platform.job.result`（键 `mode/rebuild_from/rows_written/rows_total/built_through/status/assumption/data_as_of`），**不扩 `GET /jobs` 响应**，对外摘要统一读 `GET /assets` 的 `build` 块。
- 错误码：IDEMPOTENCY_KEY_REUSED(422)、IDEMPOTENCY_IN_PROGRESS(409)、JOB_ALREADY_RUNNING(409)、QUOTA_EXCEEDED(429)、VALIDATION_ERROR(422)、INSUFFICIENT_SCOPE(403)、ADMIN_CANNOT_READ_TRADES(403)。
- 验收断言：① 202 + Location/Retry-After 且 `type=="build_assets"`；drain 后 `GET /assets` 行可见、`build.last_mode/last_rows_written/last_built_through` 齐全；② 同 key 同 body → 同一 `job_id` + `Idempotency-Replayed`，重复构建不产生重复行；缺省 body 与 `{"mode":"auto"}` 同 key 不 422；③ 不同 key 同参（首个待执行）→ 同一 `job_id` + `X-Job-Reused`；`auto` 运行中发 `full` → 409 `JOB_ALREADY_RUNNING`；④ read PAT → 403 `INSUFFICIENT_SCOPE`、admin → 403 `ADMIN_CANNOT_READ_TRADES`、无 token → 401、跨用户 job 不可见（404）；⑤ 缺 `Idempotency-Key`/`mode=bad` → 422 VALIDATION_ERROR、同 key 异体 → 422 IDEMPOTENCY_KEY_REUSED；⑥ 增量摘要：`build.last_mode=="incremental"`、`last_rows_written == end - last_rebuild_from + 1 < build.rows_count`，owner 直读 job.result 断言 `rows_written/rows_total`，结果与全量逐行相等；⑦ 无流水/无现金锚点 → 202→succeeded，`build.rows_count==0`，无锚点时 `build.last_status=="blocked"`；⑧ 进度事件 `stage ∈ {recomputing,writing,finalizing}` 且单调（平台 claim=parsing、finish=done 属既有行为）。
### 2.3 度量、诊断与报告
#### API-033 GET /metrics
- 用途：三域指标批量查询；FR-R5/R6/R8；认证：read；同步。
- 请求：query `ids`(逗号,与 domain 二选一) / `domain`(selection|timing|behavior|all) / `period` 或 `from`/`to` / `benchmark` / `include_exploratory`(默认 false)。
- 响应 200：`items[]MetricResult(metric_id,definition_version,scope,period,value?,unit,n,ci_low,ci_high,sample_status,status,assumptions,data_as_of,inputs_hash,computed_at)` / `period` / `data_as_of` / `definition_version` / `assumptions{benchmark_id,rf_rate,A:244}`。
- 错误码：METRIC_NOT_FOUND(404)、INVALID_PERIOD(422)、VALIDATION_ERROR(422)。
- 验收断言：① ids=win_rate&period=2026-09 → n==12、sample_status=="ok"、value 为小数、definition_version 匹配 `^\d+\.\d+$`；② ids=nav_return&断链 fixture → status=="blocked" 且该 item **无 value 键**；③ fx_n5 → sample_status=="insufficient" 且无 ci_low/ci_high；④ fx_n30 的 de → ci_low<value<ci_high；⑤ domain=behavior 返回 de 且 CI 不含 0（fx_de_pos），且 include_exploratory=false 时不含 exploratory 指标。
#### API-034 GET /metrics/{metric_id}/explain
- 用途：三段式（conclusion/method/drilldown）+ 人话术语；FR-D3；认证：read；同步。
- 请求：path `metric_id`；query `period`(必填) / `benchmark`(可选)；头 Accept-Language。
- 响应 200：`metric_id` / `definition_version` / `period` / `conclusion`(string|null) / `method` / `drilldown{endpoint,params,description}` / `status` / `reason?` / `what_to_do?` / `assumptions` / `data_as_of`。
- 错误码：METRIC_NOT_FOUND(404)、INVALID_PERIOD(422)、VALIDATION_ERROR(422)。
- 验收断言：① conclusion 不含裸术语（DE/BHAR/PGR/PLR/TWR/MWR/FIFO/HHI 均不出现）；② status!="ok" 时 conclusion==null 且 reason/what_to_do 非空；③ drilldown.endpoint + params 直接调用 → 200 且 items 非空；④ Accept-Language=en-US 时文案变化但 code/数值不变。
#### API-035 GET /metrics/{metric_id}/drilldown
- 用途：指标可追溯下钻明细；FR-D3；认证：read；同步；游标分页。
- 请求：path `metric_id`；query `period`(必填) / `round_id?` / `security_code?` / `limit` / `cursor`。
- 响应 200：`metric_id` / `definition_version` / `items[]`(金额定点字符串、含 trade_id/round_id) / `total` / `source_endpoints[]` / `params` / `next_cursor`。
- 错误码：METRIC_NOT_FOUND(404)、VALIDATION_ERROR(422)、INVALID_CURSOR(400)。
- 验收断言：① items 非空；② 每项金额匹配 `^-?\d+\.\d{2}$`；③ items 中 trade_id 均可在 GET /trades 查到（本人）；④ params 回填 period 与 metric_id。
#### API-036 GET /attribution
- 用途：归因拆解（P0 五因子，P1 行业 Brinson）；FR-R7；认证：read；同步。
- 请求：query `period`(必填) / `model`(five_factor|brinson_industry,默认 five_factor) / `benchmark`(可选)。
- 响应 200：`model` / `period` / `total_pnl` / `factors[]{code,name,amount,ratio,rank,conclusion}` / `residual` / `status` / `definition_version` / `data_as_of` / `assumptions`。
- 错误码：INVALID_PERIOD(422)、MODEL_NOT_AVAILABLE(422)、VALIDATION_ERROR(422)。
- 验收断言：① Σfactors.amount + residual 与 total_pnl 差 ≤0.01；② rank 从 1 连续无重复；③ Σfactors.ratio + residual_ratio 与 1 差 ≤1bp；④ fx_single_factor → rank[0].code 等于期望因子；⑤ blocked 区间 → status=="blocked" 且 total_pnl 缺省。
#### API-037 GET /trends
- 用途：季度/月趋势（口径断点必须断开）；FR-D5；认证：read；同步。
- 请求：query `metric`(必填) / `granularity`(month|quarter) / `from`/`to` / `benchmark`(可选)。
- 响应 200：`points[]{period,value,n,sample_status,definition_version,segment}` / `breaks[]{at,from_version,to_version,reason}` / `metric` / `granularity`。
- 错误码：METRIC_NOT_FOUND(404)、INVALID_GRANULARITY(422)、INVALID_PERIOD(422)。
- 验收断言：① points 按 period 升序且 segment 单调递增；② 口径断点处 breaks 非空，断点两侧 segment 不同（不跨断点连线）；③ 每个 point.value 与 GET /metrics 同 period 一致；④ granularity=week → 422。
#### API-038 POST /diagnostics/selection
- 用途：选股能力诊断（PIT/cohort/BHAR）；FR-R4；认证：write；幂等：必需；异步。
- 请求：body `period` 或 `from`/`to` / `benchmark`(默认 hs300_tr) / `as_of`(可选回测日)；头 `Idempotency-Key`。
- 响应 202：`job_id` / `type:"diagnostics_selection"` / `status:"pending"` / `links`。
- 错误码：IDEMPOTENCY_KEY_REUSED(422)、JOB_ALREADY_RUNNING(409)、QUOTA_EXCEEDED(429)、INVALID_PERIOD(422)。
- 验收断言：① 202 且 job.type 正确；② succeeded 后 GET /metrics?domain=selection&period=… 返回 bhar_20/bhar_60 的均值、中位数、t、n、sample_status；③ 传 as_of=2024-06-30 时 data_as_of≤该日且不含更晚财报（fixture 断言）；④ 同 spec 重复提交 → 复用同一 job_id。
#### API-039 POST /reports
- 用途：异步生成月报（HTML/PDF）；FR-D2；认证：write；幂等：必需；异步。
- 请求：body `period`(必填,YYYY-MM) / `format[]`(html|pdf,默认 ["html"]) / `sections[]`(可选)；头 `Idempotency-Key`。
- 响应 202：`job_id` / `report_id` / `type:"report_generate"` / `status:"pending"` / `links`。
- 错误码：INVALID_PERIOD(422)、IDEMPOTENCY_KEY_REUSED(422)、QUOTA_EXCEEDED(429)、JOB_ALREADY_RUNNING(409)。
- 验收断言：① 202 且 report_id 非空；② 同 (period,spec_hash) 重复请求 → 同一 report_id；③ 报告中每个数字与 GET /metrics 同周期一致（脚本比对 ≤0.01/1bp）；④ blocked 区间生成说明单：detail.blocked==true 且 sections 不含收益结论字段。
#### API-043 POST /reports/{report_id}/shares
- 用途：创建报告分享链接（签名、有效期、可撤销）；FR-D8；认证：write；幂等：支持；同步。
- 请求：body `expires_in`(秒,默认 604800,≤2592000) / `include_pdf`(默认 true)；头 `Idempotency-Key` 可选。
- 响应 201：`share{share_id,url,expires_at,created_at,report_id}` / `token_prefix`。
- 错误码：REPORT_NOT_READY(409)、NOT_FOUND(404)、VALIDATION_ERROR(422)、INSUFFICIENT_SCOPE(403)。
- 验收断言：① 201 且 url 含高熵签名 token、expires_at 匹配；② 无认证 GET 该 url → 200 且仅含该报告内容（无 user/email/account）；③ 撤销（DELETE /shares/{id}）后匿名 GET → 410 SHARE_EXPIRED；④ 该链接无法访问其他报告。
### 2.4 日志、导出、任务与运维
#### API-050 GET /journal/prompts
- 用途：卖出后"理由还成立吗"待对照项；FR-D4；认证：read；同步。
- 请求：query `status`(pending|done) / `round_id?` / `limit` / `cursor`。
- 响应 200：`items[]{prompt_id,round_id,security_code,closed_at,question,status,expected_review_at}` / `next_cursor`。
- 错误码：INVALID_CURSOR(400)、VALIDATION_ERROR(422)。
- 验收断言：① 回合平仓后 200 含该回合 prompt；② question 为完整问句且不含买卖建议词（合规词表）；③ 关联 journal 后 status 变 done（P1）；④ 跨用户不可见（0 条）。
#### API-059 POST /exports
- 用途：用户全量导出（Excel + JSON，异步 + 有效期下载）；FR-L7；认证：write；幂等：必需；异步。
- 请求：body `format[]`(xlsx|json) / `scope[]`(trades|rounds|journal|reports|settings,默认 all) / `include_files`(默认 false)；头 `Idempotency-Key`。
- 响应 202：`job_id` / `export_id` / `type:"export_create"` / `status:"pending"` / `links{self,events,download}`。
- 错误码：IDEMPOTENCY_KEY_REUSED(422)、EXPORT_EMPTY(422)、QUOTA_EXCEEDED(429)、JOB_ALREADY_RUNNING(409)。
- 验收断言：① 202；② 完成后 GET download → 200 + Content-Disposition（filename 含日期）；③ 包内 meta 含 definition_version/data_as_of/schema_version；④ expires_at 之后下载 → 410 EXPORT_EXPIRED；⑤ 用户 B 下载 A 的 export → 404。
#### API-063 GET /jobs/{job_id}/events
- 用途：SSE 任务进度流；FR-R2；认证：read；同步→流式。
- 请求：path `job_id`；头 `Accept: text/event-stream` / `Last-Event-ID?`。
- 响应 200：`Content-Type: text/event-stream`；事件 `progress`(`{job_id,progress,stage,message,at}`) / `done`(`{job_id,status,result_ref}`) / `error`(`{job_id,error}`) / 心跳 `:keep-alive`（15s）/ 关闭前 `event: reconnect`。
- 错误码：NOT_FOUND(404)、TOKEN_REVOKED(401)、RATE_LIMITED(429)。
- 验收断言：① Content-Type 以 text/event-stream 开头；② progress 单调不减且 ∈[0,100]，stage 属固定枚举；③ 终态收到 done，result_ref 直接 GET → 200；④ 带 Last-Event-ID 重连不重复已收事件；⑤ 订阅他人 job → 404 且**不建立事件流**。
#### API-065 GET /audit-logs
- 用途：本人审计（登录、Token、导入/撤销、修改/删除、导出、注销）；FR-A4；认证：read；同步；游标分页。
- 请求：query `action?` / `target_type?` / `from`/`to` / `limit` / `cursor`。
- 响应 200：`items[]{event_id,actor{id},action,target{type,id},at,request_id,ip_hash,source(web|pat|admin),metadata}` / `next_cursor`。
- 错误码：INVALID_CURSOR(400)、VALIDATION_ERROR(422)、FORBIDDEN(403)。
- 验收断言：① 200 含刚才的 login 与 token.revoke 事件，字段 actor/action/target/at/request_id 齐全；② 用户 A 查不到 B 的事件；③ admin 的 /admin/audit-logs 响应 schema 不含 amount/security_code；④ action 非法 → 422。
#### API-072 GET /readyz
- 用途：就绪探针（DB 可连、迁移版本匹配、非 drain）；FR-L1；认证：公开；同步。
- 请求：无；超时 2s。
- 响应 200：`{status:"ok",db:"ok",schema_version:"0042",draining:false,worker_heartbeat_age_s}`；DB 异常/迁移不匹配 → 503 + 对应 code。
- 错误码：DB_UNAVAILABLE(503)、SCHEMA_VERSION_MISMATCH(503)。
- 验收断言：① 200 且 db=="ok"、schema_version 与 /version 一致；② 停 DB → 503 DB_UNAVAILABLE；③ 迁移落后 → 503 SCHEMA_VERSION_MISMATCH；④ `SIGTERM` 后立即 503 且 draining==true；⑤ 滚动重启期间采样 5xx<0.1%。
#### API-075 GET /api/v1/openapi.json
- 用途：OpenAPI 3.1 契约快照（只读）；FR-A2；认证：公开；同步。
- 请求：无；响应头 `ETag`、`Cache-Control: no-cache`。
- 响应 200：`openapi:"3.1.0"` / `info.version` / `paths{}` / `components.schemas{Error,MetricResult,Job,...}` / 每 operation 的 operationId、示例、`x-error-codes`。
- 错误码：INTERNAL_ERROR(500)。
- 验收断言：① info.version == GET /version 的 api_version；② paths 集合与本文档 §1 端点集合完全相等；③ 每个 operationId 唯一且与 `openapi/operationId.lock.json` 一致；④ 每 operation ≥1 个 2xx 示例与 ≥1 个 4xx（引用 Error schema）；⑤ 元测试：删除任一端点示例 → `make api-contract-check` 非 0。
#### API-078 POST /chat/sessions/{session_id}/messages
- 用途：复盘问答（工具取数 + 金额后端填充 + 出站守卫）；FR-D7；认证：write；幂等：支持；同步。
- 请求：path `session_id`；body `message`(必填,1~2000) / `context{period?,round_id?}`；头 `Idempotency-Key`(推荐)。
- 响应 200：`message_id` / `answer` / `citations[]{number,value,source_endpoint,definition_version,data_as_of}` / `redaction_applied` / `llm_used` / `fallback`。
- 错误码：NOT_FOUND(404)、MESSAGE_TOO_LONG(422)、RATE_LIMITED(429)、LLM_UNAVAILABLE(503)。
- 验收断言：① 200 且 answer 非空；② 回答中每个金额都能在 citations 找到来源，且 source_endpoint 可调用返回一致值；③ LLM 关闭（`llm_provider=none`）时金额类问题仍 200 且 answer 金额正确（fallback==true）；④ 出站守卫：测试钩子捕获的出站 payload 不含金额/股东账号；⑤ 跨用户 session → 404。

## §3 错误码总表

> `GET /api/v1/errors` 返回本表机器可读版本；`code` 发布后不得改名或复用。`SAMPLE_INSUFFICIENT`/`NAV_UNRELIABLE`/`IMPORT_MISSING_CASH_BALANCE` 为 200 降级/警告码。

| code | HTTP | 触发条件 | user_message（人话） | what_to_do | retryable |
|---|---|---|---|---|---|
| INVALID_CREDENTIALS | 401 | 邮箱或密码错误 | 邮箱或密码不对。 | 检查后重试；连续失败会触发限速。 | 否 |
| TOKEN_EXPIRED | 401 | access/PAT 过期 | 访问凭证已过期。 | 用 refresh_token 换新 access，或重建 PAT。 | 否 |
| TOKEN_REVOKED | 401 | PAT/会话已撤销 | 这个令牌已被撤销。 | 创建新令牌并更新脚本。 | 否 |
| TOKEN_REUSED | 401 | 旧 refresh 复用 | 登录状态已失效，请重新登录。 | 重新登录；该令牌族已被安全撤销。 | 否 |
| MULTIPLE_CREDENTIALS | 401 | 同时传两种凭证 | 一个请求只能用一种凭证。 | 只保留一种令牌。 | 否 |
| INSUFFICIENT_SCOPE | 403 | read PAT 调写端点 | 当前令牌只有只读权限。 | 创建带 write scope 的 PAT。 | 否 |
| ADMIN_CANNOT_READ_TRADES | 403 | admin 读交易明细 | 管理员账号不能查看交易明细。 | 用数据所有者账号访问。 | 否 |
| FORBIDDEN | 403 | 角色不足（user 调 admin） | 没有权限执行该操作。 | 使用具备权限的账号。 | 否 |
| FOREIGN_RESOURCE_IN_BATCH | 403 | 批量参数含他人资源 ID | 请求中包含不属于你的数据。 | 移除无关 ID 后重试。 | 否 |
| NOT_FOUND | 404 | 资源不存在或跨用户 | 找不到该资源。 | 确认 ID；跨账号资源按不存在处理。 | 否 |
| METRIC_NOT_FOUND | 404 | metric_id 不在目录 | 没有这个指标。 | 从指标目录选择有效指标。 | 否 |
| EMAIL_TAKEN | 409 | 注册邮箱已存在 | 该邮箱已注册。 | 直接登录或找回密码。 | 否 |
| VERSION_CONFLICT | 409 | If-Match 版本不符 | 这条数据刚被改过。 | 重新获取最新版本后再提交。 | 否 |
| IDEMPOTENCY_IN_PROGRESS | 409 | 同键请求仍在执行 | 这个请求正在处理中。 | 用返回的 job_id 轮询结果。 | 是（轮询） |
| JOB_ALREADY_RUNNING | 409 | 同类任务运行中且参数不同 | 已有一个相同任务在跑。 | 复用返回的 job_id 或等待结束。 | 是（轮询） |
| JOB_NOT_CANCELLABLE | 409 | 任务已终态/不可取消阶段 | 任务已结束或不可取消。 | 等待终态或查看结果。 | 否 |
| IMPORT_ALREADY_REVOKED | 409 | 批次已撤销 | 这次导入已经撤销过了。 | 无需重复操作。 | 否 |
| REPORT_NOT_READY | 409 | 报告/导出尚未生成完 | 报告还在生成中。 | 稍后重试或订阅任务事件。 | 是 |
| WEAK_PASSWORD | 422 | 密码强度不足 | 密码太简单。 | 至少 12 位并包含大小写与数字。 | 否 |
| VALIDATION_ERROR | 422 | 参数/字段校验失败 | 请求参数有问题。 | 按 errors[] 修正字段后重试。 | 否 |
| IDEMPOTENCY_KEY_REUSED | 422 | 同键不同 body | 幂等键被用于了不同请求。 | 换一个新的 Idempotency-Key。 | 否 |
| INVALID_PERIOD | 422 | 区间格式/范围非法 | 时间区间不合法。 | 用 YYYY-MM 或 from/to（半开区间）。 | 否 |
| IMPORT_COLUMNS_UNRESOLVED | 422 | 必需列无法映射 | 账单缺少必需列。 | 手动指定列映射后重试。 | 否 |
| UNKNOWN_BENCHMARK | 422 | benchmark 不在支持列表 | 不支持这个基准。 | 使用 hs300_tr 或目录内基准。 | 否 |
| MODEL_NOT_AVAILABLE | 422 | 模型未启用（如 Brinson P1） | 该归因模型暂未开放。 | 使用 five_factor。 | 否 |
| INVALID_GRANULARITY | 422 | 趋势粒度非法 | 趋势粒度不支持。 | 使用 month 或 quarter。 | 否 |
| MESSAGE_TOO_LONG | 422 | 对话消息超 2000 字 | 消息太长了。 | 精简后再发送。 | 否 |
| EXPORT_EMPTY | 422 | 无可导出数据 | 当前没有可导出的数据。 | 先导入交易记录。 | 否 |
| INVALID_SORT_FIELD | 400 | sort 不在白名单 | 不支持这个排序字段。 | 使用文档列出的字段。 | 否 |
| INVALID_CURSOR | 400 | 游标篡改/过期/跨用户 | 分页游标无效。 | 从第一页重新翻页。 | 否 |
| INVALID_FIELD_SELECTION | 400 | fields 含未知字段 | 字段裁剪参数无效。 | 只用已登记字段。 | 否 |
| UNKNOWN_QUERY_PARAM | 400 | 未知过滤/查询参数 | 请求包含未知参数。 | 移除未知参数后重试。 | 否 |
| IMPORT_UNSUPPORTED_FILE | 415 | 扩展名/内容非账单 | 不支持这个文件格式。 | 上传 .xls/.xlsx/.csv。 | 否 |
| IMPORT_FILE_TOO_LARGE | 413 | 文件 >20MB | 文件太大了。 | 拆分账单后分批导入。 | 否 |
| IMPORT_PREVIEW_EXPIRED | 410 | 预览超过 24h | 预览已过期。 | 重新上传并预览。 | 是（重做预览） |
| EXPORT_EXPIRED | 410 | 下载链接过期 | 下载链接已过期。 | 重新创建导出任务。 | 是（重建） |
| SHARE_EXPIRED | 410 | 分享链接过期/撤销 | 分享链接已失效。 | 向分享者索取新链接。 | 否 |
| RATE_LIMITED | 429 | 请求限流 | 请求太频繁。 | 按 Retry-After 等待后重试。 | 是 |
| LOGIN_RATE_LIMITED | 429 | 登录失败 5 次/5min | 尝试次数过多。 | 5 分钟后再试或重置密码。 | 是 |
| QUOTA_EXCEEDED | 429 | 长任务并发超限 | 同时运行的任务已达上限。 | 等运行中的任务结束。 | 是 |
| INTERNAL_ERROR | 500 | 未预期异常 | 服务器出错了，已记录。 | 带 request_id 联系支持。 | 是（有限） |
| DB_UNAVAILABLE | 503 | DB 不可连/连接池耗尽 | 服务暂时不可用。 | 等待后重试；持续则联系运维。 | 是 |
| SCHEMA_VERSION_MISMATCH | 503 | 迁移版本不匹配 | 服务正在升级。 | 30s 后重试。 | 是 |
| LLM_UNAVAILABLE | 503 | LLM 故障且无兜底 | 智能问答暂时不可用。 | 稍后重试；金额类问题可用模板回答。 | 是 |
| SAMPLE_INSUFFICIENT | 200 | n<10（降级码） | 样本太少，先给你原始值。 | 继续记录交易，样本≥10 后给结论。 | 否 |
| NAV_UNRELIABLE | 200 | 期初反推失败（降级码） | 这段时间的数据不敢给你算收益。 | 补全资金余额列或修正首行后重算。 | 是（修正后） |
| IMPORT_MISSING_CASH_BALANCE | 200 | 缺资金余额列（警告码） | 账单缺少资金余额，暂时无法对账。 | 上传含资金余额列的账单。 | 是（补数据） |

## §4 关键资源 Schema（16 个；完整字段见 OpenAPI `components.schemas`）
| DTO | 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|---|
| MetricResult | metric_id / definition_version | string | 是 | 稳定指标 ID / 口径版本 `^\d+\.\d+$` | `win_rate` / `2.1` |
| MetricResult | value / n / sample_status / status | decimal-string / integer / enum | 否/是 | blocked 时 value 缺省；样本数；`ok/insufficient/pending_window/blocked`；`ok/stale/blocked/unreliable` | `0.4321` / `12` / `ok` / `ok` |
| MetricResult | data_as_of / inputs_hash / computed_at | date / string / datetime | 是 | 血缘四件套 | `2026-10-01` / `sha256:9f…` / `2026-10-19T08:00:00Z` |
| Job | job_id / type | string / enum | 是 | `import_commit/rebuild_rounds/diagnostics_selection/report_generate/export_create/backup/restore_drill/reclassify/build_assets` | `job_01J9Z6` / `report_generate` |
| Job | status / progress / stage | enum / integer / string | 是 | `pending/running/succeeded/failed/cancelled`；0–100 单调；阶段码 | `running` / `60` / `rendering` |
| Job | result_ref / error | string\|null / Error\|null | 否 | 终态资源链接 / 失败原因（含 code/user_message/what_to_do） | `/api/v1/reports/rpt_…` |
| ImportPreview | preview_id / file | string / object | 是 | 24h TTL；`name/sha256/bytes` | `prv_01J9Z6` |
| ImportPreview | columns[] / warnings[] / estimated / cash_balance_available | object[] / object / bool | 是 | 列映射建议；质量预检码；`parsed/inserted/skipped/quarantined`；可对账标志 | `{"inserted":412}` / `false` |
| Round | round_id / security_code / open_date / close_date | string / date | 是 | 回合 ID、6 位代码、FIFO 首买/末卖 | `rnd_01J9Z6` / `300308` |
| Round | pnl / return_rate / holding_days | decimal-string / integer | 是 | A8 口径；收益率=pnl/买入成本；交易日计 | `"-1234.56"` / `-0.0821` / `18` |
| NavSeries | points[] / summary | object[] / object | 是 | `date/unit_nav/twr_daily/cashflow/blocked`；`mwr/twr/beta/alpha_annual/ir/assumption(statement\|back_solved，期初现金锚点来源)` | `{"twr":0.0321,"mwr":0.0288}` |
| NavSeries | status / blocked_ranges[] / benchmark_id | enum / object[] / string | 是 | `ok/blocked/degraded`；阻断区间；默认 `hs300_tr` | `blocked` / `hs300_tr` |
| Attribution | model / period / total_pnl / residual | enum / string / decimal-string | 是 | `five_factor/brinson_industry`；区间总损益；残差 | `five_factor` / `"-8921.33"` / `"12.34"` |
| Attribution | factors[] | object[] | 是 | `code/name/amount/ratio/rank/conclusion` | `[{"code":"selection","amount":"-5100.00","rank":1}]` |
| Report | report_id / period / status / html_url / pdf_url / checksum / blocked | string / enum / bool | 是 | 报告 ID、`YYYY-MM`、状态、下载地址与校验、blocked=说明单 | `rpt_01J9Z6` / `2026-09` / `succeeded` |
| JournalEntry | entry_id / round_id / entry_type | string / enum | 是/否 | 日志 ID；关联回合；`reason/review/lesson` | `jnl_01J9Z6` / `reason` |
| JournalEntry | content / expectations | string / object | 是/否 | 1–4000 字；可判定预期 | `"买入理由…"` / `{"target_return":0.15}` |
| Error | code / status / retryable | string / integer / boolean | 是 | §3 稳定码、HTTP、可重试 | `VERSION_CONFLICT` / `409` / `false` |
| Error | user_message / what_to_do / request_id | string | 是 | 人话、行动建议、排障 ID | `"这条数据刚被改过。"` |
| Error | errors[] | object[] | 否 | 字段级错误 `field/code/message` | `[{"field":"password","code":"too_short"}]` |
| Trade | trade_id / trade_date / side / amount | string / date / enum / decimal-string | 是 | 流水 ID、业务日、方向 `buy/sell/unknown/missing`（`missing` 为对账自动补差行，`source=system`；账单可无「买卖标志」列，此时由摘要识别，仍无则 unknown）、发生金额 | `trd_01J9Z6` / `sell` / `"19098.95"` |
| Trade | cash_balance / cash_balance_available | decimal-string\|null / bool | 否 | 缺列时 null + false | `"21415.47"` / `true` |
| Trade | source / batch_id / line_no | enum / string\|null / integer\|null | 是/否 | 数据来源 `import/manual/system`（`system` 仅对账补差，`batch_id=null`）；来源批次；账单原始行号（补差行沿用断链行行号） | `system` / `null` / `12` |
| Trade | dedupe_key / version / account_masked | string / integer / string | 是 | A27 去重键 / 乐观锁 / 股东账号 MD5 摘要（32 位 hex，空账号为 null） | `sha256:…` / `3` / `ab12…9f` |
| ImportBatch | batch_id / status / mode / counts / filled_count / fills | string / enum / object / integer / object[] | 是 | 批次；`pending…revoked`；`full/replace_range`；`parsed/inserted/skipped/quarantined`；导入收尾对账补差数；补差明细 `{trade_id,trade_date,line_no,amount,cash_balance,summary}`（撤销后清零） | `imp_01J9Z6` / `succeeded` / `{"inserted":412}` / `1` / `[{"amount":"-3000.00"}]` |
| PatToken | token_id / prefix / scopes / expires_at / last_used_at | string / enum[] / datetime | 是/否 | 元信息；prefix 前 8 位；`read/write`；最长 365d | `tok_01J9Z6` / `pat_7Fh2` / `["read"]` / `2027-01-17T00:00:00Z` |
| AssetPoint | date / total_assets / status | date / decimal-string / enum | 是 | 每日资产快照行（金额 2 位定点串）；blocked 行数值显式 `null`、`holding_count=0` | `2026-06-30` / `"100620.00"` / `ok` |
| AssetPoint | cash / market_value / holding_cost / unrealized_pnl / daily_pnl / daily_return | decimal-string\|null | 否 | 现金/持仓市值/含费成本/浮动盈亏/日盈亏/日收益率；blocked 行全为 `null` | `"-70000.00"` / `"0.00620000"` |
| AssetBuildState | state / last_status / last_mode / rows_count | enum / integer | 是 | `missing/current/stale/blocked`；`ok/degraded/blocked`；`full/incremental/noop`；当前保留行数 | `current` / `ok` / `incremental` / `125` |
| AssetPage | items[] / build / definition_version / data_as_of | object / string / date | 是 | 分页 + 构建摘要 + 口径血缘；`AssetBuildAccepted` 为 202 专用体 `{job_id,type:"build_assets",status:"pending",links{self,events}}` | `{"has_more":false}` / `"1.0"` |

## §5 异步任务与 SSE 契约
### 5.1 状态机（`platform.job`）
```
enqueue ─▶ pending ─claim(SKIP LOCKED)─▶ running ─成功─▶ succeeded
            │                             ├─ 可重试失败 & attempt<max ─▶ pending(退避)
            │ cancel                      └─ attempt=max ─▶ failed
            ▼
        cancelled
```
- 合法迁移：`pending→running/cancelled`、`running→succeeded/failed/pending(重试)/cancelled`；终态不可变，重复取消 → 409 `JOB_NOT_CANCELLABLE`。
- 租约：领取写 `heartbeat_at` 每 10s 续；>60s 无心跳回收 `pending` 且 `attempt+1`；`attempt≥max_attempts` → `failed(JOB_LEASE_EXPIRED)`。单飞：`dedupe_key=hash(user_id,type,canonical_params)`，同键运行中返回同一 `job_id`，参数不同且超并发 → 429 `QUOTA_EXCEEDED`。
### 5.2 可取消性与进度
| 任务类型 | 可取消 | 说明 |
|---|---|---|
| `import_commit` / `reclassify` | 是（检查点） | 写事务前取消无副作用；写事务内不可中断，完成后按幂等回滚批次 |
| `rebuild_rounds` / `diagnostics_selection` | 是 | 计算阶段检查点取消；已提交派生数据保留并可重算 |
| `report_generate` / `export_create` | 是 | 渲染/打包前检查点取消 |
| `build_assets` | 是（写事务前检查点） | 派生重建；写事务前取消无副作用，写事务内不可中断（与 `rebuild_rounds` 一致） |
| `backup` / `restore_drill` | 否（running 后） | 一致性快照，取消即损坏风险 |
- `progress`：0–100 单调不减、粒度≥5；`stage ∈ queued/parsing/writing/recomputing/rendering/finalizing`；`message` 人话；`result_ref` 只在终态出现；`attempt/max_attempts/available_at/heartbeat_at` 用于排障。
### 5.3 SSE 事件与断线续传
```
event: progress
id: 42
data: {"job_id":"job_01J9Z6","progress":60,"stage":"recomputing","message":"正在重算回合…","at":"2026-10-19T08:00:00Z"}

event: done
id: 43
data: {"job_id":"job_01J9Z6","status":"succeeded","result_ref":"/api/v1/reports/rpt_01J9Z6"}
```
- 事件类型固定 `progress`/`done`/`error` + 心跳注释 `:keep-alive`（15s）；停机/发布前发 `event: reconnect` 后断流。
- `Last-Event-ID` 续传：事件 id 为每 job 单调整数，事件存 DB（outbox），重放窗口 5min，不重发重复事件；终态后发 `done`/`error` 并关闭。SSE 只是优化：客户端以 `GET /jobs/{id}` 轮询兜底；跨用户订阅 → 404 且不建立事件流。

## §6 验收矩阵（端点 100% 有用例）
> 每 `API-NNN` 对应 `backend/tests/api/test_api_NNN_<slug>.py` + `@pytest.mark.api("API-NNN")`；每端点至少 happy path / 鉴权越权 / 一个错误码三条用例；断言分层：传输（L1）→ 业务值（L2）→ 副作用（L3）。fixture：`fx_base/fx_round30/fx_broken/fx_missing_cash/fx_dup_rows/fx_quarantine/fx_n5/fx_n30/fx_de_pos/fx_single_factor/fx_blocked/fx_version_break/fx_llm_off/fx_alert/fx_share/fx_expired_export/fx_audit/fx_empty`。

| API-ID | 验收用例名 | 关键断言 | fixture | P |
|---|---|---|---|---|
| API-001 | auth_register_ok | 201、role=user、expires_in=900；重复邮箱 409 | fx_empty | P0 |
| API-002 | auth_login_and_limits | 200；错 5 次→429；admin 读 trades→403 | fx_base | P0 |
| API-003 | auth_refresh_reuse_detected | 旧 refresh→401 TOKEN_REUSED 且新令牌失效 | fx_base | P0 |
| API-004 | auth_logout | 204；refresh 随后 401 TOKEN_REVOKED | fx_base | P0 |
| API-005 | auth_me | 200 且 email/id/role 与登录一致 | fx_base | P0 |
| API-006 | account_delete_purge | 204；重新登录 401；业务表 0 行；审计留痕 | fx_base | P0 |
| API-007 | tokens_create_once | 201 token 匹配 `^pat_[A-Za-z0-9]{43}$`，之后不再返回 | fx_base | P0 |
| API-008 | tokens_list_no_secret | 200 无明文字段，仅 prefix | fx_base | P0 |
| API-009 | tokens_revoke | 204 后 PAT 调 /metrics→401 TOKEN_REVOKED | fx_base | P0 |
| API-010 | imports_preview_no_write | estimated 与期望一致；预览前后 trades 不变 | fx_missing_cash | P0 |
| API-011 | imports_commit_then_batch | 202；counts.inserted==preview；同文件再传 inserted=0；断链账单收尾对账 filled_count≥1、warning=RECONCILIATION_AUTOFILLED | fx_dup_rows/fx_broken | P0 |
| API-012 | imports_list | 200 分页含 batch_id/status；仅本人 | fx_base | P1 |
| API-013 | imports_batch_detail | 200 counts.inserted==412、file_sha256 匹配；断链导入 filled_count==len(fills)≥1、fills 含 amount/date/line_no，撤销后归零 | fx_base/fx_broken | P0 |
| API-014 | imports_quarantine_rows | 行号精确、raw 原样；跨用户 404 | fx_quarantine | P0 |
| API-015 | imports_revoke_restore_count | 200 后 trades 行数回退（含 side=missing 补差一并软删）；再撤销 409 | fx_base/fx_broken | P0 |
| API-016 | imports_reclassify | 202；新规则后隔离区条目减少；跨用户 batch 404 | fx_quarantine | P1 |
| API-017 | trades_cursor_sort_fields | 游标无重无漏；非法 sort 400；fields 裁剪；304 | fx_round30 | P0 |
| API-018 | trades_patch_version | version+1；旧 version 409；跨用户 404 | fx_base | P0 |
| API-019 | trades_soft_delete | 204；默认不可见；include_deleted=true 可见 | fx_base | P1 |
| API-020 | trades_restore_recompute | 200；恢复后指标与原值 diff=0 | fx_round30 | P1 |
| API-021 | trades_history | 200 含 ≥2 条版本记录 | fx_base | P1 |
| API-022 | summary_rules_default | 200 含 5 类默认规则且 code 唯一；仅系统+本人规则 | fx_base | P0 |
| API-023 | summary_rules_create | 201；下一次分类生效 | fx_quarantine | P0 |
| API-024 | summary_rules_update_conflict | 200；旧 If-Match→409；系统规则 404 | fx_base | P0 |
| API-025 | summary_rules_delete | 204；回退内置规则；系统规则 404 | fx_base | P1 |
| API-026 | reconciliation_ok_and_broken | ok 时 break_count=filled_count=0；断链 fixture 首次 filled_count=1、breaks[0].filled=true/filled_trade_id 非空、status=degraded；再次调用 break_count=0 且仍 degraded；/trades?side=missing 可见补差行（source=system） | fx_broken | P0 |
| API-027 | bridge_identity_balance | balanced=true、abs(residual)≤0.01、重复调用 residual/inputs_hash 相同；断链 fixture balanced=false | fx_round30 | P0 |
| API-028 | rebuild_rounds_single_flight | 202+Location；同参并发同一 job_id（X-Job-Reused）；异参 409 JOB_ALREADY_RUNNING；/rounds 可见 | fx_round30 | P0 |
| API-029 | rounds_list_visible_after_job | 200；job 成功后立即可见；跨用户 0 条；游标无重无漏 | fx_round30 | P0 |
| API-030 | rounds_detail_pnl | 200 pnl 与 golden 差≤0.01；跨用户 404 | fx_round30 | P0 |
| API-031 | rounds_fills_fifo | FIFO buy/sell 配对集合 100% 一致 | fx_round30 | P0 |
| API-032 | nav_series_oracle | points 升序；日收益 vs oracle≤1bp；日收益百分比两位小数；首日补齐昨日资产/日收益额（后续点与上一日自洽）；持仓数值最多三位小数且去尾零；blocked 无资产值 | fx_blocked | P0 |
| API-033 | metrics_batch_contract | n==12、sample_status=ok、value 小数、版本匹配 | fx_round30 | P0 |
| API-034 | metrics_explain_trace | 无裸术语；status!=ok 时 conclusion=null+what_to_do | fx_n5 | P0 |
| API-035 | metrics_drilldown_money | items 非空且金额定点；source_endpoints 可调 | fx_round30 | P0 |
| API-036 | attribution_identity | Σfactor+residual==total_pnl（≤0.01）；rank 连续 | fx_single_factor | P0 |
| API-037 | trends_version_breaks | breaks 非空且跨断点 segment 不同、不连线 | fx_version_break | P1 |
| API-038 | selection_as_of | 202→succeeded；含 BHAR n/CI；as_of 无未来财报 | fx_round30 | P0 |
| API-039 | reports_create_reuse | 202；同 (period,spec_hash) 复用同一 report_id | fx_base | P0 |
| API-040 | reports_list | 200 按 period 倒序，仅本人 | fx_base | P1 |
| API-041 | reports_detail_meta | status=succeeded 且含 checksum/data_as_of | fx_base | P0 |
| API-042 | reports_download_html | 200 text/html；Content-Disposition 正确；未完成 409 | fx_base | P0 |
| API-043 | shares_create_and_anonymous | 201 url；匿名 GET 200 仅该报告；撤销后 410 | fx_share | P1 |
| API-044 | shares_anonymous_scope | 200 且无 user/email/account 字段 | fx_share | P1 |
| API-045 | shares_revoke | 撤销后匿名访问 410 SHARE_EXPIRED | fx_share | P1 |
| API-046 | journal_create_link | 201、round_id 关联；跨用户 404 | fx_base | P0 |
| API-047 | journal_list_filter | 200 按 round_id 过滤，仅本人 | fx_base | P0 |
| API-048 | journal_update_version | version+1；旧 If-Match 409 | fx_base | P1 |
| API-049 | journal_delete | 204；列表不再返回 | fx_base | P1 |
| API-050 | journal_prompts_after_close | 平仓后 200 含对照项；无买卖建议 | fx_round30 | P1 |
| API-051 | journal_summary_quarter | 200 含兑现率与理由类型统计 | fx_base | P1 |
| API-052 | alerts_trigger_payload | 200 含 rule_code/payload；无建议词 | fx_alert | P1 |
| API-053 | alerts_ack | 200 acked_at 非空 | fx_alert | P1 |
| API-054 | notifications_list | 200 仅本人通知 | fx_alert | P2 |
| API-055 | settings_get | 200 含 benchmark/privacy/behavior_rules | fx_base | P0 |
| API-056 | settings_benchmark_switch | 200；GET /nav 的 benchmark_id 随之变化 | fx_base | P1 |
| API-057 | settings_privacy_llm_off | 200；LLM 关闭后 chat fallback=true | fx_llm_off | P1 |
| API-058 | settings_behavior_rules_effect | 200；触发后 alerts 增加对应 rule_code | fx_alert | P1 |
| API-059 | exports_create_meta | 202；下载含 meta.definition_version/schema_version | fx_base | P0 |
| API-060 | exports_download_expiry | 200 Content-Disposition；过期 410 EXPORT_EXPIRED | fx_expired_export | P0 |
| API-061 | jobs_list_backlog | 200 支持 status 过滤并含积压统计 | fx_base | P1 |
| API-062 | jobs_detail_progress | progress∈[0,100]；终态含 result_ref | fx_base | P0 |
| API-063 | jobs_sse_stream | 200 text/event-stream；progress 单调；done 后 result_ref 可 GET | fx_base | P0 |
| API-064 | jobs_cancel | 202；终态 cancelled；已终态 409 | fx_base | P1 |
| API-065 | audit_logs_own_events | 200 含 login/token.revoke；跨用户不可见 | fx_audit | P0 |
| API-066 | admin_audit_no_trades | admin 200；user 403；响应无交易字段 | fx_audit | P1 |
| API-067 | admin_status_backlog | admin 200 含队列积压；user 403 | fx_base | P1 |
| API-068 | admin_backup_trigger | 202；GET backups 含 checksum/verified | fx_base | P1 |
| API-069 | admin_backups_list | 200；user 403 | fx_base | P1 |
| API-070 | admin_restore_drill | 202 并产生演练记录 | fx_base | P1 |
| API-071 | ops_healthz | 200 且 status=ok（不查 DB） | — | P0 |
| API-072 | ops_readyz | 200 db=ok/schema_version>0；迁移滞后 503 | — | P0 |
| API-073 | ops_version | 200 含 version/api_version/git_sha/schema_version | — | P0 |
| API-074 | ops_prometheus | 200 text/plain 含 http_requests_total | — | P1 |
| API-075 | contract_openapi_snapshot | info.version 与 /version 一致；path 集合==§1 | — | P0 |
| API-076 | errors_catalog | 200 code 列表 ⊇ §3，每码含 user_message/what_to_do | — | P1 |
| API-077 | chat_session_create | 201 session_id | fx_base | P1 |
| API-078 | chat_answer_citations | 200 answer+citations；LLM 关闭仍答金额；出站无金额 | fx_llm_off | P1 |
| API-079 | metrics_detail_single | 200 且 definition_version/inputs_hash/data_as_of 齐全；blocked 无 value | fx_round30 | P0 |
| API-080 | trades_detail_owner | 200 且字段与列表一致；跨用户 404 | fx_base | P0 |
| API-081 | assets_daily_series | 200 日期倒序且 06-30 行金额==oracle（含 daily_return_pct）；未构建 items=[]+build.state=missing；blocked 行数值全 null；from/to 闭区间/status/sort/limit 校验；跨用户 0 条、admin 403 | fx_round30 | P0 |
| API-082 | assets_build_incremental | 202+Location/Retry-After 且 type=build_assets；同 key 重放同一 job_id+Idempotency-Replayed；同参 X-Job-Reused、异参 409；drain 后 build.last_mode=incremental 且 last_rows_written==窗口天数<rows_count；无锚点 build.last_status=blocked | fx_round30 | P0 |

### 6.1 端到端脚本（5 条，覆盖「注册登录 → PAT → preview → commit → 取指标 → 下载月报」）
```bash
BASE=https://api.example.com/api/v1
curl -sS -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"a@ex.com","password":"CorrectHorse9","accept_tos":true}'
JWT=$(curl -sS -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"a@ex.com","password":"CorrectHorse9"}' | jq -r .access_token)
curl -sS -X POST $BASE/tokens -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"name":"ci","scopes":["read","write"]}' | jq -r .token > pat.txt
```
```bash
PAT=$(cat pat.txt)
curl -sS -X POST $BASE/imports/preview -H "Authorization: Bearer $PAT" \
  -F file=@huatai_2026-09.xlsx -F mode=full | tee preview.json | jq '.estimated,.warnings'
curl -sS -X POST $BASE/imports -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" -d '{"preview_id":"'$(jq -r .preview_id preview.json)'","mode":"full"}' \
  | tee commit.json | jq '.job_id,.batch_id'
```
```bash
JOB=$(jq -r .job_id commit.json); BATCH=$(jq -r .batch_id commit.json)
for i in $(seq 1 30); do S=$(curl -sS $BASE/jobs/$JOB -H "Authorization: Bearer $PAT" | jq -r .status)
  [ "$S" = succeeded ] && break; sleep 1; done
curl -sS $BASE/imports/$BATCH -H "Authorization: Bearer $PAT" | jq '.counts'
```
```bash
curl -sS "$BASE/metrics?ids=win_rate,pnl_ratio,de&period=2026-09" -H "Authorization: Bearer $PAT" \
  | jq '.items[] | {metric_id,value,n,sample_status,definition_version}'
curl -sS "$BASE/metrics/de/explain?period=2026-09" -H "Authorization: Bearer $PAT" | jq '.conclusion,.drilldown'
curl -sS "$BASE/metrics/de/drilldown?period=2026-09" -H "Authorization: Bearer $PAT" | jq '.items[0]'
```
```bash
RID=$(curl -sS -X POST $BASE/reports -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" -d '{"period":"2026-09","format":["html"]}' | jq -r .report_id)
for i in $(seq 1 30); do S=$(curl -sS $BASE/reports/$RID -H "Authorization: Bearer $PAT" | jq -r .status)
  [ "$S" = succeeded ] && break; sleep 1; done
curl -sS -D - "$BASE/reports/$RID/download?format=html" -H "Authorization: Bearer $PAT" -o report.html
grep -c '2026-09' report.html
```

## §7 契约治理
1. **生成与快照**：FastAPI `app.openapi()` → `make openapi-export` 输出 `openapi/openapi.json`（`sort_keys=True`、UTF-8、无时间戳），PR 必须同 commit 更新快照；CI `make api-contract-check` 阻断漂移。
2. **破坏性变更（必须升 `/api/v2`）**：删除/改名端点、path、`operationId`；新增必填参数或请求体字段；删除响应字段、可选改必填、类型/格式变更；删除枚举值；收紧认证/scope；成功状态码变更；分页/幂等语义变更。**非破坏**：新增可选参数/响应字段/端点/错误码、放宽校验、新增枚举值（客户端必须忽略未知）。
3. **`operationId` 规范**：`<domain>_<action>[_object]` snake_case，domain 取 §1 域前缀（`auth/account/tokens/imports/trades/summary_rules/reconciliation/rounds/nav/assets/metrics/attribution/trends/diagnostics/reports/shares/journal/alerts/notifications/settings/exports/jobs/audit/admin/ops/chat`；**注**：本仓既有实现的实际 operationId 为 camelCase，如 `listAssets`/`buildAssets`，以 §1/OpenAPI 为准），action ∈ `list/get/create/update/delete/restore/revoke/run/cancel/download/stream/ack`；例 `imports_create`、`metrics_get_batch`、`jobs_stream_events`。发布后入 `openapi/operationId.lock.json`，改名视为破坏性变更。
4. **示例与错误码强制**：每 operation ≥1 个 2xx 示例（金额定点字符串、比率小数）、每个可返回错误在 `responses` 给 `application/problem+json` 示例、`x-error-codes` 列全 §3 code、`x-required-scope`、`x-idempotent`、`x-async`；缺任一项 CI 失败。
5. **契约测试与 SDK**：Schemathesis 对 staging（空库 + fixture）跑 `--checks all`；越权/幂等/游标负向用例由 API 验收套件覆盖；前端 SDK 由 `openapi-typescript` + `orval` 生成（TanStack Query hooks），`pnpm gen:sdk`，生成物 diff 非空即失败；**禁止手写请求类型**。
6. **版本兼容**：`/api/v1` 至少服务一个大版本；弃用端点响应头 `Deprecation` + `Sunset`，提前 ≥90 天在 CHANGELOG 与 `/version` 标注；expand-contract 迁移保证滚动升级期间新旧副本并存。

## §8 FR → API 追溯表（28/28 全覆盖）

| FR | 覆盖端点 API-ID | 验收要点 |
|---|---|---|
| L1 部署与服务化 | API-071~074、API-067 | `/healthz` 200；`/readyz` 200 含 db/schema_version、迁移滞后 503；`/version` 可读；滚动重启 5xx<0.1% |
| L2 认证授权与隔离 | API-001~006、API-017、API-030、API-065、API-066 | 注册 201/重复 409；登录 5 次 429；refresh 轮换+复用 401；跨用户 404；admin 读明细 403 |
| L3 两阶段导入 | API-010~016、API-061~063 | preview 不落库；commit 202→succeeded；重复文件 inserted=0；撤销后行数回退；同幂等键同 job |
| L4 数据维护修复 | API-017~021、API-080 | PATCH version 递增、旧版 409；软删默认不可见；restore 后指标 diff=0；历史可查 |
| L5 摘要分类与隔离区 | API-014、API-016、API-022~025 | 默认规则存在；未知摘要入隔离区且行号精确；规则变更后重跑生效；打新不断链 |
| L6 对账自证 | API-026、API-033 | 断链精确到行、diff≤0.01；首次调用补差 `filled_count=1`、`status=degraded`，再次调用 `break_count=0` 且补差行可查；`nav_return` 的阻断语义见 API-033 |
| L7 数据主权 | API-006、API-059、API-060、API-068~070 | 导出 202→下载 200、过期 410；备份含 checksum/verified；注销物理删除+审计 |
| L8 完整 REST 查询 | API-017、API-080 | 游标无重无漏；非法 sort 400；fields 裁剪；If-None-Match 304 |
| A1 PAT | API-007~009、API-033 | 明文仅一次；列表无明文；撤销后 401；只读 PAT 写 403；过期 401 |
| A2 契约与可验收 | API-073、API-075、API-076 | openapi info.version 与 `/version` 一致；每 operation 有示例/错误码/operationId；契约检查退出码 0 |
| A3 限流与配额 | API-028、API-038、API-039、API-059、API-061、API-067 | 第 N+1 次 429 + Retry-After；同参重算同 job；并发上限 429 |
| A4 审计日志 | API-065、API-066 | 含 login/token.revoke 且字段齐全；跨用户不可见；admin 视图无明细 |
| R1 数据服务化 | API-029、API-030 | 导入 job 成功后 /rounds 立即可见；跨用户不可见 |
| R2 期初资产与回合 | API-027~031、API-062~064 | 202→succeeded；pnl≤0.01、收益≤1bp；FIFO trade_id 集合一致；bridge balanced |
| R3 净值与收益 | API-032 | points/summary 字段齐全；blocked 无资产值；日收益 vs oracle≤1bp |
| R4 选股诊断 | API-038、API-033 | 202→succeeded；domain=selection 含 BHAR/t/n/CI；as_of 无未来数据 |
| R5 择时与绩效 | API-033、API-056、API-079 | timing 全字段含 n/CI/sample_status/data_as_of；默认基准显式标注 |
| R6 行为偏差 | API-033 | de 含 value/CI；处置效应 fixture de>0 且 CI 不含 0；无效应 fixture CI 含 0 |
| R7 归因 | API-036 | Σfactor+residual==total_pnl（≤0.01）；单因子主导 rank[0] 正确 |
| R8 统计可信度 | API-033、API-034、API-052、API-079 | n<10 → insufficient 且无结论；n≥30 有 CI；exploratory 不进行动建议 |
| D1 Web SPA | API-005、API-029、API-032、API-033、API-036、API-040、API-047、API-055 等全部读端点 | 页面数据端点 100% 登记并过验收；`GET /` 200 |
| D2 月报 | API-039~042、API-062 | 202→succeeded；下载 200 且 Content-Disposition 正确；数字与 metrics 一致；同 spec 复用 |
| D3 人话与下钻 | API-033~035 | explain 含 conclusion/method/drilldown；无裸术语；drilldown 端点可调非空 |
| D4 交易日志 | API-046~051 | 201；按 round_id 过滤；平仓后 prompts；季度 summary；跨用户不可见 |
| D5 趋势 | API-037 | points 含 definition_version；断点 breaks 且 segment 区分 |
| D6 行为规则与提醒 | API-052、API-053、API-057、API-058 | PUT 200；alerts 含 rule_code/payload；ack 200；无买卖建议 |
| D7 对话 | API-077、API-078 | 201/200；citations 可追溯；LLM 关闭仍答金额；出站不含金额/账号 |
| D8 通知与分享 | API-043~045、API-054 | 分享 201 含 url/expires_at；匿名 200 仅该报告；撤销 410；通知仅本人 |
