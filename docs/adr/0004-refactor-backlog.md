# 0004 重构待办的触发条件

- 状态：生效（记录暂缓项；到期条件满足时按条目执行）
- 日期：2026-11-10

## 背景

2026-11-10 的可维护性 / 扩展性审计给出了一份分档方案（低风险快赢 / 中等 /
大改 / 不建议）。低风险项已在本轮独立提交落地；部分中等项经评估后**本轮不做**，
但若不落文字，后续只会有「有人说要拆」而没有依据。本 ADR 只记录这些暂缓项与
重新评估的触发条件，不改变任何既有决策。

审计基线（2026-11-19 复核）：`src` 381 文件 / 117,231 行，`tests` 304 文件 / 93,433 行。
后续判断收益时以文件数、行数与 `# noqa` / `# pragma: no cover` 的增减为准。

## 决策

### M7：`tests/cli/test_cli.py` 直捣 `_discovery` 内部 —— 暂缓

该文件 45 处 `monkeypatch` 直接改写 `_discovery` 的模块级私有表
（`_TOOL_MODULES` / `_TOOL_ALIASES` / `importlib.import_module`），一旦发现机制
内部重构，测试会成片失败且失败原因指向实现细节而非行为。

**触发条件**：新增 / 修改 `_discovery` 内部结构（注册表形态、扫描顺序、别名表）
时，顺手在 `_discovery` 上提供公共测试接缝（例如 `install_fake_registry(mapping)`），
并把该文件的 patch 逐步迁移过去。

**为何本轮不做**：属纯粹整洁债，无产品收益；且迁移期间的失败模式与断言强度难以
逐条核对，风险高于收益。

## 交接

- 新增暂缓项时按同样格式补一节（内容 / 触发条件 / 为何暂缓），并登记到
  `docs/adr/README.md` 索引（本 ADR 已登记）。
- 已完成 / 已消失：M1（`_require_pypdf` 收敛进 `pdfbase`）、M2（`_pair_row` 随 Tk
  共享层删除而消失，见 0034）、M4（DSL 移除后 `toolcmd` 体积问题不再存在）、
  M6（`ttk_style_specs` 随 Tk 移除而删除）。
- 仍待决策：M3（各 Qt 测试文件重复的 `home` / `window` fixture 上收
  `tests/conftest.py`；`create_application` 与主题应用已缓存，收益主要在减少样板）。
- 独立立项：目录比较 / 同步引擎合并见 [0035](0035-cmder-filesync-compare-unify.md)
  （不属于本 ADR 的暂缓项）。
