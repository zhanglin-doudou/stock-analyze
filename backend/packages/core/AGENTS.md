# AGENTS.md — stock_core（纯计算内核）

**前置**：必读根目录 `AGENTS.md`。本文件只补 core 层的特有约定。

## 定位

stock_core 是纯计算内核：**零 I/O、零 wall-clock、零全局态**。只负责标度整数（Money/Price/Qty/Ratio）和确定性业务逻辑；不碰 DB、HTTP、文件、上下文管理。依赖只有 `stock_contracts`（Clock 端口 + 常量）。

## 规则速查

### 1. Scaled 族用法：内部 int，边界定点字符串

```python
# DO — 所有计算在 int 域，只有边界才转字符串
from stock_core.values import Money, Price, Qty

trade = {
    "price_units": 1_500_000,    # 价格 1.500000 元（scale=6）
    "quantity_units": 100_0000,  # 数量 100 股（scale=4）
}
amount = Money(trade["price_units"] * trade["quantity_units"] // 10_000_000)  # 全 int 运算
resp.price = str(Price(trade["price_units"]))   # 边界才定点字符串
resp.amount = str(amount)
```

### 2. 严禁 float 构造 Scaled 值

```python
# DON'T — float 精度损失
price = Price.from_decimal(1.5)    # float 1.5 → 二进制不可精确表示

# DO — 字符串 / Decimal / int
price = Price.from_str("1.500000")          # 定点字符串（推荐，API 边界输入）
price = Price.from_decimal(Decimal("1.5"))  # Decimal（内部构造）
price = Price(1_500_000)                    # 直接标度整数（ORM 列读出）
```
`Scaled.from_decimal` 内部有 float 防御分支（`isinstance(raw, float)` → raise TypeError），运行时直接拒绝。

### 3. Scaled 子类 scale 由 `scale` 类属性声明

```python
class Money(Scaled):
    scale = 2

class Price(Scaled):
    scale = 6

class Qty(Scaled):
    scale = 4

class Ratio(Scaled):
    scale = 8
```
新增标度类型必须同时：(a) 加 `scale` 类属性；(b) 在 `_QUANT` 字典里补对应 scale 的 `Decimal` 精度；(c) 加单元测试覆盖 `from_decimal` / `__str__` 两端。

### 4. 零 wall-clock：任何需要时间的函数必须收 `Clock`

```python
# DON'T
def classify(self) -> None:
    today = datetime.now(tz=UTC)   # 核心计算不能读系统时间

# DO — Clock 作为参数传入
def classify(self, *, clock: Clock) -> None:
    today = clock.today()
```

### 5. domain 逻辑放 core，IO 放 ledger

```
core        确定性计算 / 标度运算 / 纯函数逻辑（零 Session / 零 HTTP）
ledger      用 core 的纯函数 + Session 做持久化、仓储查询、解析器
platform    DB 连接池 / 配置 / JobQueue / 幂等（为 ledger 和 api 提供运行时能力）
```
边界判断：函数签名里出现 `AsyncSession` / `requests` / `contextvars` → 放 ledger 或 platform，不是 core。

### 6. 类级注解 `units: int`（mypy strict 必需）

```python
class Scaled:
    __slots__ = ("units",)
    units: int                    # ← 必须加：__slots__ 里的实例属性 mypy 默认看不到
    scale: int = 0
```
这不是风格洁癖——不加的话 mypy strict 模式会报 `Scaled 没有 units 属性`。

### 7. 覆盖门禁：core 单测必须 100% 覆盖

```bash
make test-core     # pytest --cov=stock_core --cov-fail-under=100
```
新增函数必须同时加测试；遗漏分支用 `# pragma: no cover` 标记只能用于真正不可能走到的防御代码。

## 文件清单

| 文件 | 职责 |
|---|---|
| `values.py` | Scaled 基类 + Money(2) / Price(6) / Qty(4) / Ratio(8) 四个子类 |
| `clock.py` | 从 contracts 再导出 Clock / FrozenClock / SystemClock（三行 re-export） |
| `__init__.py` | 显式 re-export 公共 API |
