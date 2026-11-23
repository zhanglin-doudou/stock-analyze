# AGENTS.md — stock_delivery.chat（智能助手编排）

**前置**：必读根目录 `AGENTS.md`，以及 `backend/packages/delivery` 的依赖边界。
本文件只补 **交付层 · 对话编排模块** 的特有约定；不重复全仓通用规则。

## 定位

`stock_delivery.chat` 负责“**单轮问答的确定性编排**”：

```text
上下文装载 → 语义理解 → 白名单工具取数 → 模板答案 → 可选 LLM 措辞/解释
→ 数字/引用校验 → 降级 → 持久化
```

它**不负责**：

- 数据库表/仓储（在 `stock_platform.repositories.chat`）
- 业务取数 SQL（在 `stock_review.application.chat`，通过 `ChatDataPort` 注入）
- HTTP 路由/SSE 编码/鉴权限流（在 `stock_api.routers.chat`）
- 前端渲染、时间格式、分页 UI（在 `frontend/apps/web/src/assistant`）

边界一句话：**编排在 delivery，数据在 review，协议在 api，展示在 web。**

## 依赖与导入铁律

允许依赖：

- `stock_contracts.chat`：DTO / 枚举 / `ChatDataPort` / `LlmClient`
- `stock_platform.config`：只读 `Settings`
- `stock_platform.repositories.chat`：通过 `ChatRuntime.repo` 注入，不自行建 session
- 第三方：`langgraph`、`langchain-openai`、`langchain-core`、`json-repair`、`tiktoken`

禁止：

- ❌ `import stock_review.*`（业务数据必须从 `ChatDataPort.invoke()` 进入）
- ❌ `import stock_api.*`（delivery 不得反向依赖 HTTP 层）
- ❌ 在 delivery 内直接 `select/update` 数据库、创建 `AsyncSession`
- ❌ 使用 `AgentExecutor`、Memory、VectorStore、RAG、checkpointer
- ❌ 把 API key、完整 prompt/密钥写日志或普通 print

## 1. 固定图，不做 Agent Loop

`graph.py` 的拓扑是固定的：

```text
START → load_context → understand
                         ├─ clarify → compose
                         └─ tools   → execute_tools → compose
compose → (answer | validate)
answer  → validate
validate → (persist | retry | fallback)
fallback → persist → END
```

规则：

- 只允许一个 `answer` 重试（`retry_count == 1`），禁止无限自循环。
- `graph.compile()` **不传 checkpointer**；会话状态只来自 `ChatRepository`。
- 新增/删除节点必须同步更新本文件、`graph.py` 与 `test_runtime.py`。
- `TOOL_PLATFORM_ENABLED=false`（默认）时工具选择由 `runtime._tools_for()` 决定；`true` 时走 `ToolRegistry + RulePlanner + ToolExecutor`。两种路径都**不允许 LLM 直接调用任意工具**。

## 2. 工具白名单与意图（2026-11-21 P0 起双路径）

`TOOL_PLATFORM_ENABLED=true` 时，`execute_tools` 走新工具平台：
`ToolRegistry.snapshot()` → `RulePlanner.plan()` → `ToolExecutor.execute()`；
内置工具元数据在 `stock_review/application/chat/provider.py` 的 `BUILTIN_SPECS`，
执行器统一做 schema 校验/超时/去重/留痕。新增工具优先只改 `BUILTIN_SPECS` +
handler；`ToolName`/`_tools_for()` 是 flag=false 的 legacy 路径，仍保持兼容。

`ToolResult.tool` 是短名字符串（兼容旧值），新代码用 `tool_id`（如
`builtin.trades_query`）做唯一标识；不要在新代码里假设 `tool` 是 `ToolName`。

`ChatQueryFacade` 是唯一数据入口；delivery 只传 `ToolName + args`。

| 意图 | 工具 |
|---|---|
| `ASSETS` / `PORTFOLIO` | `assets_query` + `portfolio_snapshot` |
| `PERFORMANCE` | `performance_metrics` |
| `ATTRIBUTION` | `attribution_summary` |
| `ROUNDS` / `ROUND_DETAIL` | `rounds_query` / `round_detail` |
| `TRADES` | `trades_query` |
| `NAV` | `nav_summary` |
| `SECTORS` | `sector_exposure` |
| `DATA_QUALITY` | `data_quality` |
| `METRIC_EXPLAIN` | `metric_explain` |

