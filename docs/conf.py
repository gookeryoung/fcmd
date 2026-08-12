"""Sphinx 配置.

ReadTheDocs 构建项目文档站。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中, autodoc 能导入包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# -- 项目信息 --------------------------------------------------------------
project = "fcmd"
author = "gookeryoung"
copyright = "2026, gookeryoung"

try:
    from fcmd import __version__  # type: ignore[import-not-found]
    release = __version__
    version = __version__
except ImportError:
    release = "0.1.0"
    version = "0.1.0"

# -- Sphinx 配置 -----------------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

# -- 主题 ------------------------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

# -- autodoc 配置 ----------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_type_hints = "description"
autodoc_typehints_format = "short"

# -- napoleon 配置 (Google/NumPy docstring 兼容) --------------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True

# -- intersphinx -----------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# -- 全局选项 ---------------------------------------------------------------
language = "zh_CN"
master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
# fcmd 顶层包通过 __getattr__ 聚合子模块符号，autodoc 会同时索引 fcmd.X 与
# fcmd.apis.Y.X，导致交叉引用多目标与重复对象描述警告。这些警告不影响文档
# 功能（Sphinx 自动选首个目标），且 RTD 配置 fail_on_warning: false，此处抑制。
suppress_warnings = ["ref.python", "duplicate_object"]
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
