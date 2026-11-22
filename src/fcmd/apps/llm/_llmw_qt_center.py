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
from collections.abc import Callable, Iterable, Sequence
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
from fcmd.apps.llm._llmw_import import (
    FolderInfo,
    FolderKind,
    ImportPlan,
    copy_import,
    inspect_folder,
    plan_import,
)
from fcmd.apps.llm._llmw_list_cache import ModelListCache, cache_key
from fcmd.apps.llm.llmbase import human_size
from fcmd.apps.llm.llmdown import (
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


class _BrowsePanel(RowDetailMixin, AppPage):  # pragma: no cover - Qt 控件
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
    def _on_filter_changed(self, key: str) -> None:
        """切换浏览筛选：分类指标走服务端关键字，适合本机在本地按容量过滤。"""
        self._filter_key = key
        self._filter_query = next((query for name, _label, query in _MODEL_FILTERS if name == key), "")
        self._persist_view_options()
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

    def _on_sort_changed(self, _index: int) -> None:
        key = self.sort_combo.currentData()
        if not isinstance(key, str) or key == self._sort_key:
            return
        self._sort_key = key
        self._persist_view_options()
        self.start_search(1)

    def _on_source_changed(self, _index: int) -> None:
        key = self.source_combo.currentData()
        if isinstance(key, str):
            self._source_filter = key
            self._persist_view_options()
            self._render_results()

    def _on_owner_changed(self, _index: int) -> None:
        owner = self.owner_combo.currentData()
        if isinstance(owner, str):
            self._owner_filter = owner
            self._persist_view_options()
            self._render_results()

    def _refresh_owner_combo(self, items: tuple[ModelSummary, ...]) -> None:
        current = self._owner_filter
        owners = sorted(
            {_llmw_models.model_owner(item) for item in items if _llmw_models.model_owner(item)}, key=str.casefold
        )
        self.owner_combo.blockSignals(True)
        self.owner_combo.clear()
        self.owner_combo.addItem("全部发布方", "")
        for owner in owners:
            self.owner_combo.addItem(owner, owner)
        index = self.owner_combo.findData(current)
        if index < 0:
            self._owner_filter = ""
            index = 0
        self.owner_combo.setCurrentIndex(index)
        self.owner_combo.blockSignals(False)

    def _on_group_toggled(self, checked: bool) -> None:
        self._group_same = checked
        self._persist_view_options()
        self._render_results()

    def _on_valid_toggled(self, checked: bool) -> None:
        self._valid_only = checked
        self._persist_view_options()
        self._render_results()

    def _persist_view_options(self) -> None:
        """把列表视图选项写回 llmw.json，避免每次启动重新选择。"""
        self.state.settings.model_view = {
            "filter": self._filter_key,
            "sort": self._sort_key,
            "source": self._source_filter,
            "owner": self._owner_filter,
            "group_same": self._group_same,
            "valid_only": self._valid_only,
        }
        self.state.persist()

    def _open_model_manager(self) -> None:
        """打开模型管理对话框：扫描已下载 / 未完成模型并批量处理。"""
        from fcmd.apps.llm._llmw_qt_model_manager import ModelManagerDialog

        dialog = ModelManagerDialog(
            _llmconfig.models_dir(),
            on_changed=self._model_manager_changed,
            on_clear_cache=self._cleanup_list_cache,
            parent=self,
        )
        dialog.exec()

    def _model_manager_changed(self) -> None:
        self.refresh_installed()
        self._render_results()

    def _cleanup_list_cache(self) -> None:
        self._list_cache.clear()
        self._log("已清理模型列表缓存")
        self.status("模型列表缓存已清理")

    def delete_model(self, repo: str) -> None:
        """删除本地模型目录；只允许模型根目录内的安全路径。"""
        if self._jobs or self._converting or self._quantizing or self._listing_repo is not None:
            self.status("任务进行中，暂不能删除模型")
            return
        models_root = _llmconfig.models_dir()
        target = models_root.joinpath(*repo.split("/"))
        answer = QMessageBox.question(
            self,
            "删除本地模型",
            f"将删除本地模型目录（不可恢复）：\n{target}\n\n如果服务正在使用该模型，请先停止服务。确定继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_local_model(models_root, repo)
        except ModelToolError as exc:
            self._log(f"删除模型失败 {repo}: {exc}")
            self.status(f"删除失败：{exc}")
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        key = repo.casefold()
        for mapping in (
            self._installed_models,
            self._partial_models,
            self._convertible,
            self._convert_blocked,
            self._quant_candidates,
            self._quant_tags,
            self._quantized_files,
        ):
            mapping.pop(key, None)
        self._installed_repos.discard(key)
        choices = dict(self.state.settings.quant_choice or {})
        choices.pop(key, None)
        self.state.settings.quant_choice = choices or None
        last_model = self.state.settings.last_model
        if last_model and Path(last_model).is_relative_to(target):
            self.state.settings.last_model = None
        self.state.persist()
        self._log(f"已删除本地模型 {repo} -> {target}")
        self.status(f"已删除本地模型 {repo}")
        self.refresh_installed()
        self._render_results()

    def refresh_installed(self) -> None:
        """后台扫描模型目录，记录已安装仓库（用于勾选标志与置顶）。

        扫描可能遍历大量 GGUF 头与转换产物，放到线程池执行；完成后一次性
        套用快照并重绘当前结果，切页 / 下载完成时界面不会被同步扫描卡住。
        扫描进行中再次请求（典型场景：刚下完一个模型）先记下标记，
        当前扫描结束后立即重扫，避免应用下载前的旧快照而停在未下载状态。
        """
        if self._scanning_installed:
            self._rescan_installed = True
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
            models = []
            seen_repos: set[str] = set()
            for root in _llmconfig.models_dirs():
                for model in scan_local_models(root):
                    if model.repo.casefold() in seen_repos:
                        continue
                    seen_repos.add(model.repo.casefold())
                    models.append(model)
        except OSError:
            return _InstalledSnapshot({}, set(), {}, {}, {}, {}, {}, {}, set(), {})
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
                output = _llmw_convert.default_output_path(model.path)
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
        usable = {model.repo.casefold() for model in models if model.weights > 0 or model.partial > 0}
        # 扩散模型（Flux / Qwen-Image 等）不进 llama.cpp 扫描，也没有量化 GGUF；
        # 单独扫描一次扩散资产，把主模型映射回仓库，供「启动服务」走 sd.cpp。
        paint_models: dict[str, Path] = {}
        for asset in _llmpaint.scan_assets(_llmconfig.models_dirs()).main_models():
            for model in models:
                try:
                    asset.path.relative_to(model.path)
                except ValueError:
                    continue
                paint_models.setdefault(model.repo.casefold(), asset.path)
                break
        return _InstalledSnapshot(
            installed,
            set(installed),
            partial,
            convertible,
            blocked,
            quant_candidates,
            quant_tags,
            {repo: tuple(entries) for repo, entries in quantized.items()},
            usable,
            paint_models,
        )

    def _apply_installed(self, snapshot: _InstalledSnapshot) -> None:
        """套用扫描结果；若已有结果行则就地重绘已安装 / 可转换 / 可量化标志。"""
        self._installed_models = snapshot.models
        self._installed_repos = snapshot.repos
        self._usable_repos = snapshot.usable
        self._partial_models = snapshot.partial
        self._convertible = snapshot.convertible
        self._convert_blocked = snapshot.blocked
        self._quant_candidates = snapshot.quant_candidates
        self._quant_tags = snapshot.quant_tags
        self._quantized_files = snapshot.quantized
        self._paint_models = snapshot.paint_models
        pending = self._pending_convert
        self._pending_convert = None
        if pending:
            if pending.casefold() in self._convertible:
                self.start_convert(pending)
            else:
                self._log(f"导入完成，但未检测到可自动转换的 safetensors：{pending}")
        if self._last_result is not None and not self._searching:
            self._render_results()

    def _installed_error(self, message: str) -> None:
        self._log(f"扫描本地模型失败: {message}")

    def _installed_finished(self) -> None:
        self._scanning_installed = False
        self._end_loading("installed")
        if self._rescan_installed:
            # 下载完成 / 切页发生在扫描途中：旧快照不含新文件，结束后续一次。
            self._rescan_installed = False
            self.refresh_installed()

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

    def _installed_name_keys(self) -> set[str]:
        """已安装模型的名称（模型名 + 目录名），用于「适合本机」时保留同名官方条目。"""
        keys: set[str] = set()
        for key, summary in self._installed_models.items():
            if summary.name:
                keys.add(summary.name.casefold())
            keys.add(key.rsplit("/", 1)[-1])
        keys.discard("")
        return keys

    def manual_update(self) -> None:
        """手动更新：强制刷新当前页并重新扫描本地模型。"""
        if self._searching:
            self.status("模型列表正在更新，请稍候…")
            return
        self.status("正在更新模型列表…")
        self.refresh_installed()
        self.start_search(self._page, force=True)

    # -- 导入 ---------------------------------------------------------- #
    def _on_import_clicked(self) -> None:
        menu = QMenu(self)
        file_action = menu.addAction("导入 GGUF 文件…")
        file_action.triggered.connect(self._pick_gguf_files)
        dir_action = menu.addAction("导入模型文件夹…")
        dir_action.triggered.connect(self._pick_model_folder)
        menu.exec(QCursor.pos())

    def _pick_gguf_files(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "导入 GGUF 模型",
            "",
            "GGUF 模型 (*.gguf);;所有文件 (*.*)",
        )
        if paths:
            self.start_import([Path(path) for path in paths])

    def _pick_model_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "导入模型文件夹")
        if not path:
            return
        try:
            info = inspect_folder(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法识别文件夹", str(exc))
            return
        proceed, convert = self._ask_folder_import(info)
        if proceed:
            self.start_import([Path(path)], convert=convert)

    def _ask_folder_import(self, info: FolderInfo) -> tuple[bool, bool]:
        """按文件夹类型给出不同选项，返回 ``(是否导入, 是否导入后转换)``。"""
        box = QMessageBox(self)
        box.setWindowTitle("导入模型文件夹")
        healthy = info.kind in (FolderKind.TENSORS, FolderKind.MIXED, FolderKind.GGUF)
        box.setIcon(QMessageBox.Icon.Information if healthy else QMessageBox.Icon.Warning)
        box.setText(f"检测到：{info.label}")
        box.setInformativeText(info.hint)
        convert_button = None
        if info.can_convert:
            convert_button = box.addButton("导入并转换", QMessageBox.ButtonRole.AcceptRole)
            import_button = box.addButton("仅导入", QMessageBox.ButtonRole.ActionRole)
        elif info.kind is FolderKind.GGUF:
            import_button = box.addButton("导入", QMessageBox.ButtonRole.AcceptRole)
        else:
            import_button = box.addButton("仍然导入", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(import_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is convert_button:
            return True, True
        if clicked is import_button:
            return True, False
        return False, False

    def start_import(self, sources: list[Path], *, convert: bool = False) -> None:
        """把选中的 GGUF / 文件夹复制进模型目录，完成后刷新本地标识。"""
        try:
            plans = plan_import(sources, _llmconfig.models_dir())
        except (OSError, ValueError) as exc:
            self.status(f"无法导入：{exc}")
            QMessageBox.warning(self, "无法导入模型", str(exc))
            return
        if not plans:
            return
        conflicts = [plan for plan in plans if plan.target.exists()]
        if conflicts and not self._confirm_import_overwrite(conflicts):
            return
        self._import_convert = convert
        self._begin_loading("import", f"正在导入 {len(plans)} 项模型…")
        self.status(f"正在导入 {len(plans)} 项模型…")
        self._log("开始导入：" + "、".join(str(plan.source) for plan in plans))
        submit(
            self._import_work,
            plans,
            on_done=self._import_done,
            on_error=self._import_error,
            on_finished=self._import_finished,
        )

    def _confirm_import_overwrite(self, conflicts: list[ImportPlan]) -> bool:
        names = "、".join(plan.target.name for plan in conflicts[:5])
        more = f" 等 {len(conflicts)} 项" if len(conflicts) > 5 else ""
        answer = QMessageBox.question(
            self,
            "目标已存在",
            f"模型目录中已存在：{names}{more}\n继续将覆盖同名文件并合并目录，确定导入？",
        )
        return answer == QMessageBox.StandardButton.Yes

    @staticmethod
    def _import_work(plans: tuple[ImportPlan, ...]) -> tuple[str, ...]:
        """后台线程：逐项复制，返回导入后的目标路径。"""
        copied: list[str] = []
        for plan in plans:
            copied.append(str(copy_import(plan, overwrite=True)))
        return tuple(copied)

    def _import_done(self, targets: tuple[str, ...]) -> None:
        convert = self._import_convert
        self._import_convert = False
        if convert and len(targets) == 1:
            self._pending_convert = Path(targets[0]).name
        self.status(f"已导入 {len(targets)} 项模型，可继续转换或启动")
        self._log("已导入模型：" + "、".join(targets))

    def _import_error(self, message: str) -> None:
        self.status(f"导入失败：{message}")
        self._log(f"导入失败: {message}")
        QMessageBox.warning(self, "导入失败", message)

    def _import_finished(self) -> None:
        self._end_loading("import")
        self.refresh_installed()
        self.state.refresh_models()

    def start_search(self, page: int, *, force: bool = False) -> None:
        """按当前筛选拉取指定页；命中新鲜缓存时直接展示，``force`` 跳过缓存。"""
        if self._searching:
            # 切换分类时上一页请求还没回来：记下最新目标，等它结束后再拉，
            # 不能直接 return，否则 chip 切了但列表仍是旧分类 / 空列表。
            self._pending_search = (max(1, page), force)
            return
        target = max(1, page)
        key = cache_key(self._filter_query, target, _PAGE_SIZE, self._sort_key)
        cached = self._list_cache.get(key)
        self._page = target
        if cached is not None:
            self._render_search(cached.search)
            if cached.fresh and not force:
                self._log(f"模型列表命中缓存（{cached.age:.0f}s 前）：{self._filter_query or '全部'}")
                return
        self._searching = True
        text = f"正在加载第 {self._page} 页…" if cached is None else f"正在刷新第 {self._page} 页…"
        self.status(text)
        self._begin_loading("search", text)
        submit(
            self._search_work,
            self._list_cache,
            key,
            self._filter_query,
            self._page,
            _PAGE_SIZE,
            self._sort_key,
            on_done=self._search_online_done,
            on_error=self._search_error,
            on_finished=self._search_finished,
        )

    def _search_online_done(self, result: ModelSearch) -> None:
        """在线请求成功：记录更新时间后渲染（缓存命中不走这里）。"""
        self.state.mark_models_updated()
        self._render_search(result)

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
        terms = _llmw_models.MULTI_SEARCH_TERMS.get(keyword)
        if terms is not None:
            # 文本 / 图像是合并分类：单一中文关键字往往漏掉官方仓库或偏向某一类，
            # 按细分并行检索后合并，再由 matches_filter 在本地精确归类。
            result = search_models_multi(terms, page, page_size, sort)
        else:
            result = search_models(keyword, page, page_size, sort)
        cache.put(key, result)
        return result

    def _search_finished(self) -> None:
        self._searching = False
        self._end_loading("search")
        if self._pending_search is not None:
            page, force = self._pending_search
            self._pending_search = None
            self.start_search(page, force=force)

    def _search_error(self, message: str) -> None:
        self.status("模型列表加载失败")
        self._log(f"模型列表加载失败: {message}")

    def _remember_items(self, items: Sequence[ModelSummary]) -> None:
        """记住已浏览过的条目：切到「全部 / 适合本机」时不能把刚看到的模型弄丢。"""
        for item in items:
            self._seen_items[item.repo.casefold()] = item
        overflow = len(self._seen_items) - _SEEN_LIMIT
        for key in list(self._seen_items)[: max(0, overflow)]:
            del self._seen_items[key]

    def _render_search(self, result: ModelSearch) -> None:
        self._remember_items(result.items)
        self._last_result = result
        self._render_results()

    def open_repo(self, repo: str) -> None:
        """直接打开 ``组织/模型``：生成单行结果并就地展开详情。"""
        item = ModelSummary(repo=repo)
        self._remember_items((item,))
        self._last_result = ModelSearch(items=(item,), total=1)
        self._render_results()
        self._expand_repo(repo)

    def _render_results(self) -> None:  # noqa: PLR0912 - 列表组合筛选集中在一处
        result = self._last_result
        if result is None:
            return
        filter_label = next((label for key, label, _query in _MODEL_FILTERS if key == self._filter_key), "")
        installed_items = tuple(self._installed_models.values())
        present = {item.repo.casefold() for item in result.items}
        # 分类搜索各拉各的关键字页；把浏览过的条目并回来，保证「全部 / 适合本机」
        # 至少包含刚在「画图 / 编程 / …」里看到的模型，不会一切分类就消失。
        remembered = [summary for key, summary in self._seen_items.items() if key not in present]
        if self._source_filter == "installed":
            base = list(installed_items)
        elif self._source_filter == "online":
            base = [*result.items, *remembered]
        else:
            base = [*result.items, *remembered] + [
                summary for key, summary in self._installed_models.items() if key not in present
            ]
        # 名称搜索是明确意图：即使停在「适合本机」也先展示全部命中，否则官方仓库
        # 会因整仓体积（非 GGUF）被容量过滤掉，用户会以为「搜不到」。
        if self._filter_key == "local" and not self._name_filter.strip():
            base = list(
                _llmw_models.filter_by_local_fit(
                    tuple(base),
                    self._local_capacity(),
                    pinned=self._installed_name_keys(),
                )
            )
        elif self._filter_key not in ("all", ""):
            base = [item for item in base if _llmw_models.matches_filter(item, self._filter_key)]
        items = _llmw_models.dedupe_by_repo(base)
        self._refresh_owner_combo(items)
        if self._owner_filter:
            items = tuple(
                item for item in items if _llmw_models.model_owner(item).casefold() == self._owner_filter.casefold()
            )
        items = _llmw_models.filter_by_name(items, self._name_filter)
        hidden = 0
        if self._valid_only:
            before = len(items)
            items = tuple(item for item in items if _llmw_models.is_usable_item(item, self._usable_repos))
            hidden = before - len(items)
        items = _llmw_models.sort_browse_items(items, self._installed_repos, self._filter_key, self._sort_key)
        same_name_counts: dict[str, int] = {}
        if self._group_same:
            grouped = _llmw_models.group_same_name(items, self._installed_repos)
            same_name_counts = {item.repo.casefold(): count for item, count in grouped}
            items = tuple(item for item, _count in grouped)
        selected = next(
            (item.repo for item in items if item.repo == self.current_repo),
            items[0].repo if items else None,
        )
        self._result_items = items
        self.detach_row_detail("models")
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
            paint_path = self._paint_models.get(key)
            default_quant = self._default_quant(key)
            serve_path = next((path for label, path in quantized if label == default_quant), None)
            if serve_path is None and quantized:
                serve_path = quantized[0][1]
            repo_parts = item.repo.split("/")
            downloadable = len(repo_parts) == 2 and all(repo_parts)
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
                startable=bool(quantized) or paint_path is not None,
                paint_startable=paint_path is not None,
                default_quant=default_quant,
                serve_path=serve_path,
                incomplete=incomplete,
                progress_text=progress_text,
                downloadable=downloadable,
                owner=_llmw_models.model_owner(item),
                size_text=human_size(self._installed_models[key].storage_size)
                if installed and key in self._installed_models and self._installed_models[key].storage_size
                else None,
                same_name=same_name_counts.get(key, 1),
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
        local_view = self._source_filter == "installed"
        pages = 1 if local_view else max(1, math.ceil(result.total / result.page_size)) if result.total else 1
        matched = f" · 名称筛选 {len(items)} 条" if self._name_filter else ""
        if hidden:
            matched += f" · 已隐藏无效 {hidden} 个"
        if hidden:
            matched += f" · 已隐藏无效 {hidden} 个"
        source_label = self.source_combo.currentText()
        total_text = f"已下载 {len(items)} 个" if local_view else f"{result.page}/{pages} · 共 {result.total:,} 条"
        self.status(f"{filter_label} · {source_label} · {total_text}{matched}")
        self.prev_button.setEnabled(not local_view and result.page > 1)
        self.next_button.setEnabled(not local_view and result.page < pages)
        if selected is not None:
            for row, item in enumerate(items):
                if item.repo == selected:
                    self.models.setCurrentRow(row)
                    break
        if not items:
            self._log("当前页没有匹配的模型，可切换筛选或翻页")

    # -- 详情 ---------------------------------------------------------- #
    def _on_model_selected(self, current: int) -> None:
        """选中行变化：只重抛光旧 / 新两行（几百行时逐行抛光会明显卡顿）。"""
        self.models.sync_selection(current)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.models.itemWidget(item)
        if isinstance(row, _ModelItemRow):
            self._on_row_toggle(row)

    def _load_repo_files(self, repo: str) -> list[ModelFile]:
        """读取仓库文件清单：先按设置里的下载源，失败再换另一个源。"""
        source = _llmw_models.settings_from_dict(self.state.settings.download).source
        errors: list[str] = []
        candidates = ("modelscope", "hf-mirror") if source == "auto" else (source, "modelscope", "hf-mirror")
        for candidate in dict.fromkeys(candidates):
            try:
                return fetch_files(repo, candidate)
            except ModelToolError as exc:
                errors.append(f"{candidate}: {exc}")
        raise ModelToolError("；".join(errors[-2:]) or "两个源都不可用")

    def _download_selected_files(self, repo: str, files: tuple[ModelFile, ...]) -> None:
        """按文件列表里选中的文件下载（VAE / 文本编码器等非量化权重）。"""
        self._log(f"按文件列表下载 {repo} 的 {len(files)} 个文件")
        self.start_download(repo, files=files)

    @staticmethod
    def _repo_local_dir(repo: str) -> Path:
        """仓库在本地模型目录里的落盘位置：优先已存在的目录，否则主目录。"""
        roots = _llmconfig.models_dirs()
        for root in roots:
            candidate = root / repo
            if candidate.is_dir():
                return candidate
        return (roots[0] if roots else _llmconfig.models_dir()) / repo

    def _on_detail_height_changed(self) -> None:
        """详情页签内容变化（切页签 / 文件清单加载完）后重算展开行高度。"""
        self.refresh_expanded_height("models")

    def _on_row_toggle(self, row: _ModelItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self.toggle_row("models", row)

    def render_row_detail(self, row: ItemRow) -> None:
        """展开时按需拉取该仓库的详情（仅本地模型跳过网络请求）。"""
        if isinstance(row, _ModelItemRow) and self.current_repo != row.repo:
            self.load_repo(row.repo, online=row.downloadable)

    def _expand_repo(self, repo: str) -> None:
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _ModelItemRow) and row.repo == repo:
                if row.is_expanded():
                    self.focus_row("models", row)
                else:
                    self.toggle_row("models", row)
                return

    def load_repo(self, repo: str, online: bool = True) -> None:
        """加载仓库详情元数据；世代号防止旧请求覆盖新选择。

        ``online=False``（仅本地模型）时不发任何网络请求，详情只标出仅本地。
        """
        self._detail_generation += 1
        generation = self._detail_generation
        self.current_repo = repo
        self.current_info = None
        self.detail_view.reset(repo, online=online)
        self.refresh_expanded_height("models")
        if not online:
            self._log(f"{repo} 是仅本地模型，跳过在线详情与文件清单")
            return
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
        self.refresh_expanded_height("models")
        self._log(f"详情加载失败 {repo}: {message}")

    def _render_detail(self, generation: int, info: ModelInfo) -> None:
        if generation != self._detail_generation:
            return
        self.current_info = info
        self.detail_view.render(info)
        self.refresh_expanded_height("models")
        self._log(f"已加载模型详情: {info.summary.repo}")

    def _log(self, message: str) -> None:
        self.state.log(message)

    # -- 下载 ---------------------------------------------------------- #
    def _sync_download_buttons(self) -> None:
        """逐行同步按钮：只有正在下载 / 排队的那一行禁用下载，转换 / 量化仍全局互斥。"""
        jobs = self._jobs
        queued = {repo.casefold() for repo, _files in self._queued}
        busy_global = bool(jobs) or self._converting or self._quantizing or self._listing_repo is not None
        for index in range(self.models.count()):
            row = self.models.itemWidget(self.models.item(index))
            if isinstance(row, _ModelItemRow):
                key = row.repo.casefold()
                job = jobs.get(key)
                row.set_download_state(
                    busy=self._converting
                    or self._quantizing
                    or self._listing_repo == row.repo
                    or job is not None
                    or key in queued,
                    active=job is not None,
                    queued=key in queued and job is None,
                )
                row.set_convert_state(
                    busy=busy_global,
                    active=self._converting and row.repo == self._convert_active_repo,
                )
                row.set_quant_state(
                    busy=busy_global,
                    active=self._quantizing and row.repo == self._quant_active_repo,
                )
                running = row.serve_path is not None and self.state.is_running(row.serve_path)
                row.set_serve_state(busy=busy_global or self.state.busy, running=running)

    def _begin_quant_choice(self, repo: str, source: str) -> None:
        """后台读取文件清单，稍后弹出量化选择（不阻塞 GUI）。"""
        if self._listing_repo is not None:
            return
        self._listing_repo = repo
        self._sync_download_buttons()
        self.download_status.setVisible(True)
        self.download_bar.setValue(0)
        self.download_text.setText(f"读取 {repo} 的量化清单…")
        self._log(f"读取 {repo} 的量化清单…")
        submit(
            self._quant_options_work,
            repo,
            source,
            on_done=lambda options: self._choose_quant(repo, options),
            on_error=lambda message: self._quant_options_error(repo, message),
        )

    @staticmethod
    def _quant_options_work(repo: str, source: str) -> tuple[QuantOption, ...]:
        """读取量化清单；显式源失败时自动切到双源选优重试一次。"""
        try:
            selection = select_source(repo, source)  # type: ignore[arg-type]
        except ModelToolError:
            if source == "auto":
                raise
            selection = select_source(repo, "auto")
        return quant_options(list(selection.files))

    def _quant_options_error(self, repo: str, message: str) -> None:
        if self._listing_repo == repo:
            self._listing_repo = None
        self._sync_download_buttons()
        self.download_text.setText(f"读取清单失败：{message[:160]}")
        self.status(f"{repo} · 读取清单失败")
        self._log(f"读取量化清单失败 {repo}: {message}")

    def _choose_quant(self, repo: str, options: tuple[QuantOption, ...]) -> None:
        """下载选择：默认整仓全部文件，也可在弹窗里只下某个量化。"""
        if self._listing_repo == repo:
            self._listing_repo = None
        self._sync_download_buttons()
        if not options:
            self._log(f"{repo} 没有量化选项，下载整仓")
            self.start_download(repo, files=())
            return
        labels = ["全部文件（整仓，避免漏项）", *(option.display for option in options)]
        choice, accepted = QInputDialog.getItem(self, "选择下载内容", "下载哪些文件：", labels, 0, False)
        if not accepted or not choice:
            self._update_download_summary()
            self.status(f"{repo} · 已取消下载")
            self._log(f"已取消下载 {repo}：未选择下载内容")
            return
        if choice == labels[0]:
            self._log(f"下载 {repo} 全部文件")
            self.start_download(repo, files=())
            return
        index = labels.index(choice) - 1
        option = options[index]
        self._log(f"选择下载 {repo} · {option.label}")
        self.start_download(repo, files=option.files)

    def start_download(self, repo: str, files: tuple[ModelFile, ...] | None = None) -> None:
        """下载指定仓库到模型目录；未指定量化时先读取文件清单让用户选择。

        支持多仓库并行：正在下载的数量达到 ``download.parallel`` 上限时先排队，
        完成一个后自动开始下一个；同一仓库不会重复入队。
        """
        if self._converting or self._quantizing or not repo:
            return
        key = repo.casefold()
        if key in self._jobs or any(queued.casefold() == key for queued, _ in self._queued):
            self._log(f"{repo} 已在下载任务 / 队列中")
            return
        settings = _llmw_models.settings_from_dict(self.state.settings.download)
        if files is None and not settings.include.strip() and not settings.exclude.strip():
            self._begin_quant_choice(repo, settings.source)
            return
        selected = tuple(files) if files is not None else ()
        if len(self._jobs) >= max(1, settings.parallel):
            self._queued.append((repo, selected))
            self._log(f"{repo} 已加入下载队列（并发上限 {settings.parallel}）")
            self.status(f"{repo} 已排队，等待空闲下载位…")
            self._update_download_summary()
            self._sync_download_buttons()
            return
        self._start_job(repo, selected, settings)

    def _start_job(
        self,
        repo: str,
        files: tuple[ModelFile, ...],
        settings: _llmw_models.DownloadGuiSettings,
    ) -> None:
        """创建并启动一个下载任务（``files`` 为空表示按设置 / 整仓下载）。"""
        dest = _llmconfig.models_dir()
        key = repo.casefold()
        job = _DownloadJob(repo=repo, target=dest / repo, bridge=ProgressBridge(self), speed=SpeedMeter())
        job.bridge.progress.connect(lambda event, job=job: self._on_progress(job, event))
        self._jobs[key] = job
        # 多个模型并行时按上限分摊 workers，避免总连接数成倍膨胀。
        per_job = max(2, settings.workers // max(1, settings.parallel))
        self._log(f"开始下载 {repo} -> {job.target}（并发文件 {per_job}）")
        self._update_download_summary()
        self._sync_download_buttons()
        submit(
            self._download_work,
            repo,
            dest,
            settings.source,
            settings.include,
            settings.exclude,
            per_job,
            settings.verify,
            tuple(file.path for file in files),
            should_stop=lambda job=job: job.stop_requested,
            reporter=job.bridge.progress.emit,
            on_done=lambda target, key=key: self._download_done(key, target),
            on_error=lambda message, key=key: self._download_error(key, message),
            on_finished=lambda key=key: self._download_finished(key),
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
        only_paths: tuple[str, ...] = (),
        *,
        should_stop: Callable[[], bool] | None = None,
        reporter: Callable[[ProgressEvent], None] | None = None,
    ) -> str:
        self._log(f"获取 {repo} 的 {source} 文件清单…")
        selection = select_source(repo, source)  # type: ignore[arg-type]
        choice = describe_selection(selection)
        self._log(f"{choice}，仓库共 {len(selection.files)} 个文件")
        self.status(choice)
        if only_paths:
            # 显式选中文件（文件列表 / 量化选项）优先于设置里的包含 / 排除掩码，
            # 否则选中的 VAE / 文本编码器可能被掩码过滤掉。
            wanted = set(only_paths)
            selected = [file for file in selection.files if file.path in wanted]
        else:
            selected = filter_files(
                list(selection.files),
                _llmw_models.parse_patterns(include),
                _llmw_models.parse_patterns(exclude),
            )
        if not selected:
            if only_paths:
                raise ModelToolError("选中的文件已不在仓库清单里，请刷新后重试")
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
            reporter=reporter,
            should_stop=should_stop,
        )
        return str(target)

    def _on_progress(self, job: _DownloadJob, event: ProgressEvent) -> None:
        if event.task != "总进度":
            return
        job.completed = event.completed
        job.total = event.total
        if event.total > 0:
            totals = dict(self.state.settings.download_totals or {})
            totals[job.repo.casefold()] = event.total
            self.state.settings.download_totals = totals
        job.speed.update(event.completed)
        self._update_download_summary()

    def _update_download_summary(self) -> None:
        """汇总所有并行任务：底部显示总进度 / 总速度 / 排队数。"""
        jobs = list(self._jobs.values())
        if not jobs:
            self.download_bar.setValue(0)
            self.download_text.clear()
            self.download_status.setVisible(False)
            self.stop_download_button.setVisible(False)
            self.stop_download_button.setEnabled(True)
            return
        completed = sum(job.completed for job in jobs)
        total = sum(job.total for job in jobs)
        speed = sum(job.speed.speed for job in jobs)
        if total > 0:
            self.download_bar.setValue(min(100, round(100 * completed / total)))
        size_text = f"{human_size(completed)} / {human_size(total)}" if total > 0 else human_size(completed)
        eta = (total - completed) / speed if total > completed and speed > 0 else None
        eta_text = f" · 剩余 {format_eta(eta)}" if eta is not None else ""
        queue_text = f" · 排队 {len(self._queued)}" if self._queued else ""
        prefix = f"下载 {jobs[0].repo}" if len(jobs) == 1 else f"下载 {len(jobs)} 个模型"
        self.download_status.setVisible(True)
        self.download_text.setText(f"{prefix} · {size_text} · {format_speed(speed)}{eta_text}{queue_text}")
        self.stop_download_button.setVisible(True)
        self.stop_download_button.setEnabled(any(not job.stop_requested for job in jobs))
        self.stop_download_button.setToolTip("停止全部下载" if len(jobs) > 1 else "停止当前下载")

    def _drain_queue(self) -> None:
        """有空闲下载位时从队列取下一个任务。"""
        if not self._queued:
            return
        settings = _llmw_models.settings_from_dict(self.state.settings.download)
        limit = max(1, settings.parallel)
        while self._queued and len(self._jobs) < limit:
            repo, files = self._queued.pop(0)
            self._start_job(repo, files, settings)

    def start_convert(self, repo: str) -> None:
        """把已安装的 HF safetensors 模型转换为 GGUF，进度复用底部状态区。"""
        if self._converting or bool(self._jobs) or self._quantizing or not repo:
            return
        info = self._convertible.get(repo.casefold())
        if info is None:
            return
        source, output, converted = info
        settings = _llmw_convert.convert_settings_from_dict(self.state.settings.convert)
        if converted or (output.is_file() and not settings.overwrite):
            self.refresh_installed()
            self._render_results()
            return
        self._converting = True
        self._convert_active_repo = repo
        self._convert_cancel = threading.Event()
        self.download_status.setVisible(True)
        # 准备阶段（探测解释器 / 获取源码 / 检查架构）没有百分比：用忙碌条表示在动。
        self.download_bar.setRange(0, 0)
        self.download_text.setText(f"准备转换 {repo}…")
        self.stop_download_button.setText("停止转换")
        self.stop_download_button.setToolTip("停止转换（含 llama.cpp 源码下载）")
        self.stop_download_button.setEnabled(True)
        self.stop_download_button.setVisible(True)
        self._sync_download_buttons()
        self._log(f"开始转换 {source} -> {output}")
        self._bridge = ProgressBridge(self)
        self._bridge.progress.connect(self._on_convert_progress)
        form = _llmw_convert._ConvertForm(
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

    def _convert_work(self, form: _llmw_convert._ConvertForm) -> list[str]:
        source = Path(form.source).expanduser()

        def report(text: str) -> None:
            """准备阶段进度：写日志并更新底部状态，避免长时间看起来「卡死」。"""
            self._log(text)
            if self._bridge is not None:
                self._bridge.progress.emit(text)

        cancel = self._convert_cancel
        python = form.python or llmconvert.find_convert_python(on_status=report, cancel=cancel)
        plan = llmconvert.plan_convert(
            source,
            output=Path(form.output),
            outtype=form.outtype,
            script=Path(form.script).expanduser() if form.script else None,
            python=python,
            tag=form.tag or None,
            on_status=report,
            cancel=cancel,
        )
        last = -1

        def relay(line: str) -> None:
            nonlocal last
            percent = _llmw_convert._progress_percent(line)
            if percent is None:
                self._log(line)
            elif percent >= last:
                last = percent
                self._bridge.progress.emit(percent) if self._bridge is not None else None

        return [str(path) for path in llmconvert.convert_model(plan, on_output=relay, on_status=report, cancel=cancel)]

    def _on_convert_progress(self, value: object) -> None:
        if isinstance(value, int):
            if self.download_bar.maximum() == 0:
                self.download_bar.setRange(0, 100)
            self.download_bar.setValue(value)
            self.download_text.setText(f"转换 {self._convert_active_repo or '-'} · {value}%")
        elif isinstance(value, str) and value:
            # 准备阶段的状态行：解释器探测 / 下载源码 / clone / 启动脚本。
            self.download_text.setText(value)

    def _convert_done(self, repo: str, produced: list[str]) -> None:
        self.download_bar.setRange(0, 100)
        self.download_bar.setValue(100)
        self.stop_download_button.setVisible(False)
        self.download_text.setText(f"转换完成：{repo}")
        self._log(f"转换完成 {repo}: {'、'.join(produced)}")
        self.refresh_installed()
        self._render_results()

    def _convert_error(self, repo: str, message: str) -> None:
        self.download_bar.setRange(0, 100)
        self.stop_download_button.setVisible(False)
        if "已取消转换" in message:
            self.download_text.setText(f"已取消转换：{repo}")
            self._log(f"已取消转换 {repo}")
            return
        self.download_text.setText(f"转换失败：{repo} · {message}")
        self._log(f"转换失败 {repo}: {message}")

    def _convert_finished(self) -> None:
        self._converting = False
        self._convert_active_repo = None
        self._convert_cancel = None
        self.stop_download_button.setText("停止")
        self.stop_download_button.setToolTip("停止当前下载")
        self.stop_download_button.setVisible(False)
        self._sync_download_buttons()

    # -- 快捷启动 ------------------------------------------------------ #
    def _on_quick_serve(self, repo: str) -> None:
        """选择量化精度后快捷启动；只有一个精度时直接启动。"""
        if self._quantizing or bool(self._jobs) or self._converting:
            return
        key = repo.casefold()
        paint_path = self._paint_models.get(key)
        if paint_path is not None:
            self._log(f"快捷启动画图服务: {paint_path}")
            self.status(f"正在准备启动画图服务 {paint_path.name}…")
            self.state.request_paint_model(paint_path)
            return
        files = self._quantized_files.get(key, ())
        if not files:
            return
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
            label = _llmw_models.quant_precision_label(path)
            if label:
                choices = dict(self.state.settings.quant_choice or {})
                choices[repo_key] = label
                self.state.settings.quant_choice = choices
                self.state.persist()
        self._log(f"快捷启动: {path}")
        self.status(f"正在准备启动 {path.name}…")
        self.state.request_serve_model(path)

    def _on_quantize_requested(self, repo: str) -> None:
        """点击「量化」后弹出精度选项，选中后立即开始单精度量化。"""
        if self._quantizing or bool(self._jobs) or self._converting:
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
        if self._quantizing or bool(self._jobs) or self._converting:
            return
        if quant.casefold() in {tag.casefold() for tag in self._quant_tags.get(repo.casefold(), ())}:
            self.status(f"{repo} · {quant} 已量化，无需重复执行")
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
        self._bridge = ProgressBridge(self)
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

    def _download_done(self, key: str, target: str) -> None:
        job = self._jobs.get(key)
        repo = job.repo if job is not None else key
        self._log(f"下载完成 {repo}: {target}")
        self.downloadFinished.emit()

    def _download_error(self, key: str, message: str) -> None:
        job = self._jobs.get(key)
        repo = job.repo if job is not None else key
        self._log(f"下载失败 {repo}: {message}")
        self.status(f"{repo} · 下载失败：{message[:120]}")

    def _download_finished(self, key: str) -> None:
        job = self._jobs.pop(key, None)
        if job is not None:
            self._log(f"下载结束 {job.repo}")
        self.state.persist()
        # 成功 / 失败 / 手动停止都会走到这里：重扫模型目录（成功后行会切到
        # 已下载 / 已安装状态；中途结束则重算 .part 进度），再重绘当前结果。
        self.refresh_installed()
        self._render_results()
        self._sync_download_buttons()
        self._drain_queue()
        self._update_download_summary()
        # 文件列表的状态列（已下载 / 部分下载）跟着刷新，不必重新拉在线清单。
        self.detail_view.refresh_file_states()

    def stop_download(self) -> None:
        if self._converting and self._convert_cancel is not None:
            self._convert_cancel.set()
            self.stop_download_button.setEnabled(False)
            self.download_text.setText("正在停止转换…")
            self._log("正在停止转换…")
            return
        if not self._jobs:
            return
        for job in self._jobs.values():
            job.stop_requested = True
        if self._queued:
            self._queued.clear()
            self._sync_download_buttons()
        self.stop_download_button.setEnabled(False)
        self.download_text.setText("正在停止下载…")
        self._log("正在停止下载…")

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
