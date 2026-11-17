"""locllmw（Qt）模型中心：ModelScope 浏览器 / 转换 / 量化三个子页。

页面口径与迁移前的窗口一致，只是把控件换成 Qt 并把后台任务统一走
:func:`fcmd.gui.qt.workers.submit`：

- **浏览**：ModelScope 在线浏览（适合本机 / 全部 / 对话 / 编程 / 看图 / 推理 / 视频 / 音频筛选 + 排序 / 翻页）、自定义结果行，行内提供下载按钮，展开显示详情元数据，底部右侧显示下载进度；
- **转换**：HF safetensors → GGUF（刷新模型列表、精度 / 解释器 / 脚本 / 引擎 tag、
  输出位置与覆盖开关、计划预览、进度与日志）；
- **量化**：GGUF 量化（输入文件、全量模型下拉、量化器环境、量化类型、输出目录、
  覆盖开关、进度与日志）。

业务逻辑全部复用 ``llmdown`` / ``llmconvert`` / ``llmqnt`` 与
:mod:`fcmd.apps.locllm._locllmw_models`、:mod:`fcmd.apps.locllm._locllmw_convert`
的纯函数；窗口只做编排。下载参数写入 ``locllmw.json`` 的 ``download`` 字段。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import (
    _llmconfig,
    _locllmw_convert,
    _locllmw_models,
    llmconvert,
    llmenv,
    llmqnt,
)
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision, quantize_input_error
from fcmd.apps.locllm.llmbase import ModelEntry, ServeError, human_size
from fcmd.apps.locllm.llmdown import (
    ModelInfo,
    ModelSearch,
    ModelSummary,
    ModelToolError,
    ProgressEvent,
    download_files,
    fetch_model_info,
    filter_files,
    scan_local_models,
    search_models,
    select_source,
)
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    LogView,
    PageScroll,
    RowListWidget,
    button,
    hint_label,
    link_button,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["ModelCenterPage"]

_PAGE_SIZE = _locllmw_models._PAGE_SIZE
_MODEL_FILTERS = _locllmw_models.MODEL_FILTERS
_DEFAULT_FILTER = _locllmw_models.DEFAULT_MODEL_FILTER
_TAB_NAMES = _locllmw_models.MODEL_TAB_NAMES
_DEFAULT_TAB = _locllmw_models.DEFAULT_MODEL_TAB


class _ProgressBridge(QObject):  # pragma: no cover - Qt 信号桥
    """下载进度信号（工作线程 → 主线程）。"""

    progress = Signal(object)


class _ModelItemRow(QFrame):  # pragma: no cover - Qt 控件
    """浏览结果行：与概览推荐行同构（大标题 + 小字副标题 + 右侧关键数值）。

    行本身只放关键内容；展开后在行下方显示详细信息，不再占用
    右侧面板，列表可以铺满整页宽度。
    """

    toggleRequested = Signal(object)
    downloadRequested = Signal(str)

    def __init__(self, item: ModelSummary, parent: QWidget | None = None, *, installed: bool = False) -> None:
        super().__init__(parent)
        title, subtitle, metrics = _locllmw_models.model_item_lines(item)
        self.repo = item.repo
        self._expanded = False
        self._installed = installed
        self.setObjectName("ModelItemRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "false")
        self.setToolTip(item.repo)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 8, 12, 8)
        header_row.setSpacing(12)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(3)
        name = ElidedLabel(title, mode=Qt.TextElideMode.ElideMiddle, parent=header)
        name.setObjectName("ModelItemName")
        name.setToolTip(item.repo)
        left.addWidget(name)
        sub = QLabel(subtitle, header)
        sub.setObjectName("Hint")
        sub.setWordWrap(False)
        sub.setMinimumWidth(0)
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sub.setToolTip(subtitle)
        left.addWidget(sub)
        header_row.addLayout(left, 1)

        stats = QLabel(metrics, header)
        stats.setObjectName("Hint")
        stats.setWordWrap(False)
        stats.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stats.setToolTip(metrics)
        header_row.addWidget(stats, 0)
        self.download_button = button("下载")
        if installed:
            self.download_button.setObjectName("InstalledButton")
            self.download_button.setText("✓ 已安装")
            self.download_button.setEnabled(False)
            self.download_button.setToolTip("该模型已在本地模型目录中")
        else:
            self.download_button.clicked.connect(lambda: self.downloadRequested.emit(self.repo))
        header_row.addWidget(self.download_button, 0)
        self.expand_button = link_button("展开", tooltip="展开 / 收起详细信息")
        self.expand_button.clicked.connect(lambda: self.toggleRequested.emit(self))
        header_row.addWidget(self.expand_button, 0)
        outer.addWidget(header)

        self.detail_host = QWidget(self)
        self.detail_host.setVisible(False)
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(0)
        outer.addWidget(self.detail_host)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """展开 / 收起详情区（行高由页面按新尺寸更新）。"""
        self._expanded = expanded
        self.detail_host.setVisible(expanded)
        self.expand_button.setText("收起" if expanded else "展开")

    def set_selected(self, selected: bool) -> None:
        """切换选中态边框（列表当前行变化时由页面同步）。"""
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_download_state(self, *, busy: bool, active: bool) -> None:
        """已安装行保持绿色勾选态；其余行下载中禁用，当前行显示「下载中…」。"""
        if self._installed:
            self.download_button.setEnabled(False)
            self.download_button.setText("✓ 已安装")
            return
        self.download_button.setEnabled(not busy)
        self.download_button.setText("下载中…" if active else "下载")


class _ModelDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的详情：模型元数据。

    同一个实例在行之间复用，展开哪一行就挂到哪一行下面；不展示 README
    与文件清单，保持列表项展开后的内容紧凑。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        self.error = hint_label("")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        layout.addWidget(QLabel("详细信息", self))
        self.meta_grid = QGridLayout()
        self.meta_grid.setContentsMargins(0, 0, 0, 0)
        self.meta_grid.setHorizontalSpacing(10)
        self.meta_grid.setVerticalSpacing(2)
        self.meta_values: dict[str, QLabel] = {}
        for index, name in enumerate(
            (
                "中文名",
                "组织",
                "任务",
                "框架",
                "架构",
                "模型类型",
                "语言",
                "许可证",
                "下载",
                "星标",
                "大小",
                "版本",
                "更新时间",
                "推理支持",
            )
        ):
            row, column = divmod(index, 2)
            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(hint_label(f"{name}：", wrap=False))
            value = QLabel("-", self)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            cell.addWidget(value, 1)
            self.meta_grid.addLayout(cell, row, column)
            self.meta_values[name] = value
        self.meta_grid.setColumnStretch(0, 1)
        self.meta_grid.setColumnStretch(1, 1)
        layout.addLayout(self.meta_grid)

    def reset(self, repo: str) -> None:
        """切换模型时清空旧内容，等后台详情回填。"""
        self.repo = repo
        self.error.setVisible(False)
        self.error.setText("")
        for value in self.meta_values.values():
            value.setText("-")

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)

    def render(self, info: ModelInfo) -> None:
        """回填仓库元数据。"""
        self.repo = info.summary.repo
        self.error.setVisible(False)
        self.error.setText("")
        for name, value in _locllmw_models.detail_pairs(info):
            if name in self.meta_values:
                self.meta_values[name].setText(value)


class _BrowsePanel(QWidget):  # pragma: no cover - Qt 控件
    """浏览页：筛选条、自定义结果行与行内展开的详情元数据。"""

    downloadFinished = Signal()

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.current_repo: str | None = None
        self.current_info: ModelInfo | None = None
        self._last_result: ModelSearch | None = None
        self._result_items: tuple[ModelSummary, ...] = ()
        self._expanded_row: _ModelItemRow | None = None
        self._page = 1
        self._filter_key = _DEFAULT_FILTER
        self._filter_query = next((query for name, _label, query in _MODEL_FILTERS if name == _DEFAULT_FILTER), "")
        self._detail_generation = 0
        self._searching = False
        self._downloading = False
        self._stop_requested = False
        self._active_repo: str | None = None
        self._bridge: _ProgressBridge | None = None
        self._download_target = _llmconfig.models_dir()
        self._installed_models: dict[str, ModelSummary] = {}
        self._installed_repos: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        browse = QWidget(self)
        browse_layout = QVBoxLayout(browse)
        browse_layout.setContentsMargins(2, 0, 2, 0)
        browse_layout.setSpacing(6)
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, Any] = {}
        for key, label, _query in _MODEL_FILTERS:
            chip = button(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked, key=key: self._on_filter_changed(key))
            self.filter_group.addButton(chip)
            filters.addWidget(chip)
            self.filter_buttons[key] = chip
        filters.addStretch(1)
        browse_layout.addLayout(filters)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        self.page_label = hint_label("正在加载…", wrap=False)
        nav.addWidget(self.page_label, 1)
        self.prev_button = button("上一页")
        self.prev_button.clicked.connect(lambda: self.start_search(self._page - 1))
        self.next_button = button("下一页")
        self.next_button.clicked.connect(lambda: self.start_search(self._page + 1))
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        browse_layout.addLayout(nav)
        layout.addWidget(browse)

        self.models = RowListWidget(self)
        self.models.setObjectName("ModelResultList")
        self.models.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.models.setSpacing(4)
        self.models.setMinimumHeight(200)
        self.models.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.models.currentRowChanged.connect(self._on_model_selected)
        self.models.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.models, 1)

        self.download_status = QWidget(self)
        status_row = QHBoxLayout(self.download_status)
        status_row.setContentsMargins(2, 0, 2, 0)
        status_row.setSpacing(8)
        status_row.addStretch(1)
        self.download_text = hint_label("", wrap=False)
        status_row.addWidget(self.download_text)
        self.download_bar = QProgressBar(self.download_status)
        self.download_bar.setRange(0, 100)
        self.download_bar.setFixedWidth(220)
        status_row.addWidget(self.download_bar)
        self.stop_download_button = link_button("停止", tooltip="停止当前下载")
        self.stop_download_button.clicked.connect(self.stop_download)
        self.stop_download_button.setVisible(False)
        status_row.addWidget(self.stop_download_button)
        self.download_status.setVisible(False)
        layout.addWidget(self.download_status)

        self.detail_view = _ModelDetailView(self)
        self.detail_view.setVisible(False)

        self.refresh_installed()
        self.filter_buttons[_DEFAULT_FILTER].setChecked(True)
        self.start_search(1)

    # -- 筛选 ---------------------------------------------------------- #
    def _on_filter_changed(self, key: str) -> None:
        """切换浏览筛选：分类指标走服务端关键字，适合本机在本地按容量过滤。"""
        self._filter_key = key
        self._filter_query = next((query for name, _label, query in _MODEL_FILTERS if name == key), "")
        self.start_search(1)

    def refresh_installed(self) -> None:
        """重新扫描模型目录，记录已安装仓库（用于勾选标志与置顶）。"""
        try:
            models = scan_local_models(_llmconfig.models_dir())
        except OSError:
            self._installed_models = {}
            self._installed_repos = set()
        else:
            self._installed_models = {
                model.repo.casefold(): ModelSummary(
                    repo=model.repo,
                    name=model.repo.rsplit("/", 1)[-1],
                    storage_size=model.size,
                )
                for model in models
            }
            self._installed_repos = set(self._installed_models)

    def _local_capacity(self) -> int:
        """本机可承载的整仓体积（显存与系统内存取较大者，未知时为 0）。"""
        hardware = getattr(self.state, "hardware", None)
        gpu_mib = getattr(self.state, "gpu_mib", None) or (hardware.gpu_mib if hardware is not None else None)
        memory = hardware.memory_total if hardware is not None else 0
        gpu_bytes = (gpu_mib or 0) * 1024**2
        memory_bytes = int(memory * 0.8) if memory else 0
        return max(gpu_bytes, memory_bytes)

    def start_search(self, page: int) -> None:
        """按当前筛选拉取指定页；筛选关键字为空时浏览全部模型。"""
        if self._searching:
            return
        self._page = max(1, page)
        self._searching = True
        self.page_label.setText(f"正在加载第 {self._page} 页…")
        submit(
            search_models,
            self._filter_query,
            self._page,
            _PAGE_SIZE,
            "default",
            on_done=self._render_search,
            on_error=self._search_error,
            on_finished=self._search_finished,
        )

    def _search_finished(self) -> None:
        self._searching = False

    def _search_error(self, message: str) -> None:
        self.page_label.setText("加载失败")
        self._log(f"模型列表加载失败: {message}")

    def _render_search(self, result: ModelSearch) -> None:
        self._last_result = result
        self._render_results()

    def open_repo(self, repo: str) -> None:
        """直接打开 ``组织/模型``：生成单行结果并就地展开详情。"""
        self._last_result = ModelSearch(items=(ModelSummary(repo=repo),), total=1)
        self._render_results()
        self._expand_repo(repo)

    def _render_results(self) -> None:
        result = self._last_result
        if result is None:
            return
        items = result.items
        filter_label = next((label for key, label, _query in _MODEL_FILTERS if key == self._filter_key), "")
        if self._filter_key == "local":
            items = _locllmw_models.filter_by_local_fit(items, self._local_capacity())
        items = _locllmw_models.merge_installed_items(items, self._installed_models, self._filter_key)
        items = _locllmw_models.sort_browse_items(items, self._installed_repos, self._filter_key)
        selected = next(
            (item.repo for item in items if item.repo == self.current_repo),
            items[0].repo if items else None,
        )
        self._result_items = items
        self._detach_detail()
        self._detail_generation += 1
        self.current_repo = None
        self.current_info = None
        self.models.clear()
        for item in items:
            list_item = QListWidgetItem(self.models)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            installed = item.repo.casefold() in self._installed_repos
            row = _ModelItemRow(item, self.models, installed=installed)
            row.toggleRequested.connect(self._on_row_toggle)
            row.downloadRequested.connect(self.start_download)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.models.setItemWidget(list_item, row)
        self.models.sync_row_widths()
        self._sync_download_buttons()
        pages = max(1, math.ceil(result.total / result.page_size)) if result.total else 1
        matched = f" · 本页显示 {len(items)} 个" if self._filter_key == "local" else ""
        self.page_label.setText(f"{filter_label} · 第 {result.page} / {pages} 页（共 {result.total:,} 条{matched}）")
        self.prev_button.setEnabled(result.page > 1)
        self.next_button.setEnabled(result.page < pages)
        if selected is not None:
            for row, item in enumerate(items):
                if item.repo == selected:
                    self.models.setCurrentRow(row)
                    break
        if not items:
            self.page_label.setText(f"{filter_label} · 当前页没有匹配的模型，可切换筛选或翻页")

    # -- 详情 ---------------------------------------------------------- #
    def _on_model_selected(self, current: int) -> None:
        for index in range(self.models.count()):
            widget = self.models.itemWidget(self.models.item(index))
            if isinstance(widget, _ModelItemRow):
                widget.set_selected(index == current)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.models.itemWidget(item)
        if isinstance(row, _ModelItemRow):
            self._on_row_toggle(row)

    def _on_row_toggle(self, row: _ModelItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self._focus_row(row)
        if row.is_expanded():
            row.set_expanded(False)
            self.detail_view.setVisible(False)
            self._expanded_row = None
            self._update_row_height(row)
            return
        if self._expanded_row is not None and self._expanded_row is not row:
            self._expanded_row.set_expanded(False)
            self._update_row_height(self._expanded_row)
        row.detail_layout.addWidget(self.detail_view)
        self.detail_view.setVisible(True)
        row.set_expanded(True)
        self._expanded_row = row
        self._update_row_height(row)
        if self.current_repo != row.repo:
            self.load_repo(row.repo)

    def _expand_repo(self, repo: str) -> None:
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _ModelItemRow) and row.repo == repo:
                self.models.setCurrentRow(index)
                if not row.is_expanded():
                    self._on_row_toggle(row)
                return

    def _focus_row(self, row: _ModelItemRow) -> None:
        for index in range(self.models.count()):
            if self.models.itemWidget(self.models.item(index)) is row:
                self.models.setCurrentRow(index)
                return

    def _detach_detail(self) -> None:
        """列表重建前把复用的详情控件从旧行摘下来，避免随旧行一起销毁。"""
        self.detail_view.setParent(None)
        self.detail_view.setVisible(False)
        if self._expanded_row is not None:
            self._expanded_row.set_expanded(False)
            self._expanded_row = None

    def _update_row_height(self, row: _ModelItemRow | None) -> None:
        if row is None:
            return
        layout = row.layout()
        if layout is not None:
            layout.activate()
        for index in range(self.models.count()):
            item = self.models.item(index)
            if self.models.itemWidget(item) is row:
                item.setSizeHint(QSize(0, row.sizeHint().height()))
                self.models.sync_row_widths()
                return

    def load_repo(self, repo: str) -> None:
        """加载仓库详情元数据；世代号防止旧请求覆盖新选择。"""
        self._detail_generation += 1
        generation = self._detail_generation
        self.current_repo = repo
        self.current_info = None
        self.detail_view.reset(repo)
        self._update_row_height(self._expanded_row)
        self.page_label.setText(f"正在加载 {repo} …")
        submit(
            self._detail_work,
            repo,
            on_done=lambda info: self._render_detail(generation, info),
            on_error=lambda message: self._detail_error(generation, repo, message),
        )

    @staticmethod
    def _detail_work(repo: str) -> ModelInfo:
        return fetch_model_info(repo)

    def _detail_error(self, generation: int, repo: str, message: str) -> None:
        if generation != self._detail_generation:
            return
        self.detail_view.set_error(f"详情加载失败：{message}")
        self._update_row_height(self._expanded_row)
        self.page_label.setText(f"{repo} 详情加载失败")
        self._log(f"详情加载失败 {repo}: {message}")

    def _render_detail(self, generation: int, info: ModelInfo) -> None:
        if generation != self._detail_generation:
            return
        self.current_info = info
        self.detail_view.render(info)
        self._update_row_height(self._expanded_row)
        self.page_label.setText(f"已加载 {info.summary.repo}")
        self.repoSelected.emit(info.summary.repo)
        self._log(f"已加载模型详情: {info.summary.repo}")

    def _log(self, message: str) -> None:
        self.state.logLine.emit(message)

    # -- 下载 ---------------------------------------------------------- #
    def _sync_download_buttons(self) -> None:
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _ModelItemRow):
                row.set_download_state(busy=self._downloading, active=row.repo == self._active_repo)

    def start_download(self, repo: str) -> None:
        """下载指定仓库到模型目录，进度显示在列表底部右侧。"""
        if self._downloading or not repo:
            return
        settings = _locllmw_models.settings_from_dict(self.state.settings.download)
        dest = _llmconfig.models_dir()
        self._downloading = True
        self._stop_requested = False
        self._active_repo = repo
        self._download_target = dest / repo
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"准备下载 {repo}…")
        self.stop_download_button.setEnabled(True)
        self.stop_download_button.setVisible(True)
        self._sync_download_buttons()
        self._log(f"开始下载 {repo} -> {self._download_target}")
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_progress)
        submit(
            self._download_work,
            repo,
            dest,
            settings.source,
            settings.include,
            settings.exclude,
            settings.workers,
            settings.verify,
            on_done=self._download_done,
            on_error=lambda message: self._download_error(repo, message),
            on_finished=self._download_finished,
        )

    def _download_work(  # noqa: PLR0913, PLR0917 - 下载参数成组传入
        self,
        repo: str,
        dest: Path,
        source: str,
        include: str,
        exclude: str,
        workers: int,
        verify: bool,
    ) -> str:
        self._log(f"获取 {repo} 的 {source} 文件清单…")
        selection = select_source(repo, source)  # type: ignore[arg-type]
        self._log(f"选用源 {selection.source}，仓库共 {len(selection.files)} 个文件")
        selected = filter_files(
            list(selection.files),
            _locllmw_models.parse_patterns(include),
            _locllmw_models.parse_patterns(exclude),
        )
        if not selected:
            raise ModelToolError("过滤后没有文件，请检查设置中的包含 / 排除掩码")
        target = dest / repo
        self._log(f"待下载 {len(selected)} 个文件，目标 {target}")
        download_files(
            repo,
            selected,
            target,
            selection,
            workers=workers,
            verify=verify,
            reporter=self._emit_progress,
            should_stop=lambda: self._stop_requested,
        )
        return str(target)

    def _emit_progress(self, event: ProgressEvent) -> None:
        if self._bridge is not None:
            self._bridge.progress.emit(event)

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.task != "总进度":
            return
        self.download_bar.setValue(round(100 * event.completed / max(event.total, 1)))
        self.download_text.setText(
            f"下载 {self._active_repo or '-'} · {human_size(event.completed)} / {human_size(event.total)}"
        )

    def _download_done(self, target: str) -> None:
        self.download_bar.setValue(100)
        self.download_text.setText(f"下载完成：{self._active_repo or target}")
        self._log(f"下载完成: {target}")
        self.refresh_installed()
        self._render_results()
        self.downloadFinished.emit()

    def _download_error(self, repo: str, message: str) -> None:
        self.download_text.setText(f"下载失败：{repo} · {message}")
        self._log(f"下载失败 {repo}: {message}")

    def _download_finished(self) -> None:
        self._downloading = False
        self._active_repo = None
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()

    def stop_download(self) -> None:
        if not self._downloading:
            return
        self._stop_requested = True
        self.stop_download_button.setEnabled(False)
        self.download_text.setText(f"正在停止 {self._active_repo or ''}…")
        self._log("正在停止下载…")

    def close(self) -> bool:
        """关窗前确认进行中的下载，返回 ``False`` 表示用户取消关闭。"""
        if not self._downloading:
            return True
        answer = QMessageBox.question(
            self,
            "下载进行中",
            "下载尚未完成，关闭窗口将停止下载（已下载部分保留为 .part）。确实关闭？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._stop_requested = True
        return True


class _ConvertPanel(QWidget):  # pragma: no cover - Qt 控件
    """转换页：HF safetensors → GGUF。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._convertible: list[tuple[Path, int]] = []
        self._running = False
        self._env_loaded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = PageScroll(self)
        outer.addWidget(scroll)
        layout = scroll.body
        layout.setSpacing(6)

        models_card = Card("可转换模型（HF safetensors 目录）")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.refresh_button = button("刷新模型列表")
        self.refresh_button.clicked.connect(self.refresh_env)
        bar.addWidget(self.refresh_button)
        self.models_hint = hint_label("正在扫描模型目录…")
        bar.addWidget(self.models_hint, 1)
        models_card.body.addLayout(bar)
        self.convertible = make_table(
            ("模型", "大小", "输出", "位置"),
            widths=(180, 90, 100, 380),
            stretch_columns=(3,),
            parent=models_card,
        )
        self.convertible.setMinimumHeight(100)
        self.convertible.itemSelectionChanged.connect(self._on_selected)
        models_card.body.addWidget(self.convertible)
        layout.addWidget(models_card)

        io_card = Card("输入 / 输出")
        io_grid = QGridLayout()
        io_grid.setContentsMargins(0, 0, 0, 0)
        io_grid.setHorizontalSpacing(8)
        io_grid.setVerticalSpacing(6)
        io_grid.setColumnStretch(1, 1)
        io_grid.addWidget(QLabel("源目录"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.source = QLineEdit(io_card)
        self.source.textChanged.connect(self._refresh_output_state)
        io_grid.addWidget(self.source, 0, 1)
        pick_source = button("浏览…")
        pick_source.clicked.connect(self._pick_source)
        io_grid.addWidget(pick_source, 0, 2)
        io_grid.addWidget(QLabel("输出 GGUF"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.output = QLineEdit(io_card)
        self.output.textChanged.connect(self._refresh_output_state)
        io_grid.addWidget(self.output, 1, 1)
        pick_output = button("另存为…")
        pick_output.clicked.connect(self._pick_output)
        io_grid.addWidget(pick_output, 1, 2)
        open_dir = button("打开目录")
        open_dir.clicked.connect(self._open_output_dir)
        io_grid.addWidget(open_dir, 1, 3)
        io_card.body.addLayout(io_grid)
        self.output_hint = hint_label("")
        io_card.body.addWidget(self.output_hint)
        layout.addWidget(io_card)

        params = Card("转换参数")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("转换精度"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.outtype = QComboBox(params)
        self.outtype.addItems(list(llmconvert.CONVERT_OUTTYPES))
        grid.addWidget(self.outtype, 0, 1)
        grid.addWidget(QLabel("引擎 tag"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.tag = QLineEdit(params)
        grid.addWidget(self.tag, 0, 3)
        grid.addWidget(QLabel("解释器"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.python = QLineEdit(params)
        self.python.setPlaceholderText("留空自动探测已安装 torch / safetensors / numpy")
        grid.addWidget(self.python, 1, 1, 1, 3)
        grid.addWidget(QLabel("转换脚本"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.script = QLineEdit(params)
        self.script.setPlaceholderText(_locllmw_convert._SCRIPT_PLACEHOLDER)
        grid.addWidget(self.script, 2, 1, 1, 2)
        pick_script = button("浏览…")
        pick_script.clicked.connect(self._pick_script)
        grid.addWidget(pick_script, 2, 3)
        self.overwrite = QCheckBox("覆盖已存在的输出文件", params)
        self.overwrite.toggled.connect(lambda _checked: self._refresh_existing())
        grid.addWidget(self.overwrite, 3, 0, 1, 2)
        params.body.addLayout(grid)
        self.existing = hint_label("")
        params.body.addWidget(self.existing)
        params.body.addWidget(
            hint_label(
                f"精度可选：{' / '.join(llmconvert.CONVERT_OUTTYPES)}；"
                f"脚本可填 convert_hf_to_gguf.py 或源码 zip，{_locllmw_convert._SCRIPT_PLACEHOLDER}"
            )
        )
        layout.addWidget(params)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.preview_button = button("预览计划")
        self.preview_button.clicked.connect(self._preview)
        actions.addWidget(self.preview_button)
        self.start_button = button("开始转换", kind="primary")
        self.start_button.clicked.connect(self._start_convert)
        actions.addWidget(self.start_button)
        self.clear_button = button("清空日志")
        actions.addWidget(self.clear_button)
        self.status = hint_label("状态：就绪", wrap=False)
        actions.addWidget(self.status, 1)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        actions.addWidget(self.progress)
        layout.addLayout(actions)

        log_card = Card("转换输出日志")
        self.log_view = LogView(height=100, parent=log_card)
        self.clear_button.clicked.connect(self.log_view.clear)
        log_card.body.addWidget(self.log_view)
        layout.addWidget(log_card, 1)
        layout.addStretch(1)
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_percent)

    # -- 环境 ---------------------------------------------------------- #
    def refresh_env(self) -> None:
        self.refresh_button.setEnabled(False)
        self.models_hint.setText("正在扫描模型目录…")
        submit(
            self._scan_env,
            on_done=self._apply_env,
            on_error=self._env_error,
            on_finished=lambda: self.refresh_button.setEnabled(True),
        )

    @staticmethod
    def _scan_env() -> _locllmw_convert.ModelEnv:
        return _locllmw_convert.scan_model_env()

    def _env_error(self, message: str) -> None:
        self.models_hint.setText(f"扫描模型目录失败: {message}")

    def _apply_env(self, env: _locllmw_convert.ModelEnv) -> None:
        if env.tag and not self.tag.text().strip():
            self.tag.setText(env.tag)
        self._apply_convertible(env.convertible)
        self._refresh_existing()

    def _apply_convertible(self, convertible: list[tuple[Path, int]]) -> None:
        self._convertible = convertible
        root = _llmconfig.models_dir()
        self.convertible.setRowCount(len(convertible))
        for row, (path, size) in enumerate(convertible):
            existing = _locllmw_convert.default_output_path(path, root).is_file()
            self.convertible.setItem(row, 0, make_table_item(path.name))
            self.convertible.setItem(row, 1, make_table_item(human_size(size)))
            self.convertible.setItem(row, 2, make_table_item("已生成" if existing else "未转换"))
            self.convertible.setItem(row, 3, make_table_item(str(path)))
        if convertible:
            self.models_hint.setText(
                f"发现 {len(convertible)} 个可转换模型；选中后自动填入源目录，“输出”列显示默认输出是否已生成"
            )
        else:
            self.models_hint.setText(
                "未发现可转换模型（模型目录中需含 config.json 与 *.safetensors）；可在设置页调整模型目录"
            )
        if self.source.text().strip():
            self._sync_selection(Path(self.source.text()).expanduser())

    def _sync_selection(self, path: Path) -> None:
        key = llmenv.path_key(path)
        for row, (candidate, _) in enumerate(self._convertible):
            if llmenv.path_key(candidate) == key:
                self.convertible.selectRow(row)
                return

    def _on_selected(self) -> None:
        row = self.convertible.currentRow()
        if not 0 <= row < len(self._convertible):
            return
        path = self._convertible[row][0]
        self.source.setText(str(path))
        base = _locllmw_convert.default_output_path(path)
        detail = "；默认输出已存在（可勾选覆盖后重新生成）" if base.is_file() else "；默认输出尚未生成"
        self.status.setText(f"状态：已选择 {path.name}（{_locllmw_convert._dir_stats(path)}{detail}）")
        self.log_view.append(f"已选择可转换模型: {path}")

    # -- 表单 ---------------------------------------------------------- #
    def _pick_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择 HF safetensors 模型目录")
        if chosen:
            self.source.setText(chosen)
            self._sync_selection(Path(chosen).resolve())
            self.status.setText(f"状态：已选择源目录 {Path(chosen).name}")

    def _pick_output(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "选择输出 GGUF 文件", "", "GGUF 文件 (*.gguf);;所有文件 (*.*)")
        if chosen:
            self.output.setText(chosen)

    def _pick_script(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择转换脚本或 llama.cpp 源码包",
            "",
            "转换脚本 / 源码包 (*.py *.zip);;所有文件 (*.*)",
        )
        if chosen:
            self.script.setText(chosen)

    def _output_path(self) -> Path | None:
        return _locllmw_convert.output_path_from_form(self.source.text(), self.output.text())

    def _refresh_output_state(self) -> None:
        base = self._output_path()
        if base is None:
            root = _llmconfig.products_dir().expanduser()
            self.output_hint.setText(f"输出留空时写入模型目录的 converted 子目录：{root}/<源目录名>/<源目录名>.gguf")
        elif self.output.text().strip():
            self.output_hint.setText(f"输出文件：{base}")
        else:
            self.output_hint.setText(f"默认输出目录：{base.parent}（文件名 {base.name}）")
        self._refresh_existing()

    def _refresh_existing(self) -> None:
        if self._running:
            return
        base = self._output_path()
        if base is not None and base.is_file():
            self.existing.setText(f"输出已存在：{base}（勾选“覆盖已存在的输出文件”后才会重新生成）")
        else:
            self.existing.setText("")

    def _open_output_dir(self) -> None:
        base = self._output_path()
        target = base.parent if base is not None else _llmconfig.products_dir()
        target.mkdir(parents=True, exist_ok=True)
        try:
            _locllmw_models.open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开目录失败", str(exc))

    def _snapshot(self) -> _locllmw_convert._ConvertForm:
        return _locllmw_convert._ConvertForm(
            source=self.source.text().strip(),
            output=self.output.text().strip(),
            outtype=self.outtype.currentText().strip() or "f16",
            script=self.script.text().strip(),
            python=self.python.text().strip(),
            tag=self.tag.text().strip(),
        )

    # -- 执行 ---------------------------------------------------------- #
    def _preview(self) -> None:
        if self._running:
            self.log_view.append("已有任务正在进行，请等待完成")
            return
        self._begin(self._preview_work, self._snapshot())

    def _start_convert(self) -> None:
        if self._running:
            self.log_view.append("已有任务正在进行，请等待完成")
            return
        base = self._output_path()
        if base is None:
            QMessageBox.warning(self, "缺少源目录", "请选择 HF safetensors 源目录")
            return
        if base.is_file() and not self.overwrite.isChecked():
            QMessageBox.warning(self, "输出已存在", f"{base}\n\n如要重新生成，请勾选“覆盖已存在的输出文件”。")
            return
        self._begin(self._convert_work, self._snapshot())

    def _begin(self, target: Any, *args: Any) -> None:
        self._set_running(True)
        submit(
            target,
            *args,
            on_done=self._task_done,
            on_error=self._task_error,
            on_finished=lambda: self._set_running(False),
        )

    def _on_percent(self, value: object) -> None:
        if isinstance(value, int):
            self.progress.setValue(value)

    def _task_done(self, payload: object) -> None:
        if payload == "preview":
            self.log_view.append("计划预览完成，可点击“开始转换”执行")
            return
        produced = [str(item) for item in payload] if isinstance(payload, list) else []
        self.progress.setValue(100)
        self._apply_convertible(self._convertible)
        self.status.setText("状态：转换完成")
        self.log_view.append("已生成: " + "、".join(produced))
        QMessageBox.information(self, "转换完成", "已生成:\n" + "\n".join(produced))

    def _task_error(self, message: str) -> None:
        self.log_view.append(message)
        self.status.setText("状态：失败")
        QMessageBox.critical(self, "错误", message)

    def _set_running(self, running: bool) -> None:
        self._running = running
        for widget in (self.preview_button, self.start_button, self.refresh_button):
            widget.setEnabled(not running)
        self.status.setText("状态：转换中…" if running else "状态：就绪")
        if running:
            self.progress.setValue(0)
        else:
            self._refresh_existing()

    def _prepare_plan(self, form: _locllmw_convert._ConvertForm) -> llmconvert.ConvertPlan:
        if not form.source:
            raise ServeError("请选择 HF safetensors 源目录")
        source = Path(form.source).expanduser()
        if not llmconvert.is_hf_safetensors_dir(source):
            raise ServeError(f"不是 HF safetensors 目录（需要 config.json 与 *.safetensors）: {source}")
        self._log(f"源目录: {source}（{_locllmw_convert._dir_stats(source)}）")
        python = form.python
        if not python:
            self._log("正在探测已安装 torch / safetensors / numpy 的解释器…")
            python = llmconvert.find_convert_python()
        self._log(f"解释器: {python}")
        self._log("正在准备转换脚本（首次使用会下载 llama.cpp 源码）…")
        base = _locllmw_convert.output_path_from_form(form.source, form.output)
        if base is None:
            raise ServeError("无法确定输出路径")
        return llmconvert.plan_convert(
            source,
            output=base,
            outtype=form.outtype,
            script=Path(form.script).expanduser() if form.script else None,
            python=python,
            tag=form.tag or None,
        )

    def _log(self, message: str) -> None:
        self.log_view.append(message)

    def _log_plan(self, plan: llmconvert.ConvertPlan) -> None:
        for line in (
            "转换计划:",
            f"  源目录: {plan.source}（{_locllmw_convert._dir_stats(plan.source)}）",
            f"  精度: {plan.outtype}",
            f"  输出: {plan.output}",
            f"  脚本: {plan.script}",
            f"  解释器: {plan.python}",
            "  转换命令: " + llmbase_format(plan),
        ):
            self._log(line)

    def _preview_work(self, form: _locllmw_convert._ConvertForm) -> str:
        plan = self._prepare_plan(form)
        self._log_plan(plan)
        return "preview"

    def _convert_work(self, form: _locllmw_convert._ConvertForm) -> list[str]:
        plan = self._prepare_plan(form)
        self._log_plan(plan)
        last = -1

        def relay(line: str) -> None:
            nonlocal last
            percent = _locllmw_convert._progress_percent(line)
            if percent is None:
                self._log(line)
            elif percent >= last:
                last = percent
                self._bridge.progress.emit(percent)

        self._log(f"开始转换 {plan.source.name}…")
        produced = [str(path) for path in llmconvert.convert_model(plan, on_output=relay)]
        return produced


def llmbase_format(plan: llmconvert.ConvertPlan) -> str:
    """格式化转换命令（复用 ``llmbase.format_command``）。"""
    from fcmd.apps.locllm import llmbase

    return llmbase.format_command(plan.convert_argv())


class _QuantPanel(QWidget):  # pragma: no cover - Qt 控件
    """量化页：GGUF 量化。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._input: Path | None = None
        self._models: list[ModelEntry] = []
        self._precisions: dict[str, GgufPrecision] = {}
        self._out_dir_auto = True
        self._running = False
        self._env_loaded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = PageScroll(self)
        outer.addWidget(scroll)
        layout = scroll.body
        layout.setSpacing(6)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(6)
        file_row.addWidget(QLabel("输入文件"))
        self.file_text = QLineEdit(self)
        self.file_text.setReadOnly(True)
        self.file_text.setPlaceholderText("尚未选择输入文件")
        file_row.addWidget(self.file_text, 1)
        pick = button("选择…")
        pick.clicked.connect(self._select_file)
        file_row.addWidget(pick)
        layout.addLayout(file_row)

        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        model_row.addWidget(QLabel("未量化模型（GGUF）"))
        self.model_combo = QComboBox(self)
        self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        model_row.addWidget(self.model_combo, 1)
        refresh = button("刷新模型列表")
        refresh.clicked.connect(self.refresh_env)
        model_row.addWidget(refresh)
        layout.addLayout(model_row)

        info_card = Card("环境信息")
        info_grid = QGridLayout()
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(4)
        info_grid.setColumnStretch(1, 1)
        self.info_values: dict[str, QLabel] = {}
        for row, (key, label) in enumerate((("engine", "引擎"), ("quantizer", "量化器"), ("version", "版本"))):
            info_grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("检测中…", info_card)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_grid.addWidget(value, row, 1)
            self.info_values[key] = value
        info_card.body.addLayout(info_grid)
        layout.addWidget(info_card)

        quant_card = Card("量化类型")
        quant_grid = QGridLayout()
        quant_grid.setContentsMargins(0, 0, 0, 0)
        quant_grid.setHorizontalSpacing(12)
        quant_grid.setVerticalSpacing(4)
        self.quant_checks: dict[str, QCheckBox] = {}
        for index, (quant, desc) in enumerate(llmqnt.QUANT_TYPES.items()):
            check = QCheckBox(f"{quant}  {desc}", quant_card)
            check.setChecked(quant in llmqnt.DEFAULT_QUANTS)
            quant_grid.addWidget(check, index // 3, index % 3)
            self.quant_checks[quant] = check
        quant_card.body.addLayout(quant_grid)
        layout.addWidget(quant_card)

        out_row = QHBoxLayout()
        out_row.setContentsMargins(0, 0, 0, 0)
        out_row.setSpacing(6)
        out_row.addWidget(QLabel("输出目录"))
        self.out_dir = QLineEdit(str(_llmconfig.products_dir()), self)
        self.out_dir.textEdited.connect(self._on_out_dir_edited)
        out_row.addWidget(self.out_dir, 1)
        pick_dir = button("浏览…")
        pick_dir.clicked.connect(self._pick_out_dir)
        out_row.addWidget(pick_dir)
        default_dir = button("默认目录")
        default_dir.clicked.connect(self._reset_out_dir)
        out_row.addWidget(default_dir)
        layout.addLayout(out_row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.overwrite = QCheckBox("覆盖已存在的量化文件", self)
        self.overwrite.toggled.connect(lambda _checked: self._mark_existing())
        actions.addWidget(self.overwrite)
        actions.addStretch(1)
        self.start_button = button("开始量化", kind="primary")
        self.start_button.clicked.connect(self._start_quant)
        actions.addWidget(self.start_button)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        actions.addWidget(self.progress)
        layout.addLayout(actions)

        log_card = Card("量化输出日志")
        self.log_view = LogView(height=100, parent=log_card)
        log_card.body.addWidget(self.log_view)
        layout.addWidget(log_card, 1)
        layout.addStretch(1)

    # -- 环境 ---------------------------------------------------------- #
    def refresh_env(self) -> None:
        for value in self.info_values.values():
            value.setText("检测中…")
        submit(self._scan_env, on_done=self._apply_env, on_error=self._env_error)

    @staticmethod
    def _scan_env() -> _locllmw_convert.ModelEnv:
        return _locllmw_convert.scan_model_env()

    def _env_error(self, message: str) -> None:
        self.info_values["engine"].setText(f"检测失败（{message}）")

    def _apply_env(self, env: _locllmw_convert.ModelEnv) -> None:
        info, entries, precisions = env.info, env.entries, env.precisions
        for key, value in info.items():
            if key in self.info_values:
                self.info_values[key].setText(value or "-")
        self._precisions = precisions
        usable = [entry for entry in entries if self._precision_for(entry.path).quantized is not True]
        self._models = usable
        labels = [
            f"{entry.relative}（{self._precision_for(entry.path).note}）  ({human_size(entry.size)})"
            for entry in usable
        ]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(labels)
        self.model_combo.setCurrentIndex(-1)
        self.model_combo.blockSignals(False)
        self.model_combo.setEnabled(bool(labels) and not self._running)
        self._mark_existing()

    def _precision_for(self, path: Path, *, refresh: bool = False) -> GgufPrecision:
        key = llmenv.path_key(path)
        if refresh or key not in self._precisions:
            self._precisions[key] = detect_precision(path)
        return self._precisions[key]

    # -- 选择 ---------------------------------------------------------- #
    def _select_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择 F16 格式的 GGUF 文件", "", "GGUF 文件 (*.gguf);;所有文件 (*.*)"
        )
        if chosen:
            self._choose_input(Path(chosen))

    def _on_model_selected(self, index: int) -> None:
        if 0 <= index < len(self._models):
            self._choose_input(self._models[index].path)

    def _choose_input(self, path: Path) -> None:
        precision = self._precision_for(path, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            QMessageBox.warning(self, "无法作为量化输入", f"{path.name}：{reason}")
            return
        changed = self._input is None or llmenv.path_key(path) != llmenv.path_key(self._input)
        self._input = path
        self.file_text.setText(str(path))
        if changed:
            for quant, check in self.quant_checks.items():
                check.setChecked(quant in llmqnt.DEFAULT_QUANTS)
        self._mark_existing()
        self.log_view.clear()
        self.log_view.append(f"精度: {precision.detail}")
        self.progress.setValue(0)

    def _on_out_dir_edited(self, _text: str) -> None:
        text = self.out_dir.text().strip()
        self._out_dir_auto = not text or (
            llmenv.path_key(Path(text).expanduser()) == llmenv.path_key(_llmconfig.products_dir())
        )
        self._mark_existing()

    def _pick_out_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择量化输出目录", str(self._out_dir()))
        if chosen:
            self.out_dir.setText(chosen)

    def _reset_out_dir(self) -> None:
        self._out_dir_auto = True
        self.out_dir.setText(str(_llmconfig.products_dir()))

    def _out_dir(self) -> Path:
        text = self.out_dir.text().strip()
        return Path(text).expanduser() if text else _llmconfig.products_dir()

    def _mark_existing(self) -> None:
        overwrite = self.overwrite.isChecked()
        out_dir = self._out_dir()
        for quant, check in self.quant_checks.items():
            desc = llmqnt.QUANT_TYPES[quant]
            exists = self._input is not None and llmqnt.output_path(self._input, quant, out_dir).is_file()
            check.setText(f"{quant}  {desc}" + ("（已存在）" if exists else ""))
            check.setEnabled(overwrite or not exists)

    def _selected_quants(self) -> list[str]:
        overwrite = self.overwrite.isChecked()
        out_dir = self._out_dir()
        selected: list[str] = []
        for quant, check in self.quant_checks.items():
            if not check.isChecked():
                continue
            if not overwrite and self._input is not None and llmqnt.output_path(self._input, quant, out_dir).is_file():
                continue
            selected.append(quant)
        return selected

    # -- 执行 ---------------------------------------------------------- #
    def _start_quant(self) -> None:
        if self._running:
            self.log_view.append("已有任务正在进行，请等待完成")
            return
        if self._input is None or not self._input.is_file():
            QMessageBox.warning(self, "缺少输入", "请先选择 F16 GGUF 文件")
            return
        precision = self._precision_for(self._input, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            QMessageBox.critical(self, "无法开始量化", f"{self._input.name}：{reason}")
            return
        quants = self._selected_quants()
        if not quants:
            QMessageBox.warning(self, "缺少量化类型", "请至少勾选一种尚未生成的量化类型")
            return
        try:
            exe = llmqnt.resolve_quantize_exe()
            jobs = llmqnt.plan_jobs(self._input, quants, self._out_dir())
            pending, skipped = llmqnt.pending_jobs(jobs, self.overwrite.isChecked())
        except llmqnt.QuantizeError as exc:
            QMessageBox.critical(self, "无法开始量化", str(exc))
            return
        self.log_view.clear()
        self.log_view.append(f"输入: {self._input}")
        self.log_view.append(f"精度: {precision.detail}")
        self.log_view.append(f"量化器: {exe}")
        self.log_view.append(f"输出目录: {self._out_dir()}")
        self.log_view.append(f"量化类型: {', '.join(quants)}（共 {len(pending)} 个任务）")
        if skipped:
            self.log_view.append(f"已存在并跳过: {', '.join(job.quant for job in skipped)}")
        if not pending:
            self.log_view.append("全部量化文件已存在，无需转换")
            return
        self._set_running(True)
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_progress)
        submit(
            self._quant_work,
            pending,
            exe,
            on_done=self._quant_done,
            on_error=self._quant_error,
            on_finished=lambda: self._set_running(False),
        )

    def _quant_work(self, pending: list[Any], exe: Path) -> bool:
        success = True
        total = len(pending)
        for index, job in enumerate(pending, 1):
            self._log_safe(f"正在转换到 {job.quant} 格式…")
            try:
                llmqnt.run_job(job, exe, on_output=self._log_safe)
            except llmqnt.QuantizeError as exc:
                success = False
                self._log_safe(f"转换 {job.quant} 失败: {exc}")
            else:
                self._log_safe(f"成功生成: {job.output}")
            if self._bridge is not None:
                self._bridge.progress.emit(index * 100 // total)
        return success

    def _log_safe(self, message: str) -> None:
        self.log_view.append(message)

    def _on_progress(self, value: object) -> None:
        if isinstance(value, int):
            self.progress.setValue(value)

    def _quant_done(self, success: bool) -> None:
        if success:
            self.progress.setValue(100)
            self.log_view.append("所有量化转换完成！")
            QMessageBox.information(self, "完成", "所有量化转换已完成")
        else:
            self.log_view.append("量化转换过程中出现错误，详见日志")
            QMessageBox.warning(self, "失败", "部分量化任务失败，详见日志")

    def _quant_error(self, message: str) -> None:
        self.log_view.append(message)
        QMessageBox.critical(self, "量化失败", message)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.start_button.setEnabled(not running)
        self.model_combo.setEnabled(bool(self._models) and not running)
        for check in self.quant_checks.values():
            check.setEnabled(not running)
        if not running:
            self._mark_existing()

    def close(self) -> bool:
        return True


class ModelCenterPage(QWidget):  # pragma: no cover - Qt 控件
    """模型中心的三个子页。"""

    def __init__(
        self,
        state: LocllmState,
        parent: QWidget | None = None,
        *,
        fit_resolver: Any = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        del fit_resolver
        self.browse = _BrowsePanel(state, self)
        self.convert = _ConvertPanel(self)
        self.quant = _QuantPanel(self)
        self.tabs = QTabWidget(self)
        for name, page in zip(_TAB_NAMES, (self.browse, self.convert, self.quant), strict=True):
            self.tabs.addTab(page, name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.tabs.setCurrentIndex(_TAB_NAMES.index(_DEFAULT_TAB))
        self._install_env_hooks()

    def _install_env_hooks(self) -> None:
        """首次进入转换 / 量化页时再扫描环境，避免冷启动做重活。"""
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        name = _TAB_NAMES[index]
        if name == "转换" and not self.convert._env_loaded:
            self.convert._env_loaded = True
            self.convert.refresh_env()
        elif name == "量化" and not self.quant._env_loaded:
            self.quant._env_loaded = True
            self.quant.refresh_env()

    def refresh_config(self) -> None:
        """模型目录变化后刷新已安装标识与转换 / 量化环境。"""
        self.browse.refresh_installed()
        self.convert.refresh_env()
        if self.tabs.currentIndex() == _TAB_NAMES.index("量化"):
            self.quant.refresh_env()

    def close(self) -> bool:
        """确认并停止下载 / 转换 / 量化任务；返回 ``False`` 表示用户取消关闭。"""
        return self.browse.close()
