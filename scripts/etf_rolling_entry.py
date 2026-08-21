#!/usr/bin/env python3
"""滚动入场分析：任意时点入场，持有 vs 双金叉策略(V2) 的收益分布。

回应"持有收益是早期入场红利"的质疑：固定起点(2020-11 底部)算持有收益是路径依赖的。
这里对窗口内每 5 个交易日作为一个入场点，分别测 持有/策略 未来 1年/2年 收益，
看分布（均值/中位数/分位/胜率），并单独统计"高位入场"（近 1 年已大涨后入场）的情形。
"""

import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from etf_portfolio_backtest import fmt_pct, load_font
from etf_strategy_early import PORTFOLIO_C, load_klines as load_hfq
from etf_strategy_v2 import run_strategy

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"


def main() -> int:
    frames = load_hfq()
    common = None
    for nm in PORTFOLIO_C:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    n = len(common)

    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in PORTFOLIO_C}
    hold_px = []
    for d in common:
        v = sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in PORTFOLIO_C) / len(
            PORTFOLIO_C
        )
        hold_px.append(v)

    # 高位判定：入场前 1 年累计涨幅（买入时已涨很多 = 高位追入）
    hi_1y = [hold_px[i] / hold_px[i - 252] - 1 if i >= 252 else None for i in range(n)]

    horizons = [126, 252, 504]
    step = 5
    results = {h: {"hold": [], "strat": [], "win": 0, "n": 0} for h in horizons}
    hi_results = {h: {"hold": [], "strat": [], "win": 0, "n": 0} for h in horizons}

    # 单只 ETF 的完整 K 线（用于策略指标），按公共日期对齐
    aligned_rows = {nm: [by_day[nm][d] for d in common] for nm in PORTFOLIO_C}

    for e in range(0, n - min(horizons), step):
        for h in horizons:
            if e + h >= n:
                continue
            hr = hold_px[e + h] / hold_px[e] - 1
            # 策略：各 ETF 从 e 起步到 e+h，等权平均
            strat_navs = []
            for nm in PORTFOLIO_C:
                rows = aligned_rows[nm]
                nav, *_ = run_strategy(rows, "graduated", True, start=e, end=e + h)
                strat_navs.append(nav[-1])
            sr = sum(strat_navs) / len(strat_navs) - 1
            results[h]["hold"].append(hr)
            results[h]["strat"].append(sr)
            results[h]["n"] += 1
            if sr > hr:
                results[h]["win"] += 1
            if hi_1y[e] is not None and hi_1y[e] > 0.30:  # 追高入场：近1年已涨>30%
                hi_results[h]["hold"].append(hr)
                hi_results[h]["strat"].append(sr)
                hi_results[h]["n"] += 1
                if sr > hr:
                    hi_results[h]["win"] += 1

    load_font()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, h, title in [
        (axes[0], 252, "入场后 1 年收益分布"),
        (axes[1], 504, "入场后 2 年收益分布"),
    ]:
        ax.boxplot(
            [results[h]["hold"], results[h]["strat"]],
            tick_labels=["持有", "V2 策略"],
            widths=0.5,
        )
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_title(f"{title}（{results[h]['n']} 个入场点）")
        ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"etf_rolling_entry_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    lines = []
    w = lines.append
    w("# 滚动入场分析：持有 vs 双金叉策略(V2)")
    w("")
    w(f"> 数据：C 代理聚焦四只（{common[0]} ~ {common[-1]}，{n} 交易日），每 5 个交易日一个入场点，策略参数与 V2 相同。")
    w("")
    w("## 一、为什么做这个分析")
    w("")
    w("- 固定起点（2020-11，接近底部）的持有收益是路径依赖的，不代表当下全仓买入也能拿到。")
    w("- 本分析把所有可能的入场时点都测一遍，看**收益分布**而不是单一路径。")
    w("")
    w("## 二、任意时点入场的收益分布")
    w("")
    for h, label in [(126, "半年"), (252, "1年"), (504, "2年")]:
        r = results[h]
        w(f"### 入场后 {label}（{r['n']} 个入场点）")
        w("")
        w("| 统计量 | 持有 | V2 策略 |")
        w("|--------|------|--------|")
        for stat, fn in [
            ("均值", statistics.mean),
            ("中位数", statistics.median),
            ("P25", lambda x: sorted(x)[int(len(x) * 0.25)]),
            ("P75", lambda x: sorted(x)[int(len(x) * 0.75)]),
            ("最差", min),
            ("最好", max),
        ]:
            w(f"| {stat} | {fmt_pct(fn(r['hold']))} | {fmt_pct(fn(r['strat']))} |")
        w(f"| 策略胜率 | - | {r['win']/r['n']*100:.1f}% |")
        w("")
    w("## 三、追高入场（入场前 1 年已涨 >30%）")
    w("")
    w("| 入场后 | 持有均值 | 策略均值 | 持有中位数 | 策略中位数 | 策略胜率 | 样本数 |")
    w("|--------|---------|---------|-----------|-----------|---------|-------|")
    for h, label in [(126, "半年"), (252, "1年"), (504, "2年")]:
        r = hi_results[h]
        if r["n"] == 0:
            continue
        w(
            f"| {label} | {fmt_pct(statistics.mean(r['hold']))} | {fmt_pct(statistics.mean(r['strat']))} "
            f"| {fmt_pct(statistics.median(r['hold']))} | {fmt_pct(statistics.median(r['strat']))} "
            f"| {r['win']/r['n']*100:.1f}% | {r['n']} |"
        )
    w("")
    w("## 四、低位入场（入场前 1 年涨幅 <0%）")
    w("")
    lo_results = {h: {"hold": [], "strat": [], "win": 0, "n": 0} for h in horizons}
    for e in range(0, n - min(horizons), step):
        for h in horizons:
            if e + h >= n:
                continue
            if hi_1y[e] is not None and hi_1y[e] < 0.0:
                hr = hold_px[e + h] / hold_px[e] - 1
                strat_navs = []
                for nm in PORTFOLIO_C:
                    rows = aligned_rows[nm]
                    nav, *_ = run_strategy(rows, "graduated", True, start=e, end=e + h)
                    strat_navs.append(nav[-1])
                sr = sum(strat_navs) / len(strat_navs) - 1
                lo_results[h]["hold"].append(hr)
                lo_results[h]["strat"].append(sr)
                lo_results[h]["n"] += 1
                if sr > hr:
                    lo_results[h]["win"] += 1
    w("| 入场后 | 持有均值 | 策略均值 | 持有中位数 | 策略中位数 | 策略胜率 | 样本数 |")
    w("|--------|---------|---------|-----------|-----------|---------|-------|")
    for h, label in [(126, "半年"), (252, "1年"), (504, "2年")]:
        r = lo_results[h]
        if r["n"] == 0:
            continue
        w(
            f"| {label} | {fmt_pct(statistics.mean(r['hold']))} | {fmt_pct(statistics.mean(r['strat']))} "
            f"| {fmt_pct(statistics.median(r['hold']))} | {fmt_pct(statistics.median(r['strat']))} "
            f"| {r['win']/r['n']*100:.1f}% | {r['n']} |"
        )
    w("")
    w("## 五、结论速览")
    w("")
    w("- 固定起点收益（持有 +93.91%）只是所有入场路径中的一条，不能代表当下入场。")
    w("- 看分布：策略 vs 持有的中位数/胜率，高位入场时策略的防御价值更明显。")
    report_path = OUT_DIR / f"etf_rolling_entry_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    print("\n=== 任意时点入场收益分布 ===")
    for h, label in [(126, "半年"), (252, "1年"), (504, "2年")]:
        r = results[h]
        print(f"\n入场后{label}（{r['n']} 个入场点）:")
        print(f"  持有: 均值 {statistics.mean(r['hold']):+.2%}  中位 {statistics.median(r['hold']):+.2%}  P25 {sorted(r['hold'])[int(len(r['hold'])*0.25)]:+.2%}")
        print(f"  策略: 均值 {statistics.mean(r['strat']):+.2%}  中位 {statistics.median(r['strat']):+.2%}  P25 {sorted(r['strat'])[int(len(r['strat'])*0.25)]:+.2%}")
        print(f"  策略胜率: {r['win']/r['n']*100:.1f}%")
    print("\n=== 追高入场（近1年已涨>30%）===")
    for h, label in [(126, "半年"), (252, "1年"), (504, "2年")]:
        r = hi_results[h]
        if r["n"]:
            print(f"  {label}: 持有均值 {statistics.mean(r['hold']):+.2%} vs 策略均值 {statistics.mean(r['strat']):+.2%}，胜率 {r['win']/r['n']*100:.1f}% (n={r['n']})")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
