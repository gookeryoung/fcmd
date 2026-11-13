"""paramcadw 纯逻辑测试：设置持久化、画布映射与工具注册（不打开 Tk 窗口）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.paramcad import paramcadw
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_tool_registration() -> None:
    assert None in _TOOL_REGISTRY["paramcadw"]


def test_settings_path_honours_fcmd_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert paramcadw.settings_path() == tmp_path / "ui" / "paramcad.json"


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "ui" / "paramcad.json"
    settings = paramcadw.Settings(geometry="1120x720+10+20", model_path=str(tmp_path / "m.yaml"))
    paramcadw.save_settings(settings, path)
    assert paramcadw.load_settings(path) == settings


def test_load_settings_falls_back_on_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "paramcad.json"
    path.write_text("not json", encoding="utf-8")
    assert paramcadw.load_settings(path) == paramcadw.Settings()


def test_load_settings_drops_invalid_geometry(tmp_path: Path) -> None:
    path = tmp_path / "paramcad.json"
    path.write_text('{"geometry": "big", "model_path": 42}', encoding="utf-8")
    assert paramcadw.load_settings(path) == paramcadw.Settings()


def test_canvas_map_keeps_aspect_and_flips_y() -> None:
    mapper = paramcadw.CanvasMap(200.0, 100.0, -10.0, 10.0, -10.0, 10.0, padding=0.0)
    assert mapper.to_pixel(0.0, 0.0) == pytest.approx((100.0, 50.0))
    assert mapper.to_pixel(10.0, 0.0) == pytest.approx((150.0, 50.0))
    assert mapper.to_pixel(0.0, 10.0) == pytest.approx((100.0, 0.0))
    assert mapper.to_pixel(10.0, 10.0) == pytest.approx((150.0, 0.0))


def test_canvas_map_polyline_flattens_points() -> None:
    mapper = paramcadw.CanvasMap(200.0, 100.0, -10.0, 10.0, -10.0, 10.0, padding=0.0)
    assert mapper.polyline([(0.0, 0.0), (10.0, 10.0)]) == pytest.approx([100.0, 50.0, 150.0, 0.0])


def test_canvas_map_handles_degenerate_spans() -> None:
    mapper = paramcadw.CanvasMap(100.0, 100.0, 0.0, 0.0, 0.0, 0.0, padding=0.0)
    x, y = mapper.to_pixel(0.0, 0.0)
    assert (x, y) == pytest.approx((50.0, 50.0))
