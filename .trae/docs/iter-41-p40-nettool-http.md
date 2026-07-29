# iter-41 P40 nettool HTTP 客户端

## 需求清单

- [x] 新增 nettool 工具：使用标准库 urllib 实现 HTTP GET/POST/HEAD 请求

## 迭代目标

R4：nettool - HTTP 客户端（urllib GET/POST/HEAD），覆盖网络请求场景。

## 改动文件清单

- src/fcmd/cli/nettool.py（新建）：HTTP 客户端工具
- tests/test_cli_nettool.py（新建）：nettool 测试
- pyproject.toml：注册 `nettool` 入口
- tests/test_cli.py：在 `_MAIN_ENTRY_TOOLS` 注册 nettool

## 关键决策与依据

1. **仅用标准库 urllib**：与项目「stdlib first」约定一致，无需引入 requests/httpx。
2. **公共函数 + CLI 子命令双层 API**：`http_get`/`http_post`/`http_head` 可被其他模块复用，CLI 子命令包装面向终端用户。
3. **错误处理在 CLI 层**：公共函数直接抛 `HTTPError`/`URLError`，CLI 子命令捕获并打印友好消息，遵循 python-cli SKILL「业务异常转友好消息」。
4. **HEAD 返回 dict**：`http_head` 返回 `dict[str, str]` 而非原始 `Message` 对象，简化跨场景使用。
5. **默认 30 秒超时**：避免脚本因网络问题无限阻塞。

## 代码实现情况

- `http_get(url, timeout=30) -> str`：GET 请求，UTF-8 解码响应体
- `http_post(url, data="", timeout=30) -> str`：POST 请求，data 编码为 UTF-8 字节
- `http_head(url, timeout=30) -> dict[str, str]`：HEAD 请求，返回响应头字典
- 三个 CLI 子命令 `get`/`post`/`head`，参数与公共函数对齐
- 标准 `main()` 入口委托 `_common.run_tool_main("nettool")`

## 整合优化情况

- 修复测试中残留的 `netool_safe()` 拼写错误调用：原代码引用了不存在的 `fcmd.cli.netool_safe` 属性，统一改为直接 `fcmd.cli.nettool`
- 修复 `MagicMock.__enter__()` 默认返回新 mock 的问题：显式配置 `resp.__enter__.return_value = resp`，使 `with urlopen(...) as resp:` 拿到配置好的同一 mock
- 修复 ruff UP012 警告：`str.encode("utf-8")` 改为 `str.encode()`（UTF-8 为默认）
- 修复 pyrefly 类型错误：`HTTPError` 第 4 参数 `hdrs` 应为 `email.message.Message`，构造 `Message()` 实例传入

## 测试验证结果

- `make check` 全部通过
- 1649 passed, 2 deselected
- 覆盖率 99.38%（≥95% 阈值），nettool.py 100% 覆盖
- 测试覆盖：注册验证、3 个公共函数的成功/Unicode/超时/错误路径、3 个 CLI 子命令的成功/错误路径

## 遗留事项

- 无真实网络请求测试（避免 CI flaky），完全依赖 mock urlopen
- 未支持自定义请求头、cookie、auth 等高级特性（保持工具简洁，遵循「不为未来预留扩展点」）

## 下一轮计划

R5：xmltool - XML 处理（format/extract/validate），使用标准库 `xml.etree.ElementTree`。
