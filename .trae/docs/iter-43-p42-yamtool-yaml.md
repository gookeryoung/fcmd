# iter-43 P42 yamtool YAML 处理

## 需求清单

- [x] 新增 yamtool 工具：基于 PyYAML（已是项目依赖）实现 pretty/get/keys/validate

## 迭代目标

R6：yamtool - YAML 处理（read/write/validate），复用项目已有 PyYAML 依赖。

## 改动文件清单

- src/fcmd/cli/yamtool.py（新建）：YAML 处理工具
- tests/test_cli_yamtool.py（新建）：yamtool 测试（50 个用例）
- pyproject.toml：注册 `yamtool` 入口
- tests/test_cli.py：在 `_MAIN_ENTRY_TOOLS` 注册 yamtool

## 关键决策与依据

1. **顶层导入 yaml**：参考既有 yaml_loader.py 模式，使用 `# type: ignore[import-not-found]` 抑制 pyrefly 警告；yamtool 模块仅在工具调用时被加载，不影响 fcmd 冷启动。
2. **使用 yaml.safe_load/safe_dump**：避免 arbitrary code execution，与项目安全约束一致。
3. **allow_unicode=True + default_flow_style=False**：保留 Unicode 字符（不转义）、强制 block 风格输出（与 jsontool 一致的可读性优先）。
4. **复用 jsontool 的点路径查询模式**：`get_yaml` 与 `query_json` 逻辑一致（数字段→列表索引，其他段→字典键），便于用户跨工具迁移。
5. **keys_yaml 区分 dict/list**：dict 返回键列表，list 返回索引字符串列表（"0"/"1"/...），标量抛 TypeError。
6. **CLI 错误处理与 jsontool/xmltool 一致**：FileNotFoundError 与 yaml.YAMLError 分支捕获并友好打印。

## 代码实现情况

- `read_yaml(path) -> Any`：读取 YAML 文件
- `write_yaml(path, data, sort_keys=False)`：写入 YAML 文件（自动创建父目录）
- `pretty_yaml(data, sort_keys=False, indent=2) -> str`：格式化为 YAML 字符串
- `get_yaml(data, path) -> Any`：点路径查询
- `keys_yaml(data) -> list[str]`：顶层键列举
- `validate_yaml(path) -> None`：语法校验
- 4 个 CLI 子命令 `pretty`/`get`/`keys`/`validate`

## 整合优化情况

- 重构：将 yaml 导入从惰性（函数内 import）改为顶层（参考 yaml_loader.py），简化代码并精确捕获 `yaml.YAMLError`（替代宽泛的 `except Exception`，避免 `# noqa: BLE001`）
- 补测：YAML 顶层为标量时 `keys` 命令的 TypeError 处理路径，恢复 100% 覆盖

## 测试验证结果

- `make check` 全部通过
- 1740 passed, 2 deselected
- 覆盖率 99.41%（高于上轮 99.39%）
- yamtool.py 100% 覆盖
- 测试覆盖：注册验证、6 个公共函数（含成功/Unicode/排序/错误/边界）、4 个 CLI 子命令（含成功/不存在/语法错误/标量/排序）

## 遗留事项

- 不支持多文档 YAML（``yaml.safe_load_all``），保持工具简洁
- 不支持自定义 YAML 标签（``safe_load`` 拒绝 ``!!python/object`` 等）
- 不支持 YAML Schema 校验（如 JSON Schema for YAML），保持工具简洁

## 下一轮计划

R7：timetool - 时间工具（convert/format/timezone），基于标准库 datetime 与 zoneinfo。
