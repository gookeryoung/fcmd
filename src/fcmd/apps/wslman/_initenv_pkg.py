"""初始化环境的「软件包源」：pip / uv 的 PyPI 索引与 uv.toml 写入。

这部分只负责 PyPI 索引：``auto`` 时按当前系统源映射，显式选择时直接使用对应 PyPI。
系统源（APT / YUM）换源用哪个域名由 :func:`fcmd.apps.wslman._initenv.system_mirror_source`
从同一个软件包源推出来，不再固定阿里云。脚本拼装只生成 shell 文本，不依赖 Qt / 外部命令。
"""

from __future__ import annotations

__all__ = [
    "PACKAGE_MIRROR_AUTO",
    "PACKAGE_MIRROR_CHOICES",
    "PACKAGE_MIRROR_OFFICIAL",
    "index_detect_lines",
    "normalize_package_mirror",
    "package_source_script",
    "pip_config_lines",
    "pip_script",
    "pypi_index_for_mirror",
    "pypi_index_for_package_mirror",
    "source_mirror_lines",
    "uv_config_lines",
    "uv_script",
]

PACKAGE_MIRROR_AUTO = "auto"  # 软件包源：pip / uv 跟随系统镜像源（兼容旧行为）。
PACKAGE_MIRROR_OFFICIAL = "official"  # 软件包源：官方 PyPI。
PACKAGE_MIRROR_CHOICES: tuple[tuple[str, str], ...] = (
    (PACKAGE_MIRROR_AUTO, "自动（跟随系统镜像源）"),
    ("mirrors.tuna.tsinghua.edu.cn", "清华 TUNA"),
    ("mirrors.ustc.edu.cn", "中科大 USTC"),
    ("mirrors.aliyun.com", "阿里云"),
    ("mirrors.tencent.com", "腾讯云"),
    ("mirrors.huaweicloud.com", "华为云"),
    (PACKAGE_MIRROR_OFFICIAL, "官方 PyPI"),
)
"""初始化环境的「软件包源」选项：pip / uv 索引按它配置，APT / YUM 换源也用同一家。"""

_PACKAGE_MIRROR_KEYS = frozenset(key for key, _label in PACKAGE_MIRROR_CHOICES)

_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
_PYPI_INDEX_BY_MIRROR: dict[str, str] = {
    "mirrors.aliyun.com": "https://mirrors.aliyun.com/pypi/simple/",
    "mirrors.tencent.com": "https://mirrors.cloud.tencent.com/pypi/simple",
    "mirrors.huaweicloud.com": "https://repo.huaweicloud.com/repository/pypi/simple",
    "mirrors.ustc.edu.cn": "https://mirrors.ustc.edu.cn/pypi/simple",
    "mirrors.tuna.tsinghua.edu.cn": _PIP_INDEX_URL,
    "mirrors.163.com": "https://mirrors.163.com/pypi/simple/",
    "mirrors.zju.edu.cn": "https://mirrors.zju.edu.cn/pypi/web/simple",
    "mirrors.nju.edu.cn": "https://mirrors.nju.edu.cn/pypi/web/simple",
    "mirror.sjtu.edu.cn": "https://mirror.sjtu.edu.cn/pypi/web/simple",
}
"""系统镜像域名 → 对应 PyPI 索引；与 ``_initenv._MIRROR_DOMAINS`` 同一套识别口径。"""

_UV_PYTHON_INSTALL_MIRROR = "https://registry.npmmirror.com/-/binary/python-build-standalone"


def pypi_index_for_mirror(mirror: str) -> str:
    """按系统镜像域名选对应 PyPI 索引；未知 / 未配置时回退清华。"""
    key = (mirror or "").strip().casefold()
    for domain, index in _PYPI_INDEX_BY_MIRROR.items():
        if domain in key:
            return index
    return _PIP_INDEX_URL


def normalize_package_mirror(value: str) -> str:
    """规范化软件包源 key；未知值回退 ``auto``（跟随系统镜像源）。"""
    key = (value or "").strip().casefold()
    return key if key in _PACKAGE_MIRROR_KEYS else PACKAGE_MIRROR_AUTO


