"""fcmd MCP 适配器的安全策略层。

本模块在 :mod:`fcmd.cli.mcp_adapter` 的 call_tool 入口前执行，
提供**统一前置闸门**——所有工具调用（不管是内置还是 @fcmd.tool）都必须先
经过这里的检查。安全逻辑全部集中在此，工具实现无需感知，避免散落到各
工具文件里造成漂移。

当前能力
--------
* **Workspace Root 约束**：路径参数（``type='path'``）必须落在 ``root`` 内。
  ``root=None`` 表示关闭路径检查（仅限本地自用场景）。
* **绝对路径白名单**：默认拒绝绝对/UNC/盘符路径；仅当 ``allow_abs=True`` 时放行。
* **网络门禁**：``meta='network'`` 的工具由策略决定是否允许（默认允许，
  可通过 ``allow_network=False`` 全局禁用）。

设计约定
--------
* 所有检查函数都返回 None / 抛出 :class:`SecurityError`；adapter 侧统一捕获
  并序列化为 ``fcmd-error/1`` 信封。
* 策略对象是**无状态、可序列化**的纯配置（便于写入配置文件、MCP resource
  展示）。
* 不做命令级白名单——白名单已经在 :mod:`fcmd.cli.agent_whitelist` 工具准入
  层完成；这里是更细粒度的运行时检查。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PathLike = str | Path


class SecurityError(Exception):
    """安全策略拦截。``kind`` 与 :mod:`fcmd.core.exit_codes` 对齐，便于 adapter
    侧序列化为 ``fcmd-error/1`` 的 envelope。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "security",
        hint: str = "",
        expected: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.kind = kind
        self.hint = hint
        self.expected = expected
        super().__init__(f"[{code}] {message}")


@dataclass(slots=True)
class AgentSecurityPolicy:
    """MCP 适配器的安全策略配置。

    Attributes
    ----------
    workspace_root:
        所有路径参数必须落在该目录内；``None`` 表示关闭路径检查（仅限本地自用）。
    allow_absolute_paths:
        是否允许绝对/UNC/盘符路径。默认 ``False``——路径必须是相对 workspace_root 的。
    allow_network:
        是否允许 ``meta='network'`` 的工具访问外网。默认 ``True``（小模型通常需要联网查数据）。
    require_write_confirmation:
        write 级工具是否强制要求先 ``--dry-run/--preview`` 做 plan 阶段，
        再 ``--yes`` 做 apply 阶段。默认 ``True``。
    deny_patterns:
        额外的路径片段黑名单（正则或字面量），命中则拦截。空 list 表示无额外限制。
    """

    workspace_root: Path | None = None
    allow_absolute_paths: bool = False
    allow_network: bool = True
    require_write_confirmation: bool = True
    deny_patterns: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的字典（用于 MCP resource 展示）。"""
        d = asdict(self)
        d["workspace_root"] = str(self.workspace_root) if self.workspace_root else None
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSecurityPolicy:
        root = data.get("workspace_root")
        return cls(
            workspace_root=Path(root) if root else None,
            allow_absolute_paths=bool(data.get("allow_absolute_paths", False)),
            allow_network=bool(data.get("allow_network", True)),
            require_write_confirmation=bool(data.get("require_write_confirmation", True)),
            deny_patterns=list(data.get("deny_patterns", [])),
        )


# ---------------------------------------------------------------------------
# 运行时检查函数
# ---------------------------------------------------------------------------


def validate_path_within_root(path: PathLike, root: Path) -> Path:
    """检查 ``path`` resolve 后必须在 ``root`` 内。

    返回 resolve 后的绝对路径（方便后续使用）。
    不做任何 IO；root 不存在或 path 不存在都只比较字符串——调用方负责处理不存在的情况。
    """
    p = Path(path).resolve()
    r = root.resolve()
    try:
        p.relative_to(r)
    except ValueError as exc:
        raise SecurityError(
            code="PATH_ESCAPE",
            kind="security",
            message=f"路径逃逸: {p} 不在 workspace root {r} 内",
            hint="请使用 workspace_root 内的相对路径或提升 workspace_root 范围",
            expected=str(r),
        ) from exc
    return p


def validate_no_denied_fragments(path: PathLike, denied: Iterable[str]) -> None:
    """检查路径字符串不包含任何 deny_patterns 片段（大小写不敏感）。"""
    s = str(path)
    low = s.lower()
    for frag in denied:
        if frag.lower() in low:
            raise SecurityError(
                code="PATH_DENIED",
                kind="security",
                message=f"路径命中黑名单片段 {frag!r}: {path}",
                hint="检查路径是否包含敏感目录名或归档内容逃逸片段",
            )


def sha256_plan_payload(payload: dict[str, Any] | list[Any] | str) -> str:
    """把 plan 结果规范化后算 SHA-256。

    adapter 用它生成 plan_hash，作为 apply 阶段的绑定凭据。
    JSON 规范化：sort_keys=True + ensure_ascii=False + separators 紧凑。
    """
    if isinstance(payload, (dict, list)):
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    else:
        canon = str(payload)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def plan_hash_for(arguments: dict[str, Any], entry_name: str) -> str:
    """把 call 的 arguments + tool_name 规范化后算 hash（plan_hash 的稳定来源）。"""
    payload = {"tool": entry_name, "args": dict(sorted(arguments.items()))}
    return sha256_plan_payload(payload)
