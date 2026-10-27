# Period2-07 三域指标实现 —— 测试设计（part_test）

> 作者：test-designer｜日期 2026-10-26｜状态：待交叉评审（**只出测试设计，不含生产代码**）
> 输入：`brief.md`（冻结范围 18 指标/引擎契约/HITL 1–18）、`Testing.md` §4/§7/§8/§9.3/§12.1/§12.3、`API-Catalog.md` API-033/034/035/038/079 + §6、`Extensibility.md` §3/§11.1/§12、`PRD-v2.md` 附录 A（A10–A26）与 FR-R8。
> 事实基线（逐条去代码核实过，**不编造现状**）：`backend/tests/` 现有 `api/ architecture/ contract/ integration/ isolation/ oracle/ unit/` 七个目录；**没有** `golden/ support/ factories/ perf/ data/`；`contract/kit.py` 不存在；`Makefile` 现有 `test-p2/verify-p1`，**无** `verify-p2`、无 `mutation` target；根 `pyproject.toml` **无** mutmut/pytest-benchmark 配置；**无** `.github/workflows`；`review.metric_result`、`metrics/` 包、`/metrics` 路由均未实现（Period2-05/06 前置）。core 实际路径是 `backend/packages/core/src/stock_core/`（文档里写的 `core/src/core` 是错的，见 H-30）。
> 数字约定：本文所有金额=元（2 位）、比率=十进制 8 位、天数/次数=整数；手算全部用 Python `Decimal`（脚本临时 `/tmp/p207_final.py`，未入库），**禁 float**。

## 1. 测试分层总览（L0–L6）

| 层 | 目录（★=本期新增） | 命名规范 | 运行命令 | 阻断级别 |
|---|---|---|---|---|
| L0 纯函数单测 | `backend/tests/unit/core/test_metrics_{perf,timing,behavior,selection}.py`★、`test_stats_ci.py`★、`test_metrics_tolerance.py`★ | `test_<func>_<behavior>`；每个公式 ≥3 例（正常/边界/退化） | `uv run pytest backend/tests/unit/core -q --cov=stock_core --cov-branch --cov-fail-under=100` | **硬**（core 100%/100% 不变） |
| L1 golden（独立 oracle + 手算表） | `backend/tests/golden/<metric_id>/golden*.json`★、`backend/tests/golden/test_metrics_golden.py`★、`test_metrics_three_way.py`★ | 目录=点号 metric_id（如 `perf.win_rate/`）；文件 `golden.json`/`golden.<variant>.json` | `uv run pytest backend/tests/golden -q -m golden` | **硬**（golden diff 未审批=一票否决，Testing D8） |
| L2a 契约 kit（纯） | `backend/tests/contract/kit.py`★、`test_metric_plugin.py`★ | `assert_<property>`；参数化 `registry.all()` | `uv run pytest backend/tests/contract/test_metric_plugin.py -q` | **硬**（缺一指标未跑=红） |
| L2b 契约 kit（连库） | `backend/tests/integration/test_metric_contract_db.py`★ | `assert_drilldown_callable` / `assert_tenant_scoped` / `assert_metric_result_rls` | `uv run pytest backend/tests/integration/test_metric_contract_db.py -q` | **硬** |
| L3 API 验收 | `backend/tests/api/test_api_033_metrics.py`★、`test_api_034_metrics_explain.py`★、`test_api_035_metrics_drilldown.py`★、`test_api_038_selection.py`★、`test_api_079_metrics_detail.py`★ | `test_api_<NNN>_<slug>.py` + `pytestmark=pytest.mark.api`；模块 docstring 写 API-NNN | `uv run pytest backend/tests/api/test_api_033*.py backend/tests/api/test_api_034*.py backend/tests/api/test_api_035*.py backend/tests/api/test_api_038*.py backend/tests/api/test_api_079*.py -q` | **硬**（每端点 ≥3 条 + `gate_endpoint_coverage`） |
| L4 隔离与越权 | `backend/tests/isolation/test_isolation_matrix.py`（扩矩阵）、`backend/tests/integration/test_metrics_rls.py`★ | 复用现有 `RESOURCE_MATRIX/ADMIN_FORBIDDEN/LIST_ISOLATION_MATRIX/UNAUTHENTICATED_MATRIX` 四张表 | `uv run pytest -m isolation -q && python scripts/ci/gate_tenant_coverage.py && python scripts/ci/gate_rls_coverage.py` | **硬·零豁免**（D3/D8） |
| L5 性能/统计可信度 | `backend/tests/perf/test_metrics_batch_p95.py`★、`test_core_microbench.py`★ | `test_pf_<num>_<scene>` | `uv run pytest backend/tests/perf -q -m perf` | PR-full 告警；release 2× 超预算**阻断** |
| L6 变异 | 根 `pyproject.toml [tool.mutmut]`★、`scripts/ci/gate_mutation_score.py`★、`Makefile mutation/mutation-full`★ | mutmut 复用 L0/L1 既有用例，**不新增测试文件**（新增 `test_kill_*` 只是措辞，落 L0 文件） | `make mutation`（diff-only）/ `make mutation-full`（nightly） | nightly 硬（≥80%）；release 阻断 |

补充：
- 新增 pytest markers（`pyproject.toml [tool.pytest.ini_options].markers`）：`golden`、`perf`；`-m "not perf and not live"` 保持 PR-fast 默认。
- L1/L2a 不连库：golden 从 `fixtures/metrics/*.csv|json` 直接在内存构造 DatasetView（纯函数输入），PR-fast 跑。**注意** `backend/tests/contract/conftest.py:1` 用同名 autouse 夹具把 `_clean_db` 覆盖为空操作，所以**需要 client/DB 的 kit 断言不能放 contract/**，放 L2b `integration/`。L2b/L3/L4/L5 连真 PG，复用 `backend/tests/conftest.py` 的 `client/user/admin/owner_engine/app_engine/account_factory`（**不新建第二套夹具**）；数据落库后「DB→引擎→API」链路在 L3 覆盖。
- 确定性：所有用例用 `FrozenClock("2026-09-30T15:00:00+08:00")`；bootstrap 固定 seed（H-21）；禁 `sleep`/`rerun`（Testing §12.2）；golden/越权用例 `skip/xfail` 由 `gate_no_security_skip.py` 判红（脚本尚不存在，本期新建，见 §11）。
- 用例预算（总量 ≤260 条，超出即说明用了重复矩阵）：L0 90 / L1 golden 40（18 指标 × 变体）/ L2a 22 / L2b 10 / L3 50（5 端点）/ L4 24（矩阵增行）/ L5 4 / L6 由 mutmut 生成。时间预算：PR-fast ≤5min（L0+L1+L2a+架构）、PR-full ≤20min（全量）。
- 口径事实源（行号，逐条核实过）：净现金流已含费 `backend/packages/ledger/src/stock_ledger/parsers/huatai.py:7`；持有天数=周一~周五近似 `backend/packages/core/src/stock_core/fifo/calendar.py:12`；回合 pnl/data_error `backend/packages/core/src/stock_core/fifo/engine.py:303`；门槛常量 `backend/packages/contracts/src/stock_contracts/constants.py:21-24`；demo 行情月频 9 点 `backend/packages/ledger/src/stock_ledger/fixtures.py:186`；replay 取数 `backend/packages/ledger/src/stock_ledger/market.py:239`；测试清库表清单 `backend/tests/conftest.py:60`；Catalog 断言 `docs/architecture/v2/API-Catalog.md:315,321,327,345,606`。

## 2. Golden 设计与目录

### 2.1 目录与 schema
`backend/tests/golden/<metric_id>/golden.json`（**与 Testing §9.1 的 `fixtures/expected/metrics/a/` 双源冲突，见 H-19；推荐本方案**）。一个目录可放多份变体（`golden.n5.json`…），loader `backend/tests/golden/loader.py`★ 用 `glob("golden/**/golden*.json")` 参数化，新增指标不改测试代码。

