"""regexvalidatew - 正则校验器（Tk GUI）。

交互参考 ``bitool_gui.regexvalidate``：输入正则与测试文本后实时校验，展示
匹配数量、匹配位置、捕获组、全匹配与 findall 结果，并提供正则语法帮助
对话框。支持忽略大小写 / 多行 / DOTALL 标志与历史记录。

设置（当前输入、标志、历史记录与窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/regexvalidate.json``，下次启动自动恢复。

示例
----
    fcmd regexvalidatew   # 打开正则校验器窗口
    regexvalidatew        # 全局命令等价入口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.text.regexvalidate import (
    PatternValidationError,
    RegexValidator,
    build_flags,
    format_validator_report,
)

__all__ = [
    "MAX_HISTORY_ITEMS",
    "REGEX_EXAMPLES",
    "RegexExample",
    "RegexHistoryEntry",
    "RegexValidateSettings",
    "config_path",
    "example_label",
    "history_label",
    "load_settings",
    "push_history",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "正则校验器窗口版（Tk）：实时校验正则与文本，展示匹配位置/捕获组/全匹配/findall，"
    "内置 20 个典型示例，支持 ignorecase/multiline/dotall 标志、历史记录与语法帮助；"
    "设置持久化到 ~/.fcmd/ui/regexvalidate.json"
)

DEFAULT_GEOMETRY = "960x680+240+140"
MIN_WIDTH = 800
MIN_HEIGHT = 540
MAX_HISTORY_ITEMS = 20
HISTORY_DELAY_MS = 1000


@dataclass(frozen=True)
class RegexExample:
    """典型示例：一键填入正则、测试文本与标志。"""

    title: str
    pattern: str
    text: str
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False


#: 20 个典型示例，覆盖校验、提取、分组、反向引用与标志位
REGEX_EXAMPLES: tuple[RegexExample, ...] = (
    RegexExample("手机号", r"^1[3-9]\d{9}$", "13812345678"),
    RegexExample("邮箱", r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$", "user.name+tag@example.com"),
    RegexExample("IPv4 地址", r"^(?:\d{1,3}\.){3}\d{1,3}$", "192.168.1.1"),
    RegexExample("URL", r"https?://[^\s]+", "访问 https://example.com/path?q=1 查看"),
    RegexExample(
        "日期（命名分组）",
        r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$",
        "2026-11-09",
    ),
    RegexExample("时间 HH:MM:SS", r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$", "23:59:59"),
    RegexExample("身份证号", r"^\d{17}[\dXx]$", "110101199003071234"),
    RegexExample("中文字符", r"[\u4e00-\u9fa5]+", "正则表达式校验"),
    RegexExample("十六进制颜色", r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", "#4A90E2"),
    RegexExample("数字（整数/小数）", r"-?\d+(?:\.\d+)?", "价格 -12.5 元"),
    RegexExample("千分位数字", r"\d{1,3}(?:,\d{3})+", "1,234,567"),
    RegexExample("用户名", r"^[A-Za-z][A-Za-z0-9_]{3,15}$", "regex_fan"),
    RegexExample("强密码", r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$", "Abcd1234"),
    RegexExample(
        "域名",
        r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$",
        "www.example.com",
    ),
    RegexExample(
        "Windows 路径",
        r'^[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]+$',
        r"C:\Users\demo\file.txt",
    ),
    RegexExample("重复单词", r"\b(\w+)\s+\1\b", "this is is a test", ignorecase=True),
    RegexExample("HTML 标签", r"<(/?)(\w+)[^>]*>", '<div class="x">hello</div>'),
    RegexExample("Markdown 链接", r"\[([^\]]+)\]\(([^)]+)\)", "[Trae](https://trae.ai)"),
    RegexExample(
        "日志级别",
        r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b",
        "2026-11-09 ERROR 请求失败",
        ignorecase=True,
    ),
    RegexExample("行尾空白", r"[ \t]+$", "line with spaces   \nsecond line", multiline=True),
)


def example_label(index: int, example: RegexExample) -> str:
    """生成示例下拉项文本：序号 + 标题。"""
    return f"{index + 1}. {example.title}"


REGEX_HELP_TEXT = """常用元字符
  .        任意字符（默认不含换行）
  \\d \\D    数字 / 非数字
  \\w \\W    单词字符 / 非单词字符
  \\s \\S    空白 / 非空白
  ^ $      行首 / 行尾
  [...]    字符集合，[^...] 取反
  |        或

