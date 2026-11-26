"""llmw 运行页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_run as _owner
from fcmd.gui.widgets import messages

from .run_compat import (
    Any,
    Path,
    ServeState,
    StopResult,
)


class RunActionsMixin:
    """ADR 0046 P4 迁移的运行页方法。"""

    def start(self) -> None:
        """校验参数与资源后，后台启动服务。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        try:
            plan = self.build_plan()
            wait_s = _owner.parse_int(self.wait_edit.text(), "等待就绪秒数")
            if wait_s <= 0:
                raise _owner.ServeError(f"等待就绪秒数必须为正整数: {wait_s}")
        except _owner.ServeError as exc:
            self._error("参数错误", str(exc))
            return
        from fcmd.apps.llm._llmw_qt_state import gpu_usage

        if self._vllm_mode():
            note = "vLLM：显存由 --gpu-memory-utilization 控制，跳过按文件大小的显存预估"
        else:
            try:
                note = _owner.memory_guard(plan, gpu_usage())
            except _owner.ServeError as exc:
                self.state.log("已取消启动：" + str(exc))
                self._error("资源不足", str(exc))
                return
        self.state.log(note)
        self._active_param_key = _owner.llmenv.path_key(plan.model)
        self._persist_current_params()
        self.state.settings.last_model = str(plan.model)
        if not self._vllm_mode():
            self.state.remember_quant_choice(plan.model)
        self.state.persist()
        self.state.log(f"正在启动 {plan.name}，等待 /health（最多 {wait_s}s）…")
        self.status(f"正在启动 {plan.name} · {plan.profile.name} · ctx {plan.context}…")
        self.state.set_busy(True)
        _owner.submit(
            _owner.llmserve.start_server,
            plan,
            wait_s=float(wait_s),
            on_done=self._on_started,
            on_error=self._on_start_failed,
            on_finished=self._on_start_finished,
        )

    def start_paint_model(self, path: Path) -> None:
        """模型页扩散模型快捷启动：切到「画图」页签、选中主模型并启动 sd-server。"""
        self.tabs.setCurrentIndex(_owner._PAINT_TAB)
        self.paint_engine_section.select_model(path)
        self._start_paint_service()

    def start_model(self, path: Path) -> None:
        """选中指定模型并立即启动（模型页快捷启动入口）。"""
        self.state.select_model(path, update_name=True)
        current = self.state.state_for_model(path)
        if current is not None and _owner.llmserve.process_alive(current.pid, current.create_time):
            self.status(f"{current.name} · 运行中 · {current.base_url}")
            self.state.log(f"{path.name} 已在运行，无需重复启动")
            return
        self.start()

    def _on_started(self, state: ServeState) -> None:
        self.state.log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  日志: {state.log}")
        self.status(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self.state.refresh_services()

    def _on_start_failed(self, message: str) -> None:
        self.state.log(f"启动失败: {message}")
        self._error("启动失败", message)
        self.state.refresh_services()

    def _on_start_finished(self) -> None:
        """启动任务收尾：解除 busy 后再刷新模型列表（刷新会跳过 busy 状态）。"""
        self.state.set_busy(False)
        self.state.refresh_models(self._port_value())

    def _port_value(self) -> int | None:
        try:
            return _owner.parse_int(self.port_edit.text(), "端口")
        except _owner.ServeError:
            return None

    def stop_all(self) -> None:
        """停止所有已登记的服务；有多台时先确认。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        try:
            names = tuple(state.name for state in _owner.llmenv.list_states())
        except _owner.ServeError as exc:
            self._error("停止失败", str(exc))
            return
        if not names:
            self.status("无已登记服务")
            self.state.log("没有已登记的服务，无需停止")
            self.state.refresh_services()
            return
        answer = _owner.QMessageBox.question(
            self,
            "停止全部服务",
            f"将停止 {len(names)} 个已登记的服务：\n{'、'.join(names)}\n\n确定继续？",
        )
        if answer != _owner.QMessageBox.StandardButton.Yes:
            return
        self.state.log(f"正在停止 {len(names)} 个服务…")
        self.state.set_busy(True)
        _owner.submit(
            self._stop_worker,
            names,
            on_done=self._on_stopped,
            on_error=lambda message: self._error("停止失败", message),
            on_finished=lambda: self.state.set_busy(False),
        )

    def stop_selected(self) -> None:
        """停止当前服务名对应的服务，不影响其他已登记服务。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        current = self._selected_service_state()
        if current is None:
            self.status("当前模型没有已登记服务")
            self.state.log("当前模型没有已登记服务，无需停止")
            return
        name = current.name
        state = current
        alive = _owner.llmserve.process_alive(state.pid, state.create_time)
        action = "停止服务" if alive else "服务已退出，清理残留状态"
        answer = _owner.QMessageBox.question(self, "停止选中服务", f"{action}：{name}？\n其他服务不受影响。")
        if answer != _owner.QMessageBox.StandardButton.Yes:
            return
        self.state.set_busy(True)
        _owner.submit(
            self._stop_worker,
            (name,),
            on_done=self._on_stopped,
            on_error=lambda message: self._error("停止失败", message),
            on_finished=lambda: self.state.set_busy(False),
        )

    def restart_selected(self) -> None:
        """停止当前服务后按当前参数重新启动（按钮仅在服务运行时可用）。"""
        if self.state.busy:
            self.state.log("已有任务正在进行，请等待完成")
            return
        current = self._selected_service_state()
        if current is None or not _owner.llmserve.process_alive(current.pid, current.create_time):
            self.status("当前模型没有运行中的服务")
            self.state.log("当前模型没有运行中的服务，无法重启")
            self._refresh_action_states()
            return
        self.state.log(f"正在重启服务 {current.name}…")
        self.status(f"正在重启 {current.name}…")
        self.state.set_busy(True)
        _owner.submit(
            self._stop_worker,
            (current.name,),
            on_done=self._on_restart_stopped,
            on_error=self._on_restart_failed,
            # busy 保持到 start() 接管，避免停止与启动之间按钮可点。
        )

    def _on_restart_stopped(self, payload: tuple[list[StopResult], list[str]]) -> None:
        """重启第一步结束：旧服务已停就按当前表单参数重新启动。"""
        results, failures = payload
        for result in results:
            if result.was_running:
                self.state.log(f"已停止服务 {result.state.name}")
        self.state.refresh_services()
        if failures:
            self.state.set_busy(False)
            self.status("重启失败")
            self._error("重启失败", "\n".join(failures))
            self._refresh_action_states()
            return
        self.state.log("旧服务已停止，正在按当前参数启动…")
        self.state.set_busy(False)  # start() 内部会再次置 busy
        self.start()

    def _on_restart_failed(self, message: str) -> None:
        """重启第一步失败（停止异常）：解除 busy 并报告原因。"""
        self.state.set_busy(False)
        self.status("重启失败")
        self._error("重启失败", message)
        self._refresh_action_states()

    @staticmethod
    def _stop_worker(names: tuple[str, ...]) -> tuple[list[StopResult], list[str]]:
        """后台线程：依次停止服务，收集成功结果与失败原因。"""
        results: list[StopResult] = []
        failures: list[str] = []
        for name in names:
            try:
                results.append(_owner.llmserve.stop_server(name))
            except _owner.ServeError as exc:
                failures.append(f"{name}: {exc}")
        return results, failures

    def _on_stopped(self, payload: tuple[list[StopResult], list[str]]) -> None:
        results, failures = payload
        stopped = [result.state.name for result in results if result.was_running]
        cleaned = [result.state.name for result in results if not result.was_running]
        for name in stopped:
            self.state.log(f"已停止服务 {name}")
        if cleaned:
            self.state.log(f"服务未在运行，已清理残留状态: {', '.join(cleaned)}")
        for failure in failures:
            self.state.log(f"停止失败: {failure}")
        if failures:
            self.status(f"停止完成 · {len(stopped)} 个已停止 · {len(failures)} 个失败")
            self._error("停止失败", "\n".join(failures))
        elif stopped:
            self.status(f"已停止 {len(stopped)} 个服务")
        else:
            self.status("无运行中的服务（已清理残留状态）")
        self.state.refresh_services()
        self._refresh_action_states()
        self.state.refresh_models(self._port_value())

    def refresh_status(self) -> None:
        """读取状态文件并刷新状态 / 日志（不改变正在进行的任务）。"""
        self.state.refresh_services()
        name = self._service_name()
        state = self._load_state_safe(name)
        if state is None:
            self.status(f"{name} · 未启动")
            self.state.log(f"服务 {name} 未启动（尚无状态文件，持续显示系统负载）")
            return
        alive = _owner.llmserve.process_alive(state.pid, state.create_time)
        self.status(f"{state.name} · {'运行中' if alive else '已退出'} · {state.base_url} · pid {state.pid}")
        self.state.log(f"服务 {state.name}: {'运行中' if alive else '已退出'}")
        self.state.log(f"  地址: {state.base_url}")
        self.state.log(f"  模型: {state.model}")
        self.state.log(f"  档位: {state.profile} / ctx {state.context}")
        self.state.log(f"  启动时间: {state.started_at}")
        self.state.log(f"  日志: {state.log}")

    def open_browser(self) -> None:
        """在默认浏览器中打开服务地址（优先运行中服务的实际地址）。"""
        import webbrowser

        name = self._service_name()
        state = self._load_state_safe(name) if name else None
        engine = state.engine if state is not None else "（未登记）"
        if state is not None and _owner.llmserve.process_alive(state.pid, state.create_time):
            url = state.base_url
            api_key = state.api_key
        else:
            try:
                url = _owner.http_base_url(
                    self.host_edit.text().strip() or _owner.DEFAULT_HOST,
                    _owner.parse_int(self.port_edit.text(), "端口"),
                )
            except _owner.ServeError as exc:
                self._error("参数错误", str(exc))
                return
            api_key = self.api_key_edit.text().strip() or _owner.DEFAULT_API_KEY
        status = _owner._probe_web_ui(url, api_key)
        if status == 404:
            message = (
                f"当前服务的引擎未内置 Web UI（GET / 返回 404）: {engine}\n"
                "LM Studio 版 llama.cpp 会移除 Web UI；请切换到带 Web UI 的官方构建后重启服务：\n"
                "  fcmd llmengine use <tag>（如 b12212）\n"
                f"也可直接用 OpenAI 兼容客户端访问 {url}/v1"
            )
            self.state.log(message)
            self._error("无 Web UI", message)
            return
        if status is None:
            message = f"服务未就绪，无法打开 Web UI: {url}"
            self.state.log(message)
            self._error("打开失败", message)
            return
        if status != 200:
            message = f"服务返回 HTTP {status}，Web UI 不可用: {url}"
            self.state.log(message)
            self._error("打开失败", message)
            return
        if webbrowser.open(url):
            self.state.log(f"已在浏览器中打开: {url}")
        else:
            self.state.log(f"无法打开浏览器，请手动访问: {url}")
            self._error("打开失败", f"无法打开浏览器，请手动访问: {url}")

    def _on_models_changed_signal(self, _outcome: object) -> None:
        """``modelsChanged`` 槽：忽略扫描结果，直接刷新列表（结果已在共享状态里）。"""
        self.on_models_changed()

    def on_models_changed(self) -> None:
        """扫描完成后刷新模型下拉与服务名；未选择时自动带入一个可启动的模型。"""
        if self._vllm_mode():
            self._refresh_hf_models()
        self._populate_model_combo()
        candidates = self._usage_models()
        if self.state.selected_model is None and candidates:
            last = self.state.settings.last_model
            last_path = _owner.Path(last) if last else None
            eligible = {entry.path for entry in candidates}
            chosen = last_path if last_path is not None and last_path in eligible else self._resolved_model()
            if chosen is not None:
                self.state.log(f"未选择模型，自动使用: {chosen}")
                self.state.select_model(chosen, update_name=True)
                return
        if self.state.selected_model is not None:
            self._select_model_combo(self.state.selected_model)
        self._refresh_action_states()

    def _on_services_changed(self, _states: list[ServeState]) -> None:
        """运行中服务变化：刷新下拉框状态、底部按钮与页签数量徽标。"""
        self._populate_model_combo()
        self._refresh_action_states()
        self.sync_running_badges()

    def paint_alive(self) -> bool:
        """画图（sd.cpp）服务是否在运行（状态文件或端口探针任一确认）。"""
        return bool(self._paint_probe is not None and self._paint_probe.alive)

    def running_counts(self) -> dict[str, int]:
        """按用途统计运行中的模型：文本 / 图像。"""
        models = [state.model for state in self.state.alive_states]
        return _owner._llmw_models.running_usage_counts(models, paint_alive=self.paint_alive())

    def sync_running_badges(self) -> None:
        """把运行数量写到页签徽标，并在数量变化时通知窗口刷新侧边栏徽标。"""
        counts = self.running_counts()
        bar = self.tabs.tabBar()
        for index, usage in _owner._USAGE_BY_TAB.items():
            bar.set_badge(index, counts.get(usage, 0))
        bar.set_badge(_owner._PAINT_TAB, counts.get("paint", 0))
        if counts != self._running_counts:
            self._running_counts = counts
            self.runningChanged.emit(dict(counts))

    def _on_busy_changed(self, busy: bool) -> None:
        self.save_params_button.setEnabled(not busy)
        self.fill_params_button.setEnabled(not busy and not self._vllm_mode())
        self._refresh_action_states()
        self._refresh_paint_actions()

    def apply_recommendation(self, recommendation: Any) -> None:
        """把推荐行的模型与启动配置带入运行页。"""
        self.state.select_model(recommendation.model.path, update_name=True)
        self.mode_choice.set_key(_owner._mode_for_profile(recommendation.profile))
        self.ctx_edit.setText(str(recommendation.context))
        self._on_mode_changed()
        self.kv_choice.set_key(recommendation.kv_type)
        self.ubatch_edit.setText(str(recommendation.ubatch))
        self.parallel_edit.setText(str(recommendation.parallel))
        self.state.navigate("run")
        self.status(f"已带入推荐配置：{recommendation.model.path.name} · {recommendation.config_text}")

    def _error(self, title: str, message: str) -> None:
        messages.error(self, title, message)

    def update_service_and_fit(self) -> None:
        """外部（启动 / 停止 / 显存采样）变化后刷新按钮状态。"""
        self._refresh_action_states()
