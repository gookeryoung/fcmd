# iter-42 P41 xmltool XML 处理

## 需求清单

- [x] 新增 xmltool 工具：基于标准库 xml.etree.ElementTree 实现 pretty/minify/extract/validate

## 迭代目标

R5：xmltool - XML 处理（format/extract/validate），覆盖 XML 数据处理场景。

## 改动文件清单

- src/fcmd/cli/xmltool.py（新建）：XML 处理工具
- tests/test_cli_xmltool.py（新建）：xmltool 测试（40 个用例）
- pyproject.toml：注册 `xmltool` 入口
- tests/test_cli.py：在 `_MAIN_ENTRY_TOOLS` 注册 xmltool

## 关键决策与依据

1. **仅用标准库 ElementTree**：与项目「stdlib first」约定一致；XML 处理无需 lxml 等第三方库。
2. **属性提取通过路径尾 ``/@attr`` 实现**：ElementTree 的 findall 不支持 ``.//item/@id`` 直接取属性值，需手动拆分路径：先 findall 元素，再 ``.get(attr)`` 取属性。
3. **XPath 错误统一转 ET.ParseError**：ElementTree 对无效 XPath 可能抛 SyntaxError 或 TypeError，统一转换为 ET.ParseError 简化 CLI 错误处理。
4. **minify_xml 移除纯空白文本节点**：避免 pretty_xml 缩进产生的空白残留；保留有意义文本。
5. **不修改原元素**：pretty_xml / minify_xml / write_xml 均通过 ``copy.deepcopy`` 操作克隆，避免副作用。
6. **移除不可达的 isinstance 检查**：findall 元素路径必返回 Element，移除多余防御代码（遵循 python-standards「Don't add error handling for scenarios that can't happen」）。

## 代码实现情况

- `read_xml(path) -> Element`：读取 XML 文件
- `write_xml(path, element, indent=2)`：写入 XML 文件（自动缩进 + XML 声明）
- `pretty_xml(element, indent=2) -> str`：格式化为多行 XML
- `minify_xml(element) -> str`：压缩为单行 XML（移除空白文本节点）
- `extract_xml(element, xpath) -> list[str]`：XPath 提取元素文本或属性值
- `validate_xml(path) -> None`：良构校验
- 4 个 CLI 子命令 `pretty`/`minify`/`extract`/`validate`

## 整合优化情况

- 移除 CLI 中 ``except (SyntaxError, TypeError)`` 不可达分支（extract_xml 已转换为 ET.ParseError）
- 简化 extract_xml 元素文本分支为推导式（移除 isinstance 防御代码）
- 修复 ruff F401：移除冗余 ``import fcmd.cli.xmltool``（from-import 已触发工具注册）

## 测试验证结果

- `make check` 全部通过
- 1690 passed, 2 deselected
- 覆盖率 99.39%（≥95% 阈值且高于上轮 99.38%）
- xmltool.py 100% 覆盖
- 测试覆盖：注册验证、6 个公共函数（含成功/错误/边界）、4 个 CLI 子命令（含成功/不存在/良构错误/无命中/XPath 错误）

## 遗留事项

- 不支持 DTD/Schema 校验（保持工具简洁）
- 不支持命名空间映射（ElementTree 的 {ns}tag 语法用户需自行处理）
- 不支持 XPath 1.0 完整语法（如 ``text()``、``string()`` 函数）

## 下一轮计划

R6：yamtool - YAML 处理（read/write/validate，复用项目已有的 PyYAML 依赖）。
