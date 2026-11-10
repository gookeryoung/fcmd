"""pdfcrypt - PDF 批量加密 / 解密。

扫描目录中的 PDF，按加密状态筛选后批量加密或解密（并发执行），输出文件与
源文件同目录：

* 加密输出 ``<原名>.enc.pdf``
* 解密输出 ``<原名>.dec.pdf``

CLI 与 Tk 窗口版（:mod:`fcmd.tools.pdf.pdfcryptw`）共用本模块的纯函数，
保证两条入口的加密参数（128 位 AES）与结果一致。

示例
----
    fcmd pdfcrypt encrypt mypassword
    fcmd pdfcrypt decrypt mypassword --directory docs --workers 8
"""

from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import fcmd
from fcmd.tools._deps import missing_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "DECRYPTED_SUFFIX",
    "DEFAULT_WORKERS",
    "ENCRYPTED_SUFFIX",
    "MIN_PASSWORD_LENGTH",
    "BatchReport",
    "CryptResult",
    "collect_pdf_files",
    "decrypt_pdf",
    "encrypt_pdf",
    "is_encrypted",
    "process_files",
    "validate_password",
]

HAS_PYPDF = importlib.util.find_spec("pypdf") is not None

# pypdf 惰性占位（_require_pypdf 首次调用填充）
pypdf: Any = None

DEFAULT_WORKERS: Final[int] = 4
MIN_PASSWORD_LENGTH: Final[int] = 6
ENCRYPTED_SUFFIX: Final[str] = ".enc.pdf"
DECRYPTED_SUFFIX: Final[str] = ".dec.pdf"


