# 注册邮箱验证码 + 忘记密码服务开发设计（阿里云邮件推送）

> 版本：**v1.1（2026-11-22，补充忘记密码）** ｜ 状态：**已实现（2026-11-22）**：迁移 `0023_email_verification`、API-117/118/119、注册/找回前端与验收用例已落地
> 基线：`b315c23`（schema `0022_chat_turn_event`；API-Catalog 115 个端点 / 63 个错误码）
> 需求来源：用户在 2026-11-22 提出的注册邮箱验证码 + 忘记密码需求；PRD-v2 **FR-L2** 与 **OQ-1**（邮箱验证是否必做）
> **v1.1 变更**：按用户确认的推荐口径（HITL-E1~E5）冻结注册验证；新增忘记密码：复用验证码服务、`PASSWORD_RESET_ENABLED` 开关、`POST /auth/password-reset`、全量会话/PAT 撤销、防枚举发送语义。
> 关联：[PRD-v2](../prd/PRD-v2.md)、[API-Catalog](v2/API-Catalog.md)、[Architecture-v2](../Architecture-v2.md)、[DATABASE](../DATABASE.md)、[DEV-PLAN-v2](../DEV-PLAN-v2.md)
> 关联代码：`backend/services/api/src/stock_api/routers/auth.py`、`backend/services/api/src/stock_api/schemas.py`、`backend/packages/platform/src/stock_platform/ratelimit.py`、`backend/packages/platform/src/stock_platform/repositories/tokens.py`、`frontend/apps/web/src/pages/Register.tsx`、`frontend/apps/web/src/pages/Login.tsx`
> **实现记录（2026-11-22）**：迁移 `0023_email_verification`；platform 验证码/节流仓储、Argon2+pepper、DM 端口/适配器、注册/找回模板；API-117/118/119 与注册改造；前端注册验证码、登录找回入口、`/forgot-password` 页；API/前端测试已补。TTL、失败次数、限流口径、错误码、邮件模板、撤销语义在此冻结，变更须 bump 本文版本并重新评审。

---

## 0. 决策摘要

### A. 核心决策

| # | 问题 | 本设计口径 | 依据 / 代价 | 状态 |
|---|---|---|---|---|
| **D-01** | 验证码怎么接入注册 | **发送与注册分离**：新增 `POST /auth/email-codes` 发送；`POST /auth/register` 增加 `email_code` 字段并在注册事务内校验、消费。不引入“验证通过 ticket” | 少一个中间态和一次性票据，注册仍保持单事务原子性；前端在提交注册时展示后端错误 | 已定 |
| **D-02** | 是否可配置关闭 | 增加两个部署开关：`EMAIL_VERIFICATION_ENABLED`（注册是否要求验证码）+ `PASSWORD_RESET_ENABLED`（是否开放邮箱找回密码），默认均为 `false`，生产上线后置 `true`；公开只读 `GET /auth/verification-policy` 同时返回两项，前端按策略显示/隐藏验证码区与“忘记密码”入口 | 兼容 PRD OQ-1「部署方决定」；注册与找回可独立启用；支持后端先行、前端后行的平滑发布；关闭时旧客户端注册不破坏 | 已定（推荐） |
| **D-03** | 存储选型 | **PostgreSQL 两表**：`platform.email_verification_code`（每邮箱每用途一行当前验证码，`purpose` 支持 `register`/`reset_password`）+ `platform.email_code_send_throttle`（IP/邮箱冷却与配额）。不引入 Redis | 仓库明确“无 Redis/Kafka”；PG 行锁即可在单机多副本下保证一致性；两表都可按 TTL 清理 | 已定 |
| **D-04** | “同一 IP 1 分钟节流” | **精确 60 秒滚动冷却**，不是固定窗口：`now - last_sent_at < 60s` 即拒绝；IP 冷却跨用途共享，邮箱冷却按 purpose 独立；另加 IP 小时/日、邮箱小时/日配额 | 现有 `RateLimiter` 是固定窗口，窗口边界会放行两次；本功能用原子条件 upsert/行锁实现真正 60s 间隔 | 已定 |
| **D-05** | 有效期与失败次数 | TTL **600 秒**；每个验证码最多 **10 次**失败；第 10 次失败即原子锁死并返回 `EMAIL_CODE_EXPIRED`（“验证码已过期，请重新获取”） | 直接满足“10 次后提示验证码过期”；错误次数与验证码绑定，重发即重置 | 已定 |
| **D-06** | 新错误码 | 新增 `EMAIL_CODE_INVALID`(422)、`EMAIL_CODE_EXPIRED`(410)、`EMAIL_SEND_FAILED`(503)；限流复用 `RATE_LIMITED`(429)；邮箱已存在复用 `EMAIL_TAKEN`(409)。**忘记密码不新增错误码**，复用验证码/弱密码/校验错误码 | 错误码稳定、前端可分支；错误码总数 63 → 66 | 已定 |
| **D-07** | 注册事务 | 校验验证码、消费验证码、创建用户、写 `email_verified_at`、签发会话、审计在**同一事务**；验证失败时先提交失败计数再抛错（不能依赖会回滚的依赖会话直接抛） | 避免“抛错回滚掉 attempts”的坑（与现有登录失败计数同源问题）；避免码已消费但用户未创建 | 已定 |
| **D-08** | 代码落点 | `stock_platform` 放验证码/节流仓储、Argon2 哈希、邮件端口与阿里云适配器；`stock_api` 放路由、schema、事务编排；`stock_contracts` 放常量与错误码 | 不新增 `stock_identity` 包、不改 L1–L5；认证前置表沿用 platform 例外 | 已定 |
| **D-09** | 邮件投递 | 同步调用阿里云 DM `SingleSendMail`，**默认凭据链**（`CredentialClient()`），SDK 懒加载并放到线程池，5s 超时；本地/测试用可替换 sender | 与用户提供的示例一致；生产不在请求内持有 DB 事务；无 AK 入库 | 已定 |
| **D-10** | 邮件样式 | 表格布局 + 内联样式 + 纯文本备选；品牌色沿用前端 tokens（纸感浅色 `#f6f5f0` / 墨绿 `#3b5f4b`）；验证码大号等宽字体、10 分钟提示、防转发提示 | 兼容 Gmail/QQ/Outlook；降低进垃圾箱概率；模板见附录 A | 已定 |
| **D-11** | 前端接入 | 注册页增加“邮箱验证码”行；新增 `/forgot-password` 页；登录页按策略显示“忘记密码”入口；错误统一走 `ProblemView`；过期时清空输入并允许重发 | 与现有 RFC 7807 错误展示一致；无新 UI 框架 | 已定 |
| **D-12** | 契约编号 | 新增 **API-117**（发送验证码，`purpose=register/reset_password`）、**API-118**（认证验证策略）、**API-119**（重置密码）；修订 API-001；端点 115 → **118**；建议归入 **Period3-12（认证增强）** | 先改 API-Catalog/快照，再写代码；同步 SDK 与验收矩阵 | 已定（编号可评审） |
| **D-13** | 忘记密码流程 | **复用验证码 + 一次性提交**：`POST /auth/email-codes`（`purpose=reset_password`）发码；`POST /auth/password-reset` 校验码并设置新密码；**不自动登录**，成功后回登录页 | 少一个 reset ticket/中间态；验证码一次性消费；避免把“重置成功”直接变成会话 | 已定（推荐） |
| **D-14** | 重置后的凭证处理 | 成功重置后：更新 `password_hash`、必要时补写 `email_verified_at`、**撤销全部 refresh token + 全部 PAT**、删除该邮箱所有验证码；现有 access JWT 最长 15 分钟内仍有效（无 token version） | 立即阻断续期与脚本访问；access 残窗是已知限制，见 §13.5；不引入全局 jti 黑名单 | 已定（推荐） |
| **D-15** | 忘记密码防枚举 | 重置发码对“已注册/未注册/已禁用”统一返回 **200 通用语义**；只有 IP 冷却/配额返回 429；邮箱维度配额命中时**静默不发**，不返回可区分错误 | 防止通过发码接口枚举账号；代价是用户可能“看到成功但没收到”，用通用文案+重试兜底 | 已定（推荐） |

### B. 非目标（明确不做）

- 不做“修改邮箱 / 登录二次验证 / 密码历史 / 密码过期策略”；本设计只做注册验证与忘记密码。
- 不做验证码通过后的短期 ticket、免密登录或自动登录；重置成功回登录页。
- 不做邮件事件回调（送达/退信/投诉）与多供应商自动切换；本期只记录 DM 请求结果。
- 不做 CAPTCHA / 人机校验；先靠 IP + 邮箱 + 全局配额，后续可加。
- 不回溯标记历史用户 `email_verified_at`；存量用户保持原状（忘记密码成功后可顺带补写）。
- 不做邮件内容多语言；本期中文。
- 不把验证码、新密码明文写入日志、审计、URL 或前端持久化存储。
- 不做“重置成功通知邮件”作为 P0；列为 P1 可选项（见 §13.9）。

### C. 评审决策项（2026-11-22 已确认按推荐实现）

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| **HITL-E1** | 生产是否强制邮箱验证 | A 生产 `EMAIL_VERIFICATION_ENABLED=true`，本地/CI 默认 false；B 所有环境强制 | **A**（兼顾发布顺序与本地开发） |
| **HITL-E2** | 发送接口对已注册邮箱 | A 注册返回 409 `EMAIL_TAKEN`；找回统一 200 通用响应；B 两者都恒定 200 | **A**（注册本身已暴露存在性；找回必须防枚举） |
| **HITL-E3** | 第 10 次失败响应 | A 第 10 次直接 `EMAIL_CODE_EXPIRED`；B 第 10 次仍 `EMAIL_CODE_INVALID`、第 11 次才过期 | **A**（严格对应“10 次后提示过期”） |
| **HITL-E4** | IP 信任边界 | A 只信 nginx 覆盖的 `X-Real-IP`，且 API 不直接暴露公网；B 直接信任 `X-Forwarded-For` | **A**（B 可被客户端伪造，等于没有 IP 限流） |
| **HITL-E5** | 验证码位数 | A 6 位数字；B 8 位数字 | **A**（10 次上限下 6 位足够，体验更好） |

> **2026-11-22 人工确认**：HITL-E1~E5 全部按推荐执行；v1.1 新增 D-13~D-15（忘记密码流程、全量凭证撤销、防枚举发送）同样按推荐实现。若评审提出不同意见，再逐项调整并 bump 版本。

---

## 1. 背景、目标与验收

### 1.1 需求拆解

| # | 需求 | 本设计落点 |
|---|---|---|
| R1 | 新增邮箱验证码服务 | API-117 + `EmailVerificationService` + DM 适配器 |
| R2 | 同一 IP 发送验证码 1 分钟节流 | 精确 60s IP 冷却 + 小时/日配额；`429 RATE_LIMITED` + `Retry-After` |
| R3 | 验证码 10 分钟有效 | `expires_at = now + 600s`；过期返回 `EMAIL_CODE_EXPIRED` |
| R4 | 验证时有 10 次失败机会 | 每码 `max_attempts=10`；第 10 次失败锁死并提示过期 |
| R5 | 使用阿里云邮件推送 + 默认凭据 | `AliyunDirectMailSender`，`CredentialClient()`，无 AK 入库 |
| R6 | 邮件内容样式优化 | 品牌化 HTML + 纯文本备选（附录 A） |
| R7 | 前端注册接入，错误显示 | 注册页验证码行 + `ProblemView` + 过期重发交互 |
| R8 | 可发布、可回滚 | 两个开关 + 认证验证策略端点 + 分阶段发布 |
| R9 | 忘记密码 | 复用验证码（`purpose=reset_password`）+ `POST /auth/password-reset`；不泄露账号存在性；不自动登录 |
| R10 | 重置后安全收口 | 更新密码、补写邮箱已验证、撤销全部 refresh token 与 PAT；access 15 分钟残窗显式披露 |

### 1.2 目标（可验收）

1. **发送可限流**：同一 IP 两次成功发送之间至少 60 秒；同一邮箱同一用途同样至少 60 秒；超限返回 429 且 `Retry-After` 为正整数（忘记密码的邮箱配额按 §13.2 静默处理）。
2. **验证码一次性**：同一验证码最多成功消费一次；重发后旧码立即失效；过期、已消费、锁定、发送失败的码统一返回“过期”语义。
3. **失败次数可解释**：第 1~9 次错误返回 422 并提示剩余次数；第 10 次返回 410 且文案明确“验证码已过期，请重新获取”。
4. **注册原子**：密码弱、未同意 TOS、邮箱已存在等前置错误不消耗验证码次数；成功时验证码消费与用户创建同事务。
5. **无明文**：DB 不存明文验证码；日志/审计/URL 不含验证码、完整邮箱、AK；验证码表存邮箱用于投递/覆盖/清理，IP 只存哈希；节流表只存 `scope+key` 哈希。
6. **可关闭**：`EMAIL_VERIFICATION_ENABLED=false` 时注册行为与当前完全一致；`PASSWORD_RESET_ENABLED=false` 时登录页不显示找回入口。
7. **忘记密码不枚举**：重置发码对已注册/未注册/禁用账号返回同一 200 语义；只有 IP 级限流返回 429。
8. **重置可收口**：成功重置后旧密码立即失效、refresh token 与 PAT 全部撤销；不自动签发新会话。
9. **可测试**：不依赖真实阿里云即可跑全部 API 测试；真实 DM 只用于手工/预发验收。

### 1.3 验收总览

| 层级 | 验收 |
|---|---|
| 单元 | 6 位补零、Argon2+pepper 校验、TTL、10 次状态迁移、60s 冷却边界、IP 解析、重置凭证撤销 |
| API | 注册发送/校验；重置发码通用 200；重置 204/422/410；旧密码失效；refresh/PAT 撤销；防枚举 |
| 契约 | API-Catalog §1/§2/§3/§6 同步；`make api-snapshot`、`make api-check`、`make sdk` 无漂移 |
| 前端 | 策略显示/隐藏、60s 倒计时、验证码错误/过期展示、注册提交带 `email_code`、忘记密码两步表单、可访问性 |
| 手工 | 真实 DM 发信到 Gmail/QQ/Outlook；SPF/DKIM/DMARC 通过；HTML 不被截断 |
| 安全 | `X-Forwarded-For` 伪造无效；验证码/邮箱/新密码不落日志；数据库无明文码；重置后旧会话失效 |

### 1.4 术语

