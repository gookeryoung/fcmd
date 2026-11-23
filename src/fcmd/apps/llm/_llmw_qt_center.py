"""llmw（Qt）模型中心：ModelScope 浏览器 / 量化两个子页。

页面口径与迁移前的窗口一致，只是把控件换成 Qt 并把后台任务统一走
:func:`fcmd.gui.workers.submit`：

- **浏览**：ModelScope 在线浏览（适合本机 / 全部 / 对话 / 编程 / 看图 / 推理 / 视频 / 音频筛选 + 排序 / 翻页）、自定义结果行，行内提供下载 / 转换按钮，展开显示详情元数据，底部右侧显示下载或转换进度；
- **量化**：GGUF 量化（输入文件、全量模型下拉、量化器环境、量化类型、输出目录、
  覆盖开关、进度与日志）。

业务逻辑全部复用 ``llmdown`` / ``llmconvert`` / ``llmqnt`` 与
:mod:`fcmd.apps.llm._llmw_models`、:mod:`fcmd.apps.llm._llmw_convert`
的纯函数；窗口只做编排。下载 / 转换参数写入 ``llmw.json`` 的 ``download`` / ``convert`` 字段。
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (  # noqa: F401
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import (
    _llmconfig,
    _llmpaint,
    _llmw_convert,
    _llmw_models,
    llmconvert,
    llmqnt,
)
from fcmd.apps.llm._llmgguf import detect_precision, quantize_input_error
from fcmd.apps.llm._llmw_import import (  # noqa: F401
    FolderInfo,
    FolderKind,
    ImportPlan,
    copy_import,
    inspect_folder,
    plan_import,
)
from fcmd.apps.llm._llmw_list_cache import ModelListCache, cache_key
from fcmd.apps.llm.llmbase import human_size
from fcmd.apps.llm.llmdown import (  # noqa: F401
    LocalModel,
    ModelFile,
    ModelInfo,
    ModelSearch,
    ModelSummary,
    ModelToolError,
    ProgressEvent,
    QuantOption,
    delete_local_model,
    describe_selection,
    download_files,
    fetch_files,
    fetch_model_info,
    filter_files,
    quant_options,
    scan_local_models,
    search_models,
    search_models_multi,
    select_source,
)
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (  # noqa: F401
    RowDetailMixin,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    hint_label,
    link_button,
    set_button_text,
    tab_widget,
)
from fcmd.gui.workers import ProgressBridge, submit
from fcmd.infras.download import SpeedMeter, format_eta, format_speed

from .qt.models_browse import ModelsBrowseMixin
from .qt.models_installed import ModelsInstalledMixin
from .qt.models_jobs import ModelsJobsMixin
from .qt.models_rows import _ModelDetailView, _ModelItemRow
from .qt.models_search import ModelsSearchMixin

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["ModelCenterPage", "quantize_menu_items"]


def quantize_menu_items(existing: Iterable[str]) -> tuple[tuple[str, str, bool], ...]:
    """量化菜单项：``(精度, 说明, 是否已量化)``；已存在的精度不再可选。

    比较按大小写不敏感；标签来自本地量化 GGUF 的文件名 / 元数据精度。
    """
    done = {tag.casefold() for tag in existing}
    return tuple((quant, desc, quant.casefold() in done) for quant, desc in llmqnt.QUANT_TYPES.items())


_SEEN_LIMIT = 600
"""浏览过程中记住的条目上限（约 20 页），避免长时间使用后内存无限增长。"""

_PAGE_SIZE = _llmw_models._PAGE_SIZE
_FILTER_DEBOUNCE_MS = 250
_DEFAULT_SORT = _llmw_models.DEFAULT_MODEL_SORT
_MODEL_SORTS = _llmw_models.MODEL_SORTS
_MODEL_FILTERS = _llmw_models.MODEL_FILTERS
_DEFAULT_FILTER = _llmw_models.DEFAULT_MODEL_FILTER

_FILTER_ALIASES = {"chat": "text", "code": "text", "reasoning": "text", "vision": "image", "paint": "image"}
"""旧分类 key → 合并后的能力域：已保存的筛选设置平滑迁移。"""

_FILTER_HINTS = {
    "text": "对话 / 编程 / 推理（文本生成能力）",
    "image": "看图理解 / 图像生成",
    "video": "视频生成",
    "audio": "语音 / 音频",
}
"""分类 chip 的 tooltip：说明合并后的能力域包含哪些细分。"""


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
    usable: set[str]
    paint_models: dict[str, Path]


@dataclass
class _DownloadJob:
    """一个进行中的仓库下载任务（支持多个仓库并行下载）。"""

    repo: str
    target: Path
    bridge: ProgressBridge
    speed: SpeedMeter
    completed: int = 0
    total: int = 0
    stop_requested: bool = False


class _BrowsePanel(
    ModelsBrowseMixin,
    ModelsInstalledMixin,
    ModelsSearchMixin,
    ModelsJobsMixin,
    RowDetailMixin,
    AppPage,
):  # pragma: no cover - Qt 控件
    """浏览页：筛选条、自定义结果行与行内展开的详情元数据。"""

    PAGE_KEY = "models"

    downloadFinished = Signal()

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, spacing=6)
        self.current_repo: str | None = None
        self.current_info: ModelInfo | None = None
        self._last_result: ModelSearch | None = None
        self._result_items: tuple[ModelSummary, ...] = ()
        self._page = 1
        view = state.settings.model_view or {}
        filter_key = view.get("filter")
        if isinstance(filter_key, str):
            filter_key = _FILTER_ALIASES.get(filter_key, filter_key)
        self._filter_key = (
            filter_key
            if isinstance(filter_key, str) and any(key == filter_key for key, _label, _q in _MODEL_FILTERS)
            else _DEFAULT_FILTER
        )
        self._filter_query = next((query for name, _label, query in _MODEL_FILTERS if name == self._filter_key), "")
        sort_key = view.get("sort")
        self._sort_key = (
            sort_key
            if isinstance(sort_key, str) and any(key == sort_key for key, _label in _MODEL_SORTS)
            else _DEFAULT_SORT
        )
        source = view.get("source")
        self._source_filter = (
            source if isinstance(source, str) and source in {"online", "installed", "all"} else "online"
        )
        owner = view.get("owner")
        self._owner_filter = owner if isinstance(owner, str) else ""
        self._group_same = view.get("group_same") is True
        self._valid_only = view.get("valid_only") is not False
        self._usable_repos: set[str] = set()
        self._detail_generation = 0
        self._searching = False
        self._pending_search: tuple[int, bool] | None = None
        self._seen_items: dict[str, ModelSummary] = {}
        self._list_cache = ModelListCache()
        self._scanning_installed = False
        self._rescan_installed = False
        self._jobs: dict[str, _DownloadJob] = {}
        self._queued: list[tuple[str, tuple[ModelFile, ...]]] = []
        self._listing_repo: str | None = None
        self._bridge: ProgressBridge | None = None
        self._installed_models: dict[str, ModelSummary] = {}
        self._installed_repos: set[str] = set()
        self._partial_models: dict[str, LocalModel] = {}
        self._convertible: dict[str, tuple[Path, Path, bool]] = {}
        self._convert_blocked: dict[str, str] = {}
        self._converting = False
        self._convert_active_repo: str | None = None
        self._convert_cancel: threading.Event | None = None
        self._quant_candidates: dict[str, tuple[Path, ...]] = {}
        self._quant_tags: dict[str, tuple[str, ...]] = {}
        self._quantized_files: dict[str, tuple[tuple[str, Path], ...]] = {}
        self._paint_models: dict[str, Path] = {}
        self._quantizing = False
        self._quant_active_repo: str | None = None
        self._pending_convert: str | None = None
        self._import_convert = False

        layout = self.body

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
            # ``_checked`` 必须有默认值：PySide2 会按「可零参调用」挑 ``clicked()`` 重载，
            # 缺省会导致点击分类时报 ``missing argument '_checked'``。
            chip.clicked.connect(lambda _checked=False, key=key: self._on_filter_changed(key))
            hint = _FILTER_HINTS.get(key)
            if hint:
                chip.setToolTip(hint)
            self.filter_group.addButton(chip)
            filters.addWidget(chip)
            self.filter_buttons[key] = chip
        filters.addStretch(1)
        self.cleanup_button = button("模型管理", kind="ghost")
        self.cleanup_button.setToolTip("查看已下载 / 未完成模型，支持多选移除与残留清理")
        self.cleanup_button.clicked.connect(self._open_model_manager)
        filters.addWidget(self.cleanup_button)
        self.update_button = button("更新模型", kind="ghost")
        self.update_button.setToolTip("强制刷新当前页并重新扫描本地模型（绕过列表缓存）")
        self.update_button.clicked.connect(self.manual_update)
        filters.addWidget(self.update_button)
        self.import_button = button("导入模型", kind="ghost")
        self.import_button.setToolTip("导入 GGUF 文件或包含可转换模型的文件夹")
        self.import_button.clicked.connect(self._on_import_clicked)
        filters.addWidget(self.import_button)
        browse_layout.addLayout(filters)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        self.name_filter = QLineEdit(self)
        self.name_filter.setPlaceholderText("按名称筛选当前页…")
        self.name_filter.setClearButtonEnabled(True)
        self.name_filter.setMinimumWidth(220)
        self.name_filter.setMaximumWidth(320)
        self.name_filter.textChanged.connect(self._on_name_filter_changed)
        nav.addWidget(self.name_filter)
        self.sort_combo = QComboBox(self)
        for key, label in _MODEL_SORTS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setCurrentIndex(max(self.sort_combo.findData(self._sort_key), 0))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        nav.addWidget(self.sort_combo)
        self.source_combo = QComboBox(self)
        for key, label in (("online", "在线"), ("installed", "已下载"), ("all", "全部来源")):
            self.source_combo.addItem(label, key)
        self.source_combo.setCurrentIndex(max(self.source_combo.findData(self._source_filter), 0))
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        nav.addWidget(self.source_combo)
        self.owner_combo = QComboBox(self)
        self.owner_combo.addItem("全部发布方", "")
        self.owner_combo.currentIndexChanged.connect(self._on_owner_changed)
        nav.addWidget(self.owner_combo)
        self.group_check = QCheckBox("同名折叠", self)
        self.group_check.setToolTip("同名仓库只保留一个代表行；取消勾选查看全部")
        self.group_check.setChecked(self._group_same)
        self.group_check.toggled.connect(self._on_group_toggled)
        nav.addWidget(self.group_check)
        self.valid_check = QCheckBox("隐藏无效", self)
        self.valid_check.setToolTip("隐藏 KB 级空壳、无可用权重且无法继续下载、或 repo 非组织/模型 的无效条目")
        self.valid_check.setChecked(self._valid_only)
        self.valid_check.toggled.connect(self._on_valid_toggled)
        nav.addWidget(self.valid_check)
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

        self.detail_view = _ModelDetailView(
            self._load_repo_files,
            self._download_selected_files,
            local_dir=self._repo_local_dir,
            parent=self,
        )
        self.detail_view.heightChanged.connect(self._on_detail_height_changed)
        self.detail_view.setVisible(False)
        self.bind_row_detail("models", self.models, self.detail_view)

        self.refresh_installed()
        self.filter_buttons[self._filter_key].setChecked(True)
        self.start_search(1)
        self.state.busyChanged.connect(lambda _busy: self._sync_download_buttons())
        self.state.servicesChanged.connect(lambda _states: self._sync_download_buttons())

    # -- 筛选 ---------------------------------------------------------- #

    # -- 加载提示 ------------------------------------------------------ #

    # -- 导入 ---------------------------------------------------------- #

    # -- 详情 ---------------------------------------------------------- #

    # -- 下载 ---------------------------------------------------------- #

    # -- 快捷启动 ------------------------------------------------------ #

    def close(self) -> bool:
        """关窗前确认进行中的下载 / 转换，返回 ``False`` 表示用户取消关闭。"""
        if self._converting:
            if not confirm(self, "转换进行中", "转换仍在进行，关闭窗口将停止转换（已生成部分保留）。确实关闭？"):
                return False
            if self._convert_cancel is not None:
                self._convert_cancel.set()
            return True
        if not self._jobs:
            return True
        if not confirm(
            self,
            "下载进行中",
            f"还有 {len(self._jobs)} 个模型在下载，关闭窗口将停止下载（已下载部分保留为 .part）。确实关闭？",
        ):
            return False
        for job in self._jobs.values():
            job.stop_requested = True
        self._queued.clear()
        return True


class ModelCenterPage(AppPage):  # pragma: no cover - Qt 控件
    """模型中心：ModelScope 浏览与行内下载 / 转换 / 量化。"""

    PAGE_KEY = "models"

    def __init__(
        self,
        state: LlmState,
        parent: QWidget | None = None,
        *,
        fit_resolver: Any = None,
    ) -> None:
        super().__init__(state, parent)
        del fit_resolver
        self.browse = _BrowsePanel(state, self)
        self.body.addWidget(self.browse)

    def refresh_config(self) -> None:
        """模型目录变化后刷新已安装标识与量化 / 转换状态。"""
        self.browse.refresh_installed()

    def close(self) -> bool:
        """确认并停止下载 / 转换 / 量化任务；返回 ``False`` 表示用户取消关闭。"""
        return self.browse.close()


_REEXPORTS = (
    FolderKind,
    ModelSearch,
    ModelSummary,
    ModelToolError,
    Path,
    ProgressBridge,
    QCursor,
    QFileDialog,
    QInputDialog,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSize,
    Qt,
    SpeedMeter,
    _DownloadJob,
    _InstalledSnapshot,
    _MODEL_FILTERS,
    _ModelItemRow,
    _PAGE_SIZE,
    _SEEN_LIMIT,
    _llmconfig,
    _llmpaint,
    _llmw_convert,
    _llmw_models,
    cache_key,
    copy_import,
    delete_local_model,
    describe_selection,
    detect_precision,
    download_files,
    fetch_files,
    fetch_model_info,
    filter_files,
    format_eta,
    format_speed,
    human_size,
    inspect_folder,
    llmconvert,
    llmqnt,
    math,
    plan_import,
    quant_options,
    quantize_input_error,
    quantize_menu_items,
    scan_local_models,
    search_models,
    search_models_multi,
    select_source,
    submit,
    threading,
)
