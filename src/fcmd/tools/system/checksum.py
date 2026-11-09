"""checksum - 校验和计算器（Tk GUI）。

交互参考 ``bitool_gui.checksum``：选择 MD5 / SHA1 / SHA256 / SHA384 / SHA512 /
Blake2b / Blake2s，计算输入字符串或所选文件的十六进制摘要；勾选「比较校验」
后可填入期望值，结果区追加不区分大小写的比对结论，支持与 ``sha256sum`` 等
工具输出的摘要直接比对。

设置（上次算法 / 比较开关 / 窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/checksum.json``，下次启动自动恢复。

示例
----
    fcmd checksum          # 打开校验和计算器窗口
    checksum               # 全局命令等价入口
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.console import print_error

__all__ = [
    "ALGORITHMS",
    "ChecksumSettings",
    "calculate_file_checksum",
    "calculate_string_checksum",
    "config_path",
    "format_result",
    "load_settings",
    "normalize_algorithm",
    "save_settings",
    "valid_geometry",
    "verify_checksum",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "校验和计算器窗口版（Tk）：选择 MD5/SHA1/SHA256/SHA384/SHA512/Blake2b/Blake2s，"
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
# 文件哈希分块大小（64 KB）
CHUNK_SIZE = 64 * 1024

_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/checksum.json``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "ui" / "checksum.json"


@dataclass
class ChecksumSettings:
    """校验和计算器的持久化设置。"""

    algorithm: str = DEFAULT_ALGORITHM
    compare: bool = False
    geometry: str | None = None


def valid_geometry(text: str | None) -> str | None:
    """校验 Tk geometry 字符串（``WxH`` 或 ``WxH+X+Y``），非法时返回 ``None``。"""
    if text is not None and _GEOMETRY_RE.fullmatch(text):
        return text
    return None


def load_settings(path: Path | None = None) -> ChecksumSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时回退默认值。"""
    file = path or config_path()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ChecksumSettings()
    if not isinstance(raw, dict):
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
    file = path or config_path()
    payload = {
        "algorithm": settings.algorithm,
        "compare": settings.compare,
        "geometry": settings.geometry,
    }
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测，不依赖 Tk）
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
    hasher = hashlib.new(normalize_algorithm(algorithm).lower())
    hasher.update(content.encode("utf-8"))
    return hasher.hexdigest()


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
    hasher = hashlib.new(normalize_algorithm(algorithm).lower())
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_checksum(calculated: str, expected: str) -> bool:
    """比对摘要与期望值（忽略大小写与首尾空白）。"""
    return calculated.strip().lower() == expected.strip().lower()


