# AGENTS.md — frontend（Vite + React + TS）

**前置**：必读根目录 `AGENTS.md`。本文件只补前端层的特有约定。

> 当前阶段（2026-10-28）：前端重构按 [调研与选型 README](../docs/reviews/2026-10-28-frontend-refactor/README.md) §13 分阶段进行；已拍板 A+ 混合方案、A 股红涨绿跌、TanStack Query（Phase 1 引入）、后端 chart-ready 视图 + geometry 白名单（Phase 1/2）、WCAG 2.2 AA。Phase 0 已落地设计 tokens（`apps/web/src/styles/tokens.css`）与主题偏好（`apps/web/src/theme/theme.ts`）。

## 定位

前端分三层：

| 层 | 路径 | 职责 |
|---|---|---|
| API client 传输层 | `packages/api-client/src/http.ts` | 手写 fetch：Bearer 注入、RFC 7807 解析、401 刷新重放、SSE 流 |
| API client 类型层 | `packages/api-client/src/api/generated/schema.d.ts` | **自动生成**（openapi-typescript），勿手改 |
| Web 应用 | `apps/web/` | 页面 / 路由守卫 / 数据请求（Phase 1 起 TanStack Query）/ 设计系统（tokens + 组件） |

## 规则速查

### 1. 金额 / 数量 / 价格一律 `string`，禁止 `number` 参与算术

```typescript
// DO — 后端已返回定点字符串，前端只展示、排序
interface Trade {
  amount: string;    // "150000.00" 定点字符串
  price: string;     // "1500.000000"
  quantity: string;  // "100.0000"
}

// DON'T — 把 string 转 number 做算术（浮点误差会在累计时爆）
const total = trades.reduce((s, t) => s + Number(t.amount), 0);   // 绝对禁止

// 一期前端不做计算——展示层只 sort / filter；二期收益计算走后端 metrics API
```
**零口径 ESLint 规则**：Phase 4 启用（重构路线图 D6）；在此之前由 `?raw` 源码断言兜底，Phase 1 起把扫描范围扩到 `src/**`。任何业务页面禁止 `parseFloat` / `parseInt` / `Number(` / `toFixed`。

> 图表几何是唯一例外：只允许 `apps/web/src/charts/geometry.ts` 做 domain→pixel 投影（Phase 1 落地），业务页面仍不得解析/计算业务数值；后端 `views/*`（Phase 2）是主路径。

### 2. `packages/api-client/src/api/generated/schema.d.ts` 自动生成，勿手改

```bash
make api-snapshot   # 后端先更新 contracts/openapi.json
make sdk            # openapi-typescript 从快照生成 schema.d.ts
```
**禁止**直接编辑 `generated/schema.d.ts`——下次 `make sdk` 会被覆盖。接口变更必须从后端契约出发（API-Catalog → openapi.json → sdk）。

### 3. HTTP 错误统一走 `ApiProblemError`（RFC 7807 形状）

```typescript
import { ApiProblemError, isApiProblemError } from "@sa/api-client";

try {
  await api.imports.commit(body, idempotencyKey);
} catch (err) {
  if (isApiProblemError(err)) {
    const problem = err.problem;
    // problem.code / problem.user_message / problem.what_to_do / problem.retryable / problem.request_id
    showError(problem.user_message, problem.what_to_do);   // 直接展示后端的人话
  }
}
```
**禁止**在 catch 里手写 `err.response.data.message`——后端已按 RFC 7807 格式化。

### 4. 401 自动刷新一次并重放（api-client 已内置）

```typescript
// api-client 构造时传 getToken / onTokens / onUnauthorized
const client = createClient({
  baseUrl: "/api/v1",
  getToken: () => ({ access: localStorage.getItem("access"), refresh: localStorage.getItem("refresh") }),
  onTokens: (t) => { /* 更新本地存储 */ },
  onUnauthorized: () => { /* 跳登录 */ },
});
```
刷新失败（无 refresh_token 或 refresh 401）→ 触发 `onUnauthorized` → 上层跳登录页。

### 5. SSE 进度流：用 `fetch` 流，不用 `EventSource`

```typescript
// EventSource 不支持 Bearer 头，所以 api-client 用 fetch ReadableStream 实现
async function* events(jobId, { lastEventId } = {}) {
  const headers = new Headers({ Accept: "text/event-stream" });
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  // 读取流、解析 event/data、yield { type, payload, id }
}
```
断线续传：`Last-Event-ID` → 后端从指定 seq 后补发。SSE 是优化，前端仍需兜底轮询 `GET /jobs/{id}`。

### 6. TanStack Query + 路由守卫

> **状态**：Phase 1 起引入 TanStack Query；当前页面仍是手写 `useState/useEffect`，迁移期允许新旧共存，不得在未迁移页面强制引入第二套数据层。