| 术语 | 含义 |
|---|---|
| 验证码记录 | `platform.email_verification_code` 中 `(email, purpose)` 对应的一行；只保留当前码 |
| 注册码 / 重置码 | `purpose=register` / `purpose=reset_password` 的验证码；两套记录互不覆盖 |
| 冷却（cooldown） | 上次成功发送与本次发送之间的最小间隔；本设计为 60s 滚动窗口 |
| 配额（quota） | 小时/日固定窗口内的最大成功发送次数；与冷却独立 |
| 锁定（locked） | 失败次数达到 `max_attempts` 后的终态；后续验证统一按过期处理 |
| 认证验证策略 | `GET /auth/verification-policy` 返回的公开配置：注册是否要求验证码、是否开放找回密码 |
| 通用 200 | 忘记密码发码对存在/不存在账号返回同一响应体，不暴露账号是否存在 |
| 默认凭据链 | 阿里云 SDK `CredentialClient()` 依次读取环境变量、配置文件、ECS RAM 角色、OIDC 等 |

---

## 2. 现状与差距（基线 `b315c23`）

### 2.1 现状证据

| 项 | 现状 | 证据 |
|---|---|---|
| 注册端点 | `POST /auth/register` 只收 `email/password/display_name/accept_tos`；注册后直接签发会话 | `backend/services/api/src/stock_api/routers/auth.py:100-141`；`schemas.py:21-26` |
| 用户已验证字段 | `platform.user.email_verified_at` 已存在，但注册路径从不写入；`UserBrief.email_verified` 只按非空判断 | `platform/models.py:45-61`；`routers/auth.py:49-56` |
| 邮箱验证 | 无发送、无存储、无校验；PRD OQ-1 仍列为未决 | `docs/architecture/v2/README.md:8`；`docs/prd/PRD-v2.md:552` |
| 限流 | `RateLimiter` 是 PG 固定窗口桶（`window_start + count`），适合“每小时 N 次”，不适合精确 60s 间隔 | `platform/ratelimit.py:30-47` |
| IP 解析 | 登录处 `_ip_hash` 直接取 `X-Forwarded-For` 最左值；客户端可伪造 | `routers/auth.py:316-325`；`deps.py:206-215` |
| 邮件依赖 | `stock-platform` 无阿里云 DM SDK；无邮件端口/模板 | `backend/packages/platform/pyproject.toml` |
| 前端注册 | 只有邮箱/密码/确认密码/昵称/TOS；错误统一 `ProblemView` | `frontend/apps/web/src/pages/Register.tsx` |
| 前端客户端 | `api.auth.register/login/refresh/logout/me`；无验证码方法 | `frontend/packages/api-client/src/http.ts:537-555` |
| 配置 | 无 `EMAIL_*`；`Settings` 已有 pydantic-settings 模式 | `platform/config.py` |
| 忘记密码 | 无重置端点、无重置邮件、无“全部凭证撤销”入口；`RefreshTokenRepository.revoke_all_for_user` 已有，但 `ApiTokenRepository` 无批量撤销 | `routers/auth.py`；`repositories/tokens.py` |

### 2.2 差距清单

1. **没有验证码生命周期模型**：发送、过期、失败计数、锁定、消费都无落点。
2. **没有精确 60s 冷却**：固定窗口会在 59s/61s 边界放行两次，不满足“同一 IP 1 分钟”。
3. **没有邮件端口**：直接依赖阿里云 SDK 会让测试和本地启动耦合外部服务。
4. **没有可关闭策略**：直接强制验证会让旧前端/旧测试一次性失效，发布不可平滑。
5. **没有防枚举/防轰炸分层**：只做 IP 限流会被“多 IP 打同一邮箱”绕过。
6. **没有忘记密码链路**：无法证明邮箱所有权后安全更新密码，也没有重置后的会话/PAT 收口。
7. **没有契约编号**：需要新增 API-117/118/119、错误码、验收矩阵与 SDK 类型。

### 2.3 不可违反的工程约束

- 金额/价格/数量/比率一律标度整数；本功能不涉及金额，但**时间/次数用整数秒/整数计数**，不用浮点。
- 时间必须通过 `Clock` 注入；TTL、冷却、配额窗口全部基于 `clock.now()`，测试用 `FrozenClock` 复放。
- 仓储方法显式收 `user_id`；**本功能是认证前置**（注册前未知 `user_id`），`platform.email_verification_code` / `platform.email_code_send_throttle` 按 `platform.user` / `login_attempt` 的先例**豁免**，仓储 docstring 必须写明。
- 新端点先改 API-Catalog/OpenAPI，再写代码；每个 API-ID 至少 happy / 越权或边界 / 关键错误码三类用例。
- 错误一律 `ApiError(ErrorCode.*)`，禁止 `HTTPException`；错误码必须同时改 `errors.py`、API-Catalog §3、`SEED_ERROR_COUNT`。
- 新增表必须走 Alembic，显式授权 `stock_app`（DML）与 `stock_backup`（只读），并同步 `SCHEMA_VERSION`。
- 前端金额/比率零口径；本功能只处理整数秒与布尔策略，不引入数值解析。

---

## 3. 总体设计

### 3.1 组件与分层

```text
┌──────────────── 浏览器（Register.tsx / ForgotPassword.tsx）───────────────────┐
│ 1) GET /auth/verification-policy → 是否显示注册验证码 / 忘记密码入口           │
│ 2) POST /auth/email-codes {email, purpose} → 60s 倒计时 + 显示验证码输入       │
│ 3a) POST /auth/register {..., email_code} → 201 / ProblemView                 │
│ 3b) POST /auth/password-reset {email, email_code, new_password} → 204         │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ HTTPS（同源 nginx）
┌───────────────────────────────────▼─────────────────────────────────────────────┐
│ stock_api.routers.auth                                                          │
│   ├─ get_verification_policy（公开只读；注册 + 找回）                            │
│   ├─ send_email_code（公开；purpose=register/reset_password；事务 + 邮件）       │
│   ├─ register（公开；验证码校验/消费 + 用户创建 + 会话签发，同一事务）           │
│   └─ reset_password（公开；验证码校验/消费 + 改密 + 全量凭证撤销，同一事务）     │
└───────────────┬───────────────────────────────────────────────┬─────────────────┘
                │                                               │
┌───────────────▼──────────────────┐              ┌─────────────▼─────────────────┐
│ stock_platform.email_verification│              │ stock_platform.mail           │
│  ├─ EmailVerificationRepository  │              │  ├─ EmailSender Protocol      │
│  ├─ EmailCodeThrottleRepository  │              │  ├─ AliyunDirectMailSender    │
│  ├─ EmailCodeHasher(Argon2+pepper)│             │  └─ LogEmailSender（本地/测试）│
│  ├─ render_registration_email    │              └───────────────────────────────┘
│  └─ render_password_reset_email  │
└───────────────┬──────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────────────────────────┐
│ PostgreSQL 16（platform schema）                                                │
│  email_verification_code（email, purpose 主键；register / reset_password）       │
│  email_code_send_throttle（scope, key_hash 主键；冷却与小时/日计数）             │
│  user / refresh_token / api_token（重置成功后批量撤销）                          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 分层职责

| 层 | 文件（建议） | 职责 | 禁止 |
|---|---|---|---|
| contracts | `stock_contracts/constants.py`、`errors.py` | TTL、最大次数、冷却、新错误码 | 任何 I/O / DB / HTTP |
| platform | `stock_platform/models.py`、`repositories/email_codes.py`、`repositories/users.py`、`repositories/tokens.py`、`email_verification.py`、`mail.py`、`mail_aliyun.py`、`email_templates.py`、`security.py` 扩展 | 验证码哈希、仓储、事务内状态迁移、改密、凭证撤销、邮件端口/适配器、注册/找回模板 | 不放 HTTP 路由；不依赖 stock_api |
| api | `stock_api/schemas.py`、`routers/auth.py`、`request_ip.py`、`app.py`、`deps.py`、`cli.py` | Pydantic 契约、事务编排、错误映射、IP 解析、启动装配、清理 CLI | 不在 router 里直接建 SDK client；不打印验证码/新密码 |
| frontend | `api-client/http.ts`、`contract.ts`、`pages/Register.tsx`、`pages/Login.tsx`、`pages/ForgotPassword.tsx`、`router.tsx`、`styles.css`、测试 | 策略获取、发送、倒计时、错误展示、注册/找回表单、可访问性 | 不把验证码/密码写 localStorage；不解析业务数值 |

### 3.3 发送流程（API-117）

> `purpose` 只允许 `register` / `reset_password`。两者的**发码基础设施相同**，但错误语义不同：注册严格报错，忘记密码防枚举。

```text
1. 校验 email / purpose；normalize_email（trim + lower）；检查对应开关
2. 解析 client_ip_hash（只信 nginx 的 X-Real-IP；见 §7.2）
3. 开启事务，先预占 IP 冷却/配额（scope=ip，所有用途共享）
4. 分支：
   A. purpose=register：
      A1. 用户已存在 → 409 EMAIL_TAKEN（回滚，不发信）
      A2. 预占邮箱冷却/配额（scope=email，key 含 purpose=register）→ 拒绝则 429
      A3. 生成 6 位码/哈希；UPSERT purpose=register；审计；提交
      A4. 事务外发信；失败 → 标记 failed + 清哈希 → 503 EMAIL_SEND_FAILED
   B. purpose=reset_password：
      B1. 查用户；不存在/非 active → 提交（IP 冷却已消耗）→ 200 通用响应，不发信
      B2. 用户存在 → 预占邮箱冷却/配额（scope=email，key 含 purpose=reset_password）
          拒绝 → 提交（IP 冷却已消耗）→ 200 通用响应，不发信（不返回 429，防枚举）
      B3. 生成 6 位码/哈希；UPSERT purpose=reset_password；审计；提交
      B4. 事务外发信；失败 → 标记 failed + 清哈希、记日志/告警 → 仍返回 200 通用响应
```

**关键点**：

- 冷却与验证码写入在**同一事务**；邮件发送在事务外，避免网络 I/O 持有行锁/连接。
- 注册发送失败会消耗本轮冷却（已提交的节流计数不回滚），用户最多等 60s 重试；这是为了避免“供应商故障时无限重试”放大故障。
- 忘记密码的 IP 冷却即使账号不存在也会消耗（否则可无限探测）；邮箱配额拒绝/发信失败**不改变响应**，避免枚举。
- 发送成功但响应丢失时，用户重试会得到 429（注册）或通用 200（找回，可能静默不发）；邮件可能已到达，属于可接受重复。

### 3.4 注册校验流程（API-001 修订）

```text
1. 校验 accept_tos（false → 422，不碰验证码）
2. 校验密码强度（弱 → 422，不碰验证码）
3. normalize email；UserRepository.by_email 已存在 → 409 EMAIL_TAKEN（不碰验证码）
4. 若 EMAIL_VERIFICATION_ENABLED=true：
   4.1 email_code 缺失/格式错 → 422 VALIDATION_ERROR（field=email_code）
   4.2 SELECT ... FOR UPDATE 当前 (email,purpose) 行
       - 无行 / status != active / expires_at <= now / attempts >= max_attempts
         → 置 expired/locked（如适用）、清 code_hash、提交、返回 410 EMAIL_CODE_EXPIRED
       - Argon2 校验失败 → attempts+1；若达到 max_attempts → locked；提交后
         返回 422 EMAIL_CODE_INVALID（1~9 次）或 410 EMAIL_CODE_EXPIRED（第 10 次）
       - 校验成功 → 置 consumed、consumed_at、清 code_hash（不提交，继续 5）
5. 创建 user（email_verified_at=now）；初始化 user_setting；签发 access/refresh；
   审计 auth.register（meta 不含验证码）
6. 提交事务 → 201 AuthOut
```

**关键事务细节**：注册 handler 不用会随异常回滚的 `get_system_session` 依赖直接抛 `ApiError`。改为显式：

```python
async with state.db.session(begin=True) as session:
    outcome = await email_codes.verify(session, email=email, code=code, now=now)
    if outcome.ok:
        user = await users.create(..., email_verified_at=now)
        ...
        response = await _issue_session(...)
    else:
        await session.commit()  # 先提交 attempts/status，再在事务外抛错
if not outcome.ok:
    raise _verification_error(outcome)
return response
```

> 现有登录失败计数也踩过同类坑（`routers/auth.py` docstring：“失败留痕与令牌签发在同一已提交事务内，避免抛错回滚掉计数”）。本设计把这条经验显式写进注册路径。

### 3.5 验证码状态机

```text
                 ┌──────────────┐
    发送/重发 ───▶│    active    │
                 └──────┬───────┘
        ┌───────────────┼────────────────┬─────────────────┐
        │ 注册成功      │ 10 次失败       │ 过期被访问       │ 邮件发送失败
        ▼               ▼                ▼                 ▼
   ┌─────────┐     ┌─────────┐      ┌─────────┐       ┌─────────┐
   │consumed │     │ locked  │      │ expired │       │ failed  │
   └─────────┘     └─────────┘      └─────────┘       └─────────┘
        │               │                │                 │
        └───────────────┴────────────────┴─────────────────┘
                    终态；重发时整行覆盖为 active
