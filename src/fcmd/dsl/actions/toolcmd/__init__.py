"""工具专用动作层：只提供命令，不承载通用动作语义。

本子包的每个模块对应一个工具域（如 ``calc`` / ``data`` / ``media``），
内容只有 ``@action`` 声明与「参数转接 / 结果呈现」代码——本质是把
:mod:`fcmd.tools` 对应域的能力包一层 CLI 外壳。真正的业务实现放在
``fcmd.tools.<域>``，通用可复用动作放在 ``fcmd.dsl.actions`` 顶层
（``basic`` / ``fileops`` / ``filenames`` / ``system`` / ``genid``）。

与顶层动作模块一样，本包不在导入期加载任何子模块：动作名 → 子模块的
定位由 :mod:`fcmd.dsl.actions._index` 静态表维护，``has_action`` 只查表，
``get_action`` 才按需导入。
"""
