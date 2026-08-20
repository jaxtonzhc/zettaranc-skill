#!/usr/bin/env python3
"""ETF 双金叉策略回测（走 SQLite 数据库）。

用法：
  python -m scripts.etf_bt_db --pool tech --strategy v2
  python -m scripts.etf_bt_db --pool focus4 --compare
"""
import sys
import argparse
import sqlite3
import statistics
from pathlib import Path
from dataclasses import dataclass

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
DB = ROOT / "data/stock_data.db"

sys.path.insert(0, str(ROOT / "scripts"))
from etf_bt.indicators import macd, kdj, rsi, boll, dmi, bbi, vol_ratio, crosses

FEE = 0.0003


# ---------- 数据层 ----------

def get_etf_codes(category=None):
    """从 stock_basic 获取 ETF 代码列表。"""
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    if category:
        cur.execute("SELECT ts_code, name FROM stock_basic WHERE industry = ?", (category,))
    else:
        cur.execute("SELECT ts_code, name FROM stock_basic WHERE industry != '' AND industry IS NOT NULL")
    rows = cur.fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def load_klines(ts_code, start="2019-01-01", end=None):
    """从 daily_kline 加载 K 线。"""
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    if end:
        cur.execute("""
            SELECT trade_date, open, high, low, close, vol
            FROM daily_kline WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """, (ts_code, start, end))
    else:
        cur.execute("""
            SELECT trade_date, open, high, low, close, vol
            FROM daily_kline WHERE ts_code = ? AND trade_date >= ?
            ORDER BY trade_date
        """, (ts_code, start))
    rows = [{"day": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "vol": r[5]}
            for r in cur.fetchall()]
    conn.close()
    return rows


def common_dates_for_codes(codes, start="2019-01-01"):
    """多只 ETF 的公共交易日。"""
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    placeholders = ",".join("?" * len(codes))
    cur.execute(f"""
        SELECT trade_date, COUNT(DISTINCT ts_code) as cnt
        FROM daily_kline WHERE ts_code IN ({placeholders}) AND trade_date >= ?
        GROUP BY trade_date HAVING cnt = ?
        ORDER BY trade_date
    """, (*codes, start, len(codes)))
    dates = [r[0] for r in cur.fetchall()]
    conn.close()
    return dates


# ---------- 指标预计算 ----------

