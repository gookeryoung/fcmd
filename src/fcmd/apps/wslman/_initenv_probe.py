"""wslmanw「初始化环境」的探测脚本与状态判定。

从 :mod:`fcmd.apps.wslman._initenv` 拆出来（主模块只留任务定义与脚本拼装）：这里放
只读探测脚本、``WSLMAN_*`` 结果解析、``EnvStatus`` 快照与界面用的状态行 / 摘要文案。

探测前先把 ``/mnt/*``（Windows 盘挂载点）从 PATH 摘掉：WSL 默认把 Windows 的 PATH
追加进实例，不摘的话没装 Linux 版 Node.js 也会被 ``command -v node`` 判成已安装，
用户接着用那个 npm 装的 ``pi`` 落在 Windows 侧，实例里根本跑不起来。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass

from fcmd.apps.wslman._initenv_display import (
    OPTIONAL_KEYS,
    display_present,
    display_status_rows,
    display_summary_parts,
    probe_lines,
)
from fcmd.apps.wslman._initenv_pkg import (
    PACKAGE_MIRROR_AUTO,
    normalize_package_mirror,
    source_mirror_lines,
    system_mirror_source,
)
from fcmd.apps.wslman._interop import WINDOWS_PATH_VAR, drop_windows_path_lines

__all__ = [
    "PROBE_PREFIX",
    "EnvStatus",
    "default_checked",
    "parse_status",
    "present",
    "probe_script",
    "ssh_key_short",
    "status_from_mapping",
    "status_rows",
    "status_to_mapping",
    "summary_text",
    "tail_lines",
]


PROBE_PREFIX = "WSLMAN_"
"""探测脚本每条结果行的前缀；以它过滤 ``bash -lc`` 读到的 profile 横幅。"""


@dataclass(frozen=True, slots=True)
class EnvStatus:
    """一次探测得到的实例环境快照；缺失字段用空串表示。"""

    user: str = ""
    home: str = ""
    os_name: str = ""
    mirror: str = ""
    ssh_public_key: str = ""
    python: str = ""
    uv: str = ""
    auto_uv_env: str = ""
    node: str = ""
    node_win: str = ""
    """Windows 侧（``/mnt``）的 Node.js 版本；仅用于提示「不算实例内已装」。"""
    nvm: str = ""
    bun: str = ""
    rust: str = ""
    docker: str = ""
    """``docker --version`` 的版本号；空串表示未安装。"""
    docker_mirror: str = ""
    """``/etc/docker/daemon.json`` 里第一个 ``registry-mirrors`` 地址。"""
    gui_libs: str = ""
    """``ok`` 表示显示依赖库齐全；否则为缺失的库名（空格分隔）。"""
    cjk_font: str = ""
    """探测到的中文字体族名；空串表示未安装。"""
    noto_cjk: str = ""
    """``yes`` 表示已装 Noto CJK 完整字体。"""
    emoji_font: str = ""
    """``yes`` 表示已装 Noto Color Emoji。"""
    pip_index: str = ""
    uv_index: str = ""
    npm_registry: str = ""
    bun_registry: str = ""

    @property
    def ssh_ready(self) -> bool:
        return bool(self.ssh_public_key)


def probe_script() -> str:
    """只读探测脚本：固定输出 ``WSLMAN_*`` 行，供 :func:`parse_status` 解析。"""
    return "\n".join(
        (
            "set +e",
            # 先摘掉 /mnt（Windows 互操作目录）：否则没装 Linux 版工具也会被 command -v 命中，
            # 进而把 Windows 侧装的 pi 当成实例内已就绪。
            *drop_windows_path_lines(),
            'printf "WSLMAN_USER=%s\\n" "$(id -un 2>/dev/null)"',
            'printf "WSLMAN_HOME=%s\\n" "$HOME"',
            'os_name=""',
            "if [ -r /etc/os-release ]; then",
            '    os_name="$(. /etc/os-release 2>/dev/null; printf "%s" "${PRETTY_NAME:-$NAME}")"',
            "fi",
            'printf "WSLMAN_OS=%s\\n" "$os_name"',
            *source_mirror_lines(),
            'printf "WSLMAN_MIRROR=%s\\n" "$mirror"',
            'ssh_pub=""',
            'if [ -r "$HOME/.ssh/id_ed25519.pub" ]; then',
            '    ssh_pub="$(base64 < "$HOME/.ssh/id_ed25519.pub" 2>/dev/null | tr -d \'\\n\')"',
            "fi",
            'printf "WSLMAN_SSH_PUB=%s\\n" "$ssh_pub"',
            'printf "WSLMAN_PYTHON=%s\\n" "$(python3 --version 2>/dev/null | awk \'{print $2}\')"',
            'uv_bin="$(command -v uv 2>/dev/null)"',
            '[ -z "$uv_bin" ] && [ -x "$HOME/.local/bin/uv" ] && uv_bin="$HOME/.local/bin/uv"',
            'printf "WSLMAN_UV=%s\\n" "$([ -n "$uv_bin" ] && "$uv_bin" --version 2>/dev/null | awk \'{print $2}\')"',
            'auto_uv_bin="$(command -v auto-uv-env 2>/dev/null)"',
            '[ -z "$auto_uv_bin" ] && [ -x "$HOME/.local/bin/auto-uv-env" ] && auto_uv_bin="$HOME/.local/bin/auto-uv-env"',
            'printf "WSLMAN_AUTO_UV_ENV=%s\\n" "$([ -n "$auto_uv_bin" ] && "$auto_uv_bin" --version 2>/dev/null | awk \'{print $2}\')"',
            'node_bin="$(command -v node 2>/dev/null)"',
            '[ -z "$node_bin" ] && [ -x "$HOME/.local/bin/node" ] && node_bin="$HOME/.local/bin/node"',
            'printf "WSLMAN_NODE=%s\\n" "$([ -n "$node_bin" ] && "$node_bin" --version 2>/dev/null | sed \'s/^v//\')"',
            # 实例里没有 Linux node 时，记下 Windows 侧的版本：界面据此提示
            # 「只是 /mnt 里有，装 pi 会落到 Windows」，而不是静默显示未安装。
            f'printf "WSLMAN_NODE_WIN=%s\\n" "$([ -z "$node_bin" ] && PATH="${{{WINDOWS_PATH_VAR}}}" node --version 2>/dev/null | sed \'s/^v//\')"',
            'printf "WSLMAN_NVM=%s\\n" "$([ -s "$HOME/.nvm/nvm.sh" ] && echo yes)"',
            'bun_bin="$(command -v bun 2>/dev/null)"',
            '[ -z "$bun_bin" ] && [ -x "$HOME/.local/bin/bun" ] && bun_bin="$HOME/.local/bin/bun"',
            'printf "WSLMAN_BUN=%s\\n" "$([ -n "$bun_bin" ] && "$bun_bin" --version 2>/dev/null)"',
            'printf "WSLMAN_RUST=%s\\n" "$(rustc --version 2>/dev/null | awk \'{print $2}\')"',
            'printf "WSLMAN_DOCKER=%s\\n" "$(docker --version 2>/dev/null | sed \'s/^Docker version \\([^,]*\\).*/\\1/\')"',
            'docker_mirror=""',
            "if [ -r /etc/docker/daemon.json ]; then",
            "    docker_mirror=\"$(grep -A8 'registry-mirrors' /etc/docker/daemon.json 2>/dev/null | grep -o 'https://[^\"]*' | head -1)\"",
            "fi",
            'printf "WSLMAN_DOCKER_MIRROR=%s\\n" "$docker_mirror"',
            *probe_lines(),
            'pip_index=""',
            'for f in /etc/pip.conf "$HOME/.pip/pip.conf" "$HOME/.config/pip/pip.conf"; do',
            '    [ -r "$f" ] || continue',
            '    value="$(sed -n \'s/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p\' "$f" 2>/dev/null | tail -1)"',
            '    [ -n "$value" ] && pip_index="$value"',
            "done",
            'printf "WSLMAN_PIP_INDEX=%s\\n" "$pip_index"',
            'printf "WSLMAN_UV_INDEX=%s\\n" "$(sed -n \'s/^index-url *= *"\\(.*\\)"/\\1/p\' "$HOME/.config/uv/uv.toml" 2>/dev/null | head -1)"',
            'printf "WSLMAN_NPM_REGISTRY=%s\\n" "$(sed -n \'s/^registry *= *//p\' "$HOME/.npmrc" 2>/dev/null | head -1)"',
            'printf "WSLMAN_BUN_REGISTRY=%s\\n" "$(sed -n \'s/^registry *= *"\\(.*\\)"/\\1/p\' "$HOME/.bunfig.toml" 2>/dev/null | head -1)"',
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
        uv=values.get("UV", ""),
        auto_uv_env=values.get("AUTO_UV_ENV", ""),
        node=values.get("NODE", ""),
        node_win=values.get("NODE_WIN", ""),
        nvm=values.get("NVM", ""),
        bun=values.get("BUN", ""),
        rust=values.get("RUST", ""),
        docker=values.get("DOCKER", ""),
        docker_mirror=values.get("DOCKER_MIRROR", ""),
        gui_libs=values.get("GUI_LIBS", ""),
        cjk_font=values.get("CJK_FONT", ""),
        noto_cjk=values.get("NOTO_CJK", ""),
        emoji_font=values.get("EMOJI_FONT", ""),
        pip_index=values.get("PIP_INDEX", ""),
        uv_index=values.get("UV_INDEX", ""),
        npm_registry=values.get("NPM_REGISTRY", ""),
        bun_registry=values.get("BUN_REGISTRY", ""),
    )


_STATUS_FIELDS: tuple[str, ...] = (
    "user",
    "home",
    "os_name",
    "mirror",
    "ssh_public_key",
    "python",
    "uv",
    "auto_uv_env",
    "node",
    "node_win",
    "nvm",
    "bun",
    "rust",
    "docker",
    "docker_mirror",
    "gui_libs",
    "cjk_font",
    "noto_cjk",
    "emoji_font",
    "pip_index",
    "uv_index",
    "npm_registry",
    "bun_registry",
)
"""``EnvStatus`` 可持久化字段；与设置文件里的缓存保持同一口径。"""


def status_to_mapping(status: EnvStatus) -> dict[str, str]:
    """把环境探测结果序列化成 JSON 友好的字符串映射（用于持久化上次结果）。"""
    return {name: str(getattr(status, name) or "") for name in _STATUS_FIELDS}


def status_from_mapping(raw: object) -> EnvStatus | None:
    """从设置文件里的缓存恢复环境结果；完全为空时返回 ``None``。"""
    if not isinstance(raw, Mapping):
        return None
    values = {name: raw.get(name) for name in _STATUS_FIELDS}
    if not any(isinstance(value, str) and value.strip() for value in values.values()):
        return None
    return EnvStatus(**{name: value.strip() if isinstance(value, str) else "" for name, value in values.items()})


def present(key: str, status: EnvStatus, package_mirror: str = PACKAGE_MIRROR_AUTO) -> bool:  # noqa: PLR0911 - 每个初始化项目独立判定，扁平分支最直观
    """判断某个初始化项目是否已就绪。

    每个项目只看自己那个工具：Python 已经有 python3 就算就绪（Kali / Ubuntu 这类
    发行版自带 python3，不应因为没装 uv 就显示未初始化）；uv / Bun / Node.js 各自
    独立成项，没装就单独出现在待初始化列表里。

    ``mirror`` 额外看软件包源：显式选了镜像时，系统源必须是同一家才算就绪，
    否则改了软件包源也不会重新换源（用户看到的「选了中科大却还走阿里云」）。
    """
    if key == "mirror":
        if not status.mirror:
            return False
        if normalize_package_mirror(package_mirror) == PACKAGE_MIRROR_AUTO:
            return True
        return system_mirror_source(package_mirror) in status.mirror.casefold()
    if key == "ssh":
        return status.ssh_ready
    if key == "python":
        return bool(status.python)
    if key == "uv":
        return bool(status.uv)
    if key == "auto_uv_env":
        return bool(status.auto_uv_env)
    if key == "node":
        return bool(status.node) and status.nvm == "yes"
    if key == "bun":
        return bool(status.bun)
    if key == "rust":
        return bool(status.rust)
    if key == "docker":
        return bool(status.docker)
    if key == "docker_mirror":
        return bool(status.docker_mirror)
    display = display_present(key, status)
    return display if display is not None else False


def default_checked(key: str, status: EnvStatus, package_mirror: str = PACKAGE_MIRROR_AUTO) -> bool:
    """默认勾选尚未完成的项目；探测失败（全空）时等于全选。

    ``noto`` / ``emoji`` 是可选大包，即使未安装也默认不勾，避免首跑多下载几十 MB。
    """
    if key in OPTIONAL_KEYS:
        return False
    return not present(key, status, package_mirror)


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


def _pair_value(primary: str, secondary: str) -> str:
    """pip / uv（或 npm / bun）镜像合成一行：一致时只显示一个，避免两行重复 URL。"""
    if primary and secondary and primary.rstrip("/") != secondary.rstrip("/"):
        return f"{primary} · {secondary}"
    return primary or secondary or "未配置"


def status_rows(status: EnvStatus) -> tuple[tuple[str, str, bool], ...]:
    """环境详情区逐项 ``(字段, 值, 是否就绪)``，供界面统一着色。"""
    node_ready = bool(status.node) and status.nvm == "yes"
    if not status.node:
        node_value = f"未安装（Windows 侧 v{status.node_win}，实例内不可用）" if status.node_win else "未安装"
    elif node_ready:
        node_value = f"{status.node}（nvm）"
    else:
        node_value = f"{status.node}（非 nvm）"
    return (
        ("系统镜像源", status.mirror or "未配置（仍为官方源）", bool(status.mirror)),
        ("SSH 密钥", ssh_key_short(status.ssh_public_key) if status.ssh_ready else "未生成", status.ssh_ready),
        ("Python", status.python or "未安装", bool(status.python)),
        ("uv", status.uv or "未安装", bool(status.uv)),
        ("auto-uv-env", status.auto_uv_env or "未安装", bool(status.auto_uv_env)),
        ("Node.js（nvm）", node_value, node_ready),
        ("Bun", status.bun or "未安装", bool(status.bun)),
        ("Rust", status.rust or "未安装", bool(status.rust)),
        ("Docker", status.docker or "未安装", bool(status.docker)),
        ("Docker 镜像加速", status.docker_mirror or "未配置", bool(status.docker_mirror)),
        (
            "pip / uv 镜像",
            _pair_value(status.pip_index, status.uv_index),
            bool(status.pip_index) and bool(status.uv_index),
        ),
        (
            "npm / bun 镜像",
            _pair_value(status.npm_registry, status.bun_registry),
            bool(status.npm_registry) and bool(status.bun_registry),
        ),
        *display_status_rows(status),
    )


def summary_text(status: EnvStatus) -> str:
    """一行摘要：``镜像源 ✓ · SSH ✗ · Python 3.12.3 / uv 0.5.14 · Node.js …``。"""
    python = f"{status.python} / uv {status.uv}" if status.python and status.uv else (status.python or "✗")
    if status.auto_uv_env:
        python = f"{python} / auto-uv-env {status.auto_uv_env}"
    node = f"{status.node} / bun {status.bun}" if status.node and status.bun else (status.node or "✗")
    parts = [
        f"镜像源 {'✓' if status.mirror else '✗'}",
        f"SSH {'✓' if status.ssh_ready else '✗'}",
        f"Python {python}",
        f"Node.js {node}",
        f"Rust {status.rust or '✗'}",
        f"Docker {status.docker or '✗'}",
        *display_summary_parts(status),
    ]
    return " · ".join(parts)
