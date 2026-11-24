"""GUI 设置存储：路径解析 + 容错读取 + 原子写入的统一入口。

各窗口仍保留自己的 ``Settings`` dataclass 与字段校验；本模块只收敛
「文件在哪、读失败怎么办、怎么写不破坏旧文件」这三件重复逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fcmd.gui import config as _guiconfig

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

SCHEMA_VERSION = 1
"""设置文件结构版本；新写入文件自动带上，旧文件缺失视为 1。"""

__all__ = ["SCHEMA_VERSION", "SettingsStore"]


@dataclass(frozen=True, slots=True)
class SettingsStore:
    """一个窗口设置文件的最小读写接口（``ui/<name>.json``）。

    Parameters
    ----------
    name:
        配置文件主名（如 ``dynacalcw``），对应 ``ui/<name>.json``。
    path:
        显式路径覆盖（测试 / 迁移用）；``None`` 时按 ``FCMD_HOME`` 解析。
    """

    name: str
    path: Path | None = None

    def resolved_path(self) -> Path:
        """返回实际使用的设置文件路径。"""
        return self.path or _guiconfig.config_path(self.name)

    def load(self) -> dict[str, Any] | None:
        """读取设置映射；文件缺失 / 损坏时返回 ``None``（含 ``.bak`` 回退）。"""
        return _guiconfig.read_settings(self.resolved_path())

    def save(self, payload: Mapping[str, Any]) -> None:
        """原子写入设置并保留 ``.bak``；自动补 ``schema_version``。

        版本号只用于未来迁移判读，当前解析仍是「逐字段容错」；旧文件没有该字段时
        按 1 处理，应用不需要改动。
        """
        data = dict(payload)
        data.setdefault("schema_version", SCHEMA_VERSION)
        _guiconfig.write_settings(data, self.resolved_path())
