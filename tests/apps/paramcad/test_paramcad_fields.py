"""解析 / 实测分布场的采样、校验与来源追溯测试。"""

from __future__ import annotations

import pytest

from fcmd.apps.paramcad._fields import (
    AnalyticProfile,
    Field,
    MeasuredProfile,
    fields_by_axis,
)
from fcmd.apps.paramcad._quantity import ParamcadError


def test_constant_profile_is_flat() -> None:
    profile = AnalyticProfile("constant", 1.6)
    assert profile.sample(0.0) == pytest.approx(1.6)
    assert profile.sample(0.7) == pytest.approx(1.6)
    assert profile.vertices() == ((0.0, 1.6), (1.0, 1.6))


def test_linear_profile_matches_vertices() -> None:
    profile = AnalyticProfile("linear", 1.0, end_value=2.0)
    assert profile.sample(0.0) == pytest.approx(1.0)
    assert profile.sample(0.25) == pytest.approx(1.25)
    assert profile.sample(1.0) == pytest.approx(2.0)
    assert profile.vertices() == ((0.0, 1.0), (1.0, 2.0))


def test_end_loose_profile_holds_then_decays() -> None:
    profile = AnalyticProfile("end_loose", 1.6, end_value=1.4, loose_length=0.2)
    assert profile.sample(0.0) == pytest.approx(1.6)
    assert profile.sample(0.8) == pytest.approx(1.6)
    assert profile.sample(0.9) == pytest.approx(1.5)
    assert profile.sample(1.0) == pytest.approx(1.4)
    assert profile.vertices() == ((0.0, 1.6), (0.8, 1.6), (1.0, 1.4))


def test_profiles_clamp_out_of_range() -> None:
    profile = AnalyticProfile("linear", 1.0, end_value=2.0)
    assert profile.sample(-1.0) == pytest.approx(1.0)
    assert profile.sample(2.0) == pytest.approx(2.0)


def test_constant_rejects_extra_fields() -> None:
    with pytest.raises(ParamcadError, match="constant"):
        AnalyticProfile("constant", 1.0, end_value=2.0)
    with pytest.raises(ParamcadError, match="疏松长度"):
        AnalyticProfile("constant", 1.0, loose_length=0.1)


def test_linear_requires_end_value() -> None:
    with pytest.raises(ParamcadError, match="端值"):
        AnalyticProfile("linear", 1.0)
    with pytest.raises(ParamcadError, match="疏松长度"):
        AnalyticProfile("linear", 1.0, end_value=2.0, loose_length=0.1)


def test_end_loose_requires_positive_span() -> None:
    with pytest.raises(ParamcadError, match="疏松段长度占比"):
        AnalyticProfile("end_loose", 1.0, end_value=0.5)
    with pytest.raises(ParamcadError, match="必须大于 0"):
        AnalyticProfile("end_loose", 1.0, end_value=0.5, loose_length=0.0)
    with pytest.raises(ParamcadError, match="0–1"):
        AnalyticProfile("end_loose", 1.0, end_value=0.5, loose_length=1.5)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ParamcadError, match="未知解析分布类型"):
        AnalyticProfile("cubic", 1.0)  # pyrefly: ignore [bad-argument-type]


def test_measured_profile_interpolates_and_clamps() -> None:
    profile = MeasuredProfile(((0.0, 1.0), (0.5, 1.4), (1.0, 1.2)))
    assert profile.sample(-0.5) == pytest.approx(1.0)
    assert profile.sample(0.25) == pytest.approx(1.2)
    assert profile.sample(0.75) == pytest.approx(1.3)
    assert profile.sample(1.5) == pytest.approx(1.2)
    assert profile.vertices() == ((0.0, 1.0), (0.5, 1.4), (1.0, 1.2))


def test_measured_profile_requires_two_increasing_points() -> None:
    with pytest.raises(ParamcadError, match="至少需要 2 个点"):
        MeasuredProfile(((0.0, 1.0),))
    with pytest.raises(ParamcadError, match="严格递增"):
        MeasuredProfile(((0.5, 1.0), (0.5, 1.2)))
    with pytest.raises(ParamcadError, match="0–1"):
        MeasuredProfile(((0.0, 1.0), (1.5, 1.2)))
    with pytest.raises(ParamcadError, match="有限数"):
        MeasuredProfile(((0.0, 1.0), (1.0, float("inf"))))


def test_field_requires_axis_source_and_density_unit() -> None:
    profile = AnalyticProfile("constant", 1.6)
    with pytest.raises(ParamcadError, match="未知分布轴"):
        Field(axis="tangential", profile=profile, source="测试")  # type: ignore[arg-type]
    with pytest.raises(ParamcadError, match="注明来源"):
        Field(axis="axial", profile=profile, source=" ")
    with pytest.raises(ParamcadError, match="密度单位"):
        Field(axis="axial", profile=profile, source="测试", unit="kg/m^3")


def test_fields_by_axis_rejects_duplicates() -> None:
    profile = AnalyticProfile("constant", 1.6)
    field = Field(axis="axial", profile=profile, source="测试")
    with pytest.raises(ParamcadError, match="同一轴"):
        fields_by_axis((field, field))
    assert fields_by_axis((field,))["axial"] is field
