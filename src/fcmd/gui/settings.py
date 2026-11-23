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

__all__ = ["SettingsStore"]


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
        """原子写入设置并保留 ``.bak``；写入失败静默（关闭路径不应报错）。"""
        _guiconfig.write_settings(payload, self.resolved_path())
