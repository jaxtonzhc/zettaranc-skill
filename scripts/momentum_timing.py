#!/usr/bin/env python3
"""动量轮动 + 双金叉进场择时 + 掉出排名换仓（吃完一波）。

M0：原始动量（月初无脑买 top3）
M1：动量选 top3 + 双金叉才进场；掉出 top3 即卖（月末换仓）
M2：M1 进场 + 掉出 top3 后等双死叉才卖（吃完一整波再换）
"""

import json
import statistics
import sys
from bisect import bisect_left
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, "scripts")
from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_v2 import kdj, macd

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003
LOOKBACK = 252
K = 3


def signals(rows):
    """返回 (gold_2d, dead_2d)：双金叉/双死叉（2日窗口内 MACD+KDJ 同向）。"""
    n = len(rows)
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)
    mg = [False] * n
    md = [False] * n
    kg = [False] * n
    kd = [False] * n
    for i in range(1, n):
        mg[i] = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        md[i] = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        kg[i] = ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]
        kd[i] = ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]

    def win(arr, i, w=2):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    gold = [win(mg, i) and win(kg, i) for i in range(n)]
    dead = [win(md, i) and win(kd, i) for i in range(n)]
    return gold, dead


def build_rankings(frames, names, all_dates):
    """月末排名：返回 {月key: [top3 名称]}。"""
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    by_open = {nm: {r["day"]: r["open"] for r in frames[nm]} for nm in names}
    month_ends = []
    cur = None
    for d in all_dates:
        ym = d[:7]
        if ym != cur:
            cur = ym
            month_ends.append(d)
        else:
            month_ends[-1] = d
    holdings = {}
    for me in month_ends:
        mom = {}
        hist = [d for d in all_dates if d <= me]
        idx = len(hist) - 1
        if idx < LOOKBACK:
            continue
        past_day = hist[idx - LOOKBACK]
        for nm in names:
            closes = by_day[nm]
            if me not in closes or past_day not in closes or closes[past_day] <= 0:
                continue
            mom[nm] = closes[me] / closes[past_day] - 1
        if len(mom) < K:
            continue
        holdings[me[:7]] = sorted(mom, key=mom.get, reverse=True)[:K]
    return holdings