```

- 状态列用 `text` + 应用层校验（不建 PG ENUM/CHECK，后续加 `reset_password` 等用途免迁移）。
- 终态统一清空 `code_hash`（或写空串），避免离线撞库；行保留用于审计/排障，按 TTL 清理。
- `expired` 是**惰性**状态：TTL 到点后不一定有后台任务改状态；验证时发现过期会落 `expired`，未访问的行由清理任务删除。

### 3.6 并发与竞态

| 场景 | 机制 | 结果 |
|---|---|---|
| 同一 IP 并发两次发送 | `email_code_send_throttle` 行 `FOR UPDATE`，串行判断 60s | 一次成功，另一次 429（注册）或通用 200（找回） |
| 同一邮箱并发两次发送（同用途） | 同一行邮箱节流键 + 验证码行 UPSERT | 一次成功；后一次被 60s 冷却拒绝 |
| 同一邮箱同时发注册码/重置码 | 邮箱节流 key 含 purpose，两行独立；IP 行共享 | 受 IP 60s 限制，最多一个；另一用途需等 60s |
| 不同 IP、同一邮箱 | 邮箱节流键串行 | 只有一次成功 |
| 发送与注册并发 | 注册对验证码行 `FOR UPDATE`；发送 UPSERT 等待 | 注册先消费则发送覆盖为新码；注册已成功后发送可在用户存在检查的竞态窗口内产生一条无用码，由 TTL 清理（不影响注册） |
| 发送与重置并发 | 重置对 `purpose=reset_password` 行 `FOR UPDATE`；发送 UPSERT 等待 | 重置先消费则重置成功、发送覆盖新码；旧码不可再用 |
| 同一码并发注册/重置 | 验证码行 `FOR UPDATE` + `status=active` 条件消费 | 只有一个事务能消费；另一个得到 410/409，用户表唯一约束兜底 |
| 同一账号并发重置 | 行锁串行；第一个改密并撤销全部凭证，第二个 410 | 密码只改一次；后一个用户需重新发码 |
| API 进程崩溃 | 事务回滚；已提交的码未发信 | 用户等 60s 重发；不会产生半个用户/半个改密 |

### 3.7 失败与降级矩阵

| 故障 | 行为 | 用户看到 |
|---|---|---|
| DM 超时/5xx/凭据失败（注册） | 码置 `failed`，清哈希 | 503 `EMAIL_SEND_FAILED`，`Retry-After: 60` |
| DM 超时/5xx/凭据失败（找回） | 码置 `failed`，清哈希；日志/告警 | 通用 200（防枚举），用户可 60s 后重发 |
| DM 配额耗尽（注册） | 同上；日志/指标 `provider_code=DailyLimit` | 503；运维告警 |
| DM 配额耗尽（找回） | 通用 200；日志/指标 | 用户可能收不到，按通用文案重试 |
| DB 不可用 | 500/503；不发送 | 现有 RFC 7807 |
| 邮件已发但响应丢失 | 码仍 active；注册重试 429 / 找回通用 200 | 用户可能收到邮件，按倒计时等待 |
| 用户连续输错 | 1~9 次 422；第 10 次 410 | `ProblemView` 显示剩余次数/过期 |
| 用户未收到邮件 | 60s 后可重发；旧码立即失效 | 新码覆盖旧码 |
| 注册配置关闭 | 策略 `email_verification_required=false` | 注册页不显示验证码区 |
| 找回配置关闭 | 策略 `password_reset_enabled=false` | 登录页不显示“忘记密码”，直达页显示未开放 |

### 3.8 忘记密码流程（API-117 + API-119）

```text
1. 登录页读取策略；password_reset_enabled=true 才显示“忘记密码”
2. 用户输入邮箱 → POST /auth/email-codes {email, purpose=reset_password}
   - IP 60s 内重复 → 429
   - 邮箱存在/不存在/禁用 → 统一 200 通用响应
3. 用户输入邮件中的 6 位码 + 新密码 + 确认密码
4. POST /auth/password-reset {email, email_code, new_password}
   4.1 校验 PASSWORD_RESET_ENABLED / 密码强度 / 新旧密码不同（不消耗次数）
   4.2 查用户；不存在/非 active → 410 EMAIL_CODE_EXPIRED（防枚举）
   4.3 SELECT ... FOR UPDATE purpose=reset_password 行；校验/计数/消费（同注册）
   4.4 更新 password_hash；email_verified_at 为空则补写 now
   4.5 撤销全部 refresh token；撤销全部 PAT
   4.6 删除该邮箱所有验证码行；审计 auth.password_reset；提交
5. 返回 204；前端跳 /login?reset=1，用户用新密码登录（不自动签发会话）
```

---

## 4. 数据模型

### 4.1 `platform.email_verification_code`

每个 `(email, purpose)` 只保留**当前一条**验证码；重发直接覆盖，避免“多码并存”导致旧码仍可用。

```sql
CREATE TABLE platform.email_verification_code (
    email            citext       NOT NULL,
    purpose          varchar(24)  NOT NULL DEFAULT 'register',
    code_hash        text         NOT NULL,
    status           varchar(16)  NOT NULL DEFAULT 'active',
    attempts         smallint     NOT NULL DEFAULT 0,
    max_attempts     smallint     NOT NULL DEFAULT 10,
    sent_at          timestamptz  NOT NULL,
    expires_at       timestamptz  NOT NULL,
    consumed_at      timestamptz,
    last_attempt_at  timestamptz,
    send_ip_hash     varchar(64),
    request_id       varchar(64),
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (email, purpose),
    CONSTRAINT ck_email_code_attempts
        CHECK (attempts >= 0 AND attempts <= max_attempts),
    CONSTRAINT ck_email_code_expiry
        CHECK (expires_at > sent_at),
    CONSTRAINT ck_email_code_terminal_hash
        CHECK (status = 'active' OR code_hash = '')
);

CREATE INDEX ix_email_code_expires
    ON platform.email_verification_code (expires_at);
CREATE INDEX ix_email_code_status_expires
    ON platform.email_verification_code (status, expires_at);
```

说明：

- `email` 用 `citext`，与 `platform.user.email` 一致；应用层仍统一 `trim().lower()`。
- `purpose` 允许 `register` / `reset_password`，用 `text` + 应用层校验；未来加用途不需要迁移枚举。
- `status ∈ {active, consumed, locked, expired, failed}`；终态 `code_hash=''`。
- `max_attempts` 存在行上，配置变更不影响已发出的码。
- `send_ip_hash` 只存 `sha256(ip)[:32]`，不存原始 IP。
- `request_id` 用于把邮件发送与审计/日志串起来。
- 同一邮箱可同时存在一条 `register` 和一条 `reset_password` 记录，互不覆盖；成功后按用途消费。

### 4.2 `platform.email_code_send_throttle`

按 `(scope, key_hash)` 一行，`scope ∈ {ip, email}`；`key_hash` 是哈希，不存原始 IP/邮箱。

```sql
CREATE TABLE platform.email_code_send_throttle (
    scope          varchar(16)  NOT NULL,
    key_hash       varchar(64)  NOT NULL,
    last_sent_at   timestamptz,
    hour_start     timestamptz,
    hour_count     smallint     NOT NULL DEFAULT 0,
    day_start      timestamptz,
    day_count      smallint     NOT NULL DEFAULT 0,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key_hash),
    CONSTRAINT ck_email_throttle_counts
        CHECK (hour_count >= 0 AND day_count >= 0)
);

CREATE INDEX ix_email_throttle_last_sent
    ON platform.email_code_send_throttle (last_sent_at);
```

`last_sent_at` 允许为 `NULL`：首次预占时插入空行，行锁后判断 `last_sent_at IS NULL OR now - last_sent_at >= cooldown`，通过后写 `now`。这样不会出现“插入即被自己冷却”的问题。

`key_hash` 口径：

- IP：`sha256("ip:" + ip_hash)`；**所有用途共享**，保证同一 IP 1 分钟总闸。
- 邮箱：`sha256("email:" + purpose + ":" + normalized_email)`；注册码与重置码各有一套配额，互不挤占。
- 未注册邮箱的找回请求只预占 IP，不写邮箱节流行，避免被任意邮箱探测撑大表。

### 4.3 迁移、授权与 RLS

- 迁移编号：`0023_email_verification`；`down_revision='0022'`；`SCHEMA_VERSION` 同步为 `0023_email_verification`，`stock_api/version.py` 同步。
- 授权：
  ```sql
  GRANT SELECT, INSERT, UPDATE, DELETE
    ON platform.email_verification_code, platform.email_code_send_throttle
    TO stock_app;
  GRANT SELECT
    ON platform.email_verification_code, platform.email_code_send_throttle
    TO stock_backup;
  ```
- **不启用 RLS**：两张表都是认证前置表，注册前没有 `user_id`，与 `platform.user` / `refresh_token` / `login_attempt` 同口径。必须在 `docs/DATABASE.md` §2.1 的“按设计不启用 RLS 的认证/系统表”中显式登记原因；若 `gate_rls_coverage.py` 口径改为扫描全部 platform 表，则加入 `ALLOWLIST` 并写明“注册前按 email/IP 访问，无 user_id 语义”。
- 仓储 docstring 必须写明此例外，禁止后续误加 `user_id` 过滤。
- 忘记密码只使用 `purpose=reset_password`，不新增表/列，因此 `0023` 迁移同时覆盖注册与找回。

### 4.4 保留与清理

| 数据 | 保留 | 清理 |
|---|---|---|
| 验证码行（终态/过期） | 24 小时（排障） | `purge-email-codes` 删除 `expires_at < now() - 24h` |
| 验证码行（active 但已过期） | 24 小时 | 同上 |
| 节流行 | 2 天（覆盖日窗口） | `last_sent_at < now() - 2d AND day_start < now() - 2d` |
| 审计 `auth.email_code.send` | 按现有审计保留策略 | 不额外处理 |

CLI：`uv run python -m stock_api.cli purge-email-codes --older-than-hours 24`；生产 cron 每日一次。清理是幂等 DELETE，失败不影响注册。

### 4.5 仓储接口（显式例外）

```python
class EmailVerificationRepository:
    """认证前置表：注册前尚无 user_id，方法不收 user_id。

    这是与根 AGENTS「仓储必须显式收 user_id」的显式例外，原因同
    platform.user / refresh_token / login_attempt：按 email 查询。
    与既有仓储一致：构造函数收 session，方法不再重复传 session。
    """

    def __init__(self, session): ...

    async def get_for_update(self, *, email, purpose) -> EmailCodeRow | None: ...
    async def upsert(self, *, email, purpose, code_hash, now, expires_at,
                     max_attempts, ip_hash, request_id) -> EmailCodeRow: ...
    async def record_failure(self, *, email, purpose, now) -> FailureOutcome: ...
    async def consume(self, *, email, purpose, now) -> bool: ...
    async def mark_terminal(self, *, email, purpose, status, now) -> None: ...
    async def delete_for_email(self, *, email) -> None: ...
    async def purge(self, *, before) -> int: ...


class EmailCodeThrottleRepository:
    def __init__(self, session): ...

    async def reserve(self, *, scope, key_hash, now,
                      cooldown_seconds, hour_limit, day_limit) -> Reservation: ...
    async def delete_for_key(self, *, scope, key_hash) -> None: ...
    async def purge(self, *, before) -> int: ...
```

`record_failure` 内部用单条 SQL 原子完成：

```sql
UPDATE platform.email_verification_code
SET attempts = attempts + 1,
    last_attempt_at = :now,
    status = CASE WHEN attempts + 1 >= max_attempts THEN 'locked' ELSE status END,
    code_hash = CASE WHEN attempts + 1 >= max_attempts THEN '' ELSE code_hash END,
    updated_at = :now
WHERE email = :email AND purpose = :purpose
  AND status = 'active' AND expires_at > :now AND attempts < max_attempts
