"""pptmake 精确预览：探测 LibreOffice 并把导出的 PPTX 转成 PDF。

LibreOffice 是可选的外部依赖，本模块只负责探测与调用，不下载、不安装：
未检测到 ``soffice`` 时抛出带有中文说明的 :class:`OfficeError`，调用方回退到
Tk 画布预览。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "OfficeError",
    "convert_to_pdf",
    "find_soffice",
]

_WINDOWS_PATHS: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


class OfficeError(RuntimeError):
    """LibreOffice 缺失或转换失败。"""


def find_soffice(env: Mapping[str, str] | None = None) -> Path | None:
    """查找 ``soffice`` 可执行文件；``PPTMAKE_SOFFICE`` 指向的路径优先。"""
    environ = os.environ if env is None else env
    override = environ.get("PPTMAKE_SOFFICE", "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for raw in _WINDOWS_PATHS:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
    return None


def convert_to_pdf(pptx_path: Path, out_dir: Path, *, timeout: float = 120.0) -> Path:
    """用 headless LibreOffice 把 PPTX 转成 PDF，返回生成的 PDF 路径。"""
    soffice = find_soffice()
    if soffice is None:
        raise OfficeError("未检测到 LibreOffice（soffice），精确预览已降级为画布预览")
    out_dir.mkdir(parents=True, exist_ok=True)
    command = (
        str(soffice),
        "--headless",
        "--norestore",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(pptx_path),
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OfficeError(f"调用 LibreOffice 失败：{exc}") from exc
    target = out_dir / f"{pptx_path.stem}.pdf"
    if not target.is_file():
        detail = (result.stderr or result.stdout).strip()
        raise OfficeError(f"LibreOffice 未生成 PDF：{detail or '无输出'}")
    return target
