"""wslmanw「初始化环境」纯逻辑测试：任务定义、脚本口径、探测解析与状态判断。"""

from __future__ import annotations

import base64

from fcmd.apps.wslman import _initenv


def test_tasks_cover_required_items() -> None:
    keys = [task.key for task in _initenv.TASKS]
    # uv / Bun 各自独立成项：没有 uv 不能让「Python」显示未初始化。
    assert keys == [
        "mirror",
        "ssh",
        "python",
        "uv",
        "auto_uv_env",
        "node",
        "bun",
        "rust",
        "gui",
        "fonts",
        "noto",
        "emoji",
    ]
    assert _initenv.task_by_key("python") is not None
    assert _initenv.task_by_key("uv") is not None
    assert _initenv.task_by_key("auto_uv_env") is not None
    assert _initenv.task_by_key("bun") is not None
    assert _initenv.task_by_key("gui") is not None
    assert _initenv.task_by_key("fonts") is not None
    assert _initenv.task_by_key("noto") is not None
    assert _initenv.task_by_key("emoji") is not None
    assert _initenv.task_by_key("missing") is None


def test_short_label_covers_all_tasks_and_falls_back() -> None:
    assert tuple(_initenv.short_label(task.key) for task in _initenv.TASKS) == (
        "镜像源",
        "SSH 密钥",
        "Python",
        "uv",
        "auto-uv-env",
        "Node.js",
        "Bun",
        "Rust",
        "显示库",
        "字体",
        "Noto",
        "Emoji",
    )
    assert _initenv.short_label("unknown") == "unknown"


def test_steps_for_keeps_task_order_and_root_flags() -> None:
    steps = _initenv.steps_for(["rust", "mirror"])
    assert [step.label for step in steps] == ["配置系统镜像源", "安装编译依赖", "安装 Rust 工具链"]
    assert steps[0].root is True
    assert steps[1].root is True
    assert steps[2].root is False
    assert _initenv.task_labels(["rust", "mirror"]) == ("运行国内源配置脚本", "安装 Rust 开发环境")


def test_mirror_script_contains_requested_command_and_non_interactive_flags() -> None:
    task = _initenv.task_by_key("mirror")
    assert task is not None
    script = task.steps[0].script
    assert "bash <(curl -fsSL --connect-timeout 15 --retry 2 --retry-delay 2 https://linuxmirrors.cn/main.sh)" in script
    assert "--source mirrors.aliyun.com" in script
    assert "--ignore-backup-tips" in script
    assert task.steps[0].root is True


def test_apt_and_mirror_scripts_wait_for_apt_lock() -> None:
    """首跑常撞上 unattended-upgrades / apt-daily 持锁：要排队等，而不是直接报 100。"""
    apt = _initenv.steps_for(["ssh"])[0].script
    assert apt.count("DPkg::Lock::Timeout=600") >= 2
    mirror = _initenv.steps_for(["mirror"])[0].script
    # linuxmirrors 内部也调 apt-get：等锁超时必须落到 apt 配置里才生效。
    assert "/etc/apt/apt.conf.d/99fcmd-lock-timeout" in mirror
    assert 'DPkg::Lock::Timeout "600";' in mirror
    assert "apt-get update -o DPkg::Lock::Timeout=600" in mirror
    assert "apt-get install -y curl -o DPkg::Lock::Timeout=600" in mirror


def test_ssh_script_is_ed25519_and_keeps_existing_key() -> None:
    steps = _initenv.steps_for(["ssh"])
    assert steps[0].root is True
    assert "openssh-client" in steps[0].script
    script = steps[1].script
    assert "ssh-keygen -t ed25519" in script
    assert 'key="$HOME/.ssh/id_ed25519"' in script
    assert '[ -f "$key" ]' in script
    assert 'cat "$key.pub"' in script


