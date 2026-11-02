# AGENTS.md — stock_api（FastAPI 应用层）

**前置**：必读根目录 `AGENTS.md`。本文件只补 api 层的特有约定。

## 定位

stock_api 是 FastAPI 应用层：路由 / Pydantic schema / 依赖注入 / 中间件。职责是 **HTTP 协议适配**——把 HTTP 请求 → application 层调用 → HTTP 响应。业务逻辑放 ledger application 层；DB / 队列 / 幂等放 platform。

## 规则速查

### 1. 所有 router 用 StrictQueryRoute（未登记 query 参数 → 400）

```python
# 全仓 14 个业务 router 一致（ops.py 内部两个 APIRouter 也均用 StrictQueryRoute）
router = APIRouter(tags=["trades"], route_class=StrictQueryRoute)

# 内部实现：收集所有 Query() 声明的参数名 / alias → handler 里校验未登记的
# 未知参数 → ApiError(ErrorCode.UNKNOWN_QUERY_PARAM, detail="...")
```
**为什么**："静默忽略未知参数"会让前端拼错 query key（比如 `limit` 写成 `limt`）时无任何报错，但数据不对。严格拒绝让 bug 立刻暴露。

### 2. operation_id 必填，与 API-Catalog 对齐

```python
@router.post("/imports", response_model=JobAcceptedOut, operation_id="commitImport")
async def commit_import(...) -> JobAcceptedOut:
    ...
```
**为什么**：`make api-check` 会扫 `openapi.json` 的 operationId 唯一性 + API-Catalog §1 端点表的 operation_id 匹配；不一致即失败。

### 3. 边界层定点字符串转换：`_xxx_out()` 函数

```python
# stock_api/routers/trades.py 的 _trade_out 模式（60+ 行）
def _trade_out(dto: TradeDTO) -> TradeOut:
    """ledger DTO（标度整数）→ API 边界模型（定点字符串）。"""
    return TradeOut(
        quantity=str(Qty(dto.quantity_units)),   # scale=4
        price=str(Price(dto.price_units)),       # scale=6
        amount=str(Money(dto.amount_units)),     # scale=2
        ...
    )
```
**禁止**在 application / repository 层做字符串转换——只有 API 边界层才把标度整数 → 定点字符串。

### 4. admin 角色禁止读写业务数据：`ctx.forbid_admin_trades()`

```python
@router.get("/trades", ...)
async def list_trades(ctx: Annotated[TenantContext, Depends(get_tenant)], ...) -> TradePage:
    user_id = ctx.require_user()
    ctx.forbid_admin_trades()   # ← admin 调这里 → 403 ADMIN_CANNOT_READ_TRADES
    ...
```
**例外**：ops router（健康检查/版本号/错误码目录）不调 forbid_admin_trades——运维监控端点 admin 可访问。

### 5. Session 打开方式：从 AppState 取

```python
@router.get("/trades/{trade_id}")
async def get_trade(request: Request, ctx: Annotated[TenantContext, Depends(get_tenant)], ...):
    state = get_state(request)   # 从 request.state 取 AppState（db + clock + config）
    async with state.db.session(user_id=ctx.require_user(), begin=True) as session:
        # RLS 已自动 SET LOCAL app.user_id
        ...
```
**禁止**在 router 里直接 `create_async_engine(...)`——引擎在 app startup 时已建好，挂在 `request.state.db` 上。

### 6. StrictModel：禁止额外字段

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

class ImportCommitIn(StrictModel):
    preview_id: str = Field(min_length=4, max_length=40)
    mode: Literal["full", "replace_range"] = "full"
```
**为什么**：extra="forbid" 让客户端拼错字段名 → 422 校验错误，而不是静默忽略。`populate_by_name=True` 允许 alias（比如 `from` 在 Python 里是关键字，alias="from" + field name "date_from"）。

### 7. 长任务返回 202 + Location

```python
@router.post("/imports", response_model=JobAcceptedOut)
async def commit_import(...) -> JobAcceptedOut:
    # 同步创建 PENDING 批次（让前端立即拿到 batch_id）
    # 投递 JobQueue（kind="import_commit"）
    # 返回 202 + Location: /api/v1/jobs/{job_id}
    return JobAcceptedOut(job_id=..., batch_id=..., links=JobLinks(self=..., events=...))
```
**禁止**同步阻塞——长任务必须走 JobQueue + 202 + Location。

### 8. 错误一律抛 `ApiError`（被 RFC 7807 中间件捕获）

```python
# deps.py 里的典型用法
async def get_tenant(authorization: str | None = Header(default=None)) -> TenantContext:
    if authorization is None:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, detail="缺少认证凭证")
    token = _extract_bearer(authorization)
    ...
```
**禁止**在 router 里手动 `JSONResponse(status_code=404, ...)`——ApiError 走中间件自动格式化。

### 9. 板块端点（API-087/088）：只读 + 公共缓存 + ETag

```python
@router.get("/sectors/overview", response_model=SectorOverviewOut, operation_id="getSectorsOverview")
async def get_sectors_overview(
    ctx: Annotated[TenantContext, Depends(get_tenant)],
    sector_data: Annotated[SectorDataSource, Depends(get_sector_data)],
    ...
) -> SectorOverviewOut:
    ctx.forbid_admin_trades()   # admin → 403 ADMIN_CANNOT_READ_TRADES
    # 数据源在 AppState 启动期装配；请求内不联网、不写 market.*
    # 金额/比率在此层转定点字符串；缺失显式 null + missing[]
```

- 两个端点都**只读**：不建 job、不写表、不联网；`If-None-Match` 命中返回 304 空 body。
- 未同步/覆盖不足 → **200 + `missing[]`**（`SECTOR_DATA_MISSING` / `SECTOR_BENCHMARK_WEIGHTS_MISSING` 等），`missing[]` 码不是 `ErrorCode`。
- 参数非法（`taxonomy`/`level`/未来 `as_of`/`benchmark=nope`/`limit>500`）→ 422；`concept` 的 `level` 只允许 null/0。
- 概念块 `Σexposure>1` 时必须透出后端 `caveat`；`weight_mode` 行业为 `additive`、概念为 `overlapping`。

## 文件清单

| 文件 | 职责 |
|---|---|
| `app.py` | FastAPI create_app（注册中间件链 + 全量 router + lifespan） |
| `deps.py` | get_state / get_tenant（依赖注入：AppState + TenantContext） |
| `routing.py` | StrictQueryRoute（未登记 query 参数 → 400） |
| `schemas.py` | Pydantic 模型（StrictModel 基类 + Field 约束） |
| `errors.py` | RFC 7807 中间件（ApiError → 标准错误响应体） |
| `middlewares.py` | 中间件链（request_id / 租户上下文 / 响应头） |
| `routers/auth.py` | 注册 / 登录 / 刷新 / 登出 / me |
| `routers/tokens.py` | PAT CRUD |
| `routers/imports.py` | API-010~015 导入链路 |
| `routers/trades.py` | API-017~021 流水维护 |
| `routers/jobs.py` | API-061~064 任务查询 / SSE 事件流 / 取消 |
| `routers/reconciliation.py` | API-026 余额链对账 |
| `routers/ops.py` | 运维根路由：/healthz /readyz /version /errors /metrics |
| `routers/sectors.py` | API-087/088 板块概览与公共目录（只读、ETag/304、200+`missing[]`） |
| `cli.py` | 运维 CLI：seed-tenants / seed-demo / sync-market / sync-sectors / api-check / api-snapshot / sdk |
