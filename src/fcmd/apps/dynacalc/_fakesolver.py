"""dynacalc 假求解器：确定性生成 ``glstat`` / ``matsum`` / ``history.csv``。

用法（由 :mod:`fcmd.apps.dynacalc._solver` 以子进程拉起）::

    python -m fcmd.apps.dynacalc._fakesolver <deck_dir>

输出只由 ``case.json`` 中的编码与指纹决定，不含时间戳，便于端到端测试。
测试注入：

- ``DYNACALC_FAKE_FAIL_CODES``：逗号分隔的工况编码，命中则退出码 1；
- ``DYNACALC_FAKE_DELAY_MS``：每个工况的模拟耗时（毫秒），用于超时测试；
- ``DYNACALC_FAKE_QUENCH_CODES``：逗号分隔的工况编码，末端反应分数停在 0.95。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

_SAMPLES = 61
_BLOCKS = 11
_LENGTH_M = 0.3


def main(argv: list[str] | None = None) -> int:
    """假求解器入口：返回进程退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("用法: python -m fcmd.apps.dynacalc._fakesolver <deck_dir>", file=sys.stderr)
        return 2
    deck = Path(args[0])
    case_path = deck / "case.json"
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"假求解器无法读取 {case_path}: {exc}", file=sys.stderr)
        return 2
    code = str(case.get("code") or "")
    fingerprint = str(case.get("fingerprint") or "")
    delay_ms = _float_env("DYNACALC_FAKE_DELAY_MS")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    if code in _codes_env("DYNACALC_FAKE_FAIL_CODES"):
        print(f"假求解器注入失败: {code}", file=sys.stderr)
        return 1
    quench = code in _codes_env("DYNACALC_FAKE_QUENCH_CODES")
    _write_outputs(deck, fingerprint, quench)
    print(f"假求解器完成: {code}")
    return 0


def _codes_env(name: str) -> set[str]:
    """读取逗号分隔的工况编码环境变量。"""
    return {item.strip() for item in os.environ.get(name, "").split(",") if item.strip()}


def _float_env(name: str) -> float:
    """读取浮点环境变量，非法值按 0 处理。"""
    try:
        return float(os.environ.get(name, "0") or 0)
    except ValueError:
        return 0.0


def _seed(fingerprint: str) -> int:
    """指纹转整数；指纹缺失时用固定值，保证确定性。"""
    try:
        return int(fingerprint[:8], 16)
    except ValueError:
        return sum(ord(char) for char in fingerprint) or 1


def _write_outputs(deck: Path, fingerprint: str, quench: bool) -> None:
    """写出三件套结果文件（UTF-8、LF）。"""
    seed = _seed(fingerprint)
    velocity = 6500.0 + seed % 400
    duration = _LENGTH_M / velocity
    plateau = 0.95 if quench else 1.0
    wall_peak = 80.0 + seed % 80
    rows = ["time,front_z,reaction_fraction,wall_velocity,wall_strain"]
    for index in range(_SAMPLES):
        time_s = duration * index / (_SAMPLES - 1)
        started = duration * 0.05
        front = velocity * (time_s - started * (1.0 - math.exp(-time_s / started))) if time_s > 0 else 0.0
        reaction = min(plateau, time_s / (duration * 0.15)) if duration > 0 else plateau
        wall_velocity = wall_peak * math.sin(math.pi * time_s / duration)
        strain = 0.02 * time_s / duration + 0.005 * math.sin(2.0 * math.pi * time_s / duration) ** 2
        rows.append(f"{time_s:.6e},{front:.6e},{reaction:.6e},{wall_velocity:.6e},{strain:.6e}")
    _write(deck / "history.csv", "\n".join(rows) + "\n")

    energy = 1.0e4 + seed % 500
    glstat: list[str] = []
    for index in range(_BLOCKS):
        time_s = duration * index / (_BLOCKS - 1)
        kinetic = 0.5 * energy * math.sin(math.pi * time_s / duration) ** 2
        internal = energy * (0.1 + 0.9 * time_s / duration)
        glstat.extend(
            [
                f" time = {time_s:.6e}",
                f"    kinetic energy = {kinetic:.6e}",
                f"    internal energy = {internal:.6e}",
                f"    total energy = {kinetic + internal:.6e}",
                "",
            ]
        )
    _write(deck / "glstat", "\n".join(glstat) + "\n")

    matsum: list[str] = []
    for index in range(_BLOCKS):
        time_s = duration * index / (_BLOCKS - 1)
        internal = energy * (0.1 + 0.9 * time_s / duration)
        matsum.extend(
            [
                f" time = {time_s:.6e}",
                "    part id = 1",
                f"      internal energy = {internal * 0.6:.6e}",
                "    part id = 2",
                f"      internal energy = {internal * 0.4:.6e}",
                "",
            ]
        )
    _write(deck / "matsum", "\n".join(matsum) + "\n")


def _write(path: Path, text: str) -> None:
    """写入 UTF-8 + LF 文本。"""
    path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":  # pragma: no cover - 子进程入口
    raise SystemExit(main())