def test_language_scripts_use_domestic_mirrors() -> None:
    python_steps = _initenv.steps_for(["python"])
    uv_steps = _initenv.steps_for(["uv"])
    node_steps = _initenv.steps_for(["node"])
    bun_steps = _initenv.steps_for(["bun"])
    rust_steps = _initenv.steps_for(["rust"])
    assert "python3-pip" in python_steps[0].script
    # pip / uv 不再写死清华：按系统源映射，中科大源必须命中中科大 PyPI。
    assert "mirrors.ustc.edu.cn) index='https://mirrors.ustc.edu.cn/pypi/simple'" in python_steps[1].script
    assert "mirrors.ustc.edu.cn) index='https://mirrors.ustc.edu.cn/pypi/simple'" in uv_steps[1].script
    auto_steps = _initenv.steps_for(["auto_uv_env"])
    assert "auto-uv-env.ashwch.com/install.sh" in auto_steps[1].script
    assert "raw.githubusercontent.com/ashwch/auto-uv-env" in auto_steps[1].script
    assert "auto-uv-env --version" in auto_steps[1].script
    assert "# >>> fcmd auto-uv-env >>>" in auto_steps[1].script
    # 自动检测先让 apt 报告启用的 URI，避免注释 / 备份 / disabled 源里的旧域名被误判。
    assert "apt-get indextargets --format '$(URI)'" in python_steps[1].script
    assert "!/Enabled:[[:space:]]*no/" in python_steps[1].script
    assert "grep -vE '^[[:space:]]*#'" in python_steps[1].script
    assert 'grep -q "$domain" "$file"' not in python_steps[1].script
    assert "pypi.tuna.tsinghua.edu.cn" in python_steps[1].script  # 仅作为识别不到时的回退
    # Node.js 用 nvm + 国内镜像：不再用 apt 的 nodejs，源码 / 二进制都走国内。
    assert "nodejs" not in node_steps[0].script
    assert "curl git ca-certificates" in node_steps[0].script
    assert "gitee.com/mirrors/nvm.git" in node_steps[1].script
    assert "NVM_NODEJS_ORG_MIRROR=https://npmmirror.com/mirrors/node" in node_steps[1].script
    # Bun 自已顾自己做不依赖：缺 Node.js 时先用同一套 nvm 镜装修好。
    assert "gitee.com/mirrors/nvm.git" in bun_steps[1].script
    assert "-g bun --registry https://registry.npmmirror.com" in bun_steps[1].script
    assert "rsproxy.cn" in rust_steps[1].script
    assert "crates-io" in rust_steps[1].script


def test_uv_script_installs_uv_without_github_and_updates_existing_index() -> None:
    script = _initenv.steps_for(["uv"])[1].script
    assert "pip install --user" in script
    assert "--break-system-packages" in script
    assert "astral.sh" not in script
    assert "$HOME/.config/uv/uv.toml" in script
    assert 'python-install-mirror = "%s"' in script
    assert "https://registry.npmmirror.com/-/binary/python-build-standalone" in script
    # 已有 uv.toml 也必须更新 index-url（旧版发现 index-url 就跳过，切到中科大源后仍用清华）。
    assert 'cp "$uv_toml" "$uv_toml.bak"' in script
    assert "grep -vE '^[[:space:]]*(index-url|python-install-mirror)" in script
    assert 'mv "$tmp" "$uv_toml"' in script
    assert 'echo "uv 镜像已配置"' not in script
    # login shell 才能找到 ~/.local/bin/uv，且只补一次。
    assert 'grep -q ".local/bin" "$HOME/.profile"' in script


def test_pypi_index_follows_system_mirror() -> None:
    """系统镜像域名 → PyPI 索引：中科大 / 阿里 / 清华各用各自的地址，未知回退清华。"""
    assert _initenv.pypi_index_for_mirror("mirrors.ustc.edu.cn") == "https://mirrors.ustc.edu.cn/pypi/simple"
    assert _initenv.pypi_index_for_mirror("https://mirrors.ustc.edu.cn/ubuntu") == (
        "https://mirrors.ustc.edu.cn/pypi/simple"
    )
    assert _initenv.pypi_index_for_mirror("MIRRORS.ALIYUN.COM") == "https://mirrors.aliyun.com/pypi/simple/"
    assert _initenv.pypi_index_for_mirror("mirrors.tuna.tsinghua.edu.cn") == (
        "https://pypi.tuna.tsinghua.edu.cn/simple"
    )
    assert _initenv.pypi_index_for_mirror("") == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert _initenv.pypi_index_for_mirror("example.com") == "https://pypi.tuna.tsinghua.edu.cn/simple"


