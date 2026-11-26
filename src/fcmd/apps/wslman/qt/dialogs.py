"""wslman 对话框（ADR 0046 P1）。

从 ``_wslmanw_qt.py`` 迁出；通过 ``_wslmanw_qt`` 复用共享控件与业务函数。
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.apps.wslman import _images, _open
from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import Distro
from fcmd.apps.wslman._errors import WslmanError
from fcmd.apps.wslman._images import ImageEntry
from fcmd.apps.wslman._transfer import sha256_file
from fcmd.gui.widgets import (
    FormDialog,
    button,
    fit_dialog_to_content,
    hint_label,
    make_table,
    make_table_item,
    messages,
    pick_dir,
    pick_file,
    save_file,
)

from .._wslmanw_qt import WslmanQtWindow
from .config_dialog import _DistroConfigDialog  # noqa: F401 - 对外保持旧导入路径


class _OpenerDialog(FormDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑打开方式：名称、运行位置与命令模板。

    模板在这里就做语法与占位符校验（Windows 侧必须带 ``{dir}`` 或 ``{unc}``），
    避免保存后在文件页点开才报错。
    """

    def __init__(self, parent: QWidget, kind: str, opener: _open.Opener | None = None) -> None:
        super().__init__(
            parent,
            title=f"{'编辑' if opener else '添加'}{_open.KIND_LABELS[kind]}打开方式",
            margins=(18, 16, 18, 14),
        )
        self.setModal(True)
        self._kind = kind
        self._values = (
            opener.name if opener else "",
            opener.host if opener else _open.HOST_WINDOWS,
            opener.command if opener else "",
        )
        self.name = self.add_row("名称", QLineEdit(self._values[0], self))
        self.name.setPlaceholderText(f"例如 {_open.KIND_LABELS[kind]} 名称")
        self.host = self.add_row("运行位置", QComboBox(self))
        for value, label in _open.HOST_LABELS.items():
            self.host.addItem(label, value)
        index = self.host.findData(self._values[1])
        self.host.setCurrentIndex(index if index >= 0 else 0)
        self.host.setToolTip("Windows 宿主机里的 GUI 程序，或要在 WSL 实例内启动的终端程序")
        self.command = self.add_row("命令模板", QLineEdit(self._values[2], self))
        self.command.setPlaceholderText('code --remote wsl+{distro} "{dir}"')
        self.command.setToolTip("可执行命令；引号内的空格会保留")
        if opener is not None and opener.note:
            self.add_hint(opener.note)
        self.add_hint(
            "占位符：{dir} 实例内绝对路径、{unc} Windows UNC 路径、{distro} 实例名、{home} 家目录。"
            "Windows 宿主机条目必须包含 {dir} 或 {unc}；WSL 内条目以当前目录启动，可不写占位符。"
        )

        self.finish(cancel_text="取消", ok_text="保存", on_ok=self._on_save)
        self.name.setFocus()

    def _on_save(self) -> None:
        try:
            self._values = _open.validate_opener(self.name.text(), self.host.currentData(), self.command.text())
        except WslmanError as exc:
            messages.warn(self, "命令模板不可用", w._error_text(exc))
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self._values


