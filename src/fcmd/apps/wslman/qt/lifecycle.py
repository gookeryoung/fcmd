"""wslman 窗口方法（ADR 0046 P2/P3 机械迁移）。"""

from __future__ import annotations

from dataclasses import replace

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
        """把界面上「当前活跃项」与 ``self.settings`` 里的缓存合并后落盘。

        用 ``replace`` 而不是重新构造 ``WslmanwSettings``：白名单式构造会静默丢掉
        没列出来的缓存字段（``distro_env_status`` / ``distro_config_status`` /
        ``distro_opener_paths`` 等），表现为「持久化了但下次打开又没了」。
        """
        _owner.w.save_settings(
            replace(
                self.settings,
                geometry=geometry,
                mirror=self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto",
                max_downloads=_owner.w.download_limit(self.settings.max_downloads),
                sidebar_collapsed=self.nav.is_collapsed(),
                image_filter=self._image_filter_key,
                image_sort=self.image_sort.currentData() or _owner.w.DEFAULT_IMAGE_SORT,
                image_source=self.image_source.currentData() or _owner.w.DEFAULT_IMAGE_SOURCE,
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