def precompute(rows):
    opens = [r["open"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    vols = [r["vol"] for r in rows]
    n = len(rows)
    dif, dea = macd(closes)
    ks, ds = kdj(highs, lows, closes)
    rsi_v = rsi(closes)
    boll_lo, boll_mid, boll_up = boll(closes)
    pdi, mdi, adx = dmi(highs, lows, closes)
    bbi_v = bbi(closes)
    vr = vol_ratio(vols)
    macd_gold, macd_dead = crosses(dif, dea)
    kdj_gold, kdj_dead = crosses(ks, ds)
    return dict(opens=opens, highs=highs, lows=lows, closes=closes, vols=vols, n=n,
                dif=dif, dea=dea, ks=ks, ds=ds, rsi=rsi_v,
                boll_lo=boll_lo, boll_mid=boll_mid, boll_up=boll_up,
                pdi=pdi, mdi=mdi, adx=adx, bbi=bbi_v, vr=vr,
                macd_gold=macd_gold, macd_dead=macd_dead,
                kdj_gold=kdj_gold, kdj_dead=kdj_dead)


# ---------- 回测引擎 ----------

@dataclass
class Config:
    fee: float = FEE
    buy_window: int = 2
    dead_window: int = 2
    reduce_ratio: float = 0.5
    buy_filter: callable = None
    sell_filter: callable = None
    regime: list = None
    use_trend_filter: bool = True


def _win(arr, i, w=2):
    return any(arr[j] for j in range(max(0, i - w + 1), i + 1))


def run_strategy(pc, cfg=None):
    if cfg is None:
        cfg = Config()
    n = pc["n"]
    opens, closes = pc["opens"], pc["closes"]
    cash, shares = 1.0, 0.0
    nav = []
    trades = []
    buys = exits = in_days = 0
    in_pos = False
    entry_price = 0.0
    entry_idx = 0

    for i in range(n):
        if cfg.regime is not None and i < len(cfg.regime) and not cfg.regime[i]:
            if in_pos:
                cash += shares * opens[i] * (1 - cfg.fee)
                shares = 0.0
                exits += 1
                trades.append(dict(entry_idx=entry_idx, exit_idx=i,
                                   entry_price=entry_price, exit_price=opens[i],
                                   hold_days=i - entry_idx,
                                   ret_pct=(opens[i] / entry_price - 1) * 100))
                in_pos = False
            nav.append(cash + shares * closes[i])
            continue

        order = None
        if i > 0:
            sig_buy = _win(pc["macd_gold"], i - 1, cfg.buy_window) and _win(pc["kdj_gold"], i - 1, cfg.buy_window)
            if not in_pos:
                if sig_buy and (cfg.buy_filter is None or cfg.buy_filter(i, pc)):
                    order = "BUY"
            else:
                double_dead = _win(pc["macd_dead"], i - 1, cfg.dead_window) and _win(pc["kdj_dead"], i - 1, cfg.dead_window)
                single_dead = (pc["macd_dead"][i - 1] and not pc["kdj_dead"][i - 1]) or \
                              (pc["kdj_dead"][i - 1] and not pc["macd_dead"][i - 1])
                if cfg.use_trend_filter:
                    weak = (pc["pdi"][i - 1] >= pc["mdi"][i - 1]) and pc["adx"][i - 1] >= 25 and pc["rsi"][i - 1] >= 50
                else:
                    weak = True
                sell_ok = cfg.sell_filter is None or cfg.sell_filter(i, pc)
                if double_dead and not weak and sell_ok:
                    order = "EXIT"
                elif single_dead and not weak and sell_ok:
                    order = "REDUCE"
                elif sig_buy and (cfg.buy_filter is None or cfg.buy_filter(i, pc)):
                    order = "BUY_MORE"

        if order == "BUY" and not in_pos:
            shares = cash / (opens[i] * (1 + cfg.fee))
            cash = 0.0
            entry_price = opens[i]
            entry_idx = i
            in_pos = True
            buys += 1
        elif order == "BUY_MORE" and in_pos and cash > 0:
            add = cash / (opens[i] * (1 + cfg.fee))
            shares += add
            cash = 0.0
            buys += 1
        elif order == "REDUCE" and in_pos:
            s = shares * cfg.reduce_ratio
            cash += s * opens[i] * (1 - cfg.fee)
            shares -= s
        elif order == "EXIT" and in_pos:
            cash += shares * opens[i] * (1 - cfg.fee)
            shares = 0.0
            exits += 1
            trades.append(dict(entry_idx=entry_idx, exit_idx=i,
                               entry_price=entry_price, exit_price=opens[i],
                               hold_days=i - entry_idx,
                               ret_pct=(opens[i] / entry_price - 1) * 100))
            in_pos = False

        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])

    if in_pos:
        trades.append(dict(entry_idx=entry_idx, exit_idx=n - 1,
                           entry_price=entry_price, exit_price=closes[-1],
                           hold_days=n - 1 - entry_idx,
                           ret_pct=(closes[-1] / entry_price - 1) * 100))
    return dict(nav=nav, trades=trades, buys=buys, exits=exits, in_days=in_days,
                total_ret=nav[-1] - 1)


def metrics(nav):
    total = nav[-1] / nav[0] - 1
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
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


# ---------- 过滤器 ----------

