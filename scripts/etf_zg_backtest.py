#!/usr/bin/env python3
"""Z 哥战法（B1/B2/SB1/长安 + 少妇六步闭环离场）在 ETF 等权组合上的回测。

用项目自带的组合回测引擎（modules.backtest.portfolio）：
- 入场：扫描 B1 / B2 / SB1 / 长安 信号（J≤-10 等 Z 哥标准）
- 离场：少妇战法六步闭环（3% 止损 / BBI 连续跌破 / 卤煮减半 / 最少持仓 3 天）
- 数据：腾讯前复权 ETF 日K（含成交量），T 日收盘出信号、收盘价成交（引擎标准）

对比对象：实盘聚焦四只（2023-07~2026-08，qfq）与长历史代理 C（2020-11~2026-08，hfq）。
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, ".")
from modules.backtest.portfolio import PortfolioBacktestEngine, PortfolioConfig
from modules.datasource import dict_to_daily
from modules.loop_engine import LoopConfig

from etf_portfolio_backtest import fmt_pct, load_font, metrics_from_prices
from etf_strategy_backtest import ETF_POOL, PORTFOLIO_A, load_klines as load_qfq
from etf_strategy_early import PORTFOLIO_C, load_klines as load_hfq
from etf_strategy_v2 import run_strategy

OUT_DIR = Path("reports")
OUT_STAMP = "20260819"


def build_daily(frames: dict, names: list[str]) -> tuple[dict[str, list], list[str]]:
    """从缓存构造 {name: DailyData[]} 与公共日期。"""
    common = None
    for nm in names:
        dates = set(r["day"] for r in frames[nm])
        common = dates if common is None else common & dates
    common = sorted(common)
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
        klines_map[nm] = dict_to_daily(rows)
    return klines_map, common


def run_zg(klines_map, common, strategies, adaptive=False, loop=None):
    cfg = PortfolioConfig(
        initial_capital=1_000_000.0,
        max_positions=4,
        position_pct=0.25,
        min_cash_pct=0.0,
        max_entries_per_day=2,
        min_signal_days=30,
        enabled_strategies=strategies,
    )
    cfg.adaptive.enabled = adaptive
    engine = PortfolioBacktestEngine(portfolio_config=cfg, loop_config=loop or LoopConfig())
    result = engine.run_with_data(klines_map, common)
    return result


def hold_nav(frames, names, common):
    by_day = {nm: {r["day"]: r["close"] for r in frames[nm]} for nm in names}
    nav = []
    for d in common:
        nav.append(sum(by_day[nm][d] / by_day[nm][common[0]] for nm in names) / len(names))
    return nav


def v2_nav(frames, names, common):
    navs = []
    for nm in names:
        by_day = {r["day"]: r for r in frames[nm]}
        rows = [by_day[d] for d in common]
        nav, *_ = run_strategy(rows, "graduated", True)
        navs.append(nav)
    return [sum(n[i] for n in navs) / len(navs) for i in range(len(common))]


def summarize(result, label):
    return {
        "label": label,
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "max_drawdown": result.max_drawdown,
        "sharpe": result.sharpe_ratio,
        "win_rate": result.win_rate,
        "trades": result.total_trades,
        "dates": result.dates,
        "net_values": result.net_values,
    }


def main() -> int:
    qfq = load_qfq()
    hfq = load_hfq()

    load_font()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    lines = []
    w = lines.append
    w("# Z 哥战法在 ETF 等权组合上的回测")
    w("")

    runs = []
    # ── 实盘聚焦四只（qfq，2023-07~2026-08）──
    km_a, common_a = build_daily(qfq, PORTFOLIO_A)
    hold_a = hold_nav(qfq, PORTFOLIO_A, common_a)
    v2a = v2_nav(qfq, PORTFOLIO_A, common_a)
    r_b1_a = run_zg(km_a, common_a, ["B1"])
    r_b1_a.label = "B1"
    r_multi_a = run_zg(km_a, common_a, ["B1", "B2", "SB1", "长安"])
    r_multi_a.label = "B1+B2+SB1+长安"
    r_trail_a = run_zg(
        km_a,
        common_a,
        ["B1", "B2", "SB1", "长安"],
        loop=LoopConfig(trailing_stop_enabled=True, trailing_stop_pct=-0.08),
    )
    r_trail_a.label = "multi+移动止损8%"
    runs.append(("A 实盘聚焦四只（2023-07~2026-08）", common_a, hold_a, v2a, r_b1_a, r_multi_a, r_trail_a))

    # ── 长历史代理 C（hfq，2020-11~2026-08）──
    km_c, common_c = build_daily(hfq, PORTFOLIO_C)
    hold_c = hold_nav(hfq, PORTFOLIO_C, common_c)
    v2c = v2_nav(hfq, PORTFOLIO_C, common_c)
    r_b1_c = run_zg(km_c, common_c, ["B1"])
    r_b1_c.label = "B1"
    r_multi_c = run_zg(km_c, common_c, ["B1", "B2", "SB1", "长安"])
    r_multi_c.label = "B1+B2+SB1+长安"
    runs.append(("C 长历史代理（2020-11~2026-08）", common_c, hold_c, v2c, r_b1_c, r_multi_c, None))

    for label, common, hold_nv, v2_nv, r_b1, r_multi, r_trail in runs:
        m_hold = metrics_from_prices(common, hold_nv)
        m_v2 = metrics_from_prices(common, v2_nv)
        w(f"## {label}")
        w("")
        w("| 方案 | 累计 | 年化 | 最大回撤 | 夏普 | 交易数 | 胜率 |")
        w("|------|------|------|---------|------|--------|------|")
        w(f"| 持有（等权） | {fmt_pct(m_hold['total_return'])} | {fmt_pct(m_hold['annualized_return'])} | {fmt_pct(m_hold['max_drawdown'])} | {m_hold['sharpe']:.2f} | - | - |")
        w(f"| 用户双金叉 V2 | {fmt_pct(m_v2['total_return'])} | {fmt_pct(m_v2['annualized_return'])} | {fmt_pct(m_v2['max_drawdown'])} | {m_v2['sharpe']:.2f} | - | - |")
        for r in [r_b1, r_multi, r_trail]:
            if r is None:
                continue
            s = summarize(r, r.label)
            w(
                f"| Z哥 {s['label']} | {fmt_pct(s['total_return'])} | {fmt_pct(s['annualized_return'])} "
                f"| {fmt_pct(s['max_drawdown'])} | {s['sharpe']:.2f} | {s['trades']} | {s['win_rate']*100:.0f}% |"
            )
        w("")
        # 画图：Z哥净值曲线（B1 + multi）
        ax = axes[0] if label.startswith("A") else axes[1]
        ax.plot(common, hold_nv, label="持有", color="black", lw=1.5)
        ax.plot(common, v2_nv, label="用户V2", color="#2ca02c", lw=1.8)
        for r, c, lab in [
            (r_b1, "#d62728", "Z哥 B1"),
            (r_multi, "#1f77b4", "Z哥 B1+B2+SB1+长安"),
            (r_trail, "#9467bd", "Z哥 multi+移动止损"),
        ]:
            if r is None:
                continue
            if len(r.dates) == len(common):
                ax.plot(r.dates, r.net_values, label=lab, lw=1.5, color=c)
            else:
                ax.plot(r.dates, r.net_values, label=f"{lab}({len(r.dates)}d)", lw=1.5, color=c)
        ax.set_title(label)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

    w("## 说明")
    w("")
    w("- Z 哥入场信号按原版规则：B1 需 J≤-10（ETF 波动小，信号稀少）；SB1/长安更严格。")
    w("- 离场为少妇战法六步闭环：3% 止损（entry_low）、BBI 连续 2 天跌破、卤煮减半、最少持仓 3 天。")
    w("- 移动止损变体：高点回落 8% 止损（v3.10.1）。")
    w("- 引擎按 T 日收盘出信号、收盘价成交（略乐观）；佣金 0.025%+印花税 0.05%。")
    w("- 持仓上限 4 只、单票 25% 仓位，与等权组合对齐。")
    fig.tight_layout()
    chart = OUT_DIR / f"etf_zg_{OUT_STAMP}.png"
    fig.savefig(chart, dpi=140)
    plt.close(fig)

    report_path = OUT_DIR / f"etf_zg_{OUT_STAMP}.md"
    report_path.write_text("\n".join(lines))

    print("\n=== Z 哥战法 ETF 回测 ===")
    for label, common, hold_nv, v2_nv, r_b1, r_multi, r_trail in runs:
        m_hold = metrics_from_prices(common, hold_nv)
        m_v2 = metrics_from_prices(common, v2_nv)
        print(f"\n{label}")
        print(f"  持有: {fmt_pct(m_hold['total_return'])}  V2: {fmt_pct(m_v2['total_return'])}")
        for r in [r_b1, r_multi, r_trail]:
            if r is None:
                continue
            print(
                f"  [{r.label}] "
                f"累计 {fmt_pct(r.total_return)} 回撤 {fmt_pct(r.max_drawdown)} 夏普 {r.sharpe_ratio:.2f} "
                f"交易 {r.total_trades} 胜率 {r.win_rate*100:.0f}%"
            )
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
