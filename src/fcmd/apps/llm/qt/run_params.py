"""llmw 运行页方法（ADR 0046 P4）。"""

from __future__ import annotations

from collections.abc import Sequence

import fcmd.apps.llm._llmw_qt_run as _owner

from .run_compat import (
    Any,
    LaunchPlan,
    Path,
    QWidget,
    llmbase,
)

_FORM_OWNED_FLAGS = frozenset(
    {
        "--host",
        "--port",
        "--alias",
        "--api-key",
        "-c",
        "--ctx-size",
        "-np",
        "--parallel",
        "--threads-http",
        "-t",
        "--threads",
        "-ub",
        "--ubatch-size",
    }
)
"""运行页表单已有控件的开关；自定义参数里的同名项以表单值为准。"""


def strip_form_owned_args(
    custom: Sequence[str],
    generated: Sequence[str],
) -> tuple[str, ...]:
    """丢弃自定义参数里与表单重复的开关及其值，其余原样保留。

    「填入当前启动参数」会把整段启动参数写进自定义参数框。之后若修改监听地址、
    端口等字段，旧值仍排在命令行末尾，llama.cpp 采用后出现的值，表单设置就
    会被静默覆盖。这里只清理表单已有控件、且当前计划确实会生成的开关；自定义
    参数独有或表单留空未生成的开关（如 ``--no-mmap``）保持不动。
    """
    owned = {token.split("=", 1)[0] for token in generated if token.startswith("-")}
    owned &= _FORM_OWNED_FLAGS
    kept: list[str] = []
    index = 0
    while index < len(custom):
        token = custom[index]
        name = token.split("=", 1)[0] if token.startswith("-") else ""
        if name in owned:
            index += 1
            if "=" not in token and index < len(custom) and not custom[index].startswith("-"):
                index += 1
            continue
        kept.append(token)
        index += 1
    return tuple(kept)


