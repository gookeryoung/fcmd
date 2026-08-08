"""``python -m fcmd`` 入口。"""

from fcmd.cli.main import main

if __name__ == "__main__":  # pragma: no cover - 脚本入口，pytest 经 import 触发，不可达
    main()