def run_momentum_timing(frames, names, all_dates, entry_filter=True, exit_mode="rank"):
    """slot 式模拟。entry_filter: 是否等双金叉；exit_mode: rank 掉出即卖 / dead 等双死叉。"""
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    by_open = {nm: {r["day"]: r["open"] for r in frames[nm]} for nm in names}
    sig = {}
    for nm in names:
        gold, dead = signals(frames[nm])
        ds = [r["day"] for r in frames[nm]]
        sig[nm] = {"dates": ds, "gold": dict(zip(ds, gold)), "dead": dict(zip(ds, dead))}
    holdings = build_rankings(frames, names, all_dates)

    def next_month(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        m += 1
        if m > 12:
            y, m = y + 1, 1
        return f"{y:04d}-{m:02d}"

    applied = {next_month(k): v for k, v in holdings.items()}
    trade_dates = [d for d in all_dates if d >= "2021-01-01"]
    idx_map = {d: i for i, d in enumerate(all_dates)}

    slots = [{"cash": 1.0 / K, "shares": 0.0, "etf": None, "target": None, "dropped": False} for _ in range(K)]
    last_px = {}
    nav = []
    last_month = None
    for d in trade_dates:
        ym = d[:7]
        targets = applied.get(ym)
        if targets is not None and last_month != ym:
            last_month = ym
            held = {s["etf"] for s in slots if s["etf"]}
            # 掉出排名：rank 模式即卖；dead 模式标记等待双死叉
            for s in slots:
                if s["etf"] and s["etf"] not in targets:
                    if exit_mode == "rank":
                        px = by_open[s["etf"]].get(d, last_px.get(s["etf"], 0))
                        s["cash"] += s["shares"] * px * (1 - FEE)
                        s["shares"] = 0.0
                        s["etf"] = None
                        s["target"] = None
                        s["dropped"] = False
                    else:
                        s["target"] = None  # 掉出，等双死叉
                        s["dropped"] = True
                elif s["etf"]:
                    s["dropped"] = False  # 仍在排名内，正常持有
            # 新目标分配到空 slot
            new_ones = [t for t in targets if t not in held]
            for s in slots:
                if s["etf"] is None and s["target"] is None and new_ones:
                    s["target"] = new_ones.pop(0)
        # 逐日：进场（双金叉）与出场（双死叉）
        for s in slots:
            if s["etf"] is None and s["target"] and d in by_day[s["target"]]:
                g = recent_signal(sig[s["target"]], "gold", d, by_day[s["target"]])
                if (not entry_filter) or g:
                    px = by_open[s["target"]].get(d, by_day[s["target"]][d])
                    s["shares"] = s["cash"] / (px * (1 + FEE))
                    s["cash"] = 0.0
                    s["etf"] = s["target"]
                    last_px[s["etf"]] = px
                    s["target"] = None
            elif s["etf"] and exit_mode == "dead" and s["dropped"] and d in by_day[s["etf"]]:
                if recent_signal(sig[s["etf"]], "dead", d, by_day[s["etf"]]):
                    px = by_open[s["etf"]].get(d, by_day[s["etf"]][d])
                    s["cash"] += s["shares"] * px * (1 - FEE)
                    s["shares"] = 0.0
                    s["etf"] = None
                    s["dropped"] = False
        # 估值
        val = sum(s["cash"] for s in slots)
        for s in slots:
            if s["etf"] is not None and d in by_day[s["etf"]]:
                last_px[s["etf"]] = by_day[s["etf"]][d]
        for s in slots:
            if s["etf"] is not None:
                val += s["shares"] * last_px.get(s["etf"], 0)
        nav.append(val)
    return nav, trade_dates


def recent_signal(sig_ent, key, d, by_day_nm):
    """该 ETF 最近 2 个交易日（含 d 之前）内是否出现 key 信号。"""
    ds = sig_ent["dates"]
    i = bisect_left(ds, d)
    if i >= len(ds) or ds[i] != d:
        i -= 1
    return any(sig_ent[key].get(ds[j], False) for j in range(max(0, i - 2), i))


def main() -> int:
    frames = json.loads(open("data/momentum_etf_hfq.json").read())["data"]
    names = list(frames.keys())
    all_dates = sorted({d for nm in names for d in (r["day"] for r in frames[nm])})

    # 基准：等权持有全部池
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    hold_nv, hold_dates = [], []
    for d in all_dates:
        if d < "2021-01-01":
            continue
        vals = [by_day[nm][d] for nm in names if d in by_day[nm]]
        if vals:
            hold_nv.append(sum(vals) / len(vals))
            hold_dates.append(d)
    hold_nv = [v / hold_nv[0] for v in hold_nv]
    m_h = metrics_from_prices(hold_dates, hold_nv)

    configs = [
        ("M0 原始动量", False, "rank"),
        ("M1 动量+双金叉进/掉出即卖", True, "rank"),
        ("M2 动量+双金叉进/双死叉出", True, "dead"),
    ]
    results = []
    for label, ef, em in configs:
        nav, dates = run_momentum_timing(frames, names, all_dates, ef, em)
        m = metrics_from_prices(dates, nav)
        # 月度胜率
        mn = {}
        for d, v in zip(dates, nav):
            mn[d[:7]] = v
        mkeys = sorted(mn)
        mret = [mn[mkeys[i]] / mn[mkeys[i - 1]] - 1 for i in range(1, len(mkeys)) if mn[mkeys[i - 1]] > 0]
        wins = sum(1 for r in mret if r > 0)
        results.append((label, nav, dates, m))
        print(
            f"\n{label}: {fmt_pct(m['total_return'])} 年化 {fmt_pct(m['annualized_return'])} "
            f"回撤 {fmt_pct(m['max_drawdown'])} 夏普 {m['sharpe']:.2f} 月度胜率 {wins/len(mret)*100:.0f}%"
        )

    print(f"\n基准 等权持有全池: {fmt_pct(m_h['total_return'])} 回撤 {fmt_pct(m_h['max_drawdown'])} 夏普 {m_h['sharpe']:.2f}")

    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(hold_dates, hold_nv, label="等权持有全池", color="black", lw=1.2, alpha=0.8)
    for label, nav, dates, m in results:
        ax.plot(dates, nav, label=label, lw=1.8)
    ax.legend(fontsize=8)
    ax.set_title("动量轮动 + 双金叉择时（32只ETF池）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"momentum_timing_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
