"""zipencrypt - ZIP 加密工具。

将目录下的文件和子目录加密为带密码保护的 ZIP 压缩包。
按优先级使用 7z / zip / rar 外部工具进行加密，无可用工具时回退到无加密 zipfile。

示例
----
    fcmd zipencrypt . mypassword                # 加密当前目录下文件
    fcmd zipencrypt /path/to/dir mypassword     # 加密指定目录
    fcmd zipencrypt . mypassword --replace      # 覆盖已有 ZIP 文件
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = ["zip_encrypt"]

# 跳过的目录名前缀（文件不受此限制，仅目录按此过滤）
_SKIP_PREFIXES: tuple[str, ...] = (".", "__")

# 跳过的压缩包扩展名（避免对已有压缩包再压缩）
_ARCHIVE_EXTS: frozenset[str] = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2"})


def _get_valid_entries(dirpath: Path) -> list[Path]:
    """获取目录下有效条目（非压缩包文件 + 非隐藏目录）。

    跳过以 ``.`` 或 ``__`` 开头的目录（如 ``.git``/``__pycache__``），
    跳过已有压缩包文件（``.zip``/``.rar``/``.7z`` 等，避免重复压缩）。
    其他文件（含 ``.env`` 等点文件）不受限制。
    """
    return [
        entry
        for entry in dirpath.iterdir()
        if (entry.is_file() and entry.suffix.lower() not in _ARCHIVE_EXTS)
        or (entry.is_dir() and not any(entry.name.startswith(p) for p in _SKIP_PREFIXES))
    ]


def _detect_encrypt_tool() -> str | None:
    """检测可用的加密工具，返回工具名（按 7z > zip > rar 优先级），无则 None。"""
    for tool in ("7z", "zip", "rar"):
        if shutil.which(tool) is not None:
            return tool
    return None


def _build_encrypt_cmd(filepath: Path, target_path: Path, password: str, tool: str) -> list[str]:
    """根据工具类型构造加密命令。"""
    if tool == "7z":
        return ["7z", "a", f"-p{password}", "-mem=AES256", str(target_path), str(filepath)]
    if tool == "zip":
        return ["zip", "-r", f"-P{password}", str(target_path), str(filepath)]
    # rar
    return ["rar", "a", f"-p{password}", "-m5", str(target_path), str(filepath)]


def _create_unencrypted_zip(filepath: Path, target_path: Path) -> None:
    """使用标准库 zipfile 创建无加密 ZIP（无外部工具时的回退方案）。"""
    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if filepath.is_file():
            zf.write(filepath, filepath.name)
        elif filepath.is_dir():
            for sub in filepath.rglob("*"):
                if sub.is_file():
                    zf.write(sub, str(sub.relative_to(filepath)))


def _make_archive(filepath: Path, password: str, tool: str | None, replace: bool) -> bool:
    """为单个文件或目录创建加密 ZIP，返回是否成功。

    目标文件名为 ``<原文件名>.zip``（取 ``stem`` 去掉原扩展名）。
    已存在时根据 ``replace`` 决定覆盖或跳过。
    """
    target_path = filepath.parent / f"{filepath.stem}.zip"
    if target_path.exists():
        if replace:
            print(f"{target_path.name} 已存在，覆盖")
            target_path.unlink()
        else:
            print(f"{target_path.name} 已存在，跳过")
            return False

    kind = "文件" if filepath.is_file() else "目录"
    print(f"正在加密{kind}: {filepath.name} ...")

    if tool is not None:
        cmd = _build_encrypt_cmd(filepath, target_path, password, tool)
        result = run_command(cmd, capture=True)
        if result.failed:
            print(f"  加密失败 (返回码 {result.returncode}): {result.stderr.strip()}")
            return False
    else:
        try:
            _create_unencrypted_zip(filepath, target_path)
        except OSError as e:
            print(f"  加密失败: {e}")
            return False

    print(f"  完成: {target_path.name}")
    return True


@fcmd.tool("zipencrypt", help="加密目录下文件为密码保护的 ZIP")
def zip_encrypt(directory: str, password: str, replace: bool = False) -> None:
    """加密目录下的文件和子目录为带密码保护的 ZIP 压缩包。

    按优先级使用 7z / zip / rar 进行加密（7z 使用 AES256），无可用工具时
    回退到标准库 zipfile（无加密）。跳过以 ``.`` 或 ``__`` 开头的目录。

    Parameters
    ----------
    directory:
        包含待加密文件/目录的目录
    password:
        加密密码
    replace:
        是否覆盖已存在的 ZIP 文件（默认跳过）
    """
    if not password:
        print("密码不能为空")
        return

    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"目录不存在: {dir_path}")
        return
    if not dir_path.is_dir():
        print(f"路径不是目录: {dir_path}")
        return

    entries = _get_valid_entries(dir_path)
    if not entries:
        print(f"在 {dir_path} 中未找到目标文件")
        return

    tool = _detect_encrypt_tool()
    if tool is not None:
        print(f"使用 {tool} 进行加密")
    else:
        print("未找到 7z/zip/rar，将使用无加密 zipfile")

    print(f"开始加密 {len(entries)} 个文件/目录...")
    success_count = sum(1 for entry in entries if _make_archive(entry, password, tool, replace))
    print(f"加密完成: {success_count}/{len(entries)} 成功")
