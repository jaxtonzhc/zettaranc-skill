#!/usr/bin/env python3
"""稳健性检验：独立时段分块 + 参数敏感性。

1) 把 2020-11~2026-08 切成 4 个互不重叠的时段，看策略在每个独立时段的胜败；
2) 对 V2 的每个关键参数单独扰动，看全窗口收益是否稳（过拟合的特征是参数一抖结果就塌）。
"""

import sys
from pathlib import Path

from etf_portfolio_backtest import fmt_pct, metrics_from_prices
from etf_strategy_early import PORTFOLIO_C, load_klines as load_hfq
from etf_strategy_v2 import (
    ADX_MIN,
    BUY_WINDOW,
    REDUCE_RATIO,
    RSI_MIN,
    run_strategy,
)

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"


def align(frames, names):
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    return sorted(common)


def run_pf(frames, names, common, **kw):
    navs = []
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        nav, *_ = run_strategy(rows, "graduated", True, **kw)
        navs.append(nav[-1])
    return sum(navs) / len(navs) - 1


def hold_ret(frames, names, common):
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    v0 = sum(by_day[nm][common[0]] for nm in names) / len(names)
    v1 = sum(by_day[nm][common[-1]] for nm in names) / len(names)
    return v1 / v0 - 1


def main() -> int:
    frames = load_hfq()
    common = align(frames, PORTFOLIO_C)

    # ── 1) 独立时段分块（互不重叠）──
    blocks = [
        ("B1 2020-11~2022-05", "2020-11-16", "2022-05-31"),
        ("B2 2022-06~2023-11", "2022-06-01", "2023-11-30"),
        ("B3 2023-12~2025-05", "2023-12-01", "2025-05-31"),
        ("B4 2025-06~2026-08", "2025-06-01", "2026-08-19"),
    ]
    lines = []
    w = lines.append
    w("# 稳健性检验：独立时段 + 参数敏感性")
    w("")
    w("## 一、互不重叠的 4 个时段（策略每个时段独立从 1.0 起步）")
    w("")
    w("| 时段 | 持有 | V2 策略 | 谁赢 |")
    w("|------|------|---------|------|")
    print("\n=== 独立时段分块 ===")
    for label, s, e in blocks:
        sub = [d for d in common if s <= d <= e]
        hr = hold_ret(frames, PORTFOLIO_C, sub)
        sr = run_pf(frames, PORTFOLIO_C, sub)
        win = "策略" if sr > hr else "持有"
        w(f"| {label} | {fmt_pct(hr)} | {fmt_pct(sr)} | {win} |")
        print(f"{label}: 持有 {fmt_pct(hr)}  V2 {fmt_pct(sr)}  → {win}")

    # ── 2) 参数敏感性（全窗口 C）──
    w("")
    w("## 二、参数敏感性（全窗口，一次只动一个参数）")
    w("")
    w("| 参数 | 基准值 | 扰动 | 策略累计（全窗口） |")
    w("|------|--------|------|------------------|")
    baseline = run_pf(frames, PORTFOLIO_C, common)
    w(f"| 基准 V2 | - | - | {fmt_pct(baseline)} |")
    print(f"\n基准 V2（全窗口）: {fmt_pct(baseline)}")
    sens = [
        ("买入窗口", BUY_WINDOW, 1),
        ("买入窗口", BUY_WINDOW, 3),
        ("ADX 阈值", ADX_MIN, 20),
        ("ADX 阈值", ADX_MIN, 30),
        ("RSI 阈值", RSI_MIN, 45),
        ("RSI 阈值", RSI_MIN, 55),
        ("减仓比例", REDUCE_RATIO, 0.33),
    ]
    for name, base, val in sens:
        kw = {}
        if name == "买入窗口":
            kw["buy_window"] = val
        elif name == "ADX 阈值":
            kw["adx_min"] = val
        elif name == "RSI 阈值":
            kw["rsi_min"] = val
        elif name == "减仓比例":
            kw["reduce_ratio"] = val
        r = run_pf(frames, PORTFOLIO_C, common, **kw)
        w(f"| {name} | {base} | {val} | {fmt_pct(r)} |")
        print(f"  {name} {base}→{val}: {fmt_pct(r)}")

    w("")
    w("## 三、解读")
    w("")
    w("- 如果策略只在某 1~2 个时段赢、参数一动结果就大幅波动 → 过拟合/脆弱。")
    w("- 如果多数时段赢且参数扰动下收益变化平缓 → 规则有真实内核。")
    report_path = OUT_DIR / f"etf_robustness_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))
    print(f"\n报告: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
