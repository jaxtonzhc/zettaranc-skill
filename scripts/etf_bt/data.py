"""数据加载层：全部从 SQLite (data/stock_data.db) 读取 ETF/指数 K 线。"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "stock_data.db"

# 池定义: 名字 -> stock_basic.industry
POOL_INDUSTRY = {
    "tech_db": "科技",
    "tech": "科技",
    "defensive": None,   # 特殊: 黄金/煤炭/红利/银行/证券
    "consumer": None,    # 特殊: 消费/酒/医药/创新药
    "med": "医药",
    "newenergy": "新能源",
    "metal": "有色金属",
    "fin": "金融",
    "broad": "宽基",
    "oversea": "海外",
}
# 行业池里要剔除的海外标的（A股科技池不含纳指/恒生）
TECH_EXCLUDE = ("纳指", "恒生")
# 非单一行业池的手工名单（用名字模糊匹配）
DEFENSIVE_NAMES = ("黄金", "煤炭", "红利", "银行", "证券")
CONSUMER_NAMES = ("消费", "酒", "医药", "创新药")


def _fetch(code, start, end):
    conn = sqlite3.connect(str(DB_PATH))
    q = "SELECT trade_date, open, high, low, close, vol FROM daily_kline WHERE ts_code=? AND trade_date>=?"
    params = [code, start]
    if end:
        q += " AND trade_date<=?"
        params.append(end)
    q += " ORDER BY trade_date"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [{"day": r[0], "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "vol": r[5]} for r in rows]


def _by_industry(industry, start, end, exclude=()):
    conn = sqlite3.connect(str(DB_PATH))
    codes = conn.execute(
        "SELECT ts_code, name FROM stock_basic WHERE industry=? ORDER BY ts_code",
        (industry,)).fetchall()
    conn.close()
    out = {}
    for code, name in codes:
        if any(x in name for x in exclude):
            continue
        rows = _fetch(code, start, end)
        if len(rows) >= 60:
            out[name] = rows
    return out


def _by_names(names, start, end):
    conn = sqlite3.connect(str(DB_PATH))
    allb = conn.execute("SELECT ts_code, name FROM stock_basic WHERE industry IS NOT NULL").fetchall()
    conn.close()
    out = {}
    for code, name in allb:
        if any(x in name for x in names):
            rows = _fetch(code, start, end)
            if len(rows) >= 60:
                out[name] = rows
    return out


def load_etf_pool(name, start="2019-01-01", end=None):
    """加载 ETF 池。全部走 SQLite。"""
    if name == "focus4":
        return _by_names(("半导体设备", "科创芯片", "芯片华夏", "纳指"), start, end)
    if name == "defensive":
        return _by_names(DEFENSIVE_NAMES, start, end)
    if name == "consumer":
        return _by_names(CONSUMER_NAMES, start, end)
    ind = POOL_INDUSTRY.get(name)
    if ind:
        excl = TECH_EXCLUDE if name in ("tech", "tech_db") else ()
        return _by_industry(ind, start, end, excl)
    # all: 全部非指数 ETF
    conn = sqlite3.connect(str(DB_PATH))
    allb = conn.execute(
        "SELECT ts_code, name FROM stock_basic WHERE industry IS NOT NULL AND industry != '指数'"
    ).fetchall()
    conn.close()
    out = {}
    for code, nm in allb:
        rows = _fetch(code, start, end)
        if len(rows) >= 60:
            out[nm] = rows
    return out


def load_etf_pool_db(industry="科技", start="2019-01-01", end=None):
    """兼容旧接口：等价于 _by_industry（剔除海外）。"""
    return _by_industry(industry, start, end, TECH_EXCLUDE)


# ---------- 指数（供 regime 开关用） ----------
INDEX_CODE = {"cyb": "399006.SZ", "sh": "000001.SH",
              "hs300": "000300.SH", "szcz": "399001.SZ"}


def load_index(name="cyb"):
    """加载指数收盘序列 {date: close}，来自 DB。"""
    code = INDEX_CODE.get(name, name)
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT trade_date, close FROM daily_kline WHERE ts_code=? ORDER BY trade_date",
        (code,)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def ma_of_index(index_data, period=120):
    cd = sorted(index_data.keys())
    out = {}
    for i, dt in enumerate(cd):
        if i >= period - 1:
            out[dt] = sum(index_data[cd[j]] for j in range(i - period + 1, i + 1)) / period
    return out


def common_dates(etf_dict, min_date="2019-01-01"):
    common = None
    for rows in etf_dict.values():
        ds = set(r["day"] for r in rows)
        common = ds if common is None else common & ds
    return sorted(dd for dd in common if dd >= min_date)


def make_index_lookup(etf_dict, dates):
    return {nm: {r["day"]: r for r in rows} for nm, rows in etf_dict.items()}
