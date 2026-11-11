"""sshcopyid - SSH 公钥部署工具。

远端脚本统一走 stdin 流（``cat >> authorized_keys``），公钥文件重定向到
ssh stdin，不把公钥内容插值进远端命令字符串：

- Linux：``sshpass -e`` 从 ``SSHPASS`` 环境变量读密码（密码不进 argv），
  shell 重定向把公钥文件送入 ssh stdin。
- Windows：探测最优 SSH 客户端（plink 优先 → OpenSSH 次之），plink 分支
  用 ``-pw`` 自动传密码，OpenSSH 分支交互式输入（不捕获输出，tty 透传）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import fcmd as fx
from fcmd.infras.dev import rewrite_ssh_cmd

_REMOTE_SCRIPT = (
    '"mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"'
)

_WIN_TEMPLATE = (
    "ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
    "-o ConnectTimeout={timeout} {username}@{hostname} " + _REMOTE_SCRIPT + ' < "{keypath}"'
)


def _failure_message(hostname: str, username: str, port: int) -> str:
    return f"部署失败，可手动执行: ssh-copy-id -p {port} {username}@{hostname}"


@fx.tool(
    "sshcopyid",
    help="部署 SSH 公钥到远程服务器",
    description="SSH 公钥部署工具（Linux: sshpass 自动密码，Windows: plink 优先自动密码 / OpenSSH 需交互）",
)
def sshcopyid(  # noqa: PLR0913, PLR0917 - 参数与 TOML 声明保持一致
    hostname: str,
    username: str,
    password: str,
    port: int = 22,
    keypath: Path = Path("~/.ssh/id_rsa.pub"),
    timeout: int = 30,
) -> None:
    """部署 SSH 公钥到远程服务器。

    Parameters
    ----------
    hostname:
        远程服务器主机名或 IP 地址
    username:
        远程服务器用户名
    password:
        远程服务器密码（Linux 经 SSHPASS 环境变量传递，不进命令行；Windows
        检测到 plink 时经 -pw 命令行参数传递，OpenSSH 分支无法自动注入需交互输入）
    port:
        SSH 端口（默认 22）
    keypath:
        公钥文件路径（默认 ~/.ssh/id_rsa.pub，使用前展开为绝对路径）
    timeout:
        SSH 连接超时秒数（默认 30）
    """
    expanded_keypath = Path(keypath).expanduser()
    # 公钥不存在：保持原 SKIPPED 可观察行为（无执行、退出码 0）
    if not expanded_keypath.exists():
        return

    if sys.platform == "win32":
        template = rewrite_ssh_cmd(_WIN_TEMPLATE, platform="win32")
        assert isinstance(template, str)
        cmd = template.format(
            port=port,
            timeout=timeout,
            username=username,
            hostname=hostname,
            password=password,
            keypath=expanded_keypath,
        )
        result = subprocess.run(cmd, shell=True, check=False)  # 不捕获输出（tty 透传）
    else:
        cmd = (
            f"sshpass -e ssh -p {port} -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null -o ConnectTimeout={timeout} "
            f'{username}@{hostname} {_REMOTE_SCRIPT} < "{expanded_keypath}"'
        )
        env = dict(os.environ)
        env["SSHPASS"] = password
        result = subprocess.run(cmd, shell=True, env=env, check=False, capture_output=True, text=True)

    if result.returncode != 0:
        print(_failure_message(hostname, username, port))
        raise RuntimeError(f"SSH 公钥部署失败（退出码 {result.returncode}）")

    print(f"SSH 密钥已部署到 {username}@{hostname}:{port}")


@fx.main("sshcopyid")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
