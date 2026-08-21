#!/usr/bin/env python3
"""V4：双金叉 + 确认制（买入看量价、卖出看多指标+1~2天观察确认）。

买入：2日内双金叉 + 量价确认（近5日有缩量回调 或 金叉日放量），不追无配合的金叉。
卖出：MACD/KDJ 死叉触发「观察」，观察 1~2 天，需同时满足：
  a) 价格延续下跌（收盘 < 死叉日收盘）
  b) DMI 朝下（+DI<-DI 或 ADX 拐头）
  c) ASI 朝下（比 2 天前低）
  d) 量价确认（放量阴线 或 缩量阴跌）
  b/c/d 中至少 2 项成立 → 次日开盘清仓；观察期价格收复则取消，继续持有。
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
from etf_strategy_v2 import dmi, kdj, macd, rsi, run_strategy

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003


def asi_series(rows, limit=0.10):
    n = len(rows)
    si = [0.0] * n
    for i in range(1, n):
        o, h, l, c = rows[i]["open"], rows[i]["high"], rows[i]["low"], rows[i]["close"]
        po, pc, ph, pl = rows[i - 1]["open"], rows[i - 1]["close"], rows[i - 1]["high"], rows[i - 1]["low"]
        a, b, cc = abs(h - pc), abs(l - pc), abs(h - pl)
        d, e = abs(pc - po), pc - po
        f, g = c - pc, c - o
        hh, ii, jj = c - po, c - ph, c - pl
        k = max(a, b)
        if a > b and a > cc:
            m = a + b / 2 + d / 4
        elif b > a and b > cc:
            m = b + a / 2 + d / 4
        else:
            m = cc + d / 4
        if m == 0:
            continue
        nn = f if (f > 0 and f > e) else (f + e if (f < e and f < 0) else 0.0)
        si[i] = 50 * ((g + 0.5 * nn + 0.25 * hh) / m) * (k / limit)
    asi, acc = [0.0] * n, 0.0
    for i in range(n):
        acc += si[i]
        asi[i] = acc
    return asi


def ma5vol(vols, i):
    if i < 4:
        return sum(vols[: i + 1]) / (i + 1)
    return sum(vols[i - 4 : i + 1]) / 5


def run_v4(rows):
    n = len(rows)
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    vols = [r["vol"] for r in rows]
    asi = asi_series(rows)
    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)
    pdi, mdi, adx = dmi(highs, lows, closes)

    macd_gold = [False] * n
    macd_dead = [False] * n
    kdj_gold = [False] * n
    kdj_dead = [False] * n
    for i in range(1, n):
        macd_gold[i] = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        macd_dead[i] = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        kdj_gold[i] = ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]
        kdj_dead[i] = ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]

    def win(arr, i, w):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    def vol_confirm_buy(i):
        """近5日有缩量回调 或 金叉日放量。"""
        shrink = any(
            j >= 1 and vols[j] <= vols[j - 1] * 0.7 for j in range(max(1, i - 4), i + 1)
        )
        surge = vols[i] > 1.2 * ma5vol(vols, i)
        return shrink or surge

    def sell_confirm(dead_idx, obs_idx):
        """观察日 obs_idx 是否确认卖出（obs_idx 在死叉后 1~2 天）。"""
        price_down = closes[obs_idx] < closes[dead_idx]
        dmi_down = mdi[obs_idx] > pdi[obs_idx] or adx[obs_idx] < adx[max(0, obs_idx - 3)]
        asi_down = asi[obs_idx] < asi[max(0, obs_idx - 2)]
        vol_ok = (
            closes[obs_idx] < opens[obs_idx] and vols[obs_idx] > 1.2 * ma5vol(vols, obs_idx)
        ) or (closes[obs_idx] < closes[obs_idx - 1] and vols[obs_idx] < 0.8 * vols[obs_idx - 1])
        hits = sum([dmi_down, asi_down, vol_ok])
        return price_down and hits >= 2

    cash, shares = 1.0, 0.0
    nav = []
    buys = sells = 0
    in_days = 0
    watching = None  # {"dead": 死叉日index, "obs": 已观察天数}
    for i in range(n):
        order = None
        if i > 0:
            sig_buy = win(macd_gold, i - 1, 2) and win(kdj_gold, i - 1, 2)
            if shares == 0:
                if sig_buy and vol_confirm_buy(i - 1):
                    order = "BUY"
                    watching = None
            else:
                # 新死叉触发观察
                if watching is None and (
                    win(macd_dead, i - 1, 2) or win(kdj_dead, i - 1, 2)
                ):
                    watching = {"dead": i - 1, "obs": 0}
                if watching is not None:
                    dead_idx = watching["dead"]
                    obs = i - 1
                    if obs <= dead_idx:
                        pass  # 信号当天不观察，从次日开始
                    elif closes[obs] >= closes[dead_idx]:  # 收复 = 假死叉，取消
                        watching = None
                    elif sell_confirm(dead_idx, obs):
                        order = "EXIT"
                        watching = None
                    else:
                        watching["obs"] += 1
                        if watching["obs"] >= 4:  # 观察上限 4 天
                            watching = None
        if order == "BUY":
            shares = cash / (opens[i] * (1 + FEE))
            cash = 0.0
            buys += 1
        elif order == "EXIT":
            cash += shares * opens[i] * (1 - FEE)
            shares = 0.0
            sells += 1
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, buys, sells, in_days


def main() -> int:
    frames = json.loads(open("data/stock_midcap_hfq.json").read())["data"]
    names = list(frames.keys())
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in names}

    hold_nv = [
        sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names) / len(names)
        for d in common
    ]
    v2_navs, v4_navs = [], []
    per = []
    for nm in names:
        rows = [by_day[nm][d] for d in common]
        v2, *_ = run_strategy(rows, "graduated", True)
        v4, b4, s4, d4 = run_v4(rows)
        v2_navs.append(v2)
        v4_navs.append(v4)
        hr = rows[-1]["close"] / rows[0]["close"] - 1
        per.append((nm, hr, v2[-1] - 1, v4[-1] - 1, b4, s4, d4))
    v2_nv = [sum(n[i] for n in v2_navs) / len(v2_navs) for i in range(len(common))]
    v4_nv = [sum(n[i] for n in v4_navs) / len(v4_navs) for i in range(len(common))]

    m_hold = metrics_from_prices(common, hold_nv)
    m_v2 = metrics_from_prices(common, v2_nv)
    m_v4 = metrics_from_prices(common, v4_nv)

    print("\n=== 全窗口（10只中小盘等权）===")
    print(f"持有: {fmt_pct(m_hold['total_return'])} 回撤 {fmt_pct(m_hold['max_drawdown'])} 夏普 {m_hold['sharpe']:.2f}")
    print(f"V2:   {fmt_pct(m_v2['total_return'])} 回撤 {fmt_pct(m_v2['max_drawdown'])} 夏普 {m_v2['sharpe']:.2f}")
    print(f"V4:   {fmt_pct(m_v4['total_return'])} 回撤 {fmt_pct(m_v4['max_drawdown'])} 夏普 {m_v4['sharpe']:.2f}")

    for h, label in [(252, "1年"), (504, "2年")]:
        rh, r2, r4 = [], [], []
        for e in range(0, len(common) - h, 5):
            rh.append(hold_nv[e + h] / hold_nv[e] - 1)
            r2.append(v2_nv[e + h] / v2_nv[e] - 1)
            r4.append(v4_nv[e + h] / v4_nv[e] - 1)
        print(
            f"\n入场后{label}: 持有中位 {statistics.median(rh):+.1%}  "
            f"V2 {statistics.median(r2):+.1%}(胜率{sum(a<b for a,b in zip(rh,r2))/len(rh)*100:.0f}%)  "
            f"V4 {statistics.median(r4):+.1%}(胜率{sum(a<b for a,b in zip(rh,r4))/len(rh)*100:.0f}%)"
        )

    print("\n单只: 持有 vs V2 vs V4 (买/卖/在场)")
    for nm, hr, v2r, v4r, b4, s4, d4 in per:
        print(f"  {nm:<6} 持有 {fmt_pct(hr):>8}  V2 {fmt_pct(v2r):>8}  V4 {fmt_pct(v4r):>8}  ({b4}/{s4}/{d4*100//len(common)}%)")

    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(common, hold_nv, label="持有", color="black", lw=1.5)
    ax.plot(common, v2_nv, label="V2 双金叉", color="#2ca02c", lw=1.8)
    ax.plot(common, v4_nv, label="V4 确认制", color="#1f77b4", lw=2)
    ax.legend()
    ax.set_title("V4 确认制 vs V2 vs 持有")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"v4_confirm_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
