"""wslman 的错误类型。

错误码只复用 ``fcmd.core.exit_codes`` 已登记的取值，避免为本应用改动共享码表：
``NOT_FOUND`` / ``RUNTIME_ERROR`` / ``INVALID_ARGUMENT`` / ``USAGE`` /
``CHECK_FAILED`` / ``INTERRUPTED``。
"""

from __future__ import annotations

__all__ = [
    "WslmanError",
    "check_failed",
    "invalid_argument",
    "not_found",
    "runtime_error",
    "usage_error",
]


class WslmanError(Exception):
    """带错误码与可执行提示的异常；CLI 层统一转成双读错误信封。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.field = field


def not_found(message: str, *, hint: str | None = None, field: str | None = None) -> WslmanError:
    """找不到对象（发行版、wsl.exe、文件）。"""
    return WslmanError("NOT_FOUND", message, hint=hint, field=field)


def invalid_argument(message: str, *, hint: str | None = None, field: str | None = None) -> WslmanError:
    """参数或目标状态不合法。"""
    return WslmanError("INVALID_ARGUMENT", message, hint=hint, field=field)


def usage_error(message: str, *, hint: str | None = None, field: str | None = None) -> WslmanError:
    """用法缺失，例如破坏性命令没有确认标志。"""
    return WslmanError("USAGE", message, hint=hint, field=field)


def runtime_error(message: str, *, hint: str | None = None, field: str | None = None) -> WslmanError:
    """外部命令失败或运行环境不可用。"""
    return WslmanError("RUNTIME_ERROR", message, hint=hint, field=field)


def check_failed(message: str, *, hint: str | None = None, field: str | None = None) -> WslmanError:
    """校验结论为不通过。"""
    return WslmanError("CHECK_FAILED", message, hint=hint, field=field)
