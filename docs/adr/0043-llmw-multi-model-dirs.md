# 0043 llmw 多模型目录与 Steam 式存储卡片

- 状态：已落地
- 日期：2026-11-22
- 关联：`src/fcmd/apps/llm/_llmconfig.py`、`src/fcmd/apps/llm/_llmw_storage.py`、
  `src/fcmd/apps/llm/_llmw_qt_storage.py`、`src/fcmd/apps/llm/_llmw_qt_settings.py`、
  `src/fcmd/gui/style.py`、[ADR 0031](0031-llm-config-unification.md)

## 背景

ADR 0031 把模型目录收敛为单一 `models_dir`，只保留一个下载落点与扫描根。实际使用中
模型会分散在多块盘：系统盘放小模型、数据盘放大模型、移动硬盘放归档；单目录迫使
用户来回切换配置，切换后另一块盘的模型就从运行页 / 模型页消失。设置页的「模型目录」
只有一行路径输入框，也看不出目录所在盘符与剩余空间。

## 决策

### 1. 配置：`models_dirs` 列表 + 首个主目录

`config.json` 新增权威键 `models_dirs: [str]`，首个目录是**主目录**：

- 下载（`llmdown` 默认落点）、转换 / 量化产物（`<主目录>/converted/`）固定写主目录，
  避免「多个目录都有 converted、产物到底在哪」的歧义；
- 模型扫描（运行页 / 模型页 / `llmmodel` / 画图资产）遍历全部目录，按真实路径去重；
  每条模型结果保留 `root`，仍可反推仓库标识；
- `FCMD_MODELS` 与 `--models-root` 仍是单目录覆盖（返回单元素列表），保持 CLI 兼容；
- 旧键 `models_dir` 继续可读，并作为 `models_dirs` 的旧版回退；写入列表时同步
  `models_dir = 首项`，让仍读单值键的调用方拿到主目录；
- 旧多值迁移记录 `models_dir_extra` 不自动加入扫描列表（避免把已失效路径重新引入），
  设置页提示用户按需添加。

`llmenv add` 改为追加目录（重复目录保持原位置），`llmenv remove` 移除指定目录
（移除主目录时下一项接任），`reset` 清空回到默认；`llmenv show` 逐行列出全部模型目录。

### 2. 设置页「存储」：Steam 式磁盘卡片并排

参考 Steam 的存储管理页，每个模型目录是一张磁盘卡：

- 标题行：硬盘图标 + 盘符 / 挂载点（`D:` 或 `/mnt/data`）+ 文件系统（NTFS / ext4）；
- 路径行：中间省略的完整路径，tooltip 显示全路径；
- 容量行：已用比例进度条 + 「剩余 X / 共 Y」；目录不存在或无法读取容量时给文字提示；
- 动作行：打开、设为主目录（非主目录时显示）、移除（至少保留一个目录；
  移除只改列表，不删除磁盘文件）；
- 卡片最多三列并排，超出换行；末尾固定一张虚线「添加模型目录」卡；
- 主目录卡片带「下载目标」胶囊，设为主目录即调整列表顺序并立即生效。

共享控件边界：磁盘卡与网格放在 llm 专用模块 `_llmw_qt_storage`（不在 `fcmd.gui`
里造「存储卡」通用控件）；`fcmd.gui` 只提供既有的 `Card` / `ElidedLabel` / `StatusTag`
与新增的 `StorageCard` / `StorageAdd` 主题样式。

### 3. 盘符 / 容量的数据来源

`_llmw_storage.locations()` 把配置路径整理为可展示数据：用
`_llmw_hardware.disk_infos()`（`psutil` 固定分区）按最长挂载点匹配盘符与文件系统，
用 `shutil.disk_usage` 读取总容量 / 剩余容量；路径不存在时向上找最近存在的父目录
取所在分区，并标记 `exists=False`。设置页只渲染，不保存第二份目录信息。

## 测试口径

- `tests/apps/llm/test_llmconfig.py`：列表读写、去重、环境变量单目录覆盖、空列表回落默认；
- `tests/apps/llm/test_llmw_storage.py`：主目录标记、容量换算、目录不存在、增删 / 设主目录；
- `tests/apps/llm/test_llmw_qt_storage.py`：卡片并排构造、只剩一个目录时不显示移除；
- 既有单目录测试保留：`set_models_dir` / `configured_models_dir` 仍按主目录语义工作。

## 落地状态与交接

- 已完成：配置列表 + 主目录、扫描遍历全部目录、设置页磁盘卡并排、`llmenv` 增删改查。
- 迁移提示保留：`models_dir_extra` 只提示不自动启用；用户确认后在卡片上添加。
- 下一步（未做）：拖拽卡片调整顺序（当前用「设为主目录」改变主目录）；跨目录同名仓库
  在模型页的文件状态仍以首个命中目录判断。

## 交接

- 多目录设置已抽成共享组件 `fcmd.gui.widgets.StorageCard`：内部 `StorageRoots`
  负责去重 / 添加 / 移除 / 设为主目录（至少保留一个目录，移除只从列表删、不删磁盘
  文件），`DiskGrid` 负责磁盘卡展示；变更只发 `changed`，由宿主落盘。
  llmw 设置页直接使用 `StorageCard`，`_llmw_storage.py` 只保留目录模型与磁盘信息。
