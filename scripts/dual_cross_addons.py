#!/usr/bin/env python3
"""在双金叉主策略之上，逐一叠加候选指标/规则，看哪些真正改善波动与回撤。

回测口径（与历史脚本一致、避免未来函数）：
  - 信号在 T 日收盘产生，T+1 开盘成交
  - 佣金 0.03%/边，空仓资金收益 0
  - 主策略：2 日内 MACD(6,13,5) 金叉 + KDJ(9,3,3) 金叉 => 满仓；
            2 日内双死叉且趋势转弱 => 清仓；单死叉且转弱 => 减半仓
  - 基准：等权买入持有

指标池（全部基于价格/成交量派生，属于"同源"指标）：
  MACD, KDJ, RSI(12), BOLL(20), DMI/ADX(14,6), BBI(3,6,12,24),
  SKDJ(9,3), VOL 量比/放量, 创业板指 MA120 大盘总开关
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
FEE = 0.0003
OUT_DIR = ROOT / "reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = "20260820"


def load_qfq():
    return json.load(open(ROOT / "data/etf_kline_cache_ohlc.json"))["data"]


def load_cyb():
    return {k: v for k, v in json.load(open(ROOT / "data/cyb_index.json"))["data"].items()}


# ---------- 指标 ----------
def ema(vals, n):
    k = 2 / (n + 1)
    out, prev = [], None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes, f=6, s=13, m=5):
    dif = [a - b for a, b in zip(ema(closes, f), ema(closes, s))]
    return dif, ema(dif, m)


def kdj(highs, lows, closes, n=9, kp=3, dp=3):
    k, d = 50.0, 50.0
    ks, ds = [], []
    for i in range(len(closes)):
        lo = min(lows[max(0, i - n + 1):i + 1])
        hi = max(highs[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        ks.append(k)
        ds.append(d)
    return ks, ds


def rsi(closes, period=12):
    n = len(closes)
    out = [50.0] * n
    if n <= period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, n)]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + al)
    return out


def boll(closes, n=20, k=2):
    mid = [sum(closes[max(0, i - n + 1):i + 1]) / min(i + 1, n) for i in range(len(closes))]
    up = [mid[i] + k * (sum((c - mid[i]) ** 2 for c in closes[max(0, i - n + 1):i + 1]) / min(i + 1, n)) ** 0.5 for i in range(len(closes))]
    lo = [mid[i] - k * (sum((c - mid[i]) ** 2 for c in closes[max(0, i - n + 1):i + 1]) / min(i + 1, n)) ** 0.5 for i in range(len(closes))]
    return lo, mid, up


def dmi(highs, lows, closes, di_p=14, adx_p=6):
    n = len(closes)
    pdm = [0.0] * n
    mdm = [0.0] * n
    tr = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm[i] = up if (up > down and up > 0) else 0.0
        mdm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    def wil(v, p, start=1):
        out = [0.0] * n
        s = sum(v[start:start + p])
        if start + p < n:
            out[start + p] = s
        for i in range(start + p + 1, n):
            s = s - s / p + v[i]
            out[i] = s
        return out

    trs = wil(tr, di_p)
    pdms = wil(pdm, di_p)
    mdms = wil(mdm, di_p)
    pdi = [100 * pdms[i] / trs[i] if trs[i] > 0 else 50.0 for i in range(n)]
    mdi = [100 * mdms[i] / trs[i] if trs[i] > 0 else 50.0 for i in range(n)]
    dx = [100 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) if pdi[i] + mdi[i] > 0 else 0.0 for i in range(n)]
    adx = [0.0] * n
    s = sum(dx[di_p + 1:di_p + 1 + adx_p])
    if di_p + 1 + adx_p < n:
        adx[di_p + 1 + adx_p] = s / adx_p
    for i in range(di_p + 1 + adx_p + 1, n):
        s = s - s / adx_p + dx[i]
        adx[i] = s / adx_p
    return pdi, mdi, adx


def bbi(closes, ps=(3, 6, 12, 24)):
    mas = [sum(closes[max(0, i - p + 1):i + 1]) / min(i + 1, p) for p in ps for i in range(len(closes))]
    out = [sum(mas[j::len(ps)][i] for j in range(len(ps))) / len(ps) for i in range(len(closes))]
    return out


def skdj(highs, lows, closes, n=9, m=3):
    rsvs = []
    for i in range(len(closes)):
        lo = min(lows[max(0, i - n + 1):i + 1])
        hi = max(highs[max(0, i - n + 1):i + 1])
        rsvs.append(50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100)
    k = ema(rsvs, m)
    d = ema(k, m)
    return k, d


def vol_ratio(vols, n=5):
    out = [1.0] * len(vols)
    for i in range(n, len(vols)):
        ma = sum(vols[i - n + 1:i + 1]) / n
        out[i] = vols[i] / ma if ma > 0 else 1.0
    return out


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
    lo, mid, up = boll(closes)
    pdi, mdi, adx = dmi(highs, lows, closes)
    bbi_v = bbi(closes)
    sk, sd = skdj(highs, lows, closes)
    vr = vol_ratio(vols)
    mg = [dif[i - 1] <= dea[i - 1] and dif[i] > dea[i] for i in range(1, n)]
    md = [dif[i - 1] >= dea[i - 1] and dif[i] < dea[i] for i in range(1, n)]
    kg = [ks[i - 1] <= ds[i - 1] and ks[i] > ds[i] for i in range(1, n)]
    kd = [ks[i - 1] >= ds[i - 1] and ks[i] < ds[i] for i in range(1, n)]
    return dict(opens=opens, highs=highs, lows=lows, closes=closes, vols=vols, n=n,
                dif=dif, dea=dea, ks=ks, ds=ds, rsi=rsi_v, up=up, mid=mid, lo=lo,
                pdi=pdi, mdi=mdi, adx=adx, bbi=bbi_v, sk=sk, sd=sd, vr=vr,
                macd_gold=[False] + mg, macd_dead=[False] + md,
                kdj_gold=[False] + kg, kdj_dead=[False] + kd)


def run_dual_cross(pc, buy_window=2, dead_window=2, reduce_ratio=0.5,
                   buy_extra=None, sell_extra=None, regime=None):
    n = pc["n"]
    opens, closes = pc["opens"], pc["closes"]
    cash, shares = 1.0, 0.0
    nav = []
    buys = reduces = exits = in_days = 0

    def win(arr, i, w):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    for i in range(n):
        if regime is not None and not regime[i]:
            if shares > 0:
                cash += shares * opens[i] * (1 - FEE)
                shares = 0.0
                exits += 1
            nav.append(cash + shares * closes[i])
            continue
        order = None
        if i > 0:
            sig_buy = win(pc["macd_gold"], i - 1, buy_window) and win(pc["kdj_gold"], i - 1, buy_window)
            if shares == 0:
                if sig_buy and (buy_extra is None or buy_extra(i, pc)):
                    order = "BUY"
            else:
                double_dead = win(pc["macd_dead"], i - 1, dead_window) and win(pc["kdj_dead"], i - 1, dead_window)
                single_dead = (pc["macd_dead"][i - 1] and not pc["kdj_dead"][i - 1]) or \
                              (pc["kdj_dead"][i - 1] and not pc["macd_dead"][i - 1])
                weak = (pc["pdi"][i - 1] >= pc["mdi"][i - 1]) and pc["adx"][i - 1] >= 25 and pc["rsi"][i - 1] >= 50
                if double_dead and (not weak) and (sell_extra is None or sell_extra(i, pc)):
                    order = "EXIT"
                elif single_dead and (not weak) and (sell_extra is None or sell_extra(i, pc)):
                    order = "REDUCE"
                elif sig_buy and (buy_extra is None or buy_extra(i, pc)):
                    order = "BUY_MORE"
        if order == "BUY" and shares == 0:
            shares = cash / (opens[i] * (1 + FEE)); cash = 0.0; buys += 1
        elif order == "BUY_MORE" and cash > 0:
            add = cash / (opens[i] * (1 + FEE)); shares += add; cash = 0.0; buys += 1
        elif order == "REDUCE":
            s = shares * reduce_ratio; cash += s * opens[i] * (1 - FEE); shares -= s; reduces += 1
        elif order == "EXIT":
            cash += shares * opens[i] * (1 - FEE); shares = 0.0; exits += 1
        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])
    return nav, buys, reduces, exits, in_days


def metrics(dates, nav):
    total = nav[-1] / nav[0] - 1
    rets = [(nav[i] / nav[i - 1] - 1) for i in range(1, len(nav))]
    n = len(rets)
    ann = (1 + total) ** (252 / n) - 1 if n else 0
    peak = nav[0]
    mdd = 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mean = sum(rets) / n if n else 0
    var = sum((r - mean) ** 2 for r in rets) / n if n else 0
    std = var ** 0.5
    sharpe = (mean / std) * (252 ** 0.5) if std else 0
    return dict(total_return=total, annualized_return=ann, max_drawdown=mdd,
                sharpe=sharpe, vol=std * (252 ** 0.5), n=n)


def pct(x):
    return f"{x*100:.1f}%"


FILTERS = {
    "A_RSI50": ("买入须 RSI(12)>50", lambda i, pc: pc["rsi"][i - 1] > 50, None),
    "B_BOLL中轨上": ("买入须收盘>布林中轨", lambda i, pc: pc["closes"][i - 1] > pc["mid"][i - 1], None),
    "C_ADX25": ("买入须 ADX>=25", lambda i, pc: pc["adx"][i - 1] >= 25, None),
    "D_BBI上": ("买入须收盘>BBI", lambda i, pc: pc["closes"][i - 1] > pc["bbi"][i - 1], None),
    "E_SKDJ低位": ("买入须 SKDJ慢K<50", lambda i, pc: pc["sk"][i - 1] < 50, None),
    "F_放量": ("买入须量比>1.2", lambda i, pc: pc["vr"][i - 1] > 1.2, None),
    "G_死叉破中轨": ("死叉且收盘<中轨才清", None, lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1]),
    "H_死叉ADX向下": ("死叉且ADX降才清", None, lambda i, pc: pc["adx"][i - 1] < pc["adx"][i - 2] if i >= 2 else True),
    "I_死叉破BBI": ("死叉且收盘<BBI才清", None, lambda i, pc: pc["closes"][i - 1] < pc["bbi"][i - 1]),
}


def run_portfolio(names, frames, common, **kw):
    per = {}
    for nm in names:
        by = {r["day"]: r for r in frames[nm]}
        rows = [by[d] for d in common]
        pc = precompute(rows)
        nav, b, r, e, id_ = run_dual_cross(pc, **kw)
        per[nm] = dict(nav=nav, buys=b, reduces=r, exits=e, in_days=id_)
    port = [sum(per[nm]["nav"][i] for nm in names) / len(names) for i in range(len(common))]
    return port, per


def main():
    data = load_qfq()
    focus = ["半导体设备ETF", "科创芯片ETF", "芯片ETF华夏", "纳指ETF广发"]
    common = None
    for nm in focus:
        ds = set(r["day"] for r in data[nm])
        common = ds if common is None else common & ds
    common = sorted(common)

    cyb = load_cyb()
    cyb_dates = sorted(cyb.keys())
    cyb_ma120 = {}
    for i, d in enumerate(cyb_dates):
        if i >= 120:
            cyb_ma120[d] = sum(cyb[cyb_dates[j]] for j in range(i - 119, i + 1)) / 120
    regime = [cyb.get(d, 0) >= cyb_ma120.get(d, 1e9) for d in common]

    by_close = {nm: {r["day"]: r["close"] for r in data[nm]} for nm in focus}
    hold = [sum(by_close[nm][d] / by_close[nm][common[0]] for nm in focus) / len(focus) for d in common]
    m_hold = metrics(common, hold)

    variants = {}
    nav, per = run_portfolio(focus, data, common)
    variants["纯双金叉"] = (nav, per)
    nav, per = run_portfolio(focus, data, common, regime=regime)
    variants["双金叉+创业板MA120总开关"] = (nav, per)
    for key, (desc, be, se) in FILTERS.items():
        nav, per = run_portfolio(focus, data, common, buy_extra=be, sell_extra=se)
        variants[f"{key}"] = (nav, per)
    nav, per = run_portfolio(focus, data, common,
                             buy_extra=lambda i, pc: pc["adx"][i - 1] >= 25 and pc["closes"][i - 1] > pc["bbi"][i - 1],
                             sell_extra=lambda i, pc: pc["closes"][i - 1] < pc["bbi"][i - 1])
    variants["组合:ADX25&上BBI买/破BBI卖"] = (nav, per)

    print(f"窗口 {common[0]} ~ {common[-1]}  共 {len(common)} 交易日")
    print(f"{'策略':<22}{'累计':>9}{'年化':>9}{'最大回撤':>10}{'夏普':>7}{'波动率':>9}{'在场':>7}{'买':>6}{'清':>6}")
    print(f"{'买入持有':<22}{pct(m_hold['total_return']):>9}{pct(m_hold['annualized_return']):>9}{pct(m_hold['max_drawdown']):>10}{m_hold['sharpe']:>7.2f}{pct(m_hold['vol']):>9}{'100%':>7}{'-':>6}{'-':>6}")
    rows_out = []
    for label, (nav, per) in variants.items():
        m = metrics(common, nav)
        in_days = sum(v["in_days"] for v in per.values()) / (len(focus) * len(common))
        tb = sum(v["buys"] for v in per.values())
        te = sum(v["exits"] for v in per.values())
        print(f"{label:<22}{pct(m['total_return']):>9}{pct(m['annualized_return']):>9}{pct(m['max_drawdown']):>10}{m['sharpe']:>7.2f}{pct(m['vol']):>9}{in_days*100:>6.1f}%{tb:>6}{te:>6}")
        rows_out.append((label, nav))

    import matplotlib.font_manager as fm
    for cand in ["/System/Library/Fonts/PingFang.ttc"]:
        try:
            fm.fontManager.addfont(cand)
            plt.rcParams["font.sans-serif"] = [fm.FontProperties(fname=cand).get_name(), "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.plot(common, hold, label="买入持有", lw=2, color="black")
    cmap = plt.cm.tab20.colors
    for idx, (label, nav) in enumerate(rows_out):
        ax.plot(common, nav, label=label, lw=1.1, color=cmap[idx % len(cmap)], alpha=0.85)
    ax.set_title("双金叉 + 各类指标叠加（聚焦四只科技ETF 2023-07~2026-08）")
    ax.legend(fontsize=6.5, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(OUT_DIR / f"dual_cross_addons_{STAMP}.png"), dpi=130)
    plt.close(fig)
    print("\n图表已保存:", OUT_DIR / f"dual_cross_addons_{STAMP}.png")


def robustness_momentum_pool():
    d = json.load(open(ROOT / "data/momentum_etf_hfq.json"))["data"]
    names = list(d.keys())
    common = None
    for nm in names:
        ds = set(r["day"] for r in d[nm])
        common = ds if common is None else common & ds
    common = sorted(common)
    common = [dd for dd in common if dd >= "2020-01-01"]
    idx = {nm: {r["day"]: r for r in d[nm]} for nm in names}

    def port_run(**kw):
        per = {}
        for nm in names:
            rows = [idx[nm][dd] for dd in common]
            pc = precompute(rows)
            nav, b, r, e, id_ = run_dual_cross(pc, **kw)
            per[nm] = dict(nav=nav, buys=b)
        port = [sum(per[nm]["nav"][i] for nm in names) / len(names) for i in range(len(common))]
        return port

    print(f"\n=== 稳健性：32-ETF 池等权（{common[0]}~{common[-1]}, {len(common)}日，更长窗口防牛市偏差）===")
    print(f"{'策略':<22}{'累计':>9}{'年化':>9}{'最大回撤':>10}{'夏普':>7}{'波动率':>9}")
    nav_hold = [sum(idx[nm][common[i]]["close"] / idx[nm][common[0]]["close"] for nm in names) / len(names) for i in range(len(common))]
    mh = metrics(common, nav_hold)
    print(f"{'买入持有':<22}{pct(mh['total_return']):>9}{pct(mh['annualized_return']):>9}{pct(mh['max_drawdown'] if 'max_drawdown' in mh else 'max_drawdown'):>10}{mh['sharpe']:>7.2f}{pct(mh['vol']):>9}")
    nav = port_run()
    m = metrics(common, nav); print(f"{'纯双金叉':<22}{pct(m['total_return']):>9}{pct(m['annualized_return']):>9}{pct(m['max_drawdown']):>10}{m['sharpe']:>7.2f}{pct(m['vol']):>9}")
    nav = port_run(sell_extra=lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1])
    m = metrics(common, nav); print(f"{'双金叉-破中轨才卖':<20}{pct(m['total_return']):>9}{pct(m['annualized_return']):>9}{pct(m['max_drawdown']):>10}{m['sharpe']:>7.2f}{pct(m['vol']):>9}")
    nav = port_run(buy_extra=lambda i, pc: pc["adx"][i - 1] >= 25, sell_extra=lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1])
    m = metrics(common, nav); print(f"{'ADX25买-破中轨卖':<18}{pct(m['total_return']):>9}{pct(m['annualized_return']):>9}{pct(m['max_drawdown']):>10}{m['sharpe']:>7.2f}{pct(m['vol']):>9}")


if __name__ == "__main__":
    main()
    robustness_momentum_pool()