BUY_FILTERS = {
    "none": ("无过滤", lambda i, pc: True),
    "vol_1.2": ("放量(量比>1.2)", lambda i, pc: pc["vr"][i - 1] > 1.2),
    "adx20": ("ADX>20", lambda i, pc: pc["adx"][i - 1] > 20),
    "mom20_pos": ("20日动量>0", lambda i, pc: (pc["closes"][i - 1] / pc["closes"][max(0, i - 20)] - 1) > 0 if pc["closes"][max(0, i - 20)] > 0 else False),
    "j_low": ("KDJ J<50", lambda i, pc: pc["ks"][i - 1] < 50),
    "ma60": ("收盘>MA60", lambda i, pc: pc["closes"][i - 1] > sum(pc["closes"][max(0, i - 59):i + 1]) / min(i + 1, 60)),
    "boll_mid": ("收盘>布林中轨", lambda i, pc: pc["closes"][i - 1] > pc["boll_mid"][i - 1]),
}

SELL_FILTERS = {
    "none": ("双死叉即清", lambda i, pc: True),
    "boll_mid_break": ("破布林中轨才清", lambda i, pc: pc["closes"][i - 1] < pc["boll_mid"][i - 1]),
    "bbi_break": ("破BBI才清", lambda i, pc: pc["closes"][i - 1] < pc["bbi"][i - 1]),
    "adx_down": ("ADX下降才清", lambda i, pc: pc["adx"][i - 1] < pc["adx"][i - 2] if i >= 2 else True),
}

PRESETS = {
    "baseline": ("双金叉原版", "none", "none", None, True),
    "v2": ("双金叉+破中轨卖", "none", "boll_mid_break", None, True),
    "v2_vol": ("双金叉+放量买+破中轨卖", "vol_1.2", "boll_mid_break", None, True),
    "v2_adx": ("双金叉+ADX20买+破中轨卖", "adx20", "boll_mid_break", None, True),
}


# ---------- 组合回测 ----------

def run_portfolio(codes, dates_map, cfg):
    per_etf = {}
    all_trades = []
    for ts_code, name in codes:
        dates = dates_map.get(ts_code, [])
        if len(dates) < 50:
            continue
        rows = load_klines(ts_code, dates[0], dates[-1])
        pc = precompute(rows)
        r = run_strategy(pc, cfg)
        per_etf[ts_code] = r
        all_trades.extend(r["trades"])
    if not per_etf:
        return None
    # 等权组合：在每只 ETF 的日期上对齐
    min_len = min(len(r["nav"]) for r in per_etf.values())
    port = [sum(per_etf[tc]["nav"][i] for tc in per_etf) / len(per_etf) for i in range(min_len)]
    m = metrics(port)
    return dict(nav=port, per_etf=per_etf, m=m, trades=all_trades)


def hold_nav(codes, dates_map):
    navs = []
    for ts_code, name in codes:
        dates = dates_map.get(ts_code, [])
        if not dates:
            continue
        rows = load_klines(ts_code, dates[0], dates[-1])
        nav = [r["close"] / rows[0]["close"] for r in rows]
        if nav:
            navs.append(nav)
    if not navs:
        return []
    min_len = min(len(nv) for nv in navs)
    return [sum(nv[i] for nv in navs) / len(navs) for i in range(min_len)]


# ---------- 报告 ----------

def pct(x):
    return f"{x * 100:.1f}%"