RETURNING attempts, status;
```

若返回 `attempts = max_attempts`，调用方返回 `EMAIL_CODE_EXPIRED`（第 10 次即过期）；否则返回 `EMAIL_CODE_INVALID` 和剩余次数。

---

## 5. API 契约

### 5.1 API-118 `GET /auth/verification-policy`（新增）

- 用途：前端在注册页/登录页/找回页加载时获取认证策略：注册是否要求验证码、是否开放找回密码，以及验证码参数。
- 认证：公开；幂等：不适用；同步。
- 响应 200：
  ```json
  {
    "email_verification_required": true,
    "password_reset_enabled": true,
    "code_length": 6,
    "code_ttl_seconds": 600,
    "resend_after_seconds": 60,
    "max_attempts": 10
  }
  ```
- 响应头：`Cache-Control: no-store`。
- 错误码：仅现有 500/503（无业务错误）。
- 说明：
  - `email_verification_required=EMAIL_VERIFICATION_ENABLED`；`password_reset_enabled=PASSWORD_RESET_ENABLED`，两者可独立。
  - 即使对应开关为 false 也返回其余字段，便于前端预热；前端不得缓存该响应。
  - v1.0 的 `GET /auth/registration-policy` 路径在实现前改为现名；若已有调用方，保留旧路径作为同 handler 的弃用别名一个版本（见 §13.11）。
- `operation_id="getVerificationPolicy"`。

### 5.2 API-117 `POST /auth/email-codes`（新增）

- 用途：发送注册/找回密码邮箱验证码。
- 认证：公开；幂等：**不支持**（发送本身非幂等；重复请求由冷却限制）；同步。
- `operation_id="sendEmailVerificationCode"`。
- 请求：
  ```json
  { "email": "user@example.com", "purpose": "register" }
  ```
  `purpose` 可选，默认 `register`；允许 `register` / `reset_password`（其他值 422）。
- 响应 200：
  ```json
  {
    "email": "user@example.com",
    "purpose": "register",
    "expires_in": 600,
    "resend_after": 60,
    "max_attempts": 10,
    "code_length": 6,
    "expires_at": "2026-11-22T04:10:00Z"
  }
  ```
- 响应头：`Cache-Control: no-store`；成功时可带
  `X-RateLimit-Limit: 1`、`X-RateLimit-Remaining: 0`、`X-RateLimit-Reset: <epoch>`（表示“本 IP 冷却桶”）。
- 错误码（`purpose=register`）：
  | code | HTTP | 触发 | Retry-After |
  |---|---|---|---|
  | `VALIDATION_ERROR` | 422 | email 非法 / purpose 非法 / 功能未启用 | 无 |
  | `EMAIL_TAKEN` | 409 | 该邮箱已注册（purpose=register） | 无 |
  | `RATE_LIMITED` | 429 | IP 冷却/配额、邮箱冷却/配额 | 正整数秒 |
  | `EMAIL_SEND_FAILED` | 503 | DM 超时/5xx/凭据或配额错误 | 60 |
  | `INTERNAL_ERROR` | 500 | 未预期异常 | 无 |
- 错误码（`purpose=reset_password`）：
  | code | HTTP | 触发 | Retry-After |
  |---|---|---|---|
  | `VALIDATION_ERROR` | 422 | email 非法 / purpose 非法 / `PASSWORD_RESET_ENABLED=false` | 无 |
  | `RATE_LIMITED` | 429 | **仅 IP** 冷却/配额 | 正整数秒 |
  | `INTERNAL_ERROR` | 500 | 未预期异常 | 无 |
  | 其他 | 200 | 邮箱存在/不存在/禁用、邮箱配额命中、DM 失败 | 无（通用响应） |
- 验收断言：
  1. 首次发送 200，`expires_in==600`，`resend_after==60`，邮件只发给请求邮箱；
  2. 同 IP 60s 内第二次（即使换邮箱/用途）→ 429，`Retry-After` 在 1..60；
  3. 同邮箱同用途 60s 内第二次（即使换 IP）→ 429（register）或通用 200（reset，静默不发）；
  4. 60s 后重发成功，旧码立即失效；
  5. 已注册邮箱 + purpose=register → 409，且不发送邮件；
  6. DM 抛错（register）→ 503，数据库无 active 码，`Retry-After: 60`；
  7. DM 抛错（reset）→ 200 通用响应，无 active 码，日志告警；
  8. 未注册/禁用邮箱 + purpose=reset_password → 200 通用响应，不发送、不落码；
  9. 响应/日志不含验证码。

### 5.3 API-001 `POST /auth/register`（修订）

- 请求体新增：
  ```json
  {
    "email": "user@example.com",
    "password": "Str0ng-Passw0rd!",
    "display_name": "星星",
    "accept_tos": true,
    "email_code": "123456"
  }
  ```
- `email_code`：6 位数字字符串，可选字段（兼容关闭开关/旧客户端）；当 `EMAIL_VERIFICATION_ENABLED=true` 时业务上必填。关闭时即使传入也**忽略且不消费**，避免旧前端/灰度期误扣次数。
- 响应 201 不变；`user.email_verified` 在验证开启且校验通过时为 `true`，并写入 `email_verified_at`。
- 错误码新增：`EMAIL_CODE_INVALID`(422)、`EMAIL_CODE_EXPIRED`(410)；保留 `EMAIL_TAKEN`(409)、`WEAK_PASSWORD`(422)、`VALIDATION_ERROR`(422)、`RATE_LIMITED`(429)。
- 校验顺序（重要）：
  1. `accept_tos` → 2. 密码强度 → 3. 邮箱已存在 → 4. 验证码 → 5. 创建用户。
  前置失败**不消耗**验证码次数。
- 验收断言：
  1. 正确码 → 201，`user.email_verified==true`，码行变 `consumed`，同码不能再次使用；
  2. 错码 1~9 次 → 422 `EMAIL_CODE_INVALID`，`errors[0].message` 含剩余次数；
  3. 第 10 次错码 → 410 `EMAIL_CODE_EXPIRED`，文案含“过期”，码行 `locked`；
  4. TTL 过期（FrozenClock +601s）→ 410，且不创建用户；
  5. 密码弱/TOS 未勾选/邮箱已存在 → 对应错误，`attempts` 不变；
  6. 重发后旧码失效、新码可用；
  7. 并发用同一码注册 → 最多一个 201，另一个 409/410，用户表只有一行。

### 5.4 错误码（新增 3 个）

| code | HTTP | 触发条件 | user_message | what_to_do | retryable |
|---|---|---|---|---|---|
| `EMAIL_CODE_INVALID` | 422 | 验证码不匹配且未达上限 | 验证码不正确。 | 检查邮件里的 6 位数字；还可以尝试 N 次，或重新获取验证码。 | 是 |
| `EMAIL_CODE_EXPIRED` | 410 | 过期/已消费/已锁定/无记录/第 10 次失败 | 验证码已过期，请重新获取。 | 点击「重新发送」获取新的验证码。 | 否 |
| `EMAIL_SEND_FAILED` | 503 | DM 超时/5xx/凭据或配额错误 | 验证码邮件发送失败。 | 稍后重试；若持续失败请联系支持。 | 是 |

> `EMAIL_SEND_FAILED` 只用于注册发码；忘记密码发码遇到 DM 失败时返回通用 200（§13.2），避免通过 503/200 差异枚举账号。注册/找回共用 `EMAIL_CODE_INVALID` / `EMAIL_CODE_EXPIRED`。

第 10 次失败时 `EMAIL_CODE_EXPIRED` 额外带字段错误，便于前端/测试区分“次数用尽”：

```json
{ "field": "email_code", "code": "attempts_exceeded", "message": "错误次数已达 10 次，验证码已失效。" }
```

1~9 次失败时 `EMAIL_CODE_INVALID` 带：

```json
{ "field": "email_code", "code": "invalid", "message": "验证码不正确，还可尝试 3 次。" }
```

实现要求：

1. `stock_contracts/errors.py`：枚举 + `CATALOG` 条目；`SEED_ERROR_COUNT` 63 → 66。
2. `docs/architecture/v2/API-Catalog.md` §3 增加三行；§1/§2/§6 同步。
3. `backend/tests/unit/contracts/test_errors.py` 断言更新。
4. `GET /api/v1/errors` 自动返回 66 条，无需单独改路由。

### 5.5 契约计数与门禁同步

| 项 | 当前 | 本次后 |
|---|---|---|
| API-Catalog §1 端点 | 115（API-001~116，API-089 预留） | **118**（+ API-117/118/119） |
| `scripts/ci/gate_endpoint_coverage.py::EXPECTED_ALL_ENDPOINTS` | 114（已落后于目录，API-116 未计入） | **118** |
| `EXPECTED_PERIOD2` | 55 | 不变（若归入 Period3-12） |
| 错误码 | 63 | **66**（忘记密码复用，不新增） |
| OpenAPI operation | 现有 | +3 |
| 前端 SDK | 现有 | `make sdk` 重生成 |

> 注：基线目录实际 115 行而脚本仍写 114，属于 API-116 合并后的计数漂移；本次一并修正，不把它留到下一个功能。

### 5.6 API-119 `POST /auth/password-reset`（新增）

- 用途：校验找回验证码并设置新密码；不自动登录。
- 认证：公开；幂等：**不支持**（验证码一次性，重放返回 410）；同步。
- `operation_id="resetPassword"`。
- 请求：
  ```json
  {
    "email": "user@example.com",
    "email_code": "123456",
    "new_password": "N3w-Str0ng-Passw0rd!"
  }
  ```
  `new_password`：12~128 位，至少 3 类字符；`email_code`：6 位数字。
- 响应：**204 No Content**（无 body，不签发 token）。
- 响应头：`Cache-Control: no-store`。
- 错误码：
  | code | HTTP | 触发 | 说明 |
  |---|---|---|---|
  | `VALIDATION_ERROR` | 422 | email/code 格式、新旧密码相同、开关关闭 | 字段错误在 `errors[]` |
  | `WEAK_PASSWORD` | 422 | 新密码不满足 12~128 / 3 类字符 | 复用注册密码强度 |
  | `EMAIL_CODE_INVALID` | 422 | 验证码不匹配且未达上限 | 带剩余次数 |
  | `EMAIL_CODE_EXPIRED` | 410 | 过期/已消费/已锁定/无记录/用户不存在或非 active/第 10 次失败 | 统一“过期”语义，防枚举 |
  | `INTERNAL_ERROR` | 500 | 未预期异常 | 现有 RFC 7807 |
- 校验顺序：
  1. 开关/参数/密码强度/新旧密码不同 → 2. 用户存在且 active → 3. 验证码 → 4. 改密/撤销/审计。
  前置失败不消耗验证码次数。
- 验收断言：
  1. 正确码 → 204；旧密码登录 401，新密码登录 200；
  2. 验证码 1~9 次错误 → 422，剩余次数递减；第 10 次 → 410，码行 locked；
  3. TTL 过期 → 410，密码未变；
  4. 新密码与旧密码相同 → 422 `VALIDATION_ERROR`，码未被消费（可重试其他密码）；
  5. 用户不存在/disabled/deleted → 410 `EMAIL_CODE_EXPIRED`，不泄露账号状态；
  6. 注册码不能用于重置（purpose 不匹配）→ 410；重置码不能用于注册 → 409 `EMAIL_TAKEN`（邮箱已存在的前置检查）；
  7. 成功后该邮箱所有验证码行被删除；refresh 全部失效；PAT 全部失效；`email_verified_at` 为空则补写；
  8. 并发两次重置 → 最多一个 204，另一个 410；密码只改一次；
  9. 响应/日志/审计不含验证码或新密码明文。

---

## 6. 邮件投递设计

### 6.1 端口与适配器

```python
@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    html_body: str
    text_body: str
    tag_name: str | None = None


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> SendReceipt: ...
```

| 实现 | 使用场景 | 行为 |
|---|---|---|
| `AliyunDirectMailSender` | 生产/preprod | 懒加载 `alibabacloud_dm20151123`、`alibabacloud_credentials`、`alibabacloud_tea_openapi`、`alibabacloud_tea_util`；`CredentialClient()` 默认凭据；`asyncio.to_thread` + `wait_for` |
| `LogEmailSender` | 本地开发 | 结构化日志打印收件人掩码、TTL、验证码（仅 `STOCK_ENV != prod`）；生产启动直接拒绝 |
| `NullEmailSender` | 功能关闭/测试 | 被调用即抛 `EmailDeliveryError`；测试用 `FakeEmailSender` 替换 |

依赖建议：放在 `stock-platform` 的可选 extra `email`，根 `pyproject.toml` 转发 `email = ["stock-platform[email]"]`；生产镜像 `uv sync --extra email`。适配器内部懒加载，未安装时本地 `log` 模式仍可启动。

### 6.2 阿里云 DM 配置与默认凭据

```python
from alibabacloud_dm20151123.client import Client as DmClient
from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_dm20151123 import models as dm_models
from alibabacloud_tea_util import models as util_models

credential = CredentialClient()          # 默认凭据链，不在代码/配置里写 AK
config = open_api_models.Config(credential=credential)
config.endpoint = settings.email_dm_endpoint   # 默认 dm.aliyuncs.com
client = DmClient(config)
```

生产建议：

- ECS 实例 RAM 角色，授予最小权限 `dm:SingleSendMail`（只发信，不查统计/不管理域名）。
- 本地开发如必须用 AK，只从 shell/密钥管理注入 `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`，禁止写入 `.env` 仓库模板。
- DM 控制台完成发信域名与发信地址验证；`EMAIL_DM_ACCOUNT_NAME` 必须与已验证地址一致。
- 不给 SDK 配置自动重试（`autoretry=False`），避免超时后重复发同一码；失败由用户重发。

### 6.3 发送参数

```python
request = dm_models.SingleSendMailRequest(
    account_name=settings.email_dm_account_name,   # noreply@www.xingst.site
    address_type=1,
    reply_to_address=False,
    to_address=message.to,
    subject=message.subject,
    html_body=message.html_body,
    text_body=message.text_body,
    click_trace="1",
    from_alias=settings.email_dm_from_alias,        # 星星助手【xingst.site】
    tag_name=message.tag_name,                       # email_verification_register
)
```

- `subject` 固定模板，不拼接用户输入；`from_alias` 固定配置。
- 超时：`EMAIL_DM_TIMEOUT_SECONDS=5`；连接/读超时由 `RuntimeOptions` 设置，外层 `asyncio.wait_for` 兜底。
- 返回 `SendReceipt(provider="aliyun", request_id=..., env_id=...)`；只记录 provider request id，不记录邮件正文。
- 错误：捕获 SDK 异常，提取 `error.code` / `error.data["Recommend"]` / request id 写入结构化日志；不把供应商细节暴露给用户。映射：
  | 供应商错误 | 对外 | 说明 |
  |---|---|---|
  | `InvalidMailAddress` / 收件地址被拒 | `VALIDATION_ERROR` 422（field=email） | 用户改邮箱后可重试；码行置 failed |
  | `InvalidAccountName` / `InvalidAccessKeyId` / `Forbidden` | `EMAIL_SEND_FAILED` 503 | 配置/权限问题，运维告警 |
  | `DailyLimit` / `QuotaExceeded` / 5xx / 超时 | `EMAIL_SEND_FAILED` 503 | 可重试；`Retry-After: 60` |
  | 其他 | `EMAIL_SEND_FAILED` 503 | 记录 provider_code，按可重试处理 |

> 上表是 adapter 内部语义；找回密码路由按 §13.2 把 DM 失败统一折叠为通用 200，不把 503 暴露给调用方。

### 6.4 邮件模板

模板函数（共用布局，仅文案/主题不同）：

```python
def render_registration_email(*, code: str, ttl_minutes: int,
                              brand: str, site_host: str) -> EmailContent:
    ...

def render_password_reset_email(*, code: str, ttl_minutes: int,
                                brand: str, site_host: str) -> EmailContent:
    ...
```

- 注册主题：`【星星助手】注册验证码（{ttl_minutes} 分钟内有效）`；找回主题：`【星星助手】重置密码验证码（{ttl_minutes} 分钟内有效）`。`ttl_minutes` 由配置派生（默认 10）；不把验证码放主题，避免锁屏通知泄露。
- HTML：注册/找回共用附录 A 的布局；找回文案见附录 A.2。
- 纯文本：注册见 §6.5；找回见 §6.5.1；必须与 HTML 同一套信息，提升送达率。
- 安全：验证码只允许 `^\d{6}$`；品牌名/域名来自配置，渲染时 `html.escape`；不包含用户邮箱、不包含外部图片、不包含可点击的登录链接（减少钓鱼面）。
- 注册内容要点：验证码、10 分钟有效、仅可使用一次、非本人操作请忽略、不会索要验证码。
- 找回内容要点：验证码、10 分钟有效、仅可使用一次、非本人操作请忽略、**重置成功后所有登录设备将退出、API Token 将失效**、不会索要验证码。

### 6.5 纯文本备选

```text
星星助手 · 注册验证码

你的注册验证码是：{code}
10 分钟内有效，仅可使用一次。

如果这不是你本人的操作，请忽略这封邮件，你的账户不会被创建。
请勿将验证码转发给他人；星星助手不会通过电话、短信或邮件向你索要验证码。

此邮件由系统自动发送，请勿直接回复。
星星助手 · {site_host}
```

#### 6.5.1 找回密码纯文本

```text
星星助手 · 重置密码验证码

你的重置密码验证码是：{code}
10 分钟内有效，仅可使用一次。

如果这不是你本人的操作，请忽略这封邮件，你的密码不会被修改。
重置成功后，所有已登录设备将退出，API Token 将失效。
请勿将验证码转发给他人；星星助手不会通过电话、短信或邮件向你索要验证码。

