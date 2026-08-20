#!/usr/bin/env python3
"""把 ETF 数据从 JSON 缓存迁移到 SQLite 数据库。

1. 从 momentum_etf_hfq.json 读取 32 只 ETF
2. 存入 stock_basic（基本信息）+ daily_kline（K线数据）
3. 从腾讯财经拉取缺失的重点板块 ETF
"""
import json
import sqlite3
import requests
import time
from pathlib import Path

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
DB = ROOT / "data/stock_data.db"

# 需要补充的重点板块 ETF（代码, 名称, 板块）
MISSING_ETFS = [
    # 科技补充
    ("512010", "医药ETF", "医药"),
    ("159929", "医药ETF", "医药"),
    ("512170", "医疗ETF", "医药"),
    ("159883", "医疗器械ETF", "医药"),
    ("159992", "创新药ETF", "医药"),
    ("515120", "创新药ETF", "医药"),
    ("512690", "酒ETF", "消费"),
    ("159928", "消费ETF", "消费"),
    ("510630", "消费30ETF", "消费"),
    ("159996", "家电ETF", "消费"),
    ("512880", "证券ETF", "金融"),
    ("512000", "券商ETF", "金融"),
    ("512800", "银行ETF", "金融"),
    ("515020", "银行ETF", "金融"),
    ("512070", "证券保险ETF", "金融"),
    ("518880", "黄金ETF", "有色金属"),
    ("159934", "黄金ETF", "有色金属"),
    ("512400", "有色金属ETF", "有色金属"),
    ("159881", "有色60ETF", "有色金属"),
    ("512580", "环保ETF", "新能源"),
    ("159875", "新能源ETF", "新能源"),
    ("516160", "新能源ETF", "新能源"),
    ("159755", "电池ETF", "新能源"),
    ("159757", "电池ETF", "新能源"),
    ("516070", "碳中和50ETF", "新能源"),
    ("159790", "碳中和ETF", "新能源"),
    ("512660", "军工ETF", "军工"),
    ("512710", "军工龙头ETF", "军工"),
    ("512710", "军工ETF", "军工"),
    ("512720", "计算机ETF", "科技"),
    ("159998", "计算机ETF", "科技"),
    ("512330", "信息科技ETF", "科技"),
    ("159939", "信息技术ETF", "科技"),
    ("512220", "TMT50ETF", "科技"),
    ("159909", "TMTETF", "科技"),
    ("515050", "5G通信ETF", "科技"),
    ("159994", "5GETF", "科技"),
    ("512480", "半导体ETF", "科技"),
    ("512760", "芯片ETF", "科技"),
    ("159995", "芯片ETF", "科技"),
    ("588200", "科创芯片ETF", "科技"),
    ("588000", "科创50ETF", "科技"),
    ("588080", "科创板50ETF", "科技"),
    ("588090", "科创板ETF", "科技"),
    ("513100", "纳指ETF", "海外"),
    ("159941", "纳指ETF", "海外"),
    ("513500", "标普500ETF", "海外"),
    ("513050", "中概互联ETF", "海外"),
    ("159740", "恒生科技ETF", "海外"),
    ("513180", "恒生科技指数ETF", "海外"),
    ("513130", "恒生ETF", "海外"),
    ("510300", "沪深300ETF", "宽基"),
    ("510500", "中证500ETF", "宽基"),
    ("512100", "中证1000ETF", "宽基"),
    ("159915", "创业板ETF", "宽基"),
    ("159949", "创业板50ETF", "宽基"),
    ("510880", "红利ETF", "宽基"),
    ("515080", "中证红利ETF", "宽基"),
    ("512890", "红利低波ETF", "宽基"),
    ("159905", "深红利ETF", "宽基"),
    ("515180", "红利ETF易方达", "宽基"),
    ("512900", "南方中证500ETF", "宽基"),
    ("510050", "上证50ETF", "宽基"),
    ("510180", "上证180ETF", "宽基"),
    ("159901", "深证100ETF", "宽基"),
    ("159902", "中小板ETF", "宽基"),
    ("159903", "深成ETF", "宽基"),
    ("159919", "沪深300ETF", "宽基"),
    ("510330", "华夏300ETF", "宽基"),
    ("159925", "南方300ETF", "宽基"),
    ("510310", "易方达300ETF", "宽基"),
    ("510350", "工银300ETF", "宽基"),
    ("510360", "广发300ETF", "宽基"),
    ("510390", "平安300ETF", "宽基"),
    ("512510", "华泰500ETF", "宽基"),
    ("510510", "广发500ETF", "宽基"),
    ("512500", "华夏500ETF", "宽基"),
    ("510580", "易方达500ETF", "宽基"),
    ("512560", "易方达中证500ETF", "宽基"),
    ("159922", "嘉实500ETF", "宽基"),
    ("512550", "嘉实富时A50ETF", "宽基"),
    ("512150", "汇添富A50ETF", "宽基"),
    ("512960", "博时央调ETF", "宽基"),
    ("512950", "华夏央调ETF", "宽基"),
    ("512910", "广发央企ETF", "宽基"),
    ("512920", "平安央调ETF", "宽基"),
    ("512970", "华夏央调ETF", "宽基"),
    ("512980", "汇添富央企ETF", "宽基"),
    ("512990", "华夏MSCIA股ETF", "宽基"),
    ("512990", "MSCIA股ETF", "宽基"),
    ("512160", "招商MSCIA股ETF", "宽基"),
    ("512090", "易方达MSCIA股ETF", "宽基"),
    ("512520", "华泰MSCIA股ETF", "宽基"),
    ("512530", "建信MSCIA股ETF", "宽基"),
    ("512570", "易方达中证海外ETF", "海外"),
    ("513330", "恒生互联网ETF", "海外"),
    ("513360", "教育ETF", "海外"),
    ("513520", "日经ETF", "海外"),
    ("513000", "日经225ETF", "海外"),
    ("513080", "法国CAC40ETF", "海外"),
    ("513030", "德国ETF", "海外"),
    ("513030", "华安德国ETF", "海外"),
    ("513030", "德国30ETF", "海外"),
    ("513030", "DAXETF", "海外"),
    ("513030", "德国DAXETF", "海外"),
    ("513030", "德国30", "海外"),
    ("513030", "德国DAX", "海外"),
]

