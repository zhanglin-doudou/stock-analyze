# 智能分析助手 · 完整开发设计 v1.0

> 状态：**设计定稿（2026-11-20）**，待实现；前端排到优先级 P3（不是 Period3），先做后端基建与端点。
> 需求基线：[PRD-v2 FR-D7](../prd/PRD-v2.md)、[DEV-PLAN Period3-07](../DEV-PLAN-v2.md)、[ADR-0022](v2/ADR.md)、[API-Catalog API-077/078](v2/API-Catalog.md)。
> 本设计取代 [2026-11-19 调研稿](2026-11-19-intelligent-assistant-research.md) 中的 L0/L1 隐私分层与“不做历史端点”结论；调研稿的其他分析保留参考价值。
> 已拍板：GLM-4.7-Flash 免费版验证、单会话、对话/工具/LLM 调用全量入库可回放、滑动窗口 10 轮 + 200K 的 80% 压缩、语义理解与指代替换、业务数据可全量使用、不做 RAG、LLM 故障输出降级原因与服务不可用提示。

---

## 1. 结论与范围

### 1.1 一句话方案

**一个用户一条对话；每轮先由 GLM-4.7-Flash 做语义理解和指代替换，再由服务端白名单工具取数，最后让同一个模型基于证据包生成回答；所有 understanding / tool call / llm call / answer 原样入库，可回放。**

### 1.2 本期做

- 单会话对话记录、上下文管理、压缩、回放。
- 语义理解（意图/期间/标的/指代）+ 规则兜底。
- 10 个只读工具（含资产表、流水旗标、回合/回合详情旗标）。
- GLM-4.7-Flash provider（OpenAI-compatible），免费额度验证。
- API-077/078 + 新增 112~115。
- 数据模型、RLS、迁移、测试、观测。
- 后端 P1/P2；前端 P3。

### 1.3 本期不做

- 多会话、会话列表、会话切换。
- RAG、向量库、文档切片、知识库。
- 模型自主调用任意工具 / 自动写 SQL / 代码执行。
- 流式输出（V1 同步 200；P3 前端再做 SSE）。
- 前端页面（P3）。
- 投资建议、买卖推荐、收益预测。

### 1.4 为什么不是“自由 Agent”

- **正确性**：数字必须来自后端；模型只做语义理解、归因解释和措辞。
- **可回放**：工具参数和结果入库，重放不依赖模型记忆。
- **防越权**：`user_id` 只从 `TenantContext` 注入，模型无法指定租户。
- **成本/延迟**：固定 1 次理解 + 1~3 次工具 + 1 次回答，不做多轮 Agent 循环。

---

## 2. 总体架构

```text
POST /chat/sessions/{session_id}/messages
  │
  ▼
[TurnService] 幂等、限流、会话归属校验
  │
  ▼
[ContextManager] 取最近 10 轮 + rolling summary + focus
  │  token 估算 > 200K*80% → 压缩（工具瘦身/滚动摘要/硬截断）
  ▼
[QueryUnderstanding] GLM-4.7-Flash JSON
  │  intent / period / entities / resolved_query / clarification
  │  失败 → 规则兜底；仍无法理解 → 降级提示
  ▼
[ToolGateway] 白名单工具，user_id 注入，参数 schema 校验
  │  assets / trades / rounds / round_detail / portfolio /
  │  metrics / attribution / nav / sectors / data_quality
  ▼
[EvidenceBundle] 值 + display + citations + status + data_as_of
  │
  ▼
[AnswerComposer] 生成回答计划（模板/claim/引用编号）
  │
  ▼
[AnswerLLM] GLM-4.7-Flash 基于证据包写回答
  │  失败 → 模板答案 + degrade_reason
  ▼
[OutputValidator] 数字白名单/引用校验/禁建议词/长度/HTML 清洗
  │  失败 → 修复一次；仍失败 → 模板答案
  ▼
[TurnStore] 写 message / turn / tool_call / llm_call
  │
  ▼
200 {answer, citations, status, degrade_reason, context_usage, trace_id}
```

### 2.1 组件落位

| 层 | 模块 | 职责 |
|---|---|---|
| `stock_contracts` | `chat.py` | `ChatMessage`/`Citation`/`ToolResult`/`ChatStatus`/`DegradeReason` DTO；`LlmClient` Protocol |
| `stock_platform` | `repositories/chat.py`、`models.py` | 会话/消息/调用仓储、RLS；不依赖 LangChain |
| `stock_review` | `application/chat_query.py` | `ChatQueryFacade`：把现有 dashboard/metrics/attribution/rounds/trades/assets/nav/sectors 结果收敛为 `ToolResult`；补旗标 |
| `stock_delivery` | `chat/` | LangGraph 编排、GLM/LangChain 适配、`ContextManager`、`QueryUnderstanding`、`ToolGateway`、`AnswerComposer`、`OutputValidator`、`ReplayService`；只依赖 contracts/platform，不 import review/ledger |
| `stock_api` | `routers/chat.py`、`schemas.py` | API、tenant 注入、facade 适配器、GLM 工厂、错误映射 |

> 关键边界：`stock_delivery` 不直接读 DB、不 import `stock_review`。工具实现由 `stock_review.application.chat_query.ChatQueryFacade` 提供，API 层注入到 `ToolGateway` 的 `ChatDataPort`。

### 2.2 依赖选型（先定，不边写边加）

只引入“编排 + 模型适配 + JSON 修复 + token 估算”四类；**不引入 LangChain 的 Agent/Memory/VectorStore 体系**。

| 包 | 版本 | 放在 | 用途 | 明确不用 |
|---|---|---|---|---|
| `langchain` | `>=1.0,<2.0` | `stock-delivery` | 元包，保持 LangChain 生态兼容 | `AgentExecutor`、`create_react_agent`、`initialize_agent` |
| `langchain-core` | `>=1.0,<2.0` | `stock-delivery` | `BaseMessage`/`RunnableConfig`/回调类型 | `ConversationBufferMemory`、`VectorStore`、`Retriever` |
| `langchain-openai` | `>=1.0,<2.0` | `stock-delivery` | `ChatOpenAI` 对接 GLM 的 OpenAI-compatible `/chat/completions` | `AzureChatOpenAI`、OpenAI Assistants |
| `langgraph` | `>=1.0,<2.0` | `stock-delivery` | `StateGraph` 编排单轮：理解→工具→回答→校验 | `create_react_agent`、`ToolNode` 自主循环 |
| `json-repair` | `>=0.30` | `stock-delivery` | GLM 偶发 JSON 截断/尾逗号修复后再 Pydantic 校验 | 不用它替代 schema 校验 |
| `tiktoken` | `>=0.8` | `stock-delivery` | 可选 token 估算（默认仍用 `approx`，避免离线下载 BPE） | 不作为唯一 token 依据；以 usage 校准 |
| `respx` | `>=0.22` | `dev` | mock GLM HTTP，测试超时/429/5xx/坏 JSON | 不在生产依赖 |

**明确不装**：