def pypi_index_for_package_mirror(mirror: str) -> str:
    """软件包源 → PyPI 索引；``auto`` 返回空串（由脚本检测系统源），无匹配回退清华。"""
    key = normalize_package_mirror(mirror)
    if key == PACKAGE_MIRROR_AUTO:
        return ""
    if key == PACKAGE_MIRROR_OFFICIAL:
        return "https://pypi.org/simple"
    return _PYPI_INDEX_BY_MIRROR.get(key, _PIP_INDEX_URL)


def source_mirror_lines() -> tuple[str, ...]:
    """检测**当前启用**的系统源并写入 ``mirror``。

    先让 apt 自己报告启用的索引 URI（``apt-get indextargets``），这样注释掉的旧行、
    ``.sources`` 里的 ``Enabled: no`` 与备份文件都不会被误认成当前源；没有 apt 时
    再按文件解析，且跳过注释行 / 未启用段落。
    """
    domains = " ".join(_PYPI_INDEX_BY_MIRROR)
    return (
        'mirror=""',
        "if command -v apt-get >/dev/null 2>&1; then",
        "    apt_uris=\"$(apt-get indextargets --format '$(URI)' 2>/dev/null)\"",
        f"    for domain in {domains}; do",
        '        if echo "$apt_uris" | grep -q "$domain"; then',
        '            mirror="$domain"',
        "            break",
        "        fi",
        "    done",
        "fi",
        'if [ -z "$mirror" ]; then',
        "    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/yum.repos.d/*.repo; do",
        '        [ -r "$file" ] || continue',
        '        case "$file" in',
        "            *.list) active=\"$(grep -vE '^[[:space:]]*#' \"$file\" | grep -E '^[[:space:]]*deb(-src)?[[:space:]]')\";;",
        '            *.sources) active="$(awk \'BEGIN{RS=""} !/Enabled:[[:space:]]*no/\' "$file")";;',
        "            *.repo) active=\"$(grep -vE '^[[:space:]]*(#|[[])' \"$file\" | grep -E '^[[:space:]]*(baseurl|metalink|mirrorlist)[[:space:]]*=')\";;",
        '            *) active="" ;;',
        "        esac",
        f"        for domain in {domains}; do",
        '            if echo "$active" | grep -q "$domain"; then',
        '                mirror="$domain"',
        "                break 2",
        "            fi",
        "        done",
        "    done",
        "fi",
    )


def index_detect_lines(package_mirror: str = PACKAGE_MIRROR_AUTO) -> tuple[str, ...]:
    """写入 shell 变量 ``index``：固定软件包源，或按系统镜像检测。"""
    fixed = pypi_index_for_package_mirror(package_mirror)
    if fixed:
        return (f"index='{fixed}'",)
    cases = tuple(f"  {domain}) index='{index}' ;;" for domain, index in _PYPI_INDEX_BY_MIRROR.items())
    return (
        *source_mirror_lines(),
        'case "$mirror" in',
        *cases,
        f"  *) index='{_PIP_INDEX_URL}' ;;",
        "esac",
    )


def uv_config_lines() -> tuple[str, ...]:
    """更新 ``uv.toml``：备份旧文件，重写 index-url / python-install-mirror。"""
    return (
        'mkdir -p "$HOME/.config/uv"',
        'uv_toml="$HOME/.config/uv/uv.toml"',
        'tmp="$uv_toml.tmp"',
        'if [ -f "$uv_toml" ]; then',
        '    cp "$uv_toml" "$uv_toml.bak"',
        '    grep -vE \'^[[:space:]]*(index-url|python-install-mirror)[[:space:]]*=\' "$uv_toml" > "$tmp" || true',
        "else",
        '    : > "$tmp"',
        "fi",
        'printf \'index-url = "%s"\\npython-install-mirror = "%s"\\n\' "$index" '
        f'"{_UV_PYTHON_INSTALL_MIRROR}" >> "$tmp"',
        'grep -qE \'^[[:space:]]*link-mode[[:space:]]*=\' "$tmp" || printf \'link-mode = "copy"\\n\' >> "$tmp"',
        'mv "$tmp" "$uv_toml"',
    )


