"""devman 的错误模型：场景 → 稳定错误码 → 退出码。

错误码必须在 :data:`fcmd.core.exit_codes.ERROR_CODE_KINDS` 登记，否则会回退成
``input``；本模块只做「异常携带码 + 人话 + 改法」，渲染交给
:func:`fcmd.core.result.report_error`。
"""

from __future__ import annotations

from typing import Literal, override

__all__ = [
    "BUSY",
    "CHECKSUM_MISMATCH",
    "CHECKSUM_UNAVAILABLE",
    "CONFIRM_REQUIRED",
    "DOWNLOAD_FAILED",
    "INCOMPLETE_INSTALL",
    "NOT_INSTALLED",
    "PATH_WRITE_FAILED",
    "ROOT_NOT_WRITABLE",
    "UV_UNAVAILABLE",
    "VERSION_NOT_FOUND",
    "DevmanError",
    "check_failed",
    "confirm_required",
    "invalid_argument",
    "not_found",
    "runtime_error",
    "usage_error",
]

CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
NOT_INSTALLED = "NOT_INSTALLED"
CHECKSUM_UNAVAILABLE = "CHECKSUM_UNAVAILABLE"
CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
UV_UNAVAILABLE = "UV_UNAVAILABLE"
ROOT_NOT_WRITABLE = "ROOT_NOT_WRITABLE"
BUSY = "DEVICE_BUSY"
PATH_WRITE_FAILED = "PATH_WRITE_FAILED"
INCOMPLETE_INSTALL = "INCOMPLETE_INSTALL"

Kind = Literal["usage", "input", "runtime", "not_found", "check", "interrupted"]


class DevmanError(Exception):
    """带稳定错误码的业务异常；``str`` 即人读消息。"""

    def __init__(  # noqa: PLR0913 - 错误信封字段齐全，可选字段按需传入
        self,
        message: str,
        code: str,
        *,
        hint: str | None = None,
        field: str | None = None,
        expected: str | None = None,
        kind: Kind | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.field = field
        self.expected = expected
        self.kind = kind

    @override
    def __str__(self) -> str:
        return self.message


def usage_error(message: str, *, hint: str | None = None) -> DevmanError:
    """用法错误（未知子命令 / 缺参数）。"""
    return DevmanError(message, "USAGE", hint=hint, kind="usage")


def invalid_argument(message: str, *, hint: str | None = None, field: str | None = None) -> DevmanError:
    """参数值不合法。"""
    return DevmanError(message, "INVALID_ARGUMENT", hint=hint, field=field, kind="input")


def not_found(message: str, *, hint: str | None = None, code: str = VERSION_NOT_FOUND) -> DevmanError:
    """目标不存在（版本 / 安装记录）。"""
    return DevmanError(message, code, hint=hint, kind="not_found")


def runtime_error(message: str, *, hint: str | None = None, code: str = "RUNTIME_ERROR") -> DevmanError:
    """运行期失败（网络、子进程、文件系统）。"""
    return DevmanError(message, code, hint=hint, kind="runtime")


def check_failed(message: str, *, hint: str | None = None, code: str = INCOMPLETE_INSTALL) -> DevmanError:
    """检查类结论不通过（校验和 / 半完成安装）。"""
    return DevmanError(message, code, hint=hint, kind="check")


def confirm_required(message: str, *, hint: str | None = None) -> DevmanError:
    """破坏性操作缺少 ``--yes``。"""
    return DevmanError(message, CONFIRM_REQUIRED, hint=hint, kind="usage")
