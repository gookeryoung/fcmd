"""llmw 运行页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_run as _owner

from .run_compat import (
    Any,
    QVBoxLayout,
    _llmpaint_server,
)


class RunPaintMixin:
    """ADR 0046 P4 迁移的运行页方法。"""

    def _build_paint_tab(self, layout: QVBoxLayout) -> None:
        """图像页签：与文本一致——顶部选模型 + 启停，下面只配启动参数。

        引擎安装 / 设为默认在设置页；这里决定「用哪个扩散模型、怎么加载、哪个端口」，
        下方启动参数卡与文本页同构：加载方式方块、运行选项胶囊、高级参数
        折叠区、只读命令预览与自定义启动参数（保存 / 填入）。启停按钮的文案与配色
        对齐 LLM 服务的模型卡片。
        """
        self.paint_model_card = _owner.Card("选择模型")
        model_row = _owner.QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(8)
        paint_combo = _owner._ModelCombo(self.paint_model_card)
        paint_combo.setObjectName("ModelCombo")
        paint_combo.setSizeAdjustPolicy(_owner.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        paint_combo.setMinimumContentsLength(8)
        self.paint_engine_section = _owner.PaintEngineSection(
            _owner.parse_paint_settings(self.state.settings.paint),
            self.paint_model_card,
            show_model=False,
            model_combo=paint_combo,
        )
        self.paint_engine_section.changed.connect(self._on_paint_engine_changed)
        self.paint_engine_section.statusRequested.connect(self.state.status)
        self.paint_engine_section.saveRequested.connect(self._save_paint_params)
        model_row.addWidget(self.paint_engine_section.model_combo, 1)
        self.paint_action_button = _owner.button("启动服务", kind="primary")
        self.paint_action_button.setObjectName("RunPrimary")
        self.paint_action_button.setToolTip("按本页启动参数启动 sd-server")
        self.paint_action_button.clicked.connect(self._on_paint_action)
        model_row.addWidget(self.paint_action_button)
        self.paint_restart_button = _owner.button("重启服务", kind="ghost", tooltip="按当前参数停止并重新启动画图服务")
        self.paint_restart_button.setObjectName("RunAction")
        self.paint_restart_button.setEnabled(False)
        self.paint_restart_button.clicked.connect(self._restart_paint_service)
        model_row.addWidget(self.paint_restart_button)
        self.paint_model_card.body.addLayout(model_row)
        self.paint_compat_hint = _owner.hint_label("", wrap=True)
        self.paint_model_card.body.addWidget(self.paint_compat_hint)
        # 运行状态只看启停按钮文案 + 模型下拉的「✓ 已启动」标记（与文本页一致），
        # 不再在模型行右侧放状态胶囊，也不在下方占一行说明；明细在概览页。
        self.paint_engine_section.assetsLoaded.connect(self._report_paint_hint)
        self.paint_engine_section.assetsLoaded.connect(self._refresh_paint_marker)
        self.paint_engine_section.assetsLoaded.connect(self._refresh_paint_compat_hint)
        self.paint_engine_section.model_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_paint_compat_hint()
        )
        layout.addWidget(self.paint_model_card)

        self.paint_config_card = _owner.Card("启动参数")
        self.paint_config_card.body.addWidget(self.paint_engine_section)
        layout.addWidget(self.paint_config_card)
        layout.addStretch(1)

    @staticmethod
    def _paint_default_hint() -> str:
        return (
            "画图在「对话」页切到「画图」模式后用提示词出图；采样 / 输出目录在对话页 ⚙ 里，"
            "引擎安装 / 设为默认在设置页。"
        )

    def _report_paint_hint(self) -> None:
        """把画图说明 / 缺模型提示写到底部状态栏（只在本页签可见时刷新）。"""
        if self.tabs.currentIndex() != _owner._PAINT_TAB:
            return
        section = getattr(self, "paint_engine_section", None)
        hint = section.missing_model_hint() if section is not None else ""
        self.status(hint or self._paint_default_hint())

    def refresh_paint(self) -> None:
        """刷新画图服务状态：状态文件（同步）+ 端口探针（后台）→ 标签 / 按钮。"""
        state, alive = _owner._llmpaint_server.status()
        self._apply_paint_status(_owner._llmpaint_server.PaintProbe(state, alive, alive, alive))
        if self.tabs.currentIndex() == _owner._PAINT_TAB:
            self._probe_paint()

    def _probe_paint(self) -> None:
        """后台探测端口 / 健康检查：状态文件丢失或外部启动的服务也能看到。"""
        settings = self.paint_settings()
        port = int(settings.get("port") or _owner._llmpaint_server.DEFAULT_PORT)
        self._paint_probe_seq += 1
        seq = self._paint_probe_seq
        _owner.submit(
            _owner._llmpaint_server.probe_paint,
            host=_owner._llmpaint_server.DEFAULT_HOST,
            port=port,
            on_done=lambda probe: self._paint_probed(seq, probe),
            on_error=lambda _message: None,
        )

    def _paint_probed(self, seq: int, probe: _llmpaint_server.PaintProbe) -> None:
        """端口探针回到主线程：只采用最后一次探测结果，避免旧结果覆盖新状态。"""
        if seq != self._paint_probe_seq:
            return
        self._paint_probe = probe
        self._apply_paint_status(probe)

    def _apply_paint_status(self, probe: _llmpaint_server.PaintProbe) -> None:
        """记录探针结果，并同步模型下拉标记与启停按钮（运行状态只看这两处）。"""
        self._paint_probe = probe
        self._refresh_paint_marker()
        self._refresh_paint_actions(probe.alive, probe=probe)
        # 画图服务是独立后端：它的启停同样要反映到「画图」页签的数量徽标。
        self.sync_running_badges()

    def _refresh_paint_marker(self) -> None:
        """把「运行中的扩散模型」标记到画图下拉（与文本页的「✓ 已启动」一致）。"""
        section = getattr(self, "paint_engine_section", None)
        if section is None:
            return
        probe = self._paint_probe
        model = ""
        if probe is not None and probe.alive and probe.state is not None:
            model = probe.state.model
        section.set_running_model(model)

    def _refresh_paint_actions(
        self,
        alive: bool | None = None,
        *,
        probe: _owner._llmpaint_server.PaintProbe | None = None,
    ) -> None:
        """按运行状态切换「启动 / 停止」与重启按钮：与 LLM 模型卡片同一口径。

        ``alive`` 只看本工具登记的进程；端口上探测到未登记的服务（``probe.reachable``）
        也算「在跑」——按钮才能用来结束显存占用。重启只对已登记服务开放。
        """
        if alive is None:
            _state, alive = _owner._llmpaint_server.status()
        if probe is None:
            probe = self._paint_probe
        reachable = bool(probe.reachable) if probe is not None else False
        running = alive or reachable
        busy = self.state.busy or self._paint_busy
        self.paint_action_button.set_text(
            "停止服务" if running else "启动服务",
            tone="danger" if running else "on_accent",
        )
        self.paint_action_button.set_style_name("RunDanger" if running else "RunPrimary")
        self.paint_action_button.setEnabled(not busy)
        self.paint_restart_button.setEnabled(not busy and alive)
        section = getattr(self, "paint_engine_section", None)
        if section is not None:
            section.save_params_button.setEnabled(not busy)
            section.fill_params_button.setEnabled(not busy)

    def paint_settings(self) -> dict[str, Any]:
        """完整画图配置：本页引擎配置 + 已保存的采样 / 输出 + 启用标记。"""
        settings = _owner.parse_paint_settings(self.state.settings.paint)
        section = getattr(self, "paint_engine_section", None)
        if section is not None:
            settings.update(section.settings())
        return _owner.parse_paint_settings(settings)

    def apply_paint_settings(self, settings: dict[str, Any]) -> None:
        """外部（工作流推荐）写入画图配置：同步图像页签控件并刷新未选模型提示。"""
        parsed = _owner.parse_paint_settings(settings)
        self.state.settings.paint = parsed
        self.paint_engine_section.set_settings(parsed)
        self._report_paint_hint()

    def refresh_paint_assets(self) -> None:
        """模型目录变化后重扫扩散模型（图像页签里的下拉）并刷新提示。"""
        self.paint_engine_section.refresh_assets()
        self._report_paint_hint()

    def _on_paint_engine_changed(self) -> None:
        """引擎配置改动：合并写回 paint 设置，延迟落盘，并刷新未选模型提示。"""
        self.state.settings.paint = self.paint_settings()
        self._paint_save_timer.start()
        self._report_paint_hint()

    def _persist_paint(self) -> None:
        self.state.settings.paint = self.paint_settings()
        self.state.persist()

    def _save_paint_params(self) -> None:
        """图像页签「保存参数」：把当前启动参数立即写进 llmw.json（全局只一份）。"""
        self.state.settings.paint = self.paint_settings()
        self.state.persist()
        self.status("画图启动参数已保存")
        self.state.log("已保存画图启动参数")

    def _on_paint_action(self) -> None:
        """同一个按钮：服务空闲时启动，已登记进程则停止，未登记服务则结束残留。"""
        _state, alive = _owner._llmpaint_server.status()
        if alive:
            self._stop_paint_service()
            return
        probe = self._paint_probe
        if probe is not None and probe.reachable:
            self._stop_untracked_paint()
            return
        self._start_paint_service()

    def _stop_untracked_paint(self) -> None:
        """结束端口上未登记的画图服务（状态文件丢失 / CLI / 旧版本遗留）。"""
        port = int(self.paint_settings().get("port") or _owner._llmpaint_server.DEFAULT_PORT)
        probe = self._paint_probe
        pids = [
            item.pid
            for item in (probe.occupants if probe is not None else ())
            if _owner._llmpaint_server.is_paint_process_name(item.name)
        ]
        if not pids:
            self._error(
                "停止画图服务失败",
                f"端口 {port} 上的服务没有登记，也没找到可结束的 sd-server；用 fcmd portcheck c {port} 查看占用进程。",
            )
            return
        answer = _owner.QMessageBox.question(
            self,
            "停止画图服务",
            f"端口 {port} 上的画图服务未登记（pid {', '.join(map(str, pids))}），仍要结束这些 sd-server 吗？",
            _owner.QMessageBox.StandardButton.Yes | _owner.QMessageBox.StandardButton.No,
            _owner.QMessageBox.StandardButton.No,
        )
        if answer != _owner.QMessageBox.StandardButton.Yes:
            return
        self._paint_expected = False
        self._set_paint_busy(True)
        self.status("正在停止未登记的画图服务…")
        _owner.submit_daemon(
            _owner._llmpaint_server.stop_orphans,
            port,
            on_done=self._paint_orphans_stopped,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_orphans_stopped(self, pids: tuple[int, ...]) -> None:
        self._paint_expected = False
        if pids:
            self.status(f"已停止 {len(pids)} 个未登记的画图服务（pid {', '.join(map(str, pids))}）")
            self.state.log(f"已停止未登记的画图服务：{pids}")
        else:
            self.status("端口上没有可停止的 sd-server")
        self.refresh_paint()

    def _start_paint_service(self) -> None:
        """按当前画图配置启动 / 复用 sd-server。"""
        settings = self.paint_settings()
        try:
            _owner._llmpaint.validate_components(_owner.components_from_settings(settings))
        except _owner.ServeError as exc:
            self._error("启动画图服务失败", str(exc))
            return
        self._set_paint_busy(True)
        self.status("正在启动画图服务…")
        _owner.submit_daemon(
            _owner.start_paint_service,
            settings,
            on_done=self._paint_started,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_started(self, started: bool) -> None:
        self._paint_expected = True
        self._paint_watch_failures = 0
        self.status("画图服务已就绪" if started else "画图服务已在按当前配置运行")
        self.state.log("已启动画图服务" if started else "画图服务已在运行")
        self.refresh_paint()

    def _stop_paint_service(self) -> None:
        self._paint_expected = False
        self._set_paint_busy(True)
        self.status("正在停止画图服务…")
        _owner.submit_daemon(
            _owner._llmpaint_server.stop_paint,
            on_done=self._paint_stopped,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_stopped(self, result: _llmpaint_server.PaintStopResult) -> None:
        self._paint_expected = False
        self.status("画图服务已停止" if result.stopped else "画图服务未运行")
        if result.stopped:
            self.state.log("已停止画图服务")
        self.refresh_paint()

    def _restart_paint_service(self) -> None:
        """按当前参数停止并重新启动画图服务。"""
        settings = self.paint_settings()
        try:
            _owner._llmpaint.validate_components(_owner.components_from_settings(settings))
        except _owner.ServeError as exc:
            self._error("重启画图服务失败", str(exc))
            return
        self._set_paint_busy(True)
        self.status("正在重启画图服务…")
        _owner.submit_daemon(
            self._restart_paint_work,
            settings,
            on_done=lambda _started: self._paint_started(True),
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    @staticmethod
    def _restart_paint_work(settings: dict[str, Any]) -> bool:
        _owner._llmpaint_server.stop_paint()
        return _owner.start_paint_service(settings)

    def _paint_failed(self, message: str) -> None:
        self._paint_expected = False
        self.state.log(f"画图服务操作失败：{message}")
        self._error("画图服务", message)
        self.refresh_paint()

    def _paint_finished(self) -> None:
        self._set_paint_busy(False)

    def _watch_paint(self) -> None:
        """服务意外退出时自动拉起一次；连续失败后停止，避免无限重启。"""
        if self._paint_watch_restarting or self.state.closing or not self._paint_expected:
            return
        state, alive = _owner._llmpaint_server.status()
        if alive or state is None:
            return
        now = _owner.time.monotonic()
        if now < self._paint_watch_retry_at:
            return
        self._paint_watch_failures += 1
        if self._paint_watch_failures > 3:
            self._paint_expected = False
            self.state.log("画图服务连续自动重启失败，已停止看护；请在运行页手动启动")
            self.status("画图服务多次异常退出，已停止自动重启")
            return
        self._paint_watch_restarting = True
        self.state.log(f"画图服务意外退出（pid {state.pid}），正在自动重启…")
        self.status("画图服务意外退出，正在自动重启…")
        _owner.submit_daemon(
            _owner.start_paint_service,
            self.paint_settings(),
            on_done=self._paint_watch_done,
            on_error=self._paint_watch_error,
            on_finished=self._paint_watch_finished,
        )

    def _paint_watch_done(self, started: bool) -> None:
        self._paint_watch_failures = 0
        self.state.log("画图服务已自动恢复" if started else "画图服务已在运行")
        self.refresh_paint()

    def _paint_watch_error(self, message: str) -> None:
        self._paint_watch_retry_at = _owner.time.monotonic() + 120.0
        self.state.log(f"画图服务自动重启失败：{message}")
        self.status("画图服务自动重启失败，稍后重试")
        self.refresh_paint()

    def _paint_watch_finished(self) -> None:
        self._paint_watch_restarting = False

    def _set_paint_busy(self, busy: bool) -> None:
        self._paint_busy = busy
        self._refresh_paint_actions()

    def _wire(self) -> None:
        self.kv_choice.changed.connect(lambda _key: self._mark_manual("kv"))
        self.mode_choice.changed.connect(lambda _key: self._on_mode_changed())
        self.mode_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.kv_choice.changed.connect(lambda _key: self._refresh_command_preview())
        self.ubatch_edit.textChanged.connect(lambda _text: self._mark_manual("ubatch"))
        self.parallel_edit.textChanged.connect(lambda _text: self._mark_manual("parallel"))
        self.threads_http_edit.textChanged.connect(lambda _text: self._mark_manual("threads_http"))
        for widget in (
            self.host_edit,
            self.port_edit,
            self.threads_edit,
            self.ctx_edit,
            self.ubatch_edit,
            self.alias_edit,
            self.parallel_edit,
            self.threads_http_edit,
            self.api_key_edit,
            self.wait_edit,
        ):
            signal = getattr(widget, "textChanged", None) or getattr(widget, "currentTextChanged", None)
            if signal is not None:
                signal.connect(lambda *_args: self._refresh_command_preview())
        self.extra_args_edit.textChanged.connect(self._refresh_command_preview)
        self.model_combo.currentIndexChanged.connect(self._refresh_command_preview)
        self.state.modelsChanged.connect(self._on_models_changed_signal)
        self.state.hardwareChanged.connect(lambda *_args: self._refresh_compat_hints())
        self.state.servicesChanged.connect(self._on_services_changed)
        self.state.selectionChanged.connect(self._on_selection_changed)
        self.state.busyChanged.connect(self._on_busy_changed)
