"""locllmw（Qt）模型中心：ModelScope 浏览器 / 量化两个子页。

页面口径与迁移前的窗口一致，只是把控件换成 Qt 并把后台任务统一走
:func:`fcmd.gui.qt.workers.submit`：

- **浏览**：ModelScope 在线浏览（适合本机 / 全部 / 对话 / 编程 / 看图 / 推理 / 视频 / 音频筛选 + 排序 / 翻页）、自定义结果行，行内提供下载 / 转换按钮，展开显示详情元数据，底部右侧显示下载或转换进度；
- **量化**：GGUF 量化（输入文件、全量模型下拉、量化器环境、量化类型、输出目录、
  覆盖开关、进度与日志）。

业务逻辑全部复用 ``llmdown`` / ``llmconvert`` / ``llmqnt`` 与
:mod:`fcmd.apps.locllm._locllmw_models`、:mod:`fcmd.apps.locllm._locllmw_convert`
的纯函数；窗口只做编排。下载 / 转换参数写入 ``locllmw.json`` 的 ``download`` / ``convert`` 字段。
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
from fcmd.apps.locllm.llmbase import ModelEntry, human_size
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
    convertRequested = Signal(str)

    def __init__(  # noqa: PLR0913 - 行状态位由列表统一透传
        self,
        item: ModelSummary,
        parent: QWidget | None = None,
        *,
        installed: bool = False,
        convertible: bool = False,
        converted: bool = False,
        convert_blocked: str = "",
    ) -> None:
        super().__init__(parent)
        title, subtitle, metrics = _locllmw_models.model_item_lines(item)
        self.repo = item.repo
        self._expanded = False
        self._installed = installed
        self._convertible = convertible
        self._converted = converted
        self._convert_blocked = convert_blocked
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
        self.convert_button: Any = None
        if convertible:
            self.convert_button = button("已转换" if converted else "转换")
            if converted:
                self.convert_button.setObjectName("ConvertedButton")
                self.convert_button.setEnabled(False)
                self.convert_button.setToolTip("已存在转换后的 GGUF")
            else:
                self.convert_button.clicked.connect(lambda: self.convertRequested.emit(self.repo))
            header_row.addWidget(self.convert_button, 0)
        elif convert_blocked:
            self.convert_button = button("不可转换")
            self.convert_button.setEnabled(False)
            self.convert_button.setToolTip(convert_blocked)
            header_row.addWidget(self.convert_button, 0)
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

    def set_convert_state(self, *, busy: bool, active: bool) -> None:
        """转换中禁用按钮；已转换行保持不可点击的「已转换」。"""
        if self.convert_button is None:
            return
        if self._converted:
            self.convert_button.setEnabled(False)
            self.convert_button.setText("已转换")
            return
        if self._convert_blocked:
            self.convert_button.setEnabled(False)
            self.convert_button.setText("不可转换")
            return
        self.convert_button.setEnabled(not busy)
        self.convert_button.setText("转换中…" if active else "转换")


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
        self._convertible: dict[str, tuple[Path, Path, bool]] = {}
        self._convert_blocked: dict[str, str] = {}
        self._converting = False
        self._convert_active_repo: str | None = None

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
            self._convertible = {}
            self._convert_blocked = {}
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
            self._convertible = {}
            self._convert_blocked = {}
            for model in models:
                key = model.repo.casefold()
                if llmconvert.is_hf_safetensors_dir(model.path):
                    output = _locllmw_convert.default_output_path(model.path)
                    self._convertible[key] = (model.path, output, output.is_file())
                elif (model.path / "model_index.json").is_file():
                    self._convert_blocked[key] = (
                        "diffusers 多组件模型（model_index.json），当前仅支持单目录 HF transformers safetensors"
                    )

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
            key = item.repo.casefold()
            installed = key in self._installed_repos
            convert_info = self._convertible.get(key)
            row = _ModelItemRow(
                item,
                self.models,
                installed=installed,
                convertible=convert_info is not None,
                converted=bool(convert_info and convert_info[2]),
                convert_blocked=self._convert_blocked.get(key, ""),
            )
            row.toggleRequested.connect(self._on_row_toggle)
            row.downloadRequested.connect(self.start_download)
            row.convertRequested.connect(self.start_convert)
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
                row.set_download_state(
                    busy=self._downloading or self._converting,
                    active=self._downloading and row.repo == self._active_repo,
                )
                row.set_convert_state(
                    busy=self._downloading or self._converting,
                    active=self._converting and row.repo == self._convert_active_repo,
                )

    def start_download(self, repo: str) -> None:
        """下载指定仓库到模型目录，进度显示在列表底部右侧。"""
        if self._downloading or self._converting or not repo:
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

    def start_convert(self, repo: str) -> None:
        """把已安装的 HF safetensors 模型转换为 GGUF，进度复用底部状态区。"""
        if self._converting or self._downloading or not repo:
            return
        info = self._convertible.get(repo.casefold())
        if info is None:
            return
        source, output, converted = info
        settings = _locllmw_convert.convert_settings_from_dict(self.state.settings.convert)
        if converted or (output.is_file() and not settings.overwrite):
            self.refresh_installed()
            self._render_results()
            return
        self._converting = True
        self._convert_active_repo = repo
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"准备转换 {repo}…")
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()
        self._log(f"开始转换 {source} -> {output}")
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_convert_progress)
        form = _locllmw_convert._ConvertForm(
            source=str(source),
            output=str(output),
            outtype=settings.outtype,
            script=settings.script,
            python=settings.python,
            tag=settings.tag,
        )
        submit(
            self._convert_work,
            form,
            on_done=lambda produced: self._convert_done(repo, produced),
            on_error=lambda message: self._convert_error(repo, message),
            on_finished=self._convert_finished,
        )

    def _convert_work(self, form: _locllmw_convert._ConvertForm) -> list[str]:
        source = Path(form.source).expanduser()
        python = form.python or llmconvert.find_convert_python()
        plan = llmconvert.plan_convert(
            source,
            output=Path(form.output),
            outtype=form.outtype,
            script=Path(form.script).expanduser() if form.script else None,
            python=python,
            tag=form.tag or None,
        )
        last = -1

        def relay(line: str) -> None:
            nonlocal last
            percent = _locllmw_convert._progress_percent(line)
            if percent is None:
                self._log(line)
            elif percent >= last:
                last = percent
                self._bridge.progress.emit(percent) if self._bridge is not None else None

        return [str(path) for path in llmconvert.convert_model(plan, on_output=relay)]

    def _on_convert_progress(self, value: object) -> None:
        if isinstance(value, int):
            self.download_bar.setValue(value)
            self.download_text.setText(f"转换 {self._convert_active_repo or '-'} · {value}%")

    def _convert_done(self, repo: str, produced: list[str]) -> None:
        self.download_bar.setValue(100)
        self.download_text.setText(f"转换完成：{repo}")
        self._log(f"转换完成 {repo}: {'、'.join(produced)}")
        self.refresh_installed()
        self._render_results()

    def _convert_error(self, repo: str, message: str) -> None:
        self.download_text.setText(f"转换失败：{repo} · {message}")
        self._log(f"转换失败 {repo}: {message}")

    def _convert_finished(self) -> None:
        self._converting = False
        self._convert_active_repo = None
        self._sync_download_buttons()

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
    """模型中心的两个子页：浏览 / 量化。"""

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
        self.quant = _QuantPanel(self)
        self.tabs = QTabWidget(self)
        for name, page in zip(_TAB_NAMES, (self.browse, self.quant), strict=True):
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
        if _TAB_NAMES[index] == "量化" and not self.quant._env_loaded:
            self.quant._env_loaded = True
            self.quant.refresh_env()

    def refresh_config(self) -> None:
        """模型目录变化后刷新已安装标识与量化环境。"""
        self.browse.refresh_installed()
        if self.tabs.currentIndex() == _TAB_NAMES.index("量化"):
            self.quant.refresh_env()

    def close(self) -> bool:
        """确认并停止下载 / 转换 / 量化任务；返回 ``False`` 表示用户取消关闭。"""
        return self.browse.close()
