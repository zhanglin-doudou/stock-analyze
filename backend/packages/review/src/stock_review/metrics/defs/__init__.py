"""生产指标 def 包（Period2-07 交付）。

05 只提供引擎与注册表；本包暂时为空。additive 扩展方式：放一个 `<name>.py`，
模块级导出 `DEF = <MetricDef 实例>` 即被 `registry.build_registry()` 发现，
**不需要修改任何既有文件**（X-1 判据）。
"""

from __future__ import annotations