```json
{
  "schema_version": 1, "metric_id": "perf.win_rate", "definition_version": "1.0",
  "fixture": {"id": "fx_metrics_core", "sha256": "<csv sha256>", "manifest": "backend/tests/fixtures/metrics/MANIFEST.json"},
  "period": "2026-09", "scope": "round", "params": {"basis": "closed", "benchmark": "hs300_tr", "rf": "0.015"},
  "input_snapshot": {"trades_token": "<Σversion+max(updated_at) 指纹>", "bars_sha256": "<fx_bars_min sha256>"},
  "expected": {"value": "0.66666667", "unit": "ratio", "n": 12, "ci_low": null, "ci_high": null,
    "sample_status": "ok", "status": "ok",
    "decomposition": {"win_rate_all": "0.60000000", "n_pos": 8, "n_neg": 4, "n_open": 3, "n_data_error": 1}},
  "tolerance": {"kind": "ratio_bp", "eps": "0.0001"},
  "expected_source": {"kind": "hand_calc", "ref": "part_test.md §4 表4-2", "oracle": null},
  "review": {"author": "test-designer", "reviewer": null, "approved": false, "approved_at": null, "golden_approved_label": null},
  "boundaries": ["n=12∈[10,30) 不出 CI", "data_error 不入分母", "open 仅入 win_rate_all"],
  "notes": "CI 由框架代算；本文件禁止写 CI 期望（n<30）"
}
```
字段硬规则：`value` 必须字符串（防 float 中转）；`unit∈{CNY,ratio,bp,days,count,z,score}`；`ci_low/ci_high` 仅在 `sample_status=ok 且 n≥ci_min_n` 时非 null；`expected_source.kind∈{hand_calc,oracle}`；`review.approved=false` 的文件由 `gate_golden_approval.py`★ 判红（Testing §12.3：只允许独立 PR + `golden-approved` label + 金额到分/比率到 bp 的 diff）。

### 2.2 18 指标 golden 清单

| # | metric_id | fixture | 期望值来源 | 容差 | 覆盖边界 |
|---|---|---|---|---|---|
| 1 | `perf.win_rate` | fx_metrics_core | 手算 | ratio 1bp | n=12；全样本 decomp；pnl=0 排除；data_error 排除；n=5/9/10/29/30 门槛 |
| 2 | `perf.pnl_ratio` | fx_metrics_core | 手算 | ratio 1bp | 全盈/全亏/无亏（null）；median decomp |
| 3 | `perf.holding_days` | fx_metrics_core + fx_multilot | 手算 | days 精确 | 加权多 fill；median/自然日 decomp；open 不计 |
| 4 | `perf.concentration` | fx_metrics_core | 手算 | ratio 1bp | n_pos=8；top3/CR1/Gini；n_pos<2→insufficient |
| 5 | `perf.fee` | fx_metrics_core | 手算 | ratio 1bp | 三子项；禁二次扣费；data_error 变体 |
| 6 | `perf.fee_breakeven` | fx_metrics_core + fx_asset_core | oracle | ratio 1bp | 分母≤0→unreliable；TWR 缺失→blocked |
| 7 | `perf.expectancy` | fx_metrics_core | 手算 | CNY 0.01 | p* decomp；exploratory 过滤 |
| 8 | `timing.chase` | fx_metrics_core + fx_bars_min | oracle | ratio 1bp | max20=min20（flat）→unreliable；缺 bar→pending |
| 9 | `timing.fly` | fx_cohort_fly + fx_bars_missing | oracle | ratio 1bp | h=5/10/20 decomp；z=1.0 边界 `<`↔`<=`；停牌>50% 剔除 |
| 10 | `timing.beta` | fx_bars_min | oracle | β/α 1bp，t 1e-4 | 交易日 59→pending_window / 60→ok；缺基准 blocked |
| 11 | `timing.drawdown` | fx_asset_core | 手算小例 + oracle | ratio 1bp | MDD/持续/恢复/水下/Ulcer；build≠current→stale |
| 12 | `behavior.de` | fx_metrics_core | 手算（PGR/PLR，笔数口径） | ratio 1bp | 方向未定 H-20；n=15 无 CI；bootstrap 固定种子 |
| 13 | `behavior.turnover` | fx_metrics_core + fx_asset_core | oracle | ratio 1bp | 平均持仓市值口径 H-26；data_error 排除 |
| 14 | `behavior.averaging_down` | fx_multilot | 手算 | ratio 1bp / count 精确 | 含费均价比较；补仓 vs 摊平定界 |
| 15 | `behavior.hhi` | fx_metrics_core + fx_cash_only | oracle + 手算小例 | ratio 1bp | HHI_eq/HHI_total；现金口径；无持仓 insufficient |
| 16 | `selection.bhar_20` | fx_cohort_sel | oracle | ratio 1bp | PIT as_of；窗口不足→pending；n<10 insufficient |
| 17 | `selection.bhar_60` | fx_cohort_sel | oracle | ratio 1bp | 59/60 bar 边界；停牌剔除 |
| 18 | `selection.bhar_hit` | fx_cohort_sel | oracle | ratio 1bp | exploratory；n=0→insufficient 且无 value |

golden 覆盖检查（`test_golden_manifest.py`★）：`registry.all()` 的 id 集合 == golden 目录集合；缺一即 fail；`definition_version` 不匹配且快照变化 → fail（Testing §12.3）。

## 3. 夹具设计

### 3.1 `fx_metrics_core` 确定性构造规格（12 closed + 3 open + 1 data_error，3 标的）
文件：`backend/tests/fixtures/metrics/fx_metrics_core/trades.csv`★（列为 `line_no,date,code,name,side,qty,price,commission,stamp_tax,other_fee,amount,balance,summary,round_key`）。金额口径同 parser：`amount_units=净现金流（买=-(量×价+费)，卖=量×价-费，股息=净额）`；`cash_balance=balance` 列（对账链闭合，`|diff|≤0.01`）；`line_no`=下表 `#`；`price_units=价格×1e6`、`qty_units=数量×1e4`。初始行 2026-08-31 银证转入 400000.00（区间前锚点，`assumption=statement`；选 40 万使 32 行余额全程为正，避免负现金触发额外降级语义）。

| # | 日期 | 证券 | 方向 | 数量 | 价格 | 佣金 | 印花 | 净额 | 余额 | 回合 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-31 | CASH | transfer | 0 | 0 | 0.00 | 0.00 | 400000.00 | 400000.00 | — |
| 2 | 2026-09-01 | 600519 | buy | 100 | 1650.00 | 41.25 | 0.00 | -165041.25 | 234958.75 | A1 |
| 3 | 2026-09-01 | 000001 | buy | 2000 | 12.50 | 6.25 | 0.00 | -25006.25 | 209952.50 | B1 |
| 4 | 2026-09-01 | 300750 | buy | 100 | 213.00 | 5.33 | 0.00 | -21305.33 | 188647.17 | C1 |
| 5 | 2026-09-07 | 600519 | dividend | 0 | 0 | 0.00 | 0.00 | 300.00 | 188947.17 | A1 |
| 6 | 2026-09-08 | 600519 | sell | 100 | 1700.00 | 42.50 | 85.00 | 169872.50 | 358819.67 | A1 |
| 7 | 2026-09-09 | 600519 | buy | 100 | 1680.00 | 42.00 | 0.00 | -168042.00 | 190777.67 | A2 |
| 8 | 2026-09-09 | 300750 | sell | 100 | 220.00 | 5.50 | 11.00 | 21983.50 | 212761.17 | C1 |
| 9 | 2026-09-10 | 000001 | sell | 2000 | 12.80 | 6.40 | 12.80 | 25580.80 | 238341.97 | B1 |
| 10 | 2026-09-10 | 300750 | buy | 200 | 215.00 | 10.75 | 0.00 | -43010.75 | 195331.22 | C2 |
| 11 | 2026-09-11 | 000001 | buy | 3000 | 12.60 | 9.45 | 0.00 | -37809.45 | 157521.77 | B2 |
| 12 | 2026-09-15 | 600519 | sell | 100 | 1650.00 | 41.25 | 82.50 | 164876.25 | 322398.02 | A2 |
| 13 | 2026-09-16 | 000001 | sell | 3000 | 12.40 | 9.30 | 18.60 | 37172.10 | 359570.12 | B2 |
| 14 | 2026-09-16 | 600519 | buy | 200 | 1660.00 | 83.00 | 0.00 | -332083.00 | 27487.12 | A3 |
| 15 | 2026-09-17 | 300750 | sell | 200 | 210.00 | 10.50 | 21.00 | 41968.50 | 69455.62 | C2 |
| 16 | 2026-09-17 | 000001 | buy | 2000 | 12.30 | 6.15 | 0.00 | -24606.15 | 44849.47 | B3 |
| 17 | 2026-09-18 | 300750 | buy | 100 | 205.00 | 5.13 | 0.00 | -20505.13 | 24344.34 | C3 |
| 18 | 2026-09-22 | 600519 | sell | 200 | 1690.00 | 84.50 | 169.00 | 337746.50 | 362090.84 | A3 |
| 19 | 2026-09-23 | 000001 | sell | 2000 | 12.70 | 6.35 | 12.70 | 25380.95 | 387471.79 | B3 |
| 20 | 2026-09-23 | 600519 | buy | 100 | 1700.00 | 42.50 | 0.00 | -170042.50 | 217429.29 | A4 |
| 21 | 2026-09-24 | 300750 | sell | 100 | 218.00 | 5.45 | 10.90 | 21783.65 | 239212.94 | C3 |
| 22 | 2026-09-24 | 000001 | buy | 1000 | 12.80 | 3.20 | 0.00 | -12803.20 | 226409.74 | B4 |
| 23 | 2026-09-25 | 600519 | sell | 100 | 1695.00 | 42.38 | 84.75 | 169372.87 | 395782.61 | A4 |
| 24 | 2026-09-25 | 300750 | buy | 100 | 210.00 | 5.25 | 0.00 | -21005.25 | 374777.36 | C4 |
| 25 | 2026-09-28 | 600519 | buy | 100 | 1640.00 | 41.00 | 0.00 | -164041.00 | 210736.36 | A5 |
| 26 | 2026-09-28 | 300750 | sell | 150 | 212.00 | 7.95 | 15.90 | 31776.15 | 242512.51 | C4 |
| 27 | 2026-09-29 | 600519 | sell | 100 | 1680.00 | 42.00 | 84.00 | 167874.00 | 410386.51 | A5 |
| 28 | 2026-09-29 | 000001 | sell | 1000 | 12.90 | 3.23 | 6.45 | 12890.32 | 423276.83 | B4 |
| 29 | 2026-09-29 | 300750 | buy | 200 | 215.00 | 10.75 | 0.00 | -43010.75 | 380266.08 | C5 |
| 30 | 2026-09-30 | 600519 | buy | 100 | 1680.00 | 42.00 | 0.00 | -168042.00 | 212224.08 | A6(open) |
| 31 | 2026-09-30 | 000001 | buy | 1000 | 12.80 | 3.20 | 0.00 | -12803.20 | 199420.88 | B5(open) |
| 32 | 2026-09-30 | 000001 | dividend | 0 | 0 | 0.00 | 0.00 | 100.00 | 199520.88 | B5(open) |

