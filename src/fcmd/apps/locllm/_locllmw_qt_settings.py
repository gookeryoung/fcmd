"""locllmw（Qt）设置页：存储、下载参数、模型转换与引擎。

目录口径与 CLI 完全一致：模型目录只有 ``models_dir`` 一个来源（:mod:`fcmd.apps.locllm._llmconfig`），
引擎根目录单独登记；改动即时写配置并通知其它页刷新（``configChanged``）。
引擎安装 / 切换仍复用 :mod:`fcmd.apps.locllm.llmengine` 的既有实现，页面只做展示与
「设为默认」这类轻动作，重活留给 ``fcmd llmengine``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import _llmconfig, _locllmw_convert, _locllmw_models, llmconvert, llmenv
from fcmd.apps.locllm import _locllmw_settings as settings_logic
from fcmd.apps.locllm._locllmw_models import open_path
from fcmd.apps.locllm.llmbase import EngineInfo, ServeError
from fcmd.gui.qt.widgets import (
    Card,
    HintLabel,
    PageScroll,
    PathRow,
    button,
    hint_label,
    make_table,
    make_table_item,
)

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["SettingsPage", "engine_rows_values"]

ENGINE_COLUMNS = ("标签", "来源", "llama-server")


def engine_rows_values(engines: list[EngineInfo]) -> list[tuple[str, str, str]]:
    """已安装引擎表的行（与 :data:`ENGINE_COLUMNS` 对应）。"""
    return [(info.tag, info.source or "-", str(info.exe)) for info in engines]


class SettingsPage(QWidget):  # pragma: no cover - Qt 控件
    """设置页：存储目录、下载参数、模型转换、引擎根目录与运行状态。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
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
        layout.addWidget(self._build_download())
        layout.addWidget(self._build_convert())
        layout.addWidget(self._build_engines())
        layout.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------ #
    # 存储
    # ------------------------------------------------------------------ #
    def _build_storage(self) -> Card:
        card = Card("存储")
        row = QHBoxLayout()
        row.addWidget(hint_label("模型目录", wrap=False))
        self.models_path = PathRow(value="", directory=True, show_reset=True)
        row.addWidget(self.models_path, 1)
        self.models_open = button("打开", kind="ghost")
        row.addWidget(self.models_open)
        card.body.addLayout(row)
        self.models_source = hint_label("来源：默认")
        card.body.addWidget(self.models_source)
        self.migration_note = HintLabel("")
        card.body.addWidget(self.migration_note)
        card.body.addWidget(hint_label("下载与转换产物统一写入模型目录；旧配置里的其它目录不再扫描。"))
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
        settings = _locllmw_models.settings_from_dict(self.state.settings.download)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.addWidget(QLabel("下载源"), 0, 0, Qt.AlignmentFlag.AlignRight)
        self.download_source = QComboBox(card)
        self.download_source.addItems(list(_locllmw_models._SOURCE_LABELS.values()))
        self.download_source.setCurrentText(_locllmw_models._SOURCE_LABELS.get(settings.source, "ModelScope"))
        grid.addWidget(self.download_source, 0, 1)
        grid.addWidget(QLabel("并发"), 0, 2, Qt.AlignmentFlag.AlignRight)
        self.download_workers = QSpinBox(card)
        self.download_workers.setRange(1, _locllmw_models._MAX_WORKERS)
        self.download_workers.setValue(settings.workers)
        grid.addWidget(self.download_workers, 0, 3)
        self.download_verify = QCheckBox("校验 SHA256", card)
        self.download_verify.setChecked(settings.verify)
        grid.addWidget(self.download_verify, 0, 4)
        grid.addWidget(QLabel("包含掩码"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.download_include = QLineEdit(settings.include, card)
        self.download_include.setPlaceholderText("如 *.gguf，留空表示全部")
        grid.addWidget(self.download_include, 1, 1, 1, 3)
        grid.addWidget(QLabel("排除掩码"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.download_exclude = QLineEdit(settings.exclude, card)
        self.download_exclude.setPlaceholderText("如 *.bin，留空表示不排除")
        grid.addWidget(self.download_exclude, 2, 1, 1, 3)
        card.body.addLayout(grid)
        card.body.addWidget(
            hint_label("下载目标固定为上方模型目录；修改后自动保存，参数在模型列表点击「下载」时生效。")
        )
        self.download_source.currentIndexChanged.connect(lambda _index: self._save_download())
        self.download_workers.valueChanged.connect(lambda _value: self._save_download())
        self.download_verify.toggled.connect(lambda _checked: self._save_download())
        self.download_include.editingFinished.connect(self._save_download)
        self.download_exclude.editingFinished.connect(self._save_download)
        return card

    def _save_download(self) -> None:
        settings = _locllmw_models.DownloadGuiSettings(
            source=_locllmw_models._SOURCE_KEYS.get(self.download_source.currentText(), "modelscope"),
            workers=min(max(self.download_workers.value(), 1), _locllmw_models._MAX_WORKERS),
            verify=self.download_verify.isChecked(),
            include=self.download_include.text(),
            exclude=self.download_exclude.text(),
        )
        self.state.settings.download = _locllmw_models.settings_to_dict(settings)
        self.state.persist()
        self.state.log("已自动保存下载设置")
        self.state.status("已自动保存下载设置")

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _build_convert(self) -> Card:
        """HF safetensors → GGUF 的持久化参数；转换入口在模型列表行内。"""
        card = Card("模型转换")
        settings = _locllmw_convert.convert_settings_from_dict(self.state.settings.convert)
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
        self.convert_tag.setPlaceholderText("留空自动取当前默认引擎")
        grid.addWidget(self.convert_tag, 0, 3)
        grid.addWidget(QLabel("解释器"), 1, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_python = QLineEdit(settings.python, card)
        self.convert_python.setPlaceholderText("留空自动探测已安装 torch / safetensors / numpy")
        grid.addWidget(self.convert_python, 1, 1, 1, 3)
        grid.addWidget(QLabel("转换脚本"), 2, 0, Qt.AlignmentFlag.AlignRight)
        self.convert_script = QLineEdit(settings.script, card)
        self.convert_script.setPlaceholderText(_locllmw_convert._SCRIPT_PLACEHOLDER)
        grid.addWidget(self.convert_script, 2, 1, 1, 2)
        pick_script = button("浏览…")
        pick_script.clicked.connect(self._pick_convert_script)
        grid.addWidget(pick_script, 2, 3)
        self.convert_overwrite = QCheckBox("覆盖已存在的输出文件", card)
        self.convert_overwrite.setChecked(settings.overwrite)
        grid.addWidget(self.convert_overwrite, 3, 0, 1, 2)
        card.body.addLayout(grid)
        card.body.addWidget(hint_label("模型列表中的可转换模型会显示「转换」按钮；参数修改后自动保存。"))
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
        settings = _locllmw_convert.ConvertSettings(
            outtype=self.convert_outtype.currentText().strip() or llmconvert.CONVERT_OUTTYPES[0],
            tag=self.convert_tag.text().strip(),
            python=self.convert_python.text().strip(),
            script=self.convert_script.text().strip(),
            overwrite=self.convert_overwrite.isChecked(),
        )
        self.state.settings.convert = _locllmw_convert.convert_settings_to_dict(settings)
        self.state.persist()
        self.state.log("已自动保存模型转换设置")
        self.state.status("已自动保存模型转换设置")

    # ------------------------------------------------------------------ #
    # 引擎
    # ------------------------------------------------------------------ #
    def _build_engines(self) -> Card:
        card = Card("引擎")
        row = QHBoxLayout()
        row.addWidget(hint_label("引擎根目录", wrap=False))
        self.engines_path = PathRow(value="", directory=True, show_reset=True)
        row.addWidget(self.engines_path, 1)
        self.engines_open = button("打开", kind="ghost")
        row.addWidget(self.engines_open)
        card.body.addLayout(row)
        self.engines_source = hint_label("来源：默认")
        card.body.addWidget(self.engines_source)
        self.engine_status_label = QLabel("-", card)
        self.engine_status_label.setObjectName("Value")
        self.engine_status_label.setWordWrap(True)
        card.body.addWidget(self.engine_status_label)

        self.engines_table = make_table(ENGINE_COLUMNS, widths=(120, 120, 420), stretch_columns=(2,))
        self.engines_table.setMinimumHeight(120)
        card.body.addWidget(self.engines_table)
        actions = QHBoxLayout()
        self.use_engine_button = button("设为默认")
        self.check_engine_button = button("体检当前引擎", kind="ghost")
        self.refresh_engine_button = button("刷新", kind="ghost")
        actions.addWidget(self.use_engine_button)
        actions.addWidget(self.check_engine_button)
        actions.addWidget(self.refresh_engine_button)
        actions.addStretch(1)
        card.body.addLayout(actions)
        self.engine_hint = HintLabel(
            "安装 / 切换引擎用 `fcmd llmengine install <tag>` / `fcmd llmengine use <tag>`；"
            "本页只做登记与默认引擎切换。"
        )
        card.body.addWidget(self.engine_hint)

        self.engines_path.committed.connect(lambda text: self._save_path("engines_root", text))
        self.engines_path.reset.clicked.connect(lambda: self._save_path("engines_root", None))
        self.engines_open.clicked.connect(lambda: open_path(_llmconfig.engines_root()))
        self.use_engine_button.clicked.connect(self._use_engine)
        self.check_engine_button.clicked.connect(self._check_engine)
        self.refresh_engine_button.clicked.connect(self.refresh)
        return card

    # ------------------------------------------------------------------ #
    # 刷新与保存
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取配置与引擎列表。"""
        directories = settings_logic.directory_rows()
        for _name, path, source in directories:
            self.models_path.set_value(path)
            self.models_source.setText(f"来源：{source}")
        for _name, path, source in settings_logic.engine_rows():
            self.engines_path.set_value(path)
            self.engines_source.setText(f"来源：{source}")
        note = settings_logic.migration_note()
        self.migration_note.setText(note)
        self.migration_note.setVisible(bool(note))
        self.refresh_engines()

    def refresh_engines(self) -> None:
        """刷新引擎摘要与已安装列表。"""
        try:
            status = settings_logic.engine_status()
            self._engines = settings_logic.installed_engines()
        except (OSError, ServeError) as exc:
            self.engine_status_label.setText(f"引擎信息不可用：{exc}")
            return
        self.engine_status_label.setText(
            f"当前引擎：{status.current}\n根目录：{status.root}\n已安装：{status.installed} 个"
        )
        rows = engine_rows_values(self._engines)
        self.engines_table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for column, text in enumerate(values):
                self.engines_table.setItem(index, column, make_table_item(text))

    def _save_path(self, key: str, value: str | None) -> None:
        """写入目录项并通知其它页（模型目录变化会触发重新扫描）。"""
        try:
            resolved = settings_logic.apply_path(key, value or None)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        label = "模型目录" if key == "models_dir" else "引擎根目录"
        self.state.log(f"已自动保存{label}: {resolved}")
        self.state.status(f"已自动保存{label}：{resolved}")
        self.refresh()
        if key == "models_dir":
            self.state.refresh_models()

    def _selected_engine(self) -> EngineInfo | None:
        row = self.engines_table.currentRow()
        if 0 <= row < len(self._engines):
            return self._engines[row]
        return None

    def _use_engine(self) -> None:
        info = self._selected_engine()
        if info is None:
            self.engine_hint.setText("先在已安装引擎表中选择一行")
            return
        try:
            chosen = settings_logic.select_engine(info.tag)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "切换失败", str(exc))
            return
        self.state.log(f"默认引擎已切换为 {chosen.tag} · {chosen.exe}")
        self.engine_hint.setText(f"默认引擎：{chosen.tag} · {chosen.exe}")
        self.refresh_engines()

    def _check_engine(self) -> None:
        try:
            check = settings_logic.check_engine_tag(None)
        except (OSError, ServeError) as exc:
            QMessageBox.critical(self, "体检失败", str(exc))
            return
        self.engine_hint.setText(f"{check.name}: {check.status} · {check.detail}")
        self.state.log(f"引擎体检 {check.name}: {check.status} · {check.detail}")
