# FIFO 回合分析增强设计 · 独立评审记录与修订处置（2026-11-04）

> 评审对象：[FIFO 回合分析增强开发设计](../architecture/2026-11-04-fifo-round-insights.md)
> 评审轮次：v0.1 → **v1.0（评审修订版）**
> 评审方式：3 名独立评审员（量化/架构/产品），只读代码/文档/迁移/门禁，**未修改跟踪文件、未跑 DB/网络**；评审证据落 `var/review-round-insights/`（gitignored）。
> 结论：3 名评审均为 **REQUEST_CHANGES**；v1.0 已按下述 P0 全部修订、P1 采用/明确延后；**设计定稿，可进入实现**（仅 API-ID 期次待 PM，见 §4）。
> **编号修订（2026-11-05）**：API-089 预留、暂不实现；本任务端点登记为 **API-090~094**（API-Catalog §1/§2.6），设计内容不变。

---

## 1. 评审范围与判决

| 评审员 | 关注面 | 判决 | 主要 P0 |
|---|---|---|---|
| `reviewer-quant`（金融/量化） | 术语、公式、前视/生存偏差、基准、费用/复权、排序统计 | **REQUEST_CHANGES** | B2/执行口径与归因 D-03 冲突；B3 与 `selection.bhar_20` 重复；`holding_days` 实为工作日；公司行动检测错；净价/费用混用；股息/成本基座；percentile 非能力；卖飞条件冗余；复用函数不成立；样本边界 |
| `reviewer-arch`（架构/数据/API） | 迁移、RLS、稳定键、refresh、路由、并发、门禁 | **REQUEST_CHANGES** | `/rounds/insights` 被 `/{round_id}` 路由吞；`trade_id` 重导入不稳定；`scope=missing` 无指纹；`market_data.as_of` 不可靠；公司行动检测错；`annotated_at` 客户端可伪造；refresh 写三件套/202；3s 预算与逐行 I/O 不符 |
| `reviewer-product`（产品/可用性/合规） | 需求覆盖、页面、零口径、免责、验收可执行 | **REQUEST_CHANGES** | 理由筛选/待补清单未落 API；展示契约缺 `_pct/_display`；公司行动检测错；免责声明/词表缺失；期间归属/`data_error`/分母未定；ETag 理由变更 304；文档仍 v0.1 且 HITL 映射错 |

> 评审同时确认：`timing.fly` 阈值方向、`behavior.averaging_down` 的摊平判定、越权 404/403、迁移编号 0012、当前 API 计数 88/56/49 等基本事实成立；本记录不重复列“通过项”。

---

## 2. P0 处置明细（全部接受并修订）

| # | 评审问题 | 处置 | v1.0 位置 |
|---|---|---|---|
| 1 | B2 被称为“执行质量”，与归因 D-03（成交价 vs 当日收盘）冲突；B1“决策漂移”不专业 | **接受**：新增 B0 执行价差（毛/净）、B1 事件后收盘收益、B2 净价后收益、B3 基准相对收益；页面中文标签；排序只用 20D B3 | §0 D-04、§3.3.2、§7.1 |
| 2 | B3 与 `selection.bhar_20` 同式却重复实现，基准口径不同 | **接受**：B3 复用 `compute_bhar_events`/`close_on_or_before`/同一基准；单事件 B3 vs BHAR 均值；同 fixture 对账 AC-F7 | §0 D-15、§3.3.2、AC-F7 |
| 3 | `holding_days` 是 weekday proxy，设计误称交易日 | **接受**：新增 `holding_trading_days`（真实日历）；旧字段只展示；波段/`late_annotated`/前瞻统一真实交易日；H-01 意图保留 | §0 D-03/D-05、§3.3.1、§3.4、§5.2 |
| 4 | 送转/配股/分红用账本行、`category=other` 送转漏检、到账日≠除息日 | **接受**：共用 `is_corporate_action`（含 `other`+摘要关键词）；窗口内任何公司行动 → `corporate_action`、收益 null、不排名；P1 行情源/后复权 | §0 D-12、§3.3.4、AC-F5 |
| 5 | B2/止盈/卖飞用不含费价，摊平/止盈均价含费 | **接受**：净价 `P_net=amount/qty`；B0 毛价与归因 D-03 对齐；B2/止盈/卖飞/摊平用净价；毛/净字段分开 | §0 D-04、§3.3.2、§3.5/3.6 |
| 6 | 红利不调成本 → 摊平/止盈误判；会改动 18 指标 | **接受但限界**：本期**不改** 18 指标；成本基座 `broker_net_cost_excl_dividends` 明示；公司行动窗口 fail-closed；P1 total-return 成本 | §0 D-06/D-15、§3.3.4、§10 T-06 |
| 7 | percentile 非能力、n=20 无 CI、closed-only、重叠窗口、70/40 象限构造性 | **接受**：改为 **20D B3 原始百分点**排序；n≥30 才排名、10–29 样本不足、<10 不下结论；CI/`sample_status` 显式；象限按正负号；百分位仅辅助；open/closed 分离 | §0 D-09、§3.7、§5.3、AC-K |
| 8 | 卖飞公式 L251 无盈利过滤、A.3/AC-D5 要求排除止损、`B2>5%` 被 max_close 恒包含 | **接受**：单一主口径 `max_close_20 > net_sell×1.03`；删除 OR；拆 `profit_take_fly`/`stop_loss_rebound`；错失收益 theoretical+end 两版 | §0 D-08、§3.6、AC-D5、A.3 |
| 9 | “复用 averaging_down/timing.fly”函数级不成立 | **接受**：抽 core 纯函数 `classify_addon/classify_sell`；metric 与洞察共用；不改旧指标口径；B3 共用 BHAR 函数 | §0 D-15、§5.2 |
| 10 | 样本/边界：货基/CASH/债券 alias、停牌阈值、`base_shifted`、T+1/部分卖出、多账户 | **接受**：证券白名单/排除码；stale≤5、base_shifted≤1；`is_same_day/is_full_exit/is_partial_exit/is_new_stock`；多账户合并披露 | §3.3.5、§3.6、AC-F11、AC-D7 |
| 11 | `/rounds/insights` 被 `/{round_id}` 吞 | **接受**：静态路由先注册；前后端路由顺序测试 | §0 D-14、§6.3、§7.3、AC-P1 |
| 12 | `trade_id` 重导入不稳定、理由丢失 | **接受**：逻辑键 `(user_id, trade_dedupe_key)`；`trade_id` 仅当前展示；重导入关联；孤儿提示 | §0 D-01、§4.1、AC-R4 |
| 13 | `scope=missing` 无 trade/bar 指纹；data_as_of 不可靠 | **接受**：`trade_version` + `bars_fingerprint` + `data_as_of=max(daily_bar.trade_date)`；失效重算；清理孤儿 | §0 D-10、§4.3、§5.1、AC-F4/F8 |
| 14 | `annotated_at` 客户端可伪造 | **接受**：服务端 Clock 写；请求不接收；`late_annotated` 服务端算 | §0 D-02、§4.1、§6.1、AC-R7 |
| 15 | refresh 无 Idempotency-Key/单飞；202 阈值未定；逐行 N+1 | **接受**：Idempotency-Key 必填、per-user 单飞、≤5000 同步/超过 202、批量 IN+ON CONFLICT；审计 | §0 D-11、§5.1、§6.5、AC-F8/F9 |
| 16 | 迁移/门禁常量/ETag/路由/pageMeta 未列 | **接受**：0012 + schema_version/version.py + REVIEW_TABLES/conftest/RLS/downgrade；ETag 纳入 reason.version；EXPECTED 88→93、ops 56→61、JSON +4；页面路由/pageMeta/图标 | §4、§6.6、§8.7、§9 |
| 17 | 文档仍 v0.1、HITL 映射错、入口无 H 项 | **接受**：升 **v1.0**；H-01~H-10 确认记录；T-01~T-11 新增；D-13/D-14 映射修正；API-ID 期次列 PM | 本记录、§10 |

