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


class ImagesDownloadMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _on_mirror_changed(self, _index: int) -> None:
        """切换下载源：立刻写回设置并提示（配置只落在设置页，镜像页不再放下拉）。"""
        if 0 <= self.mirror.currentIndex() < len(self.mirror_ids):
            self.set_page_status(
                "settings", f"下载源：{_owner._mirrors.label(self.mirror_ids[self.mirror.currentIndex()])}"
            )
            self._persist_image_view()

    def _on_download_limit_changed(self, _index: int) -> None:
        """调整并发上限：写回设置，并立刻让排队的下载补上新增的槽位。"""
        value = self.download_limit_combo.currentData()
        if not isinstance(value, int):
            return
        self.settings = _owner.w.with_max_downloads(self.settings, value)
        self._persist_settings(self.geometry_text())
        if not self._busy:
            self.set_page_status("settings", f"并发下载：最多 {value} 个")
        self._pump_downloads()

    def _restore_images_cache(self) -> Any:
        """把上次落盘的镜像清单直接铺到列表上（不等网络），返回命中的缓存。"""
        cache = _owner._images.load_cache()
        if cache is None or not cache.entries:
            return None
        self.image_entries = cache.entries
        self._images_from_cache = True
        self._images_note = f"本地缓存 · 更新于 {_owner._images_stamp(cache.fetched_at)}"
        self._render_images()
        self._refresh_distro_images()
        self._log(
            f"[镜像列表] 已载入本地缓存：{len(cache.entries)} 个，更新于 {_owner._images_stamp(cache.fetched_at)}"
        )
        return cache

    def _refresh_image_states(self) -> None:
        """重扫本地状态并重绘行（不重新拉在线清单）。

        下载完成 → 行标签变「已下载」且按钮变「安装」；安装 / 删除实例完成 → 「已安装」
        随之增减。只跑 ``wsl -l -v`` 与下载目录扫描，代价远小于重新联网拉清单。
        """
        if self._closing or not self.image_entries:
            return
        if self._states_busy:
            # 并发下载同时收尾时，合并成一次补扫，避免最后一个完成项漏掉状态。
            self._states_pending = True
            return
        self._states_busy = True
        entries = self.image_entries

        def worker() -> tuple[ImageEntry, ...]:
            return self._mark_local(entries, _owner.cli._build_environment())

        _owner.submit_daemon(
            worker,
            on_done=self._apply_image_states,
            on_error=lambda message: self._log(f"[镜像状态] 刷新失败：{message}"),
            on_finished=self._finish_image_states,
        )

    def _finish_image_states(self) -> None:
        self._states_busy = False
        if self._states_pending and not self._closing:
            self._states_pending = False
            self._refresh_image_states()

    def _apply_image_states(self, entries: tuple[ImageEntry, ...]) -> None:
        if self._closing or not entries:
            return
        self.image_entries = entries
        self._render_images()
        self._refresh_distro_images()

    def _manual_update_images(self) -> None:
        """「更新列表」按钮：绕过缓存重新拉取两个在线源（对齐 llmw「更新模型」）。"""
        if self._images_busy:
            self.set_page_status("images", "镜像清单正在更新，请稍候…")
            return
        self.set_page_status("images", "正在更新镜像清单…")
        self._log("[镜像列表] 手动更新：重新获取 wsl --list --online 与官方清单")
        self._load_images(force=True)

    def _set_images_busy(self, busy: bool) -> None:
        """更新镜像清单期间禁用「更新列表」，避免重复拉取。"""
        self._images_busy = busy
        if hasattr(self, "images_refresh"):
            self.images_refresh.setEnabled(not busy)
            self.images_refresh.setText("更新中…" if busy else "更新列表")

    def _load_images(self, *, force: bool = False) -> None:
        if self._images_busy or self._closing:
            return
        # 只有「真正拉过一遍」的列表才跳过重复请求；缓存回显的列表必须继续后台刷新。
        if self.image_entries and not force and not self._images_from_cache:
            return
        self._set_images_busy(True)
        self.images_status.setText(
            "正在更新镜像清单…（跳过缓存，重新拉取 wsl --list --online 与官方清单）"
            if force
            else "正在获取镜像清单…（已显示本地缓存，稍后自动更新）"
            if self.image_entries
            else "正在获取镜像清单…（wsl --list --online 首次较慢）"
        )
        # 拉清单要跑 ``wsl --list --online`` 再下载官方 JSON，网络不好时会长时间阻塞；
        # 放进 ``QThreadPool`` 会让进程在关窗后仍等任务结束（窗口关了进程却退不出），
        # 因此走守护线程：结果回来前关窗，进程可以直接退出。
        _owner.submit_daemon(
            lambda: self._images_worker(force),
            on_done=self._apply_images,
            on_error=self._images_error,
            on_finished=lambda: self._set_images_busy(False),
        )

    def _images_worker(self, force: bool = False) -> tuple[Any, tuple[ImageEntry, ...], Any]:
        env = _owner.cli._build_environment()
        catalog = _owner._images.collect_images(
            fetch_cli=lambda: _owner.cli._online_rows(env),
            fetch_manifest=_owner.cli._manifest_images,
            force=force,
        )
        # WSLUI 镜像源是附加信息：失败不能拖垮官方清单，只记 warning。
        wslui = _owner._wslui.load_catalog(force=force)
        return catalog, self._mark_local(catalog.entries, env), wslui

    def _mark_local(self, entries: Any, env: Any) -> tuple[ImageEntry, ...]:
        """标注本地状态：已安装 / 已备份（``wsl`` 侧）+ 已下载（下载目录里的归档）。"""
        marked = _owner._images.mark_local_state(
            entries,
            installed=_owner.cli._installed_names(env),
            backups=_owner.cli._backup_names(env),
        )
        return _owner.w.mark_downloaded(marked, self._download_dir_path())

    def _images_error(self, message: str) -> None:
        kept = "；列表仍是上一次的结果" if self.image_entries else ""
        self.images_status.setText(f"镜像清单获取失败：{message}{kept}")
        self._log(f"[镜像列表] {message}")

    def _apply_images(self, payload: tuple[Any, ...]) -> None:
        # 兼容测试 / 旧调用只传 (官方清单, 条目)；运行时多带一份 WSLUI 镜像源。
        if len(payload) >= 3:
            catalog, entries, wslui = payload[0], payload[1], payload[2]
        else:
            catalog, entries = payload[0], payload[1]
            wslui = None
        self.image_entries = entries
        self._wslui_catalog = wslui
        self._images_from_cache = catalog.from_cache
        self._images_note = _owner._images_note_text(catalog)
        for warning in catalog.warnings:
            self._log(f"[镜像列表] {warning}")
        for warning in getattr(wslui, "warnings", ()):
            self._log(f"[镜像源] {warning}")
        self._render_images()
        self._refresh_distro_images()

    def _mirror_note(self, entry: ImageEntry) -> tuple[str, str]:
        """镜像行副标题后的「可用镜像源」文本与 tooltip。"""
        sources = _owner._wslui.match_sources(self._wslui_catalog, entry)
        if sources:
            labels = _owner._wslui.mirror_labels(sources)
            return f"镜像源：{' / '.join(labels)}", _owner._wslui.mirror_tooltip(sources)
        if entry.downloads:
            return "镜像源：官方源", "未在 WSLUI 镜像清单中找到该发行版，使用官方下载地址"
        return "镜像源：wsl --install（无直链）", "该条目只有系统安装来源，没有可直接下载的镜像源"

    def _render_images(self) -> None:
        filter_key = self._image_filter_key
        source = self.image_source.currentData()
        sort_key = self.image_sort.currentData()
        query = self.image_search.text().strip()
        keep = self._selected_image()
        rows = _owner.w.sort_images(
            _owner.w.filter_images(self.image_entries, query=query, filter_key=filter_key, source=source or "all"),
            sort_key if isinstance(sort_key, str) else _owner.w.DEFAULT_IMAGE_SORT,
        )
        self.detach_row_detail("images")
        self.image_list.clear()
        for entry in rows:
            item = _owner.QListWidgetItem(self.image_list)
            mirror_text, mirror_tip = self._mirror_note(entry)
            row = _owner._ImageItemRow(entry, self.image_list, mirror_text=mirror_text, mirror_tip=mirror_tip)
            row.toggleRequested.connect(self._on_image_toggle)
            row.downloadRequested.connect(self._download_entry)
            row.installRequested.connect(self._install_entry)
            # 每行的「取消」只作用于这一项下载，不碰其它并发任务。
            row.cancelRequested.connect(lambda name=entry.name: self._cancel_download(name))
            task = self._downloads.get(entry.name)
            row.set_download_state(
                busy=task is not None,
                active=task is not None,
                queued=task is not None and task.state == "queued",
                enabled=not self._busy,
            )
            if task is not None:
                row.set_metrics(self._download_metrics(task))
            item.setSizeHint(_owner.QSize(0, row.sizeHint().height()))
            self.image_list.setItemWidget(item, row)
        self.image_list.sync_row_widths()
        self.images_status.setText(
            _owner.w.image_status_text(
                self.image_entries,
                len(rows),
                filter_key=filter_key,
                source=source or "all",
                note=self._images_note,
            )
        )
        target = None
        if keep is not None:
            target = next((index for index, entry in enumerate(rows) if entry.name == keep.name), None)
        if target is None and rows:
            target = 0
        if target is not None:
            self.image_list.setCurrentRow(target)
        # 重绘会重写镜像页状态行：有并发下载时改回「正在下载 / 排队」口径。
        self._update_download_status()

    def _on_image_filter_changed(self, key: str) -> None:
        """切换筛选按钮：只重绘列表，不重新拉取在线清单。"""
        if key == self._image_filter_key:
            return
        self._image_filter_key = key
        self._persist_image_view()
        self._render_images()

    def _on_image_sort_changed(self, _index: int) -> None:
        self._persist_image_view()
        self._render_images()

    def _on_image_source_changed(self, _index: int) -> None:
        self._persist_image_view()
        self._render_images()

    def _persist_image_view(self) -> None:
        """筛选 / 排序 / 来源立即写回设置，下次启动恢复上次视角。"""
        self._persist_settings(self.geometry_text())

    def _schedule_image_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.start()

    def _on_image_selected(self, current: int) -> None:
        self.image_list.sync_selection(current)

    def _on_image_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.image_list.itemWidget(item)
        if isinstance(row, _owner._ImageItemRow):
            self._on_image_toggle(row)

    def _on_image_toggle(self, row: _owner._ImageItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self.toggle_row("images", row)

    def _selected_image(self) -> ImageEntry | None:
        item = self.image_list.currentItem()
        row = self.image_list.itemWidget(item) if item is not None else None
        return row.entry if isinstance(row, _owner._ImageItemRow) else None
