"""wslman 窗口方法（ADR 0046 P2/P3 机械迁移）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner

from ._compat import (  # noqa: F401
    Any,
    Distro,
    ImageEntry,
    ItemRow,
    Mapping,
    Path,
    QDialog,
    QListWidgetItem,
    QPushButton,
    QWidget,
    Sequence,
    _files,
    _initenv,
    _open,
    threading,
)


class LifecycleMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _persist_settings(self, geometry: str) -> None:
        _owner.w.save_settings(
            _owner.w.WslmanwSettings(
                geometry=geometry,
                work_dir=self.settings.work_dir,
                work_dirs=self.settings.work_dirs,
                mirror=self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto",
                max_downloads=_owner.w.download_limit(self.settings.max_downloads),
                sidebar_collapsed=self.nav.is_collapsed(),
                image_filter=self._image_filter_key,
                image_sort=self.image_sort.currentData() or _owner.w.DEFAULT_IMAGE_SORT,
                image_source=self.image_source.currentData() or _owner.w.DEFAULT_IMAGE_SOURCE,
                distro_terminal_dirs=self.settings.distro_terminal_dirs,
                dialog_geometry=self.settings.dialog_geometry,
                distro_init_tasks=self.settings.distro_init_tasks,
                ide_openers=self.settings.ide_openers,
                agent_openers=self.settings.agent_openers,
                ide_opener=self.settings.ide_opener,
                agent_opener=self.settings.agent_opener,
            ),
            self._own_settings_path,
        )

    def _on_close(self) -> bool:
        if self._busy and not _owner.confirm(self, "任务进行中", "有任务正在执行，取消并退出？"):
            return False
        if self._downloads and not _owner.confirm(self, "下载进行中", "还有镜像正在下载，取消并退出？"):
            return False
        self._cancel.set()
        for task in self._downloads.values():
            task.cancel.set()
        self._closing = True
        if self._poll is not None:
            self._poll.stop()
        for timer in (self._filter_timer,):
            if timer is not None:
                timer.stop()
        return True
