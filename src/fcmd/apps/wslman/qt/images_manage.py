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


class ImagesManageMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _open_image_manager(self) -> None:
        """「管理镜像」：导出 / 删除本地镜像包，并清理清单缓存与未完成下载。"""
        if self._busy:
            _owner.QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        dialog = _owner._ImageManagerDialog(self, self._download_dir_path(), self._catalog_entries())
        self._restore_dialog_geometry("image_manager", dialog)
        try:
            dialog.exec()
            self._save_dialog_geometry("image_manager", dialog)
        finally:
            changed = dialog.changed
            cleared = dialog.cache_cleared
            dialog.deleteLater()
        if changed:
            self._refresh_image_states()
        if cleared:
            self.set_page_status("images", "镜像清单缓存已清理；点「更新列表」可重新获取")

    def _install_local(self) -> None:
        """安装磁盘上已有的镜像包：扫描下载目录或手动选包，确认信息后走 ``wsl --import``。"""
        if self._busy:
            _owner.QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        dialog = _owner._InstallLocalDialog(self, self._catalog_entries())
        self._restore_dialog_geometry("install_local", dialog)
        accepted = dialog.exec() == _owner.QDialog.DialogCode.Accepted
        self._save_dialog_geometry("install_local", dialog)
        if not accepted:
            return
        payload = dialog.payload()
        if payload is None:
            return
        archive, name, target, replace, backup = payload
        self._active_entry = name
        self._install_archive(archive, name, target, replace=replace, backup=backup)

    def _install_archive(self, archive: Path, name: str, target: Path, *, replace: bool, backup: Path | None) -> None:
        """工作线程执行 ``wsl --import``；替换沿用 CLI 的 ``_guard_replace`` 与备份顺序。"""

        def work(_cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            existing = _owner.cli._view(env).find(name)
            command = f'fcmd wslman import {name} "{archive}" --dir "{target}"'
            if existing is not None:
                if not replace:
                    raise _owner.invalid_argument(
                        f"实例 {name} 已存在",
                        hint=f"勾选「替换同名实例」后重试，或改用 CLI：{command} --replace --yes",
                    )
                running = existing.state == "running"
                steps = [
                    step for step, flag in (("停止", running), ("备份", backup is not None), ("删除实例", True)) if flag
                ]
                if self._bridge is not None:
                    self._bridge.event.emit(f"[安装] 替换 {existing.name}：" + " → ".join(steps) + " → 重新导入")
                _owner.cli._guard_replace(
                    env, existing=existing, yes=True, backup=backup, terminate_first=running, command=command
                )
                if running:
                    _owner.cli._run_step(env, ["--terminate", existing.name], action=f"停止 {existing.name}")
                if backup is not None:
                    _owner.cli._run_step(
                        env,
                        ["--export", existing.name, str(backup)],
                        action=f"备份 {existing.name}",
                        hint="备份失败时不会删除旧实例",
                        timeout=3600.0,
                    )
                _owner.cli._run_step(env, ["--unregister", existing.name], action=f"删除实例 {existing.name}")
            # 目录校验放在替换处理之后：替换会删除并清空旧目录，先校验会把「已存在」误报成目录问题。
            problem = _owner.cli._dir_problem(target)
            if problem:
                raise _owner.invalid_argument(f"安装目录不可用：{problem}", hint="选择一个空目录（不存在会自动创建）")
            # 默认装到工作目录的 install/<名称>，父目录可能还没建过；``wsl --import``
            # 只创建最后一级目录，父目录缺失会直接报「系统找不到指定的路径」。
            # CLI 导入同样是先 ``mkdir(parents=True)`` 再执行，这里保持同一口径。
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise _owner.invalid_argument(
                    f"无法创建安装目录：{target}",
                    hint=f"换一个可写的本地目录（{exc}）",
                ) from exc
            if self._bridge is not None:
                self._bridge.event.emit(f"[安装] 导入 {archive.name} → {target}")
            _owner.cli._run_step(
                env,
                ["--import", name, str(target), str(archive), "--version", "2"],
                action=f"安装 {name}",
                hint="目标目录必须为空；WSL1 镜像请改用 CLI 指定 --version 1",
                timeout=3600.0,
            )
            size = archive.stat().st_size
            return f"已安装 {name} 到 {target}（{archive.name}，{_owner.format_bytes(size)}）"

        self._start("安装", work)

    def _catalog_entries(self) -> tuple[ImageEntry, ...]:
        """已知镜像清单：优先本次会话从下载页加载的结果，否则只读缓存。"""
        if self.image_entries:
            return self.image_entries
        cache = _owner._images.load_cache()
        return cache.entries if cache is not None else ()

    def _local_start_dir(self) -> str:
        """下载目录：工作目录的 download 子目录（不再单独配置）。"""
        return str(_owner.w.derived_dirs(self.settings)["download"])

    def _download_entry(self, name: str) -> None:
        """行内「下载 / 安装」按钮：先聚焦该行，再复用同一套下载流程。"""
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _owner._ImageItemRow) and row.entry.name == name:
                self.image_list.setCurrentRow(index)
                self._download_payload(row.entry)
                return

    def _download_payload(self, entry: ImageEntry) -> None:
        """把一次镜像下载放进队列；并发上限由设置决定，超出上限先排队。

        每一项各有取消标记、进度与速度，互相独立：单项失败 / 取消只影响自己。
        下载落点、校验、续传仍复用 ``_source.download_mirrored``，与 CLI 同一口径。
        """
        if entry.name in self._downloads:
            self._cancel_download(entry.name)
            return
        if self._busy:
            self.set_page_status("images", "已有重操作在执行；等它结束再下载，避免磁盘与网络互相拖慢")
            return
        self._downloads[entry.name] = _owner._DownloadTask(entry)
        self._download_meters[entry.name] = _owner.SpeedMeter()
        self._pump_downloads()

    def _pump_downloads(self) -> None:
        """按并发上限启动排队项；完成 / 取消 / 改上限后都重新收敛一次。"""
        limit = _owner.w.download_limit(self.settings.max_downloads)
        running = sum(1 for task in self._downloads.values() if task.state == "running")
        for task in list(self._downloads.values()):
            if running >= limit:
                break
            if task.state != "queued":
                continue
            task.state = "running"
            running += 1
            self._launch_download(task)
        self._refresh_download_rows()
        self._update_download_status()

    def _cancel_download(self, name: str) -> None:
        """单独取消一项：排队项直接移除；运行中置取消标记，由下载层尽快收尾。"""
        task = self._downloads.get(name)
        if task is None:
            return
        if task.state == "queued":
            self._downloads.pop(name, None)
            self._download_meters.pop(name, None)
            self._log(f"[下载] {name} 已取消（尚未开始）")
            self._refresh_download_rows()
            self._update_download_status()
            if not self._downloads and not self._busy:
                self._render_images()
            return
        task.cancel.set()
        self._log(f"[下载] {name} 取消中…")
        if not self._busy:
            self.set_page_status("images", f"{name} 取消中…")

    def _launch_download(self, task: _owner._DownloadTask) -> None:
        """启动一项下载；设置项在主线程读好，工作线程只碰纯数据。"""
        entry = task.entry
        expected = self.expected_sha.text().strip() or None
        mirror_index = self.mirror.currentIndex()
        mirror_policy = self.mirror_ids[mirror_index] if mirror_index >= 0 else "auto"
        directory = self._download_dir_path()

        def work(cancel: threading.Event) -> str:
            env = _owner.cli._build_environment()
            if not entry.downloads:
                # 只有 CLI / Store 来源：没有归档可下载，直接走 wsl --install。
                _owner.cli._run_step(
                    env,
                    ["--install", "-d", entry.name, "--no-launch"],
                    action=f"安装 {entry.name}",
                    hint="系统级安装可能弹 UAC；失败时检查网络或 WSL 版本",
                    timeout=3600.0,
                )
                return f"已通过 wsl --install 安装 {entry.name}；用「打开终端」启动"

            from fcmd.apps.wslman import _source

            download = _owner.w.pick_download(entry)
            target = directory / _owner.w.image_filename(entry.name, download)
            if target.exists():
                raise _owner.invalid_argument(f"目标文件已存在：{target}", hint="换一个目录或先删除旧文件")
            check = _owner.cli._preflight_for(env, target, None, estimated=True)
            if not check.ok:
                raise _owner.runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个目录")
            result_data = _source.download_mirrored(
                target,
                candidates=_owner._mirrors.candidates(
                    download.url,
                    policy=mirror_policy,
                    verifiable=bool(expected or download.sha256),
                ),
                expected_sha256=expected or download.sha256,
                cancelled=cancel.is_set,
                progress=lambda done, total: self._progress_bridge.progress.emit((entry.name, done, total)),
            )
            return (
                f"已下载 {target}（{_owner.format_bytes(result_data.size)}，"
                f"来源 {_owner._mirrors.label(result_data.source)}，sha256 {result_data.sha256}）；"
                "点行内「安装」导入 WSL"
            )

        def runner(cancel: threading.Event) -> tuple[str, str]:
            try:
                return "ok", work(cancel)
            except KeyboardInterrupt:
                return "cancelled", "已取消；未完成的 .part 保留在下载目录，可续传"
            except Exception as exc:  # 统一转成可读文案，单项失败不打断队列
                return "failed", _owner.w._error_text(exc)

        _owner.submit(
            runner,
            task.cancel,
            on_done=lambda payload: self._download_done(task, payload),
            on_error=lambda message: self._download_done(task, ("failed", message)),
            on_finished=lambda: self._download_finished(task),
        )

    def _download_done(self, task: _owner._DownloadTask, payload: tuple[str, str]) -> None:
        status, message = payload
        task.state = status
        task.message = message
        if self._closing:
            return
        self._log(f"[下载] {task.entry.name}：{message}")
        if self._busy:
            # 重操作占着镜像页状态行时只留日志，避免把「迁移 / 安装」误标成下载。
            return
        if status == "ok":
            self.set_page_status("images", f"{task.entry.name} 下载完成")
        elif status == "cancelled":
            self.set_page_status("images", f"{task.entry.name} 已取消")
        else:
            self.set_page_status("images", f"{task.entry.name} 下载失败：{message}")

    def _download_finished(self, task: _owner._DownloadTask) -> None:
        """单项收尾：释放槽位并启动下一项；单点失败 / 取消不影响其它下载。"""
        name = task.entry.name
        self._downloads.pop(name, None)
        self._download_meters.pop(name, None)
        if self._closing:
            return
        # 已下载标记按磁盘重扫，行内按钮随结果从「下载」切到「安装」。
        self._refresh_image_states()
        self._render_images()
        self._pump_downloads()

    def _install_entry(self, name: str) -> None:
        """行内「安装」：已下载的归档 → 复用「安装镜像」对话框；无归档 → wsl --install。"""
        entry = self._entry_by_name(name)
        if entry is None:
            return
        if entry.installed:
            self.set_page_status("images", f"{entry.name} 已安装，无需重复安装")
            return
        if entry.archive and _owner.Path(entry.archive).is_file():
            dialog = _owner._InstallLocalDialog(self, self._catalog_entries(), preset=_owner.Path(entry.archive))
            self._restore_dialog_geometry("install_local", dialog)
            accepted = dialog.exec() == _owner.QDialog.DialogCode.Accepted
            self._save_dialog_geometry("install_local", dialog)
            if not accepted:
                return
            payload = dialog.payload()
            if payload is None:
                return
            archive, chosen_name, target, replace, backup = payload
            self._active_entry = entry.name
            self._install_archive(archive, chosen_name, target, replace=replace, backup=backup)
            return
        # 没有本地归档（CLI / Store 来源，或归档已被删除）：走下载 / wsl --install 路径。
        self._download_entry(name)

    def _entry_by_name(self, name: str) -> ImageEntry | None:
        """按实例名（大小写不敏感）取当前列表里的条目。"""
        key = name.casefold()
        for entry in self.image_entries:
            if entry.name.casefold() == key:
                return entry
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _owner._ImageItemRow) and row.entry.name.casefold() == key:
                return row.entry
        return None

    def _download_dir_path(self) -> _owner.Path:
        """下载目录（与「安装镜像」扫描目录同一口径）。"""
        return _owner.Path(self._local_start_dir())

    def _install_dir_path(self) -> Path:
        """安装目录：工作目录的 install 子目录。"""
        return _owner.w.derived_dirs(self.settings)["install"]

    def _backup_dir_path(self) -> Path:
        """备份目录：工作目录的 backup 子目录（不再单独配置）。"""
        return _owner.w.derived_dirs(self.settings)["backup"]
