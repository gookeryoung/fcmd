"""PDF 依赖策略守卫。

PyMuPDF 2.x 会硬依赖 onnxruntime / pymupdf-layout / pymupdf4llm，发布物因此
多出约 72 MB；fcmd 只用 1.x 的基础 PDF 读写。这里把约束固化成断言，避免
依赖更新（如 dependabot）再次把 ``pdf`` extra 放宽到 2.x。

发布产物层面的校验由 ``.github/workflows/release.yml`` 的
``Assert PDF dependency policy`` 步骤负责。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_requirements() -> list[Requirement]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    extras = project["optional-dependencies"]
    return [Requirement(item) for item in project["dependencies"]] + [
        Requirement(item) for items in extras.values() for item in items
    ]


def test_pymupdf_pinned_below_2() -> None:
    """``pdf`` extra 必须解析到 1.x，不能允许 2.0 / 2.1。"""
    req = next(item for item in _declared_requirements() if item.name.lower() == "pymupdf")
    assert req.specifier.contains("1.28.2")
    assert not req.specifier.contains("2.0.0")
    assert not req.specifier.contains("2.1.0")


def test_onnxruntime_not_declared() -> None:
    """onnxruntime 只能是 PyMuPDF 2.x 的传递依赖，不能出现在声明里。"""
    names = {item.name.lower() for item in _declared_requirements()}
    assert "onnxruntime" not in names
