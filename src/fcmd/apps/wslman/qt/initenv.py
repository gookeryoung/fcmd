"""wslman 初始化环境卡片（ADR 0046 P2/P3）。"""

from __future__ import annotations

import fcmd.apps.wslman._wslmanw_qt as _owner
from fcmd.gui.widgets import Card

from ._compat import Any, Distro, Sequence


class _InitEnvSection(Card):  # pragma: no cover - Qt 控件
    """初始化环境卡片：就地检测、勾选项目、执行并复制 SSH 公钥。

    探测与安装都走 ``wsl.exe -d <实例> --exec bash -lc``，命令在后台线程执行，
    界面只负责调度；需要 root 的步骤（APT / 系统源）由
    :class:`~fcmd.apps.wslman._initenv.InitStep` 的 ``root`` 标记决定加 ``-u root``。
    不做成独立对话框：配置对话框一层里就能看到各项状态并操作，避免多层弹窗。
    """

    def __init__(  # noqa: PLR0913 - 缓存 / 回调 / 状态提供者都是可选注入点
        self,
        parent: _owner.QWidget,
        distro: Distro,
        selected: Sequence[str] | None = None,
        *,
        status: _owner._initenv.EnvStatus | None = None,
        on_detected: Any = None,
        state_provider: Any = None,
        package_mirror: str = _owner._initenv.PACKAGE_MIRROR_AUTO,
        on_package_mirror_changed: Any = None,
    ) -> None:
        super().__init__("初始化环境", parent)
        self._distro = distro
        self._state_provider = state_provider
        self._selected = None if selected is None else {key for key in selected if key in _owner._initenv.task_keys()}
        """上次保存的勾选（``None`` 表示未保存过，按默认勾选）。"""
        self._on_detected = on_detected
        self._on_package_mirror_changed = on_package_mirror_changed
        self.setToolTip(_owner._INIT_ENV_HINT)
        self._status: _owner._initenv.EnvStatus | None = None
        self._done: set[str] = set()
        """检测结果里已就绪的项目 key；这些勾选框保持勾选 + 禁用，并显示绿色标签。"""
        self._tasks = {task.key: task for task in _owner._initenv.TASKS}
        self._busy = False
        self._package_mirror_dirty = False
        """软件包源改过（或探测到的 pip/uv 索引不匹配）时，下次初始化先同步配置。"""
        self._closing = False
        self._refresh_after_run = False
        self._keepalive = False
        self._log_lines: list[str] = []
        self._bridge = _owner.EventBridge(self)
        self._bridge.event.connect(self._append_log)
        # 对话框先于后台任务结束就被销毁时，回调只写日志、不再碰控件。
        self.destroyed.connect(self._on_destroyed)

        self._collapsed = True
        self._collapsed_tags: dict[str, _owner.StatusTag] = {}

        # 收起 / 展开共用一行：收起显示「已初始化情况」，展开换成一行摘要。
        # 三个动作（检测 / 复制 SSH / 开始初始化）与全选不随详情收起。
        header = _owner.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.collapsed_hint = _owner.hint_label("尚未检测环境。", wrap=False)
        header.addWidget(self.collapsed_hint)
        for task in _owner._initenv.TASKS:
            tag = _owner.StatusTag(_owner._initenv.short_label(task.key), tone="success", parent=self)
            tag.setToolTip(f"{task.label}：已初始化")
            tag.setVisible(False)
            header.addWidget(tag)
            self._collapsed_tags[task.key] = tag
        self.summary = _owner.ElidedLabel("尚未检测环境。", parent=self)
        self.summary.setObjectName("Hint")
        self.summary.setToolTip(_owner._INIT_ENV_HINT)
        self.summary.setVisible(False)
        header.addWidget(self.summary, 1)
        self.toggle_button = _owner.icon_button("", tooltip="展开详情")
        self.toggle_button.setIconSize(_owner.QSize(16, 16))
        _owner._qt_icons.set_icon(self.toggle_button, "chevron-down", tone="muted")
        self.toggle_button.setAccessibleName("展开详情")
        # ``clicked`` 会带一个 checked(bool) 参数；直接连 ``toggle_collapsed`` 会把
        # 它当成 ``collapsed=False``，展开后再也收不起来。
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self.toggle_button)
        self.body.addLayout(header)

        # 展开后才出现的内容：左右两栏（勾选 / 状态）+ 日志；说明只在 tooltip。
        self.details = _owner.QWidget(self)
        details = _owner.QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(2)

        columns = _owner.QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(12)
        checks_col = _owner.QVBoxLayout()
        checks_col.setContentsMargins(0, 0, 0, 0)
        checks_col.setSpacing(2)
        status_col = _owner.QVBoxLayout()
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(2)

        self.checks: dict[str, _owner.QCheckBox] = {}
        self.status_tags: dict[str, _owner.StatusTag] = {}
        for task in _owner._initenv.TASKS:
            row = _owner.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            box = _owner.QCheckBox(task.label, self)
            box.setToolTip(task.detail)
            box.setChecked(self._initial_checked(task.key))
            row.addWidget(box)
            tag = _owner.StatusTag("已初始化", tone="success", parent=self)
            tag.setToolTip("环境已就绪：保持勾选但不可再执行，需要重跑时请手动在实例内操作")
            tag.setVisible(False)
            row.addWidget(tag)
            row.addStretch(1)
            checks_col.addLayout(row)
            self.checks[task.key] = box
            self.status_tags[task.key] = tag
        checks_col.addStretch(1)

        # 状态值单行显示（不换行才不撑高），完整内容放 tooltip；每项独占一行，
        # 避免两对一行时 URL 之类长值被压到 1/4 卡片宽而大量截断。
        self.status_grid = _owner.FormGrid(self)
        self._status_values: dict[str, _owner.QLabel] = {}
        for label, _value, _ready in _owner._initenv.status_rows(_owner._initenv.EnvStatus()):
            value = self._make_status_value()
            self._status_values[label] = value
            self.status_grid.add_row(label, value)
        status_col.addWidget(self.status_grid)
        status_col.addStretch(1)

        columns.addLayout(checks_col, 1)
        columns.addLayout(status_col, 1)
        details.addLayout(columns)

        self.log = _owner.QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("执行日志…")
        self.log.setMinimumHeight(56)
        self.log.setMaximumHeight(72)
        details.addWidget(self.log)

        self.body.addWidget(self.details)
        self.details.setVisible(False)

        # 软件包源独立于系统镜像源：只影响 pip / uv，可选跟随系统源或指定源。
        source_row = _owner.QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        source_row.addWidget(_owner.QLabel("软件包源", self))
        self.package_mirror = _owner.QComboBox(self)
        for key, label in _owner._initenv.PACKAGE_MIRROR_CHOICES:
            self.package_mirror.addItem(label, key)
        selected_mirror = _owner._initenv.normalize_package_mirror(package_mirror)
        self.package_mirror.setCurrentIndex(max(self.package_mirror.findData(selected_mirror), 0))
        self.package_mirror.setToolTip(
            "独立于系统镜像源（APT / YUM），只影响 pip / uv 的索引；"
            "「自动」表示跟随当前系统镜像源。npm / bun 仍使用 npmmirror。"
        )
        self.package_mirror.currentIndexChanged.connect(self._on_package_mirror_index_changed)
        source_row.addWidget(self.package_mirror)
        source_row.addWidget(
            _owner.hint_label("独立于系统镜像源；仅影响 pip / uv，npm / bun 仍用 npmmirror。", wrap=False), 1
        )
        self.body.addLayout(source_row)

        # 动作行固定在卡片里，不随详情显隐：收起态下也能全选 / 检测 / 复制 / 初始化。
        actions = _owner.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.copy_key = _owner.button("复制 SSH 公钥", kind="ghost")
        self.copy_key.setEnabled(False)
        self.copy_key.setToolTip("SSH 密钥生成后可用；复制的是 ~/.ssh/id_ed25519.pub 全文")
        self.copy_key.clicked.connect(self._copy_ssh_key)
        actions.addWidget(self.copy_key)
        select_all = _owner.button("全选", kind="ghost")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = _owner.button("全不选", kind="ghost")
        select_none.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(select_all)
        actions.addWidget(select_none)
        actions.addStretch(1)
        self.detect_button = _owner.button("检测环境")
        self.detect_button.clicked.connect(self.refresh_status)
        actions.addWidget(self.detect_button)
        self.run_button = _owner.button("开始初始化", kind="primary")
        self.run_button.clicked.connect(self._run_selected)
        actions.addWidget(self.run_button)
        self.body.addLayout(actions)

        self._sync_collapsed_view()
        if status is not None:
            self._apply_status(status)
            self._append_log("显示上次检测结果（打开配置不会启动实例）")

    @property
    def collapsed(self) -> bool:
        """是否收起（默认收起：只显示已初始化情况）。"""
        return self._collapsed

    def _on_toggle_clicked(self, _checked: bool = False) -> None:
        """按钮点击入口：忽略 ``clicked`` 带上的 checked 参数，只做反转。"""
        self.toggle_collapsed()

    def toggle_collapsed(self, collapsed: bool | None = None) -> None:
        """展开 / 收起；不传参时反转。收起后只留已初始化标签、动作行与展开按钮。"""
        self._collapsed = (not self._collapsed) if collapsed is None else bool(collapsed)
        self._sync_collapsed_view()
        window = self.window()
        if window is not self and isinstance(window, _owner.QDialog):
            # 行内控件显示 / 隐藏后，窗口的 minimumSizeHint 要等事件循环重算；
            # 立即 adjustSize 会读到展开时的旧值，收起后高度就回不去。
            # PySide2 singleShot 的 (int, QObject, Callable) 签名不支持，
            # 用 (int, Callable) 两参数版本，lambda 捕获 window 保持引用。
            _owner.QTimer.singleShot(0, lambda w=window: w.adjustSize())

    def _sync_collapsed_view(self) -> None:
        """收起：显示全部项目的初始化状态；展开：额外显示勾选、状态与日志。

        检测完成后，已就绪项目用绿色短标签、未就绪项目用红色短标签；不再只展示
        「已初始化」的项目，避免用户以为没显示的项目也已完成。
        """
        if self._status is None:
            hint = "尚未检测环境。"
        elif len(self._done) == len(self.checks):
            hint = "已初始化："
        elif not self._done:
            hint = "未初始化："
        else:
            hint = "初始化状态："
        self.collapsed_hint.setText(hint)
        for key, tag in self._collapsed_tags.items():
            if self._status is None:
                tag.setVisible(False)
                continue
            done = key in self._done
            tag.set_status(_owner._initenv.short_label(key), "success" if done else "danger")
            tag.setToolTip(f"{self._tasks[key].label}：{'已初始化' if done else '未初始化'}")
            tag.setVisible(self._collapsed)
        self.collapsed_hint.setVisible(self._collapsed)
        self.summary.setVisible(not self._collapsed)
        self.details.setVisible(not self._collapsed)
        _owner._qt_icons.set_icon(
            self.toggle_button, "chevron-up" if not self._collapsed else "chevron-down", tone="muted"
        )
        self.toggle_button.setToolTip("收起详情" if not self._collapsed else "展开详情")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())

    def _make_status_value(self) -> _owner.QLabel:
        """状态值控件：单行 + 超宽省略靠 tooltip，避免长 URL 换行把卡片撑高。"""
        value = _owner.QLabel("—", self)
        value.setWordWrap(False)
        value.setMinimumWidth(64)
        value.setSizePolicy(_owner.QSizePolicy.Policy.Ignored, _owner.QSizePolicy.Policy.Preferred)
        value.setTextInteractionFlags(_owner.Qt.TextInteractionFlag.TextSelectableByMouse)
        return value

    def _initial_checked(self, key: str) -> bool:
        """首次渲染的勾选：有保存值用保存值，否则按「未完成就勾选」默认。"""
        if self._selected is not None:
            return key in self._selected
        return _owner._initenv.default_checked(key, _owner._initenv.EnvStatus())

    def package_mirror_key(self) -> str:
        """当前选择的软件包源 key。"""
        value = self.package_mirror.currentData()
        return _owner._initenv.normalize_package_mirror(value if isinstance(value, str) else "")

    def _on_package_mirror_index_changed(self, _index: int) -> None:
        self._package_mirror_dirty = True
        if callable(self._on_package_mirror_changed):
            self._on_package_mirror_changed(self.package_mirror_key())
        self._sync_check_states()

    def _package_mirror_pending(self, status: _owner._initenv.EnvStatus) -> bool:
        """显式软件包源与实例里的 pip / uv 索引不一致时，视为待同步。"""
        expected = _owner._initenv.pypi_index_for_package_mirror(self.package_mirror_key())
        if not expected:
            return self._package_mirror_dirty
        pip_ok = (status.pip_index or "").rstrip("/") == expected.rstrip("/")
        uv_ok = (status.uv_index or "").rstrip("/") == expected.rstrip("/")
        return not (pip_ok and uv_ok)

    def selected_keys(self) -> tuple[str, ...]:
        """当前勾选且未就绪的项目 key，用于保存「已设置参数」。"""
        return tuple(key for key, box in self.checks.items() if box.isChecked() and key not in self._done)

    @property
    def busy(self) -> bool:
        """是否有检测 / 初始化任务在跑。"""
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

    def stop(self) -> None:
        """对话框关闭时调用：忽略后续回调；后台命令本身继续执行。"""
        self._closing = True

    def _on_destroyed(self) -> None:
        self._closing = True

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def _set_all(self, checked: bool) -> None:
        if self._busy:
            return
        # 已就绪的项目是禁用状态，全选 / 全不选都不应改变它们的勾选。
        for box in self.checks.values():
            if box.isEnabled():
                box.setChecked(checked)

    def _apply_status(self, status: _owner._initenv.EnvStatus) -> None:
        """回填详情；已就绪的项目勾选 + 禁用 + 绿色标签，其余按「尚未完成」默认勾选。"""
        self._status = status
        self._package_mirror_dirty = self._package_mirror_pending(status)
        self._done = {key for key in self.checks if _owner._initenv.present(key, status)}
        summary = _owner._initenv.summary_text(status)
        self.summary.setText(summary)
        # 摘要行会自动省略，完整文案放 tooltip。
        self.summary.setToolTip(summary)
        for key, box in self.checks.items():
            if key in self._done:
                box.setChecked(True)
            elif self._selected is not None:
                box.setChecked(key in self._selected)
            else:
                box.setChecked(_owner._initenv.default_checked(key, status))
        for label, value, _ok in _owner._initenv.status_rows(status):
            widget = self._status_values.get(label)
            if widget is not None:
                widget.setText(value)
                widget.setToolTip(value)
        self._sync_check_states()
        self._sync_collapsed_view()
        self.copy_key.setEnabled(status.ssh_ready)
        if status.user:
            self._append_log(f"用户 {status.user} · 主目录 {status.home or '未知'} · {status.os_name or '未知系统'}")

    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log_lines.append(line)
        if len(self._log_lines) > _owner._INIT_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _owner._INIT_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _copy_ssh_key(self) -> None:
        key = self._status.ssh_public_key if self._status is not None else ""
        if not key:
            return
        _owner.QGuiApplication.clipboard().setText(key)
        self.summary.setText("已复制 SSH 公钥")
        # 收起态下 summary 不可见：把回执写到收起行，避免点了没反应。
        self.collapsed_hint.setText("已复制 SSH 公钥")
        self._append_log("已复制 SSH 公钥到剪贴板")

    # ------------------------------------------------------------------ #
    # 检测 / 执行
    # ------------------------------------------------------------------ #
    def _sync_check_states(self) -> None:
        """按「已就绪 / 执行中」同步勾选框、状态标签与「开始初始化」可用性。

        检测完成后每个项目都显示标签：已就绪＝绿色「已初始化」，未就绪＝红色
        「未初始化」；检测前保持隐藏，避免把「还没检测」误报成未初始化。
        """
        checked = self._status is not None
        all_done = bool(self.checks) and len(self._done) == len(self.checks)
        needs_source = self._package_mirror_dirty
        for key, box in self.checks.items():
            done = key in self._done
            box.setEnabled(not self._busy and not done)
            tag = self.status_tags[key]
            tag.setVisible(checked)
            if checked:
                tag.set_status("已初始化" if done else "未初始化", "success" if done else "danger")
                tag.setToolTip(
                    "环境已就绪：保持勾选但不可再执行，需要重跑时请手动在实例内操作"
                    if done
                    else f"{self._tasks[key].label}尚未初始化：勾选后点「开始初始化」"
                )
        self.run_button.setEnabled(not self._busy and (not all_done or needs_source))
        if all_done and needs_source:
            self.run_button.setToolTip("软件包源已修改：点「开始初始化」更新 pip / uv 镜像")
        elif all_done:
            self.run_button.setToolTip("所有项目均已初始化，无需重复执行")
        else:
            self.run_button.setToolTip("")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.detect_button.setEnabled(not busy)
        self._sync_check_states()
        self.copy_key.setEnabled(not busy and self._status is not None and self._status.ssh_ready)

    def refresh_status(self) -> None:
        """后台探测实例环境；实例未运行时先确认，因为它会被 WSL 启动。"""
        if self._busy:
            return
        state = self._current_state()
        if state != "running" and not _owner.confirm(
            self,
            "检测环境",
            f"检测 {self._distro.name} 需要在实例内执行命令，WSL 会启动/唤醒该实例。继续？",
        ):
            self.summary.setText("已取消检测（实例未运行）")
            return
        self._keepalive = state != "running"
        self._set_busy(True)
        self.summary.setText("正在检测环境…")
        _owner.submit_daemon(
            self._detect,
            on_done=self._detection_done,
            on_error=self._detection_error,
            on_finished=self._detection_finished,
        )

    def _detect(self) -> _owner._initenv.EnvStatus:
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        if self._keepalive:
            _owner._ensure_running(env, self._distro.name)
            self._keepalive = False
        result = env.runner.run(
            [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", _owner._initenv.probe_script()],
            timeout=180.0,
        )
        text = _owner.decode_console(result.stdout + result.stderr)
        status = _owner._initenv.parse_status(text)
        if result.returncode != 0 and not status.user:
            raise _owner.runtime_error(
                f"检测 {self._distro.name} 的环境失败（退出码 {result.returncode}）",
                hint=_owner._initenv.tail_lines(text, 3)[-1]
                if text.strip()
                else "确认实例可启动，或运行 fcmd wslman doctor",
            )
        return status

    def _detection_done(self, status: _owner._initenv.EnvStatus) -> None:
        if self._closing:
            return
        self._apply_status(status)
        self._append_log("环境检测完成")
        if callable(self._on_detected):
            self._on_detected(status)

    def _detection_error(self, message: str) -> None:
        if self._closing:
            return
        self.summary.setText("检测失败，可点「检测环境」重试")
        self.collapsed_hint.setText("检测失败，展开后可重试。")
        self._append_log(f"检测失败：{message}")

    def _detection_finished(self) -> None:
        if not self._closing:
            self._set_busy(False)

    def _run_selected(self) -> None:
        # 已就绪的项目是禁用状态，勾着也不重新执行。
        keys = [key for key, box in self.checks.items() if box.isChecked() and box.isEnabled()]
        needs_source = self._package_mirror_dirty
        if not keys and not needs_source:
            _owner.QMessageBox.information(self, "未选择项目", "请至少勾选一项初始化内容。")
            return
        state = self._current_state()
        if state != "running" and not _owner.confirm(
            self,
            "开始初始化",
            f"初始化 {self._distro.name} 需要在实例内执行命令，WSL 会启动/唤醒该实例。继续？",
        ):
            self.summary.setText("已取消初始化（实例未运行）")
            return
        self._keepalive = state != "running"
        steps: list[_owner._initenv.InitStep] = []
        if needs_source:
            steps.extend(_owner._initenv.package_source_steps(self.package_mirror_key()))
        steps.extend(_owner._initenv.steps_for(keys, package_mirror=self.package_mirror_key()))
        self._set_busy(True)
        self._refresh_after_run = False
        labels = list(_owner._initenv.task_labels(keys))
        if needs_source:
            labels.insert(0, "软件包源")
        self._append_log("开始初始化：" + "、".join(labels))
        if self._status is not None and not self._status.mirror and set(keys) & {"fonts", "noto", "emoji"}:
            self._append_log("提示：未检测到国内镜像源，字体包下载可能较慢；可先勾选「运行国内源配置脚本」。")
        _owner.submit_daemon(
            lambda: self._execute(steps),
            on_done=self._run_done,
            on_error=self._run_error,
            on_finished=self._run_finished,
        )

    def _execute(self, steps: Sequence[_owner._initenv.InitStep]) -> str:
        """按顺序执行步骤；输出通过事件桥增量回到界面，失败即停止。"""
        env = _owner.cli._build_environment()
        wsl_path = _owner.cli._require_wsl(env)
        if self._keepalive:
            _owner._ensure_running(env, self._distro.name)
            self._keepalive = False
        for step in steps:
            self._bridge.event.emit(f"[{step.label}] 开始…")
            argv = [wsl_path, "-d", self._distro.name]
            if step.root:
                argv.extend(["-u", "root"])
            # 必须用 --exec：``wsl --`` 仍会经过默认 shell，脚本里的 ``$HOME`` /
            # ``$(...)`` 会被提前展开成空串。--exec 直接 exec，变量留给 bash -lc。
            argv.extend(["--exec", "bash", "-lc", step.script])
            result = env.runner.run(argv, timeout=step.timeout_s)
            text = _owner.decode_console(result.stdout + result.stderr).strip()
            for line in _owner._initenv.tail_lines(text):
                self._bridge.event.emit(line)
            if result.returncode != 0:
                tail = _owner._initenv.tail_lines(text, 3)
                raise _owner.runtime_error(
                    f"{step.label}失败（退出码 {result.returncode}）",
                    hint=tail[-1] if tail else "查看上方日志，或在实例内手动执行",
                )
            self._bridge.event.emit(f"[{step.label}] 完成")
        return "初始化完成"

    def _run_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._package_mirror_dirty = False
        self._append_log(message)
        self.summary.setText("初始化完成，正在刷新环境…")

    def _run_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(f"初始化失败：{message}")
        self.summary.setText("初始化失败")
        _owner.QMessageBox.critical(self, "初始化失败", message)

    def _run_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh_status()
