"""LLM（llmw）- 本地 LLM 服务管理窗口（Qt / PySide6）。

侧边栏分页组织为概览、模型、运行、对话、设置五页：提供启动参数
配置、模型扫描与显存适配、服务启停、概览页实时 ``/metrics`` 与系统负载采样、
智能推荐；画图并入对话页（勾选「画图」后用提示词出图，画图配置在对话的
「模型与生成配置」窗口里），画图引擎（stable-diffusion.cpp 的 ``sd-server``）
的安装与状态在运行页；性能测试与服务日志在顶部「帮助」菜单打开；模型页内嵌
ModelScope 浏览 / 下载 / 已安装 / 转换 / 量化五个子页。开工与停止都在线程池执行，界面不阻塞；窗口位置与大小记忆到
``${FCMD_HOME:-~/.fcmd}/ui/llmw.json``。

本模块只保留工具注册与启动入口；窗口实现在 ``_llmw_qt_window.py``，纯逻辑
（设置、硬件快照、监控采样、性能测试、模型扫描）在对应的 ``_llmw_*`` 模块中，
并从这里 re-export 以保持既有访问路径。

示例
----
    fcmd llmw        # 打开本地 LLM 服务窗口
    llmw             # 全局命令等价入口
"""

from __future__ import annotations

from pathlib import Path

import fcmd
from fcmd.apps.llm._llmw_monitor import (  # noqa: F401  门面 re-export：保留旧访问路径
    RATE_WINDOW_SECONDS,
    GpuSample,
    LoadSample,
    RateWindow,
    _busy_advice,
    _fit_advice,
    _fit_state,
    _format_duration,
    _format_gpu,
    _format_load,
    _parse_gpu_rows,
    _parse_number,
    _probe_web_ui,
    _sample_gpu,
    _sample_load,
)
from fcmd.apps.llm._llmw_winsettings import (  # noqa: F401  门面 re-export：设置模型
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MIN_HEIGHT,
    MIN_WIDTH,
    LlmSettings,
    config_path,
    load_settings,
    resolve_geometry,
    save_settings,
    valid_geometry,
)
from fcmd.apps.llm.llmbase import (  # noqa: F401  门面 re-export：Qt 页面与测试共用
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    ModelEntry,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)

__all__ = [
    "LlmSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "LLM——本地 LLM 服务窗口版（Qt/PySide6，侧边栏分页：概览 / 模型 / 运行 / 对话 / 设置；"
    "模型页含 ModelScope 浏览、下载管理、已安装仓库、转换与量化向导；"
    "画图并入对话页（勾选「画图」按提示词出图，画图配置在对话的「模型与生成配置」里），"
    "画图引擎的安装 / 状态在运行页，由 stable-diffusion.cpp 的 sd-server 出图）："
    "列出已配置的可选模型并内置能力标签（代码/推理/对话/视觉等）"
    "与最佳实践提示，按整卡与当前可用显存分别标注“硬件不足”（标红加删除线）与“需释放显存”（标橙）并给出建议，"
    "配置档位/端口/上下文等参数，预览启动计划，后台启动与停止 llama.cpp 服务，"
    "查看状态与日志，并在概览页实时采样 /metrics 展示平滑后的 token/s 与请求队列状态，"
    "同时展示 CPU / 内存 / 交换 / 服务进程等系统负载以及 GPU 利用率、显存、温度、功耗等关键指标"
    "（系统负载在服务未启动时也持续采样展示）；「帮助 → 性能测试…」提供短对话 / 长输出 / 代码补全 / "
    "长上下文 8K / 长上下文 32K / 并发 4 路等典型场景，可调重复次数、超时与输出上限，"
    "后台批量实测延迟与 token 吞吐并按场景汇总聚合吞吐；窗口位置与大小记忆到 ~/.fcmd/ui/llmw.json"
)


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.llm._llmw_qt_window import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("llmw", help="打开本地 LLM 服务窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开本地 LLM 服务窗口。"""
    run_gui()


@fcmd.main("llmw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
