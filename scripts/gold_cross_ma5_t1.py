#!/usr/bin/env python3
"""金叉状态 + 跌破 MA5 退出策略，T+1 开盘成交口径。

规则：
  买入：T 日收盘时 MACD 零轴上方金叉状态（DIF>0 且 DEA>0 且 DIF>DEA），T+1 开盘买入
  卖出：T 日收盘跌破 MA5，T+1 开盘卖出
  空仓：所有不满足金叉状态的时间
  佣金：0.03%/边
"""
import sqlite3
import statistics
import sys
sys.path.insert(0, '/Users/krystal/Projects/zhc_projects/zettaranc-skill/scripts')
from etf_bt.indicators import macd

DB = '/Users/krystal/Projects/zhc_projects/zettaranc-skill/data/stock_data.db'
FEE = 0.0003


def load_klines(ts_code, start="2024-01-01"):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, open, high, low, close, vol
        FROM daily_kline WHERE ts_code = ? AND trade_date >= ?
        ORDER BY trade_date
    """, (ts_code, start))
    rows = [{"day": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "vol": r[5]}
            for r in cur.fetchall()]
    conn.close()
    return rows


def run_strategy(rows):
    """T+1 开盘成交。"""
    closes = [r["close"] for r in rows]
    opens = [r["open"] for r in rows]
    dif, dea = macd(closes)
    ma5 = [sum(closes[max(0, i-4):i+1]) / min(i+1, 5) for i in range(len(closes))]
    
    cash = 1.0
    shares = 0.0
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    trades = []
    nav = []
    
    for i in range(len(rows)):
        # T 日收盘判断信号，T+1 开盘成交
        if i > 0:
            # 昨天收盘时的状态
            gold = dif[i-1] > 0 and dea[i-1] > 0 and dif[i-1] > dea[i-1]
            below_ma5 = closes[i-1] < ma5[i-1]
            
            if not in_pos and gold:
                # 今天开盘买入
                entry_price = opens[i]
                shares = cash / (opens[i] * (1 + FEE))
                cash = 0.0
                in_pos = True
                entry_idx = i
            elif in_pos and below_ma5:
                # 今天开盘卖出
                exit_price = opens[i]
                cash += shares * opens[i] * (1 - FEE)
                shares = 0.0
                in_pos = False
                ret = (exit_price / entry_price - 1) * 100
                trades.append({
                    "entry": rows[entry_idx]["day"],
                    "exit": rows[i]["day"],
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "days": i - entry_idx,
                    "ret_pct": ret,
                })
        
        nav.append(cash + shares * closes[i])
    
    # 最后未平仓
    if in_pos:
        ret = (closes[-1] / entry_price - 1) * 100
        trades.append({
            "entry": rows[entry_idx]["day"],
            "exit": rows[-1]["day"],
            "entry_price": entry_price,
            "exit_price": closes[-1],
            "days": len(rows) - 1 - entry_idx,
            "ret_pct": ret,
        })
    
    return nav, trades


def metrics(nav):
    total = nav[-1] / nav[0] - 1
    rets = [nav[i] / nav[i-1] - 1 for i in range(1, len(nav))]
    n = len(rets)
    ann = (1 + total) ** (252 / n) - 1 if n else 0
    peak = nav[0]
    mdd = 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mean_r = statistics.mean(rets) if rets else 0
    std_r = statistics.stdev(rets) if len(rets) > 1 else 1
    sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r else 0
    return dict(total_return=total, annualized_return=ann, max_drawdown=mdd,
                sharpe=sharpe, vol=std_r * (252 ** 0.5), n=n)


def pct(x):
    return f"{x*100:.1f}%"


def analyze_etf(ts_code, name):
    rows = load_klines(ts_code)
    if len(rows) < 100:
        return None
    nav, trades = run_strategy(rows)
    m = metrics(nav)
    # 持有基准
    hold_nav = [r["close"] / rows[0]["close"] for r in rows]
    m_hold = metrics(hold_nav)
    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    return {
        "name": name,
        "trades": trades,
        "nav": nav,
        "m": m,
        "m_hold": m_hold,
        "wins": len(wins),
        "losses": len(losses),
        "win_pct": len(wins) / len(trades) * 100 if trades else 0,
        "avg_ret": statistics.mean([t["ret_pct"] for t in trades]) if trades else 0,
        "avg_days": statistics.mean([t["days"] for t in trades]) if trades else 0,
    }


def print_result(r):
    print(f"  {r['name']:<12} "
          f"{pct(r['m']['total_return']):>9} "
          f"{pct(r['m_hold']['total_return']):>9} "
          f"{pct(r['m']['max_drawdown']):>9} "
          f"{pct(r['m_hold']['max_drawdown']):>9} "
          f"{r['m']['sharpe']:>6.2f} "
          f"{r['m_hold']['sharpe']:>6.2f} "
          f"{len(r['trades']):>5} "
          f"{r['win_pct']:>6.1f}% "
          f"{r['avg_ret']:>+7.1f}% "
          f"{r['avg_days']:>5.1f}天")


def main():
    etfs = [
        ('159516.SZ', '半导体设备'),
        ('588200.SH', '科创芯片'),
        ('159995.SZ', '芯片华夏'),
        ('512480.SH', '半导体'),
        ('515880.SH', '通信'),
        ('515050.SH', '5G'),
        ('159819.SZ', '人工智能'),
        ('562500.SH', '机器人'),
        ('516160.SH', '新能源'),
        ('515790.SH', '光伏'),
        ('159992.SZ', '创新药'),
        ('512010.SH', '医药'),
    ]
    
    print("=" * 120)
    print("金叉状态 + 跌破 MA5 退出（T+1 开盘成交）")
    print("=" * 120)
    print(f"{'ETF':<12} {'策略收益':>9} {'持有收益':>9} {'策略回撤':>9} {'持有回撤':>9} {'策略夏普':>7} {'持有夏普':>7} {'交易数':>5} {'胜率':>7} {'单笔均':>8} {'持有天':>7}")
    print("-" * 120)
    
    results = []
    for ts_code, name in etfs:
        r = analyze_etf(ts_code, name)
        if r:
            results.append(r)
            print_result(r)
    
    # 汇总
    print("-" * 120)
    tech_results = [r for r in results if r['name'] in ['半导体设备','科创芯片','芯片华夏','半导体','通信','5G','人工智能','机器人']]
    if tech_results:
        avg_tech = statistics.mean([r['m']['total_return'] for r in tech_results])
        avg_tech_hold = statistics.mean([r['m_hold']['total_return'] for r in tech_results])
        print(f"{'科技平均':<12} {pct(avg_tech):>9} {pct(avg_tech_hold):>9}")
    
    other_results = [r for r in results if r['name'] in ['新能源','光伏','创新药','医药']]
    if other_results:
        avg_other = statistics.mean([r['m']['total_return'] for r in other_results])
        avg_other_hold = statistics.mean([r['m_hold']['total_return'] for r in other_results])
        print(f"{'其他平均':<12} {pct(avg_other):>9} {pct(avg_other_hold):>9}")
    
    # 打印半导体设备的交易明细
    print()
    print("=" * 80)
    print("半导体设备交易明细")
    print("=" * 80)
    for r in results:
        if r['name'] == '半导体设备':
            for t in r['trades']:
                print(f"  {t['entry']} ~ {t['exit']}  {t['entry_price']:.3f} -> {t['exit_price']:.3f}  {t['ret_pct']:>+6.1f}%  {t['days']:>2}天")


if __name__ == "__main__":
    main()