设计要点：① 每标的回合**严格串行**（同一标的不允许持仓重叠，否则 FIFO 会把两个"回合"合成一个周期）；② C4 为 data_error（买 100/卖 150，超卖 50，成交额按 100/150 比例分摊=21184.10）；③ open 估值用 2026-09-30 收盘（A6 1720.00、B5 12.20、C5 205.00）；④ **不放红利税行**（现有约定 DIVIDEND 已是税后净额，另加税行会双计，H-27）；⑤ 期末余额 199520.88（全程 >0），recon `break_count=0`。

### 3.2 其余夹具（全部 `backend/tests/fixtures/metrics/<id>/`★ + `MANIFEST.json` sha256）
| fixture | 构造要点 | 用途 |
|---|---|---|
| `fx_bars_min` | 93 个工作日 2026-05-25..2026-09-30，4 条序列（600519/000001/300750/hs300_tr），8 个锚点线性插值到 0.01（锚点见下）；末值钉死 1720.00/12.20/205.00/3200.50；`return_units`=日环比 8 位 ROUND_HALF_UP；`meta{as_of,provenance,sha256,supports_daily_bars:true}` | 择时域全部；β/α/IR；BHAR；open 估值 |
| 锚点 | 05-25/06-15/07-06/07-27/08-17/09-01/09-15/09-30：600519=1600/1580/1620/1650/1660/1640/1660/1720；000001=12.00/12.10/12.20/12.40/12.50/12.60/12.40/12.20；300750=200/205/210/208/212/210/212/205；hs300_tr=3000/3020/3050/3080/3100/3120/3150/3200.50 | 可复算、无 demo 依赖 |
| `fx_n5` | fx_metrics_core 的 A1–A5（5 closed，无 open）独立副本 | n=5→insufficient、无 CI（Catalog ③） |
| `fx_n30` | fx_metrics_core + 18 条规则扩展 closed（A/B/C 轮换，pnl 交替 ±300/±150，佣金=毛额×0.00025）→ closed 30、open 3 | n≥30→CI 非空、`ci_low<value<ci_high`（Catalog ④） |
| `fx_de_pos` | 30 closed（27 盈 + 3 亏）+ 6 open 全为浮亏（笔数口径：PGR=27/(27+0)=1.0、PLR=3/(3+6)=0.33333333） | "只卖盈不卖亏"→DE(PGR−PLR)=**+0.66666667** 且 CI 不含 0（FR-R6；**方向须 H-20 拍板**） |
| `fx_no_de` | 24 closed（12 盈/12 亏）+ 6 open（3 浮盈、3 浮亏）（笔数口径：PGR=PLR=12/15=0.8） | DE=0、CI 含 0（FR-R6） |
| `fx_blocked` | fx_metrics_core 但**清空全部余额列**（全历史无现金锚点 → nav/asset 整段 blocked） | nav 类（drawdown/fee_breakeven）blocked 无 value；round 类仍 ok（部分阻断） |
| `fx_broken_chain` | fx_metrics_core 但 09-16 行余额 +100.00（断链，锚点仍在） | 对账 degraded、`break_count=1`；资产序列可算但标 degraded（drawdown 仍可出值） |
| `fx_bars_missing` | 删 600519 在 09-14..09-30 的 bar；300750 只留 5 根 | chase/fly/beta blocked 或 pending；open 估值 degraded |
| `fx_pit_asof` | 双快照：base（bar 到 09-30）与 future（10-01..12-31 收益全部 +300%）；请求恒为 `as_of=2026-09-30` | 两次运行 BHAR 必须完全相同（读未来即 diff）；`data_as_of≤as_of` |
| `fx_selection_no_event` | 只保留卖出行（无买入事件） | cohort n=0→selection 指标 insufficient、无 value、诊断 job 仍 succeeded |
| `fx_multilot` | 单一标的 1 回合：3 笔买入（价格递减）+ 2 笔卖出 → `fills_count=5` | 加权 holding_days、摊平、下钻 items=5、HHI 重建 |
| `fx_cohort_sel` | 20 个买入事件（2026-06-01/06-15/07-01 三批，含 5 个停牌标的） | `selection.bhar_20/60`（bar 60 个前向交易日须落在 09-30 前，故事件 ≤07-01） |
| `fx_cohort_fly` | 9 个卖出事件（06-15/07-01/07-15 三批）＋对应 HS300 日线 | `timing.fly`（h=5/10/20 前向窗口全部走完） |
| `fx_cash_only` | 仅现金无持仓 | HHI_total=1.0、HHI_eq insufficient |
| `fx_asset_core` | 手写 22 行 `asset_daily`（09-01..09-22，`cash/market_value/total_assets/daily_return` 逐行手算），含 1 行 `status=blocked` | 回撤族/费用门槛/换手（不依赖生产 build） |

夹具约束：`owner_tenant=A`、`synthetic:true`、`MANIFEST.sha256` 缺条目即 fail；loader★ 只用 owner 直插 `ledger.trade` + 调用生产 `rebuild_rounds_for_user`/`build_assets` **物化输入**（允许），**期望值永不由生产代码生成**；测试期行情用 `ReplayMarketDataSource(<fixture path>)`，不读根 `fixtures/market/quotes.json`（demo 仅 9 个点，**不可用于择时域**，brief 已确认）。

## 4. 手算表（核心交付）

> 来源声明：以下全部由 `/tmp/p207_final.py`（Python 3.12 + `Decimal`，`ROUND_HALF_UP`，scale=40）独立脚本推导，**非生产代码生成、非 float**；生产侧期望值另行冻结进金标，二者独立。公式照抄 PRD 附录 A。

