# AGENTS.md — frontend（Vite + React + TS）

**前置**：必读根目录 `AGENTS.md`。本文件只补前端层的特有约定。

## 定位

前端分三层：

| 层 | 路径 | 职责 |
|---|---|---|
| API client 传输层 | `packages/api-client/src/http.ts` | 手写 fetch：Bearer 注入、RFC 7807 解析、401 刷新重放、SSE 流 |
| API client 类型层 | `packages/api-client/src/api/generated/schema.d.ts` | **自动生成**（openapi-typescript），勿手改 |
| Web 应用 | `apps/web/` | 页面 / 路由守卫 / TanStack Query / 组件库 |

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
**零口径 ESLint 规则**（二期 P2-15 启用）：自定义规则禁止在业务页面里 `parseFloat` / `parseInt` / `Number(` / `toFixed`。一期先遵守此约定，避免临时代码积累后再清理。

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

## 文件清单

| 路径 | 职责 |
|---|---|
| `packages/api-client/src/http.ts` | 手写传输层：fetch + Bearer + 7807 + 401 刷新 + SSE |
| `packages/api-client/src/api/generated/schema.d.ts` | openapi-typescript 自动生成的 TS 类型（勿手改） |
| `apps/web/src/pages/` | 页面（Import.tsx 上传预览、TradeList.tsx 流水、Settings.tsx PAT） |
| `apps/web/src/ui/` | 通用组件（Cursor.tsx 游标、Problem.tsx 错误态、Table.tsx 表格） |

## 门禁命令

```bash
cd frontend && pnpm -r lint       # eslint
cd frontend && pnpm -r typecheck   # tsc --noEmit 全量（含 api-client + web + api）
cd frontend && pnpm -r test        # vitest
make sdk                           # 从后端快照同步 TS 类型（后端改接口后必先跑）
```
