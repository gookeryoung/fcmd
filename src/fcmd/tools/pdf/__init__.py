"""fcmd.tools.pdf —— PDF 工具：图片转 PDF（CLI / 窗口版）与 PDF 处理。

领域子包：内含的工具模块经 `@fx.tool` 注册为顶层命令（工具名 = 模块名，
调用方式 `fcmd <工具名>`），本包门面不 re-export 任何工具符号，
避免导入单个工具时连带触发同域其他工具的导入。

可复用的 PDF 操作函数与依赖守卫集中在 :mod:`fcmd.tools.pdf.pdfbase`，
导入该模块不会注册任何 CLI 工具。
"""

from __future__ import annotations

__all__: list[str] = []
