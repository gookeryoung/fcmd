"""sshcopyid - SSH 密钥部署工具。

将本地 SSH 公钥部署到远程服务器，依赖 ``sshpass`` 与 ``ssh`` 命令。

示例
----
    fcmd sshcopyid host user password
    fcmd sshcopyid host user password --port 2222
    fcmd sshcopyid host user password --keypath ~/.ssh/id_ed25519.pub
"""

from __future__ import annotations

from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = ["ssh_copy_id"]


@fcmd.tool("sshcopyid", help="部署 SSH 公钥到远程服务器")
def ssh_copy_id(  # noqa: PLR0913
    hostname: str,
    username: str,
    password: str,
    port: int = 22,
    keypath: str = "~/.ssh/id_rsa.pub",
    timeout: int = 30,
) -> None:
    """将 SSH 公钥部署到远程服务器。

    依赖 ``sshpass`` 命令，未安装时命令失败并提示手动执行 ``ssh-copy-id``。

    Parameters
    ----------
    hostname:
        远程服务器主机名或 IP 地址
    username:
        远程服务器用户名
    password:
        远程服务器密码
    port:
        SSH 端口（默认 22）
    keypath:
        公钥文件路径（默认 ``~/.ssh/id_rsa.pub``）
    timeout:
        SSH 连接超时秒数（默认 30）
    """
    pub_key_path = Path(keypath).expanduser()
    if not pub_key_path.exists():
        print(f"公钥文件不存在: {pub_key_path}")
        return

    pub_key = pub_key_path.read_text(encoding="utf-8").strip()

    # 构造远程执行脚本：确保目录权限 + 去重追加公钥
    script = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "cd ~/.ssh && touch authorized_keys && chmod 600 authorized_keys && "
        f"grep -qF '{pub_key.split()[1]}' authorized_keys 2>/dev/null || "
        f"echo '{pub_key}' >> authorized_keys"
    )

    result = run_command(
        [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-p",
            str(port),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ConnectTimeout={timeout}",
            f"{username}@{hostname}",
            script,
        ],
    )
    if result.failed:
        print(f"部署失败，可手动执行: ssh-copy-id -p {port} {username}@{hostname}")
        return
    print(f"SSH 密钥已部署到 {username}@{hostname}:{port}")
