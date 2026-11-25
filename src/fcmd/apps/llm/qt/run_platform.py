"""llmw 运行页：平台探测（后台）与引擎族选项（ADR 0057；布局遵循 ADR 0046 P4）。"""

from __future__ import annotations

import fcmd.apps.llm._llmw_qt_run as _owner

PLATFORM_PENDING = "正在检测 WSL / vLLM 环境…"
"""占位平台的原因文案：后台探测结束前一直显示。"""


def infer_engine_note(platform: _owner._llmvllm.VllmPlatform) -> str:
    """推理引擎方块下方的摘要：区分「未探测完」与「确实不支持」。"""
    if platform.supported:
        return "HF safetensors · WSL2 / Linux"
    if platform.reason == PLATFORM_PENDING:
        return "正在检测运行环境…"
    return "当前系统不可用"


def infer_engine_options(platform: _owner._llmvllm.VllmPlatform) -> list[tuple[str, str, str]]:
    """文本页的引擎族选项：llama.cpp（GGUF）与 vLLM（HF safetensors）。"""
    return [
        ("llama.cpp", "llama.cpp", "GGUF · 现有引擎，通用"),
        ("vllm", "vLLM", infer_engine_note(platform)),
    ]


class RunPlatformMixin:
    """平台探测放后台，探测结果回来后再校正引擎选项与模型列表。"""

    def _detect_platform_async(self) -> None:
        """首帧后后台探测 vLLM 平台：WSL 子进程不能占 GUI 线程。"""
        _owner.submit(
            _owner._llmvllm.detect_platform,
            on_done=self._apply_platform,
            on_error=self._platform_failed,
        )

    def _sync_engine_platform(self) -> None:
        """把平台可用性同步到引擎选项：禁用 vLLM 并写说明 / 工具提示。"""
        platform = self._vllm_platform
        self.engine_choice.set_option_enabled(
            "vllm",
            platform.supported,
            "" if platform.supported else f"vLLM 不可用：{platform.reason}",
        )
        self.engine_choice.set_option_note("vllm", infer_engine_note(platform))

    def _apply_platform(self, platform: _owner._llmvllm.VllmPlatform) -> None:
        """探测完成：更新引擎选项，并按持久化的引擎选择恢复 vLLM 模式。"""
        self._vllm_platform = platform
        self._sync_engine_platform()
        if self._engine_kind() == "vllm":
            # 占位平台期间被强制回退 llama.cpp：平台确认可用后恢复用户的选择。
            self.engine_choice.set_key("vllm", emit=False)
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
        else:
            self.engine_choice.set_key("llama.cpp", emit=False)
        self._refresh_command_preview()
        self._refresh_action_states()
        self.state.log(f"vLLM 平台探测完成：{platform.reason}")

    def _platform_failed(self, message: str) -> None:
        """探测异常：保持 llama.cpp 回退，只写日志与状态栏，不弹窗。"""
        self.engine_choice.set_option_enabled("vllm", False, f"vLLM 平台探测失败：{message}")
        self.engine_choice.set_option_note("vllm", "检测失败，已回退 llama.cpp")
        self.state.log(f"vLLM 平台探测失败：{message}")
        self.status("vLLM 平台检测失败，已回退 llama.cpp")