- `langchain-community`、`langchain-postgres`、`langgraph-checkpoint-postgres`：我们用自己的 5 张表做持久化和回放，不引入第二套 checkpoint schema。
- `langsmith`：默认关闭外部 tracing；用 `chat_llm_call` 本地留痕。设置 `LANGCHAIN_TRACING_V2=false`、`LANGSMITH_TRACING=false`。
- `langchain-text-splitters`、`pgvector`、任何向量库：本期不做 RAG。
- `openai` 直接 SDK：只通过 `langchain-openai` 使用，避免两套客户端。

**版本策略**：LangChain/LangGraph 1.x 承诺 2.0 前不做破坏性变更；本项目锁 `<2.0`。安装后以 `uv lock` 固定精确版本，CI 用 `uv sync --frozen`。

安装命令（确认本设计后执行，本轮不改 `pyproject.toml`）：

```bash
uv add --package stock-delivery "langchain>=1.0,<2.0" "langchain-core>=1.0,<2.0" \
  "langchain-openai>=1.0,<2.0" "langgraph>=1.0,<2.0" \
  "json-repair>=0.30,<1" "tiktoken>=0.8,<1"
uv add --dev "respx>=0.22,<1"
uv lock
```


**为什么不用 LangGraph checkpointer**：

- 我们需要的是**完整 request/response 回放**，checkpointer 只存图状态，不存工具结果和两次 LLM 的原始 payload。
- 单会话 + 自定义 `chat_turn/chat_tool_call/chat_llm_call` 已经覆盖恢复与审计；再加 checkpointer 会出现两套状态、两套清理策略。
- 图本身无状态依赖，`ChatRuntime` 通过闭包注入；`thread_id` 只用于日志/trace 关联，不作为持久化。

### 2.3 文件目录（实现时按此落地）

```text
backend/
├── migrations/versions/
│   └── 0020_chat_assistant.py          # 5 表 + RLS + 索引 + 回滚
├── packages/
│   ├── contracts/src/stock_contracts/chat/
│   │   ├── __init__.py
│   │   ├── dto.py                      # ChatTurn/Citation/ToolResult/ContextUsage
│   │   ├── enums.py                    # ChatStatus/DegradeReason/ToolName/Intent
│   │   └── ports.py                    # ChatDataPort/LlmClient Protocol（不依赖 LangChain）
│   ├── platform/src/stock_platform/
│   │   ├── config.py                   # GLM_*/LLM_* 配置
│   │   ├── models.py                   # + chat_conversation/message/turn/tool_call/llm_call
│   │   └── repositories/chat.py        # 单会话、消息、trace、回放查询
│   ├── review/src/stock_review/application/chat/
│   │   ├── __init__.py
│   │   ├── facade.py                   # ChatQueryFacade：统一入口/并发/裁剪
│   │   ├── assets.py                   # assets_query
│   │   ├── trades.py                   # trades_query + 旗标/解释
│   │   ├── rounds.py                   # rounds_query + round_detail + 旗标/解释
│   │   ├── portfolio.py                # portfolio_snapshot
│   │   ├── metrics.py                  # performance_metrics
│   │   ├── attribution.py              # attribution_summary
│   │   ├── nav.py                      # nav_summary
│   │   ├── sectors.py                  # sector_exposure
│   │   └── quality.py                  # data_quality
│   └── delivery/src/stock_delivery/chat/
│       ├── __init__.py
│       ├── state.py                    # ChatState TypedDict（无 DB 对象）
│       ├── graph.py                    # StateGraph 构建/编译
│       ├── runtime.py                  # ChatRuntime：依赖注入 + TraceRecorder
│       ├── context.py                  # 10 轮窗口/160K 压缩/摘要
│       ├── understanding.py            # 语义理解/指代/规则兜底
│       ├── tools.py                    # ToolGateway（调 ChatDataPort）
│       ├── composer.py                 # 模板/claim/引用编号
│       ├── validator.py                # 数字白名单/引用/禁建议词
│       ├── provider.py                 # ChatOpenAI(GLM)/FakeProvider
│       ├── replay.py                   # deterministic/llm/full
│       └── prompts/
│           ├── understand.v1.md
│           ├── answer.v1.md
│           └── compress.v1.md
├── services/api/src/stock_api/
│   ├── routers/chat.py                 # API-077/078/112~115
│   ├── schemas.py                      # 追加 chat schemas
│   └── openapi_meta.py                 # 追加 operationId/x-*
└── tests/
    ├── unit/delivery/chat/
    │   ├── test_context.py
    │   ├── test_understanding.py
    │   ├── test_validator.py
    │   └── test_graph.py
    ├── unit/review/test_chat_query.py
    ├── api/test_chat.py
    ├── api/test_chat_replay.py
    ├── isolation/test_chat_isolation.py
    └── contract/test_chat_contract.py
```

> `stock_delivery` 只依赖 `stock_contracts`/`stock_platform`；`stock_review` 提供 `ChatDataPort` 实现；`stock_api` 负责把两者接起来。这样 LangChain/LangGraph 不会渗到 contracts/core/review/platform。

### 2.4 LangGraph 图（固定拓扑）

```python
# 伪代码；实际节点为 ChatRuntime 的绑定方法
state = ChatState(...)
graph = StateGraph(ChatState)
graph.add_node("load_context", runtime.load_context)
graph.add_node("understand", runtime.understand)
graph.add_node("execute_tools", runtime.execute_tools)
graph.add_node("compose", runtime.compose)
graph.add_node("answer", runtime.answer)
graph.add_node("validate", runtime.validate)
graph.add_node("fallback", runtime.fallback)
graph.add_node("persist", runtime.persist)
graph.add_edge(START, "load_context")
graph.add_edge("load_context", "understand")
graph.add_conditional_edges("understand", runtime.route_after_understand,
    {"tools": "execute_tools", "clarify": "compose", "degrade": "fallback"})
graph.add_edge("execute_tools", "compose")
graph.add_conditional_edges("compose", runtime.route_after_compose,
    {"answer": "answer", "template": "validate"})
graph.add_edge("answer", "validate")
graph.add_conditional_edges("validate", runtime.route_after_validate,
    {"ok": "persist", "retry": "answer", "fallback": "fallback"})
graph.add_edge("fallback", "persist")
graph.add_edge("persist", END)
app = graph.compile()  # 不传 checkpointer
```

- `ChatState` 只放可 JSON 序列化的字段；DB session/HTTP client/LangChain model 放 `ChatRuntime`，不放进 state。
- 节点内不直接 `commit`；由 `ChatRuntime` 的 `TraceRecorder` 在每次工具/LLM 调用后短事务落库。
- `thread_id=conversation_id`、`turn_id` 只用于 trace 关联；不使用 checkpointer。
- `route_after_validate` 的 `retry` 最多 1 次；仍失败走 `fallback`。

---

## 3. LLM：GLM-4.7-Flash

### 3.1 选型事实

- GLM-4.7-Flash 是智谱 2026-01 发布并开源的 30B-A3B 混合思考模型，**免费调用**，**200K 上下文**，最大输出 128K；支持流式、Function Calling、结构化输出、上下文缓存。
- 通过 BigModel 的 OpenAI-compatible `/chat/completions` 调用；默认 base URL `https://open.bigmodel.cn/api/paas/v4`。
- 本期用它做两类调用：`understanding`（JSON）和 `answer`（JSON/文本）；压缩时再加 `compression`。