def print_result(label, m, trades, per_etf, dates_map):
    wins = [t for t in trades if t["ret_pct"] > 0]
    win_pct = len(wins) / len(trades) * 100 if trades else 0
    avg_ret = sum(t["ret_pct"] for t in trades) / len(trades) if trades else 0
    avg_win = sum(t["ret_pct"] for t in wins) / len(wins) if wins else 0
    losses = [t for t in trades if t["ret_pct"] <= 0]
    avg_loss = sum(t["ret_pct"] for t in losses) / len(losses) if losses else 0
    avg_days = sum(t["hold_days"] for t in trades) / len(trades) if trades else 0
    fake = [t for t in trades if t["hold_days"] <= 5 and t["ret_pct"] < 0]
    fake_pct = len(fake) / len(trades) * 100 if trades else 0
    n_buys = sum(r["buys"] for r in per_etf.values()) if per_etf else 0
    n_exits = sum(r["exits"] for r in per_etf.values()) if per_etf else 0
    in_days_pct = sum(r["in_days"] for r in per_etf.values()) / sum(len(dates_map.get(tc, [])) for tc in per_etf) * 100 if per_etf else 0

    print(f"  {label:<32}"
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


POOLS = {
    "focus4": ["半导体设备", "科创芯片", "芯片华夏", "纳指"],
    "tech": ["半导体", "半导体设备", "芯片华夏", "芯片国泰", "科创50", "科创芯片",
             "通信", "人工智能", "5G", "机器人", "计算机ETF"],
    "defensive": ["黄金", "煤炭", "红利", "红利低波", "银行ETF", "证券"],
    "consumer": ["消费", "酒", "医药", "创新药", "军工", "有色金属"],
    "all": None,
}


def main():
    parser = argparse.ArgumentParser(description="ETF 双金叉回测（DB 版）")
    parser.add_argument("--pool", default="focus4", help="ETF 池")
    parser.add_argument("--strategy", default="v2", help="策略")
    parser.add_argument("--start", default="2019-01-01", help="起始日期")
    parser.add_argument("--end", default=None, help="结束日期")
    parser.add_argument("--compare", action="store_true", help="对比全部预设策略")
    args = parser.parse_args()

    # 获取 ETF 列表
    if args.pool in POOLS and POOLS[args.pool]:
        names = POOLS[args.pool]
        conn = sqlite3.connect(str(DB))
        cur = conn.cursor()
        codes = []
        for name in names:
            cur.execute("SELECT ts_code, name FROM stock_basic WHERE name = ?", (name,))
            row = cur.fetchone()
            if row:
                codes.append((row[0], row[1]))
        conn.close()
    elif args.pool == "all":
        codes = get_etf_codes()
    else:
        codes = get_etf_codes(args.pool)

    if not codes:
        print(f"池 {args.pool} 无 ETF")
        return

    print(f"池: {args.pool}  ({len(codes)}只: {','.join(n for _, n in codes)})")
    # 每只 ETF 用自己的日期范围，不需要公共日
    dates_map = {}
    all_dates = set()
    for ts_code, name in codes:
        rows = load_klines(ts_code, args.start, args.end)
        dates_map[ts_code] = [r["day"] for r in rows]
        all_dates.update(dates_map[ts_code])
    dates = sorted(all_dates)
    print(f"窗口: {dates[0]} ~ {dates[-1]}  ({len(dates)}交易日)")
    print(f"{'策略':<34}{'总收益':>9} {'年化':>10} {'最大回撤':>9} {'夏普':>6} {'波动率':>7} {'交易':>5} {'胜率':>6} {'单笔':>8} {'假信号':>7} {'在场':>7} {'买':>4} {'清':>4}")
    print("-" * 130)

    # 持有基准
    nav_h = hold_nav(codes, dates_map)
    if nav_h:
        mh = metrics(nav_h)
        print(f"  {'买入持有（基准）':<32}"
              f"{mh['total_return']*100:>+7.1f}% "
              f"{mh['annualized_return']*100:>+6.1f}%/年 "
              f"{mh['max_drawdown']*100:>+7.1f}% "
              f"夏普{mh['sharpe']:>5.2f} "
              f"波动{mh['vol']*100:>5.1f}% "
              f"{'':>4} {'':>5} {'':>7} {'':>5} {'100%':>7}")

    strategies = list(PRESETS.keys()) if args.compare else [args.strategy]
    for sname in strategies:
        desc, buy_name, sell_name, regime_name, use_tf = PRESETS[sname]
        buy_fn = BUY_FILTERS[buy_name][1] if buy_name != "none" else None
        sell_fn = SELL_FILTERS[sell_name][1] if sell_name != "none" else None
        cfg = Config(buy_filter=buy_fn, sell_filter=sell_fn, use_trend_filter=use_tf)
        result = run_portfolio(codes, dates_map, cfg)
        if result:
            print_result(f"{sname}: {desc[:20]}", result["m"], result["trades"], result["per_etf"], dates_map)

    print()


if __name__ == "__main__":
    main()
