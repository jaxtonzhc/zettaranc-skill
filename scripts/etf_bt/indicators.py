"""技术指标计算层。

所有指标输入 OHLCV 列表，输出与输入等长的序列。
信号检测（金叉/死叉）也在这里，产出布尔列表。
"""

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


def kdj(highs, lows, closes, n=9):
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
    std = [(sum((c - mid[i]) ** 2 for c in closes[max(0, i - n + 1):i + 1]) / min(i + 1, n)) ** 0.5 for i in range(len(closes))]
    up = [mid[i] + k * std[i] for i in range(len(closes))]
    lo = [mid[i] - k * std[i] for i in range(len(closes))]
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
    mas = []
    for p in ps:
        mas.append([sum(closes[max(0, i - p + 1):i + 1]) / min(i + 1, p) for i in range(len(closes))])
    return [sum(mas[j][i] for j in range(len(ps))) / len(ps) for i in range(len(closes))]


def vol_ratio(vols, n=5):
    out = [1.0] * len(vols)
    for i in range(n, len(vols)):
        ma = sum(vols[i - n + 1:i + 1]) / n
        out[i] = vols[i] / ma if ma > 0 else 1.0
    return out


def crosses(above, below):
    """金叉序列：above[i-1] <= below[i-1] and above[i] > below[i]"""
    n = len(above)
    gold = [False] * n
    dead = [False] * n
    for i in range(1, n):
        gold[i] = above[i - 1] <= below[i - 1] and above[i] > below[i]
        dead[i] = above[i - 1] >= below[i - 1] and above[i] < below[i]
    return gold, dead

def chip_peak(highs, lows, closes, vols, lookback=60, bins=20):
    """筹码峰（近似）: 近 lookback 天按成交量加权的价格分布。
    返回每天两个序列: (获利比例%, 平均成本)。
    获利比例 = 成本低于当前收盘价的筹码占比。>90% 视为高度获利(易回调)，<10% 视为深度套牢。
    平均成本 = 近 lookback 天的成交量加权平均价（VWAP 近似）。
    """
    n = len(closes)
    profit_ratio = [0.0] * n
    avg_cost = [0.0] * n
    for i in range(n):
        s = max(0, i - lookback + 1)
        seg_v = vols[s:i + 1]
        tot_v = sum(seg_v)
        if tot_v <= 0:
            avg_cost[i] = closes[i]
            profit_ratio[i] = 50.0
            continue
        # 每天的典型价(最高+最低+收盘)/3 作为当日筹码成交重心
        typ = [(highs[j] + lows[j] + closes[j]) / 3 for j in range(s, i + 1)]
        cost = sum(t * v for t, v in zip(typ, seg_v)) / tot_v
        avg_cost[i] = cost
        # 获利筹码: 成本低于现价的成交量占比
        win_v = sum(v for t, v in zip(typ, seg_v) if t < closes[i])
        profit_ratio[i] = win_v / tot_v * 100
    return profit_ratio, avg_cost

