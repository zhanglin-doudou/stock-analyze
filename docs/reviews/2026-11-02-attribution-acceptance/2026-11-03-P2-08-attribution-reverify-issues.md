# Period2-08 五因子归因 · P0/P1 修复复验问题清单（2026-11-03）

> 复验对象：本地实例 Web `http://127.0.0.1:5173`、API `http://127.0.0.1:8000`、DB 本机 `127.0.0.1:5432/stock_analyze`（live 数据）
> 复验账号：`1234@dev.com` / `1234`；对照 `b@demo.example.com`、`admin@demo.example.com`
> 复验时间：2026-11-03 01:35–01:45（Asia/Shanghai）
> 环境：分支 `feature/period-2`，HEAD `a69f9ee`（P0 修复 `830815b`/`824195e`/`5e86a89`/`d9be91e`，P1 修复 `ec04731`…`5fcca31`；P1-1 文案 `49759b9`，lint `a69f9ee`）
> 浏览器自动化：Chrome 153 `--headless=new` + CDP（Node 24 原生 WebSocket，端口 `9222`；页面 Web `5173`/API `8000`），视口 1440×900、768×900、390×844、375×812、320×720
> 测试库门禁：Docker `127.0.0.1:5433`（`STOCK_DB_PORT=5433 make test-p2`）

## 1. 逐项复验结论

### 1.1 后端（§7.1）

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 桥接对账（P0-A） | ✅ 通过 | 2026-10、2026-01-01..2026-10-31 均无 `BRIDGE_MISMATCH`；`valuation_gap=0.00` |
| 2 | 残差桶/镜像持仓（P0-B） | ✅ 通过（按单用户口径） | 期初快照按资产表稳定序重放，`valuation_gap` 由 −336,022.00 → 0.00；全区间残差组成 `unattributed 109,603.33 + interest 5.97`。注：验收文 §4.2 对 `000422` 的手算用了 15,800 股（跨账号重复行），本账号实际 7,900 股；按单账号重算与 API 一致，非缺陷 |
| 3 | 用户可见单位（P0-C） | ✅ 通过 | `RESIDUAL_LARGE` reason 使用 `Money` 元格式（两位小数）；`missing[].field_label` 已返回 |
| 4 | 空结论原因（P0） | ✅ 通过 | blocked 返回 `summary_na_reason="区间数据不可用（blocked）…"`；覆盖率/估值差门禁未过时返回原因；请求区间 `summary` 已非空 |
| 5 | 契约 additive | ✅ 通过 | `presentation.share_pct`、`residual_breakdown_labels`、`missing[].severity/field_label`、`assumptions.data_sources` 均可用；`make api-check`、`api-snapshot --check` 通过 |
| 6 | 请求区间复验 | ✅ 通过 | `2026-01-01..2026-10-31`：`summary` 非空、无 `BRIDGE_MISMATCH`/`RESIDUAL_LARGE`，`missing` 仅 `SECTOR_BAR_MISSING`(5 只)/`SECTOR_MAPPING_MISSING`(7 只)；`2026-10`：仅 `SECTOR_MAPPING_MISSING`；页面数据质量均为真实可解释项 |

### 1.2 前端（§7.2）

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 空结论态（U-01） | ✅ 通过 | `summary=null` 时渲染 `summary_na_reason`，不再只有 `—`（单测覆盖） |
| 2 | 百分比/单位（U-03） | ✅ 通过 | 页面显示 `98.9%`、`-1,900.00 元`；金额千分位+“元”，无原始小数 |
| 3 | 排序（U-04） | ✅ 通过 | DOM 排名 `1,2,3,4,5`，`rank=1` 在首行 |
| 4 | 页头（U-06） | ✅ 通过 | 总损益、比率分母、基准（沪深 300 全收益）分列 |
| 5 | 原始码值映射（U-02） | ✅ 通过 | 缺失项 `reason` 人话 + `影响：...` + `建议：...`；技术详情折叠保留码值 |
| 6 | 移动端（U-07） | ✅ 通过 | 390/375/320/768 均 `scrollWidth == clientWidth`，offenders=[] |
| 7 | 残差/KPI/数据质量（U-08~U-11） | ✅ 通过 | “残差去向”+正负含义；桶按金额降序；覆盖率三口径；页头三源截至；blocked 显示区间末端 |
| 8 | 测试（U-19） | ✅ 通过 | `pnpm test` 128/128（新增 7 条针对 U-08~U-16 的断言，不再断言 raw decimal） |

### 1.3 §7.3 复验收标准