def pip_config_lines() -> tuple[str, ...]:
    """直接写 pip 用户配置（不依赖 pip 本体）。

    Ubuntu / Debian 的 WSL 镜像自带 python3 却不带 pip，``python3 -m pip config
    set`` 会直接报 ``No module named pip``。这里用 awk 在 ``[global]`` 段内就地
    替换 ``index-url``：已有配置先备份，其它键与段落原样保留；优先复用已存在的
    ``~/.pip/pip.conf``（envdev 的口径），否则写 XDG 的 ``~/.config/pip/pip.conf``。
    """
    return (
        'pip_conf="$HOME/.config/pip/pip.conf"',
        '[ -f "$HOME/.pip/pip.conf" ] && pip_conf="$HOME/.pip/pip.conf"',
        'mkdir -p "$(dirname "$pip_conf")"',
        'pip_tmp="$pip_conf.tmp"',
        "# 首次运行配置文件还不存在：备份的同时给 awk 一个可读的空文件。",
        'if [ -f "$pip_conf" ]; then',
        '    cp "$pip_conf" "$pip_conf.bak"',
        "else",
        '    : > "$pip_conf"',
        "fi",
        'awk -v idx="$index" \'',
        "BEGIN { done = 0; inglobal = 0 }",
        '{ sub(/\\r$/, "") }',
        "/^\\[global\\][[:space:]]*$/ {",
        '    print "[global]"',
        '    if (!done) { print "index-url = " idx; done = 1 }',
        "    inglobal = 1",
        "    next",
        "}",
        "/^[[:space:]]*\\[[^]]*\\][[:space:]]*$/ { inglobal = 0 }",
        "inglobal && /^[[:space:]]*index-url[[:space:]]*=/ { next }",
        "{ print }",
        "END {",
        '    if (!done) { print "[global]"; print "index-url = " idx }',
        "}",
        '\' "$pip_conf" > "$pip_tmp"',
        'mv "$pip_tmp" "$pip_conf"',
    )


def pip_script(package_mirror: str = PACKAGE_MIRROR_AUTO) -> str:
    """pip 索引 → 软件包源（自动时跟随系统镜像源）。"""
    return "\n".join(
        (
            "set -e",
            *index_detect_lines(package_mirror),
            *pip_config_lines(),
            'echo "pip 镜像已配置：$index"',
        )
    )


def uv_script(package_mirror: str = PACKAGE_MIRROR_AUTO) -> str:
    """装 uv（pip --user 走软件包源，不走 astral.sh / GitHub）并更新 ``uv.toml``。"""
    return "\n".join(
        (
            "set -e",
            *index_detect_lines(package_mirror),
            *pip_config_lines(),
            'export PATH="$HOME/.local/bin:$PATH"',
            "if ! python3 -m pip --version >/dev/null 2>&1; then",
            '    echo "缺少 pip：先勾选「安装 Python 开发环境」，或在实例内执行 sudo apt-get install -y python3-pip" >&2',
            "    exit 1",
            "fi",
            "if command -v uv >/dev/null 2>&1; then",
            '    echo "uv 已安装：$(uv --version)"',
            "else",
            '    echo "安装 uv（pip --user + 软件包源，不走 GitHub）…"',
            '    python3 -m pip install --user -q --break-system-packages -i "$index" uv 2>/dev/null || \\',
            '        python3 -m pip install --user -q -i "$index" uv',
            "fi",
            *uv_config_lines(),
            'if [ -f "$HOME/.profile" ] && ! grep -q ".local/bin" "$HOME/.profile"; then',
            '    printf "\\nexport PATH=\\"\\$HOME/.local/bin:\\$PATH\\"\\n" >> "$HOME/.profile"',
            "fi",
            'echo "uv 已就绪：$(uv --version)"',
        )
    )


def package_source_script(package_mirror: str = PACKAGE_MIRROR_AUTO) -> str:
    """只更新 pip / uv 的镜像配置：不安装 uv、不碰系统源。"""
    return "\n".join(
        (
            "set -e",
            *index_detect_lines(package_mirror),
            *pip_config_lines(),
            'echo "pip 镜像已配置：$index"',
            *uv_config_lines(),
            'echo "软件包镜像已配置：$index"',
        )
    )
