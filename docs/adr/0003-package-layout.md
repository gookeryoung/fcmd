# 0003 包内目录结构与布局不变量

- 状态：决策已全部落地（B0–B4）
- 日期：2026-11-10

## 背景

"层级过深"不是问题本身：目录每多一层，必须换来一种新的隔离维度，否则只是路径噪音。盘点后真正的病灶只有三处：

- `dsl/actions/toolcmd/<域>.py` 是唯一的三层嵌套，域名与 `tools/<域>` 重复，未提供新信息；
- GUI 共享基座（`tools/_guiwindow.py` / `_guitheme.py` / `_guiconfig.py`）寄居在工具发现命名空间里，只能靠 `_` 前缀躲开扫描；
- `tools/ui/` 空壳目录，以及 `tools/data` / `tools/hash` 这类单文件域，属于名义分层。

同时，三套机制隐式依赖目录形状：工具发现（顶层 + 一层领域子包）、静态清单的布局指纹（`tools/*.py`、`apps/*.py` 与 `tools/<域>/*.py`）、动作懒加载表（`_index` 的模块路径）。布局被无意改动时，三者都不会报错，只会静默回退或漏发现。

## 决策

### D1 保留 src 布局与 `tools/<域>` 域层（不压平）

- `src/` 布局由 hatch `packages=["src/fcmd"]`、`tox.ini` 的 `PYTHONPATH=src` 与 pytest 共同确定；搬到仓库根会让 `pip install -e .` 把仓库杂项塞进 `sys.path`。
- `tools/<域>` 保留：域提供归属、可选依赖 extras 边界（archive/img/ocr/office/pdf/pe/win32）与测试镜像；62 个工具模块零重名只是当前巧合，压平后单目录 62 文件、`tests/tools/<域>` 镜像失效，`pyproject.toml` 48 条硬编码入口与数百处测试引用都要改。收益是少一层目录，代价是失去归属与镜像，**不压平**。

### D2 模块深度不变量：`fcmd/<包>/[<子包>/]<模块>.py`

包内目录不超过 2 层。唯一违反项 `dsl/actions/toolcmd/` 提升为 `dsl/toolcmd/`，与 `dsl/actions/` 平级——通用动作与工具专用动作仍是两个包（用结构表达 ADR 0002 的边界），但不需要第三层目录。`_index.ACTION_MODULES` 因此改存绝对模块路径，懒加载直接按路径导入。

### D3 单文件域不构成包

`src/fcmd/tools/data/database.py` 提为 `src/fcmd/tools/database.py`，`src/fcmd/tools/hash/hashbase.py` 提为 `src/fcmd/tools/hashbase.py`，删除 `tools/data`、`tools/hash` 两个单文件子包。依据：单文件包只增加路径长度，不提供隔离；工具名取模块名末段，顶层模块与领域子包内模块在发现机制里等价。

代价：`fcmd.tools.data.database` 与 `fcmd.tools.hash.hashbase` 是半公开导入路径（README 未承诺，但被 dsl 动作与测试复用），改为 `fcmd.tools.database` / `fcmd.tools.hashbase`，不保留兼容 shim（与 README「不保留兼容 shim」口径一致）。

### D4 展示基座独立成 `fcmd/gui`

`tools/_guiwindow.py` / `_guitheme.py` / `_guiconfig.py` → `fcmd/gui/window.py` / `theme.py` / `config.py`。依据：三者只依赖标准库 tkinter，被十余个窗口复用，不属于任何业务域；`tools/` 是发现命名空间，共享代码靠 `_` 前缀躲扫描是设计破损信号。

窗口本身默认与业务同域（`<tool>w.py`）；独立桌面应用（如 `cmder`，命令 `cmderw`）归 `fcmd/apps/`，与 `fcmd.tools` 同为工具发现根。

### D5 生成物就地、只重生成

`cli/_manifest_data.py`（工具清单 + 布局指纹 + 列表描述 + 源码内容指纹）与 `dsl/actions/_index.py`（动作 → 模块与静态签名）留在消费方包内，因为必须在 wheel 内可导入。重生成命令固定为 `uv run python -m fcmd.cli._manifest_data` 与 `uv run python -m fcmd.dsl.actions._index`，禁止手改，一致性由测试校验。

### D6 布局不变量由测试守卫

`tests/test_layout.py` 固化五条断言：模块深度 ≤2 层、无空目录、含 `.py` 的目录必须有 `__init__.py`、工具名全局唯一、领域子包不再嵌套。注入式目录已验证断言会红。

## 落地状态

- B0：删除 `tools/ui/` 空壳目录（无跟踪文件），由 D6 的断言防止复发。
- B1：新增 `tests/test_layout.py`，五条断言全绿。
- B2：`dsl/actions/toolcmd/` → `dsl/toolcmd/`；`_index` 改存绝对模块路径并重生成。
- B4：`tools/data`、`tools/hash` 两个单文件域提为顶层模块，`_manifest_data` 布局指纹同步重生成。
- B5：`tools/_guiwindow.py` / `_guitheme.py` / `_guiconfig.py` → `fcmd/gui/window.py` / `theme.py` / `config.py`，测试镜像迁至 `tests/gui/`；布局指纹不含下划线模块，清单无需重生成。
- B6：独立应用包 `cmder`（命令 `cmderw`，实现模块统一 `_` 前缀）迁至 `fcmd/apps/`，测试迁至 `tests/apps/cmder/`；工具发现与静态清单的扫描根同步扩展为 `tools` + `apps`。

## 交接

- 新增工具：放 `tools/<域>/<工具>.py`。只有单个模块支撑的能力不要新建子包，直接放 `tools/<工具>.py`。
- 新增独立桌面应用：在 `apps/<应用>/` 下新建包（入口 `app.py`，`__init__.py` 只做门面与注册），包名默认即工具名；目录名需与命令解耦时在 `__init__.py` 声明 `__tool_name__`（如 `cmder` → `cmderw`）。
- 新增动作：通用动作进 `dsl/actions/`，工具专用声明进 `dsl/toolcmd/`，随后重生成 `_index`。
- 改动 `tools/` 或 `apps/` 目录形状后必须重生成 `_manifest_data`，并跑 `tests/test_layout.py` 与 `tests/cli/test_tools_manifest.py`。
