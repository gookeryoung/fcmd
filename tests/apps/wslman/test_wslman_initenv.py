"""wslmanw「初始化环境」纯逻辑测试：任务定义、脚本口径、探测解析与状态判断。"""

from __future__ import annotations

import base64

from fcmd.apps.wslman import _initenv


def test_tasks_cover_required_items() -> None:
    keys = [task.key for task in _initenv.TASKS]
    assert keys == ["mirror", "ssh", "python", "node", "rust"]
    assert _initenv.task_by_key("python") is not None
    assert _initenv.task_by_key("missing") is None


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
    assert "bash <(curl -sSL https://linuxmirrors.cn/main.sh)" in script
    assert "--source mirrors.aliyun.com" in script
    assert "--ignore-backup-tips" in script
    assert task.steps[0].root is True


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
    node_steps = _initenv.steps_for(["node"])
    rust_steps = _initenv.steps_for(["rust"])
    assert "python3-pip" in python_steps[0].script
    assert "pypi.tuna.tsinghua.edu.cn" in python_steps[1].script
    assert "nodejs" in node_steps[0].script
    assert "registry.npmmirror.com" in node_steps[1].script
    assert "rsproxy.cn" in rust_steps[1].script
    assert "crates-io" in rust_steps[1].script


def test_probe_script_is_read_only_and_prefixed() -> None:
    script = _initenv.probe_script()
    assert script.startswith("set +e")
    assert "WSLMAN_SSH_PUB" in script
    assert "mirrors.aliyun.com" in script
    assert "id_ed25519.pub" in script
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
            "WSLMAN_NODE=20.11.0",
            "WSLMAN_RUST=1.80.0",
            "WSLMAN_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple",
            "WSLMAN_NPM_REGISTRY=https://registry.npmmirror.com/",
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
    assert status.node == "20.11.0"
    assert status.rust == "1.80.0"
    assert status.pip_index.endswith("/simple")
    assert status.npm_registry.endswith("/")


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
    assert all(_initenv.default_checked(task.key, empty) for task in _initenv.TASKS)
    assert not any(_initenv.present(task.key, empty) for task in _initenv.TASKS)
    assert _initenv.present("unknown", empty) is False
    assert _initenv.default_checked("unknown", empty) is True

    ready = _initenv.EnvStatus(
        mirror="mirrors.aliyun.com",
        ssh_public_key="ssh-ed25519 AAAA",
        python="3.12.3",
        node="20.11.0",
        rust="1.80.0",
    )
    assert all(_initenv.present(task.key, ready) for task in _initenv.TASKS)
    assert not any(_initenv.default_checked(task.key, ready) for task in _initenv.TASKS)


def test_status_rows_and_summary_reflect_state() -> None:
    status = _initenv.EnvStatus(
        ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc me@host",
        python="3.12.3",
    )
    rows = {label: (value, ok) for label, value, ok in _initenv.status_rows(status)}
    assert rows["系统镜像源"][1] is False
    assert rows["SSH 密钥"][0].startswith("ssh-ed25519 AAAAC3NzaC1l")
    assert rows["SSH 密钥"][1] is True
    assert rows["Python"] == ("3.12.3", True)
    assert rows["Node.js"] == ("未安装", False)
    assert rows["Rust"] == ("未安装", False)
    assert len(rows) == 7

    summary = _initenv.summary_text(status)
    assert "镜像源 ✗" in summary
    assert "SSH ✓" in summary
    assert "Python 3.12.3" in summary
    assert "Rust ✗" in summary


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
