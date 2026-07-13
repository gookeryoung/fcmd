# fcmd

极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。

## 特性

- **极速冷启动**（< 100ms）：三层懒加载，核心模块零外部依赖
- **DAG 任务调度**：四种执行策略（sequential/thread/async/dependency）
- **组合 API 简洁**：`@fx.task` 装饰器 + 自动依赖推断 + `@fx.tool` CLI 工具
- **纯 CLI（rich 增强）**：彩色输出、表格、进度条
- **便捷脚本模式**：`fcmd pymake b` 一键调用

## 快速上手

```python
import fcmd as fx

@fx.task
def extract() -> list[int]: return [1, 2, 3]

@fx.task
def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]

graph = fx.graph(extract, double)  # double 自动依赖 extract
report = fx.run(graph)
print(report["double"])  # [2, 4, 6]
```

```bash
fcmd pymake b           # 构建
fcmd pymake tc          # 类型检查
fcmd --list             # 列出所有工具
```

## 许可证

MIT