def format_result(digest: str, expected: str | None = None) -> str:
    """生成结果区文本：摘要，启用比较时追加校验结论。"""
    if expected is None:
        return digest
    verdict = VERIFY_PASSED if verify_checksum(digest, expected) else VERIFY_FAILED
    return f"{digest}\n{verdict}"


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class ChecksumWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 校验和计算器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog

        self._tk = tk
        self._filedialog = filedialog
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)

        self.root = tk.Tk()
        self.root.title(f"校验和计算器 v{__version__}")
        self.root.geometry(valid_geometry(self._settings.geometry) or DEFAULT_GEOMETRY)
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._algorithm_var = tk.StringVar(value=self._settings.algorithm)
        self._string_var = tk.StringVar()
        self._file_var = tk.StringVar()
        self._compare_enabled_var = tk.BooleanVar(value=self._settings.compare)
        self._compare_var = tk.StringVar()

        self._build_widgets()
        self._toggle_compare()

    def _build_widgets(self) -> None:
        tk = self._tk
        source = tk.LabelFrame(self.root, text="数据源", padx=8, pady=8)
        source.pack(fill="x", padx=12, pady=(12, 4))

        string_row = tk.Frame(source)
        string_row.pack(fill="x")
        tk.Label(string_row, text="字符串：", width=8, anchor="w").pack(side="left")
        string_entry = tk.Entry(string_row, textvariable=self._string_var)
        string_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        string_entry.bind("<Return>", lambda _event: self._compute_string())
        tk.Button(string_row, text="计算", command=self._compute_string).pack(side="right")

        file_row = tk.Frame(source)
        file_row.pack(fill="x", pady=(8, 0))
        tk.Label(file_row, text="文件：", width=8, anchor="w").pack(side="left")
        tk.Entry(file_row, textvariable=self._file_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(file_row, text="浏览…", command=self._browse_file).pack(side="right")
        tk.Button(file_row, text="计算", command=self._compute_file).pack(side="right", padx=(0, 4))

        algorithm = tk.LabelFrame(self.root, text="算法", padx=8, pady=8)
        algorithm.pack(fill="x", padx=12, pady=4)
        for index, name in enumerate(ALGORITHMS):
            tk.Radiobutton(algorithm, text=name, value=name, variable=self._algorithm_var).grid(
                row=index // 4, column=index % 4, sticky="w", padx=(0, 16), pady=2
            )

        comparison = tk.LabelFrame(self.root, text="比较校验", padx=8, pady=8)
        comparison.pack(fill="x", padx=12, pady=4)
        tk.Checkbutton(
            comparison,
            text="启用比较（忽略大小写）",
            variable=self._compare_enabled_var,
            command=self._toggle_compare,
        ).pack(side="left")
        self._compare_entry = tk.Entry(comparison, textvariable=self._compare_var)
        self._compare_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        result = tk.LabelFrame(self.root, text="结果", padx=8, pady=8)
        result.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._result = tk.Text(result, height=6, wrap="word", state="disabled")
        scrollbar = tk.Scrollbar(result, orient="vertical", command=self._result.yview)
        self._result.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._result.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _toggle_compare(self) -> None:
        """启用 / 禁用比较输入框；关闭时清空期望值。"""
        enabled = bool(self._compare_enabled_var.get())
        self._compare_entry.configure(state="normal" if enabled else "disabled")
        if not enabled:
            self._compare_var.set("")

    def _browse_file(self) -> None:
        """选择待计算摘要的文件。"""
        filename = self._filedialog.askopenfilename(title="选择文件")
        if filename:
            self._file_var.set(filename)

    def _show_result(self, text: str) -> None:
        """把文本写入只读结果区。"""
        self._result.configure(state="normal")
        self._result.delete("1.0", "end")
        self._result.insert("1.0", text)
        self._result.configure(state="disabled")

    def _expected(self) -> str | None:
        """返回当前期望值；未启用比较时返回 ``None``。"""
        if not self._compare_enabled_var.get():
            return None
        return self._compare_var.get().strip()

    def _render(self, digest: str) -> None:
        """输出摘要，启用比较时校验期望值。"""
        expected = self._expected()
        if expected is not None and not expected:
            self._show_result("请输入比较值")
            return
        self._show_result(format_result(digest, expected))

    def _compute_string(self) -> None:
        """计算字符串摘要。"""
        content = self._string_var.get()
        if not content.strip():
            self._show_result("请输入字符串")
            return
        try:
            digest = calculate_string_checksum(content, self._algorithm_var.get())
        except (ValueError, OSError) as exc:
            self._show_result(f"错误：{exc}")
            return
        self._render(digest)

    def _compute_file(self) -> None:
        """计算文件摘要。"""
        raw = self._file_var.get().strip()
        if not raw or not Path(raw).is_file():
            self._show_result("请选择有效文件")
            return
        try:
            digest = calculate_file_checksum(Path(raw), self._algorithm_var.get())
        except (OSError, ValueError) as exc:
            self._show_result(f"错误：{exc}")
            return
        self._render(digest)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口。"""
        save_settings(
            ChecksumSettings(
                algorithm=self._algorithm_var.get(),
                compare=bool(self._compare_enabled_var.get()),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    try:
        window = ChecksumWindow(settings_path)
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动校验和计算器: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("checksum", help="打开校验和计算器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开校验和计算器窗口。"""
    run_gui()


@fcmd.main("checksum")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