### 4.1 回合级 pnl / 持有天数（由 §3.1 流水配对得出，A8：pnl=卖出净额−买入含费成本+股息净额）
| 回合 | 买入（量×价+佣金） | 卖出（量×价−佣金−印花） | 股息净额 | pnl | 持有交易日 | 自然日 |
|---|---|---|---|---|---|---|
| A1 | 100×1650.00+41.25=165041.25 | 170000.00−42.50−85.00=169872.50 | 300.00 | **+5131.25** | 5 | 7 |
| A2 | 100×1680.00+42.00=168042.00 | 165000.00−41.25−82.50=164876.25 | 0 | **-3165.75** | 4 | 6 |
| A3 | 200×1660.00+83.00=332083.00 | 338000.00−84.50−169.00=337746.50 | 0 | **+5663.50** | 4 | 6 |
| A4 | 100×1700.00+42.50=170042.50 | 169500.00−42.38−84.75=169372.87 | 0 | **-669.63** | 2 | 2 |
| A5 | 100×1640.00+41.00=164041.00 | 168000.00−42.00−84.00=167874.00 | 0 | **+3833.00** | 1 | 1 |
| B1 | 2000×12.50+6.25=25006.25 | 25600.00−6.40−12.80=25580.80 | 0 | **+574.55** | 7 | 9 |
| B2 | 3000×12.60+9.45=37809.45 | 37200.00−9.30−18.60=37172.10 | 0 | **-637.35** | 3 | 5 |
| B3 | 2000×12.30+6.15=24606.15 | 25400.00−6.35−12.70=25380.95 | 0 | **+774.80** | 4 | 6 |
| B4 | 1000×12.80+3.20=12803.20 | 12900.00−3.23−6.45=12890.32 | 0 | **+87.12** | 3 | 5 |
| C1 | 100×213.00+5.33=21305.33 | 22000.00−5.50−11.00=21983.50 | 0 | **+678.17** | 6 | 8 |
| C2 | 200×215.00+10.75=43010.75 | 42000.00−10.50−21.00=41968.50 | 0 | **-1042.25** | 5 | 7 |
| C3 | 100×205.00+5.13=20505.13 | 21800.00−5.45−10.90=21783.65 | 0 | **+1278.52** | 4 | 6 |
| C4† | 100×210.00+5.25=21005.25 | 21184.10（卖出净额 31776.15 × 100/150，整数分向下取整=2118410 分） | 0 | **+178.85** | 1 | 3 |
| A6（open） | 100×1680.00+42.00=168042.00 | 期末市值 172000.00 | 0 | **+3958.00**（浮） | — | — |
| B5（open） | 1000×12.80+3.20=12803.20 | 期末市值 12200.00 | 100.00 | **-503.20**（浮） | — | — |
| C5（open） | 200×215.00+10.75=43010.75 | 期末市值 41000.00 | 0 | **-2010.75**（浮） | — | — |
† C4=`status=data_error`（超卖 50），**不入 closed 分母**（H-28）。

### 4.2 指标手算表（period=2026-09）
| 指标 | 公式（PRD） | 中间值 | 期望值 | 容差 | 来源 |
|---|---|---|---|---|---|
| win_rate | A10 已平仓 `pnl>0` 占比 | 8 盈 / 12 closed（盈 18020.91，亏 5514.98） | **0.66666667** | ratio 1bp | 手算 |
| win_rate_all（decomp） | A10 含浮盈亏 | 8+1=9 / 12+3=15 | **0.60000000** | ratio 1bp | 手算 |
| pnl_ratio | A11 平均盈/abs(平均亏) | 2252.61375 ÷ 1378.745 | **1.63381463** | ratio 1bp | 手算 |
| expectancy | A12 `p·W−(1−p)·L` | 0.66666667×2252.61375 − 0.33333333×1378.745 | **1042.16** CNY | 0.01 | 手算 |
| p*（decomp） | `1/(1+盈亏比)` | 1÷2.63381463 | **0.37967744** | ratio 1bp | 手算 |
| holding_days | A13 交易日均值 | 48 ÷ 12（列表 5,4,4,2,1,7,3,4,3,6,5,4） | **4.00**；median **4.0** | 精确 | 手算 |
| holding_days 自然日（decomp） | A13 并列 | 68 ÷ 12 | **5.66666667**；median **6.0** | 精确 | 手算 |
| concentration_top3 | A14 Top3 盈利/全部盈利 | 14627.75 ÷ 18020.91 | **0.81170984** | ratio 1bp | 手算 |
| concentration_CR1 | A14 CR1 | 5663.50 ÷ 18020.91 | **0.31427381** | ratio 1bp | 手算 |
| concentration_Gini | A14 Gini（盈利回合分布，HITL-10） | `2Σi·x_i/(nΣx)−(n+1)/n`，x 升序 8 项 | **0.49793802** | ratio 1bp | 手算 |
| fee_ratio | A21 费用/双边成交额 | 1250.02 ÷ 2605200.00（佣金 651.32+印花 598.70+其他 0） | **0.00047982** | ratio 1bp | 手算 |
| DE（PGR−PLR，Odean/FR-R6，**笔数口径**） | A15 | 已实现盈利回合 8、已实现亏损回合 4；期末浮盈证券 1（A6）、浮亏证券 2（B5/C5）→ PGR=8/(8+1)=**0.88888889**，PLR=4/(4+2)=**0.66666667** | **+0.22222222** | ratio 1bp | 手算（笔数口径） |
| DE（PLR−PGR，A15 字面，**笔数口径**） | A15 | 同上两值相减反向 | **-0.22222222** | ratio 1bp | 手算（方向 H-20） |
| HHI（**独立小例，非 fx_metrics_core 期望**） | A19 | 持仓 172000/12200、现金 50000，总资产 234200 | HHI_eq **0.54207904**，HHI_total **0.58765819** | ratio 1bp | 手算 |

> 笔数口径（Lead 复核修正）：DE 的“笔”=1 个回合（closed 记 realized）或 1 个期末持仓证券（open 记 paper），**不做金额加权**；PGR=盈利回合数/(盈利回合数+期末浮盈证券数)，PLR=亏损回合数/(亏损回合数+期末浮亏证券数)。本表 DE 三个值（PGR/PLR/DE）均按此定义。

**Wilson CI（A10）**：`z=1.959964`，n=12、k=8、p=2/3 → center=`(p+z²/2n)/(1+z²/n)`=0.6262509984，half=`z·sqrt(p(1−p)/n+z²/4n²)/(1+z²/n)`=0.2356289115 → **ci=[0.39062209, 0.86187991]**。注意：按 `ci_min_n=30`，n=12 **不输出** CI（§6 门槛表），该值只作为 `wilson_interval(k,n)` 纯函数 golden 与 n=30 用例的公式基准。
**MDD 小例（timing.drawdown 纯函数）**：日收益 `[0,+0.01,-0.02,-0.03,+0.01,-0.04,+0.02,+0.03,-0.01,+0.02]` → 净值 1/1/1.01/0.9898/0.960106/0.969707/0.930919/0.949537/0.978023/0.968243/0.987608 → **MDD=0.07829824**，峰值 i=2、谷底 i=6、未恢复（recovery=null）、水下 **8** 天、**Ulcer=0.03971421**。
**chase 小例**：20 根收盘 `10.00…11.20`（min=10.00,max=11.20），买入 10.85 → **(10.85−10.00)/(11.20−10.00)=0.70833333**。

### 4.3 bootstrap CI 算法小例（验证算法，不是生产 CI；生产 B=1000/seed 见 H-21）
样本 5 笔（**笔数口径**，1 笔=1 回合/持仓）：2 笔已实现盈利（`rg`）、1 笔已实现亏损（`rl`）、1 笔期末浮盈（`pg`）、1 笔期末浮亏（`pl`）→ PGR=2/(2+1)=0.66666667，PLR=1/(1+1)=0.5 → 基准 **DE=0.16666667**。重抽样：SplitMix64(`seed=20261019`)，`randbelow(5)` 用位掩码拒绝采样；每次抽 5 笔重算笔数口径 DE，**分母为 0 的 replicate 丢弃并重抽**（上限 10B）；前 20 个 replicate 的 DE：
`-0.16666667,-0.75000000,0.66666667,-0.16666667,0,-0.33333333,0.16666667,0.33333333,0,0,0,0.33333333,0.66666667,-0.75000000,-0.33333333,0.5,-0.33333333,-0.16666667,0,-0.25000000`
线性分位（`h=(B−1)q`，floor/ceil 插值）→ **p2.5=-0.75000000，p50=0.00000000，p97.5=0.66666667**（CI 含 0）。生产 CI 期望值由 `naive_metrics.py`（同一笔数口径）独立算出并冻结，测试只断言「同 seed 两次完全相等」+「与 oracle 差 ≤1bp」。
**DE(PGR/PLR) 方向冲突**：PRD A15 写 `DE=PLR−PGR`，FR-R6/Catalog 写"只卖盈不卖亏 → DE>0 且 CI 不含 0"（即 Odean `PGR−PLR`）。**本表同时给出两值**；golden 冻结前必须由人拍板（H-20），killer 断言写 `value == sign×(PGR−PLR)`，sign 来自 `definition_version` 决策。

