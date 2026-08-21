#!/usr/bin/env python3
"""A 股中小盘（流通市值 100-500 亿）五行业代表股三策略回测。

行业 × 2 只：科技(晶方科技/卓胜微)、软件(用友网络/广联达)、电力(华能蒙电/申能股份)、
能源(平煤股份/广汇能源)、有色(云南铜业/中金岭南)。

三策略：
  1. 纯持有：10 只等权买入持有
  2. 用户双金叉 V2：2天双金叉买 / 单死叉减半 / 双死叉清仓 / DMI+RSI 趋势过滤
  3. Z 哥战法：B1+B2+SB1+长安 多策略扫描 + 少妇六步闭环离场（项目自带引擎）

全窗口对比 + 滚动入场分布（1年/2年）。
"""

import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

sys.path.insert(0, ".")
from modules.backtest.portfolio import PortfolioBacktestEngine, PortfolioConfig
from modules.indicators.core import calculate_ema_series
from modules.datasource import dict_to_daily
from modules.indicators.core import (
    precompute_bbi_sequence,
    precompute_kdj_sequence,
    precompute_macd_sequence,
)
from modules.loop_engine import LoopConfig

from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_v2 import run_strategy

import modules.indicators.price_patterns.base as _ppb
import modules.loop_engine as _le

# ── 双线（白线/黄线）序列缓存：把 O(n²) 的逐日重算变成 O(1) 查表 ──
_DL_CACHE: dict[int, dict] = {}
_orig_white = _ppb.calculate_zg_white
_orig_dg = _ppb.calculate_dg_yellow


def _cached_white(klines) -> float:
    if len(klines) < 10:
        return 0
    ent = _DL_CACHE.get(id(klines[0]))
    if ent is None or len(ent["white"]) <= len(klines) - 1:
        return _orig_white(klines)
    return ent["white"][len(klines) - 1]


def _cached_dg(klines) -> float:
    if len(klines) < 114:
        return 0
    ent = _DL_CACHE.get(id(klines[0]))
    if ent is None or len(ent["dg"]) <= len(klines) - 1:
        return _orig_dg(klines)
    return ent["dg"][len(klines) - 1]


_ppb.calculate_zg_white = _cached_white
_ppb.calculate_dg_yellow = _cached_dg
_le.calculate_zg_white = _cached_white
_le.calculate_dg_yellow = _cached_dg


def prefill_double_line_cache(km: dict[str, list]) -> None:
    """按股票预计算白线/黄线全序列，供缓存的战法检测 O(1) 读取。"""
    import pandas as pd

    for daily in km.values():
        if not daily:
            continue
        key = id(daily[0])
        if key in _DL_CACHE:
            continue
        closes = [k.close for k in daily]
        n = len(closes)
        ema1 = calculate_ema_series(closes, 10)
        ema2 = calculate_ema_series(ema1, 10) if ema1 else []
        white = [round(v, 2) for v in ema2] if ema2 else [0.0] * n
        s = pd.Series(closes)
        ma14 = s.rolling(14, min_periods=14).mean()
        ma28 = s.rolling(28, min_periods=28).mean()
        ma57 = s.rolling(57, min_periods=57).mean()
        ma114 = s.rolling(114, min_periods=114).mean()
        dg = ((ma14 + ma28 + ma57 + ma114) / 4).round(2).fillna(0).tolist()
        _DL_CACHE[key] = {"white": white, "dg": dg}

TENCENT_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
CACHE_FILE = Path("data/stock_midcap_hfq.json")
OUT_DIR = Path("reports")
OUT_STAMP = "20260819"
EARLY_START = "2018-01-01"
LATEST = "2026-08-19"

STOCKS = [
    ("晶方科技", "sh603005", "科技"),
    ("卓胜微", "sz300782", "科技"),
    ("用友网络", "sh600588", "软件"),
    ("广联达", "sz002410", "软件"),
    ("华能蒙电", "sh600863", "电力"),
    ("申能股份", "sh600642", "电力"),
    ("平煤股份", "sh601666", "能源"),
    ("广汇能源", "sh600256", "能源"),
    ("云南铜业", "sz000878", "有色"),
    ("中金岭南", "sz000060", "有色"),
]