此邮件由系统自动发送，请勿直接回复。
星星助手 · {site_host}
```

### 6.6 失败与可观测性

| 事件 | 日志字段 | 指标 |
|---|---|---|
| 发送成功 | `event=email_code.sent`、`purpose`、`email_hash`、`ip_hash`、`request_id`、`provider_request_id`、`expires_in` | `email_code_send_total{purpose,result="ok"}` |
| 冷却/配额拒绝 | `event=email_code.rate_limited`、`purpose`、`scope`、`retry_after`、`silent` | `...{purpose,result="rate_limited"}` |
| 验证失败 | `event=email_code.verify_failed`、`purpose`、`email_hash`、`attempts`、`remaining` | `email_code_verify_total{purpose,result="invalid"}` |
| 锁定 | `event=email_code.locked`、`purpose`、`email_hash` | `...{purpose,result="locked"}` |
| 过期 | `event=email_code.expired`、`purpose` | `...{purpose,result="expired"}` |
| DM 失败 | `event=email_dm.failed`、`purpose`、`provider`、`provider_code`、`provider_request_id`、`latency_ms` | `email_dm_send_seconds`、`email_dm_errors_total{purpose,provider_code}` |
| 找回已发/静默 | `event=email_code.reset_accepted`、`delivered`、`reason`（quota/provider/unknown） | `email_code_reset_total{result}` |
| 密码重置成功 | `event=auth.password_reset`、`refresh_revoked`、`pat_revoked`、`email_verified_promoted` | `password_reset_total{result="ok"}` |

告警建议：DM 失败率 > 5%（5 分钟）、`DailyLimit`/`QuotaExceeded` 出现、`email_code.locked` 突增、429 比例突增、找回 `delivered=false` 比例突增。

### 6.7 域名与送达率清单

- [ ] DM 控制台验证发信域名（`www.xingst.site` 或 `xingst.site`）与发信地址 `noreply@www.xingst.site`。
- [ ] 按控制台给出的记录配置 SPF、DKIM、DMARC；`From` 与 Return-Path 对齐。
- [ ] 不用免费邮箱发信；不在正文放短链/图片；保留纯文本备选。
- [ ] 预发用 Gmail、QQ 邮箱、Outlook 各测一封；检查是否进垃圾箱、移动端是否换行。
- [ ] 监控退信/投诉；DM 配额接近上限时提前扩容。
- [ ] 生产禁止 `EMAIL_PROVIDER=log`（启动校验）。

---

## 7. 防滥用与安全设计

### 7.1 限流矩阵

| 维度 | 冷却 | 小时配额 | 日配额 | 说明 |
|---|---|---|---|---|
| IP（所有用途共享） | **60s** | 30 | 100 | 精确滚动冷却；NAT 场景可调；找回请求也消耗 |
| 邮箱（注册码） | **60s** | 5 | 10 | 防止多 IP 打同一邮箱 |
| 邮箱（重置码） | **60s** | 5 | 10 | 与注册码独立；**命中时静默不发、仍返回通用 200** |
| 验证码失败 | 每码 10 次 | — | — | 第 10 次锁死，注册/找回共用 |
| 全局 DM | — | — | 供应商配额 | 指标/告警；不在应用内硬编码 |

- 冷却和配额都按**成功发送**计数；被拒绝的请求不增加计数（避免“越拒越久”）。
- 冷却精确到秒：`now - last_sent_at < cooldown` 即拒绝；`Retry-After` 为剩余秒数向上取整，最小 1。
- 小时/日为固定窗口（`hour_start`/`day_start`），配额是防轰炸的粗粒度上限，不要求精确滚动。
- 配额设为 `0` 表示不限制（仅排障），生产禁止 0。
- 注册发送：IP 或邮箱限制 → 429，`Retry-After` 取较大值。
- 忘记密码发送：**只有 IP 限制返回 429**；邮箱限制/发信失败一律通用 200（见 §13.2），避免通过响应差异枚举账号。

### 7.2 IP 解析与信任边界

现有 `_ip_hash` / `get_request_ip_hash` 直接取 `X-Forwarded-For` 最左值，客户端可伪造。本设计新增统一 helper：

```python
def client_ip_hash(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else None
    if peer and _is_trusted_proxy(peer, settings.trusted_proxy_cidrs):
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip and _valid_ip(real_ip):
            return hash_ip(real_ip)
    # peer 缺失/非法时不能返回 None 让限流键退化为“无键”；归一到固定值，
    # 所有未知来源共享一个冷却桶（fail-closed）。
    return hash_ip(peer) or hash_ip("unknown")
```

- **只信 nginx 覆盖的 `X-Real-IP`**，不信 `X-Forwarded-For` 的左值。
- `TRUSTED_PROXY_CIDRS` 默认 `127.0.0.1/32,::1/128`；生产按 Docker 网络/反代地址配置。
- `X-Real-IP` 非法/为空时回落到 peer；peer 也缺失时归一到固定哈希，避免绕过限流。
- nginx：
  ```nginx
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  ```
  nginx 必须**覆盖**而不是透传客户端传来的 `X-Real-IP`。
- API 端口不直接暴露公网；只允许 nginx/内网访问。
- 登录失败计数也改用同一 helper，避免两套 IP 语义；这是本设计附带的小重构，需回归登录限流测试。

### 7.3 验证码存储与校验

- 生成：`secrets.randbelow(1_000_000)` → `f"{n:06d}"`；禁止 `random`。
- 哈希：Argon2id（复用 `argon2_time_cost`），输入为 `f"{pepper}:{code}"`；`EMAIL_CODE_PEPPER` 至少 32 字符，`repr=False`，生产不得使用开发默认值。
- 校验：`hasher.verify(row.code_hash, f"{pepper}:{code}")`；失败计数原子更新；成功消费原子条件更新。
- DB 不存明文；终态清空 `code_hash`；日志/审计不出现验证码或完整邮箱。
- 重发覆盖：UPSERT 同一 `(email,purpose)`，旧码立即失效；不需要“旧码列表”。

### 7.4 枚举、重放与竞态

| 风险 | 处理 |
|---|---|
| 邮箱枚举 | 注册发码对已注册邮箱 409；找回发码对存在/不存在/禁用统一 200（§13.2） |
| 验证码重放 | `status='active'` 条件消费 + 行锁；成功即 `consumed` |
| 并发注册 | 行锁 + 用户表 `citext unique` 兜底；失败回滚不消费 |
| 并发重置 | 行锁 + 一次性消费；密码只改一次，另一个 410 |
| 多 IP 轰炸同一邮箱 | 邮箱 60s 冷却 + 5/h + 10/d（注册/找回各自计数） |
| 单 IP 轰炸多邮箱 | IP 60s + 30/h + 100/d + DM 配额告警；找回未注册邮箱也占 IP 冷却 |
| 伪造 `X-Forwarded-For` | 只信受信代理的 `X-Real-IP`（§7.2） |
| 找回账号枚举（状态码/响应体） | `purpose=reset_password` 对存在/不存在/禁用统一 200；只有 IP 级 429；邮箱配额/发信失败静默 |
| 找回账号枚举（时序） | 残余风险：邮件发送耗时可能被观测；见 §7.6，严格恒定时间需异步队列/固定延迟 |
| 日志泄露 | 结构化日志只记 email_hash/ip_hash/request_id；禁止 body/code/新密码 |
| 备份泄露 | 码为 Argon2+pepper，离线撞库成本高；终态清空；24h 清理 |

### 7.5 隐私与数据最小化

- 验证码表需要邮箱以完成投递/覆盖/清理；不记录邮箱正文、IP 明文。
- 节流表只存 `sha256(scope:key)`，无法反查邮箱/IP。
- 审计 `auth.email_code.send`：`actor_id=null`、`target_type="email"`、`target_id=email_hash`、`meta={"purpose", "expires_at": ISO8601 字符串, "provider"}`；不含验证码/完整邮箱。
- 审计 `auth.password_reset`：`actor_id=user.id`、`target_type="user"`、`target_id=user.id`、`meta={"refresh_revoked": n, "pat_revoked": m, "email_verified_promoted": bool}`；不含新密码/验证码。
- 注销账号：在删除 user 的同一事务里按 email 删除验证码行、按 email 哈希删除节流行，避免残留 PII。
- 生产日志级别不要开 DEBUG 打请求体；ASGI 访问日志不含 body；密码重置接口的 body 不进访问日志。

### 7.6 残余风险

- 6 位数字 + 10 次上限，单码暴力成功率约 `10/10^6`；配合发送配额可接受，但无法抵御“大量新码 + 分布式 IP”。后续可加 CAPTCHA/设备指纹。
- 邮件 bomb（攻击者用脚本给受害者邮箱发验证码）无法完全消除；邮箱小时/日配额把影响限制在 10 封/天。
- 阿里云 DM 故障/配额耗尽时无法发信；注册返回 503，找回返回通用 200；本期不自动切换供应商，只日志/告警。
- 退信/投诉回调未接入，无法自动停发问题地址。
- **找回时序侧信道**：未注册邮箱不调用 DM，响应通常比已注册快；重置提交对“无码/无用户”快速返回 410，对有码请求会做 Argon2 校验而更慢。响应体/状态码已统一，但严格恒定时间需要异步发信队列、dummy verify 或固定延迟，本期不做；若安全要求升级，另立设计。
- **access token 残窗**：重置后 refresh/PAT 立即失效，但已签发的 access JWT 最长 15 分钟仍有效（JWT 无 token version）。如要求立即失效，需后续引入 user token version / jti 黑名单并评估每请求查库成本。

---

## 8. 前端设计

### 8.1 注册页交互

```text
邮箱 [____________________]  [获取验证码] / [重新发送（59s）]
      验证码已发送，10 分钟内有效；请查看收件箱/垃圾箱。
验证码 [______]  6 位数字
密码 / 确认密码 / 昵称 / TOS
[注册]
```

状态：

| 状态 | 触发 | UI |
|---|---|---|
| `policyLoading` | 页面加载 | 默认按“需要验证”渲染，避免闪烁；策略返回 false 后隐藏 |
| `codeIdle` | 未发送 | 按钮“获取验证码”，禁用条件：邮箱空/非法、发送中、倒计时>0 |
| `codeSending` | 点击发送 | 按钮 loading；“发送中…” |
| `codeSent` | 发送成功 | 显示验证码输入 + 状态文案；按钮进入 60s 倒计时 |
| `cooldown` | 发送成功/429 | 每秒递减；0 时恢复“重新发送” |
| `codeError` | 发送/注册失败 | `ProblemView` 展示 `user_message`/`what_to_do`/`request_id` |
| `codeExpired` | 410 | 清空输入、`codeSent=false`、提示“验证码已过期，请重新获取” |

### 8.2 前端状态与请求

```tsx
const [policy, setPolicy] = useState<VerificationPolicy | null>(null);
const [emailCode, setEmailCode] = useState("");
const [codeSent, setCodeSent] = useState(false);
const [cooldown, setCooldown] = useState(0);
const [sendingCode, setSendingCode] = useState(false);
const [codeNotice, setCodeNotice] = useState<string | null>(null);
const [error, setError] = useState<unknown>(null);

const verificationRequired = policy?.email_verification_required ?? true;
```

- 挂载时 `api.auth.getVerificationPolicy()`；失败时 `verificationRequired=true`（fail-closed），但只在用户点击发送时提示。
- 发送：
  ```ts
  const result = await api.auth.sendEmailCode({ email, purpose: "register" });
  setCodeSent(true);
  setCooldown(result.resend_after);
  setCodeNotice(`验证码已发送，${Math.floor(result.expires_in / 60)} 分钟内有效。`);
  ```
  不把验证码写入 URL/localStorage/sessionStorage。
- 倒计时：
  ```ts
  const isCoolingDown = cooldown > 0;
  useEffect(() => {
    if (!isCoolingDown) return;
    const timer = window.setInterval(() => {
      setCooldown((seconds) => (seconds > 0 ? seconds - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [isCoolingDown]);
  ```
  倒计时是 UI 秒数，不涉及业务金额/比率，不违反零口径。
- 提交：
  ```ts
  await api.auth.register({
    email, password, display_name: displayName || undefined,
    accept_tos: acceptTos,
    email_code: verificationRequired ? emailCode : undefined,
  });
  ```
- 邮箱输入变化：若已发送，重置 `codeSent=false`、`emailCode=""`、`codeNotice="邮箱已修改，请重新获取验证码。"`；倒计时保留（服务端仍按 IP 限流）。
- 错误分支：
  - `EMAIL_CODE_INVALID`：保留输入，`ProblemView` 显示剩余次数；聚焦验证码输入。
  - `EMAIL_CODE_EXPIRED`：清空输入、`codeSent=false`、倒计时归零、提示重新获取；聚焦“获取验证码”按钮。
  - `RATE_LIMITED`：按 60s 兜底设置倒计时；`ProblemView` 显示 `user_message`。
  - `EMAIL_SEND_FAILED`：显示错误；倒计时 60s；允许稍后重试。
  - `EMAIL_TAKEN`：显示错误；可附“去登录”链接（不强制）。

### 8.3 API client 变更

`frontend/packages/api-client/src/contract.ts`（类型来自 generated）：

```ts
export type VerificationPolicy = ApiSchemas["VerificationPolicyOut"];
export type SendEmailCodeIn = ApiSchemas["SendEmailCodeIn"];
export type SendEmailCodeOut = ApiSchemas["SendEmailCodeOut"];
export type PasswordResetIn = ApiSchemas["PasswordResetIn"];
```

`http.ts`：

```ts
const auth = {
  getVerificationPolicy() {
    return send<VerificationPolicy>({
      path: "/auth/verification-policy", method: "GET", omitAuth: true,
    }).then((r) => r.data);
  },
  sendEmailCode(body: SendEmailCodeIn) {
    return send<SendEmailCodeOut>({
      path: "/auth/email-codes", method: "POST", body, omitAuth: true,
    }).then((r) => r.data);
  },
  resetPassword(body: PasswordResetIn) {
    return send<void>({
      path: "/auth/password-reset", method: "POST", body, omitAuth: true,
    }).then(() => undefined);
  },
  // register 的 RegisterIn 类型由 SDK 带上 email_code?: string
};
```

- 不手改 `generated/schema.d.ts`；先 `make api-snapshot`，再 `make sdk`。
- `contract.test.ts` 增加 API-117/118/119 的 method+path 断言（单独数组，不要把一期 30 端点计数改乱）。
- 可选增强（P1）：`ApiProblemError` 暴露 `retryAfter` 解析 `Retry-After`，用于精确倒计时；本期用成功响应 `resend_after` + 60s 兜底即可。

### 8.4 错误展示

- 统一复用 `frontend/apps/web/src/ui/Problem.tsx`，不手写错误字符串。
- 验证码字段自身错误（格式）用 `field-error` 展示；业务错误（无效/过期/限流/发送失败）用 `ProblemView`。
- `ProblemView` 已展示 `user_message`、`what_to_do`、`code`、`request_id`；满足“验证码不通过时返回错误信息并显示”。
- 不在前端把 `EMAIL_CODE_INVALID` 映射成“邮箱不存在”等推断文案，避免超出后端语义。

### 8.5 可访问性

- “获取验证码”是 `type="button"`，不触发表单提交。
- 倒计时文案放在 `role="status" aria-live="polite"`；每秒变化不放进 live region，避免读屏器刷屏。
- 验证码输入：`inputMode="numeric"`、`autoComplete="one-time-code"`、`maxLength={6}`、`pattern="[0-9]{6}"`、`aria-describedby` 指向提示/错误。
- 发送成功/失败都有文本状态，不只靠颜色。
- 键盘：按钮可 Tab 聚焦；错误后用 `ref.focus()` 把焦点移到验证码或发送按钮。
- 提交按钮禁用条件：`busy || !acceptTos || passwordsMismatch || (verificationRequired && emailCode.length !== 6)`。

### 8.6 样式

在 `frontend/apps/web/src/styles.css` 增加（只用 tokens，不新增 hex）：

```css
.auth-code-row { display: flex; gap: var(--s2); align-items: flex-start; }
.auth-code-row input { flex: 1; }
.auth-code-button { white-space: nowrap; min-width: 112px; }
.auth-code-hint { display: block; margin-top: var(--s1); color: var(--muted); font-size: var(--fs-sm); }
.auth-code-hint.is-error { color: var(--danger); }
```

- 复用 `.btn` / `.field-error` / `.problem`；不引入第二套表单组件。
- 移动端 380px 表单内按钮 + 输入可换行；必要时把 `.auth-page` 最大宽从 380 调到 420（视觉评审决定）。
- 禁裸 hex；主题切换由 tokens 自动生效。

### 8.7 前端测试

| 用例 | 断言 |
|---|---|
| 策略 false | 不渲染验证码行；提交不带 `email_code` |
| 策略 true | 渲染“获取验证码”；点击发送后显示输入与倒计时 |
| 发送 200 | 使用 `resend_after`；状态文案含“10 分钟” |
| 发送 429 | 显示 ProblemView；倒计时复位；按钮禁用 |
| 注册 422 `EMAIL_CODE_INVALID` | 显示后端 `user_message` 与剩余次数；输入保留 |
| 注册 410 `EMAIL_CODE_EXPIRED` | 清空输入、显示重新获取、聚焦发送按钮 |
| 邮箱修改 | 清除已发送状态与验证码 |
| 表单校验 | 6 位未满/密码不一致/TOS 未勾选时不可提交 |
| 可访问性 | 按钮有可访问名称；错误 `role="alert"`；验证码输入 `autocomplete=one-time-code` |

测试用 `vi.useFakeTimers()` 推进倒计时；mock `api.auth.*`，不发真实网络。

### 8.8 忘记密码页（新增 `/forgot-password`）

**入口**：登录页读取 `GET /auth/verification-policy`；仅当 `password_reset_enabled=true` 显示“忘记密码？”链接。策略请求失败时显示链接（fail-open 可发现），页面自行处理未开放。

**页面形态**（单页两步，避免多路由传邮箱）：

```text
步骤 1：输入注册邮箱
邮箱 [____________________]  [发送验证码]
提示：如果该邮箱已注册，我们会发送 6 位验证码。请检查收件箱和垃圾箱。

步骤 2：验证码 + 新密码
验证码 [______]  [重新发送（59s）]
新密码 [____________________]  （12~128 位，至少 3 类字符）
确认新密码 [________________]
[重置密码]
```

**状态**：

| 状态 | 触发 | UI |
|---|---|---|
| `policyLoading` | 页面加载 | 加载中；策略关闭则显示“当前部署未开放邮箱找回密码，请联系管理员。” |
| `codeIdle` | 未发送 | 邮箱 + 发送按钮 |
| `codeSent` | 发送 200（不论账号是否存在） | 显示步骤 2；文案固定为“如果该邮箱已注册…” |
| `cooldown` | 发送 200/429 | 60s 倒计时；服务端权威 |
| `codeInvalid` | 422 | `ProblemView`；保留输入；剩余次数 |
| `codeExpired` | 410 | 清空验证码；允许重新发送；提示“验证码已过期，请重新获取” |
| `resetSuccess` | 204 | 跳 `/login?reset=1`；登录页显示“密码已重置，请用新密码登录” |

**请求/错误**：

```ts
// 发码：与注册共用
await api.auth.sendEmailCode({ email, purpose: "reset_password" });

// 重置
await api.auth.resetPassword({ email, email_code: code, new_password: password });
```

- 发码成功文案必须是**条件式**（“如果该邮箱已注册…”），不得写“验证码已发送至该邮箱”。
- 发码 200 但未收到邮件时，用户可在 60s 后重试；不提供“账号不存在”提示。
- 重置 204 后不保存 token；跳登录页。密码只在提交时使用，不写 localStorage。
- 弱密码/新旧密码相同在提交前尽量本地提示；最终以服务端 422 为准。
- 确认密码不一致时前端拦截；`autoComplete="new-password"`；提供显示/隐藏密码按钮（复用登录页样式）。
- 成功后清空 `emailCode`/`password`/`confirmPassword` 状态。
- 可访问性：错误 `role="alert"`；发送状态 `role="status"`；倒计时 `aria-live="off"`；提交按钮在 6 位未满/密码不一致/弱密码提示时禁用（服务端仍兜底）。

**测试**：

| 用例 | 断言 |
|---|---|
| 登录页策略 true/false | 显示/隐藏“忘记密码？” |
| 找回页策略 false | 显示未开放，不渲染表单 |
| 发送 200（存在/不存在） | 两者文案完全一致；进入步骤 2；启动倒计时 |
| 发送 429 | 显示 ProblemView；倒计时复位；按钮禁用 |
| 重置 204 | 调用成功后跳 `/login?reset=1`，不保存 token |
| 重置 422 `EMAIL_CODE_INVALID` | 显示剩余次数；输入保留 |
| 重置 410 `EMAIL_CODE_EXPIRED` | 清空验证码；允许重发 |
| 密码不一致/弱密码 | 前端拦截或显示字段错误 |
| 可访问性 | 两步表单 label/描述关联；错误可被读屏播报 |

---

## 9. 配置与部署

### 9.1 环境变量

| 变量 | 默认 | 必填 | 说明 |
|---|---|---|---|
| `EMAIL_VERIFICATION_ENABLED` | `false` | 生产建议 `true` | 注册是否要求验证码；关闭时策略 `email_verification_required=false` |
| `PASSWORD_RESET_ENABLED` | `false` | 生产建议 `true` | 是否开放邮箱找回密码；关闭时策略 `password_reset_enabled=false` |
| `EMAIL_PROVIDER` | `none` | 是（任一开关开启时） | `aliyun` / `log` / `none` |
| `EMAIL_DM_ACCOUNT_NAME` | 空 | aliyun 必填 | `noreply@www.xingst.site` |
| `EMAIL_BRAND` / `EMAIL_SITE_HOST` | `星星助手` / `xingst.site` | 否 | 邮件模板品牌名/站点域名 |
| `EMAIL_DM_FROM_ALIAS` | `星星助手【xingst.site】` | 否 | 发件人显示名 |
| `EMAIL_DM_ENDPOINT` | `dm.aliyuncs.com` | 否 | DM endpoint |
| `EMAIL_DM_TIMEOUT_SECONDS` | `5` | 否 | 发信超时 |
| `EMAIL_CODE_PEPPER` | 开发默认值 | 生产必填 | 至少 32 字符；`repr=False` |
| `EMAIL_CODE_TTL_SECONDS` | `600` | 否 | 10 分钟 |
| `EMAIL_CODE_MAX_ATTEMPTS` | `10` | 否 | 每码失败上限 |
| `EMAIL_CODE_RESEND_SECONDS` | `60` | 否 | 冷却；需求固定 60 |
| `EMAIL_CODE_IP_HOURLY_LIMIT` | `30` | 否 | 0=不限 |
| `EMAIL_CODE_IP_DAILY_LIMIT` | `100` | 否 | 0=不限 |
| `EMAIL_CODE_EMAIL_HOURLY_LIMIT` | `5` | 否 | 0=不限 |
| `EMAIL_CODE_EMAIL_DAILY_LIMIT` | `10` | 否 | 0=不限 |
| `TRUSTED_PROXY_CIDRS` | `127.0.0.1/32,::1/128` | 生产按反代网络 | 只信这些 peer 的 `X-Real-IP` |

`.env.example` 增加上述键的注释与安全提示；`infra/compose/docker-compose.dev.yml` 的 api 环境透传（本地默认 `EMAIL_VERIFICATION_ENABLED=false`、`PASSWORD_RESET_ENABLED=false`，需要体验时手动开）。`EMAIL_CODE_EMAIL_*` 配额按 purpose 独立计数；`EMAIL_CODE_IP_*` 所有用途共享。

### 9.2 启动校验

`build_state()` / `create_app()` 启动时：

1. `EMAIL_VERIFICATION_ENABLED=true` 或 `PASSWORD_RESET_ENABLED=true`，且 `EMAIL_PROVIDER=none` → `RuntimeError("邮箱验证/找回已启用但 EMAIL_PROVIDER=none")`。
2. 任一开关为 true 且 `EMAIL_PROVIDER=aliyun`：account name 非空、pepper 长度 ≥32；否则拒绝启动。
3. `STOCK_ENV=prod` 且 `EMAIL_PROVIDER=log` → 拒绝启动（防止生产把验证码打进日志）。
4. `STOCK_ENV=prod` 且 `EMAIL_CODE_PEPPER` 等于开发默认值 → 拒绝启动。
5. `EMAIL_CODE_TTL_SECONDS`、`MAX_ATTEMPTS`、`RESEND_SECONDS` 必须 >0；配额 ≥0。
6. 不在启动时调用 DM 网络；`/readyz` 不因邮件服务不可用而失败（避免邮件故障拖垮整个 API）。
7. `PASSWORD_RESET_ENABLED=true` 但 `EMAIL_VERIFICATION_ENABLED=false` 是合法组合（允许只开找回），启动日志显式打印两个开关。

### 9.3 发布顺序

1. **DB**：执行迁移 `0023_email_verification`；`make migrate-check`。
2. **后端（开关关闭）**：部署 API-117/118/119 + 注册可选 `email_code`；`EMAIL_VERIFICATION_ENABLED=false`、`PASSWORD_RESET_ENABLED=false`。旧前端注册仍可用。
3. **前端**：部署注册页 + 登录页 + `/forgot-password`；策略接口返回两个 false，页面不显示验证码区/找回入口，行为不变。
4. **配置 DM**：RAM 角色、域名验证、SPF/DKIM/DMARC；预发用 `EMAIL_PROVIDER=aliyun` 发注册/找回各一封测试邮件。
5. **灰度开启**：先开 `PASSWORD_RESET_ENABLED=true`（登录页出现找回入口，注册不受影响），观察发码/重置/撤销；再开 `EMAIL_VERIFICATION_ENABLED=true`（注册页出现验证码区）。两个开关可独立回滚。
6. **全量**：确认 Gmail/QQ/Outlook 送达；更新 README/DATABASE/DEV-PLAN/API-Catalog 数字。

### 9.4 回滚

- **首选**：把 `EMAIL_VERIFICATION_ENABLED=false` / `PASSWORD_RESET_ENABLED=false`；前端策略接口立即隐藏对应入口，注册/登录恢复旧流程；无需回滚代码/迁移。
- 后端回滚：回退 API 版本；迁移表可保留（不影响旧代码），或执行 downgrade 删除两表（会丢失未使用验证码，无业务数据损失）。
- 找回故障：保持开关 true 时发信失败静默；应急可关 `PASSWORD_RESET_ENABLED`，登录页入口消失；用户仍可用原密码登录。
- DM 故障：注册 503、找回静默；应急可临时关闭对应开关，需人工批准并记录。

### 9.5 运维

- 每日 cron：`purge-email-codes`。
- 监控：DM 成功率、P95 延迟、`DailyLimit`、429 比例、锁定率、验证码消费率；找回另看 `delivered=false` 比例、重置成功/失败、撤销数量。
- 账号注销：确认删除验证码/节流行；审计保留。
- 密钥轮换：`EMAIL_CODE_PEPPER` 轮换会让未使用码立即失效；在低峰期执行，并接受用户重发。
- 重置排障：按 `request_id`/`email_hash` 查 `auth.password_reset` 审计与 `password_reset_total`；不得在日志中查新密码。

---

## 10. 测试与验收

### 10.1 单元测试

| 文件 | 覆盖 |
|---|---|
| `backend/tests/unit/platform/test_email_codes.py` | 6 位补零、`secrets` 生成范围、Argon2+pepper 正确/错误、TTL 计算、状态迁移、purpose 隔离 |
| `backend/tests/unit/platform/test_email_throttle.py` | 60s 边界（59/60/61）、小时/日窗口重置、配额 0=不限、不同 scope/purpose 隔离 |
| `backend/tests/unit/platform/test_request_ip.py` | 受信 peer 用 `X-Real-IP`；非受信 peer 忽略；`X-Forwarded-For` 永不使用 |
| `backend/tests/unit/platform/test_email_template.py` | 注册/找回两套主题不含码、验证码出现一次、纯文本含 TTL、HTML 转义、无外部图片 |
| `backend/tests/unit/platform/test_email_codes.py` | 生成/哈希/模板/用途隔离 |
| `backend/tests/unit/contracts/test_errors.py` | `SEED_ERROR_COUNT==66`；新错误码在 CATALOG |

### 10.2 API 验收矩阵

每个 API-ID 至少 3 条；建议命名 `test_api_117_*` / `test_api_118_*` / `test_api_119_*` / 扩展 `test_api_001_*`。

| # | 场景 | 断言 |
|---|---|---|
| 1 | 发送 happy | 200；`expires_in=600`；fake sender 收到 1 封；DB 行 active |
| 2 | 发送-IP 60s | 同 IP 换邮箱第二次 → 429；`Retry-After` 1..60；无第二封 |
| 3 | 发送-邮箱 60s | 同邮箱换 IP 第二次 → 429（注册）/ 通用 200（找回） |
| 4 | 60s 后重发 | FrozenClock +61s → 200；旧码失效；新码可注册 |
| 5 | 已注册邮箱（注册） | 409 `EMAIL_TAKEN`；无邮件 |
| 6 | DM 失败（注册） | 503 `EMAIL_SEND_FAILED`；`Retry-After: 60`；码行 `failed` |
| 7 | 注册 happy | 201；`user.email_verified=true`；码行 `consumed`；同码再用 410 |
| 8 | 错码 1~9 | 422 `EMAIL_CODE_INVALID`；剩余次数递减 |
| 9 | 第 10 次错码 | 410 `EMAIL_CODE_EXPIRED`；码行 `locked`；正确码也 410 |
| 10 | TTL 过期 | FrozenClock +601s → 410；用户未创建 |
| 11 | 前置错误不扣次数 | 弱密码/未勾 TOS/邮箱已存在后，`attempts` 不变，正确码仍可注册 |
| 12 | 重发失效 | 发 A → 发 B（+61s）→ A 注册 410，B 201 |
| 13 | 并发消费 | 两个并发注册同一码 → 最多一个 201；用户表 1 行 |
| 14 | 策略关闭 | `GET policy` required=false / reset=false；注册不带码 201；登录页无找回入口 |
| 15 | 策略开启 | `GET policy` required=true / reset=true；注册不带码 422 `VALIDATION_ERROR` |
| 16 | IP 伪造 | 带 `X-Forwarded-For: 1.2.3.4` 不改变限流键；带受信 `X-Real-IP` 才隔离 |
| 17 | 跨邮箱 | A 的码不能注册 B → 410；B 无用户 |
| 18 | 无明文 | DB `code_hash` 不是 6 位明文；日志捕获无 code/完整邮箱 |
| 19 | 找回发码-存在账号 | 200 通用；fake sender 收到 reset 邮件；DB 行 `purpose=reset_password` |
| 20 | 找回发码-不存在/禁用 | 200 通用；无邮件；无码行；响应体与 #19 一致 |
| 21 | 找回发码-邮箱配额 | 同邮箱第 6 次/小时 → 200 通用；无邮件；无 429（防枚举） |
| 22 | 找回发码-IP 配额 | 同 IP 超限 → 429；`Retry-After` 正整数 |
| 23 | 找回发码-DM 失败 | 200 通用；码行 `failed`；日志/指标记录 provider 错误 |
| 24 | 重置 happy | 204；旧密码登录 401；新密码登录 200；码行删除；`email_verified_at` 补写 |
| 25 | 重置-旧密码失效 | 204 后用旧密码登录 401；用新密码登录 200 |
| 26 | 重置-凭证撤销 | 现有 refresh → 401 `TOKEN_REUSED/REVOKED`；现有 PAT → 401 `TOKEN_REVOKED` |
| 27 | 重置-错码 | 1~9 次 422；第 10 次 410；码行 locked；密码不变 |
| 28 | 重置-TTL 过期 | +601s → 410；密码不变 |
| 29 | 重置-弱密码/新旧相同 | 弱密码 422 `WEAK_PASSWORD`；新旧相同 422 `VALIDATION_ERROR`；码未被消费，可换密码重试 |
| 30 | 重置-用户不存在/禁用 | 410 `EMAIL_CODE_EXPIRED`；不泄露状态；无密码变更 |
| 31 | 重置-用途不匹配 | 注册码提交重置 → 410；重置码提交注册 → 409（邮箱已存在） |
| 32 | 重置-并发 | 两个并发同一码 → 最多一个 204；另一个 410；密码只改一次 |
| 33 | 重置-无明文 | 日志/审计/DB 无验证码/新密码明文；审计 `auth.password_reset` 含撤销计数 |

测试夹具：

- 现有 `settings` fixture 必须**显式覆盖**为 `email_verification_enabled=False`、`password_reset_enabled=False`（不能只依赖默认值；`.env` 可能被开发者改成 true），保持既有注册用例不变；新用例 `model_copy(update={"email_verification_enabled": True, "password_reset_enabled": True, "email_provider": "log"})`，并注入 `FakeEmailSender`。
- `FakeEmailSender` 记录 `EmailMessage` 并暴露 `last_code`；测试可通过 `EmailVerificationService(code_factory=lambda: "123456")` 注入确定性码，避免解析 HTML。生产代码不提供“读码”接口。
- `FrozenClock` 推进 60/600 秒；所有时间断言不 sleep。
- `httpx.ASGITransport` 显式设置 `client=("127.0.0.1", 12345)`，确保默认 `TRUSTED_PROXY_CIDRS` 生效；通过不同 `X-Real-IP` 模拟不同 IP。
- `conftest._PLATFORM_TABLES` 增加两张新表，避免用例间串数据。

### 10.3 前端测试

见 §8.7 与 §8.8；新增 `frontend/apps/web/src/pages/Register.test.tsx`、`Login.test.tsx`、`ForgotPassword.test.tsx`，使用 `vi.mock("../client")` 或注入 fake API。

### 10.4 契约与门禁

```bash
make api-snapshot          # 重新生成 contracts/openapi.json
make api-check             # 端点/错误码/operationId/示例
make sdk                   # 生成前端 schema.d.ts
make lint
make typecheck             # mypy strict + import-linter + tsc
make test-p2
cd frontend && pnpm -r test
cd frontend && pnpm -r build && pnpm budget
```

额外检查：

- `python scripts/ci/gate_endpoint_coverage.py`（更新 `EXPECTED_ALL_ENDPOINTS=118`）。
- `python scripts/ci/gate_rls_coverage.py`（两张新表无 `user_id`，不误报；如口径变化按 §4.3 加 allowlist）。
- `make migrate-check`（`0023` 与 `SCHEMA_VERSION` 一致）。

### 10.5 手工验收

1. `EMAIL_VERIFICATION_ENABLED=true`、`PASSWORD_RESET_ENABLED=true`、`EMAIL_PROVIDER=log`：本地跑通注册发码/注册、找回发码/重置；日志取码。
2. `EMAIL_PROVIDER=aliyun` 预发：真实发信到 Gmail/QQ/Outlook；检查注册/找回两套主题、验证码显示、10 分钟文案、移动端换行、垃圾箱。
3. 连续发送：60s 内第二次看到 429 和倒计时；60s 后可重发。
4. 输错 10 次：第 10 次看到“验证码已过期，请重新获取”；重发后新码可注册/重置。
5. 找回防枚举：未注册/已注册邮箱发码都返回 200，页面文案一致；未注册邮箱收不到邮件。
6. 重置收口：重置成功回登录页；旧密码登录失败、新密码成功；旧 refresh/PAT 失效。
7. 关闭开关：注册页不显示验证码区、登录页不显示找回入口，旧流程可用；重新打开后显示。
8. 检查日志/审计/DB：无验证码/新密码明文、无完整邮箱、无 AK。

---

## 11. 实施计划

### 11.1 文件清单

| 文件 | 变更 |
|---|---|
| `backend/packages/contracts/src/stock_contracts/constants.py` | `EMAIL_CODE_*` 常量 |
| `backend/packages/contracts/src/stock_contracts/errors.py` | 3 个错误码 + CATALOG + `SEED_ERROR_COUNT=66` |
| `backend/packages/platform/src/stock_platform/models.py` | 两个 ORM 模型 |
| `backend/packages/platform/src/stock_platform/repositories/email_codes.py` | 验证码/节流仓储 |
| `backend/packages/platform/src/stock_platform/repositories/users.py` | `create()` 支持写入 `email_verified_at`；用户对象改密 |
| `backend/packages/platform/src/stock_platform/repositories/tokens.py` | `ApiTokenRepository.revoke_all_for_user` |
| `backend/packages/platform/src/stock_platform/email_verification.py` | 服务：生成/哈希/验证/状态迁移；重置编排 |
| `backend/packages/platform/src/stock_platform/mail.py` | `EmailSender` 端口、`LogEmailSender`、`NullEmailSender`、工厂 |
| `backend/packages/platform/src/stock_platform/mail_aliyun.py` | 阿里云 DM 适配器（懒加载） |
| `backend/packages/platform/src/stock_platform/email_templates.py` | HTML/纯文本模板（注册 + 重置） |
| `backend/packages/platform/src/stock_platform/security.py` | `EmailCodeHasher`（Argon2+pepper） |
| `backend/packages/platform/pyproject.toml` | 可选 extra `email` |
| `backend/packages/platform/src/stock_platform/config.py` | `EMAIL_*` / `PASSWORD_RESET_ENABLED` / `TRUSTED_PROXY_CIDRS` 设置 |
| `backend/migrations/versions/0023_email_verification.py` | 建表/索引/授权/版本 |
| `backend/services/api/src/stock_api/version.py` | `SCHEMA_VERSION=0023_email_verification` |
| `backend/services/api/src/stock_api/schemas.py` | `SendEmailCodeIn/Out`、`VerificationPolicyOut`、`PasswordResetIn`、`RegisterIn.email_code` |
| `backend/services/api/src/stock_api/routers/auth.py` | 三个新端点；注册/重置事务改造；IP helper |
| `backend/services/api/src/stock_api/request_ip.py` | `client_ip_hash`（受信代理 + `X-Real-IP`） |
| `backend/services/api/src/stock_api/app.py` / `deps.py` | 装配 `email_sender` / `email_codes`；启动校验 |
| `backend/services/api/src/stock_api/cli.py` | `purge-email-codes` |
| `backend/tests/conftest.py` | 清库表、settings 默认、FakeEmailSender |
| `backend/tests/api/test_auth_tokens_ops.py` | API-001 新用例 |
| `backend/tests/api/test_api_117_118_119_email_codes.py` | 新端点验收 |
| `frontend/packages/api-client/src/contract.ts` / `http.ts` | 三个方法 + 类型 |
| `frontend/packages/api-client/src/contract.test.ts` | API-117/118/119 契约断言 |
| `frontend/apps/web/src/pages/Register.tsx` | 验证码 UI/状态/错误 |
| `frontend/apps/web/src/pages/Login.tsx` | “忘记密码”入口 + 重置成功提示 |
| `frontend/apps/web/src/pages/ForgotPassword.tsx` | 两步找回表单 |
| `frontend/apps/web/src/router.tsx` | `/forgot-password` 公开路由 |
| `frontend/apps/web/src/pages/Register.test.tsx` / `Login.test.tsx` / `ForgotPassword.test.tsx` | 新测试 |
| `frontend/apps/web/src/styles.css` | 验证码行/找回表单样式 |
| `docs/architecture/v2/API-Catalog.md` | API-001/117/118/119、错误码、验收矩阵 |
| `docs/DATABASE.md` | 两表、RLS 例外、迁移、清理、表数 44→46 |
| `docs/DEV-PLAN-v2.md` | Period3-12 + API-117/118/119 |
| `docs/prd/PRD-v2.md` | OQ-1 决策记录：可配置关闭，生产默认启用；FR-L2 补忘记密码 |
| `docs/architecture/v2/README.md` / `README.md` | 数字/功能说明；OQ-1 标记为已解决（开关可配置） |
| `docs/Architecture-v2.md` | API 摘要/端点计数与认证说明同步 |
| `infra/.env.example` | 新增配置注释 |
| `scripts/ci/gate_endpoint_coverage.py` | `EXPECTED_ALL_ENDPOINTS=118` |

### 11.2 实施顺序

1. 评审本文，冻结 HITL-E1~E5 与 D-13~D-15。
2. 改 API-Catalog §1/§2/§3/§6 + DEV-PLAN；先跑 `make api-check` 允许失败（端点未实现），确认清单。
3. contracts 常量/错误码；更新错误码测试。
4. 迁移 + models + repositories（含 PAT 批量撤销）；`make migrate-check`。
5. platform 服务/哈希/邮件端口/两套模板；单元测试。
6. API schema/router/DI/CLI；注册/找回 API 测试 + FakeEmailSender。
7. `make api-snapshot` → `make sdk`；前端 client + Register + Login + ForgotPassword + 测试。
8. 全量门禁；预发真实 DM 手工验收（注册/找回各一封）。
9. 更新 README/DATABASE 数字；发布（按 §9.3）。

### 11.3 工作量估算

| 工作 | 人日 |
|---|---|
| 后端（contracts/platform/API/迁移/注册 + 找回/测试） | 4.0–5.5 |
| 前端（client/Register/Login/ForgotPassword/样式/测试） | 1.5–2.0 |
| 邮件模板/域名/DM 联调（注册 + 找回） | 0.5–1.0 |
| 文档/契约/门禁/验收 | 1.0–1.5 |
| **合计** | **7.0–10.0 人日** |

---

## 12. 风险与开放问题

| # | 风险/问题 | 影响 | 处理 |
|---|---|---|---|
| R-1 | 生产开启后旧前端缓存无法注册 | 用户看到 422 | 按 §9.3 先部署后端（关闭）→ 前端 → 开启；旧前端在开启前已被新前端替换 |
| R-2 | NAT/校园网共享 IP，1 分钟限制误伤 | 多人无法同时注册 | 需求明确按 IP；可调 `EMAIL_CODE_IP_*`，但 60s 冷却保持；邮箱维度独立 |
| R-3 | DM 进入垃圾箱 | 用户收不到 | SPF/DKIM/DMARC、纯文本备选、预发多邮箱测试、监控退信 |
| R-4 | DM 配额/故障 | 注册阻塞；找回静默失败 | 注册 503 + 告警；找回通用 200 + 告警；应急可关闭开关（需人工批准） |
| R-5 | 6 位码被撞库 | 账户被抢注 | 10 次上限 + 发送配额 + 监控；后续 CAPTCHA/8 位 |
| R-6 | 代理头伪造 | IP 限流失效 | 只信受信代理的 `X-Real-IP`；API 不直连公网 |
| R-7 | 验证码/邮箱/新密码进日志 | 隐私泄露 | 结构化日志只记 hash；代码审查 + 日志断言测试 |
| R-8 | 计数漂移 | CI 门禁失败 | §5.5 一次性修正 114→118、63→66 |
| R-9 | `EMAIL_VERIFICATION_ENABLED` / `PASSWORD_RESET_ENABLED` 被误关 | 注册无验证 / 无法找回 | 生产启动日志显式打印两个开关；变更需审计/审批；监控策略接口值 |
| R-10 | 找回接口时序差异 | 账号枚举侧信道 | 响应体/状态码统一；残余时序风险见 §7.6；严格方案需异步发信 |
| R-11 | 重置后旧 access token 仍有效 | 最长 15 分钟窗口 | 文档披露；刷新/PAT 立即撤销；后续可加 token version |
| R-12 | 重置后通知邮件未发 | 用户不知情 | P1 可选；不阻塞重置；日志/告警 |

**决策状态**：HITL-E1~E5 已于 2026-11-22 确认按推荐执行；D-13~D-15 为 v1.1 新增的推荐口径，若评审无异议按本文实现。

---

## 13. 忘记密码专项设计（v1.1）

### 13.1 用户故事与范围

- 用户忘记密码，但能访问注册邮箱；通过邮箱验证码证明所有权后设置新密码。
- 成功重置后，用户用新密码重新登录；不自动签发会话，避免把“邮箱验证”直接变成登录态。
- 本期不做：密码历史、密码过期、修改邮箱、登录二次验证、短信找回、管理员代重置。

### 13.2 防枚举：响应矩阵

`POST /auth/email-codes`（`purpose=reset_password`）对以下情况返回**同一响应体**（200 + `expires_in/resend_after/max_attempts/code_length`）：

| 内部情况 | HTTP | 是否发信 | 是否落码 | 备注 |
|---|---|---|---|---|
| 邮箱存在且 active | 200 | 是（正常） | 是 | 正常路径 |
| 邮箱不存在 | 200 | 否 | 否 | 只消耗 IP 冷却 |
| 用户 disabled/deleted | 200 | 否 | 否 | 与不存在同语义 |
| 邮箱命中 60s 冷却/小时/日配额 | 200 | 否 | 否 | 静默，不返回 429 |
| DM 超时/5xx/凭据/配额失败 | 200 | 否 | 码置 failed | 日志 + 指标告警 |
| IP 命中 60s/小时/日限制 | 429 | 否 | 否 | 唯一可区分的限流响应；与邮箱无关 |
| email/purpose 非法、开关关闭 | 422 | 否 | 否 | 参数/配置错误，不泄露账号 |

原则：

- **响应体/状态码不泄露账号存在性**；只有 IP 级限流允许 429（对所有邮箱一致）。
- 邮箱维度配额命中时“假装已发送”：用户按通用文案等待/重试；这是防枚举的代价。
- 时序侧信道仍在（发码时未注册邮箱不调用 DM；重置提交时无码/无用户不做 Argon2 校验，有码时较慢），见 §7.6；严格恒定时间需要异步队列/dummy verify/固定延迟，本期不承诺。
- 前端文案必须条件式：“如果该邮箱已注册，我们已发送验证码”，不得写“已发送至该邮箱”。

### 13.3 数据与用途隔离

- `purpose=reset_password` 复用 `platform.email_verification_code`，PK `(email,purpose)`；注册码/重置码互不覆盖。
- 注册码不能用于重置：重置路径按 `purpose=reset_password` 查行，查不到即 `EMAIL_CODE_EXPIRED`（410）。
- 重置码不能用于注册：注册路径先检查邮箱已存在 → 409 `EMAIL_TAKEN`；若邮箱不存在则不存在重置码，按 410 处理。
- 节流邮箱 key 含 purpose：`sha256("email:reset_password:" + normalized_email)`；IP key 所有用途共享。
- 重置成功后删除该邮箱**所有** purpose 的验证码行，清理未使用码与 PII；审计不依赖这些行。
- 账号注销/清理任务同样按 email 删除两用途行。

### 13.4 重置事务（API-119）

```python
async with state.db.session(begin=True) as session:
    user = await UserRepository(session).by_email(email)
    if user is None or user.status != "active":
        # 不消耗验证码，也不泄露账号状态
        return _expired()

    # 先校验新密码，避免用弱密码/旧密码白白消耗验证码
    weak = validate_password_strength(new_password)
    if weak:
        raise ApiError(ErrorCode.WEAK_PASSWORD, errors=weak)
    if password_hasher.verify(user.password_hash, new_password):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            errors=[FieldError("new_password", "same_as_current", "新密码不能与当前密码相同")],
        )

    result = await email_codes.verify(
        session, email=email, purpose="reset_password", code=code, now=now
    )
    if result.status != "ok":
        await session.commit()  # 提交 attempts/locked
        raise _verification_error(result)

    user.password_hash = password_hasher.hash(new_password)
    promoted = user.email_verified_at is None
    if promoted:
        user.email_verified_at = now
    refresh_revoked = await RefreshTokenRepository(session).revoke_all_for_user(user.id, now=now)
    pat_revoked = await ApiTokenRepository(session).revoke_all_for_user(user.id, now=now)
    await EmailVerificationRepository(session).delete_for_email(email=email)
    await audit.record(
        session,
        action="auth.password_reset",
        actor_id=user.id,
        target_type="user",
        target_id=str(user.id),
        meta={
            "refresh_revoked": refresh_revoked,
            "pat_revoked": pat_revoked,
            "email_verified_promoted": promoted,
        },
        request_id=request.headers.get("x-request-id"),
        ip_hash=client_ip_hash(request, state.settings),
        source="api",
    )
# 提交后返回 204
```

要点：

- 新密码强度、新旧相同在验证码校验前；前置错误不扣 attempts。
- 用户不存在/disabled → 410，不泄露状态；不设置密码。
- 验证码成功消费、改密、撤销、删码、审计在同一事务；任一步失败回滚，密码/验证码都不变。
- `RefreshTokenRepository.revoke_all_for_user` 已存在；`ApiTokenRepository.revoke_all_for_user` 本期新增。
- 返回 204 不签发 token；前端跳登录页。

### 13.5 重置后的凭证收口

| 凭证 | 处理 | 生效时间 |
|---|---|---|
| Refresh token | 全部撤销（`revoked_at=now`） | 立即；后续 refresh 401 |
| PAT | 全部撤销（`revoked_at=now`） | 立即；下次请求 401 |
| Access JWT | 不主动撤销（无 token version） | 最长剩余 15 分钟 |
| 密码 | 新 Argon2id 哈希 | 立即；旧密码登录 401 |
| 邮箱验证 | `email_verified_at` 为空则补写 | 立即 |

**为什么不做 access 立即撤销**：现有 JWT 无 `token_version`/jti 黑名单；每请求查库会改变认证热路径性能和可用性。接受 15 分钟残窗，文档显式披露（§7.6）；如安全要求升级，另立设计引入 user token version 或短 TTL + 黑名单。

### 13.6 找回邮件

- 主题：`【星星助手】重置密码验证码（10 分钟内有效）`。
- 正文：与注册同一布局，标题“重置你的密码”；说明“你正在重置密码”“10 分钟有效”“仅可使用一次”“非本人操作请忽略”“重置成功后所有登录设备将退出、API Token 将失效”。
- 不包含登录链接/外部图片；不包含用户邮箱；验证码只出现于正文与隐藏预header。
- 纯文本见 §6.5.1。
- 发送失败：码置 `failed`，日志/告警；对外仍 200 通用响应（§13.2）。

### 13.7 前端

见 §8.8：登录页按 `password_reset_enabled` 显示入口；`/forgot-password` 单页两步；发码文案条件式；重置成功跳 `/login?reset=1`；错误用 `ProblemView`；不持久化验证码/密码。

### 13.8 契约与计数

- API-117：`purpose` 扩展 `reset_password`；注册/找回错误语义不同（§5.2）。
- API-118：`GET /auth/verification-policy`，新增 `password_reset_enabled`；v1.0 的 `/auth/registration-policy` 在实现前改名，若已有调用方保留一个版本的弃用别名。
- API-119：`POST /auth/password-reset`，204。
- 端点总数 115 → **118**；错误码 **66**（找回复用，不新增）；`EXPECTED_ALL_ENDPOINTS=118`。
- OpenAPI 快照/SDK/API-Catalog §1/§2/§3/§6 同步。

### 13.9 P1 可选：密码修改成功通知

成功重置并提交后，可 best-effort 发送“密码已修改”通知邮件：

- 主题：`【星星助手】你的密码已修改`；不含验证码/新密码；提示“若非本人操作，请立即联系支持并重置密码”。
- 发送失败只记日志/指标，不影响 204；使用独立模板与 `tag_name=password_changed`。
- 不在 P0 验收；若做，需补模板/测试/日志指标。

### 13.10 兼容与迁移

- 不新增表：`purpose` 是 text + 应用校验，`reset_password` 不需要迁移；`0023` 只建两表/索引/授权。
- 存量用户 `email_verified_at` 可能为空；重置成功补写，不回溯批量更新。
- 旧客户端：没有找回入口，不影响登录；`PASSWORD_RESET_ENABLED=false` 时新前端隐藏入口。
- 若 v1.0 的 `getRegistrationPolicy` 已被外部引用：保留 `GET /auth/registration-policy` 作为同 handler 的弃用别名一个版本，响应 additive 带 `password_reset_enabled`，并在 API-Catalog 标注弃用；新代码用 `/auth/verification-policy`。

### 13.11 验收清单

以 §10.2 的 #19~#33 为准；手工重点：

1. 存在/不存在邮箱发码均为 200 且文案一致；不存在邮箱无邮件。
2. 重置成功旧密码失效、新密码可登录；旧 refresh/PAT 失效。
3. 第 10 次错码提示过期；重发后新码可用。
4. 弱密码/新旧相同不消耗验证码。
5. 关闭 `PASSWORD_RESET_ENABLED` 后登录页无入口，直达页显示未开放。

---

## 附录 A：注册验证码 HTML 模板（建议稿）

> 变量：`{code}`（6 位数字）、`{ttl_minutes}`（10）、`{brand}`（星星助手）、`{site_host}`（xingst.site）。
> 渲染时对非数字变量做 HTML 转义；不要把用户邮箱放进正文。模板控制在 20KB 内，不引外部图片。

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>注册验证码</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
    你的注册验证码是 {code}，{ttl_minutes} 分钟内有效。
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f6f5f0;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:560px;background:#fbfaf6;border:1px solid #e3e1d6;border-radius:14px;overflow:hidden;">
          <tr>
            <td style="height:6px;background:#3b5f4b;font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          <tr>
            <td style="padding:32px 32px 8px;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;color:#1b1d19;">
              <p style="margin:0 0 8px;font-size:12px;line-height:1.4;letter-spacing:.08em;color:#5b6057;text-transform:uppercase;">
                {brand} · 账户安全
              </p>
              <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;font-weight:600;color:#1b1d19;">
                验证你的邮箱
              </h1>
              <p style="margin:0;font-size:14px;line-height:1.7;color:#3c4038;">
                你正在注册{brand}。请在注册页面输入下面的验证码，验证码
                <strong style="color:#1b1d19;">{ttl_minutes} 分钟</strong>内有效。
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:24px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#f1efe8;border:1px solid #e3e1d6;border-radius:10px;">
                <tr>
                  <td align="center" style="padding:20px 12px;">
                    <div style="font-family:'SF Mono','JetBrains Mono',Menlo,Consolas,monospace;font-size:36px;line-height:1.1;letter-spacing:8px;font-weight:600;color:#1b1d19;white-space:nowrap;">
                      {code}
                    </div>
                    <div style="margin-top:8px;font-size:12px;line-height:1.5;color:#5b6057;">
                      {ttl_minutes} 分钟内有效 · 仅可使用一次
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:0 32px 28px;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;">
              <p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:#3c4038;">
                如果这不是你本人的操作，请忽略这封邮件，你的账户不会被创建。
              </p>
              <p style="margin:0;font-size:12px;line-height:1.7;color:#5b6057;">
                为了账户安全，请勿将验证码转发给他人。{brand}不会通过电话、短信或邮件向你索要验证码。
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:16px 32px;background:#f1efe8;border-top:1px solid #e3e1d6;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;">
              <p style="margin:0;font-size:11px;line-height:1.6;color:#686d63;">
                此邮件由系统自动发送，请勿直接回复。<br>
                {brand} · {site_host}
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
```

可选暗色增强（部分客户端支持）：

```html
<style>
  @media (prefers-color-scheme: dark) {
    .mail-bg { background:#141613 !important; }
    .mail-card { background:#1b1d19 !important; border-color:#3c4038 !important; }
    .mail-text { color:#f1efe8 !important; }
    .mail-muted { color:#a9afa4 !important; }
    .mail-code { background:#242822 !important; border-color:#3c4038 !important; }
  }
</style>
```

> 若加入暗色类名，需同步给表格/文字加 `class`；不加入时以浅色内联样式为准，保证 Outlook 等客户端可读。

## 附录 A.2：找回密码邮件文案（与 A 共用布局）

将附录 A 的以下文案替换即可，HTML 结构/样式不变：

| 位置 | 注册版 | 找回版 |
|---|---|---|
| `<title>` | 注册验证码 | 重置密码验证码 |
| 隐藏预header | 你的注册验证码是 {code}… | 你的重置密码验证码是 {code}… |
| 小标题 | {brand} · 账户安全 | {brand} · 密码安全 |
| H1 | 验证你的邮箱 | 重置你的密码 |
| 正文 | 你正在注册{brand}… | 你正在重置{brand}的登录密码… |
| 安全提示 | 非本人操作请忽略，你的账户不会被创建 | 非本人操作请忽略，你的密码不会被修改 |
| 追加提示 | — | 重置成功后，所有已登录设备将退出，API Token 将失效 |
| 页脚 | 此邮件由系统自动发送… | 同注册版 |
| 主题 | 【星星助手】注册验证码（{ttl_minutes} 分钟内有效） | 【星星助手】重置密码验证码（{ttl_minutes} 分钟内有效） |

> 找回邮件不包含“去登录/重置”按钮或链接；用户回到应用内输入验证码，减少钓鱼面。

## 附录 B：关键算法伪代码

### B.1 精确冷却预占

```python
async def reserve(session, *, scope, key_hash, now, cooldown, hour_limit, day_limit):
    await session.execute(
        pg_insert(EmailCodeSendThrottle)
        .values(scope=scope, key_hash=key_hash)
        .on_conflict_do_nothing(index_elements=["scope", "key_hash"])
    )
    row = await session.scalar(
        select(EmailCodeSendThrottle)
        .where(scope=scope, key_hash=key_hash)
        .with_for_update()
    )
    assert row is not None

    if row.last_sent_at is not None:
        elapsed = int((now - row.last_sent_at).total_seconds())
        if elapsed < cooldown:
            return Reservation.denied(max(1, cooldown - elapsed))

    if row.hour_start is None or now >= row.hour_start + timedelta(hours=1):
        row.hour_start, row.hour_count = now, 0
    if hour_limit and row.hour_count >= hour_limit:
        reset = row.hour_start + timedelta(hours=1)
        return Reservation.denied(max(1, int((reset - now).total_seconds())))

    if row.day_start is None or now >= row.day_start + timedelta(days=1):
        row.day_start, row.day_count = now, 0
    if day_limit and row.day_count >= day_limit:
        reset = row.day_start + timedelta(days=1)
        return Reservation.denied(max(1, int((reset - now).total_seconds())))

    row.last_sent_at = now
    row.hour_count += 1
    row.day_count += 1
    row.updated_at = now
    return Reservation.allowed()
```

> 注册：IP/邮箱两个 scope 在同一事务内、按 `ip` → `email` 固定顺序预占；任一拒绝则回滚，避免 IP 被白占。找回：只预占 IP；用户存在时才预占邮箱，邮箱拒绝时保留 IP 冷却并返回通用 200。`Reservation.denied` 不写计数。

### B.2 验证码校验

```python
async def verify(session, *, email, purpose, code, now):
    repo = EmailVerificationRepository(session)
    row = await repo.get_for_update(email=email, purpose=purpose)
    if row is None:
        return VerifyResult.expired()
    if row.status != "active" or row.expires_at <= now or row.attempts >= row.max_attempts:
        # 已是 locked/failed/consumed 的行保留原终态；只把 active 过期行落为 expired。
        if row.status == "active":
            await repo.mark_terminal(email=email, purpose=purpose,
                                     status="expired", now=now)
        return VerifyResult.expired()
    if not hasher.verify(row.code_hash, f"{pepper}:{code}"):
        outcome = await repo.record_failure(email=email, purpose=purpose, now=now)
        if outcome is None:
            # 并发下另一事务已消费/锁定；按过期处理，注册事务回滚/提交由调用方决定。
            return VerifyResult.expired()
        if outcome.attempts >= row.max_attempts:
            return VerifyResult.expired(attempts_exceeded=True)
        return VerifyResult.invalid(remaining=row.max_attempts - outcome.attempts)
    consumed = await repo.consume(email=email, purpose=purpose, now=now)
    return VerifyResult.ok() if consumed else VerifyResult.expired()
```

## 附录 C：参考

- [PRD-v2 FR-L2 / OQ-1](../prd/PRD-v2.md)
- [API-Catalog §0.8 限流、§1 端点、§3 错误码、§6 验收](v2/API-Catalog.md)
- [DATABASE §2.1 RLS 例外](../DATABASE.md)
- [AGENTS：错误码/契约/分层/仓储约束](../../AGENTS.md)
- 阿里云邮件推送 `SingleSendMail` 文档与默认凭据链说明（实现时以控制台/官方文档为准）