## 5. 独立 oracle 设计（`backend/tests/oracle/`）
| 文件★ | 职责 | 独立铁律 |
|---|---|---|
| `naive_metrics.py` | perf/behavior/selection 的朴素实现：`win_rate(rounds)`、`pnl_ratio`、`holding_days`、`concentration`（含 Gini）、`fee_ratio`、`expectancy`、`de_pgr_plr`、`hhi`、`bhar` | 只 import 标准库（`decimal/dataclasses/statistics`），**禁 import `stock_core/stock_review/stock_ledger`**；自然单位（元/股/比例）直算；不复制生产结构（生产用标度整数+尾差分摊，差异天然暴露同错同消） |
| `naive_stats.py` | `wilson(k,n)`、`bootstrap_percentile(sample,B,seed,fn)`、`percentile_linear`、`ols_beta_alpha(returns,bench)`、`mdd_family(daily_returns)`、`gini` | `Decimal.sqrt()` 做 σ/t/Ulcer，**禁 float**；PRNG=SplitMix64（显式常量，不依赖 Python 版本）；回归用闭式 `cov/var` |
| `naive_fifo.py`（现有唯一 oracle） | 不动 | 现有 `backend/tests/oracle/` 只有 `naive_fifo.py`；`naive_nav/naive_xirr` 是 Testing §9.3 的规划、**尚未实现**，本期不新增（避免编造现状） |

各指标朴素实现要点：**bootstrap / DE（笔数口径）**——观测单元=1 回合（closed 记 realized 盈/亏）或 1 个期末持仓证券（open 记 paper 盈/亏），**按笔数计数、不做金额加权**；`PGR=盈利回合数/(盈利回合数+浮盈证券数)`、`PLR=亏损回合数/(亏损回合数+浮亏证券数)`，`DE=PGR−PLR`（Odean，H-20 待拍板）；重抽 n 笔，重算 DE，B=1000，种子=`int(sha256(inputs_hash)[:8],16)`，2.5/97.5 分位线性插值；**β/α/IR**——日收益对齐（同起点同日历、全收益），`β=cov(r_i,r_b)/var(r_b)`、`α_daily=r̄_i−β·r̄_b`、年化 `(1+α_daily)^244−1`、`t=α_daily/se(α_daily)`、`IR=(r̄_i−r̄_b)/σ(excess)·√244`、TE=`σ(excess)·√244`；**MDD 族**——净值序列复利链，MDD/持续期（峰→谷）/恢复期（谷→回峰）/水下占比/Ulcer=`sqrt(mean(dd²))`/Calmar；**Gini**——盈利回合 pnl 升序，`G=2Σi·x_i/(nΣx)−(n+1)/n`；**HHI**——逐日重建持仓（trades×bars，现金计入 total 版），`Σ(w_i)²`，再取区间均值/最大/期末；**BHAR**——`Π(1+r_i)−Π(1+r_b,h)`，事件日聚类 t 值（按买入日分组），PIT 严格 `trade_date+h ≤ as_of`。

三方比对落成参数化测试（`backend/tests/golden/test_metrics_three_way.py`★）：
1. `pytest.mark.parametrize("golden", load_goldens())`：`production==golden.expected`（容差走 `backend/tests/support/tolerance.py`★，**唯一容差入口**，禁字面量 0.01）。
2. 同一参数再断言 `oracle==golden.expected`（hand_calc 项则 `oracle==hand_calc`，**手算与 oracle 互证**）。
3. `production==oracle` 逐项（比率按 1bp、金额按 0.01、天数/计数精确）——三方两两独立，抓"期望值本身抄错"。
4. oracle 自检：同一输入重复两次结果全等（确定性）；oracle 模块被 `test_oracle_no_production_import`（AST）看守。

## 6. 契约 kit 断言（纯 kit=`backend/tests/contract/kit.py`★ + 连库 kit=`backend/tests/integration/test_metric_contract_db.py`★，参数化 `registry.all()`）
| 断言 | 内容 | 失败信息要求（必须含 metric_id@version + 字段 + 实际/期望 + 修复指向） |
|---|---|---|
| `assert_metric_def_valid(d)` | 13 字段齐全且类型正确；id 匹配 `^(selection\|timing\|behavior\|attribution\|perf)\.[a-z0-9_]+$`；`definition_version` 匹配 `^\d+\.\d+$`；`params_schema` 为 `BaseModel` 且 `extra="forbid"`；dependencies 引用存在、深度≤3、无环、level 合法；explain 三字段非空；drilldown endpoint ∈ OpenAPI 操作集合、params 为 endpoint 查询参数子集；sample_policy 常量取自 `contracts.constants`（禁魔数 10/30/60）；unit/precision 合法；`core_conclusion` 布尔且总数∈[8,12] | `"[perf.win_rate@1.0] 字段 sample_policy.ci_min_n=10 与 contracts.MIN_CI_SAMPLE/决策 30 不一致；阈值必须来自 constants，禁止在 def 里写魔数"` |
| `assert_purity(compute)` | AST：defs/被调 core 模块禁 import `time/datetime/random/socket/os/pathlib/sqlalchemy/fastapi/httpx/stock_platform/stock_ledger`、禁 `open/now/utcnow/time.time`；运行时 ctx 用"毒属性"桩（访问 `ctx.clock/session/random` 即抛） | `"[{id}] compute 触碰了 I/O 依赖 {symbol}（文件行号）；必须只读 ctx 注入的数据视图"` |
| `assert_deterministic(d, ctx)` | 同一 ctx 连调 2 次结果全等；打乱 `ctx.rounds/trades` 顺序结果不变（$n$、value、decomposition 全等）；两次引擎运行 `inputs_hash` 相同、`computed_at` 相同（FrozenClock） | `"[{id}] 两次运行 inputs_hash 不同：{h1}≠{h2}；禁止依赖 dict 迭代序/集合序/未声明随机"` |
| `assert_sample_gate(d, n)` | 参数化 n=0/5/9/10/29/30：见下表；同时断 `compute` 返回值**不含** `sample_status/ci_*`（阈值只能在框架） | `"[{id}] n={n} 期望 sample_status={exp} 实际={act}；检查 sample_policy 而非 compute 内分支"` |
| `assert_no_status_leak(d)` | `MetricValue` 字段集精确 ∈{value,n,decomposition,n_pending}（+已批准的 `ci_sample`）；结果对象无 status/ci/data_as_of/computed_at/inputs_hash/conclusion 键 | `"[{id}] compute 返回了框架保留字段 {field}；违反 Extensibility §3.1（FR-R8 三重保障）"` |
| `assert_drilldown_callable(d, client, acc)` | OpenAPI 中存在该 endpoint+params；本人调用 200；item key 稳定（round_id/trade_id/event_date 至少一）；金额/比率格式按 unit；租户 B 调用 items 为空或 404 且不含 A 的 id | `"[{id}] drilldown 不可调：endpoint={ep} 返回 {code}；params={params} 未被 endpoint 接受"` |
| `assert_tenant_scoped(d, engine, a, b)` | 同 fixture 同 period 下 A/B 结果分别等于各自独立期望；B 结果不含 A 的 UUID/金额；`scope_key` 含 tenant；缓存不串（先算 A 再算 B，值不变）；`review.metric_result` 直连 RLS 只看到本人 | `"[{id}] B 的结果包含 A 的 {sentinel}；scope_key 未含 tenant（跨租户缓存污染）"` |
| `assert_explain_no_jargon(d, client)` | `conclusion/reason/what_to_do` 对词表 `DE,BHAR,PGR,PLR,TWR,MWR,FIFO,HHI,Wilson,β,α,IR` 做词边界、忽略大小写扫描；命中即 fail（`method` 允许 1 个括注术语，超 1 即 fail） | `"[{id}] conclusion 含裸术语 '{term}'（字段={field}）；按 PRD 附录 C 改人话，术语只允许在口径详情页"` |

