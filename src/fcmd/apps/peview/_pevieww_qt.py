"""pevieww 的 Qt 主窗口与入口。

结构树、详情、概览与十六进制内容仍由 :mod:`fcmd.apps.peview._view` /
``_report`` 生成，窗口只负责摆放与联动；编辑对话框复用 ``_edit`` 的预演与
写入流程，行为与 Tk 版一致。

布局口径：左侧结构树与右侧页签用 ``QSplitter`` 分隔，分栏比例写入
``ui/pevieww.json`` 并在下次启动恢复；窗口变窄时树保底 240px、页签保底
320px；当前文件路径用 :class:`ElidedLabel` 中间省略并挂 tooltip；十六进制
视图固定等宽字体且不换行，长行靠水平滚动查看。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.peview import _edit, _report, _view
from fcmd.apps.peview._pe import PEImage, load_pe
from fcmd.apps.peview._reader import PEError
from fcmd.apps.peview.pevieww import (
    DEFAULT_GEOMETRY,
    MIN_HEIGHT,
    MIN_WIDTH,
    PeviewSettings,
    __version__,
    add_recent_file,
    load_settings,
    save_settings,
)
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import ElidedLabel, apply_splitter_sizes, button, hint_label, splitter_sizes

__all__ = ["PeviewWindow", "run_qt"]

_PE_FILTER = "PE 文件 (*.exe *.dll *.sys *.ocx *.cpl *.scr *.efi);;所有文件 (*.*)"
_DEFAULT_SPLIT = (320, 820)


def _settings_size() -> tuple[int, int]:
    """从 ``DEFAULT_GEOMETRY`` 解析默认窗口尺寸。"""
    width, rest = DEFAULT_GEOMETRY.split("x", 1)
    height = rest.split("+", 1)[0]
    return int(width), int(height)


def _mono_font() -> QFont:
    """等宽字体（十六进制视图与日志共用系统固定字宽字形）。"""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(max(font.pointSize(), 9))
    return font


def _text_view(parent: QWidget, *, wrap: bool = True, mono: bool = False) -> QPlainTextEdit:
    """只读文本视图；``wrap=False`` 时靠水平滚动条查看长行。"""
    view = QPlainTextEdit(parent)
    view.setReadOnly(True)
    if mono:
        view.setFont(_mono_font())
    view.setLineWrapMode(
        QPlainTextEdit.LineWrapMode.WidgetWidth if wrap else QPlainTextEdit.LineWrapMode.NoWrap,
    )
    return view


class _EditDialog(QDialog):  # pragma: no cover - 需要 Qt 事件循环
    """字段编辑对话框：预演 / 写入，风险与备份说明常驻可见。"""

    def __init__(self, window: PeviewWindow, pe: PEImage, path: Path) -> None:
        super().__init__(window)
        self._window = window
        self._pe = pe
        self._path = path
        self._values: dict[str, QLineEdit] = {}
        self._original = {name: field.value for name, field in pe.header_fields.items()}
        self.setWindowTitle(f"编辑字段 - {path.name}")
        self.setMinimumSize(600, 440)
        self.resize(760, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 10)
        outer.setSpacing(8)
        file_label = ElidedLabel(str(path), parent=self)
        file_label.setObjectName("Mono")
        outer.addWidget(file_label)
        outer.addWidget(
            hint_label(
                "安全边界：写入前自动创建 <文件名>.backup[N] 备份；写入后重新解析并校验结构与未改字段，"
                "校验失败则放弃写入、原文件不变。只允许修改下列白名单字段，结构性字段"
                "（e_lfanew、节地址/大小、数据目录等）一律拒绝。"
            )
        )

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QWidget(scroll)
        form = QFormLayout(inner)
        form.setContentsMargins(0, 0, 6, 0)
        for name, field_value in pe.header_fields.items():
            edit = QLineEdit(f"0x{field_value.value:X}", inner)
            self._values[name] = edit
            form.addRow(f"{field_value.label}（{name}）", edit)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        if pe.signature.present:
            outer.addWidget(hint_label("该文件带 Authenticode 签名：写入任何字段都会使签名失效。"))

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self._confirm = QCheckBox("我已确认风险：写入前自动备份", self)
        buttons.addWidget(self._confirm)
        self._signature = QCheckBox("我已知晓签名将失效", self)
        self._signature.setChecked(not pe.signature.present)
        self._signature.setVisible(pe.signature.present)
        buttons.addWidget(self._signature)
        buttons.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        preview = button("预演")
        preview.clicked.connect(lambda: self._submit(apply=False))
        buttons.addWidget(preview)
        apply_button = button("写入", kind="primary")
        apply_button.clicked.connect(lambda: self._submit(apply=True))
        buttons.addWidget(apply_button)
        outer.addLayout(buttons)

    def _submit(self, *, apply: bool) -> None:
        """解析字段差异：预演只展示，写入前校验确认勾选与签名声明。"""
        try:
            edits: dict[str, int] = {}
            for name, edit in self._values.items():
                parsed = _edit.parse_value(edit.text())
                if parsed != self._original[name]:
                    edits[name] = parsed
        except PEError as exc:
            QMessageBox.critical(self, "字段值无效", str(exc))
            return
        if not edits:
            QMessageBox.information(self, "没有修改", "所有字段都与当前值相同。")
            return
        if apply and not self._confirm.isChecked():
            QMessageBox.warning(self, "需要确认", "请先勾选「我已确认风险：写入前自动备份」。")
            return
        if apply and self._pe.signature.present and not self._signature.isChecked():
            QMessageBox.warning(self, "需要确认", "文件带 Authenticode 签名，请勾选「我已知晓签名将失效」。")
            return
        try:
            result = _edit.apply_edits(
                self._path,
                edits,
                apply=apply,
                allow_signature_break=True if not apply else self._signature.isChecked(),
            )
        except PEError as exc:
            QMessageBox.critical(self, "编辑失败", str(exc))
            return
        lines = [f"{change.name}: 0x{change.old:X} → 0x{change.new:X}" for change in result.changes]
        if apply:
            lines.append(f"备份: {result.backup}")
            lines.append("写入后已重新解析校验通过。")
            QMessageBox.information(self, "写入完成", "\n".join(lines))
            self.accept()
            self._window.reload_current()
        else:
            lines.append("预演完成：未修改文件。")
            QMessageBox.information(self, "预演结果", "\n".join(lines))


class PeviewWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """PE 查看器主窗口。"""

    def __init__(self, file: Path | None = None) -> None:
        self._settings = load_settings()
        self._pe: PEImage | None = None
        self._current_file: Path | None = None
        self._hex_offset = 0
        size = _settings_size()
        super().__init__(
            title=f"PE 查看器 v{__version__}",
            config_name="pevieww",
            default_size=size,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
            status="就绪：打开一个 PE 文件开始",
        )
        self._build()
        self._install_menu_actions()
        self._refresh_recent_menu()

        initial = file or (Path(self._settings.file_path).expanduser() if self._settings.file_path else None)
        if initial is not None and initial.is_file():
            QTimer.singleShot(60, self, lambda: self._load_file(initial))

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """工具栏 + 结构树 / 页签分栏。"""
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)
        open_button = button("打开…", kind="primary")
        open_button.clicked.connect(self._choose_file)
        toolbar_layout.addWidget(open_button)
        reload_button = button("重新加载")
        reload_button.clicked.connect(self._reload)
        toolbar_layout.addWidget(reload_button)
        edit_button = button("编辑字段…")
        edit_button.clicked.connect(self._open_editor)
        toolbar_layout.addWidget(edit_button)
        self._path_label = ElidedLabel("写前自动备份，写后自校验", parent=toolbar)
        toolbar_layout.addWidget(self._path_label, 1)
        self.add(toolbar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._tree = QTreeWidget(self._splitter)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setMinimumWidth(240)
        self._tree.itemSelectionChanged.connect(self._on_select)
        self._splitter.addWidget(self._tree)
        self._splitter.addWidget(self._build_tabs())
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        sizes = list(self._settings.splitter) if self._settings.splitter else list(_DEFAULT_SPLIT)
        apply_splitter_sizes(self._splitter, tuple(sizes))
        self.add(self._splitter, 1)

    def _build_tabs(self) -> QTabWidget:
        """右侧页签：详情、概览、十六进制（带偏移跳转）。"""
        tabs = QTabWidget(self._splitter)
        tabs.setDocumentMode(True)
        tabs.setMinimumWidth(320)
        self._detail_text = _text_view(tabs)
        tabs.addTab(self._detail_text, "详情")
        self._summary_text = _text_view(tabs)
        tabs.addTab(self._summary_text, "概览")

        hex_page = QWidget(tabs)
        hex_layout = QVBoxLayout(hex_page)
        hex_layout.setContentsMargins(0, 0, 0, 0)
        hex_layout.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        bar.addWidget(QLabel("偏移", hex_page))
        self._offset_edit = QLineEdit("0x0", hex_page)
        self._offset_edit.setMaximumWidth(160)
        self._offset_edit.returnPressed.connect(self._jump)
        bar.addWidget(self._offset_edit)
        jump_button = button("跳转")
        jump_button.clicked.connect(self._jump)
        bar.addWidget(jump_button)
        bar.addStretch(1)
        hex_layout.addLayout(bar)
        self._hex_text = _text_view(hex_page, wrap=False, mono=True)
        hex_layout.addWidget(self._hex_text, 1)
        tabs.addTab(hex_page, "十六进制")
        return tabs

    def _install_menu_actions(self) -> None:
        """把打开 / 最近打开 / 重新加载 / 编辑插入骨架「文件」菜单。"""
        file_menu = self._menu("文件")
        quit_action = file_menu.actions()[0] if file_menu.actions() else None
        for text, slot in (
            ("打开…", self._choose_file),
            ("重新加载", self._reload),
            ("编辑字段…", self._open_editor),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if quit_action is not None:
                file_menu.insertAction(quit_action, action)
            else:
                file_menu.addAction(action)
        self._recent_menu = QMenu("最近打开", self)
        if quit_action is not None:
            file_menu.insertMenu(quit_action, self._recent_menu)
            file_menu.insertSeparator(quit_action)
        else:
            file_menu.addMenu(self._recent_menu)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单。"""
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is not None and action.text() == title:
                return menu
        raise LookupError(f"缺少菜单：{title}")

    # ------------------------------------------------------------------ #
    # 加载与展示
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "选择 PE 文件", "", _PE_FILTER)
        if chosen:
            self._load_file(Path(chosen))

    def _reload(self) -> None:
        if self._current_file is not None:
            self._load_file(self._current_file)
        else:
            self._choose_file()

    def reload_current(self) -> None:
        """重新加载当前文件（编辑写入后调用）。"""
        if self._current_file is not None:
            self._load_file(self._current_file)

    def _open_editor(self) -> None:
        if self._pe is None or self._current_file is None:
            QMessageBox.information(self, "尚未打开文件", "请先打开一个 PE 文件再编辑。")
            return
        _EditDialog(self, self._pe, self._current_file).exec()

    def _load_file(self, path: Path) -> None:
        """解析 PE 并刷新树、概览、设置与最近列表。"""
        try:
            pe = load_pe(path)
        except PEError as exc:
            QMessageBox.critical(self, "无法打开 PE 文件", str(exc))
            self.set_status(f"打开失败：{exc}")
            return
        self._pe = pe
        self._current_file = path
        self._settings.file_path = str(path)
        self._settings.recent_files = add_recent_file(self._settings.recent_files, path)
        self._refresh_recent_menu()
        self.setWindowTitle(f"PE 查看器 v{__version__} - {path.name}")
        self._path_label.setText(str(path))
        self._path_label.setToolTip(str(path))
        self._populate_tree()
        self._summary_text.setPlainText(_report.format_summary(pe))
        self.set_status(
            f"{path}  |  {_report.format_size(pe.size)}  |  {len(pe.sections)} 节  |  "
            f"{len(pe.imports)} 个导入库  |  {'有签名' if pe.signature.present else '无签名'}"
        )

    def _populate_tree(self) -> None:
        """按 ``_view.build_tree`` 的父子关系重建结构树。"""
        self._tree.clear()
        if self._pe is None:
            return
        items: dict[str, QTreeWidgetItem] = {}
        for row in _view.build_tree(self._pe):
            item = QTreeWidgetItem([row.text])
            item.setData(0, Qt.ItemDataRole.UserRole, row.node_id)
            if row.parent is None:
                self._tree.addTopLevelItem(item)
            else:
                items[row.parent].addChild(item)
            items[row.node_id] = item
        self._tree.expandToDepth(0)
        first = self._tree.topLevelItem(0)
        if first is not None:
            self._tree.setCurrentItem(first)

    def _on_select(self) -> None:
        """选中节点后刷新详情、偏移与十六进制视图。"""
        if self._pe is None:
            return
        item = self._tree.currentItem()
        if item is None:
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        rows = _view.details_for(self._pe, node_id)
        body = "\n".join(f"{label}: {value}" for label, value in rows) if rows else "(该节点没有详情)"
        self._detail_text.setPlainText(body)
        offset = _view.node_offset(self._pe, node_id)
        if offset is not None:
            self._hex_offset = offset
            self._offset_edit.setText(f"0x{offset:X}")
        self._render_hex()

    def _render_hex(self) -> None:
        if self._pe is None:
            return
        self._hex_text.setPlainText(_view.hex_view(self._pe, self._hex_offset))

    def _jump(self) -> None:
        """按十进制或 ``0x`` 十六进制偏移跳转，越界时提示。"""
        if self._pe is None:
            return
        text = self._offset_edit.text().strip()
        try:
            offset = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError:
            QMessageBox.critical(self, "偏移无效", f"请输入十进制或 0x 开头的十六进制偏移：{text}")
            return
        if offset < 0 or offset >= self._pe.size:
            QMessageBox.critical(
                "偏移超出范围",
                f"偏移 0x{offset:X} 不在 0 - 0x{self._pe.size:X} 之间",
            )
            return
        self._hex_offset = offset
        self._offset_edit.setText(f"0x{offset:X}")
        self._render_hex()

    def _refresh_recent_menu(self) -> None:
        """重建「最近打开」子菜单（空时放一条禁用占位）。"""
        self._recent_menu.clear()
        if not self._settings.recent_files:
            placeholder = self._recent_menu.addAction("（无）")
            placeholder.setEnabled(False)
            return
        for item in self._settings.recent_files:
            path = Path(item)
            action = self._recent_menu.addAction(path.name or item)
            action.triggered.connect(lambda _checked=False, p=path: self._load_file(p))

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回当前文件、最近列表、分栏比例与 geometry。"""
        save_settings(
            PeviewSettings(
                file_path=self._settings.file_path,
                recent_files=self._settings.recent_files,
                geometry=geometry,
                splitter=splitter_sizes(self._splitter),
            )
        )


def run_qt(file: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: PeviewWindow(file), app_name="pevieww")
