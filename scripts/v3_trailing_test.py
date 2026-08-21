#!/usr/bin/env python3
"""V3：V2 双金叉 + ATR 吊灯止损（替代双死叉清仓），让利润奔跑。"""

import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "scripts")
from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_v2 import dmi, kdj, macd, rsi, run_strategy

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003


def atr_series(rows, n=14):
    trs = []
    for i in range(len(rows)):
        if i == 0:
            trs.append(rows[i]["high"] - rows[i]["low"])
            continue
        tr = max(
            rows[i]["high"] - rows[i]["low"],
            abs(rows[i]["high"] - rows[i - 1]["close"]),
            abs(rows[i]["low"] - rows[i - 1]["close"]),
        )
        trs.append(tr)
    atr = [0.0] * len(rows)
    if len(rows) > n:
        atr[n] = sum(trs[1 : n + 1]) / n
        for i in range(n + 1, len(rows)):
            atr[i] = (atr[i - 1] * (n - 1) + trs[i]) / n
    return atr


def run_v3(rows, sell_mode="chandelier", atr_mult=2.5):
    """V2 买入（2天双金叉）+ DMI/RSI 过滤；出场：
    弱趋势单死叉减半（同 V2）；清仓用 ATR 吊灯止损（从持仓最高收盘回落 atr_mult*ATR）。
    sell_mode='chandelier' 或 'both'（双死叉+吊灯谁先到）。"""
    n = len(rows)
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    atr = atr_series(rows)
    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)
    pdi, mdi, adx = dmi(highs, lows, closes)
    rsi_v = rsi(closes)

    macd_gold = [False] * n
    macd_dead = [False] * n
    kdj_gold = [False] * n
    kdj_dead = [False] * n
    trend_ok = [False] * n
    for i in range(1, n):
        macd_gold[i] = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        macd_dead[i] = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        kdj_gold[i] = ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]
        kdj_dead[i] = ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]
        trend_ok[i] = pdi[i] >= mdi[i] and adx[i] >= 25 and rsi_v[i] >= 50
    trend_ok[0] = trend_ok[1] if n > 1 else False

    def win(arr, i, w):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    cash, shares = 1.0, 0.0
    highest = 0.0
    nav = []
    buys = reduces = exits = 0
    in_days = 0
    for i in range(n):
        order = None
        if i > 0:
            sig_buy = win(macd_gold, i - 1, 2) and win(kdj_gold, i - 1, 2)
            if shares == 0:
                if sig_buy:
                    order = "BUY"
            else:
                dbl = win(macd_dead, i - 1, 2) and win(kdj_dead, i - 1, 2)
                sgl = (macd_dead[i - 1] and not kdj_dead[i - 1]) or (
                    kdj_dead[i - 1] and not macd_dead[i - 1]
                )
                weak = not trend_ok[i - 1]
                if weak and sgl:
                    order = "REDUCE"
                if weak and dbl and sell_mode == "both":
                    order = "EXIT"
                # ATR 吊灯止损
                if shares > 0 and atr[i - 1] > 0:
                    if closes[i - 1] < highest - atr_mult * atr[i - 1]:
                        order = "EXIT"
        if order == "BUY":
            shares = cash / (opens[i] * (1 + FEE))
            cash = 0.0
            buys += 1
            highest = closes[i]
        elif order == "REDUCE":
            sell_sh = shares * 0.5
            cash += sell_sh * opens[i] * (1 - FEE)
            shares -= sell_sh
            reduces += 1
        elif order == "EXIT":
            cash += shares * opens[i] * (1 - FEE)
            shares = 0.0
            exits += 1
        if shares > 0:
            highest = max(highest, closes[i])
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, buys, reduces, exits, in_days


def main() -> int:
    frames = json.loads(open("data/stock_midcap_hfq.json").read())["data"]
    names = list(frames.keys())
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)

    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in names}
    hold_nv = []
    for d in common:
        hold_nv.append(
            sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names)
            / len(names)
        )
    v2_navs = []
    v3_navs = []
    per_stock = []
    for nm in names:
        rows = [by_day[nm][d] for d in common]
        v2, *_ = run_strategy(rows, "graduated", True)
        v3, b3, r3, e3, d3 = run_v3(rows)
        v2_navs.append(v2)
        v3_navs.append(v3)
        hr = rows[-1]["close"] / rows[0]["close"] - 1
        per_stock.append((nm, hr, v2[-1] - 1, v3[-1] - 1, b3, r3, e3))
    v2_nv = [sum(n[i] for n in v2_navs) / len(v2_navs) for i in range(len(common))]
    v3_nv = [sum(n[i] for n in v3_navs) / len(v3_navs) for i in range(len(common))]

    m_hold = metrics_from_prices(common, hold_nv)
    m_v2 = metrics_from_prices(common, v2_nv)
    m_v3 = metrics_from_prices(common, v3_nv)

    # 滚动 1Y/2Y
    for h, label in [(252, "1年"), (504, "2年")]:
        rh, r2, r3 = [], [], []
        for e in range(0, len(common) - h, 5):
            rh.append(hold_nv[e + h] / hold_nv[e] - 1)
            r2.append(v2_nv[e + h] / v2_nv[e] - 1)
            r3.append(v3_nv[e + h] / v3_nv[e] - 1)
        print(
            f"\n入场后{label}（{len(rh)} 点）: 持有中位 {statistics.median(rh):+.1%}  "
            f"V2 {statistics.median(r2):+.1%}(胜率{sum(a<b for a,b in zip(rh,r2))/len(rh)*100:.0f}%)  "
            f"V3吊灯 {statistics.median(r3):+.1%}(胜率{sum(a<b for a,b in zip(rh,r3))/len(rh)*100:.0f}%)"
        )

    print(f"\n=== 全窗口（10只等权）===")
    print(f"持有: {fmt_pct(m_hold['total_return'])} 回撤 {fmt_pct(m_hold['max_drawdown'])} 夏普 {m_hold['sharpe']:.2f}")
    print(f"V2:   {fmt_pct(m_v2['total_return'])} 回撤 {fmt_pct(m_v2['max_drawdown'])} 夏普 {m_v2['sharpe']:.2f}")
    print(f"V3:   {fmt_pct(m_v3['total_return'])} 回撤 {fmt_pct(m_v3['max_drawdown'])} 夏普 {m_v3['sharpe']:.2f}")
    print(f"\n单只: 持有 vs V2 vs V3 (买/减/清)")
    for nm, hr, v2r, v3r, b3, r3, e3 in per_stock:
        print(f"  {nm:<6} 持有 {fmt_pct(hr):>8}  V2 {fmt_pct(v2r):>8}  V3 {fmt_pct(v3r):>8}  ({b3}/{r3}/{e3})")

    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(common, hold_nv, label="持有", color="black", lw=1.5)
    ax.plot(common, v2_nv, label="V2 双金叉+死叉", color="#2ca02c", lw=1.8)
    ax.plot(common, v3_nv, label="V3 双金叉+ATR吊灯", color="#1f77b4", lw=2)
    ax.legend()
    ax.set_title("V3 ATR吊灯止损 vs V2 vs 持有（10只中小盘等权）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"v3_trailing_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
