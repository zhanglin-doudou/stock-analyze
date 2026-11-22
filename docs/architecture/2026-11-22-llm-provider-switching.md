# 模型供应商/模型动态切换（2026-11-22）

> 状态：**已实现（2026-11-22）**：`config/llm_providers.json` + `stock_platform.llm_registry`、
> 迁移 `0024_user_llm_prefs`、偏好组 `llm`（API-055/096 扩展）、`OpenAiCompatProvider` +
> `LlmProviderFactory` 按请求路由、设置页「助手模型」卡片；测试与门禁已补齐。变更口径须更新本文。

## 1. 背景与目标

智能分析助手（2026-11-20）当前把 LLM 供应商写死为智谱 GLM：`Settings.glm_*` + `GlmProvider`
在进程启动时构建一次，换模型要改 `.env` 并重启 API。本期目标：

1. **后端 JSON 文件配置**可用的模型供应商、对应模型、以及各供应商 API key 的**环境变量名**；
2. **按用户偏好动态切换**供应商/模型（存 `platform.user_setting`，走既有 PATCH /settings 偏好体系），
   聊天按请求解析所选模型，**无需重启**；
3. **前端设置页**选择供应商与模型。

首发目录（`config/llm_providers.json`）：

| 供应商 | id | base URL | key 环境变量 | 模型 |
|---|---|---|---|---|
| 智谱 | `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `GLM_API_KEY`（沿用） | `glm-4.7-flash`、`glm-4-flash` |
| 小米 | `xiaomi` | `https://token-plan-cn.xiaomimimo.com/v1` | `MIMO_API_KEY` | `mimo-v2.6-flash`、`mimo-v2.6-pro` |

## 2. JSON 目录（部署级配置）

路径：仓库根 `config/llm_providers.json`，可用 `LLM_PROVIDERS_FILE` 覆盖（同
`SECTOR_FIXTURES_FILE` 先例）。结构：

```json
{
  "default_provider": "zhipu",
  "default_model": "glm-4.7-flash",
  "providers": [
    {
      "id": "zhipu",
      "name": "智谱",
      "base_url": "https://open.bigmodel.cn/api/paas/v4",
      "api_key_env": "GLM_API_KEY",
      "supports_thinking": true,
      "models": ["glm-4.7-flash", "glm-4-flash"]
    },
    {
      "id": "xiaomi",
      "name": "小米",
      "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
      "api_key_env": "MIMO_API_KEY",
      "models": ["mimo-v2.6-flash", "mimo-v2.6-pro"]
    }
  ]
}
```

- `api_key_env` 是**环境变量名**，不是 key 本身；key 只从环境注入（`os.environ` 优先，
  本地开发回落读 `.env`，与 pydantic-settings 同源），**永不进日志/响应/入库**。
- `supports_thinking=true` 时才随请求下发 `thinking={"type":"disabled"}`（GLM 系参数，
  MiMo 不发）。`response_format=json_object`（json_mode）为 OpenAI-compatible 通用行为，照发。
- 加载校验 fail-fast：id/base_url/api_key_env/models 非空、id 唯一、`default_*` 必须指向
  目录内模型、`api_key_env` 匹配 `^[A-Z][A-Z0-9_]*$`；非法即启动失败。
- 修改 JSON 后**需重启 API**（与 `.env` 同规：进程启动时加载，不做热更新）。

加载器：`stock_platform.llm_registry`（`LlmProviderSpec` / `LlmTarget` / `LlmRegistry` /
`load_llm_registry(settings)`）。`LlmRegistry.catalog()` 产出前端快照（id/name/available/models
+ default_*），**不含** base_url 与 api_key_env。

## 3. Settings 语义调整（`stock_platform.config`）

| 字段 | 变化 |
|---|---|
| `LLM_PROVIDER` | 改为 `none`（关闭）/ `local`（Fake，测试离线）/ `auto`（默认，目录 default_*）/ 目录内 provider id；**旧值 `glm` 兼容别名 → `auto`** |
| `GLM_API_KEY` | 不再是 Settings 字段；改由注册表按目录 `api_key_env` 解析（env 优先、`.env` 回落） |
| `GLM_MODEL` / `GLM_BASE_URL` | **移除**（模型与 base URL 唯一来源是 JSON 目录；默认模型 = `default_model`） |
| `LLM_PROVIDERS_FILE` | 新增：目录文件覆盖路径 |
| `llm_enabled` | = `llm_provider not in ("none","local")`（是否允许出站 LLM；key 是否齐备由解析层决定） |

`llm_enabled=True` 但所选供应商缺 key 时，调用侧抛 `LLM_NOT_CONFIGURED` → 走既有降级
（模板答案 + `degrade_reason`），不静默换供应商、不 500。

## 4. 用户偏好（`llm` 组，API-055/096 扩展）

- 存储：`platform.user_setting.llm_prefs` JSONB（迁移 `0024_user_llm_prefs`），
  键 `provider` / `model`；**空 = 跟随部署默认**（RFC 7396 `null` 删键回到默认）。
- PATCH /settings 新增组 `llm`：`provider` 与 `model` **必须同时提交**（或整组 `null` 重置）；
  取值必须命中目录：provider 存在、model 属于该 provider，否则 422 `VALIDATION_ERROR`
  （目录成员校验在 API 层对着注册表做，core 只做形状/交叉校验——与概念 code 的
  「core 形状 / API 外部引用」分工一致）。
- GET /settings 的 `llm` 组回显：`{provider, model, enabled, default_provider, default_model,
  providers: [{id, name, available, models}]}`；`provider/model` 为用户原始选择（空 = 默认）。

## 5. 聊天按请求动态路由

- `GlmProvider` 泛化为 `OpenAiCompatProvider`（`provider`/`model`/`base_url`/`api_key` 显式注入；
  `provider.model` 取代 `settings.glm_model` 进日志与 `chat_llm_call`）。
- `LlmProviderFactory`：按 `(provider_id, model)` 缓存构建；`resolve(selection)` 解析用户选择，
  无效/为空回落默认目标。
- `ChatRuntime` 新增 `llm_enabled` 显式参数（缺省 `settings.llm_enabled`）；`AppState.llm` 保留
  为默认 provider（测试仍可注入 `FakeProvider`），`_make_runtime`/replay 按用户 `llm_prefs`
  解析后再装配。选择即生效：设置页改完，下一条消息即走新模型。

## 6. 契约与测试

- API-055/096 契约更新（§6 验收矩阵补 llm 组用例）；无新端点。
- `make api-snapshot` / `make api-check` / `make sdk`；核心单测：
  注册表加载/校验/可用性、偏好 llm 组合并与 422、chat 动态装配、设置页渲染。
- CI 仍一律 `FakeProvider`，不调真实模型。

## 7. 兼容性

- `.env`：`LLM_PROVIDER=glm` 仍可用（别名）；`GLM_MODEL`/`GLM_BASE_URL` 变为无效键
  （Settings `extra="ignore"`，静默忽略）；默认模型从 `.env` 的 `GLM_MODEL` 改为目录
  `default_model=glm-4.7-flash`（要沿用 `glm-4-flash` 请在设置页选择或改目录）。
- 小米 key 未注入（`MIMO_API_KEY` 缺失）时该供应商 `available=false`，前端不可选。
