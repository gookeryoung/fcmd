"""复制实例对话框（ADR 0046 P2/P3 模块拆分）。

从 ``dialogs.py`` 独立出来，避免单文件超过 800 行架构预算；通过
``_wslmanw_qt`` re-export，调用方仍用 ``_owner._CopyDistroDialog``。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLineEdit

from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import Distro
from fcmd.gui.widgets import FormDialog, button, messages, pick_dir

if TYPE_CHECKING:
    from fcmd.apps.wslman._wslmanw_qt import WslmanQtWindow


class _CopyDistroDialog(FormDialog):  # pragma: no cover - 模态对话框
    """复制实例：填新实例名称与目标目录，默认值按源实例自动生成。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        distro: Distro,
        size: tuple[int | None, bool],
        taken: Sequence[str],
    ) -> None:
        super().__init__(owner, title=f"复制实例 {distro.name}", min_width=620)
        self._owner = owner
        self._distro = distro
        self._taken = tuple(taken)
        self._payload: tuple[str, Path] | None = None
        self._auto_target = True
        estimate, estimated = size

        self.add_hint(
            f"源实例：{distro.name}\n"
            f"BasePath: {distro.base_path or '未知'}\n"
            f"占用: {format_bytes(estimate, estimated=estimated)}\n\n"
            "复制会先导出源实例，再以新名称导入到目标目录；源实例保持不变。"
        )

        default_name = w.copy_default_name(distro.name, self._taken)
        self.name = self.add_row("新实例名称", QLineEdit(default_name, self))
        self._choose = button("选择…")
        self._choose.clicked.connect(self._choose_dir)
        self.target = self.add_row(
            "目标目录",
            QLineEdit(str(w.copy_default_dir(owner.settings, distro.base_path, default_name)), self),
            self._choose,
        )
        self.target.textEdited.connect(self._on_target_edited)
        self.name.textChanged.connect(self._on_name_changed)

        self.add_hint(
            "目标目录必须为空或不存在；默认放在源实例所属工作目录的 install 子目录。"
            "导入后可像普通实例一样启动、迁移或备份。"
        )

        self.finish(ok_text="开始复制", on_ok=self._start)

    def _on_name_changed(self, text: str) -> None:
        if self._auto_target:
            name = text.strip() or "copy"
            self.target.setText(str(w.copy_default_dir(self._owner.settings, self._distro.base_path, name)))

    def _on_target_edited(self, _text: str) -> None:
        self._auto_target = False

    def _choose_dir(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_dir(self, "选择复制目标目录（必须为空）")
        if chosen:
            self._auto_target = False
            self.target.setText(chosen)

    def _start(self) -> None:
        name = self.name.text().strip()
        problem = w.copy_name_problem(name, self._taken)
        if problem is not None:
            messages.error(self, "复制实例", problem)
            return
        target_text = self.target.text().strip()
        if not target_text:
            messages.error(self, "复制实例", "请填写目标目录")
            return
        self._payload = (name, Path(target_text))
        self.accept()

    def payload(self) -> tuple[str, Path] | None:
        """确认后的 ``(新实例名称, 目标目录)``；取消时为 ``None``。"""
        return self._payload
