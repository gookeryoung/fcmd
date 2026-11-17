"""locllmw（Qt）设置页：存储、引擎与「关于与诊断」。

目录口径与 Tk 版完全一致：模型目录只有 ``models_dir`` 一个来源（:mod:`fcmd.apps.locllm._llmconfig`），
引擎根目录单独登记；改动即时写配置并通知其它页刷新（``configChanged``）。
引擎安装 / 切换仍复用 :mod:`fcmd.apps.locllm.llmengine` 的既有实现，页面只做展示与
「设为默认」这类轻动作，重活留给 ``fcmd llmengine``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from fcmd.apps.locllm import _llmconfig, llmenv
from fcmd.apps.locllm import _locllmw_settings as settings_logic
from fcmd.apps.locllm._locllmw_models import open_path
from fcmd.apps.locllm.llmbase import EngineInfo, ServeError
from fcmd.gui.qt.widgets import Card, HintLabel, KeyValueGrid, PathRow, button, hint_label, make_table, make_table_item

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["SettingsPage", "engine_rows_values"]

ENGINE_COLUMNS = ("标签", "来源", "llama-server")


def engine_rows_values(engines: list[EngineInfo]) -> list[tuple[str, str, str]]:
    """已安装引擎表的行（与 :data:`ENGINE_COLUMNS` 对应）。"""
    return [(info.tag, info.source or "-", str(info.exe)) for info in engines]


class SettingsPage(QWidget):  # pragma: no cover - Qt 控件
    """设置页：存储目录、引擎根目录与运行状态、关于与诊断。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._engines: list[EngineInfo] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_storage())
        layout.addWidget(self._build_engines())
        layout.addWidget(self._build_about(), 1)
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
        self.models_save = button("保存")
        self.models_open = button("打开", kind="ghost")
        row.addWidget(self.models_save)
        row.addWidget(self.models_open)
        card.body.addLayout(row)
        self.models_source = hint_label("来源：默认")
        card.body.addWidget(self.models_source)
        self.migration_note = HintLabel("")
        card.body.addWidget(self.migration_note)
        card.body.addWidget(hint_label("下载与转换产物统一写入模型目录；旧配置里的其它目录不再扫描。"))
        self.models_save.clicked.connect(lambda: self._save_path("models_dir", self.models_path.value()))
        self.models_path.reset.clicked.connect(lambda: self._save_path("models_dir", None))
        self.models_open.clicked.connect(lambda: open_path(llmenv.models_dir()))
        return card

    # ------------------------------------------------------------------ #
    # 引擎
    # ------------------------------------------------------------------ #
    def _build_engines(self) -> Card:
        card = Card("引擎")
        row = QHBoxLayout()
        row.addWidget(hint_label("引擎根目录", wrap=False))
        self.engines_path = PathRow(value="", directory=True, show_reset=True)
        row.addWidget(self.engines_path, 1)
        self.engines_save = button("保存")
        self.engines_open = button("打开", kind="ghost")
        row.addWidget(self.engines_save)
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

        self.engines_save.clicked.connect(lambda: self._save_path("engines_root", self.engines_path.value()))
        self.engines_path.reset.clicked.connect(lambda: self._save_path("engines_root", None))
        self.engines_open.clicked.connect(lambda: open_path(_llmconfig.engines_root()))
        self.use_engine_button.clicked.connect(self._use_engine)
        self.check_engine_button.clicked.connect(self._check_engine)
        self.refresh_engine_button.clicked.connect(self.refresh)
        return card

    # ------------------------------------------------------------------ #
    # 关于
    # ------------------------------------------------------------------ #
    def _build_about(self) -> Card:
        card = Card("关于与诊断")
        self.about_grid = KeyValueGrid(("项目", "值", "来源"), card)
        card.body.addWidget(self.about_grid)
        row = QHBoxLayout()
        self.config_open = button("打开配置文件", kind="ghost")
        self.refresh_about = button("刷新", kind="ghost")
        row.addWidget(self.config_open)
        row.addWidget(self.refresh_about)
        row.addStretch(1)
        card.body.addLayout(row)
        self.config_open.clicked.connect(lambda: open_path(_llmconfig.config_path()))
        self.refresh_about.clicked.connect(self.refresh)
        return card

    # ------------------------------------------------------------------ #
    # 刷新与保存
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取配置、引擎列表与诊断信息。"""
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
        self.about_grid.set_rows(settings_logic.ui_rows())
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
        self.state.log(f"已更新{('模型目录' if key == 'models_dir' else '引擎根目录')}: {resolved}")
        self.state.status(f"已保存：{resolved}")
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