def test_package_mirror_fixes_pypi_index_without_detection() -> None:
    """软件包源可单独指定：不选自动时 pip / uv 用指定 PyPI，不再读系统源。"""
    template = _initenv.steps_for(["uv"], package_mirror="mirrors.ustc.edu.cn")
    script = template[1].script
    assert "index='https://mirrors.ustc.edu.cn/pypi/simple'" in script
    assert "/etc/apt/sources.list" not in script
    assert "for domain in" not in script

    official = _initenv.steps_for(["python"], package_mirror="official")[1].script
    assert "index='https://pypi.org/simple'" in official
    assert "for domain in" not in official

    # 自动仍按系统镜像检测，旧行为保持可用。
    auto = _initenv.steps_for(["python"])[1].script
    assert "for domain in" in auto
    assert "mirrors.ustc.edu.cn) index='https://mirrors.ustc.edu.cn/pypi/simple'" in auto
    assert _initenv.pypi_index_for_package_mirror("mirrors.ustc.edu.cn") == ("https://mirrors.ustc.edu.cn/pypi/simple")
    assert _initenv.pypi_index_for_package_mirror("bogus") == ""
    assert _initenv.normalize_package_mirror("bogus") == _initenv.PACKAGE_MIRROR_AUTO


def test_mirror_script_uses_selected_package_mirror() -> None:
    """选了指定软件包源，APT / YUM 也要换到同一家：不能再固定走阿里云。"""
    assert _initenv.system_mirror_source("mirrors.ustc.edu.cn") == "mirrors.ustc.edu.cn"
    assert _initenv.system_mirror_source("mirrors.tuna.tsinghua.edu.cn") == "mirrors.tuna.tsinghua.edu.cn"
    assert _initenv.system_mirror_source("mirrors.tencent.com") == "mirrors.cloud.tencent.com"
    assert _initenv.system_mirror_source("mirrors.huaweicloud.com") == "mirrors.huaweicloud.com"
    # 自动 / 官方 PyPI / 非法值都回退阿里云，旧行为不变。
    for value in ("auto", "official", "bogus", ""):
        assert _initenv.system_mirror_source(value) == "mirrors.aliyun.com"

    ustc = _initenv.steps_for(["mirror"], package_mirror="mirrors.ustc.edu.cn")
    assert [step.label for step in ustc] == ["配置系统镜像源"]
    assert "--source mirrors.ustc.edu.cn" in ustc[0].script
    assert ustc[0].root is True
    assert "--source mirrors.aliyun.com" in _initenv.steps_for(["mirror"])[0].script
    assert "--source mirrors.aliyun.com" in _initenv.steps_for(["mirror"], package_mirror="official")[0].script


def test_present_mirror_tracks_selected_package_mirror() -> None:
    """系统源与选定的软件包源不是同一家时，镜像项视为未就绪（要点才会重新换源）。"""
    aliyun = _initenv.EnvStatus(mirror="mirrors.aliyun.com")
    ustc = _initenv.EnvStatus(mirror="mirrors.ustc.edu.cn")
    assert _initenv.present("mirror", aliyun) is True
    # 自动：任一国内源都算就绪。
    assert _initenv.present("mirror", ustc) is True
    assert _initenv.present("mirror", aliyun, package_mirror="mirrors.ustc.edu.cn") is False
    assert _initenv.present("mirror", ustc, package_mirror="mirrors.ustc.edu.cn") is True
    assert _initenv.default_checked("mirror", aliyun, package_mirror="mirrors.ustc.edu.cn") is True
    assert _initenv.default_checked("mirror", aliyun, package_mirror="auto") is False
    assert _initenv.present("mirror", _initenv.EnvStatus(), package_mirror="mirrors.ustc.edu.cn") is False