关键约定：

- “本月赚了多少 / 赚了还是亏了 / 总收益 / 累计盈亏”属于 **`PORTFOLIO`**，读资产表/Dashboard，不读指标；由 `understanding._explicit_intents()` 优先覆盖模型输出。
- “收益率 / 胜率 / 回撤 / 夏普”才属于 `PERFORMANCE` 指标口径。
- 每轮最多 3 个工具；`_dedupe()` 必须保留。
- 工具参数唯一生成点是 `runtime._tools_for()`：
  - `period` 只在 `understanding.period.value` 非空时写入 `args`；不要在各工具里另造默认区间。
  - 意图 → 工具、`limit`、`sort`/`sort_dir` 都在这里集中约束；工具实现只读 `args`。
  - “操作/交易/买卖/成交”必须归 `TRADES`；“本月/这个月”由 `_rule_period` 或模型 period 归一化保证有 `YYYY-MM`。
- 工具只能只读；写操作/任务提交不属于聊天工具。
- 工具返回 `status`、`citations`、`data_as_of`、`missing`、`error_code` 必须原样传递到回答与 trace。

## 3. 语义理解

`understanding.py` 负责：

- 意图（最多 2 个）
- 期间（`month/year/range/all/relative/none`）
- 实体（证券/回合/指标/板块/旗标）
- 指代替换（它/这个/那笔/那只…）
- `resolved_query`、`needs_clarification`

规则：

- 指代只能从 `focus` 或最近对话解析；没有唯一目标才澄清。
- 低质量模型输出（`confidence <= 0` 或 `resolved_query` 为空）必须回落到规则理解，不能把每个问题都变成“请补充”。
- `glm-4-flash` 可能出现 `confidence=0`、`period.value=null`；这是已知模型特性，不是模型不可用。
- 不要把模型返回的 `needs_clarification` 无条件透传；先跑本模块的确定性修正。
- 修改意图/轨迹后必须补 `backend/tests/unit/delivery/chat/test_understanding.py`。

## 4. 上下文与压缩

`context.py`：

- 默认窗口：`LLM_HISTORY_TURNS=10` 轮。
- 触发压缩：`LLM_CONTEXT_WINDOW_TOKENS * LLM_CONTEXT_COMPRESS_RATIO`（默认 200K × 0.8 = 160K）。
- 压缩输出写回 `chat_conversation.summary`；不新增会话/checkpoint 表。
- `build(..., exclude_message_id=...)` 必须排除当前已落库的用户消息，避免当前问题在上下文里出现两次。
- 历史消息按 `created_at + id` 稳定排序；同一轮 user/assistant 的 `created_at` 可能完全相同，不能只按时间排序。
- 不做 RAG/向量检索；工具结果只以摘要/瘦身形式进上下文。

## 5. LLM Provider（2026-11-22 供应商目录化）

`provider.py`：

- 供应商/模型/key 的唯一来源是 **`config/llm_providers.json`**（`stock_platform.llm_registry`）：
  供应商 id/name/base_url/`api_key_env`（key 的**环境变量名**）/模型列表 + `default_*`；
  目录改动需重启 API。key 只从环境解析（`os.environ` 优先、本地回落 `.env`），禁止打印/入库。
- `OpenAiCompatProvider`：任意 OpenAI-compatible `/chat/completions`（智谱/小米/…），
  `provider`/`model`/`base_url`/`api_key` 显式注入；key 缺失**不阻断构造**，请求期抛
  `LLM_NOT_CONFIGURED` 走降级。`json_mode=True` 用 `response_format=json_object`；
  `thinking` 参数只在目录 `supports_thinking=true` 的供应商上随 `LLM_THINKING=disabled` 下发。
- `LlmProviderFactory.resolve(selection)`：按用户 `llm_prefs`（`{"provider","model"}`，空 = 跟随
  默认）解析并按 `(provider_id, model)` 缓存构建；选择无效/为空返回 `None` 由调用侧回落默认。
  装配点在 `stock_api.routers.chat._resolve_llm()`（按请求动态路由，改设置后下一条消息生效）。
- `FakeProvider`：CI/单测/无网环境；禁止在 CI 调真实模型。
- `build_provider(settings, registry)`：`LLM_PROVIDER=none/local` → `FakeProvider`；`auto` →
  目录默认目标；目录内 provider id → 该供应商首个模型；未知 id 启动失败。