| 标准 | 结论 | 证据 |
|---|---|---|
| 页面无 `—` 空结论；数据质量首条非 `BRIDGE_MISMATCH` | ✅ 通过 | 请求区间首条为 `SECTOR_BAR_MISSING`；结论非空 |
| 重跑 §3 矩阵：⑲ 转 PASS | ✅ 通过 | 19 条中 16 PASS / 3 SKIP（④⑮⑱ 仍由 fixture 覆盖） |
| 390px 无横向滚动 | ✅ 通过 | `390/390`，另测 320/375/768 均 0 溢出 |
| `pnpm test` 全绿 + ≥4 条新断言 | ✅ 通过 | 128 passed；新增 U-08/09/10/11/12/13/14/15/16 断言 |

## 2. §3 矩阵逐项（live 复跑）

| # | 结果 | 证据 |
|---|---|---|
| ① | ✅ | Σfactors+residual−total_pnl = 0 |
| ② | ✅ | ranks 排序后 `[1,2,3,4,5]` |
| ③ | ✅ | Σratio+residual_ratio = 1 |
| ④ | ⏭️ SKIP | 依赖 `fx_attr_single_*` fixture（`test-p2` 通过） |
| ⑤ | ✅ | `period=2026-06` → blocked、`total_pnl=null`、页面无金额 |
| ⑥ | ✅ | 5 因子 `selection/sector/buy_timing/sell_timing/fee`，中文名 |
| ⑦ | ✅ | 桶合计与 residual 差 0 |
| ⑧ | ✅ | 结论无裸术语/建议（原有校验） |
| ⑨ | ✅ | B 用户 200 且 `total_pnl=null`，无泄漏 |
| ⑩ | ✅ | admin 403 `ADMIN_CANNOT_READ_TRADES` |
| ⑪ | ✅ | 422 `INVALID_PERIOD` / 422 `MODEL_NOT_AVAILABLE`（`model=brinson_industry`）/ 422 `UNKNOWN_BENCHMARK` / 400 `UNKNOWN_QUERY_PARAM` |
| ⑫ | ✅ | 两次响应 SHA-256 一致 |
| ⑬ | ✅ | degraded + `capital_ratio=0.9924`，未归因 5 条 |
| ⑭ | ✅ | 2026-10 负总损益：summary 含“亏损/拖累/占构成” |
| ⑮ | ⏭️ SKIP | live 未构造出 <10 段区间；fixture 覆盖 |
| ⑯ | ✅ | `data_as_of=2026-10-30=min(资产 2026-11-02, 行情 2026-10-30, 板块 2026-10-30)`；`assumptions` 18 键 |
| ⑰ | ✅ | `2026-10-30..10-31` degraded，无 `ASSET_SERIES_STALE` |
| ⑱ | ⏭️ SKIP | live 未命中公司行动；fixture 覆盖 |
| ⑲ | ✅ | 无 `BRIDGE_MISMATCH` |

## 3. 余留问题（按新分级）

### 新 P1-1（文案语义）：正总损益下“主要来自”引用了负贡献因子 —— ✅ 已修复（`49759b9`）

- 现象：`2026-01-01..2026-10-31` 响应 `total_pnl=+84,198.47`，`summary="本期盈利 84,198.47 元，主要来自「板块配置」（-91,228.02 元，占构成 52.7%）…"`。
- 根因：`backend/packages/review/src/stock_review/attribution/presentation.py:198-214` 用绝对贡献排序取 rank=1 后固定写“主要来自”，未区分该因子金额与总损益同号/异号。
- 影响：用户会读成“盈利主要来自亏损项”，与页面“残差大于本期总损益”提示叠加后更难理解。
- 修复（`49759b9`）：措辞跟随 dominant 因子金额符号（正贡献“主要来自”，负贡献“主要拖累”）；英文同步 `mainly from` / `mainly dragged by`；数值不变。位置 `presentation.py:196-209`，新增单测 `test_summary_wording_follows_dominant_amount_sign`（及中英文既有断言更新）。

### P2-1（精确度）：`missing.field_label` 粒度偏粗

- `SECTOR_BAR_MISSING` 的 `field_label` 为“因子归因”，未指出具体是 `factors.sector`。不影响理解，可在后端映射中细化。

### P2-2（完整性）：U-16 仅提供入口链接

- 因子行已给“查看相关回合/板块”链接，满足验收最低要求；点击展开 Top 标的/回合的 drilldown 仍为后续能力（依赖后端 additive `drilldown`）。

### 门禁

- `make lint` 已全绿（`a69f9ee` 修复 6 条 ruff 问题 + 3 个文件格式化）；`make typecheck` 通过。
- `verify-p2` 未提供（Period2-14 暂停），以 `test-p2` 替代。
