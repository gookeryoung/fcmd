"""系统管理动作：进程终止/命令查找/系统信息/文件夹备份。"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath

from fcmd.dsl.actions import action

__all__: list[str] = []


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
