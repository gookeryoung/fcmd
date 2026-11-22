"""wslmanw「初始化环境」的纯逻辑。

把「勾选哪些项目、每个项目在实例里执行什么命令、如何判断当前状态」从 Qt
控件里独立出来：界面只负责展示与调度，单测覆盖这里的脚本拼装与结果解析。

执行口径与 :mod:`fcmd.tools.dev.envdev` 保持一致（国内系统镜像源用
linuxmirrors，pip / npm / cargo 走国内镜像），但这里通过 ``wsl.exe -d <实例>
-- bash -lc`` 在目标实例内执行，不要求实例内安装 fcmd。
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "TASKS",
    "EnvStatus",
    "InitStep",
    "InitTask",
    "default_checked",
    "parse_status",
    "present",
    "probe_script",
    "short_label",
    "ssh_key_short",
    "status_rows",
    "steps_for",
    "summary_text",
    "tail_lines",
    "task_by_key",
    "task_keys",
    "task_labels",
]

_MIRROR_URL = "https://linuxmirrors.cn/main.sh"
_MIRROR_SOURCE = "mirrors.aliyun.com"
_MIRROR_DOMAINS: tuple[str, ...] = (
    "mirrors.aliyun.com",
    "mirrors.tencent.com",
    "mirrors.huaweicloud.com",
    "mirrors.ustc.edu.cn",
    "mirrors.tuna.tsinghua.edu.cn",
    "mirrors.163.com",
    "mirrors.zju.edu.cn",
    "mirrors.nju.edu.cn",
    "mirror.sjtu.edu.cn",
)
"""判断系统源是否已换成国内镜像时识别的域名（与 linuxmirrors 默认列表的前几项一致）。"""

_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
_NPM_REGISTRY = "https://registry.npmmirror.com"

PROBE_PREFIX = "WSLMAN_"
"""探测脚本每条结果行的前缀；以它过滤 ``bash -lc`` 读到的 profile 横幅。"""


@dataclass(frozen=True, slots=True)
class InitStep:
    """一次在实例内执行的命令；``root=True`` 时用 ``wsl -u root`` 执行。"""

    label: str
    script: str
    root: bool = False
    timeout_s: float = 1800.0


@dataclass(frozen=True, slots=True)
class InitTask:
    """一个可勾选的初始化项目。"""

    key: str
    label: str
    detail: str
    steps: tuple[InitStep, ...]


@dataclass(frozen=True, slots=True)
class EnvStatus:
    """一次探测得到的实例环境快照；缺失字段用空串表示。"""

    user: str = ""
    home: str = ""
    os_name: str = ""
    mirror: str = ""
    ssh_public_key: str = ""
    python: str = ""
    node: str = ""
    rust: str = ""
    pip_index: str = ""
    npm_registry: str = ""

    @property
    def ssh_ready(self) -> bool:
        return bool(self.ssh_public_key)


def _apt_script(*packages: str) -> str:
    return "\n".join(
        (
            "set -e",
            "apt-get update",
            f"apt-get install -y {' '.join(packages)}",
        )
    )


def _mirror_script() -> str:
    """系统镜像源：按 linuxmirrors 的非交互参数执行，避免 GUI 里卡在菜单。"""
    return "\n".join(
        (
            "set -e",
            "if ! command -v curl >/dev/null 2>&1; then",
            "    apt-get update",
            "    apt-get install -y curl",
            "fi",
            f"bash <(curl -sSL {_MIRROR_URL}) "
            f"--source {_MIRROR_SOURCE} --use-intranet-source false --protocol https "
            "--backup true --install-epel false --upgrade-software false --clean-cache true --ignore-backup-tips",
            'echo "系统镜像源配置完成"',
        )
    )


def _ssh_key_script() -> str:
    return "\n".join(
        (
            "set -e",
            'mkdir -p "$HOME/.ssh"',
            'chmod 700 "$HOME/.ssh"',
            'key="$HOME/.ssh/id_ed25519"',
            'if [ -f "$key" ]; then',
            '    echo "SSH 密钥已存在：$key"',
            "else",
            '    ssh-keygen -t ed25519 -N "" -C "$(id -un)@$(hostname)" -f "$key" >/dev/null',
            '    echo "已生成 SSH 密钥：$key"',
            "fi",
            'chmod 600 "$key" 2>/dev/null || true',
            'chmod 644 "$key.pub" 2>/dev/null || true',
            'echo "公钥："',
            'cat "$key.pub"',
        )
    )


def _python_script() -> str:
    return "\n".join(
        (
            "set -e",
            "python3 -m pip config set global.index-url " + _PIP_INDEX_URL,
            'echo "Python 与 pip 镜像已配置"',
        )
    )


def _node_script() -> str:
    return "\n".join(
        (
            "set -e",
            f"npm config set registry {_NPM_REGISTRY}",
            'echo "Node.js 与 npm 镜像已配置"',
        )
    )


def _rust_script() -> str:
    """rustup 走 rsproxy 国内源；已安装时只补 cargo 镜像配置。"""
    return "\n".join(
        (
            "set -e",
            "if command -v rustc >/dev/null 2>&1; then",
            '    echo "Rust 已安装：$(rustc --version)"',
            "else",
            "    export RUSTUP_DIST_SERVER=https://rsproxy.cn",
            "    export RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup",
            "    curl --proto '=https' --tlsv1.2 -sSf https://rsproxy.cn/rustup-init.sh | sh -s -- -y --profile default",
            "fi",
            'mkdir -p "$HOME/.cargo"',
            'if grep -q "rsproxy.cn" "$HOME/.cargo/config.toml" 2>/dev/null; then',
            '    echo "cargo 镜像已配置"',
            "else",
            "    cat >> \"$HOME/.cargo/config.toml\" <<'EOF'",
            "[source.crates-io]",
            "replace-with = 'rsproxy-sparse'",
            "",
            "[source.rsproxy-sparse]",
            'registry = "https://rsproxy.cn/index/"',
            "",
            "[registries.rsproxy]",
            'index = "https://rsproxy.cn/crates.io-index"',
            "",
            "[net]",
            "git-fetch-with-cli = true",
            "EOF",
            "fi",
            'echo "Rust 开发环境配置完成"',
        )
    )


TASKS: tuple[InitTask, ...] = (
    InitTask(
        "mirror",
        "运行国内源配置脚本",
        f"通过 linuxmirrors 把 APT / YUM 系统源切换到国内镜像（默认 {_MIRROR_SOURCE}），"
        "以 root 执行；已配置时重复执行也是安全的。",
        (InitStep("配置系统镜像源", _mirror_script(), root=True),),
    ),
    InitTask(
        "ssh",
        "生成 SSH 密钥",
        "生成无口令的 ed25519 密钥对（~/.ssh/id_ed25519），已存在时保留原密钥；生成后可复制公钥。",
        (
            InitStep("安装 OpenSSH 客户端", _apt_script("openssh-client"), root=True),
            InitStep("生成 SSH 密钥", _ssh_key_script()),
        ),
    ),
    InitTask(
        "python",
        "安装 Python 开发环境",
        "安装 python3 / pip / venv，并把 pip 索引指向清华镜像。",
        (
            InitStep("安装 Python 软件包", _apt_script("python3", "python3-pip", "python3-venv"), root=True),
            InitStep("配置 pip 镜像", _python_script()),
        ),
    ),
    InitTask(
        "node",
        "安装 Node.js 开发环境",
        "安装 nodejs / npm，并把 npm registry 指向 npmmirror。",
        (
            InitStep("安装 Node.js 软件包", _apt_script("nodejs", "npm"), root=True),
            InitStep("配置 npm 镜像", _node_script()),
        ),
    ),
    InitTask(
        "rust",
        "安装 Rust 开发环境",
        "通过 rustup 安装 stable 工具链（下载源 rsproxy.cn），并写入 cargo 镜像配置。",
        (
            InitStep("安装编译依赖", _apt_script("build-essential", "curl"), root=True),
            InitStep("安装 Rust 工具链", _rust_script()),
        ),
    ),
)


def task_by_key(key: str) -> InitTask | None:
    return next((task for task in TASKS if task.key == key), None)


_TASK_SHORT: dict[str, str] = {
    "mirror": "镜像源",
    "ssh": "SSH 密钥",
    "python": "Python",
    "node": "Node.js",
    "rust": "Rust",
}


def short_label(key: str) -> str:
    """项目短名：收起态把已初始化情况挤在一行标签里时用。"""
    return _TASK_SHORT.get(key, key)


def steps_for(keys: Sequence[str]) -> tuple[InitStep, ...]:
    """按 :data:`TASKS` 的固定顺序展开勾选项对应的执行步骤。"""
    selected = set(keys)
    steps: list[InitStep] = []
    for task in TASKS:
        if task.key in selected:
            steps.extend(task.steps)
    return tuple(steps)


def task_keys() -> tuple[str, ...]:
    """全部可初始化项目的 key（按固定顺序）。"""
    return tuple(task.key for task in TASKS)


def task_labels(keys: Sequence[str]) -> tuple[str, ...]:
    selected = set(keys)
    return tuple(task.label for task in TASKS if task.key in selected)


def probe_script() -> str:
    """只读探测脚本：固定输出 ``WSLMAN_*`` 行，供 :func:`parse_status` 解析。"""
    domains = " ".join(_MIRROR_DOMAINS)
    return "\n".join(
        (
            "set +e",
            'printf "WSLMAN_USER=%s\\n" "$(id -un 2>/dev/null)"',
            'printf "WSLMAN_HOME=%s\\n" "$HOME"',
            'os_name=""',
            "if [ -r /etc/os-release ]; then",
            '    os_name="$(. /etc/os-release 2>/dev/null; printf "%s" "${PRETTY_NAME:-$NAME}")"',
            "fi",
            'printf "WSLMAN_OS=%s\\n" "$os_name"',
            'mirror=""',
            "for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/yum.repos.d/*.repo; do",
            '    [ -r "$file" ] || continue',
            f"    for domain in {domains}; do",
            '        if grep -q "$domain" "$file" 2>/dev/null; then',
            '            mirror="$domain"',
            "            break 2",
            "        fi",
            "    done",
            "done",
            'printf "WSLMAN_MIRROR=%s\\n" "$mirror"',
            'ssh_pub=""',
            'if [ -r "$HOME/.ssh/id_ed25519.pub" ]; then',
            '    ssh_pub="$(base64 < "$HOME/.ssh/id_ed25519.pub" 2>/dev/null | tr -d \'\\n\')"',
            "fi",
            'printf "WSLMAN_SSH_PUB=%s\\n" "$ssh_pub"',
            'printf "WSLMAN_PYTHON=%s\\n" "$(python3 --version 2>/dev/null | awk \'{print $2}\')"',
            'printf "WSLMAN_NODE=%s\\n" "$(node --version 2>/dev/null | sed \'s/^v//\')"',
            'printf "WSLMAN_RUST=%s\\n" "$(rustc --version 2>/dev/null | awk \'{print $2}\')"',
            'printf "WSLMAN_PIP_INDEX=%s\\n" "$(python3 -m pip config get global.index-url 2>/dev/null)"',
            'printf "WSLMAN_NPM_REGISTRY=%s\\n" "$(npm config get registry 2>/dev/null)"',
        )
    )


def _decode_key(raw: str) -> str:
    """探测脚本把公钥做了 base64；解不开时当作明文（便于手工喂样例）。"""
    text = raw.strip()
    if not text:
        return ""
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8", errors="replace").strip()
    except (binascii.Error, ValueError):
        return text
    return decoded


def parse_status(text: str) -> EnvStatus:
    """解析探测脚本输出；非 ``WSLMAN_`` 行（profile 横幅）直接忽略。"""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith(PROBE_PREFIX) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key[len(PROBE_PREFIX) :]] = value.strip()
    return EnvStatus(
        user=values.get("USER", ""),
        home=values.get("HOME", ""),
        os_name=values.get("OS", ""),
        mirror=values.get("MIRROR", ""),
        ssh_public_key=_decode_key(values.get("SSH_PUB", "")),
        python=values.get("PYTHON", ""),
        node=values.get("NODE", ""),
        rust=values.get("RUST", ""),
        pip_index=values.get("PIP_INDEX", ""),
        npm_registry=values.get("NPM_REGISTRY", ""),
    )


def present(key: str, status: EnvStatus) -> bool:
    """判断某个初始化项目是否已就绪。"""
    if key == "mirror":
        return bool(status.mirror)
    if key == "ssh":
        return status.ssh_ready
    if key == "python":
        return bool(status.python)
    if key == "node":
        return bool(status.node)
    if key == "rust":
        return bool(status.rust)
    return False


def default_checked(key: str, status: EnvStatus) -> bool:
    """默认勾选尚未完成的项目；探测失败（全空）时等于全选。"""
    return not present(key, status)


def ssh_key_short(public_key: str) -> str:
    """公钥的短展示：``ssh-ed25519 AAAAC3NzaC1l…``，避免撑宽对话框。"""
    parts = public_key.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[1][:16]}…"
    return public_key[:32] + ("…" if len(public_key) > 32 else "")


def tail_lines(text: str, limit: int = 80) -> tuple[str, ...]:
    """保留命令输出的最后 ``limit`` 个非空行，供界面日志与错误摘要复用。"""
    if limit <= 0:
        return ()
    lines = [line for line in text.splitlines() if line.strip()]
    return tuple(lines[-limit:])


def status_rows(status: EnvStatus) -> tuple[tuple[str, str, bool], ...]:
    """环境详情区逐项 ``(字段, 值, 是否就绪)``，供界面统一着色。"""
    return (
        ("系统镜像源", status.mirror or "未配置（仍为官方源）", bool(status.mirror)),
        ("SSH 密钥", ssh_key_short(status.ssh_public_key) if status.ssh_ready else "未生成", status.ssh_ready),
        ("Python", status.python or "未安装", bool(status.python)),
        ("Node.js", status.node or "未安装", bool(status.node)),
        ("Rust", status.rust or "未安装", bool(status.rust)),
        ("pip 镜像", status.pip_index or "未配置", bool(status.pip_index)),
        ("npm 镜像", status.npm_registry or "未配置", bool(status.npm_registry)),
    )


def summary_text(status: EnvStatus) -> str:
    """一行摘要：``镜像源 ✓ · SSH ✗ · Python 3.12.3 · Node.js ✗ · Rust ✗``。"""
    parts = [
        f"镜像源 {'✓' if status.mirror else '✗'}",
        f"SSH {'✓' if status.ssh_ready else '✗'}",
        f"Python {status.python or '✗'}",
        f"Node.js {status.node or '✗'}",
        f"Rust {status.rust or '✗'}",
    ]
    return " · ".join(parts)
