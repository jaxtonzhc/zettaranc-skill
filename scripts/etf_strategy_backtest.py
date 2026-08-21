#!/usr/bin/env python3
"""ETF 双金叉策略回测（MACD 6,13,5 + KDJ 9,3,3）。

规则：
  买入：同一日 MACD 金叉（DIF 上穿 DEA）且 KDJ 金叉（K 上穿 D）→ 双金叉
  卖出：MACD 死叉（DIF 下穿 DEA）或 KDJ 死叉（K 下穿 D）→ 任一死叉即卖
  执行：T 日收盘产生信号，T+1 日开盘成交（避免未来函数）
  仓位：单只 ETF 独立跑策略，满仓进出；空仓资金收益为 0
  费用：佣金 0.03%/边，ETF 无印花税
"""

import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

from etf_portfolio_backtest import (
    ETF_POOL,
    PORTFOLIO_A,
    PORTFOLIO_B,
    fmt_pct,
    load_font,
    metrics_from_prices,
)

TENCENT_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
DATA_DAYS = 760
CACHE_FILE = Path("data/etf_kline_cache_ohlc.json")
OUT_DIR = Path("reports")
OUT_STAMP = "20260819"

# 策略参数
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 6, 13, 5
KDJ_N, KDJ_K, KDJ_D = 9, 3, 3
FEE = 0.0003  # 佣金 0.03%/边
SELL_MODE = "either"  # either=任一死叉即卖；both=双死叉才卖


def fetch_klines_ohlc(symbol: str, days: int) -> list[dict] | None:
    """腾讯前复权日K，含 OHLC。"""
    r = requests.get(
        TENCENT_FQKLINE,
        params={"param": f"{symbol},day,,,{days},qfq"},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    payload = r.json()
    sec = payload.get("data", {}).get(symbol)
    if not sec:
        return None
    rows = sec.get("qfqday") or sec.get("day") or []
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


def load_klines() -> dict[str, list[dict]]:
    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("days") == DATA_DAYS and cached.get("source") == "tx-qfq-ohlc" and all(
            name in cached["data"] for name, _, _ in ETF_POOL
        ):
            print(f"[缓存] 命中 {CACHE_FILE}")
            return cached["data"]

    result: dict[str, list[dict]] = {}
    for name, ex, code in ETF_POOL:
        symbol = f"{ex}{code}"
        try:
            rows = fetch_klines_ohlc(symbol, DATA_DAYS)
            if rows:
                result[name] = sorted(rows, key=lambda x: x["day"])
                print(f"[OK] {name} {symbol} {len(rows)} 根")
            else:
                print(f"[FAIL] {name} {symbol} 空数据")
        except Exception as e:
            print(f"[FAIL] {name} {symbol}: {e}")
        time.sleep(0.3)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"source": "tx-qfq-ohlc", "days": DATA_DAYS, "data": result}, ensure_ascii=False)
    )
    return result


def ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    prev = None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes: list[float]) -> tuple[list[float], list[float]]:
    dif = [a - b for a, b in zip(ema(closes, MACD_FAST), ema(closes, MACD_SLOW))]
    dea = ema(dif, MACD_SIGNAL)
    return dif, dea


def kdj(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[list[float], list[float]]:
    k, d = 50.0, 50.0
    ks: list[float] = []
    ds: list[float] = []
    n = KDJ_N
    for i in range(len(closes)):
        lo = min(lows[max(0, i - n + 1) : i + 1])
        hi = max(highs[max(0, i - n + 1) : i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        ks.append(k)
        ds.append(d)
    return ks, ds


def strategy_backtest(rows: list[dict]) -> tuple[list[float], int, int]:
    """单只 ETF 双金叉策略回测，返回 (净值序列, 交易次数, 在场天数)。"""
    n = len(rows)
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]

    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)

    buy = [False] * n
    sell = [False] * n
    for i in range(1, n):
        macd_gold = dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]
        macd_dead = dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]
        kdj_gold = ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]
        kdj_dead = ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]
        if macd_gold and kdj_gold:
            buy[i] = True
        if SELL_MODE == "both":
            sell[i] = macd_dead and kdj_dead
        else:
            sell[i] = macd_dead or kdj_dead

    cash = 1.0
    shares = 0.0
    nav: list[float] = []
    trades = 0
    in_days = 0
    for i in range(n):
        if i > 0:
            if shares == 0 and buy[i - 1]:
                shares = cash / (opens[i] * (1 + FEE))
                cash = 0.0
                trades += 1
            elif shares > 0 and sell[i - 1]:
                cash = shares * opens[i] * (1 - FEE)
                shares = 0.0
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, trades, in_days


def align(frames: dict[str, list[dict]], names: list[str]) -> dict[str, dict]:
    """对齐公共交易日，返回 {name: {date: {open,high,low,close}}} 及日期索引。"""
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
    out = {}
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        out[nm] = {d: by_day[d] for d in common}
    return out, common


