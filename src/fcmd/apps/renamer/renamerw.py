"""renamerw - 批量重命名窗口版（Tk）。

上方「文件」表同时列出名称、新名与状态，下方编辑规则流水线（替换 / 重排 / 搬移
/ 插入 / 大小写 / 计数器），规则或文件变动后自动重算；确认后两阶段执行，可撤销
本次运行结果。规则可导出 / 导入 JSON；

窗口设置（文件列表、规则、递归与排序选项、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/renamerw.json``，下次启动自动恢复。

示例
----
    fcmd renamerw        # 打开批量重命名窗口
    renamerw             # 全局命令等价入口
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.renamer._core import (
    ANCHOR_AFTER,
    ANCHOR_BEFORE,
    ANCHOR_END,
    ANCHOR_INDEX,
    ANCHOR_START,
    FIELD_EXT,
    FIELD_STEM,
    SORT_KEYS,
    SORT_NAME,
    STATUS_CONFLICT,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_UNCHANGED,
    RenamePreview,
    RenameReport,
    RenameRule,
    RuleError,
    build_preview,
    collect_targets,
    execute_plan,
    format_preview_status,
    make_target,
    parse_rules,
    preview_to_plan,
    rules_to_json,
    undo_report,
    unique_paths,
    validate_rules,
)
from fcmd.apps.renamer._examples import EXAMPLES, HELP_TEXT, Example
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ANCHOR_TEXT",
    "CASE_TEXT",
    "DEFAULT_GEOMETRY",
    "FIELD_TEXT",
    "KIND_TEXT",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SORT_TEXT",
    "STATUS_TEXT",
    "RenamerSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "rule_to_row",
    "rules_from_text",
    "rules_to_text",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "批量重命名窗口版（Tk）：目标文件/目录选择、规则流水线编辑（正则替换、捕获组重排、"
    "匹配文本搬移、插入、大小写、计数器）、勾选前预览与冲突/非法标记、两阶段执行与撤销、"
    "规则 JSON 导入导出；设置持久化到 ~/.fcmd/ui/renamerw.json"
)

DEFAULT_GEOMETRY = "1180x820+120+60"
MIN_WIDTH = 980
MIN_HEIGHT = 680

KIND_TEXT = {
    "replace": "替换",
    "reorder": "重排",
    "move": "搬移",
    "insert": "插入",
    "case": "大小写",
    "sequence": "计数器",
}
FIELD_TEXT = {FIELD_STEM: "名称", FIELD_EXT: "扩展名"}
ANCHOR_TEXT = {
    ANCHOR_START: "开头",
    ANCHOR_END: "结尾",
    ANCHOR_BEFORE: "锚点前",
    ANCHOR_AFTER: "锚点后",
    ANCHOR_INDEX: "指定位置",
}
CASE_TEXT = {
    "keep": "保持",
    "lower": "小写",
    "upper": "大写",
    "title": "每词首字母",
    "sentence": "仅首字母",
}
SORT_TEXT = {"name": "名称", "modified": "修改时间", "created": "创建时间"}
STATUS_TEXT = {
    STATUS_OK: "可执行",
    STATUS_UNCHANGED: "未变化",
    STATUS_INVALID: "非法",
    STATUS_CONFLICT: "冲突",
}


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测）
# ---------------------------------------------------------------------- #
def rules_from_text(text: str) -> tuple[RenameRule, ...]:
    """解析规则 JSON 文本；空文本视为空规则集。"""
    if not text.strip():
        return ()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则 JSON 解析失败：{exc}") from exc
    return parse_rules(raw)


def rules_to_text(rules: list[RenameRule] | tuple[RenameRule, ...]) -> str:
    """把规则集格式化为 JSON 文本。"""
    return json.dumps(rules_to_json(rules), ensure_ascii=False, indent=2)


def rule_to_row(rule: RenameRule) -> tuple[str, str, str, str]:
    """把规则转成表格行：类型 / 作用域 / 规则参数 / 说明。"""
    kind = KIND_TEXT.get(rule.kind, rule.kind)
    scope = FIELD_TEXT.get(rule.field, rule.field)
    if rule.kind in ("replace", "reorder", "move"):
        params = f"{rule.pattern}  ⇒  {rule.replacement}" if rule.replacement else rule.pattern
    elif rule.kind == "insert":
        params = rule.text
    elif rule.kind == "case":
        params = CASE_TEXT.get(rule.mode, rule.mode)
    else:
        params = f"{rule.text}{rule.start}..（步长 {rule.step}，{rule.digits} 位）"
    return kind, scope, params, _rule_detail(rule)


def _rule_detail(rule: RenameRule) -> str:
    """生成规则的一行人类可读说明。"""
    if rule.kind == "replace":
        detail = "正则替换"
        if rule.occurrence:
            detail += f"，仅第 {rule.occurrence} 处"
        return detail + ("（忽略大小写）" if rule.ignore_case else "")
    if rule.kind == "reorder":
        return f"捕获组按模板重排：{rule.replacement}"
    if rule.kind == "move":
        detail = f"匹配文本搬到{ANCHOR_TEXT.get(rule.anchor, rule.anchor)}"
        if rule.anchor in (ANCHOR_BEFORE, ANCHOR_AFTER):
            detail += f"「{rule.anchor_text}」"
        elif rule.anchor == ANCHOR_INDEX:
            detail += f" {rule.position}"
        return detail
    if rule.kind == "insert":
        return f"在{ANCHOR_TEXT.get(rule.anchor, rule.anchor)}插入"
    if rule.kind == "case":
        return f"转为{CASE_TEXT.get(rule.mode, rule.mode)}"
    return f"计数器：{rule.start} 起步长 {rule.step}，{rule.digits} 位补零"


def config_path() -> Path:
    """返回设置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/renamerw.json``。"""
    return _guiconfig.config_path("renamerw")


valid_geometry = _guiconfig.valid_geometry


@dataclass
class RenamerSettings:
    """窗口与配置文件共用的设置。"""

    targets: list[str] = field(default_factory=list)
    rules_json: str = "[]"
    recursive: bool = False
    include_dirs: bool = False
    sort_key: str = SORT_NAME
    geometry: str | None = None


def settings_to_mapping(settings: RenamerSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典。"""
    return {
        "targets": list(settings.targets),
        "rules_json": settings.rules_json,
        "recursive": settings.recursive,
        "include_dirs": settings.include_dirs,
        "sort_key": settings.sort_key,
        "geometry": settings.geometry,
    }