def _require_pypdf() -> bool:
    """pypdf 未安装时返回 ``False``；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        return missing_dependency("pypdf", "pdf")
    global pypdf  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if pypdf is None:
        import pypdf as _pypdf

        pypdf = _pypdf
    return True


def validate_password(password: str) -> str:
    """校验密码非空，返回原密码。

    Raises
    ------
    ValueError
        密码为空。
    """
    if not password:
        raise ValueError("密码不能为空")
    return password


def is_encrypted(path: Path) -> bool:
    """判断 PDF 是否已加密。

    Raises
    ------
    RuntimeError
        pypdf 未安装。
    OSError
        文件无法读取。
    """
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")
    with path.open("rb") as stream:
        return bool(pypdf.PdfReader(stream).is_encrypted)


def encrypt_pdf(source: Path, password: str, output_path: Path | None = None) -> Path:
    """加密单个 PDF（128 位 AES），返回输出路径。

    默认输出到 ``<原名>.enc.pdf``（与源文件同目录）。

    Raises
    ------
    ValueError
        密码为空或源文件已加密。
    RuntimeError
        pypdf 未安装。
    """
    validate_password(password)
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")

    reader = pypdf.PdfReader(str(source))
    if reader.is_encrypted:
        raise ValueError(f"源文件已加密，无法再次加密: {source}")

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=password, owner_password=password, use_128bit=True)

    target = output_path or source.with_suffix(ENCRYPTED_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        writer.write(stream)
    return target


def decrypt_pdf(source: Path, password: str, output_path: Path | None = None) -> Path:
    """解密单个 PDF，返回输出路径。

    默认输出到 ``<原名>.dec.pdf``（与源文件同目录）。

    Raises
    ------
    ValueError
        密码为空、源文件未加密或密码错误。
    RuntimeError
        pypdf 未安装。
    """
    validate_password(password)
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")

    reader = pypdf.PdfReader(str(source))
    if not reader.is_encrypted:
        raise ValueError(f"文件未加密，无需解密: {source}")
    if not reader.decrypt(password):
        raise ValueError(f"密码错误或文件损坏: {source.name}")

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    target = output_path or source.with_suffix(DECRYPTED_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        writer.write(stream)
    return target


def collect_pdf_files(directory: Path, *, encrypted: bool | None = None, recursive: bool = True) -> list[Path]:
    """收集目录中的 PDF 文件（按路径排序）。

    Parameters
    ----------
    directory:
        待扫描目录。
    encrypted:
        ``True`` 只保留已加密文件，``False`` 只保留未加密文件，``None`` 不筛选。
    recursive:
        是否递归子目录。

    Raises
    ------
    FileNotFoundError
        目录不存在。
    NotADirectoryError
        路径不是目录。
    RuntimeError
        pypdf 未安装且需要按加密状态筛选。
    """
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")

    candidates = directory.rglob("*") if recursive else directory.glob("*")
    paths = sorted(path for path in candidates if path.is_file() and path.suffix.lower() == ".pdf")
    if encrypted is None:
        return paths

    result: list[Path] = []
    for path in paths:
        try:
            matched = is_encrypted(path) is encrypted
        except Exception:  # 损坏或无法解析的 PDF 跳过，不影响其余文件
            continue
        if matched:
            result.append(path)
    return result


@dataclass(frozen=True)
class CryptResult:
    """单个文件的处理结果。

    ``output`` 为 ``None`` 表示失败，原因见 ``error``。
    """

    source: Path
    output: Path | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """是否处理成功。"""
        return self.output is not None


@dataclass(frozen=True)
class BatchReport:
    """批量处理汇总。"""

    results: tuple[CryptResult, ...]

    @property
    def succeeded(self) -> tuple[CryptResult, ...]:
        """成功条目。"""
        return tuple(item for item in self.results if item.ok)

    @property
    def failed(self) -> tuple[CryptResult, ...]:
        """失败条目。"""
        return tuple(item for item in self.results if not item.ok)


def process_files(
    files: Sequence[Path],
    password: str,
    *,
    decrypt: bool = False,
    workers: int = DEFAULT_WORKERS,
    progress: Callable[[int, int, Path, CryptResult], None] | None = None,
) -> BatchReport:
    """并发加密 / 解密文件列表，返回按输入顺序排列的结果。

    Parameters
    ----------
    files:
        待处理 PDF 文件列表。
    password:
        加密或解密密码。
    decrypt:
        ``True`` 执行解密，``False`` 执行加密。
    workers:
        并发线程数（小于 1 时按 1 处理）。
    progress:
        进度回调 ``(已完成数, 总数, 源文件, 结果)``。
    """
    validate_password(password)
    if not files:
        return BatchReport(())

    worker = decrypt_pdf if decrypt else encrypt_pdf
    order = {path: index for index, path in enumerate(files)}
    results: list[CryptResult] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(worker, path, password): path for path in files}
        for done, future in enumerate(as_completed(futures), start=1):
            source = futures[future]
            try:
                result = CryptResult(source, future.result())
            except Exception as exc:  # 单个文件失败记入结果，不中断整批处理
                result = CryptResult(source, None, str(exc))
            results.append(result)
            if progress is not None:
                progress(done, len(files), source, result)

    results.sort(key=lambda item: order[item.source])
    return BatchReport(tuple(results))


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def _run_cli(directory: Path, password: str, workers: int, decrypt: bool, recursive: bool) -> None:
    """CLI 公共流程：筛选文件、校验、批量处理并打印汇总。"""
    if not _require_pypdf():
        return
    try:
        validate_password(password)
        files = collect_pdf_files(directory, encrypted=decrypt, recursive=recursive)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误: {exc}")
        return

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"警告: 密码长度小于 {MIN_PASSWORD_LENGTH} 字符，建议使用更强的密码")

    action = "解密" if decrypt else "加密"
    kind = "已加密" if decrypt else "未加密"
    if not files:
        print(f"目录中未找到{kind}的 PDF 文件: {directory}")
        return

    def progress(done: int, total: int, source: Path, result: CryptResult) -> None:
        if result.ok:
            print(f"[{done}/{total}] {action}完成: {source.name}")
        else:
            print(f"[{done}/{total}] {action}失败: {source.name} ({result.error})")

    report = process_files(files, password, decrypt=decrypt, workers=workers, progress=progress)
    print(f"{action}汇总: 成功 {len(report.succeeded)} 个，失败 {len(report.failed)} 个")


@fcmd.tool("pdfcrypt", subcommand="encrypt", help="批量加密目录中的 PDF 文件")
def pdfcrypt_encrypt(
    password: str,
    directory: Path = Path(),
    workers: int = DEFAULT_WORKERS,
    recursive: bool = True,
) -> None:
    """批量加密目录中的 PDF 文件（pdfcrypt encrypt 子命令）。

    Parameters
    ----------
    password:
        加密密码（同时作为用户密码与所有者密码）。
    directory:
        待扫描目录（默认当前目录）。
    workers:
        并发线程数（默认 4）。
    recursive:
        递归子目录（默认开启）。
    """
    _run_cli(directory, password, workers, decrypt=False, recursive=recursive)


@fcmd.tool("pdfcrypt", subcommand="decrypt", help="批量解密目录中的 PDF 文件")
def pdfcrypt_decrypt(
    password: str,
    directory: Path = Path(),
    workers: int = DEFAULT_WORKERS,
    recursive: bool = True,
) -> None:
    """批量解密目录中的 PDF 文件（pdfcrypt decrypt 子命令）。

    Parameters
    ----------
    password:
        解密密码。
    directory:
        待扫描目录（默认当前目录）。
    workers:
        并发线程数（默认 4）。
    recursive:
        递归子目录（默认开启）。
    """
    _run_cli(directory, password, workers, decrypt=True, recursive=recursive)


@fcmd.main("pdfcrypt")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
