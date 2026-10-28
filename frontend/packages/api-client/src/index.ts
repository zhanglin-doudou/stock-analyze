/**
 * @sa/api-client
 *
 * 契约由后端 `GET /api/v1/openapi.json` 生成 SDK 类型到 `packages/api-client/src/api/generated/schema.d.ts`（由 `make sdk` 生成，勿手改）。
 * 本层（手写层）只负责 fetch 传输：注入 Bearer、解析 RFC 7807 错误、401 刷新后重放，不含任何业务逻辑。
 * 金额/数量/价格等定点字段一律使用 string，禁止转 number 参与计算。
 */
export * from "./http";
