"""checksum - 校验和计算器（Qt GUI）。

交互参考 ``bitool_gui.checksum``：选择 MD5 / SHA1 / SHA256 / SHA384 / SHA512 /
Blake2b / Blake2s，计算输入字符串或所选文件的十六进制摘要；勾选「比较校验」
后可填入期望值，结果区追加不区分大小写的比对结论，支持与 ``sha256sum`` 等
工具输出的摘要直接比对。

摘要计算与分块策略复用 :mod:`fcmd.infras.hashing`，本模块只保留展示用的算法
列表、参数归一与结果文案。

设置（上次算法 / 比较开关 / 窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/checksum.json``，下次启动自动恢复。

示例
----
    fcmd checksum          # 打开校验和计算器窗口
    checksum               # 全局命令等价入口
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry
from fcmd.infras import hashing

__all__ = [
    "ALGORITHMS",
    "ChecksumSettings",
    "calculate_file_checksum",
    "calculate_string_checksum",
    "config_path",
    "format_result",
    "load_settings",
    "normalize_algorithm",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
    "verify_checksum",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "校验和计算器窗口版（Qt）：选择 MD5/SHA1/SHA256/SHA384/SHA512/Blake2b/Blake2s，"
    "计算字符串或文件的十六进制摘要，可选用期望值做忽略大小写的校验比对；"
    "设置持久化到 ~/.fcmd/ui/checksum.json"
)

# 支持的算法展示名（顺序即界面排列顺序）
ALGORITHMS: tuple[str, ...] = ("MD5", "SHA1", "SHA256", "SHA384", "SHA512", "Blake2b", "Blake2s")

VERIFY_PASSED = "校验通过"
VERIFY_FAILED = "校验失败"
DEFAULT_ALGORITHM = "MD5"
DEFAULT_GEOMETRY = "720x480+300+200"
MIN_WIDTH = 640
MIN_HEIGHT = 440
# 文件哈希分块大小（与 infras.hashing 保持一致）
CHUNK_SIZE = hashing.CHUNK_SIZE


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/checksum.json``。"""
    return _guiconfig.config_path("checksum")


@dataclass
class ChecksumSettings:
    """校验和计算器的持久化设置。"""

    algorithm: str = DEFAULT_ALGORITHM
    compare: bool = False
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> ChecksumSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return ChecksumSettings()

    algorithm = raw.get("algorithm")
    compare = raw.get("compare")
    geometry = raw.get("geometry")
    settings = ChecksumSettings(
        compare=compare if isinstance(compare, bool) else False,
        geometry=geometry if isinstance(geometry, str) else None,
    )
    if isinstance(algorithm, str):
        try:
            settings.algorithm = normalize_algorithm(algorithm)
        except ValueError:
            settings.algorithm = DEFAULT_ALGORITHM
    return settings


def save_settings(settings: ChecksumSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "algorithm": settings.algorithm,
        "compare": settings.compare,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _geometry.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测，不依赖 GUI 工具包）
# ---------------------------------------------------------------------- #
def normalize_algorithm(name: str) -> str:
    """把算法名规范为 :data:`ALGORITHMS` 中的展示名（忽略大小写与首尾空白）。

    Raises
    ------
    ValueError
        算法不在支持列表中。
    """
    key = name.strip().lower()
    for candidate in ALGORITHMS:
        if candidate.lower() == key:
            return candidate
    raise ValueError(f"不支持的哈希算法: {name}，支持: {', '.join(ALGORITHMS)}")


def calculate_string_checksum(content: str, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """计算字符串的十六进制摘要。

    Parameters
    ----------
    content:
        待计算的字符串，按 UTF-8 编码后哈希
    algorithm:
        算法名（``ALGORITHMS`` 之一，忽略大小写）

    Raises
    ------
    ValueError
        内容为空或算法不受支持。
    """
    if not content:
        raise ValueError("内容不能为空")
    return hashing.hash_text(content, normalize_algorithm(algorithm).lower())


def calculate_file_checksum(path: Path, algorithm: str = DEFAULT_ALGORITHM, chunk_size: int = CHUNK_SIZE) -> str:
    """分块计算文件的十六进制摘要（支持大文件，不整体载入内存）。

    Raises
    ------
    FileNotFoundError
        路径不存在或不是文件。
    ValueError
        算法不受支持。
    """
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    return hashing.compute_file_hash(path, normalize_algorithm(algorithm).lower(), chunk_size)


def verify_checksum(calculated: str, expected: str) -> bool:
    """比对摘要与期望值（忽略大小写与首尾空白）。"""
    return calculated.strip().lower() == expected.strip().lower()


def format_result(digest: str, expected: str | None = None) -> str:
    """生成结果区文本：摘要，启用比较时追加校验结论。"""
    if expected is None:
        return digest
    verdict = VERIFY_PASSED if verify_checksum(digest, expected) else VERIFY_FAILED
    return f"{digest}\n{verdict}"


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.tools.system._checksum_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("checksum", help="打开校验和计算器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开校验和计算器窗口。"""
    run_gui()


@fcmd.main("checksum")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
