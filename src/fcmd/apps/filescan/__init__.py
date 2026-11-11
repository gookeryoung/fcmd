"""filescan —— 目录敏感内容扫描（规则表达式 + 并行扫描 + 打码报告）。

包内 ``filescan`` 为命令行入口、``filescanw`` 为 Tk 窗口入口；规则模型与表达式、
扫描引擎、报告生成分别位于 ``_rules`` / ``_engine`` / ``_report``，两个入口共用
同一套口径。扫描全程只读，报告默认打码，只有显式开关才输出敏感原文。
"""

from __future__ import annotations

__all__: list[str] = []
