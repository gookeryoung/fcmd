"""计算型动作原语（DSL compute 数据流：返回值注入模板插值）。

与副作用型动作（返回 None，语义是「做事情」）不同，计算型动作的返回值
被 DSL 引擎消费：注入共享变量供本命令 cmd/cwd/env/message 模板插值，
空结果（空 list/空串）任务 SKIPPED。逐条目过程反馈（受保护包跳过提示）
保留 print（沿用批量操作消息约定）。
"""

from __future__ import annotations

from fcmd.dsl.actions import action

__all__: list[str] = []

# 受保护包名（pip 卸载/重装时跳过，避免破坏运行环境）
_PROTECTED_PACKAGES: frozenset[str] = frozenset({"fcmd"})


def _pip_installed_packages() -> list[str]:
    """获取当前环境已安装包名列表（``pip list --format=freeze`` 输出解析）。"""
    import subprocess  # 延迟导入：仅 pip 计算动作执行时需要（冷启动导入税）

    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["pip", "list", "--format=freeze"], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"pip list 失败（退出码 {result.returncode}）: {result.stderr.strip()}")
    return [line.split("==")[0].strip() for line in result.stdout.splitlines() if "==" in line]


def _filter_protected(packages: list[str]) -> list[str]:
    """过滤受保护包（跳过提示走 print：逐条目运行时反馈）。"""
    protected_lower = {p.lower() for p in _PROTECTED_PACKAGES}
    skipped = [p for p in packages if p.lower() in protected_lower]
    if skipped:
        print(f"跳过受保护的包: {', '.join(skipped)}")
    return [p for p in packages if p.lower() not in protected_lower]


@action("pip_expand", param_help={"packages": "包名通配符模式列表（支持 * ? [ ]）"})
def _pip_expand(packages: list[str]) -> list[str]:
    """展开包名通配符并过滤受保护包（计算型动作：返回具体包名列表）。

    无通配符字符（``* ? [ ]``）的模式原样保留（不触发 ``pip list`` 采集）；
    有通配符的模式按已安装包名大小写不敏感匹配展开（全模式共享一次采集）。
    """
    import fnmatch  # 延迟导入：仅 pip 计算动作执行时需要（冷启动导入税）

    expanded: list[str] = []
    installed: list[str] | None = None
    for pattern in packages:
        if not any(ch in pattern for ch in "*?[]"):
            expanded.append(pattern)
            continue
        if installed is None:
            installed = _pip_installed_packages()
        expanded.extend(pkg for pkg in installed if fnmatch.fnmatchcase(pkg.lower(), pattern.lower()))
    return _filter_protected(expanded)


@action("pip_filter", param_help={"packages": "包名列表"})
def _pip_filter(packages: list[str]) -> list[str]:
    """过滤受保护包（计算型动作：返回安全包名列表；全受保护时返回空列表）。"""
    return _filter_protected(packages)
