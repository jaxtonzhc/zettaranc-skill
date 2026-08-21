#!/usr/bin/env python3
"""策略实验室：本地 DB 数据 -> 策略回测 -> 迭代优化

用法:
    python scripts/strategy_lab.py inspect           # 查看数据概况
    python scripts/strategy_lab.py run               # 跑当前策略
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "stock_data.db"

import pandas as pd  # noqa: E402

# 板块股票池（大盘 + 中小盘混合）。已有多年数据的标注 have
SECTORS = {
    "科技": [
        "000977.SZ", "000063.SZ", "002230.SZ", "000725.SZ", "600487.SH",
        "002281.SZ", "002463.SZ", "300308.SZ", "300502.SZ",  # have
        "002415.SZ", "002475.SZ", "000938.SZ", "000021.SZ", "000066.SZ",
        "002008.SZ", "300124.SZ", "000988.SZ", "000100.SZ", "000636.SZ",
        "001287.SZ", "001270.SZ", "001298.SZ", "000070.SZ", "000823.SZ",
        "000555.SZ",
    ],
    "半导体": [
        "688981.SH", "002371.SZ", "603501.SH",  # have
        "688012.SH", "688008.SH", "603986.SH", "600584.SH", "002049.SZ",
        "688041.SH", "688256.SH", "688396.SH", "002156.SZ", "300373.SZ",
        "300604.SZ", "688082.SH", "688072.SH", "300661.SZ",
    ],
    "能源": [
        "601088.SH", "601857.SH", "600028.SH",  # have
        "600938.SH", "601225.SH", "600188.SH", "601898.SH", "601699.SH",
        "000983.SZ", "600348.SH", "000552.SZ", "000937.SZ", "000723.SZ",
        "000968.SZ", "600508.SH", "600123.SH",
    ],
    "有色": [
        "601899.SH", "600111.SH", "603993.SH",  # have
        "600547.SH", "600489.SH", "600362.SH", "601600.SH", "002460.SZ",
        "002466.SZ", "000792.SZ", "000630.SZ", "000878.SZ", "000807.SZ",
        "000960.SZ", "000831.SZ", "000426.SZ", "000737.SZ", "000758.SZ",
        "000657.SZ", "000962.SZ", "000603.SZ", "000975.SZ",
    ],
    "家电": [
        "000333.SZ", "000651.SZ", "000921.SZ",  # 美的 格力 海信家电
        "600690.SH", "002032.SZ", "002508.SZ",  # 海尔 苏泊尔 老板
        "002242.SZ", "002959.SZ", "688169.SH",  # 九阳 小熊 石头
        "603486.SH", "600060.SH", "600839.SH",  # 科沃斯 海信视像 四川长虹
    ],
}


def sync(days: int = 1300):
    """用免费数据源补齐股票池的历史K线，写入本地 daily_kline"""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from modules.a_stock_data_client import baidu_kline_with_ma

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    codes = [c for lst in SECTORS.values() for c in lst]
    for i, code in enumerate(codes, 1):
        have = cur.execute(
            "SELECT COUNT(*), MIN(trade_date) FROM daily_kline WHERE ts_code=?", (code,)
        ).fetchone()
        if have[0] >= 1000 and have[1] <= "20220110":
            print(f"[{i}/{len(codes)}] {code} 已有 {have[0]} 行, 跳过")
            continue
        try:
            recs = fetch_full_kline(code)
        except Exception as e:
            print(f"[{i}/{len(codes)}] {code} 抓取失败: {e}")
            continue
        if not recs:
            print(f"[{i}/{len(codes)}] {code} 无数据")
            continue
        cur.executemany(
            "INSERT OR REPLACE INTO daily_kline"
            " (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["ts_code"], r["trade_date"], r["open"], r["high"], r["low"],
                    r["close"], r["vol"], r["amount"], r["pct_chg"],
                )
                for r in recs
            ],
        )
        conn.commit()
        print(f"[{i}/{len(codes)}] {code} 写入 {len(recs)} 行 ({recs[0]['trade_date']}~{recs[-1]['trade_date']})")
    conn.close()


def fetch_full_kline(ts_code: str, start_time: str = "20220101") -> list[dict]:
    """百度股市通全量K线（带 start_time 突破500行限制），对齐 tushare 字段"""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from modules.a_stock_data_client import baidu_kline_with_ma

    code = ts_code.split(".")[0]
    d = baidu_kline_with_ma(code, start_time=start_time)
    keys, rows = d.get("keys", []), d.get("rows", [])
    recs = []
    prev_close = None
    for row_str in rows:
        if not row_str.strip():
            continue
        vals = row_str.split(",")
        if len(vals) < 8:
            continue
        rec = dict(zip(keys, vals))
        close = float(rec["close"])
        pct = round((close / prev_close - 1) * 100, 4) if prev_close else 0.0
        recs.append(
            {
                "ts_code": ts_code,
                "trade_date": rec["time"].replace("-", "") if "-" in rec["time"] else rec["time"],
                "open": float(rec["open"]),
                "high": float(rec["high"]),
                "low": float(rec["low"]),
                "close": close,
                "vol": float(rec["volume"]),
                "amount": float(rec["amount"]),
                "pct_chg": pct,
            }
        )
        prev_close = close
    return recs


# ---------------------------------------------------------------------------
# 回测引擎
# ---------------------------------------------------------------------------

DEFAULT_CFG = {
    # 买入
    "entry": "gc",         # gc=双金叉 | mr=超卖反弹 | pb=金叉后回踩
    "gc_window": 3,        # 双金叉允许间隔天数（0=同一天）
    "cooldown": 5,         # 买入信号冷却（FILTER）
    "trend": "none",       # none | c>ma20 | c>ma60 | ma20>ma60
    "vol_mult": 0.0,       # 信号日量能 >= vol_mult * 3日均量, 0=不过滤
    "j_max": 999.0,        # 信号日 J 值上限
    "mr_j": 20.0,          # mr 模式：J 值低位阈值
    "breadth_min": 0.0,    # 板块宽度下限（板块内 c>ma20 占比）, 0=不过滤
    "wk_filter": False,    # 周线 MACD 多头才买
    # 卖出（按优先级逐日检查，先触发先执行）
    "sell_ddc": 0,         # 双死叉窗口（0=同日, -1=关闭）
    "sell_single_dc": False,  # 任一单死叉即卖
    "sell_break_ma": 0,    # 收盘跌破 MA_n 卖（0=关闭）
    "stop_loss": 0.0,      # 固定止损（0.05=5%），盘中触及
    "take_profit": 0.0,    # 固定止盈，盘中触及
    "trail_stop": 0.0,     # 自最高收盘回撤止盈
    "vol_crash": 0.0,      # 放量大跌: 单日跌幅<=-x 且 量>=1.5*3日均量
    "consec_drop": 0.0,    # 连续2日累计跌幅 <= -x
    "time_stop": 0,        # 最大持有交易日（0=不限）
    "exit_adx_turn": 0.0,  # ADX > x 后拐头向下 -> 卖出（0=关闭）
    "exit_adxr_cross": False,  # ADX 死叉 ADXR -> 卖出
    "partial_tp": 0.0,     # 分批止盈：触及 +x 先卖 partial_frac
    "partial_frac": 0.5,   # 分批止盈比例
    "be_after_partial": True,  # 分批止盈后剩余仓位成本价止损
    "cost": 0.001,         # 单边成本
}


def load_sector_data() -> dict[str, dict[str, pd.DataFrame]]:
    """从本地 DB 加载股票池数据 -> {sector: {ts_code: df}}"""
    conn = sqlite3.connect(DB)
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for sector, codes in SECTORS.items():
        out[sector] = {}
        for code in codes:
            df = pd.read_sql_query(
                "SELECT trade_date, open, high, low, close, vol FROM daily_kline"
                " WHERE ts_code=? ORDER BY trade_date",
                conn,
                params=(code,),
            )
            if len(df) >= 200:
                out[sector][code] = add_indicators(df)
    conn.close()
    return out


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """MACD(6,13,5) + KDJ(9,3,3) + 均线 + 量能"""
    c, h, low, v = df["close"], df["high"], df["low"], df["vol"]
    dif = c.ewm(span=6, adjust=False).mean() - c.ewm(span=13, adjust=False).mean()
    dea = dif.ewm(span=5, adjust=False).mean()
    llv = low.rolling(9).min()
    hhv = h.rolling(9).max()
    rsv = (c - llv) / (hhv - llv).replace(0, float("nan")) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    df = df.copy()
    df["dif"], df["dea"], df["k"], df["d"], df["j"] = dif, dea, k, d, j
    df["macd_gc"] = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    df["macd_dc"] = (dif < dea) & (dif.shift(1) >= dea.shift(1))
    df["kdj_gc"] = (k > d) & (k.shift(1) <= d.shift(1))
    df["kdj_dc"] = (k < d) & (k.shift(1) >= d.shift(1))
    for n in (5, 10, 20, 30, 60):
        df[f"ma{n}"] = c.rolling(n).mean()
    df["vol_ma3"] = v.rolling(3).mean()
    df["pct"] = c.pct_change() * 100
    df["pct2"] = c.pct_change(2) * 100  # 2日累计涨跌
    # 趋势柱（用户原版）: 典型价EMA10 方向
    jj = (c + h + low) / 3
    ta = jj.ewm(span=10, adjust=False).mean()
    df["ta"] = ta
    df["ta_up"] = ta > ta.shift(1)
    df["high20"] = h.rolling(20).max()
    # BOLL(20,2) 与 RSI6
    mid = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    df["boll_mid"], df["boll_up"], df["boll_dn"] = mid, mid + 2 * std20, mid - 2 * std20
    diff = c.diff()
    gup = diff.clip(lower=0).ewm(alpha=1 / 6, adjust=False).mean()
    gdn = (-diff.clip(upper=0)).ewm(alpha=1 / 6, adjust=False).mean()
    df["rsi6"] = (gup / gdn.replace(0, float("nan")) * 100).fillna(100.0)
    # DMI(14,6): +DI -DI ADX ADXR
    tr = pd.concat(
        [h - low, (h - c.shift(1)).abs(), (low - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    up_move = h.diff()
    dn_move = -low.diff()
    pdm = ((up_move > dn_move) & (up_move > 0)) * up_move
    ndm = ((dn_move > up_move) & (dn_move > 0)) * dn_move
    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr14
    ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, float("nan"))
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
    df["pdi"], df["ndi"], df["adx"] = pdi, ndi, adx
    df["adxr"] = adx.ewm(alpha=1 / 6, adjust=False).mean()
    # 周线 MACD 方向（周线收盘确认，回填到日线）
    wk = df.set_index(pd.to_datetime(df["trade_date"]))["close"].resample("W-FRI").last().dropna()
    wdif = wk.ewm(span=6, adjust=False).mean() - wk.ewm(span=13, adjust=False).mean()
    wdea = wdif.ewm(span=5, adjust=False).mean()
    wup = (wdif > wdea).astype(float)
    wup.index = wup.index.strftime("%Y%m%d")
    df["wk_up"] = df["trade_date"].map(wup).ffill().fillna(0).values
    return df


def _roll_any(s: pd.Series, window: int) -> pd.Series:
    """过去 window 天（含今天）内是否出现过 True"""
    return s.rolling(window + 1).sum().fillna(0) > 0


def run_stock(df: pd.DataFrame, cfg: dict, breadth: pd.Series | None = None):
    """单股票交易仿真。信号收盘确认，次日开盘价成交。返回交易列表"""
    n = len(df)
    o = df["open"].values
    h = df["high"].values
    low = df["low"].values
    c = df["close"].values
    dates = df["trade_date"].values
    macd_gc = df["macd_gc"].values
    kdj_gc = df["kdj_gc"].values
    macd_dc = df["macd_dc"].values
    kdj_dc = df["kdj_dc"].values
    j = df["j"].values
    adx = df["adx"].values
    adxr = df["adxr"].values
    v = df["vol"].values
    vma3 = df["vol_ma3"].values
    pct = df["pct"].values
    pct2 = df["pct2"].values
    mas = {x: df[f"ma{x}"].values for x in (5, 10, 20, 30, 60)}
    bvals = breadth.values if breadth is not None else None

    gc_win = cfg["gc_window"]
    recent_macd_gc = _roll_any(df["macd_gc"], gc_win).values
    recent_kdj_gc = _roll_any(df["kdj_gc"], gc_win).values
    dc_win = cfg["sell_ddc"]
    recent_macd_dc = _roll_any(df["macd_dc"], dc_win).values if dc_win >= 0 else None
    recent_kdj_dc = _roll_any(df["kdj_dc"], dc_win).values if dc_win >= 0 else None

    trades = []
    pos = None  # (entry_idx_exec, entry_price)
    last_buy_i = -10**9
    high_close = 0.0
    partial_done = False  # 分批止盈是否已执行
    pending_feats: dict | None = None

    wk_up = df["wk_up"].values

    for i in range(60, n - 1):
        if pos is not None:
            high_close = max(high_close, c[i])
            entry_i, entry_p = pos
            ret_c = c[i] / entry_p - 1
            sell = None
            # 分批止盈：盘中触及目标价先卖一部分，剩余用成本价保护
            if (
                cfg["partial_tp"] > 0
                and not partial_done
                and h[i] >= entry_p * (1 + cfg["partial_tp"])
            ):
                partial_done = True
            # 分批后的成本价止损
            if partial_done and cfg["be_after_partial"] and c[i] < entry_p:
                sell = ("保本出", c[i], False)
            # 盘中止损/止盈（用当日高低价判断触及）
            if sell is None and cfg["stop_loss"] > 0 and low[i] <= entry_p * (1 - cfg["stop_loss"]):
                sell = ("止损", entry_p * (1 - cfg["stop_loss"]), True)
            elif sell is None and cfg["take_profit"] > 0 and h[i] >= entry_p * (1 + cfg["take_profit"]):
                sell = ("止盈", entry_p * (1 + cfg["take_profit"]), True)
            elif cfg["trail_stop"] > 0 and ret_c <= (high_close / entry_p - 1) - cfg["trail_stop"] and high_close / entry_p - 1 > 0.02:
                sell = ("回撤止盈", c[i], False)
            elif cfg["vol_crash"] > 0 and pct[i] <= -cfg["vol_crash"] * 100 and v[i] >= 1.5 * vma3[i]:
                sell = ("放量大跌", c[i], False)
            elif cfg["consec_drop"] > 0 and pct2[i] <= -cfg["consec_drop"] * 100:
                sell = ("连续下跌", c[i], False)
            elif cfg["exit_adx_turn"] > 0 and adx[i] > cfg["exit_adx_turn"] and adx[i] < adx[i - 1]:
                sell = ("ADX拐头", c[i], False)
            elif cfg["exit_adxr_cross"] and adx[i] < adxr[i] and adx[i - 1] >= adxr[i - 1]:
                sell = ("ADX死叉ADXR", c[i], False)
            elif cfg["sell_break_ma"] > 0 and c[i] < mas[cfg["sell_break_ma"]][i]:
                sell = (f"破MA{cfg['sell_break_ma']}", c[i], False)
            elif cfg["sell_single_dc"] and (macd_dc[i] or kdj_dc[i]):
                sell = ("单死叉", c[i], False)
            elif dc_win >= 0 and recent_macd_dc[i] and recent_kdj_dc[i]:
                sell = ("双死叉", c[i], False)
            elif cfg["time_stop"] > 0 and i - entry_i >= cfg["time_stop"]:
                sell = ("到期", c[i], False)

            if sell is not None:
                reason, price, intraday = sell
                exec_i = i if intraday else i + 1
                exec_p = price if intraday else o[i + 1]
                ret = exec_p / entry_p - 1 - 2 * cfg["cost"]
                if partial_done:
                    frac = cfg["partial_frac"]
                    ret = frac * (cfg["partial_tp"] - cfg["cost"]) + (1 - frac) * (
                        exec_p / entry_p - 1 - 2 * cfg["cost"]
                    )
                    if reason != "保本出":
                        reason = f"分批+{reason}"
                trade = {
                    "entry_date": dates[entry_i],
                    "exit_date": dates[exec_i],
                    "ret": ret,
                    "hold": exec_i - entry_i,
                    "reason": reason,
                }
                if pending_feats:
                    trade.update(pending_feats)
                trades.append(trade)
                pos = None
                last_buy_i = entry_i
            continue

        # ---- 买入判断 ----
        if i - last_buy_i <= cfg["cooldown"]:
            continue
        t = cfg["trend"]
        if t == "c>ma20" and not c[i] > mas[20][i]:
            continue
        if t == "c>ma60" and not c[i] > mas[60][i]:
            continue
        if t == "ma20>ma60" and not mas[20][i] > mas[60][i]:
            continue
        if bvals is not None and bvals[i] < cfg["breadth_min"]:
            continue
        if cfg["wk_filter"] and not wk_up[i]:
            continue

        mode = cfg["entry"]
        if mode == "gc":  # 双金叉（默认）
            if not (recent_macd_gc[i] and recent_kdj_gc[i]):
                continue
            if gc_win == 0 and not (macd_gc[i] and kdj_gc[i]):
                continue
            if cfg["vol_mult"] > 0 and not v[i] >= cfg["vol_mult"] * vma3[i]:
                continue
            if j[i] > cfg["j_max"]:
                continue
        elif mode == "mr":  # 超卖反弹：J 值低位拐头向上
            if not (j[i] <= cfg["mr_j"] and j[i] > j[i - 1]):
                continue
        elif mode == "pb":  # 金叉后回踩：近10日有双金叉，现价回踩 MA20 附近
            if not (recent_macd_gc[i] and recent_kdj_gc[i]):
                continue
            if not _roll_any(df["macd_gc"], 10).values[i] and not _roll_any(df["kdj_gc"], 10).values[i]:
                continue
            ma20 = mas[20][i]
            if not (abs(c[i] / ma20 - 1) <= 0.03 and c[i] > c[i - 1]):
                continue
        pos = (i + 1, o[i + 1])  # 次日开盘买入
        high_close = o[i + 1]
        partial_done = False
        pending_feats = {
            "c>ma60": bool(c[i] > mas[60][i]),
            "c>ma20": bool(c[i] > mas[20][i]),
            "wk_up": bool(wk_up[i]),
            "vol_ratio": round(v[i] / vma3[i], 2) if vma3[i] else 0,
            "j": round(j[i], 1),
            "breadth": round(bvals[i], 2) if bvals is not None else None,
            "signal_date": dates[i],
        }

    if pos is not None:  # 数据末尾强制平仓
        entry_i, entry_p = pos
        trade = {
            "entry_date": dates[entry_i],
            "exit_date": dates[n - 1],
            "ret": c[n - 1] / entry_p - 1 - 2 * cfg["cost"],
            "hold": n - 1 - entry_i,
            "reason": "末尾",
        }
        if pending_feats:
            trade.update(pending_feats)
        trades.append(trade)
    return trades


def sector_breadth(dfs: dict[str, pd.DataFrame]) -> pd.Series:
    """板块宽度：板块内收盘 > MA20 的股票占比（按日期）"""
    frames = []
    for df in dfs.values():
        s = (df["close"] > df["ma20"]).astype(float)
        s.index = df["trade_date"].values
        frames.append(s)
    m = pd.concat(frames, axis=1).mean(axis=1)
    return m


def run_all(cfg: dict, data=None, start=None, end=None):
    """全板块回测。start/end 形如 '20220101'，过滤交易（按入场日）"""
    if data is None:
        data = load_sector_data()
    all_trades = []
    per_sector = {}
    for sector, dfs in data.items():
        br = sector_breadth(dfs)
        sector_trades = []
        for code, df in dfs.items():
            b = br.reindex(df["trade_date"].values).ffill().fillna(0.5)
            b.index = range(len(df))
            trs = run_stock(df, cfg, breadth=b)
            for t in trs:
                t["sector"] = sector
                t["code"] = code
                if start and t["entry_date"] < start:
                    continue
                if end and t["entry_date"] > end:
                    continue
                sector_trades.append(t)
        per_sector[sector] = sector_trades
        all_trades.extend(sector_trades)
    return all_trades, per_sector


def stats(trades: list[dict], label: str = "") -> dict:
    if not trades:
        print(f"{label}: 无交易")
        return {}
    rets = pd.Series([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    out = {
        "trades": len(rets),
        "win": round((rets > 0).mean() * 100, 1),
        "avg": round(rets.mean() * 100, 2),
        "median": round(rets.median() * 100, 2),
        "pf": round(pf, 2),
        "hold": round(pd.Series([t["hold"] for t in trades]).mean(), 1),
        "worst": round(rets.min() * 100, 1),
        "best": round(rets.max() * 100, 1),
        "sum": round(rets.sum() * 100, 1),
    }
    print(
        f"{label}: 交易{out['trades']} 胜率{out['win']}% 均值{out['avg']}% "
        f"中位{out['median']}% 盈亏比{out['pf']} 持有{out['hold']}天 "
        f"最差{out['worst']}% 总和{out['sum']}%"
    )
    return out


def breakdown(trades: list[dict]):
    df = pd.DataFrame(trades)
    if df.empty:
        return
    df["year"] = df["entry_date"].str[:4]
    print("  按年:", end=" ")
    for y, g in df.groupby("year"):
        print(
            f"{y}: {len(g)}笔 {(g['ret'] > 0).mean() * 100:.0f}%胜 均{g['ret'].mean() * 100:+.2f}%",
            end="  ",
        )
    print()
    print("  按板块:", end=" ")
    for s, g in df.groupby("sector"):
        print(
            f"{s}: {len(g)}笔 {(g['ret'] > 0).mean() * 100:.0f}%胜 均{g['ret'].mean() * 100:+.2f}%",
            end="  ",
        )
    print()
    print("  按卖出原因:", end=" ")
    for r, g in df.groupby("reason"):
        print(
            f"{r}: {len(g)}笔 {(g['ret'] > 0).mean() * 100:.0f}%胜 均{g['ret'].mean() * 100:+.2f}%",
            end="  ",
        )
    print()


def analyze(trades: list[dict]):
    """入场日特征分桶：什么条件下的交易更赚钱"""
    df = pd.DataFrame(trades)
    if df.empty:
        print("无交易")
        return

    def bucket(col, cond, label):
        sub = df[cond(df)]
        if len(sub) < 10:
            return
        print(
            f"  {label:28s} {len(sub):5d}笔 {(sub['ret'] > 0).mean() * 100:5.1f}%胜 "
            f"均{sub['ret'].mean() * 100:+6.2f}% 盈亏比{_pf(sub):5.2f}"
        )

    def _pf(g):
        w = g["ret"][g["ret"] > 0].sum()
        lo = g["ret"][g["ret"] <= 0].sum()
        return w / abs(lo) if lo != 0 else float("inf")

    print(f"总样本 {len(df)} 笔")
    bucket("c>ma60", lambda d: d["c>ma60"], "站上60日线")
    bucket("c>ma60", lambda d: ~d["c>ma60"], "60日线下方")
    bucket("wk_up", lambda d: d["wk_up"], "周线多头")
    bucket("wk_up", lambda d: ~d["wk_up"], "周线空头")
    bucket("vol", lambda d: d["vol_ratio"] >= 1.5, "放量(>=1.5倍)")
    bucket("vol", lambda d: d["vol_ratio"] < 1.5, "未放量(<1.5倍)")
    bucket("vol", lambda d: d["vol_ratio"] >= 2.0, "明显放量(>=2倍)")
    bucket("j", lambda d: d["j"] <= 20, "J<=20(低位金叉)")
    bucket("j", lambda d: (d["j"] > 20) & (d["j"] <= 60), "20<J<=60")
    bucket("j", lambda d: d["j"] > 60, "J>60(高位金叉)")
    bucket("br", lambda d: d["breadth"] >= 0.6, "板块宽度>=60%")
    bucket("br", lambda d: d["breadth"] < 0.4, "板块宽度<40%")
    # 组合
    bucket(
        "combo",
        lambda d: d["wk_up"] & d["c>ma60"] & (d["vol_ratio"] >= 1.5),
        "周线多+站上60日+放量",
    )
    bucket(
        "combo2",
        lambda d: d["wk_up"] & (d["j"] <= 20),
        "周线多+低位金叉",
    )


def inspect():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print("--- tables ---")
    for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        print(r[0])
    print("--- stock_basic industry distribution ---")
    for r in cur.execute(
        "SELECT industry, COUNT(*) FROM stock_basic GROUP BY industry ORDER BY 2 DESC LIMIT 50"
    ):
        print(r)
    print("--- kline coverage ---")
    print(
        cur.execute(
            "SELECT COUNT(DISTINCT ts_code), MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_kline"
        ).fetchone()
    )
    print("--- stock_basic sample ---")
    for r in cur.execute("SELECT * FROM stock_basic LIMIT 2"):
        print(r)
    cols = cur.execute("PRAGMA table_info(stock_basic)").fetchall()
    print([c[1] for c in cols])
    cols = cur.execute("PRAGMA table_info(daily_kline)").fetchall()
    print([c[1] for c in cols])


def pool():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT k.ts_code, COALESCE(s.name,''), COUNT(*) n,
               ROUND(AVG(k.amount)/10000,1) avg_yi
        FROM daily_kline k LEFT JOIN stock_basic s ON s.ts_code=k.ts_code
        GROUP BY k.ts_code ORDER BY avg_yi DESC
        """
    ).fetchall()
    print(f"total {len(rows)} codes")
    for r in rows:
        print(r)