def test_package_source_steps_update_runtime_without_reinstalling() -> None:
    """软件包源变更用独立步骤同步 pip / uv 配置，不重装 uv、不碰系统源。"""
    steps = _initenv.package_source_steps("mirrors.ustc.edu.cn")
    assert [step.label for step in steps] == ["配置软件包镜像"]
    script = steps[0].script
    assert "index='https://mirrors.ustc.edu.cn/pypi/simple'" in script
    # 直接写 pip 配置文件：没有 pip 的 Ubuntu 实例不该因为 `python3 -m pip` 直接报错。
    assert "python3 -m pip" not in script
    assert 'pip_conf="$HOME/.config/pip/pip.conf"' in script
    assert '[ -f "$HOME/.pip/pip.conf" ]' in script
    assert 'print "index-url = " idx' in script
    # 首次运行 pip.conf 还不存在：先建空文件，否则 awk 报 cannot open file。
    assert ': > "$pip_conf"' in script
    assert 'cp "$pip_conf" "$pip_conf.bak"' in script
    assert "uv_toml.tmp" in script
    assert 'python-install-mirror = "%s"' in script
    assert "pip install --user" not in script
    assert "/etc/apt/sources.list" not in script


def test_python_script_only_configures_pip_mirror() -> None:
    """Python 项只管 python3 / pip：就绪判定只看 python3（Kali 自带时就该显示已初始化）。"""
    script = _initenv.steps_for(["python"])[1].script
    assert "index-url = " in script
    assert "pip.conf" in script
    assert "python3 -m pip config" not in script
    assert "uv" not in script


def test_pip_config_written_without_pip_module() -> None:
    """Ubuntu 自带 python3 但没有 pip：写配置只靠 shell，不调用 pip。"""
    script = _initenv.steps_for(["uv"])[1].script
    assert "python3 -m pip config" not in script
    assert 'cp "$pip_conf" "$pip_conf.bak"' in script
    # 装 uv 仍需要 pip，缺 pip 时给出可执行的提示而不是 `No module named pip`。
    assert "if ! python3 -m pip --version" in script
    assert "sudo apt-get install -y python3-pip" in script


def test_probe_reads_pip_index_from_config_file() -> None:
    """探测同样不依赖 pip：直接读 pip.conf（XDG / legacy / 系统级）。"""
    script = _initenv.probe_script()
    assert "python3 -m pip config" not in script
    assert 'for f in /etc/pip.conf "$HOME/.pip/pip.conf" "$HOME/.config/pip/pip.conf"' in script
    assert "s/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p" in script


def test_node_script_keeps_nvm_reachable_from_login_shell() -> None:
    """``wsl bash -lc`` 只读 profile：nvm 与 ~/.local/bin 要写进 rc 文件且可重复执行。"""
    script = _initenv.steps_for(["node"])[1].script
    assert 'add_rc "$HOME/.bashrc"' in script
    assert 'add_rc "$HOME/.profile"' in script
    assert 'grep -qF "fcmd wslman init"' in script
    assert 'ln -sf "$node_bin/$name" "$HOME/.local/bin/$name"' in script
    assert "registry.npmmirror.com" in script
    # 已有 nvm + default 版本时不重装（否则每次勾选都会重新下载 node）。
    assert "if ! nvm which default >/dev/null 2>&1; then" in script


def test_apt_script_skips_installed_packages_and_stale_update() -> None:
    script = _initenv.steps_for(["ssh"])[0].script
    assert "dpkg -s" in script
    assert "--no-install-recommends" in script
    assert "DEBIAN_FRONTEND=noninteractive" in script
    assert "-1 hour" in script


def test_probe_script_is_read_only_and_prefixed() -> None:
    script = _initenv.probe_script()
    assert script.startswith("set +e")
    assert "WSLMAN_SSH_PUB" in script
    assert "mirrors.aliyun.com" in script
    assert "apt-get indextargets --format '$(URI)'" in script
    assert "id_ed25519.pub" in script
    assert "WSLMAN_NOTO_CJK" in script
    # Emoji 按字体族名判定；``fc-list family`` 会被当成家族名模式，必须写 ``fc-list : family``。
    assert "fc-list : family" in script
    assert "apt-get install" not in script