量词
  * + ?        零次或多次 / 一次或多次 / 零次或一次
  {n} {n,m}    恰好 n 次 / n 到 m 次
  *? +? ??     非贪婪版本

分组
  (...)          捕获组
  (?:...)        非捕获组
  (?P<name>...)  命名组
  (?P=name)      按名称反向引用

标志
  ignorecase    忽略大小写（re.IGNORECASE）
  multiline     多行模式（re.MULTILINE，^ / $ 匹配每行）
  dotall        DOTALL 模式（re.DOTALL，. 匹配换行）

提示
  校验采用搜索语义（search）：文本中任一处匹配即视为匹配；
  「全匹配」要求整段文本完全匹配。
"""


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/regexvalidate.json``。"""
    return _guiconfig.config_path("regexvalidate")


@dataclass(frozen=True)
class RegexHistoryEntry:
    """一条历史记录：正则、测试文本与三个标志开关。"""

    pattern: str
    test_string: str = ""
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        """转为可 JSON 序列化的字典。"""
        return {
            "pattern": self.pattern,
            "test_string": self.test_string,
            "ignorecase": self.ignorecase,
            "multiline": self.multiline,
            "dotall": self.dotall,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RegexHistoryEntry | None:
        """从 JSON 对象还原；缺少有效 ``pattern`` 时返回 ``None``。"""
        if not isinstance(raw, dict):
            return None
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return None
        test_string = raw.get("test_string")
        return cls(
            pattern=pattern,
            test_string=test_string if isinstance(test_string, str) else "",
            ignorecase=raw.get("ignorecase") is True,
            multiline=raw.get("multiline") is True,
            dotall=raw.get("dotall") is True,
        )


@dataclass
class RegexValidateSettings:
    """正则校验器的持久化设置。"""

    pattern: str = ""
    test_string: str = ""
    ignorecase: bool = False
    multiline: bool = False
    dotall: bool = False
    history: list[RegexHistoryEntry] = field(default_factory=list)
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def history_label(index: int, entry: RegexHistoryEntry) -> str:
    """生成历史下拉项文本：序号 + 截断后的正则。"""
    pattern = entry.pattern
    if len(pattern) > 50:
        pattern = pattern[:50] + "…"
    return f"{index + 1}. {pattern}"


def push_history(
    history: list[RegexHistoryEntry],
    entry: RegexHistoryEntry,
    limit: int = MAX_HISTORY_ITEMS,
) -> list[RegexHistoryEntry]:
    """把条目放到历史最前：完全相同的条目去重，超过上限截断。

    正则为空的条目不入历史，原样返回副本。
    """
    if not entry.pattern:
        return list(history)
    return [entry, *(item for item in history if item != entry)][:limit]


def load_settings(path: Path | None = None) -> RegexValidateSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return RegexValidateSettings()

    settings = RegexValidateSettings()
    pattern = raw.get("pattern")
    test_string = raw.get("test_string")
    geometry = raw.get("geometry")
    if isinstance(pattern, str):
        settings.pattern = pattern
    if isinstance(test_string, str):
        settings.test_string = test_string
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    settings.ignorecase = raw.get("ignorecase") is True
    settings.multiline = raw.get("multiline") is True
    settings.dotall = raw.get("dotall") is True

    raw_history = raw.get("history")
    if isinstance(raw_history, list):
        for item in raw_history:
            entry = RegexHistoryEntry.from_dict(item)
            if entry is not None:
                settings.history.append(entry)
        settings.history = settings.history[:MAX_HISTORY_ITEMS]
    return settings


def save_settings(settings: RegexValidateSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "pattern": settings.pattern,
        "test_string": settings.test_string,
        "ignorecase": settings.ignorecase,
        "multiline": settings.multiline,
        "dotall": settings.dotall,
        "history": [entry.to_dict() for entry in settings.history],
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class RegexValidateWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 正则校验器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._history = list(self._settings.history)
        self._history_job: str | None = None
        self._loading = False

        self.root, self._theme = _guiwindow.create_root(
            title=f"正则校验器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._pattern_var = tk.StringVar(value=self._settings.pattern)
        self._ignorecase_var = tk.BooleanVar(value=self._settings.ignorecase)
        self._multiline_var = tk.BooleanVar(value=self._settings.multiline)
        self._dotall_var = tk.BooleanVar(value=self._settings.dotall)
        self._status_var = tk.StringVar(value="校验结果：")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._pattern_var.trace_add("write", self._on_change)
        self._text.bind("<KeyRelease>", self._on_change)
        if self._settings.pattern or self._settings.test_string:
            self._refresh()

    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        palette = self._theme.palette
        self._build_menu()
        self._configure_styles()

        container = ttk.Frame(
            self.root,
            padding=(self._theme.spacing.sm, self._theme.spacing.sm, self._theme.spacing.sm, self._theme.spacing.sm),
        )
        container.pack(fill="both", expand=True)

        pattern_frame = ttk.LabelFrame(
            container, text=" 正则 ", padding=(self._theme.spacing.sm, self._theme.spacing.xs)
        )
        pattern_frame.pack(fill="x")
        pattern_frame.columnconfigure(1, weight=3)
        pattern_frame.columnconfigure(3, weight=2)

        ttk.Label(pattern_frame, text="模式", width=6, anchor="w").grid(
            row=0, column=0, sticky="w", pady=self._theme.spacing.hairline
        )
        ttk.Entry(pattern_frame, textvariable=self._pattern_var, font=_guitheme.MONO_FONT).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(self._theme.spacing.xs, self._theme.spacing.sm),
            pady=self._theme.spacing.hairline,
        )
        ttk.Button(pattern_frame, text="语法帮助", command=self._show_help).grid(
            row=0, column=4, sticky="e", pady=self._theme.spacing.hairline
        )

        ttk.Label(pattern_frame, text="示例", width=6, anchor="w").grid(
            row=1, column=0, sticky="w", pady=self._theme.spacing.hairline
        )
        self._example_combo = ttk.Combobox(pattern_frame, state="readonly")
        self._example_combo.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(self._theme.spacing.xs, self._theme.spacing.sm),
            pady=self._theme.spacing.hairline,
        )
        self._example_combo.bind("<<ComboboxSelected>>", self._on_example_selected)
        self._update_example_combo()

        ttk.Label(pattern_frame, text="历史", width=6, anchor="w").grid(
            row=1, column=2, sticky="w", pady=self._theme.spacing.hairline
        )
        self._combo = ttk.Combobox(pattern_frame, state="readonly")
        self._combo.grid(
            row=1, column=3, sticky="ew", padx=(self._theme.spacing.xs, 0), pady=self._theme.spacing.hairline
        )
        self._combo.bind("<<ComboboxSelected>>", self._on_history_selected)
        self._update_history_combo()

        ttk.Label(pattern_frame, text="标志", width=6, anchor="w").grid(
            row=2, column=0, sticky="w", pady=self._theme.spacing.hairline
        )
        flags_row = ttk.Frame(pattern_frame)
        flags_row.grid(
            row=2,
            column=1,
            columnspan=4,
            sticky="w",
            padx=(self._theme.spacing.xs, 0),
            pady=self._theme.spacing.hairline,
        )
        ttk.Checkbutton(flags_row, text="忽略大小写", variable=self._ignorecase_var, command=self._refresh).pack(
            side="left"
        )
        ttk.Checkbutton(flags_row, text="多行（^/$）", variable=self._multiline_var, command=self._refresh).pack(
            side="left", padx=(self._theme.spacing.md, 0)
        )
        ttk.Checkbutton(flags_row, text="DOTALL（. 匹配换行）", variable=self._dotall_var, command=self._refresh).pack(
            side="left", padx=(self._theme.spacing.md, 0)
        )

        # 上下分栏可拖拽：测试文本与校验结果各自按权重分配初始空间
        panes = ttk.Panedwindow(container, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))

        text_frame = ttk.LabelFrame(panes, text=" 测试文本 ", padding=(self._theme.spacing.sm, self._theme.spacing.xs))
        self._text = tk.Text(
            text_frame,
            height=6,
            wrap="word",
            font=_guitheme.MONO_FONT,
            padx=self._theme.spacing.sm,
            pady=self._theme.spacing.sm,
            undo=True,
            relief="flat",
            highlightthickness=1,
            highlightbackground=palette.border,
            highlightcolor=palette.accent,
        )
        text_scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=text_scrollbar.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        panes.add(text_frame, weight=2)
        if self._settings.test_string:
            self._text.insert("1.0", self._settings.test_string)

        result_frame = ttk.LabelFrame(
            panes, text=" 校验结果 ", padding=(self._theme.spacing.sm, self._theme.spacing.xs)
        )
        self._result = tk.Text(
            result_frame,
            height=12,
            wrap="none",
            state="disabled",
            font=_guitheme.MONO_FONT,
            padx=self._theme.spacing.sm,
            pady=self._theme.spacing.sm,
            relief="flat",
            highlightthickness=1,
            highlightbackground=palette.border,
            highlightcolor=palette.accent,
        )
        result_scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self._result.yview)
        result_xscrollbar = ttk.Scrollbar(result_frame, orient="horizontal", command=self._result.xview)
        self._result.configure(yscrollcommand=result_scrollbar.set, xscrollcommand=result_xscrollbar.set)
        self._result.grid(row=0, column=0, sticky="nsew")
        result_scrollbar.grid(row=0, column=1, sticky="ns")
        result_xscrollbar.grid(row=1, column=0, sticky="ew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        panes.add(result_frame, weight=3)

        status_bar = ttk.Frame(container)
        status_bar.pack(fill="x", pady=(self._theme.spacing.xs, 0))
        self._status_label = ttk.Label(status_bar, textvariable=self._status_var, style="Status.TLabel")
        self._status_label.pack(side="left")
        ttk.Label(status_bar, text="输入时自动校验", style="Hint.TLabel").pack(side="right")

    def _configure_styles(self) -> None:
        """定义状态栏配色，跟随当前主题，避免混用 tk/ttk 控件导致视觉不一致。"""
        palette = self._theme.palette
        style = self._ttk.Style(self.root)
        style.configure("Status.TLabel", padding=(0, self._theme.spacing.xxs))
        style.configure("Ok.TLabel", foreground=palette.success)
        style.configure("Error.TLabel", foreground=palette.danger)
        style.configure("Hint.TLabel", foreground=palette.muted)

    def _build_menu(self) -> None:
        _guiwindow.build_menu(
            self.root,
            on_close=self.close,
            on_about=self._show_about,
            help_items=(("语法帮助", self._show_help),),
        )

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _current_flags(self) -> int:
        """读取三个标志开关并组合为 re 标志位。"""
        return build_flags(
            ignorecase=bool(self._ignorecase_var.get()),
            multiline=bool(self._multiline_var.get()),
            dotall=bool(self._dotall_var.get()),
        )

    def _refresh(self) -> None:
        """实时校验并在结果区展示报告。"""
        pattern = self._pattern_var.get()
        if not pattern:
            self._show_result("请输入正则表达式", None)
            return
        text = self._text.get("1.0", "end-1c")
        try:
            validator = RegexValidator(pattern, self._current_flags())
        except PatternValidationError as exc:
            self._show_result(str(exc), False)
            return
        self._show_result(format_validator_report(validator, text), validator.is_valid(text))

    def _show_result(self, text: str, ok: bool | None) -> None:
        """写入只读结果区并更新状态标签颜色。"""
        self._result.configure(state="normal")
        self._result.delete("1.0", "end")
        self._result.insert("1.0", text)
        self._result.configure(state="disabled")
        if ok is None:
            self._status_var.set("校验结果：")
            self._status_label.configure(style="Status.TLabel")
        elif ok:
            self._status_var.set("校验结果：✓ 匹配")
            self._status_label.configure(style="Ok.TLabel")
        else:
            self._status_var.set("校验结果：✗ 不匹配")
            self._status_label.configure(style="Error.TLabel")

    def _on_change(self, *_args: object) -> None:
        """输入变化：立即校验，并延迟记录历史（避免每次按键都写盘）。"""
        if self._loading:
            return
        self._refresh()
        if self._history_job is not None:
            self.root.after_cancel(self._history_job)
        self._history_job = self.root.after(HISTORY_DELAY_MS, self._record_history)

    def _record_history(self) -> None:
        """把当前输入写入历史并持久化。"""
        self._history_job = None
        entry = RegexHistoryEntry(
            pattern=self._pattern_var.get(),
            test_string=self._text.get("1.0", "end-1c"),
            ignorecase=bool(self._ignorecase_var.get()),
            multiline=bool(self._multiline_var.get()),
            dotall=bool(self._dotall_var.get()),
        )
        if not entry.pattern:
            return
        self._history = push_history(self._history, entry)
        self._save()
        self._update_history_combo()

    def _apply_inputs(
        self,
        pattern: str,
        text: str,
        ignorecase: bool,
        multiline: bool,
        dotall: bool,
    ) -> None:
        """批量填入正则、文本与标志，期间抑制变更回调后统一刷新。"""
        self._loading = True
        self._pattern_var.set(pattern)
        self._text.delete("1.0", "end")
        self._text.insert("1.0", text)
        self._ignorecase_var.set(ignorecase)
        self._multiline_var.set(multiline)
        self._dotall_var.set(dotall)
        self._loading = False
        self._refresh()

    def _update_example_combo(self) -> None:
        """按内置示例重建下拉项，并回到占位项。"""
        values = [f"-- 选择示例（{len(REGEX_EXAMPLES)} 个） --"]
        values.extend(example_label(index, example) for index, example in enumerate(REGEX_EXAMPLES))
        self._example_combo.configure(values=values)
        self._example_combo.current(0)

    def _on_example_selected(self, _event: object) -> None:
        """载入选中示例的正则、文本与标志，并保持该示例在下拉框中显示。"""
        index = self._example_combo.current()
        if index <= 0 or index > len(REGEX_EXAMPLES):
            return
        example = REGEX_EXAMPLES[index - 1]
        self._apply_inputs(example.pattern, example.text, example.ignorecase, example.multiline, example.dotall)

    def _update_history_combo(self) -> None:
        """按当前历史重建下拉项，并回到占位项。"""
        values = ["-- 选择历史 --"]
        values.extend(history_label(index, entry) for index, entry in enumerate(self._history))
        self._combo.configure(values=values)
        self._combo.current(0)

    def _on_history_selected(self, _event: object) -> None:
        """恢复选中历史条目，并保持该条目在下拉框中显示。"""
        index = self._combo.current()
        if index <= 0 or index > len(self._history):
            return
        entry = self._history[index - 1]
        self._apply_inputs(entry.pattern, entry.test_string, entry.ignorecase, entry.multiline, entry.dotall)

    def _show_help(self) -> None:
        """打开正则语法帮助窗口。"""
        tk = self._tk
        dialog = tk.Toplevel(self.root)
        dialog.title("正则语法帮助")
        dialog.transient(self.root)
        _guiwindow.center_over_parent(dialog, self.root, width=640, height=560)
        text = tk.Text(dialog, wrap="word", padx=self._theme.spacing.md, pady=self._theme.spacing.md)
        scrollbar = tk.Scrollbar(dialog, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", REGEX_HELP_TEXT)
        text.configure(state="disabled")

    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"正则校验器 v{__version__}\n\n"
            "实时校验正则与文本\n"
            "展示匹配位置、捕获组、全匹配与 findall\n"
            "支持历史记录与正则语法帮助",
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        """保存当前设置（历史记录、输入、标志与窗口位置）。"""
        save_settings(
            RegexValidateSettings(
                pattern=self._pattern_var.get(),
                test_string=self._text.get("1.0", "end-1c"),
                ignorecase=bool(self._ignorecase_var.get()),
                multiline=bool(self._multiline_var.get()),
                dotall=bool(self._dotall_var.get()),
                history=list(self._history),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )

    def close(self) -> None:
        """保存设置并退出窗口。"""
        if self._history_job is not None:
            self.root.after_cancel(self._history_job)
            self._history_job = None
        self._save()
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    _guiwindow.run_window(lambda: RegexValidateWindow(settings_path), "正则校验器")


@fcmd.tool("regexvalidatew", help="打开正则校验器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开正则校验器窗口。"""
    run_gui()


@fcmd.main("regexvalidatew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
