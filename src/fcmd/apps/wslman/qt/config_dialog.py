"""wslman 实例配置对话框。

从 :mod:`fcmd.apps.wslman.qt.dialogs` 拆出：这个对话框包含「初始化环境」与
「迁移配置文件」两张卡片、纵向滚动区与屏幕夹取逻辑，单独成文件以控制单文件行数。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._distro import Distro
from fcmd.gui.widgets import PageScroll, button, fit_dialog_to_content, hint_label

from .._wslmanw_qt import WslmanQtWindow, _ConfigMigrateSection, _InitEnvSection


class _DistroConfigDialog(QDialog):  # pragma: no cover - 模态对话框
    """实例配置：设为默认发行版、终端默认目录，以及就地完成的环境初始化卡片。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        distro: Distro,
        cwd: str,
        *,
        selected: Sequence[str] | None = None,
        instances: Sequence[str] = (),
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"配置 {distro.name}")
        self.setToolTip(f"实例：{distro.name}")
        self._distro = distro
        self._clamping = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        # 两张卡片都可能展开：内容超过屏幕时用滚动区承接，底部保存按钮固定可见。
        self.scroll = PageScroll(self, margins=(0, 0, 0, 0))
        outer.addWidget(self.scroll, 1)
        content = self.scroll.body

        self.make_default = QCheckBox("设为默认发行版", self)
        self.make_default.setChecked(distro.default)
        if distro.default:
            self.make_default.setEnabled(False)
            self.make_default.setToolTip("该实例已是默认发行版；设置为其它实例后才会取消")
        content.addWidget(self.make_default)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("终端默认目录", self), 0, 0)
        self.cwd = QLineEdit(cwd or w.DEFAULT_TERMINAL_CWD, self)
        self.cwd.setPlaceholderText(w.DEFAULT_TERMINAL_CWD)
        form.addWidget(self.cwd, 0, 1)
        reset = button("默认 ~")
        reset.clicked.connect(lambda: self.cwd.setText(w.DEFAULT_TERMINAL_CWD))
        form.addWidget(reset, 0, 2)
        content.addLayout(form)
        content.addWidget(
            hint_label("打开终端时作为 wsl --cd 的目录；可填 ~（用户主目录）或 /home/me/project 这类 Linux 路径。")
        )

        # 初始化环境就地铺在配置对话框里：状态、勾选与三个动作都在同一层，不再开二级对话框。
        # 打开配置只回显上次检测结果，不自动进入实例（``wsl -d`` 会把停止的实例拉起来）。
        settings = getattr(owner, "settings", None)
        cached_status = w.env_status_for(settings, distro.name) if settings is not None else None
        self.init_env = _InitEnvSection(
            self,
            distro,
            selected,
            status=cached_status,
            on_detected=lambda status: getattr(owner, "_save_env_status", lambda *_: None)(distro.name, status),
            state_provider=self._live_state,
            package_mirror=w.package_mirror_for(settings, distro.name) if settings is not None else "auto",
            on_package_mirror_changed=lambda value: getattr(owner, "_save_package_mirror", lambda *_: None)(
                distro.name, value
            ),
        )
        content.addWidget(self.init_env)

        # 配置迁移单独一张卡：要选来源、看项目进度与冲突，比塞进「初始化环境」更清楚。
        # 同样只回显上次扫描结果（不启动实例、不重扫），扫描完成后立即写回缓存。
        self.config_migrate = _ConfigMigrateSection(
            self,
            distro,
            instances=instances,
            status=w.config_status_for(settings, distro.name) if settings is not None else None,
            restore=w.config_restore_for(settings, distro.name) if settings is not None else None,
            on_scanned=lambda snapshot: getattr(owner, "_save_config_status", lambda *_: None)(distro.name, snapshot),
            on_restored=lambda record: getattr(owner, "_save_config_restore", lambda *_: None)(distro.name, record),
            state_provider=self._live_state,
        )
        self.ssh_migrate = self.config_migrate  # 旧属性名：迁移卡片已从 SSH 扩展为配置文件
        content.addWidget(self.config_migrate)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = button("保存", kind="primary")
        save.clicked.connect(self.accept)
        actions.addWidget(save)
        outer.addLayout(actions)
        fit_dialog_to_content(self, min_width=680)

    def _live_state(self) -> str | None:
        """读取实例当前状态：窗口视图优先，必要时刷新 ``wsl -l -v``（不会启动实例）。"""
        owner = self.parent()
        view = getattr(owner, "view", None)
        if view is None:
            return None
        found = view.find(self._distro.name)
        if found is not None and found.state == "running":
            return found.state
        try:
            fresh = cli._view(cli._build_environment()).find(self._distro.name)
        except Exception:
            return found.state if found is not None else None
        return fresh.state if fresh is not None else (found.state if found is not None else None)

    def showEvent(self, event: Any) -> None:
        """显示时按内容收口并夹回屏幕；不自动检测，避免打开配置就把停止的实例启动。"""
        super().showEvent(event)
        # 上次展开态关窗会把「展开后的高度」存进几何；本次卡片默认收起，
        # 原样恢复会多出大块底部空白，所以每次显示都按内容收口一次。
        fit_dialog_to_content(self, min_width=680)
        self._clamp_to_screen()

    def adjustSize(self) -> None:
        """卡片展开 / 收起时按内容高度调整对话框，但不超过屏幕。

        ``PageScroll`` 自己不会把内容高度传给对话框的 ``sizeHint``；这里显式取内容
        容器的高度，让展开后对话框尽量长高、收起后再缩回，同时把上限留给屏幕。
        """
        super().adjustSize()
        if not self.isVisible() or not hasattr(self, "scroll"):
            return
        screen = self.screen() or _owner.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_height = max(frame.height() - self.height(), 0)
        max_height = max(available.height() - extra_height, 240)
        chrome = max(self.height() - self.scroll.viewport().height(), 0)
        content = self.scroll.widget().sizeHint().height()
        desired = min(max(content + chrome, 320), max_height)
        if desired != self.height():
            self.resize(self.width(), desired)
        self._clamp_to_screen()

    def resizeEvent(self, event: Any) -> None:
        """卡片展开 / 收起会 adjustSize：尺寸变化后重新夹回屏幕，别把底部按钮顶出去。"""
        super().resizeEvent(event)
        if self.isVisible():
            self._clamp_to_screen()

    def _clamp_to_screen(self) -> None:
        """把对话框收进当前屏幕可用区域；优先保证底部的保存 / 取消按钮可见。"""
        if self._clamping:
            return
        screen = self.screen() or _owner.QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_width = max(frame.width() - self.width(), 0)
        extra_height = max(frame.height() - self.height(), 0)
        max_width = max(available.width() - extra_width, 320)
        max_height = max(available.height() - extra_height, 240)
        self._clamping = True
        try:
            width = min(self.width(), max_width)
            height = min(self.height(), max_height)
            if (width, height) != (self.width(), self.height()):
                self.resize(width, height)
            # move() 作用于窗口框架，而 geometry() 是客户区；用 frameGeometry 算增量，
            # 否则标题栏高度会让「刚好贴底」的对话框再多出几个像素。
            frame = self.frameGeometry()
            x = min(max(frame.x(), available.left()), available.left() + max(available.width() - frame.width(), 0))
            y = min(max(frame.y(), available.top()), available.top() + max(available.height() - frame.height(), 0))
            position = self.pos()
            if (x, y) != (frame.x(), frame.y()):
                self.move(position.x() + x - frame.x(), position.y() + y - frame.y())
        finally:
            self._clamping = False

    def _close_allowed(self) -> bool:
        """初始化 / 迁移在后台进行时允许关闭，但先说明任务不会中止。"""
        if self.init_env.busy and not _owner.confirm(self, "初始化仍在运行", "命令会在后台继续执行；现在关闭对话框？"):
            return False
        if self.config_migrate.busy and not _owner.confirm(
            self, "配置迁移仍在进行", "写入命令会在后台继续执行；现在关闭对话框？"
        ):
            return False
        self.init_env.stop()
        self.config_migrate.stop()
        return True

    def reject(self) -> None:
        if self._close_allowed():
            super().reject()

    def accept(self) -> None:
        if self._close_allowed():
            super().accept()

    def payload(self) -> tuple[bool, str]:
        """返回 ``(是否设为默认, 终端默认目录)``；目录为空时回落 ``~``。"""
        make_default = self.make_default.isChecked() and not self._distro.default
        return make_default, self.cwd.text().strip() or w.DEFAULT_TERMINAL_CWD