def settings_from_mapping(
    raw: Mapping[str, Any],
    base: RenamerSettings | None = None,
) -> RenamerSettings:
    """从字典解析设置；缺失 / 类型不符的字段保留默认值或 ``base``。"""
    settings = replace(base) if base is not None else RenamerSettings()
    targets = raw.get("targets")
    if isinstance(targets, list):
        settings.targets = [item for item in targets if isinstance(item, str)]
    rules_json = raw.get("rules_json")
    if isinstance(rules_json, str):
        try:
            rules_from_text(rules_json)
        except RuleError:
            settings.rules_json = "[]"
        else:
            settings.rules_json = rules_json
    for name in ("recursive", "include_dirs"):
        value = raw.get(name)
        if isinstance(value, bool):
            setattr(settings, name, value)
    sort_key = raw.get("sort_key")
    if isinstance(sort_key, str) and sort_key in SORT_KEYS:
        settings.sort_key = sort_key
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> RenamerSettings:
    """读取设置；文件缺失、损坏或顶层非对象时返回默认设置。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return RenamerSettings()
    return settings_from_mapping(raw)


def save_settings(settings: RenamerSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界 / 异常时回落到默认位置并按屏幕夹紧。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class RenamerWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 批量重命名主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._selected = [Path(item) for item in self._settings.targets]
        try:
            self._rules = list(rules_from_text(self._settings.rules_json))
        except RuleError:
            self._rules = []
        self._preview: RenamePreview | None = None
        self._last_report: RenameReport | None = None
        self._row_targets: list[int] = []

        self.root, self._theme = _guiwindow.create_root(
            title=f"renamer - 批量重命名 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._recursive_var = tk.BooleanVar(value=self._settings.recursive)
        self._dirs_var = tk.BooleanVar(value=self._settings.include_dirs)
        self._sort_var = tk.StringVar(value=SORT_TEXT.get(self._settings.sort_key, SORT_TEXT[SORT_NAME]))
        self._status_var = tk.StringVar(value="就绪")
        self._show_old_var = tk.BooleanVar(value=True)
        self._init_rule_vars()

        self._build_widgets()
        self._selected = self._expand_paths(self._selected)
        self._refresh_preview()
        self._refresh_rule_tree()
        _guitheme.install_theme_switcher(self.root)
        _guiwindow.build_menu(
            self.root,
            on_close=self.close,
            on_about=self._show_about,
            help_items=(
                ("使用帮助", self._show_help),
                ("载入规则…", self._load_rules_file),
                ("保存规则…", self._save_rules_file),
            ),
        )

    # ------------------------------------------------------------------ #
    # 变量与界面
    # ------------------------------------------------------------------ #
    def _init_rule_vars(self) -> None:
        tk = self._tk
        self._kind_var = tk.StringVar(value=KIND_TEXT["replace"])
        self._field_var = tk.StringVar(value=FIELD_TEXT[FIELD_STEM])
        self._pattern_var = tk.StringVar()
        self._replacement_var = tk.StringVar()
        self._ignore_case_var = tk.BooleanVar(value=False)
        self._occurrence_var = tk.StringVar(value="0")
        self._anchor_var = tk.StringVar(value=ANCHOR_TEXT[ANCHOR_START])
        self._anchor_text_var = tk.StringVar()
        self._position_var = tk.StringVar(value="0")
        self._mode_var = tk.StringVar(value=CASE_TEXT["lower"])
        self._text_var = tk.StringVar()
        self._start_var = tk.StringVar(value="1")
        self._step_var = tk.StringVar(value="1")
        self._digits_var = tk.StringVar(value="3")

    def _build_widgets(self) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        gap = space.xs
        tight = space.xxs
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ---- 文件：操作栏 + 名称/新名/状态一张表 + 底部操作栏 ----
        files = ttk.LabelFrame(self.root, text="文件", padding=space.sm)
        files.grid(row=0, column=0, sticky="nsew", padx=space.md, pady=(space.md, tight))
        files.columnconfigure(0, weight=1)
        files.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(files)
        toolbar.grid(row=0, column=0, sticky="ew")
        actions = ttk.Frame(toolbar)
        actions.grid(row=0, column=0, sticky="w")
        for index, (label, command) in enumerate(
            (
                ("添加文件…", self._add_files),
                ("添加目录…", self._add_dir),
                ("移除选中", self._remove_targets),
                ("清空", self._clear_targets),
            )
        ):
            ttk.Button(actions, text=label, command=command).grid(row=0, column=index, padx=(gap if index else 0, 0))

        options = ttk.Frame(toolbar)
        options.grid(row=0, column=1, sticky="e")
        ttk.Checkbutton(options, text="递归子目录", variable=self._recursive_var).grid(row=0, column=0)
        ttk.Checkbutton(options, text="包含目录", variable=self._dirs_var).grid(row=0, column=1, padx=(space.md, 0))
        ttk.Label(options, text="计数器排序").grid(row=0, column=2, padx=(space.md, tight))
        sort_box = ttk.Combobox(
            options,
            textvariable=self._sort_var,
            values=list(SORT_TEXT.values()),
            state="readonly",
            width=10,
        )
        sort_box.grid(row=0, column=3)
        sort_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_preview())

        self._file_tree = ttk.Treeview(files, columns=("old", "new", "status"), show="headings")
        for column, title, width in (
            ("old", "名称", 340),
            ("new", "新名", 460),
            ("status", "状态", 90),
        ):
            self._file_tree.heading(column, text=title)
            self._file_tree.column(column, width=width, anchor="w")
        self._file_tree.grid(row=1, column=0, sticky="nsew", pady=(gap, 0))

        bar = ttk.Frame(files)
        bar.grid(row=2, column=0, sticky="ew", pady=(gap, 0))
        ttk.Button(bar, text="执行改名", command=self._execute).grid(row=0, column=0)
        ttk.Button(bar, text="撤销上次", command=self._undo).grid(row=0, column=1, padx=(gap, 0))
        ttk.Checkbutton(
            bar,
            text="旧名",
            variable=self._show_old_var,
            command=self._toggle_old_column,
        ).grid(row=0, column=2, padx=(gap, 0))
        bar.columnconfigure(3, weight=1)
        ttk.Label(bar, textvariable=self._status_var).grid(row=0, column=3, sticky="e")

        # ---- 规则：参数一行、长文本两行、锚点/大小写一行、按钮与列表 ----
        rules = ttk.LabelFrame(self.root, text="规则（按顺序执行；双击列表可载入表单）", padding=space.sm)
        rules.grid(row=1, column=0, sticky="ew", padx=space.md, pady=(tight, space.md))
        rules.columnconfigure(0, weight=1)

        basics = ttk.Frame(rules)
        basics.grid(row=0, column=0, sticky="ew")
        ttk.Label(basics, text="类型").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            basics,
            textvariable=self._kind_var,
            values=list(KIND_TEXT.values()),
            state="readonly",
            width=9,
        ).grid(row=0, column=1, sticky="w", padx=(tight, space.md))
        ttk.Label(basics, text="作用域").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            basics,
            textvariable=self._field_var,
            values=list(FIELD_TEXT.values()),
            state="readonly",
            width=9,
        ).grid(row=0, column=3, sticky="w", padx=(tight, space.md))
        ttk.Label(basics, text="第几个匹配").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(basics, textvariable=self._occurrence_var, from_=0, to=99, width=5).grid(
            row=0, column=5, sticky="w", padx=(tight, space.md)
        )
        ttk.Checkbutton(basics, text="忽略大小写", variable=self._ignore_case_var).grid(row=0, column=6, sticky="w")
        ttk.Label(basics, text="起始").grid(row=0, column=7, sticky="w", padx=(space.md, tight))
        ttk.Spinbox(basics, textvariable=self._start_var, from_=-9999, to=9999, width=6).grid(
            row=0, column=8, sticky="w", padx=(0, space.md)
        )
        ttk.Label(basics, text="步长").grid(row=0, column=9, sticky="w", padx=(0, tight))
        ttk.Spinbox(basics, textvariable=self._step_var, from_=-99, to=99, width=5).grid(
            row=0, column=10, sticky="w", padx=(0, space.md)
        )
        ttk.Label(basics, text="位数").grid(row=0, column=11, sticky="w", padx=(0, tight))
        ttk.Spinbox(basics, textvariable=self._digits_var, from_=1, to=32, width=5).grid(row=0, column=12, sticky="w")

        texts = ttk.Frame(rules)
        texts.grid(row=1, column=0, sticky="ew")
        texts.columnconfigure(1, weight=1)
        texts.columnconfigure(3, weight=1)
        ttk.Label(texts, text="正则").grid(row=0, column=0, sticky="w", padx=(0, tight), pady=tight)
        ttk.Entry(texts, textvariable=self._pattern_var).grid(
            row=0, column=1, sticky="ew", padx=(0, space.md), pady=tight
        )
        ttk.Label(texts, text="替换 / 模板").grid(row=0, column=2, sticky="w", padx=(0, tight), pady=tight)
        ttk.Entry(texts, textvariable=self._replacement_var).grid(row=0, column=3, sticky="ew", pady=tight)
        ttk.Label(texts, text="插入文本 / 分隔符").grid(row=1, column=0, sticky="w", padx=(0, tight), pady=tight)
        ttk.Entry(texts, textvariable=self._text_var).grid(row=1, column=1, columnspan=3, sticky="ew", pady=tight)

        placement = ttk.Frame(rules)
        placement.grid(row=2, column=0, sticky="ew")
        ttk.Label(placement, text="锚点").grid(row=0, column=0, sticky="w", padx=(0, tight), pady=tight)
        ttk.Combobox(
            placement,
            textvariable=self._anchor_var,
            values=list(ANCHOR_TEXT.values()),
            state="readonly",
            width=9,
        ).grid(row=0, column=1, sticky="w", padx=(0, space.md), pady=tight)
        ttk.Label(placement, text="锚点文本").grid(row=0, column=2, sticky="w", padx=(0, tight), pady=tight)
        ttk.Entry(placement, textvariable=self._anchor_text_var, width=22).grid(
            row=0, column=3, sticky="w", padx=(0, space.md), pady=tight
        )
        ttk.Label(placement, text="位置").grid(row=0, column=4, sticky="w", padx=(0, tight), pady=tight)
        ttk.Spinbox(placement, textvariable=self._position_var, from_=-9999, to=9999, width=6).grid(
            row=0, column=5, sticky="w", padx=(0, space.md), pady=tight
        )
        ttk.Label(placement, text="大小写").grid(row=0, column=6, sticky="w", padx=(0, tight), pady=tight)
        ttk.Combobox(
            placement,
            textvariable=self._mode_var,
            values=list(CASE_TEXT.values()),
            state="readonly",
            width=9,
        ).grid(row=0, column=7, sticky="w", pady=tight)

        rule_actions = ttk.Frame(rules)
        rule_actions.grid(row=3, column=0, sticky="ew", pady=(gap, 0))
        for index, (label, command) in enumerate(
            (
                ("添加规则", self._add_rule),
                ("更新选中", self._update_rule),
                ("删除", self._remove_rule),
                ("上移", lambda: self._move_rule(-1)),
                ("下移", lambda: self._move_rule(1)),
            )
        ):
            ttk.Button(rule_actions, text=label, command=command).grid(
                row=0, column=index, padx=(gap if index else 0, 0)
            )
        rule_actions.columnconfigure(5, weight=1)
        ttk.Label(
            rule_actions,
            text="reorder 用 {0}/{1}/{name} 引用捕获组；move occurrence=0 表示全部",
            style="Hint.TLabel",
        ).grid(row=0, column=6, sticky="e")

        self._rule_tree = ttk.Treeview(rules, columns=("kind", "field", "params", "detail"), show="headings", height=4)
        for column, title, width in (
            ("kind", "类型", 70),
            ("field", "作用域", 70),
            ("params", "规则参数", 380),
            ("detail", "说明", 420),
        ):
            self._rule_tree.heading(column, text=title)
            self._rule_tree.column(column, width=width, anchor="w")
        self._rule_tree.grid(row=4, column=0, sticky="ew", pady=(gap, 0))
        self._rule_tree.bind("<Double-1>", lambda _event: self._load_selected_rule())

    def _toggle_old_column(self) -> None:
        """按「旧名」勾选状态切换文件表中的名称列（默认显示）。"""
        columns = ("old", "new", "status") if self._show_old_var.get() else ("new", "status")
        self._file_tree.configure(displaycolumns=columns)

    # ------------------------------------------------------------------ #
    # 文件列表与目标
    # ------------------------------------------------------------------ #
    def _append_targets(self, candidates: list[Path]) -> int:
        """把候选路径并入目标列表（按路径去重），返回新增数量。"""
        known = {os.path.normcase(str(path)) for path in self._selected}
        added = 0
        for path in candidates:
            key = os.path.normcase(str(path))
            if key in known:
                continue
            known.add(key)
            self._selected.append(path)
            added += 1
        return added

    def _expand_paths(self, paths: list[Path]) -> list[Path]:
        """按当前递归 / 包含目录选项把目录展开为文件，文件原样保留。"""
        expanded: list[Path] = []
        for path in paths:
            if not path.is_dir():
                expanded.append(path)
                continue
            try:
                targets = collect_targets(
                    [path],
                    recursive=bool(self._recursive_var.get()),
                    include_dirs=bool(self._dirs_var.get()),
                )
            except OSError:
                expanded.append(path)
                continue
            expanded.extend(target.path for target in targets)
        return list(unique_paths(expanded))

    def _add_files(self) -> None:
        selected = self._filedialog.askopenfilenames(parent=self.root, title="选择待重命名文件")
        if not selected:
            return
        self._append_targets([Path(item) for item in selected])
        self._refresh_preview()

    def _add_dir(self) -> None:
        selected = self._filedialog.askdirectory(parent=self.root, title="选择目录（按当前递归 / 包含目录选项展开）")
        if not selected:
            return
        added = self._append_targets(self._expand_paths([Path(selected)]))
        self._refresh_preview()
        if not added:
            self._status_var.set("目录中没有可添加的文件；可勾选「递归子目录」或「包含目录」后重新添加")

    def _remove_targets(self) -> None:
        rows = self._file_tree.selection()
        indexes = sorted({self._row_targets[int(iid)] for iid in rows}, reverse=True)
        for index in indexes:
            if 0 <= index < len(self._selected):
                del self._selected[index]
        self._refresh_preview()

    def _clear_targets(self) -> None:
        self._selected.clear()
        self._refresh_preview()

    # ------------------------------------------------------------------ #
    # 规则表
    # ------------------------------------------------------------------ #
    def _refresh_rule_tree(self) -> None:
        self._rule_tree.delete(*self._rule_tree.get_children())
        for rule in self._rules:
            self._rule_tree.insert("", "end", values=rule_to_row(rule))

    def _rule_from_form(self) -> RenameRule:
        """从表单构造规则；数字字段非法时抛 ``RuleError``。"""
        try:
            occurrence = int(self._occurrence_var.get())
            position = int(self._position_var.get())
            start = int(self._start_var.get())
            step = int(self._step_var.get())
            digits = int(self._digits_var.get())
        except ValueError as exc:
            raise RuleError(f"数字字段不合法：{exc}") from exc
        return RenameRule(
            kind=_label_value(self._kind_var.get(), KIND_TEXT, "replace"),
            pattern=self._pattern_var.get(),
            replacement=self._replacement_var.get(),
            field=_label_value(self._field_var.get(), FIELD_TEXT, FIELD_STEM),
            ignore_case=bool(self._ignore_case_var.get()),
            occurrence=occurrence,
            anchor=_label_value(self._anchor_var.get(), ANCHOR_TEXT, ANCHOR_START),
            anchor_text=self._anchor_text_var.get(),
            position=position,
            mode=_label_value(self._mode_var.get(), CASE_TEXT, "lower"),
            text=self._text_var.get(),
            start=start,
            step=step,
            digits=digits,
        )

    def _add_rule(self) -> None:
        try:
            rule = self._rule_from_form()
            validate_rules([rule])
        except RuleError as exc:
            self._messagebox.showerror("规则错误", str(exc), parent=self.root)
            return
        self._rules.append(rule)
        self._refresh_rule_tree()
        self._refresh_preview()

    def _update_rule(self) -> None:
        selection = self._rule_tree.selection()
        if not selection:
            return
        index = self._rule_tree.index(selection[0])
        try:
            rule = self._rule_from_form()
            validate_rules([rule])
        except RuleError as exc:
            self._messagebox.showerror("规则错误", str(exc), parent=self.root)
            return
        self._rules[index] = rule
        self._refresh_rule_tree()
        self._refresh_preview()

    def _remove_rule(self) -> None:
        indexes = sorted((self._rule_tree.index(iid) for iid in self._rule_tree.selection()), reverse=True)
        for index in indexes:
            if 0 <= index < len(self._rules):
                del self._rules[index]
        self._refresh_rule_tree()
        self._refresh_preview()

    def _move_rule(self, offset: int) -> None:
        selection = self._rule_tree.selection()
        if not selection:
            return
        index = self._rule_tree.index(selection[0])
        target = index + offset
        if not 0 <= target < len(self._rules):
            return
        self._rules[index], self._rules[target] = self._rules[target], self._rules[index]
        self._refresh_rule_tree()
        self._refresh_preview()
        children = self._rule_tree.get_children()
        self._rule_tree.selection_set(children[target])

    def _load_selected_rule(self) -> None:
        selection = self._rule_tree.selection()
        if not selection:
            return
        rule = self._rules[self._rule_tree.index(selection[0])]
        self._kind_var.set(KIND_TEXT.get(rule.kind, rule.kind))
        self._field_var.set(FIELD_TEXT.get(rule.field, rule.field))
        self._pattern_var.set(rule.pattern)
        self._replacement_var.set(rule.replacement)
        self._ignore_case_var.set(rule.ignore_case)
        self._occurrence_var.set(str(rule.occurrence))
        self._anchor_var.set(ANCHOR_TEXT.get(rule.anchor, rule.anchor))
        self._anchor_text_var.set(rule.anchor_text)
        self._position_var.set(str(rule.position))
        self._mode_var.set(CASE_TEXT.get(rule.mode, rule.mode))
        self._text_var.set(rule.text)
        self._start_var.set(str(rule.start))
        self._step_var.set(str(rule.step))
        self._digits_var.set(str(rule.digits))

    def _rules_file_action(self, save: bool) -> None:
        if save:
            selected = self._filedialog.asksaveasfilename(
                parent=self.root,
                title="保存规则",
                defaultextension=".json",
                initialfile="rename-rules.json",
                filetypes=(("JSON 规则", "*.json"), ("所有文件", "*.*")),
            )
            if selected:
                try:
                    Path(selected).write_text(rules_to_text(self._rules), encoding="utf-8")
                except OSError as exc:
                    self._messagebox.showerror("保存失败", str(exc), parent=self.root)
                    return
                self._status_var.set(f"规则已保存：{selected}")
            return
        selected = self._filedialog.askopenfilename(
            parent=self.root,
            title="载入规则",
            filetypes=(("JSON 规则", "*.json"), ("所有文件", "*.*")),
        )
        if not selected:
            return
        try:
            self._rules = list(rules_from_text(Path(selected).read_text(encoding="utf-8")))
        except (OSError, RuleError) as exc:
            self._messagebox.showerror("载入失败", str(exc), parent=self.root)
            return
        self._refresh_rule_tree()
        self._refresh_preview()

    def _load_rules_file(self) -> None:
        self._rules_file_action(save=False)

    def _save_rules_file(self) -> None:
        self._rules_file_action(save=True)

    # ------------------------------------------------------------------ #
    # 帮助与示例
    # ------------------------------------------------------------------ #
    def _show_help(self) -> None:
        """打开非模态帮助窗口：左侧典型示例，正文为规则说明。"""
        tk = self._tk
        ttk = self._ttk
        space = self._theme.spacing
        window = tk.Toplevel(self.root)
        window.title("renamer 使用帮助")
        window.transient(self.root)
        window.geometry("880x600")
        window.minsize(700, 460)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)

        ttk.Label(
            window,
            text="典型规则示例（双击或点「载入选中示例」写入规则表）",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=space.md, pady=(space.md, space.xxs))
        example_tree = ttk.Treeview(window, columns=("name", "summary"), show="headings", height=4)
        example_tree.heading("name", text="示例")
        example_tree.heading("summary", text="说明")
        example_tree.column("name", width=220, anchor="w")
        example_tree.column("summary", width=620, anchor="w")
        for example in EXAMPLES:
            example_tree.insert("", "end", values=(example.name, example.summary))
        example_tree.grid(row=1, column=0, sticky="ew", padx=space.md)

        help_frame = ttk.Frame(window)
        help_frame.grid(row=2, column=0, sticky="nsew", padx=space.md, pady=space.sm)
        help_frame.columnconfigure(0, weight=1)
        help_frame.rowconfigure(0, weight=1)
        text = _guiwindow.create_log(help_frame, height=14, grid=True)
        text.configure(state="normal")
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")

        def load_selected() -> None:
            selection = example_tree.selection()
            if not selection:
                return
            self._apply_example(EXAMPLES[example_tree.index(selection[0])])

        bar = ttk.Frame(window)
        bar.grid(row=3, column=0, sticky="ew", padx=space.md, pady=(0, space.md))
        ttk.Button(bar, text="载入选中示例", command=load_selected).grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="关闭", command=window.destroy).grid(row=0, column=1, sticky="w", padx=(space.sm, 0))
        bar.columnconfigure(2, weight=1)
        example_tree.bind("<Double-1>", lambda _event: load_selected())

    def _apply_example(self, example: Example) -> bool:
        """把示例规则写入规则表；已有规则时先确认覆盖。"""
        if self._rules and not self._messagebox.askyesno(
            "载入示例",
            f"用示例「{example.name}」替换当前 {len(self._rules)} 条规则？",
            parent=self.root,
        ):
            return False
        self._rules = list(example.rules)
        self._refresh_rule_tree()
        self._refresh_preview()
        return True

    # ------------------------------------------------------------------ #
    # 预览与执行
    # ------------------------------------------------------------------ #
    def _sort_key(self) -> str:
        return _label_value(self._sort_var.get(), SORT_TEXT, SORT_NAME)

    def _refresh_file_tree(self) -> None:
        """无规则时只显示名称，提示规则生效后会自动预览。"""
        self._file_tree.delete(*self._file_tree.get_children())
        self._row_targets = []
        for index, path in enumerate(self._selected):
            self._file_tree.insert("", "end", iid=str(index), values=(path.name, "", ""))
            self._row_targets.append(index)
        if not self._selected:
            self._status_var.set("请先添加文件或目录")
        else:
            self._status_var.set(f"共 {len(self._selected)} 项，添加规则后自动预览")

    def _refresh_preview(self) -> None:
        if not self._selected or not self._rules:
            self._preview = None
            self._refresh_file_tree()
            return
        preview = build_preview(
            tuple(make_target(path) for path in self._selected),
            self._rules,
            self._sort_key(),
        )
        self._preview = preview
        if preview.error:
            self._refresh_file_tree()
            self._status_var.set(f"规则错误：{preview.error}")
            return
        self._file_tree.delete(*self._file_tree.get_children())
        self._row_targets = []
        index_by_path = {os.path.normcase(str(path)): index for index, path in enumerate(self._selected)}
        for position, item in enumerate(preview.items):
            self._file_tree.insert(
                "",
                "end",
                iid=str(position),
                values=(item.old_name, item.new_name, STATUS_TEXT.get(item.status, item.status)),
            )
            self._row_targets.append(index_by_path[os.path.normcase(str(item.source))])
        self._status_var.set(format_preview_status(preview))

    def _execute(self) -> None:
        self._refresh_preview()
        if self._preview is None:
            return
        if self._preview.error:
            self._messagebox.showerror("规则错误", self._preview.error, parent=self.root)
            return
        plan = preview_to_plan(self._preview)
        if not plan.changes:
            self._messagebox.showinfo("没有可执行项", "当前没有状态为「可执行」的改名。", parent=self.root)
            return
        if not self._messagebox.askyesno("确认改名", f"将改名 {len(plan.changes)} 项，继续？", parent=self.root):
            return
        report = execute_plan(plan)
        self._last_report = report
        renamed = dict(report.completed)
        self._selected = [renamed.get(path, path) for path in self._selected]
        self._messagebox.showinfo(
            "改名结果",
            f"成功 {len(report.completed)} 项，失败 {len(report.errors)} 项。",
            parent=self.root,
        )
        self._refresh_preview()

    def _undo(self) -> None:
        if self._last_report is None or not self._last_report.completed:
            self._messagebox.showinfo("无法撤销", "没有本次运行可撤销的改名。", parent=self.root)
            return
        restored = {target: source for source, target in self._last_report.completed}
        report = undo_report(self._last_report)
        self._last_report = None
        self._selected = [restored.get(path, path) for path in self._selected]
        self._messagebox.showinfo(
            "撤销结果",
            f"已恢复 {len(report.completed)} 项，失败 {len(report.errors)} 项。",
            parent=self.root,
        )
        self._refresh_preview()

    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"renamer v{__version__}\n批量重命名：规则流水线 + 正则重排 / 搬移\n"
            "规则可导出为 JSON 复用；执行采用两阶段改名并支持本次撤销。",
            parent=self.root,
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置与窗口位置后退出。"""
        settings = RenamerSettings(
            targets=[str(path) for path in self._selected],
            rules_json=rules_to_text(self._rules),
            recursive=bool(self._recursive_var.get()),
            include_dirs=bool(self._dirs_var.get()),
            sort_key=self._sort_key(),
            geometry=self.root.winfo_geometry(),
        )
        save_settings(settings, self._settings_path)
        self.root.destroy()


def _label_value(label: str, labels: Mapping[str, str], default: str) -> str:
    """把界面标签（或值本身）转回枚举值；无法识别时返回 ``default``。"""
    for value, text in labels.items():
        if label in (text, value):
            return value
    return default


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    _guiwindow.run_window(lambda: RenamerWindow(settings_path), "批量重命名")


@fcmd.tool("renamerw", help="打开批量重命名窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开批量重命名窗口。"""
    run_gui()


@fcmd.main("renamerw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
