#!/usr/bin/env python3
"""ETF 双金叉策略长历史回测（后复权，跨 2021-2024 熊市段）。

背景：半导体设备ETF(159516) 2023-07 上市、科创芯片ETF(588200) 2022-11 上市，
聚焦四只无法回测到 2023 年之前。这里用上市早的代理标的映射：
  半导体设备 → 半导体ETF(512480)
  科创芯片   → 科创50ETF(588000)
  芯片       → 芯片ETF华夏(159995)
  纳指       → 纳指ETF广发(159941)

数据：腾讯财经后复权(hfq)日K，分页抓取合并（后复权以上市日为参照，跨段一致）。
策略规则与 etf_strategy_backtest.py 相同：双金叉买、任一死叉卖、次日开盘成交。
"""

import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_backtest import strategy_backtest

TENCENT_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
CACHE_FILE = Path("data/etf_kline_cache_hfq.json")
OUT_DIR = Path("reports")
OUT_STAMP = "20260819"

EARLY_START = "2019-01-01"
LATEST = "2026-08-19"

# 代理组合 C（聚焦四只映射）与 F（纯科技五只映射）
PROXY_POOL = [
    ("半导体ETF", "sh512480"),
    ("科创50ETF", "sh588000"),
    ("芯片ETF华夏", "sz159995"),
    ("纳指ETF广发", "sz159941"),
    ("通信ETF", "sh515880"),
    ("人工智能ETF", "sz159819"),
]
PORTFOLIO_C = ["半导体ETF", "科创50ETF", "芯片ETF华夏", "纳指ETF广发"]
PORTFOLIO_F = ["半导体ETF", "科创50ETF", "芯片ETF华夏", "通信ETF", "人工智能ETF"]


def fetch_page(code: str, end: str, fq: str = "hfq") -> list[dict] | None:
    r = requests.get(
        TENCENT_FQKLINE,
        params={"param": f"{code},day,{EARLY_START},{end},640,{fq}"},
        timeout=25,
    )
    if r.status_code != 200:
        return None
    payload = r.json()
    sec = payload.get("data", {}).get(code)
    if not sec:
        return None
    rows = sec.get(f"{fq}day") or sec.get("day") or []
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


def fetch_full_history(code: str) -> list[dict]:
    """分页向后翻取全历史，按日期合并（hfq 跨段一致）。"""
    merged: dict[str, dict] = {}
    end = LATEST
    for _ in range(8):
        rows = fetch_page(code, end)
        if not rows:
            break
        for r in rows:
            merged[r["day"]] = r
        first = min(r["day"] for r in rows)
        if first <= "2019-12-01":
            break
        end = f"{int(first[:4])}-{int(first[5:7]) - 1:02d}-{first[8:]}"
        if end <= "2019-12-01":
            break
        time.sleep(0.3)
    return [merged[d] for d in sorted(merged)]


def load_klines() -> dict[str, list[dict]]:
    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("source") == "tx-hfq" and all(
            name in cached["data"] for name, _ in PROXY_POOL
        ):
            print(f"[缓存] 命中 {CACHE_FILE}")
            return cached["data"]
    result = {}
    for name, code in PROXY_POOL:
        try:
            rows = fetch_full_history(code)
            if rows:
                result[name] = rows
                print(f"[OK] {name} {code} {rows[0]['day']}~{rows[-1]['day']} {len(rows)}根")
            else:
                print(f"[FAIL] {name} {code} 空数据")
        except Exception as e:
            print(f"[FAIL] {name} {code}: {e}")
        time.sleep(0.4)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"source": "tx-hfq", "data": result}, ensure_ascii=False))
    return result


def align_common(frames: dict[str, list[dict]], names: list[str]) -> tuple[dict[str, dict], list[str]]:
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