def test_parse_status_reads_prefixed_lines_and_ignores_banners() -> None:
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc me@host"
    encoded = base64.b64encode(public_key.encode("utf-8")).decode("ascii")
    text = "\n".join(
        (
            "Welcome to Ubuntu 24.04 LTS",
            "WSLMAN_USER=me",
            "WSLMAN_HOME=/home/me",
            "WSLMAN_OS=Ubuntu 24.04.1 LTS",
            "WSLMAN_MIRROR=mirrors.aliyun.com",
            f"WSLMAN_SSH_PUB={encoded}",
            "WSLMAN_PYTHON=3.12.3",
            "WSLMAN_UV=0.5.14",
            "WSLMAN_AUTO_UV_ENV=1.1.5",
            "WSLMAN_NODE=20.11.0",
            "WSLMAN_NVM=yes",
            "WSLMAN_BUN=1.2.0",
            "WSLMAN_RUST=1.80.0",
            "WSLMAN_GUI_LIBS=ok",
            "WSLMAN_CJK_FONT=Noto Sans CJK SC",
            "WSLMAN_NOTO_CJK=yes",
            "WSLMAN_EMOJI_FONT=yes",
            "WSLMAN_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple",
            "WSLMAN_UV_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple",
            "WSLMAN_NPM_REGISTRY=https://registry.npmmirror.com/",
            "WSLMAN_BUN_REGISTRY=https://registry.npmmirror.com",
            "not a prefixed line",
        )
    )
    status = _initenv.parse_status(text)
    assert status.user == "me"
    assert status.home == "/home/me"
    assert status.os_name == "Ubuntu 24.04.1 LTS"
    assert status.mirror == "mirrors.aliyun.com"
    assert status.ssh_public_key == public_key
    assert status.python == "3.12.3"
    assert status.uv == "0.5.14"
    assert status.auto_uv_env == "1.1.5"
    assert status.node == "20.11.0"
    assert status.nvm == "yes"
    assert status.bun == "1.2.0"
    assert status.rust == "1.80.0"
    assert status.gui_libs == "ok"
    assert status.cjk_font == "Noto Sans CJK SC"
    assert status.noto_cjk == "yes"
    assert status.emoji_font == "yes"
    assert status.pip_index.endswith("/simple")
    assert status.uv_index.endswith("/simple")
    assert status.npm_registry.endswith("/")
    assert status.bun_registry == "https://registry.npmmirror.com"


def test_parse_status_falls_back_to_plain_public_key() -> None:
    status = _initenv.parse_status("WSLMAN_SSH_PUB=ssh-ed25519 AAAA me@host\n")
    assert status.ssh_public_key == "ssh-ed25519 AAAA me@host"
    assert status.ssh_ready is True


def test_parse_status_empty_input_and_empty_value() -> None:
    assert _initenv.parse_status("") == _initenv.EnvStatus()
    status = _initenv.parse_status("WSLMAN_SSH_PUB=\nWSLMAN_PYTHON=\n")
    assert status.ssh_public_key == ""
    assert status.ssh_ready is False


