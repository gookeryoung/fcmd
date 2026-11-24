"""filescanw 的 Qt 主窗口与入口。

布局与 wslmanw / llmw 对齐：复用共享 :class:`fcmd.gui.window.QtMainWindow` 的左侧
:class:`~fcmd.gui.sidebar.SidebarNav` 导航（折叠、``Ctrl+Tab`` 与窗口几何记忆都由
基座提供），右侧按 **扫描 / 结果 / 规则 / 设置** 四页分页，每页拿满整个内容区，
不再用顶部标签条挤压横向空间。分页按「动作 / 结果 / 规则 / 配置」分层：

- **扫描**（扫描任务列表）：按任务管理目录，每行可开始 / 停止扫描、查看该任务的
  结果；扫描按钮排在行内动作首位并加宽，最近扫描时间紧挨目录显示；当前选项用
  只读标签放在卡片标题行，进设置页修改；
- **结果**（按文件聚合命中、规则 / 路径筛选、打码导出）：表格吃满页面高度，
  标题胶囊标明当前结果所属任务，扫描结束自动切到本页；上下文列按列宽换行完整
  显示，命中片段用主题 warning 底色单独标出；
- **规则**（规则表、表达式校验与规则集导入导出）；
- **设置**（扫描范围表单、后缀白名单、缓存 / 配置导入导出）：三张卡片纵向排布
  并套 :class:`~fcmd.gui.widgets.PageScroll`，窗口压矮时滚动而不是裁掉底部动作行。

每个页面是 :class:`~fcmd.gui.page.AppPage` 子类，状态栏文字按页隔离：扫描进度
写到扫描页，筛选 / 导出结果写到结果页，规则增删改写到规则页，白名单 / 缓存 / 配置
操作写到设置页，切页后各自恢复。
扫描在 ``QThreadPool`` 中执行，一次只跑一个任务，进度经信号回投主线程；行内按钮
开始后切换为「停止扫描」，点击置位 :class:`CancelToken`，关窗时同样置位取消并等待
线程池收尾。扫描结果按任务 id 落盘（``ui/filescanw-results.json``），退出应用后
重开仍能恢复，结果页与任务行都能重新进入查看。

扫描参数、规则集、设置与报告格式全部复用 :mod:`fcmd.apps.filescan` 的纯逻辑；
本模块只做控件编排与信号连接。窗口设置继续写
``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``，与旧设置文件同名字段互读（新增
``tasks`` 记录扫描任务与最近一次扫描摘要、``sidebar_collapsed`` 记录侧边栏
折叠状态）。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex, QPoint, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
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
    FileHit,
    RuleMatch,
    ScanOptions,
    ScanResult,
    format_size,
    normalize_exts,
    parse_ext_text,
    parse_skip_dirs_text,
    scan_paths,
)
from fcmd.apps.filescan._metadata import METADATA_KEYS, METADATA_LABELS
from fcmd.apps.filescan._report import (
    context_segments,
    engine_label,
    filter_result,
    mask_text,
    match_note,
    render_context,
    write_report,
)
from fcmd.apps.filescan._rules import (
    DEFAULT_SEVERITY,
    MATCH_DICTIONARY,
    MATCH_ENTROPY,
    MATCH_FILETYPE,
    MATCH_HASH,
    MATCH_KEYWORD,
    MATCH_METADATA,
    MATCH_REGEX,
    MATCH_SIZE,
    MATCH_TIME,
    SEVERITY_LABELS,
    SEVERITY_LEVELS,
    SIZE_BETWEEN,
    SIZE_LT,
    SIZE_OP_LABELS,
    SIZE_OPS,
    TARGET_ARCHIVE_PATH,
    TARGET_CONTENT,
    TARGET_FILENAME,
    TARGET_PATH,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_MODIFIED,
    TIME_OP_LABELS,
    TIME_OPS,
    TIME_TARGET_LABELS,
    TIME_TARGETS,
    VALIDATOR_LABELS,
    VALIDATORS,
    Expression,
    Matcher,
    Rule,
    RuleSet,
    add_path_allowlist,
    builtin_ruleset,
    clear_rules,
    effective_expression_text,
    load_ruleset,
    referenced_rule_ids,
    resolve_expression,
    save_ruleset,
    severity_label,
    validate_expression,
    validate_ruleset,
)
from fcmd.apps.filescan._scenarios import load_scenario, scenario_templates
from fcmd.gui import style as _qt_theme
from fcmd.gui.icons import icon
from fcmd.gui.page import AppPage, PageSpec
from fcmd.gui.uistate import PathHistory, WindowState
from fcmd.gui.widgets import (
    ButtonStateMixin,
    Card,
    FlowLayout,
    FormGrid,
    ItemRow,
    PageScroll,
    RowDetailMixin,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    hint_label,
    icon_button,
    make_table,
    make_table_item,
)
from fcmd.gui.window import QtMainWindow
from fcmd.gui.workers import submit
from fcmd.infras.download import SpeedMeter

__all__ = ["FilescanQtWindow", "run_qt"]

TITLE = "敏感内容扫描 - filescan"
NAV_TITLE = "敏感扫描"
DEFAULT_SIZE = (1120, 760)

PAGES: tuple[PageSpec, ...] = (
    PageSpec("scan", "扫描", "按任务管理扫描目录，每个任务单独开始 / 停止与查看结果", "scan"),
    PageSpec("results", "结果", "按文件聚合的命中、筛选与打码导出", "search"),
    PageSpec("rules", "规则", "规则表、启停、组合表达式与规则集导入导出", "check-square"),
    PageSpec("settings", "设置", "扫描范围、后缀白名单与缓存 / 配置", "settings"),
)
"""侧边栏页顺序与提示：与 wslmanw / llmw 同一套 ``SidebarNav`` 分页。"""

_EXT_HINT = "多个后缀用英文逗号【,】分隔；支持 .log / log / *.log / *"


class _FilescanPage(AppPage):  # pragma: no cover - Qt 控件
    """filescan 的页面骨架：滚动表单页 / 满高内容页 + 页面级状态栏。

    ``scroll=True`` 时内容放进 :class:`PageScroll`（扫描页表单很高，窗口矮时要能滚动）；
    结果 / 规则页以表格为主体，直接吃满页面高度，不套滚动区（否则表格被压成内容高度）。
    """

    def __init__(
        self,
        state: object,
        key: str,
        *,
        scroll: bool = True,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        spacing: int = 8,
    ) -> None:
        super().__init__(state, spacing=0)
        self.PAGE_KEY = key
        if scroll:
            self.scroll: PageScroll | None = PageScroll(self, margins=margins)
            self.body.addWidget(self.scroll, 1)
            self.content = self.scroll.body
            self.content.setSpacing(spacing)
        else:
            self.scroll = None
            self.body.setContentsMargins(*margins)
            self.body.setSpacing(spacing)
            self.content = self.body


_TASK_STATUS = {
    "ok": ("已扫描", "success"),
    "cancelled": ("已停止", "warning"),
    "failed": ("失败", "danger"),
}
"""任务的最近一次扫描状态 → 胶囊文案 / 语义色。"""


class _ScanTaskRow(ItemRow):  # pragma: no cover - Qt 控件
    """扫描任务行卡：与 wslmanw 实例行同一套 ItemRow 骨架。

    标题是任务名，副标题是目录；目录旁用标签显示扫描时间与命中情况，行尾是最近一次
    扫描状态。行内动作依次是「扫描（加宽主按钮）→ 更多扫描方式（增量扫描 / 全量重扫）
    → 查看结果」；移除统一走卡片右上角，避免在行内误点。展开箭头就地显示完整目录、
    状态、时间与精简结果摘要。
    """

    scanRequested = Signal(str)
    incrementalRequested = Signal(str)
    fullRequested = Signal(str)
    stopRequested = Signal(str)
    resultRequested = Signal(str)

    def __init__(  # noqa: PLR0913 - 行状态由任务 + 全局忙态成组传入
        self,
        task: fw.ScanTask,
        *,
        has_result: bool,
        busy: bool,
        active: bool,
        stopping: bool,
        dense: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            task.display_name,
            task.path or "未指定目录",
            elide=Qt.TextElideMode.ElideMiddle,
            dense=dense,
            parent=parent,
        )
        self.task = task
        # 扫描时间紧挨目录显示，不再占用右侧指标列。
        self.time_tag = StatusTag("", tone="muted", parent=self.header)
        self.add_note_widget(self.time_tag)
        # 命中情况放在折叠面板上：不展开也能一眼看到「命中几个文件 / 几条」。
        self.hit_tag = StatusTag("", tone="muted", parent=self.header)
        self.add_note_widget(self.hit_tag)
        self.status_tag = StatusTag("", tone="muted", parent=self.header)
        self.add_tag(self.status_tag)

        # 扫描是每行最先看到的主动作：带文字并加宽到普通图标按钮的两倍。
        self.scan_button = button("扫描", kind="primary", icon="scan")
        self.scan_button.setMinimumWidth(88)
        self.scan_button.clicked.connect(self._on_scan_clicked)
        self.add_action(self.scan_button)
        # 增量 / 全量收进扫描按钮旁的菜单，避免一行摆三个文字按钮把动作区撑宽。
        self.scan_mode_button = icon_button("", tooltip="更多扫描方式：增量扫描 / 全量重扫")
        self.scan_mode_button.set_icon_name("chevron-down", tone="muted")
        self.scan_mode_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        scan_menu = QMenu(self.scan_mode_button)
        scan_menu.setObjectName("RowActionMenu")
        muted = _qt_theme.active_theme().palette.muted
        incremental = scan_menu.addAction(icon("refresh", size=16, color=muted), "增量扫描")
        incremental.setToolTip("只重扫新增或改动过的文件，其余复用缓存")
        incremental.triggered.connect(lambda: self.incrementalRequested.emit(self.task.id))
        full = scan_menu.addAction(icon("rotate-ccw", size=16, color=muted), "全量重扫")
        full.setToolTip("忽略缓存重新读取所有文件（目录 / 文件大改后校正结果）")
        full.triggered.connect(lambda: self.fullRequested.emit(self.task.id))
        self.scan_mode_button.setMenu(scan_menu)
        self.add_action(self.scan_mode_button)
        self.result_button = button("", kind="ghost", icon="search")
        self.result_button.clicked.connect(lambda: self.resultRequested.emit(self.task.id))
        self.add_action(self.result_button)

        self._active = active
        self._stopping = stopping
        self.update_state(task, has_result=has_result, busy=busy, active=active, stopping=stopping)

    def _on_scan_clicked(self) -> None:
        if self._active and not self._stopping:
            self.stopRequested.emit(self.task.id)
        else:
            self.scanRequested.emit(self.task.id)

    def update_state(
        self,
        task: fw.ScanTask,
        *,
        has_result: bool,
        busy: bool,
        active: bool,
        stopping: bool,
    ) -> None:
        """按任务数据与全局扫描忙态重算文案 / 胶囊 / 按钮可用性。"""
        self.task = task
        self._active = active
        self._stopping = stopping
        self.set_title(task.display_name)
        self.set_subtitle(task.path or "未指定目录")
        self.time_tag.set_status(task.last_scanned_at, "muted")
        self.time_tag.setVisible(bool(task.last_scanned_at))
        self.time_tag.setToolTip(f"最近扫描：{task.last_scanned_at}" if task.last_scanned_at else "")
        hit_text, hit_tone = self._hit_summary(task)
        self.hit_tag.set_status(hit_text, hit_tone)
        self.hit_tag.setVisible(bool(hit_text))
        self.hit_tag.setToolTip(task.last_summary)
        if active and stopping:
            self.status_tag.set_status("停止中…", "warning")
        elif active:
            self.status_tag.set_status("扫描中", "accent")
        else:
            text, tone = _TASK_STATUS.get(task.last_status, ("未扫描", "muted"))
            self.status_tag.set_status(text, tone)

        self.result_button.setEnabled(has_result and not busy)
        self.result_button.setToolTip("查看该任务的扫描结果" if has_result else "还没有该任务的扫描结果，先扫描一次")
        self.scan_mode_button.setEnabled(not busy)
        self.scan_mode_button.setToolTip("已有扫描在进行" if busy else "更多扫描方式：增量扫描 / 全量重扫")
        if active:
            self.scan_button.set_text("停止中" if stopping else "停止")
            self.scan_button.set_kind("danger")
            self.scan_button.set_icon_name("stop", tone="danger" if not stopping else "muted")
            self.scan_button.setToolTip("停止扫描" if not stopping else "正在停止")
            self.scan_button.setEnabled(not stopping)
        else:
            self.scan_button.set_text("扫描")
            self.scan_button.set_kind("primary")
            self.scan_button.set_icon_name("scan", tone="on_accent" if not busy else "muted")
            self.scan_button.setToolTip("已有扫描在进行" if busy else f"扫描 {task.display_name}")
            self.scan_button.setEnabled(not busy)

    @staticmethod
    def _hit_summary(task: fw.ScanTask) -> tuple[str, str]:
        """折叠行上的命中摘要与语义色；没有扫描历史时返回空串。"""
        if not task.last_status:
            return "", "muted"
        if task.last_status == "failed":
            return "扫描失败", "danger"
        if task.last_status == "cancelled":
            return (task.last_summary or "已停止"), "warning"
        if task.last_summary.startswith("无命中"):
            return task.last_summary, "success"
        return task.last_summary, "danger"

    def set_progress(self, done: int, total: int) -> None:
        """扫描中就地更新胶囊（不重建整行）。"""
        percent = max(0, min(100, int(done * 100 / total))) if total > 0 else 0
        self.status_tag.set_status(f"扫描 {percent}%", "accent")


class _ScanTaskAddRow(ButtonStateMixin, QPushButton):  # pragma: no cover - Qt 控件
    """扫描任务列表首项：虚线「添加任务」，始终固定在列表最上方。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("添加任务", parent)
        self.setObjectName("ScanTaskAdd")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(54)
        self.setMaximumHeight(54)
        self.set_icon_name("plus", size=18, tone="muted")


