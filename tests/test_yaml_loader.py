"""YAML 任务编排（简化版）测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.dag import Graph
from fcmd.yaml_loader import load_yaml, parse_yaml_string


def _echo_cmd(text: str) -> list[str]:
    """跨平台 echo 命令：Windows 用 cmd /c，Unix 直接 echo。"""
    if sys.platform == "win32":
        return ["cmd", "/c", "echo", text]
    return ["echo", text]


# ---------------------------------------------------------------------- #
# 基本解析
# ---------------------------------------------------------------------- #
class TestBasicParsing:
    """基本字段解析测试。"""

    def test_parse_simple_cmd_job(self) -> None:
        """cmd 列表形式正确解析。"""
        graph = parse_yaml_string("""
jobs:
  hello:
    cmd: ["echo", "hello"]
""")
        assert "hello" in graph
        spec = graph.spec("hello")
        assert spec.cmd == ["echo", "hello"]

    def test_parse_run_as_shell_string(self) -> None:
        """run 字段作为 shell 字符串存入 cmd。"""
        graph = parse_yaml_string("""
jobs:
  greet:
    run: "echo hello | wc -l"
""")
        spec = graph.spec("greet")
        assert spec.cmd == "echo hello | wc -l"

    def test_parse_needs_dependency(self) -> None:
        """needs 列表形式正确转为 depends_on。"""
        graph = parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
  b:
    needs: [a]
    cmd: ["echo", "b"]
""")
        assert graph.dependencies("b") == ("a",)

    def test_parse_needs_single_string(self) -> None:
        """needs 单字符串形式也支持。"""
        graph = parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
  b:
    needs: a
    cmd: ["echo", "b"]
""")
        assert graph.dependencies("b") == ("a",)

    def test_parse_timeout(self) -> None:
        """timeout 字段转为 float。"""
        graph = parse_yaml_string("""
jobs:
  slow:
    cmd: ["sleep", "10"]
    timeout: 300
""")
        assert graph.spec("slow").timeout == 300.0

    def test_parse_retry(self) -> None:
        """retry 字段完整解析为 RetryPolicy。"""
        graph = parse_yaml_string("""
jobs:
  flaky:
    cmd: ["curl", "http://example.com"]
    retry: {max_attempts: 3, delay: 1.0, backoff: 2.0}
""")
        spec = graph.spec("flaky")
        assert spec.retry.max_attempts == 3
        assert spec.retry.delay == 1.0
        assert spec.retry.backoff == 2.0

    def test_parse_retry_partial_fields(self) -> None:
        """retry 部分字段：未指定的字段用 RetryPolicy 默认值。"""
        graph = parse_yaml_string("""
jobs:
  flaky:
    cmd: ["echo", "flaky"]
    retry: {delay: 0.5}
""")
        spec = graph.spec("flaky")
        assert spec.retry.max_attempts == 1
        assert spec.retry.delay == 0.5

    def test_parse_retry_with_jitter(self) -> None:
        """retry 字段含 jitter（覆盖 _parse_retry 全部分支）。"""
        graph = parse_yaml_string("""
jobs:
  flaky:
    cmd: ["echo", "flaky"]
    retry: {max_attempts: 3, delay: 1.0, backoff: 2.0, jitter: 0.5}
""")
        spec = graph.spec("flaky")
        assert spec.retry.max_attempts == 3
        assert spec.retry.delay == 1.0
        assert spec.retry.backoff == 2.0
        assert spec.retry.jitter == 0.5

    def test_parse_env(self) -> None:
        """env 字段转为 dict。"""
        graph = parse_yaml_string("""
jobs:
  build:
    cmd: ["make", "all"]
    env: {CI: "true", DEBUG: "1"}
""")
        spec = graph.spec("build")
        assert spec.env == {"CI": "true", "DEBUG": "1"}

    def test_parse_cwd(self) -> None:
        """cwd 字段转为 Path。"""
        graph = parse_yaml_string("""
jobs:
  build:
    cmd: ["make", "all"]
    cwd: /tmp/build
""")
        assert graph.spec("build").cwd == Path("/tmp/build")

    def test_parse_verbose(self) -> None:
        """verbose 字段转为 bool。"""
        graph = parse_yaml_string("""
jobs:
  build:
    cmd: ["echo", "build"]
    verbose: true