def test_present_and_default_checked() -> None:
    empty = _initenv.EnvStatus()
    assert all(
        _initenv.default_checked(task.key, empty) for task in _initenv.TASKS if task.key not in _initenv.OPTIONAL_KEYS
    )
    # 可选大包（Noto CJK / Emoji）默认不勾。
    assert not any(
        _initenv.default_checked(task.key, empty) for task in _initenv.TASKS if task.key in _initenv.OPTIONAL_KEYS
    )
    assert not any(_initenv.present(task.key, empty) for task in _initenv.TASKS)
    assert _initenv.present("unknown", empty) is False
    assert _initenv.default_checked("unknown", empty) is True

    ready = _initenv.EnvStatus(
        mirror="mirrors.aliyun.com",
        ssh_public_key="ssh-ed25519 AAAA",
        python="3.12.3",
        uv="0.5.14",
        auto_uv_env="1.1.5",
        node="20.11.0",
        nvm="yes",
        bun="1.2.0",
        rust="1.80.0",
        gui_libs="ok",
        cjk_font="Noto Sans CJK SC",
        noto_cjk="yes",
        emoji_font="yes",
    )
    assert all(_initenv.present(task.key, ready) for task in _initenv.TASKS)
    assert not any(_initenv.default_checked(task.key, ready) for task in _initenv.TASKS)

    # 每个项目只看自己那个工具，不互相绑架。
    kali = _initenv.EnvStatus(python="3.12.3")
    assert _initenv.present("python", kali) is True
    assert _initenv.default_checked("python", kali) is False
    assert _initenv.present("uv", kali) is False
    assert _initenv.default_checked("uv", kali) is True
    assert _initenv.present("auto_uv_env", kali) is False
    assert _initenv.default_checked("auto_uv_env", kali) is True
    # 只装了 node（非 nvm）也不能算「已初始化」：还没走 nvm 方案。
    assert _initenv.present("node", _initenv.EnvStatus(node="20.11.0")) is False
    assert _initenv.present("node", _initenv.EnvStatus(node="20.11.0", nvm="yes")) is True
    assert _initenv.present("bun", _initenv.EnvStatus(node="20.11.0", nvm="yes")) is False
    assert _initenv.status_from_mapping(_initenv.status_to_mapping(kali)) == kali


def test_kali_with_system_python_counts_as_initialized() -> None:
    """回归：Kali 这类自带 python3、没有 uv 的实例，Python 项必须显示已初始化。"""
    status = _initenv.parse_status("WSLMAN_PYTHON=3.11.9\nWSLMAN_UV=\n")
    assert _initenv.present("python", status) is True
    assert _initenv.default_checked("python", status) is False
    assert _initenv.present("uv", status) is False
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(status)}
    assert rows["Python"] == ("3.11.9", True)
    assert rows["uv"] == ("未安装", False)


def test_status_rows_and_summary_reflect_state() -> None:
    status = _initenv.EnvStatus(
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc me@host",
        python="3.12.3",
        uv="0.5.14",
    )
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(status)}
    assert rows["系统镜像源"][1] is False
    assert rows["SSH 密钥"][0].startswith("ssh-ed25519 AAAAC3NzaC1l")
    assert rows["SSH 密钥"][1] is True
    assert rows["Python"] == ("3.12.3", True)
    assert rows["uv"] == ("0.5.14", True)
    assert rows["auto-uv-env"] == ("未安装", False)
    assert rows["Node.js（nvm）"] == ("未安装", False)
    assert rows["Bun"] == ("未安装", False)
    assert rows["Rust"] == ("未安装", False)
    assert rows["pip / uv 镜像"] == ("未配置", False)
    assert rows["npm / bun 镜像"] == ("未配置", False)
    assert rows["显示依赖库"] == ("未检测", False)
    assert rows["中文字体"] == ("未安装", False)
    assert rows["可选字体"] == ("Noto CJK ✗ · Emoji ✗", False)
    assert len(rows) == 13

    summary = _initenv.summary_text(status)
    assert "镜像源 ✗" in summary
    assert "SSH ✓" in summary
    assert "Python 3.12.3 / uv 0.5.14" in summary
    assert "Rust ✗" in summary
    assert "显示库 ✗" in summary
    assert "字体 ✗" in summary


def test_display_libs_and_fonts_detect_and_report() -> None:
    """缺库 / 缺字体各自成项：探测结果要能显示缺了哪些库和已装字体族。"""
    missing = _initenv.parse_status("WSLMAN_GUI_LIBS=libEGL.so.1 libxcb-cursor.so.0\n")
    assert _initenv.present("gui", missing) is False
    assert _initenv.default_checked("gui", missing) is True
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(missing)}
    assert rows["显示依赖库"] == ("缺 2 个：libEGL.so.1 libxcb-cursor.so.0", False)

    ready = _initenv.parse_status("WSLMAN_GUI_LIBS=ok\nWSLMAN_CJK_FONT=Noto Sans CJK SC\n")
    assert _initenv.present("gui", ready) is True
    assert _initenv.present("fonts", ready) is True
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(ready)}
    assert rows["显示依赖库"] == ("已就绪", True)
    assert rows["中文字体"] == ("Noto Sans CJK SC", True)
    # 可选大包即使未装也默认不勾，不会让首跑多下几十 MB。
    assert _initenv.default_checked("noto", ready) is False
    assert _initenv.default_checked("emoji", ready) is False
    assert _initenv.default_checked("noto", _initenv.EnvStatus()) is False

    no_font = _initenv.parse_status("WSLMAN_GUI_LIBS=ok\nWSLMAN_CJK_FONT=\n")
    assert _initenv.present("fonts", no_font) is False
    assert _initenv.default_checked("fonts", no_font) is True


