"""wslmanw「初始化环境」的纯逻辑。

把「勾选哪些项目、每个项目在实例里执行什么命令、如何判断当前状态」从 Qt
控件里独立出来：界面只负责展示与调度，单测覆盖这里的脚本拼装与结果解析。

执行口径与 :mod:`fcmd.apps.envdev.envdev` 保持一致（国内系统镜像源用
linuxmirrors，pip / uv / npm / bun / cargo 走国内镜像），但这里通过
``wsl.exe -d <实例> -- bash -lc`` 在目标实例内执行，不要求实例内安装 fcmd。

国内加速口径（都是「直接吃国内镜像」，不在初始化时探测选优，避免拖长首跑时间）：

- APT：先跑 linuxmirrors 换系统源再装包；已装的包直接跳过，apt 索引 1 小时内
  有效则不再 ``apt-get update``，并统一 ``--no-install-recommends`` + 重试；
- Python / uv：pip 索引与 uv 默认索引**跟随系统镜像源**（中科大 / 阿里 / 腾讯 /
  华为 / 清华等各自映射到对应 PyPI 镜像；识别不到时回退清华），所选软件包源同时
  决定 linuxmirrors 用哪家换系统源，uv 直接用 pip 装
  （不走 astral.sh / GitHub），uv 的 Python 下载走 npmmirror 的 python-build-standalone；
- Node.js：nvm 源码走 gitee 镜像、node 二进制走 npmmirror 的 node 镜像，不再用
  apt 里那个版本很老的 ``nodejs``；
- Bun：走 ``npm i -g bun``（二进制随 ``@oven/bun-linux-x64`` 从 npmmirror 分发），
  不走 bun.sh 的 GitHub Releases；
- Rust：rustup 与 cargo 都走 rsproxy.cn。

探测前会把 ``/mnt/*``（Windows 盘挂载点）从 PATH 摘掉：WSL 默认把 Windows 的
PATH 追加进实例，不摘的话没装 Linux 版 Node.js 也会被 ``command -v node`` 判成
已安装，用户接着用那个 npm 装的 ``pi`` 落在 Windows 侧，实例里根本跑不起来。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from fcmd.apps.wslman._initenv_display import (
    OPTIONAL_KEYS,
)
from fcmd.apps.wslman._initenv_display import (
    init_tasks as _display_tasks,
)
from fcmd.apps.wslman._initenv_pkg import (
    PACKAGE_MIRROR_AUTO,
    PACKAGE_MIRROR_CHOICES,
    PACKAGE_MIRROR_OFFICIAL,
    SYSTEM_MIRROR_DEFAULT,
    mirror_script,
    normalize_package_mirror,
    package_source_script,
    pip_script,
    pypi_index_for_mirror,
    pypi_index_for_package_mirror,
    system_mirror_source,
    uv_script,
)
from fcmd.apps.wslman._initenv_probe import (
    EnvStatus,
    default_checked,
    parse_status,
    present,
    probe_script,
    ssh_key_short,
    status_from_mapping,
    status_rows,
    status_to_mapping,
    summary_text,
    tail_lines,
)

__all__ = [
    "OPTIONAL_KEYS",
    "PACKAGE_MIRROR_AUTO",
    "PACKAGE_MIRROR_CHOICES",
    "PACKAGE_MIRROR_OFFICIAL",
    "TASKS",
    "EnvStatus",
    "InitStep",
    "InitTask",
    "default_checked",
    "normalize_package_mirror",
    "package_source_steps",
    "parse_status",
    "present",
    "probe_script",
    "pypi_index_for_mirror",
    "pypi_index_for_package_mirror",
    "short_label",
    "ssh_key_short",
    "status_from_mapping",
    "status_rows",
    "status_to_mapping",
    "steps_for",
    "summary_text",
    "system_mirror_source",
    "tail_lines",
    "task_by_key",
    "task_keys",
    "task_labels",
]

_NPM_REGISTRY = "https://registry.npmmirror.com"
_NODE_MIRROR = "https://npmmirror.com/mirrors/node"
_NVM_GIT = "https://gitee.com/mirrors/nvm.git"
_NVM_GIT_FALLBACK = "https://github.com/nvm-sh/nvm.git"
"""国内加速用的固定镜像地址：nvm 源码走 gitee，node 二进制与 bun 走 npmmirror。"""

_AUTO_UV_ENV_INSTALL = "https://auto-uv-env.ashwch.com/install.sh"
_AUTO_UV_ENV_INSTALL_FALLBACK = "https://raw.githubusercontent.com/ashwch/auto-uv-env/main/docs/install.sh"
"""auto-uv-env 官方安装脚本；域名不可达时回退 GitHub 上的同版本脚本。"""


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


_APT_LOCK_TIMEOUT = "-o DPkg::Lock::Timeout=600"
"""等锁时长（秒）：首跑常撞上 unattended-upgrades / apt-daily 持锁，直接失败很挫。"""


def _apt_script(*packages: str) -> str:
    """装包但尽量少干活：已装的跳过，apt 索引新鲜时不重复 update，遇锁排队等。"""
    wanted = " ".join(packages)
    return "\n".join(
        (
            "set -e",
            "export DEBIAN_FRONTEND=noninteractive",
            'missing=""',
            f"for pkg in {wanted}; do",
            '    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"',
            "done",
            'if [ -z "$missing" ]; then',
            f'    echo "软件包已安装：{wanted}"',
            "    exit 0",
            "fi",
            'echo "需要安装：$missing"',
            "if ! find /var/lib/apt/lists -maxdepth 1 -name '*_Packages*' -newermt '-1 hour' -print -quit | grep -q .; then",
            f"    apt-get update -o Acquire::Retries=3 -o Acquire::Languages=none {_APT_LOCK_TIMEOUT}",
            "fi",
            "apt-get install -y --no-install-recommends -o Acquire::Retries=3 "
            f"-o Acquire::Languages=none -o Dpkg::Use-Pty=0 {_APT_LOCK_TIMEOUT} $missing",
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


def _rc_lines() -> tuple[str, ...]:
    """把 nvm 与 ``~/.local/bin`` 幂等写进 login / interactive shell 的 rc 文件。"""
    return (
        "add_rc() {",
        '    file="$1"',
        '    [ -e "$file" ] || touch "$file"',
        '    if grep -qF "fcmd wslman init" "$file" 2>/dev/null; then',
        "        return 0",
        "    fi",
        "    cat >> \"$file\" <<'FCMD_RC'",
        "# >>> fcmd wslman init >>>",
        'export NVM_DIR="$HOME/.nvm"',
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1',
        'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac',
        "# <<< fcmd wslman init <<<",
        "FCMD_RC",
        "}",
        'add_rc "$HOME/.bashrc"',
        'add_rc "$HOME/.profile"',
        'if [ -e "$HOME/.zshrc" ]; then add_rc "$HOME/.zshrc"; fi',
    )


def _nvm_lines() -> tuple[str, ...]:
    """nvm + Node.js LTS：nvm 源码走 gitee、node 二进制走 npmmirror。

    已经装过就不重装（``nvm which default`` 能拿到路径即视为就绪），并把 node / npm
    软链到 ``~/.local/bin``，让不读 rc 的 ``bash -lc`` 也能测到。
    """
    return (
        'export NVM_DIR="$HOME/.nvm"',
        'if [ ! -s "$NVM_DIR/nvm.sh" ]; then',
        '    echo "安装 nvm（gitee 镜像，避免 GitHub 超时）…"',
        '    if [ -d "$NVM_DIR" ]; then rm -rf "$NVM_DIR"; fi',
        f'    if ! git clone --depth 1 {_NVM_GIT} "$NVM_DIR" 2>/dev/null; then',
        '        echo "gitee 镜像不可用，回退 GitHub…"',
        f'        git clone --depth 1 {_NVM_GIT_FALLBACK} "$NVM_DIR"',
        "    fi",
        "fi",
        '. "$NVM_DIR/nvm.sh"',
        f"export NVM_NODEJS_ORG_MIRROR={_NODE_MIRROR}",
        "if ! nvm which default >/dev/null 2>&1; then",
        '    echo "安装 Node.js LTS（npmmirror 镜像）…"',
        "    nvm install --lts >/dev/null",
        "    nvm alias default 'lts/*' >/dev/null",
        "fi",
        'node_bin="$(dirname "$(nvm which default)")"',
        'export PATH="$node_bin:$PATH"',
        'mkdir -p "$HOME/.local/bin"',
        "for name in node npm npx corepack; do",
        '    if [ -x "$node_bin/$name" ]; then',
        '        ln -sf "$node_bin/$name" "$HOME/.local/bin/$name"',
        "    fi",
        "done",
    )


def _node_script() -> str:
    """nvm（gitee）+ Node.js LTS（npmmirror）+ npm 镜像。"""
    return "\n".join(
        (
            "set -e",
            *_nvm_lines(),
            f"npm config set registry {_NPM_REGISTRY} >/dev/null",
            *_rc_lines(),
            'echo "Node.js $(node --version)（nvm）已就绪"',
        )
    )


def _bun_script() -> str:
    """Bun：走 npm 包（二进制随 ``@oven/bun-linux-x64`` 从 npmmirror 分发）。

    缺 Node.js 时先用 nvm 装好（同一套国内镜像），不把失败丢给用户。
    """
    return "\n".join(
        (
            "set -e",
            *_nvm_lines(),
            'if [ ! -x "$node_bin/bun" ]; then',
            '    echo "安装 Bun（npm 包 + npmmirror，不走 GitHub Releases）…"',
            f"    npm install -g bun --registry {_NPM_REGISTRY} --no-fund --no-audit",
            "fi",
            'if ! grep -qs "registry" "$HOME/.bunfig.toml"; then',
            "    cat >> \"$HOME/.bunfig.toml\" <<'FCMD_BUN'",
            "[install]",
            f'registry = "{_NPM_REGISTRY}"',
            "FCMD_BUN",
            "fi",
            'if [ -x "$node_bin/bun" ]; then ln -sf "$node_bin/bun" "$HOME/.local/bin/bun"; fi',
            'if [ -x "$node_bin/bunx" ]; then ln -sf "$node_bin/bunx" "$HOME/.local/bin/bunx"; fi',
            *_rc_lines(),
            'echo "Bun $(bun --version 2>/dev/null) 已就绪"',
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


def _auto_uv_env_rc_lines() -> tuple[str, ...]:
    """把 auto-uv-env 的 shell 集成幂等写进 rc 文件。

    官方安装脚本会自己追加一段 ``# auto-uv-env``；这里只在完全没配置时补
    ``~/.local/share/auto-uv-env/env``，避免重复 source 两遍 hook。
    """
    return (
        "add_auto_uv_env_rc() {",
        '    file="$1"',
        '    [ -e "$file" ] || touch "$file"',
        '    if grep -qF "auto-uv-env" "$file" 2>/dev/null; then',
        "        return 0",
        "    fi",
        "    cat >> \"$file\" <<'FCMD_AUTO_UV_ENV'",
        "# >>> fcmd auto-uv-env >>>",
        '[ -f "$HOME/.local/share/auto-uv-env/env" ] && . "$HOME/.local/share/auto-uv-env/env"',
        "# <<< fcmd auto-uv-env <<<",
        "FCMD_AUTO_UV_ENV",
        "}",
        'add_auto_uv_env_rc "$HOME/.bashrc"',
        'add_auto_uv_env_rc "$HOME/.profile"',
        'if [ -e "$HOME/.zshrc" ]; then add_auto_uv_env_rc "$HOME/.zshrc"; fi',
    )


def _auto_uv_env_script() -> str:
    """安装 auto-uv-env：uv 就绪后装，官方脚本不可达时回退 GitHub raw。

    auto-uv-env 负责在进入含 ``pyproject.toml`` 的目录时用 uv 自动建 / 激活
    ``.venv``；这里只装二进制与 shell 集成，不修改 ``uv.toml``。
    """
    return "\n".join(
        (
            "set -e",
            'export PATH="$HOME/.local/bin:$PATH"',
            "if command -v auto-uv-env >/dev/null 2>&1; then",
            '    echo "auto-uv-env 已安装：$(auto-uv-env --version 2>/dev/null)"',
            "else",
            '    echo "安装 auto-uv-env（uv 已就绪）…"',
            '    tmp="$(mktemp)"',
            f'    if ! curl -fsSL {_AUTO_UV_ENV_INSTALL} -o "$tmp" 2>/dev/null; then',
            '        echo "官方安装地址不可用，回退 GitHub…"',
            f'        curl -fsSL {_AUTO_UV_ENV_INSTALL_FALLBACK} -o "$tmp"',
            "    fi",
            '    sh "$tmp"',
            '    rm -f "$tmp"',
            "fi",
            'mkdir -p "$HOME/.local/bin"',
            *_auto_uv_env_rc_lines(),
            'echo "auto-uv-env 已就绪：$(auto-uv-env --version 2>/dev/null)"',
        )
    )


TASKS: tuple[InitTask, ...] = (
    InitTask(
        "mirror",
        "运行国内源配置脚本",
        f"通过 linuxmirrors 把 APT / YUM 系统源切换到国内镜像（默认 {SYSTEM_MIRROR_DEFAULT}；"
        "选择了指定软件包源时改用同一家镜像），以 root 执行；已配置时重复执行也是安全的。",
        (InitStep("配置系统镜像源", mirror_script(), root=True, timeout_s=900.0),),
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
        "安装 python3 / pip / venv，pip 索引按「软件包源」配置（自动＝跟随系统镜像源）；已自带 python3 的发行版（如 Kali）装完直接视为就绪。",
        (
            InitStep("安装 Python 软件包", _apt_script("python3", "python3-pip", "python3-venv"), root=True),
            InitStep("配置 pip 镜像", pip_script()),
        ),
    ),
    InitTask(
        "uv",
        "安装 uv",
        "用 pip 从软件包源安装 uv（不走 astral.sh / GitHub），并更新 ~/.config/uv/uv.toml："
        "索引按「软件包源」配置（自动＝跟随系统镜像源）、Python 下载走 npmmirror 的 python-build-standalone。",
        (
            InitStep("安装 Python 软件包", _apt_script("python3", "python3-pip", "python3-venv"), root=True),
            InitStep("安装 uv 并配置镜像", uv_script()),
        ),
    ),
    InitTask(
        "auto_uv_env",
        "安装 auto-uv-env",
        "在 uv 之后安装 auto-uv-env：进入含 pyproject.toml 的目录时自动用 uv 创建并激活 .venv；"
        "官方安装脚本不可达时回退 GitHub，并把 shell 集成写入 rc 文件。",
        (
            InitStep("安装 auto-uv-env 依赖", _apt_script("curl", "ca-certificates", "tar"), root=True),
            InitStep("安装 auto-uv-env", _auto_uv_env_script()),
        ),
    ),
    InitTask(
        "node",
        "安装 Node.js 开发环境",
        "用 nvm 安装 Node.js LTS（nvm 源码走 gitee 镜像、node 二进制走 npmmirror），npm registry 指向 npmmirror。"
        "Windows 侧的 node / npm（/mnt 下）不算实例内已安装；装好后 npm install -g 的包才会落在实例里。",
        (
            InitStep("安装 Node.js 依赖", _apt_script("curl", "git", "ca-certificates"), root=True),
            InitStep("安装 nvm / Node.js", _node_script()),
        ),
    ),
    InitTask(
        "bun",
        "安装 Bun",
        "通过 npm 包安装 Bun（二进制随 @oven/bun-linux-x64 从 npmmirror 分发，不走 GitHub "
        "Releases），写入 ~/.bunfig.toml 镜像；缺 Node.js 时先用 nvm 装好。",
        (
            InitStep("安装 Node.js 依赖", _apt_script("curl", "git", "ca-certificates"), root=True),
            InitStep("安装 Bun 并配置镜像", _bun_script()),
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
    *_display_tasks(apt_script=_apt_script, step=InitStep, task=InitTask),
)


def task_by_key(key: str) -> InitTask | None:
    return next((task for task in TASKS if task.key == key), None)


_TASK_SHORT: dict[str, str] = {
    "mirror": "镜像源",
    "ssh": "SSH 密钥",
    "python": "Python",
    "uv": "uv",
    "auto_uv_env": "auto-uv-env",
    "node": "Node.js",
    "bun": "Bun",
    "rust": "Rust",
    "gui": "显示库",
    "fonts": "字体",
    "noto": "Noto",
    "emoji": "Emoji",
}


def short_label(key: str) -> str:
    """项目短名：收起态把已初始化情况挤在一行标签里时用。"""
    return _TASK_SHORT.get(key, key)


def package_source_steps(package_mirror: str = PACKAGE_MIRROR_AUTO) -> tuple[InitStep, ...]:
    """只更新 pip / uv 镜像配置的步骤（不安装软件、不改系统源）。"""
    mirror = normalize_package_mirror(package_mirror)
    return (InitStep("配置软件包镜像", package_source_script(mirror)),)


def steps_for(keys: Sequence[str], *, package_mirror: str = PACKAGE_MIRROR_AUTO) -> tuple[InitStep, ...]:
    """按 :data:`TASKS` 的固定顺序展开勾选项对应的执行步骤。

    ``package_mirror`` 决定 pip / uv 的索引，也决定 APT / YUM 换源用哪家镜像
    （``auto`` / ``official`` 用默认阿里云，与 :data:`TASKS` 里的口径一致）。
    """
    selected = set(keys)
    mirror = normalize_package_mirror(package_mirror)
    steps: list[InitStep] = []
    for task in TASKS:
        if task.key not in selected:
            continue
        if task.key == "mirror":
            # 换源域名跟着软件包源走，避免选了中科大却还用阿里云换系统源。
            steps.append(
                InitStep("配置系统镜像源", mirror_script(system_mirror_source(mirror)), root=True, timeout_s=900.0)
            )
        elif task.key == "python":
            steps.extend((task.steps[0], InitStep("配置 pip 镜像", pip_script(mirror))))
        elif task.key == "uv":
            steps.extend((task.steps[0], InitStep("安装 uv 并配置镜像", uv_script(mirror))))
        else:
            steps.extend(task.steps)
    return tuple(steps)


def task_keys() -> tuple[str, ...]:
    """全部可初始化项目的 key（按固定顺序）。"""
    return tuple(task.key for task in TASKS)


def task_labels(keys: Sequence[str]) -> tuple[str, ...]:
    selected = set(keys)
    return tuple(task.label for task in TASKS if task.key in selected)
