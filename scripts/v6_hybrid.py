#!/usr/bin/env python3
"""V6：双金叉进场 + 创指 MA120 择时出场（死叉一律不动）。

规则：
  买入：创业板指 > MA120（大盘牛市）时，个股 2 日内双金叉 → 次日开盘满仓
  持有：中间任何死叉/指标信号都不动
  卖出：创业板指收盘跌破 MA120 → 次日开盘清仓
  空仓后：等下一次「大盘在 MA120 之上 + 个股双金叉」再进
"""

import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "scripts")
from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_v2 import kdj, macd, run_strategy
from v5_trend_follow import fetch_index

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003

TECH = ["晶方科技", "卓胜微", "用友网络", "广联达"]
ALL10 = ["晶方科技", "卓胜微", "用友网络", "广联达", "华能蒙电", "申能股份", "平煤股份", "广汇能源", "云南铜业", "中金岭南"]


def run_v6(rows, regime, exit_confirm=False):
    """双金叉进（大盘牛市），创指破 MA120 出；死叉不动。exit_confirm=跌破确认1天。"""
    n = len(rows)
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)
    macd_gold = [False] * n
    kdj_gold = [False] * n
    for i in range(1, n):
        macd_gold[i] = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        kdj_gold[i] = ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]

    def win(arr, i, w):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    cash, shares = 1.0, 0.0
    nav, buys, sells, in_days = [], 0, 0, 0
    regime_off_since = -10
    for i in range(n):
        order = None
        if i > 0:
            if shares == 0:
                if regime[i - 1] and win(macd_gold, i - 1, 2) and win(kdj_gold, i - 1, 2):
                    order = "BUY"
            else:
                if not regime[i - 1]:
                    if not exit_confirm:
                        order = "EXIT"
                    elif regime_off_since >= 0 and i - 1 - regime_off_since >= 1:
                        order = "EXIT"
        if order == "BUY":
            shares = cash / (opens[i] * (1 + FEE))
            cash = 0.0
            buys += 1
        elif order == "EXIT":
            cash += shares * opens[i] * (1 - FEE)
            shares = 0.0
            sells += 1
        if not regime[i - 1] if i > 0 else False:
            if regime_off_since < 0:
                regime_off_since = i - 1
        else:
            regime_off_since = -10
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, buys, sells, in_days


def main() -> int:
    frames = json.loads(open("data/stock_midcap_hfq.json").read())["data"]
    cyb = fetch_index()
    common = None
    for nm in ALL10:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in ALL10}

    cybc = [cyb.get(d) for d in common]
    regime = [False] * len(common)
    for i in range(120, len(common)):
        if cybc[i] is None or any(v is None for v in cybc[i - 119 : i + 1]):
            continue
        regime[i] = cybc[i] > sum(cybc[i - 119 : i + 1]) / 120

    for subset_name, names in [("科技4只", TECH), ("全部10只", ALL10)]:
        hold_nv = [
            sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names) / len(names)
            for d in common
        ]
        v2n, v6n, v6cn = [], [], []
        for nm in names:
            rows = [by_day[nm][d] for d in common]
            v2, *_ = run_strategy(rows, "graduated", True)
            v6, *_ = run_v6(rows, regime)
            v6c, *_ = run_v6(rows, regime, exit_confirm=True)
            v2n.append(v2)
            v6n.append(v6)
            v6cn.append(v6c)
        v2_nv = [sum(n[i] for n in v2n) / len(names) for i in range(len(common))]
        v6_nv = [sum(n[i] for n in v6n) / len(names) for i in range(len(common))]
        v6c_nv = [sum(n[i] for n in v6cn) / len(names) for i in range(len(common))]
        m_h = metrics_from_prices(common, hold_nv)
        m2 = metrics_from_prices(common, v2_nv)
        m6 = metrics_from_prices(common, v6_nv)
        m6c = metrics_from_prices(common, v6c_nv)
        print(f"\n=== {subset_name} ===")
        print(f"持有: {fmt_pct(m_h['total_return'])} 回撤 {fmt_pct(m_h['max_drawdown'])} 夏普 {m_h['sharpe']:.2f}")
        print(f"V2:   {fmt_pct(m2['total_return'])} 回撤 {fmt_pct(m2['max_drawdown'])} 夏普 {m2['sharpe']:.2f}")
        print(f"V6(双金叉进+创指出): {fmt_pct(m6['total_return'])} 回撤 {fmt_pct(m6['max_drawdown'])} 夏普 {m6['sharpe']:.2f}")
        print(f"V6c(出场确认1天): {fmt_pct(m6c['total_return'])} 回撤 {fmt_pct(m6c['max_drawdown'])} 夏普 {m6c['sharpe']:.2f}")
        for h, label in [(252, "1年"), (504, "2年")]:
            rh, r2, r6 = [], [], []
            for e in range(0, len(common) - h, 5):
                rh.append(hold_nv[e + h] / hold_nv[e] - 1)
                r2.append(v2_nv[e + h] / v2_nv[e] - 1)
                r6.append(v6_nv[e + h] / v6_nv[e] - 1)
            print(
                f"  滚动{label}: 持有中位 {statistics.median(rh):+.1%}  "
                f"V2 {statistics.median(r2):+.1%}({sum(a<b for a,b in zip(rh,r2))/len(rh)*100:.0f}%)  "
                f"V6 {statistics.median(r6):+.1%}({sum(a<b for a,b in zip(rh,r6))/len(rh)*100:.0f}%)"
            )

    # 图：科技4只
    names = TECH
    hold_nv = [
        sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names) / len(names)
        for d in common
    ]
    v6n = []
    for nm in names:
        rows = [by_day[nm][d] for d in common]
        v6, *_ = run_v6(rows, regime)
        v6n.append(v6)
    v6_nv = [sum(n[i] for n in v6n) / len(names) for i in range(len(common))]
    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(common, hold_nv, label="持有", color="black", lw=1.5)
    ax.plot(common, v6_nv, label="V6 双金叉进+创指MA120出", color="#d62728", lw=2)
    ax.set_title("科技4只：V6 混合版 vs 满仓持有")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"v6_hybrid_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
