"""wslmanw 迁移配置卡片：还原上次操作与初始化目标配置。

从 :mod:`fcmd.apps.wslman.qt.configfiles` 拆出的混入类，只放「还原 / 初始化」这两组
需要后台执行、会写还原记录的动作；卡片本身仍负责扫描、勾选与迁移。
"""

from __future__ import annotations

from ._compat import Any, Sequence


class _OwnerProxy:
    """延迟取 ``_wslmanw_qt``：卡片会在窗口模块导入过程中被加载，顶层 import 会成环。"""

    def __getattr__(self, name: str) -> Any:
        from fcmd.apps.wslman import _wslmanw_qt

        return getattr(_wslmanw_qt, name)


_owner: Any = _OwnerProxy()


class _ConfigUndoMixin:
    """还原 / 初始化动作；依赖宿主卡片提供的控件与状态。"""

    _distro: Any
    _busy: bool
    _closing: bool
    _refresh_after_run: bool
    _pending_restore: Any
    _restore_record: Any
    _on_restored: Any
    _target: Any
    _bridge: Any
    _append_log: Any
    _checked_paths: Any
    _files: Any
    _boxes: Any
    _sync_actions: Any
    log: Any
    migrate_button: Any
    select_all: Any
    select_none: Any
    _set_busy: Any
    _persist_restore: Any
    _sync_restore_hint: Any
    _sync_actions: Any
    refresh: Any
    summary: Any
    restore_hint: Any
    restore_button: Any
    init_button: Any

    def _checked_paths(self) -> list[str]:
        return [path for path, box in self._boxes.items() if box.isChecked() and box.isEnabled()]

    def _sync_actions(self) -> None:
        checked = self._checked_paths()
        self.migrate_button.setEnabled(not self._busy and self._target is not None and bool(checked))
        if not checked:
            self.migrate_button.setToolTip("先扫描并勾选要迁移的配置文件")
        elif self._target is None:
            self.migrate_button.setToolTip("先扫描确认实例现状")
        else:
            self.migrate_button.setToolTip("")
        self.restore_button.setEnabled(not self._busy and self._restore_record is not None)
        self.init_button.setEnabled(not self._busy and bool(self._target_paths()))
        self.select_all.setEnabled(not self._busy)
        self.select_none.setEnabled(not self._busy)

    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log_lines.append(line)
        if len(self._log_lines) > _owner._CONFIG_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _owner._CONFIG_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _sync_restore_hint(self) -> None:
        """显示「可还原上次迁移」：记录只在这里回显，不自动启动实例。"""
        record = self._restore_record
        if record is None or not record.entries:
            self.restore_hint.setVisible(False)
            self.restore_button.setToolTip("没有可还原的迁移 / 初始化记录")
            return
        text = f"可还原上次操作：{record.summary}"
        self.restore_hint.setText(text)
        self.restore_hint.setToolTip(text)
        self.restore_hint.setVisible(True)
        self.restore_button.setToolTip(f"恢复 {record.summary} 前的实例配置；操作后被修改的新增文件会跳过")

    def _persist_restore(self) -> None:
        """把本次迁移的还原记录交给窗口持久化（失败也会记录已完成部分）。"""
        if self._pending_restore is None or not self._pending_restore.entries:
            return
        self._restore_record = self._pending_restore
        self._pending_restore = None
        if callable(self._on_restored):
            self._on_restored(self._restore_record)
        self._sync_restore_hint()
        self._sync_actions()

    def _target_paths(self) -> list[str]:
        """实例里当前存在的受管配置文件（初始化操作的对象）。"""
        if self._target is None:
            return []
        return [item.path for item in self._target.files if _owner._configfiles.group_for_path(item.path) is not None]

    def _initialize_config(self) -> None:
        """把实例内已有的受管配置移进备份目录，回到「没有个人配置」的初始状态。"""
        paths = self._target_paths()
        if not paths:
            _owner.QMessageBox.information(self, "没有可初始化的配置", "先点「扫描」确认实例内的受管配置。")
            return
        listing = "\n".join(f"· {path}" for path in paths)
        text = (
            "将把实例内以下配置移进备份目录，实例回到初始状态：\n\n"
            f"{listing}\n\n"
            "文件不会直接删除，可用「还原上次操作」恢复。继续？"
        )
        if not _owner.confirm(self, "初始化目标配置", text):
            return
        self._set_busy(True)
        self._refresh_after_run = False
        self._pending_restore = None
        self._append_log("开始初始化目标配置：" + "、".join(paths))
        _owner.submit_daemon(
            lambda: self._run_initialize(paths),
            on_done=self._initialize_done,
            on_error=self._initialize_error,
            on_finished=self._initialize_finished,
        )

    def _run_initialize(self, paths: Sequence[str]) -> str:
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        stamp = _owner.time.time()
        stamp_text = _owner.time.strftime("%Y-%m-%d %H:%M:%S", _owner.time.localtime(stamp))
        moved: list[tuple[str, str]] = []
        for rel in paths:
            group = _owner._configfiles.group_for_path(rel)
            if group is None:
                continue
            moved.append((rel, _owner._configfiles.backup_root(group.key, stamp)))
        script = _owner._configio.initialize_script(moved)
        result = env.runner.run(
            [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", script],
            timeout=180.0,
        )
        text = _owner.decode_console(result.stdout + result.stderr).strip()
        for line in _owner._initenv.tail_lines(text):
            self._bridge.event.emit(line)
        pairs = _owner._configio.parse_init_result(text)
        if pairs:
            entries = tuple(_owner._configfiles.RestoreEntry(path, backup, 0) for path, backup in pairs)
            self._pending_restore = _owner._configfiles.RestoreRecord(stamp_text, "初始化配置", entries)
        if result.returncode != 0:
            tail = _owner._initenv.tail_lines(text, 3)
            raise _owner.runtime_error(
                f"初始化配置失败（退出码 {result.returncode}）",
                hint=tail[-1] if tail else "查看日志；已移走的文件仍可用「还原上次操作」恢复",
            )
        return f"已初始化 {len(pairs)} 个文件"

    def _initialize_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._persist_restore()
        self._append_log(message)
        self.summary.setText("初始化完成，正在刷新…")

    def _initialize_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._persist_restore()
        self._append_log(f"初始化失败：{message}")
        self.summary.setText("初始化失败")
        _owner.QMessageBox.critical(self, "初始化失败", message)

    def _initialize_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh()

    def _restore_last(self) -> None:
        record = self._restore_record
        if record is None or not record.entries or self._busy:
            return
        text = (
            f"将把实例配置恢复到「{record.stamp}」迁移之前：\n\n"
            f"· 迁移前已存在、被覆盖的 {record.overwritten} 个文件：从备份恢复\n"
            f"· 迁移时新增的 {record.created} 个文件：删除（迁移后被修改过的会跳过）\n\n继续？"
        )
        if not _owner.confirm(self, "还原上次迁移", text):
            return
        self._set_busy(True)
        self._refresh_after_run = False
        self._append_log(f"开始还原：{record.summary}")
        _owner.submit_daemon(
            lambda: self._run_restore(record),
            on_done=self._restore_done,
            on_error=self._restore_error,
            on_finished=self._restore_finished,
        )

    def _run_restore(self, record: _owner._configfiles.RestoreRecord) -> str:
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        script = _owner._configfiles.restore_script(record)
        result = env.runner.run(
            [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", script],
            timeout=180.0,
        )
        text = _owner.decode_console(result.stdout + result.stderr).strip()
        for line in _owner._initenv.tail_lines(text):
            self._bridge.event.emit(line)
        if result.returncode != 0:
            tail = _owner._initenv.tail_lines(text, 3)
            raise _owner.runtime_error(
                f"还原失败（退出码 {result.returncode}）",
                hint=tail[-1] if tail else "查看日志；被修改过的新增文件会跳过，可按需手动删除",
            )
        return f"已还原 {len(record.entries)} 个文件"

    def _restore_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._restore_record = None
        self._pending_restore = None
        if callable(self._on_restored):
            self._on_restored(None)
        self._sync_restore_hint()
        self._append_log(message)
        self.summary.setText("还原完成，正在刷新…")

    def _restore_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(f"还原失败：{message}")
        self.summary.setText("还原失败")
        _owner.QMessageBox.critical(self, "还原失败", message)

    def _restore_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh()


def _host_path(base: _owner.Path, path: str) -> _owner.Path:
    """把相对 ``$HOME`` 的 POSIX 路径拼到 Windows 家目录上。"""
    result = base
    for part in path.split("/"):
        result = result / part
    return result