样本门槛表（`assert_sample_gate` 的数据源，FR-R8/A26；与 HITL-8 建议一致，待 H-22 拍板）：
| n / 交易日 | sample_status | value | ci_low/ci_high | conclusion | status |
|---|---|---|---|---|---|
| 0 | insufficient | **无 value 键** | 无 | null | ok |
| 1–9（5/9） | insufficient | 有 | 无 | null | ok |
| 10–29（10/29） | ok | 有 | **无**（ci_min_n=30） | 有 | ok |
| ≥30（30） | ok | 有 | **有** | 有 | ok |
| 交易日 59 | pending_window | **无**（窗口不可定义） | 无 | null | ok |
| 交易日 60 | ok | 有 | 有 | 有 | ok |
| 上游 blocked | blocked | 无 | 无 | null | blocked |

口径（Lead 裁决，H-22）：**`pending_window` 无 value 键**（A26「日频<60 交易日不出夏普/回撤/IR」）；**`insufficient`（n≥1<10）有 value、无 CI、无 conclusion**；n=0 时 value 不可定义 → 无 value（sample_status 仍为 insufficient）。

配套结构性 kit（Extensibility §11.1/§12，均为新增测试）：`test_metric_discovery_is_decoupled`（把 def 放进测试专用 defs 路径即被 registry 发现、kit 跑到、`GET /metrics?ids=` 可见——"加文件不改框架"的证明）、`test_metric_core_conclusion_budget`（`core_conclusion` 总数 ∈[8,12]）、`test_metric_depth_cap`（依赖深度 ≤3）、`test_no_wildcard_plugin_loading`（禁 `importlib` 扫外部路径）、`test_change_scope`（新增指标 PR 的 diff 只允许 `defs/`+`core/metrics|stats/`+fixture，改到 `engine.py`/DTO 即 fail）。

## 7. API 验收矩阵（API-033/034/035/038/079，每端点 ≥3 条 + 关键断言）
| 端点 | 用例（happy/越权/错误码分开计数） | 关键断言 |
|---|---|---|
| API-033 `GET /metrics` | ① happy `ids=perf.win_rate,perf.pnl_ratio&period=2026-09` → 200；② `domain=behavior` 过滤 + `include_exploratory=false` 不含 exploratory；③ fx_n5 → insufficient + 有 value 无 CI；④ fx_blocked → `timing.drawdown` 无 value 键（同响应 `win_rate` 仍 ok=部分阻断）；⑤ fx_n30 → `ci_low<value<ci_high`；⑥ **fx_bars_missing → `timing.beta`/`timing.chase` 为 `sample_status=pending_window` 且无 value 键、无 CI**；⑦ 越权：B token 同请求只返回 B 数据、body 无 A 的 UUID/金额；⑧ admin → 403 `ADMIN_CANNOT_READ_TRADES`；read PAT 200、无 token 401；⑨ 错误码：`ids`+`domain` 同传→422、未知 id→404 `METRIC_NOT_FOUND`、`period=2026-13`→422 `INVALID_PERIOD`；⑩ `ETag: W/"…"` + `If-None-Match`→304 空 body；⑪ 每 item 含 `definition_version="1.0"`、`data_as_of`、`assumptions{benchmark_id,rf_rate,A:244}`、`inputs_hash` | flat id（`win_rate`）按 HITL-5 决议：本期应为 404（Catalog 断言①须改，H-24）；`blocked` 项**无 value 键**（不是 null/0）；`insufficient` 项**无 ci_* 键** |
| API-034 `GET /metrics/{id}/explain` | ① happy：conclusion/method/drilldown 齐全；② fx_n5 → conclusion=null + reason/what_to_do 非空；③ 越权：无 token 401、admin 403；B 调同 id 时 conclusion/reason 不含 A 的金额/哨兵（数据无关文案可以相同）；④ 错误码：未知 metric 404、缺 `period` 422、非法 period 422；⑤ `Accept-Language=en-US` 文案变、数值/code 不变；⑥ 裸术语扫描（§6 词表）0 命中；⑦ `drilldown.endpoint+params` 直接调用→200 | 非 ok 时 `conclusion=null`；`drilldown` 是可执行声明（API-035 断言改为"可调且结构合法"，items 可空，H-24） |
| API-035 `GET /metrics/{id}/drilldown` | ① happy `period=2026-09` → items 非空、每项含稳定 key+value+unit+label、`params` 回填 metric_id/period、`source_endpoints[]` 逐个可调 200；② 格式按 unit：金额 `^-?\d+\.\d{2}$`、比率 `^-?\d+\.\d{8}$`、天数 `^\d+$`（**比率指标不得套金额断言**，HITL-15）；③ 分页 `limit/cursor` 无重无漏、非法 cursor→400 `INVALID_CURSOR`；④ 越权：B 的 items 为空或 404，绝无 A 的 round_id/trade_id；admin 403、无 token 401；⑤ 错误码：未知 metric 404、缺 period 422；⑥ 过滤 `round_id`/`security_code` 收窄；不存在 round_id→404 | 每项 `trade_id/round_id` 必须能在本人 `GET /trades`/`GET /rounds` 查到（下钻可复核）；比率型指标 item value 不得按 2 位断言 |
| API-079 `GET /metrics/{id}` | ① happy：单指标完整 MetricResult（含 `definition_version/inputs_hash/data_as_of/computed_at/assumptions`）；② blocked 无 value；③ insufficient 无 CI；④ 与 API-033 同 period 的同一 item **逐字段相等**（批量/单查一致性）；⑤ `ETag`/304；两次调用 `inputs_hash` 相同，改一条流水后 `inputs_hash` 变且 `status` 可能 stale；⑥ 越权/错误码同 033（404/422/401/403）；⑦ admin 403 | `status/ci/value` 出现规则与 §6 门槛表逐格一致（本端点最容易漏 `data_as_of`） |
| API-038 `POST /diagnostics/selection` | ① happy：202 + `Location` + `type="diagnostics_selection"`；drain 后 `GET /metrics?domain=selection&period=` 返回 bhar_20/60 的 mean/median/t/n/sample_status；② 幂等：同 key 同 body 重放→同 job_id + `Idempotency-Replayed`；③ 同参异 key 并发→`X-Job-Reused` 同 job；异参→409 `JOB_ALREADY_RUNNING`；④ PIT：`as_of=2026-09-15` → `data_as_of≤as_of`，换 future 快照后 BHAR 不变（fx_pit_asof）；⑤ 越权：跨租户 job 404、admin 403、read PAT→403 `INSUFFICIENT_SCOPE`；⑥ 错误码：缺 `Idempotency-Key`→422、非法 period→422；⑦ fx_selection_no_event → 202→succeeded 但指标 n=0 无 value（不得 500）；⑧ `as_of` 财报断言删除（无财报数据源，HITL-4） | 选股域无财报数据 → Catalog 断言③"不含更晚财报"本期不可达，改为"日线 PIT + data_as_of≤as_of" |

**Catalog §6 需修订点（H-24/HITL-5/HITL-15/HITL-4）**：① flat id → dotted id（`win_rate`→`perf.win_rate`，`de`→`behavior.de`，`bhar_20`→`selection.bhar_20`）；② API-033 断言① n==12 的 fixture 由 fx_round30 改为 `fx_metrics_core`（fx_round30 命名暗示 30 回合，与 n=12 矛盾）；③ API-033 ② 的 `nav_return` 不在 18 指标目录 → 改用 `timing.drawdown`/`perf.fee_breakeven` 作 blocked 示例（或补指标，另立）；④ API-035 断言②"金额 2 位"改为按 unit 分派；⑤ API-034 断言③"items 非空"改为"可调且结构合法"；⑥ API-079 补 `ETag/304` 与单查-批量一致性断言；⑦ API-038 删"不含更晚财报"、加 PIT 日线断言。