参考：[智谱 GLM-4.7-Flash 发布页](https://www.zhipuai.cn/zh/research/148)、[GLM-4.7-Flash 文档](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash)、[GLM-4.7 模型页](https://docs.z.ai/guides/llm/glm-4.7)。

### 3.2 配置

`backend/packages/platform/src/stock_platform/config.py` 增加：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `none`（本地/CI）；验证环境 `glm` | 已有字段，扩展 `glm` 实现 |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | 已在本地 `.env` 配置；可换代理/兼容网关 |
| `GLM_API_KEY` | 空 | 已在本地 `.env` 配置；只从环境/密钥注入，禁止入库/日志 |
| `GLM_MODEL` | `glm-4.7-flash` | 已在本地 `.env` 配置；免费验证模型 |
| `LLM_TIMEOUT_SECONDS` | `60` | 总超时；连接 5s；免费模型冷启动可能 >30s |
| `LLM_MAX_OUTPUT_TOKENS` | `4096` | 回答上限；理解 2048 |
| `LLM_TEMPERATURE` | `0.2` | 低温度，减少漂移 |
| `LLM_THINKING` | `disabled` | 免费验证优先低延迟；复杂问题可切 `enabled` |
| `LLM_CONTEXT_WINDOW_TOKENS` | `200000` | GLM-4.7-Flash 上下文 |
| `LLM_CONTEXT_COMPRESS_RATIO` | `0.8` | 160K 触发压缩 |
| `LLM_HISTORY_TURNS` | `10` | 滑动窗口默认轮数 |
| `LLM_MAX_CONCURRENCY` | `2` | 每副本并发；免费额度下先保守 |
| `LLM_RETRY_ON_429` | `2` | 429 最多重试 2 次（2s/8s 退避，尊重 `Retry-After`） |
| `LLM_RETRY_BACKOFF_SECONDS` | `2` | 429 首次退避；第二次 ×4 |
| `LLM_DAILY_TOKEN_BUDGET` | 待定 | 免费额度保护；80% 告警 |
| `LLM_TOKEN_ESTIMATOR` | `approx` | `approx`/`tiktoken`；预检保守估算 |

> 当前本地 `.env` 已存在 `GLM_API_KEY` / `GLM_BASE_URL` / `GLM_MODEL`；实现时只需把 `LLM_PROVIDER=glm` 打开（CI/无网环境保持 `none`）。本设计不会读取或打印密钥值。

`.env.example` 增加占位：

```bash
LLM_PROVIDER=none
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-4.7-flash
GLM_API_KEY=
LLM_TIMEOUT_SECONDS=60
LLM_MAX_OUTPUT_TOKENS=4096
LLM_TEMPERATURE=0.2
LLM_THINKING=disabled
LLM_CONTEXT_WINDOW_TOKENS=200000
LLM_CONTEXT_COMPRESS_RATIO=0.8
LLM_HISTORY_TURNS=10
LLM_MAX_CONCURRENCY=2
LLM_RETRY_ON_429=2
LLM_RETRY_BACKOFF_SECONDS=2
```

### 3.3 调用协议

- `POST {GLM_BASE_URL}/chat/completions`
- Header：`Authorization: Bearer {GLM_API_KEY}`、`Content-Type: application/json`
- Body：
  - `model`
  - `messages[]`
  - `temperature`
  - `max_tokens`
  - `response_format={"type":"json_object"}`（understanding/answer/compression 需要 JSON）
  - `thinking={"type":"disabled"}`（若网关不支持则省略，配置可关）
- LangChain 适配（`stock_delivery/chat/provider.py`）：

```python
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

llm = ChatOpenAI(
    model=settings.glm_model,
    api_key=SecretStr(settings.glm_api_key),
    base_url=settings.glm_base_url,
    temperature=settings.llm_temperature,
    max_tokens=settings.llm_max_output_tokens,
    timeout=settings.llm_timeout_seconds,
    max_retries=0,  # 429/超时由 ChatRuntime 统一处理，避免 SDK 隐式重试
    model_kwargs={"response_format": {"type": "json_object"}},
    extra_body={"thinking": {"type": "disabled"}},  # 若当前版本/网关不支持则省略
)
```

- 解析 `choices[0].message.content`、`usage.prompt_tokens/completion_tokens/total_tokens`。
- 错误映射：

| 上游 | `degrade_reason` | 处理 |
|---|---|---|
| 401/403 | `LLM_AUTH_FAILED` | 不重试；模板/503 |
| 429 | `LLM_RATE_LIMITED` | 最多 2 次（2s/8s），按 `Retry-After`；仍失败则降级 |
| 5xx/超时/连接失败 | `LLM_UNAVAILABLE` | 不重试；模板/503 |
| 200 但 JSON 非法 | `LLM_BAD_RESPONSE` | 修复一次；仍失败则模板 |
| 内容过滤/拒答 | `LLM_CONTENT_FILTER` | 模板 + 说明 |

- 免费额度：`cost_units=0`，但仍记录 token/延迟/状态；按 `LLM_DAILY_TOKEN_BUDGET` 与每用户日轮数保护。

### 3.4 本地连通性验证（2026-11-20）

- `.env` 中 `GLM_API_KEY` / `GLM_BASE_URL` / `GLM_MODEL` 均已存在；未打印密钥。
- POST `{GLM_BASE_URL}/chat/completions` 可达，模型名 `glm-4.7-flash`。
- 第一次最小请求在 30s 客户端超时；第二次立即返回 **HTTP 429 / code 1302（账户速率限制）**。说明免费版限流是现实约束，不是异常。
- 结论：
  - 本地验证不要并发打真实 GLM；CI/golden 用 `FakeProvider`，真实 GLM 只做手工 smoke。
  - 默认并发 2、429 退避重试 2 次、单用户 1 在途；仍 429 → `LLM_RATE_LIMITED` 降级，不重试放大。
  - 冷启动/排队可能超过 30s，默认总超时 60s；响应慢时先降级，不拖垮 API。


---

## 4. 会话、上下文与压缩

### 4.1 单会话模型

- **一个用户一条对话**：`chat_conversation.user_id` 唯一；没有“新建会话/会话列表”。
- `POST /chat/sessions` 语义改为“确保当前用户对话存在”：首次 201，已存在 200 + 现有 `conversation_id`；不创建第二条。
- 兼容旧契约：保留 `session_id` 命名，实际它就是 `conversation_id`；`POST /chat/sessions/{session_id}/messages` 校验 id 必须等于当前用户唯一会话，否则 404。
- 删除对话：软删该对话的消息并把摘要/focus 重置；不物理删审计所需的 `chat_turn`/`chat_tool_call`/`chat_llm_call`（用户明确要求可回放），或按保留期任务物理清理（后续）。

### 4.2 滑动窗口

- 默认 `window_turns=10`（1 轮 = 1 条用户消息 + 1 条助手消息）。
- 上下文由四段组成：
  1. 固定 system prompt（意图 schema、规则、当前时间）。
  2. `rolling_summary`（压缩摘要）。
  3. 最近 `window_turns` 轮消息（user/assistant；assistant 带结构化 `content_json`）。
  4. 当前问题 + 当前轮工具证据。
- 窗口只影响“送给模型的历史”，不影响数据库保留；历史仍可查/回放。
- 未来 `window_turns` 放用户设置：本期先落在 `chat_conversation.window_turns`（默认 10），后续再迁到用户设置，不提前加 `chat_prefs` 列。

### 4.3 Token 预算

- 上下文窗口：`200_000`。
- 触发阈值：`200_000 × 0.8 = 160_000`。
- 为输出预留 `LLM_MAX_OUTPUT_TOKENS`（默认 4096），预检输入预算取 `160_000 - 4_096 = 155_904`。
- 估算：
  - 默认 `approx`：CJK 字符约 1 token，ASCII 约 3.5 字符/token，JSON 额外 +10% 安全系数。
  - 可用 `tiktoken`（`cl100k_base`）近似；GLM 实际 tokenizer 可能不同，因此必须保留 10% 余量，并以 `usage` 回填校准。
- 每轮记录：
  - `estimated_input_tokens`
  - `actual_prompt_tokens`（来自 usage）
  - `actual_completion_tokens`
  - `compression_steps[]`
  - `included_turn_ids[]`
  - `summary_version`

### 4.4 压缩算法

按顺序执行，直到预检 token ≤ 预算：

1. **工具结果瘦身**（确定性，不调 LLM）
   - 单工具结果超过 `TOOL_RESULT_MAX_TOKENS=20K`：只保留聚合、TopN、命中项、citation 字段；`truncated=true`。
   - 历史轮中的工具结果替换为 `ToolResultSummary`（tool/status/关键事实/citation id），不保留全量行。
2. **滚动摘要**（调 GLM，`purpose=compression`）
   - 把最旧的 5 轮（或超过预算的最旧一半）压成 ≤2000 token 的摘要。
   - 保留：用户目标、已确认期间/标的、关键数字 + citation id、未解决问题、助手承诺、用户纠正。
   - 写回 `chat_conversation.summary`、`summary_tokens`、`summary_message_id`（compacted through）；`summary_version += 1`。
   - 压缩调用本身写入 `chat_llm_call(purpose=compression)`，可回放。
3. **硬截断**
   - 仍超预算：从最旧轮开始丢弃，至少保留 `rolling_summary + 当前轮 + 最近 4 轮`。
   - 标记 `context_truncated=true`，回答中提示“较早对话已压缩”。

**永不压缩**：当前用户问题、当前轮工具证据、当前轮 citations。若当前轮单独超预算，先裁剪工具结果；仍超则返回 `CONTEXT_OVERFLOW` 降级，不把半截证据交给模型。

**压缩失败**：摘要调用失败/超时 → 用确定性摘要（最近用户问题 + 工具 key facts + citation id），标记 `compression_fallback=true`；不阻塞回答。

### 4.5 语义理解与指代替换

#### 4.5.1 理解输出 JSON

```json
{
  "intents": ["attribution"],
  "confidence": 0.92,
  "period": {"kind": "month", "value": "2026-11", "label": "本月"},
  "entities": [
    {"mention": "它", "type": "round", "resolved_id": "rnd_...", "resolved_name": "XX", "source": "focus", "confidence": 0.95}
  ],
  "resolved_query": "2026-11 回合 rnd_...（XX）为什么亏损？",
  "needs_clarification": false,
  "clarification": null
}
```

- `intents` 最多 2 个；多意图时工具取并集，回答分节。
- `period.kind`：`month|year|range|all|relative|none`；相对时间（“上个月/今年/最近三个月”）按 `Clock` 解析。
- `entities.type`：`security|round|metric|sector|flag`。
- `source`：`focus|recent_tool|db_match|user_explicit`；用于审计“指代是怎么解析的”。
- `resolved_query`：必须能脱离上下文独立理解；不能残留“它/这个/那笔”。
- `needs_clarification=true` 时只问一个澄清问题，不调工具、不编答案。

#### 4.5.2 指代解析

- `focus` 存在 `chat_conversation.focus`：最近一次明确提到的 `security_code/name`、`round_id`、`metric_id`、`sector_code`、`period`。
- 候选来源优先级：用户显式 → 当前 focus → 最近 10 轮工具结果 → 用户持仓/回合库匹配。
- 唯一命中才解析；多候选（如“那只股票”对应多只持仓）→ `needs_clarification`，给出 2~3 个选项。
- 解析成功后写回 `focus`，供下一轮“它/那笔/这个指标”使用。
- 规则兜底（LLM 不可用）：固定问题词典 + 日期/代码正则 + focus；无法理解时提示使用固定问题。

#### 4.5.3 提示词版本

- `chat.understand.v1`：system + summary + last turns + focus + question → JSON。
- `chat.answer.v1`：system + evidence + answer plan → JSON。
- `chat.compress.v1`：system + old turns → JSON summary。
- 所有 prompt 版本写入 `chat_llm_call.prompt_version`；改 prompt 必须升版本并跑 golden 集。

---

## 5. 工具层

### 5.1 工具总表（10 个）

| 工具 | 用途 | 关键参数 | 数据源 | 上限 |
|---|---|---|---|---|
| `assets_query` | **直接查资产表** | `date_from/date_to/status/limit/cursor/sort` | `list_assets` | 100 行 |
| `trades_query` | 流水查询 + **旗标 + 旗标解释** | `period/security/side/category/limit/cursor` | `list_trades` + `load_trade_flags` | 50 行 |
| `rounds_query` | 回合列表 + **旗标 + 解释** | `period/security/flag/bucket/sort/limit` | `list_round_insights` | 20 行 |
| `round_detail` | 回合详情 + **旗标 + 解释** | `round_id` | `get_round_insight` | 1 回合 |
| `portfolio_snapshot` | 资产/持仓/仓位/盈亏概览 | `period?` | `build_dashboard` | 1 |
| `performance_metrics` | 18 指标 | `period/ids/domain` | `batch_metrics` | 18 |
| `attribution_summary` | 五因子归因 | `period/benchmark?` | `build_attribution` | 1 |
| `nav_summary` | TWR/XIRR/回撤 | `period?` | `compute_nav_points` | 1 |
| `sector_exposure` | 行业/概念暴露 | `taxonomy/period?` | `build_sector_overview` | 50 行业 |
| `data_quality` | 对账/构建/行情/口径 | `period?` | reconciliation + asset state | 1 |

可选第 11 个：`metric_explain`（口径解释），P1 可后置。

### 5.2 统一返回包

```json
{
  "tool": "trades_query",
  "status": "ok|degraded|blocked|insufficient|missing|error",
  "data_as_of": "2026-11-18",
  "definition_version": "1.2",
  "missing": [{"code": "NAV_UNRELIABLE", "message": "...", "what_to_do": "..."}],
  "truncated": false,
  "row_count": 12,
  "citations": [
    {"citation_id": "c1", "label": "流水金额", "value_display": "-1,234.56",
     "source_endpoint": "GET /api/v1/trades", "source_params": {"period": "2026-11"},
     "definition_version": "1.2", "data_as_of": "2026-11-18"}
  ],
  "data": {}
}
```

- 工具只读；参数由服务端 schema 校验；`user_id` 由 `TenantContext` 注入，工具参数里没有 `user_id`。
- 任一工具失败不拖垮整轮：部分成功 → `status=degraded` + `missing[]`；全部失败 → 回答说明数据不可用。
- `citations` 由服务端生成，模型只引用 `citation_id`。

### 5.3 `assets_query`

```json
{
  "tool": "assets_query",
  "data": {
    "items": [
      {"trade_date":"2026-11-18","cash":"12,345.67","market_value":"88,000.00",
       "total_assets":"100,345.67","holding_cost":"80,000.00","unrealized_pnl":"8,000.00",
       "daily_pnl":"123.45","daily_return_pct":"0.12%","status":"ok","definition_version":"2.4"}
    ],
    "build_state": {"state":"ok","data_as_of":"2026-11-18","definition_version":"2.4"},
    "next_cursor": null,
    "has_more": false,
    "summary": {"start":"2026-11-01","end":"2026-11-18","days":18,"latest_total_assets":"100,345.67"}
  }
}
```

用途：回答“我现在有多少资产/现金/仓位”“某天资产多少”“资产表是否构建/是否 blocked”。只读，不触发构建。

### 5.4 `trades_query`：旗标 + 顶层解释

```json
{
  "tool": "trades_query",
  "data": {
    "items": [
      {
        "trade_id": "trd_...",
        "trade_date": "2026-11-12",
        "security_code": "600000",
        "security_name": "浦发银行",
        "side": "buy",
        "category": "buy",
        "quantity": "100",
        "price": "10.000000",
        "amount": "-1001.00",
        "commission": "1.00",
        "stamp_tax": "0.00",
        "other_fee": "0.00",
        "summary": "证券买入",
        "flags": [
          {"code": "first_entry", "label_zh": "首次建仓", "status": "hit",
           "status_label": "命中", "reason": null, "lookahead": false}
        ]
      }
    ],
    "flag_glossary": [
      {"code": "first_entry", "label_zh": "首次建仓", "label_en": "First entry",
       "description_zh": "买入前该标的持仓为 0……", "description_en": "...",
       "side": "buy", "lookahead": false}
    ],
    "flag_config": {"window_trading_days": "5", "flags_hash": "...", "is_default": true},
    "flag_summary": {"first_entry": 3, "addon_average": 2}
  }
}
```

- `flag_glossary` 是**顶层单独字段**，前端/模型不需要自己拼文案。
- 每笔 `flags` 只放码值、状态、文案；解释统一在顶层，避免重复。
- 仅返回当前 `definition_version` + 当前 `flags_hash` 的旗标；旧指纹行不混入，另给 `flag_stale` 提示。
- 旗标读取复用 `load_trade_flags` / `trade_flag_glossary` / `config_view`，不复制判定逻辑。

### 5.5 `rounds_query` 与 `round_detail`

`rounds_query`：

```json
{
  "tool": "rounds_query",
  "data": {
    "items": [
      {"round_id":"rnd_...","security_code":"600000","security_name":"浦发银行",
       "status":"closed","open_date":"2026-10-01","close_date":"2026-10-20",
       "holding_trading_days":12,"bucket":"short","pnl":"1,234.56",
       "return_rate_pct":"5.00%","flags":["bottom_buy","swing_t"],
       "status_counts":{"bottom_buy":1,"stop_loss_sold_early":0},"quality":[]}
    ],
    "flag_glossary": [
      {"code":"bottom_buy","label_zh":"抄底","label_en":"Bottom buy",
       "description_zh":"……","kind":"item_flag"}
    ],
    "flag_config": {"window_trading_days":"5","flags_hash":"...","is_default":true}
  }
}
```

`round_detail`：

```json
{
  "tool": "round_detail",
  "data": {
    "round": {"round_id":"rnd_...","security_code":"600000","security_name":"浦发银行",
      "status":"closed","open_date":"2026-10-01","close_date":"2026-10-20",
      "holding_trading_days":12,"bucket":"short","pnl":"1,234.56","return_rate_pct":"5.00%"},
    "flags": ["bottom_buy","swing_t"],
    "status_counts": {"bottom_buy":1,"swing_t":0},
    "flag_glossary": [{"code":"bottom_buy","label_zh":"抄底",...}],
    "fills": [
      {"fill_id":"...","buy":{"trade_id":"...","trade_flags":[...]},
       "sell":{"trade_id":"...","trade_flags":[...]}}
    ],
    "trade_flag_glossary": [{"code":"first_entry","label_zh":"首次建仓",...}],
    "flag_config": {"window_trading_days":"5","flags_hash":"...","is_default":true}
  }
}
```

- `rounds_query` 和 `round_detail` 都带**顶层** `flag_glossary`；详情额外带 `trade_flag_glossary`（账单级旗标解释）。
- 复用 `RoundInsightItemOut` / `RoundInsightDetailOut` 的字段语义，不另造一套口径。
- 回合旗标（`bottom_buy` 等）与账单旗标（`first_entry` 等）不混用：前者是回合行为，后者是单笔账单状态。

### 5.6 公共约束

- 工具只读；不调用 `GET /reconciliation` 这类首次会写补差行的端点（如需对账状态，用只读查询）。
- 工具结果不超过 20K tokens；超出裁剪并 `truncated=true`。
- 所有 `source_endpoint` 必须是可调用的真实端点；引用值与端点返回一致。
- 工具参数不允许 `user_id`、`SQL`、`path`、任意 URL。

---

## 6. 回答协议与校验

### 6.1 回答 JSON

```json
{
  "answer_markdown": "本月已实现收益 **-1,234.56 元**，胜率 **45.00%**。归因显示板块因子是最大拖累（-1.2 个百分点）……",
  "claims": [
    {"claim_id":"k1","text":"本月已实现收益 -1,234.56 元","citation_ids":["c1"]},
    {"claim_id":"k2","text":"胜率 45.00%","citation_ids":["c2"]}
  ],
  "used_citations": ["c1","c2","c3"],
  "confidence": "high|medium|low",
  "followups": ["要不要看板块配置明细？"]
}
```

- 模型可以看全量业务数据，但**只能使用证据包里出现的数字**；不得自行计算新数字。
- 引用标记 `[[c1]]` 由模型写在正文；服务端替换为角标；未使用的 citation 不返回。
- 不允许 HTML、图片、外链、`javascript:`；Markdown 只允许段落/列表/加粗/行内代码。

### 6.2 校验

1. **数字白名单**：先剥离 citation 角标/Markdown 标记，再提取正文中数字（含 `%`、`元`、`万`、负号、千分位）；必须能在 `EvidenceBundle.citations.value_display`、`period` 标签、`data_as_of` 日期或本地派生字段中找到；未知数字 → 拒答/回退模板。
2. **引用覆盖**：每个含数字的 claim 至少 1 个 citation；citation 的 `source_endpoint` 可调用且值一致（验收阶段跑）。
3. **禁建议词**：买入/卖出/加仓/减仓/必涨/必跌/抄底建议/目标价等；命中 → 删除该句或回退。
4. **长度**：默认 ≤2000 字；超长截断。
5. **一致性**：`status=blocked` 时不得出现资产类数值；`degraded` 必须带说明。
6. **引用不存在的数字/引用**：`LLM_BAD_RESPONSE` → 修复一次（把错误列表回给模型）；仍失败 → 模板答案 + `fallback=true`。

> **引用范围**：V1 的回答只允许引用**当前轮** `EvidenceBundle` 的 citations；历史摘要里的 citation id 只用于上下文，不作为本轮引用返回。若回答需要历史数字，必须由本轮工具重新取数。这样 validator 不需要跨轮 citation 解析，回放也更稳定。

### 6.3 降级原因与服务不可用

| `degrade_reason` | 触发 | 返回 | 用户文案 |
|---|---|---|---|
| `LLM_NOT_CONFIGURED` | `LLM_PROVIDER=none` 且无固定模板 | 200 或 503 | “智能分析服务未配置，当前仅能返回结构化摘要。” |
| `LLM_TIMEOUT` | 30s 超时 | 200 模板 | “模型响应超时，已返回结构化摘要；可稍后重试。” |
| `LLM_RATE_LIMITED` | 429 且重试后仍失败 | 200 模板 | “免费模型额度/频率受限，请稍后重试。” |
| `LLM_AUTH_FAILED` | 401/403 | 503 | “模型服务鉴权失败，请联系管理员。” |
| `LLM_BAD_RESPONSE` | 非法 JSON/校验失败 | 200 模板 | “模型返回格式异常，已回退结构化摘要。” |
| `LLM_CONTENT_FILTER` | 内容过滤 | 200 模板 | “该问题被模型安全策略拦截，已返回结构化摘要。” |
| `LLM_UNAVAILABLE` | 5xx/连接失败 | 200/503 | “智能分析服务暂时不可用；你仍可使用固定问题查看数据。” |
| `CONTEXT_OVERFLOW` | 压缩后仍超预算 | 200 | “对话上下文过长，已压缩；请缩小时间范围或新开问题。” |
| `TOOL_PARTIAL` | 部分工具失败 | 200 | “部分数据暂不可用，以下结论基于可用数据。” |
| `TOOL_UNAVAILABLE` | 全部工具失败 | 503 | “数据服务暂不可用，请稍后重试。” |

- **原则**：能给出正确结构化答案就 200 + `status=degraded`；完全无法给出安全答案才 503 `LLM_UNAVAILABLE`/`TOOL_UNAVAILABLE`。
- 所有错误体仍按 RFC 7807；`degrade_reason` 是响应字段，不替代 `ErrorCode`。
- `retryable`：限流/超时 true；鉴权/配置 false。

---

## 7. 数据模型与迁移

### 7.1 迁移 `0020_chat_assistant`

全部落 `platform` schema，启用 RLS（`user_id` 策略，和现有租户表一致）。金额字段不存在，token/延迟用 `Integer/BigInteger`。

| 表 | 关键列 | 说明 |
|---|---|---|
| `chat_conversation` | `id`, `user_id`(unique), `title`, `summary`, `summary_tokens`, `summary_message_id`, `summary_version`, `focus` jsonb, `window_turns`, `turn_count`, `created_at`, `updated_at`, `deleted_at` | 每用户一条；软删也保留行，确保不会出现第二条 |
| `chat_message` | `id`, `conversation_id`, `user_id`, `turn_id`, `role`, `content`, `content_json`, `token_count`, `status`, `created_at`, `deleted_at` | user/assistant 消息 |
| `chat_turn` | `id`, `conversation_id`, `user_id`, `user_message_id`, `assistant_message_id`, `question`, `resolved_query`, `understanding` jsonb, `context_manifest` jsonb, `status`, `degrade_reason`, `llm_used`, `fallback`, `latency_ms`, `created_at` | 一轮一条 |
| `chat_tool_call` | `id`, `turn_id`, `user_id`, `seq`, `tool_name`, `args` jsonb, `result` jsonb, `status`, `error_code`, `row_count`, `result_bytes`, `latency_ms`, `created_at` | 工具调用全量 |
| `chat_llm_call` | `id`, `turn_id`, `user_id`, `seq`, `purpose`, `provider`, `model`, `prompt_version`, `request` jsonb, `response` jsonb, `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms`, `status`, `error_code`, `created_at` | LLM 调用全量 |

索引：

- `chat_conversation(user_id)` 唯一（不按 `deleted_at` 放开；单会话是硬约束）。
- `chat_message(conversation_id, created_at)`、`chat_message(turn_id)`、`chat_message(user_id, created_at)`。
- `chat_turn(conversation_id, created_at)`、`chat_turn(user_id, created_at)`。
- `chat_tool_call(turn_id, seq)`、`chat_tool_call(user_id, created_at)`。
- `chat_llm_call(turn_id, seq)`、`chat_llm_call(user_id, created_at)`、`chat_llm_call(purpose, created_at)`。

存储上限：

- 单工具结果 ≤256KB；单轮工具结果总量 ≤1MB；`chat_llm_call.request/response` ≤1MB。
- 超限截断并 `truncated=true`，同时记录 `original_bytes`；回放时明确“当时已截断”。

### 7.2 保留与清理

- 默认保留 180 天；`CHAT_RETENTION_DAYS` 可配。
- 用户 `DELETE /chat/conversation` 软删消息；审计/回放数据保留策略后续决定。
- 物理清理任务 P2 后置：按 `created_at` 分批删除，避免长事务。

---

## 8. API 设计

### 8.1 端点

| API | 方法/路径 | 说明 |
|---|---|---|
| API-077 | `POST /chat/sessions` | 确保单会话存在；首次 201，已存在 200；返回 `conversation_id` |
| API-078 | `POST /chat/sessions/{session_id}/messages` | 发消息；200；幂等键；`session_id` 即唯一 `conversation_id` |
| API-112 | `GET /chat/conversation` | 单会话元数据 + 分页消息 + `context_usage` |
| API-113 | `GET /chat/turns/{turn_id}/trace` | 完整回放轨迹：understanding/tool_calls/llm_calls/answer |
| API-114 | `POST /chat/turns/{turn_id}/replay` | 回放：`deterministic|llm|full`；返回 diff |
| API-115 | `DELETE /chat/conversation` | 清空对话（软删消息，幂等） |

> 编号从 API-112 起；API-089 仍预留。实现时需同步 API-Catalog、OpenAPI、SDK、隔离矩阵、验收用例。
> **契约变更**：API-077 从“新建会话”调整为“确保单会话存在”（首次 201，已存在 200）；API-078 路径参数仍叫 `session_id`，值即唯一 `conversation_id`。这是未实现前的语义调整，落地时按契约先行更新 Catalog/快照/SDK。

### 8.2 API-078 请求/响应

请求：

```json
{
  "message": "这个月为什么亏？",
  "context": {"period": "2026-11", "round_id": null}
}
```

响应 200：

```json
{
  "conversation_id": "cnv_...",
  "turn_id": "trn_...",
  "message_id": "msg_...",
  "answer": "本月……[[1]]",
  "answer_format": "markdown",
  "citations": [
    {"number": 1, "citation_id": "c1", "label": "本月收益", "value": "-1234.56",
     "source_endpoint": "GET /api/v1/dashboard", "definition_version": "1.0",
     "data_as_of": "2026-11-18"}
  ],
  "claims": [{"claim_id": "k1", "text": "...", "citation_ids": ["c1"]}],
  "status": "ok|degraded|needs_clarification|blocked",
  "degrade_reason": null,
  "degrade_message": null,
  "what_to_do": null,
  "retryable": false,
  "llm_used": true,
  "fallback": false,
  "understanding": {"intents": ["attribution"], "resolved_query": "...", "entities": []},
  "context_usage": {
    "window_turns": 10, "included_turns": 8, "estimated_input_tokens": 12345,
    "actual_prompt_tokens": 12000, "compressed": false, "summary_version": 3
  },
  "timings": {"understand_ms": 800, "tools_ms": 250, "llm_ms": 3200, "total_ms": 4300}
}
```

错误：

- 404 `NOT_FOUND`：会话不属于当前用户/不存在。
- 422 `MESSAGE_TOO_LONG`：>2000 字。
- 422 `VALIDATION_ERROR`：非法 context。
- 429 `RATE_LIMITED` / `CHAT_DAILY_LIMIT`。
- 503 `LLM_UNAVAILABLE`：无可安全答案。
- 409 `IDEMPOTENCY_IN_PROGRESS` / 422 `IDEMPOTENCY_KEY_REUSED`：与现有幂等一致。

### 8.3 幂等

- `POST /chat/sessions`：同一用户重复调用返回同一 `conversation_id`，不重复建。
- `POST messages`：`Idempotency-Key` 必填/推荐；同 key 同 body 返回同一 `turn_id`，不重复调用模型/工具；同 key 不同 body → 422。
- 处理中重复 → 409 `IDEMPOTENCY_IN_PROGRESS`。

### 8.4 限流

- 每用户：1 个在途对话请求；每分钟 `CHAT_RPM`（默认 3）；每日 `CHAT_DAILY_TURNS`（默认 50）。
- 全局：`LLM_MAX_CONCURRENCY=2`/副本；免费额度 80% 告警；429 时按退避重试，仍失败则降级。
- 429 返回 `Retry-After` + RFC 7807。

---

## 9. 回放设计

### 9.1 存什么

- `chat_turn`：问题、`resolved_query`、understanding、context manifest、状态。
- `chat_tool_call`：序号、工具名、参数、完整结果、状态、耗时、行数。`args` 必须存**已解析的具体值**（`period_start/end`、`security_code`、`round_id`、`limit`），不存“上个月/它”这类相对表达；原始问题与 `resolved_query` 存在 `chat_turn`，否则回放会受当前时间/焦点影响。
- `chat_llm_call`：序号、purpose、provider/model、prompt_version、完整 request/response、token usage、耗时、状态。
- `chat_message`：最终回答、claims、citations、状态。

### 9.2 查轨迹

`GET /chat/turns/{turn_id}/trace` 返回：

```json
{
  "turn": {...},
  "context": {"summary": "...", "included_turn_ids": [...], "estimated_input_tokens": 12345},
  "understanding": {...},
  "tool_calls": [{"seq": 1, "tool": "attribution_summary", "args": {...}, "result": {...}, "status": "ok", "latency_ms": 120}],
  "llm_calls": [{"seq": 1, "purpose": "understanding", "request": {...}, "response": {...}, "usage": {...}}],
  "answer": {...}
}
```

### 9.3 重放

`POST /chat/turns/{turn_id}/replay`：

| mode | 行为 | 用途 |
|---|---|---|
| `deterministic` | 用存储的 understanding + 工具参数重跑工具，重新组装模板答案；不调 LLM | 验证数据/工具/组装是否漂移 |
| `llm` | 用存储的 answer request 重新调用 GLM，比较回答 | 验证模型输出稳定性/回归 |
| `full` | 用**存储的** understanding request 重跑理解，再跑 tools + 存储的 answer request | 端到端复现（最贵；不按当前上下文重新理解） |

响应：

```json
{
  "mode": "deterministic",
  "match": false,
  "diffs": [
    {"kind": "answer_text", "expected": "...", "actual": "..."},
    {"kind": "citation_value", "citation_id": "c1", "expected": "-1234.56", "actual": "-1200.00"}
  ],
  "replayed": {"understanding": {...}, "tool_calls": [...], "answer": {...}}
}
```

- 不覆盖原记录；`persist=false` 默认。需要留档时再写入新的 `chat_turn`（可选）。
- `llm`/`full` 受 rate limit/预算约束；回放失败返回 `replay_status=failed` + 原因。
- `full` 不重新做指代/摘要，否则输入会变；需要“按当前上下文重新理解”属于新问题，不是回放。
- 回放必须校验 `turn.user_id`；跨用户 404。

---

## 10. 安全、权限与数据

- **租户隔离**：所有查询显式 `user_id`；RLS；跨用户 404；admin 不可读聊天明细（与业务明细一致）。
- **数据使用**：用户已确认账单敏感处理、可全量使用业务数据；仍**不发送** JWT/PAT/API key、邮箱、账号/股东代码、其他用户数据。
- **提示注入**：工具结果中的备注/摘要/证券名不可作为指令；用分隔符包裹并标“数据”；模型没有工具调用权；用户输入只作为 user message。
- **输出安全**：Markdown 白名单；禁止 HTML/图片/外链；前端不自动加载模型生成的 URL。
- **日志**：请求日志不打印完整 prompt/answer；审计看 `chat_llm_call` 的 token/耗时/状态；敏感 key 不入库。
- **免费模型**：GLM-4.7-Flash 免费，但仍需在隐私说明中披露“对话内容会发送给模型服务”；用户可删除对话。
- **回放数据**：包含完整业务数据，仅 owner 可读；`trace` 不做 admin 视图。

---

## 11. 测试与验收

### 11.1 单元

- ContextManager：10 轮窗口、160K 触发、工具瘦身、摘要、硬截断、当前轮不压缩。
- Token 估算：CJK/ASCII/JSON；与 usage 误差记录。
- QueryUnderstanding：JSON schema、多意图、相对日期、指代唯一/歧义、低置信度澄清。
- OutputValidator：未知数字、无 citation、禁建议词、HTML、超长。
- ToolGateway：参数 schema、user_id 注入、行数上限、超时、部分失败。

### 11.2 API

- API-077/078/112/113/114/115：happy / 401 / 403 admin / 跨用户 404 / 422 / 429 / 幂等。
- 单会话：重复 `POST /sessions` 返回同一 id；不能创建第二条。
- LLM 关闭：固定问题仍 200，`fallback=true`，金额正确。
- LLM 故障注入：超时/429/401/5xx/非法 JSON → `degrade_reason` + 文案；需要时 503。
- 工具部分失败：`status=degraded` + `missing[]`；不编数。
- 回放：deterministic 一致；llm 模式可跑；跨用户 404。
- 数据：`assets_query` 行数与 `GET /assets` 一致；`trades_query` 旗标与 `load_trade_flags` 一致；`rounds_query`/`round_detail` 旗标与 insights 一致。

### 11.3 Golden 集（40 条起）

覆盖：总览、收益/风险、归因、回合、回合详情、流水/旗标、资产表、费用、行为、板块、数据质量、指代、多意图、LLM 关闭、注入、压缩。

硬指标：

- 每个数字 citation 覆盖 100%；未知数字 0。
- `LLM_PROVIDER=none` 固定问题通过率 100%。
- 10 轮上下文 + 超 160K 压缩后仍能回答，citation 不丢。
- 回放 deterministic 答案/引用一致率 100%（同一数据版本）。
- 跨用户 0 泄漏；admin 403。

### 11.4 性能

- 工具层 p95 ≤1.5s（10 工具并发/串行受控）。
- 无 LLM 模板路径 p95 ≤2s。
- GLM 路径 p95 ≤10s（免费额度/网络波动单独记录）；超时 30s 降级。
- 单轮 token：目标 ≤20K（10 轮 + 工具），压缩触发可观测。

---

## 12. 后端实施计划

> 前端 P3；以下为后端 P1/P2。原 DEV-PLAN 的 3 人日只覆盖最窄路径，本设计后端约 **12.5 人日**（不含前端），前端 P3 约 3 人日，合计约 15.5 人日。

| ID | 任务 | 产出 | 估时 | 依赖 |
|---|---|---|---|---|
| Chat-00 | 依赖锁定 + 目录骨架 | `uv add` LangChain/LangGraph/tiktoken/json-repair；`stock_delivery/chat/`、`stock_review/application/chat/`、`stock_api/routers/chat.py` 空骨架；config 字段 | 0.5d | — |
| Chat-01 | 迁移 0020 + 模型/仓储/RLS | 5 表、索引、策略、回滚 | 1.5d | — |
| Chat-02 | GLM provider + 配置 | `ChatOpenAI` 适配、错误映射、usage、超时/429 | 0.5d | Chat-00/01 |
| Chat-03 | `ChatQueryFacade` + 10 工具 | 资产/流水/回合/详情/概览/指标/归因/净值/板块/质量 | 2.0d | Chat-01 |
| Chat-04 | 旗标补齐 | `trades_query.flags/flag_glossary`；回合/详情旗标 | 0.5d | Chat-03 |
| Chat-05 | ContextManager + 压缩 | 10 轮、160K、工具瘦身、滚动摘要、硬截断 | 1.5d | Chat-01/02 |
| Chat-06 | QueryUnderstanding + 指代 | JSON schema、规则兜底、focus、澄清 | 1.5d | Chat-05 |
| Chat-07 | 编排 + 回答 + 校验 | Composer、AnswerLLM、Validator、降级 | 1.5d | Chat-03/06 |
| Chat-08 | API-077/078/112~115 + 回放 | router/schema/openapi/幂等/限流 | 1.5d | Chat-07 |
| Chat-09 | 测试/观测/文档 | golden、故障注入、trace、README/API-Catalog | 1.5d | 全部 |
| **合计** | 后端可用 V1 | | **约 12.5d** | |
| Chat-F1 | 前端 `/assistant` | 对话、引用抽屉、状态、固定问题 | 3d（P3） | Chat-08 |

### 12.1 里程碑

- **M1（Chat-00~04，约 5d）**：工具能取到资产/流水/回合/旗标，模板可答。
- **M2（Chat-05~07，约 4.5d）**：上下文/语义/回答/降级可用。
- **M3（Chat-08~09，约 3d）**：API/回放/测试达标，后端验收。
- **M4（P3）**：前端。

### 12.2 验收命令（建议）

```bash
make test-chat          # 单测 + API + 回放 + 故障注入
make test-isolation     # 单会话/跨用户 404
make api-check
make api-coverage
```

### 12.3 前端 P3（仅约束，不在本期）

- 路由 `/assistant`，导航放「核心」组；单会话，不显示会话列表/切换。
- 首屏：欢迎语 + 固定问题 chips（本月赚多少/为什么亏/胜率/费用/最赚最亏/资产表/数据可信吗）。
- 消息流：用户/助手气泡；助手回答带 `status` 徽标（ok/degraded/blocked/needs_clarification）、`degrade_reason` 文案、citations 角标；点击角标打开来源抽屉。
- 顶部显示 `context_usage`（已用轮数/是否压缩）和「清空对话」；`DELETE /chat/conversation` 二次确认。
- 输入：2000 字上限、发送中禁用、429/503 按 RFC 7807 展示 `user_message/what_to_do`。
- 零口径：只渲染 `answer`/`citations[].value`/`display`，不做算术、不拼结论。
- 回放/轨迹入口 P3 可放在“调试/详情”抽屉，仅本人可见；默认不暴露给普通用户。
- 流式 SSE 不在 P3 首批；先同步 200，后续再加。


---

## 13. 风险与待确认

| 风险 | 影响 | 应对 |
|---|---|---|
| GLM 免费额度/限流未知 | 429、延迟 | 1 次重试 + 降级文案；可观测 429 比例；必要时换 FlashX/付费 |
| 200K 上下文成本/延迟 | 慢 | 80% 触发压缩、工具上限、10 轮窗口、thinking disabled |
| 小模型 JSON 不稳定 | 理解/回答失败 | Pydantic 校验 + 修复一次 + 规则兜底 + 模板 |
| 压缩丢引用 | 回答不可追溯 | 摘要保留 citation id；当前轮不压缩；golden 断言 |
| 工具结果过大 | 超上下文 | 行数上限、字段裁剪、20K 单工具上限 |
| 指代歧义 | 答错对象 | 多候选项澄清，不猜 |
| 回放与线上模型漂移 | 对比失败 | 存 `prompt_version`/`model`；区分 deterministic/llm 模式 |
| 单会话无限增长 | 存储/性能 | 180 天保留、软删、摘要、分页 |

**待确认（实现前）**：

1. GLM API Key / 免费额度是否够本地验收；是否需要代理 base URL。
2. `LLM_THINKING` 默认关闭是否满足质量；若回答质量不足，再对复杂意图开启。
3. 现有 `/trades`、`/rounds` 公共响应是否也要加旗标（本设计只改助手工具，不动公共 API）。
4. 回放 `llm`/`full` 是否允许普通用户调用，还是仅本地/管理员（建议先仅本地验收开放）。
5. 单轮工具证据上限（20K tokens）是否够用；不够再调。

---

## 14. 参考

- [PRD-v2 FR-D7](../prd/PRD-v2.md)
- [DEV-PLAN Period3-07](../DEV-PLAN-v2.md)
- [ADR-0022](v2/ADR.md)
- [API-Catalog API-077/078](v2/API-Catalog.md)
- [智谱 GLM-4.7-Flash 发布页](https://www.zhipuai.cn/zh/research/148)
- [GLM-4.7-Flash 文档（200K / 免费）](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash)
- [GLM-4.7 模型页（200K / 128K 输出）](https://docs.z.ai/guides/llm/glm-4.7)
- [Intrinio: Financial Data for AI Agents](https://intrinio.com/blog/financial-data-for-ai-agents-architecture-grounding-and-guardrails)