---

## 3. P1 处置

| 类别 | 处置 |
|---|---|
| MAE/MFE 术语 | 改为 `worst_close_20`/`max_close_20`，明示 close-only；不再用 intraday 术语 |
| 分页/汇总/排序 | 汇总与 cursor 解耦；`sort` 白名单；`sample_status`/`basis`/`n` 显式；`sort` 回退写死 |
| 基准/β/风格 | 页面写“相对 ETF 基准”，不称“择时能力”；P1 β/行业分层；敏感性说明 |
| 与评分卡命名冲突 | 本设计叫“买卖后表现排序”；评分卡叫“行为健康度过程分”；两者不混 |
| 理由×结果 | `late_annotated` 分层；覆盖率近 90 日/全历史；增加 AC-D6 |
| 跨期/版本 | 按 close_date 归期；`period_basis`/`ranking_as_of`；旧 `definition_version` 不参与读 |
| 性能 | 5k/20k 两档基准；批量 SQL；3s 先标目标，实测校准；不达标 202 |
| 自动化 | JobType/HANDLERS/单飞/租约/取消；CLI/cron 触发；不假设 `sync-market` 有 scheduler |
| 页面可用性 | 待补理由 tab、术语中文、免责声明、词表、移动端/可访问性/空态 |

**明确延后（P2/后续，不阻塞 v1.0）**：后复权/hfq、行情侧公司行动源、β/行业中性、bootstrap CI 完整实现、理由码自定义、journal 合并、分账户过滤、capture ratio、市场状态分层。均已在文档中标 P1/P2 与数据/范围影响。

---

## 4. 残余风险与唯一待 PM 项

- **T-10（API-ID/期次）**：**API-089 预留（暂不实现）**；本任务从 API-090~094（理由 PUT/DELETE、insights list/detail、refresh）编号，期次建议 Period3-05；需 PM 在 Catalog/DEV-PLAN 定稿后同步 `EXPECTED_ALL_ENDPOINTS`/contract 计数/隔离矩阵。
- **P1 未实现项**：CI/bootstrap、hfq/公司行动源、β 中性等，已在验收中显式标 P1/P2，不冒充已完成。
- **数据现状**：本地 2026 账户 99 个已平仓回合；`behavior.hhi` n=0 等既有数据缺口不影响本设计（本设计不依赖 HHI）。
- **不可量化项**：买卖理由的真实性、能力圈、情绪/新闻等仍无法从账单验证；本设计只做人工标注+后验统计，不做心理推断。

---

## 5. 结论

- **v0.1 不可实现**；v1.0 已修复全部 17 项 P0（含 8 项量化/口径、6 项架构/API、3 项产品/验收）。
- **设计定稿（v1.0，2026-11-04）**；实现前只差 T-10（API-ID 期次）PM 确认。
- 评审证据：`var/review-round-insights/review-quant.md`、`reviewer-arch.md`、`review-product.md`（gitignored）。
- 本记录与设计文档同版本入库；后续若改阈值/公式/表结构，必须 bump `definition_version` 并追加本记录修订。

> 评审记录不代表评审员对修订后文本逐字复核；v1.0 由 Lead 按上述处置修订，并保留 P1/P2 残余风险披露。
