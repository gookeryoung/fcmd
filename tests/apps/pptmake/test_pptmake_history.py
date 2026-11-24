"""pptmake 编辑历史：记录、撤销 / 重做、连续输入合并与上限。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake._history import TemplateHistory


def _template(name: str = "T") -> core.TemplateSpec:
    return core.new_template(name=name, slides=(core.new_slide(core.LAYOUT_BULLETS),))


def test_record_undo_redo_roundtrip() -> None:
    first = _template("一")
    second = replace(first, name="二")
    third = replace(second, name="三")
    history = TemplateHistory(first)
    assert history.record(second, "改名") is True
    assert history.record(third, "改风格") is True
    assert history.can_undo() and not history.can_redo()
    assert history.undo_label() == "改风格"

    assert history.undo() == second
    assert history.undo() == first
    assert history.undo() is None
    assert history.can_redo()

    assert history.redo() == second
    assert history.redo() == third
    assert history.redo() is None


def test_record_ignores_identical_template() -> None:
    template = _template()
    history = TemplateHistory(template)
    assert history.record(replace(template), "无变化") is False
    assert not history.can_undo()


def test_new_record_clears_redo() -> None:
    first = _template()
    history = TemplateHistory(first)
    history.record(replace(first, name="二"), "改名")
    history.undo()
    assert history.can_redo()
    history.record(replace(first, name="三"), "改名")
    assert not history.can_redo()


def test_coalesce_same_key_within_window() -> None:
    first = _template()
    history = TemplateHistory(first)
    second = replace(first, name="一")
    third = replace(second, name="一二")
    assert history.record(second, "输入标题", coalesce_key="title", now=10.0) is True
    assert history.record(third, "输入标题", coalesce_key="title", now=10.3) is True
    # 两次输入合并成一条历史：撤销直接回到最初模板。
    assert history.undo() == first
    assert history.undo() is None


def test_coalesce_different_key_or_timeout_pushes_new_entry() -> None:
    first = _template()
    second = replace(first, name="一")
    third = replace(second, name="一二")
    fourth = replace(third, name="一二三")
    history = TemplateHistory(first)
    history.record(second, "输入标题", coalesce_key="title", now=10.0)
    history.record(third, "输入标题", coalesce_key="title", now=11.5)
    history.record(fourth, "拖动滑杆", coalesce_key="intensity", now=11.6)
    assert history.undo() == third
    assert history.undo() == second
    assert history.undo() == first


def test_undo_redo_clears_coalesce() -> None:
    first = _template()
    second = replace(first, name="一")
    history = TemplateHistory(first)
    history.record(second, "输入", coalesce_key="title", now=10.0)
    assert history.undo() == first
    third = replace(first, name="二")
    assert history.record(third, "输入", coalesce_key="title", now=10.1) is True
    # 撤销后的新输入必须开启新历史，不能和撤销前的输入合并。
    assert history.undo() == first


def test_limit_drops_oldest() -> None:
    template = _template()
    history = TemplateHistory(template, limit=2)
    for index in range(5):
        history.record(replace(template, name=f"版本{index}"), f"编辑{index}")
    assert history.can_undo()
    history.undo()
    history.undo()
    assert history.undo() is None


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        TemplateHistory(_template(), limit=0)
