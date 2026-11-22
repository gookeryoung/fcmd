"""PDF 依赖策略守卫。

PyMuPDF 2.x 会硬依赖 onnxruntime / pymupdf-layout / pymupdf4llm，发布物因此
多出约 72 MB；fcmd 只用 1.x 的基础 PDF 读写。这里把约束固化成断言，避免
依赖更新（如 dependabot）再次把 ``pdf`` extra 放宽到 2.x。

发布产物层面的校验由 ``.github/workflows/release.yml`` 的
``Assert PDF dependency policy`` 步骤负责。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packaging.requirements import Requirement

from fcmd.infras import _toml as tomllib

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


# ---------------------------------------------------------------------- #
# extras 一致性：分组不重复声明常驻依赖，引用不留悬空分组
# ---------------------------------------------------------------------- #
def _project() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _main_requirements() -> list[Requirement]:
    return [Requirement(item) for item in _project()["dependencies"]]


def _extra_names() -> set[str]:
    return set(_project()["optional-dependencies"])


def test_extras_do_not_duplicate_main_dependencies() -> None:
    """常驻依赖不重复进 extras（例如 Pillow 已在主依赖里，就不该再有 img 分组）。"""
    main = {item.name.lower() for item in _main_requirements()}
    duplicated: dict[str, list[str]] = {}
    for extra, items in _project()["optional-dependencies"].items():
        for item in items:
            req = Requirement(item)
            # 只看真正的第三方包；``fcmd[...]`` 组合引用由下面的悬空引用用例守。
            if not req.extras and req.name.lower() in main:
                duplicated.setdefault(extra, []).append(req.name)
    assert not duplicated, f"这些 extra 重复声明了常驻依赖：{duplicated}"


def test_extras_references_are_declared() -> None:
    """``fcmd[...]`` 组合引用必须指向已声明的 extra（删分组时别留悬空引用）。"""
    declared = _extra_names()
    dangling: list[tuple[str, str]] = []
    for extra, items in _project()["optional-dependencies"].items():
        for item in items:
            req = Requirement(item)
            if req.name.lower() != "fcmd":
                continue
            for referenced in req.extras:
                if referenced not in declared:
                    dangling.append((extra, referenced))
    assert not dangling, f"这些 extra 引用了不存在的分组：{dangling}"


def test_reported_optional_extras_exist() -> None:
    """``fcmd env`` / ``fcmd doctor`` 报告的可选依赖分组必须真实存在。"""
    from fcmd.cli._env_helpers import _OPTIONAL_DEPS

    declared = _extra_names()
    unknown = sorted({extra for extra, _module, _package in _OPTIONAL_DEPS if extra not in declared})
    assert not unknown, f"env/doctor 报告了未声明的 extra：{unknown}"


def test_reported_optional_packages_are_not_required() -> None:
    """常驻依赖不该出现在可选依赖报告里（缺了说明安装不完整，不是没装 extra）。"""
    from fcmd.cli._env_helpers import _OPTIONAL_DEPS

    main = {item.name.lower() for item in _main_requirements()}
    misreported = sorted({package for _extra, _module, package in _OPTIONAL_DEPS if package.lower() in main})
    assert not misreported, f"这些常驻依赖被当成可选依赖报告：{misreported}"


def test_fspack_extras_are_declared() -> None:
    """``[tool.fspack] extras`` 打包清单只允许出现已声明的分组。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    configured = set(data.get("tool", {}).get("fspack", {}).get("extras", []))
    assert configured, "缺少 [tool.fspack] extras"
    assert configured <= _extra_names(), f"打包清单里的未声明分组：{sorted(configured - _extra_names())}"
    # 命令行打包（release.yml Linux 矩阵）用的分组也必须存在
    assert {"archive", "color", "crypto", "filecheck", "ocr", "office", "pdf", "pe"} <= configured
