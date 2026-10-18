"""zipencrypt - ZIP 加密工具。

使用 pyzipper 将目录下的文件和子目录加密为 AES-256 加密的 ZIP 压缩包。
逐条目输出进度；跳过以 ``.`` / ``__`` 开头的目录与已有压缩包。

注意：AES ZIP 需支持 AES 加密的解压器（7-Zip / WinRAR / Bandizip 等），
Windows 资源管理器自带的解压功能无法打开。

示例
----
    fcmd zipencrypt . mypassword                # 加密当前目录下文件
    fcmd zipencrypt /path/to/dir mypassword     # 加密指定目录
    fcmd zipencrypt . mypassword --replace      # 覆盖已有 ZIP 文件
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fcmd

__all__ = ["zip_encrypt"]

# 跳过的目录名前缀（文件不受此限制，仅目录按此过滤）
_SKIP_PREFIXES: tuple[str, ...] = (".", "__")

# 跳过的压缩包扩展名（避免对已有压缩包再压缩）
_ARCHIVE_EXTS: frozenset[str] = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2"})

# 惰性导入的 pyzipper 模块（首次调用时填充）
_pyzipper: Any = None


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


def _require_pyzipper() -> Any | None:
    """惰性导入 pyzipper；缺失时返回 ``None``（由调用方打印安装提示）。

    pyzipper 只用于写 AES ZIP，``import fcmd`` 与工具发现路径不应为其付费。
    """
    global _pyzipper  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if _pyzipper is not None:
        return _pyzipper
    try:
        import pyzipper
    except ImportError:  # pragma: no cover - 由 extra 保证，缺库时给出安装提示
        return None
    _pyzipper = pyzipper
    return pyzipper


def _write_aes_zip(module: Any, filepath: Path, target_path: Path, password: str) -> None:
    """用 pyzipper 写 AES-256 加密 ZIP。

    单文件条目的 arcname 为文件名本身；目录条目的 arcname 为相对路径。
    密码按 UTF-8 编码（pyzipper 只接受 bytes）。
    """
    with module.AESZipFile(
        target_path,
        "w",
        compression=module.ZIP_DEFLATED,
        encryption=module.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        if filepath.is_file():
            zf.write(filepath, filepath.name)
            return
        for sub in filepath.rglob("*"):
            if sub.is_file():
                zf.write(sub, str(sub.relative_to(filepath)))


def _make_archive(filepath: Path, password: str, module: Any, replace: bool) -> bool:
    """为单个文件或目录创建 AES 加密 ZIP，返回是否成功。

    目标文件名为 ``<原文件名>.zip``（取 ``stem`` 去掉原扩展名）。
    已存在时根据 ``replace`` 决定覆盖或跳过；写入失败时清理半成品文件，
    避免下次运行被"已存在"规则挡住。
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
    try:
        _write_aes_zip(module, filepath, target_path, password)
    except OSError as e:
        print(f"  加密失败: {e}")
        target_path.unlink(missing_ok=True)
        return False

    print(f"  完成: {target_path.name}")
    return True


@fcmd.tool("zipencrypt", help="加密目录下文件为密码保护的 ZIP")
def zip_encrypt(directory: str, password: str, replace: bool = False) -> None:
    """加密目录下的文件和子目录为 AES-256 加密的 ZIP 压缩包。

    使用 pyzipper 的 ``WZ_AES`` 加密（需 ``pip install fcmd[archive]``）。
    跳过以 ``.`` 或 ``__`` 开头的目录与已有压缩包文件。

    Parameters
    ----------
    directory:
        包含待加密文件/目录的目录
    password:
        加密密码（UTF-8 编码后作为 AES 密钥口令）
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

    module = _require_pyzipper()
    if module is None:
        print("未安装 pyzipper 库，请安装: pip install fcmd[archive]")
        return

    print("使用 pyzipper 进行 AES-256 加密")
    print(f"开始加密 {len(entries)} 个文件/目录...")
    success_count = sum(1 for entry in entries if _make_archive(entry, password, module, replace))
    print(f"加密完成: {success_count}/{len(entries)} 成功")


@fcmd.main("zipencrypt")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