def test_display_and_font_tasks_install_expected_packages() -> None:
    """默认字体走轻量微米黑；Noto CJK / Emoji 拆成可选；缓存用系统级增量扫描。"""
    gui_steps = _initenv.steps_for(["gui"])
    assert len(gui_steps) == 1
    assert gui_steps[0].root is True
    for package in ("libegl1", "libxcb-cursor0", "libxcb-icccm4", "libwayland-cursor0", "libgl1"):
        assert package in gui_steps[0].script

    font_steps = _initenv.steps_for(["fonts"])
    assert [step.label for step in font_steps] == ["安装中文字体", "刷新字体缓存"]
    assert font_steps[0].root is True
    assert "fonts-wqy-microhei" in font_steps[0].script
    assert "fonts-noto-cjk" not in font_steps[0].script
    # 系统缓存增量刷新（-s），不再全量 -f，也不必以普通用户重复建缓存。
    assert "fc-cache -s" in font_steps[1].script
    assert "fc-cache -f" not in font_steps[1].script
    assert font_steps[1].root is True
    # apt 少拉翻译索引；无 TTY 时不让 dpkg 开伪终端。
    assert "Acquire::Languages=none" in font_steps[0].script
    assert "Dpkg::Use-Pty=0" in font_steps[0].script

    noto_steps = _initenv.steps_for(["noto"])
    assert "fonts-noto-cjk" in noto_steps[0].script
    assert noto_steps[0].root is True

    emoji_steps = _initenv.steps_for(["emoji"])
    assert "fonts-noto-color-emoji" in emoji_steps[0].script
    assert emoji_steps[0].root is True


def test_status_rows_distinguish_nvm_and_merge_mirrors() -> None:
    """非 nvm 装的 node 不算就绪；pip/uv 或 npm/bun 镜像同源时只显示一个。"""
    status = _initenv.EnvStatus(
        node="20.11.0",
        bun="1.2.0",
        pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        uv_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        npm_registry="https://registry.npmmirror.com/",
        bun_registry="https://registry.npmmirror.com",
    )
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(status)}
    assert rows["Node.js（nvm）"] == ("20.11.0（非 nvm）", False)
    assert rows["pip / uv 镜像"] == ("https://pypi.tuna.tsinghua.edu.cn/simple", True)
    assert rows["npm / bun 镜像"] == ("https://registry.npmmirror.com/", True)
    assert "20.11.0 / bun 1.2.0" in _initenv.summary_text(status)

    nvm_status = _initenv.EnvStatus(node="20.11.0", nvm="yes")
    assert _initenv.status_rows(nvm_status)[5] == ("Node.js（nvm）", "20.11.0（nvm）", True)

    split = _initenv.EnvStatus(pip_index="https://a/simple", uv_index="https://b/simple")
    assert _initenv.status_rows(split)[8] == ("pip / uv 镜像", "https://a/simple · https://b/simple", True)


def test_ssh_key_short_truncates() -> None:
    short = _initenv.ssh_key_short("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc me@host")
    assert short.startswith("ssh-ed25519 AAAAC3NzaC1l")
    assert short.endswith("…")
    assert _initenv.ssh_key_short("short") == "short"
    assert _initenv.ssh_key_short("x" * 40).endswith("…")


def test_tail_lines_limits_and_skips_blank() -> None:
    text = "a\n\nb\nc\nd\n"
    assert _initenv.tail_lines(text, 2) == ("c", "d")
    assert _initenv.tail_lines(text, 0) == ()
    assert _initenv.tail_lines("", 3) == ()