def probe(code: str, start_time: str):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from modules.a_stock_data_client import baidu_kline_with_ma

    d = baidu_kline_with_ma(code, start_time=start_time)
    rows = d.get("rows", [])
    print("keys:", d.get("keys", []))
    print("rows:", len(rows))
    print("first:", rows[0][:80] if rows else None)
    print("last:", rows[-1][:80] if rows else None)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "inspect"
    if cmd == "inspect":
        inspect()
    elif cmd == "pool":
        pool()
    elif cmd == "sync":
        sync()
    elif cmd == "probe":
        probe(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    elif cmd == "run":
        import json

        cfg = dict(DEFAULT_CFG)
        if len(sys.argv) > 2:
            cfg.update(json.loads(sys.argv[2]))
        data = load_sector_data()
        print(
            "数据:",
            {s: len(d) for s, d in data.items()},
        )
        # 样本内 2022-2024
        trades_is, _ = run_all(cfg, data, "20220101", "20241231")
        stats(trades_is, "IS 2022-2024")
        breakdown(trades_is)
        # 样本外 2025-2026
        trades_oos, _ = run_all(cfg, data, "20250101", "20261231")
        stats(trades_oos, "OOS 2025-2026")
        breakdown(trades_oos)
    elif cmd == "analyze":
        data = load_sector_data()
        cfg = dict(DEFAULT_CFG)
        if len(sys.argv) > 2:
            import json

            cfg.update(json.loads(sys.argv[2]))
        trades, _ = run_all(cfg, data, "20220101", "20241231")
        print("== IS 2022-2024 ==")
        analyze(trades)
        trades, _ = run_all(cfg, data, "20250101", "20261231")
        print("== OOS 2025-2026 ==")
        analyze(trades)
    elif cmd == "final":
        import json

        cfg = dict(DEFAULT_CFG, j_max=15, partial_tp=0.06, gc_window=2)
        if len(sys.argv) > 2:
            cfg.update(json.loads(sys.argv[2]))
        data = load_sector_data()
        for label, s, e in [("IS 2022-2024", "20220101", "20241231"), ("OOS 2025-2026", "20250101", "20261231")]:
            trades, _ = run_all(cfg, data, s, e)
            stats(trades, label + " 全板块")
            breakdown(trades)
            t3 = [t for t in trades if t["sector"] != "能源"]
            stats(t3, label + " 三板块(剔能源)")
            t3df = pd.DataFrame(t3)
            t3df["year"] = t3df["entry_date"].str[:4]
            print("  三板块按年:", end=" ")
            for y, g in t3df.groupby("year"):
                print(
                    f"{y}: {len(g)}笔 {(g['ret'] > 0).mean() * 100:.0f}%胜 均{g['ret'].mean() * 100:+.2f}%",
                    end="  ",
                )
            print()
    elif cmd == "signals":
        # 按公式原始逻辑统计每只股票的买/卖信号分布
        import json

        cfg = {"gc_count_n": 2, "j_max": 15.0, "cooldown": 5}
        if len(sys.argv) > 2:
            cfg.update(json.loads(sys.argv[2]))
        n, jmax, cd = cfg["gc_count_n"], cfg["j_max"], cfg["cooldown"]
        data = load_sector_data()
        print(f"公式逻辑: COUNT(金叉,{n}) 窗口, J<={jmax}, FILTER {cd}")
        print(f"{'代码':10s} {'板块':4s} {'买信号':>4s} {'近1年买':>5s} {'卖信号':>4s}  最近买点日期")
        tot_b, tot_s = 0, 0
        for sector, dfs in data.items():
            for code, df in dfs.items():
                mjc2 = df["macd_gc"].rolling(n).sum() >= 1
                kjc2 = df["kdj_gc"].rolling(n).sum() >= 1
                sjc = mjc2 & kjc2
                if cfg.get("need_fresh"):
                    sjc = sjc & (df["macd_gc"] | df["kdj_gc"])
                bs_raw = sjc & (df["j"] <= jmax)
                # FILTER 5
                idx = bs_raw.values.nonzero()[0]
                kept, last = [], -10**9
                for i in idx:
                    if i - last > cd:
                        kept.append(i)
                        last = i
                ss = (df["macd_dc"] & df["kdj_dc"]).sum()
                recent = [i for i in kept if df["trade_date"].iloc[i] >= "20250818"]
                last_date = df["trade_date"].iloc[kept[-1]] if kept else "-"
                tot_b += len(kept)
                tot_s += ss
                print(
                    f"{code:10s} {sector:4s} {len(kept):4d} {len(recent):5d} {ss:4d}  {last_date}"
                )
        print(f"合计: 买 {tot_b}, 卖 {tot_s}")
    elif cmd == "debug":
        # 对比: signals 原始计数 vs run_stock 实际成交数
        code = sys.argv[2] if len(sys.argv) > 2 else "000977.SZ"
        data = load_sector_data()
        df = None
        for dfs in data.values():
            if code in dfs:
                df = dfs[code]
        m3 = df["macd_gc"].rolling(3).sum() >= 1
        k3 = df["kdj_gc"].rolling(3).sum() >= 1
        raw = m3 & k3 & (df["j"] <= 15)
        print(f"{code}: 状态天数(3日窗&J<=15)={raw.sum()}, 其中当日有金叉={(raw & (df['macd_gc'] | df['kdj_gc'])).sum()}")
        cfg = dict(DEFAULT_CFG, j_max=15, gc_window=2, partial_tp=0.06, partial_frac=0.333)
        trs = run_stock(df, cfg)
        print(f"run_stock 交易数={len(trs)}")
        for t in trs:
            print(t["entry_date"], f"{t['ret'] * 100:+.1f}%", t["reason"])
    elif cmd == "verify":
        # 1) 按通达信语义精确模拟公式, 与回测引擎入场逐日对齐
        data = load_sector_data()
        total_formula, total_bt = 0, 0
        mismatch = []
        for sector, dfs in data.items():
            for code, df in dfs.items():
                # --- 公式语义 ---
                sjc = (df["macd_gc"].rolling(3).sum() >= 1) & (
                    df["kdj_gc"].rolling(3).sum() >= 1
                )
                bs_raw = (sjc & (df["j"] <= 15)).values
                idx = bs_raw.nonzero()[0]
                f_sig, last = [], -10**9
                for i in idx:
                    if i > last + 5:  # FILTER(x,5)
                        f_sig.append(i)
                        last = i
                # --- 回测引擎 ---
                cfg = dict(DEFAULT_CFG, j_max=15, gc_window=2)
                trs = run_stock(df, cfg)
                total_formula += len(f_sig)
                total_bt += len(trs)
                f_dates = {df["trade_date"].iloc[i] for i in f_sig}
                # 回测入场日 = 信号日次日, 信号日 = entry_date 前一交易日
                dates = list(df["trade_date"])
                for t in trs:
                    ei = dates.index(t["entry_date"])
                    sig_date = dates[ei - 1]
                    if sig_date not in f_dates:
                        mismatch.append((code, sig_date))
        print(f"公式信号总数={total_formula}, 回测入场数={total_bt}")
        print(f"回测有但公式漏掉的: {len(mismatch)}")
        for m in mismatch[:20]:
            print("  漏:", m)
    elif cmd == "inspect2":
        # 查看某股票某日期前后的指标细节
        code, target = sys.argv[2], sys.argv[3]
        data = load_sector_data()
        df = None
        for dfs in data.values():
            if code in dfs:
                df = dfs[code]
        dates = list(df["trade_date"])
        i = dates.index(target)
        sub = df.iloc[i - 4 : i + 2][
            ["trade_date", "close", "dif", "dea", "k", "d", "j", "macd_gc", "kdj_gc"]
        ]
        print(sub.to_string(index=False))
    elif cmd == "addtest":
        # 信号后确认条件分析: 哪些"加仓确认"真正有效
        data = load_sector_data()
        for label, start, end in [("IS 2022-2024", "20220101", "20241231"), ("OOS 2025-2026", "20250101", "20261231")]:
            rows = []
            for sector, dfs in data.items():
                if sector == "能源":
                    continue
                for code, df in dfs.items():
                    sjc = (df["macd_gc"].rolling(3).sum() >= 1) & (df["kdj_gc"].rolling(3).sum() >= 1)
                    idx = sjc.values.nonzero()[0]
                    last = -10**9
                    for s in idx:
                        if s <= last + 5 or s < 60 or s >= len(df) - 2:
                            continue
                        last = s
                        if df["trade_date"].iloc[s] < start or df["trade_date"].iloc[s] > end:
                            continue
                        # 标准退出: 双死叉清仓（不加分批, 便于比较）
                        ep = df["open"].iloc[s + 1]
                        ret = None
                        for i in range(s + 1, len(df)):
                            if df["macd_dc"].iloc[i] and df["kdj_dc"].iloc[i]:
                                ret = df["open"].iloc[min(i + 1, len(df) - 1)] / ep - 1
                                break
                        if ret is None:
                            ret = df["close"].iloc[-1] / ep - 1
                        # 确认窗口: 信号日 ~ 信号后5日
                        win = df.iloc[s : min(s + 6, len(df))]
                        entry_close = df["close"].iloc[s]
                        rows.append(
                            {
                                "ret": ret - 0.002,
                                "j": df["j"].iloc[s],
                                "站上ma20": bool((win["close"] > win["ma20"]).any()),
                                "上穿ma20": bool(
                                    ((win["close"] > win["ma20"]) & (win["close"].shift(1) <= win["ma20"].shift(1)).values).any()
                                ),
                                "站上ma60": bool((win["close"] > win["ma60"]).any()),
                                "放量涨": bool(((win["vol"] >= 1.5 * win["vol_ma3"]) & (win["pct"] > 0)).any()),
                                "创新高20": bool((win["close"] >= win["high20"]).any()),
                                "黄柱3天": bool(win["ta_up"].rolling(3).sum().max() >= 3),
                                "J上50": bool((win["j"] > 50).any()),
                                "信号日在60日线上": bool(entry_close > df["ma60"].iloc[s]),
                            }
                        )
            d = pd.DataFrame(rows)
            print(f"== {label}  共{len(d)}个信号(三板块) ==")
            base_wr = (d["ret"] > 0).mean() * 100
            base_avg = d["ret"].mean() * 100
            print(f"  全部信号基准: {base_wr:.1f}%胜 均{base_avg:+.2f}%")
            for col in ["站上ma20", "上穿ma20", "站上ma60", "放量涨", "创新高20", "黄柱3天", "J上50", "信号日在60日线上"]:
                yes, no = d[d[col]], d[~d[col]]
                if len(yes) < 15:
                    continue
                print(
                    f"  {col:12s} 有:{len(yes):4d}笔 {(yes['ret'] > 0).mean() * 100:5.1f}%胜 均{yes['ret'].mean() * 100:+6.2f}%   "
                    f"无:{len(no):4d}笔 {(no['ret'] > 0).mean() * 100:5.1f}%胜 均{no['ret'].mean() * 100:+6.2f}%"
                )
            for label2, cond in [
                ("上穿ma20+放量", d["上穿ma20"] & d["放量涨"]),
                ("创新高20+放量", d["创新高20"] & d["放量涨"]),
                ("黄柱3天+上穿ma20", d["黄柱3天"] & d["上穿ma20"]),
            ]:
                yes = d[cond]
                if len(yes) < 15:
                    continue
                print(
                    f"  {label2:16s} 有:{len(yes):4d}笔 {(yes['ret'] > 0).mean() * 100:5.1f}%胜 均{yes['ret'].mean() * 100:+6.2f}%"
                )
    elif cmd == "mdtest":
        # 双金叉信号按确认条件组合分桶（美的模式验证）
        data = load_sector_data()
        only_jd = "--jd" in sys.argv
        rows = []
        for sector, dfs in data.items():
            if sector != "家电":
                continue
            for code, df in dfs.items():
                if only_jd and code != "000333.SZ":
                    continue
                sjc = (df["macd_gc"].rolling(3).sum() >= 1) & (df["kdj_gc"].rolling(3).sum() >= 1)
                idx = sjc.values.nonzero()[0]
                last = -10**9
                for s in idx:
                    if s <= last + 5 or s < 60 or s >= len(df) - 21:
                        continue
                    last = s
                    vol_ok = bool(df["vol"].iloc[s] >= 1.5 * df["vol_ma3"].iloc[s] and df["pct"].iloc[s] > 0)
                    boll_ok = bool(
                        df["close"].iloc[s] > df["boll_mid"].iloc[s]
                        and df["low"].iloc[s - 2 : s + 1].min() <= df["boll_mid"].iloc[s - 2 : s + 1].max()
                    )
                    rsi_ok = bool(df["rsi6"].iloc[s] > 50)
                    ep = df["open"].iloc[s + 1]
                    ret = None
                    for i in range(s + 1, len(df)):
                        if df["macd_dc"].iloc[i] and df["kdj_dc"].iloc[i]:
                            ret = df["open"].iloc[min(i + 1, len(df) - 1)] / ep - 1
                            break
                    if ret is None:
                        ret = df["close"].iloc[-1] / ep - 1
                    rows.append(
                        {
                            "code": code,
                            "date": df["trade_date"].iloc[s],
                            "ret": ret - 0.002,
                            "r5": df["close"].iloc[s + 5] / ep - 1,
                            "r10": df["close"].iloc[s + 10] / ep - 1,
                            "r20": df["close"].iloc[s + 20] / ep - 1,
                            "放量": vol_ok,
                            "boll": boll_ok,
                            "rsi": rsi_ok,
                        }
                    )
        d = pd.DataFrame(rows)
        print(f"样本: {len(d)} 个双金叉信号 ({'仅美的' if only_jd else '家电板块'})")

        def show(label, sub):
            if len(sub) < 5:
                print(f"  {label:22s} {len(sub):3d}笔 样本太少")
                return
            print(
                f"  {label:22s} {len(sub):3d}笔 胜率{(sub['ret'] > 0).mean() * 100:5.1f}% "
                f"均{sub['ret'].mean() * 100:+6.2f}% | 5日{sub['r5'].mean() * 100:+5.2f}% "
                f"10日{sub['r10'].mean() * 100:+5.2f}% 20日{sub['r20'].mean() * 100:+5.2f}%"
            )

        show("全部信号", d)
        show("放量+BOLL+RSI全满足", d[d["放量"] & d["boll"] & d["rsi"]])
        show("放量+BOLL", d[d["放量"] & d["boll"]])
        show("放量+RSI", d[d["放量"] & d["rsi"]])
        show("仅放量", d[d["放量"] & ~d["boll"] & ~d["rsi"]])
        show("仅BOLL+RSI(不放量)", d[~d["放量"] & d["boll"] & d["rsi"]])
        show("三个都不满足", d[~d["放量"] & ~d["boll"] & ~d["rsi"]])
        show("放量(不论其他)", d[d["放量"]])
        show("不放量(不论其他)", d[~d["放量"]])
        if only_jd:
            print("")
            print("美的全部信号明细:")
            for _, rr in d.iterrows():
                print(
                    f"  {rr['date']} 放量={int(rr['放量'])} BOLL={int(rr['boll'])} RSI={int(rr['rsi'])}"
                    f" -> 双死叉退出{rr['ret'] * 100:+6.1f}%  20日{rr['r20'] * 100:+5.1f}%"
                )
