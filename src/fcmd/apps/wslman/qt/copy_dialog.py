"""复制实例对话框（ADR 0046 P2/P3 模块拆分）。

从 ``dialogs.py`` 独立出来，避免单文件超过 800 行架构预算；通过
``_wslmanw_qt`` re-export，调用方仍用 ``_owner._CopyDistroDialog``。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import Distro
from fcmd.gui.widgets import button, fit_dialog_to_content, hint_label

if TYPE_CHECKING:
    from fcmd.apps.wslman._wslmanw_qt import WslmanQtWindow


class _CopyDistroDialog(QDialog):  # pragma: no cover - 模态对话框
    """复制实例：填新实例名称与目标目录，默认值按源实例自动生成。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        distro: Distro,
        size: tuple[int | None, bool],
        taken: Sequence[str],
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"复制实例 {distro.name}")
        self.setMinimumWidth(620)
        self._owner = owner
        self._distro = distro
        self._taken = tuple(taken)
        self._payload: tuple[str, Path] | None = None
        self._auto_target = True
        estimate, estimated = size

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                f"源实例：{distro.name}\n"
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n\n"
                "复制会先导出源实例，再以新名称导入到目标目录；源实例保持不变。"
            )
        )

        default_name = w.copy_default_name(distro.name, self._taken)
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        self.name = QLineEdit(default_name, self)
        form.addWidget(QLabel("新实例名称", self), 0, 0)
        form.addWidget(self.name, 0, 1)
        self.target = QLineEdit(str(w.copy_default_dir(owner.settings, distro.base_path, default_name)), self)
        self.target.textEdited.connect(self._on_target_edited)
        self.name.textChanged.connect(self._on_name_changed)
        form.addWidget(QLabel("目标目录", self), 1, 0)
        form.addWidget(self.target, 1, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_dir)
        form.addWidget(choose, 1, 2)
        outer.addLayout(form)
        outer.addWidget(
            hint_label(
                "目标目录必须为空或不存在；默认放在源实例所属工作目录的 install 子目录。"
                "导入后可像普通实例一样启动、迁移或备份。"
            )
        )

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        actions.addStretch(1)
        start = button("开始复制", kind="primary")
        start.clicked.connect(self._start)
        actions.addWidget(start)
        outer.addLayout(actions)
        fit_dialog_to_content(self, min_width=620)

    def _on_name_changed(self, text: str) -> None:
        if self._auto_target:
            name = text.strip() or "copy"
            self.target.setText(str(w.copy_default_dir(self._owner.settings, self._distro.base_path, name)))

    def _on_target_edited(self, _text: str) -> None:
        self._auto_target = False

    def _choose_dir(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = QFileDialog.getExistingDirectory(self, "选择复制目标目录（必须为空）")
        if chosen:
            self._auto_target = False
            self.target.setText(chosen)

    def _start(self) -> None:
        name = self.name.text().strip()
        problem = w.copy_name_problem(name, self._taken)
        if problem is not None:
            QMessageBox.critical(self, "复制实例", problem)
            return
        target_text = self.target.text().strip()
        if not target_text:
            QMessageBox.critical(self, "复制实例", "请填写目标目录")
            return
        self._payload = (name, Path(target_text))
        self.accept()

    def payload(self) -> tuple[str, Path] | None:
        """确认后的 ``(新实例名称, 目标目录)``；取消时为 ``None``。"""
        return self._payload
