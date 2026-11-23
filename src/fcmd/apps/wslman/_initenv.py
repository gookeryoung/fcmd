"""wslmanw「初始化环境」的纯逻辑。

把「勾选哪些项目、每个项目在实例里执行什么命令、如何判断当前状态」从 Qt
控件里独立出来：界面只负责展示与调度，单测覆盖这里的脚本拼装与结果解析。

执行口径与 :mod:`fcmd.tools.dev.envdev` 保持一致（国内系统镜像源用
linuxmirrors，pip / uv / npm / bun / cargo 走国内镜像），但这里通过
``wsl.exe -d <实例> -- bash -lc`` 在目标实例内执行，不要求实例内安装 fcmd。

国内加速口径（都是「直接吃国内镜像」，不在初始化时探测选优，避免拖长首跑时间）：

- APT：先跑 linuxmirrors 换系统源再装包；已装的包直接跳过，apt 索引 1 小时内
  有效则不再 ``apt-get update``，并统一 ``--no-install-recommends`` + 重试；
- Python / uv：pip 索引与 uv 默认索引都指向清华，uv 直接用 pip 装（不走
  astral.sh / GitHub），uv 的 Python 下载走 npmmirror 的 python-build-standalone；
- Node.js：nvm 源码走 gitee 镜像、node 二进制走 npmmirror 的 node 镜像，不再用
  apt 里那个版本很老的 ``nodejs``；
- Bun：走 ``npm i -g bun``（二进制随 ``@oven/bun-linux-x64`` 从 npmmirror 分发），
  不走 bun.sh 的 GitHub Releases；
- Rust：rustup 与 cargo 都走 rsproxy.cn。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
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
    "status_from_mapping",
    "status_rows",
    "status_to_mapping",
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
_UV_PYTHON_INSTALL_MIRROR = "https://registry.npmmirror.com/-/binary/python-build-standalone"
_NODE_MIRROR = "https://npmmirror.com/mirrors/node"
_NVM_GIT = "https://gitee.com/mirrors/nvm.git"
_NVM_GIT_FALLBACK = "https://github.com/nvm-sh/nvm.git"
"""国内加速用的固定镜像地址：nvm 源码走 gitee，node 二进制与 bun 走 npmmirror。"""

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
    uv: str = ""
    node: str = ""
    nvm: str = ""
    bun: str = ""
    rust: str = ""
    pip_index: str = ""
    uv_index: str = ""
    npm_registry: str = ""
    bun_registry: str = ""

    @property
    def ssh_ready(self) -> bool:
        return bool(self.ssh_public_key)


def _apt_script(*packages: str) -> str:
    """装包但尽量少干活：已装的跳过，apt 索引新鲜时不重复 update。"""
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
            "    apt-get update -o Acquire::Retries=3",
            "fi",
            "apt-get install -y --no-install-recommends -o Acquire::Retries=3 $missing",
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


def _pip_script() -> str:
    """pip 索引 → 清华镜像。"""
    return "\n".join(
        (
            "set -e",
            f"index='{_PIP_INDEX_URL}'",
            'python3 -m pip config set global.index-url "$index" >/dev/null',
            'echo "pip 镜像已配置：$index"',
        )
    )


def _uv_script() -> str:
    """装 uv（pip --user 走清华，不走 astral.sh / GitHub）并写 ``uv.toml`` 镜像。"""
    return "\n".join(
        (
            "set -e",
            f"index='{_PIP_INDEX_URL}'",
            'python3 -m pip config set global.index-url "$index" >/dev/null',
            'export PATH="$HOME/.local/bin:$PATH"',
            "if command -v uv >/dev/null 2>&1; then",
            '    echo "uv 已安装：$(uv --version)"',
            "else",
            '    echo "安装 uv（pip --user + 清华镜像，不走 GitHub）…"',
            '    python3 -m pip install --user -q --break-system-packages -i "$index" uv 2>/dev/null || \\',
            '        python3 -m pip install --user -q -i "$index" uv',
            "fi",
            'mkdir -p "$HOME/.config/uv"',
            'if grep -qs "index-url" "$HOME/.config/uv/uv.toml"; then',
            '    echo "uv 镜像已配置"',
            "else",
            '    if [ -f "$HOME/.config/uv/uv.toml" ]; then',
            '        cp "$HOME/.config/uv/uv.toml" "$HOME/.config/uv/uv.toml.bak"',
            "    fi",
            "    cat > \"$HOME/.config/uv/uv.toml\" <<'FCMD_UV'",
            f'index-url = "{_PIP_INDEX_URL}"',
            f'python-install-mirror = "{_UV_PYTHON_INSTALL_MIRROR}"',
            'link-mode = "copy"',
            "FCMD_UV",
            "fi",
            # login shell（``bash -lc``）才能测到 ~/.local/bin 里的 uv。
            'if [ -f "$HOME/.profile" ] && ! grep -q ".local/bin" "$HOME/.profile"; then',
            '    printf "\\nexport PATH=\\"\\$HOME/.local/bin:\\$PATH\\"\\n" >> "$HOME/.profile"',
            "fi",
            'echo "uv 已就绪：$(uv --version)"',
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
        "安装 python3 / pip / venv，并把 pip 索引指向清华镜像；已自带 python3 的发行版（如 Kali）装完直接视为就绪。",
        (
            InitStep("安装 Python 软件包", _apt_script("python3", "python3-pip", "python3-venv"), root=True),
            InitStep("配置 pip 镜像", _pip_script()),
        ),
    ),
    InitTask(
        "uv",
        "安装 uv",
        "用 pip 从清华镜像安装 uv（不走 astral.sh / GitHub），并写入 ~/.config/uv/uv.toml："
        "默认索引清华、Python 下载走 npmmirror 的 python-build-standalone。",
        (
            InitStep("安装 Python 软件包", _apt_script("python3", "python3-pip", "python3-venv"), root=True),
            InitStep("安装 uv 并配置镜像", _uv_script()),
        ),
    ),
    InitTask(
        "node",
        "安装 Node.js 开发环境",
        "用 nvm 安装 Node.js LTS（nvm 源码走 gitee 镜像、node 二进制走 npmmirror），npm registry 指向 npmmirror。",
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
)


def task_by_key(key: str) -> InitTask | None:
    return next((task for task in TASKS if task.key == key), None)


_TASK_SHORT: dict[str, str] = {
    "mirror": "镜像源",
    "ssh": "SSH 密钥",
    "python": "Python",
    "uv": "uv",
    "node": "Node.js",
    "bun": "Bun",
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
            'uv_bin="$(command -v uv 2>/dev/null)"',
            '[ -z "$uv_bin" ] && [ -x "$HOME/.local/bin/uv" ] && uv_bin="$HOME/.local/bin/uv"',
            'printf "WSLMAN_UV=%s\\n" "$([ -n "$uv_bin" ] && "$uv_bin" --version 2>/dev/null | awk \'{print $2}\')"',
            'node_bin="$(command -v node 2>/dev/null)"',
            '[ -z "$node_bin" ] && [ -x "$HOME/.local/bin/node" ] && node_bin="$HOME/.local/bin/node"',
            'printf "WSLMAN_NODE=%s\\n" "$([ -n "$node_bin" ] && "$node_bin" --version 2>/dev/null | sed \'s/^v//\')"',
            'printf "WSLMAN_NVM=%s\\n" "$([ -s "$HOME/.nvm/nvm.sh" ] && echo yes)"',
            'bun_bin="$(command -v bun 2>/dev/null)"',
            '[ -z "$bun_bin" ] && [ -x "$HOME/.local/bin/bun" ] && bun_bin="$HOME/.local/bin/bun"',
            'printf "WSLMAN_BUN=%s\\n" "$([ -n "$bun_bin" ] && "$bun_bin" --version 2>/dev/null)"',
            'printf "WSLMAN_RUST=%s\\n" "$(rustc --version 2>/dev/null | awk \'{print $2}\')"',
            'printf "WSLMAN_PIP_INDEX=%s\\n" "$(python3 -m pip config get global.index-url 2>/dev/null)"',
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
        node=values.get("NODE", ""),
        nvm=values.get("NVM", ""),
        bun=values.get("BUN", ""),
        rust=values.get("RUST", ""),
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
    "node",
    "nvm",
    "bun",
    "rust",
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


def present(key: str, status: EnvStatus) -> bool:  # noqa: PLR0911 - 每个初始化项目独立判定，扁平分支最直观
    """判断某个初始化项目是否已就绪。

    每个项目只看自己那个工具：Python 已经有 python3 就算就绪（Kali / Ubuntu 这类
    发行版自带 python3，不应因为没装 uv 就显示未初始化）；uv / Bun / Node.js 各自
    独立成项，没装就单独出现在待初始化列表里。
    """
    if key == "mirror":
        return bool(status.mirror)
    if key == "ssh":
        return status.ssh_ready
    if key == "python":
        return bool(status.python)
    if key == "uv":
        return bool(status.uv)
    if key == "node":
        return bool(status.node) and status.nvm == "yes"
    if key == "bun":
        return bool(status.bun)
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


def _pair_value(primary: str, secondary: str) -> str:
    """pip / uv（或 npm / bun）镜像合成一行：一致时只显示一个，避免两行重复 URL。"""
    if primary and secondary and primary.rstrip("/") != secondary.rstrip("/"):
        return f"{primary} · {secondary}"
    return primary or secondary or "未配置"


def status_rows(status: EnvStatus) -> tuple[tuple[str, str, bool], ...]:
    """环境详情区逐项 ``(字段, 值, 是否就绪)``，供界面统一着色。"""
    node_ready = bool(status.node) and status.nvm == "yes"
    if not status.node:
        node_value = "未安装"
    elif node_ready:
        node_value = f"{status.node}（nvm）"
    else:
        node_value = f"{status.node}（非 nvm）"
    return (
        ("系统镜像源", status.mirror or "未配置（仍为官方源）", bool(status.mirror)),
        ("SSH 密钥", ssh_key_short(status.ssh_public_key) if status.ssh_ready else "未生成", status.ssh_ready),
        ("Python", status.python or "未安装", bool(status.python)),
        ("uv", status.uv or "未安装", bool(status.uv)),
        ("Node.js（nvm）", node_value, node_ready),
        ("Bun", status.bun or "未安装", bool(status.bun)),
        ("Rust", status.rust or "未安装", bool(status.rust)),
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
    )


def summary_text(status: EnvStatus) -> str:
    """一行摘要：``镜像源 ✓ · SSH ✗ · Python 3.12.3 / uv 0.5.14 · Node.js …``。"""
    python = f"{status.python} / uv {status.uv}" if status.python and status.uv else (status.python or "✗")
    node = f"{status.node} / bun {status.bun}" if status.node and status.bun else (status.node or "✗")
    parts = [
        f"镜像源 {'✓' if status.mirror else '✗'}",
        f"SSH {'✓' if status.ssh_ready else '✗'}",
        f"Python {python}",
        f"Node.js {node}",
        f"Rust {status.rust or '✗'}",
    ]
    return " · ".join(parts)
