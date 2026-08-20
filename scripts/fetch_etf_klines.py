#!/usr/bin/env python3
"""从腾讯财经拉取 ETF 日K 线数据，存入 SQLite。

接口：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
参数：param=sh510300,day,2018-01-01,2026-08-19,640,qfq
"""
import json
import sqlite3
import requests
import time
from pathlib import Path

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
DB = ROOT / "data/stock_data.db"
TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def fetch_kline(code, market, start="2018-01-01", end="2026-08-19", days=2000):
    symbol = f"{market.lower()}{code}"
    params = {"param": f"{symbol},day,{start},{end},{days},hfq"}
    try:
        r = requests.get(TENCENT_URL, params=params, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        if data.get("code") != 0:
            return None
        klines = data.get("data", {}).get(symbol, {}).get("hfqday", [])
        if not klines:
            klines = data.get("data", {}).get(symbol, {}).get("day", [])
        return klines
    except Exception as e:
        print(f"    请求失败: {e}")
        return None


def save_to_db(code, name, market, klines):
    ts_code = f"{code}.{market}"
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    # 基本信息
    cur.execute("""
        INSERT OR IGNORE INTO stock_basic (ts_code, name, area, industry, market, list_date, is_hs)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (ts_code, name, "", "金融" if "银行" in name else "其他", market, "", ""))
    # K线
    inserted = 0
    for k in klines:
        if len(k) < 6:
            continue
        day = k[0]
        try:
            cur.execute("""
                INSERT OR IGNORE INTO daily_kline (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, vol_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts_code, day, float(k[1]), float(k[3]), float(k[4]), float(k[2]),
                   float(k[5]) if len(k) > 5 else 0, 0, 0, 1.0))
            inserted += 1
        except:
            pass
    conn.commit()
    conn.close()
    return inserted


# 需要拉取的 ETF 列表（代码, 名称, 交易所）
ETFS_TO_FETCH = [
    # 银行
    ("512800", "银行ETF", "SH"),
    ("515020", "银行ETF基金", "SH"),
    ("512700", "银行ETF龙头", "SH"),
    ("159887", "银行ETF", "SZ"),
    # 补充科技
    ("512720", "计算机ETF", "SH"),
    ("159998", "计算机ETF", "SZ"),
    ("512330", "信息科技ETF", "SH"),
    ("512220", "TMT50ETF", "SH"),
    ("512480", "半导体ETF", "SH"),
    ("512760", "芯片ETF国泰", "SH"),
    ("159995", "芯片ETF华夏", "SZ"),
    ("588200", "科创芯片ETF", "SH"),
    ("588000", "科创50ETF", "SH"),
    ("588080", "科创板50ETF", "SH"),
    ("588090", "科创板ETF", "SH"),
    ("515050", "5G通信ETF", "SH"),
    ("159994", "5GETF", "SZ"),
    ("515880", "通信ETF", "SH"),
    ("159819", "人工智能ETF", "SZ"),
    ("159516", "半导体设备ETF", "SZ"),
    ("562500", "机器人ETF", "SH"),
    ("159770", "机器人ETF", "SZ"),
    ("159310", "芯片ETF天弘", "SZ"),
    # 医药
    ("512010", "医药ETF", "SH"),
    ("159929", "医药ETF", "SZ"),
    ("512170", "医疗ETF", "SH"),
    ("159883", "医疗器械ETF", "SZ"),
    ("159992", "创新药ETF", "SZ"),
    ("515120", "创新药ETF", "SH"),
    # 消费
    ("512690", "酒ETF", "SH"),
    ("159928", "消费ETF", "SZ"),
    ("510630", "消费30ETF", "SH"),
    ("159996", "家电ETF", "SZ"),
    # 金融
    ("512880", "证券ETF", "SH"),
    ("512000", "券商ETF", "SH"),
    ("512070", "证券保险ETF", "SH"),
    # 有色金属
    ("518880", "黄金ETF", "SH"),
    ("159934", "黄金ETF", "SZ"),
    ("512400", "有色金属ETF", "SH"),
    ("159881", "有色60ETF", "SZ"),
    # 新能源
    ("512580", "环保ETF", "SH"),
    ("159875", "新能源ETF", "SZ"),
    ("516160", "新能源ETF", "SH"),
    ("159755", "电池ETF", "SZ"),
    ("159757", "电池ETF", "SZ"),
    ("516070", "碳中和50ETF", "SH"),
    ("159790", "碳中和ETF", "SZ"),
    # 军工
    ("512660", "军工ETF", "SH"),
    ("512710", "军工龙头ETF", "SH"),
    # 宽基
    ("510300", "沪深300ETF", "SH"),
    ("510500", "中证500ETF", "SH"),
    ("512100", "中证1000ETF", "SH"),
    ("159915", "创业板ETF", "SZ"),
    ("159949", "创业板50ETF", "SZ"),
    ("510880", "红利ETF", "SH"),
    ("515080", "中证红利ETF", "SH"),
    ("512890", "红利低波ETF", "SH"),
    ("159905", "深红利ETF", "SZ"),
    ("510050", "上证50ETF", "SH"),
    ("510180", "上证180ETF", "SH"),
    ("159901", "深证100ETF", "SZ"),
    ("159902", "中小板ETF", "SZ"),
    ("159903", "深成ETF", "SZ"),
    ("159919", "沪深300ETF", "SZ"),
    # 海外
    ("513100", "纳指ETF国泰", "SH"),
    ("159941", "纳指ETF广发", "SZ"),
    ("513500", "标普500ETF", "SH"),
    ("513050", "中概互联50ETF", "SH"),
    ("159740", "恒生科技ETF", "SZ"),
    ("513180", "恒生科技指数ETF", "SH"),
    ("513130", "恒生ETF", "SH"),
    ("513330", "恒生互联网ETF", "SH"),
    ("513520", "日经ETF", "SH"),
    ("513000", "日经225ETF", "SH"),
    ("513080", "法国CAC40ETF", "SH"),
    ("513030", "德国ETF", "SH"),
    # 周期
    ("512200", "房地产ETF", "SH"),
    ("515220", "煤炭ETF", "SH"),
]


def main():
    print("从腾讯财经拉取 ETF 数据...")
    total = 0
    for code, name, market in ETFS_TO_FETCH:
        ts_code = f"{code}.{market}"
        # 检查是否已有数据
        conn = sqlite3.connect(str(DB))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM daily_kline WHERE ts_code = ?", (ts_code,))
        existing = cur.fetchone()[0]
        conn.close()
        if existing > 100:
            print(f"  {name}({ts_code}): 已有 {existing} 条，跳过")
            continue
        print(f"  {name}({ts_code}): 拉取中...", end=" ")
        klines = fetch_kline(code, market)
        if not klines:
            print("失败")
            continue
        n = save_to_db(code, name, market, klines)
        total += n
        print(f"{n} 条")
        time.sleep(0.3)
    print(f"\n共拉取 {total} 条 K 线")


if __name__ == "__main__":
    main()