class _UnregisterDialog(QDialog):  # pragma: no cover - 模态对话框
    """删除实例确认：手输实例名、可选先停止、可选先备份；明确写清磁盘会被删除。"""

    def __init__(self, owner: WslmanQtWindow, distro: Distro, size: tuple[int | None, bool]) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"删除实例 {distro.name}")
        self.setMinimumWidth(560)
        self._owner = owner
        self._distro = distro
        estimate, estimated = size

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n\n"
                "删除会移除该实例的注册信息，并删除其 ext4.vhdx 虚拟磁盘（不可恢复，无回收站）。\n"
                "需要保留数据请勾选「先导出备份」。"
            )
        )
        self.terminate_first = QCheckBox("先停止实例", self)
        self.terminate_first.setChecked(distro.state == "running")
        outer.addWidget(self.terminate_first)
        self.backup_enabled = QCheckBox("先导出备份到", self)
        self.backup_enabled.setChecked(bool(owner.config_data.require_backup_on_replace))
        outer.addWidget(self.backup_enabled)
        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.setSpacing(6)
        self.backup = QLineEdit(str(owner._backup_dir_path() / f"{distro.name}.tar"), self)
        backup_row.addWidget(self.backup, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_backup)
        backup_row.addWidget(choose)
        outer.addLayout(backup_row)
        outer.addWidget(hint_label(f"手输实例名以确认：{distro.name}"))
        self.typed = QLineEdit(self)
        self.typed.textChanged.connect(self._sync)
        outer.addWidget(self.typed)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = button("删除实例", kind="danger")
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        outer.addLayout(actions)
        self._sync()
        self.typed.setFocus()

    def _sync(self) -> None:
        self.confirm.setEnabled(w.unregister_confirm_enabled(self.typed.text(), self._distro.name))

    def _choose_backup(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = save_file(
            self,
            "选择备份文件",
            str(self._owner._backup_dir_path() / f"{self._distro.name}.tar"),
            "tar 归档 (*.tar)",
        )
        if chosen:
            self.backup.setText(chosen)

    def backup_path(self) -> Path | None:
        text = self.backup.text().strip()
        return Path(text) if self.backup_enabled.isChecked() and text else None


class _ShutdownDialog(QDialog):  # pragma: no cover - 模态对话框
    """``wsl --shutdown`` 确认：必须勾选影响说明。"""

    def __init__(self, owner: WslmanQtWindow) -> None:
        super().__init__(owner)
        self.setWindowTitle("关闭全部 WSL")
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label("会停止全部实例与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端。"))
        self.checked = QCheckBox("我了解上述影响", self)
        self.checked.toggled.connect(lambda _checked: self._sync())
        outer.addWidget(self.checked)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = button("关闭全部", kind="danger")
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        outer.addLayout(actions)
        self._sync()

    def _sync(self) -> None:
        self.confirm.setEnabled(w.shutdown_confirm_enabled(self.checked.isChecked()))


