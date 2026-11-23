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


class TasksMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def _start(self, label: str, work: Any, *, quiet: bool = False) -> None:
        if self._busy:
            _owner.QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        self._busy = True
        self._label = label
        # 任务消息归属于「发起它的那一页」：下载在镜像页启动，用户切到日志页时
        # 完成提示不应写到日志页的状态栏上。
        self._task_page = self.current_page()
        self._cancel = _owner.threading.Event()
        self._bridge = _owner.EventBridge(self)
        self._bridge.event.connect(self._on_log_event)
        if not quiet:
            self._set_task_status(f"{label}…")
        self._set_buttons(False)
        # 任务进行中在状态栏右侧显示加载条；可预估总量的导出会切成确定进度。
        self._export_meter.reset()
        if not quiet:
            self.show_loading(f"{label}…")

        def runner(cancel: _owner.threading.Event) -> tuple[str, str]:
            try:
                return "ok", work(cancel)
            except KeyboardInterrupt:
                # 取消不是失败：单独的终态，避免弹「下载失败」并让行内进度残留。
                return "cancelled", "已取消；未完成的 .part 保留在下载目录，可续传"
            except Exception as exc:  # 统一把失败转成可读文案
                return "failed", _owner.w._error_text(exc)

        _owner.submit(
            runner,
            self._cancel,
            on_done=lambda payload: self._task_done(label, payload, quiet=quiet),
            on_error=(lambda message: self._quiet_error(label, message))
            if quiet
            else (lambda message: self._task_error(label, message)),
            on_finished=self._task_finished,
        )

    def _task_done(self, label: str, payload: tuple[str, str], *, quiet: bool = False) -> None:
        status, message = payload
        if status == "cancelled":
            self._log(f"[{label}] {message}")
            if not quiet:
                self._set_task_status(f"{label}已取消")
            self._refresh_image_states()
            return
        if status != "ok":
            self._log(f"[{label}] {message}")
            if not quiet:
                # 状态行要能读到失败原因：错误框可能被切走 / 忽略，日志也不是唯一入口。
                self._set_task_status(f"{label}失败：{message}")
                _owner.QMessageBox.critical(self, label, message)
            return
        self._log(f"[{label}] {message}")
        if not quiet:
            # 刷新 / 修复结果可能包含具体处理说明，直接回显比一句泛化「完成」有用。
            self._set_task_status(message if label in {"刷新", "修复注册"} and message else f"{label}完成")
        if label == "刷新":
            self._refresh_rows()
        else:
            self.refresh()
        # 下载 / 安装 / 删除实例后重扫本地状态：行内标签与动作按钮（已下载→安装、
        # 安装完成→已安装）要立刻跟着变，不必等用户手动点「刷新」。
        self._refresh_image_states()

    def _task_error(self, label: str, message: str) -> None:
        self._log(f"[{label}] 失败：{message}")
        self._set_task_status(f"{label}失败：{message}")
        _owner.QMessageBox.critical(self, label, message)

    def _quiet_error(self, label: str, message: str) -> None:
        """静默刷新失败：只记日志，不覆盖用户刚看到的任务结果。"""
        self._log(f"[{label}] 失败：{message}")

    def _task_finished(self) -> None:
        self._busy = False
        self._active_entry = ""
        self.show_loading("")
        self._export_meter.reset()
        self._set_buttons(True)
        self._update_download_status()
        if self._closing:
            return
        self._update_action_states()
        if self._reload_pending:
            # 「刷新」或注册信息变化等到了空闲点：重读注册信息，按当前位置重算
            # 归属目录与占用（不能降级成轻量状态轮询，它只重读 ``wsl -l -v``）。
            quiet = self._reload_quiet
            self._reload_pending = False
            self._reload_quiet = False
            self._list_pending = False
            _owner.QTimer.singleShot(0, lambda: self.refresh(quiet=quiet))
        if self._list_pending:
            # 工作目录在任务进行中改过：当时没重建列表，收尾后按新配置重算归属。
            self._list_pending = False
            if hasattr(self, "distro_list"):
                self._refresh_rows()
        if self._refresh_pending:
            # 任务收尾前发起的列表刷新（``_task_done`` 里的 ``refresh()``）会被忙态挡下。
            # 这里改走轻量状态刷新而不是再起一个「刷新」任务：窗口保持空闲，只重读
            # ``wsl -l -v`` 并就地更新行卡，默认胶囊在任务结束后立刻换行。
            self._refresh_pending = False
            _owner.QTimer.singleShot(0, self._request_status)

    def _set_buttons(self, enabled: bool) -> None:
        for widget in self._buttons:
            widget.setEnabled(enabled)
        # 行内动作按钮跟着任务忙闲一起恢复：刷新任务结束时列表刚在忙态重建过，
        # 若只靠 3 秒轮询回填，启动按钮会先以禁用态（灰底）显示一段时间。
        if hasattr(self, "distro_list"):
            for index in range(self.distro_list.count()):
                row = self.distro_list.itemWidget(self.distro_list.item(index))
                if isinstance(row, _owner._DistroItemRow):
                    row.set_busy(not enabled)
        if hasattr(self, "image_list"):
            # 镜像行的按钮由下载队列决定：有任务的保持「取消」，其余按重操作忙闲禁用；
            # 不能把全局忙态当成「正在下载」，否则安装 / 迁移时整列都变成取消。
            self._refresh_download_rows()
        if enabled:
            self._update_action_states()

    def _set_task_status(self, text: str) -> None:
        """把任务状态写到发起任务的那一页（没有记录时退回当前页）。"""
        self.set_page_status(self._task_page or self.current_page(), text)

    def _on_log_event(self, message: Any) -> None:
        """共享事件桥 → 任务日志（桥只对 ``str`` 载荷生效）。"""
        if isinstance(message, str):
            self._log(message)

    def _on_download_event(self, payload: Any) -> None:
        """下载线程的 ``(镜像名, 累计字节, 总量 / None)`` → 该行进度与队列状态。"""
        if not (isinstance(payload, tuple) and len(payload) == 3):
            return
        name, done, total = payload
        task = self._downloads.get(name)
        if task is None:
            return
        task.done = done if isinstance(done, int) else 0
        task.total = total if isinstance(total, int) and total > 0 else None
        meter = self._download_meters.get(name)
        if meter is not None:
            meter.update(task.done)
        row = self._image_row(name)
        if row is not None:
            row.set_metrics(self._download_metrics(task))
        self._update_download_status()

    def _download_metrics(self, task: _owner._DownloadTask) -> str:
        if task.state == "queued":
            return "排队中"
        meter = self._download_meters.get(task.entry.name)
        speed = meter.speed if meter is not None else 0.0
        return _owner.w.download_metrics_text(task.done, task.total, speed)

    def _update_download_status(self) -> None:
        """镜像页状态行：并发数 / 上限 / 排队数；重操作占用状态行时不抢。"""
        if self._busy:
            return
        running = sum(1 for task in self._downloads.values() if task.state == "running")
        queued = sum(1 for task in self._downloads.values() if task.state == "queued")
        text = _owner.w.download_queue_text(running, queued, _owner.w.download_limit(self.settings.max_downloads))
        if text:
            self.set_page_status("images", text)

    def _image_row(self, name: str) -> _owner._ImageItemRow | None:
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _owner._ImageItemRow) and row.entry.name == name:
                return row
        return None

    def _refresh_download_rows(self) -> None:
        """按下载队列重算每一行：有任务的显示「取消」，其余按重操作忙态禁用。"""
        if not hasattr(self, "image_list"):
            return
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if not isinstance(row, _owner._ImageItemRow):
                continue
            task = self._downloads.get(row.entry.name)
            row.set_download_state(
                busy=task is not None,
                active=task is not None,
                queued=task is not None and task.state == "queued",
                enabled=not self._busy,
            )
            if task is not None:
                row.set_metrics(self._download_metrics(task))

    def _on_export_event(self, payload: Any) -> None:
        """共享事件桥的 ``(发行版, 累计字节, 总量 / None)`` → 导出进度。"""
        name, done, total = payload
        self._on_export_progress(name, done, total)

    def _on_export_progress(self, name: str, done: int, total: object) -> None:
        """导出进度：左侧状态栏写「百分比 · 已写 / 估算 · 速度 · 剩余」，右侧进度条同步。

        总量是发行版占用的估算值（稀疏 vhdx 取实际占用），可能不完全准确；任务结束
        后由 ``_task_done`` 把进度条收起并改写「导出完成」。
        """
        if not self._busy:
            return
        total_bytes = total if isinstance(total, int) and total > 0 else None
        self._export_meter.update(done)
        speed = self._export_meter.speed
        self.set_page_status(
            self._task_page or "distros", _owner.w.export_progress_text(name, done, total_bytes, speed)
        )
        if total_bytes:
            brief = f"导出 {min(100, int(done * 100 / total_bytes))}% · {_owner.format_speed(speed)}"
        else:
            brief = f"导出 {_owner.format_bytes(done)} · {_owner.format_speed(speed)}"
        self.show_progress(brief, done, total_bytes)

    def _cancel_task(self) -> None:
        """取消按钮在「正在跑的那一行」里：只对进行中的任务生效。"""
        if self._busy:
            self._cancel.set()
            self._set_task_status(f"{self._label}取消中…")

    def _schedule_poll(self) -> None:
        self._poll = _owner.periodic(self, _owner.w.POLL_INTERVAL_MS, self._poll_tick)

    def _poll_tick(self) -> None:
        """固定 3 秒轮询一次；窗口最小化时暂停，避免后台无意义开销。"""
        if self._closing or self.isMinimized():
            return
        self._request_status()

    def _request_status(self) -> None:
        if self._status_busy or self._busy:
            return
        self._status_busy = True
        _owner.submit(
            self._status_worker,
            on_done=self._on_status,
            on_error=self._on_status_error,
        )

    def _status_worker(self) -> Any:
        """轮询只读视图，但标记注册表 BasePath 下磁盘文件已不存在的实例。"""
        view, _needs_repair, _orphans = _owner.w.reconcile_distros(
            _owner.cli._view(_owner.cli._build_environment()), self.settings
        )
        return view

    def _on_status(self, view: Any) -> None:
        self._status_busy = False
        if self._busy or self._closing:
            return
        self._apply_status(view)

    def _on_status_error(self, message: str) -> None:
        self._status_busy = False
        if not self._busy:
            self.set_page_status("distros", f"状态刷新失败：{message}")

    def _apply_status(self, view: Any) -> None:
        """轮询结果：名单没变就只刷新行内状态，不重建列表（选中与滚动位置不动）。"""
        previous = self.view
        self.view = view
        # 文件页可见时同步实例下拉框：首次进入通常列表还没回来，名单到位后补一次。
        if self.current_page() == "files":
            self._open_files_page()
        names = [distro.name for distro in view.distros]
        if previous is None or names != [distro.name for distro in previous.distros]:
            self._refresh_rows()
            if previous is not None and any(distro.name not in self.sizes for distro in view.distros):
                # 外部新增实例：新行还没有占用数据，完整重读一次补上。
                self._schedule_reload()
            return
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if not isinstance(row, _owner._DistroItemRow):
                continue
            distro = view.find(row.distro.name)
            if distro is None:
                continue
            size, estimated = self.sizes.get(distro.name, (None, False))
            # 轮询会每 3 秒走一遍；数据完全没变时不碰行内控件，避免列表反复重绘。
            if row.distro != distro or row.size != size or row.estimated != estimated:
                row.update_distro(distro, size, estimated)
            row.set_busy(self._busy)
            if row.is_expanded():
                self.distro_detail_view.render(distro, size, estimated)
        self._update_action_states()
        self._show_details()
        if self._base_paths_changed(previous, view):
            # BasePath 变了（实例被移动 / 迁移）：归属目录与 vhdx 占用都要按新位置重算，
            # 就地更新补不上，安排一次完整重读。
            self._schedule_reload()

    def _schedule_reload(self) -> None:
        """安排一次完整的静默重读：忙时并入任务收尾，空闲时下一拍执行。"""
        if self._busy:
            self._reload_pending = True
            self._reload_quiet = True
            return
        _owner.QTimer.singleShot(0, lambda: self.refresh(quiet=True))