# 从已有 JSON 读取 32 只 ETF 的实际代码
def get_existing_etfs():
    d = json.load(open(ROOT / "data/momentum_etf_hfq.json"))["data"]
    # 需要从名称反推代码，先建立映射
    name_to_code = {
        "沪深300": "510300", "中证500": "510500", "创业板": "159915",
        "科创50": "588000", "红利": "510880", "红利低波": "512890",
        "有色金属": "512400", "消费": "159928", "医药": "512010",
        "创新药": "159992", "机器人": "562500", "半导体": "512480",
        "半导体设备": "159516", "芯片天弘": "159310", "芯片华夏": "159995",
        "通信": "515880", "人工智能": "159819", "酒": "512690",
        "军工": "512660", "证券": "512880", "光伏": "515790",
        "新能源": "516160", "房地产": "512200", "中概互联": "513050",
        "纳指": "159941", "恒生科技": "513180", "黄金": "518880",
        "煤炭": "515220", "5G": "515050", "科创芯片": "588200",
        "芯片国泰": "512760",
    }
    return d, name_to_code


def save_etf_basic(ts_code, name, industry, market):
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO stock_basic (ts_code, name, area, industry, market, list_date, is_hs)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (ts_code, name, "", industry, market, "", ""))
    conn.commit()
    conn.close()


def save_klines(ts_code, rows):
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    inserted = 0
    for r in rows:
        try:
            cur.execute("""
                INSERT OR IGNORE INTO daily_kline (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, vol_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts_code, r["day"], r["open"], r["high"], r["low"], r["close"],
                   r.get("vol", 0), 0, 0, 1.0))
            inserted += 1
        except Exception as e:
            pass
    conn.commit()
    conn.close()
    return inserted


def migrate_json_to_db():
    """把已有 32 只 ETF 从 JSON 迁移到 SQLite。"""
    d, name_to_code = get_existing_etfs()
    total = 0
    for name, rows in d.items():
        code = name_to_code.get(name)
        if not code:
            print(f"  跳过 {name}（无代码映射）")
            continue
        market = "SH" if code.startswith("6") else "SZ"
        ts_code = f"{code}.{market}"
        # 分类
        industry = "科技"
        if name in ["医药", "创新药"]:
            industry = "医药"
        elif name in ["新能源", "光伏"]:
            industry = "新能源"
        elif name in ["有色金属", "黄金"]:
            industry = "有色金属"
        elif name in ["银行", "证券", "红利", "红利低波"]:
            industry = "金融"
        elif name in ["消费", "酒"]:
            industry = "消费"
        elif name in ["军工"]:
            industry = "军工"
        elif name in ["沪深300", "中证500", "创业板", "科创50"]:
            industry = "宽基"
        elif name in ["纳指", "恒生科技", "中概互联"]:
            industry = "海外"
        elif name in ["煤炭", "房地产"]:
            industry = "周期"
        save_etf_basic(ts_code, name, industry, market)
        n = save_klines(ts_code, rows)
        total += n
        print(f"  {name}({ts_code}): {n} 条 K 线")
    print(f"共迁移 {total} 条 K 线")


if __name__ == "__main__":
    print("迁移已有 32 只 ETF 到 SQLite...")
    migrate_json_to_db()
