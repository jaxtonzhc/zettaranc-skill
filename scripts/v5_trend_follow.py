#!/usr/bin/env python3
"""V5 趋势持有：个股站上 MA60 持有 / 跌破卖出 + 创业板指 MA120 大盘过滤。

反向思维：不再用金叉死叉猜进出，而是「牛市满仓、破位就跑」——
保留满仓持有的上涨收益，砍掉它的深回撤。
"""

import json
import statistics
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

sys.path.insert(0, "scripts")
from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_v2 import run_strategy

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003

TECH = ["晶方科技", "卓胜微", "用友网络", "广联达"]
ALL10 = ["晶方科技", "卓胜微", "用友网络", "广联达", "华能蒙电", "申能股份", "平煤股份", "广汇能源", "云南铜业", "中金岭南"]


def fetch_index():
    cache = Path("data/cyb_index.json")
    if cache.exists():
        return json.loads(cache.read_text())["data"]
    rows = []
    end = "2026-08-19"
    for _ in range(5):
        r = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"sz399006,day,2018-01-01,{end},640,day"},
            timeout=25,
        )
        sec = r.json()["data"]["sz399006"]
        page = sec.get("day") or []
        if not page:
            break
        for x in page:
            rows.append({"day": x[0], "close": float(x[2])})
        first = min(x[0] for x in page)
        if first <= "2018-06-01":
            break
        end = f"{int(first[:4])}-{int(first[5:7]) - 1:02d}-{first[8:]}"
        time.sleep(0.3)
    data = {r["day"]: r["close"] for r in rows}
    cache.write_text(json.dumps({"source": "tencent", "data": data}, ensure_ascii=False))
    return data


def ma(values, idx, period):
    if idx + 1 < period:
        return None
    return sum(values[idx - period + 1 : idx + 1]) / period


def run_v5(rows, regime, period=60, regime_only=False):
    """个股收盘 > MA(period) 且 regime=True 才持有；信号次日开盘执行。"""
    n = len(rows)
    closes = [r["close"] for r in rows]
    opens = [r["open"] for r in rows]
    hold_sig = []
    for i in range(n):
        if regime_only:
            hold_sig.append(regime[i])
        else:
            m = ma(closes, i, period)
            hold_sig.append(m is not None and closes[i] > m and regime[i])

    cash, shares = 1.0, 0.0
    nav, trades, in_days = [], 0, 0
    for i in range(n):
        if i > 0:
            want = hold_sig[i - 1]
            if want and shares == 0:
                shares = cash / (opens[i] * (1 + FEE))
                cash = 0.0
                trades += 1
            elif not want and shares > 0:
                cash += shares * opens[i] * (1 - FEE)
                shares = 0.0
                trades += 1
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, trades, in_days