## 8. 隔离与越权
| 项 | 断言 | 落地 |
|---|---|---|
| 两租户同 fixture | A 与 B（诱饵：1 trade + 1 round，`user-b@example.test`）各跑同一 period，值分别等于各自独立期望；B 响应扫描 A 的哨兵 UUID/金额/证券名，**零命中** | `test_isolation_matrix.py` 扩 `LIST_ISOLATION_MATRIX += ("GET","/metrics")`；新 `backend/tests/support/tenant_assert.py`★ `assert_tenant_invisible` |
| 跨用户 metric/drilldown | `GET /metrics/{id}`、`/explain`、`/drilldown` 的 `metric_id` 是**全局注册表键**（非租户资源）→ 不适用 404；隔离断言改为「响应内容不含他人数据 + B 无数据时 items=[]」；**禁止**因此把它从越权矩阵剔除 | `scripts/ci/gate_tenant_coverage.py` 的 `EXEMPT` 表新增 3 条并写原因（测试工具改动，非生产代码） |
| admin 403 / 401 / scope | admin 调 5 个指标端点 → 403 `ADMIN_CANNOT_READ_TRADES`（与 assets/rounds 一致，H-32）；无 token → 401 + `WWW-Authenticate`；read PAT 可读、read PAT 调 API-038 → 403 `INSUFFICIENT_SCOPE`、write PAT 可调 | `ADMIN_FORBIDDEN` / `UNAUTHENTICATED_MATRIX` 增行；`test_api_038_selection.py::test_read_pat_cannot_post` |
| RLS 直连 | Period2-05 新增 `review.metric_result`（若建）自动纳入现有 `test_every_tenant_table_has_tenant_policy`（读 `information_schema`）；直连断言 `SET LOCAL app.user_id=A` 只看到 A、不设变量 0 行、伪造成 B 的 INSERT 被 `WITH CHECK` 拒 | `backend/tests/integration/test_metrics_rls.py`★；**注意**：`backend/tests/conftest.py::_REVIEW_TABLES` 必须补新表名，否则跨用例脏数据（本期无表则记为前置依赖） |
| 门禁自动纳入 | `gate_endpoint_coverage.py` 要求 API-NNN 在 `backend/tests/api|isolation` 被引用 ≥3 条（新增测试文件即满足）；`gate_tenant_coverage.py` 要求每个受保护 operation 在 4 张矩阵出现（metrics 增行）；`gate_rls_coverage.py` 按 `information_schema` 自动覆盖新表 | `make verify-p2` 串起来（§11） |

## 9. 变异测试
配置（根 `pyproject.toml`，`mutmut>=3.2,<4` 新增 dev 依赖；**若版本为 2.x，键名改为 `paths_to_mutate/runner`，见 H-30**）：
```toml
[tool.mutmut]
paths_to_mutate = ["backend/packages/core/src/stock_core/metrics", "backend/packages/core/src/stock_core/stats"]
tests_dir = ["backend/tests/unit/core", "backend/tests/golden"]
runner = ".venv/bin/python -m pytest -x -q -p no:cacheprovider"
also_copy = ["backend/tests/fixtures/metrics"]
```
门槛：新增 `stock_core/metrics/**`、`stock_core/stats/**` **≥80%**；`stats/ci.py`、`metrics/tolerance.py` ≥95%；**现有 core（values/fifo/nav）不降低**（仍由 `make test-core` 100%/100% 看守，变异门槛沿用 Testing §12.1 的 90–100%）。`make mutation`=只跑 `git diff --name-only` 命中的 mutator（PR，≤10min）；`make mutation-full`=nightly 全量（≤40min）；`scripts/ci/gate_mutation_score.py`★ 读 `mutation.json`（每文件 score + 总 score），`<80%` 或环比降 >2pp → exit 1；脚本自带元测试（喂假 score 必 fail）。release 阻断。
下表杀手测试**全部为本期新增**（沿用 `backend/tests/unit/core/` 既有 `test_*.py` 命名约定，未实现前不得被当作既有用例引用）。

| 指标 | 必杀算子 | 杀手测试 |
|---|---|---|
| perf.win_rate | `>`↔`>=`（pnl>0）；分母 `n`/`n−1`；open 计入/不计入 | `test_metrics_win_rate::test_excludes_zero_pnl`、`test_win_rate_all_includes_open` |
| perf.pnl_ratio | 分子/分母交换；`abs(平均亏)` 去 abs；全亏时除零 | `test_pnl_ratio_all_loss_returns_insufficient` |
| perf.holding_days | `<`↔`<=`（同日记 1）；自然日 vs 交易日；median 取错 | `test_holding_days_same_day_is_one`、`test_holding_days_weighted_by_qty` |
| perf.concentration | 分母换成全部回合；Top3 取最小 3 个；Gini i 从 0 起 | `test_concentration_denominator_is_positive_pnl_only`、`test_gini_matches_handcalc` |
| perf.fee | 费用二次扣减；分母用单边；成本加费 vs 不加费 | `test_fee_ratio_no_double_charge`、`test_fee_matches_handcalc` |
| perf.fee_breakeven | 分母 ≤0 未判 unreliable；年化 244→252 | `test_fee_breakeven_nonpositive_twr_unreliable`、`test_a244_constant` |
| perf.expectancy | 符号翻转 `p·W+(1−p)·L`；p 用全样本 | `test_expectancy_sign`、`test_p_star_boundary` |
| timing.chase | `max−min` 分母为 0 未判；窗口含 T 日（应为 T−20..T−1） | `test_chase_flat_window_unreliable`、`test_chase_window_excludes_today` |
| timing.fly | `z>1.0`↔`>=`；σ 用 √h 漏乘；h=5/10/20 写死 20 | `test_fly_threshold_boundary`、`test_fly_pending_excluded` |
| timing.beta | β 分子分母互换；交易日 `<60` 判成 `<=60`；α 年化漏复合 | `test_beta_boundary_59_60`、`test_alpha_annualized_vs_handcalc` |
| timing.drawdown | 回撤分母用谷底；峰谷指针初值错；Ulcer 漏 sqrt | `test_mdd_handcalc_mini`、`test_mdd_peak_trough_indices` |
| behavior.de | **符号翻转**（PLR−PGR ↔ PGR−PLR，H-20）；bootstrap 种子里写死时间；重抽分母 0 未处理 | `test_de_direction_contract`、`test_bootstrap_seed_from_inputs_hash` |
| behavior.turnover | 平均市值用期初或期末单点；分母用平均总资产 | `test_turnover_avg_market_value` |
| behavior.averaging_down | 比较价漏费用；`<`↔`<=`；补仓误判为摊平 | `test_averaging_down_includes_fees` |
| behavior.hhi | `w` 未先归一；现金漏入 HHI_eq；Σ 后未平方 | `test_hhi_toy_handcalc`、`test_hhi_total_includes_cash` |
| selection.bhar_20/60 | 几何链写成算术和；基准与个股日期错配；窗口少一天 | `test_bhar_geometric_not_arithmetic`、`test_bhar_window_days` |
| selection.bhar_hit | `>`↔`>=`（BHAR>0）；n=0 未判 blocked | `test_bhar_hit_zero_sample_blocked` |

## 10. 性能与统计可信度
- **Pf-2 批量指标（20 指标 × 12 期 ≤300ms）**：`backend/tests/perf/test_metrics_batch_p95.py`★ 用 fixture 构造 12 期×~50 回合的内存 DatasetView（不连库热循环），预热 10 次后跑 50 次，断言 `p95 ≤ 300ms`、批内 SQL 计数 = 0（计数探针）；失败输出分位与最慢指标。预算与 Testing Pf-2 对齐；环境只用测试机 CPU 基线，不阻塞 PR（release 2× 阻断）。
- **HHI 内存重建上限**：要求实现**逐日流式**聚合（每日持仓映射 → 权重平方 → 累加），不得保留全历史每日持仓列表；测试 `tracemalloc` 断言 750 交易日 × 200 标的场景峰值 <16MB，且 250 日场景 <8MB；超预算 → 记 warning 并强制走"逐日流式"评审（口径上限与降级见 H-26）。
- **回归微基准**：`test_core_microbench.py`★ 对 18 个纯函数各跑 N 次取中位数，基线 `backend/tests/perf/baseline/core.json`（只允许审批 PR 更新）；>2× 阻断 PR-full（Testing Pf-10）。用 stdlib `time.perf_counter`（**不新增 pytest-benchmark 依赖**，CI 更稳）。
- **统计可信度补充**：bootstrap 两次运行逐位相等；`B=1000` 下 CI 宽度随 n 单调不增（属性测试）；Wilson 与朴素二项式近似差 <0.02（n=30）；所有 CI 只在 `n≥30` 出现（门槛表）。

