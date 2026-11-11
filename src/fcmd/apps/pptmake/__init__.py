"""pptmake —— PPT 模板设计器（布局 / 风格 / 导出）。

包内 ``pptmake`` 为命令行入口、``pptmakew`` 为 Tk 设计器入口；模板模型、
版式引擎、PPTX 渲染与导入全部放在下划线前缀模块中，两个入口共用同一套
口径。模板以 JSON 保存（``*.pptmtpl.json``），可无损导入导出，也可渲染为
``.pptx`` 演示文稿。
"""

from __future__ import annotations

__all__: list[str] = []
