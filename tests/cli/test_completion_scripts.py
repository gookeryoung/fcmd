"""``fcmd.cli._completion_scripts`` 生成器测试（重点覆盖 PowerShell）。"""

from __future__ import annotations

from fcmd.cli._completion_scripts import gen_powershell_script


def test_powershell_script_structure() -> None:
    """脚本包含补全注册、第一层与子命令映射。"""
    script = gen_powershell_script(
        [
            {"name": "pymake", "aliases": ["pm"], "subs": [("b", "构建"), ("t", "测试")]},
            {"name": "hashfile", "aliases": [], "subs": []},
        ]
    )
    assert "Register-ArgumentCompleter -Native -CommandName fcmd" in script
    assert "[System.Management.Automation.CompletionResult]::new" in script
    assert "'graph'" in script  # 内建命令
    assert "'--version'" in script
    assert "'pymake'" in script and "'pm'" in script
    assert "'b', 't'" in script
    # 无子命令的工具只出现在第一层，不进入子命令映射
    assert "'hashfile' = " not in script


def test_powershell_script_escapes_single_quotes() -> None:
    """名称含单引号时双写转义，保证生成的 PowerShell 字符串合法。"""
    script = gen_powershell_script([{"name": "it's", "aliases": [], "subs": [("a", "")]}])
    assert "'it''s'" in script


def test_powershell_script_without_subcommands() -> None:
    """所有工具都没有子命令时子命令映射为空表，脚本仍可生成。"""
    script = gen_powershell_script([{"name": "plain", "aliases": [], "subs": []}])
    assert "$subcommands = @{" in script
    assert "'plain'" in script
