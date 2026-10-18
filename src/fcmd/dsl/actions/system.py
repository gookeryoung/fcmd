"""系统管理动作：进程终止/命令查找/系统信息/文件夹备份。"""

from __future__ import annotations

import sys
from pathlib import Path

from fcmd.dsl.actions import action

__all__: list[str] = []


@action("taskkill", param_help={"names": "进程名称列表（按名称前缀匹配）"})
def _taskkill(names: list[str]) -> None:
    """按名称终止进程（跨平台，基于 psutil）。

    逐个名称用 ``psutil.process_iter`` 匹配名称前缀，命中后先 ``terminate()``
    给进程一个优雅退出的机会，等待 3 秒仍未退出的 ``kill()``。
    逐条目回显；未匹配时打印提示后继续，不视为失败。

    用 psutil 而非子进程 ``taskkill``/``pkill``：无需解析本地化输出，
    也不会因 fcmd 自身注册的同名 entry script 抢占 PATH 而递归调用自身。
    """
    import psutil  # 延迟导入：仅终止进程场景需要（冷启动导入税）

    for name in names:
        print(f"终止进程: {name}")
        targets = [
            proc for proc in psutil.process_iter(["pid", "name"]) if (proc.info.get("name") or "").startswith(name)
        ]
        if not targets:
            print(f"  未找到匹配进程: {name}")
            continue
        for proc in targets:
            print(f"  PID={proc.pid} 名称={proc.info.get('name')} -> terminate")
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                print(f"  PID={proc.pid} 已退出")
            except psutil.AccessDenied:
                print(f"  PID={proc.pid} 权限不足，跳过")
        _gone, alive = psutil.wait_procs(targets, timeout=3)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                print(f"  PID={proc.pid} 权限不足，无法强制终止")
            else:
                print(f"  PID={proc.pid} 已强制终止")


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
    import time

    import psutil

    info: dict[str, str] = {
        "Python 版本": sys.version.split()[0],
        "Python 路径": sys.executable,
        "平台": platform.platform(),
        "架构": platform.machine(),
        "处理器": platform.processor() or "未知",
        "操作系统": f"{platform.system()} {platform.release()}",
    }

    mem = psutil.virtual_memory()
    info["内存总量"] = _format_bytes(mem.total)
    info["内存已用"] = f"{_format_bytes(mem.used)} ({mem.percent:.1f}%)"
    info["内存可用"] = _format_bytes(mem.available)

    swap = psutil.swap_memory()
    info["交换区总量"] = _format_bytes(swap.total)
    info["交换区已用"] = _format_bytes(swap.used)

    # 磁盘信息（当前目录所在分区）
    try:
        usage = psutil.disk_usage(str(Path.cwd()))
    except OSError:  # pragma: no cover - 罕见平台
        pass
    else:
        info["磁盘总量"] = _format_bytes(usage.total)
        info["磁盘已用"] = _format_bytes(usage.used)
        info["磁盘可用"] = _format_bytes(usage.free)

    info["CPU 核心数"] = str(psutil.cpu_count() or "未知")
    try:
        boot_time = psutil.boot_time()
    except OSError:  # pragma: no cover - 罕见平台
        pass
    else:
        info["开机时间"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot_time))
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
