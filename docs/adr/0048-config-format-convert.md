# 0048 配置格式跨转换：`tomltool convert` 与 `yamtool convert`

- 状态：已落地
- 日期：2026-11-24
- 关联：[0037](0037-unified-result-envelope.md)（结果信封 / 错误码）、[0047](0047-confirm-gate-and-filetime.md)（对照补齐排期）

## 背景

对照 rscmd 后确认缺口：fcmd 的 `tomltool` / `yamtool` 只能读与格式化，
不能在 JSON / YAML / TOML 之间转换。Python 标准库只有 TOML 读取
（`tomllib`），没有写方向序列化，因此「YAML → TOML」需要自己的写出逻辑；
「TOML → JSON / YAML」可直接复用既有读取与 `PyYAML`。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 新增 `tomltool convert <file> [--to json\|yaml] [--output PATH]`，默认 `json` 输出到标准输出 | 扩展 `format` 子命令加 `--to`（`format` 语义是「TOML 转 JSON 打印」，混入多目标会产生歧义） | `tools/data/tomltool.py` |
| D2 | 新增 `yamtool convert <file> [--to json\|toml] [--output PATH]`，默认 `json` | 复用 `pretty`（`pretty` 是 YAML 内部格式化，不跨格式） | `tools/data/yamtool.py` |
| D3 | TOML 写方向自行实现 `infras.data.to_toml`：标量 / 数组 / 子表 / 表数组；`None`、非字符串键、未知类型抛 `ValueError` | 引入 `tomli-w`（多一个运行依赖，而所需子集很小）；只支持 `TOML → 其他`（缺口里「YAML → TOML」正是用户最常要的方向） | `infras/data/toml.py` |
| D4 | 转换失败按统一退出码报错：源文件缺失 4、语法/不可表达 3、写目标失败 1；成功时 `--output` 打印「已写入: <路径>」 | 沿用该工具既有的「打印错误、退出 0」（机读方无法判定失败，与 0037 相悖） | `core/result.py`、`tools/data/_convert.py` |

`--output` 省略写标准输出；给出时父目录自动创建，覆盖写入同内容文件。

## 验证

- `tests/tools/data/test_cli_tomltool.py`：`to_toml` 的标量 / 数组 / 子表 /
  表数组 / 空映射 / 往返解析 / 引号与转义 / `nan`/`inf` / 日期时间 /
  四类非法输入；`convert` 的 JSON、YAML、写文件、非法 `--to`（2）、
  源文件缺失（4）、源语法错误（3）、缺 PyYAML（1）、写目标失败（1）；
- `tests/tools/data/test_cli_yamtool.py`：`convert` 的 JSON、TOML（回读校验）、
  写文件、非法 `--to`（2）、源文件缺失（4）、源语法错误（3）、
  顶层非映射转 TOML（3）、缺 PyYAML（1）；
- `tests/cli/test_tools_manifest.py`：静态清单随两个新子命令重生成。

## 后果

- 三种配置文件格式可互相搬运，`fcmd <tool> convert ... --output` 即可脚本化；
- 新增子命令不改动既有 `get` / `keys` / `format` / `pretty` / `validate` 的行为；
- `to_toml` 只覆盖 TOML 能表达的形态，不做隐式字符串化（避免把 `None`
  静默写成 `"None"` 这类失真）。

## 交接

- 其余对照缺口按「价值 / 成本」继续：只读环境变量查询、`pathtool join` /
  绝对路径判定、`completion powershell`、`txttool count` 字节数、
  `stattool percentile`；`--json` 空缺工具按 [0037](0037-unified-result-envelope.md)
  P2 继续接入；
- 目录锁、通用断点续传、缓存框架、PyO3 绑定明确不补（见 0047 交接）。
