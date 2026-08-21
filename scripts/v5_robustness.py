#!/usr/bin/env python3
"""V5c 稳健性检验：换指数、扫 MA 参数、主板子集、样本外分段。

回应"过拟合"质疑：如果回撤减半+滚动期望为正的效果在
不同大盘指数/不同MA周期/不同股票子集/不同时间分段都成立，
说明是 A 股市场择时的真实效应，不是对单一标的的参数拟合。
"""

import json
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, "scripts")
from etf_portfolio_backtest import fmt_pct, metrics_from_prices

FEE = 0.0003
ALL10 = ["晶方科技", "卓胜微", "用友网络", "广联达", "华能蒙电", "申能股份", "平煤股份", "广汇能源", "云南铜业", "中金岭南"]
MAINBOARD = ["晶方科技", "用友网络", "广联达", "华能蒙电", "申能股份", "平煤股份", "广汇能源", "云南铜业", "中金岭南"]


def fetch_index(code, name):
    cache = Path(f"data/{name}_index.json")
    if cache.exists():
        return json.loads(cache.read_text())["data"]
    rows = []
    end = "2026-08-19"
    for _ in range(5):
        r = None
        for _ in range(3):
            try:
                r = requests.get(
                    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                    params={"param": f"{code},day,2018-01-01,{end},640,day"},
                    timeout=25,
                )
                break
            except Exception:
                time.sleep(1.5)
        if r is None:
            break
        sec = r.json()["data"][code]
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


def run_regime_only(rows, regime):
    n = len(rows)
    closes = [r["close"] for r in rows]
    opens = [r["open"] for r in rows]
    cash, shares = 1.0, 0.0
    nav, in_days = [], 0
    for i in range(n):
        if i > 0:
            if regime[i - 1] and shares == 0:
                shares = cash / (opens[i] * (1 + FEE))
                cash = 0.0
            elif not regime[i - 1] and shares > 0:
                cash += shares * opens[i] * (1 - FEE)
                shares = 0.0
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav


def main() -> int:
    frames = json.loads(open("data/stock_midcap_hfq.json").read())["data"]
    common = None
    for nm in ALL10:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    by_day = {nm: {r["day"]: r for r in frames[nm]} for nm in ALL10}

    indexes = {
        "创业板指": fetch_index("sz399006", "cyb"),
        "上证指数": fetch_index("sh000001", "sh"),
        "沪深300": fetch_index("sh000300", "hs300"),
        "深证成指": fetch_index("sz399001", "szcz"),
    }
    mas = [100, 120, 150, 200]

    def run(names, idx_data, ma_p):
        ic = [idx_data.get(d) for d in common]
        reg = [False] * len(common)
        for i in range(ma_p, len(common)):
            if ic[i] is None or any(v is None for v in ic[i - ma_p + 1 : i + 1]):
                continue
            reg[i] = ic[i] > sum(ic[i - ma_p + 1 : i + 1]) / ma_p
        navs = []
        for nm in names:
            rows = [by_day[nm][d] for d in common]
            navs.append(run_regime_only(rows, reg))
        return [sum(n[i] for n in navs) / len(navs) for i in range(len(common))]

    hold_nv = [
        sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in ALL10) / len(ALL10)
        for d in common
    ]
    m_hold = metrics_from_prices(common, hold_nv)
    print(f"持有(10只): {fmt_pct(m_hold['total_return'])} 回撤 {fmt_pct(m_hold['max_drawdown'])} 夏普 {m_hold['sharpe']:.2f}")

    print("\n=== 1) 换指数（MA120，10只）===")
    for iname, idata in indexes.items():
        nv = run(ALL10, idata, 120)
        m = metrics_from_prices(common, nv)
        r2 = [nv[e + 504] / nv[e] - 1 for e in range(0, len(common) - 504, 5)]
        rh = [hold_nv[e + 504] / hold_nv[e] - 1 for e in range(0, len(common) - 504, 5)]
        win = sum(a < b for a, b in zip(rh, r2)) / len(rh)
        print(
            f"  {iname}: {fmt_pct(m['total_return'])} 回撤 {fmt_pct(m['max_drawdown'])} "
            f"夏普 {m['sharpe']:.2f}  滚动2Y中位 {statistics.median(r2):+.1%} 胜率 {win*100:.0f}%"
        )

    print("\n=== 2) 扫 MA 参数（创业板指，10只）===")
    for mp in mas:
        nv = run(ALL10, indexes["创业板指"], mp)
        m = metrics_from_prices(common, nv)
        r2 = [nv[e + 504] / nv[e] - 1 for e in range(0, len(common) - 504, 5)]
        rh = [hold_nv[e + 504] / hold_nv[e] - 1 for e in range(0, len(common) - 504, 5)]
        win = sum(a < b for a, b in zip(rh, r2)) / len(rh)
        print(
            f"  MA{mp}: {fmt_pct(m['total_return'])} 回撤 {fmt_pct(m['max_drawdown'])} "
            f"夏普 {m['sharpe']:.2f}  滚动2Y中位 {statistics.median(r2):+.1%} 胜率 {win*100:.0f}%"
        )

    print("\n=== 3) 主板子集（9只，剔除创业板卓胜微，创指MA120）===")
    hold_mb = [
        sum(by_day[nm][d]["close"] / by_day[nm][common[0]]["close"] for nm in MAINBOARD)
        / len(MAINBOARD)
        for d in common
    ]
    m_hmb = metrics_from_prices(common, hold_mb)
    nv = run(MAINBOARD, indexes["创业板指"], 120)
    m = metrics_from_prices(common, nv)
    print(f"  主板持有: {fmt_pct(m_hmb['total_return'])} 回撤 {fmt_pct(m_hmb['max_drawdown'])} 夏普 {m_hmb['sharpe']:.2f}")
    print(f"  主板V5c:  {fmt_pct(m['total_return'])} 回撤 {fmt_pct(m['max_drawdown'])} 夏普 {m['sharpe']:.2f}")

    print("\n=== 4) 样本外分段（样本内2019-2022选参数，样本外2023-2026验证）===")
    for label, s, e in [("样本内 2019-2022", "2019-06-18", "2022-12-31"), ("样本外 2023-2026", "2023-01-01", "2026-08-19")]:
        idx = [i for i, d in enumerate(common) if s <= d <= e]
        sub = [common[i] for i in idx]
        nv = run(ALL10, indexes["创业板指"], 120)
        hold_sub = [hold_nv[i] for i in idx]
        v5_sub = [nv[i] for i in idx]
        mh = metrics_from_prices(sub, hold_sub)
        mv = metrics_from_prices(sub, v5_sub)
        print(
            f"  {label}: 持有 {fmt_pct(mh['total_return'])} 回撤 {fmt_pct(mh['max_drawdown'])}  "
            f"V5c {fmt_pct(mv['total_return'])} 回撤 {fmt_pct(mv['max_drawdown'])} 夏普 {mv['sharpe']:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