def main() -> int:
    frames = json.loads(open("data/stock_midcap_hfq.json").read())["data"]
    cyb = fetch_index()
    common = None
    for nm in ALL10:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in ALL10}

    cyb_close = [cyb.get(d) for d in common]
    regime = [False] * len(common)
    for i in range(len(common)):
        if cyb_close[i] is None or i < 120:
            continue
        m120 = sum(cyb_close[i - 119 : i + 1]) / 120
        regime[i] = cyb_close[i] > m120

    for subset_name, names in [("科技4只", TECH), ("全部10只", ALL10)]:
        hold_nv = [
            sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names) / len(names)
            for d in common
        ]
        v5a_navs, v5b_navs, v5c_navs, v2_navs = [], [], [], []
        for nm in names:
            rows = [by_day[nm][d] for d in common]
            v5a, *_ = run_v5(rows, [True] * len(common))
            v5b, *_ = run_v5(rows, regime)
            v5c, *_ = run_v5(rows, regime, regime_only=True)
            v2, *_ = run_strategy(rows, "graduated", True)
            v5a_navs.append(v5a)
            v5b_navs.append(v5b)
            v5c_navs.append(v5c)
            v2_navs.append(v2)
        h_nv = [sum(n[i] for n in v5a_navs) / len(names) for i in range(len(common))]
        b_nv = [sum(n[i] for n in v5b_navs) / len(names) for i in range(len(common))]
        c_nv = [sum(n[i] for n in v5c_navs) / len(names) for i in range(len(common))]
        v2_nv = [sum(n[i] for n in v2_navs) / len(names) for i in range(len(common))]

        m_h = metrics_from_prices(common, hold_nv)
        m_a = metrics_from_prices(common, h_nv)
        m_b = metrics_from_prices(common, b_nv)
        m_c = metrics_from_prices(common, c_nv)
        m_v2 = metrics_from_prices(common, v2_nv)
        print(f"\n=== {subset_name}（{common[0]}~{common[-1]}）===")
        print(f"持有: {fmt_pct(m_h['total_return'])} 回撤 {fmt_pct(m_h['max_drawdown'])} 夏普 {m_h['sharpe']:.2f}")
        print(f"V2:   {fmt_pct(m_v2['total_return'])} 回撤 {fmt_pct(m_v2['max_drawdown'])} 夏普 {m_v2['sharpe']:.2f}")
        print(f"V5a(个股MA60): {fmt_pct(m_a['total_return'])} 回撤 {fmt_pct(m_a['max_drawdown'])} 夏普 {m_a['sharpe']:.2f}")
        print(f"V5b(MA60+创指MA120): {fmt_pct(m_b['total_return'])} 回撤 {fmt_pct(m_b['max_drawdown'])} 夏普 {m_b['sharpe']:.2f}")
        print(f"V5c(仅创指MA120择时): {fmt_pct(m_c['total_return'])} 回撤 {fmt_pct(m_c['max_drawdown'])} 夏普 {m_c['sharpe']:.2f}")
        if subset_name == "科技4只":
            print("  分年度 持有 vs V5b:")
            for y in range(2019, 2027):
                idx = [i for i, d in enumerate(common) if d.startswith(str(y))]
                if len(idx) < 10:
                    continue
                i0, i1 = idx[0], idx[-1]
                print(
                    f"    {y}: 持有 {fmt_pct(hold_nv[i1]/hold_nv[i0]-1)}  "
                    f"V5b {fmt_pct(b_nv[i1]/b_nv[i0]-1)}"
                )
        for h, label in [(252, "1年"), (504, "2年")]:
            rh, ra, rb, rc = [], [], [], []
            for e in range(0, len(common) - h, 5):
                rh.append(hold_nv[e + h] / hold_nv[e] - 1)
                ra.append(h_nv[e + h] / h_nv[e] - 1)
                rb.append(b_nv[e + h] / b_nv[e] - 1)
                rc.append(c_nv[e + h] / c_nv[e] - 1)
            print(
                f"  滚动{label}: 持有中位 {statistics.median(rh):+.1%}  "
                f"V5a {statistics.median(ra):+.1%}(胜率{sum(a<b for a,b in zip(rh,ra))/len(rh)*100:.0f}%)  "
                f"V5b {statistics.median(rb):+.1%}(胜率{sum(a<b for a,b in zip(rh,rb))/len(rh)*100:.0f}%)  "
                f"V5c {statistics.median(rc):+.1%}(胜率{sum(a<b for a,b in zip(rh,rc))/len(rh)*100:.0f}%)"
            )

    names = TECH
    hold_nv = [
        sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in names) / len(names)
        for d in common
    ]
    v5a_navs, v5b_navs = [], []
    for nm in names:
        rows = [by_day[nm][d] for d in common]
        v5a, *_ = run_v5(rows, [True] * len(common))
        v5b, *_ = run_v5(rows, regime)
        v5a_navs.append(v5a)
        v5b_navs.append(v5b)
    a_nv = [sum(n[i] for n in v5a_navs) / len(names) for i in range(len(common))]
    b_nv = [sum(n[i] for n in v5b_navs) / len(names) for i in range(len(common))]
    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(common, hold_nv, label="持有", color="black", lw=1.5)
    ax.plot(common, a_nv, label="V5a 个股MA60", color="#1f77b4", lw=2)
    ax.plot(common, b_nv, label="V5b MA60+创指过滤", color="#2ca02c", lw=2)
    ax.set_title("科技4只：V5 趋势持有 vs 满仓持有")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"v5_trend_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