def run_strategy(frames: dict[str, list[dict]], names: list[str], common: list[str]) -> tuple[list[float], dict]:
    per_etf = {}
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        nav, trades, in_days = strategy_backtest(rows)
        per_etf[nm] = {"nav": nav, "trades": trades, "in_days": in_days, "total": nav[-1] - 1}
    port_nav = [sum(per_etf[nm]["nav"][i] for nm in names) / len(names) for i in range(len(common))]
    return port_nav, per_etf


def hold_portfolio(frames: dict[str, list[dict]], names: list[str], common: list[str]) -> list[float]:
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    nav = []
    for d in common:
        v = sum(by_day[nm][d] / by_day[nm][common[0]] for nm in names) / len(names)
        nav.append(v)
    return nav


def slice_window(dates: list[str], closes: list[float], start: str, end: str):
    idx = [i for i, d in enumerate(dates) if start <= d <= end]
    if len(idx) < 60:
        return None
    return metrics_from_prices([dates[i] for i in idx], [closes[i] for i in idx])


def main() -> int:
    frames = load_klines()
    aligned_c, common_c = align_common(frames, PORTFOLIO_C)
    aligned_f, common_f = align_common(frames, PORTFOLIO_F)

    nav_c_strat, c_etf = run_strategy(frames, PORTFOLIO_C, common_c)
    nav_f_strat, _ = run_strategy(frames, PORTFOLIO_F, common_f)
    nav_c_hold = hold_portfolio(frames, PORTFOLIO_C, common_c)
    nav_f_hold = hold_portfolio(frames, PORTFOLIO_F, common_f)

    m_c_hold = metrics_from_prices(common_c, nav_c_hold)
    m_c_strat = metrics_from_prices(common_c, nav_c_strat)
    m_f_hold = metrics_from_prices(common_f, nav_f_hold)
    m_f_strat = metrics_from_prices(common_f, nav_f_strat)

    # 分段
    early_end = "2024-12-31"
    bull_start = "2025-01-01"
    seg = {}
    for label, dates, closes in [
        ("early_hold", common_c, nav_c_hold),
        ("early_strat", common_c, nav_c_strat),
        ("bull_hold", common_c, nav_c_hold),
        ("bull_strat", common_c, nav_c_strat),
    ]:
        start = "2020-01-01" if label.startswith("early") else bull_start
        end = early_end if label.startswith("early") else LATEST
        m = slice_window(dates, closes, start, end)
        if m:
            seg[label] = m

    # 分年度
    yearly = {}
    for label, dates, closes in [
        ("hold", common_c, nav_c_hold),
        ("strat", common_c, nav_c_strat),
    ]:
        yd: dict[str, float] = {}
        ypx: dict[str, list[float]] = {}
        for d, c in zip(dates, closes):
            ypx.setdefault(d[:4], []).append(c)
        for y, px in ypx.items():
            if len(px) >= 2:
                yd[y] = px[-1] / px[0] - 1
        yearly[label] = yd

    load_font()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    ax = axes[0]
    ax.plot(common_c, nav_c_hold, label="C 持有（等权）", lw=1.5)
    ax.plot(common_c, nav_c_strat, label="C 双金叉策略", lw=2)
    ax.axvline("2025-01-01", color="gray", ls="--", lw=0.8)
    ax.text("2025-01-01", ax.get_ylim()[1] * 0.95 if False else 1.0, " 牛市分界", fontsize=9)
    ax.set_title("代理聚焦四只：持有 vs 双金叉策略（2020-11 ~ 2026-08，后复权）")
    ax.legend()
    ax.grid(alpha=0.3)

    ax2 = axes[1]
    ec = [d for d in common_c if d <= early_end]
    eh = [nav_c_hold[i] for i, d in enumerate(common_c) if d <= early_end]
    es = [nav_c_strat[i] for i, d in enumerate(common_c) if d <= early_end]
    ax2.plot(ec, eh, label="C 持有", lw=1.5)
    ax2.plot(ec, es, label="C 双金叉策略", lw=2)
    ax2.set_title("早期段（2020-11 ~ 2024-12，含 2021-2023 科技熊市）")
    ax2.legend()
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    chart = OUT_DIR / f"etf_strategy_early_{OUT_STAMP}.png"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    # ── 报告 ──
    lines = []
    w = lines.append
    w("# ETF 双金叉策略长历史回测（2020-11 ~ 2026-08）")
    w("")
    w(f"> 窗口：{common_c[0]} ~ {common_c[-1]}（{len(common_c)} 交易日），腾讯后复权日K")
    w("")
    w("## 一、为什么换标的")
    w("")
    w("- 半导体设备ETF(159516) 2023-07 上市、科创芯片ETF(588200) 2022-11 上市，聚焦四只无法回测 2023 年之前。")
    w("- 用上市早的代理标的映射：半导体设备→半导体ETF(512480)、科创芯片→科创50ETF(588000)、芯片→芯片ETF华夏(159995)、纳指→纳指ETF广发(159941)。")
    w("")
    w("## 二、全窗口（2020-11-16 ~ 2026-08-19）")
    w("")
    w("### C 代理聚焦四只")
    w("")
    w("| 指标 | 持有（等权） | 双金叉策略 | 差额 |")
    w("|------|------------|-----------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("年化收益", "annualized_return", fmt_pct),
        ("年化波动", "annualized_vol", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = m_c_hold[key], m_c_strat[key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {m_c_hold['sharpe']:.2f} | {m_c_strat['sharpe']:.2f} | {m_c_strat['sharpe'] - m_c_hold['sharpe']:+.2f} |")
    w("")
    w("### F 代理纯科技五只")
    w("")
    w("| 指标 | 持有（等权） | 双金叉策略 | 差额 |")
    w("|------|------------|-----------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("年化收益", "annualized_return", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = m_f_hold[key], m_f_strat[key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {m_f_hold['sharpe']:.2f} | {m_f_strat['sharpe']:.2f} | {m_f_strat['sharpe'] - m_f_hold['sharpe']:+.2f} |")
    w("")
    w("## 三、早期段（2020-11-16 ~ 2024-12-31，含 2021-2023 科技熊市）")
    w("")
    w("| 指标 | 持有 | 策略 | 差额 |")
    w("|------|------|------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("年化收益", "annualized_return", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = seg["early_hold"][key], seg["early_strat"][key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {seg['early_hold']['sharpe']:.2f} | {seg['early_strat']['sharpe']:.2f} | {seg['early_strat']['sharpe'] - seg['early_hold']['sharpe']:+.2f} |")
    w("")
    w("## 四、牛市段（2025-01-01 ~ 2026-08-19）")
    w("")
    w("| 指标 | 持有 | 策略 | 差额 |")
    w("|------|------|------|------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
    ]:
        a, b = seg["bull_hold"][key], seg["bull_strat"][key]
        w(f"| {label} | {fmt(a)} | {fmt(b)} | {fmt(b - a)} |")
    w(f"| 夏普(0rf) | {seg['bull_hold']['sharpe']:.2f} | {seg['bull_strat']['sharpe']:.2f} | {seg['bull_strat']['sharpe'] - seg['bull_hold']['sharpe']:+.2f} |")
    w("")
    w("## 五、分年度（C 代理聚焦四只）")
    w("")
    w("| 年度 | 持有 | 策略 | 差值 |")
    w("|------|------|------|------|")
    for y in sorted(set(yearly["hold"]) | set(yearly["strat"])):
        a, b = yearly["hold"].get(y, 0.0), yearly["strat"].get(y, 0.0)
        w(f"| {y} | {fmt_pct(a)} | {fmt_pct(b)} | {fmt_pct(b - a)} |")
    w("")
    w("## 六、单只策略明细（C 组合，全窗口）")
    w("")
    w("| ETF | 交易次数 | 在场天数占比 | 策略累计 | 持有累计 |")
    w("|-----|---------|------------|---------|---------|")
    for nm in PORTFOLIO_C:
        s = c_etf[nm]
        by_day = {r["day"]: r for r in frames[nm]}
        sub = [by_day[d] for d in common_c]
        hold_ret = sub[-1]["close"] / sub[0]["close"] - 1
        w(
            f"| {nm} | {s['trades']} | {s['in_days']/len(common_c)*100:.1f}% "
            f"| {fmt_pct(s['total'])} | {fmt_pct(hold_ret)} |"
        )
    w("")
    w("## 七、结论速览")
    w("")
    w(f"- 全窗口：C 策略累计 {fmt_pct(m_c_strat['total_return'])} vs 持有 {fmt_pct(m_c_hold['total_return'])}，"
      f"夏普 {m_c_strat['sharpe']:.2f} vs {m_c_hold['sharpe']:.2f}。")
    w(f"- 早期熊市段：C 策略累计 {fmt_pct(seg['early_strat']['total_return'])} vs 持有 {fmt_pct(seg['early_hold']['total_return'])}，"
      f"回撤 {fmt_pct(seg['early_strat']['max_drawdown'])} vs {fmt_pct(seg['early_hold']['max_drawdown'])}。")
    w(f"- 牛市段：C 策略累计 {fmt_pct(seg['bull_strat']['total_return'])} vs 持有 {fmt_pct(seg['bull_hold']['total_return'])}。")
    w("- 注：代理标的结论供参考，策略规则、费用、成交假设与上一版完全一致。")
    report_path = OUT_DIR / f"etf_strategy_early_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    summary = {
        "window": {"start": common_c[0], "end": common_c[-1], "days": len(common_c)},
        "portfolio_c_strat_full": {k: v for k, v in m_c_strat.items() if k not in ("dates", "closes")},
        "portfolio_c_hold_full": {k: v for k, v in m_c_hold.items() if k not in ("dates", "closes")},
        "portfolio_c_strat_early": {k: v for k, v in seg["early_strat"].items() if k not in ("dates", "closes")},
        "portfolio_c_hold_early": {k: v for k, v in seg["early_hold"].items() if k not in ("dates", "closes")},
        "yearly": yearly,
        "per_etf": {k: {kk: vv for kk, vv in v.items() if kk != "nav"} for k, v in c_etf.items()},
    }
    (OUT_DIR / f"etf_strategy_early_{OUT_STAMP}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    print("\n=== C 代理聚焦四只（2020-11 ~ 2026-08）===")
    print(f"{'指标':<10}{'持有':>12}{'策略':>12}")
    for label, key in [
        ("累计收益", "total_return"),
        ("年化收益", "annualized_return"),
        ("最大回撤", "max_drawdown"),
        ("夏普", "sharpe"),
    ]:
        a, b = m_c_hold[key], m_c_strat[key]
        if key == "sharpe":
            print(f"{label:<10}{a:>12.2f}{b:>12.2f}")
        else:
            print(f"{label:<10}{fmt_pct(a):>12}{fmt_pct(b):>12}")
    print("\n=== 早期段（2020-11 ~ 2024-12）===")
    for label, key in [("累计收益", "total_return"), ("最大回撤", "max_drawdown"), ("夏普", "sharpe")]:
        a, b = seg["early_hold"][key], seg["early_strat"][key]
        if key == "sharpe":
            print(f"{label:<10}{a:>12.2f}{b:>12.2f}")
        else:
            print(f"{label:<10}{fmt_pct(a):>12}{fmt_pct(b):>12}")
    print("\n=== 分年度（C）===")
    for y in sorted(set(yearly["hold"]) | set(yearly["strat"])):
        print(f"  {y}: 持有 {fmt_pct(yearly['hold'].get(y, 0.0))}  策略 {fmt_pct(yearly['strat'].get(y, 0.0))}")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
