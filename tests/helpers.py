"""测试共用辅助：DSL 动作输出捕获、框架输出行过滤与 Qt 控件回收。"""

from __future__ import annotations

import re
from typing import Any, cast

import pytest

from fcmd.core.toolkit import run_tool

# 框架进度行（与 fcmd.console 的步骤行约定一致）：
#   $ git add .       命令回显
#   完成  暂存更改  0.02s
#   失败  提交更改  0.02s
#   跳过  上传（上游失败）
#   共 3 步，用时 0.03s
# 注意：``错误:`` / ``提示:`` 属于用户可见的诊断信息，不属于「框架噪音」，
# 不在这里过滤，否则会掩盖真正的失败原因。
_FRAMEWORK_LINE_RE = re.compile(
    r"^(\$ |完成  |失败  |跳过  |共 \d+ 步|  运行  |  目录  |  返回码 |  没有需要提交的更改$)"
)


def strip_framework_lines(text: str) -> str:
    """去掉框架自己的进度行，只留工具的业务输出与诊断信息。"""
    return "\n".join(line for line in text.splitlines() if not _FRAMEWORK_LINE_RE.match(line))


def run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """运行工具并返回 (退出码, 过滤掉框架输出行后的业务输出)。"""
    code = run_tool(name, argv)
    return code, strip_framework_lines(capsys.readouterr().out)


def qt_top_levels() -> set[Any]:
    """记录当前进程的顶层 Qt 控件（构造窗口前调用，配合 :func:`destroy_qt_top_levels`）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return set(app.topLevelWidgets()) if app is not None else set()


def destroy_qt_top_levels(before: set[Any]) -> None:
    """销毁本轮新建的顶层 Qt 控件。

    Qt 里 ``close()`` 只隐藏窗口、不销毁对象；而按需构建的页面 / 菜单 / 对话框
    常常没有父控件，只 close 会让它们一直留在 ``QApplication`` 的顶层控件表里。
    累积到上万个控件后，下一次 ``apply_theme`` 的 ``setStyleSheet`` 要为整棵旧树
    重新 polish（实测单次数秒），表现为测试尾部长时间没动静。

    这里只删掉 ``before`` 之后新建的顶层控件，不动会话级 / 其它 fixture 的控件。
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget not in before:
            widget.deleteLater()
    # deleteLater 只在事件循环里生效；测试里没有事件循环，显式派发 DeferredDelete。
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def hermetic_vllm_platform() -> Any:
    """把 vLLM 平台探测隔离：不探测宿主机，也不启动任何 WSL 实例。返回恢复函数。

    ``_llmvllm.detect_platform()`` 会先 ``wsl -l -v`` 再对每个发行版执行
    ``wsl -d <名称> -- sh -lc ...``——**探测等于启动**，所以 llmw 窗口（``RunPage``
    构造即调用）与相关测试都会顺手把 Ubuntu / kali-linux 拉起来。这里：

    - 显式注入 ``runner`` 的调用（纯逻辑测试）照常走真实实现；
    - 其余默认调用返回「不支持」，保证离屏冒烟 / 页面构造不触外部环境；
    - 同时清空平台缓存，避免上一条测试的真实探测结果泄漏到下一条。

    刻意不接 ``monkeypatch``：autouse fixture 若在更早的位置接管 ``monkeypatch``，
    会把它的撤销推迟到其它 autouse fixture 的收尾之后，破坏那些 fixture 的前置 /
    收尾假设（``tests/infras/test_dev.py`` 的 ``cache_clear`` 曾因此报错）。
    """
    from fcmd.apps.llm import _llmvllm

    real_detect = _llmvllm.detect_platform

    def detect(runner: Any = None, **kwargs: Any) -> Any:
        if runner is not None:
            return real_detect(runner, **kwargs)
        return _llmvllm.VllmPlatform("none", False, "测试环境不探测 vLLM 平台（避免启动 WSL 实例）")

    _llmvllm.detect_platform = detect
    _llmvllm.reset_platform_cache()

    def restore() -> None:
        _llmvllm.detect_platform = real_detect
        _llmvllm.reset_platform_cache()

    return restore


_WSL_START_FLAGS: tuple[str, ...] = (
    "-d",
    "--distribution",
    "--exec",
    "--terminate",
    "--shutdown",
    "--unregister",
    "--import",
    "--import-in-place",
    "--export",
    "--install",
    "--set-default",
    "--set-default-version",
    "--set-version",
    "--mount",
    "--update",
)
"""会让 WSL 启动 / 改动实例的参数：测试里出现即视为「动了真实环境」。"""


def forbid_wsl_distro_start() -> Any:
    """守卫：测试期间不允许真正启动 / 改动 WSL 实例。返回恢复函数。

    只读命令（``--version`` / ``--help`` / ``-l -v`` / ``--status``）照常执行，
    它们不会把发行版拉起来；带 :data:`_WSL_START_FLAGS` 的调用直接失败，避免
    以后有人（或某次重构）在测试路径里重新引入 ``wsl -d <实例>``。
    """
    import subprocess

    real_run = subprocess.run
    real_popen = subprocess.Popen

    def is_start(argv: Any) -> bool:
        try:
            parts = [str(part) for part in argv]
        except TypeError:
            return False
        if not parts:
            return False
        name = parts[0].replace("/", "\\").rsplit("\\", 1)[-1].casefold()
        if name not in {"wsl", "wsl.exe"}:
            return False
        return any(part in _WSL_START_FLAGS for part in parts[1:])

    def guarded_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if is_start(argv):
            raise AssertionError(f"测试不应启动 / 改动 WSL 实例：{argv!r}；请注入假执行器")
        return real_run(argv, *args, **kwargs)

    def guarded_popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if is_start(argv):
            raise AssertionError(f"测试不应启动 / 改动 WSL 实例：{argv!r}；请注入假启动器")
        return real_popen(argv, *args, **kwargs)

    subprocess.run = cast(Any, guarded_run)  # pyrefly: 测试替身与真实签名不同
    subprocess.Popen = cast(Any, guarded_popen)

    def restore() -> None:
        subprocess.run = real_run
        subprocess.Popen = real_popen

    return restore
