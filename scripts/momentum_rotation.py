#!/usr/bin/env python3
"""月度动量轮动验证：每月末按过去12个月涨幅排名，持有最强 K 只 ETF 一个月。

口径：月末最后交易日收盘确认排名 → 次月首个交易日开盘成交；佣金 0.03%/边，无印花税。
ETF 池：32 只（预先固定，含宽基/行业/主题/海外），与 etf_scan 覆盖范围一致。
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

from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
FEE = 0.0003

POOL = [
    ("沪深300", "sh510300"), ("中证500", "sh510500"), ("创业板", "sz159915"),
    ("科创50", "sh588000"), ("红利", "sh510880"), ("红利低波", "sh512890"),
    ("有色金属", "sh512400"), ("消费", "sz159928"), ("医药", "sh512010"),
    ("创新药", "sz159992"), ("机器人", "sz159770"), ("半导体", "sh512480"),
    ("半导体设备", "sz159516"), ("芯片天弘", "sz159310"), ("芯片华夏", "sz159995"),
    ("通信", "sh515880"), ("人工智能", "sz159819"), ("酒", "sh512690"),
    ("军工", "sh512660"), ("银行", "sh512800"), ("证券", "sh512880"),
    ("光伏", "sh515790"), ("新能源", "sh516160"), ("房地产", "sh512200"),
    ("中概互联", "sh513050"), ("纳指", "sz159941"), ("恒生科技", "sz159740"),
    ("黄金", "sh518880"), ("煤炭", "sh515220"), ("5G", "sh515050"),
    ("科创芯片", "sh588200"), ("芯片国泰", "sh512760"),
]


def fetch_history(code):
    merged = {}
    end = "2026-08-19"
    for _ in range(6):
        rows = None
        for _ in range(3):
            try:
                r = requests.get(
                    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                    params={"param": f"{code},day,2018-01-01,{end},640,hfq"},
                    timeout=25,
                )
                sec = r.json().get("data", {}).get(code)
                rows = sec.get("hfqday") or sec.get("day") or []
                if rows:
                    break
            except Exception:
                time.sleep(1.5)
        if not rows:
            break
        cur_first = min(x[0] for x in rows)
        if merged:
            prev_first = min(v["day"] for v in merged.values())
            if cur_first >= prev_first:
                for x in rows:
                    merged[x[0]] = {"day": x[0], "close": float(x[2])}
                break
        for x in rows:
            merged[x[0]] = {
                "day": x[0],
                "open": float(x[1]),
                "close": float(x[2]),
                "high": float(x[3]),
                "low": float(x[4]),
                "vol": float(x[5]),
            }
        if cur_first <= "2018-12-01":
            break
        end = f"{int(cur_first[:4])}-{int(cur_first[5:7]) - 1:02d}-{cur_first[8:]}"
        time.sleep(0.35)
    return [merged[d] for d in sorted(merged)]


def load_pool():
    cache = Path("data/momentum_etf_hfq.json")
    if cache.exists():
        return json.loads(cache.read_text())["data"]
    data = {}
    for name, code in POOL:
        rows = fetch_history(code)
        if rows:
            data[name] = rows
            print(f"[OK] {name} {code} {rows[0]['day']}~{rows[-1]['day']} {len(rows)}根")
        else:
            print(f"[FAIL] {name} {code}")
        time.sleep(0.35)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"source": "tx-hfq", "data": data}, ensure_ascii=False))
    return data


def momentum_backtest(frames, names, lookback=252, k=3, start="2021-01-01"):
    """月度动量：月末排名，次月首日成交。返回 (nav, dates, 每月持仓, 每月收益)。"""
    # 全体交易日历（并集）
    all_dates = set()
    for nm in names:
        all_dates |= {r["day"] for r in frames[nm]}
    all_dates = sorted(all_dates)
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}

    # 月末日序列
    month_ends = []
    cur = None
    for d in all_dates:
        ym = d[:7]
        if ym != cur:
            cur = ym
            month_ends.append(d)
        else:
            month_ends[-1] = d

    def month_of(d):
        return d[:7]

    holdings = {}  # 排名月 -> [names]（月末收盘排名）
    ranking_dates = []
    prev_rank_day = None
    for me in month_ends:
        # 该月末需要 ≥ lookback 天历史
        mom = {}
        for nm in names:
            closes = by_day[nm]
            if me not in closes:
                continue
            # 取 me 往前 lookback 个交易日
            hist = [d for d in all_dates if d <= me]
            idx = len(hist) - 1
            if idx < lookback:
                continue
            past_day = hist[idx - lookback]
            if past_day not in closes or closes[past_day] <= 0:
                continue
            mom[nm] = closes[me] / closes[past_day] - 1
        if len(mom) < k:
            continue
        top = sorted(mom, key=mom.get, reverse=True)[:k]
        holdings[month_of(me)] = top
        ranking_dates.append(me)

    # 应用月 = 排名月的下一个月（避免未来函数）
    def next_month(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        m += 1
        if m > 12:
            y, m = y + 1, 1
        return f"{y:04d}-{m:02d}"

    applied = {next_month(k): v for k, v in holdings.items()}

    # 回测：排名月 M 的持仓在 M+1 月初成交，持有整月；估值用最后已知价前推
    trade_dates = [d for d in all_dates if d >= start]
    idx_map = {d: i for i, d in enumerate(all_dates)}
    nav, dates_out = [], []
    cash, shares = 1.0, {}
    last_px = {}  # 每只持仓的最后已知收盘价
    last_month = None
    for d in trade_dates:
        ym = month_of(d)
        target = applied.get(ym)
        if target is not None and last_month != ym:
            # 全部卖出（月度轮动：每月重新选仓）
            for nm in list(shares):
                if shares[nm] > 0:
                    px = by_day[nm].get(d, last_px.get(nm, 0))
                    cash += shares[nm] * px * (1 - FEE)
                    shares[nm] = 0.0
            # 目标月首日可交易者（当日有价的，否则跳过该 ETF 本月）
            avail = [nm for nm in target if d in by_day[nm]]
            if avail:
                per = cash / len(avail)
                for nm in avail:
                    px = by_day[nm][d]
                    shares[nm] = per / (px * (1 + FEE))
                    last_px[nm] = px
                cash = 0.0
            last_month = ym
        # 更新持仓最后已知价
        for nm, sh in shares.items():
            if sh > 0 and d in by_day[nm]:
                last_px[nm] = by_day[nm][d]
        val = cash
        for nm, sh in shares.items():
            if sh > 0:
                val += sh * last_px.get(nm, 0)
        if val == 0 and d.startswith("2025-03"):
            print(f"[dbg] {d} ym={ym} target={target} shares={ {k: round(v,4) for k,v in shares.items() if v>0} } last_px={ {k: round(v,3) for k,v in last_px.items()} } cash={round(cash,4)}")
        nav.append(val)
        dates_out.append(d)

    # 月度收益
    m_ret = {}
    m_nav = {}
    for d, v in zip(dates_out, nav):
        m_nav[d[:7]] = v
    mkeys = sorted(m_nav)
    for i in range(1, len(mkeys)):
        if m_nav[mkeys[i - 1]] == 0:
            print(f"[debug] zero nav at month {mkeys[i-1]} -> {mkeys[i]}")
            continue
        m_ret[mkeys[i]] = m_nav[mkeys[i]] / m_nav[mkeys[i - 1]] - 1
    return nav, dates_out, holdings, m_ret


def main() -> int:
    frames = load_pool()
    names = list(frames.keys())

    hold_nv, hold_dates = [], []
    all_dates = sorted({d for nm in names for d in (r["day"] for r in frames[nm])})
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    for d in all_dates:
        if d < "2021-01-01":
            continue
        vals = [by_day[nm][d] for nm in names if d in by_day[nm]]
        if vals:
            hold_nv.append(sum(vals) / len(vals))
            hold_dates.append(d)
    # 归一
    hold_nv = [v / hold_nv[0] for v in hold_nv]

    m_hold = metrics_from_prices(hold_dates, hold_nv)
    print(f"等权持有全部池（2021-01起）: {fmt_pct(m_hold['total_return'])} 回撤 {fmt_pct(m_hold['max_drawdown'])} 夏普 {m_hold['sharpe']:.2f}")

    for lookback, k, label in [(252, 3, "动量12月/K3"), (252, 2, "动量12月/K2"), (126, 3, "动量6月/K3")]:
        nav, dates, holdings, m_ret = momentum_backtest(frames, names, lookback, k)
        m = metrics_from_prices(dates, nav)
        wins = sum(1 for r in m_ret.values() if r > 0)
        print(
            f"\n{label}: {fmt_pct(m['total_return'])} 年化 {fmt_pct(m['annualized_return'])} "
            f"回撤 {fmt_pct(m['max_drawdown'])} 夏普 {m['sharpe']:.2f} 月度胜率 {wins/len(m_ret)*100:.0f}%"
        )
        # 分年度
        y_ret = {}
        ypx = {}
        for d, v in zip(dates, nav):
            ypx.setdefault(d[:4], []).append(v)
        for y, px in ypx.items():
            if len(px) > 2:
                y_ret[y] = px[-1] / px[0] - 1
        print("  分年度:", {y: f"{v*100:.1f}%" for y, v in y_ret.items()})
        if label == "动量12月/K3":
            # 年末持仓
            for y in sorted(holdings):
                if y.endswith("12"):
                    print(f"  {y} 末持仓: {holdings[y]}")

    # 图
    nav, dates, _, _ = momentum_backtest(frames, names, 252, 3)
    load_font()
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(hold_dates, hold_nv, label="等权持有全部池", color="black", lw=1.5)
    ax.plot(dates, nav, label="月度动量轮动(12月/K3)", color="#d62728", lw=2)
    ax.legend()
    ax.set_title("月度动量轮动 vs 等权持有（32只ETF池）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"momentum_rotation_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    print(f"\n图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