def fetch_page(code, end):
    for _ in range(3):
        try:
            r = requests.get(
                TENCENT_FQKLINE,
                params={"param": f"{code},day,{EARLY_START},{end},640,hfq"},
                timeout=25,
            )
            if r.status_code != 200:
                return None
            sec = r.json().get("data", {}).get(code)
            if not sec:
                return None
            rows = sec.get("hfqday") or sec.get("day") or []
            if rows:
                return [
                    {
                        "day": x[0],
                        "open": float(x[1]),
                        "close": float(x[2]),
                        "high": float(x[3]),
                        "low": float(x[4]),
                        "vol": float(x[5]),
                    }
                    for x in rows
                ]
        except Exception:
            pass
        time.sleep(1.0)
    return None


def fetch_history(code):
    merged = {}
    end = LATEST
    for _ in range(8):
        rows = fetch_page(code, end)
        if not rows:
            break
        cur_first = min(r["day"] for r in rows)
        if merged:
            prev_first = min(r["day"] for r in merged.values())
            if cur_first >= prev_first:  # 没继续往回翻 = 数据到头
                for r in rows:
                    merged[r["day"]] = r
                break
        for r in rows:
            merged[r["day"]] = r
        if cur_first <= "2018-01-15":
            break
        end = f"{int(cur_first[:4])}-{int(cur_first[5:7]) - 1:02d}-{cur_first[8:]}"
        time.sleep(0.3)
    return [merged[d] for d in sorted(merged)]


def load_klines():
    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("source") == "tx-hfq" and all(
            name in cached["data"] for name, _, _ in STOCKS
        ):
            print(f"[缓存] 命中 {CACHE_FILE}")
            return cached["data"]
    result = {}
    for name, code, _ in STOCKS:
        rows = fetch_history(code)
        if rows:
            result[name] = rows
            print(f"[OK] {name} {code} {rows[0]['day']}~{rows[-1]['day']} {len(rows)}根")
        else:
            print(f"[FAIL] {name} {code}")
        time.sleep(0.3)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"source": "tx-hfq", "data": result}, ensure_ascii=False))
    return result


def common_dates(frames, names):
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    return sorted(common)


def hold_nav(frames, names, common):
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    return [
        sum(by_day[nm][d] / by_day[nm][common[0]] for nm in names) / len(names) for d in common
    ]


def v2_nav(frames, names, common):
    navs = []
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        nav, *_ = run_strategy(rows, "graduated", True)
        navs.append(nav)
    return [sum(n[i] for n in navs) / len(navs) for i in range(len(common))]


def build_daily(frames, names, common):
    """构造 DailyData 并预填 KDJ/BBI/MACD，让战法检测变为 O(1) 查表。"""
    klines_map = {}
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = []
        prev = None
        for d in common:
            r = by_day[d]
            rows.append(
                {
                    "ts_code": nm,
                    "trade_date": d,
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "vol": r["vol"],
                    "amount": r["vol"] * r["close"],
                    "pct_chg": r["close"] / prev - 1 if prev else 0.0,
                }
            )
            prev = r["close"]
        daily = dict_to_daily(rows)
        kseq = precompute_kdj_sequence(daily)
        bseq = precompute_bbi_sequence(daily)
        difs, deas, hiss = precompute_macd_sequence(daily)
        for i, d in enumerate(daily):
            d.kdj_k, d.kdj_d, d.kdj_j = kseq[i]
            d.bbi = bseq[i]
            d.macd_dif, d.macd_dea, d.macd_hist = difs[i], deas[i], hiss[i]
        klines_map[nm] = daily
    prefill_double_line_cache(klines_map)
    return klines_map


def run_zg(km, common, start=None, end=None):
    cfg = PortfolioConfig(
        initial_capital=1_000_000.0,
        max_positions=10,
        position_pct=0.1,
        min_cash_pct=0.0,
        max_entries_per_day=3,
        min_signal_days=30,
        enabled_strategies=["B1", "B2", "SB1", "长安"],
    )
    engine = PortfolioBacktestEngine(portfolio_config=cfg, loop_config=LoopConfig())
    return engine.run_with_data(km, common, start_date=start, end_date=end)


_ZG_KM = None
_ZG_COMMON = None


def _zg_init(km, common):
    global _ZG_KM, _ZG_COMMON
    _ZG_KM = km
    _ZG_COMMON = common
    prefill_double_line_cache(km)


def _zg_worker(eh):
    e, h = eh
    r = run_zg(_ZG_KM, _ZG_COMMON, start=_ZG_COMMON[e], end=_ZG_COMMON[e + h])
    return h, (r.net_values[-1] / r.net_values[0] - 1 if r.net_values else None)