- `ChatRuntime(llm_enabled=...)`：是否允许调用 LLM 是**按请求**解析值（缺省 `settings.llm_enabled`
  = `LLM_PROVIDER` 非 none/local）；不要在 runtime 内再读 `settings.llm_enabled`。
- `ChatOpenAI(max_retries=0)`：**不要在 SDK 内自动重试**；runtime 的 `_complete_with_retry()` 统一处理 understanding/answer 的超时、429、5xx、坏响应。
- 重试次数：`LLM_RETRY_ON_429`（兼容旧名，默认 2 次；即最多 3 次调用），退避 `LLM_RETRY_BACKOFF_SECONDS × 2^attempt`。
- 不重试：`LLM_NOT_CONFIGURED` / `LLM_AUTH_FAILED` / `LLM_CONTENT_FILTER`；每次失败都写 `chat_llm_call`，最终失败再降级。
- 请求/响应必须进入 `ChatRepository` 的 `chat_llm_call`（`request`/`response`/usage/status/error_code；`provider`/`model` 取自实际调用的 provider）。
- `.env` / `config/llm_providers.json` 修改后 **必须重启 API**；`Settings` 在进程启动时加载，uvicorn `--reload` 不监听 `.env`。
- 禁止打印任何供应商 API key；禁止把 key 写入 trace、日志、测试快照。
- 每次真实 LLM 调用写一条 `chat.llm.call` 结构化日志：
  - 成功：`info`，字段 `status=ok` / `purpose` / `provider` / `model` / `prompt_tokens` / `completion_tokens` / `total_tokens` / `latency_ms`。
  - 失败：`error`，字段 `status=error` / `purpose` / `provider` / `model` / `error_code` / `status_code` / `latency_ms`。
  - **不记录** messages、answer 正文、API key；日志测试见 `test_provider_logging.py`。

## 6. 模板答案与 LLM 答案

`composer.py`：

- 先产生确定性模板；金额/比率只渲染后端字符串。
- 模板也必须有 citations 与 `fallback=True` 语义，保证 LLM 故障时仍可用。
- 不计算新数字，不做买卖建议/目标价/收益预测。

`runtime.answer()`：

- 只使用证据包中的数字；`validator.py` 做数字白名单。
- 引用标记统一 `[[n]]`，`n` 为 `citations[].number`；不要把 `citation_id` 当正文。
- 若模型只填 `claims`、`answer_markdown` 为空，允许从有实质内容的 claims 组装答案；不要把用户问题本身当答案。
- LLM 输出 HTML 必须清洗；禁止外链/图片。
- 模型失败/坏 JSON/校验失败 → `fallback` 节点；不能让请求 500。
- `llm_used=True` 表示至少一次 LLM 调用成功；仅理解成功但需要澄清时为 `False` 是正常的。

## 7. 校验与降级

`validator.py` 必须检查：

- 数字是否在工具结果/citations 白名单内
- 引用标记是否存在且编号有效
- 是否包含买卖建议/收益预测
- 是否包含 HTML/脚本
- 模板有数字时，LLM 答案不能把数字全部替换成引用标记
- 数字分类：金额/价格/数量严格白名单；符号差异、日期/序号/计数、缺失 inline citation 记 warning，不阻断
- `_parse_answer` 使用 `_AnswerIn` schema 校验 answer_markdown/claims/used_citations/confidence；不要退回裸 dict
- warnings 写 `chat.llm.validation_warning`，errors 写 `chat.llm.validation_failed`

失败处理：

- 首次校验失败可重试一次 `answer`；第二次仍失败 → `fallback`。
- 降级原因必须写入 `AnswerDraft.degrade_reason` 与 `chat_turn.degrade_reason`。
- 禁止只返回空字符串或静默模板；必须给 `degrade_message` / `what_to_do`。
- 状态列宽度为 32（迁移 `0021_chat_message_status_width`），`needs_clarification` 必须可落库。

## 8. 持久化与回放

`runtime.persist()` 负责：

- 先写 `chat_tool_call` / `chat_llm_call`，再写 assistant `chat_message`，最后 update `chat_turn`。
- 任何一条写入失败都必须让请求失败，不能留下半轮。
- `request`/`response` 原样保存（敏感 key 除外），供 `GET /chat/turns/{id}/trace` 与 replay。
- 不在 delivery 内做软删/清空；清空由 API-115 的 `ChatRepository.reset_conversation()` 负责。
- 回放模式 `deterministic` 用存储的工具参数重跑；`llm` 用存储的 answer request；`full` 用存储 understanding 结果 + tools + answer request。
- 修改持久化字段必须同步 `backend/migrations/`、`ChatRepository`、`ChatTraceOut`、API tests。

