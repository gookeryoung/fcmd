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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
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
from fcmd.gui.widgets import (
    ItemRow,
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


class _ModelItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """浏览结果行：共享 :class:`ItemRow` 骨架 + 模型自己的标签与行内动作。

    标题是仓库名，副标题是参数 / 体积，右侧是数值摘要与下载 / 转换 / 量化 /
    快捷启动按钮；发布方、发布时间、量化精度与状态胶囊都往共享骨架的插槽里加。
    行本身只放关键内容，展开后在行下方显示详细信息，列表可以铺满整页宽度。
    """

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
        paint_startable: bool = False,
        default_quant: str = "",
        serve_path: Path | None = None,
        downloadable: bool = True,
        incomplete: bool = False,
        owner: str = "",
        size_text: str | None = None,
        same_name: int = 1,
        progress_text: str = "",
    ) -> None:
        title, subtitle, metrics = _llmw_models.model_item_lines(item)
        if size_text:
            metrics = size_text
        super().__init__(
            title,
            subtitle,
            elide=Qt.TextElideMode.ElideMiddle,
            expand_hint="展开详细信息",
            collapse_hint="收起详细信息",
            # 标题显示的是模型名（省略在行内），复制按钮给完整仓库名（组织/模型）。
            copy_text=item.repo,
            parent=parent,
        )
        self.repo = item.repo
        self.serve_path = serve_path
        self._downloadable = downloadable
        self._convertible = convertible
        self._converted = converted
        self._convert_blocked = convert_blocked
        self._quantizable = quantizable
        self._quant_blocked = quant_blocked
        self._incomplete = incomplete
        self.setToolTip(item.repo)
        self.title_label.setToolTip(item.repo)

        if owner:
            owner_label = QLabel(owner, self.header)
            owner_label.setObjectName("OwnerTag")
            owner_label.setToolTip(f"发布方 {owner}")
            self.add_title_widget(owner_label)
        published = _llmw_models.model_published_text(item)
        if published:
            published_label = QLabel(published, self.header)
            published_label.setObjectName("Hint")
            published_label.setToolTip(f"模型发布时间：{_llmw_models.format_timestamp(item.updated)}")
            self.add_note_widget(published_label)
        if installed and not incomplete:
            self.add_tag(StatusTag("已安装", tone="success", parent=self.header))
        elif incomplete:
            self.add_tag(StatusTag("部分下载", tone="warning", parent=self.header))
        if not downloadable:
            self.add_tag(StatusTag("仅本地", tone="muted", parent=self.header))
        if any(tag.upper().startswith("UD-") for tag in quant_tags):
            self.add_tag(StatusTag("UD", tone="accent", parent=self.header))
        if same_name > 1:
            self.add_tag(StatusTag(f"同名 {same_name}", tone="muted", parent=self.header))
        if converted:
            self.add_tag(StatusTag("已转换", tone="success", parent=self.header))
        elif convert_blocked:
            blocked_tag = StatusTag("不可转换", tone="warning", parent=self.header)
            blocked_tag.setToolTip(convert_blocked)
            self.add_tag(blocked_tag)
        if quant_blocked:
            self.add_tag(StatusTag("不可量化", tone="warning", parent=self.header))
        visible_tags = list(quant_tags)
        if default_quant in visible_tags:
            visible_tags.remove(default_quant)
            visible_tags.insert(0, default_quant)
        for tag in visible_tags[:4]:
            chip = QLabel(tag, self.header)
            if tag == default_quant:
                chip.setText(f"{tag} · 上次启动")
                chip.setToolTip("快捷启动默认使用该量化精度（记住用户上次启动过的精度）")
            else:
                chip.setToolTip(f"已量化精度 {tag}")
            chip.setObjectName("QuantTag")
            self.add_tag(chip)
        hidden_tags = visible_tags[4:]
        if hidden_tags:
            hidden_chip = QLabel(f"+{len(hidden_tags)}", self.header)
            hidden_chip.setObjectName("QuantTag")
            hidden_chip.setToolTip("其他量化：" + "、".join(hidden_tags))
            self.add_tag(hidden_chip)
        if incomplete and progress_text:
            progress = QLabel(progress_text, self.header)
            progress.setObjectName("DownloadProgress")
            progress.setToolTip(progress_text)
            self.add_tag(progress)

        self.set_metrics(metrics)
        self.convert_button: Any = None
        if convertible and not converted:
            # 次要动作（转换 / 量化）用 Ghost，主操作（启动服务 / 下载）保持填充色，
            # 一行有多个按钮时不会互相抢焦点。
            self.convert_button = button("转换", kind="ghost")
            self.convert_button.clicked.connect(lambda: self.convertRequested.emit(self.repo))
            self.add_action(self.convert_button)
        self.quant_button: Any = None
        if quantizable:
            self.quant_button = button("量化", kind="ghost")
            self.quant_button.clicked.connect(lambda: self.quantizeRequested.emit(self.repo))
            self.add_action(self.quant_button)
        self.start_button: Any = None
        if startable:
            self.start_button = button("启动服务", kind="primary")
            self.start_button.setToolTip(
                "启动画图服务（sd.cpp）：切到运行页「画图」并加载该扩散模型"
                if paint_startable
                else "选择量化精度并启动服务；默认使用上次启动过的精度"
            )
            self.start_button.clicked.connect(lambda: self.startRequested.emit(self.repo))
            self.add_action(self.start_button)
        self.download_button: Any = None
        # 已安装且无 .part 的模型视为已全部下载完成：不再显示下载按钮。
        if downloadable and not (installed and not incomplete):
            self.download_button = button("继续下载" if incomplete else "下载")
            if incomplete:
                self.download_button.setToolTip("继续下载未完成的文件（.part 保留）")
            self.download_button.clicked.connect(lambda: self.downloadRequested.emit(self.repo))
            self.add_action(self.download_button)

    @property
    def downloadable(self) -> bool:
        """能否对应到远程仓库；仅本地条目为 False，详情不读在线清单。"""
        return self._downloadable

    def set_download_state(self, *, busy: bool, active: bool, queued: bool = False) -> None:
        """下载中禁用；未完成显示「继续下载」，未安装显示「下载」，排队时显示「排队中…」。"""
        if self.download_button is None:
            return
        self.download_button.setEnabled(not busy)
        if active:
            text = "下载中…"
        elif queued:
            text = "排队中…"
        else:
            text = "继续下载" if self._incomplete else "下载"
        set_button_text(self.download_button, text)

    def set_convert_state(self, *, busy: bool, active: bool) -> None:
        """转换中禁用按钮；已转换行保持不可点击的「已转换」。"""
        if self.convert_button is None:
            return
        if self._converted:
            self.convert_button.setEnabled(False)
            set_button_text(self.convert_button, "已转换", tone="success")
            return
        if self._convert_blocked:
            self.convert_button.setEnabled(False)
            set_button_text(self.convert_button, "不可转换")
            return
        self.convert_button.setEnabled(not busy)
        set_button_text(self.convert_button, "转换中…" if active else "转换")

    def set_quant_state(self, *, busy: bool, active: bool) -> None:
        """量化中禁用按钮；无候选时保持不可量化的禁用态。"""
        if self.quant_button is None:
            return
        if self._quant_blocked:
            self.quant_button.setEnabled(False)
            set_button_text(self.quant_button, "不可量化")
            return
        self.quant_button.setEnabled(not busy)
        set_button_text(self.quant_button, "量化中…" if active else "量化")

    def set_serve_state(self, *, busy: bool, running: bool) -> None:
        """快捷启动按钮：任务进行中禁用，已运行时显示「已启动」。

        禁用态的主按钮底色会变成浅色（``$surface_hover``），图标必须同步换成
        ``muted``：否则浅色主题下保留下来的 ``on_accent`` 白勾在白底上几乎看不见。
        """
        if self.start_button is None:
            return
        enabled = not busy and not running
        self.start_button.setEnabled(enabled)
        set_button_text(
            self.start_button,
            "已启动" if running else "启动服务",
            tone="on_accent" if enabled else "muted",
        )


