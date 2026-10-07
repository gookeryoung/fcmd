"""内建动作注册表：DSL ``action`` 命令的进程内实现。

TOML 声明 ``action = "<名>"`` 的命令由本注册表分发执行：动作实现是普通
Python 函数，其签名即 CLI 参数 schema（synth 层拷贝签名注入合成函数，
零改动复用 ``fcmd.apis._tool_args._build_parser_for_tool`` 的参数推导）。
与 cmd 任务（subprocess）不同，动作在 fcmd 进程内直接执行——无进程边界，
异常由引擎任务失败机制统一捕获（任务失败汇总 + 退出码非零）。

新动作的注册方式::

    @action("setenv", param_help={"name": "环境变量名"})
    def _setenv(name: str, value: str, default: bool = False) -> None:
        ...

约束：实现函数的参数名不得命中全局选项保留名（dry_run/quiet/strategy，
由 :mod:`fcmd.dsl.decl` 声明期校验）。两类语义：

* **副作用型**（TOML ``action`` 键）：无返回值消费， fn 任务进程内执行；
* **计算型**（TOML ``compute`` 键）：返回值被引擎消费——注入共享变量供
  本命令模板插值（``{as 名}``），空结果任务 SKIPPED（输出管道形态）。

消息约定：单条完成/失败提示走 TOML ``message``/``fail_message``；批量
操作中逐条目的过程反馈（预览/重命名回显/缺失跳过提示）保留 ``print``——
内容依赖运行时逐条目结果，无法静态模板化，且是批量操作的必要反馈。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

__all__ = ["Action", "action", "action_names", "get_action", "has_action"]


@dataclass(frozen=True)
class Action:
    """内建动作描述符。

    参数
    ----
    name:
        动作名（TOML ``action = "<名>"`` 引用）
    func:
        进程内实现函数（签名即 CLI 参数 schema）
    param_help:
        参数帮助文本（``{参数名: 帮助}``，供 ``--help`` 展示）
    """

    name: str
    func: Callable[..., Any]
    param_help: Mapping[str, str] = field(default_factory=dict)


_ACTIONS: dict[str, Action] = {}


def action(
    name: str, param_help: Mapping[str, str] | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册内建动作的装饰器（模块导入期执行，重复注册为编程错误）。

    Parameters
    ----------
    name:
        动作名（TOML ``action = "<名>"`` 引用，须匹配工具名风格）
    param_help:
        参数帮助文本（``{参数名: 帮助}``）

    Returns
    -------
    Callable
        装饰器（原函数原样返回）
    """

    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        if name in _ACTIONS:
            raise ValueError(f"内建动作 {name!r} 重复注册")
        _ACTIONS[name] = Action(name=name, func=func, param_help=dict(param_help or {}))
        return func

    return deco


def has_action(name: str) -> bool:
    """查询动作是否已注册（声明期校验用）。"""
    return name in _ACTIONS


def action_names() -> tuple[str, ...]:
    """全部已注册动作名（排序，供声明期错误提示）。"""
    return tuple(sorted(_ACTIONS))


def get_action(name: str) -> Action:
    """取出动作描述符（调用方须经声明期校验，未知名抛 KeyError）。"""
    return _ACTIONS[name]


# ---------------------------------------------------------------------- #
# 内建动作实现（print 一律移除，完成/失败提示走 TOML message/fail_message）
# ---------------------------------------------------------------------- #
@action(
    "setenv",
    param_help={
        "name": "环境变量名",
        "value": "环境变量值",
        "default": "为 True 时使用 setdefault 不覆盖已有值",
    },
)
def _setenv(name: str, value: str, default: bool = False) -> None:
    """设置当前进程环境变量（仅影响 fcmd 进程及其后续子任务）。"""
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value


@action(
    "writefile",
    param_help={
        "path": "目标文件路径",
        "content": "写入内容",
        "encoding": "文件编码（默认 utf-8）",
    },
)
def _writefile(path: str, content: str, encoding: str = "utf-8") -> None:
    """将文本内容写入指定路径的文件。"""
    Path(path).write_text(content, encoding=encoding)


# ---------------------------------------------------------------------- #
# 文件名批量操作原语（filedate/filerename/filelevel/folderback，2026-10-07 迁入）
# ---------------------------------------------------------------------- #
def _file_timestamp(filepath: Path) -> str:
    """获取文件时间戳（取修改时间与创建时间的较大值），``YYYYMMDD`` 格式。"""
    import time  # 延迟导入：time 仅日期前缀/备份场景需要（冷启动导入税）

    stat = filepath.stat()
    return time.strftime("%Y%m%d", time.localtime(max(stat.st_mtime, stat.st_ctime)))


