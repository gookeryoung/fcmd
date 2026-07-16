"""conditions 模块测试：if 表达式解析与 matrix 展开。

覆盖 :mod:`fcmd.conditions` 的全部公共 API：
- :func:`parse_if` —— if 表达式解析为 Condition 函数
- :func:`expand_matrix` —— matrix 配置笛卡尔积展开
- :func:`substitute_matrix_vars` —— ``${{ matrix.X }}`` 占位符替换
- :func:`matrix_suffix` —— 任务名后缀生成
- :class:`ConditionError` —— 解析/求值错误
"""

from __future__ import annotations

import pytest

from fcmd.conditions import (
    ConditionError,
    expand_matrix,
    matrix_suffix,
    parse_if,
    substitute_matrix_vars,
)


# ============================================================================ #
# parse_if: 状态检查函数
# ============================================================================ #
class TestParseIfStatus:
    """状态检查函数 success()/failure()/always() 测试。"""

    def test_success_no_upstream(self) -> None:
        """无上游时 success() 返回 True。"""
        cond = parse_if("success()")
        assert cond({}) is True

    def test_success_all_upstream_success(self) -> None:
        """所有上游成功时 success() 返回 True。"""
        cond = parse_if("success()")
        ctx = {"__status__": {"build": "success", "test": "success"}}
        assert cond(ctx) is True

    def test_success_some_upstream_failed(self) -> None:
        """任一上游失败时 success() 返回 False。"""
        cond = parse_if("success()")
        ctx = {"__status__": {"build": "success", "test": "failed"}}
        assert cond(ctx) is False

    def test_failure_no_upstream(self) -> None:
        """无上游时 failure() 返回 False。"""
        cond = parse_if("failure()")
        assert cond({}) is False

    def test_failure_some_upstream_failed(self) -> None:
        """任一上游失败时 failure() 返回 True。"""
        cond = parse_if("failure()")
        ctx = {"__status__": {"build": "success", "test": "failed"}}
        assert cond(ctx) is True

    def test_failure_all_upstream_success(self) -> None:
        """所有上游成功时 failure() 返回 False。"""
        cond = parse_if("failure()")
        ctx = {"__status__": {"build": "success"}}
        assert cond(ctx) is False

    def test_always_returns_true_empty_context(self) -> None:
        """always() 总是返回 True（空上下文）。"""
        cond = parse_if("always()")
        assert cond({}) is True

    def test_always_returns_true_with_failed_upstream(self) -> None:
        """always() 总是返回 True（即使上游失败）。"""
        cond = parse_if("always()")
        ctx = {"__status__": {"build": "failed"}}
        assert cond(ctx) is True


# ============================================================================ #
# parse_if: 上下文与环境变量访问
# ============================================================================ #
class TestParseIfContext:
    """ctx.NAME / vars.NAME 访问测试。"""

    def test_ctx_eq_string(self) -> None:
        """ctx.X == 'Y' 上下文比较。"""
        cond = parse_if("ctx.build == 'ok'")
        assert cond({"build": "ok"}) is True
        assert cond({"build": "fail"}) is False

    def test_ctx_neq_string(self) -> None:
        """ctx.X != 'Y' 上下文不等比较。"""
        cond = parse_if("ctx.build != 'fail'")
        assert cond({"build": "ok"}) is True
        assert cond({"build": "fail"}) is False

    def test_ctx_missing_key_returns_none(self) -> None:
        """ctx.X 访问缺失 key 返回 None（与 None 比较为 False）。"""
        cond = parse_if("ctx.missing == None")
        assert cond({}) is True

    def test_vars_eq_env_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """vars.X == 'Y' 环境变量比较。"""
        monkeypatch.setenv("CI", "true")
        cond = parse_if("vars.CI == 'true'")
        assert cond({}) is True

    def test_vars_missing_env_returns_none(self) -> None:
        """vars.X 访问未设置环境变量返回 None。"""
        cond = parse_if("vars.NONEXISTENT == None")
        assert cond({}) is True

    def test_ctx_compare_number(self) -> None:
        """ctx.X 与数字比较。"""
        cond = parse_if("ctx.count == 3")
        assert cond({"count": 3}) is True
        assert cond({"count": 5}) is False