class _ModelDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的详情：「详细信息」与「文件列表」两个页签。

    同一个实例在行之间复用，展开哪一行就挂到哪一行下面；元数据先展示，文件
    清单在第一次切到「文件列表」时才后台读取（仓库文件可能上百个，不拖慢展开）。
    ``heightChanged`` 供列表在页签高度变化时重算展开行高度。
    """

    heightChanged = Signal()

    def __init__(
        self,
        load_files: Callable[[str], list[ModelFile]] | None = None,
        download_files: Callable[[str, tuple[ModelFile, ...]], None] | None = None,
        local_dir: Callable[[str], Path | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo: str | None = None
        self._load_files = load_files
        self._download_files = download_files
        # 仓库在本地模型目录里的落盘位置：文件列表据此标「已下载 / 部分下载 / 未下载」。
        self._local_dir = local_dir
        self._files_root: Path | None = None
        self._files_repo: str | None = None
        self._files_list: list[ModelFile] = []
        self._files_loading = False
        self._online = True
        self._files: dict[str, ModelFile] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)

        self.error = hint_label("")
        self.error.setVisible(False)
        layout.addWidget(self.error)

        # 与运行页同一套药丸页签：没有页签上方的分隔线与 QTabBar 基线；
        # 容器底色用 $surface，与展开它的行卡同色（否则详情区会露出窗口底色）。
        self.tabs = tab_widget(self, object_name="RowTabs")
        layout.addWidget(self.tabs)

        meta_page = QWidget(self.tabs)
        meta_layout = QVBoxLayout(meta_page)
        meta_layout.setContentsMargins(0, 6, 0, 0)
        meta_layout.setSpacing(4)
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
            value = QLabel("-", meta_page)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            cell.addWidget(value, 1)
            self.meta_grid.addLayout(cell, row, column)
            self.meta_values[name] = value
        self.meta_grid.setColumnStretch(0, 1)
        self.meta_grid.setColumnStretch(1, 1)
        meta_layout.addLayout(self.meta_grid)
        meta_layout.addStretch(1)
        self.tabs.addTab(meta_page, "详细信息")

        files_page = QWidget(self.tabs)
        files_layout = QVBoxLayout(files_page)
        files_layout.setContentsMargins(0, 6, 0, 0)
        files_layout.setSpacing(4)
        self.files_status = hint_label("", wrap=True)
        self.files_status.setVisible(False)
        files_layout.addWidget(self.files_status)
        self.files_table = QTableWidget(0, 3, files_page)
        self.files_table.setObjectName("ModelFiles")
        self.files_table.setHorizontalHeaderLabels(["文件", "大小", "状态"])
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # 行卡展开区里不另起盒子：表格与详情区同底色，只有表头 / 选中行有底色。
        self.files_table.setAlternatingRowColors(False)
        self.files_table.setShowGrid(False)
        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.setFixedHeight(220)
        self.files_table.itemSelectionChanged.connect(self._sync_files_actions)
        files_layout.addWidget(self.files_table)

        files_actions = QHBoxLayout()
        files_actions.setContentsMargins(0, 0, 0, 0)
        files_actions.addWidget(
            hint_label("Ctrl / Shift 多选后可单独下载（如 VAE / 文本编码器）；「下载全部」只补未下载的", wrap=False)
        )
        files_actions.addStretch(1)
        self.files_download_button = button("下载所选", kind="ghost")
        self.files_download_button.setToolTip("只下载文件列表里选中的文件，不影响已下载的量化")
        self.files_download_button.setVisible(False)
        self.files_download_button.clicked.connect(self._download_selected)
        files_actions.addWidget(self.files_download_button)
        self.files_download_all_button = button("下载全部", kind="primary")
        self.files_download_all_button.setToolTip("下载列表里所有尚未下载的文件（已下载的跳过）")
        self.files_download_all_button.setVisible(False)
        self.files_download_all_button.clicked.connect(self._download_all)
        files_actions.addWidget(self.files_download_all_button)
        files_layout.addLayout(files_actions)
        # 表格高度固定（行高度可控），剩余空间留在下方，文件列表紧跟页签顶部。
        files_layout.addStretch(1)
        self.tabs.addTab(files_page, "文件列表")
        self.tabs.currentChanged.connect(self._on_tab_changed)

    # ------------------------------------------------------------------ #
    # 详细信息
    # ------------------------------------------------------------------ #
    def reset(self, repo: str, online: bool = True) -> None:
        """切换模型时清空旧内容，等后台详情回填；文件清单按需重新拉取。

        ``online=False``（仅本地模型）时不读在线清单：仓库名可能只是本地目录名，
        请求必然失败，文件列表只提示「仅本地」而不报错。
        """
        self.repo = repo
        self._online = online
        self.error.setVisible(False)
        self.error.setText("")
        if not online:
            self.error.setText("仅本地模型：无法确定远程仓库，不读取在线详情与文件清单。")
            self.error.setVisible(True)
        for value in self.meta_values.values():
            value.setText("-")
        if repo != self._files_repo:
            self._files_repo = None
            self._files = {}
            self._files_list = []
            self._files_root = None
            self.files_table.setRowCount(0)
            self.files_status.setText("")
            self.files_status.setVisible(False)
        else:
            # 同一个仓库重新展开（例如刚下载完）：清单不重拉，只刷新状态列。
            self._sync_file_states()
        self._sync_files_actions()
        if self.tabs.currentIndex() == 1:
            self._ensure_files()

    def set_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)
        self.heightChanged.emit()

    def render(self, info: ModelInfo) -> None:
        """回填仓库元数据。"""
        self.repo = info.summary.repo
        self.error.setVisible(False)
        self.error.setText("")
        for name, value in _llmw_models.detail_pairs(info):
            if name in self.meta_values:
                self.meta_values[name].setText(value)
        self.heightChanged.emit()

    # ------------------------------------------------------------------ #
    # 文件列表
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._ensure_files()
        self.heightChanged.emit()

    def _ensure_files(self) -> None:
        repo = self.repo
        if not repo or self._files_loading:
            return
        if self._files_repo == repo:
            # 清单已读过：只按当前文件系统刷新状态（下载完成后回到这个页签）。
            self._sync_file_states()
            return
        if not self._online:
            self._files = {}
            self.files_table.setRowCount(0)
            self.files_status.setText("仅本地模型：无法确定远程仓库，不读取在线文件清单。")
            self.files_status.setVisible(True)
            self._sync_files_actions()
            self.heightChanged.emit()
            return
        if self._load_files is None:
            self.files_status.setText("当前没有可用的文件清单来源。")
            self.files_status.setVisible(True)
            return
        self._files_loading = True
        self._files_repo = repo
        self.files_status.setText(f"正在读取 {repo} 的文件清单…")
        self.files_status.setVisible(True)
        submit(
            self._load_files,
            repo,
            on_done=lambda files: self._files_ready(repo, files),
            on_error=lambda message: self._files_failed(repo, message),
            on_finished=self._files_finished,
        )

    def _files_finished(self) -> None:
        self._files_loading = False

    def _files_ready(self, repo: str, files: list[ModelFile]) -> None:
        if repo != self.repo:
            return
        # 记住这份清单属于哪个仓库：再次展开同一个仓库时可以只刷新状态、不重拉在线清单。
        self._files_repo = repo
        self.files_table.setRowCount(0)
        self._files = {}
        self._files_list = list(files)
        self._files_root = self._resolve_files_root(repo)
        total = 0
        for file in files:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            name = QTableWidgetItem(str(file.path))
            name.setToolTip(str(file.path))
            self.files_table.setItem(row, 0, name)
            self._files[str(file.path)] = file
            size = int(getattr(file, "size", 0) or 0)
            total += size
            size_item = QTableWidgetItem(human_size(size) if size else "-")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.files_table.setItem(row, 1, size_item)
            self.files_table.setItem(row, 2, self._state_item(file))
        pending = len(_llmw_models.files_needing_download(self._files_root, self._files_list))
        downloaded = len(self._files_list) - pending
        summary = f"共 {len(files)} 个文件" + (f" · 合计 {human_size(total)}" if total else "")
        if self._files_root is not None:
            summary += f" · 已下载 {downloaded} / 未下载 {pending}"
        self.files_status.setText(summary)
        self.files_status.setVisible(True)
        self._sync_files_actions()
        self.heightChanged.emit()

    def _files_failed(self, repo: str, message: str) -> None:
        if repo != self.repo:
            return
        self._files_repo = None
        self._files = {}
        self._files_list = []
        self._files_root = None
        self.files_table.setRowCount(0)
        self.files_status.setText(f"读取文件清单失败：{message}")
        self.files_status.setVisible(True)
        self._sync_files_actions()
        self.heightChanged.emit()

    def _selected_files(self) -> tuple[ModelFile, ...]:
        """文件列表当前选中的文件（按行号排序，路径从 ``_files`` 映射取回）。"""
        model = self.files_table.selectionModel()
        rows = sorted({index.row() for index in model.selectedRows()}) if model is not None else []
        selected: list[ModelFile] = []
        for row in rows:
            item = self.files_table.item(row, 0)
            file = self._files.get(item.text()) if item is not None else None
            if file is not None:
                selected.append(file)
        return tuple(selected)

    def _resolve_files_root(self, repo: str) -> Path | None:
        """仓库在本地的落盘目录（本地位置未知时为 ``None``）。"""
        if self._local_dir is None or not repo:
            return None
        return self._local_dir(repo)

    def _state_item(self, file: ModelFile) -> QTableWidgetItem:
        """文件状态列：已下载 / 部分下载 / 未下载（本地目录未知时 ``-``）。"""
        state = _llmw_models.file_local_state(self._files_root, file.path)
        item = QTableWidgetItem(state)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setToolTip(
            "本地已存在完整文件"
            if state == _llmw_models.FILE_STATE_DONE
            else "有 .part 断点，尚未下完"
            if state == _llmw_models.FILE_STATE_PARTIAL
            else "本地没有这个文件"
            if state == _llmw_models.FILE_STATE_MISSING
            else "无法确定本地目录"
        )
        return item

    def refresh_file_states(self) -> None:
        """按照前文件系统刷新每一行的状态列（不重新拉清单；下载完成后由页面调用）。"""
        self._sync_file_states()

    def _sync_file_states(self) -> None:
        if not self._files_list:
            return
        # 仓库 / 模型目录可能变化（设置里改过模型目录），每次刷新都重新解析。
        self._files_root = self._resolve_files_root(self.repo or "")
        for row, file in enumerate(self._files_list):
            item = self._state_item(file)
            old = self.files_table.item(row, 2)
            if old is None:
                self.files_table.setItem(row, 2, item)
            else:
                old.setText(item.text())
                old.setToolTip(item.toolTip())
        self._sync_files_actions()

    def _pending_files(self) -> tuple[ModelFile, ...]:
        """清单里还没下载完的文件（「下载全部」用）。"""
        return _llmw_models.files_needing_download(self._files_root, self._files_list)

    def _sync_files_actions(self) -> None:
        """按「是否有清单 / 是否选中 / 还剩多少未下载」启用两个下载动作。"""
        visible = self._online and self._download_files is not None and bool(self._files)
        self.files_download_button.setVisible(visible)
        self.files_download_button.setEnabled(visible and bool(self._selected_files()))
        pending = self._pending_files() if visible else ()
        self.files_download_all_button.setVisible(visible)
        self.files_download_all_button.setEnabled(bool(pending))
        self.files_download_all_button.setToolTip(
            f"下载 {len(pending)} 个尚未下载的文件（已下载的跳过）" if pending else "全部文件已下载"
        )

    def _download_selected(self) -> None:
        repo = self.repo
        files = self._selected_files()
        if not repo or not files or self._download_files is None:
            return
        self._download_files(repo, files)

    def _download_all(self) -> None:
        """只补下「还没下载完」的文件：已下载的跳过，``.part`` 继续续传。"""
        repo = self.repo
        pending = self._pending_files()
        if not repo or not pending or self._download_files is None:
            return
        self._download_files(repo, pending)


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
