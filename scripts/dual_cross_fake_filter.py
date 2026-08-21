#!/usr/bin/env python3
"""假金叉过滤：在双金叉买入之前叠加各种条件，看哪个能有效降低"金叉后马上打脸"。

核心问题：双金叉经常产生虚假信号，买进去两三天就死叉亏钱。如何过滤？

评价指标（不是总收益，而是信号质量）：
  - 胜率：买入后整个持仓期是赚钱的还是亏钱的
  - 平均持有天数：假信号往往很快就死叉退出
  - 平均单次收益：过滤后每次交易赚多少
  - 交易次数：过滤越严，交易越少（机会成本）
  - 总收益/最大回撤/夏普：整体表现

候选过滤条件：
  1. 价格位置：收盘价 > MA60（中线趋势已经向上）
  2. 成交量：金叉当日量比 > 1.2（放量确认）
  3. 价格动量：过去 20 日涨幅 > 0（不是从急跌中反弹）
  4. 金叉位置：MACD 柱体 > 0（DIF 已经在 DEA 上方）= 已有动能
  5. 低 J 值金叉：KDJ 的 J 值在金叉时 < 50（低位金叉，有上涨空间）
  6. ADX 趋势：ADX > 20（有一定趋势力度，不是完全震荡）
  7. 价格站上 BBI：多头排列确认
  8. 近期没有假金叉：过去 10 天内没有金叉过（避免反复金叉）
"""
import json
import statistics
from pathlib import Path

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import dual_cross_addons as M

d = json.load(open(ROOT / "data/momentum_etf_hfq.json"))["data"]

# 科技成长池（双金叉主战场）
TECH = ['创业板', '科创50', '机器人', '半导体', '芯片华夏', '通信',
        '人工智能', '科创芯片', '5G', '纳指', '恒生科技']
idx = {nm: {r["day"]: r for r in d[nm]} for nm in TECH}
common = sorted(set.intersection(*[set(idx[nm].keys()) for nm in TECH]))
common = [dd for dd in common if dd >= "2019-06-01"]


def evaluate_filter(basket_names, buy_filter=None, label=""):
    """跑一组 ETF，返回：交易列表 + 组合净值"""
    all_trades = []
    navs = []
    for nm in basket_names:
        rows = [idx[nm][dd] for dd in common]
        pc = M.precompute(rows)
        n = pc["n"]
        opens, closes = pc["opens"], pc["closes"]

        def win(arr, i, w=2):
            return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

        # 记录每笔交易
        in_pos = False
        entry_price = 0
        entry_idx = 0
        cash, shares = 1.0, 0.0
        nav = []
        trades = []

        for i in range(n):
            order = None
            if i > 0 and not in_pos:
                sig_buy = win(pc["macd_gold"], i - 1, 2) and win(pc["kdj_gold"], i - 1, 2)
                if sig_buy:
                    # 应用过滤器
                    if buy_filter is None or buy_filter(i, pc):
                        order = "BUY"
            elif i > 0 and in_pos:
                double_dead = win(pc["macd_dead"], i - 1, 2) and win(pc["kdj_dead"], i - 1, 2)
                single_dead = (pc["macd_dead"][i - 1] and not pc["kdj_dead"][i - 1]) or \
                              (pc["kdj_dead"][i - 1] and not pc["macd_dead"][i - 1])
                weak = (pc["pdi"][i - 1] >= pc["mdi"][i - 1]) and pc["adx"][i - 1] >= 25 and pc["rsi"][i - 1] >= 50
                if double_dead and not weak:
                    order = "EXIT"
                elif single_dead and not weak:
                    order = "REDUCE"
                elif win(pc["macd_gold"], i - 1, 2) and win(pc["kdj_gold"], i - 1, 2):
                    order = "BUY_MORE"

            if order == "BUY" and not in_pos:
                shares = cash / (opens[i] * 1.0003)
                cash = 0.0
                entry_price = opens[i]
                entry_idx = i
                in_pos = True
            elif order == "BUY_MORE" and in_pos and cash > 0:
                add = cash / (opens[i] * 1.0003)
                shares += add
                cash = 0.0
            elif order == "REDUCE" and in_pos:
                s = shares * 0.5
                cash += s * opens[i] * 0.9997
                shares -= s
            elif order == "EXIT" and in_pos:
                exit_price = opens[i]
                ret = (exit_price / entry_price - 1) * 100
                hold_days = i - entry_idx
                trades.append({"entry": entry_price, "exit": exit_price, "ret": ret, "days": hold_days})
                cash += shares * opens[i] * 0.9997
                shares = 0.0
                in_pos = False

            nav.append(cash + shares * closes[i])

        if in_pos:
            exit_price = closes[-1]
            ret = (exit_price / entry_price - 1) * 100
            hold_days = n - 1 - entry_idx
            trades.append({"entry": entry_price, "exit": exit_price, "ret": ret, "days": hold_days})

        all_trades.extend(trades)
        navs.append(nav)

    port = [sum(navs[j][i] for j in range(len(basket_names))) / len(basket_names) for i in range(len(common))]
    return all_trades, port


