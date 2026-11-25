"""五份合成样本的导入回归：文本 / 数据 / 图文 / 多表 / 混合+备注。

样本覆盖 ``import_pptx`` 的主要分派分支，断言每份样本的内容保留率不低于
95%，且导入产物直接通过设计检查（``quality_issues`` 为 0）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.pptmake import _pptx, _pptx_import, _quality

_RATE_RE = re.compile(rf"({'|'.join(_pptx_import._REPORT_KINDS)}) (\d+(?:\.\d+)?)%")


def _texts(shape: Any) -> Any:
    return shape.text_frame


def _cover(presentation: Any, title: str) -> Any:
    """新建一页并写入标题文本，返回该页。"""
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    _texts(slide.shapes.add_textbox(0, 0, 400, 60)).text = title
    return slide


def _sample_text(api: Any, presentation: Any, _image: Path) -> None:
    _cover(presentation, "文本样本")
    slide = _cover(presentation, "要点集合")
    for index in range(3):
        _texts(slide.shapes.add_textbox(0, 80 + index * 70, 400, 60)).text = f"要点 {index + 1}"


def _sample_data(api: Any, presentation: Any, _image: Path) -> None:
    _cover(presentation, "数据样本")
    slide = _cover(presentation, "季度收入")
    data = api.CategoryChartData()
    data.categories = ["Q1", "Q2", "Q3"]
    data.add_series("收入", (42, 68, 54))
    slide.shapes.add_chart(
        api.XL_CHART_TYPE.COLUMN_CLUSTERED,
        0,
        100,
        presentation.slide_width,
        3000000,
        data,
    )


def _sample_image(api: Any, presentation: Any, image: Path) -> None:
    _cover(presentation, "图文样本")
    slide = _cover(presentation, "品牌主图")
    _texts(slide.shapes.add_textbox(0, 80, 400, 60)).text = "并排展示"
    slide.shapes.add_picture(
        str(image),
        round(presentation.slide_width * 0.02),
        round(presentation.slide_height * 0.2),
        round(presentation.slide_width * 0.24),
        round(presentation.slide_height * 0.55),
    )


def _sample_tables(api: Any, presentation: Any, _image: Path) -> None:
    _cover(presentation, "多表样本")
    slide = _cover(presentation, "方案对比")
    for index in range(2):
        table = slide.shapes.add_table(2, 3, 0, 100 + index * 100, 3000000, 800000).table
        for row_index, row in enumerate((("指标", "现状", "目标"), (f"项{index}", "甲", "乙"))):
            for column_index, value in enumerate(row):
                table.cell(row_index, column_index).text = value


def _sample_mixed(api: Any, presentation: Any, image: Path) -> None:
    _cover(presentation, "混合样本")
    image_slide = _cover(presentation, "主图页")
    _texts(image_slide.shapes.add_textbox(0, 80, 400, 60)).text = "替换为品牌主图"
    image_slide.shapes.add_picture(
        str(image),
        round(presentation.slide_width * 0.02),
        round(presentation.slide_height * 0.2),
        round(presentation.slide_width * 0.24),
        round(presentation.slide_height * 0.55),
    )
    image_slide.notes_slide.notes_text_frame.text = "备注：主图待替换"
    table_slide = _cover(presentation, "方案对比")
    table = table_slide.shapes.add_table(2, 3, 0, 100, 3000000, 800000).table
    for row_index, row in enumerate((("指标", "现状", "目标"), ("速度", "小时级", "分钟级"))):
        for column_index, value in enumerate(row):
            table.cell(row_index, column_index).text = value
    chart_slide = _cover(presentation, "增长趋势")
    data = api.CategoryChartData()
    data.categories = ["Q1", "Q2", "Q3"]
    data.add_series("收入", (42, 68, 54))
    chart_slide.shapes.add_chart(
        api.XL_CHART_TYPE.COLUMN_CLUSTERED,
        0,
        100,
        presentation.slide_width,
        3000000,
        data,
    )


_SAMPLES: tuple[tuple[str, Callable[[Any, Any, Path], None]], ...] = (
    ("text", _sample_text),
    ("data", _sample_data),
    ("image", _sample_image),
    ("tables", _sample_tables),
    ("mixed", _sample_mixed),
)


def _rates(summary: str) -> dict[str, float]:
    """从 ``保留率：…`` 行解析各类别百分比。"""
    return {kind: float(rate) for kind, rate in _RATE_RE.findall(summary)}


def test_import_stats_summary_flags_low_retention() -> None:
    """任一类保留率低于 95% 时，摘要末尾点名该类别。"""
    stats = _pptx_import.ImportStats()
    stats.found["文本"] = 10
    stats.kept["文本"] = 9
    stats.found["图片"] = 4
    stats.kept["图片"] = 4
    summary = stats.summary()
    assert "文本 90.0%（9/10）" in summary
    assert summary.endswith("；低于 95%：文本")
    assert "低于 95%：图片" not in summary


@pytest.mark.parametrize(("name", "builder"), _SAMPLES, ids=[item[0] for item in _SAMPLES])
def test_import_sample_keeps_content_and_passes_quality(
    tmp_path: Path, name: str, builder: Callable[[Any, Any, Path], None]
) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    api = _pptx._api()
    image_path = tmp_path / f"{name}.png"
    Image.new("RGB", (640, 360), "#3366FF").save(image_path)
    presentation = api.Presentation()
    builder(api, presentation, image_path)
    source = tmp_path / f"{name}.pptx"
    presentation.save(str(source))

    report: list[str] = []
    template = _pptx.import_pptx(source, assets_dir=tmp_path / f"{name}.assets", report=report)
    rates = _rates(report[-1])
    assert set(rates) == set(_pptx_import._REPORT_KINDS)
    assert all(rate >= 95.0 for rate in rates.values()), report[-1]
    assert _quality.quality_issues(template, base_dir=tmp_path, verbose=False).total == 0
    assert template.slides
