# Bug: 迁移后 DSL 命令入口启动延迟增大

> Status: FIXED
> Mode: (default)
> Severity: functional
> Author: user
> Last updated: 2026-10-07

## Symptom

iter-22 CLI 迁移收尾后，部分命令（尤其经 `fcmd.dsl.entry:run_named` 的 34 个
DSL 声明式命令）启动延迟明显增大。

## Expected

迁移后命令启动延迟应与迁移前独立模块入口相当（几十毫秒级）。

## Reproduction

- 命令 / 步骤：子进程执行 `import fcmd.dsl.entry` +
  `ensure_tools_discovered()`，`perf_counter` 计时。
- 测试位置：`tests/test_dsl.py::TestBootstrap::test_run_path_does_not_load_heavy_libs`
- 复现稳定性：确定性失败（非时序），断言重型库不出现在 `sys.modules`。

## Hypotheses & diagnosis

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | `fcmd.dsl.actions.media` 顶层 try-import cairosvg/PIL，每个 DSL 命令启动都加载 | confirmed (root cause) | `python -X importtime`：media 累计 437ms（cairosvg 228 + PIL 141，importtime 口径）；实测 `import cairosvg` 144ms + `PIL.Image` 50ms |
| H2 | net/envdev/packtool 顶层 `urllib.request`（→ssl 链）拖慢发现与动作注册 | confirmed (secondary root cause) | 实测 `urllib.request` 37ms；importtime 树定位三处顶层导入 |
| H3 | `fcmd.dsl.__init__` eager 全量导入 17 个 action 子模块本身是瓶颈 | eliminated（对本次而言） | 子模块多为轻量 stdlib；重负载在可选依赖，动作注册在 TOML 声明校验期必然发生，仅把重型依赖下沉即可回收 ~195ms |
| H4 | `ensure_tools_discovered` 全量导入 cli 模块是主因 | eliminated | 实测发现仅 +40~46ms，且为 `fcmd` 主入口既有设计 |

## Root cause

迁移后所有 console script 统一入口 `run_named` → `ensure_tools_discovered()`
→ TOML 声明校验 → 导入全部 17 个动作子模块；而 media/net/envdev/packtool
在模块顶层 eager 导入重型库（cairosvg 144ms + PIL 50ms + ssl 链 37ms），
使每个命令启动固定多付约 230ms。media.py docstring 本承诺"工具发现阶段
不触发重型库加载"，实现与文档矛盾。

## Fix

- `src/fcmd/dsl/actions/media.py`：PIL/cairosvg 改 `find_spec` 探测 +
  `_require_pil`/`_require_cairosvg` 首次调用惰性导入（镜像既有
  `_require_pymupdf` 模式）；`icon_build`/`_load_font` 增加直调兜底。
- `src/fcmd/dsl/actions/net.py`：`urllib.request` 下沉到 5 个发起请求的函数内。
- `src/fcmd/cli/dev/envdev.py`：urllib 三件套移入 `fetch_mirrorz_sites`。
- `src/fcmd/cli/dev/packtool.py`：`urllib.request` 移入下载分支。
- `src/fcmd/dsl/actions/archive.py`：`shutil` 移入 `archive_folder`。
- 测试 patch 指向定义模块（项目约定）：envdev/nettool/urlcheck 的 urlopen
  → `urllib.request.urlopen`；packtool 的 urlretrieve → `urllib.request.urlretrieve`。

## Verification

- V-1: 新增回归测试 RED（修复前）→ GREEN（修复后）✓
- V-2: 修改影响面 8 个测试文件全量对比 stash 前后失败集，完全一致（104 个
  失败均为存量"工具未注册"测试隔离问题，与本次无关）✓
- V-3: `make check` 全绿（2892 passed，覆盖率 95.88%）+ `pytest -m slow`
  7 passed（perf 基线无回归）✓
- V-4: 命令启动路径实测 316ms → 134ms（-57%，子进程 perf_counter 中位）

## Regression test

- 路径：`tests/test_dsl.py::TestBootstrap::test_run_path_does_not_load_heavy_libs`
- 名称：DSL 命令全路径（entry 导入 + 工具发现）不触发 cairosvg/PIL/ssl 加载

## Pattern analysis

| 搜索方式 | 命中数 | 是否本次同类隐患 |
|---|---|---|
| `grep "^import (PIL\|cairosvg\|fitz\|pypdf\|urllib.request)"` 于 dsl/actions 与 cli | 0（修复后） | 否，已清零 |

## Open questions / Follow-ups

- 存量测试隔离问题：单文件运行时有大量"工具 'xxx' 未注册"失败（104+9），
  依赖其他测试文件触发发现的导入副作用；make check 全量运行不暴露。
  建议为相关测试补 `ensure_tools_discovered` fixture（独立迭代处理）。
- fcmd.dsl.actions 仍为导入期全量注册（~97ms stdlib 开销）；若需进一步
  压缩到 <50ms，需把动作注册改为按需加载并推迟声明校验时机（涉及
  loader/synth 设计，属结构性改动，需单独评估）。
- `run_named` 对 DSL 命令可跳过 Python 模块导入（别名解析语义需权衡，
  收益 ~40ms）。