## 9. SSE 与 API 的边界

SSE 编码在 `stock_api.routers.chat`，不在本模块：

- `POST /chat/sessions/{session_id}/messages` 带 `Accept: text/event-stream` 时返回 SSE；
- 事件顺序：`meta` → `delta`（可多次）→ `done`；
- 默认 `Accept: application/json` 仍返回完整 `ChatMessageOut`；
- delivery 只负责生成最终 `ChatMessageOut`，不要在这里拼 SSE 字符串或写 HTTP header。

修改 SSE 时同时检查：

- 前端 `frontend/apps/web/src/assistant/sse.ts` 的解析（CRLF/跨 chunk/多行 data）；
- `AssistantRuntime.tsx` 的 async generator、abortSignal、done 后 citations/来源；
- `backend/tests/api/test_chat.py` 的 SSE 用例。

## 10. 测试与门禁

本模块改动至少跑：

```bash
uv run pytest backend/tests/unit/delivery/chat -q
uv run ruff check backend/packages/delivery/src/stock_delivery/chat
uv run mypy backend/packages/delivery/src/stock_delivery/chat
```

涉及 API/迁移/真实 DB：

```bash
make db-up
STOCK_DB_PORT=5433 make test-chat
STOCK_DB_PORT=5433 make test-p2
```

涉及契约/路由：

```bash
make api-snapshot
make api-check
make sdk
```

约定：

- CI 一律用 `FakeProvider`，不调真实 GLM；真实 GLM 只做人工 smoke。
- 新增内置工具：`TOOL_PLATFORM_ENABLED=true` 时补 `stock_review/application/chat/provider.py` 的 `BUILTIN_SPECS` + handler；`false` 时补 `ToolName`、`_tools_for()`。两条路径都要补 citations 与单元/API 测试，不能只改一条。
- 改 prompt 必须升 `prompt_version`（`chat.understand.v1` / `chat.answer.v1`），并跑 golden/validation。
- 新增/修改 SSE 事件必须同步 API-Catalog、设计文档、前端 parser 与测试。

## 11. 常见故障定位

| 现象 | 优先检查 |
|---|---|
| `llm_calls` 为空 | 部署 `LLM_PROVIDER` / 目录 `api_key_env` 对应 key 是否注入；改 `.env`/目录后是否重启 |
| 每个问题都“请补充” | `understanding._normalize()` 的低质量回落；模型是否 `confidence=0` / 空 `resolved_query` |
| 回答读指标不读资产表 | `_explicit_intents()` 与 `_tools_for()` 的意图映射 |
| `needs_clarification` 写入 500 | `chat_message.status` 宽度（0021）是否已迁移 |
| 消息顺序错乱 | `list_messages` 是否 `ORDER BY created_at, id`；同一事务时间戳相同 |
| SSE 看起来不生效 | 请求 `Accept`、响应 `text/event-stream`、API 是否旧进程、delta 是否被缓冲 |
| 前端只显示 8 条但无法加载更多 | API `next_cursor`、`before_id` 的 `created_at+id` 游标 |
| 模型 429 | 免费额度/频率；不要靠增大重试放大请求 |

## 12. 文件清单

| 文件 | 职责 |
|---|---|
| `graph.py` | 固定 LangGraph 拓扑；无 checkpointer |
| `state.py` | `ChatState` TypedDict |
| `runtime.py` | 节点实现、工具选择、答案/校验/降级/持久化 |
| `tools/` | `ToolRegistry`/`ToolCatalog`/`ToolExecutor`/`RulePlanner`（P0 工具平台） |
| `context.py` | 10 轮窗口、160K 压缩、understanding messages |
| `understanding.py` | 意图/期间/实体/指代与规则回落 |
| `provider.py` | OpenAI-compatible/Fake provider、错误映射、动态工厂（2026-11-22） |
| `composer.py` | 确定性模板答案 + citations/claims |
| `prompts.py` | understanding/answer/compress v1 提示词 |
| `validator.py` | 数字/引用/建议/HTML 校验 |
| `AGENTS.md` | 本文件 |