## 11. 门禁与执行计划
`make verify-p2`★（新增；**保留现有 `test-p2` 不动**，`verify-p2` 在其后追加）建议 11 步：
1. `pytest backend/tests/unit/core --cov=stock_core --cov-fail-under=100`（L0，含新 metrics/stats 模块 100% 行/分支）
2. `pytest backend/tests/golden -m golden`（L1 三方：手算/oracle/生产）
3. `pytest backend/tests/contract/test_metric_plugin.py`（L2 kit × registry）
4. `pytest backend/tests/api -k "api_033 or api_034 or api_035 or api_038 or api_079"`（L3 五端点）
5. `pytest -m isolation` + `gate_tenant_coverage.py` + `gate_rls_coverage.py`（L4）
6. `gate_endpoint_coverage.py`（端点 100% + 每端 ≥3 用例）
7. `python -m stock_api.cli api-check` + `api-snapshot --check`（契约漂移）
8. `gate_golden_approval.py`（MANIFEST sha256 + `approved=true` + `definition_version` 一致性）
9. `gate_mutation_score.py --min 80 --diff`（PR 用）
10. `pytest backend/tests/perf -m perf -k microbench`（Pf-10 微基准）
11. `pytest backend/tests/architecture`（import-linter / no-wall-clock / no-float）
golden 审批流程：口径负责人 + 1 reviewer 在**独立 PR** 贴 diff（金额到分/比率到 bp）→ 打 `golden-approved` label → `review.approved=true/approved_at/reviewer` 落 JSON → `gate_golden_approval.py` 校验；`definition_version` 未升而快照变 → fail。fixture 修改需 ≥2 reviewer（含口径负责人）+ MANIFEST（Testing §12.3）。
flaky 规则：metrics golden/越权用例 **0 容差**——禁 `skip/xfail/quarantine/sleep/rerun`；`gate_no_security_skip.py`★（新建，扫描 `api/isolation/golden` 三目录）判红；nightly 把 L1 连跑 3 次比对（"三跑一致"），不一致即 blocker；超时/时区类失败一律视为产品缺陷。
CI 分层（无 `.github/workflows`，本期新建 `.github/workflows/p2-metrics.yml`★）：PR-fast ≤5min（步骤 1/2/3/7/11，无 DB）；PR-full ≤20min（全量 1–11 + 5 端点 API + isolation + RLS）；nightly（`make mutation-full` + Pf-2 + PIT/fixture integrity + L1 三跑）。
实现期测试执行顺序与工作量（人日，到 0.1）：夹具 0.8（core+bars+10 变体+loader+MANIFEST）→ 手算 golden 0.6（18 份，9 份手算已在本文件 §4）→ oracle 0.6（naive_metrics/naive_stats）→ kit 0.3 → API 验收 0.5（5 端点，~26 用例）→ 隔离/门禁脚本 0.3 → 变异配置与 killer 0.4 → 性能 0.2 → CI/verify-p2 0.2 → **合计 3.9 人日**（与 HITL-6 的 3.5 人日冲突，见 H-31；建议按 HITL-6 缩到 12 核心指标或延长）。

## 12. HITL（新增，编号续 H-19，与 brief HITL-1..18 不冲突）
| 编号 | 问题 | 推荐取向 |
|---|---|---|
| H-19 | golden 目录双源：Testing §9.1 `fixtures/expected/metrics/` vs §9.3/Extensibility §12 `golden/<metric_id>/` | 统一 `backend/tests/golden/<metric_id>/`；Testing §9.1 勘误 |
| H-20 | **DE 方向矛盾**：PRD A15 `DE=PLR−PGR`，FR-R6/Catalog 断言"只卖盈不卖亏→DE>0"（Odean `PGR−PLR`） | 走 Odean/FR-R6（`PGR−PLR`）并勘误 A15；否则 Catalog/FR-R6 断言必须反向。**阻塞 behavior.de golden 冻结** |
| H-21 | bootstrap CI 的观测样本不在冻结 `MetricValue` 契约里（compute 只返 value/n/decomposition） | `MetricValue` 增加内部 `ci_sample`（不出口 API）并补契约文档；否则 CI 只能由框架重算=口径双写 |
| H-22 | `insufficient`/`pending_window` 是否返回值/CI/conclusion：brief §2.1、Catalog API-033③、Architecture §11 三方不完全一致；`MIN_CI_SAMPLE=10` vs `ci_min_n=30` | 采用 §6 两级门槛表：**`pending_window` 无 value 键**（A26 明说日频<60 交易日不出回撤/IR）；**`insufficient`（n≥1<10）有 value、无 CI、无 conclusion**；CI 仅 n≥30 |
| H-23 | Catalog API-033② 的 `nav_return` 不在 18 指标目录；API-033① n==12 却引用 `fx_round30` | 用 `timing.drawdown`/`perf.fee_breakeven` 作 blocked 示例；fixture 改名 `fx_metrics_core` |
| H-24 | Catalog 断言需修订：flat id、API-035 金额格式、API-034"items 非空"、API-038 财报 PIT、API-079 缺 ETag | 见 §7 末尾修订清单（7 条） |
| H-25 | `fx_*` fixture 在 Catalog 只有名字、无定义；n=0 是否 `blocked` 还是 `insufficient` 未定 | 本文件 §3 即规范；n=0 定 `insufficient` 且无 value |
| H-26 | 换手/费用门槛的"平均持仓市值"口径（期初期末均值 vs 逐日均值）；HHI 重建内存上限 | 逐日市值均值；HHI 流式实现（§10 预算），超限降级 unreliable |
| H-27 | 红利税归属：A21 三子项是否含红利税；parser 约定 DIVIDEND 已税后、税行独立 → 毛股息+税行会双计 | 账单只给税后净额（本期 fixture 不放税行）；若给毛+税，engine 需按税行冲减 |
| H-28 | data_error 回合是否进入 win_rate/win_rate_all/fee_ratio/DE 分母 | 一律排除并单列 `n_data_error`（decomp）；golden 显式断言 |
| H-29 | period 语义：perf/behavior 按 close_date、fee/turnover 按 trade_date、timing 按收益区间；跨期回合归属未定 | 按本文件口径（round=close_date，trade=trade_date），补跨期参数化用例 |
| H-30 | 变异门禁：文档路径 `core/src/core` 实为 `backend/packages/core/src/stock_core`；mutmut 2.x/3.x 配置键不同；工具未引入 | pin `mutmut>=3.2,<4`，路径按实际；threshold 脚本自带元测试 |
| H-31 | 18 指标 golden+变异 ≥80% 在 3.5 人日内不可达（测试工作量估 3.9 人日，HITL-6 延续） | 缩到 12 核心指标全做、其余 exploratory 仅 L0+oracle；或延长工期 |
| H-32 | admin 对指标端点是否 403：admin 不可读交易明细，但指标是聚合值；Catalog API-033 错误码未列 `ADMIN_CANNOT_READ_TRADES` | 403（与 assets/rounds 一致），Catalog 补错误码 |

## 给 Lead 的合并提示（≤15 行）
1. 决策摘要必须写进：**H-20（DE 方向）**、H-22（insufficient/pending_window 两级门槛表）、H-21（bootstrap 样本进 MetricValue 内部字段）、H-19（golden 目录单源）、H-24（Catalog 7 处修订）。
2. 手算表（§4）已独立可复算：win_rate 0.66666667、win_rate_all 0.6、pnl_ratio 1.63381463、holding_days 4.0、top3 0.81170984/CR1 0.31427381/Gini 0.49793802、fee_ratio 0.00047982、expectancy 1042.16、**DE 笔数口径 PGR 0.88888889 / PLR 0.66666667 / DE ±0.22222222**、Wilson [0.39062209,0.86187991]；夹具 32 行流水余额链闭合。
3. 夹具清单已完整（core/bars_min/n5/n30/de_pos/no_de/blocked/broken_chain/bars_missing/pit_asof/selection_no_event/multilot/cash_only/cohort_sel/cohort_fly），**均新增、不依赖 demo 的 9 点月频行情**；需在 MANIFEST 落 sha256。
4. 与 05/06 耦合：`registry.all()`、`MetricValue` 字段、`ctx` 数据视图名、`review.metric_result` RLS + conftest TRUNCATE 清单；07 不实现引擎，测试按冻结形状先行（HITL-1）。
5. 与 perf/timing/behavior_selection 三个 part 的接口：本文 §2.2 的 fixture/容差表即它们的 golden 契约，差异以本文件为准；DE 一律**笔数口径**（与 behavior part 对齐），其他 HITL 引用本文 H-19..H-32 或 Lead 重排后的 HX-*。
6. 新增文件均在 §1/§5/§9–§11 标注★（golden/、kit.py、fixtures/metrics/、perf/、workflow、脚本）；现有 `test-p2` 保留，`verify-p2` 追加。
7. 最高风险三条：H-20（指标语义反向）、H-21（CI 无法由框架代算）、H-28（data_error 分母歧义影响 4 份 golden）。
8. 编号说明：本文 **H-20 = DE 方向（A15 与 FR-R6 冲突）**，与 behavior part 的 H-19/H-20（bootstrap 交接/抽样单元）不是同一问题；final doc 重排 HX-* 时请勿按编号误合并。
