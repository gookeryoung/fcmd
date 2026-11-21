"""filescanw 的 Qt 主窗口与入口。

三个分页与迁移前的窗口等价：**扫描**（目录、选项、后缀白名单、配置导入导出、缓存）、
**结果**（按文件聚合命中、规则 / 路径筛选、打码导出）、**规则**（规则表、表达式
校验与规则集导入导出）。扫描在 ``QThreadPool`` 中执行，进度经信号回投主线程，
取消走 :class:`CancelToken`，关窗时置位取消并等待线程池收尾。

扫描参数、规则集、设置与报告格式全部复用 :mod:`fcmd.apps.filescan` 的纯逻辑；
本模块只做控件编排与信号连接。窗口设置继续写
``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``，与旧设置文件同名字段互读。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.filescan import filescanw as fw
from fcmd.apps.filescan._cache import ScanCache, cache_fingerprint
from fcmd.apps.filescan._config import (
    ScanConfig,
    format_timestamp,
    load_config,
)
from fcmd.apps.filescan._config import (
    save_config as save_config_file,
)
from fcmd.apps.filescan._engine import (
    DEFAULT_SCAN_EXTENSIONS,
    CancelToken,
    ScanOptions,
    ScanResult,
    format_size,
    normalize_exts,
    parse_ext_text,
    scan_paths,
)
from fcmd.apps.filescan._report import filter_result, render_context, write_report
from fcmd.apps.filescan._rules import (
    MATCH_KEYWORD,
    MATCH_REGEX,
    Matcher,
    Rule,
    RuleSet,
    builtin_ruleset,
    default_expression,
    load_ruleset,
    resolve_expression,
    save_ruleset,
)
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import (
    Card,
    PageScroll,
    button,
    confirm,
    hint_label,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["FilescanQtWindow", "run_qt"]

DEFAULT_SIZE = (1120, 760)

_EXT_HINT = "多个后缀用英文逗号【,】分隔；支持 .log / log / *.log / *"


class _ScanProgress(QObject):  # pragma: no cover - Qt 信号桥
    """扫描线程 → 主线程的进度信号（跨线程自动排队）。"""

    progress = Signal(int, int, str)


def _scan_worker(  # noqa: PLR0913 - 扫描参数成组传入，与 scan_paths 对齐
    paths: list[Path],
    ruleset: RuleSet,
    options: ScanOptions,
    token: CancelToken,
    bridge: _ScanProgress,
    *,
    use_cache: bool,
    mask: bool,
) -> tuple[ScanResult, str]:
    """在线程池里执行扫描；返回 ``(结果, 缓存降级说明)``。"""
    store = ScanCache() if use_cache else None
    notes: list[str] = []
    if store is not None and store.error:
        notes.append(store.error)
    try:
        key = cache_fingerprint(ruleset, options, mask=mask) if store is not None else ""
        result = scan_paths(
            paths,
            ruleset,
            options,
            token=token,
            progress=lambda done, total, path: bridge.progress.emit(done, total, str(path)),
            cache=store,
            cache_key=key,
        )
        if store is not None and store.error and store.error not in notes:
            notes.append(store.error)
        return result, "；".join(notes)
    finally:
        if store is not None:
            store.close()


class _WhitelistDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑白名单类型：类型名 + 后缀串，提交时在窗口内联校验。"""

    def __init__(
        self,
        parent: QWidget,
        *,
        label: str = "",
        exts: tuple[str, ...] = (),
        taken: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑白名单类型" if label else "新增白名单类型")
        self.setMinimumWidth(680)
        self._taken = set(taken)
        self._original = label
        self._result: tuple[str, tuple[str, ...]] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("类型"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self._label = QLineEdit(label, self)
        grid.addWidget(self._label, 0, 1)
        grid.addWidget(QLabel("后缀"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self._exts = QLineEdit(",".join(exts), self)
        self._exts.setPlaceholderText(_EXT_HINT)
        grid.addWidget(self._exts, 1, 1)
        self._hint = hint_label(_EXT_HINT)
        grid.addWidget(self._hint, 2, 0, 1, 2)
        outer.addLayout(grid)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("确定", kind="primary")
        confirm.clicked.connect(self._commit)
        actions.addWidget(confirm)
        outer.addLayout(actions)
        self._label.selectAll()
        self._label.setFocus()

    def _fail(self, message: str) -> None:
        self._hint.setText(message)

    def _commit(self) -> None:
        label = self._label.text().strip()
        if not label:
            self._fail("请填写类型名称")
            return
        if label != self._original and label in self._taken:
            self._fail(f"类型「{label}」已存在")
            return
        valid, invalid = fw.parse_and_validate_ext_line(self._exts.text())
        if invalid:
            self._fail(f"无效条目：{'、'.join(invalid)}")
            return
        if not valid:
            self._fail("请至少填写一个有效后缀")
            return
        self._result = (label, valid)
        self.accept()

    def value(self) -> tuple[str, tuple[str, ...]] | None:
        """提交结果；取消时为 ``None``。"""
        return self._result


class _RuleDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑单条规则；校验（id 唯一、匹配器合法）由窗口侧统一执行。"""

    def __init__(self, parent: QWidget, rule: Rule | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑规则" if rule is not None else "新增规则")
        self.setMinimumWidth(640)
        self._result: Rule | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._id = QLineEdit(rule.id if rule else "", self)
        self._name = QLineEdit(rule.name if rule else "", self)
        self._group = QLineEdit(rule.group if rule else "自定义", self)
        self._text = QLineEdit(rule.matcher.text if rule else "", self)
        self._pattern = QLineEdit(rule.matcher.pattern if rule else "", self)
        for row, (label, widget) in enumerate(
            (
                ("规则 id", self._id),
                ("名称", self._name),
                ("分组", self._group),
                ("关键词", self._text),
                ("正则", self._pattern),
            )
        ):
            grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(widget, row, 1)
        grid.addWidget(QLabel("类型"), 5, 0, Qt.AlignmentFlag.AlignRight)
        self._kind = QComboBox(self)
        self._kind.addItems([MATCH_KEYWORD, MATCH_REGEX])
        self._kind.setCurrentText(rule.matcher.kind if rule else MATCH_KEYWORD)
        grid.addWidget(self._kind, 5, 1)
        outer.addLayout(grid)

        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        flags.setSpacing(12)
        self._case = QCheckBox("区分大小写", self)
        self._case.setChecked(rule.matcher.is_case_sensitive() if rule else False)
        self._whole = QCheckBox("整词匹配（关键词）", self)
        self._whole.setChecked(rule.matcher.whole_word if rule else False)
        self._enabled = QCheckBox("启用", self)
        self._enabled.setChecked(rule.enabled if rule else True)
        for widget in (self._case, self._whole, self._enabled):
            flags.addWidget(widget)
        flags.addStretch(1)
        outer.addLayout(flags)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("保存", kind="primary")
        confirm.clicked.connect(self._commit)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    def _commit(self) -> None:
        kind = self._kind.currentText()
        matcher = Matcher(
            kind=kind,
            text=self._text.text().strip() if kind == MATCH_KEYWORD else "",
            pattern=self._pattern.text().strip() if kind == MATCH_REGEX else "",
            case_sensitive=self._case.isChecked(),
            whole_word=self._whole.isChecked(),
        )
        self._result = Rule(
            id=self._id.text().strip(),
            name=self._name.text().strip(),
            matcher=matcher,
            group=self._group.text().strip() or "自定义",
            enabled=self._enabled.isChecked(),
        )
        self.accept()

    def value(self) -> Rule | None:
        """提交的规则对象；取消时为 ``None``。"""
        return self._result


class FilescanQtWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """扫描 / 结果 / 规则三页的敏感内容扫描窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self.settings = fw.load_settings(settings_path or fw.config_path())
        self.ruleset: RuleSet = self.settings.ruleset
        self.result: ScanResult | None = None
        self._token: CancelToken | None = None
        self._bridge: _ScanProgress | None = None
        self._worker_running = False
        self._paths: list[str] = list(self.settings.paths)
        self._whitelist_notice = ""
        super().__init__(
            title="敏感内容扫描 - filescan",
            config_name=fw.SETTINGS_NAME,
            default_size=DEFAULT_SIZE,
            min_size=(fw.MIN_WIDTH, fw.MIN_HEIGHT),
            on_close=self._on_close,
            persist=self._persist_settings,
        )
        self._build_widgets()
        self._refresh_dirs()
        self._load_whitelist()
        self._refresh_rules()
        if self.settings.filter_rule:
            index = self._filter_rule.findText(self.settings.filter_rule)
            if index >= 0:
                self._filter_rule.setCurrentIndex(index)
        self.set_status(self._whitelist_notice or self._summary_text())

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        self.tabs = QTabWidget(self)
        self.tabs.setMinimumHeight(520)
        self.tabs.addTab(self._build_scan_page(), "扫描")
        self.tabs.addTab(self._build_result_page(), "结果")
        self.tabs.addTab(self._build_rules_page(), "规则")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.add(self.tabs, 1)

    def _build_scan_page(self) -> QWidget:
        page = PageScroll(margins=(0, 0, 4, 8))
        body = page.body

        dirs_card = Card("扫描目录")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._dirs_list = QListWidget(dirs_card)
        self._dirs_list.setMinimumHeight(96)
        self._dirs_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        dirs_card.body.addWidget(self._dirs_list)
        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setVerticalSpacing(4)
        for row_index, (text, handler) in enumerate(
            (
                ("添加目录…", self._add_directory),
                ("移除选中", self._remove_directory),
                ("清空", self._clear_directories),
            )
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            buttons.addWidget(widget, row_index, 0)
        row.addLayout(buttons)
        dirs_card.body.addLayout(row)
        body.addWidget(dirs_card)

        options = Card("扫描选项")
        checks = QHBoxLayout()
        checks.setContentsMargins(0, 0, 0, 0)
        checks.setSpacing(16)
        self._recursive = QCheckBox("递归子目录", options)
        self._recursive.setChecked(self.settings.recursive)
        self._follow = QCheckBox("跟随符号链接", options)
        self._follow.setChecked(self.settings.follow_symlinks)
        self._skip_binary = QCheckBox("跳过二进制", options)
        self._skip_binary.setChecked(self.settings.skip_binary)
        self._scan_documents = QCheckBox("解析文档", options)
        self._scan_documents.setChecked(self.settings.scan_documents)
        for widget in (self._recursive, self._follow, self._skip_binary, self._scan_documents):
            checks.addWidget(widget)
        checks.addStretch(1)
        options.body.addLayout(checks)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self._max_depth = self._spin(options, 0, 64, self.settings.max_depth)
        self._min_size = self._spin(options, 0, 2_000_000_000, self.settings.min_size)
        self._max_size = self._spin(options, 0, 2_000_000_000, self.settings.max_size)
        self._max_file_bytes = self._spin(options, 1, 1_073_741_824, self.settings.max_file_bytes)
        self._workers = self._spin(options, 1, 64, self.settings.workers)
        self._context_chars = self._spin(options, 0, 400, self.settings.context_chars)
        self._include_ext = QLineEdit(self.settings.include_ext, options)
        self._exclude_ext = QLineEdit(self.settings.exclude_ext, options)
        self._force_ext = QLineEdit(self.settings.force_ext, options)
        self._modified_after = QLineEdit(self.settings.modified_after, options)
        self._modified_before = QLineEdit(self.settings.modified_before, options)
        self._modified_after.setPlaceholderText("YYYY-MM-DD HH:MM:SS，可空")
        self._modified_before.setPlaceholderText("YYYY-MM-DD HH:MM:SS，可空")
        rows: tuple[tuple[str, QWidget, str, QWidget], ...] = (
            ("深度(0=不限)", self._max_depth, "后缀包含", self._include_ext),
            ("最小字节", self._min_size, "后缀排除", self._exclude_ext),
            ("最大字节(0=不限)", self._max_size, "强制后缀", self._force_ext),
            ("单文件上限", self._max_file_bytes, "并发数", self._workers),
            ("上下文长度", self._context_chars, "修改时间≥", self._modified_after),
            ("修改时间≤", self._modified_before, "", None),
        )
        for index, (left_label, left_widget, right_label, right_widget) in enumerate(rows):
            form.addWidget(QLabel(left_label), index, 0, Qt.AlignmentFlag.AlignRight)
            form.addWidget(left_widget, index, 1)
            if right_widget is not None:
                form.addWidget(QLabel(right_label), index, 2, Qt.AlignmentFlag.AlignRight)
                form.addWidget(right_widget, index, 3)
        options.body.addLayout(form)
        options.body.addWidget(hint_label("白名单外强制扫描（如 .pdf）"))

        options.body.addWidget(
            hint_label(
                "后缀白名单（每行一个类型；双击行编辑，后缀用英文逗号【,】分隔；"
                "可新增类型，Ctrl / Shift 多选后批量删除）"
            )
        )
        whitelist_row = QHBoxLayout()
        whitelist_row.setContentsMargins(0, 0, 0, 0)
        whitelist_row.setSpacing(6)
        self._whitelist_table = make_table(
            ("类型", "后缀（用 , 分隔）"),
            widths=(140, 560),
            stretch_columns=(1,),
            parent=options,
        )
        self._whitelist_table.setMinimumHeight(150)
        self._whitelist_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._whitelist_table.cellDoubleClicked.connect(lambda _row, _column: self._edit_whitelist_row())
        whitelist_row.addWidget(self._whitelist_table, 1)
        whitelist_buttons = QVBoxLayout()
        whitelist_buttons.setContentsMargins(0, 0, 0, 0)
        whitelist_buttons.setSpacing(4)
        for text, handler in (
            ("新增类型", lambda: self._whitelist_dialog(None)),
            ("编辑选中", self._edit_whitelist_row),
            ("删除选中", self._delete_whitelist_row),
            ("恢复默认", self._reset_whitelist),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            whitelist_buttons.addWidget(widget)
        whitelist_buttons.addStretch(1)
        whitelist_row.addLayout(whitelist_buttons)
        options.body.addLayout(whitelist_row)
        body.addWidget(options)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._start_button = button("开始扫描", kind="primary")
        self._start_button.clicked.connect(self._start_scan)
        actions.addWidget(self._start_button)
        self._cancel_button = button("取消")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel_scan)
        actions.addWidget(self._cancel_button)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        actions.addWidget(self._progress, 1)
        for text, handler in (
            ("导入配置…", self._import_config),
            ("导出配置…", self._export_config),
            ("清理缓存", self._clear_cache),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        self._use_cache = QCheckBox("使用缓存", self)
        self._use_cache.setChecked(self.settings.use_cache)
        actions.addWidget(self._use_cache)
        body.addLayout(actions)
        body.addStretch(1)
        return page

    @staticmethod
    def _spin(parent: QWidget, minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox(parent)
        widget.setRange(minimum, maximum)
        widget.setValue(max(minimum, min(maximum, value)))
        widget.setMaximumWidth(160)
        return widget

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        card = Card("扫描结果")
        self._tree = QTreeWidget(card)
        self._tree.setHeaderLabels(["文件 / 规则", "规则", "行号", "上下文（打码）"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setColumnWidth(0, 420)
        self._tree.setColumnWidth(1, 140)
        self._tree.setColumnWidth(2, 70)
        self._tree.header().setStretchLastSection(True)
        self._tree.setMinimumHeight(240)
        card.body.addWidget(self._tree, 1)
        layout.addWidget(card, 1)

        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(0, 0, 0, 0)
        filter_bar.setSpacing(6)
        filter_bar.addWidget(QLabel("规则筛选"))
        self._filter_rule = QComboBox(self)
        self._filter_rule.addItem("全部")
        filter_bar.addWidget(self._filter_rule)
        filter_bar.addWidget(QLabel("路径包含"))
        self._filter_path = QLineEdit(self.settings.filter_path, self)
        self._filter_path.setClearButtonEnabled(True)
        filter_bar.addWidget(self._filter_path, 1)
        apply_button = button("应用筛选")
        apply_button.clicked.connect(self._apply_filter)
        filter_bar.addWidget(apply_button)
        reset = button("重置")
        reset.clicked.connect(self._reset_filter)
        filter_bar.addWidget(reset)
        self._export_raw = QCheckBox("导出原文", self)
        filter_bar.addWidget(self._export_raw)
        export = button("导出报告…", kind="primary")
        export.clicked.connect(self._export_report)
        filter_bar.addWidget(export)
        layout.addLayout(filter_bar)
        return page

    def _build_rules_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._rules_table = make_table(
            ("启用", "分组", "名称", "匹配器"),
            widths=(60, 110, 170, 560),
            stretch_columns=(3,),
            parent=page,
        )
        self._rules_table.setMinimumHeight(240)
        self._rules_table.cellDoubleClicked.connect(lambda _row, _column: self._edit_rule())
        layout.addWidget(self._rules_table, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        for text, handler in (
            ("新增规则", self._add_rule),
            ("编辑规则", self._edit_rule),
            ("删除规则", self._delete_rule),
            ("启用", lambda: self._set_rules_enabled(True)),
            ("禁用", lambda: self._set_rules_enabled(False)),
            ("载入内置", self._load_builtin),
            ("清空", self._clear_rules),
            ("导入…", self._import_rules),
            ("导出…", self._export_rules),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        expression = Card("组合表达式（and / or / not，括号可嵌套；留空表示 OR 全部启用规则）")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._expression = QLineEdit(expression)
        self._expression.setPlaceholderText("例如：pii and not email")
        row.addWidget(self._expression, 1)
        apply_button = button("应用")
        apply_button.clicked.connect(self._apply_expression)
        row.addWidget(apply_button)
        validate_button = button("校验")
        validate_button.clicked.connect(self._validate_expression)
        row.addWidget(validate_button)
        expression.body.addLayout(row)
        self._rule_hint = hint_label("")
        expression.body.addWidget(self._rule_hint)
        layout.addWidget(expression)
        return page

    # ------------------------------------------------------------------ #
    # 数据刷新
    # ------------------------------------------------------------------ #
    def _refresh_dirs(self) -> None:
        self._dirs_list.clear()
        self._dirs_list.addItems(self._paths)

    def _refresh_rules(self) -> None:
        self._rules_table.setRowCount(0)
        for row, rule in enumerate(self.ruleset.rules):
            self._rules_table.insertRow(row)
            enabled = make_table_item("是" if rule.enabled else "否")
            enabled.setData(Qt.ItemDataRole.UserRole, rule.id)
            self._rules_table.setItem(row, 0, enabled)
            self._rules_table.setItem(row, 1, make_table_item(rule.group))
            self._rules_table.setItem(row, 2, make_table_item(rule.name))
            self._rules_table.setItem(row, 3, make_table_item(rule.matcher.describe()))
        self._expression.setText(self.ruleset.expression)
        self._rule_hint.setText(
            f"{len(self.ruleset.rules)} 条规则，启用 {self.ruleset.enabled_count()}；"
            f"生效表达式：{self.ruleset.expression.strip() or default_expression(self.ruleset) or '（无）'}"
        )
        current = self._filter_rule.currentText()
        self._filter_rule.blockSignals(True)
        self._filter_rule.clear()
        self._filter_rule.addItems(["全部", *[rule.id for rule in self.ruleset.rules]])
        if current:
            index = self._filter_rule.findText(current)
            self._filter_rule.setCurrentIndex(max(index, 0))
        self._filter_rule.blockSignals(False)

    def _summary_text(self) -> str:
        return (
            f"规则 {len(self.ruleset.rules)} 条（启用 {self.ruleset.enabled_count()}）；"
            f"目录 {len(self._paths)} 个；单文件上限 {format_size(self._max_file_bytes.value())}"
        )

    def _sync_ruleset(self, ruleset: RuleSet, *, message: str) -> None:
        self.ruleset = ruleset
        self._refresh_rules()
        self.set_status(message)

    def _selected_rule_id(self) -> str | None:
        row = self._rules_table.currentRow()
        if row < 0:
            return None
        item = self._rules_table.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def _selected_rule_ids(self) -> set[str]:
        ids: set[str] = set()
        for index in self._rules_table.selectionModel().selectedRows():
            item = self._rules_table.item(index.row(), 0)
            if item is not None:
                value = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(value, str):
                    ids.add(value)
        return ids

    # ------------------------------------------------------------------ #
    # 目录
    # ------------------------------------------------------------------ #
    def _add_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择要扫描的目录")
        if not chosen or chosen in self._paths:
            return
        self._paths.append(chosen)
        self._refresh_dirs()
        self.set_status(self._summary_text())

    def _remove_directory(self) -> None:
        rows = sorted((index.row() for index in self._dirs_list.selectedIndexes()), reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self._paths):
                self._paths.pop(row)
        self._refresh_dirs()
        self.set_status(self._summary_text())

    def _clear_directories(self) -> None:
        self._paths.clear()
        self._refresh_dirs()
        self.set_status(self._summary_text())

    # ------------------------------------------------------------------ #
    # 白名单
    # ------------------------------------------------------------------ #
    def _whitelist_rows(self) -> list[tuple[str, tuple[str, ...]]]:
        rows: list[tuple[str, tuple[str, ...]]] = []
        for index in range(self._whitelist_table.rowCount()):
            label_item = self._whitelist_table.item(index, 0)
            ext_item = self._whitelist_table.item(index, 1)
            label = label_item.text() if label_item is not None else ""
            exts, _ = fw.parse_and_validate_ext_line(ext_item.text() if ext_item is not None else "")
            rows.append((label, exts))
        return rows

    def _whitelist_exts(self) -> tuple[str, ...]:
        return normalize_exts([ext for _, exts in self._whitelist_rows() for ext in exts])

    def _refresh_whitelist_table(self, groups: list[tuple[str, tuple[str, ...]]]) -> None:
        rows: list[tuple[str, tuple[str, ...]]] = []
        for label, exts in groups:
            valid, _ = fw.validate_exts(exts)
            if valid:
                rows.append((str(label).strip(), valid))
        if not rows:
            rows = list(fw.group_whitelist(DEFAULT_SCAN_EXTENSIONS))
        self._whitelist_table.setRowCount(len(rows))
        for index, (label, exts) in enumerate(rows):
            self._whitelist_table.setItem(index, 0, make_table_item(label))
            self._whitelist_table.setItem(index, 1, make_table_item(",".join(exts)))

    def _load_whitelist(self) -> None:
        if self.settings.whitelist_groups:
            self._refresh_whitelist_table([(label, tuple(exts)) for label, exts in self.settings.whitelist_groups])
            return
        loaded, invalid = fw.validate_exts(fw.parse_whitelist_text(self.settings.ext_whitelist))
        self._refresh_whitelist_table(list(fw.group_whitelist(loaded)))
        if invalid:
            self._whitelist_notice = f"白名单中 {'、'.join(invalid)} 无效，已忽略"

    def _reset_whitelist(self) -> None:
        self._refresh_whitelist_table(list(fw.group_whitelist(DEFAULT_SCAN_EXTENSIONS)))
        self.set_status(self._summary_text())

    def _delete_whitelist_row(self) -> None:
        rows = self._whitelist_rows()
        selected = {index.row() for index in self._whitelist_table.selectedIndexes()}
        targets = [rows[index] for index in sorted(selected) if 0 <= index < len(rows)]
        if not targets:
            self.set_status("请先在白名单表格中选择要删除的类型（可按住 Ctrl / Shift 多选）")
            return
        if len(rows) - len(targets) < 1:
            self.set_status("白名单至少保留一个类型，请先新增其他类型")
            return
        total_exts = sum(len(exts) for _, exts in targets)
        if len(targets) == 1:
            prompt = f"确认删除类型「{targets[0][0]}」及其 {total_exts} 个后缀？"
        else:
            preview = "、".join(label for label, _ in targets[:5])
            if len(targets) > 5:
                preview += " 等"
            prompt = f"确认删除选中的 {len(targets)} 个类型（共 {total_exts} 个后缀）？\n\n{preview}"
        if not confirm(self, "删除类型", prompt):
            return
        kept = [row for index, row in enumerate(rows) if index not in selected]
        self._refresh_whitelist_table(kept)
        if len(targets) == 1:
            self.set_status(f"已删除类型「{targets[0][0]}」")
        else:
            self.set_status(f"已删除 {len(targets)} 个类型（共 {total_exts} 个后缀）")

    def _edit_whitelist_row(self) -> None:
        row = self._whitelist_table.currentRow()
        if row < 0:
            self.set_status("请先在白名单表格中选择一行")
            return
        self._whitelist_dialog(row)

    def _whitelist_dialog(self, index: int | None) -> None:
        rows = self._whitelist_rows()
        label, exts = rows[index] if index is not None and 0 <= index < len(rows) else ("", ())
        dialog = _WhitelistDialog(
            self,
            label=label,
            exts=exts,
            taken=tuple(row_label for row_label, _ in rows),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        value = dialog.value()
        if value is None:
            return
        new_label, valid = value
        new_rows: list[tuple[str, tuple[str, ...]]] = []
        for position, (row_label, row_exts) in enumerate(rows):
            if position == index:
                new_rows.append((new_label, valid))
                continue
            kept = tuple(ext for ext in row_exts if ext not in valid)
            if kept:
                new_rows.append((row_label, kept))
        if index is None:
            new_rows.append((new_label, valid))
        self._refresh_whitelist_table(new_rows)
        self.set_status(f"已保存类型「{new_label}」：{len(valid)} 个后缀，共 {len(self._whitelist_exts())} 个")

    # ------------------------------------------------------------------ #
    # 规则
    # ------------------------------------------------------------------ #
    def _add_rule(self) -> None:
        self._rule_dialog(None)

    def _edit_rule(self) -> None:
        rule_id = self._selected_rule_id()
        if rule_id is None:
            self.set_status("请先在规则表中选择一条规则")
            return
        rule = self.ruleset.find(rule_id)
        if rule is not None:
            self._rule_dialog(rule)

    def _rule_dialog(self, rule: Rule | None) -> None:
        dialog = _RuleDialog(self, rule)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = dialog.value()
        if candidate is None:
            return
        try:
            candidate.validate()
            duplicated = any(
                item.id == candidate.id and (rule is None or item.id != rule.id) for item in self.ruleset.rules
            )
            if duplicated:
                raise ValueError(f"规则 id 已存在: {candidate.id}")
            rules = tuple(candidate if item.id == candidate.id else item for item in self.ruleset.rules)
            if not any(item.id == candidate.id for item in self.ruleset.rules):
                rules = (*self.ruleset.rules, candidate)
            new_set = replace(self.ruleset, rules=rules)
            resolve_expression(new_set)
        except ValueError as exc:
            QMessageBox.critical(self, "规则无效", str(exc))
            return
        self._sync_ruleset(new_set, message=f"已保存规则 {candidate.id}")

    def _delete_rule(self) -> None:
        rule_id = self._selected_rule_id()
        if rule_id is None:
            self.set_status("请先在规则表中选择一条规则")
            return
        rule = self.ruleset.find(rule_id)
        name = rule.name if rule is not None else rule_id
        if not confirm(self, "删除规则", f"确定删除规则 {rule_id}（{name}）？"):
            return
        try:
            new_set = self.ruleset.remove_rule(rule_id)
        except ValueError as exc:
            QMessageBox.critical(self, "无法删除", str(exc))
            return
        self._sync_ruleset(new_set, message=f"已删除规则 {rule_id}")

    def _set_rules_enabled(self, enabled: bool) -> None:
        selected = self._selected_rule_ids()
        if not selected:
            self.set_status("请先在规则表中选择规则")
            return
        rules = tuple(replace(rule, enabled=enabled) if rule.id in selected else rule for rule in self.ruleset.rules)
        self._sync_ruleset(
            replace(self.ruleset, rules=rules),
            message=f"已{'启用' if enabled else '禁用'} {len(selected)} 条规则",
        )

    def _load_builtin(self) -> None:
        if self.ruleset.rules and not confirm(self, "载入内置规则", "当前规则将被内置规则覆盖，继续？"):
            return
        self._sync_ruleset(builtin_ruleset(), message="已载入内置敏感规则")

    def _clear_rules(self) -> None:
        if not confirm(self, "清空规则", "确定清空全部规则与表达式？"):
            return
        self._sync_ruleset(replace(self.ruleset, rules=(), expression=""), message="规则已清空")

    def _import_rules(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "导入规则集", "", "JSON (*.json);;全部文件 (*.*)")
        if not chosen:
            return
        try:
            imported = load_ruleset(Path(chosen))
        except ValueError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._sync_ruleset(imported, message=f"已导入规则集（{len(imported.rules)} 条）")

    def _export_rules(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "导出规则集",
            "filescan-rules.json",
            "JSON (*.json)",
        )
        if not chosen:
            return
        try:
            save_ruleset(self.ruleset, Path(chosen))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.set_status(f"规则集已导出到 {chosen}")

    def _apply_expression(self) -> None:
        try:
            updated = fw.apply_expression(self.ruleset, self._expression.text())
        except ValueError as exc:
            QMessageBox.critical(self, "表达式无效", str(exc))
            return
        self._sync_ruleset(updated, message="表达式已应用")

    def _validate_expression(self) -> None:
        try:
            expression = self._expression.text().strip()
            resolve_expression(replace(self.ruleset, expression=expression))
        except ValueError as exc:
            QMessageBox.critical(self, "表达式无效", str(exc))
            return
        QMessageBox.information(self, "表达式有效", "表达式解析通过")

    # ------------------------------------------------------------------ #
    # 扫描
    # ------------------------------------------------------------------ #
    def _scan_options(self) -> ScanOptions:
        after = _parse_optional_timestamp(self._modified_after.text())
        before = _parse_optional_timestamp(self._modified_before.text())
        if after is not None and before is not None and after > before:
            raise ValueError("修改时间下界（≥）晚于上界（≤），请调整时间过滤")
        max_size = self._max_size.value()
        return ScanOptions(
            recursive=self._recursive.isChecked(),
            max_depth=max(0, self._max_depth.value()),
            follow_symlinks=self._follow.isChecked(),
            ext_whitelist=self._whitelist_exts() or DEFAULT_SCAN_EXTENSIONS,
            include_exts=parse_ext_text(self._include_ext.text()),
            force_exts=parse_ext_text(self._force_ext.text()),
            exclude_exts=parse_ext_text(self._exclude_ext.text()),
            min_size=max(0, self._min_size.value()),
            max_size=None if max_size <= 0 else max_size,
            modified_after=after,
            modified_before=before,
            skip_binary=self._skip_binary.isChecked(),
            scan_documents=self._scan_documents.isChecked(),
            max_file_bytes=max(1, self._max_file_bytes.value()),
            workers=max(1, self._workers.value()),
            context_chars=max(0, self._context_chars.value()),
        )

    def _start_scan(self) -> None:
        if self._worker_running:
            return
        if not self._paths:
            QMessageBox.warning(self, "缺少目录", "请先添加至少一个扫描目录")
            return
        try:
            resolve_expression(self.ruleset)
        except ValueError as exc:
            QMessageBox.critical(self, "规则表达式无效", str(exc))
            return
        if not self.ruleset.enabled_rules():
            QMessageBox.warning(self, "没有启用规则", "请先启用至少一条规则")
            return
        try:
            options = self._scan_options()
        except ValueError as exc:
            QMessageBox.critical(self, "扫描选项无效", str(exc))
            return
        self.result = None
        self._tree.clear()
        self._token = CancelToken()
        self._bridge = _ScanProgress(self)
        self._bridge.progress.connect(self._on_progress)
        self._worker_running = True
        self._progress.setValue(0)
        self.set_status("正在扫描…")
        self._start_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        submit(
            _scan_worker,
            [Path(item) for item in self._paths],
            self.ruleset,
            options,
            self._token,
            self._bridge,
            use_cache=self._use_cache.isChecked(),
            mask=not self._export_raw.isChecked(),
            on_done=self._scan_done,
            on_error=self._scan_error,
            on_finished=self._scan_finished,
        )

    def _on_progress(self, done: int, total: int, path: str) -> None:
        self._progress.setValue(round(100.0 * done / max(total, 1)))
        self.set_status(f"正在扫描 {done}/{total}：{path}")

    def _scan_done(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        result, cache_note = payload
        if not isinstance(result, ScanResult):
            return
        self.result = result
        self._render_result(result)
        self.tabs.setCurrentIndex(1)
        self._progress.setValue(100)
        message = fw.scan_status_text(result)
        self.set_status(f"缓存降级：{cache_note}｜{message}" if cache_note else message)

    def _scan_error(self, message: str) -> None:
        self.result = None
        self._progress.setValue(0)
        self.set_status(f"扫描失败：{message}")

    def _scan_finished(self) -> None:
        self._worker_running = False
        self._start_button.setEnabled(True)
        self._cancel_button.setEnabled(False)

    def _cancel_scan(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.set_status("正在取消，等待进行中的文件结束…")

    def _render_result(self, result: ScanResult) -> None:
        self._tree.clear()
        for index, hit in enumerate(result.hits):
            parent = QTreeWidgetItem(
                [
                    str(hit.path),
                    "",
                    "",
                    f"{len(hit.matches)} 条 · {hit.encoding} · {format_size(hit.size)}",
                ]
            )
            parent.setToolTip(0, str(hit.path))
            self._tree.addTopLevelItem(parent)
            for match in hit.matches:
                child = QTreeWidgetItem(
                    [
                        match.rule_name,
                        match.rule_name,
                        str(match.location or match.line_no),
                        render_context(match, False),
                    ]
                )
                parent.addChild(child)
            parent.setExpanded(True)
            if index >= 5000:  # 极端结果集下不再逐项展开，避免长时间占用 GUI 线程
                break
        for item in result.skipped:
            entry = QTreeWidgetItem([str(item.path), "", "", f"跳过：{item.reason}"])
            entry.setToolTip(0, str(item.path))
            self._tree.addTopLevelItem(entry)
        for item in result.errors:
            entry = QTreeWidgetItem([str(item.path), "", "", f"错误：{item.message}"])
            entry.setToolTip(0, str(item.path))
            self._tree.addTopLevelItem(entry)

    def _apply_filter(self) -> None:
        if self.result is None:
            self.set_status("还没有扫描结果")
            return
        selected = self._filter_rule.currentText()
        rule_ids = None if selected in ("", "全部") else [selected]
        filtered = filter_result(self.result, rule_ids=rule_ids, path_contains=self._filter_path.text())
        self._render_result(filtered)
        self.set_status(f"筛选后命中 {filtered.stats.hit_files} 个文件 / {filtered.stats.hit_count} 条")

    def _reset_filter(self) -> None:
        self._filter_rule.setCurrentIndex(0)
        self._filter_path.clear()
        if self.result is not None:
            self._render_result(self.result)
            self.set_status(self._summary_text())

    def _export_report(self) -> None:
        if self.result is None:
            QMessageBox.information(self, "没有结果", "请先完成一次扫描")
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "导出扫描报告",
            "filescan-report.csv",
            "CSV (*.csv);;JSON (*.json);;文本 (*.txt)",
        )
        if not chosen:
            return
        suffix = Path(chosen).suffix.lower()
        fmt = {".json": "json", ".txt": "text", ".log": "text"}.get(suffix, "csv")
        include_raw = self._export_raw.isChecked()
        try:
            write_report(self.result, Path(chosen), fmt, include_raw=include_raw)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.set_status(f"报告已导出到 {chosen}（{fmt}，{'含原文' if include_raw else '已打码'}）")

    # ------------------------------------------------------------------ #
    # 配置与缓存
    # ------------------------------------------------------------------ #
    def _clear_cache(self) -> None:
        store = ScanCache()
        if store.error:
            QMessageBox.warning(self, "缓存不可用", store.error)
            store.close()
            return
        removed = store.clear()
        error = store.error
        store.close()
        if error:
            QMessageBox.warning(self, "清理缓存失败", error)
            return
        self.set_status(f"扫描缓存已清理（{removed} 条）")

    def _import_config(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "导入扫描配置", "", "JSON (*.json);;全部文件 (*.*)")
        if not chosen:
            return
        try:
            loaded = load_config(Path(chosen))
        except ValueError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._apply_config(loaded)
        if loaded.warnings:
            QMessageBox.warning(
                self,
                "配置已导入（部分字段回落到默认值）",
                "\n".join(loaded.warnings),
            )
        self.set_status(f"已导入配置：{chosen}（目录 {len(loaded.roots)} 个，规则 {len(self.ruleset.rules)} 条）")

    def _apply_config(self, config: ScanConfig) -> None:
        options = config.options
        self._paths = [str(path) for path in config.roots]
        self._refresh_dirs()
        self._recursive.setChecked(options.recursive)
        self._follow.setChecked(options.follow_symlinks)
        self._skip_binary.setChecked(options.skip_binary)
        self._scan_documents.setChecked(options.scan_documents)
        self._max_depth.setValue(options.max_depth)
        self._min_size.setValue(options.min_size)
        self._max_size.setValue(options.max_size or 0)
        self._max_file_bytes.setValue(options.max_file_bytes)
        self._workers.setValue(options.workers)
        self._context_chars.setValue(options.context_chars)
        self._include_ext.setText(";".join(options.include_exts))
        self._force_ext.setText(";".join(options.force_exts))
        self._exclude_ext.setText(";".join(options.exclude_exts))
        self._modified_after.setText(format_timestamp(options.modified_after) or "")
        self._modified_before.setText(format_timestamp(options.modified_before) or "")
        self._refresh_whitelist_table(list(fw.group_whitelist(options.ext_whitelist)))
        if config.ruleset is not None:
            self.ruleset = config.ruleset
            self._refresh_rules()
        self.settings = replace(
            self.settings,
            max_hits_per_file=options.max_hits_per_file,
            use_processes=options.use_processes,
            encodings=options.encodings,
            report_format=config.format,
        )

    def _export_config(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "导出扫描配置",
            "filescan-config.json",
            "JSON (*.json)",
        )
        if not chosen:
            return
        try:
            options = self._scan_options()
        except ValueError as exc:
            QMessageBox.critical(self, "扫描选项无效", str(exc))
            return
        config = ScanConfig(
            roots=tuple(Path(item) for item in self._paths),
            options=options,
            ruleset=self.ruleset,
            format=self.settings.report_format,
            mask=not self._export_raw.isChecked(),
        )
        try:
            save_config_file(config, Path(chosen))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.set_status(f"扫描配置已导出到 {chosen}（含规则集与扫描选项）")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _current_settings(self) -> fw.FilescanSettings:
        return replace(
            self.settings,
            paths=tuple(self._paths),
            recursive=self._recursive.isChecked(),
            max_depth=max(0, self._max_depth.value()),
            follow_symlinks=self._follow.isChecked(),
            ext_whitelist=";".join(self._whitelist_exts()),
            whitelist_groups=tuple(self._whitelist_rows()),
            include_ext=self._include_ext.text(),
            force_ext=self._force_ext.text(),
            exclude_ext=self._exclude_ext.text(),
            min_size=max(0, self._min_size.value()),
            max_size=max(0, self._max_size.value()),
            modified_after=self._modified_after.text().strip(),
            modified_before=self._modified_before.text().strip(),
            skip_binary=self._skip_binary.isChecked(),
            scan_documents=self._scan_documents.isChecked(),
            max_file_bytes=max(1, self._max_file_bytes.value()),
            workers=max(1, self._workers.value()),
            context_chars=max(0, self._context_chars.value()),
            use_cache=self._use_cache.isChecked(),
            ruleset=self.ruleset,
            filter_rule="" if self._filter_rule.currentText() == "全部" else self._filter_rule.currentText(),
            filter_path=self._filter_path.text(),
        )

    def _persist_settings(self, geometry: str) -> None:
        fw.save_settings(replace(self._current_settings(), geometry=geometry), self._own_settings_path)

    def _on_close(self) -> bool:
        if self._token is not None:
            self._token.cancel()
        if self._worker_running:
            from PySide6.QtCore import QThreadPool

            QThreadPool.globalInstance().waitForDone(3000)
        return True

    # 结果页筛选：进入结果页后由「应用筛选」按钮触发；筛选条件在切页时也自动套用一次
    def _on_tab_changed(self, index: int) -> None:
        if index == 1 and self.result is not None and self._filter_path.text().strip():
            self._apply_filter()


def _parse_optional_timestamp(text: str) -> datetime | None:
    """把时间输入框转成时间戳；空串返回 ``None``，非法文本抛 ``ValueError``。"""
    value = text.strip()
    return fw.parse_timestamp(value) if value else None


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.qt.app import run_window

    run_window(lambda: FilescanQtWindow(settings_path), app_name="敏感内容扫描")