class _ImageManagerDialog(QDialog):  # pragma: no cover - 模态对话框
    """管理下载目录里的镜像包：导出、删除、清理清单缓存与未完成下载。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        directory: Path,
        entries: tuple[ImageEntry, ...],
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle("管理镜像")
        self.setMinimumWidth(820)
        self.directory = directory
        self._entries = entries
        self._items: list[tuple[Path, int, str, bool]] = []
        self.changed = False
        self.cache_cleared = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(f"下载目录：{directory}"))

        self.table = make_table(
            ("镜像包", "大小", "识别为", "状态"),
            widths=(430, 100, 160, 90),
            stretch_columns=(0,),
            parent=self,
        )
        self.table.setMinimumHeight(240)
        self.table.itemSelectionChanged.connect(self._on_select)
        outer.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.export_button = button("导出…")
        self.export_button.clicked.connect(self._export_selected)
        actions.addWidget(self.export_button)
        self.delete_button = button("删除", kind="danger")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        clean = button("清理缓存")
        clean.setToolTip("删除镜像清单缓存与下载目录里的未完成 .part 文件")
        clean.clicked.connect(self._clean_cache)
        actions.addWidget(clean)
        open_dir = button("打开目录")
        open_dir.clicked.connect(self._open_folder)
        actions.addWidget(open_dir)
        refresh = button("刷新")
        refresh.clicked.connect(self._reload)
        actions.addWidget(refresh)
        actions.addStretch(1)
        close = button("关闭", kind="primary")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)

        self.status = hint_label("选择一个镜像包后可导出或删除；.part 是未完成的下载")
        outer.addWidget(self.status)
        self._reload()
        fit_dialog_to_content(self, min_width=820)

    def _reload(self) -> None:
        """重扫下载目录：完整镜像包与 ``.part`` 未完成下载都列出来。"""
        self.table.setRowCount(0)
        self._items.clear()
        if not self.directory.is_dir():
            self.status.setText(f"下载目录不存在：{self.directory}")
            self._sync_actions()
            return
        try:
            paths = sorted(self.directory.iterdir(), key=lambda path: path.name.casefold())
        except OSError as exc:
            self.status.setText(f"无法读取下载目录：{exc}")
            self._sync_actions()
            return
        for path in paths:
            if not path.is_file():
                continue
            name = path.name.casefold()
            incomplete = name.endswith(".part")
            if not incomplete and not name.endswith(w._ARCHIVE_SUFFIXES):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if incomplete:
                distro, complete = "—", False
            else:
                info = w.inspect_archive(path, self._entries)
                distro, complete = info.distro, True
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, make_table_item(str(path)))
            self.table.setItem(row, 1, make_table_item(format_bytes(size)))
            self.table.setItem(row, 2, make_table_item(distro))
            self.table.setItem(row, 3, make_table_item("完整" if complete else "未完成"))
            self._items.append((path, size, distro, complete))
        if self.table.rowCount():
            self.table.selectRow(0)
            self.status.setText("选择一个镜像包后可导出或删除；.part 是未完成的下载")
        else:
            self.status.setText("下载目录里没有可管理的镜像包")
        self._sync_actions()

    def _current(self) -> tuple[Path, int, str, bool] | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _on_select(self) -> None:
        self._sync_actions()

    def _sync_actions(self) -> None:
        current = self._current()
        self.export_button.setEnabled(current is not None and current[3])
        self.delete_button.setEnabled(current is not None)

    def _export_selected(self) -> None:
        archive = self._current()
        if archive is None or not archive[3]:
            self.status.setText("未完成的 .part 不能导出；请先下载完成")
            return
        chosen = pick_dir(self, "导出镜像包到目录", str(self.directory))
        if not chosen:
            return
        target = Path(chosen) / archive[0].name
        if target.resolve() == archive[0].resolve():
            self.status.setText("导出目录就是当前下载目录")
            return
        if target.exists() and not _owner.confirm(self, "覆盖镜像包", f"目标已存在，覆盖？\n{target}"):
            return
        try:
            shutil.copy2(archive[0], target)
        except OSError as exc:
            messages.error(self, "导出失败", f"无法复制镜像包：\n{exc}")
            return
        self.status.setText(f"已导出到 {target}")

    def _delete_selected(self) -> None:
        archive = self._current()
        if archive is None:
            return
        if not _owner.confirm(self, "删除镜像包", f"永久删除？\n{archive[0]}\n大小：{format_bytes(archive[1])}"):
            return
        try:
            archive[0].unlink()
        except OSError as exc:
            messages.error(self, "删除失败", f"无法删除镜像包：\n{exc}")
            return
        self.changed = True
        self.status.setText(f"已删除 {archive[0].name}")
        self._reload()

    def _clean_cache(self) -> None:
        targets: list[Path] = []
        cache = _images.cache_path()
        if cache.is_file():
            targets.append(cache)
        if self.directory.is_dir():
            with contextlib.suppress(OSError):
                targets.extend(sorted(self.directory.glob("*.part")))
        if not targets:
            self.status.setText("没有可清理的缓存或未完成下载")
            return
        if not _owner.confirm(self, "清理缓存", f"删除镜像清单缓存与 {len(targets)} 个缓存 / 未完成文件？"):
            return
        removed = 0
        for path in targets:
            try:
                path.unlink()
                removed += 1
                if path == cache:
                    self.cache_cleared = True
            except OSError:
                continue
        self.changed = self.changed or removed > 0
        self.status.setText(f"已清理 {removed} 个缓存 / 未完成文件")
        self._reload()

    def _open_folder(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messages.error(self, "打开失败", f"无法创建下载目录：\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory)))


class _InstallLocalDialog(QDialog):  # pragma: no cover - 模态对话框
    """安装镜像：扫描目录 / 选文件、查看识别结果、填名称与安装目录、可选替换。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        entries: tuple[ImageEntry, ...],
        *,
        preset: Path | None = None,
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle("安装实例")
        self.setMinimumWidth(820)
        self._owner = owner
        self._entries = entries
        self._preset = preset
        self._archives: dict[int, Any] = {}
        self._payload: tuple[Path, str, Path, bool, Path | None] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel("扫描目录"))
        self.directory = QLineEdit(owner._local_start_dir(), self)
        top.addWidget(self.directory, 1)
        pick_button = button("选择文件…")
        pick_button.clicked.connect(self._add_file)
        top.addWidget(pick_button)
        rescan = button("重新扫描")
        rescan.clicked.connect(self._scan)
        top.addWidget(rescan)
        outer.addLayout(top)

        self.table = make_table(("镜像包", "大小", "识别为"), widths=(440, 100, 180), stretch_columns=(0,), parent=self)
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._start())
        outer.addWidget(self.table, 1)

        info_grid = QGridLayout()
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(4)
        info_grid.setColumnStretch(1, 1)
        self._info: dict[str, QLabel] = {}
        for row, key in enumerate(("实例", "大小", "来源", "校验状态")):
            info_grid.addWidget(hint_label(key, wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_grid.addWidget(value, row, 1)
            self._info[key] = value
        outer.addLayout(info_grid)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        self.name = QLineEdit(self)
        form.addWidget(QLabel("实例名称"), 0, 0)
        form.addWidget(self.name, 0, 1)
        self.target = QLineEdit(self)
        form.addWidget(QLabel("安装目录"), 1, 0)
        form.addWidget(self.target, 1, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_dir)
        form.addWidget(choose, 1, 2)
        self.replace = QCheckBox("替换同名实例（会先删除现有实例，必要时先备份）", self)
        form.addWidget(self.replace, 2, 1)
        outer.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        start = button("开始安装", kind="primary")
        start.clicked.connect(self._start)
        footer.addWidget(start)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        footer.addStretch(1)
        outer.addLayout(footer)
        self._scan()
        fit_dialog_to_content(self, min_width=820)

    def _scan(self) -> None:
        self.table.setRowCount(0)
        self._archives.clear()
        paths = list(w.local_archives(Path(self.directory.text().strip() or ".")))
        # 行内「安装」带进来的归档一定在表里，即使它的目录与扫描目录不同。
        if self._preset is not None and self._preset.is_file() and self._preset not in paths:
            paths.insert(0, self._preset)
        for path in paths:
            self._add(w.inspect_archive(path, self._entries))
        preset_row = self._row_of(self._preset) if self._preset is not None else None
        if preset_row is not None:
            self.table.selectRow(preset_row)
        elif not self._archives:
            self._info["校验状态"].setText(
                "该目录没有可安装的镜像包（tar / wsl / vhdx / zip / 7z / rar 等），可用「选择文件…」指定"
            )
        elif self.table.rowCount():
            self.table.selectRow(0)

    def _row_of(self, path: Path | None) -> int | None:
        """归档路径在表格里的行号（用于预选行内「安装」带进来的包）。"""
        if path is None:
            return None
        target = path.resolve()
        for row, archive in self._archives.items():
            if archive.path.resolve() == target:
                return row
        return None

    def _add(self, archive: Any) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, make_table_item(str(archive.path)))
        self.table.setItem(row, 1, make_table_item(format_bytes(archive.size)))
        self.table.setItem(row, 2, make_table_item(archive.distro))
        self._archives[row] = archive

    def _add_file(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_file(
            self,
            "选择本地镜像包",
            self.directory.text(),
            "镜像包 (*.tar *.tar.gz *.tgz *.tar.bz2 *.tbz *.tar.xz *.txz *.wsl *.vhdx "
            "*.zip *.7z *.rar *.gz *.bz2 *.xz);;全部文件 (*.*)",
        )
        if not chosen:
            return
        self._add(w.inspect_archive(Path(chosen), self._entries))
        self.table.selectRow(self.table.rowCount() - 1)

    def _choose_dir(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_dir(self, "选择安装目录（必须为空）")
        if chosen:
            self.target.setText(chosen)

    def _current(self) -> Any:
        row = self.table.currentRow()
        return self._archives.get(row)

    def _on_select(self) -> None:
        archive = self._current()
        if archive is None:
            return
        arch = archive.arch if archive.arch != "未知" else "架构未知"
        self._info["实例"].setText(f"{archive.distro}（{arch}）")
        self._info["大小"].setText(format_bytes(archive.size))
        self._info["来源"].setText(archive.source)
        if archive.expected_sha256 is None:
            self._info["校验状态"].setText(w.verification_label(None, None))
        else:
            self._info["校验状态"].setText("正在计算 sha256…")
            archive_path = archive.path
            expected = archive.expected_sha256
            _owner.submit(
                lambda: w.verification_label(expected, sha256_file(archive_path)),
                on_done=self._info["校验状态"].setText,
            )
        self.name.setText(archive.distro)
        self.target.setText(str(self._owner._install_dir_path() / archive.distro))

    def _start(self) -> None:
        archive = self._current()
        if archive is None:
            messages.error(self, "安装镜像", "请先选择一个镜像包")
            return
        name = self.name.text().strip()
        target_text = self.target.text().strip()
        if not name:
            messages.error(self, "安装镜像", "请填写实例名称")
            return
        if not target_text:
            messages.error(self, "安装镜像", "请选择安装目录")
            return
        replace = self.replace.isChecked()
        if replace and not _owner.confirm(self, "替换实例", f"替换 {name} 会先删除现有实例（运行中会先停止），继续？"):
            return
        backup: Path | None = None
        if replace and _owner.load_config().require_backup_on_replace:
            chosen = save_file(
                self,
                "替换前先备份到",
                str(self._owner._backup_dir_path() / f"{name}.tar"),
                "tar 归档 (*.tar)",
            )
            if not chosen:
                return
            backup = Path(chosen)
        self._payload = (archive.path, name, Path(target_text), replace, backup)
        self.accept()

    def payload(self) -> tuple[Path, str, Path, bool, Path | None] | None:
        """确认后的安装参数；取消时为 ``None``。"""
        return self._payload
