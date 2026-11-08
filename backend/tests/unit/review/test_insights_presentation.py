"""回合洞察展示层：卖飞金额符号 → 措辞，以及证券类型筛选口径。"""

from __future__ import annotations

from stock_review.insights.service import (
    SECURITY_TYPES,
    _missed_presentation,
    _security_type,
)


def test_missed_presentation_positive_is_missed_gain() -> None:
    """正数 = 本该赚到却没赚到 → 少赚，展示绝对值串。"""
    assert _missed_presentation(5_206_21) == ("5206.21", "少赚", "missed gain")


def test_missed_presentation_negative_is_extra_gain() -> None:
    """负数 = 卖后继续下跌、卖出反而多赚 → 多赚，展示绝对值串。"""
    assert _missed_presentation(-21_965_03) == ("21965.03", "多赚", "extra gain")


def test_missed_presentation_zero_and_none() -> None:
    """零归入「少赚」侧；None 三项全空（前端只渲染「—」，不拼措辞）。"""
    assert _missed_presentation(0) == ("0.00", "少赚", "missed gain")
    assert _missed_presentation(None) == (None, None, None)


def test_security_type_classification() -> None:
    """代码区段是单一真相；货基靠流水标记/名称兜底，其它债券归入 other。"""
    assert _security_type("600519", "贵州茅台", set()) == "stock"
    assert _security_type("159892", "恒生生物", set()) == "fund"
    assert _security_type("113708", "曙26转债", set()) == "convertible_bond"
    assert _security_type("019547", "16国债19", set()) == "other"
    assert _security_type("940018", "华泰紫金天天发货币", set()) == "fund"
    assert _security_type("940018", "天天发", {"940018"}) == "fund"
    assert _security_type("754019", "曙26发债", set()) == "other"


def test_security_type_covers_every_symbol() -> None:
    """完备性：任何标的都必须落在四类之一，不能因分类不出来而从筛选项消失。"""
    codes = (
        "600519",  # 沪市股票
        "002475",  # 深市股票
        "300009",  # 创业板
        "688181",  # 科创板
        "159892",  # 场内基金
        "513330",  # 沪市基金
        "113708",  # 可转债
        "019547",  # 国债
        "940018",  # 货币基金（代码区段外）
        "754019",  # 新债申购代码
        "CASH",  # 资金流水行
    )
    for code in codes:
        assert _security_type(code, code, set()) in SECURITY_TYPES