""")
        assert graph.spec("build").verbose is True

    def test_parse_tags(self) -> None:
        """tags 字段转为 tuple。"""
        graph = parse_yaml_string("""
jobs:
  build:
    cmd: ["echo", "build"]
    tags: [api, build]
""")
        assert graph.spec("build").tags == ("api", "build")

    def test_parse_strategy_per_job(self) -> None:
        """单任务 strategy 字段覆盖图级策略。"""
        graph = parse_yaml_string("""
jobs:
  build:
    cmd: ["echo", "build"]
    strategy: thread
""")
        assert graph.spec("build").strategy == "thread"

    def test_parse_cmd_string_form(self) -> None:
        """cmd 字段允许字符串形式（非列表）。"""
        graph = parse_yaml_string("""
jobs:
  hi:
    cmd: "echo hi"
""")
        spec = graph.spec("hi")
        assert spec.cmd == "echo hi"

    def test_parse_cmd_list_coerced_to_str(self) -> None:
        """cmd 列表中非字符串元素被转为字符串。"""
        graph = parse_yaml_string("""
jobs:
  count:
    cmd: ["echo", 42]
""")
        spec = graph.spec("count")
        assert spec.cmd == ["echo", "42"]


# ---------------------------------------------------------------------- #
# hyphen/underscore 字段名兼容
# ---------------------------------------------------------------------- #
class TestFieldnameCompat:
    """hyphen/underscore 字段名兼容测试。"""

    def test_continue_on_error_hyphen(self) -> None:
        """continue-on-error（hyphen）正确解析。"""
        graph = parse_yaml_string("""
jobs:
  flaky:
    cmd: ["echo", "flaky"]
    continue-on-error: true
""")
        assert graph.spec("flaky").continue_on_error is True

    def test_continue_on_error_underscore(self) -> None:
        """continue_on_error（underscore）正确解析。"""
        graph = parse_yaml_string("""
jobs:
  flaky:
    cmd: ["echo", "flaky"]
    continue_on_error: true
""")
        assert graph.spec("flaky").continue_on_error is True

    def test_allow_upstream_skip_hyphen(self) -> None:
        """allow-upstream-skip（hyphen）正确解析。"""
        graph = parse_yaml_string("""
jobs:
  cleanup:
    cmd: ["echo", "cleanup"]
    allow-upstream-skip: true
""")
        assert graph.spec("cleanup").allow_upstream_skip is True

    def test_allow_upstream_skip_underscore(self) -> None:
        """allow_upstream_skip（underscore）正确解析。"""
        graph = parse_yaml_string("""
jobs:
  cleanup:
    cmd: ["echo", "cleanup"]
    allow_upstream_skip: true
""")
        assert graph.spec("cleanup").allow_upstream_skip is True


# ---------------------------------------------------------------------- #
# 图级默认值
# ---------------------------------------------------------------------- #
class TestGraphDefaults:
    """图级 defaults 与 strategy 字段测试。"""

    def test_graph_level_strategy(self) -> None:
        """strategy 字段设为图级默认策略。"""
        graph = parse_yaml_string("""
