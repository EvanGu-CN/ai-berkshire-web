# -*- coding: utf-8 -*-
"""金融严谨性工具封装：调用 ai-berkshire/tools/financial_rigor.py。"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .paths import FINANCIAL_RIGOR


def run_tool(command: str, args: list[str]) -> dict[str, Any]:
    if not FINANCIAL_RIGOR.is_file():
        return {"ok": False, "output": "financial_rigor.py 未找到"}
    cmd = [sys.executable, str(FINANCIAL_RIGOR), command, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, encoding="utf-8"
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "计算超时"}
    except OSError as e:
        return {"ok": False, "output": f"执行失败：{e}"}
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "output": out.strip()}


def verify_market_cap(price: float, shares: float, reported: float, currency: str = "USD") -> dict:
    return run_tool("verify-market-cap", [
        "--price", str(price), "--shares", str(shares),
        "--reported", str(reported), "--currency", currency,
    ])


def verify_valuation(price: float, eps: float | None = None, bvps: float | None = None,
                     fcf_per_share: float | None = None, dividend: float | None = None) -> dict:
    args = ["--price", str(price)]
    if eps is not None:
        args += ["--eps", str(eps)]
    if bvps is not None:
        args += ["--bvps", str(bvps)]
    if fcf_per_share is not None:
        args += ["--fcf-per-share", str(fcf_per_share)]
    if dividend is not None:
        args += ["--dividend", str(dividend)]
    return run_tool("verify-valuation", args)


def cross_validate(field: str, values: dict, unit: str = "") -> dict:
    args = ["--field", field, "--values", json.dumps(values, ensure_ascii=False)]
    if unit:
        args += ["--unit", unit]
    return run_tool("cross-validate", args)


def calc(expr: str) -> dict:
    return run_tool("calc", ["--expr", expr])


def three_scenario(price: float, eps: float, shares: float,
                   growth: tuple[float, float, float],
                   pe: tuple[float, float, float],
                   years: int = 5, currency: str = "CNY") -> dict:
    """三情景估值：growth/pe 依次为乐观、中性、悲观。"""
    return run_tool("three-scenario", [
        "--price", str(price), "--eps", str(eps), "--shares", str(shares),
        "--growth", str(growth[0]), str(growth[1]), str(growth[2]),
        "--pe", str(pe[0]), str(pe[1]), str(pe[2]),
        "--years", str(years), "--currency", currency,
    ])