# ============================================================================ #
# parse_if: 逻辑组合
# ============================================================================ #
class TestParseIfLogic:
    """not / and / or / 括号组合测试。"""

    def test_not_expr(self) -> None:
        """not <expr> 取反。"""
        cond = parse_if("not success()")
        assert cond({}) is False

    def test_and_both_true(self) -> None:
        """a and b 两边 True 返回 True。"""
        cond = parse_if("success() and always()")
        assert cond({}) is True

    def test_and_one_false(self) -> None:
        """a and b 一边 False 返回 False。"""
        cond = parse_if("success() and failure()")
        assert cond({}) is False

    def test_or_both_false(self) -> None:
        """a or b 两边 False 返回 False。"""
        cond = parse_if("failure() or failure()")
        assert cond({}) is False

    def test_or_one_true(self) -> None:
        """a or b 一边 True 返回 True。"""
        cond = parse_if("failure() or success()")
        assert cond({}) is True

    def test_and_short_circuit(self) -> None:
        """and 短路求值：左边 False 时不求右边。"""
        cond = parse_if("failure() and ctx.nonexistent == 'x'")
        # 左边 False 直接返回，不访问 ctx.nonexistent
        assert cond({}) is False

    def test_or_short_circuit(self) -> None:
        """or 短路求值：左边 True 时不求右边。"""
        cond = parse_if("success() or ctx.nonexistent == 'x'")
        assert cond({}) is True

    def test_parentheses_grouping(self) -> None:
        """括号改变优先级。"""
        # (failure() or success()) and failure() → True and False → False
        cond = parse_if("(failure() or success()) and failure()")
        assert cond({}) is False

    def test_chained_compare(self) -> None:
        """链式比较 a == b == c。"""
        cond = parse_if("ctx.x == ctx.y == 'ok'")
        assert cond({"x": "ok", "y": "ok"}) is True
        assert cond({"x": "ok", "y": "no"}) is False

    def test_complex_expr(self) -> None:
        """复杂组合表达式。"""
        # not failure() and (ctx.build == 'ok' or vars.CI == 'true')
        cond = parse_if("not failure() and (ctx.build == 'ok' or vars.CI == 'true')")
        # not failure() → not False → True
        # ctx.build == 'ok' → True (build='ok')
        # True and (True or ...) → True
        assert cond({"build": "ok"}) is True


# ============================================================================ #
# parse_if: 比较运算符
# ============================================================================ #
class TestParseIfComparison:
    """``<`` / ``>`` / ``<=`` / ``>=`` / ``in`` / ``not in`` 比较运算测试。"""

    def test_less_than(self) -> None:
        """``ctx.x < 5`` 数值小于比较。"""
        cond = parse_if("ctx.x < 5")
        assert cond({"x": 3}) is True
        assert cond({"x": 5}) is False
        assert cond({"x": 7}) is False

    def test_greater_than(self) -> None:
        """``ctx.x > 5`` 数值大于比较。"""
        cond = parse_if("ctx.x > 5")
        assert cond({"x": 7}) is True
        assert cond({"x": 5}) is False

    def test_less_equal(self) -> None:
        """``ctx.x <= 5`` 数值小于等于比较。"""
        cond = parse_if("ctx.x <= 5")
        assert cond({"x": 5}) is True
        assert cond({"x": 6}) is False

    def test_greater_equal(self) -> None:
        """``ctx.x >= 5`` 数值大于等于比较。"""
        cond = parse_if("ctx.x >= 5")
        assert cond({"x": 5}) is True
        assert cond({"x": 4}) is False

    def test_in_string(self) -> None:
        """``ctx.x in 'abc'`` 字符串成员检查。"""
        cond = parse_if("ctx.x in 'abc'")
        assert cond({"x": "a"}) is True
        assert cond({"x": "d"}) is False

    def test_in_list(self) -> None:
        """``ctx.x in ['a', 'b']`` 列表成员检查。"""
        cond = parse_if("ctx.x in ['a', 'b']")
        assert cond({"x": "a"}) is True
        assert cond({"x": "c"}) is False

    def test_in_tuple(self) -> None:
        """``ctx.x in ('a', 'b')`` 元组成员检查。"""
        cond = parse_if("ctx.x in ('a', 'b')")
        assert cond({"x": "a"}) is True
        assert cond({"x": "c"}) is False

    def test_not_in_tuple(self) -> None:
        """``ctx.x not in ('a', 'b')`` 元组非成员检查。"""
        cond = parse_if("ctx.x not in ('a', 'b')")
        assert cond({"x": "c"}) is True
        assert cond({"x": "a"}) is False

    def test_not_in_string(self) -> None:
        """``ctx.x not in 'abc'`` 字符串非成员检查。"""
        cond = parse_if("ctx.x not in 'abc'")
        assert cond({"x": "d"}) is True
        assert cond({"x": "a"}) is False

    def test_chained_less_than(self) -> None:
        """链式比较 ``ctx.x < ctx.y < 10``。"""
        cond = parse_if("ctx.x < ctx.y < 10")
        assert cond({"x": 3, "y": 7}) is True
        assert cond({"x": 3, "y": 15}) is False
        assert cond({"x": 8, "y": 7}) is False

    def test_compare_with_logic(self) -> None:
        """比较运算与逻辑组合：``ctx.x > 0 and ctx.x < 10``。"""
        cond = parse_if("ctx.x > 0 and ctx.x < 10")
        assert cond({"x": 5}) is True
        assert cond({"x": 0}) is False
        assert cond({"x": 10}) is False

    def test_mixed_type_compare_raises(self) -> None:
        """混合类型比较（int < str）抛 ConditionError。"""
        cond = parse_if("ctx.x < 'a'")
        with pytest.raises(ConditionError, match="比较运算类型错误"):
            cond({"x": 3})


