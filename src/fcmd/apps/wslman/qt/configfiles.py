"""wslman 迁移配置文件卡片（ADR 0046 P2/P3）。

原「迁移 SSH 文件」卡片只处理 ``~/.ssh``；现在按项目扩展成「迁移配置文件」：
SSH / Git / Shell 三个选项像「初始化环境」那样各自带状态标签，来源可以是
Windows 宿主家目录，也可以是任一其它已安装 WSL 实例的 ``$HOME``。
"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.gui.widgets import Card

from ._compat import Any, Distro, Sequence


class _ConfigMigrateSection(Card):  # pragma: no cover - Qt 控件
    """迁移配置文件卡片：按项目勾选 SSH / Git / Shell 配置，备份后写入当前实例。

    与「初始化环境」并列放在实例配置对话框里：初始化负责在实例内生成密钥、装好工具，
    本卡片负责把已经在用的个人配置带进来。跨实例来源通过 ``wsl.exe`` 的 ``cat``
    文件流读取，不依赖实例内是否装了 rsync；目标始终是当前实例。文件内容通过
    stdin 送进目标实例，命令行与日志里只有路径和权限。
    """

    def __init__(  # noqa: PLR0913 - 缓存 / 回调 / 状态提供者都是可选注入点
        self,
        parent: _owner.QWidget,
        distro: Distro,
        *,
        instances: Sequence[str] = (),
        status: _owner._configfiles.MigrationSnapshot | None = None,
        on_scanned: Any = None,
        state_provider: Any = None,
    ) -> None:
        super().__init__("迁移配置文件", parent)
        self._distro = distro
        self._instances = tuple(instances)
        self._state_provider = state_provider
        self._on_scanned = on_scanned
        self._busy = False
        self._closing = False
        self._scanned = False
        self._syncing = False
        self._files: dict[str, _owner._configfiles.ConfigFile] = {}
        self._target: _owner._configfiles.ConfigTarget | None = None
        self._source_snapshot: _owner._configfiles.ConfigSource | None = None
        self._keepalive_target = False
        self._keepalive_source = False
        self._boxes: dict[str, _owner.QCheckBox] = {}
        self._group_boxes: dict[str, _owner.QCheckBox] = {}
        self._group_tags: dict[str, _owner.StatusTag] = {}
        self._row_tags: dict[str, _owner.StatusTag] = {}
        self._group_hints: dict[str, _owner.ElidedLabel] = {}
        self._group_files: dict[str, _owner.QVBoxLayout] = {}
        self._log_lines: list[str] = []
        self._refresh_after_run = False
        self._bridge = _owner.EventBridge(self)
        self._bridge.event.connect(self._append_log)
        self.destroyed.connect(self._on_destroyed)
        self.setToolTip(_owner._CONFIG_HINT)

        self._collapsed = True
        header = _owner.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.collapsed_hint = _owner.hint_label("尚未扫描来源目录。", wrap=False)
        header.addWidget(self.collapsed_hint)
        # 每个项目一个标签（绿＝已一致 / 黄＝部分 / 红＝未迁移 / 灰＝无文件），
        # 与「初始化环境」收起行的短标签同口径。
        for group in _owner._configfiles.GROUPS:
            tag = _owner.StatusTag(group.short, tone="muted", parent=self)
            tag.setToolTip(group.detail)
            tag.setVisible(False)
            header.addWidget(tag)
            self._group_tags[group.key] = tag
        self.state_tag = _owner.StatusTag("", tone="muted", parent=self)
        self.state_tag.setVisible(False)
        header.addWidget(self.state_tag)
        self.summary = _owner.ElidedLabel("", parent=self)
        self.summary.setObjectName("Hint")
        self.summary.setToolTip(_owner._CONFIG_HINT)
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

        # 来源类型：Windows 宿主家目录，或另一个已安装的 WSL 实例（当前实例不作为来源）。
        source_row = _owner.QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        source_row.addWidget(_owner.hint_label("来源", wrap=False))
        self.source_kind = _owner.QComboBox(self)
        self.source_kind.addItem(
            _owner._configfiles.SOURCE_LABELS[_owner._configfiles.SOURCE_WINDOWS],
            _owner._configfiles.SOURCE_WINDOWS,
        )
        if self._instances:
            self.source_kind.addItem(
                _owner._configfiles.SOURCE_LABELS[_owner._configfiles.SOURCE_WSL],
                _owner._configfiles.SOURCE_WSL,
            )
        self.source_kind.setToolTip("Windows 宿主的 %USERPROFILE%，或另一个 WSL 实例的 $HOME")
        self.source_kind.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_kind)
        self.source_instance = _owner.QComboBox(self)
        for name in self._instances:
            self.source_instance.addItem(name, name)
        self.source_instance.setToolTip("从这里读取配置文件；实例未运行时会由 wsl.exe 启动后再读")
        self.source_instance.setVisible(False)
        self.source_instance.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_instance, 1)
        source_row.addStretch(1)
        details.addLayout(source_row)

        path_row = _owner.QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        self.source_label = _owner.hint_label("Windows 侧家目录", wrap=False)
        path_row.addWidget(self.source_label)
        self.source = _owner.PathRow(
            value=str(_owner._configfiles.default_source_dir()),
            placeholder=r"%USERPROFILE%",
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

        # 三个项目固定展示（即使来源里没有）；文件勾选框挂在各自项目下面。
        for group in _owner._configfiles.GROUPS:
            group_box = _owner.QCheckBox(group.label, self)
            group_box.setToolTip(group.detail)
            group_box.setChecked(False)
            group_box.setEnabled(False)
            group_box.stateChanged.connect(lambda _state, key=group.key: self._on_group_toggled(key))
            self._group_boxes[group.key] = group_box
            tag = _owner.StatusTag("无文件", tone="muted", parent=self)
            tag.setVisible(False)
            self._row_tags[group.key] = tag
            hint = _owner.ElidedLabel("", parent=self)
            hint.setObjectName("Hint")
            row = _owner.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(group_box)
            row.addWidget(tag)
            row.addWidget(hint, 1)
            details.addLayout(row)
            self._group_hints[group.key] = hint
            files_box = _owner.QWidget(self)
            files_layout = _owner.QVBoxLayout(files_box)
            files_layout.setContentsMargins(20, 0, 0, 0)
            files_layout.setSpacing(2)
            details.addWidget(files_box)
            self._group_files[group.key] = files_layout

        # 实例侧现状（含同名文件与权限过宽的提示）直接铺开，不用再点一层。
        self.target_hint = _owner.hint_label("尚未检测实例内的配置文件。")
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
        self.migrate_button = _owner.button("迁移所选配置", kind="primary")
        self.migrate_button.clicked.connect(self._migrate_selected)
        self.migrate_button.setEnabled(False)
        actions.addWidget(self.migrate_button)
        self.body.addLayout(actions)

        self._sync_collapsed_view()
        self._sync_source_controls()
        if status is not None:
            self._apply_cached(status)

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
        return _owner.Path(text) if text else _owner._configfiles.default_source_dir()

    def _source(self) -> _owner._configfiles.ConfigSource:
        """当前选择的来源；WSL 来源的家目录要等扫描探测后再补齐。"""
        if self.source_kind.currentData() == _owner._configfiles.SOURCE_WSL:
            return _owner._configfiles.ConfigSource(
                _owner._configfiles.SOURCE_WSL, str(self.source_instance.currentData() or "")
            )
        return _owner._configfiles.ConfigSource(_owner._configfiles.SOURCE_WINDOWS, "", str(self._source_dir()))

    def _on_source_changed(self, _index: int = 0) -> None:
        """切换来源类型 / 实例：清掉旧结果，展开态下立即重新扫描。"""
        self._reset_results()
        self._sync_source_controls()
        if not self._collapsed and not self._busy:
            self.refresh()

    def _sync_source_controls(self) -> None:
        """按来源类型切换「本地目录」与「实例 + 实际读取路径」两组控件。"""
        wsl_source = self.source_kind.currentData() == _owner._configfiles.SOURCE_WSL
        self.source_instance.setVisible(wsl_source)
        self.source.setVisible(not wsl_source)
        self.source_remote.setVisible(wsl_source)
        self.source_label.setText("来源实例家目录" if wsl_source else "Windows 侧家目录")
        if wsl_source:
            self.source_remote.setText(self._remote_path_text())
            self.source_remote.setToolTip(self.source_remote.text())

    def _remote_path_text(self) -> str:
        """WSL 来源实际读取的位置；扫描前给出预期位置，扫描后换成探测到的家目录。"""
        distro = str(self.source_instance.currentData() or "")
        snapshot = self._source_snapshot
        if snapshot is not None and snapshot.is_wsl and snapshot.distro == distro:
            return snapshot.display_path()
        return f"{distro or '（未选择实例）'}:$HOME（扫描后显示实际路径）"

    def _reset_results(self) -> None:
        """来源变化或扫描失败后，旧的判定与勾选都不再成立。"""
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._scanned = False
        self._rebuild_files()
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self.summary.setText("")
        for tag in self._group_tags.values():
            tag.setVisible(False)
        self.file_hint.setText("点「扫描」读取来源目录并检测实例。")
        self.target_hint.setText("尚未检测实例内的配置文件。")
        self.collapsed_hint.setText("尚未扫描来源目录。")
        self._sync_actions()

    def refresh(self) -> None:
        """后台扫描来源（宿主家目录或实例 $HOME）并探测当前实例。"""
        if self._busy:
            return
        state = self._current_state()
        wsl_source = self.source_kind.currentData() == _owner._configfiles.SOURCE_WSL
        may_start = state != "running" or wsl_source
        if may_start and not _owner.confirm(
            self,
            "扫描配置文件",
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

    def _probe(self, env: Any, wsl_path: str, distro: str, action: str, hint: str) -> _owner._configfiles.ConfigTarget:
        """在实例内跑只读探测脚本，解析受管配置文件快照。"""
        result = env.runner.run(
            [wsl_path, "-d", distro, "--exec", "bash", "-lc", _owner._configfiles.probe_script()],
            timeout=180.0,
        )
        text = _owner.decode_console(result.stdout + result.stderr)
        target = _owner._configfiles.parse_probe(text)
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
    ) -> tuple[
        tuple[_owner._configfiles.ConfigFile, ...],
        _owner._configfiles.ConfigTarget,
        _owner._configfiles.ConfigSource,
    ]:
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
                f"读取实例 {source.distro} 的配置文件",
                f"确认实例已安装；若未运行，先在实例页启动 {source.distro} 后重试"
                "（wsl.exe 通常也会自动拉起已停止的实例）。",
            )
            source = _owner._configfiles.ConfigSource(_owner._configfiles.SOURCE_WSL, source.distro, remote.home)
            files = _owner._configfiles.scan_target(remote)
        else:
            files = _owner._configfiles.scan_host(_owner.Path(source.directory))
        target = self._probe(
            env,
            wsl_path,
            self._distro.name,
            f"检测 {self._distro.name} 的配置文件",
            "确认实例可启动，或运行 fcmd wslman doctor",
        )
        return files, target, source

    def _scan_done(
        self,
        result: tuple[
            tuple[_owner._configfiles.ConfigFile, ...],
            _owner._configfiles.ConfigTarget,
            _owner._configfiles.ConfigSource,
        ],
    ) -> None:
        if self._closing:
            return
        files, target, source = result
        snapshot = _owner._configfiles.build_snapshot(files, target, source)
        self._source_snapshot = source
        self._files = {item.path: item for item in files}
        self._target = target
        self._scanned = True
        self._rebuild_files()
        paths = "、".join(item.path for item in files)
        self.file_hint.setText(f"找到 {len(files)} 个文件：{paths}" if files else "来源里没有受管项目的配置文件")
        self.target_hint.setText(snapshot.target_summary)
        self.summary.setText(snapshot.summary)
        self.state_tag.set_status(snapshot.state.label, snapshot.state.tone)
        self.state_tag.setToolTip(snapshot.state.detail)
        self.state_tag.setVisible(True)
        self.collapsed_hint.setText(snapshot.collapsed_hint)
        self._apply_group_states(files, target)
        self._append_log(f"扫描完成：来源 {source.display_path()}（{len(files)} 个文件）")
        self._append_log(snapshot.target_summary)
        self._group_states_visibility(True)
        self._sync_source_controls()
        self._sync_actions()
        self._sync_collapsed_view()
        if callable(self._on_scanned):
            self._on_scanned(snapshot)

    def _apply_group_states(
        self, files: Sequence[_owner._configfiles.ConfigFile], target: _owner._configfiles.ConfigTarget
    ) -> None:
        """把每个项目的进度写进标签与勾选框提示。"""
        for state in _owner._configfiles.group_states(files, target):
            for tag in (self._group_tags.get(state.key), self._row_tags.get(state.key)):
                if tag is None:
                    continue
                tag.set_status(f"{state.short} {state.label}", state.tone)
                tag.setToolTip(state.detail)
            hint = self._group_hints.get(state.key)
            if hint is not None:
                hint.setText(state.detail)
                hint.setToolTip(state.detail)

    def _group_states_visibility(self, visible: bool) -> None:
        for tags in (self._group_tags, self._row_tags):
            for tag in tags.values():
                tag.setVisible(visible)

    def _apply_cached(self, snapshot: _owner._configfiles.MigrationSnapshot) -> None:
        """回显上次扫描状态：不启动实例、不重扫，只把标签与说明填回去。

        文件清单与实例侧快照不恢复：迁移要按最新现状判断冲突与权限，标签上明确
        写「上次扫描」并提示迁移前先扫描，避免照着旧结果写盘。
        """
        state = snapshot.state
        self.state_tag.set_status(state.label, state.tone)
        detail = state.detail
        if snapshot.scanned_at:
            detail = f"{detail}\n上次扫描：{snapshot.scanned_at}"
        self.state_tag.setToolTip(detail)
        self.state_tag.setVisible(True)
        self.summary.setText(snapshot.summary)
        self.collapsed_hint.setText(snapshot.collapsed_hint)
        self.file_hint.setText("显示上次扫描结果；迁移前请点「扫描」确认来源与实例现状。")
        self.target_hint.setText(snapshot.target_summary or "尚未检测实例内的配置文件。")
        counts = snapshot.counts
        for group in _owner._configfiles.GROUPS:
            progress = counts.get(group.key)
            for tag in (self._group_tags.get(group.key), self._row_tags.get(group.key)):
                if tag is None:
                    continue
                if progress is None:
                    tag.setVisible(False)
                    continue
                label = progress.label if progress.total else "无文件"
                tag.set_status(f"{group.short} {label}", progress.tone)
                tag.setToolTip(f"{group.label}：上次扫描 {progress.matched}/{progress.total}")
                tag.setVisible(True)
        source = f"（来源 {snapshot.source_path}）" if snapshot.source_path else ""
        self._append_log(f"显示上次扫描结果：{state.label}{source}")
        self._sync_collapsed_view()

    def _scan_error(self, message: str) -> None:
        if self._closing:
            return
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._scanned = False
        self._rebuild_files()
        self.file_hint.setText("扫描失败，检查来源后重试")
        self.summary.setText("扫描失败")
        self.collapsed_hint.setText("扫描失败，展开后可重试。")
        self.target_hint.setText("")
        # 扫描失败时旧结果已经不成立，隐藏标签而不是继续展示过期状态。
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self._group_states_visibility(False)
        self._sync_source_controls()
        self._sync_actions()
        self._append_log(f"扫描失败：{message}")

    def _scan_finished(self) -> None:
        if not self._closing:
            self._set_busy(False)

    def _rebuild_files(self) -> None:
        self._syncing = True
        for layout in self._group_files.values():
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._boxes.clear()
        for group in _owner._configfiles.GROUPS:
            entries = [item for item in self._files.values() if item.group == group.key]
            layout = self._group_files.get(group.key)
            box = self._group_boxes.get(group.key)
            if layout is None or box is None:
                continue
            for entry in entries:
                widget = _owner.QCheckBox(entry.display, self)
                tip = f"写入 ~/{entry.path}，权限 {entry.mode_text}"
                if entry.warning:
                    tip += f"\n注意：{entry.warning}"
                widget.setToolTip(tip)
                widget.setChecked(entry.checked_by_default)
                widget.stateChanged.connect(self._on_file_toggled)
                layout.addWidget(widget)
                self._boxes[entry.path] = widget
            box.setEnabled(bool(entries) and not self._busy)
            box.setChecked(bool(entries) and any(item.checked_by_default for item in entries))
        self._syncing = False
        self._sync_group_boxes()

    def _on_file_toggled(self, _state: int = 0) -> None:
        if self._syncing:
            return
        self._sync_group_boxes()
        self._sync_actions()

    def _on_group_toggled(self, key: str) -> None:
        """勾选项目 = 勾选该项目下所有可迁移文件；只在用户操作时生效。"""
        if self._syncing:
            return
        box = self._group_boxes.get(key)
        if box is None:
            return
        checked = box.isChecked()
        self._syncing = True
        for path, child in self._boxes.items():
            if self._files[path].group == key and child.isEnabled():
                child.setChecked(checked)
        self._syncing = False
        self._sync_actions()

    def _sync_group_boxes(self) -> None:
        self._syncing = True
        for group in _owner._configfiles.GROUPS:
            box = self._group_boxes.get(group.key)
            if box is None:
                continue
            children = [
                child
                for path, child in self._boxes.items()
                if self._files[path].group == group.key and child.isEnabled()
            ]
            box.setChecked(bool(children) and all(child.isChecked() for child in children))
        self._syncing = False

    def _set_all(self, checked: bool) -> None:
        if self._busy:
            return
        self._syncing = True
        for box in self._boxes.values():
            if box.isEnabled():
                box.setChecked(checked)
        self._syncing = False
        self._sync_group_boxes()
        self._sync_actions()

    def _checked_paths(self) -> list[str]:
        return [path for path, box in self._boxes.items() if box.isChecked() and box.isEnabled()]

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        # 扫描 / 迁移期间锁住来源选择，避免结果回来时来源已经换了。
        self.source_kind.setEnabled(not busy)
        self.source_instance.setEnabled(not busy)
        self.source.setEnabled(not busy)
        for box in self._boxes.values():
            box.setEnabled(not busy)
        for key, box in self._group_boxes.items():
            box.setEnabled(not busy and any(item.group == key for item in self._files.values()))
        self._sync_actions()

    def _sync_actions(self) -> None:
        checked = self._checked_paths()
        self.migrate_button.setEnabled(not self._busy and self._target is not None and bool(checked))
        if not checked:
            self.migrate_button.setToolTip("先扫描并勾选要迁移的配置文件")
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
        if len(self._log_lines) > _owner._CONFIG_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _owner._CONFIG_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #
    def _migrate_selected(self) -> None:
        paths = self._checked_paths()
        if not paths:
            _owner.QMessageBox.information(self, "未选择配置", "请先勾选要迁移的配置文件。")
            return
        if self._target is None:
            _owner.QMessageBox.information(self, "尚未扫描", "先点「扫描」确认来源目录与实例现状。")
            return
        overwrite = set(_owner._configfiles.conflicts(paths, self._target))
        if overwrite:
            listing = "\n".join(f"· {path}" for path in paths if path in overwrite)
            text = (
                "实例内已存在以下文件，迁移会先把原文件备份到 "
                f"~/{_owner._configfiles.CONFIG_BACKUP_PREFIX}<时间>/（SSH 仍在 ~/.ssh 下）再覆盖：\n\n"
                f"{listing}\n\n继续？"
            )
            if not _owner.confirm(self, "覆盖实例内已有配置文件", text):
                return
        self._set_busy(True)
        self._refresh_after_run = False
        self._append_log("开始迁移：" + "、".join(paths))
        _owner.submit_daemon(
            lambda: self._migrate(paths, overwrite),
            on_done=self._migrate_done,
            on_error=self._migrate_error,
            on_finished=self._migrate_finished,
        )

    def _read_remote(
        self,
        env: Any,
        wsl_path: str,
        source: _owner._configfiles.ConfigSource,
        path: str,
        entry: _owner._configfiles.ConfigFile,
    ) -> bytes:
        """经 ``wsl.exe --exec cat`` 读来源实例文件：内容只走 stdout，不进命令行。"""
        remote = _owner._configfiles.source_file_path(source, path)
        self._bridge.event.emit(f"[{path}] 从实例 {source.distro} 读取…")
        result = env.runner.run(
            [wsl_path, "-d", source.distro, "--exec", *_owner._configfiles.remote_read_argv(remote)],
            timeout=120.0,
        )
        if result.returncode != 0:
            tail = _owner._initenv.tail_lines(_owner.decode_console(result.stderr).strip(), 3)
            raise _owner.runtime_error(
                f"从实例 {source.distro} 读取 {path} 失败（退出码 {result.returncode}）",
                hint=tail[-1] if tail else f"确认 {source.distro} 可启动，并重新扫描后再试",
            )
        data = result.stdout
        if len(data) != entry.size:
            raise _owner.runtime_error(
                f"{path} 自扫描后大小已变化（{len(data)} ≠ {entry.size} 字节）",
                hint="重新扫描后再迁移",
            )
        return data

    def _migrate(self, paths: Sequence[str], overwrite: set[str]) -> str:
        """逐个文件写入：内容走 stdin，命令行只带路径、权限与备份目录。"""
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        source = self._source_snapshot or self._source()
        stamp = _owner.time.time()
        for path in paths:
            entry = self._files.get(path)
            if entry is None:
                raise _owner.runtime_error(f"来源文件已变化：{path}", hint="重新扫描后再迁移")
            if source.is_wsl:
                data = self._read_remote(env, wsl_path, source, path, entry)
            else:
                data = _owner._configfiles.read_source(_host_path(_owner.Path(source.directory), path))
            # 写入前做 WSL 适配：去掉 Windows 专用 sshCommand / 路径，CRLF 转 LF。
            adaptation = _owner._configadapt.adapt(entry.group, path, data)
            for note in adaptation.notes:
                self._bridge.event.emit(f"[{path}] 适配：{note}")
            data = adaptation.data
            group = _owner._configfiles.group_by_key(entry.group)
            if group is None:
                raise _owner.runtime_error(f"未知的迁移项目：{entry.group}", hint="重新扫描后再迁移")
            script = _owner._configfiles.write_script(
                path,
                entry.mode,
                overwrite=path in overwrite,
                backup=_owner._configfiles.backup_root(entry.group, stamp),
                dir_mode=_owner._configfiles.target_dir_mode(group),
            )
            self._bridge.event.emit(f"[{path}] 写入 ~/{path}（权限 {entry.mode_text}）…")
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
                hint = tail[-1] if tail else "查看日志，或在实例内手动检查配置文件"
                if result.returncode == 3:
                    hint = "目标文件在扫描后才出现；重新扫描并确认覆盖后再试"
                raise _owner.runtime_error(f"{path} 迁移失败（退出码 {result.returncode}）", hint=hint)
            mode, owner = _owner._configfiles.parse_write_result(text)
            if mode != entry.mode_text:
                raise _owner.runtime_error(
                    f"{path} 写入后权限为 {mode or '未知'}（期望 {entry.mode_text}）",
                    hint="确认目标目录位于 Linux 文件系统（不要落在 /mnt/c），再重新迁移",
                )
            self._bridge.event.emit(f"[{path}] 完成（{owner or '未知用户'} · {mode}）")
        return f"已迁移 {len(paths)} 个文件"

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


def _host_path(base: _owner.Path, path: str) -> _owner.Path:
    """把相对 ``$HOME`` 的 POSIX 路径拼到 Windows 家目录上。"""
    result = base
    for part in path.split("/"):
        result = result / part
    return result


_SshMigrateSection = _ConfigMigrateSection
"""兼容旧引用名：卡片已从「迁移 SSH 文件」扩展为「迁移配置文件」。"""
