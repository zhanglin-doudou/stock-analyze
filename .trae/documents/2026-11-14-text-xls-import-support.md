# 支持制表符文本型 `.xls` 对账单

## Context

用户提供的 `20261112 对账单查询06.xls` 需要能够预览并导入。实测该文件并非传统 BIFF/OLE Excel，而是 **GBK 编码、CRLF、TAB 分隔文本**，共 22 列、47 条数据；字符串单元格使用 `="..."` 包装，证券代码等前导零必须保留。

当前 [table.py](file:///Users/bean/Documents/AI/stock-analyze/backend/packages/ledger/src/stock_ledger/parsers/table.py) 对所有 `.xls` 无条件调用 `xlrd.open_workbook`，原文件实测抛出 `XLRDError: Expected BOF record`。legacy 与 v2 最终都复用这里的 `read_rows`，因此可在同一读取点修复两条链路。

## 实施方案

1. **在共享读表层增加严格的文本型 XLS 分支**
   - 修改 `backend/packages/ledger/src/stock_ledger/parsers/table.py`。
   - `.xls` 输入先尝试严格 UTF-8-sig / GBK 解码；仅当解码成功、无 NUL、首行含 TAB 时，按 TAB 文本读取。
   - 不满足条件时保持原有 `xlrd` 路径，不把任意损坏文件当文本吞掉，也不改变 `.csv` / `.xlsx` 行为。
   - 复用现有 `csv.reader`、`normalize_cell` 和后续 `inspect_table` / 映射 / 解析流程，不新增依赖。

2. **只在该兼容分支解除 Excel 文本包装**
   - 对完整匹配的 `="..."` 去掉外层 `="` 与末尾 `"`，不执行公式、不做全局字符串替换。
   - 裸数字仍交现有 `Decimal → 标度整数` 逻辑；`="000650"` 必须解析为字符串 `000650`，禁止经 float。
   - `发生金额` 继续按现有 `amount_signed=True` 口径处理，避免含费净现金流被重复扣费。

3. **增加脱敏、确定性的永久回归**
   - 修改 `backend/tests/unit/ledger/test_import_mapping.py`，在测试内构造 22 列 GBK/CRLF/TAB 合成样本，使用伪账号、伪代码和重造金额。
   - 覆盖：表头定位与别名建议、字符串去壳、前导零、带符号金额、legacy 与 plan/v2 解析一致性；并证明文本分支不会调用 `xlrd`，非文本 `.xls` 仍走原路径。
   - 真实上传件不入仓、不复制为公开 fixture，不在断言失败输出中打印账号或完整原始行。

4. **用用户原文件做 opt-in 端到端导入测试**
   - 在 `backend/tests/api/test_import_mapping_v2.py` 增加默认 SKIP、由 `REAL_XLS` 显式指定的真实文件测试。
   - 在隔离测试库执行：API-010 v2 preview → API-098 映射（`If-Match`）→ API-099 逐类型确认 → API-011 commit → drain worker → 批次详情/隔离区核对。
   - 对 10 类业务使用明确语义分类；未知或噪音不默认归入 `other`、不默认丢弃。未映射的委托/订单等辅助列需显式忽略并二次确认。
   - 核对 22 列、47 行及 parsed/inserted/skipped/quarantined/discarded 的实际关系，不预设全部 47 行都必须入账。若暴露的是新业务语义而非读表问题，只为明确失败场景补最小规则和测试，不做宽泛放行。

5. **边界**
   - 不改 API 契约、OpenAPI/SDK、数据库迁移、前端和既有 M1–M5 无关改动。
   - 不创建 git commit；不修改上传原件。
   - 自动化数据库测试只使用 `STOCK_DB_PORT=5433`。

## 验证

```bash
STOCK_DB_PORT=5433 uv run pytest backend/tests/unit/ledger/test_import_mapping.py backend/tests/api/test_import_mapping_v2.py -q
STOCK_DB_PORT=5433 REAL_XLS='/absolute/path/20261112 对账单查询06.xls' uv run pytest backend/tests/api/test_import_mapping_v2.py -k real_xls -q
STOCK_DB_PORT=5433 make test-p2
make lint
make typecheck
```

最终报告真实文件的预览列数/行数、类型确认情况、提交批次状态与各类计数；默认套件中的真实文件测试必须明确显示 SKIP，不能把它计入已自动覆盖的固定样本。
