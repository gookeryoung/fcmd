"""filesync —— 文件夹比较与同步（参考 FreeFileSync）。

应用包内含两个工具模块：``filesync``（CLI）与 ``filesyncw``（Tk GUI），
模块名即命令名；核心算法在 :mod:`fcmd.apps.filesync._core`，CLI 与窗口
共用同一套扫描 / 比较 / 计划 / 执行逻辑。
"""

from __future__ import annotations

__all__: list[str] = []
