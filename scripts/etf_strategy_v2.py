#!/usr/bin/env python3
"""ETF 双金叉策略 v2：2天窗口双金叉买入 + 分级卖出（单死叉减半仓/双死叉清仓）+ DMI/RSI 趋势过滤。

规则（用户口径，可配置）：
  买入：2 个交易日内 MACD 金叉与 KDJ 金叉都出现 → 满仓；半仓时出现则加回满仓
  减仓：当日出现单个死叉（MACD 或 KDJ 任一）且趋势转弱 → 减半仓
  清仓：2 个交易日内 MACD 死叉与 KDJ 死叉都出现（双死叉）且趋势转弱 → 清仓
  趋势过滤（防短期波动）：+DI>=−DI 且 ADX>=25 且 RSI(12)>=50 → 视为强趋势，死叉不执行
  MACD 6,13,5；KDJ 9,3,3；DMI 14,6；RSI 12；信号 T 日收盘产生、T+1 开盘成交；佣金 0.03%/边
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_early import PORTFOLIO_C, load_klines as load_hfq
from etf_strategy_backtest import ETF_POOL, PORTFOLIO_A, load_klines as load_qfq

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003

MACD_P = (6, 13, 5)
KDJ_P = (9, 3, 3)
DMI_P = (14, 6)
RSI_P = 12
ADX_MIN, RSI_MIN = 25.0, 50.0
BUY_WINDOW = 2
DEAD_WINDOW = 2
REDUCE_RATIO = 0.5


def ema(values, n):
    k = 2 / (n + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes):
    f, s, m = MACD_P
    dif = [a - b for a, b in zip(ema(closes, f), ema(closes, s))]
    return dif, ema(dif, m)


def kdj(highs, lows, closes):
    n, kp, dp = KDJ_P
    k, d = 50.0, 50.0
    ks, ds = [], []
    for i in range(len(closes)):
        lo = min(lows[max(0, i - n + 1) : i + 1])
        hi = max(highs[max(0, i - n + 1) : i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        ks.append(k)
        ds.append(d)
    return ks, ds


def rsi(closes, period=RSI_P):
    n = len(closes)
    out = [50.0] * n
    if n <= period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, n)]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out[period] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def dmi(highs, lows, closes):
    """DMI(14,6)，返回 +DI, -DI, ADX 序列。"""
    di_p, adx_p = DMI_P
    n = len(closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    def wilder(vals, period, start=1):
        out = [0.0] * n
        s = sum(vals[start : start + period])
        if start + period < n:
            out[start + period] = s
        for i in range(start + period + 1, n):
            s = s - s / period + vals[i]
            out[i] = s
        return out

    tr_s = wilder(tr, di_p)
    pdm_s = wilder(plus_dm, di_p)
    mdm_s = wilder(minus_dm, di_p)
    pdi = [100 * pdm_s[i] / tr_s[i] if tr_s[i] > 0 else 50.0 for i in range(n)]
    mdi = [100 * mdm_s[i] / tr_s[i] if tr_s[i] > 0 else 50.0 for i in range(n)]
    dx = [100 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) if pdi[i] + mdi[i] > 0 else 0.0 for i in range(n)]
    adx = [0.0] * n
    s = sum(dx[di_p + 1 : di_p + 1 + adx_p])
    if di_p + 1 + adx_p < n:
        adx[di_p + 1 + adx_p] = s / adx_p
    for i in range(di_p + 1 + adx_p + 1, n):
        s = s - s / adx_p + dx[i]
        adx[i] = s / adx_p
    return pdi, mdi, adx


def run_strategy(
    rows,
    sell_scheme="graduated",
    use_filter=True,
    buy_window=BUY_WINDOW,
    start=0,
    end=None,
    adx_min=ADX_MIN,
    rsi_min=RSI_MIN,
    reduce_ratio=REDUCE_RATIO,
):
    """单只 ETF 回测。sell_scheme: 'graduated' 或 'either'。start/end 用于滚动入场分析。"""
    n = len(rows) if end is None else end
    start = max(start, 0)
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]

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
        trend_ok[i] = pdi[i] >= mdi[i] and adx[i] >= adx_min and rsi_v[i] >= rsi_min
    trend_ok[0] = trend_ok[1] if n > 1 else False

    def win(arr, i, w):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    cash = 1.0
    shares = 0.0
    nav = []
    buys = reduces = exits = 0
    in_days = 0
    nav = []
    if start >= n:
        return [1.0], 0, 0, 0, 0
    for i in range(start, n):
        order = None
        if i > 0:
            sig_buy = win(macd_gold, i - 1, buy_window) and win(kdj_gold, i - 1, buy_window)
            if shares == 0:
                if sig_buy:
                    order = "BUY"
            else:
                double_dead = win(macd_dead, i - 1, DEAD_WINDOW) and win(kdj_dead, i - 1, DEAD_WINDOW)
                single_dead = (macd_dead[i - 1] and not kdj_dead[i - 1]) or (
                    kdj_dead[i - 1] and not macd_dead[i - 1]
                )
                weak = (not use_filter) or (not trend_ok[i - 1])
                if sell_scheme == "either":
                    if weak and (single_dead or double_dead):
                        order = "EXIT"
                else:
                    if weak and double_dead:
                        order = "EXIT"
                    elif weak and single_dead:
                        order = "REDUCE"
                    elif sig_buy:
                        order = "BUY_MORE"
        # 执行：开盘价（i 日）
        if order == "BUY" and shares == 0:
            shares = cash / (opens[i] * (1 + FEE))
            cash = 0.0
            buys += 1
        elif order == "BUY_MORE" and cash > 0:
            add = cash / (opens[i] * (1 + FEE))
            shares += add
            cash = 0.0
            buys += 1
        elif order == "REDUCE":
            sell_sh = shares * reduce_ratio
            cash += sell_sh * opens[i] * (1 - FEE)
            shares -= sell_sh
            reduces += 1
        elif order == "EXIT":
            cash += shares * opens[i] * (1 - FEE)
            shares = 0.0
            exits += 1
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, buys, reduces, exits, in_days


def run_portfolio(frames, names, common, sell_scheme, use_filter, buy_window=BUY_WINDOW):
    per_etf = {}
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        nav, buys, reduces, exits, in_days = run_strategy(rows, sell_scheme, use_filter, buy_window)
        per_etf[nm] = {
            "nav": nav,
            "buys": buys,
            "reduces": reduces,
            "exits": exits,
            "in_days": in_days,
            "total": nav[-1] - 1,
        }
    port = [sum(per_etf[nm]["nav"][i] for nm in names) / len(names) for i in range(len(common))]
    return port, per_etf


def hold(frames, names, common):
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    nav = []
    for d in common:
        nav.append(sum(by_day[nm][d] / by_day[nm][common[0]] for nm in names) / len(names))
    return nav


def main() -> int:
    hfq = load_hfq()
    qfq = load_qfq()

    # 长历史代理 C
    common_c = None
    for nm in PORTFOLIO_C:
        dates = set(r["day"] for r in hfq[nm])
        common_c = dates if common_c is None else common_c & dates
    common_c = sorted(common_c)
    hold_c = hold(hfq, PORTFOLIO_C, common_c)
    m_hold_c = metrics_from_prices(common_c, hold_c)

    variants = [
        ("V0 原版（同日双金叉/任一死叉清仓）", "either", False),
        ("V1 放宽（2天双金叉/分级卖出）", "graduated", False),
        ("V2 放宽+DMI/RSI过滤", "graduated", True),
    ]
    results_c = {}
    for label, scheme, flt in variants:
        bw = 1 if label.startswith("V0") else BUY_WINDOW
        nav, per = run_portfolio(hfq, PORTFOLIO_C, common_c, scheme, flt, bw)
        m = metrics_from_prices(common_c, nav)
        results_c[label] = {"nav": nav, "m": m, "per": per, "scheme": scheme, "flt": flt}

    # 实盘聚焦四只（qfq，2023-07 起）
    common_a = None
    for nm in PORTFOLIO_A:
        dates = set(r["day"] for r in qfq[nm])
        common_a = dates if common_a is None else common_a & dates
    common_a = sorted(common_a)
    hold_a = hold(qfq, PORTFOLIO_A, common_a)
    m_hold_a = metrics_from_prices(common_a, hold_a)
    nav_a2, per_a2 = run_portfolio(qfq, PORTFOLIO_A, common_a, "graduated", True)
    m_a2 = metrics_from_prices(common_a, nav_a2)
    nav_a1, _ = run_portfolio(qfq, PORTFOLIO_A, common_a, "graduated", False)
    m_a1 = metrics_from_prices(common_a, nav_a1)

    # 分年度（C，V2）
    yearly = {}
    for label, closes in [("hold", hold_c), ("v2", results_c["V2 放宽+DMI/RSI过滤"]["nav"])]:
        ypx = {}
        for d, c in zip(common_c, closes):
            ypx.setdefault(d[:4], []).append(c)
        yearly[label] = {y: px[-1] / px[0] - 1 for y, px in ypx.items() if len(px) >= 2}

    load_font()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    ax = axes[0]
    ax.plot(common_c, hold_c, label="持有（等权）", lw=1.5, color="black")
    colors = ["#d62728", "#ff7f0e", "#2ca02c"]
    for (label, _, _), c in zip(variants, colors):
        ax.plot(common_c, results_c[label]["nav"], label=label, lw=1.8, color=c)
    ax.set_title("C 代理聚焦四只（2020-11 ~ 2026-08）：持有 vs 策略三版")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax2 = axes[1]
    ax2.plot(common_a, hold_a, label="持有", lw=1.5, color="black")
    ax2.plot(common_a, nav_a1, label="V1 放宽（无过滤）", lw=1.6, color="#ff7f0e")
    ax2.plot(common_a, nav_a2, label="V2 放宽+DMI/RSI过滤", lw=2, color="#2ca02c")
    ax2.set_title("实盘聚焦四只（2023-07 ~ 2026-08）")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"etf_strategy_v2_{OUT_STAMP}.png"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    lines = []
    w = lines.append
    w("# ETF 双金叉策略 v2：放宽买入 + 分级卖出 + DMI/RSI 过滤")
    w("")
    w(f"> 长历史窗口：{common_c[0]} ~ {common_c[-1]}（{len(common_c)} 交易日，腾讯后复权）")
    w("")
    w("## 一、规则（用户口径）")
    w("")
    w(f"- 买入：{BUY_WINDOW} 个交易日内 MACD 金叉与 KDJ 金叉都出现 → 满仓；半仓持有中再出现 → 加回满仓")
    w("- 减仓：当日单死叉（MACD 或 KDJ 任一）且趋势转弱 → 减半仓")
    w(f"- 清仓：{DEAD_WINDOW} 日内双死叉（MACD 死叉与 KDJ 死叉都出现）且趋势转弱 → 清仓")
    w("- 趋势过滤（防短期波动）：+DI≥−DI 且 ADX≥25 且 RSI(12)≥50 → 视为强趋势，死叉不执行")
    w(f"- MACD {MACD_P[0]},{MACD_P[1]},{MACD_P[2]}；KDJ {KDJ_P[0]},{KDJ_P[1]},{KDJ_P[2]}；DMI {DMI_P[0]},{DMI_P[1]}；RSI {RSI_P}")
    w("- 信号 T 日收盘产生、T+1 开盘成交；佣金 0.03%/边；空仓资金收益 0")
    w("")
    w("## 二、C 代理聚焦四只（长历史）三版对比")
    w("")
    w("| 版本 | 累计 | 年化 | 最大回撤 | 夏普 | 买入/减仓/清仓 | 在场天数 |")
    w("|------|------|------|---------|------|--------------|---------|")
    w(f"| 持有（等权） | {fmt_pct(m_hold_c['total_return'])} | {fmt_pct(m_hold_c['annualized_return'])} | {fmt_pct(m_hold_c['max_drawdown'])} | {m_hold_c['sharpe']:.2f} | - | 100% |")
    for label, scheme, flt in variants:
        r = results_c[label]
        m = r["m"]
        tot_b = sum(v["buys"] for v in r["per"].values())
        tot_r = sum(v["reduces"] for v in r["per"].values())
        tot_e = sum(v["exits"] for v in r["per"].values())
        in_days = sum(v["in_days"] for v in r["per"].values()) / (len(PORTFOLIO_C) * len(common_c))
        w(
            f"| {label} | {fmt_pct(m['total_return'])} | {fmt_pct(m['annualized_return'])} "
            f"| {fmt_pct(m['max_drawdown'])} | {m['sharpe']:.2f} | {tot_b}/{tot_r}/{tot_e} | {in_days*100:.1f}% |"
        )
    w("")
    w("## 三、C 组合 V2 分年度")
    w("")
    w("| 年度 | 持有 | V2 | 差值 |")
    w("|------|------|-----|------|")
    for y in sorted(set(yearly["hold"]) | set(yearly["v2"])):
        a, b = yearly["hold"].get(y, 0.0), yearly["v2"].get(y, 0.0)
        w(f"| {y} | {fmt_pct(a)} | {fmt_pct(b)} | {fmt_pct(b - a)} |")
    w("")
    w("## 四、C 组合 V2 单只明细")
    w("")
    w("| ETF | 买入 | 减仓 | 清仓 | 在场 | 策略累计 | 持有累计 |")
    w("|-----|------|------|------|------|---------|---------|")
    r2 = results_c["V2 放宽+DMI/RSI过滤"]
    for nm in PORTFOLIO_C:
        s = r2["per"][nm]
        by_day = {r["day"]: r["close"] for r in hfq[nm]}
        hr = by_day[common_c[-1]] / by_day[common_c[0]] - 1
        w(
            f"| {nm} | {s['buys']} | {s['reduces']} | {s['exits']} | {s['in_days']/len(common_c)*100:.1f}% "
            f"| {fmt_pct(s['total'])} | {fmt_pct(hr)} |"
        )
    w("")
    w("## 五、实盘聚焦四只（2023-07 ~ 2026-08，半导体设备/科创芯片真实数据）")
    w("")
    w("| 版本 | 累计 | 最大回撤 | 夏普 |")
    w("|------|------|---------|------|")
    w(f"| 持有（等权） | {fmt_pct(m_hold_a['total_return'])} | {fmt_pct(m_hold_a['max_drawdown'])} | {m_hold_a['sharpe']:.2f} |")
    w(f"| V1 放宽（无过滤） | {fmt_pct(m_a1['total_return'])} | {fmt_pct(m_a1['max_drawdown'])} | {m_a1['sharpe']:.2f} |")
    w(f"| V2 放宽+DMI/RSI | {fmt_pct(m_a2['total_return'])} | {fmt_pct(m_a2['max_drawdown'])} | {m_a2['sharpe']:.2f} |")
    w("")
    w("## 六、结论速览")
    w("")
    w(f"- 长历史：V2 累计 {fmt_pct(r2['m']['total_return'])} vs 持有 {fmt_pct(m_hold_c['total_return'])}，"
      f"夏普 {r2['m']['sharpe']:.2f} vs {m_hold_c['sharpe']:.2f}，回撤 {fmt_pct(r2['m']['max_drawdown'])} vs {fmt_pct(m_hold_c['max_drawdown'])}。")
    w(f"- 实盘聚焦四只：V2 累计 {fmt_pct(m_a2['total_return'])} vs 持有 {fmt_pct(m_hold_a['total_return'])}。")
    report_path = OUT_DIR / f"etf_strategy_v2_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    summary = {
        "window_c": {"start": common_c[0], "end": common_c[-1], "days": len(common_c)},
        "params": {
            "macd": list(MACD_P),
            "kdj": list(KDJ_P),
            "dmi": list(DMI_P),
            "rsi": RSI_P,
            "adx_min": ADX_MIN,
            "rsi_min": RSI_MIN,
            "buy_window": BUY_WINDOW,
            "dead_window": DEAD_WINDOW,
            "reduce_ratio": REDUCE_RATIO,
        },
        "hold_c": {k: v for k, v in m_hold_c.items() if k not in ("dates", "closes")},
        "variants_c": {
            k: {kk: vv for kk, vv in v["m"].items() if kk not in ("dates", "closes")}
            for k, v in results_c.items()
        },
        "hold_a": {k: v for k, v in m_hold_a.items() if k not in ("dates", "closes")},
        "v2_a": {k: v for k, v in m_a2.items() if k not in ("dates", "closes")},
    }
    (OUT_DIR / f"etf_strategy_v2_{OUT_STAMP}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    print("\n=== C 代理聚焦四只（长历史）===")
    print(f"{'版本':<22}{'累计':>10}{'回撤':>10}{'夏普':>8}{'买入/减/清':>14}{'在场':>8}")
    print(f"{'持有（等权）':<22}{fmt_pct(m_hold_c['total_return']):>10}{fmt_pct(m_hold_c['max_drawdown']):>10}{m_hold_c['sharpe']:>8.2f}{'-':>14}{'100%':>8}")
    for label, scheme, flt in variants:
        r = results_c[label]
        m = r["m"]
        tb = sum(v["buys"] for v in r["per"].values())
        tr = sum(v["reduces"] for v in r["per"].values())
        te = sum(v["exits"] for v in r["per"].values())
        ind = sum(v["in_days"] for v in r["per"].values()) / (4 * len(common_c))
        print(f"{label:<22}{fmt_pct(m['total_return']):>10}{fmt_pct(m['max_drawdown']):>10}{m['sharpe']:>8.2f}{f'{tb}/{tr}/{te}':>14}{ind*100:>7.1f}%")
    print("\n=== 实盘聚焦四只（2023-07~）===")
    print(f"持有: {fmt_pct(m_hold_a['total_return'])}  V1: {fmt_pct(m_a1['total_return'])}  V2: {fmt_pct(m_a2['total_return'])}")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