strategy: thread
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.strategy == "thread"

    def test_graph_level_defaults_retry(self) -> None:
        """defaults.retry 设为图级默认重试策略。"""
        graph = parse_yaml_string("""
defaults:
  retry: {max_attempts: 3}
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.retry is not None
        assert graph.defaults.retry.max_attempts == 3

    def test_graph_level_defaults_env(self) -> None:
        """defaults.env 设为图级默认环境变量。"""
        graph = parse_yaml_string("""
defaults:
  env: {CI: "true"}
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.env == {"CI": "true"}

    def test_graph_level_defaults_cwd(self) -> None:
        """defaults.cwd 设为图级默认工作目录。"""
        graph = parse_yaml_string("""
defaults:
  cwd: /tmp
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.cwd == Path("/tmp")

    def test_graph_level_defaults_timeout(self) -> None:
        """defaults.timeout 设为图级默认超时。"""
        graph = parse_yaml_string("""
defaults:
  timeout: 60
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.timeout == 60.0

    def test_graph_level_defaults_continue_on_error(self) -> None:
        """defaults.continue_on_error / continue-on-error 都支持。"""
        graph_hyphen = parse_yaml_string("""
defaults:
  continue-on-error: true
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph_hyphen.defaults.continue_on_error is True

    def test_graph_level_defaults_strategy_in_defaults(self) -> None:
        """defaults.strategy 也可设置图级策略。"""
        graph = parse_yaml_string("""
defaults:
  strategy: async
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.strategy == "async"

    def test_graph_level_defaults_tags(self) -> None:
        """defaults.tags 设为图级默认标签。"""
        graph = parse_yaml_string("""
defaults:
  tags: [ci, build]
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.tags == ("ci", "build")

    def test_graph_level_defaults_verbose(self) -> None:
        """defaults.verbose 设为图级默认 verbose。"""
        graph = parse_yaml_string("""
defaults:
  verbose: true
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.verbose is True

    def test_graph_strategy_overrides_defaults_strategy(self) -> None:
        """顶层 strategy 覆盖 defaults.strategy。"""
        graph = parse_yaml_string("""
strategy: thread
defaults:
  strategy: sequential
jobs:
  a:
    cmd: ["echo", "a"]
""")
        assert graph.defaults.strategy == "thread"

    def test_resolved_spec_uses_graph_defaults(self) -> None:
        """resolved_spec 在 spec 字段为默认空值时回退到图级默认值。"""
        graph = parse_yaml_string("""
defaults:
  retry: {max_attempts: 5}
jobs:
  a:
    cmd: ["echo", "a"]
""")
        resolved = graph.resolved_spec("a")
        assert resolved.retry.max_attempts == 5


# ---------------------------------------------------------------------- #
# 错误处理
# ---------------------------------------------------------------------- #
class TestErrorHandling:
    """错误场景测试。"""

    def test_empty_yaml_raises(self) -> None:
        """空 YAML 文档（safe_load 返回 None）抛 ValueError。"""
        with pytest.raises(ValueError, match="根节点必须是映射"):
            parse_yaml_string("")

    def test_root_not_mapping_raises(self) -> None:
        """根节点非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="根节点必须是映射"):
            parse_yaml_string("- item1\n- item2\n")

    def test_jobs_not_mapping_raises(self) -> None:
        """jobs 非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="jobs 必须是映射"):
            parse_yaml_string("jobs: [a, b]\n")

    def test_job_missing_cmd_and_run_raises(self) -> None:
        """job 缺少 cmd 和 run 抛 ValueError。"""
        with pytest.raises(ValueError, match="必须提供 cmd 或 run"):
            parse_yaml_string("""
jobs:
  broken:
    needs: [a]
""")

    def test_job_not_mapping_raises(self) -> None:
        """job 数据非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="必须是映射"):
            parse_yaml_string("""
jobs:
  broken: "just a string"
""")

    def test_invalid_yaml_syntax_raises(self) -> None:
        """非法 YAML 语法抛 ValueError（包装 YAMLError）。"""
        with pytest.raises(ValueError, match="YAML 解析失败"):
            parse_yaml_string("key: [unclosed bracket\n")

    def test_retry_not_mapping_raises(self) -> None:
        """retry 非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="retry 必须是映射"):
            parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
    retry: 3
""")

    def test_env_not_mapping_raises(self) -> None:
        """env 非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="env 必须是映射"):
            parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
    env: "not a mapping"
""")

    def test_tags_not_list_raises(self) -> None:
        """tags 非列表抛 ValueError。"""
        with pytest.raises(ValueError, match="tags 必须是列表"):
            parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
    tags: "not a list"
""")

    def test_needs_invalid_type_raises(self) -> None:
        """needs 非列表/字符串抛 ValueError。"""
        with pytest.raises(ValueError, match="needs 必须是列表或字符串"):
            parse_yaml_string("""
jobs:
  a:
    cmd: ["echo", "a"]
  b:
    needs: 42
    cmd: ["echo", "b"]
""")

    def test_defaults_not_mapping_raises(self) -> None:
        """defaults 非映射抛 ValueError。"""
        with pytest.raises(ValueError, match="defaults 必须是映射"):
            parse_yaml_string("""
defaults: "not a mapping"
jobs:
  a:
    cmd: ["echo", "a"]
""")

    def test_load_yaml_file_not_found(self, tmp_path: Path) -> None:
        """load_yaml 文件不存在抛 OSError。"""
        with pytest.raises(OSError):
            load_yaml(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------- #
# load_yaml 文件读取
# ---------------------------------------------------------------------- #
class TestLoadYamlFile:
    """load_yaml 文件读取测试。"""

    def test_load_yaml_from_file(self, tmp_path: Path) -> None:
        """从 YAML 文件加载任务图。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            """
jobs:
  setup:
    cmd: ["echo", "setup"]
  build:
    needs: [setup]
    cmd: ["echo", "build"]
""",
            encoding="utf-8",
        )
        graph = load_yaml(yaml_file)
        assert "setup" in graph
        assert "build" in graph
        assert graph.dependencies("build") == ("setup",)

    def test_load_yaml_with_defaults(self, tmp_path: Path) -> None:
        """从文件加载时图级 defaults 正确解析。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            """
strategy: thread
defaults:
  env: {CI: "true"}
  retry: {max_attempts: 2}
jobs:
  a:
    cmd: ["echo", "a"]
""",
            encoding="utf-8",
        )
        graph = load_yaml(yaml_file)
        assert graph.defaults.strategy == "thread"
        assert graph.defaults.env == {"CI": "true"}
        assert graph.defaults.retry is not None
        assert graph.defaults.retry.max_attempts == 2