# ============================================================================ #
# parse_if: 错误场景
# ============================================================================ #
class TestParseIfErrors:
    """if 表达式错误场景测试。"""

    def test_syntax_error_raises(self) -> None:
        """语法错误抛 ConditionError。"""
        with pytest.raises(ConditionError, match="语法错误"):
            parse_if("not not")

    def test_unsupported_function_raises(self) -> None:
        """不支持的函数名抛 ConditionError。"""
        cond = parse_if("unknown_func()")
        with pytest.raises(ConditionError, match="不支持的函数"):
            cond({})

    def test_function_with_args_raises(self) -> None:
        """success() 传参抛 ConditionError。"""
        cond = parse_if("success('arg')")
        with pytest.raises(ConditionError, match="不接受参数"):
            cond({})

    def test_unsupported_variable_raises(self) -> None:
        """不支持的变量名抛 ConditionError。"""
        cond = parse_if("unknown_var == 'x'")
        with pytest.raises(ConditionError, match="不支持的标识符"):
            cond({})

    def test_unsupported_attribute_object_raises(self) -> None:
        """不支持的属性访问对象抛 ConditionError。"""
        cond = parse_if("other.attr == 'x'")
        with pytest.raises(ConditionError, match="不支持的变量"):
            cond({})

    def test_attribute_on_literal_raises(self) -> None:
        """字面量属性访问（如 'str'.upper）抛 ConditionError。"""
        cond = parse_if("'str'.upper")
        with pytest.raises(ConditionError, match="仅支持 ctx"):
            cond({})

    def test_method_call_raises(self) -> None:
        """方法调用（如 'str'.upper()）抛 ConditionError。"""
        cond = parse_if("'str'.upper()")
        with pytest.raises(ConditionError, match="仅支持 success"):
            cond({})

    def test_unsupported_compare_op_raises(self) -> None:
        """不支持的比较运算（``is``）抛 ConditionError。"""
        cond = parse_if("ctx.x is None")
        with pytest.raises(ConditionError, match="不支持的比较运算"):
            cond({"x": 3})

    def test_unsupported_unary_op_raises(self) -> None:
        """不支持的一元运算抛 ConditionError。"""
        cond = parse_if("-ctx.x")
        with pytest.raises(ConditionError, match="不支持的一元运算"):
            cond({"x": 3})

    def test_subscript_not_supported(self) -> None:
        """下标访问 ctx['x'] 抛 ConditionError。"""
        cond = parse_if("ctx['x'] == 'y'")
        with pytest.raises(ConditionError, match="不支持的表达式节点"):
            cond({})

    def test_condition_has_reason_attr(self) -> None:
        """解析后的 condition 函数带 _reason 属性。"""
        cond = parse_if("success()")
        assert hasattr(cond, "_reason")
        assert "success()" in cond._reason  # type: ignore[attr-defined]