class RunParamsMixin:
    """ADR 0046 P4 迁移的运行页方法。"""

    def _build_launch_form(self, parent: _owner.QWidget) -> _owner.QWidget:
        """构建 llama.cpp 启动参数表单（文本页使用）。"""
        form_root = _owner.QWidget(parent)
        form_layout = _owner.QVBoxLayout(form_root)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)
        form = _owner.FormGrid(form_root)
        self.host_edit = _owner.QLineEdit(_owner.DEFAULT_HOST, form_root)
        self.host_edit.setToolTip("127.0.0.1 仅本机可访问；填 0.0.0.0 允许局域网其他机器访问")
        self.port_edit = _owner.QLineEdit(str(_owner.DEFAULT_PORT), form_root)
        form.add_pair("监听地址", self.host_edit, "端口", self.port_edit)
        self.threads_edit = _owner.QLineEdit(str(_owner.DEFAULT_THREADS), form_root)
        form.add_row("线程数", self.threads_edit)
        form_layout.addWidget(form)

        form_layout.addWidget(_owner.QLabel("运行模式（档位 + 调优）", form_root))
        self.mode_choice = _owner.ChoiceGroup(
            _owner._mode_options(),
            current="balanced",
            columns=3,
            tones=_owner._MODE_TONE,
            parent=form_root,
        )
        form_layout.addWidget(self.mode_choice)

        self.advanced = _owner.Collapsible("高级参数（留空回落档位默认）", expanded=False, parent=form_root)
        adv_form = _owner.FormGrid(self.advanced.content)
        self.ctx_edit = _owner.QLineEdit("", self.advanced.content)
        adv_form.add_row("上下文", self.ctx_edit)
        self.kv_choice = _owner.ChoiceGroup(_owner._kv_options(), current="", columns=4, parent=self.advanced.content)
        adv_form.add_row("KV", self.kv_choice)
        self.ubatch_edit = _owner.QLineEdit("", self.advanced.content)
        self.alias_edit = _owner.QLineEdit("", self.advanced.content)
        adv_form.add_pair("ubatch", self.ubatch_edit, "模型别名（留空自动）", self.alias_edit)
        self.parallel_edit = _owner.QLineEdit("", self.advanced.content)
        self.parallel_edit.setPlaceholderText("默认 1")
        self.threads_http_edit = _owner.QLineEdit("", self.advanced.content)
        adv_form.add_pair("并发槽（-np）", self.parallel_edit, "HTTP 线程", self.threads_http_edit)
        self.api_key_edit = _owner.QLineEdit(_owner.DEFAULT_API_KEY, self.advanced.content)
        self.wait_edit = _owner.QLineEdit("180", self.advanced.content)
        adv_form.add_pair("API Key", self.api_key_edit, "等待就绪（秒）", self.wait_edit)
        self.advanced.body.addWidget(adv_form)
        # vLLM 专用参数：仅在引擎切到 vLLM 时显示；llama.cpp 下隐藏，避免误导。
        self.vllm_hint = _owner.hint_label(
            "vLLM 只接受 HF safetensors 模型目录（config.json + *.safetensors）；"
            "GGUF 请继续用 llama.cpp。服务经 WSL 启动时对 Windows 暴露同一端口。",
            wrap=True,
        )
        self.vllm_hint.setVisible(False)
        self.advanced.body.addWidget(self.vllm_hint)
        self.vllm_options = _owner.QWidget(self.advanced.content)
        vllm_form = _owner.FormGrid(self.vllm_options)
        self.gpu_util_edit = _owner.QLineEdit("0.90", self.vllm_options)
        self.gpu_util_edit.setPlaceholderText("0.90")
        self.max_seqs_edit = _owner.QLineEdit("", self.vllm_options)
        self.max_seqs_edit.setPlaceholderText("默认 256")
        vllm_form.add_pair("GPU 显存占比", self.gpu_util_edit, "最大并发序列", self.max_seqs_edit)
        self.vllm_options.setVisible(False)
        self.advanced.body.addWidget(self.vllm_options)
        form_layout.addWidget(self.advanced)

        form_layout.addWidget(_owner.QLabel("启动命令预览", form_root))
        self.command_preview = _owner.QPlainTextEdit(form_root)
        self.command_preview.setObjectName("Log")
        self.command_preview.setReadOnly(True)
        self.command_preview.setFixedHeight(64)
        form_layout.addWidget(self.command_preview)

        form_layout.addWidget(_owner.QLabel("自定义启动参数", form_root))
        self.extra_args_edit = _owner.QPlainTextEdit(form_root)
        self.extra_args_edit.setFixedHeight(56)
        self.extra_args_edit.setPlaceholderText("每行或空格分隔，例如：--no-mmap --flash-attn on")
        form_layout.addWidget(self.extra_args_edit)
        params_actions = _owner.QHBoxLayout()
        self.save_params_button = _owner.button("保存参数")
        self.save_params_button.clicked.connect(self._save_params)
        self.fill_params_button = _owner.button("填入当前参数")
        self.fill_params_button.clicked.connect(self._fill_params)
        params_actions.addWidget(self.save_params_button)
        params_actions.addWidget(self.fill_params_button)
        params_actions.addStretch(1)
        form_layout.addLayout(params_actions)
        return form_root

    def _install_param_form(self, form: QWidget) -> None:
        """替换「启动参数」卡的内容；旧表单移出但保留对象，便于切回时复用。"""
        while self.param_host.count():
            item = self.param_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.param_host.addWidget(form)

    def _param_form_for(self, usage: str) -> QWidget:  # noqa: ARG002 - 后端落地前各用途共用表单
        """按用途取参数表单。

        视频 / 音频目前没有本地推理后端（ADR 0027 / 0040），先复用 llama.cpp
        启动表单；后端落地时在这里换成各自的引擎表单即可，页面结构与按模型
        持久化的入口保持不变。
        """
        return self.launch_form

    def _mount_service_page(self, usage: str) -> None:
        """把启动参数页挂到当前用途的页签（目前只有文本页使用）。"""
        self._usage = usage
        for target in self._service_layouts.values():
            target.removeWidget(self.service_page)
        self._service_layouts[usage].addWidget(self.service_page, 1)
        self._install_param_form(self._param_form_for(usage))
        self._apply_engine_mode()

    def _on_tab_changed(self, index: int) -> None:
        """切换页签：文本换模型候选并同步启动参数；图像只刷新状态。"""
        if index == _owner._PAINT_TAB:
            # 进入图像页签就重扫一次扩散模型：下载 / 转换刚完成的模型不必等
            # 其它页面的信号（扩散权重不进 LLM 模型列表）。
            self.refresh_paint_assets()
            self.refresh_paint()
            self._report_paint_hint()
            return
        usage = _owner._USAGE_BY_TAB.get(index)
        if usage is None or usage == self._usage:
            return
        self._persist_current_params()
        self._mount_service_page(usage)
        self.on_models_changed()
        candidates = [entry.path for entry in self._usage_models()]
        current = self.state.selected_model
        if current is not None and candidates and current not in candidates:
            self.state.select_model(candidates[0], update_name=True)
        self.status(f"已切到「{_owner._USAGE_LABELS[usage]}」启动参数")

    def _usage_models(self) -> list[_owner.ModelEntry]:
        """当前用途的模型候选：文本页列出全部 LLM（含代码 / 推理）。

        vLLM 模式下改列 HF safetensors 目录；GGUF 与 safetensors 不混排，
        避免把不支持的格式交给对应引擎。
        """
        if self._vllm_mode():
            return [
                _owner.ModelEntry(root=item.root, path=item.path, size=item.size, tags=("vLLM",))
                for item in self._hf_models
            ]
        return list(self.state.models)

    def _engine_kind(self) -> str:
        """当前生效的引擎族；平台不支持时强制回退 llama.cpp。"""
        if not self._vllm_platform.supported:
            return "llama.cpp"
        try:
            value = _owner._llmconfig.get_option("infer_engine")
        except (OSError, ValueError):
            return "llama.cpp"
        return "vllm" if value == "vllm" else "llama.cpp"

    def _vllm_mode(self) -> bool:
        """文本页是否正用 vLLM（图像 / 其它用途仍走各自引擎）。"""
        return self._usage == "text" and self._engine_kind() == "vllm"

    def _refresh_hf_models(self) -> None:
        """重扫 HF safetensors 模型（vLLM 模式或引擎切换时调用）。"""
        self._hf_models = _owner._llmvllm.scan_hf_models() if self._vllm_platform.supported else []

    def _on_engine_changed(self, key: str) -> None:
        """切换推理引擎族：落盘选择、换模型候选并重建参数预览。"""
        if key == "vllm" and not self._vllm_platform.supported:
            self.engine_choice.set_key("llama.cpp")
            return
        self._persist_current_params()
        try:
            _owner._llmconfig.set_option("infer_engine", key)
        except (OSError, ValueError) as exc:
            self.state.log(f"保存推理引擎选择失败: {exc}")
        self._apply_engine_mode()
        self._refresh_hf_models()
        self._populate_model_combo()
        candidates = self._usage_models()
        current = self.state.selected_model
        if current is None or not any(entry.path == current for entry in candidates):
            if candidates:
                self.state.select_model(candidates[0].path, update_name=True)
            else:
                self.state.selected_model = None
                self.state.selectionChanged.emit(None, False)
        self._refresh_command_preview()
        self._refresh_action_states()
        self._populate_model_combo()
        self.state.log(f"推理引擎已切到 {key}")

    def _apply_engine_mode(self) -> None:
        """按引擎族切换参数表单的可用性：vLLM 隐藏 llama.cpp 专用项。"""
        vllm = self._engine_kind() == "vllm"
        for widget in getattr(self, "vllm_options", None), getattr(self, "vllm_hint", None):
            if widget is not None:
                widget.setVisible(vllm)
        llama_only = (
            self.threads_edit,
            self.mode_choice,
            self.kv_choice,
            self.ubatch_edit,
            self.parallel_edit,
            self.threads_http_edit,
        )
        for widget in llama_only:
            widget.setEnabled(not vllm)
            widget.setToolTip("llama.cpp 专用参数；vLLM 由上方 vLLM 字段控制" if vllm else "")
        self.fill_params_button.setEnabled(not vllm)
        self.fill_params_button.setToolTip("vLLM 参数由字段直接生成，无需填入自定义参数" if vllm else "")

    def _resolved_model(self) -> Path | None:
        """表单实际使用的模型：显式选择，或按档位量化 / 大小兜底。"""
        if self._vllm_mode():
            selected = self.state.selected_model
            entries = self._usage_models()
            if selected is not None and any(entry.path == selected for entry in entries):
                return selected
            return entries[0].path if entries else None
        profile = _owner.PROFILES.get(self._mode_profile(), _owner.PROFILES[_owner.DEFAULT_PROFILE])
        return _owner.pick_model(self.state.selected_model, self.state.models, profile.quant)

    def _custom_args(self) -> tuple[str, ...]:
        return _owner.sanitize_custom_args(_owner.parse_custom_args(self.extra_args_edit.toPlainText()))

    def _workflow(self, *, extra_args: str | None = None) -> dict[str, Any]:
        return {
            "name": self._service_name(),
            "profile": self._mode_profile(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self._mode_tune(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "extra_args": _owner.shlex.join(self._custom_args()) if extra_args is None else extra_args,
            "model": self._resolved_model(),
            "gpu_mib": self.state.gpu_mib,
        }

    def _param_values(self) -> dict[str, str]:
        """可持久化的表单值（不含模型路径与硬件快照）。"""
        if self._vllm_mode():
            return self._vllm_param_values()
        return {
            "name": self._service_name(),
            "profile": self._mode_profile(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "threads": self.threads_edit.text(),
            "tune": self._mode_tune(),
            "ctx": self.ctx_edit.text(),
            "kv": self.kv_choice.key(),
            "ubatch": self.ubatch_edit.text(),
            "alias": self.alias_edit.text(),
            "parallel": self.parallel_edit.text(),
            "threads_http": self.threads_http_edit.text(),
            "api_key": self.api_key_edit.text(),
            "wait": self.wait_edit.text(),
            "extra_args": self.extra_args_edit.toPlainText(),
            "mode": self.mode_choice.key(),
        }

    def _param_key(self) -> str:
        path = self.state.selected_model or self._resolved_model()
        return _owner.llmenv.path_key(path) if path is not None else ""

    def _persist_current_params(self) -> None:
        key = self._active_param_key
        if not key:
            return
        saved = dict(self.state.settings.run_params or {})
        saved[key] = self._param_values()
        self.state.settings.run_params = saved

    def persist_params(self) -> None:
        """模型切换 / 关窗时把当前模型的启动参数落到配置。"""
        self._persist_current_params()
        self.state.persist()

    def _save_params(self) -> None:
        key = self._param_key()
        self._active_param_key = key
        self._persist_current_params()
        self.state.persist()
        self.status("启动参数已保存")
        self.state.log(f"已保存启动参数: {self.state.selected_model or '（未选择模型）'}")

    def _apply_saved_params(self, saved: dict[str, Any]) -> None:
        if self._vllm_mode():
            self._apply_saved_vllm_params(saved)
            return
        if saved.get("engine") == "vllm":
            self._apply_model_recommendation(self.state.selected_model)
            return
        mode = str(saved.get("mode") or _owner._mode_for_profile(str(saved.get("profile", "balanced"))))
        self.mode_choice.set_key(mode)
        self.host_edit.setText(str(saved.get("host", self.host_edit.text())))
        self.port_edit.setText(str(saved.get("port", self.port_edit.text())))
        self.threads_edit.setText(str(saved.get("threads", self.threads_edit.text())))
        self.ctx_edit.setText(str(saved.get("ctx", self.ctx_edit.text())))
        self.kv_choice.set_key(str(saved.get("kv", self.kv_choice.key())))
        self.ubatch_edit.setText(str(saved.get("ubatch", self.ubatch_edit.text())))
        self.alias_edit.setText(str(saved.get("alias", self.alias_edit.text())))
        self.parallel_edit.setText(str(saved.get("parallel", self.parallel_edit.text())))
        self.threads_http_edit.setText(str(saved.get("threads_http", self.threads_http_edit.text())))
        self.api_key_edit.setText(str(saved.get("api_key", self.api_key_edit.text())))
        self.wait_edit.setText(str(saved.get("wait", self.wait_edit.text())))
        self.extra_args_edit.setPlainText(str(saved.get("extra_args", "")))
        self._refresh_command_preview()

    def _vllm_param_values(self) -> dict[str, str]:
        """vLLM 模式的可持久化字段（与 GGUF 参数分开标记）。"""
        return {
            "engine": "vllm",
            "name": self._service_name(),
            "host": self.host_edit.text(),
            "port": self.port_edit.text(),
            "ctx": self.ctx_edit.text(),
            "alias": self.alias_edit.text(),
            "api_key": self.api_key_edit.text(),
            "wait": self.wait_edit.text(),
            "extra_args": self.extra_args_edit.toPlainText(),
            "gpu_util": self.gpu_util_edit.text(),
            "max_seqs": self.max_seqs_edit.text(),
        }

    def _apply_saved_vllm_params(self, saved: dict[str, Any]) -> None:
        """回填 vLLM 参数；旧记录（llama.cpp 参数）不复用，改走默认值。"""
        if saved.get("engine") != "vllm":
            self._apply_vllm_defaults(self.state.selected_model)
            return
        self.host_edit.setText(str(saved.get("host", self.host_edit.text())))
        self.port_edit.setText(str(saved.get("port", self.port_edit.text())))
        self.ctx_edit.setText(str(saved.get("ctx", "")))
        self.alias_edit.setText(str(saved.get("alias", "")))
        self.api_key_edit.setText(str(saved.get("api_key", self.api_key_edit.text())))
        self.wait_edit.setText(str(saved.get("wait", "600")))
        self.extra_args_edit.setPlainText(str(saved.get("extra_args", "")))
        self.gpu_util_edit.setText(str(saved.get("gpu_util", "0.90")))
        self.max_seqs_edit.setText(str(saved.get("max_seqs", "")))
        self._refresh_command_preview()

    def _apply_vllm_defaults(self, path: Path | None) -> None:
        """vLLM 模式首次选中模型：上下文取模型配置，等待时间放宽。"""
        self.alias_edit.setText("")
        self.ctx_edit.setText(str(_owner._hf_context(path)) if path is not None else "")
        self.wait_edit.setText("600")
        self.gpu_util_edit.setText("0.90")
        self.max_seqs_edit.setText("")
        self.extra_args_edit.setPlainText("")
        self._refresh_command_preview()

    def _fill_params(self) -> None:
        """把当前表单生成的启动参数填入自定义参数框，与命令预览保持一致。"""
        if self._vllm_mode():
            self.status("vLLM 参数由字段直接生成，无需填入")
            return
        try:
            base = _owner.build_plan_from(self._workflow(extra_args=""))
        except _owner.ServeError as exc:
            self._error("参数错误", str(exc))
            return
        tail = _owner.startup_args_tail(base.argv())
        self.extra_args_edit.setPlainText(_owner.shlex.join(tail))
        self._refresh_command_preview()
        self.status("已填入当前启动参数")

    def _refresh_command_preview(self) -> None:
        """重建命令预览：同一轮事件循环内的重复请求合并成一次。

        构造页面与逐字段编辑会连续触发很多次（每个 textChanged / 选项卡切换），
        每次都要扫模型目录拼启动计划；用页面自己的单次定时器延到下一个事件循环，
        只算最后一次。定时器以页面为父控件，页面销毁时随之释放。
        """
        timer = getattr(self, "_preview_timer", None)
        if timer is None:
            timer = _owner.QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(0)
            timer.timeout.connect(self._flush_command_preview)
            self._preview_timer = timer
        # 已经有待触发的重建就不再重启：刷新时现读控件，后到的修改不会丢，
        # 重启反而会把它一直推到下一轮（后台探测回调就可能连续触发）。
        if not timer.isActive():
            timer.start()

    def _flush_command_preview(self) -> None:
        """真正构造启动计划并写入预览框（控件值在 GUI 线程现读，保证是最新值）。"""
        try:
            plan = self.build_plan()
        except _owner.ServeError as exc:
            self.command_preview.setPlainText(f"参数未就绪：{exc}")
            return
        self.command_preview.setPlainText(_owner.format_command(plan.argv()))

    def build_plan(self) -> LaunchPlan:
        """按当前表单构造启动计划；重复填入的参数不二次追加。

        自定义参数为空时只构造一次（原先先算 base 再重建，白白多扫一遍模型）；
        只有填了自定义参数、需要判断「是否与自动生成的一致」时才走两段。
        """
        if self._vllm_mode():
            return self._build_vllm_plan()
        custom = self._custom_args()
        if not custom:
            return _owner.build_plan_from(self._workflow())
        base = _owner.build_plan_from(self._workflow(extra_args=""))
        tail = _owner.startup_args_tail(base.argv())
        if custom == tail:
            return base
        # 「填入当前启动参数」会把整段参数写进自定义框；之后改监听地址等字段，
        # 旧值排在新值之后会被 llama.cpp 采用，导致表单设置不生效。表单有控件的
        # 开关一律以表单为准，自定义参数只补表单没有的参数。
        merged = strip_form_owned_args(custom, tail)
        if not merged:
            return base
        return _owner.build_plan_from(self._workflow(extra_args=_owner.shlex.join(merged)))

    def _build_vllm_plan(self) -> LaunchPlan:
        """vLLM 启动计划：模型目录 + 服务字段 + vLLM 专用参数。"""
        model = self._resolved_model()
        if model is None:
            raise _owner.ServeError(
                "未找到 HF safetensors 模型：请把带 config.json + *.safetensors 的模型目录放入模型目录，"
                "或在设置页检查存储路径"
            )
        engine = _owner._llmvllm.resolve_engine()
        name = _owner.llmbase.validate_name(self._service_name())
        raw_ctx = self.ctx_edit.text().strip()
        context = _owner.parse_int(raw_ctx, "上下文") if raw_ctx else _owner._hf_context(model)
        return _owner._llmvllm.make_plan(
            name,
            model,
            engine,
            host=self.host_edit.text().strip() or _owner.DEFAULT_HOST,
            port=_owner.parse_int(self.port_edit.text(), "端口"),
            context=context,
            alias=self.alias_edit.text().strip() or model.name,
            api_key=self.api_key_edit.text().strip(),
            gpu_memory_utilization=_owner._parse_ratio(self.gpu_util_edit.text()),
            max_num_seqs=_owner.parse_optional_int(self.max_seqs_edit.text(), "最大并发序列"),
            extra_args=self._custom_args(),
        )

    def profile_context(self) -> tuple[str, int, str]:
        """当前表单下的档位名、上下文与 KV 类型（输入无效时回退档位默认值）。"""
        name = self._mode_profile() or _owner.DEFAULT_PROFILE
        profile = _owner.PROFILES.get(name, _owner.PROFILES[_owner.DEFAULT_PROFILE])
        try:
            context = _owner.parse_optional_int(self.ctx_edit.text(), "上下文") or profile.context
        except _owner.ServeError:
            context = profile.context
        kv_type = self.kv_choice.key() or profile.kv_type
        return name, context, kv_type if kv_type in _owner.KV_SCALE else profile.kv_type

    def tuning(self) -> llmbase.Tuning:
        """按当前档位 / 上下文 / GPU 解析调优策略。"""
        _, context, _ = self.profile_context()
        size = self._selected_size()
        tune = self._mode_tune()
        return _owner._llmtune.resolve(tune, context=context, model_bytes=size, gpu_mib=self.state.gpu_mib)

    def fit_estimate(self, size: int) -> tuple[_owner.llmmodel.VramFit | None, _owner.llmmodel.VramFit | None]:
        """返回（整卡能力, 当前可用）两套显存估算；未检测到 GPU 时均为 ``None``。"""
        if self.state.gpu_mib is None:
            return None, None
        _, context, kv_type = self.profile_context()
        tuning = self.tuning()
        kv_type = tuning.kv_type or kv_type
        total = _owner.llmmodel.fit_vram(size, self.state.gpu_mib * 1024 * 1024, context, kv_type)
        free = None
        if self.state.gpu_free_mib is not None:
            free = _owner.llmmodel.fit_vram(size, self.state.gpu_free_mib * 1024 * 1024, context, kv_type)
        return total, free

    def _selected_size(self) -> int:
        path = self.state.selected_model
        if path is None:
            return 0
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _mark_manual(self, key: str) -> None:
        if not self._syncing_tune:
            self._tune_auto[key] = False

    def _apply_tuning(self) -> None:
        """把调优策略值同步到高级参数，保留用户手填的非策略值。"""
        tuning = self.tuning()
        synced = _owner._llmtune.sync_advanced_fields(
            tuning,
            kv_value=self.kv_choice.key(),
            ubatch_value=self.ubatch_edit.text().strip(),
            parallel_value=self.parallel_edit.text().strip(),
            threads_http_value=self.threads_http_edit.text().strip(),
            kv_auto=self._tune_auto["kv"],
            ubatch_auto=self._tune_auto["ubatch"],
            parallel_auto=self._tune_auto["parallel"],
            threads_http_auto=self._tune_auto["threads_http"],
        )
        self._tune_auto = {
            "kv": synced.kv_auto,
            "ubatch": synced.ubatch_auto,
            "parallel": synced.parallel_auto,
            "threads_http": synced.threads_http_auto,
        }
        self._syncing_tune = True
        try:
            self.kv_choice.set_key(synced.kv_value)
            self.ubatch_edit.setText(synced.ubatch_value)
            self.parallel_edit.setText(synced.parallel_value)
            self.threads_http_edit.setText(synced.threads_http_value)
        finally:
            self._syncing_tune = False

    def effective_tuning_text(self, tuning: llmbase.Tuning) -> str:
        """提示区展示的生效高级参数（输入框 > 策略 > 档位）。"""
        _, _, profile_kv = self.profile_context()
        profile = _owner.PROFILES.get(self._mode_profile())
        kv_type = self.kv_choice.key() or tuning.kv_type or profile_kv
        ubatch = self.ubatch_edit.text().strip() or str(tuning.ubatch or (profile.ubatch if profile else ""))
        parallel = self.parallel_edit.text().strip() or str(tuning.parallel)
        threads_http = self.threads_http_edit.text().strip() or str(tuning.threads_http or "-")
        return (
            f"生效: b{tuning.batch} / ub {ubatch} / np {parallel} / th {threads_http} / "
            f"cr {tuning.cache_reuse} / kv {kv_type} / fa {tuning.flash_attn}"
        )

    def _mode_profile(self) -> str:
        return _owner._MODE_PROFILE.get(self.mode_choice.key(), _owner.DEFAULT_PROFILE)

    def _mode_tune(self) -> str:
        return _owner._MODE_TUNE.get(self.mode_choice.key(), "auto")

    def _on_mode_changed(self) -> None:
        """切换运行模式后同步高级参数。"""
        self._apply_tuning()
