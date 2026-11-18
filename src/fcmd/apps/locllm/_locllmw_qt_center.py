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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import (
    _llmconfig,
    _locllmw_convert,
    _locllmw_models,
    llmconvert,
    llmqnt,
)
from fcmd.apps.locllm._llmgguf import detect_precision, quantize_input_error
from fcmd.apps.locllm._locllmw_list_cache import ModelListCache, cache_key
from fcmd.apps.locllm.llmbase import human_size
from fcmd.apps.locllm.llmdown import (
    LocalModel,
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
    ElidedLabel,
    RowListWidget,
    button,
    hint_label,
    link_button,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["ModelCenterPage", "quantize_menu_items"]


def quantize_menu_items(existing: Iterable[str]) -> tuple[tuple[str, str, bool], ...]:
    """量化菜单项：``(精度, 说明, 是否已量化)``；已存在的精度不再可选。

    比较按大小写不敏感；标签来自本地量化 GGUF 的文件名 / 元数据精度。
    """
    done = {tag.casefold() for tag in existing}
    return tuple((quant, desc, quant.casefold() in done) for quant, desc in llmqnt.QUANT_TYPES.items())


_PAGE_SIZE = _locllmw_models._PAGE_SIZE
_FILTER_DEBOUNCE_MS = 250
_MODEL_FILTERS = _locllmw_models.MODEL_FILTERS
_DEFAULT_FILTER = _locllmw_models.DEFAULT_MODEL_FILTER


class _ProgressBridge(QObject):  # pragma: no cover - Qt 信号桥
    """下载进度信号（工作线程 → 主线程）。"""

    progress = Signal(object)


@dataclass(frozen=True)
class _InstalledSnapshot:
    """一次本地模型目录扫描的结果（工作线程算好，GUI 线程一次性套用）。"""

    models: dict[str, ModelSummary]
    repos: set[str]
    partial: dict[str, LocalModel]
    convertible: dict[str, tuple[Path, Path, bool]]
    blocked: dict[str, str]
    quant_candidates: dict[str, tuple[Path, ...]]
    quant_tags: dict[str, tuple[str, ...]]
    quantized: dict[str, tuple[tuple[str, Path], ...]]


class _ModelItemRow(QFrame):  # pragma: no cover - Qt 控件
    """浏览结果行：与概览推荐行同构（大标题 + 小字副标题 + 右侧关键数值）。

    行本身只放关键内容；展开后在行下方显示详细信息，不再占用
    右侧面板，列表可以铺满整页宽度。
    """

    toggleRequested = Signal(object)
    downloadRequested = Signal(str)
    convertRequested = Signal(str)
    quantizeRequested = Signal(str)
    startRequested = Signal(str)

    def __init__(  # noqa: PLR0912, PLR0913 - 行状态位由列表统一透传
        self,
        item: ModelSummary,
        parent: QWidget | None = None,
        *,
        installed: bool = False,
        convertible: bool = False,
        converted: bool = False,
        convert_blocked: str = "",
        quant_tags: tuple[str, ...] = (),
        quantizable: bool = False,
        quant_blocked: bool = False,
        startable: bool = False,
        default_quant: str = "",
        serve_path: Path | None = None,
        incomplete: bool = False,
        progress_text: str = "",
    ) -> None:
        super().__init__(parent)
        title, subtitle, metrics = _locllmw_models.model_item_lines(item)
        self.repo = item.repo
        self.serve_path = serve_path
        self._expanded = False
        self._installed = installed
        self._convertible = convertible
        self._converted = converted
        self._convert_blocked = convert_blocked
        self._quantizable = quantizable
        self._quant_blocked = quant_blocked
        self._incomplete = incomplete
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
        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(6)
        sub = QLabel(subtitle, header)
        sub.setObjectName("Hint")
        sub.setWordWrap(False)
        sub.setMinimumWidth(0)
        sub.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sub.setToolTip(subtitle)
        note_row.addWidget(sub, 0)
        for tag in quant_tags:
            chip = QLabel(tag, header)
            if tag == default_quant:
                chip.setText(f"{tag} · 上次启动")
                chip.setToolTip("快捷启动默认使用该量化精度（记住用户上次启动过的精度）")
            else:
                chip.setToolTip(f"已量化精度 {tag}")
            chip.setObjectName("QuantTag")
            note_row.addWidget(chip, 0)
        if incomplete and progress_text:
            progress = QLabel(progress_text, header)
            progress.setObjectName("DownloadProgress")
            progress.setToolTip(progress_text)
            note_row.addWidget(progress, 0)
        note_row.addStretch(1)
        left.addLayout(note_row)
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
        self.quant_button: Any = None
        if quantizable:
            self.quant_button = button("量化")
            self.quant_button.clicked.connect(lambda: self.quantizeRequested.emit(self.repo))
            header_row.addWidget(self.quant_button, 0)
        elif quant_blocked:
            self.quant_button = button("不可量化")
            self.quant_button.setEnabled(False)
            self.quant_button.setToolTip("当前模型没有可量化的全量精度 GGUF")
            header_row.addWidget(self.quant_button, 0)
        self.start_button: Any = None
        if startable:
            self.start_button = button("启动服务", kind="primary")
            self.start_button.setToolTip("选择量化精度并启动服务；默认使用上次启动过的精度")
            self.start_button.clicked.connect(lambda: self.startRequested.emit(self.repo))
            header_row.addWidget(self.start_button, 0)
        self.download_button = button("继续下载" if incomplete else "下载")
        if installed and not incomplete:
            self.download_button.setObjectName("InstalledButton")
            self.download_button.setText("✓ 已安装")
            self.download_button.setEnabled(False)
            self.download_button.setToolTip("该模型已在本地模型目录中")
        else:
            if incomplete:
                self.download_button.setToolTip("继续下载未完成的文件（.part 保留）")
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
        self.download_button.setText("下载中…" if active else ("继续下载" if self._incomplete else "下载"))

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

    def set_quant_state(self, *, busy: bool, active: bool) -> None:
        """量化中禁用按钮；无候选时保持不可量化的禁用态。"""
        if self.quant_button is None:
            return
        if self._quant_blocked:
            self.quant_button.setEnabled(False)
            self.quant_button.setText("不可量化")
            return
        self.quant_button.setEnabled(not busy)
        self.quant_button.setText("量化中…" if active else "量化")

    def set_serve_state(self, *, busy: bool, running: bool) -> None:
        """快捷启动按钮：任务进行中禁用，已运行时显示「已启动」。"""
        if self.start_button is None:
            return
        self.start_button.setEnabled(not busy and not running)
        self.start_button.setText("已启动" if running else "启动服务")


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
        self._list_cache = ModelListCache()
        self._scanning_installed = False
        self._downloading = False
        self._stop_requested = False
        self._active_repo: str | None = None
        self._bridge: _ProgressBridge | None = None
        self._download_target = _llmconfig.models_dir()
        self._installed_models: dict[str, ModelSummary] = {}
        self._installed_repos: set[str] = set()
        self._partial_models: dict[str, LocalModel] = {}
        self._convertible: dict[str, tuple[Path, Path, bool]] = {}
        self._convert_blocked: dict[str, str] = {}
        self._converting = False
        self._convert_active_repo: str | None = None
        self._quant_candidates: dict[str, tuple[Path, ...]] = {}
        self._quant_tags: dict[str, tuple[str, ...]] = {}
        self._quantized_files: dict[str, tuple[tuple[str, Path], ...]] = {}
        self._quantizing = False
        self._quant_active_repo: str | None = None

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
        self.name_filter = QLineEdit(self)
        self.name_filter.setPlaceholderText("按名称筛选当前页…")
        self.name_filter.setClearButtonEnabled(True)
        self.name_filter.setMaximumWidth(260)
        self.name_filter.textChanged.connect(self._on_name_filter_changed)
        nav.addWidget(self.name_filter)
        nav.addStretch(1)
        self._name_filter = ""
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_FILTER_DEBOUNCE_MS)
        self._filter_timer.timeout.connect(self._apply_name_filter)
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
        self.state.busyChanged.connect(lambda _busy: self._sync_download_buttons())
        self.state.servicesChanged.connect(lambda _states: self._sync_download_buttons())

    # -- 筛选 ---------------------------------------------------------- #
    def _on_filter_changed(self, key: str) -> None:
        """切换浏览筛选：分类指标走服务端关键字，适合本机在本地按容量过滤。"""
        self._filter_key = key
        self._filter_query = next((query for name, _label, query in _MODEL_FILTERS if name == key), "")
        self.start_search(1)

    def _on_name_filter_changed(self, _text: str) -> None:
        """名称筛选输入防抖：停止输入 250ms 后才重建列表。"""
        self._filter_timer.start()

    def _apply_name_filter(self) -> None:
        text = self.name_filter.text().strip()
        if text == self._name_filter:
            return
        self._name_filter = text
        self._render_results()

    def refresh_installed(self) -> None:
        """后台扫描模型目录，记录已安装仓库（用于勾选标志与置顶）。

        扫描可能遍历大量 GGUF 头与转换产物，放到线程池执行；完成后一次性
        套用快照并重绘当前结果，切页 / 下载完成时界面不会被同步扫描卡住。
        """
        if self._scanning_installed:
            return
        self._scanning_installed = True
        self._begin_loading("installed", "正在扫描本地模型…")
        submit(
            self._scan_installed,
            on_done=self._apply_installed,
            on_error=self._installed_error,
            on_finished=self._installed_finished,
        )

    @staticmethod
    def _scan_installed() -> _InstalledSnapshot:  # noqa: PLR0912 - 一次扫描归集安装 / 转换 / 量化状态
        """在线程池里扫描本地模型目录（不碰任何 Qt 控件）。"""
        try:
            models = scan_local_models(_llmconfig.models_dir())
        except OSError:
            return _InstalledSnapshot({}, set(), {}, {}, {}, {}, {}, {})
        installed = {
            model.repo.casefold(): ModelSummary(
                repo=model.repo,
                name=model.repo.rsplit("/", 1)[-1],
                storage_size=model.size,
            )
            for model in models
        }
        partial = {model.repo.casefold(): model for model in models if not model.complete}
        convertible: dict[str, tuple[Path, Path, bool]] = {}
        blocked: dict[str, str] = {}
        quant_candidates: dict[str, tuple[Path, ...]] = {}
        quant_tags: dict[str, tuple[str, ...]] = {}
        quantized: dict[str, list[tuple[str, Path]]] = {}
        try:
            product_ggufs = sorted(_llmconfig.products_dir().glob("*.gguf"))
        except OSError:
            product_ggufs = []
        for model in models:
            key = model.repo.casefold()
            if llmconvert.is_hf_safetensors_dir(model.path):
                output = _locllmw_convert.default_output_path(model.path)
                convertible[key] = (model.path, output, output.is_file())
            elif (model.path / "model_index.json").is_file():
                blocked[key] = "diffusers 多组件模型（model_index.json），当前仅支持单目录 HF transformers safetensors"
            full: list[Path] = []
            tags: list[str] = []
            for gguf in sorted(model.path.glob("*.gguf")):
                precision = detect_precision(gguf, tensors=False)
                if precision.quantized is True:
                    label = precision.filename_label or precision.label.removesuffix(" 量化")
                    if label not in tags:
                        tags.append(label)
                    entries = quantized.setdefault(key, [])
                    if all(existing != label for existing, _path in entries):
                        entries.append((label, gguf))
                elif quantize_input_error(precision) is None:
                    full.append(gguf)
            bases = {llmqnt.strip_f16_suffix(path.stem) for path in full}
            for product in product_ggufs:
                if not bases or not any(product.stem.startswith(base + "-") for base in bases):
                    continue
                precision = detect_precision(product, tensors=False)
                if precision.quantized is True:
                    label = precision.filename_label or precision.label.removesuffix(" 量化")
                    if label not in tags:
                        tags.append(label)
                    entries = quantized.setdefault(key, [])
                    if all(existing != label for existing, _path in entries):
                        entries.append((label, product))
            if full:
                quant_candidates[key] = tuple(full)
            if tags:
                quant_tags[key] = tuple(tags)
        return _InstalledSnapshot(
            installed,
            set(installed),
            partial,
            convertible,
            blocked,
            quant_candidates,
            quant_tags,
            {repo: tuple(entries) for repo, entries in quantized.items()},
        )

    def _apply_installed(self, snapshot: _InstalledSnapshot) -> None:
        """套用扫描结果；若已有结果行则就地重绘已安装 / 可转换 / 可量化标志。"""
        self._installed_models = snapshot.models
        self._installed_repos = snapshot.repos
        self._partial_models = snapshot.partial
        self._convertible = snapshot.convertible
        self._convert_blocked = snapshot.blocked
        self._quant_candidates = snapshot.quant_candidates
        self._quant_tags = snapshot.quant_tags
        self._quantized_files = snapshot.quantized
        if self._last_result is not None and not self._searching:
            self._render_results()

    def _installed_error(self, message: str) -> None:
        self._log(f"扫描本地模型失败: {message}")

    def _installed_finished(self) -> None:
        self._scanning_installed = False
        self._end_loading("installed")

    # -- 加载提示 ------------------------------------------------------ #
    def _begin_loading(self, key: str, text: str) -> None:
        """登记一项加载任务；同一时刻只显示一个加载条，任务结束才收起。"""
        self.state.begin_loading(key, text)

    def _end_loading(self, key: str) -> None:
        self.state.end_loading(key)

    def _default_quant(self, repo_key: str) -> str:
        """快捷启动默认精度：持久化的上次启动精度 → 上次运行模型 → 首个量化文件。"""
        files = self._quantized_files.get(repo_key, ())
        if not files:
            return ""
        labels = [label for label, _path in files]
        saved = (self.state.settings.quant_choice or {}).get(repo_key)
        if saved in labels:
            return saved
        last = self.state.settings.last_model
        if last:
            last_key = _llmconfig.path_key(Path(last))
            for label, path in files:
                if _llmconfig.path_key(path) == last_key:
                    return label
        return labels[0]

    def _local_capacity(self) -> int:
        """本机可承载的整仓体积（显存与系统内存取较大者，未知时为 0）。"""
        hardware = getattr(self.state, "hardware", None)
        gpu_mib = getattr(self.state, "gpu_mib", None) or (hardware.gpu_mib if hardware is not None else None)
        memory = hardware.memory_total if hardware is not None else 0
        gpu_bytes = (gpu_mib or 0) * 1024**2
        memory_bytes = int(memory * 0.8) if memory else 0
        return max(gpu_bytes, memory_bytes)

    def start_search(self, page: int) -> None:
        """按当前筛选拉取指定页；命中新鲜缓存时直接展示，不再请求网络。"""
        if self._searching:
            return
        target = max(1, page)
        key = cache_key(self._filter_query, target, _PAGE_SIZE, "default")
        cached = self._list_cache.get(key)
        self._page = target
        if cached is not None:
            self._render_search(cached.search)
            if cached.fresh:
                self._log(f"模型列表命中缓存（{cached.age:.0f}s 前）：{self._filter_query or '全部'}")
                return
        self._searching = True
        text = f"正在加载第 {self._page} 页…" if cached is None else f"正在刷新第 {self._page} 页…"
        self.state.status(text)
        self._begin_loading("search", text)
        submit(
            self._search_work,
            self._list_cache,
            key,
            self._filter_query,
            self._page,
            _PAGE_SIZE,
            "default",
            on_done=self._render_search,
            on_error=self._search_error,
            on_finished=self._search_finished,
        )

    @staticmethod
    def _search_work(  # noqa: PLR0913, PLR0917 - 缓存键与请求参数成组透传
        cache: ModelListCache,
        key: str,
        keyword: str,
        page: int,
        page_size: int,
        sort: str,
    ) -> ModelSearch:
        """后台线程：请求网络并把结果写入缓存，避免 GUI 线程落盘。"""
        result = search_models(keyword, page, page_size, sort)
        cache.put(key, result)
        return result

    def _search_finished(self) -> None:
        self._searching = False
        self._end_loading("search")

    def _search_error(self, message: str) -> None:
        self.state.status("模型列表加载失败")
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
        items = _locllmw_models.filter_by_name(items, self._name_filter)
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
            partial = self._partial_models.get(key)
            incomplete = partial is not None
            progress_text = ""
            if partial is not None:
                total = (self.state.settings.download_totals or {}).get(key)
                if not total and item.storage_size > partial.size:
                    total = item.storage_size
                if total and total > 0:
                    percent = min(99, max(0, round(partial.size * 100 / total)))
                    progress_text = f"已下载 {percent}%"
                else:
                    progress_text = f"已下载 {human_size(partial.size)}"
            convert_info = self._convertible.get(key)
            quantizable = key in self._quant_candidates
            quantized = self._quantized_files.get(key, ())
            default_quant = self._default_quant(key)
            serve_path = next((path for label, path in quantized if label == default_quant), None)
            if serve_path is None and quantized:
                serve_path = quantized[0][1]
            row = _ModelItemRow(
                item,
                self.models,
                installed=installed and not incomplete,
                convertible=convert_info is not None,
                converted=bool(convert_info and convert_info[2]),
                convert_blocked=self._convert_blocked.get(key, ""),
                quant_tags=self._quant_tags.get(key, ()),
                quantizable=quantizable,
                quant_blocked=installed and not quantizable,
                startable=bool(quantized),
                default_quant=default_quant,
                serve_path=serve_path,
                incomplete=incomplete,
                progress_text=progress_text,
            )
            row.toggleRequested.connect(self._on_row_toggle)
            row.downloadRequested.connect(self.start_download)
            row.convertRequested.connect(self.start_convert)
            row.quantizeRequested.connect(self._on_quantize_requested)
            row.startRequested.connect(self._on_quick_serve)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.models.setItemWidget(list_item, row)
        self.models.sync_row_widths()
        self._sync_download_buttons()
        pages = max(1, math.ceil(result.total / result.page_size)) if result.total else 1
        matched = f" · 名称筛选 {len(items)} 条" if self._name_filter else ""
        self.state.status(f"{filter_label} · {result.page}/{pages} · 共 {result.total:,} 条{matched}")
        self.prev_button.setEnabled(result.page > 1)
        self.next_button.setEnabled(result.page < pages)
        if selected is not None:
            for row, item in enumerate(items):
                if item.repo == selected:
                    self.models.setCurrentRow(row)
                    break
        if not items:
            self._log("当前页没有匹配的模型，可切换筛选或翻页")

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
        self._log(f"详情加载失败 {repo}: {message}")

    def _render_detail(self, generation: int, info: ModelInfo) -> None:
        if generation != self._detail_generation:
            return
        self.current_info = info
        self.detail_view.render(info)
        self._update_row_height(self._expanded_row)
        self._log(f"已加载模型详情: {info.summary.repo}")

    def _log(self, message: str) -> None:
        self.state.logLine.emit(message)

    # -- 下载 ---------------------------------------------------------- #
    def _sync_download_buttons(self) -> None:
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _ModelItemRow):
                busy = self._downloading or self._converting or self._quantizing
                row.set_download_state(
                    busy=busy,
                    active=self._downloading and row.repo == self._active_repo,
                )
                row.set_convert_state(
                    busy=busy,
                    active=self._converting and row.repo == self._convert_active_repo,
                )
                row.set_quant_state(
                    busy=busy,
                    active=self._quantizing and row.repo == self._quant_active_repo,
                )
                running = row.serve_path is not None and self.state.is_running(row.serve_path)
                row.set_serve_state(busy=busy or self.state.busy, running=running)

    def start_download(self, repo: str) -> None:
        """下载指定仓库到模型目录，进度显示在列表底部右侧。"""
        if self._downloading or self._converting or self._quantizing or not repo:
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
        if self._active_repo and event.total > 0:
            totals = dict(self.state.settings.download_totals or {})
            totals[self._active_repo.casefold()] = event.total
            self.state.settings.download_totals = totals
        self.download_text.setText(
            f"下载 {self._active_repo or '-'} · {human_size(event.completed)} / {human_size(event.total)}"
        )

    def start_convert(self, repo: str) -> None:
        """把已安装的 HF safetensors 模型转换为 GGUF，进度复用底部状态区。"""
        if self._converting or self._downloading or self._quantizing or not repo:
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

    # -- 快捷启动 ------------------------------------------------------ #
    def _on_quick_serve(self, repo: str) -> None:
        """选择量化精度后快捷启动；只有一个精度时直接启动。"""
        if self._quantizing or self._downloading or self._converting:
            return
        files = self._quantized_files.get(repo.casefold(), ())
        if not files:
            return
        key = repo.casefold()
        if len(files) == 1:
            self._request_serve(files[0][1], key)
            return
        default = self._default_quant(key)
        menu = QMenu(self)
        for label, path in files:
            suffix = "（上次启动）" if label == default else ""
            action = menu.addAction(f"{label}{suffix}  {path.name}")
            action.triggered.connect(lambda _checked=False, target=path: self._request_serve(target, key))
        menu.exec(QCursor.pos())

    def _request_serve(self, path: Path, repo_key: str = "") -> None:
        """请求窗口切到运行页并启动指定量化模型，同时记住该仓库的精度选择。"""
        if repo_key:
            label = _locllmw_models.quant_precision_label(path)
            if label:
                choices = dict(self.state.settings.quant_choice or {})
                choices[repo_key] = label
                self.state.settings.quant_choice = choices
                self.state.persist()
        self._log(f"快捷启动: {path}")
        self.state.status(f"正在准备启动 {path.name}…")
        self.state.request_serve_model(path)

    def _on_quantize_requested(self, repo: str) -> None:
        """点击「量化」后弹出精度选项，选中后立即开始单精度量化。"""
        if self._quantizing or self._downloading or self._converting:
            return
        if repo.casefold() not in self._quant_candidates:
            return
        menu = QMenu(self)
        existing = self._quant_tags.get(repo.casefold(), ())
        for quant, desc, done in quantize_menu_items(existing):
            if done:
                action = menu.addAction(f"{quant}  {desc}（已量化）")
                action.setCheckable(True)
                action.setChecked(True)
                action.setEnabled(False)
                continue
            action = menu.addAction(f"{quant}  {desc}")
            action.triggered.connect(lambda _checked=False, quant=quant, repo=repo: self.start_quantize(repo, quant))
        menu.exec(QCursor.pos())

    def start_quantize(self, repo: str, quant: str) -> None:
        """对指定仓库中最大的全量 GGUF 执行一次量化。"""
        if self._quantizing or self._downloading or self._converting:
            return
        if quant.casefold() in {tag.casefold() for tag in self._quant_tags.get(repo.casefold(), ())}:
            self.state.status(f"{repo} · {quant} 已量化，无需重复执行")
            self._log(f"跳过重复量化 {repo}: {quant} 已存在")
            return
        files = self._quant_candidates.get(repo.casefold())
        if not files:
            return
        source = max(files, key=lambda path: path.stat().st_size if path.is_file() else 0)
        try:
            exe = llmqnt.resolve_quantize_exe()
            jobs = llmqnt.plan_jobs(source, [quant])
            pending, _skipped = llmqnt.pending_jobs(jobs)
        except (OSError, llmqnt.QuantizeError) as exc:
            self.download_status.setVisible(True)
            self.download_text.setText(f"无法量化：{repo} · {exc}")
            self._log(f"无法量化 {repo}: {exc}")
            return
        if not pending:
            self.download_status.setVisible(True)
            self.download_bar.setValue(100)
            self.download_text.setText(f"已存在：{repo} · {quant}")
            self.refresh_installed()
            self._render_results()
            return
        self._quantizing = True
        self._quant_active_repo = repo
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"准备量化 {repo} · {quant}…")
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()
        self._log(f"开始量化 {source} -> {quant}")
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_quant_progress)
        submit(
            self._quant_work,
            pending,
            exe,
            on_done=lambda success: self._quant_done(repo, success),
            on_error=lambda message: self._quant_error(repo, message),
            on_finished=self._quant_finished,
        )

    def _quant_work(self, pending: list[Any], exe: Path) -> bool:
        success = True
        total = len(pending)
        for index, job in enumerate(pending, 1):
            try:
                llmqnt.run_job(job, exe, on_output=self._log)
            except llmqnt.QuantizeError as exc:
                success = False
                self._log(f"量化 {job.quant} 失败: {exc}")
            else:
                self._log(f"已生成: {job.output}")
            if self._bridge is not None:
                self._bridge.progress.emit(index * 100 // total)
        return success

    def _on_quant_progress(self, value: object) -> None:
        if isinstance(value, int):
            self.download_bar.setValue(value)
            self.download_text.setText(f"量化 {self._quant_active_repo or '-'} · {value}%")

    def _quant_done(self, repo: str, success: bool) -> None:
        self.download_bar.setValue(100 if success else self.download_bar.value())
        self.download_text.setText(f"量化{'完成' if success else '结束'}：{repo}")
        self._log(f"量化{'完成' if success else '结束'} {repo}")
        self.refresh_installed()
        self._render_results()

    def _quant_error(self, repo: str, message: str) -> None:
        self.download_text.setText(f"量化失败：{repo} · {message}")
        self._log(f"量化失败 {repo}: {message}")

    def _quant_finished(self) -> None:
        self._quantizing = False
        self._quant_active_repo = None
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
        self.state.persist()
        # 成功 / 失败 / 手动停止都会走到这里：重新扫描 .part 大小并刷新进度标签。
        self.refresh_installed()
        self._render_results()
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


class ModelCenterPage(QWidget):  # pragma: no cover - Qt 控件
    """模型中心：ModelScope 浏览与行内下载 / 转换 / 量化。"""

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browse)

    def refresh_config(self) -> None:
        """模型目录变化后刷新已安装标识与量化 / 转换状态。"""
        self.browse.refresh_installed()

    def close(self) -> bool:
        """确认并停止下载 / 转换 / 量化任务；返回 ``False`` 表示用户取消关闭。"""
        return self.browse.close()