def main() -> int:
    frames = load_klines()
    names = [n for n, _, _ in STOCKS]
    common = common_dates(frames, names)
    print(f"\n公共窗口: {common[0]} ~ {common[-1]} ({len(common)} 交易日)")

    hold_nv = hold_nav(frames, names, common)
    v2_nv = v2_nav(frames, names, common)
    km = build_daily(frames, names, common)
    r_zg = run_zg(km, common)

    m_hold = metrics_from_prices(common, hold_nv)
    m_v2 = metrics_from_prices(common, v2_nv)
    m_zg = metrics_from_prices(r_zg.dates, r_zg.net_values) if len(r_zg.net_values) > 60 else None

    step_hold = 5
    step_zg = 20
    horizons = [252, 504]
    roll = {h: {"hold": [], "v2": [], "zg": [], "zg_n": 0} for h in horizons}
    n = len(common)
    for e in range(0, n - min(horizons), step_hold):
        for h in horizons:
            if e + h >= n:
                continue
            hr = hold_nv[e + h] / hold_nv[e] - 1
            vr = v2_nv[e + h] / v2_nv[e] - 1
            roll[h]["hold"].append(hr)
            roll[h]["v2"].append(vr)
    # Z哥滚动：引擎重，用多进程并行
    tasks = []
    for e in range(0, n - min(horizons), step_zg):
        for h in horizons:
            if e + h >= n:
                continue
            tasks.append((e, h))

    zg_rows = {h: [] for h in horizons}
    with ProcessPoolExecutor(max_workers=8, initializer=_zg_init, initargs=(km, common)) as ex:
        for h, ret in ex.map(_zg_worker, tasks):
            if ret is not None:
                zg_rows[h].append(ret)
                roll[h]["zg_n"] += 1
            print(f"  [Z哥滚动] h={h} 样本={roll[h]['zg_n']}", flush=True)
    for h in horizons:
        roll[h]["zg"] = zg_rows[h]

    per_stock = []
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        hr = rows[-1]["close"] / rows[0]["close"] - 1
        vnav, *_ = run_strategy(rows, "graduated", True)
        vr = vnav[-1] - 1
        per_stock.append((nm, hr, vr))

    load_font()
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    ax = axes[0][0]
    ax.plot(common, hold_nv, label="纯持有", color="black", lw=1.5)
    ax.plot(common, v2_nv, label="双金叉 V2", color="#2ca02c", lw=2)
    if m_zg:
        zg_norm = [v / r_zg.net_values[0] for v in r_zg.net_values]
        ax.plot(r_zg.dates, zg_norm, label="Z哥多战法", color="#d62728", lw=1.8)
    ax.set_title("全窗口净值（起始=1）")
    ax.legend()
    ax.grid(alpha=0.3)

    for idx, h in enumerate(horizons):
        ax = axes[0][1] if idx == 0 else axes[1][0]
        data = [roll[h]["hold"], roll[h]["v2"], roll[h]["zg"]]
        labels = ["持有", "V2", "Z哥"]
        ax.boxplot(data, tick_labels=labels, widths=0.5)
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_title(f"入场后 {h//252} 年收益分布（{len(roll[h]['hold'])} 入场点）")
        ax.grid(alpha=0.3)

    ax = axes[1][1]
    names_s = [p[0] for p in per_stock]
    x = range(len(names_s))
    ax.bar([i - 0.2 for i in x], [p[1] for p in per_stock], width=0.4, label="持有")
    ax.bar([i + 0.2 for i in x], [p[2] for p in per_stock], width=0.4, label="V2", color="#2ca02c")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names_s, rotation=45, fontsize=8)
    ax.set_title("单只累计收益：持有 vs V2")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"stock_midcap_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    lines = []
    w = lines.append
    w("# A 股中小盘五行业三策略回测")
    w("")
    w(f"> 窗口：{common[0]} ~ {common[-1]}（{len(common)} 交易日），腾讯后复权日K")
    w("")
    w("## 一、样本（流通市值 100-500 亿）")
    w("")
    w("| 行业 | 股票 |")
    w("|------|------|")
    for nm, code, sec in STOCKS:
        w(f"| {sec} | {nm} {code} |")
    w("")
    w("## 二、全窗口对比（10 只等权）")
    w("")
    w("| 策略 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 交易 | 胜率 |")
    w("|------|------|------|------|---------|------|------|------|")
    w(
        f"| 纯持有 | {fmt_pct(m_hold['total_return'])} | {fmt_pct(m_hold['annualized_return'])} "
        f"| {fmt_pct(m_hold['annualized_vol'])} | {fmt_pct(m_hold['max_drawdown'])} | {m_hold['sharpe']:.2f} | - | - |"
    )
    w(
        f"| 双金叉 V2 | {fmt_pct(m_v2['total_return'])} | {fmt_pct(m_v2['annualized_return'])} "
        f"| {fmt_pct(m_v2['annualized_vol'])} | {fmt_pct(m_v2['max_drawdown'])} | {m_v2['sharpe']:.2f} | - | - |"
    )
    if m_zg:
        w(
            f"| Z哥多战法 | {fmt_pct(m_zg['total_return'])} | {fmt_pct(m_zg['annualized_return'])} "
            f"| {fmt_pct(m_zg['annualized_vol'])} | {fmt_pct(m_zg['max_drawdown'])} | {m_zg['sharpe']:.2f} "
            f"| {r_zg.total_trades} | {r_zg.win_rate*100:.0f}% |"
        )
    w("")
    w("## 三、滚动入场分布")
    w("")
    for h, label in [(252, "1年"), (504, "2年")]:
        r = roll[h]
        w(f"### 入场后 {label}（{len(r['hold'])} 个入场点）")
        w("")
        w("| 统计量 | 持有 | V2 | Z哥 |")
        w("|--------|------|-----|-----|")
        for stat, fn in [
            ("均值", statistics.mean),
            ("中位数", statistics.median),
            ("P25", lambda x: sorted(x)[int(len(x) * 0.25)]),
            ("最差", min),
        ]:
            zg_v = fn(r["zg"]) if r["zg"] else float("nan")
            w(f"| {stat} | {fmt_pct(fn(r['hold']))} | {fmt_pct(fn(r['v2']))} | {fmt_pct(zg_v)} |")
        v2_win = sum(1 for a, b in zip(r["hold"], r["v2"]) if b > a) / len(r["hold"])
        zg_win = sum(1 for a, b in zip(r["hold"], r["zg"]) if b > a) / len(r["zg"]) if r["zg"] else float("nan")
        w(f"| 胜率(vs持有) | - | {v2_win*100:.1f}% | {zg_win*100:.1f}% |")
        w("")
    w("## 四、单只累计（全窗口）")
    w("")
    w("| 股票 | 持有 | V2 |")
    w("|------|------|-----|")
    for nm, hr, vr in per_stock:
        w(f"| {nm} | {fmt_pct(hr)} | {fmt_pct(vr)} |")
    report_path = OUT_DIR / f"stock_midcap_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    print("\n=== 全窗口 ===")
    print(f"持有: {fmt_pct(m_hold['total_return'])} 回撤 {fmt_pct(m_hold['max_drawdown'])} 夏普 {m_hold['sharpe']:.2f}")
    print(f"V2:   {fmt_pct(m_v2['total_return'])} 回撤 {fmt_pct(m_v2['max_drawdown'])} 夏普 {m_v2['sharpe']:.2f}")
    if m_zg:
        print(
            f"Z哥:  {fmt_pct(m_zg['total_return'])} 回撤 {fmt_pct(m_zg['max_drawdown'])} 夏普 {m_zg['sharpe']:.2f} "
            f"交易 {r_zg.total_trades} 胜率 {r_zg.win_rate*100:.0f}%"
        )
    print("\n=== 滚动入场 ===")
    for h, label in [(252, "1年"), (504, "2年")]:
        r = roll[h]
        zg_m = statistics.median(r["zg"]) if r["zg"] else float("nan")
        zg_win = sum(1 for a, b in zip(r["hold"], r["zg"]) if b > a) / len(r["zg"]) * 100 if r["zg"] else float("nan")
        print(
            f"{label}: 持有中位 {statistics.median(r['hold']):+.1%}  V2中位 {statistics.median(r['v2']):+.1%} "
            f"(胜率 {sum(1 for a,b in zip(r['hold'],r['v2']) if b>a)/len(r['hold'])*100:.0f}%)  "
            f"Z哥中位 {zg_m:+.1%} (胜率 {zg_win:.0f}%)"
        )
    print("\n单只:")
    for nm, hr, vr in per_stock:
        print(f"  {nm}: 持有 {fmt_pct(hr)}  V2 {fmt_pct(vr)}")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
