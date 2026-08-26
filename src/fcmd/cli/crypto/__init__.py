"""fcmd.cli.crypto —— 哈希与加密工具：对称加密、文件/字符串哈希、ID 生成。

领域子包：内含的工具模块经 `@fx.tool` 注册为顶层命令（工具名 = 模块名，
调用方式 `fcmd <工具名>`），本包门面不 re-export 任何工具符号，
避免导入单个工具时连带触发同域其他工具的导入。
"""

from __future__ import annotations

__all__: list[str] = []
