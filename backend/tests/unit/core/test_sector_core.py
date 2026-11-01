"""板块纯函数单测（覆盖全部分支；`make test-core` 要求 stock_core 100% 覆盖）。"""

from __future__ import annotations

import pytest
from stock_core.sector import (
    STATUS_DEGRADED,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    SectorNode,
    active_weights,
    concept_exposure,
    coverage_status,
    deviation_units,
    effective_n_units,
    normalize_weights,
    resolve_level,
    sector_hhi_from_values,
    sum_by_sector,
    topk_weights,
    weights_from_values,
)

SCALE = 8


def _catalog() -> dict[str, SectorNode]:
    return {
        "801030.SI": SectorNode("801030.SI", 1, None),
        "801031.SI": SectorNode("801031.SI", 2, "801030.SI"),
        "850101.SI": SectorNode("850101.SI", 3, "801031.SI"),
    }


class TestResolveLevel:
    def test_rolls_up_to_level_one(self) -> None:
        assert resolve_level({"A": "850101.SI"}, _catalog(), 1) == {"A": "801030.SI"}

    def test_keeps_same_level(self) -> None:
        assert resolve_level({"A": "850101.SI"}, _catalog(), 3) == {"A": "850101.SI"}

    def test_negative_level_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_level({}, _catalog(), -1)

    def test_unknown_code_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_level({"A": "nope"}, _catalog(), 1)

    def test_cannot_drill_deeper_than_node(self) -> None:
        with pytest.raises(ValueError):
            resolve_level({"A": "801030.SI"}, _catalog(), 3)

    def test_missing_parent_rejected(self) -> None:
        catalog = {"850101.SI": SectorNode("850101.SI", 3, "801031.SI")}
        with pytest.raises(ValueError):
            resolve_level({"A": "850101.SI"}, catalog, 1)

    def test_root_without_parent_rejected(self) -> None:
        catalog = {"850101.SI": SectorNode("850101.SI", 3, None)}
        with pytest.raises(ValueError):
            resolve_level({"A": "850101.SI"}, catalog, 1)

    def test_broken_parent_chain_rejected(self) -> None:
        catalog = {
            "850101.SI": SectorNode("850101.SI", 3, "801030.SI"),
            "801030.SI": SectorNode("801030.SI", 1, None),
        }
        with pytest.raises(ValueError):
            resolve_level({"A": "850101.SI"}, catalog, 1)


class TestSumBySector:
    def test_sums_mapped_symbols_only(self) -> None:
        assert sum_by_sector({"A": "x", "B": "x", "C": "y"}, {"A": 50, "B": 30}) == {"x": 80}

    def test_empty(self) -> None:
        assert sum_by_sector({}, {}) == {}


class TestWeights:
    def test_weights_sum_one(self) -> None:
        assert weights_from_values([80, 10, 6], 96) == [83333333, 10416667, 6250000]

    def test_scale_is_not_multiplied_twice(self) -> None:
        """div_round scale 误用回归：0.833… 不能被放大成 83.33…。"""
        assert weights_from_values([80], 96, scale=SCALE) == [83333333]

    def test_zero_total_rejected(self) -> None:
        with pytest.raises(ZeroDivisionError):
            weights_from_values([1], 0)

    def test_normalize(self) -> None:
        assert normalize_weights({"a": 50000000, "b": 50000000}) == {
            "a": 50000000,
            "b": 50000000,
        }

    def test_normalize_zero_total_rejected(self) -> None:
        with pytest.raises(ZeroDivisionError):
            normalize_weights({"a": 0})

    def test_active_uses_union_and_zero_default(self) -> None:
        assert active_weights({"a": 50000000}, {"b": 50000000}) == {
            "a": 50000000,
            "b": -50000000,
        }


class TestConcentration:
    def test_hhi_matches_golden(self) -> None:
        assert sector_hhi_from_values([80, 10, 6], 96) == 70920139

    def test_hhi_zero_total_is_none(self) -> None:
        assert sector_hhi_from_values([], 0) is None

    def test_topk(self) -> None:
        assert topk_weights([80, 10, 6], 96, k=1) == 83333333
        assert topk_weights([80, 10, 6], 96, k=3) == 100000000

    def test_topk_guards(self) -> None:
        with pytest.raises(ValueError):
            topk_weights([1], 10, k=0)
        assert topk_weights([1], 0) is None

    def test_effective_n_from_hhi_units(self) -> None:
        assert effective_n_units(70920139) == 141003672
        assert effective_n_units(0) is None


class TestPreference:
    def test_deviation_halves_absolute_sum(self) -> None:
        active = {"a": 53333333, "b": -29583333, "c": -23750000}
        assert deviation_units(active) == 53333333

    def test_empty_is_none(self) -> None:
        assert deviation_units({}) is None


class TestConcepts:
    def test_exposure_does_not_normalize(self) -> None:
        result = concept_exposure(
            {"A": 50, "B": 30, "D": 6},
            {"A": ("C1", "C2"), "B": ("C1",), "D": ("C2",)},
            total_units=100,
        )
        assert result.exposure == {"C1": 80000000, "C2": 56000000}
        assert result.coverage_units == 86000000
        assert result.overlap_units == 136000000
        assert result.multi_tag_units == 50000000
        assert result.hhi_tag_units == 51557093

    def test_duplicate_tag_counted_once(self) -> None:
        result = concept_exposure({"A": 10}, {"A": ("C1", "C1")}, total_units=10)
        assert result.exposure == {"C1": 100000000}
        assert result.multi_tag_units == 0

    def test_all_untagged_has_zero_coverage(self) -> None:
        result = concept_exposure({"A": 10}, {}, total_units=10)
        assert result.exposure == {}
        assert result.coverage_units == 0
        assert result.hhi_tag_units is None

    def test_zero_total_rejected(self) -> None:
        with pytest.raises(ZeroDivisionError):
            concept_exposure({"A": 10}, {"A": ("C1",)}, total_units=0)


class TestCoverageStatus:
    def test_thresholds(self) -> None:
        assert coverage_status(95000000) == STATUS_OK
        assert coverage_status(94999999) == STATUS_DEGRADED
        assert coverage_status(90000000) == STATUS_DEGRADED
        assert coverage_status(89999999) == STATUS_INSUFFICIENT

    def test_override(self) -> None:
        assert coverage_status(50, ok_min_units=10, degraded_min_units=5) == STATUS_OK