def run_strategy_portfolio(
    aligned: dict[str, dict], names: list[str]
) -> tuple[list[float], dict[str, dict]]:
    """每只 ETF 独立跑策略，组合净值 = 各策略净值等权平均（每日再平衡）。"""
    common = sorted(next(iter(aligned.values())))
    per_etf = {}
    for nm in names:
        rows = [
            {
                "day": d,
                "open": aligned[nm][d]["open"],
                "high": aligned[nm][d]["high"],
                "low": aligned[nm][d]["low"],
                "close": aligned[nm][d]["close"],
            }
            for d in common
        ]
        nav, trades, in_days = strategy_backtest(rows)
        per_etf[nm] = {
            "nav": nav,
            "trades": trades,
            "in_days": in_days,
            "total": nav[-1] - 1,
        }
    port_nav = [sum(per_etf[nm]["nav"][i] for nm in names) / len(names) for i in range(len(common))]
    return port_nav, per_etf


def main() -> int:
    frames = load_klines()
    aligned_a, common_a = align(frames, PORTFOLIO_A)
    aligned_b, common_b = align(frames, PORTFOLIO_B)

    pa_strat, a_etf = run_strategy_portfolio(aligned_a, PORTFOLIO_A)
    pb_strat, b_etf = run_strategy_portfolio(aligned_b, PORTFOLIO_B)

    # 持有基线：直接从缓存收盘价算等权净值
    def hold_portfolio(frames, names, common):
        nav = []
        for d in common:
            v = 0.0
            for nm in names:
                by_day = {r["day"]: r for r in frames[nm]}
                p0 = by_day[common[0]]["close"]
                v += by_day[d]["close"] / p0
            nav.append(v / len(names))
        return metrics_from_prices(common, nav)

    pa_hold = hold_portfolio(frames, PORTFOLIO_A, common_a)
    pb_hold = hold_portfolio(frames, PORTFOLIO_B, common_b)
    pa_strat_m = metrics_from_prices(common_a, pa_strat)
    pb_strat_m = metrics_from_prices(common_b, pb_strat)

    # 近一年
    def last(port: dict, n: int = 250) -> dict:
        return metrics_from_prices(port["dates"][-n:], port["closes"][-n:])

    pa_strat_1y = last(pa_strat_m)
    pa_hold_1y = last(pa_hold)

    load_font()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    ax = axes[0]
    ax.plot(pa_hold["dates"], pa_hold["closes"], label="A 持有（等权）", lw=1.5)
    ax.plot(pa_strat_m["dates"], pa_strat_m["closes"], label="A 双金叉策略", lw=2)
    ax.set_title("A 聚焦四只：持有 vs 双金叉策略（起始=1）")
    ax.legend()
    ax.grid(alpha=0.3)

    ax2 = axes[1]
    ax2.plot(pb_hold["dates"], pb_hold["closes"], label="B 持有（等权）", lw=1.5)
    ax2.plot(pb_strat_m["dates"], pb_strat_m["closes"], label="B 双金叉策略", lw=2)
    ax2.set_title("B 纯科技五只：持有 vs 双金叉策略（起始=1）")
    ax2.legend()
    ax2.grid(alpha=0.3)
    fig.tight_layout()

    chart = OUT_DIR / f"etf_strategy_{OUT_STAMP}.png"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    # 报告
    lines = []
    w = lines.append
    w("# ETF 双金叉策略回测（MACD 6,13,5 + KDJ 9,3,3）")
    w("")
    w(f"> 窗口：{common_a[0]} ~ {common_a[-1]}（{len(common_a)} 交易日），腾讯前复权日K")
    w("")
    w("## 一、策略规则")
    w("")
    w("- 买入：同一日 MACD 金叉（DIF 上穿 DEA）**且** KDJ 金叉（K 上穿 D）→ 双金叉")
    w(f"- 卖出：{'MACD 或 KDJ 任一死叉' if SELL_MODE == 'either' else 'MACD 与 KDJ 双死叉'}")
    w("- 执行：T 日收盘出信号，T+1 日开盘成交（无未来函数）")
    w("- 仓位：单只 ETF 独立满仓进出，空仓资金收益 0")
    w(f"- 费用：佣金 {FEE*100:.2f}%/边，无印花税")
    w(f"- MACD 参数：{MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}；KDJ 参数：{KDJ_N},{KDJ_K},{KDJ_D}")
    w("")
    w("## 二、A 聚焦四只：持有 vs 策略")
    w("")
    w("| 指标 | 持有（等权） | 双金叉策略 | 差额 |")
    w("|------|------------|-----------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("年化收益", "annualized_return", fmt_pct),
        ("年化波动", "annualized_vol", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = pa_hold[key], pa_strat_m[key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {pa_hold['sharpe']:.2f} | {pa_strat_m['sharpe']:.2f} | {pa_strat_m['sharpe'] - pa_hold['sharpe']:+.2f} |")
    w("")
    w("### 近一年（250 交易日）")
    w("")
    w("| 指标 | 持有 | 策略 |")
    w("|------|------|------|")
    w(f"| 累计收益 | {fmt_pct(pa_hold_1y['total_return'])} | {fmt_pct(pa_strat_1y['total_return'])} |")
    w(f"| 最大回撤 | {fmt_pct(pa_hold_1y['max_drawdown'])} | {fmt_pct(pa_strat_1y['max_drawdown'])} |")
    w(f"| 夏普 | {pa_hold_1y['sharpe']:.2f} | {pa_strat_1y['sharpe']:.2f} |")
    w("")
    w("## 三、B 纯科技五只：持有 vs 策略")
    w("")
    w("| 指标 | 持有（等权） | 双金叉策略 | 差额 |")
    w("|------|------------|-----------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("年化收益", "annualized_return", fmt_pct),
        ("年化波动", "annualized_vol", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = pb_hold[key], pb_strat_m[key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {pb_hold['sharpe']:.2f} | {pb_strat_m['sharpe']:.2f} | {pb_strat_m['sharpe'] - pb_hold['sharpe']:+.2f} |")
    w("")
    w("## 四、单只 ETF 策略明细（A 组合）")
    w("")
    w("| ETF | 交易次数 | 在场天数占比 | 策略累计 | 持有累计 |")
    w("|-----|---------|------------|---------|---------|")
    for nm in PORTFOLIO_A:
        s = a_etf[nm]
        rows = sorted(frames[nm], key=lambda x: x["day"])
        common_set = set(common_a)
        sub = [r for r in rows if r["day"] in common_set]
        hold_ret = sub[-1]["close"] / sub[0]["close"] - 1
        w(
            f"| {nm} | {s['trades']} | {s['in_days']/len(common_a)*100:.1f}% "
            f"| {fmt_pct(s['total'])} | {fmt_pct(hold_ret)} |"
        )
    w("")
    w("## 五、结论速览")
    w("")
    w(f"- A 策略累计 {fmt_pct(pa_strat_m['total_return'])} vs 持有 {fmt_pct(pa_hold['total_return'])}，"
      f"夏普 {pa_strat_m['sharpe']:.2f} vs {pa_hold['sharpe']:.2f}。")
    w(f"- B 策略累计 {fmt_pct(pb_strat_m['total_return'])} vs 持有 {fmt_pct(pb_hold['total_return'])}，"
      f"夏普 {pb_strat_m['sharpe']:.2f} vs {pb_hold['sharpe']:.2f}。")
    report_path = OUT_DIR / f"etf_strategy_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    summary = {
        "window": {"start": common_a[0], "end": common_a[-1], "days": len(common_a)},
        "params": {
            "macd": [MACD_FAST, MACD_SLOW, MACD_SIGNAL],
            "kdj": [KDJ_N, KDJ_K, KDJ_D],
            "fee": FEE,
            "sell_mode": SELL_MODE,
        },
        "portfolio_a_strategy": {k: v for k, v in pa_strat_m.items() if k not in ("dates", "closes")},
        "portfolio_a_hold": {k: v for k, v in pa_hold.items() if k not in ("dates", "closes")},
        "portfolio_b_strategy": {k: v for k, v in pb_strat_m.items() if k not in ("dates", "closes")},
        "portfolio_b_hold": {k: v for k, v in pb_hold.items() if k not in ("dates", "closes")},
        "per_etf": {
            k: {kk: vv for kk, vv in v.items() if kk != "nav"} for k, v in a_etf.items()
        },
    }
    (OUT_DIR / f"etf_strategy_{OUT_STAMP}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    print("\n=== A 聚焦四只：持有 vs 双金叉策略 ===")
    print(f"{'指标':<8}{'持有':>12}{'策略':>12}")
    for label, key in [
        ("累计收益", "total_return"),
        ("年化收益", "annualized_return"),
        ("最大回撤", "max_drawdown"),
        ("夏普", "sharpe"),
    ]:
        a, b = pa_hold[key], pa_strat_m[key]
        if key == "sharpe":
            print(f"{label:<8}{a:>12.2f}{b:>12.2f}")
        else:
            print(f"{label:<8}{fmt_pct(a):>12}{fmt_pct(b):>12}")
    print("\n=== B 纯科技五只：持有 vs 双金叉策略 ===")
    for label, key in [
        ("累计收益", "total_return"),
        ("最大回撤", "max_drawdown"),
        ("夏普", "sharpe"),
    ]:
        a, b = pb_hold[key], pb_strat_m[key]
        if key == "sharpe":
            print(f"{label:<8}{a:>12.2f}{b:>12.2f}")
        else:
            print(f"{label:<8}{fmt_pct(a):>12}{fmt_pct(b):>12}")
    print("\n单只 ETF 策略明细（A 组合）：")
    for nm in PORTFOLIO_A:
        s = a_etf[nm]
        print(f"  {nm}: 交易 {s['trades']} 次, 在场 {s['in_days']/len(common_a)*100:.1f}%, "
              f"策略 {fmt_pct(s['total'])}")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
