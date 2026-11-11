"""peview：PE 文件查看与安全编辑（自研标准库解析，不依赖 pefile）。

公开模块：

- :mod:`fcmd.apps.peview.peview` —— CLI（``fcmd peview ...``）
- :mod:`fcmd.apps.peview.pevieww` —— Tk 查看器（``fcmd pevieww``）

解析、展示、编辑校验分别在 ``_reader`` / ``_pe`` / ``_report`` / ``_edit`` /
``_view`` 中实现，均为纯逻辑模块。
"""