# 日期前缀正则：匹配 19xx/20xx 开头的 YYYYMMDD（允许分隔符 -_#.~）
_DATE_PATTERN = re.compile(r"(20|19)\d{2}[-_#.~]?((0[1-9])|(1[012]))[-_#.~]?((0[1-9])|([12]\d)|(3[01]))[-_#.~]?")
_DATE_SEP = "_"


def _strip_date_prefix(filepath: Path) -> Path:
    """移除文件名主干中的日期前缀（无前缀时打印提示返回原路径）。"""
    stem = filepath.stem
    new_stem = _DATE_PATTERN.sub("", stem)
    if new_stem == stem:
        print(f"{filepath} 无日期前缀")
        return filepath
    new_path = filepath.with_name(new_stem + filepath.suffix)
    filepath.rename(new_path)
    return new_path


@action("dateprefix_add", param_help={"files": "文件路径列表"})
def _dateprefix_add(files: list[Path]) -> None:
    """为文件名添加/更新日期前缀（基于文件修改/创建时间，先清后加避免重复）。"""
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            stripped = _strip_date_prefix(filepath)
            timestamp = _file_timestamp(stripped)
            target = stripped.with_name(f"{timestamp}{_DATE_SEP}{stripped.stem}{stripped.suffix}")
            stripped.rename(target)


@action("dateprefix_clear", param_help={"files": "文件路径列表"})
def _dateprefix_clear(files: list[Path]) -> None:
    """清除文件名中的日期前缀（缺失文件与点文件跳过）。"""
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            _strip_date_prefix(filepath)


def _safe_rename(filepath: Path, new_stem: str, preview: bool) -> bool:
    """安全重命名文件名主干（保留扩展名），返回是否执行。

    目标名与原名相同时静默跳过；目标已存在且非同一文件时跳过并提示
    （大小写不敏感文件系统上仅大小写不同视为同一文件允许重命名）。
    """
    if new_stem == filepath.stem:
        return False
    target = filepath.with_name(new_stem + filepath.suffix)
    if target.exists() and target.resolve() != filepath.resolve():
        print(f"跳过（目标已存在）: {filepath.name} -> {target.name}")
        return False
    if preview:
        print(f"[预览] {filepath.name} -> {target.name}")
    else:
        filepath.rename(target)
        print(f"重命名: {filepath.name} -> {target.name}")
    return True