# ---------------------------------------------------------------------- #
# Graph.from_yaml 集成
# ---------------------------------------------------------------------- #
class TestGraphFromYaml:
    """Graph.from_yaml classmethod 集成测试。"""

    def test_from_yaml_classmethod(self, tmp_path: Path) -> None:
        """Graph.from_yaml 等价于 load_yaml。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            """
jobs:
  a:
    cmd: ["echo", "a"]
""",
            encoding="utf-8",
        )
        graph = Graph.from_yaml(yaml_file)
        assert isinstance(graph, Graph)
        assert "a" in graph
        assert graph.spec("a").cmd == ["echo", "a"]

    def test_from_yaml_preserves_defaults(self, tmp_path: Path) -> None:
        """Graph.from_yaml 保留图级默认值。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            """
strategy: thread
jobs:
  a:
    cmd: ["echo", "a"]
""",
            encoding="utf-8",
        )
        graph = Graph.from_yaml(yaml_file)
        assert graph.defaults.strategy == "thread"


# ---------------------------------------------------------------------- #
# 集成执行（端到端）
# ---------------------------------------------------------------------- #
class TestYamlExecution:
    """YAML 任务图端到端执行测试。"""

    def test_execute_simple_graph(self) -> None:
        """解析后的图可直接执行（cmd 任务）。"""
        from fcmd.executors import run

        # 跨平台 echo：Windows 用 cmd /c，Unix 用 echo
        echo_cmd = '["cmd", "/c", "echo", "hello"]' if sys.platform == "win32" else '["echo", "hello"]'
        graph = parse_yaml_string(f"""
jobs:
  hello:
    cmd: {echo_cmd}
""")
        report = run(graph)
        assert report.success

    def test_execute_dag_with_deps(self) -> None:
        """带依赖的 DAG 解析后按拓扑序执行。"""
        from fcmd.executors import run

        echo = _echo_cmd
        graph = parse_yaml_string(f"""
jobs:
  setup:
    cmd: {echo("setup")}
  build:
    needs: [setup]
    cmd: {echo("build")}
  deploy:
    needs: [build]
    cmd: {echo("deploy")}
""")
        report = run(graph, strategy="sequential")
        assert report.success
        # 所有任务都应成功
        for name in ("setup", "build", "deploy"):
            assert report.result_of(name).status.value == "success"

    def test_execute_with_only_filter(self) -> None:
        """only= 参数只运行指定 job 及其依赖。"""
        from fcmd.executors import run

        echo = _echo_cmd
        graph = parse_yaml_string(f"""
jobs:
  setup:
    cmd: {echo("setup")}
  build:
    needs: [setup]
    cmd: {echo("build")}
  deploy:
    needs: [build]
    cmd: {echo("deploy")}
""")
        # 只执行 build（及依赖 setup），不执行 deploy
        report = run(graph, only=["build"])
        assert report.success
        assert report.result_of("setup").status.value == "success"
        assert report.result_of("build").status.value == "success"
        # deploy 不在 only 范围内，不在报告中
        assert "deploy" not in report
