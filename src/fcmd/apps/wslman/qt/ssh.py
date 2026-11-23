"""wslman SSH 迁移卡片（ADR 0046 P2/P3）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.gui.widgets import Card

from ._compat import Any, Distro, Sequence


class _SshMigrateSection(Card):  # pragma: no cover - Qt 控件
    """迁移 SSH 文件卡片：从 Windows 宿主或另一个实例读取，备份后写入当前实例。

    与「初始化环境」并列放在实例配置对话框里：初始化负责在实例内生成密钥，
    本卡片负责把已经在用的密钥、known_hosts、config 带进来，两者互不替代。
    跨实例来源通过 ``wsl.exe`` 的 ``cat`` 文件流读取，不依赖实例内是否装了 rsync；
    目标侧始终是当前实例，反向迁移用目标实例的配置卡片完成。文件内容通过 stdin
    送进目标实例，命令行与日志里只有文件名和权限。
    """

    def __init__(
        self,
        parent: _owner.QWidget,
        distro: Distro,
        *,
        instances: Sequence[str] = (),
        state_provider: Any = None,
    ) -> None:
        super().__init__("迁移 SSH 文件", parent)
        self._distro = distro
        self._instances = tuple(instances)
        self._state_provider = state_provider
        self._busy = False
        self._closing = False
        self._files: dict[str, _owner._sshfiles.SshFile] = {}
        self._target: _owner._sshfiles.SshTarget | None = None
        self._source_snapshot: _owner._sshfiles.SshSource | None = None
        self._keepalive_target = False
        self._keepalive_source = False
        self._boxes: dict[str, _owner.QCheckBox] = {}
        self._log_lines: list[str] = []
        self._refresh_after_run = False
        self._bridge = _owner.EventBridge(self)
        self._bridge.event.connect(self._append_log)
        self.destroyed.connect(self._on_destroyed)
        self.setToolTip(_owner._SSH_HINT)

        self._collapsed = True
        header = _owner.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.collapsed_hint = _owner.hint_label("尚未扫描来源目录。", wrap=False)
        header.addWidget(self.collapsed_hint)
        # 迁移完成度：每次扫描按实际文件推导，不依赖「点过迁移按钮」。
        self.state_tag = _owner.StatusTag("", tone="muted", parent=self)
        self.state_tag.setVisible(False)
        header.addWidget(self.state_tag)
        self.summary = _owner.ElidedLabel("", parent=self)
        self.summary.setObjectName("Hint")
        self.summary.setToolTip(_owner._SSH_HINT)
        self.summary.setVisible(False)
        header.addWidget(self.summary, 1)
        self.toggle_button = _owner.icon_button("", tooltip="展开详情")
        self.toggle_button.setIconSize(_owner.QSize(16, 16))
        _owner._qt_icons.set_icon(self.toggle_button, "chevron-down", tone="muted")
        self.toggle_button.setAccessibleName("展开详情")
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self.toggle_button)
        self.body.addLayout(header)

        self.details = _owner.QWidget(self)
        details = _owner.QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(4)

        # 来源类型：Windows 宿主，或另一个已安装的 WSL 实例（当前实例不作为来源）。
        source_row = _owner.QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        source_row.addWidget(_owner.hint_label("来源", wrap=False))
        self.source_kind = _owner.QComboBox(self)
        self.source_kind.addItem(
            _owner._sshfiles.SOURCE_LABELS[_owner._sshfiles.SOURCE_WINDOWS], _owner._sshfiles.SOURCE_WINDOWS
        )
        if self._instances:
            self.source_kind.addItem(
                _owner._sshfiles.SOURCE_LABELS[_owner._sshfiles.SOURCE_WSL], _owner._sshfiles.SOURCE_WSL
            )
        self.source_kind.setToolTip("Windows 宿主的 %USERPROFILE%\\.ssh，或另一个 WSL 实例的 ~/.ssh")
        self.source_kind.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_kind)
        self.source_instance = _owner.QComboBox(self)
        for name in self._instances:
            self.source_instance.addItem(name, name)
        self.source_instance.setToolTip("从这里读取 SSH 文件；实例未运行时会由 wsl.exe 启动后再读")
        self.source_instance.setVisible(False)
        self.source_instance.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_instance, 1)
        source_row.addStretch(1)
        details.addLayout(source_row)

        path_row = _owner.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        self.source_label = _owner.hint_label("Windows 侧目录", wrap=False)
        path_row.addWidget(self.source_label)
        self.source = _owner.PathRow(
            value=str(_owner._sshfiles.default_source_dir()),
            placeholder=r"%USERPROFILE%\.ssh",
            directory=True,
            parent=self,
        )
        path_row.addWidget(self.source, 1)
        self.source_remote = _owner.ElidedLabel("", parent=self)
        self.source_remote.setObjectName("Hint")
        self.source_remote.setVisible(False)
        path_row.addWidget(self.source_remote, 1)
        details.addLayout(path_row)

        self.file_hint = _owner.ElidedLabel("点「扫描」读取来源目录并检测实例。", parent=self)
        self.file_hint.setObjectName("Hint")
        details.addWidget(self.file_hint)

        self.files_box = _owner.QWidget(self)
        self.files_layout = _owner.QVBoxLayout(self.files_box)
        self.files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_layout.setSpacing(2)
        details.addWidget(self.files_box)

        # 实例侧现状（含同名文件与权限过宽的提示）直接铺开，不用再点一层。
        self.target_hint = _owner.hint_label("尚未检测实例的 ~/.ssh。")
        self.target_hint.setWordWrap(True)
        details.addWidget(self.target_hint)

        self.log = _owner.QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("迁移日志…")
        self.log.setMinimumHeight(56)
        self.log.setMaximumHeight(72)
        details.addWidget(self.log)

        self.body.addWidget(self.details)
        self.details.setVisible(False)

        actions = _owner.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.select_all = _owner.button("全选", kind="ghost")
        self.select_all.clicked.connect(lambda: self._set_all(True))
        actions.addWidget(self.select_all)
        self.select_none = _owner.button("全不选", kind="ghost")
        self.select_none.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(self.select_none)
        actions.addStretch(1)
        self.scan_button = _owner.button("扫描")
        self.scan_button.clicked.connect(self.refresh)
        actions.addWidget(self.scan_button)
        self.migrate_button = _owner.button("迁移所选文件", kind="primary")
        self.migrate_button.clicked.connect(self._migrate_selected)
        self.migrate_button.setEnabled(False)
        actions.addWidget(self.migrate_button)
        self.body.addLayout(actions)

        self._sync_collapsed_view()
        self._sync_source_controls()

    @property
    def collapsed(self) -> bool:
        """是否收起（默认收起，展开时才发起扫描）。"""
        return self._collapsed

    @property
    def busy(self) -> bool:
        """是否有扫描 / 迁移任务在跑。"""
        return self._busy

    def _current_state(self) -> str | None:
        """当前实例状态：优先窗口提供的最新值，避免用打开对话框时的旧快照。"""
        if callable(self._state_provider):
            try:
                state = self._state_provider()
            except Exception:
                state = None
            if state:
                return str(state)
        return self._distro.state

    def _on_toggle_clicked(self, _checked: bool = False) -> None:
        self.toggle_collapsed()

    def toggle_collapsed(self, collapsed: bool | None = None) -> None:
        """展开 / 收起；首次展开自动扫描一次（来源目录与实例现状）。"""
        was_collapsed = self._collapsed
        self._collapsed = (not self._collapsed) if collapsed is None else bool(collapsed)
        self._sync_collapsed_view()
        if was_collapsed and not self._collapsed and self._target is None and not self._busy:
            if self._current_state() == "running":
                self.refresh()
            else:
                self.file_hint.setText("实例未运行；点「扫描」会先确认再启动实例并检测。")
        window = self.window()
        if window is not self and isinstance(window, _owner.QDialog):
            _owner.QTimer.singleShot(0, lambda w=window: w.adjustSize())

    def _sync_collapsed_view(self) -> None:
        self.collapsed_hint.setVisible(self._collapsed)
        self.summary.setVisible(not self._collapsed)
        self.details.setVisible(not self._collapsed)
        _owner._qt_icons.set_icon(
            self.toggle_button, "chevron-up" if not self._collapsed else "chevron-down", tone="muted"
        )
        self.toggle_button.setToolTip("收起详情" if not self._collapsed else "展开详情")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())

    def stop(self) -> None:
        """对话框关闭时调用：忽略后续回调；后台命令本身继续执行。"""
        self._closing = True

    def _on_destroyed(self) -> None:
        self._closing = True

    # ------------------------------------------------------------------ #
    # 扫描 / 选择
    # ------------------------------------------------------------------ #
    def _source_dir(self) -> _owner.Path:
        text = self.source.value()
        return _owner.Path(text) if text else _owner._sshfiles.default_source_dir()

    def _source(self) -> _owner._sshfiles.SshSource:
        """当前选择的来源；WSL 来源的目录要等扫描探测后再补齐。"""
        if self.source_kind.currentData() == _owner._sshfiles.SOURCE_WSL:
            return _owner._sshfiles.SshSource(
                _owner._sshfiles.SOURCE_WSL, str(self.source_instance.currentData() or "")
            )
        return _owner._sshfiles.SshSource(_owner._sshfiles.SOURCE_WINDOWS, "", str(self._source_dir()))

    def _on_source_changed(self, _index: int = 0) -> None:
        """切换来源类型 / 实例：清掉旧结果，展开态下立即重新扫描。"""
        self._reset_results()
        self._sync_source_controls()
        if not self._collapsed and not self._busy:
            self.refresh()

    def _sync_source_controls(self) -> None:
        """按来源类型切换「本地目录」与「实例 + 实际读取路径」两组控件。"""
        wsl_source = self.source_kind.currentData() == _owner._sshfiles.SOURCE_WSL
        self.source_instance.setVisible(wsl_source)
        self.source.setVisible(not wsl_source)
        self.source_remote.setVisible(wsl_source)
        self.source_label.setText("来源实例目录" if wsl_source else "Windows 侧目录")
        if wsl_source:
            self.source_remote.setText(self._remote_path_text())
            self.source_remote.setToolTip(self.source_remote.text())

    def _remote_path_text(self) -> str:
        """WSL 来源实际读取的位置；扫描前给出预期位置，扫描后换成探测到的目录。"""
        distro = str(self.source_instance.currentData() or "")
        snapshot = self._source_snapshot
        if snapshot is not None and snapshot.is_wsl and snapshot.distro == distro:
            return snapshot.display_path()
        return f"{distro or '（未选择实例）'}:{_owner._sshfiles.SSH_DIR}（扫描后显示实际路径）"

    def _reset_results(self) -> None:
        """来源变化或扫描失败后，旧的判定与勾选都不再成立。"""
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._rebuild_files()
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self.summary.setText("")
        self.file_hint.setText("点「扫描」读取来源目录并检测实例。")
        self.target_hint.setText("尚未检测实例的 ~/.ssh。")
        self.collapsed_hint.setText("尚未扫描来源目录。")
        self._sync_actions()

    def refresh(self) -> None:
        """后台扫描来源（Windows 目录或实例 ~/.ssh）并探测当前实例。"""
        if self._busy:
            return
        state = self._current_state()
        wsl_source = self.source_kind.currentData() == _owner._sshfiles.SOURCE_WSL
        may_start = state != "running" or wsl_source
        if may_start and not _owner.confirm(
            self,
            "扫描 SSH",
            f"扫描需要在 {'来源实例与 ' if wsl_source else ''}"
            f"{self._distro.name} 内执行命令，WSL 会启动/唤醒未运行的实例。继续？",
        ):
            self.file_hint.setText("已取消扫描（实例未运行）")
            return
        self._keepalive_target = state != "running"
        self._keepalive_source = wsl_source
        self._set_busy(True)
        self.file_hint.setText("正在扫描…")
        _owner.submit_daemon(
            self._scan,
            on_done=self._scan_done,
            on_error=self._scan_error,
            on_finished=self._scan_finished,
        )

    def _probe(self, env: Any, wsl_path: str, distro: str, action: str, hint: str) -> _owner._sshfiles.SshTarget:
        """在实例内跑只读探测脚本，解析 ``~/.ssh`` 快照。"""
        result = env.runner.run(
            [wsl_path, "-d", distro, "--exec", "bash", "-lc", _owner._sshfiles.probe_script()],
            timeout=180.0,
        )
        text = _owner.decode_console(result.stdout + result.stderr)
        target = _owner._sshfiles.parse_probe(text)
        if result.returncode != 0 and not target.user:
            tail = _owner._initenv.tail_lines(text, 3)
            # 保留可操作提示，把 wsl.exe 的原始错误附在后面便于排查。
            detail = tail[-1] if tail else ""
            raise _owner.runtime_error(
                f"{action} 失败（退出码 {result.returncode}）",
                hint=f"{hint}（wsl.exe：{detail}）" if detail else hint,
            )
        return target

    def _scan(
        self,
    ) -> tuple[tuple[_owner._sshfiles.SshFile, ...], _owner._sshfiles.SshTarget, _owner._sshfiles.SshSource]:
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        source = self._source()
        if self._keepalive_target:
            _owner._ensure_running(env, self._distro.name)
            self._keepalive_target = False
        if source.is_wsl:
            if not source.distro:
                raise _owner.invalid_argument("没有可选的 WSL 来源实例", hint="先在实例页创建或导入一个实例")
            if self._keepalive_source:
                _owner._ensure_running(env, source.distro)
                self._keepalive_source = False
            remote = self._probe(
                env,
                wsl_path,
                source.distro,
                f"读取实例 {source.distro} 的 ~/.ssh",
                f"确认实例已安装；若未运行，先在实例页启动 {source.distro} 后重试"
                "（wsl.exe 通常也会自动拉起已停止的实例）。",
            )
            files = _owner._sshfiles.remote_files(remote) if remote.dir else ()
            source = _owner._sshfiles.SshSource(_owner._sshfiles.SOURCE_WSL, source.distro, remote.dir)
        else:
            files = _owner._sshfiles.scan_source(_owner.Path(source.directory))
        target = self._probe(
            env,
            wsl_path,
            self._distro.name,
            f"检测 {self._distro.name} 的 ~/.ssh",
            "确认实例可启动，或运行 fcmd wslman doctor",
        )
        return files, target, source

    def _scan_done(
        self,
        result: tuple[tuple[_owner._sshfiles.SshFile, ...], _owner._sshfiles.SshTarget, _owner._sshfiles.SshSource],
    ) -> None:
        if self._closing:
            return
        files, target, source = result
        self._source_snapshot = source
        self._files = {item.name: item for item in files}
        self._target = target
        self._rebuild_files()
        names = "、".join(item.name for item in files)
        self.file_hint.setText(f"找到 {len(files)} 个文件：{names}" if files else "来源没有可迁移的普通文件")
        self.target_hint.setText(_owner._sshfiles.target_summary(target))
        self.summary.setText(f"{source.label} {len(files)} 个文件 · 实例内 {len(target.files)} 个文件")
        state = _owner._sshfiles.migration_state(files, target)
        self.state_tag.set_status(state.label, state.tone)
        self.state_tag.setToolTip(state.detail)
        self.state_tag.setVisible(True)
        self.collapsed_hint.setText(f"{source.label} {len(files)} 个文件")
        self._append_log(f"扫描完成：来源 {source.display_path()}（{len(files)} 个文件）")
        self._append_log(_owner._sshfiles.target_summary(target))
        self._sync_source_controls()
        self._sync_actions()
        self._sync_collapsed_view()

    def _scan_error(self, message: str) -> None:
        if self._closing:
            return
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._rebuild_files()
        self.file_hint.setText("扫描失败，检查来源后重试")
        self.summary.setText("扫描失败")
        self.collapsed_hint.setText("扫描失败，展开后可重试。")
        self.target_hint.setText("")
        # 扫描失败时旧结果已经不成立，隐藏标签而不是继续展示过期状态。
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self._sync_source_controls()
        self._sync_actions()
        self._append_log(f"扫描失败：{message}")

    def _scan_finished(self) -> None:
        if not self._closing:
            self._set_busy(False)

    def _rebuild_files(self) -> None:
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes.clear()
        for entry in self._files.values():
            box = _owner.QCheckBox(f"{entry.name}  ·  {entry.kind_label}  ·  {entry.size_text}", self.files_box)
            tip = f"写入 ~/.ssh/{entry.name}，权限 {entry.mode_text}"
            if entry.warning:
                tip += f"\n注意：{entry.warning}"
            box.setToolTip(tip)
            box.setChecked(entry.checked_by_default)
            box.stateChanged.connect(self._sync_actions)
            self.files_layout.addWidget(box)
            self._boxes[entry.name] = box

    def _set_all(self, checked: bool) -> None:
        if self._busy:
            return
        for box in self._boxes.values():
            box.setChecked(checked)
        self._sync_actions()

    def _checked_names(self) -> list[str]:
        return [name for name, box in self._boxes.items() if box.isChecked() and box.isEnabled()]

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        # 扫描 / 迁移期间锁住来源选择，避免结果回来时来源已经换了。
        self.source_kind.setEnabled(not busy)
        self.source_instance.setEnabled(not busy)
        self.source.setEnabled(not busy)
        for box in self._boxes.values():
            box.setEnabled(not busy)
        self._sync_actions()

    def _sync_actions(self) -> None:
        checked = self._checked_names()
        self.migrate_button.setEnabled(not self._busy and self._target is not None and bool(checked))
        if not checked:
            self.migrate_button.setToolTip("先扫描并勾选要迁移的文件")
        elif self._target is None:
            self.migrate_button.setToolTip("先扫描确认实例现状")
        else:
            self.migrate_button.setToolTip("")
        self.select_all.setEnabled(not self._busy)
        self.select_none.setEnabled(not self._busy)

    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log_lines.append(line)
        if len(self._log_lines) > _owner._SSH_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _owner._SSH_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #
    def _migrate_selected(self) -> None:
        names = self._checked_names()
        if not names:
            _owner.QMessageBox.information(self, "未选择文件", "请先勾选要迁移的 SSH 文件。")
            return
        if self._target is None:
            _owner.QMessageBox.information(self, "尚未扫描", "先点「扫描」确认来源目录与实例现状。")
            return
        overwrite = set(_owner._sshfiles.conflicts(names, self._target))
        if overwrite:
            listing = "\n".join(f"· {name}" for name in names if name in overwrite)
            text = (
                "实例内已存在以下文件，迁移会先把原文件备份到 "
                f"~/.ssh/{_owner._sshfiles.backup_name(_owner.time.time())}/ 再覆盖：\n\n{listing}\n\n继续？"
            )
            if not _owner.confirm(self, "覆盖实例内已有 SSH 文件", text):
                return
        self._set_busy(True)
        self._refresh_after_run = False
        self._append_log("开始迁移：" + "、".join(names))
        _owner.submit_daemon(
            lambda: self._migrate(names, overwrite),
            on_done=self._migrate_done,
            on_error=self._migrate_error,
            on_finished=self._migrate_finished,
        )

    def _read_remote(
        self,
        env: Any,
        wsl_path: str,
        source: _owner._sshfiles.SshSource,
        name: str,
        entry: _owner._sshfiles.SshFile,
    ) -> bytes:
        """经 ``wsl.exe --exec cat`` 读来源实例文件：内容只走 stdout，不进命令行。"""
        path = _owner._sshfiles.source_file_path(source, name)
        self._bridge.event.emit(f"[{name}] 从实例 {source.distro} 读取…")
        result = env.runner.run(
            [wsl_path, "-d", source.distro, "--exec", *_owner._sshfiles.remote_read_argv(path)],
            timeout=120.0,
        )
        if result.returncode != 0:
            tail = _owner._initenv.tail_lines(_owner.decode_console(result.stderr).strip(), 3)
            raise _owner.runtime_error(
                f"从实例 {source.distro} 读取 {name} 失败（退出码 {result.returncode}）",
                hint=tail[-1] if tail else f"确认 {source.distro} 可启动，并重新扫描后再试",
            )
        data = result.stdout
        if len(data) != entry.size:
            raise _owner.runtime_error(
                f"{name} 自扫描后大小已变化（{len(data)} ≠ {entry.size} 字节）",
                hint="重新扫描后再迁移",
            )
        return data

    def _migrate(self, names: Sequence[str], overwrite: set[str]) -> str:
        """逐个文件写入：内容走 stdin，命令行只带文件名、权限与备份目录。"""
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        source = self._source_snapshot or self._source()
        backup = _owner._sshfiles.backup_name(_owner.time.time())
        for name in names:
            entry = self._files.get(name)
            if entry is None:
                raise _owner.runtime_error(f"来源文件已变化：{name}", hint="重新扫描后再迁移")
            if source.is_wsl:
                data = self._read_remote(env, wsl_path, source, name, entry)
            else:
                data = _owner._sshfiles.read_source(_owner.Path(source.directory) / name)
            script = _owner._sshfiles.write_script(name, entry.mode, overwrite=name in overwrite, backup=backup)
            self._bridge.event.emit(f"[{name}] 写入 ~/.ssh（权限 {entry.mode_text}）…")
            result = env.runner.run(
                [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", script],
                timeout=120.0,
                stdin=data,
            )
            text = _owner.decode_console(result.stdout + result.stderr).strip()
            for line in _owner._initenv.tail_lines(text):
                self._bridge.event.emit(line)
            if result.returncode != 0:
                tail = _owner._initenv.tail_lines(text, 3)
                hint = tail[-1] if tail else "查看日志，或在实例内手动检查 ~/.ssh"
                if result.returncode == 3:
                    hint = "目标文件在扫描后才出现；重新扫描并确认覆盖后再试"
                raise _owner.runtime_error(f"{name} 迁移失败（退出码 {result.returncode}）", hint=hint)
            mode, owner = _owner._sshfiles.parse_write_result(text)
            if mode != entry.mode_text:
                raise _owner.runtime_error(
                    f"{name} 写入后权限为 {mode or '未知'}（期望 {entry.mode_text}）",
                    hint="确认实例的 ~/.ssh 位于 Linux 文件系统（不要落在 /mnt/c），再重新迁移",
                )
            self._bridge.event.emit(f"[{name}] 完成（{owner or '未知用户'} · {mode}）")
        return f"已迁移 {len(names)} 个文件"

    def _migrate_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(message)
        self.summary.setText("迁移完成，正在刷新…")

    def _migrate_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(f"迁移失败：{message}")
        self.summary.setText("迁移失败")
        _owner.QMessageBox.critical(self, "迁移失败", message)

    def _migrate_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh()