@action(
    "filerename_replace",
    param_help={
        "files": "待重命名的文件列表",
        "pattern": "正则表达式（Python re 语法，支持反向引用）",
        "replacement": "替换字符串（默认空字符串，即删除匹配部分）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_replace(files: list[Path], pattern: str, replacement: str = "", preview: bool = False) -> None:
    """正则替换文件名主干中的匹配部分（保留扩展名，仅匹配时执行）。"""
    compiled = re.compile(pattern)  # 语法错误 → 任务失败汇总 + 退出码 1
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        if not compiled.search(filepath.stem):
            continue
        _safe_rename(filepath, compiled.sub(replacement, filepath.stem), preview)


@action(
    "filerename_insert",
    param_help={
        "files": "待重命名的文件列表",
        "text": "待插入文本",
        "position": "插入位置（0=开头，负数从末尾计算，超出范围自动截断）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_insert(files: list[Path], text: str, position: int = 0, preview: bool = False) -> None:
    """在文件名主干指定位置插入文本（保留扩展名）。"""
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        if not text:
            continue
        stem = filepath.stem
        pos = max(0, min(position, len(stem)))
        _safe_rename(filepath, stem[:pos] + text + stem[pos:], preview)


@action(
    "filerename_case",
    param_help={
        "files": "待重命名的文件列表",
        "mode": "转换模式：lower / upper / title（默认 lower）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_case(files: list[Path], mode: str = "lower", preview: bool = False) -> None:
    """转换文件名主干大小写（保留扩展名）。"""
    mode_map = {"lower": str.lower, "upper": str.upper, "title": str.title}
    if mode not in mode_map:
        raise ValueError(f"不支持的大小写模式: {mode}（可选: lower/upper/title）")
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        _safe_rename(filepath, mode_map[mode](filepath.stem), preview)


# 等级标记映射：0 表示清除等级，1-4 对应不同等级标记
_LEVELS: dict[str, str] = {"0": "", "1": "PUB,NOR", "2": "INT", "3": "CON", "4": "CLA"}
# 左右括号集合：标记两侧的括号字符（用于识别并整体移除）
_LEVEL_BRACKETS: tuple[str, str] = (" ([_(【-", " )]_）】")


def _remove_level_marks(stem: str, marks: list[str]) -> str:
    """从文件名主干中移除所有被括号包裹的标记（裸字符串形式保留）。"""
    left_brackets, right_brackets = _LEVEL_BRACKETS
    for mark in marks:
        pos = 0
        while True:
            pos = stem.find(mark, pos)
            if pos == -1:
                break
            b, e = pos - 1, pos + len(mark)
            if b >= 0 and e < len(stem) and stem[b] in left_brackets and stem[e] in right_brackets:
                stem = stem[:b] + stem[e + 1 :]
            else:
                pos = e
    return stem


def _process_file_level(filepath: Path, level: int) -> None:
    """单文件等级标记处理：先清除所有已有标记，再按 ``level`` 添加新标记。"""
    filestem = filepath.stem
    original_stem = filestem
    for level_names in _LEVELS.values():
        if level_names:
            filestem = _remove_level_marks(filestem, level_names.split(","))
    for digit in map(str, range(1, 10)):
        filestem = _remove_level_marks(filestem, [digit])
    if level > 0:
        levelstr = _LEVELS[str(level)].split(",")[0]
        if levelstr:
            filestem = f"{filestem}({levelstr})"
    if filestem != original_stem:
        new_path = filepath.with_name(filestem + filepath.suffix)
        filepath.rename(new_path)
        print(f"重命名: {filepath} -> {new_path}")


@action(
    "filelevel_set",
    param_help={
        "files": "文件路径列表",
        "level": "文件等级（0-4），0 用于清除等级",
    },
)
def _filelevel_set(files: list[Path], level: int = 0) -> None:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。"""
    if not (0 <= level < len(_LEVELS)):
        raise ValueError(f"无效的等级 {level}，必须在 0 和 {len(_LEVELS) - 1} 之间")
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        _process_file_level(filepath, level)


# ---------------------------------------------------------------------- #
# 系统管理原语（taskkill/which/sysinfo，2026-10-07 迁入）
# ---------------------------------------------------------------------- #
def _system_taskkill_path() -> str:
    """返回系统 ``taskkill.exe`` 绝对路径。

    必须使用绝对路径调用系统 taskkill.exe，避免 fcmd 自身注册的 ``taskkill``
    entry script（``pip install`` 生成于 Python Scripts 目录）在 PATH 中
    优先于 ``C:\\Windows\\System32\\taskkill.exe``，导致 ``subprocess.run``
    递归调用 fcmd taskkill 自身，指数级进程爆炸直至系统资源耗尽。

    使用 ``PureWindowsPath`` 而非 ``Path``：本函数仅在 ``sys.platform == 'win32'``
    分支调用，但 CI 在 Linux 上运行时会 monkeypatch ``sys.platform``，此时
    ``Path`` 会退化为 ``PosixPath``，把 ``C:\\Windows`` 当作单一组件用 ``/``
    拼接，产生混合分隔符路径。``PureWindowsPath`` 跨平台一致使用反斜杠。
    """
    # Windows 环境变量大小写不敏感，SystemRoot 是系统约定写法
    system_root = os.environ.get("SystemRoot", r"C:\Windows")  # noqa: SIM112
    return str(PureWindowsPath(system_root) / "System32" / "taskkill.exe")


@action("taskkill", param_help={"names": "进程名称列表（自动追加 * 通配符）"})
def _taskkill(names: list[str]) -> None:
    """按名称终止进程（跨平台，Windows taskkill /FI 过滤器 / Unix pkill）。

    Windows 使用 ``taskkill /f /fi "imagename eq <name>*"``（``/FI`` 过滤器
    支持通配符，兼容 Win7）；Linux/macOS 使用 ``pkill -f <name>*``。
    逐条目回显结果；未匹配（pkill 返回码 1）为常见正常场景，打印提示后
    继续，不视为失败。
    """
    import subprocess  # 延迟导入：subprocess 仅终止进程场景需要（冷启动导入税）

    if sys.platform == "win32":
        # 用 /FI 过滤器替代 /IM 通配符：Win7 的 /IM 不支持部分通配符
        prefix: tuple[str, ...] = (_system_taskkill_path(), "/f", "/fi")
        target = "imagename eq {}*"
    else:
        prefix = ("pkill", "-f")
        target = "{}*"
    for name in names:
        print(f"终止进程: {name}")
        # pkill 返回 1 表示无匹配进程（非错误），故 check=False + 手动检查返回码
        result = subprocess.run([*prefix, target.format(name)], check=False, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  已发送终止信号: {name}")
        else:
            print(f"  未找到匹配进程或终止失败 (returncode={result.returncode}): {name}")


@action("which", param_help={"commands": "要查找的命令名称列表"})
def _which(commands: list[str]) -> None:
    """逐条查找可执行命令路径（跨平台 shutil.which），回显查找结果。"""
    import shutil  # 延迟导入：shutil 仅命令查找场景需要（冷启动导入税）

    for command in commands:
        path = shutil.which(command)
        if path is None:
            print(f"{command} -> 未找到")
        else:
            print(f"{command} -> {path}")


def _format_bytes(size: int) -> str:
    """将字节数格式化为人类可读字符串（形如 ``1.5 GB``）。"""
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


@action("sysinfo")
def _sysinfo() -> None:
    """收集并打印当前环境的系统信息（Python/平台/内存/磁盘/CPU）。"""
    import platform  # 延迟导入：platform 仅系统诊断场景需要（冷启动导入税）
    import shutil

    info: dict[str, str] = {
        "Python 版本": sys.version.split()[0],
        "Python 路径": sys.executable,
        "平台": platform.platform(),
        "架构": platform.machine(),
        "处理器": platform.processor() or "未知",
        "操作系统": f"{platform.system()} {platform.release()}",
    }

    # 内存信息（仅 Linux/macOS 可获取， Windows 无 resource 模块）
    try:
        import resource

        # getrusage 返回 ru_maxrss: Linux 上单位 KB, macOS 上单位字节
        usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[missing-attribute]
        if platform.system() == "Darwin":
            info["内存峰值"] = _format_bytes(usage.ru_maxrss)
        else:
            info["内存峰值"] = _format_bytes(usage.ru_maxrss * 1024)
    except (OSError, AttributeError, ImportError):
        pass

    # 磁盘信息（当前目录所在分区）
    try:
        usage = shutil.disk_usage(Path.cwd())
        info["磁盘总量"] = _format_bytes(usage.total)
        info["磁盘已用"] = _format_bytes(usage.used)
        info["磁盘可用"] = _format_bytes(usage.free)
    except OSError:  # pragma: no cover - 罕见平台
        pass

    info["CPU 核心数"] = str(os.cpu_count() or "未知")
    info["工作目录"] = str(Path.cwd())

    print("=" * 50)
    print("系统信息")
    print("=" * 50)
    for key, value in info.items():
        print(f"  {key:16s}: {value}")
    print("=" * 50)


@action(
    "folderback",
    param_help={
        "src": "源文件夹路径（默认: 当前目录）",
        "dst": "目标文件夹路径（默认: ./backup）",
        "max_zip": "最大备份数量（默认: 5，超出时删除最旧的）",
    },
)
def _folderback(src: str = ".", dst: str = "./backup", max_zip: int = 5) -> None:
    """备份文件夹到指定目录（zip 压缩），自动清理旧备份。"""
    import time  # 延迟导入：time/zipfile 仅备份场景需要（冷启动导入税）
    import zipfile

    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.exists():
        raise FileNotFoundError(f"源文件夹不存在: {src_path}")
    if not dst_path.exists():
        dst_path.mkdir(parents=True, exist_ok=True)
        print(f"创建目标文件夹: {dst_path}")

    files = [str(f) for f in src_path.rglob("*")]
    timestamp = time.strftime("_%Y%m%d_%H%M%S")
    target_path = dst_path / (src_path.stem + timestamp + ".zip")
    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, arcname=file.replace(str(src_path.parent), ""))

    # 清理旧备份：递归删除匹配源文件夹名的旧 zip，保留最新 max_zip 个
    while True:
        zip_paths = [fp for fp in dst_path.rglob("*.zip") if src_path.stem in str(fp)]
        zip_files = sorted(zip_paths, key=lambda fn: str(fn)[-19:-4])
        if len(zip_files) <= max_zip:
            break
        zip_files[0].unlink()
    print(f"备份完成: {target_path}")


# ---------------------------------------------------------------------- #
# 计算型动作原语（DSL compute 数据流：返回值注入模板插值，2026-10-07 迁入）
# ---------------------------------------------------------------------- #
# 与副作用型动作（返回 None，语义是「做事情」）不同，计算型动作的返回值
# 被 DSL 引擎消费：注入共享变量供本命令 cmd/cwd/env/message 模板插值，
# 空结果（空 list/空串）任务 SKIPPED。逐条目过程反馈（受保护包跳过提示）
# 保留 print（沿用批量操作消息约定）。

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
