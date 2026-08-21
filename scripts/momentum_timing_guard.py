#!/usr/bin/env python3
"""动量轮动 + 双金叉 + 熊市守卫规则（规避"吃到完整熊市"问题）。

守卫选项：
  abs     : 绝对动量过滤，12个月涨幅必须 > 0，否则踢出候选
  ma200   : 候选必须收盘 > MA200（自身趋势未破坏）
  mom3    : 近3个月动量必须 > 0（转熊板块早转负）
  trail   : 持仓从高点回落 15% 即卖（不等排名）
  regime  : 创业板指 < MA120 时全部空仓（大盘总开关）
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
from momentum_timing import signals

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003
LOOKBACK = 252
K = 3
TRAIL = 0.15


def build_detail(frames, names, all_dates):
    """月末返回 {月: [ (名称, mom12, mom3, above_ma200) ] top6 }。"""
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    month_ends = []
    cur = None
    for d in all_dates:
        ym = d[:7]
        if ym != cur:
            cur = ym
            month_ends.append(d)
        else:
            month_ends[-1] = d
    out = {}
    for me in month_ends:
        hist = [d for d in all_dates if d <= me]
        idx = len(hist) - 1
        if idx < LOOKBACK:
            continue
        p12 = hist[idx - LOOKBACK]
        p3 = hist[max(0, idx - 63)]
        rows = []
        for nm in names:
            c = by_day[nm]
            if me not in c or p12 not in c or c[p12] <= 0:
                continue
            mom12 = c[me] / c[p12] - 1
            mom3 = c[me] / c[p3] - 1 if p3 in c and c[p3] > 0 else -1
            closes = [c[d] for d in hist if d in c]
            ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else 0
            rows.append((nm, mom12, mom3, c[me] > ma200 if ma200 else True))
        rows.sort(key=lambda x: x[1], reverse=True)
        out[me[:7]] = rows[:6]
    return out


def run_guarded(frames, names, all_dates, guards, regime=None):
    """guards: {'abs','ma200','mom3','trail','regime'} 子集。"""
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    by_open = {nm: {r["day"]: r["open"] for r in frames[nm]} for nm in names}
    sig = {}
    for nm in names:
        gold, dead = signals(frames[nm])
        ds = [r["day"] for r in frames[nm]]
        sig[nm] = {"dates": ds, "gold": dict(zip(ds, gold)), "dead": dict(zip(ds, dead))}
    detail = build_detail(frames, names, all_dates)

    def next_month(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        m += 1
        if m > 12:
            y, m = y + 1, 1
        return f"{y:04d}-{m:02d}"

    detail = {next_month(k): v for k, v in detail.items()}

    def eligible(cand):
        nm, mom12, mom3, above = cand
        if "abs" in guards and mom12 <= 0:
            return False
        if "ma200" in guards and not above:
            return False
        if "mom3" in guards and mom3 <= 0:
            return False
        return True

    def recent_signal(sig_ent, key, d):
        ds = sig_ent["dates"]
        i = bisect_left(ds, d)
        if i >= len(ds) or ds[i] != d:
            i -= 1
        return any(sig_ent[key].get(ds[j], False) for j in range(max(0, i - 2), i))

    slots = [{"cash": 1.0 / K, "shares": 0.0, "etf": None, "target": None, "peak": 0.0, "dropped": False} for _ in range(K)]
    last_px = {}
    nav, dates_out = [], []
    last_month = None
    exposure_days = 0
    for d in all_dates:
        if d < "2021-01-01":
            continue
        ym = d[:7]
        reg_on = True if regime is None else regime.get(d, True)
        cands = detail.get(ym)
        month_start = last_month != ym
        if cands and month_start:
            last_month = ym
            top3 = [c[0] for c in cands[:3]]
            held = {s["etf"] for s in slots if s["etf"]}
            if guards:
                eligible_rank = [c for c in cands[:3] if c[0] not in held and eligible(c)]
                if len(eligible_rank) + len(held) < K:
                    for c in cands[3:]:
                        if c[0] not in held and eligible(c):
                            eligible_rank.append(c)
            else:
                eligible_rank = [c for c in cands[:3] if c[0] not in held]
            for s in slots:
                if s["etf"]:
                    cand = next((c for c in cands if c[0] == s["etf"]), None)
                    if not eligible(cand) if cand else False:
                        # 守卫不通过 → 立即卖
                        px = by_open[s["etf"]].get(d, last_px.get(s["etf"], 0))
                        s["cash"] += s["shares"] * px * (1 - FEE)
                        s["shares"] = 0.0
                        s["etf"] = None
                        s["target"] = None
                        s["peak"] = 0.0
                        s["dropped"] = False
                    elif s["etf"] in top3:
                        s["dropped"] = False  # 仍在 top3，正常持有
                    else:
                        s["dropped"] = True  # 掉出 top3（含掉出候选池），等双死叉
                if s["etf"] is None and s["target"] is None and eligible_rank:
                    s["target"] = eligible_rank.pop(0)[0]
        # regime 开关：关闭时全空仓
        if "regime" in guards and not reg_on:
            for s in slots:
                if s["etf"]:
                    px = by_open[s["etf"]].get(d, last_px.get(s["etf"], 0))
                    s["cash"] += s["shares"] * px * (1 - FEE)
                    s["shares"] = 0.0
                    s["etf"] = None
                    s["target"] = None
                    s["peak"] = 0.0
        # 日线：进场 / 双死叉 / 移动止损
        for s in slots:
            if s["etf"] is None and s["target"] and d in by_day[s["target"]]:
                if recent_signal(sig[s["target"]], "gold", d):
                    px = by_open[s["target"]].get(d, by_day[s["target"]][d])
                    s["shares"] = s["cash"] / (px * (1 + FEE))
                    s["cash"] = 0.0
                    s["etf"] = s["target"]
                    s["peak"] = by_day[s["etf"]][d]
                    last_px[s["etf"]] = px
                    s["target"] = None
            elif s["etf"] and d in by_day[s["etf"]]:
                px_c = by_day[s["etf"]][d]
                s["peak"] = max(s["peak"], px_c)
                exit_sig = s["dropped"] and recent_signal(sig[s["etf"]], "dead", d)
                if "trail" in guards and px_c < s["peak"] * (1 - TRAIL):
                    exit_sig = True
                if exit_sig:
                    px = by_open[s["etf"]].get(d, px_c)
                    s["cash"] += s["shares"] * px * (1 - FEE)
                    s["shares"] = 0.0
                    s["etf"] = None
                    s["peak"] = 0.0
                    s["dropped"] = False
        # 估值
        for s in slots:
            if s["etf"] and d in by_day[s["etf"]]:
                last_px[s["etf"]] = by_day[s["etf"]][d]
        val = sum(s["cash"] for s in slots)
        for s in slots:
            if s["etf"]:
                val += s["shares"] * last_px.get(s["etf"], 0)
        if val > 0:
            invested = sum(1 for s in slots if s["etf"])
            if invested > 0:
                exposure_days += 1
        nav.append(val)
        dates_out.append(d)
    return nav, dates_out, exposure_days, len(dates_out)


def main() -> int:
    frames = json.loads(open("data/momentum_etf_hfq.json").read())["data"]
    names = list(frames.keys())
    all_dates = sorted({d for nm in names for d in (r["day"] for r in frames[nm])})

    cyb = json.loads(open("data/cyb_index.json").read())["data"]
    reg = {}
    for d in all_dates:
        ic = cyb.get(d)
        if ic is None:
            continue
        hist = [cyb[x] for x in all_dates if x <= d and x in cyb]
        if len(hist) >= 120:
            reg[d] = ic > sum(hist[-120:]) / 120
        else:
            reg[d] = True

    configs = [
        ("G0 M2基线", set()),
        ("G1 +绝对动量(12m>0)", {"abs"}),
        ("G2 +站上MA200", {"abs", "ma200"}),
        ("G3 +近3月动量>0", {"abs", "mom3"}),
        ("G4 +移动止损15%", {"abs", "trail"}),
        ("G5 +创指MA120空仓", {"abs", "regime"}),
    ]
    load_font()
    fig, ax = plt.subplots(figsize=(12, 7))
    results = []
    for label, guards in configs:
        nav, dates, exp_days, tot = run_guarded(frames, names, all_dates, guards, reg)
        m = metrics_from_prices(dates, nav)
        mn = {}
        for d, v in zip(dates, nav):
            mn[d[:7]] = v
        mk = sorted(mn)
        mr = [mn[mk[i]] / mn[mk[i - 1]] - 1 for i in range(1, len(mk)) if mn[mk[i - 1]] > 0]
        wins = sum(1 for r in mr if r > 0)
        results.append((label, nav, dates, m))
        print(
            f"{label:<24} {fmt_pct(m['total_return']):>8} 年化 {fmt_pct(m['annualized_return']):>7} "
            f"回撤 {fmt_pct(m['max_drawdown']):>8} 夏普 {m['sharpe']:.2f} 月度胜率 {wins/len(mr)*100:.0f}% "
            f"在场天数 {exp_days/tot*100:.0f}%"
        )
        ax.plot(dates, nav, label=label, lw=1.6)
    ax.legend(fontsize=8)
    ax.set_title("动量轮动 + 熊市守卫规则对比（32只ETF池）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"momentum_guard_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
