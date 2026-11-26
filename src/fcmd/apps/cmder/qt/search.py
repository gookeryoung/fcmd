"""搜索对话框：掩码 / 内容 / 日期条件与结果表。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._search import SearchFilters, parse_datetime, parse_size
from fcmd.gui.widgets import Card, button, hint_label, make_table, make_table_item, pick_dir
from fcmd.gui.workers import submit

if TYPE_CHECKING:
    pass

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = ["SearchDialog"]


class SearchDialog(QDialog):
    """搜索：文件名 / 内容（含过滤器）与归档内搜索。"""

    def __init__(self, owner: Any, directory: Path, *, archive: bool = False) -> None:
        super().__init__(owner)
        self.owner = owner
        self.directory = directory
        self.archive_mode = archive
        self.hits: list[Any] = []
        self.setWindowTitle("搜索压缩包" if archive else "搜索")
        self.setMinimumSize(900, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("目录"), 0, 0)
        self.directory_edit = QLineEdit(str(directory), self)
        form.addWidget(self.directory_edit, 0, 1)
        browse = button("浏览…")
        browse.clicked.connect(self._pick_directory)
        form.addWidget(browse, 0, 2)
        form.addWidget(QLabel("模式"), 1, 0)
        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("文件名 glob（*.txt）或内容正则")
        form.addWidget(self.pattern_edit, 1, 1)
        self.content = QCheckBox("搜索内容", self)
        form.addWidget(self.content, 1, 2)
        form.addWidget(QLabel("扩展名"), 2, 0)
        self.extension_edit = QLineEdit(self)
        self.extension_edit.setPlaceholderText(".txt（仅内容搜索）")
        form.addWidget(self.extension_edit, 2, 1)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.case_sensitive = QCheckBox("区分大小写", self)
        self.whole_word = QCheckBox("整词", self)
        flags.addWidget(self.case_sensitive)
        flags.addWidget(self.whole_word)
        flags.addStretch(1)
        form.addLayout(flags, 2, 2)
        outer.addLayout(form)

        filters = Card("过滤器（可选）")
        filter_grid = QGridLayout()
        filter_grid.setHorizontalSpacing(8)
        filter_grid.setVerticalSpacing(4)
        self.mask_edit = QLineEdit(filters)
        self.exclude_edit = QLineEdit(filters)
        self.size_min = QLineEdit(filters)
        self.size_max = QLineEdit(filters)
        self.after_edit = QLineEdit(filters)
        self.before_edit = QLineEdit(filters)
        filter_grid.addWidget(QLabel("包含掩码"), 0, 0)
        filter_grid.addWidget(self.mask_edit, 0, 1)
        filter_grid.addWidget(QLabel("排除掩码"), 0, 2)
        filter_grid.addWidget(self.exclude_edit, 0, 3)
        filter_grid.addWidget(QLabel("最小 / 最大字节"), 1, 0)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.addWidget(self.size_min)
        size_row.addWidget(self.size_max)
        filter_grid.addLayout(size_row, 1, 1)
        filter_grid.addWidget(QLabel("修改时间 ≥ / ≤"), 1, 2)
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.addWidget(self.after_edit)
        time_row.addWidget(self.before_edit)
        filter_grid.addLayout(time_row, 1, 3)
        filters.body.addLayout(filter_grid)
        outer.addWidget(filters)

        self.results = make_table(
            ("路径", "行", "内容"),
            widths=(520, 70, 320),
            stretch_columns=(0, 2),
            parent=self,
        )
        self.results.setMinimumHeight(260)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        outer.addWidget(self.results, 1)
        self.status = hint_label("输入模式后点「搜索」")
        outer.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.search_button = button("搜索", kind="primary")
        self.search_button.clicked.connect(self._search)
        actions.addWidget(self.search_button)
        actions.addStretch(1)
        for text, handler in (
            ("定位", self._reveal),
            ("加入选择", self._adopt),
            ("复制到…", lambda: self._transfer(move=False)),
            ("移动到…", lambda: self._transfer(move=True)),
            ("删除", self._delete),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        close = button("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        outer.addLayout(actions)
        self.pattern_edit.returnPressed.connect(self._search)

    def _pick_directory(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_dir(self, "选择搜索目录", self.directory_edit.text())
        if chosen:
            self.directory_edit.setText(chosen)

    def _filters(self) -> SearchFilters:
        return SearchFilters(
            min_size=parse_size(self.size_min.text()),
            max_size=parse_size(self.size_max.text()),
            modified_after=parse_datetime(self.after_edit.text()),
            modified_before=parse_datetime(self.before_edit.text()),
        )

    def _masked(self, hits: list[Any]) -> list[Any]:
        """按包含 / 排除掩码过滤命中（目录名与文件名都参与）。"""
        from fcmd.apps.cmder._ops import match_names

        include = self.mask_edit.text().strip()
        exclude = self.exclude_edit.text().strip()
        result = hits
        if include:
            names = match_names([hit.path.name for hit in result], include)
            result = [hit for hit in result if hit.path.name in names]
        if exclude:
            names = match_names([hit.path.name for hit in result], exclude)
            result = [hit for hit in result if hit.path.name not in names]
        return result

    def _search(self) -> None:
        pattern = self.pattern_edit.text().strip()
        directory = Path(self.directory_edit.text().strip())
        if not pattern:
            self.status.setText("搜索内容不能为空")
            return
        self.search_button.setEnabled(False)
        self.status.setText("正在搜索…")
        if self.archive_mode:
            from fcmd.apps.cmder._arcsearch import search_archive

            submit(
                search_archive,
                directory,
                pattern,
                content=self.content.isChecked(),
                case_sensitive=self.case_sensitive.isChecked(),
                whole_word=self.whole_word.isChecked(),
                on_done=self._show_archive,
                on_error=self._failed,
                on_finished=lambda: self.search_button.setEnabled(True),
            )
            return
        submit(
            core.search_paths,
            directory,
            pattern,
            content=self.content.isChecked(),
            extension=self.extension_edit.text(),
            case_sensitive=self.case_sensitive.isChecked() or None,
            whole_word=self.whole_word.isChecked(),
            filters=self._filters(),
            on_done=self._show_hits,
            on_error=self._failed,
            on_finished=lambda: self.search_button.setEnabled(True),
        )

    def _failed(self, message: str) -> None:
        self.status.setText(f"搜索失败：{message}")

    def _show_hits(self, hits: list[Any]) -> None:
        self.hits = self._masked(list(hits))
        self.results.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            self.results.setItem(row, 0, make_table_item(str(hit.path)))
            self.results.setItem(row, 1, make_table_item(str(hit.line or "")))
            self.results.setItem(row, 2, make_table_item(hit.text))
        self.status.setText(f"命中 {len(self.hits)} 条")

    def _show_archive(self, report: Any) -> None:
        self.hits = list(report.hits)
        self.results.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            self.results.setItem(row, 0, make_table_item(f"{hit.archive}:{hit.member}"))
            self.results.setItem(row, 1, make_table_item(str(hit.line or "")))
            self.results.setItem(row, 2, make_table_item(hit.text))
        self.status.setText(f"压缩包内命中 {len(self.hits)} 条")

    def _selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in sorted({index.row() for index in self.results.selectedIndexes()}):
            if 0 <= row < len(self.hits):
                hit = self.hits[row]
                if isinstance(getattr(hit, "path", None), Path):
                    paths.append(hit.path)
        return paths

    def _reveal(self) -> None:
        for path in self._selected_paths()[:1]:
            self.owner.reveal_path(path)

    def _adopt(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.status.setText(self.owner.adopt_paths(paths))

    def _transfer(self, *, move: bool) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        target = pick_dir(self, "选择目标目录")
        if target:
            self.owner.transfer_paths(paths, move=move, destination=Path(target))

    def _delete(self) -> None:
        paths = self._selected_paths()
        if paths and self.owner.ask("删除", f"删除选中的 {len(paths)} 个文件（回收站）？"):
            self.owner.delete_paths(paths)
