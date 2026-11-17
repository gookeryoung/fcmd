"""locllmw（Qt）模型中心：ModelScope 浏览 / 下载 / 已安装 / 转换 / 量化五个子页。

页面口径与原 Tk 版一致，只是把控件换成 Qt 并把后台任务统一走
:func:`fcmd.gui.qt.workers.submit`：

- **浏览**：ModelScope 搜索（类别 / 系列 / 排序 / 翻页）、模型详情与文件清单；
- **下载**：目标目录（唯一模型目录）、下载源、并发、SHA256 校验、包含 / 排除掩码、
  汇总 / 单文件进度、失败记录与重试、日志；
- **已安装**：模型仓库列表（扫描 / 打开 / 确认后删除）与运行用 GGUF 分组树；
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
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import (
    _llmconfig,
    _locllmw_convert,
    _locllmw_models,
    llmconvert,
    llmengine,
    llmenv,
    llmmodel,
    llmqnt,
)
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision, quantize_input_error
from fcmd.apps.locllm._llmgguf_cache import PrecisionCache
from fcmd.apps.locllm.llmbase import ModelEntry, ServeError, human_size
from fcmd.apps.locllm.llmdown import (
    LocalModel,
    ModelFile,
    ModelInfo,
    ModelSearch,
    ModelTask,
    ModelToolError,
    ProgressEvent,
    delete_local_model,
    download_files,
    fetch_files,
    fetch_model_info,
    fetch_tasks,
    filter_by_series,
    filter_files,
    scan_local_models,
    search_models,
    select_source,
    series_matches,
    series_options,
    sort_by_popularity,
)
from fcmd.gui.qt.widgets import (
    Card,
    LogView,
    PageScroll,
    button,
    hint_label,
    make_splitter,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["ModelCenterPage"]

_PAGE_SIZE = _locllmw_models._PAGE_SIZE
_MAX_WORKERS = _locllmw_models._MAX_WORKERS
_ALL_TASKS = _locllmw_models._ALL_TASKS
_ALL_SERIES = _locllmw_models._ALL_SERIES
_SOURCE_LABELS = _locllmw_models._SOURCE_LABELS
_SOURCE_KEYS = _locllmw_models._SOURCE_KEYS
_SORT_LABELS = _locllmw_models._SORT_LABELS
_SORT_KEYS = _locllmw_models._SORT_KEYS
_MODEL_COLUMNS = _locllmw_models._MODEL_COLUMNS
_TAB_NAMES = _locllmw_models.MODEL_TAB_NAMES
_DEFAULT_TAB = _locllmw_models.DEFAULT_MODEL_TAB


class _ProgressBridge(QObject):  # pragma: no cover - Qt 信号桥
    """下载进度信号（工作线程 → 主线程）。"""

    progress = Signal(object)


def _task_text(exc: BaseException) -> str:
    """后台异常转可读文案（含类型名），与 Tk 版日志口径一致。"""
    return (
        str(exc)
        if isinstance(exc, (ModelToolError, ServeError, OSError, ValueError))
        else f"{type(exc).__name__}: {exc}"
    )


class _BrowsePanel(QWidget):  # pragma: no cover - Qt 控件
    """浏览页：搜索、结果表、详情与文件清单。"""

    repoSelected = Signal(str)

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.current_repo: str | None = None
        self.current_info: ModelInfo | None = None
        self.current_files: tuple[ModelFile, ...] = ()
        self._tasks: tuple[ModelTask, ...] = ()
        self._last_result: ModelSearch | None = None
        self._page = 1
        self._sort_key = "default"
        self._sort_column: str | None = None
        self._sort_desc = True
        self._series_options: tuple[str, ...] = ()
        self._series_applied = ""
        self._detail_generation = 0
        self._searching = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        search = Card("搜索 ModelScope")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel("关键字"))
        self.keyword = QLineEdit(search)
        self.keyword.setPlaceholderText("关键字，或直接输入「组织/模型」查看详情")
        self.keyword.returnPressed.connect(lambda: self.start_search(1))
        top.addWidget(self.keyword, 1)
        self.search_button = button("搜索", kind="primary")
        self.search_button.clicked.connect(lambda: self.start_search(1))
        top.addWidget(self.search_button)
        search.body.addLayout(top)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        nav.addWidget(QLabel("类别"))
        self.task_combo = QComboBox(search)
        self.task_combo.addItem(_ALL_TASKS)
        self.task_combo.currentIndexChanged.connect(lambda _index: self.start_search(1))
        nav.addWidget(self.task_combo)
        nav.addWidget(QLabel("系列"))
        self.series_combo = QComboBox(search)
        self.series_combo.setEditable(True)
        self.series_combo.addItem(_ALL_SERIES)
        self.series_combo.currentIndexChanged.connect(lambda _index: self._on_series_changed())
        series_edit = self.series_combo.lineEdit()
        if series_edit is not None:
            series_edit.returnPressed.connect(self._on_series_enter)
        nav.addWidget(self.series_combo, 1)
        nav.addWidget(QLabel("排序"))
        self.sort_combo = QComboBox(search)
        self.sort_combo.addItems(list(_SORT_LABELS.values()))
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        nav.addWidget(self.sort_combo)
        self.page_label = hint_label("尚未搜索", wrap=False)
        nav.addWidget(self.page_label)
        self.prev_button = button("上一页")
        self.prev_button.clicked.connect(lambda: self.start_search(self._page - 1))
        self.next_button = button("下一页")
        self.next_button.clicked.connect(lambda: self.start_search(self._page + 1))
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        search.body.addLayout(nav)
        layout.addWidget(search)

        split = make_splitter(sizes=(460, 520), parent=self)
        layout.addWidget(split, 1)
        list(_MODEL_COLUMNS)
        self.models = make_table(
            [title for title, _width, _anchor in _MODEL_COLUMNS.values()],
            widths=[width for _title, width, _anchor in _MODEL_COLUMNS.values()],
            stretch_columns=(0,),
            parent=split,
        )
        self.models.setMinimumHeight(200)
        self.models.itemSelectionChanged.connect(self._on_model_selected)
        self.models.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        split.addWidget(self.models)

        right = make_splitter(Qt.Orientation.Vertical, sizes=(280, 220), parent=split)
        split.addWidget(right)

        detail = QWidget(right)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(6, 0, 0, 0)
        detail_layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("选择左侧模型查看详情", detail)
        self.title.setObjectName("Value")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subtitle = hint_label("")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.go_download = button("前往下载", kind="primary")
        self.go_download.setEnabled(False)
        self.go_download.clicked.connect(lambda: self.repoSelected.emit(self.current_repo or ""))
        header.addWidget(self.go_download)
        detail_layout.addLayout(header)

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
            value = QLabel("-", detail)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            cell.addWidget(value, 1)
            self.meta_grid.addLayout(cell, row, column)
            self.meta_values[name] = value
        self.meta_grid.setColumnStretch(0, 1)
        self.meta_grid.setColumnStretch(1, 1)
        detail_layout.addLayout(self.meta_grid)

        readme_card = Card("README", detail)
        self.readme = QPlainTextEdit(readme_card)
        self.readme.setReadOnly(True)
        self.readme.setMinimumHeight(90)
        readme_card.body.addWidget(self.readme)
        detail_layout.addWidget(readme_card, 1)
        right.addWidget(detail)

        files_card = Card("文件清单", right)
        self.files = make_table(
            ("文件", "大小", "SHA256"),
            widths=(360, 90, 130),
            stretch_columns=(0,),
            parent=files_card,
        )
        self.files.setMinimumHeight(120)
        files_card.body.addWidget(self.files)
        self.files_hint = hint_label("")
        files_card.body.addWidget(self.files_hint)
        right.addWidget(files_card)

        self._load_tasks()

    # -- 搜索 ---------------------------------------------------------- #
    def _load_tasks(self) -> None:
        submit(
            fetch_tasks,
            on_done=self._render_tasks,
            on_error=lambda message: self._log(f"类别列表加载失败: {message}"),
        )

    def _render_tasks(self, tasks: tuple[ModelTask, ...]) -> None:
        self._tasks = tasks
        labels = [_ALL_TASKS, *dict.fromkeys(task.label for task in tasks)]
        current = self.task_combo.currentText()
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.task_combo.addItems(labels)
        if current in labels:
            self.task_combo.setCurrentText(current)
        self.task_combo.blockSignals(False)

    def _active_task(self) -> ModelTask | None:
        label = self.task_combo.currentText()
        return next((task for task in self._tasks if task.label == label), None)

    def start_search(self, page: int) -> None:
        """后台搜索指定页；关键字为 ``组织/模型`` 时直接加载仓库详情。"""
        if self._searching:
            return
        keyword = self.keyword.text().strip()
        if keyword.count("/") == 1 and " " not in keyword:
            self.load_repo(keyword)
            return
        task = self._active_task()
        query = keyword or (task.name if task is not None else "")
        self._page = max(1, page)
        self._searching = True
        self.search_button.setEnabled(False)
        self.page_label.setText(f"正在搜索第 {self._page} 页…")
        submit(
            search_models,
            query,
            self._page,
            _PAGE_SIZE,
            self._sort_key,
            on_done=self._render_search,
            on_error=self._search_error,
            on_finished=self._search_finished,
        )

    def _search_finished(self) -> None:
        self._searching = False
        self.search_button.setEnabled(True)

    def _search_error(self, message: str) -> None:
        self.page_label.setText("搜索失败")
        self._log(f"搜索失败: {message}")

    def _render_search(self, result: ModelSearch) -> None:
        self._last_result = result
        self._render_results()

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_key = _SORT_KEYS.get(self.sort_combo.currentText(), "default")
        self.start_search(1)

    def _on_header_clicked(self, index: int) -> None:
        keys = list(_MODEL_COLUMNS)
        if not 0 <= index < len(keys):
            return
        column = keys[index]
        if self._sort_column != column:
            self._sort_column = column
            self._sort_desc = column in {"downloads", "size"}
        elif self._sort_desc:
            self._sort_desc = False
        else:
            self._sort_column = None
        self._render_results()

    def _on_series_changed(self) -> None:
        label = self.series_combo.currentText().strip()
        if label in ("", _ALL_SERIES) or label not in self._series_options:
            self._series_applied = ""
            self.series_combo.blockSignals(True)
            self.series_combo.setCurrentText(_ALL_SERIES)
            self.series_combo.blockSignals(False)
        else:
            self._series_applied = label
        self._render_results()

    def _on_series_enter(self) -> None:
        """回车应用系列筛选：精确匹配优先，唯一子串候选时自动补全。"""
        text = self.series_combo.currentText().strip()
        if text in ("", _ALL_SERIES):
            self._series_applied = ""
            self.series_combo.setCurrentText(_ALL_SERIES)
            self._render_results()
            return
        candidates = [label for label in self._series_options if series_matches(label, text)]
        if text not in self._series_options and len(candidates) == 1:
            self.series_combo.setCurrentText(candidates[0])
        elif text not in self._series_options:
            self.page_label.setText("没有匹配的系列，请从下拉中选择")
            return
        self._series_applied = self.series_combo.currentText()
        self._render_results()

    def _update_series_options(self, items: tuple[Any, ...]) -> None:
        options = series_options(items)
        self._series_options = options
        if self._series_applied and self._series_applied not in options:
            self._series_applied = ""
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        self.series_combo.addItems([_ALL_SERIES, *options])
        self.series_combo.setCurrentText(self._series_applied or _ALL_SERIES)
        self.series_combo.blockSignals(False)

    def _render_results(self) -> None:  # noqa: PLR0912 - 结果渲染集中处理筛选 / 排序 / 翻页状态
        result = self._last_result
        if result is None:
            return
        task = self._active_task()
        items = result.items
        if task is not None and task.chinese_name:
            items = _locllmw_models.filter_models(items, task.chinese_name)
        self._update_series_options(items)
        items = filter_by_series(items, self._series_applied)
        if self._sort_column is None:
            items = sort_by_popularity(items)
        else:
            items = _locllmw_models.sort_models(items, self._sort_column, self._sort_desc)
        selected = next(
            (item.repo for item in items if item.repo == self.current_repo),
            items[0].repo if items else None,
        )
        self.models.setRowCount(len(items))
        for row, item in enumerate(items):
            for column, text in enumerate(_locllmw_models.model_row(item)):
                cell = make_table_item(text)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.repo)
                self.models.setItem(row, column, cell)
        pages = max(1, math.ceil(result.total / result.page_size)) if result.total else 1
        filters = []
        if task is not None:
            filters.append(f"类别「{task.label}」")
        if self._series_applied:
            filters.append(f"系列「{self._series_applied}」")
        matched = f" · 本页匹配 {len(items)} 个" if filters else ""
        self.page_label.setText(f"第 {result.page} / {pages} 页（共 {result.total:,} 条{matched}）")
        self.prev_button.setEnabled(result.page > 1)
        self.next_button.setEnabled(result.page < pages)
        if selected is not None:
            for row in range(self.models.rowCount()):
                cell = self.models.item(row, 0)
                if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == selected:
                    self.models.selectRow(row)
                    break
        if not items:
            self.page_label.setText("没有匹配的模型，可清空关键字或翻页后再试")

    # -- 详情 ---------------------------------------------------------- #
    def _on_model_selected(self) -> None:
        row = self.models.currentRow()
        if row < 0:
            return
        cell = self.models.item(row, 0)
        repo = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
        if isinstance(repo, str) and repo and repo != self.current_repo:
            self.load_repo(repo)

    def load_repo(self, repo: str) -> None:
        """加载仓库详情与文件清单；世代号防止旧请求覆盖新选择。"""
        self._detail_generation += 1
        generation = self._detail_generation
        self.current_repo = repo
        self.current_info = None
        self.current_files = ()
        self.go_download.setEnabled(False)
        self.title.setText(repo)
        self.subtitle.setText("正在加载详情…")
        for value in self.meta_values.values():
            value.setText("-")
        self.readme.clear()
        self.files.setRowCount(0)
        self.files_hint.setText("")
        self.page_label.setText(f"正在加载 {repo} …")
        submit(
            self._detail_work,
            repo,
            on_done=lambda payload: self._render_detail(generation, *payload),
            on_error=lambda message: self._detail_error(generation, repo, message),
        )

    @staticmethod
    def _detail_work(repo: str) -> tuple[ModelInfo, tuple[ModelFile, ...], str]:
        info = fetch_model_info(repo)
        try:
            files = tuple(fetch_files(repo, "modelscope"))
        except Exception as exc:  # 清单异常不影响详情展示
            return info, (), _task_text(exc)
        return info, files, ""

    def _detail_error(self, generation: int, repo: str, message: str) -> None:
        if generation != self._detail_generation:
            return
        self.subtitle.setText("详情加载失败")
        self.page_label.setText(f"{repo} 详情加载失败")
        self._log(f"详情加载失败 {repo}: {message}")

    def _render_detail(self, generation: int, info: ModelInfo, files: tuple[ModelFile, ...], error: str) -> None:
        if generation != self._detail_generation:
            return
        self.current_info = info
        self.current_files = files
        summary = info.summary
        self.title.setText(summary.repo)
        self.subtitle.setText(summary.chinese_name or info.description[:80] or " ")
        for name, value in _locllmw_models.detail_pairs(info):
            if name in self.meta_values:
                self.meta_values[name].setText(value)
        self.readme.setPlainText(info.readme or "（该模型未提供 README）")
        self.files.setRowCount(len(files))
        total = 0
        for row, file in enumerate(files):
            total += file.size
            self.files.setItem(row, 0, make_table_item(file.path))
            self.files.setItem(row, 1, make_table_item(human_size(file.size)))
            self.files.setItem(row, 2, make_table_item(file.sha256[:12] or "-"))
        if files:
            self.files_hint.setText(f"共 {len(files)} 个文件，合计 {human_size(total)}（ModelScope 清单）")
        else:
            self.files_hint.setText(f"文件清单获取失败：{error}" if error else "文件清单为空")
        self.go_download.setEnabled(True)
        self.page_label.setText(f"已加载 {summary.repo}")
        self.repoSelected.emit(summary.repo)
        self._log(f"已加载模型详情: {summary.repo}")

    def _log(self, message: str) -> None:
        self.state.logLine.emit(message)


class _DownloadPanel(QWidget):  # pragma: no cover - Qt 控件
    """下载页：参数、进度、失败记录与日志。"""

    def __init__(self, state: LocllmState, browse: _BrowsePanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.browse = browse
        self._settings = _locllmw_models.settings_from_dict(state.settings.download)
        self._downloading = False
        self._stop_requested = False
        self._failures: list[tuple[str, str, str]] = []
        self._target = _llmconfig.models_dir()
        self._bridge: _ProgressBridge | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        current = Card("当前模型")
        self.selected_repo = QLabel("未选择模型", current)
        self.selected_repo.setObjectName("Value")
        current.body.addWidget(self.selected_repo)
        current.body.addWidget(hint_label("在「浏览」页选中模型后，到此页配置下载参数并开始下载"))
        layout.addWidget(current)
        browse.repoSelected.connect(self._on_repo_selected)

        settings = Card("下载设置")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("模型目录"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.dest = QLineEdit(str(_llmconfig.models_dir()), settings)
        self.dest.setReadOnly(True)
        grid.addWidget(self.dest, 0, 1, 1, 3)
        grid.addWidget(QLabel("下载源"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.source = QComboBox(settings)
        self.source.addItems(list(_SOURCE_LABELS.values()))
        self.source.setCurrentText(_SOURCE_LABELS.get(self._settings.source, "ModelScope"))
        grid.addWidget(self.source, 1, 1)
        grid.addWidget(QLabel("并发"), 1, 2, Qt.AlignmentFlag.AlignRight)
        self.workers = QSpinBox(settings)
        self.workers.setRange(1, _MAX_WORKERS)
        self.workers.setValue(self._settings.workers)
        grid.addWidget(self.workers, 1, 3)
        self.verify = QCheckBox("校验 SHA256", settings)
        self.verify.setChecked(self._settings.verify)
        grid.addWidget(self.verify, 1, 4)
        grid.addWidget(QLabel("包含掩码"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.include = QLineEdit(self._settings.include, settings)
        grid.addWidget(self.include, 2, 1, 1, 3)
        grid.addWidget(QLabel("排除掩码"), 3, 0, Qt.AlignmentFlag.AlignRight)
        self.exclude = QLineEdit(self._settings.exclude, settings)
        grid.addWidget(self.exclude, 3, 1, 1, 3)
        settings.body.addLayout(grid)
        layout.addWidget(settings)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.download_button = button("下载", kind="primary")
        self.download_button.clicked.connect(self.start_download)
        actions.addWidget(self.download_button)
        self.stop_button = button("停止", kind="danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_download)
        actions.addWidget(self.stop_button)
        self.retry_button = button("重试下载")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self.start_download)
        actions.addWidget(self.retry_button)
        actions.addStretch(1)
        open_target = button("打开目标目录")
        open_target.clicked.connect(self.open_target)
        actions.addWidget(open_target)
        layout.addLayout(actions)

        progress = Card("进度")
        progress_grid = QGridLayout()
        progress_grid.setContentsMargins(0, 0, 0, 0)
        progress_grid.setHorizontalSpacing(8)
        progress_grid.setVerticalSpacing(4)
        progress_grid.setColumnStretch(1, 1)
        progress_grid.addWidget(QLabel("总进度"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.overall_bar = QProgressBar(progress)
        self.overall_bar.setRange(0, 100)
        progress_grid.addWidget(self.overall_bar, 0, 1)
        self.overall_text = hint_label("-", wrap=False)
        progress_grid.addWidget(self.overall_text, 0, 2)
        progress_grid.addWidget(QLabel("当前文件"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.file_bar = QProgressBar(progress)
        self.file_bar.setRange(0, 100)
        progress_grid.addWidget(self.file_bar, 1, 1)
        self.file_text = hint_label("-", wrap=False)
        progress_grid.addWidget(self.file_text, 1, 2)
        progress.body.addLayout(progress_grid)
        layout.addWidget(progress)

        failed = Card("失败记录")
        self.failures = make_table(
            ("时间", "模型", "原因"), widths=(100, 220, 420), stretch_columns=(2,), parent=failed
        )
        self.failures.setMinimumHeight(80)
        failed.body.addWidget(self.failures)
        fail_bar = QHBoxLayout()
        fail_bar.setContentsMargins(0, 0, 0, 0)
        fail_bar.setSpacing(6)
        retry = button("重试选中")
        retry.clicked.connect(self.retry_failure)
        fail_bar.addWidget(retry)
        clear = button("清空记录")
        clear.clicked.connect(self.clear_failures)
        fail_bar.addWidget(clear)
        self.failure_hint = hint_label("暂无失败记录")
        fail_bar.addWidget(self.failure_hint, 1)
        failed.body.addLayout(fail_bar)
        layout.addWidget(failed)

        log_card = Card("日志")
        self.log_view = LogView(height=110, parent=log_card)
        log_card.body.addWidget(self.log_view)
        layout.addWidget(log_card, 1)

    # -- 设置持久化 ---------------------------------------------------- #
    def settings_dict(self) -> dict[str, Any]:
        return _locllmw_models.settings_to_dict(
            _locllmw_models.DownloadGuiSettings(
                source=_SOURCE_KEYS.get(self.source.currentText(), "modelscope"),
                workers=min(max(self.workers.value(), 1), _MAX_WORKERS),
                verify=self.verify.isChecked(),
                include=self.include.text(),
                exclude=self.exclude.text(),
            )
        )

    def _save_settings(self) -> None:
        self.state.settings.download = self.settings_dict()
        self.state.persist()

    def refresh_config(self) -> None:
        """模型目录变化后同步目标目录与已下载列表。"""
        self.dest.setText(str(_llmconfig.models_dir()))
        self._target = _llmconfig.models_dir()

    def _on_repo_selected(self, repo: str) -> None:
        if repo:
            self.selected_repo.setText(repo)
            self._target = _llmconfig.models_dir()

    # -- 下载 ---------------------------------------------------------- #
    def _log(self, message: str) -> None:
        self.log_view.append(message)
        self.state.logLine.emit(message)

    def start_download(self) -> None:
        if self._downloading:
            return
        repo = self.browse.current_repo
        if not repo:
            self._log("请先在「浏览」页选择一个模型")
            return
        source = _SOURCE_KEYS.get(self.source.currentText(), "modelscope")
        dest = _llmconfig.models_dir()
        self._save_settings()
        self._downloading = True
        self._stop_requested = False
        self.download_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.overall_bar.setValue(0)
        self.file_bar.setValue(0)
        self.overall_text.setText("-")
        self.file_text.setText("-")
        self._target = dest / repo
        self._log(f"开始下载 {repo} -> {self._target}")
        self._log(f"下载源: {source}，并发: {self.workers.value()}")
        self._bridge = _ProgressBridge(self)
        self._bridge.progress.connect(self._on_progress)
        submit(
            self._download_work,
            repo,
            dest,
            source,
            self.include.text(),
            self.exclude.text(),
            self.workers.value(),
            self.verify.isChecked(),
            on_done=self._download_done,
            on_error=self._download_error,
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
        self._log_safe(f"获取 {repo} 的 {source} 文件清单…")
        selection = select_source(repo, source)  # type: ignore[arg-type]
        self._log_safe(f"选用源 {selection.source}，仓库共 {len(selection.files)} 个文件")
        selected = filter_files(
            list(selection.files),
            _locllmw_models.parse_patterns(include),
            _locllmw_models.parse_patterns(exclude),
        )
        if not selected:
            raise ModelToolError("过滤后没有文件，请检查包含 / 排除掩码")
        target = dest / repo
        self._log_safe(f"待下载 {len(selected)} 个文件，目标 {target}")
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

    def _log_safe(self, message: str) -> None:
        """工作线程内追加日志：经状态栏日志通道排队回主线程。"""
        self.state.logLine.emit(message)

    def _emit_progress(self, event: ProgressEvent) -> None:
        if self._bridge is not None:
            self._bridge.progress.emit(event)

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.task == "总进度":
            self.overall_bar.setValue(round(100 * event.completed / max(event.total, 1)))
            self.overall_text.setText(f"{human_size(event.completed)} / {human_size(event.total)}")
            return
        self.file_bar.setValue(round(100 * event.completed / max(event.total, 1)))
        if event.state == "start":
            self.file_text.setText(event.task)
            self._log(f"开始: {event.task}")

    def _download_done(self, target: str) -> None:
        self.overall_bar.setValue(100)
        self._log(f"下载完成: {target}")
        self._refresh_after_download()

    def _download_error(self, message: str) -> None:
        self._record_failure(message)
        self._log(f"下载失败: {message}")
        QMessageBox.warning(self, "下载未完成", message)
        if self.browse.current_repo:
            self.retry_button.setEnabled(True)

    def _download_finished(self) -> None:
        self._downloading = False
        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _refresh_after_download(self) -> None:
        panel = getattr(self, "_installed_panel", None)
        if panel is not None:
            panel.refresh()

    def stop_download(self) -> None:
        if not self._downloading:
            return
        self._stop_requested = True
        self.stop_button.setEnabled(False)
        self._log("正在停止…")

    def open_target(self) -> None:
        target = self._target if self._target.is_dir() else _llmconfig.models_dir()
        if not target.is_dir():
            QMessageBox.warning(self, "目录不存在", f"目录不存在:\n{target}")
            return
        try:
            _locllmw_models.open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开目录失败", str(exc))

    # -- 失败记录 ------------------------------------------------------ #
    def _record_failure(self, message: str) -> None:
        repo = self.browse.current_repo or "-"
        stamp = time.strftime("%H:%M:%S")
        self._failures.append((stamp, repo, message))
        del self._failures[:-20]
        self.failures.insertRow(self.failures.rowCount())
        row = self.failures.rowCount() - 1
        self.failures.setItem(row, 0, make_table_item(stamp))
        self.failures.setItem(row, 1, make_table_item(repo))
        self.failures.setItem(row, 2, make_table_item(message))
        self.failure_hint.setText(f"最近 {len(self._failures)} 次失败，选中后点「重试选中」可重下")

    def retry_failure(self) -> None:
        row = self.failures.currentRow()
        if row < 0:
            self.failure_hint.setText("先在失败记录里选中一行")
            return
        cell = self.failures.item(row, 1)
        repo = cell.text() if cell is not None else "-"
        if not repo or repo == "-":
            self.failure_hint.setText("该记录没有可重试的模型名")
            return
        self.browse.current_repo = repo
        self.selected_repo.setText(repo)
        self._log(f"重试下载 {repo}")
        self.start_download()

    def clear_failures(self) -> None:
        self._failures.clear()
        self.failures.setRowCount(0)
        self.failure_hint.setText("暂无失败记录")

    def close(self) -> bool:
        if self._downloading and not self._ask(
            "下载进行中", "下载尚未完成，关闭窗口将停止下载（已下载部分保留为 .part）。确实关闭？"
        ):
            return False
        self._stop_requested = True
        return True

    def _ask(self, title: str, question: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes


class _InstalledPanel(QWidget):  # pragma: no cover - Qt 控件
    """已安装页：模型仓库（扫描 / 打开 / 删除）与运行用 GGUF 分组树。"""

    repoActivated = Signal(str)

    def __init__(self, state: LocllmState, fit_resolver: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from fcmd.apps.locllm._locllmw_qt_models import ModelTree

        self.state = state
        self._local_models: list[LocalModel] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        repos = Card("模型仓库（下载目录）")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        refresh = button("刷新仓库列表")
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        open_selected = button("打开目录")
        open_selected.clicked.connect(self.open_selected)
        bar.addWidget(open_selected)
        self.delete_button = button("删除模型", kind="danger")
        self.delete_button.clicked.connect(self.delete_selected)
        bar.addWidget(self.delete_button)
        bar.addWidget(hint_label("仅在二次确认后删除整个模型目录"))
        bar.addStretch(1)
        self.repo_hint = hint_label("")
        bar.addWidget(self.repo_hint)
        repos.body.addLayout(bar)
        self.repos = make_table(
            ("仓库", "大小", "文件", "状态", "路径"),
            widths=(220, 90, 70, 150, 320),
            stretch_columns=(0, 4),
            parent=repos,
        )
        self.repos.setMinimumHeight(120)
        self.repos.cellDoubleClicked.connect(lambda _row, _column: self._open_repo())
        repos.body.addWidget(self.repos)
        layout.addWidget(repos)

        self.card = Card("运行模型（模型目录中的 GGUF）")
        self.tree = ModelTree(state, self.card)
        if fit_resolver is not None:
            self.tree.set_fit_resolver(fit_resolver)
        self.tree.tree.setMinimumHeight(240)
        self.card.body.addWidget(self.tree)
        layout.addWidget(self.card, 1)
        self.refresh()

    def refresh(self) -> None:
        root = _llmconfig.models_dir()
        self.repo_hint.setText(f"正在扫描 {root} …")
        submit(
            scan_local_models,
            root,
            on_done=self._render,
            on_error=lambda message: self._repo_error(message, root),
        )

    def _repo_error(self, message: str, root: Path) -> None:
        self.repo_hint.setText(f"扫描本地模型失败: {message}")
        if not root.is_dir():
            self.repo_hint.setText(f"模型目录不存在: {root}")

    def _render(self, models: list[LocalModel]) -> None:
        self._local_models = models
        root = _llmconfig.models_dir()
        self.repos.setRowCount(len(models))
        for row, item in enumerate(models):
            for column, text in enumerate(_locllmw_models.local_row(item)):
                cell = make_table_item(text)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.repo)
                self.repos.setItem(row, column, cell)
        if models:
            total = sum(item.size for item in models)
            self.repo_hint.setText(f"模型仓库 {len(models)} 个，合计 {human_size(total)}；运行模型按 GGUF 文件计数")
        elif not root.is_dir():
            self.repo_hint.setText(f"模型目录不存在: {root}")
        else:
            self.repo_hint.setText(f"模型目录 {root} 下没有模型")
        self.card.setTitle(f"运行模型（模型目录中的 GGUF，共 {len(self.state.models)} 个）")
        self.tree.refresh()

    def _current(self) -> LocalModel | None:
        row = self.repos.currentRow()
        if row < 0:
            return None
        cell = self.repos.item(row, 0)
        repo = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
        return next((item for item in self._local_models if item.repo == repo), None)

    def open_selected(self) -> None:
        model = self._current()
        target = model.path if model is not None else _llmconfig.models_dir()
        if not target.is_dir():
            QMessageBox.warning(self, "目录不存在", f"目录不存在:\n{target}")
            return
        try:
            _locllmw_models.open_path(target)
        except OSError as exc:
            QMessageBox.critical(self, "打开目录失败", str(exc))

    def _open_repo(self) -> None:
        model = self._current()
        if model is None:
            self.open_selected()
            return
        self.repoActivated.emit(model.repo)

    def delete_selected(self) -> None:
        model = self._current()
        if model is None:
            self.repo_hint.setText("请先在列表中选择要删除的模型")
            return
        answer = QMessageBox.question(
            self,
            "确认删除模型",
            f"将从磁盘删除以下目录：\n\n{model.path}\n\n"
            f"大小：{human_size(model.size)}，文件数：{model.files}\n\n该操作不可撤销，确定删除？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.delete_button.setEnabled(False)
        self.repo_hint.setText(f"正在删除 {model.repo} …")
        submit(
            delete_local_model,
            _llmconfig.models_dir(),
            model.repo,
            on_done=self._deleted,
            on_error=self._delete_error,
            on_finished=lambda: self.delete_button.setEnabled(True),
        )

    def _deleted(self, removed: object) -> None:
        self.repo_hint.setText(f"已删除 {removed}")
        self.refresh()

    def _delete_error(self, message: str) -> None:
        self.repo_hint.setText(f"删除失败: {message}")
        QMessageBox.critical(self, "删除失败", message)


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

    def _scan_env(self) -> tuple[list[tuple[Path, int]], list[ModelEntry], dict[str, GgufPrecision], str | None]:
        root = _llmconfig.models_dir()
        convertible = _locllmw_convert._find_convertible(root)
        entries: list[ModelEntry] = []
        precisions: dict[str, GgufPrecision] = {}
        try:
            if root.is_dir():
                entries = llmmodel.scan_models(root)
        except OSError:
            entries = []
        store = PrecisionCache()
        for entry in entries:
            try:
                precision = store.get(entry.path) or detect_precision(entry.path, tensors=False)
            except OSError:
                continue
            precisions[llmenv.path_key(entry.path)] = precision
            store.remember(entry.path, precision)
        store.save()
        tag: str | None = None
        try:
            tag = llmengine.resolve_engine().tag
        except ServeError:
            tag = None
        return convertible, entries, precisions, tag

    def _env_error(self, message: str) -> None:
        self.models_hint.setText(f"扫描模型目录失败: {message}")

    def _apply_env(
        self, payload: tuple[list[tuple[Path, int]], list[ModelEntry], dict[str, GgufPrecision], str | None]
    ) -> None:
        convertible, _entries, _precisions, tag = payload
        if tag and not self.tag.text().strip():
            self.tag.setText(tag)
        self._apply_convertible(convertible)
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

    def _scan_env(self) -> tuple[dict[str, str], list[ModelEntry], dict[str, GgufPrecision]]:
        info = {"engine": "-", "quantizer": "-", "version": "-"}
        try:
            engine = llmengine.resolve_engine()
        except ServeError as exc:
            info["engine"] = f"未找到（{exc}）"
        else:
            info["engine"] = f"{engine.tag} · {engine.exe}"
            try:
                info["version"] = llmengine.probe_version(engine)
            except ServeError:
                info["version"] = "-"
        try:
            info["quantizer"] = str(llmqnt.resolve_quantize_exe())
        except llmqnt.QuantizeError as exc:
            info["quantizer"] = f"未找到（{exc}）"
        root = _llmconfig.models_dir()
        entries: list[ModelEntry] = []
        try:
            if root.is_dir():
                entries = llmmodel.scan_models(root)
        except OSError:
            entries = []
        precisions: dict[str, GgufPrecision] = {}
        store = PrecisionCache()
        for entry in entries:
            try:
                precision = store.get(entry.path) or detect_precision(entry.path, tensors=False)
            except OSError:
                continue
            precisions[llmenv.path_key(entry.path)] = precision
            store.remember(entry.path, precision)
        store.save()
        return info, entries, precisions

    def _env_error(self, message: str) -> None:
        self.info_values["engine"].setText(f"检测失败（{message}）")

    def _apply_env(self, payload: tuple[dict[str, str], list[ModelEntry], dict[str, GgufPrecision]]) -> None:
        info, entries, precisions = payload
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
    """模型中心的五个子页；宿主窗口提供运行模型树的适配回调。"""

    def __init__(
        self,
        state: LocllmState,
        parent: QWidget | None = None,
        *,
        fit_resolver: Any = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.browse = _BrowsePanel(state, self)
        self.download = _DownloadPanel(state, self.browse, self)
        self.installed = _InstalledPanel(state, fit_resolver, self)
        self.download._installed_panel = self.installed
        self.convert = _ConvertPanel(self)
        self.quant = _QuantPanel(self)
        self.tabs = QTabWidget(self)
        for name, page in zip(
            _TAB_NAMES, (self.browse, self.download, self.installed, self.convert, self.quant), strict=True
        ):
            self.tabs.addTab(page, name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.tabs.setCurrentIndex(_TAB_NAMES.index(_DEFAULT_TAB))
        self.browse.repoSelected.connect(self._on_repo_selected)
        self.installed.repoActivated.connect(self._open_repo)
        self.card = self.installed.card
        self.tree = self.installed.tree
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

    def _on_repo_selected(self, repo: str) -> None:
        if repo:
            self.download.selected_repo.setText(repo)

    def _open_repo(self, repo: str) -> None:
        self.tabs.setCurrentIndex(_TAB_NAMES.index("浏览"))
        self.browse.keyword.setText(repo)
        self.browse.load_repo(repo)

    def settings_dict(self) -> dict[str, Any]:
        """导出下载参数，供窗口写回 ``locllmw.json``。"""
        return self.download.settings_dict()

    def refresh_config(self) -> None:
        """模型目录变化后同步下载目标与已安装列表，并刷新转换 / 量化环境。"""
        self.download.refresh_config()
        self.installed.refresh()
        self.convert.refresh_env()
        if self.tabs.currentIndex() == _TAB_NAMES.index("量化"):
            self.quant.refresh_env()

    def close(self) -> bool:
        """确认并停止下载 / 转换 / 量化任务；返回 ``False`` 表示用户取消关闭。"""
        self.download._save_settings()
        return self.download.close()
