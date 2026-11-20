"""llmw（Qt）设置页：存储、引擎、下载参数与模型转换。

目录口径与 CLI 完全一致：模型目录只有 ``models_dir`` 一个来源（:mod:`fcmd.apps.llm._llmconfig`），
引擎目录独立登记；改动即时写配置并通知其它页刷新（``configChanged``）。
引擎安装 / 切换仍复用 :mod:`fcmd.apps.llm.llmengine` 的既有实现，页面只做展示与
「设为默认」这类轻动作，重活留给 ``fcmd llmengine``。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import _llmw_convert, _llmw_models, llmconvert, llmenv
from fcmd.apps.llm import _llmw_settings as settings_logic
from fcmd.apps.llm._llmw_models import open_path
from fcmd.apps.llm.llmbase import EngineInfo, ServeError
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    HintLabel,
    PageScroll,
    PathRow,
    RowListWidget,
    StatusTag,
    button,
    hint_label,
    link_button,
)

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["SettingsPage", "engine_rows_values"]


def engine_rows_values(engines: list[EngineInfo]) -> list[tuple[str, str, str]]:
    """已安装引擎的 ``(标签, 来源, llama-server)`` 摘要（供测试与调试）。"""
    return [(info.tag, info.source or "-", str(info.exe)) for info in engines]


class _EngineRow(QFrame):  # pragma: no cover - Qt 控件
    """已安装引擎行：单行紧凑布局（标签 + 状态 + 可执行文件 + 行内动作）。"""

    useRequested = Signal(str)

    def __init__(self, info: EngineInfo, *, current: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("EngineRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "false")
        source = info.source or "-"
        self.setToolTip(f"{info.tag}\n{info.exe}\n来源：{source}")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(8)

        name = QLabel(info.tag, self)
        name.setObjectName("EngineItemName")
        name.setMaximumWidth(220)
        row.addWidget(name, 0)

        exe = ElidedLabel(str(info.exe), mode=Qt.TextElideMode.ElideMiddle, parent=self)
        exe.setObjectName("Hint")
        row.addWidget(exe, 1)

        self.use_button = None
        if current:
            row.addWidget(StatusTag("默认", tone="success", parent=self), 0)
        else:
            self.use_button = button("设为默认")
            self.use_button.setToolTip("把该引擎设为默认引擎")
            self.use_button.clicked.connect(lambda: self.useRequested.emit(self.info.tag))
            row.addWidget(self.use_button, 0)

        variant = settings_logic.engine_variant(info)
        category = StatusTag(variant, tone="muted" if variant == "CPU" else "accent", parent=self)
        tooltip = f"构建类别：{variant}"
        if info.path_prepend:
            tooltip += "\n启动时前置到 PATH：" + "、".join(info.path_prepend)
        category.setToolTip(tooltip)
        row.addWidget(category, 0)
        self.setMinimumHeight(self.fontMetrics().height() + 14)

    def set_selected(self, selected: bool) -> None:
        """切换选中态边框（列表当前行变化时由页面同步）。"""
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SettingsPage(QWidget):  # pragma: no cover - Qt 控件
    """设置页：存储、引擎（目录与已安装列表）、下载参数与模型转换。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._engines: list[EngineInfo] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)
        layout.addWidget(self._build_storage())
        layout.addWidget(self._build_engines())
        layout.addWidget(self._build_download())
        layout.addWidget(self._build_convert())
        layout.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------ #
    # 存储
    # ------------------------------------------------------------------ #
    def _build_storage(self) -> Card:
        """存储卡片：只放模型目录与迁移提示；引擎独立成卡片紧随其后。"""
        card = Card("存储")
        row = QHBoxLayout()
        row.addWidget(hint_label("模型目录", wrap=False))
        self.models_path = PathRow(value="", directory=True, show_reset=True, browse_text="更改")
        row.addWidget(self.models_path, 1)
        self.models_open = button("打开", kind="ghost")
        row.addWidget(self.models_open)
        card.body.addLayout(row)
        self.migration_note = HintLabel("")
        card.body.addWidget(self.migration_note)
        self.models_path.committed.connect(lambda text: self._save_path("models_dir", text))
        self.models_path.reset.clicked.connect(lambda: self._save_path("models_dir", None))
        self.models_open.clicked.connect(lambda: open_path(llmenv.models_dir()))
        return card

    # ------------------------------------------------------------------ #
    # 下载
    # ------------------------------------------------------------------ #
    def _build_download(self) -> Card:
        """下载参数：源、并发、校验与包含 / 排除掩码；目标仍是模型目录。"""
        card = Card("下载")
        settings = _llmw_models.settings_from_dict(self.state.settings.download)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("下载源"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.download_source = QComboBox(card)
        self.download_source.addItems(list(_llmw_models._SOURCE_LABELS.values()))
        self.download_source.setCurrentText(_llmw_models._SOURCE_LABELS.get(settings.source, "ModelScope"))
        grid.addWidget(self.download_source, 0, 1)
        grid.addWidget(QLabel("并发"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.download_workers = QSpinBox(card)
        self.download_workers.setRange(1, _llmw_models._MAX_WORKERS)
        self.download_workers.setValue(settings.workers)
        grid.addWidget(self.download_workers, 0, 3)
        self.download_verify = QCheckBox("校验 SHA256", card)
        self.download_verify.setChecked(settings.verify)
        grid.addWidget(self.download_verify, 0, 4)
        grid.addWidget(QLabel("包含掩码"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.download_include = QLineEdit(settings.include, card)
        self.download_include.setPlaceholderText("如 *.gguf，留空为全部")
        grid.addWidget(self.download_include, 1, 1, 1, 3)
        grid.addWidget(QLabel("排除掩码"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.download_exclude = QLineEdit(settings.exclude, card)
        self.download_exclude.setPlaceholderText("如 *.bin，留空不排除")
        grid.addWidget(self.download_exclude, 2, 1, 1, 3)
        card.body.addLayout(grid)
        self.download_source.currentIndexChanged.connect(lambda _index: self._save_download())
        self.download_workers.valueChanged.connect(lambda _value: self._save_download())
        self.download_verify.toggled.connect(lambda _checked: self._save_download())
        self.download_include.editingFinished.connect(self._save_download)
        self.download_exclude.editingFinished.connect(self._save_download)
        return card

    def _save_download(self) -> None:
        settings = _llmw_models.DownloadGuiSettings(
            source=_llmw_models._SOURCE_KEYS.get(self.download_source.currentText(), "modelscope"),
            workers=min(max(self.download_workers.value(), 1), _llmw_models._MAX_WORKERS),
            verify=self.download_verify.isChecked(),
            include=self.download_include.text(),
            exclude=self.download_exclude.text(),
        )
        self.state.settings.download = _llmw_models.settings_to_dict(settings)
        self.state.persist()
        self.state.log("已自动保存下载设置")
        self.state.status("已自动保存下载设置")

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _build_convert(self) -> Card:
        """HF safetensors → GGUF 的持久化参数；转换入口在模型列表行内。"""
        card = Card("模型转换")
        settings = _llmw_convert.convert_settings_from_dict(self.state.settings.convert)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("转换精度"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_outtype = QComboBox(card)
        self.convert_outtype.addItems(list(llmconvert.CONVERT_OUTTYPES))
        self.convert_outtype.setCurrentText(settings.outtype)
        grid.addWidget(self.convert_outtype, 0, 1)
        grid.addWidget(QLabel("引擎 tag"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.convert_tag = QLineEdit(settings.tag, card)
        self.convert_tag.setPlaceholderText("留空用默认引擎")
        grid.addWidget(self.convert_tag, 0, 3)
        grid.addWidget(QLabel("解释器"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_python = QLineEdit(settings.python, card)
        self.convert_python.setPlaceholderText("留空自动探测 torch / safetensors / numpy")
        grid.addWidget(self.convert_python, 1, 1, 1, 3)
        grid.addWidget(QLabel("转换脚本"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_script = QLineEdit(settings.script, card)
        self.convert_script.setPlaceholderText(_llmw_convert._SCRIPT_PLACEHOLDER)
        grid.addWidget(self.convert_script, 2, 1, 1, 2)
        pick_script = button("浏览…")
        pick_script.clicked.connect(self._pick_convert_script)
        grid.addWidget(pick_script, 2, 3)
        self.convert_overwrite = QCheckBox("覆盖已存在的输出文件", card)
        self.convert_overwrite.setChecked(settings.overwrite)
        grid.addWidget(self.convert_overwrite, 3, 0, 1, 2)
        card.body.addLayout(grid)
        self.convert_outtype.currentIndexChanged.connect(lambda _index: self._save_convert())
        self.convert_tag.editingFinished.connect(self._save_convert)
        self.convert_python.editingFinished.connect(self._save_convert)
        self.convert_script.editingFinished.connect(self._save_convert)
        self.convert_overwrite.toggled.connect(lambda _checked: self._save_convert())
        return card

    def _pick_convert_script(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择转换脚本或 llama.cpp 源码包",
            "",
            "转换脚本 / 源码包 (*.py *.zip);;所有文件 (*.*)",
        )
        if chosen:
            self.convert_script.setText(chosen)
            self._save_convert()

    def _save_convert(self) -> None:
        settings = _llmw_convert.ConvertSettings(
            outtype=self.convert_outtype.currentText().strip() or llmconvert.CONVERT_OUTTYPES[0],
            tag=self.convert_tag.text().strip(),
            python=self.convert_python.text().strip(),
            script=self.convert_script.text().strip(),
            overwrite=self.convert_overwrite.isChecked(),
        )
        self.state.settings.convert = _llmw_convert.convert_settings_to_dict(settings)
        self.state.persist()
        self.state.log("已自动保存模型转换设置")
        self.state.status("已自动保存模型转换设置")

    # ------------------------------------------------------------------ #
    # 引擎
    # ------------------------------------------------------------------ #
    def _build_engines(self) -> Card:
        """引擎卡片（位于存储下方）：刷新与标题同行，目录行右侧是导入动作。

        不设状态摘要行，不设底部动作行；列表高度随已安装引擎数量伸缩，
        空态 / 错误态由同一提示行承接。
        """
        card = Card("引擎")
        self.refresh_engine_button = link_button("刷新", tooltip="重新读取已安装引擎与默认引擎")
        card.add_title_action(self.refresh_engine_button)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(hint_label("引擎目录", wrap=False))
        self.engines_path = PathRow(value="", directory=True, show_reset=True, browse_text="更改")
        row.addWidget(self.engines_path, 1)
        self.import_engine_dir_button = button("导入目录", kind="ghost")
        self.import_engine_dir_button.setToolTip("选择已编译的 llama.cpp 目录，复制到引擎目录并设为默认")
        row.addWidget(self.import_engine_dir_button)
        self.import_engine_archive_button = button("导入压缩包", kind="ghost")
        self.import_engine_archive_button.setToolTip("从本地 llama.cpp / cudart zip 离线包安装引擎")
        row.addWidget(self.import_engine_archive_button)
        card.body.addLayout(row)

        self.engines_list = RowListWidget(card)
        self.engines_list.setObjectName("EngineList")
        self.engines_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.engines_list.setSpacing(4)
        self.engines_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.engines_list.setToolTip("双击一行可直接设为默认引擎")
        self.engines_empty = hint_label("还没有已安装引擎；用「导入目录」或「导入压缩包」添加。")
        self.engines_empty.setVisible(False)
        card.body.addWidget(self.engines_list)
        card.body.addWidget(self.engines_empty)

        self.engine_hint = HintLabel("")
        self.engine_hint.setVisible(False)
        card.body.addWidget(self.engine_hint)

        self.engines_path.committed.connect(lambda text: self._save_path("engines_root", text))
        self.engines_path.reset.clicked.connect(lambda: self._save_path("engines_root", None))
        self.import_engine_dir_button.clicked.connect(self._import_engine_dir)
        self.import_engine_archive_button.clicked.connect(self._import_engine_archive)
        self.refresh_engine_button.clicked.connect(self.refresh)
        self.engines_list.currentRowChanged.connect(self._on_engine_row_selected)
        self.engines_list.itemDoubleClicked.connect(self._on_engine_double_clicked)
        return card

    # ------------------------------------------------------------------ #
    # 刷新与保存
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取配置与引擎列表。"""
        directories = settings_logic.directory_rows()
        for _name, path, _source in directories:
            self.models_path.set_value(path)
        for _name, path, _source in settings_logic.engine_rows():
            self.engines_path.set_value(path)
        note = settings_logic.migration_note()
        self.migration_note.setText(note)
        self.migration_note.setVisible(bool(note))
        self.refresh_engines()

    def refresh_engines(self) -> None:
        """刷新已安装引擎列表；空态 / 错误态由提示行承接，列表随之收起。"""
        try:
            self._engines = settings_logic.installed_engines()
        except (OSError, ServeError) as exc:
            self._engines = []
            self.engines_list.clear()
            self.engines_list.setVisible(False)
            self.engines_empty.setText(f"引擎信息不可用：{exc}")
            self.engines_empty.setVisible(True)
            return

        self.engines_list.clear()
        for info in self._engines:
            list_item = QListWidgetItem(self.engines_list)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            row = _EngineRow(info, current=info.source == "current", parent=self.engines_list)
            row.useRequested.connect(self._use_engine_tag)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.engines_list.setItemWidget(list_item, row)
        self.engines_list.sync_row_widths()
        self.engines_list.setVisible(bool(self._engines))
        self._fit_engines_list()
        self.engines_empty.setText("还没有已安装引擎；用「导入目录」或「导入压缩包」添加。")
        self.engines_empty.setVisible(not self._engines)

    def _fit_engines_list(self) -> None:
        """把列表高度收到内容高度，超过 6 行后再出现滚动条。"""
        count = self.engines_list.count()
        if count == 0:
            self.engines_list.setFixedHeight(0)
            return
        heights = [self.engines_list.sizeHintForRow(index) for index in range(count)]
        spacing = self.engines_list.spacing()
        content = sum(heights) + spacing * (count + 2) + 2
        cap = max(heights) * 6 + spacing * 8 + 2
        self.engines_list.setFixedHeight(min(content, cap))

    def _on_engine_row_selected(self, current: int) -> None:
        """同步脚本行的选中态（只加粗边框，不影响内容）。"""
        for index in range(self.engines_list.count()):
            item = self.engines_list.item(index)
            row = self.engines_list.itemWidget(item)
            if isinstance(row, _EngineRow):
                row.set_selected(index == current)

    def _on_engine_double_clicked(self, item: QListWidgetItem) -> None:
        """双击引擎行直接设为默认。"""
        index = self.engines_list.row(item)
        if 0 <= index < len(self._engines):
            self._use_engine_tag(self._engines[index].tag)

    def _save_path(self, key: str, value: str | None) -> None:
        """写入目录项并通知其它页（模型目录变化会触发重新扫描）。"""
        try:
            resolved = settings_logic.apply_path(key, value or None)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        label = "模型目录" if key == "models_dir" else "引擎目录"
        self.state.log(f"已自动保存{label}: {resolved}")
        self.state.status(f"已自动保存{label}：{resolved}")
        self.refresh()
        if key == "models_dir":
            self.state.refresh_models()

    def _set_engine_hint(self, text: str) -> None:
        """显示引擎操作反馈；无内容时不占位。"""
        self.engine_hint.setText(text)
        self.engine_hint.setVisible(bool(text))

    def _use_engine_tag(self, tag: str) -> None:
        """把指定 tag 的引擎设为默认，并刷新列表。"""
        try:
            chosen = settings_logic.select_engine(tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换失败", str(exc))
            return
        self.state.log(f"默认引擎已切换为 {chosen.tag} · {chosen.exe}")
        self.state.status(f"默认引擎：{chosen.tag}")
        self._set_engine_hint(f"默认引擎：{chosen.tag} · {chosen.exe}")
        self.refresh_engines()

    def _ask_engine_tag(self, default: str) -> str | None:
        """询问引擎标签并做安全校验（只允许单级目录名）。"""
        text, accepted = QInputDialog.getText(self, "引擎标签", "标签（用于引擎目录名）：", text=default)
        if not accepted:
            return None
        tag = text.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
            QMessageBox.warning(
                self, "标签不合法", "标签只能包含字母、数字、点、下划线和短横线，且不能包含路径分隔符。"
            )
            return None
        return tag

    def _after_engine_install(self, info: EngineInfo, action: str) -> None:
        """注册 / 安装成功后切换默认引擎并刷新列表。"""
        try:
            settings_logic.select_engine(info.tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换失败", str(exc))
            return
        self.state.log(f"{action}引擎 {info.tag} · {info.exe}")
        self._set_engine_hint(f"{action}引擎：{info.tag} · {info.exe}")
        self.state.status(f"{action}引擎：{info.tag}")
        self.refresh()

    def _import_engine_dir(self) -> None:
        """选择已编译 llama.cpp 目录，复制到引擎目录并设为默认引擎。"""
        chosen = QFileDialog.getExistingDirectory(self, "选择 llama.cpp 目录")
        if not chosen:
            return
        source = Path(chosen)
        tag = self._ask_engine_tag(source.name or "local")
        if tag is None:
            return
        try:
            info, _actual = settings_logic.install_offline(tag, source, copy_directory=True)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._after_engine_install(info, "已导入")

    def _import_engine_archive(self) -> None:
        """选择一个或多个本地 zip，解压安装并设为默认引擎。"""
        paths, _selected = QFileDialog.getOpenFileNames(
            self,
            "选择 llama.cpp 压缩包",
            "",
            "压缩包 (*.zip);;所有文件 (*.*)",
        )
        if not paths:
            return
        archives = [Path(path) for path in paths]
        default = archives[0].stem
        try:
            for archive in archives:
                inferred = next((item.tag for item in settings_logic.inspect_offline(archive) if item.tag), None)
                if inferred:
                    default = inferred
                    break
        except (OSError, ServeError):
            pass
        tag = self._ask_engine_tag(default)
        if tag is None:
            return
        try:
            info = settings_logic.install_archives(tag, archives)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._after_engine_install(info, "已导入")