class _ScanTaskEmptyRow(QWidget):  # pragma: no cover - Qt 控件
    """没有任务时的说明卡：在「添加任务」虚线框下方给出下一步操作。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 18)
        layout.setSpacing(4)
        layout.addStretch(1)
        title = QLabel("还没有扫描任务", self)
        title.setObjectName("SectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = hint_label("点上方虚线框选择目录，任务创建后即可单独扫描、查看结果或展开详情。")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        layout.addStretch(1)


class _ScanTaskDetailView(QWidget):  # pragma: no cover - Qt 控件
    """任务行展开后的详情：完整目录 / 状态 / 最近扫描时间 / 结果摘要。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 10)
        layout.setSpacing(6)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}
        for row, label in enumerate(("目录", "状态", "最近扫描", "结果摘要")):
            grid.addWidget(hint_label(f"{label}：", wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
            self.values[label] = value
        layout.addLayout(grid)

    def render(self, task: fw.ScanTask) -> None:
        """按任务数据回填详情；没有历史时给出可读占位。"""
        status, _tone = _TASK_STATUS.get(task.last_status, ("未扫描", "muted"))
        fields = {
            "目录": task.path or "未指定目录",
            "状态": status,
            "最近扫描": task.last_scanned_at or "从未扫描",
            "结果摘要": task.last_summary or "还没有扫描记录",
        }
        for label, widget in self.values.items():
            text = fields.get(label, "—")
            widget.setText(text)
            widget.setToolTip(text)


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
    refresh: bool = False,
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
            refresh=refresh,
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


_KIND_LABELS: dict[str, str] = {
    MATCH_KEYWORD: "关键词",
    MATCH_REGEX: "正则",
    MATCH_TIME: "时间",
    MATCH_SIZE: "文件大小",
    MATCH_FILETYPE: "文件类型",
    MATCH_METADATA: "文档元数据",
    MATCH_DICTIONARY: "自定义词典",
    MATCH_HASH: "文件哈希",
    MATCH_ENTROPY: "高熵串",
}
"""规则类型下拉的中文名称（内部仍用稳定 id）。"""


def _collapse_multiline(text: str) -> str:
    """把多行输入合并为单行：换行只用于排版，行首缩进一并去掉。"""
    return "".join(part.strip() for part in text.splitlines()).strip()


def _list_text(text: str) -> str:
    """把多行列表合并为以分号分隔的单行：这里换行是列表分隔符。"""
    return ";".join(part.strip() for part in text.splitlines() if part.strip())


def _multiline_editor(
    text: str = "",
    placeholder: str = "",
    *,
    min_height: int = 64,
    max_height: int = 96,
) -> QPlainTextEdit:
    """「单行语义 / 多行排版」输入框：按宽度折行，保存时再压成一行或按分隔符解析。

    用于可能很长的配置文字（跳过目录、后缀列表、正则表达式、组合式表达式）：单行
    ``QLineEdit`` 会把内容藏在水平滚动后面，这里固定高度内自动换行。
    """
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    editor.setPlaceholderText(placeholder)
    editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    editor.setMinimumHeight(min_height)
    editor.setMaximumHeight(max_height)
    editor.setTabChangesFocus(True)
    return editor


class _RuleDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑单条规则；校验（id 唯一、匹配器合法）由窗口侧统一执行。"""

    def __init__(self, parent: QWidget, rule: Rule | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑规则" if rule is not None else "新增规则")
        self.setMinimumWidth(660)
        self._result: Rule | None = None

        current_kind = rule.matcher.kind if rule is not None else MATCH_KEYWORD
        # 关键词 / 正则各自记住大小写开关：新增时用类型默认值（关键词不区分、正则区分）。
        self._case_by_kind = {MATCH_KEYWORD: False, MATCH_REGEX: True}
        if rule is not None and current_kind in self._case_by_kind:
            self._case_by_kind[current_kind] = rule.matcher.is_case_sensitive()

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
        self._kind = QComboBox(self)
        for kind, label in _KIND_LABELS.items():
            self._kind.addItem(label, kind)
        self._kind.setCurrentIndex(max(self._kind.findData(current_kind), 0))
        self._text = _multiline_editor(placeholder="关键词原文，如 password")
        self._text.setPlainText(rule.matcher.text if rule else "")
        self._pattern = _multiline_editor(placeholder="正则表达式，如 \\d{11}；可换行，保存时合并为单行")
        self._pattern.setPlainText(rule.matcher.pattern if rule else "")
        self._severity = QComboBox(self)
        self._severity.addItems([SEVERITY_LABELS[level] for level in SEVERITY_LEVELS])
        self._severity.setCurrentIndex(SEVERITY_LEVELS.index(rule.severity if rule else DEFAULT_SEVERITY))
        self._target = QComboBox(self)
        self._target.addItem("文件内容", TARGET_CONTENT)
        self._target.addItem("文件名", TARGET_FILENAME)
        self._target.addItem("完整路径", TARGET_PATH)
        self._target.addItem("归档成员路径", TARGET_ARCHIVE_PATH)
        current_target = (
            rule.matcher.target if rule and rule.matcher.target in (TARGET_CONTENT, TARGET_FILENAME) else TARGET_CONTENT
        )
        self._target.setCurrentIndex(max(self._target.findData(current_target), 0))
        self._time_field = QComboBox(self)
        for target in TIME_TARGETS:
            self._time_field.addItem(TIME_TARGET_LABELS[target], target)
        current_time_field = rule.matcher.target if rule and rule.matcher.target in TIME_TARGETS else TIME_MODIFIED
        self._time_field.setCurrentIndex(max(self._time_field.findData(current_time_field), 0))
        self._time_op = QComboBox(self)
        for op in TIME_OPS:
            self._time_op.addItem(TIME_OP_LABELS[op], op)
        current_op = rule.matcher.compare if rule and rule.matcher.kind == MATCH_TIME else TIME_BEFORE
        self._time_op.setCurrentIndex(max(self._time_op.findData(current_op), 0))
        self._time_value = QLineEdit(rule.matcher.value if rule and rule.matcher.kind == MATCH_TIME else "", self)
        self._time_value.setPlaceholderText("2026-01-01T00:00:00 或 epoch 秒")
        self._time_end = QLineEdit(rule.matcher.value2 if rule and rule.matcher.kind == MATCH_TIME else "", self)
        self._time_end.setPlaceholderText("结束时间（介于时必填）")
        self._size_op = QComboBox(self)
        for op in SIZE_OPS:
            self._size_op.addItem(SIZE_OP_LABELS[op], op)
        current_size_op = rule.matcher.compare if rule and rule.matcher.kind == MATCH_SIZE else SIZE_LT
        self._size_op.setCurrentIndex(max(self._size_op.findData(current_size_op), 0))
        self._size_value = QLineEdit(rule.matcher.value if rule and rule.matcher.kind == MATCH_SIZE else "", self)
        self._size_value.setPlaceholderText("如 10MB / 1048576")
        self._size_end = QLineEdit(rule.matcher.value2 if rule and rule.matcher.kind == MATCH_SIZE else "", self)
        self._size_end.setPlaceholderText("结束大小（介于时必填）")
        self._filetype_value = QLineEdit(
            rule.matcher.value if rule and rule.matcher.kind == MATCH_FILETYPE else "", self
        )
        self._filetype_value.setPlaceholderText(" pdf,zip,exe（逗号分隔）")
        self._metadata_key = QComboBox(self)
        for key in METADATA_KEYS:
            self._metadata_key.addItem(METADATA_LABELS[key], key)
        current_key = rule.matcher.value if rule and rule.matcher.kind == MATCH_METADATA else METADATA_KEYS[0]
        self._metadata_key.setCurrentIndex(max(self._metadata_key.findData(current_key), 0))
        self._metadata_text = QLineEdit(rule.matcher.text if rule and rule.matcher.kind == MATCH_METADATA else "", self)
        self._metadata_text.setPlaceholderText("元数据中包含的文本（忽略大小写）")
        self._extra = _multiline_editor(placeholder="每行一个词条 / SHA-256")
        if rule and rule.matcher.kind in (MATCH_DICTIONARY, MATCH_HASH):
            self._extra.setPlainText(rule.matcher.value)
        self._entropy_value = QLineEdit(
            rule.matcher.value if rule and rule.matcher.kind == MATCH_ENTROPY else "4.5", self
        )
        self._entropy_value.setPlaceholderText("熵阈值 (0, 8]，默认 4.5")
        self._entropy_min = QLineEdit(
            rule.matcher.value2 if rule and rule.matcher.kind == MATCH_ENTROPY else "24", self
        )
        self._entropy_min.setPlaceholderText("最小长度，默认 24")
        self._validator = QComboBox(self)
        for validator in VALIDATORS:
            self._validator.addItem(VALIDATOR_LABELS[validator], validator)
        current_validator = rule.matcher.validator if rule else ""
        self._validator.setCurrentIndex(max(self._validator.findData(current_validator), 0))

        def _label(text: str) -> QLabel:
            label = QLabel(text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return label

        for row, (text, widget) in enumerate(
            (
                ("规则 id", self._id),
                ("名称", self._name),
                ("分组", self._group),
                ("类型", self._kind),
            )
        ):
            grid.addWidget(_label(text), row, 0)
            grid.addWidget(widget, row, 1)
        # 关键词 / 正则共用第 4 行：切换类型时只显示对应标签与输入框。
        self._text_label = _label("关键词")
        self._pattern_label = _label("正则")
        grid.addWidget(self._text_label, 4, 0)
        grid.addWidget(self._text, 4, 1)
        grid.addWidget(self._pattern_label, 4, 0)
        grid.addWidget(self._pattern, 4, 1)
        self._kind_hint = hint_label("", wrap=True)
        grid.addWidget(self._kind_hint, 5, 1)
        grid.addWidget(_label("严重级"), 6, 0)
        grid.addWidget(self._severity, 6, 1)
        self._target_label = _label("匹配对象")
        grid.addWidget(self._target_label, 7, 0)
        grid.addWidget(self._target, 7, 1)
        self._time_labels: dict[str, QLabel] = {}
        for row, (text, widget) in enumerate(
            (
                ("时间字段", self._time_field),
                ("时间条件", self._time_op),
                ("起始时间", self._time_value),
                ("结束时间", self._time_end),
            ),
            start=8,
        ):
            label = _label(text)
            self._time_labels[text] = label
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        self._size_labels: dict[str, QLabel] = {}
        for row, (text, widget) in enumerate(
            (
                ("大小条件", self._size_op),
                ("起始大小", self._size_value),
                ("结束大小", self._size_end),
            ),
            start=12,
        ):
            label = _label(text)
            self._size_labels[text] = label
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
        self._validator_label = _label("校验器")
        grid.addWidget(self._validator_label, 15, 0)
        grid.addWidget(self._validator, 15, 1)
        self._filetype_label = _label("文件类型")
        grid.addWidget(self._filetype_label, 16, 0)
        grid.addWidget(self._filetype_value, 16, 1)
        self._metadata_key_label = _label("元数据字段")
        grid.addWidget(self._metadata_key_label, 17, 0)
        grid.addWidget(self._metadata_key, 17, 1)
        self._metadata_text_label = _label("元数据文本")
        grid.addWidget(self._metadata_text_label, 18, 0)
        grid.addWidget(self._metadata_text, 18, 1)
        self._extra_label = _label("词条 / 哈希")
        grid.addWidget(self._extra_label, 19, 0)
        grid.addWidget(self._extra, 19, 1)
        self._entropy_value_label = _label("熵阈值")
        grid.addWidget(self._entropy_value_label, 20, 0)
        grid.addWidget(self._entropy_value, 20, 1)
        self._entropy_min_label = _label("最小长度")
        grid.addWidget(self._entropy_min_label, 21, 0)
        grid.addWidget(self._entropy_min, 21, 1)
        outer.addLayout(grid)

        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        flags.setSpacing(12)
        self._case = QCheckBox("区分大小写", self)
        self._case.setChecked(self._case_by_kind.get(current_kind, False))
        self._case.toggled.connect(self._remember_case)
        self._whole = QCheckBox("整词匹配", self)
        self._whole.setChecked(rule.matcher.whole_word if rule else False)
        self._whole.setToolTip("仅关键词类型可用；按词边界匹配，避免中文关键词被误判")
        self._enabled = QCheckBox("启用", self)
        self._enabled.setChecked(rule.enabled if rule else True)
        for widget in (self._case, self._whole, self._enabled):
            flags.addWidget(widget)
        flags.addSpacing(12)
        flags.addWidget(QLabel("最少命中", self))
        self._min_hits = QSpinBox(self)
        self._min_hits.setRange(1, 9999)
        self._min_hits.setValue(rule.min_hits if rule else 1)
        self._min_hits.setToolTip("同一文件内至少命中多少次才算该规则命中（默认 1）")
        flags.addWidget(self._min_hits)
        flags.addWidget(QLabel("最多命中", self))
        self._max_hits = QSpinBox(self)
        self._max_hits.setRange(0, 9999)
        self._max_hits.setSpecialValueText("不限")
        self._max_hits.setValue(rule.max_hits if rule else 0)
        self._max_hits.setToolTip("同一文件最多命中多少次；0 表示不限。超过上限视为规则未命中")
        flags.addWidget(self._max_hits)
        flags.addStretch(1)
        outer.addLayout(flags)

        self._kind.currentIndexChanged.connect(lambda _index: self._sync_kind_fields(self._current_kind))
        self._time_op.currentIndexChanged.connect(self._sync_time_end)
        self._size_op.currentIndexChanged.connect(self._sync_size_end)
        self._sync_kind_fields(current_kind)

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

    @property
    def _current_kind(self) -> str:
        data = self._kind.currentData()
        return data if isinstance(data, str) and data else MATCH_KEYWORD

    def _remember_case(self, checked: bool) -> None:
        kind = self._current_kind
        if kind in self._case_by_kind:
            self._case_by_kind[kind] = checked

    def _sync_time_end(self, *_args: object) -> None:
        """「介于」才显示结束时间；其它条件隐藏，避免误以为需要填。"""
        between = self._current_kind == MATCH_TIME and self._time_op.currentData() == TIME_BETWEEN
        self._time_end.setVisible(between)
        self._time_labels["结束时间"].setVisible(between)

    def _sync_size_end(self, *_args: object) -> None:
        """大小「介于」才显示结束大小。"""
        between = self._current_kind == MATCH_SIZE and self._size_op.currentData() == SIZE_BETWEEN
        self._size_end.setVisible(between)
        self._size_labels["结束大小"].setVisible(between)
        self._relayout()

    def _relayout(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
        if self.isVisible():
            self.adjustSize()

    def _sync_kind_fields(self, kind: str) -> None:  # noqa: PLR0912 - 逐类型显隐
        """按类型切换可见字段：关键词 / 正则 / 时间 / 大小各显示自己的输入项。"""
        keyword = kind == MATCH_KEYWORD
        regex = kind == MATCH_REGEX
        time_rule = kind == MATCH_TIME
        size_rule = kind == MATCH_SIZE
        filetype_rule = kind == MATCH_FILETYPE
        metadata_rule = kind == MATCH_METADATA
        dictionary_rule = kind == MATCH_DICTIONARY
        hash_rule = kind == MATCH_HASH
        entropy_rule = kind == MATCH_ENTROPY
        pattern_rule = keyword or regex
        self._text.setVisible(keyword)
        self._text_label.setVisible(keyword)
        self._pattern.setVisible(regex)
        self._pattern_label.setVisible(regex)
        self._whole.setVisible(keyword)
        self._case.setVisible(pattern_rule)
        self._target.setVisible(pattern_rule)
        self._target_label.setVisible(pattern_rule)
        self._validator.setVisible(regex)
        self._validator_label.setVisible(regex)
        self._filetype_value.setVisible(filetype_rule)
        self._filetype_label.setVisible(filetype_rule)
        for widget in (self._metadata_key, self._metadata_key_label, self._metadata_text, self._metadata_text_label):
            widget.setVisible(metadata_rule)
        for widget in (self._extra, self._extra_label):
            widget.setVisible(dictionary_rule or hash_rule)
        if dictionary_rule:
            self._extra.setPlaceholderText("每行一个词条（忽略大小写）")
        elif hash_rule:
            self._extra.setPlaceholderText("每行一个 64 位 SHA-256")
        for widget in (
            self._entropy_value,
            self._entropy_value_label,
            self._entropy_min,
            self._entropy_min_label,
        ):
            widget.setVisible(entropy_rule)
        for text in ("时间字段", "时间条件", "起始时间"):
            self._time_labels[text].setVisible(time_rule)
        self._time_field.setVisible(time_rule)
        self._time_op.setVisible(time_rule)
        self._time_value.setVisible(time_rule)
        self._sync_time_end()
        for text in ("大小条件", "起始大小"):
            self._size_labels[text].setVisible(size_rule)
        self._size_op.setVisible(size_rule)
        self._size_value.setVisible(size_rule)
        self._sync_size_end()
        self._case.blockSignals(True)
        self._case.setChecked(self._case_by_kind.get(kind, False))
        self._case.blockSignals(False)
        if time_rule:
            hint = "时间：按文件时间戳比较；创建时间在 Linux 上退化为 ctime（更接近元数据变更时间）。"
        elif size_rule:
            hint = "大小：按文件字节数比较，支持 10KB / 1.5MB / 2GB 或裸字节数；区间为半开 [起始, 结束)。"
        elif filetype_rule:
            hint = "文件类型：按魔数识别真实类型（逗号分隔，如 pdf,zip,exe），可发现改名文件。"
        elif metadata_rule:
            hint = "文档元数据：读取 docx/xlsx/pptx 的作者 / 公司 / 标题等字段并做包含匹配。"
        elif dictionary_rule:
            hint = "自定义词典：每行一个词条，命中任意词条即算该规则命中（忽略大小写）。"
        elif hash_rule:
            hint = "文件哈希：每行一个 SHA-256，按文件内容摘要精确匹配。"
        elif entropy_rule:
            hint = "高熵串：长度 ≥ 最小长度且 Shannon 熵 ≥ 阈值的随机 / 加密风格字符串。"
        elif keyword:
            hint = "关键词：按原文包含匹配，可勾选整词；默认不区分大小写。可换行，保存时合并为单行。"
        else:
            hint = "正则：按行逐条匹配（Python re 语法）；可选校验器过滤误报。可换行，保存时合并为单行。"
        self._kind_hint.setText(hint)
        # 显隐切换后让对话框高度跟随内容重新计算（隐藏字段不占空间）。
        self._relayout()

    def _commit(self) -> None:
        kind = self._current_kind
        if kind == MATCH_TIME:
            compare = self._time_op.currentData() or TIME_BEFORE
            matcher = Matcher(
                kind=kind,
                target=self._time_field.currentData() or TIME_MODIFIED,
                compare=compare,
                value=self._time_value.text().strip(),
                value2=self._time_end.text().strip() if compare == TIME_BETWEEN else "",
            )
        elif kind == MATCH_FILETYPE:
            matcher = Matcher(kind=kind, value=self._filetype_value.text().strip())
        elif kind == MATCH_METADATA:
            matcher = Matcher(
                kind=kind,
                value=self._metadata_key.currentData() or METADATA_KEYS[0],
                text=self._metadata_text.text().strip(),
            )
        elif kind in (MATCH_DICTIONARY, MATCH_HASH):
            matcher = Matcher(kind=kind, value=self._extra.toPlainText().strip())
        elif kind == MATCH_ENTROPY:
            matcher = Matcher(
                kind=kind,
                value=self._entropy_value.text().strip(),
                value2=self._entropy_min.text().strip() or "24",
            )
        elif kind == MATCH_SIZE:
            compare = self._size_op.currentData() or SIZE_LT
            matcher = Matcher(
                kind=kind,
                compare=compare,
                value=self._size_value.text().strip(),
                value2=self._size_end.text().strip() if compare == SIZE_BETWEEN else "",
            )
        else:
            matcher = Matcher(
                kind=kind,
                text=_collapse_multiline(self._text.toPlainText()) if kind == MATCH_KEYWORD else "",
                pattern=_collapse_multiline(self._pattern.toPlainText()) if kind == MATCH_REGEX else "",
                case_sensitive=self._case.isChecked(),
                whole_word=self._whole.isChecked() if kind == MATCH_KEYWORD else False,
                target=self._target.currentData() or TARGET_CONTENT,
                validator=self._validator.currentData() if kind == MATCH_REGEX else "",
            )
        self._result = Rule(
            id=self._id.text().strip(),
            name=self._name.text().strip(),
            matcher=matcher,
            group=self._group.text().strip() or "自定义",
            enabled=self._enabled.isChecked(),
            severity=SEVERITY_LEVELS[self._severity.currentIndex()],
            min_hits=self._min_hits.value(),
            max_hits=self._max_hits.value(),
        )
        self.accept()

    def value(self) -> Rule | None:
        """提交的规则对象；取消时为 ``None``。"""
        return self._result


_CONTEXT_ROLE = Qt.ItemDataRole.UserRole + 11
_CONTEXT_PADDING = 4
_PATH_ROLE = Qt.ItemDataRole.UserRole + 12
_MATCH_ROLE = Qt.ItemDataRole.UserRole + 13
_MATCHES_ROLE = Qt.ItemDataRole.UserRole + 14
"""结果树的数据角色：上下文列内边距（像素）、文件路径、单条命中与文件全部命中。"""


def reveal_command(path: Path) -> list[str]:
    """在系统文件管理器中定位文件的命令（Windows 选中文件 / macOS Finder 显示）。"""
    if sys.platform.startswith("win"):
        return ["explorer", f"/select,{path}"]
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    # Linux 的 xdg-open 只能打开目录：目标是文件时退到父目录。
    return ["xdg-open", str(path if path.is_dir() else path.parent)]


def _context_parts(index: QModelIndex | QPersistentModelIndex) -> tuple[str, str, str] | None:
    """读取单元格存的三段上下文（命中前 / 命中 / 命中后）；未存时返回 ``None``。"""
    data = index.data(_CONTEXT_ROLE)
    if isinstance(data, tuple) and len(data) == 3 and all(isinstance(part, str) for part in data):
        return data
    return None


def _utf16_units(text: str) -> int:
    """Python 字符数 → QTextDocument 位置单位（UTF-16，emoji 占两格）。"""
    return len(text.encode("utf-16-le")) // 2


_SEVERITY_COLORS: dict[str, str] = {
    "critical": "danger",
    "high": "warning",
    "medium": "accent",
    "low": "muted",
    "info": "muted",
}
"""严重级 → 主题调色板字段名；未登记的级别不着色。"""


def _severity_color(severity: str) -> QColor | None:
    """严重级对应的前景色；空值或未知级别返回 ``None``（沿用默认前景）。"""
    tone = _SEVERITY_COLORS.get(severity)
    if tone is None:
        return None
    return QColor(getattr(_qt_theme.active_theme().palette, tone))


def _highest_severity(matches: Sequence[RuleMatch]) -> str:
    """取一组命中的最高严重级；没有可识别级别时返回空串。"""
    ranks = {level: index for index, level in enumerate(SEVERITY_LEVELS)}
    best = ""
    for match in matches:
        if ranks.get(match.severity, -1) > ranks.get(best, -1):
            best = match.severity
    return best


def _hit_summary(hit: FileHit) -> str:
    """文件行摘要：命中条数 · 编码 · 大小 · 引擎 · 耗时（列收起后的合并口径）。"""
    parts = [f"{len(hit.matches)} 条"]
    if hit.encoding:
        parts.append(hit.encoding)
    parts.append(format_size(hit.size))
    if hit.engine:
        parts.append(engine_label(hit.engine))
    parts.append(f"{hit.elapsed * 1000:.1f} ms")
    return " · ".join(parts)


class _ContextDelegate(QStyledItemDelegate):  # pragma: no cover - Qt 控件
    """结果树「上下文」列：按列宽换行完整显示，命中片段用强调底色标出。

    默认 delegate 只在固定列宽内省略文本（``…``），长上下文必须靠悬停提示才能
    看全。这里改用 ``QTextDocument`` 排版：按列宽自动换行、行高随内容增长，命中段
    单独加底色，保证「整段相关上下文」和「命中位置」在同一行里同时可见；换行只
    影响这一列，其它列仍是单行。
    """

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._documents: dict[tuple[int, int, bool, str], QTextDocument] = {}
        self._width = 0
        self._compact = compact

    def set_compact(self, compact: bool) -> None:
        """紧凑模式：上下文不再换行，单行省略，行高保持一致。"""
        if self._compact == compact:
            return
        self._compact = compact
        self.reset()

    def reset(self) -> None:
        """结果树重建时清掉排版缓存，避免旧命中的文档对象继续驻留。"""
        self._documents.clear()
        self._width = 0

    def _column_width(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> int:
        """单元格可用文本宽度：减去内边距，列宽未知时退回 ``option.rect``。"""
        widget = option.widget
        width = widget.columnWidth(index.column()) if widget is not None and hasattr(widget, "columnWidth") else 0
        if width <= 0:
            width = option.rect.width()
        return max(width - _CONTEXT_PADDING * 2, 1)

    def _document(
        self,
        index: QModelIndex | QPersistentModelIndex,
        width: int,
        *,
        selected: bool,
        font: QFont,
    ) -> QTextDocument | None:
        parts = _context_parts(index)
        if parts is None:
            return None
        theme = _qt_theme.active_theme()
        # 紧凑模式用不限宽（-1）排版，文本保持单行，超出部分由单元格裁剪。
        wrap_width = -1 if self._compact else width
        key = (id(parts), wrap_width, selected, theme.name)
        cached = self._documents.get(key)
        if cached is not None:
            return cached
        if wrap_width != self._width:
            # 窗口 / 列宽变化后旧排版的换行位置全部失效，直接丢弃重排。
            self._documents.clear()
            self._width = wrap_width
        before, matched, after = parts
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(font)
        document.setPlainText(f"{before}{matched}{after}")
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        base = QTextCharFormat()
        base.setForeground(QColor(theme.palette.select_fg if selected else theme.palette.text))
        cursor.mergeCharFormat(base)
        highlight = QTextCharFormat()
        highlight.setBackground(QColor(theme.palette.warning))
        highlight.setForeground(QColor(theme.palette.on_accent))
        start = _utf16_units(before)
        cursor.setPosition(start)
        cursor.setPosition(start + _utf16_units(matched), QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(highlight)
        document.setTextWidth(wrap_width)
        self._documents[key] = document
        return document

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if _context_parts(index) is None:
            # 父节点摘要 / 跳过与错误行仍是普通单行文本，交回默认绘制。
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        width = self._column_width(opt, index)
        document = self._document(index, width, selected=selected, font=opt.font)
        if document is None:
            return
        rect = opt.rect.adjusted(_CONTEXT_PADDING, _CONTEXT_PADDING, -_CONTEXT_PADDING, -_CONTEXT_PADDING)
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.topLeft())
        document.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        base = super().sizeHint(option, index)
        if _context_parts(index) is None or self._compact:
            return base
        width = self._column_width(option, index)
        document = self._document(index, width, selected=False, font=option.font)
        if document is None:
            return base
        height = int(document.size().height()) + _CONTEXT_PADDING * 2
        return QSize(base.width(), max(base.height(), height))


class _ExpressionDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑组合式：id、名称、表达式文本与启用状态，可插入规则 / 组合式 id。"""

    def __init__(self, parent: QWidget, ruleset: RuleSet, expression: Expression | None = None) -> None:
        super().__init__(parent)
        self._ruleset = ruleset
        self._result: Expression | None = None
        self.setWindowTitle("编辑组合式" if expression is not None else "新增组合式")
        self.setMinimumWidth(760)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._id = QLineEdit(expression.id if expression is not None else fw.new_expression_id(ruleset), self)
        grid.addWidget(QLabel("ID", self), 0, 0)
        grid.addWidget(self._id, 0, 1)
        self._name = QLineEdit(expression.name if expression is not None else "", self)
        grid.addWidget(QLabel("名称", self), 1, 0)
        grid.addWidget(self._name, 1, 1)
        self._expr = QLineEdit(expression.expression if expression is not None else "", self)
        self._expr.setPlaceholderText("例如：(pii or phone) and not allow_list")
        grid.addWidget(QLabel("表达式", self), 2, 0)
        grid.addWidget(self._expr, 2, 1)

        insert_row = QHBoxLayout()
        insert_row.setContentsMargins(0, 0, 0, 0)
        insert_row.setSpacing(6)
        self._insert_source = QComboBox(self)
        for rule in ruleset.rules:
            self._insert_source.addItem(f"规则：{rule.id}（{rule.name}）", rule.id)
        for item in ruleset.expressions:
            if expression is not None and item.id == expression.id:
                continue
            self._insert_source.addItem(f"组合式：{item.id}（{item.name}）", item.id)
        insert_row.addWidget(hint_label("插入 ID", wrap=False))
        insert_row.addWidget(self._insert_source, 1)
        insert_button = button("插入到表达式")
        insert_button.clicked.connect(self._insert_selected_id)
        insert_row.addWidget(insert_button)
        grid.addLayout(insert_row, 3, 1)
        outer.addLayout(grid)

        flags = QHBoxLayout()
        self._enabled = QCheckBox("启用（默认参与生效表达式）", self)
        self._enabled.setChecked(expression.enabled if expression is not None else True)
        flags.addWidget(self._enabled)
        flags.addStretch(1)
        validate_button = button("校验")
        validate_button.clicked.connect(self._validate)
        flags.addWidget(validate_button)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        flags.addWidget(cancel)
        confirm_button = button("保存", kind="primary")
        confirm_button.clicked.connect(self._commit)
        flags.addWidget(confirm_button)
        outer.addLayout(flags)

    def _candidate(self) -> Expression:
        return Expression(
            id=self._id.text().strip(),
            name=self._name.text().strip(),
            expression=self._expr.text().strip(),
            enabled=self._enabled.isChecked(),
        )

    def _checked(self) -> Expression:
        candidate = self._candidate()
        validate_expression(self._ruleset, candidate)
        return candidate

    def _insert_selected_id(self) -> None:
        target = self._insert_source.currentData()
        if not isinstance(target, str) or not target:
            return
        text = self._expr.text()
        cursor = max(0, min(self._expr.cursorPosition(), len(text)))
        before = "" if cursor == 0 or text[cursor - 1].isspace() or text[cursor - 1] == "(" else " "
        after = "" if cursor >= len(text) or text[cursor].isspace() or text[cursor] == ")" else " "
        inserted = f"{before}{target}{after}"
        self._expr.setText(f"{text[:cursor]}{inserted}{text[cursor:]}")
        self._expr.setCursorPosition(cursor + len(inserted))
        self._expr.setFocus()

    def _validate(self) -> None:
        try:
            self._checked()
        except ValueError as exc:
            QMessageBox.critical(self, "组合式无效", str(exc))
            return
        QMessageBox.information(self, "组合式有效", "表达式解析通过")

    def _commit(self) -> None:
        try:
            self._result = self._checked()
        except ValueError as exc:
            QMessageBox.critical(self, "组合式无效", str(exc))
            return
        self.accept()

    def value(self) -> Expression | None:
        """提交的组合式；取消时为 ``None``。"""
        return self._result


class FilescanQtWindow(RowDetailMixin, QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """敏感内容扫描窗口：侧边栏分页（扫描 / 结果 / 规则 / 设置）+ 状态栏 + 任务日志。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self.settings = fw.load_settings(settings_path or fw.config_path())
        self.ruleset: RuleSet = self.settings.ruleset
        self.result: ScanResult | None = None
        self._token: CancelToken | None = None
        self._bridge: _ScanProgress | None = None
        self._worker_running = False
        # 扫描进度按文件数计速（个/s），与状态栏右侧进度条同一套采样口径。
        self._scan_meter = SpeedMeter(smoothing=0.5)
        self._tasks: list[fw.ScanTask] = list(self.settings.tasks)
        # 扫描结果按任务 id 落盘在 ui/filescanw-results.json，退出应用后仍可恢复。
        # 只保留仍存在的任务的结果，避免移除 / 清空任务后残留。
        self._results_file = (
            settings_path.with_name(f"{settings_path.stem}-results.json")
            if settings_path is not None
            else fw.results_path()
        )
        stored_results = fw.load_results(self._results_file)
        task_ids = {task.id for task in self._tasks}
        self._results: dict[str, ScanResult] = {
            task_id: result for task_id, result in stored_results.items() if task_id in task_ids
        }
        self._scanning_task_id = ""
        self._stopping_task_id = ""
        self._viewing_task_id = ""
        self._rendered_result: ScanResult | None = None
        self._whitelist_notice = ""
        # 文件 / 目录对话框的上次位置：与设置字段同名，读写交给共用 PathHistory。
        self._dialog_state: dict[str, str] = {
            "last_task_dir": self.settings.last_task_dir,
            "last_import_dir": self.settings.last_import_dir,
            "last_export_dir": self.settings.last_export_dir,
        }
        self._paths = PathHistory(self._dialog_state)
        # 界面状态（当前页 / 结果树列宽）也走共用 WindowState；落盘仍由设置字段承载。
        self._ui_payload: dict[str, object] = {
            "active_page": self.settings.active_page,
            "result_columns": list(self.settings.result_columns),
        }
        self._ui = WindowState(self._ui_payload)  # type: ignore[arg-type]
        super().__init__(
            title=TITLE,
            config_name=fw.SETTINGS_NAME,
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=(fw.MIN_WIDTH, fw.MIN_HEIGHT),
            on_close=self._on_close,
            persist=self._persist_settings,
            sidebar_collapsed=self.settings.sidebar_collapsed,
            settings_path=settings_path,
        )
        # 四页都先建好再登记：扫描页要立即填目录 / 白名单，结果与规则页虽然首屏
        # 不可见，也必须在读设置时就能回填（切页只是把已建好的页挂上去）。
        # 设置页先建：扫描页的「当前选项」摘要要读设置页里的控件。
        self._settings_page = self._build_settings_page()
        self._scan_page = self._build_scan_page()
        self._result_page = self._build_result_page()
        self._rules_page = self._build_rules_page()
        self._apply_compact_mode(self.settings.compact_mode, rebuild_rows=False)
        self.add_pages(
            PAGES,
            {
                "scan": lambda: self._scan_page,
                "results": lambda: self._result_page,
                "rules": lambda: self._rules_page,
                "settings": lambda: self._settings_page,
            },
        )
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)
        # 任务行在 _build_scan_page 里建好列表骨架，这里再按设置回填行卡。
        self._load_whitelist()
        self._refresh_rules()
        if self.settings.filter_rule:
            index = self._filter_rule.findText(self.settings.filter_rule)
            if index >= 0:
                self._filter_rule.setCurrentIndex(index)
        self._refresh_options_tags()
        self._refresh_task_rows()
        self._restore_results_view()
        saved_page = self.settings.active_page if self.settings.active_page in {spec.key for spec in PAGES} else "scan"
        self.show_page(saved_page)
        self.set_page_status("scan", self._whitelist_notice or self._summary_text())

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_scan_page(self) -> _FilescanPage:
        """扫描页只保留任务列表：目录、扫描、查看结果都落在每一行任务上。"""
        page = _FilescanPage(self, "scan", scroll=False, spacing=6)
        layout = page.content

        card = Card("扫描任务")
        self._option_tags_host = QWidget(card)
        tags = FlowLayout(self._option_tags_host, h_spacing=6, v_spacing=4)
        self._option_tags: dict[str, StatusTag] = {}
        for key in ("recursive", "depth", "workers", "whitelist", "cache"):
            tag = StatusTag("", tone="muted", parent=self._option_tags_host)
            self._option_tags[key] = tag
            tags.addWidget(tag)
        card.add_title_action(self._option_tags_host)
        # 移除 / 清空放在卡片右上角：任务行内只留扫描与查看结果，避免误点删除。
        self._remove_task_button = button("移除", kind="ghost", icon="trash")
        self._remove_task_button.setToolTip("移除当前选中的扫描任务（不会删除磁盘文件）")
        self._remove_task_button.clicked.connect(self._remove_selected_task)
        card.add_title_action(self._remove_task_button)
        self._clear_tasks_button = button("清空", kind="ghost", icon="trash")
        self._clear_tasks_button.setToolTip("移除全部扫描任务（不会删除磁盘文件）")
        self._clear_tasks_button.clicked.connect(self._clear_tasks)
        card.add_title_action(self._clear_tasks_button)

        self._task_list = RowListWidget(card)
        self._task_list.setObjectName("ScanTaskList")
        self._task_list.setSpacing(4)
        self._task_list.setMinimumHeight(200)
        self._task_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self._task_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._task_list.currentRowChanged.connect(self._on_task_selected)
        self._task_list.itemDoubleClicked.connect(self._on_task_double_clicked)
        card.body.addWidget(self._task_list, 1)
        self._task_empty = _ScanTaskEmptyRow(card)
        self._task_empty.setVisible(False)
        card.body.addWidget(self._task_empty, 1)
        self._task_detail = _ScanTaskDetailView(card)
        self._task_detail.setVisible(False)
        self.bind_row_detail("tasks", self._task_list, self._task_detail)
        layout.addWidget(card, 1)
        return page

    @staticmethod
    def _spin(parent: QWidget, minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox(parent)
        widget.setRange(minimum, maximum)
        widget.setValue(max(minimum, min(maximum, value)))
        widget.setMaximumWidth(160)
        return widget

    @staticmethod
    def _mb_spin(parent: QWidget, value: int) -> QDoubleSpinBox:
        """单文件读取上限：界面上用 MB（0.1 步长），存储与扫描仍是字节。"""
        widget = QDoubleSpinBox(parent)
        widget.setDecimals(1)
        widget.setRange(0.1, 1024.0)
        widget.setSingleStep(1.0)
        widget.setSuffix(" MB")
        widget.setValue(max(0.1, min(1024.0, value / (1024 * 1024))))
        widget.setMaximumWidth(160)
        return widget

    def _build_settings_page(self) -> _FilescanPage:
        """设置页：扫描范围 / 后缀白名单 / 缓存与配置，三张卡片纵向排布。"""
        page = _FilescanPage(self, "settings", scroll=True, margins=(0, 0, 4, 8))
        body = page.content

        options = Card("扫描范围")
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
        self._skip_hidden = QCheckBox("跳过隐藏文件/目录", options)
        self._skip_hidden.setChecked(self.settings.skip_hidden)
        self._skip_hidden.setToolTip("默认不跳过，避免漏扫 .env 等隐藏配置；勾选后按名称前缀 . 与 Windows 隐藏属性过滤")
        self._normalize_text = QCheckBox("文本归一化", options)
        self._normalize_text.setChecked(self.settings.normalize_text)
        self._normalize_text.setToolTip(
            "匹配前做 NFKC 归一化，全角字母数字、①、㈱ 等按兼容形式匹配；命中片段仍取自原文"
        )
        for widget in (
            self._recursive,
            self._follow,
            self._skip_binary,
            self._scan_documents,
            self._skip_hidden,
            self._normalize_text,
        ):
            checks.addWidget(widget)
        checks.addStretch(1)
        options.body.addLayout(checks)

        form = FormGrid(options)
        self._max_depth = self._spin(form, 0, 64, self.settings.max_depth)
        self._min_size = self._spin(form, 0, 2_000_000_000, self.settings.min_size)
        self._max_size = self._spin(form, 0, 2_000_000_000, self.settings.max_size)
        self._max_file_bytes = self._mb_spin(form, self.settings.max_file_bytes)
        self._workers = self._spin(form, 1, 64, self.settings.workers)
        self._context_chars = self._spin(form, 0, 400, self.settings.context_chars)
        self._include_ext = QLineEdit(self.settings.include_ext, options)
        self._exclude_ext = QLineEdit(self.settings.exclude_ext, options)
        self._force_ext = QLineEdit(self.settings.force_ext, options)
        self._include_ext.setPlaceholderText("留空表示不限制；如 .txt;.md")
        self._exclude_ext.setPlaceholderText("留空表示不排除；如 .log;.tmp")
        self._force_ext.setPlaceholderText("留空表示不强制；如 .pdf")
        self._modified_after = QLineEdit(self.settings.modified_after, options)
        self._modified_before = QLineEdit(self.settings.modified_before, options)
        self._skip_dirs = _multiline_editor(
            self.settings.skip_dirs,
            "分号分隔，如 .git;node_modules；留空表示不跳过目录",
            min_height=64,
            max_height=96,
        )
        self._skip_dirs.setToolTip("跳过目录名，按任意层级匹配；含斜杠的条目匹配相对根目录的路径后缀")
        self._modified_after.setPlaceholderText("YYYY-MM-DD HH:MM:SS，可空")
        self._modified_before.setPlaceholderText("YYYY-MM-DD HH:MM:SS，可空")
        form.add_pair("深度(0=不限)", self._max_depth, "后缀包含", self._include_ext)
        form.add_pair("最小字节", self._min_size, "后缀排除", self._exclude_ext)
        form.add_pair("最大字节(0=不限)", self._max_size, "强制后缀", self._force_ext)
        form.add_pair("单文件上限", self._max_file_bytes, "并发数", self._workers)
        form.add_pair("上下文长度", self._context_chars, "修改时间≥", self._modified_after)
        form.add_pair("修改时间≤", self._modified_before)
        form.add_pair("跳过目录", self._skip_dirs)
        options.body.addWidget(form)
        options.body.addWidget(hint_label("白名单外强制扫描（如 .pdf）；大小 / 时间过滤属「范围外」，从不读取"))
        body.addWidget(options)

        whitelist = Card("后缀白名单")
        whitelist.body.addWidget(
            hint_label("每行一个类型；双击行编辑，后缀用英文逗号【,】分隔；可新增类型，Ctrl / Shift 多选后批量删除")
        )
        whitelist_row = QHBoxLayout()
        whitelist_row.setContentsMargins(0, 0, 0, 0)
        whitelist_row.setSpacing(6)
        self._whitelist_table = make_table(
            ("类型", "后缀（用 , 分隔）"),
            widths=(140, 560),
            stretch_columns=(1,),
            parent=whitelist,
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
        whitelist.body.addLayout(whitelist_row)
        body.addWidget(whitelist)

        cache = Card("缓存与配置")
        cache.body.addWidget(
            hint_label("缓存按「路径 + 大小 + 修改时间 + 规则/选项指纹」复用上次结果；配置可整份导入导出。")
        )
        cache_row = QHBoxLayout()
        cache_row.setContentsMargins(0, 0, 0, 0)
        cache_row.setSpacing(6)
        self._use_cache = QCheckBox("使用缓存", cache)
        self._use_cache.setChecked(self.settings.use_cache)
        self._use_cache.setToolTip("关闭后每次都完整读盘，不复用上次结果")
        cache_row.addWidget(self._use_cache)
        cache_row.addStretch(1)
        for text, handler in (
            ("导入配置…", self._import_config),
            ("导出配置…", self._export_config),
            ("清理缓存", self._clear_cache),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            cache_row.addWidget(widget)
        cache.body.addLayout(cache_row)
        body.addWidget(cache)
        body.addStretch(1)
        return page

    def _build_result_page(self) -> _FilescanPage:
        page = _FilescanPage(self, "results", scroll=False, spacing=6)
        layout = page.content

        card = Card("扫描结果")
        # 任务结果切换器用按钮 + 弹出菜单，而不是 QComboBox：菜单选取后由 Qt
        # 自动关闭，不会出现下拉列表停在展开态的问题。
        self._result_task_button = button("暂无任务结果  ▾", kind="ghost")
        self._result_task_button.setMinimumWidth(200)
        self._result_task_button.setToolTip("切换要查看的扫描任务结果")
        self._result_task_button.setEnabled(False)
        self._result_task_button.clicked.connect(self._open_result_task_menu)
        card.add_title_action(hint_label("任务", wrap=False))
        card.add_title_action(self._result_task_button)
        self._sync_result_switcher()
        # 选中行后可定位文件 / 复制命中；按钮与右键菜单共用同一套动作。
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 0, 0, 0)
        action_bar.setSpacing(6)
        self._locate_button = button("定位文件", kind="ghost")
        self._locate_button.setToolTip("在系统文件管理器中显示选中命中的文件")
        self._locate_button.clicked.connect(self._locate_selected_file)
        action_bar.addWidget(self._locate_button)
        self._copy_match_button = button("复制命中内容", kind="ghost")
        self._copy_match_button.setToolTip("复制选中命中的匹配片段（默认打码，勾选「导出 / 复制原文」后复制原文）")
        self._copy_match_button.clicked.connect(self._copy_selected_matches)
        action_bar.addWidget(self._copy_match_button)
        self._copy_context_button = button("复制上下文", kind="ghost")
        self._copy_context_button.clicked.connect(lambda: self._copy_selected_matches(context=True))
        action_bar.addWidget(self._copy_context_button)
        self._copy_note_button = button("复制扫描说明", kind="ghost")
        self._copy_note_button.setToolTip("按「【规则：…，匹配项：…】」逐条生成扫描说明并复制")
        self._copy_note_button.clicked.connect(self._copy_selected_notes)
        action_bar.addWidget(self._copy_note_button)
        action_bar.addStretch(1)
        card.body.addLayout(action_bar)

        self._tree = QTreeWidget(card)
        self._tree.setHeaderLabels(["文件 / 规则", "严重级", "行号", "上下文（打码）"])
        self._tree.setAlternatingRowColors(True)
        # 引擎 / 耗时并进文件行摘要，固定列宽只留给路径与两列短指标。
        restore_columns = self._ui.sizes("result_columns", count=3, default=(300, 56, 48))
        for index, width in enumerate(restore_columns):
            self._tree.setColumnWidth(index, width)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree.setToolTip("双击行定位文件，右键行可打开 / 复制更多")
        self._tree.header().setStretchLastSection(True)
        self._tree.setMinimumHeight(240)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._open_result_menu)
        self._tree.itemDoubleClicked.connect(self._result_item_double_clicked)
        self._tree.itemSelectionChanged.connect(self._update_result_actions)
        # 上下文列换行完整显示 + 命中底色；其余列继续用默认 delegate 单行绘制。
        self._context_delegate = _ContextDelegate(self._tree, compact=self.settings.compact_mode)
        self._tree.setItemDelegateForColumn(3, self._context_delegate)
        # 列宽变化后旧排版的换行位置失效：清缓存并重算行高，避免行高停在首帧宽度上。
        self._tree.header().sectionResized.connect(self._context_layout_changed)
        card.body.addWidget(self._tree, 1)
        layout.addWidget(card, 1)
        self._update_result_actions()

        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(0, 0, 0, 0)
        filter_bar.setSpacing(6)
        filter_bar.addWidget(QLabel("规则筛选"))
        self._filter_rule = QComboBox(self)
        self._filter_rule.addItem("全部")
        filter_bar.addWidget(self._filter_rule)
        filter_bar.addWidget(QLabel("严重级"))
        self._filter_severity = QComboBox(self)
        self._filter_severity.addItems(["全部", *[SEVERITY_LABELS[level] for level in SEVERITY_LEVELS]])
        self._filter_severity.setToolTip("按规则严重级筛选命中；「全部」不筛选")
        filter_bar.addWidget(self._filter_severity)
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
        layout.addLayout(filter_bar)

        # 导出 / 原文开关独占一行：与筛选控件挤在同一行会把结果页最小宽度撑到窗口
        # 无法缩小，也让路径筛选框没有伸展空间。
        export_bar = QHBoxLayout()
        export_bar.setContentsMargins(0, 0, 0, 0)
        export_bar.setSpacing(6)
        export_bar.addWidget(hint_label("双击行定位文件，右键行可打开 / 复制更多", wrap=False), 1)
        self._compact_mode = QCheckBox("紧凑模式", self)
        self._compact_mode.setToolTip("任务行、结果行与规则表使用更紧凑的高度；结果上下文只显示单行，悬停看全文")
        self._compact_mode.setChecked(self.settings.compact_mode)
        self._compact_mode.toggled.connect(self._set_compact_mode)
        export_bar.addWidget(self._compact_mode)
        self._show_skipped = QCheckBox("显示跳过项", self)
        self._show_skipped.setToolTip("默认隐藏跳过 / 范围外条目；勾选后在结果树中列出跳过的文件与原因")
        self._show_skipped.setChecked(self.settings.show_skipped)
        self._show_skipped.toggled.connect(self._set_show_skipped)
        export_bar.addWidget(self._show_skipped)
        self._export_raw = QCheckBox("导出 / 复制原文", self)
        self._export_raw.setToolTip("勾选后导出报告与复制命中内容使用原文；默认打码")
        export_bar.addWidget(self._export_raw)
        export = button("导出报告…", kind="primary")
        export.clicked.connect(self._export_report)
        export_bar.addWidget(export)
        layout.addLayout(export_bar)
        return page

    def _build_rules_page(self) -> _FilescanPage:
        page = _FilescanPage(self, "rules", scroll=False, spacing=6)
        layout = page.content

        self._rules_table = make_table(
            ("启用", "分组", "严重级", "ID", "名称", "匹配器"),
            widths=(60, 110, 70, 140, 170, 420),
            stretch_columns=(5,),
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
            ("载入场景", self._load_scenario),
            ("清空", self._clear_rules),
            ("导入…", self._import_rules),
            ("导出…", self._export_rules),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            buttons.addWidget(widget)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        expression = Card("组合式（and / or / not / near[k]，括号可嵌套；可引用规则 id 与其它组合式 id）")
        self._expression_table = make_table(
            ("启用", "ID", "名称", "表达式"),
            widths=(60, 150, 170, 540),
            stretch_columns=(3,),
            parent=page,
        )
        self._expression_table.setMinimumHeight(150)
        self._expression_table.cellDoubleClicked.connect(lambda _row, _column: self._edit_expression())
        expression.body.addWidget(self._expression_table, 1)
        expression_buttons = QHBoxLayout()
        expression_buttons.setContentsMargins(0, 0, 0, 0)
        expression_buttons.setSpacing(4)
        for text, handler in (
            ("新增组合式", self._add_expression),
            ("编辑组合式", self._edit_expression),
            ("删除组合式", self._delete_expression),
            ("启用", lambda: self._set_expressions_enabled(True)),
            ("禁用", lambda: self._set_expressions_enabled(False)),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            expression_buttons.addWidget(widget)
        expression_buttons.addStretch(1)
        expression.body.addLayout(expression_buttons)
        expression.body.addWidget(
            hint_label("启用的组合式按 OR 生效；未启用的组合式仍可被引用。没有启用组合式时默认 OR 全部启用规则。")
        )
        self._rule_hint = hint_label("")
        expression.body.addWidget(self._rule_hint)
        layout.addWidget(expression)
        return page

    # ------------------------------------------------------------------ #
    # 数据刷新
    # ------------------------------------------------------------------ #
    def _refresh_rules(self) -> None:
        self._rules_table.setRowCount(0)
        for row, rule in enumerate(self.ruleset.rules):
            self._rules_table.insertRow(row)
            enabled = make_table_item("是" if rule.enabled else "否")
            enabled.setData(Qt.ItemDataRole.UserRole, rule.id)
            self._rules_table.setItem(row, 0, enabled)
            self._rules_table.setItem(row, 1, make_table_item(rule.group))
            severity_item = make_table_item(severity_label(rule.severity))
            color = _severity_color(rule.severity)
            if color is not None:
                severity_item.setForeground(QBrush(color))
            self._rules_table.setItem(row, 2, severity_item)
            id_item = make_table_item(rule.id)
            id_item.setToolTip("组合表达式引用这个 id，例如：pii and not email")
            self._rules_table.setItem(row, 3, id_item)
            self._rules_table.setItem(row, 4, make_table_item(rule.name))
            self._rules_table.setItem(row, 5, make_table_item(rule.matcher.describe()))
        self._expression_table.setRowCount(0)
        for row, item in enumerate(self.ruleset.expressions):
            self._expression_table.insertRow(row)
            enabled = make_table_item("是" if item.enabled else "否")
            enabled.setData(Qt.ItemDataRole.UserRole, item.id)
            self._expression_table.setItem(row, 0, enabled)
            self._expression_table.setItem(row, 1, make_table_item(item.id))
            self._expression_table.setItem(row, 2, make_table_item(item.name))
            self._expression_table.setItem(row, 3, make_table_item(item.expression))
        self._rule_hint.setText(
            f"{len(self.ruleset.rules)} 条规则（启用 {self.ruleset.enabled_count()}）；"
            f"{len(self.ruleset.expressions)} 条组合式（启用 {len(self.ruleset.enabled_expressions())}）；"
            f"生效表达式：{effective_expression_text(self.ruleset) or '（无）'}"
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
            f"扫描任务 {len(self._tasks)} 个；单文件上限 {format_size(self._max_file_bytes_value())}"
        )

    # ------------------------------------------------------------------ #
    # 扫描任务
    # ------------------------------------------------------------------ #
    def _task_by_id(self, task_id: str) -> fw.ScanTask | None:
        return next((item for item in self._tasks if item.id == task_id), None)

    def _task_row(self, task_id: str) -> _ScanTaskRow | None:
        for index in range(self._task_list.count()):
            row = self._task_list.itemWidget(self._task_list.item(index))
            if isinstance(row, _ScanTaskRow) and row.task.id == task_id:
                return row
        return None

    def _set_task(self, task: fw.ScanTask) -> None:
        self._tasks = [task if item.id == task.id else item for item in self._tasks]

    def _refresh_task_rows(self, *, update_status: bool = True) -> None:
        """重建任务列表：首项固定是虚线的「添加任务」，任务行保留选中与展开态。"""
        current = self._task_list.currentItem()
        current_row = self._task_list.itemWidget(current) if current is not None else None
        selected_id = current_row.task.id if isinstance(current_row, _ScanTaskRow) else ""
        expanded = self.expanded_row("tasks")
        expanded_id = expanded.task.id if isinstance(expanded, _ScanTaskRow) else ""
        self.detach_row_detail("tasks")
        self._task_list.clear()

        add_row = _ScanTaskAddRow(self._task_list)
        add_row.clicked.connect(self._add_task)
        add_item = QListWidgetItem(self._task_list)
        add_item.setSizeHint(QSize(0, add_row.sizeHint().height()))
        self._task_list.setItemWidget(add_item, add_row)

        for task in self._tasks:
            item = QListWidgetItem(self._task_list)
            row = _ScanTaskRow(
                task,
                has_result=task.id in self._results,
                busy=self._worker_running,
                active=self._worker_running and task.id == self._scanning_task_id,
                stopping=task.id == self._stopping_task_id,
                dense=self.settings.compact_mode,
                parent=self._task_list,
            )
            row.toggleRequested.connect(lambda row: self.toggle_row("tasks", row))
            row.scanRequested.connect(self._start_task_scan)
            row.incrementalRequested.connect(lambda task_id: self._start_task_scan(task_id, mode="incremental"))
            row.fullRequested.connect(lambda task_id: self._start_task_scan(task_id, mode="full"))
            row.stopRequested.connect(self._cancel_task_scan)
            row.resultRequested.connect(self._show_task_result)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._task_list.setItemWidget(item, row)

        # 空态说明不塞进列表：列表只留「添加任务」一行，说明占满剩余高度并垂直居中。
        self._task_empty.setVisible(not self._tasks)
        compact_height = add_row.sizeHint().height() + 8
        self._task_list.setMinimumHeight(compact_height if not self._tasks else 200)
        self._task_list.setMaximumHeight(compact_height if not self._tasks else 16777215)
        self._task_list.sync_row_widths()
        target = next((index + 1 for index, task in enumerate(self._tasks) if task.id == selected_id), -1)
        if target < 0 and self._tasks:
            target = 1
        self._task_list.setCurrentRow(target)
        self._sync_task_actions()
        if expanded_id:
            for index in range(self._task_list.count()):
                row = self._task_list.itemWidget(self._task_list.item(index))
                if isinstance(row, _ScanTaskRow) and row.task.id == expanded_id:
                    self.expand_row("tasks", row)
                    break
        if update_status:
            self.set_page_status("scan", self._summary_text())

    def render_row_detail(self, row: ItemRow) -> None:
        """展开任务行时刷新详情内容。"""
        if isinstance(row, _ScanTaskRow):
            self._task_detail.render(row.task)

    def _dialog_dir(self, field: str) -> str:
        """文件 / 目录对话框的上次选择目录（共用 PathHistory）。"""
        return self._paths.start(field)

    def _remember_dialog_dir(self, field: str, chosen: str | Path) -> None:
        """记住本次选择所在目录并立即落盘。"""
        self._paths.remember(field, chosen)
        self._persist_settings(self.geometry_text())

    def _default_save_name(self, field: str, filename: str) -> str:
        """保存对话框的默认路径：上次导出目录 + 默认文件名。"""
        return self._paths.default_name(field, filename)

    def _add_task(self) -> None:
        """新增任务：先选目录，再生成唯一 id 与默认名称。"""
        chosen = QFileDialog.getExistingDirectory(self, "添加扫描任务", self._dialog_dir("last_task_dir"))
        if not chosen:
            return
        self._remember_dialog_dir("last_task_dir", chosen)
        normalized = str(Path(chosen).expanduser())
        if any(item.path == normalized for item in self._tasks):
            self.set_page_status("scan", f"目录已在任务列表中：{normalized}")
            return
        task = fw.new_scan_task(normalized, existing=self._tasks)
        self._tasks.append(task)
        self._refresh_task_rows()
        for index, item in enumerate(self._tasks):
            if item.id == task.id:
                self._task_list.setCurrentRow(index + 1)  # 首项是「添加任务」
                break
        self._persist_settings(self.geometry_text())
        self.set_page_status("scan", f"已添加扫描任务「{task.display_name}」")

    def _selected_task(self) -> fw.ScanTask | None:
        """当前选中的任务（选中「添加任务」行或空列表时返回 ``None``）。"""
        item = self._task_list.currentItem()
        row = self._task_list.itemWidget(item) if item is not None else None
        return row.task if isinstance(row, _ScanTaskRow) else None

    def _sync_task_actions(self) -> None:
        """顶部移除 / 清空按钮按选中项与扫描忙态启用。"""
        has_selection = self._selected_task() is not None
        self._remove_task_button.setEnabled(has_selection and not self._worker_running)
        self._clear_tasks_button.setEnabled(bool(self._tasks) and not self._worker_running)

    def _on_task_selected(self, current: int) -> None:
        self._task_list.sync_selection(current)
        self._sync_task_actions()

    def _remove_selected_task(self) -> None:
        task = self._selected_task()
        if task is not None:
            self._remove_task(task.id)

    def _clear_tasks(self) -> None:
        """清空全部任务：只清列表与已保存的结果，不删除磁盘目录。"""
        if not self._tasks or self._worker_running:
            return
        self._tasks.clear()
        self._results.clear()
        self._persist_results()
        self._clear_result_view("任务已清空")
        self._refresh_task_rows()
        self._persist_settings(self.geometry_text())
        self.set_page_status("scan", "已清空全部扫描任务")

    def _remove_task(self, task_id: str) -> None:
        """移除任务：只清列表与已保存的结果，不删除磁盘目录。"""
        task = self._task_by_id(task_id)
        if task is None or self._worker_running:
            return
        self._tasks = [item for item in self._tasks if item.id != task_id]
        self._results.pop(task_id, None)
        self._persist_results()
        if self._viewing_task_id == task_id:
            self._clear_result_view("任务已移除")
        self._refresh_task_rows()
        self._persist_settings(self.geometry_text())
        self.set_page_status("scan", f"已移除扫描任务「{task.display_name}」")

    def _on_task_double_clicked(self, item: QListWidgetItem) -> None:
        row = self._task_list.itemWidget(item)
        if not isinstance(row, _ScanTaskRow):
            return
        if row.task.id in self._results:
            self._show_task_result(row.task.id)
        else:
            self._start_task_scan(row.task.id)

    def _refresh_options_tags(self) -> None:
        """扫描页动作卡里的只读标签：语义色区分开关状态，数值 / 白名单用强调色。"""
        recursive = self._recursive.isChecked()
        cache = self._use_cache.isChecked()
        depth = "不限" if self._max_depth.value() <= 0 else str(self._max_depth.value())
        self._option_tags["recursive"].set_status(
            "递归" if recursive else "不递归", "success" if recursive else "muted"
        )
        self._option_tags["depth"].set_status(f"深度 {depth}", "muted")
        self._option_tags["workers"].set_status(f"并发 {self._workers.value()}", "muted")
        self._option_tags["whitelist"].set_status(f"白名单 {len(self._whitelist_exts())} 个后缀", "accent")
        self._option_tags["cache"].set_status(f"缓存{'开' if cache else '关'}", "success" if cache else "muted")

    def _sync_ruleset(self, ruleset: RuleSet, *, message: str) -> None:
        self.ruleset = ruleset
        self._refresh_rules()
        self.set_page_status("rules", message)

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
        self.set_page_status("settings", "后缀白名单已恢复默认分组")

    def _delete_whitelist_row(self) -> None:
        rows = self._whitelist_rows()
        selected = {index.row() for index in self._whitelist_table.selectedIndexes()}
        targets = [rows[index] for index in sorted(selected) if 0 <= index < len(rows)]
        if not targets:
            self.set_page_status("settings", "请先在白名单表格中选择要删除的类型（可按住 Ctrl / Shift 多选）")
            return
        if len(rows) - len(targets) < 1:
            self.set_page_status("settings", "白名单至少保留一个类型，请先新增其他类型")
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
            self.set_page_status("settings", f"已删除类型「{targets[0][0]}」")
        else:
            self.set_page_status("settings", f"已删除 {len(targets)} 个类型（共 {total_exts} 个后缀）")

    def _edit_whitelist_row(self) -> None:
        row = self._whitelist_table.currentRow()
        if row < 0:
            self.set_page_status("settings", "请先在白名单表格中选择一行")
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
        self.set_page_status(
            "settings", f"已保存类型「{new_label}」：{len(valid)} 个后缀，共 {len(self._whitelist_exts())} 个"
        )

    # ------------------------------------------------------------------ #
    # 规则
    # ------------------------------------------------------------------ #
    def _add_rule(self) -> None:
        self._rule_dialog(None)

    def _edit_rule(self) -> None:
        rule_id = self._selected_rule_id()
        if rule_id is None:
            self.set_page_status("rules", "请先在规则表中选择一条规则")
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
            validate_ruleset(new_set)
        except ValueError as exc:
            QMessageBox.critical(self, "规则无效", str(exc))
            return
        self._sync_ruleset(new_set, message=f"已保存规则 {candidate.id}")

    def _delete_rule(self) -> None:
        rule_id = self._selected_rule_id()
        if rule_id is None:
            self.set_page_status("rules", "请先在规则表中选择一条规则")
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
            self.set_page_status("rules", "请先在规则表中选择规则")
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

    def _load_scenario(self) -> None:
        """列出随包发布的场景模板，选中后整体替换当前规则集。"""
        try:
            templates = scenario_templates()
        except ValueError as exc:
            QMessageBox.critical(self, "场景模板不可用", str(exc))
            return
        labels = [f"{item.name}（{item.slug}，{item.rule_count} 条）" for item in templates]
        chosen, ok = QInputDialog.getItem(self, "载入场景规则", "选择场景模板：", labels, 0, False)
        if not ok or not chosen:
            return
        template = templates[labels.index(chosen)]
        if self.ruleset.rules and not confirm(self, "载入场景规则", f"当前规则将被「{template.name}」覆盖，继续？"):
            return
        try:
            loaded = load_scenario(template.slug)
        except ValueError as exc:
            QMessageBox.critical(self, "载入失败", str(exc))
            return
        self._sync_ruleset(loaded, message=f"已载入场景：{template.name}")

    def _clear_rules(self) -> None:
        if not confirm(self, "清空规则", "确定清空全部规则与组合式？"):
            return
        self._sync_ruleset(clear_rules(self.ruleset), message="规则与组合式已清空")

    def _import_rules(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "导入规则集",
            self._dialog_dir("last_import_dir"),
            "JSON (*.json);;全部文件 (*.*)",
        )
        if not chosen:
            return
        self._remember_dialog_dir("last_import_dir", chosen)
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
            self._default_save_name("last_export_dir", "filescan-rules.json"),
            "JSON (*.json)",
        )
        if not chosen:
            return
        self._remember_dialog_dir("last_export_dir", chosen)
        try:
            save_ruleset(self.ruleset, Path(chosen))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.set_page_status("rules", f"规则集已导出到 {chosen}")

    # ------------------------------------------------------------------ #
    # 组合式
    # ------------------------------------------------------------------ #
    def _selected_expression_id(self) -> str | None:
        row = self._expression_table.currentRow()
        if row < 0:
            return None
        item = self._expression_table.item(row, 1)
        return item.text() if item is not None and item.text() else None

    def _selected_expression_ids(self) -> set[str]:
        ids: set[str] = set()
        for index in self._expression_table.selectionModel().selectedRows():
            item = self._expression_table.item(index.row(), 1)
            if item is not None and item.text():
                ids.add(item.text())
        return ids

    def _add_expression(self) -> None:
        self._expression_dialog(None)

    def _edit_expression(self) -> None:
        expression_id = self._selected_expression_id()
        if expression_id is None:
            self.set_page_status("rules", "请先在组合式表中选择一条组合式")
            return
        item = self.ruleset.find_expression(expression_id)
        if item is not None:
            self._expression_dialog(item)

    def _expression_dialog(self, expression: Expression | None) -> None:
        dialog = _ExpressionDialog(self, self.ruleset, expression)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        candidate = dialog.value()
        if candidate is None:
            return
        try:
            validate_expression(self.ruleset, candidate)
        except ValueError as exc:
            QMessageBox.critical(self, "组合式无效", str(exc))
            return
        self._sync_ruleset(self.ruleset.replace_expression(candidate), message=f"已保存组合式 {candidate.id}")

    def _delete_expression(self) -> None:
        expression_id = self._selected_expression_id()
        if expression_id is None:
            self.set_page_status("rules", "请先在组合式表中选择一条组合式")
            return
        item = self.ruleset.find_expression(expression_id)
        name = item.name if item is not None else expression_id
        if not confirm(self, "删除组合式", f"确定删除组合式 {expression_id}（{name}）？"):
            return
        try:
            new_set = self.ruleset.remove_expression(expression_id)
        except ValueError as exc:
            QMessageBox.critical(self, "无法删除", str(exc))
            return
        self._sync_ruleset(new_set, message=f"已删除组合式 {expression_id}")

    def _set_expressions_enabled(self, enabled: bool) -> None:
        selected = self._selected_expression_ids()
        if not selected:
            self.set_page_status("rules", "请先在组合式表中选择组合式")
            return
        expressions = tuple(
            replace(item, enabled=enabled) if item.id in selected else item for item in self.ruleset.expressions
        )
        self._sync_ruleset(
            replace(self.ruleset, expressions=expressions),
            message=f"已{'启用' if enabled else '禁用'} {len(selected)} 条组合式",
        )

    # ------------------------------------------------------------------ #
    # 扫描
    # ------------------------------------------------------------------ #
    def _max_file_bytes_value(self) -> int:
        """界面上的 MB → 引擎使用的字节数（至少 1 字节）。"""
        return max(1, round(self._max_file_bytes.value() * 1024 * 1024))

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
            skip_dirs=parse_skip_dirs_text(_list_text(self._skip_dirs.toPlainText())),
            skip_hidden=self._skip_hidden.isChecked(),
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
            normalize_text=self._normalize_text.isChecked(),
            max_file_bytes=self._max_file_bytes_value(),
            workers=max(1, self._workers.value()),
            context_chars=max(0, self._context_chars.value()),
        )

    def _start_task_scan(self, task_id: str, *, mode: str = "default") -> None:
        """从任务行启动扫描；规则 / 选项仍用设置页里的全局配置。

        ``mode`` 决定缓存口径：``default`` 跟随设置页的「使用缓存」开关，
        ``incremental`` 强制只重扫变动的文件（复用缓存），``full`` 忽略缓存复用、
        逐个文件重新读盘（本次结果仍回写缓存，供后续增量扫描使用）。
        """
        if self._worker_running:
            return
        task = self._task_by_id(task_id)
        if task is None:
            return
        if not task.path:
            QMessageBox.warning(self, "缺少目录", "请先给该任务指定扫描目录")
            return
        try:
            expression = resolve_expression(self.ruleset)
        except ValueError as exc:
            QMessageBox.critical(self, "规则表达式无效", str(exc))
            return
        # 启用只决定默认 OR；显式表达式里引用的未启用规则同样会参与匹配。
        if not referenced_rule_ids(expression):
            QMessageBox.warning(self, "没有可用规则", "请先启用规则，或在表达式里引用规则 id")
            return
        try:
            options = self._scan_options()
        except ValueError as exc:
            QMessageBox.critical(self, "扫描选项无效", str(exc))
            return
        self._scanning_task_id = task_id
        self._stopping_task_id = ""
        # 重新扫描先丢掉磁盘上的旧结果：扫描中途退出时不会恢复上一次的过期数据。
        self._results.pop(task_id, None)
        self._persist_results()
        if self._viewing_task_id == task_id:
            self._clear_result_view("该任务正在重新扫描…")
        self._token = CancelToken()
        self._bridge = _ScanProgress(self)
        self._bridge.progress.connect(self._on_progress)
        self._worker_running = True
        self._scan_meter.reset()
        self._refresh_task_rows(update_status=False)
        use_cache = self._use_cache.isChecked()
        refresh = False
        action = "扫描"
        if mode == "incremental":
            # 增量扫描就是要复用未变动文件的结果，不受「使用缓存」开关影响。
            use_cache = True
            action = "增量扫描"
        elif mode == "full":
            refresh = True
            action = "全量重扫"
        self.set_page_status("scan", f"正在{action}「{task.display_name}」…")
        self.show_progress(f"{action}准备中…", 0, None)
        submit(
            _scan_worker,
            [Path(task.path)],
            self.ruleset,
            options,
            self._token,
            self._bridge,
            use_cache=use_cache,
            mask=not self._export_raw.isChecked(),
            refresh=refresh,
            on_done=self._scan_done,
            on_error=self._scan_error,
            on_finished=self._scan_finished,
        )

    def _on_progress(self, done: int, total: int, path: str) -> None:
        """左侧状态栏写当前任务 / 文件，右侧进度条按文件数给百分比 / 速度 / 剩余。"""
        task = self._task_by_id(self._scanning_task_id)
        label = task.display_name if task is not None else "扫描"
        rate = self._scan_meter.update(done)
        self.set_page_status("scan", f"{label} · 正在扫描 {done}/{total}：{path}")
        row = self._task_row(self._scanning_task_id)
        if row is not None:
            row.set_progress(done, total)
        self.show_progress(fw.scan_progress_text(done, total, rate), done, total or None)

    def _persist_results(self) -> None:
        """把每个任务最近一次结果写回结果文件（退出应用后结果页仍能恢复）。"""
        fw.save_results(self._results, self._results_file)

    def _restore_results_view(self) -> None:
        """启动时恢复最近一次结果：默认展示第一个有结果的任务。"""
        task = next((item for item in self._tasks if item.id in self._results), None)
        if task is None:
            return
        self._viewing_task_id = task.id
        self.result = self._results[task.id]
        self._render_result(self.result)
        self._sync_result_switcher()
        self.set_page_status("results", f"{task.display_name}：{fw.scan_status_text(self.result)}")

    def _scan_done(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        result, cache_note = payload
        if not isinstance(result, ScanResult):
            return
        task_id = self._scanning_task_id
        task = self._task_by_id(task_id)
        if task is None:
            return
        message = fw.scan_status_text(result)
        summary = f"缓存降级：{cache_note}｜{message}" if cache_note else message
        # 任务行 / 详情只留精简摘要；完整口径仍写状态栏与结果页。
        self._set_task(
            replace(
                task,
                last_scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                last_summary=fw.scan_task_summary(result),
                last_status="cancelled" if result.stats.cancelled else "ok",
            )
        )
        self._results[task_id] = result
        self._persist_results()
        self._viewing_task_id = task_id
        self.result = result
        self._render_result(result)
        self._sync_result_switcher()
        # 扫描页保留该任务本次结论，结果页显示同一份摘要。
        self.set_page_status("scan", f"{task.display_name}：{summary}")
        self.set_page_status("results", f"{task.display_name}：{summary}")
        self._persist_settings(self.geometry_text())
        # 扫描完成自动切到结果页（关联的就是刚扫描的任务）。
        self.show_page("results")

    def _scan_error(self, message: str) -> None:
        task_id = self._scanning_task_id
        task = self._task_by_id(task_id)
        if task is not None:
            self._set_task(
                replace(
                    task,
                    last_scanned_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    last_summary="扫描失败",
                    last_status="failed",
                )
            )
        self._results.pop(task_id, None)
        self._persist_results()
        if self._viewing_task_id == task_id:
            self._clear_result_view(f"{task.display_name if task else '扫描'}失败：{message}")
        self._persist_settings(self.geometry_text())
        self.set_page_status("scan", f"扫描失败：{message}")

    def _scan_finished(self) -> None:
        self._worker_running = False
        self._scanning_task_id = ""
        self._stopping_task_id = ""
        self._refresh_task_rows(update_status=False)
        self.show_progress("", 0, None)

    def _cancel_task_scan(self, task_id: str) -> None:
        """停止当前任务的扫描；结果按取消状态写回任务摘要。"""
        if not self._worker_running or task_id != self._scanning_task_id:
            return
        if self._token is None:
            return
        self._token.cancel()
        self._stopping_task_id = task_id
        self._refresh_task_rows(update_status=False)
        task = self._task_by_id(task_id)
        label = task.display_name if task is not None else "扫描"
        self.set_page_status("scan", f"正在停止「{label}」，等待进行中的文件结束…")

    def _show_task_result(self, task_id: str) -> None:
        """从任务行进入结果页：展示该任务最近一次扫描结果（含上次运行落盘的）。"""
        result = self._results.get(task_id)
        task = self._task_by_id(task_id)
        if result is None or task is None:
            self.set_page_status("scan", "该任务还没有可用结果，请先扫描")
            return
        self._viewing_task_id = task_id
        self.result = result
        self._render_result(result)
        self._sync_result_switcher()
        self.set_page_status("results", f"{task.display_name}：{fw.scan_status_text(result)}")
        self.show_page("results")

    def _clear_result_view(self, message: str) -> None:
        """清掉结果页与任务的关联（目录改变 / 移除 / 重新扫描时）。"""
        self.result = None
        self._rendered_result = None
        self._viewing_task_id = ""
        self._tree.clear()
        self._sync_result_switcher()
        self.set_page_status("results", message)

    def _render_result(self, result: ScanResult) -> None:
        self._rendered_result = result
        self._tree.clear()
        self._context_delegate.reset()
        for index, hit in enumerate(result.hits):
            highest = _highest_severity(hit.matches)
            parent = QTreeWidgetItem(
                [
                    str(hit.path),
                    severity_label(highest) if highest else "",
                    "",
                    _hit_summary(hit),
                ]
            )
            parent.setToolTip(0, str(hit.path))
            parent.setToolTip(
                3, f"命中 {len(hit.matches)} 条 · 引擎 {engine_label(hit.engine)} · 耗时 {hit.elapsed * 1000:.2f} ms"
            )
            if highest:
                parent.setToolTip(1, f"该文件最高严重级：{severity_label(highest)}")
            parent.setData(0, _PATH_ROLE, str(hit.path))
            parent.setData(0, _MATCHES_ROLE, tuple(hit.matches))
            self._tree.addTopLevelItem(parent)
            for match in hit.matches:
                rendered = render_context(match, False)
                child = QTreeWidgetItem(
                    [
                        match.rule_name,
                        severity_label(match.severity) if match.severity else "",
                        str(match.location or match.line_no),
                        rendered,
                    ]
                )
                child.setData(0, _PATH_ROLE, str(hit.path))
                child.setData(0, _MATCH_ROLE, match)
                # 单元格文本仍是完整打码上下文（复制 / 悬停 / 测试可读）；
                # 富文本绘制由 delegate 按三段重新排版，命中段另外上底色。
                child.setData(3, _CONTEXT_ROLE, context_segments(match, False))
                child.setToolTip(0, str(hit.path))
                if match.severity:
                    child.setToolTip(1, f"严重级：{severity_label(match.severity)}")
                child.setToolTip(3, rendered)
                color = _severity_color(match.severity)
                if color is not None:
                    brush = QBrush(color)
                    child.setForeground(0, brush)
                    child.setForeground(1, brush)
                parent.addChild(child)
            parent.setExpanded(True)
            if index >= 5000:  # 极端结果集下不再逐项展开，避免长时间占用 GUI 线程
                break
        for item in result.skipped:
            if not self._show_skipped.isChecked():
                break
            entry = QTreeWidgetItem([str(item.path), "", "", f"跳过：{item.reason}"])
            entry.setToolTip(0, str(item.path))
            entry.setData(0, _PATH_ROLE, str(item.path))
            self._tree.addTopLevelItem(entry)
        for item in result.errors:
            entry = QTreeWidgetItem([str(item.path), "", "", f"错误：{item.message}"])
            entry.setToolTip(0, str(item.path))
            entry.setData(0, _PATH_ROLE, str(item.path))
            self._tree.addTopLevelItem(entry)
        self._update_result_actions()

    def _context_layout_changed(self, *_args: object) -> None:
        """列宽变化后重排上下文列，并记住前三列宽度供下次启动恢复。"""
        self._ui.remember_sizes("result_columns", [self._tree.columnWidth(index) for index in range(3)])
        self._context_delegate.reset()
        self._tree.scheduleDelayedItemsLayout()

    # ------------------------------------------------------------------ #
    # 结果行操作：定位文件 / 复制命中
    # ------------------------------------------------------------------ #
    def _item_path(self, item: QTreeWidgetItem | None) -> Path | None:
        """行绑定的文件路径（父节点 / 命中行 / 跳过与错误行都有）。"""
        if item is None:
            return None
        stored = item.data(0, _PATH_ROLE)
        return Path(stored) if isinstance(stored, str) and stored else None

    def _item_matches(self, item: QTreeWidgetItem | None) -> list[RuleMatch]:
        """行绑定的命中：命中行一条，父节点 / 文件行是该文件的全部命中。"""
        if item is None:
            return []
        match = item.data(0, _MATCH_ROLE)
        if isinstance(match, RuleMatch):
            return [match]
        stored = item.data(0, _MATCHES_ROLE)
        if isinstance(stored, (tuple, list)):
            return [entry for entry in stored if isinstance(entry, RuleMatch)]
        return []

    def _selected_matches(self) -> list[RuleMatch]:
        """选中的命中（父节点自动展开成该文件的全部命中，按签名去重）。"""
        matches: list[RuleMatch] = []
        seen: set[tuple[str, str, int, int]] = set()
        for item in self._tree.selectedItems():
            path = str(self._item_path(item) or "")
            for match in self._item_matches(item):
                key = (path, match.rule_id, match.line_no, match.start)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
        return matches

    def _selected_paths(self) -> list[Path]:
        """选中的文件路径（去重，保持选择顺序）。"""
        paths: list[Path] = []
        seen: set[str] = set()
        for item in self._tree.selectedItems():
            path = self._item_path(item)
            if path is None:
                continue
            key = str(path).casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
        return paths

    def _update_result_actions(self) -> None:
        """结果动作按钮跟随选择启用：没有选中行时置灰。"""
        selected = bool(self._tree.selectedItems())
        for widget in (
            self._locate_button,
            self._copy_match_button,
            self._copy_context_button,
            self._copy_note_button,
        ):
            widget.setEnabled(selected)

    def _copy_to_clipboard(self, text: str, message: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.set_page_status("results", message)

    def _copy_selected_matches(self, *, context: bool = False) -> None:
        """复制选中命中：默认打码，勾选「导出 / 复制原文」后复制原文。"""
        matches = self._selected_matches()
        if not matches:
            self.set_page_status("results", "请先在结果树选择命中行（父节点会包含该文件的全部命中）")
            return
        raw = self._export_raw.isChecked()
        if context:
            lines = [render_context(match, include_raw=raw) for match in matches]
            label = "上下文"
        else:
            lines = [match.excerpt if raw or not match.sensitive else mask_text(match.excerpt) for match in matches]
            label = "命中内容"
        self._copy_to_clipboard(
            "\n".join(lines),
            f"已复制 {len(matches)} 条{label}（{'原文' if raw else '已打码'}）",
        )

    def _copy_selected_notes(self) -> None:
        """复制扫描说明：每条命中生成「【规则：…，匹配项：…】」，默认打码。"""
        matches = self._selected_matches()
        if not matches:
            self.set_page_status("results", "请先在结果树选择命中行（父节点会包含该文件的全部命中）")
            return
        raw = self._export_raw.isChecked()
        lines = [match_note(match, include_raw=raw) for match in matches]
        self._copy_to_clipboard(
            "\n".join(lines),
            f"已复制 {len(matches)} 条扫描说明（{'原文' if raw else '已打码'}）",
        )

    def _copy_selected_paths(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self.set_page_status("results", "请先在结果树选择一行")
            return
        self._copy_to_clipboard("\n".join(str(path) for path in paths), f"已复制 {len(paths)} 个文件路径")

    def _reveal_file(self, path: Path) -> None:
        """在系统文件管理器中定位文件（文件已不存在时退回父目录）。"""
        target = path if path.exists() else path.parent
        if not target.exists():
            self.set_page_status("results", f"定位失败：路径不存在 {path}")
            return
        try:
            subprocess.Popen(reveal_command(target), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.set_page_status("results", f"定位失败：{exc}")
            return
        self.set_page_status("results", f"已在文件管理器中定位：{target}")

    def _locate_selected_file(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self.set_page_status("results", "请先在结果树选择一行")
            return
        # 只定位第一个：批量选中时弹出多个文件管理器窗口反而难用。
        self._reveal_file(paths[0])

    def _open_file(self, path: Path) -> None:
        """用系统默认程序打开文件（只在本机打开，不改变打码口径）。"""
        if not path.is_file():
            self.set_page_status("results", f"打开失败：文件不存在 {path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.set_page_status("results", f"已用默认程序打开：{path}")

    def _open_result_menu(self, pos: QPoint) -> None:
        """结果树右键菜单：定位 / 打开文件与复制命中、上下文、路径。"""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self._tree.setCurrentItem(item)
        path = self._item_path(item)
        raw = self._export_raw.isChecked()
        menu = QMenu(self)
        locate = menu.addAction("在文件夹中定位")
        locate.triggered.connect(self._locate_selected_file)
        if path is not None:
            open_file = menu.addAction("打开文件")
            open_file.triggered.connect(lambda _checked=False, target=path: self._open_file(target))
        menu.addSeparator()
        copy_match = menu.addAction(f"复制命中内容（{'原文' if raw else '已打码'}）")
        copy_match.setEnabled(bool(self._item_matches(item)))
        copy_match.triggered.connect(self._copy_selected_matches)
        copy_context = menu.addAction("复制上下文")
        copy_context.setEnabled(bool(self._item_matches(item)))
        copy_context.triggered.connect(lambda: self._copy_selected_matches(context=True))
        copy_note = menu.addAction("复制扫描说明")
        copy_note.setEnabled(bool(self._item_matches(item)))
        copy_note.triggered.connect(self._copy_selected_notes)
        copy_path = menu.addAction("复制文件路径")
        copy_path.setEnabled(path is not None)
        copy_path.triggered.connect(self._copy_selected_paths)
        menu.addSeparator()
        allow = menu.addAction("加入路径排除白名单")
        allow.setEnabled(path is not None)
        allow.triggered.connect(self._allowlist_selected)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _allowlist_selected(self) -> None:
        """把选中行路径加入排除白名单（新增禁用路径规则并追加到启用组合式）。"""
        paths = self._selected_paths()
        if not paths:
            self.set_page_status("results", "请先在结果树选择一行")
            return
        ruleset = self.ruleset
        for path in paths:
            ruleset = add_path_allowlist(ruleset, str(path))
        self._sync_ruleset(ruleset, message=f"已将 {len(paths)} 条路径加入排除白名单（下次扫描生效）")
        self.set_page_status("results", f"已将 {len(paths)} 条路径加入排除白名单（下次扫描生效）")

    def _result_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """双击行直接定位该行文件。"""
        path = self._item_path(item)
        if path is not None:
            self._reveal_file(path)

    def _sync_result_switcher(self) -> None:
        """更新任务切换按钮：只列本次会话已有结果的任务。"""
        available = [task for task in self._tasks if task.id in self._results]
        current = self._task_by_id(self._viewing_task_id)
        if not available:
            self._result_task_button.setText("暂无任务结果  ▾")
            self._result_task_button.setEnabled(False)
        elif current is not None:
            self._result_task_button.setText(f"任务：{current.display_name}  ▾")
            self._result_task_button.setEnabled(True)
        else:
            self._result_task_button.setText("选择任务结果  ▾")
            self._result_task_button.setEnabled(True)

    def _result_task_menu(self) -> QMenu:
        """构造任务结果菜单；每个动作直接切到对应任务的结果。"""
        menu = QMenu(self)
        for task in self._tasks:
            if task.id not in self._results:
                continue
            action = menu.addAction(task.display_name)
            action.setCheckable(True)
            action.setChecked(task.id == self._viewing_task_id)
            action.triggered.connect(lambda _checked=False, task_id=task.id: self._show_task_result(task_id))
        return menu

    def _open_result_task_menu(self) -> None:
        """弹出任务结果菜单；菜单关闭后再渲染，不会把下拉列表卡在展开态。"""
        if not self._result_task_button.isEnabled():
            return
        menu = self._result_task_menu()
        menu.exec(self._result_task_button.mapToGlobal(QPoint(0, self._result_task_button.height())))

    def _result_prefix(self) -> str:
        """结果页文案前缀：当前结果属于哪个任务（没有关联时为空串）。"""
        task = self._task_by_id(self._viewing_task_id)
        return f"{task.display_name}：" if task is not None else ""

    def _apply_filter(self) -> None:
        if self.result is None:
            self.set_page_status("results", "还没有扫描结果")
            return
        selected = self._filter_rule.currentText()
        rule_ids = None if selected in ("", "全部") else [selected]
        level_index = self._filter_severity.currentIndex()
        severities = None if level_index <= 0 else [SEVERITY_LEVELS[level_index - 1]]
        filtered = filter_result(
            self.result,
            rule_ids=rule_ids,
            path_contains=self._filter_path.text(),
            severities=severities,
        )
        self._render_result(filtered)
        self.set_page_status(
            "results",
            f"{self._result_prefix()}筛选后命中 {filtered.stats.hit_files} 个文件 / {filtered.stats.hit_count} 条",
        )

    def _reset_filter(self) -> None:
        self._filter_rule.setCurrentIndex(0)
        self._filter_severity.setCurrentIndex(0)
        self._filter_path.clear()
        if self.result is not None:
            self._render_result(self.result)
            self.set_page_status("results", f"{self._result_prefix()}{fw.scan_status_text(self.result)}")

    def _export_report(self) -> None:
        if self.result is None:
            QMessageBox.information(self, "没有结果", "请先完成一次扫描")
            return
        task = self._task_by_id(self._viewing_task_id)
        stem = task.display_name if task is not None else "filescan-report"
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "导出扫描报告",
            self._default_save_name("last_export_dir", f"{stem}.csv"),
            "CSV (*.csv);;JSON (*.json);;SARIF (*.sarif);;文本 (*.txt)",
        )
        if not chosen:
            return
        self._remember_dialog_dir("last_export_dir", chosen)
        suffix = Path(chosen).suffix.lower()
        fmt = {".json": "json", ".txt": "text", ".log": "text", ".sarif": "sarif"}.get(suffix, "csv")
        include_raw = self._export_raw.isChecked()
        try:
            write_report(self.result, Path(chosen), fmt, include_raw=include_raw)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.set_page_status("results", f"报告已导出到 {chosen}（{fmt}，{'含原文' if include_raw else '已打码'}）")

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
        self.set_page_status("settings", f"扫描缓存已清理（{removed} 条）")

    def _import_config(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "导入扫描配置",
            self._dialog_dir("last_import_dir"),
            "JSON (*.json);;全部文件 (*.*)",
        )
        if not chosen:
            return
        self._remember_dialog_dir("last_import_dir", chosen)
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
        self.set_page_status(
            "settings", f"已导入配置：{chosen}（任务 {len(self._tasks)} 个，规则 {len(self.ruleset.rules)} 条）"
        )

    def _apply_config(self, config: ScanConfig) -> None:
        options = config.options
        existing = {item.path: item for item in self._tasks}
        imported: list[fw.ScanTask] = []
        for path in config.roots:
            text = str(path)
            imported.append(existing.get(text) or fw.new_scan_task(text, existing=imported))
        self._tasks = imported
        self._results.clear()
        self._clear_result_view("配置已导入，请重新扫描")
        self._refresh_task_rows()
        self._recursive.setChecked(options.recursive)
        self._follow.setChecked(options.follow_symlinks)
        self._skip_binary.setChecked(options.skip_binary)
        self._scan_documents.setChecked(options.scan_documents)
        self._normalize_text.setChecked(options.normalize_text)
        self._max_depth.setValue(options.max_depth)
        self._min_size.setValue(options.min_size)
        self._max_size.setValue(options.max_size or 0)
        self._max_file_bytes.setValue(options.max_file_bytes / (1024 * 1024))
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
            self._default_save_name("last_export_dir", "filescan-config.json"),
            "JSON (*.json)",
        )
        if not chosen:
            return
        self._remember_dialog_dir("last_export_dir", chosen)
        try:
            options = self._scan_options()
        except ValueError as exc:
            QMessageBox.critical(self, "扫描选项无效", str(exc))
            return
        roots = tuple(Path(item.path) for item in self._tasks if item.path)
        config = ScanConfig(
            roots=roots,
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
        self.set_page_status("settings", f"扫描配置已导出到 {chosen}（含规则集与扫描选项）")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _set_compact_mode(self, compact: bool) -> None:
        """结果页「紧凑模式」开关：同步全部列表并落盘。"""
        self._apply_compact_mode(compact)
        self._persist_settings(self.geometry_text())

    def _set_show_skipped(self, show: bool) -> None:
        """默认隐藏跳过 / 范围外条目；勾选后就地重渲染当前结果（保留筛选后的集合）。"""
        self.settings = replace(self.settings, show_skipped=show)
        if self._rendered_result is not None:
            self._render_result(self._rendered_result)
        self._persist_settings(self.geometry_text())

    def _apply_compact_mode(
        self,
        compact: bool,
        *,
        rebuild_rows: bool = True,
    ) -> None:
        """把紧凑密度应用到任务行、结果树与三张表；默认只改展示，不影响扫描口径。"""
        self.settings = replace(self.settings, compact_mode=compact)
        checkbox = getattr(self, "_compact_mode", None)
        if checkbox is not None and checkbox.isChecked() != compact:
            checkbox.blockSignals(True)
            checkbox.setChecked(compact)
            checkbox.blockSignals(False)
        delegate = getattr(self, "_context_delegate", None)
        if delegate is not None:
            delegate.set_compact(compact)
        for name in ("_whitelist_table", "_rules_table", "_expression_table"):
            table = getattr(self, name, None)
            if table is not None:
                table.verticalHeader().setDefaultSectionSize(24 if compact else 30)
        task_list = getattr(self, "_task_list", None)
        if task_list is not None:
            task_list.setSpacing(2 if compact else 4)
            if rebuild_rows:
                self._refresh_task_rows(update_status=False)
        tree = getattr(self, "_tree", None)
        if tree is not None:
            tree.scheduleDelayedItemsLayout()

    def _current_settings(self) -> fw.FilescanSettings:
        return replace(
            self.settings,
            paths=tuple(item.path for item in self._tasks if item.path),
            tasks=tuple(self._tasks),
            recursive=self._recursive.isChecked(),
            max_depth=max(0, self._max_depth.value()),
            follow_symlinks=self._follow.isChecked(),
            skip_dirs=_list_text(self._skip_dirs.toPlainText()),
            skip_hidden=self._skip_hidden.isChecked(),
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
            normalize_text=self._normalize_text.isChecked(),
            max_file_bytes=self._max_file_bytes_value(),
            workers=max(1, self._workers.value()),
            context_chars=max(0, self._context_chars.value()),
            use_cache=self._use_cache.isChecked(),
            ruleset=self.ruleset,
            filter_rule="" if self._filter_rule.currentText() == "全部" else self._filter_rule.currentText(),
            filter_path=self._filter_path.text(),
            compact_mode=self.settings.compact_mode,
            show_skipped=self.settings.show_skipped,
            last_task_dir=self._paths.raw("last_task_dir"),
            last_import_dir=self._paths.raw("last_import_dir"),
            last_export_dir=self._paths.raw("last_export_dir"),
            active_page=self._ui.choice("active_page", tuple(spec.key for spec in PAGES), "scan"),
            result_columns=tuple(self._ui.sizes("result_columns", count=3, default=(300, 56, 48))),
        )

    def _persist_settings(self, geometry: str) -> None:
        fw.save_settings(replace(self._current_settings(), geometry=geometry), self._own_settings_path)

    def _persist_sidebar_collapsed(self, collapsed: bool) -> None:
        """侧边栏折叠状态随窗口设置落盘（与 wslmanw / llmw 同一口径）。"""
        self.settings = replace(self.settings, sidebar_collapsed=collapsed)
        self._persist_settings(self.geometry_text())

    def _on_close(self) -> bool:
        if self._token is not None:
            self._token.cancel()
        if self._worker_running:
            from PySide6.QtCore import QThreadPool

            QThreadPool.globalInstance().waitForDone(3000)
        return True

    def show_page(self, key: str) -> None:
        """切页：结果页自动套用路径筛选；扫描页刷新「当前选项」摘要。"""
        self._ui.set("active_page", key)
        super().show_page(key)
        if key == "scan":
            self._refresh_options_tags()
        elif key == "results" and self.result is not None and self._filter_path.text().strip():
            self._apply_filter()


def _parse_optional_timestamp(text: str) -> datetime | None:
    """把时间输入框转成时间戳；空串返回 ``None``，非法文本抛 ``ValueError``。"""
    value = text.strip()
    return fw.parse_timestamp(value) if value else None


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.app import run_window

    run_window(lambda: FilescanQtWindow(settings_path), app_name="敏感内容扫描")
