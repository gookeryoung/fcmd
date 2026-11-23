"""llmw 运行页方法（ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_run as _owner
from fcmd.gui.widgets import HintLabel

from .run_compat import (
    Path,
    ServeState,
    _llmw_compat,
)


class RunModelsMixin:
    """ADR 0046 P4 迁移的运行页方法。"""

    def _service_name(self) -> str:
        """自动服务名：优先运行中服务，否则由当前模型文件名推导。"""
        path = self.state.selected_model or self._resolved_model()
        if path is not None:
            current = self.state.state_for_model(path)
            return current.name if current is not None else _owner.service_name_from_model(path)
        return _owner.default_service_name()

    def service_name(self) -> str:
        """当前应使用的服务名（供概览采样等外部逻辑读取）。"""
        return self._service_name()

    def _on_model_action(self) -> None:
        """顶部按钮：当前模型运行中则停止，否则启动。"""
        selected = self._selected_service_state()
        if selected is not None and _owner.llmserve.process_alive(selected.pid, selected.create_time):
            self.stop_selected()
        else:
            self.start()

    def _selected_service_state(self) -> ServeState | None:
        path = self.state.selected_model or self._resolved_model()
        return self.state.state_for_model(path) if path is not None else None

    def _refresh_action_states(self) -> None:
        """按运行状态切换三个底部按钮的可用性。"""
        busy = self.state.busy
        selected = self._selected_service_state()
        running = selected is not None and _owner.llmserve.process_alive(selected.pid, selected.create_time)
        has_model = self.state.selected_model is not None or bool(self._usage_models())
        self.model_action_button.set_text(
            "停止服务" if running else "启动服务",
            tone="danger" if running else "on_accent",
        )
        self.model_action_button.set_style_name("RunDanger" if running else "RunPrimary")
        self.model_action_button.setEnabled(not busy and has_model)
        # 重启只在服务已运行时可用；任务进行中禁用，避免停止 / 启动竞争。
        self.restart_button.setEnabled(not busy and running)

    @staticmethod
    def _load_state_safe(name: str) -> ServeState | None:
        if not name:
            return None
        try:
            return _owner.llmenv.load_state(name, missing_ok=True)
        except _owner.ServeError:
            return None

    def _on_model_combo_changed(self, index: int) -> None:
        path = self.model_combo.itemData(index)
        if path:
            self.state.select_model(_owner.Path(str(path)), update_name=True)
        else:
            self.state.selected_model = None
            self.state.selectionChanged.emit(None, False)
        self._refresh_combo_current_color()
        self._refresh_model_compat_hint()

    def _gpu_status(self) -> _owner._llmw_compat.GpuStatus:
        """当前硬件快照里的 GPU 摘要；未检测到时按未知处理，不额外起进程。"""
        return _owner._llmw_compat.gpu_status(self.state.hardware, self.state.gpu_mib)

    def _refresh_compat_hints(self) -> None:
        self._refresh_model_compat_hint()
        self._refresh_paint_compat_hint()

    def _refresh_model_compat_hint(self) -> None:
        """模型与推理引擎 / 硬件驱动的适配提示（选择或硬件变化时刷新）。"""
        hint = getattr(self, "model_compat_hint", None)
        if hint is None:
            return
        path = self.state.selected_model
        if self._vllm_mode():
            if path is None:
                hint.setText("vLLM · 未找到 HF safetensors 模型目录")
            else:
                hint.setText(f"vLLM · {path.name} · {self._vllm_platform.reason}")
            hint.set_tone("accent" if path is not None else "muted")
            return
        if path is None:
            hint.setText("")
            return
        target = _owner.llmenv.path_key(path)
        entry = next((item for item in self._usage_models() if _owner.llmenv.path_key(item.path) == target), None)
        if entry is None:
            hint.setText("")
            return
        compat = _owner._llmw_compat.model_compat(
            entry,
            _owner._llmw_compat.current_engine_variant(),
            gpu=self._gpu_status(),
        )
        self._set_compat_hint(hint, compat)

    def _refresh_paint_compat_hint(self) -> None:
        """扩散模型与 sd.cpp 引擎 / 硬件驱动的适配提示。"""
        hint = getattr(self, "paint_compat_hint", None)
        section = getattr(self, "paint_engine_section", None)
        if hint is None or section is None:
            return
        data = section.model_combo.currentData()
        if not isinstance(data, str) or not data.strip():
            hint.setText("")
            return
        compat = _owner._llmw_compat.image_compat(
            _owner.Path(data),
            _owner._llmw_compat.current_image_backend(),
            gpu=self._gpu_status(),
        )
        self._set_compat_hint(hint, compat)

    @staticmethod
    def _set_compat_hint(label: HintLabel, compat: _llmw_compat.Compat) -> None:
        label.setText(f"模型适配 · {compat.hint}")
        label.set_tone(compat.tone)

    def _service_status_text(self, path: Path) -> str:
        """只有正在运行的服务才返回状态；未运行不占用下拉项文本。"""
        return "✓ 已启动" if self.state.is_running(path) else ""

    def _paint_combo_status(self, index: int, path: Path) -> None:
        self.model_combo.setItemData(index, bool(self._service_status_text(path)), _owner._MODEL_RUNNING_ROLE)

    def _refresh_combo_current_color(self) -> None:
        path = self.state.selected_model
        running = path is not None and bool(self._service_status_text(path))
        self.model_combo.setProperty("running", "true" if running else "false")
        self.model_combo.style().unpolish(self.model_combo)
        self.model_combo.style().polish(self.model_combo)

    def _populate_model_combo(self) -> None:
        """把当前扫描到的 GGUF 与启动状态填入顶部下拉框，保留已选模型。"""
        current = self.state.selected_model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for entry in self._usage_models():
            self.model_combo.addItem(
                _owner.model_combo_text(entry, status=self._service_status_text(entry.path)), str(entry.path)
            )
            index = self.model_combo.count() - 1
            self.model_combo.setItemData(index, str(entry.path), _owner.Qt.ToolTipRole)
            self._paint_combo_status(index, entry.path)
        self.model_combo.blockSignals(False)
        if current is not None:
            self._select_model_combo(current)
        self._refresh_combo_current_color()
        self._refresh_model_compat_hint()

    def _select_model_combo(self, path: Path) -> None:
        target = str(path)
        index = self.model_combo.findData(target)
        if index < 0:
            self.model_combo.blockSignals(True)
            label = _owner.model_combo_text(
                _owner.ModelEntry(root=path.parent, path=path, size=self._size_of(path)),
                status=self._service_status_text(path),
            )
            self.model_combo.addItem(label, target)
            index = self.model_combo.count() - 1
            self.model_combo.setItemData(index, target, _owner.Qt.ToolTipRole)
            self._paint_combo_status(index, path)
            self.model_combo.blockSignals(False)
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._refresh_combo_current_color()
        self._refresh_model_compat_hint()

    def _on_selection_changed(self, path: object, update_name: bool) -> None:
        if path is None:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(-1)
            self.model_combo.blockSignals(False)
            self._refresh_action_states()
            self._refresh_model_compat_hint()
            return
        selected = _owner.Path(path)  # type: ignore[arg-type]
        self._select_model_combo(selected)
        del update_name
        key = _owner.llmenv.path_key(selected)
        if self._active_param_key and self._active_param_key != key:
            self._persist_current_params()
        self._active_param_key = key
        saved = (self.state.settings.run_params or {}).get(key)
        if isinstance(saved, dict):
            self._apply_saved_params(saved)
        elif self._vllm_mode():
            self._apply_vllm_defaults(selected)
        else:
            self._apply_model_recommendation(selected)
        self._refresh_action_states()

    def _apply_model_recommendation(self, path: Path) -> None:
        """选中模型后自动套用该模型在当前硬件上的推荐配置。"""
        limit = max(1, len(self.state.models))
        rows = _owner._llmw_recommend.recommend(
            self.state.models, self.state.hardware, self.state.measured, limit=limit
        )
        rec = next((row for row in rows if row.model.path == path), None)
        if rec is None:
            return
        self.mode_choice.set_key(_owner._mode_for_profile(rec.profile))
        self.ctx_edit.setText(str(rec.context))
        self._apply_tuning()
        self.kv_choice.set_key(rec.kv_type)
        self.ubatch_edit.setText(str(rec.ubatch))
        self.parallel_edit.setText(str(rec.parallel))
        self._refresh_command_preview()
        self.state.log(
            f"已套用推荐配置: {path.name} · {rec.profile} / ctx {rec.context} / kv {rec.kv_type} "
            f"/ ub {rec.ubatch} / np {rec.parallel}"
        )

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0