```typescript
// 路由守卫：无 token → 跳 /login；有 token 但 role 不匹配 → 403 页
// Query 键按资源类型前缀：queryKey: ["trades", filters]

// DO — mutation 后 invalidate
const commit = useMutation({
  mutationFn: (body) => api.imports.commit(body, key),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["imports"] }),
});
```

### 7. `/api` 代理到 8000（Vite dev 配置）

```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8000" } },
  },
});
```
生产构建产物同源部署（nginx 反代）。

### 8. 设计系统 / 主题 / 图表零口径（Phase 0 起）

- **唯一视觉变量来源**：`apps/web/src/styles/tokens.css`。组件只消费语义 token（`--surface/--line/--accent/--up/--down/...`），禁止新增私有 hex；新增 token 需同步本文件与评审文档。
- **主题**：`<html data-theme="light|dark" data-density="cozy|compact" data-trend="red-up|green-up">`；默认 `light + cozy + red-up`（A 股红涨绿跌），深色 Phase 5。
- **数字排版**：金额/比率列右对齐 + `font-variant-numeric: tabular-nums`；格式（千分位/小数位/符号）一律来自后端字符串，前端不补零/不四舍五入。
- **状态语义**：`build_state` 与 `data_status` 两轴共用一组 status token；禁止只用颜色表达状态（WCAG 2.2 1.4.1），必须带文案与形状。
- **图表**：Phase 1 先自研 SVG，几何投影收敛到 `src/charts/geometry.ts`（唯一允许 `Number()` 的模块，配 golden `path d` 测试）；Phase 2 起后端 `GET /views/*` 返回归一化序列为主路径。

## 文件清单

| 路径 | 职责 |
|---|---|
| `packages/api-client/src/http.ts` | 手写传输层：fetch + Bearer + 7807 + 401 刷新 + SSE |
| `packages/api-client/src/api/generated/schema.d.ts` | openapi-typescript 自动生成的 TS 类型（勿手改） |
| `apps/web/src/pages/` | 页面（Import.tsx 上传预览、Trades.tsx 流水、Settings.tsx PAT） |
| `apps/web/src/layout/` | Phase 1b AppShell（76/64px 图标栏、面包屑、5 组导航）、navigation 配置、页面标题映射 |
| `apps/web/src/ui/icons.tsx` | Phase 1b 自研线性图标集（不引图标库） |
| `apps/web/src/ui/` | 通用组件（Cursor 游标、Problem 错误态、DataTable 表格；旧 Table.tsx 已移除） |
| `apps/web/src/theme/theme.test.ts` | 偏好回落/持久化/根属性测试 |
| `apps/web/src/data/queryClient.ts` | Phase 1 TanStack Query Client 与 queryKeys 约定 |
| `apps/web/src/charts/geometry.ts` | Phase 1 图表像素投影（零口径唯一白名单） |
| `apps/web/src/ui/status.ts` | Phase 1 两轴状态语义（文案/色调/优先级） |
| `apps/web/src/ui/format.ts` | Phase 1 符号/空值展示辅助（只读后端字符串） |
| `apps/web/src/charts/TrendChart.tsx` | Phase 1c 总资产趋势图（自研 SVG，数据来自 /assets） |
| `apps/web/src/pages/dashboard.css` | Phase 1c Dashboard 样式（tokens-only） |
| `apps/web/src/pages/settings.css` | Phase 3a 设置页样式（外观偏好 + PAT，tokens-only） |
| `apps/web/src/pages/detail.css` | Phase 3b 详情/导入页共享样式（tokens-only） |
| `apps/web/src/lib/date.ts` | Phase 3a 本地月份工具（修正 UTC 月份 bug） |
| `apps/web/src/ui/DataTable.tsx` | Phase 2 统一数据表（粘性表头/列优先级/键盘行/空加载态） |
| `apps/web/src/charts/zero-caliber.test.ts` | 全 src 零口径门禁（geometry 白名单除外） |
| `apps/web/src/styles/tokens.css` | Phase 0 设计 tokens（颜色/字体/间距/圆角/状态/图表/密度），组件只消费语义变量 |
| `apps/web/src/theme/theme.ts` | Phase 0 主题/密度/趋势色偏好读写（`data-theme`/`data-density`/`data-trend`） |

## 门禁命令

```bash
cd frontend && pnpm -r lint       # 当前 = tsc；Phase 4 起接入 eslint
cd frontend && pnpm -r typecheck   # tsc --noEmit 全量（含 api-client + web + api）
cd frontend && pnpm -r test        # vitest
cd frontend && pnpm -r build       # tsc + vite build
make sdk                           # 从后端快照同步 TS 类型（后端改接口后必先跑）
```
