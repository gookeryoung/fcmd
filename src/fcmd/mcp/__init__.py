"""MCP 集成层：stdio server、安全策略与工具白名单（可选依赖 ``mcp``）。

与 CLI 分离：``fcmd.cli`` 只负责入口与路由，本包负责把工具注册表暴露为
MCP tools；缺 ``mcp`` 依赖时导入本包仍安全，只有真正启动 server 才报错。
"""

from __future__ import annotations

__all__: list[str] = []