# ============================================================================ #
# expand_matrix
# ============================================================================ #
class TestExpandMatrix:
    """matrix 笛卡尔积展开测试。"""

    def test_empty_matrix(self) -> None:
        """空 matrix 返回单个空组合。"""
        assert expand_matrix({}) == [{}]

    def test_single_key_single_value(self) -> None:
        """单键单值返回单个组合。"""
        result = expand_matrix({"py": ["3.8"]})
        assert result == [{"py": "3.8"}]

    def test_single_key_multiple_values(self) -> None:
        """单键多值返回多个组合。"""
        result = expand_matrix({"py": ["3.8", "3.9"]})
        assert result == [{"py": "3.8"}, {"py": "3.9"}]

    def test_multiple_keys_cartesian(self) -> None:
        """多键笛卡尔积展开。"""
        result = expand_matrix({"py": ["3.8", "3.9"], "os": ["linux", "windows"]})
        assert len(result) == 4
        assert {"py": "3.8", "os": "linux"} in result
        assert {"py": "3.8", "os": "windows"} in result
        assert {"py": "3.9", "os": "linux"} in result
        assert {"py": "3.9", "os": "windows"} in result

    def test_value_not_list_raises(self) -> None:
        """matrix 值非列表抛 ConditionError。"""
        with pytest.raises(ConditionError, match="必须是列表"):
            expand_matrix({"py": "3.8"})  # type: ignore[arg-type]

    def test_empty_values_list(self) -> None:
        """matrix 值为空列表返回空结果（无组合）。"""
        result = expand_matrix({"py": []})
        assert result == []

    def test_non_string_values(self) -> None:
        """matrix 值支持非字符串（数字等）。"""
        result = expand_matrix({"count": [1, 2, 3]})
        assert result == [{"count": 1}, {"count": 2}, {"count": 3}]


# ============================================================================ #
# substitute_matrix_vars
# ============================================================================ #
class TestSubstituteMatrixVars:
    """``${{ matrix.X }}`` 占位符替换测试。"""

    def test_single_substitution(self) -> None:
        """单个占位符替换。"""
        assert substitute_matrix_vars("echo ${{ matrix.py }}", {"py": "3.8"}) == "echo 3.8"

    def test_multiple_substitutions(self) -> None:
        """多个占位符替换。"""
        text = "${{ matrix.py }} on ${{ matrix.os }}"
        result = substitute_matrix_vars(text, {"py": "3.9", "os": "linux"})
        assert result == "3.9 on linux"

    def test_missing_key_kept_as_is(self) -> None:
        """未在 combo 中的 key 保持原样。"""
        text = "${{ matrix.py }} ${{ matrix.unknown }}"
        result = substitute_matrix_vars(text, {"py": "3.8"})
        assert result == "3.8 ${{ matrix.unknown }}"

    def test_no_placeholders(self) -> None:
        """无占位符原样返回。"""
        assert substitute_matrix_vars("plain text", {"py": "3.8"}) == "plain text"

    def test_empty_combo(self) -> None:
        """空 combo 时占位符保持原样。"""
        assert substitute_matrix_vars("${{ matrix.py }}", {}) == "${{ matrix.py }}"

    def test_whitespace_in_placeholder(self) -> None:
        """占位符内允许空白。"""
        assert substitute_matrix_vars("${{  matrix.py  }}", {"py": "3.8"}) == "3.8"

    def test_non_string_value_coerced(self) -> None:
        """非字符串值被转为字符串。"""
        assert substitute_matrix_vars("count=${{ matrix.n }}", {"n": 42}) == "count=42"


# ============================================================================ #
# matrix_suffix
# ============================================================================ #
class TestMatrixSuffix:
    """matrix 任务名后缀生成测试。"""

    def test_empty_combo(self) -> None:
        """空组合返回空字符串。"""
        assert matrix_suffix({}) == ""

    def test_single_key(self) -> None:
        """单键后缀格式。"""
        assert matrix_suffix({"py": "3.8"}) == "(py-3.8)"

    def test_multiple_keys(self) -> None:
        """多键后缀格式（按插入顺序）。"""
        assert matrix_suffix({"py": "3.8", "os": "linux"}) == "(py-3.8)(os-linux)"

    def test_non_string_value(self) -> None:
        """非字符串值用 str() 转换。"""
        assert matrix_suffix({"count": 3}) == "(count-3)"