def report(trades, nav, label):
    if not trades:
        print(f"  {label:<30}{'无交易':>10}")
        return
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    win_pct = len(wins) / len(trades) * 100
    avg_ret = statistics.mean([t["ret"] for t in trades])
    avg_win = statistics.mean([t["ret"] for t in wins]) if wins else 0
    avg_loss = statistics.mean([t["ret"] for t in losses]) if losses else 0
    avg_days = statistics.mean([t["days"] for t in trades])
    # 假信号定义：持有 ≤5 天且亏钱
    fake = [t for t in trades if t["days"] <= 5 and t["ret"] < 0]
    fake_pct = len(fake) / len(trades) * 100
    # 总收益
    total_ret = nav[-1] - 1
    peak = max(nav)
    mdd = min(v / peak - 1 for v in nav)
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    mean_r = statistics.mean(rets)
    std_r = statistics.stdev(rets) if len(rets) > 1 else 1
    sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r else 0

    print(f"  {label:<28}"
          f"{len(trades):>5}笔 {win_pct:>5.1f}%胜 "
          f"均{avg_ret:>+6.1f}% 盈{avg_win:>+6.1f}% 亏{avg_loss:>+6.1f}% "
          f"均{avg_days:>4.1f}天 "
          f"假信号{fake_pct:>4.1f}% "
          f"总{total_ret*100:>+7.1f}% 回撤{mdd*100:>+6.1f}% "
          f"夏普{sharpe:>5.2f}")


def ma(vals, n):
    out = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        out[i] = sum(vals[i - n + 1:i + 1]) / n
    return out


def main():
    print("=" * 100)
    print("假金叉过滤实验：科技成长池（11只 ETF 等权，2019-06~2026-08）")
    print("=" * 100)
    print(f"{'过滤条件':<30}{'交易数/胜率':>14}{'收益统计':>42}{'持有天数':>10}{'假信号%':>10}{'整体表现':>30}")
    print("-" * 100)

    filters = {
        "无过滤（基准）": None,
        "① 价格>MA60": lambda i, pc: pc["closes"][i - 1] > pc["closes"][max(0, i - 59):i],
        "② 放量(量比>1.2)": lambda i, pc: pc["vr"][i - 1] > 1.2,
        "③ 20日动量>0": lambda i, pc: (pc["closes"][i - 1] / pc["closes"][max(0, i - 20)] - 1) > 0 if pc["closes"][max(0, i - 20)] > 0 else False,
        "④ MACD柱>0": lambda i, pc: pc["dif"][i - 1] > pc["dea"][i - 1],
        "⑤ J值<50低位金叉": lambda i, pc: pc["ks"][i - 1] < 50,
        "⑥ ADX>20有趋势": lambda i, pc: pc["adx"][i - 1] > 20,
        "⑦ 收盘>BBI": lambda i, pc: pc["closes"][i - 1] > pc["bbi"][i - 1],
        "⑧ 10日内无金叉": lambda i, pc: not any(pc["macd_gold"][max(0, i - 10):i - 1]) if i > 10 else True,
        # 组合过滤器
        "组合A:价格>MA60+放量": lambda i, pc: pc["closes"][i - 1] > pc["closes"][max(0, i - 59):i] and pc["vr"][i - 1] > 1.2,
        "组合B:价格>MA60+ADX>20": lambda i, pc: pc["closes"][i - 1] > pc["closes"][max(0, i - 59):i] and pc["adx"][i - 1] > 20,
        "组合C:20日动量+MACD柱>0": lambda i, pc: (pc["closes"][i - 1] / pc["closes"][max(0, i - 20)] - 1) > 0 and pc["dif"][i - 1] > pc["dea"][i - 1],
        "组合D:价格>MA60+放量+ADX>20": lambda i, pc: pc["closes"][i - 1] > pc["closes"][max(0, i - 59):i] and pc["vr"][i - 1] > 1.2 and pc["adx"][i - 1] > 20,
        "组合E:全部5项": lambda i, pc: (
            pc["closes"][i - 1] > pc["closes"][max(0, i - 59):i]  # 价格>MA60
            and pc["vr"][i - 1] > 1.2   # 放量
            and (pc["closes"][i - 1] / pc["closes"][max(0, i - 20)] - 1) > 0  # 20日动量
            and pc["adx"][i - 1] > 20  # ADX>20
            and pc["dif"][i - 1] > pc["dea"][i - 1]  # MACD柱>0
        ),
    }

    for name, filt in filters.items():
        # MA60 filter needs special handling (list vs bool)
        if name == "① 价格>MA60":
            def make_ma60_f(f):
                def wrapped(i, pc):
                    ma60 = sum(pc["closes"][max(0, i - 59):i + 1]) / min(i + 1, 60)
                    return pc["closes"][i - 1] > ma60
                return wrapped
            filt = make_ma60_f(filt)
        elif name.startswith("组合A") or name.startswith("组合B") or name.startswith("组合D") or name.startswith("组合E"):
            # rebuild these with proper MA60
            def rebuild_combo(name):
                def wrapped(i, pc):
                    ma60 = sum(pc["closes"][max(0, i - 59):i + 1]) / min(i + 1, 60)
                    cond_price = pc["closes"][i - 1] > ma60
                    cond_vol = pc["vr"][i - 1] > 1.2
                    cond_adx = pc["adx"][i - 1] > 20
                    cond_macd = pc["dif"][i - 1] > pc["dea"][i - 1]
                    cond_mom20 = (pc["closes"][i - 1] / pc["closes"][max(0, i - 20)] - 1) > 0 if pc["closes"][max(0, i - 20)] > 0 else False
                    if name == "组合A:价格>MA60+放量":
                        return cond_price and cond_vol
                    elif name == "组合B:价格>MA60+ADX>20":
                        return cond_price and cond_adx
                    elif name == "组合C:20日动量+MACD柱>0":
                        return cond_mom20 and cond_macd
                    elif name == "组合D:价格>MA60+放量+ADX>20":
                        return cond_price and cond_vol and cond_adx
                    elif name == "组合E:全部5项":
                        return cond_price and cond_vol and cond_mom20 and cond_adx and cond_macd
                    return True
                return wrapped
            filt = rebuild_combo(name)

        trades, nav = evaluate_filter(TECH, buy_filter=filt, label=name)
        report(trades, nav, name)


if __name__ == "__main__":
    main()
