"""一键回测入口。

用法：
  python -m etf_bt.runner --pool tech --strategy v2
  python -m etf_bt.runner --pool focus4 --strategy v2_full --start 2023-01-01
  python -m etf_bt.runner --pool all32 --compare
  python -m etf_bt.runner --pool tech_db --strategy tech_b1 --start 2024-01-01   # 科技ETF专用

池：
  focus4    半导体设备+科创芯片+芯片+纳指（实盘聚焦四只）
  tech      科技成长 11 只
  tech_db   科技ETF池（stock_data.db, industry=科技, 配合 tech_b1 战法）
  defensive 防御/弱势 6 只
  consumer  消费白马 6 只
  all32     全 32 只

策略（--strategy）：
  baseline  纯双金叉（双死叉即清）
  v2        双金叉+破布林中轨才清（当前最优）
  v2_vol    双金叉+放量买入+破中轨卖出
  v2_cyb    双金叉+破中轨+创业板MA120总开关
  v2_full   双金叉+放量+破中轨+创业板MA120总开关
  tech_b1   科技双金叉战法B1（仅科技ETF池 tech_db 使用）
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etf_bt import data as dmod
from etf_bt.engine import Config, run_portfolio, hold_nav, metrics, fmt_pct
from etf_bt import filters as F


def pct(x):
    return f"{x * 100:.1f}%"


def print_result(label, nav, m, trades, dates, per_etf=None):
    wins = [t for t in trades if t.ret_pct > 0]
    win_pct = len(wins) / len(trades) * 100 if trades else 0
    avg_ret = sum(t.ret_pct for t in trades) / len(trades) if trades else 0
    avg_win = sum(t.ret_pct for t in wins) / len(wins) if wins else 0
    losses = [t for t in trades if t.ret_pct <= 0]
    avg_loss = sum(t.ret_pct for t in losses) / len(losses) if losses else 0
    avg_days = sum(t.hold_days for t in trades) / len(trades) if trades else 0
    fake = [t for t in trades if t.hold_days <= 5 and t.ret_pct < 0]
    fake_pct = len(fake) / len(trades) * 100 if trades else 0
    n_buys = sum(r.buys for r in per_etf.values()) if per_etf else 0
    n_exits = sum(r.exits for r in per_etf.values()) if per_etf else 0
    in_days_pct = sum(r.in_days for r in per_etf.values()) / (len(per_etf) * len(dates)) * 100 if per_etf else 0

    print(f"  {label:<30}"
          f"{m['total_return']*100:>+7.1f}% "
          f"{m['annualized_return']*100:>+6.1f}%/年 "
          f"{m['max_drawdown']*100:>+7.1f}% "
          f"夏普{m['sharpe']:>5.2f} "
          f"波动{m['vol']*100:>5.1f}% "
          f"{len(trades):>4}笔 "
          f"{win_pct:>5.1f}%胜 "
          f"均{avg_ret:>+6.1f}% "
          f"假{fake_pct:>4.1f}% "
          f"在场{in_days_pct:>4.1f}% "
          f"买{n_buys:>3} 清{n_exits:>3}")


def main():
    parser = argparse.ArgumentParser(description="ETF 双金叉策略回测")
    parser.add_argument("--pool", default="focus4", help="ETF 池: focus4/tech/defensive/consumer/all32")
    parser.add_argument("--strategy", default="v2", help="策略: baseline/v2/v2_vol/v2_cyb/v2_full")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--compare", action="store_true", help="跑全部预设策略对比")
    parser.add_argument("--hold", action="store_true", default=True, help="同时显示买入持有基准")
    args = parser.parse_args()

    # 加载数据
    if args.pool == "tech_db":
        etf_dict = dmod.load_etf_pool_db("科技", start=args.start or "2019-01-01", end=args.end)
    else:
        etf_dict = dmod.load_etf_pool(args.pool)
    if not etf_dict:
        print(f"池 {args.pool} 无数据")
        return
    dates = dmod.common_dates(etf_dict, args.start or "2019-01-01")
    if args.end:
        dates = [dd for dd in dates if dd <= args.end]
    print(f"池: {args.pool}  ({len(etf_dict)}只: {','.join(etf_dict.keys())})")
    print(f"窗口: {dates[0]} ~ {dates[-1]}  ({len(dates)}交易日)")
    print(f"{'策略':<32}{'总收益':>9} {'年化':>10} {'最大回撤':>9} {'夏普':>6} {'波动率':>7} {'交易':>5} {'胜率':>6} {'单笔':>8} {'假信号':>7} {'在场':>7} {'买':>4} {'清':>4}")
    print("-" * 120)

    if args.hold:
        nav_h = hold_nav(etf_dict, dates)
        mh = metrics(nav_h)
        print(f"  {'买入持有（基准）':<30}"
              f"{mh['total_return']*100:>+7.1f}% "
              f"{mh['annualized_return']*100:>+6.1f}%/年 "
              f"{mh['max_drawdown']*100:>+7.1f}% "
              f"夏普{mh['sharpe']:>5.2f} "
              f"波动{mh['vol']*100:>5.1f}% "
              f"{'':>4} "
              f"{'':>5} "
              f"{'':>7} "
              f"{'':>5} "
              f"{'100%':>7}")

    all_names = list(F.PRESETS.keys()) + list(F.POLICIES.keys())
    strategies_to_run = all_names if args.compare else [args.strategy]

    for sname in strategies_to_run:
        if sname in F.POLICIES:
            desc, factory = F.POLICIES[sname]
            cfg = Config(stateful=factory())
        else:
            desc, buy_name, sell_name, regime_name, use_tf = F.PRESETS[sname]
            buy_fn = F.BUY_FILTERS[buy_name][1] if buy_name != "none" else None
            sell_fn = F.SELL_FILTERS[sell_name][1] if sell_name != "none" else None
            regime = F.REGIME_SWITCHES[regime_name][1](dates) if regime_name else None
            cfg = Config(buy_filter=buy_fn, sell_filter=sell_fn, regime=regime, use_trend_filter=use_tf)
        result = run_portfolio(etf_dict, dates, cfg)
        print_result(f"{sname}: {desc[:20]}", result.nav, result.m, result.trades, dates, result.per_etf)

    print()


if __name__ == "__main__":
    main()
