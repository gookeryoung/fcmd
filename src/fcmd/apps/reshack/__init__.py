"""reshack / reshackw —— Windows PE 资源查看与修改。

应用包内含两个工具模块：``reshack``（CLI）与 ``reshackw``（Tk GUI），
模块名即命令名。发现逻辑在包未声明 ``__tool_name__`` 时逐公开模块注册，
因此无需为每个命令单独建包。
"""

from __future__ import annotations

__all__: list[str] = []
