#!/usr/bin/env python3
import json
import requests
import sqlite3
import time
from pathlib import Path
from collections import Counter

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
DB = ROOT / "data/stock_data.db"
URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"

def fetch_board(board_code, board_name):
    etfs = []
    page = 1
    while True:
        params = {
            "board_code": board_code,
            "sort_type": "PriceRatio",
            "direct": "down",
            "offset": (page - 1) * 200,
            "count": 200,
        }
        try:
            r = requests.get(URL, params=params, timeout=30,
                           headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()
            if data.get("code") != 0:
                print(f"  {board_name} 接口错误: {data.get('msg')}")
                break
            rank_list = data.get("data", {}).get("rank_list", [])
            if not rank_list:
                break
            for item in rank_list:
                code = item.get("code", "")
                name = item.get("name", "")
                if code and name:
                    market = "SH" if code.startswith("6") else "SZ"
                    ts_code = f"{code}.{market}"
                    etfs.append({"ts_code": ts_code, "code": code, "name": name, "market": market})
            print(f"  {board_name} 第{page}页: {len(rank_list)} 只, 累计 {len(etfs)}")
            if len(rank_list) < 200:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  {board_name} 请求失败: {e}")
            break
    return etfs

def classify_etf(name):
    name = name.upper()
    if any(k in name for k in ["半导体", "芯片", "集成电路", "科创", "5G", "通信", "人工智能", "AI", "软件", "计算机", "电子", "信息技术", "互联网", "数字经济", "云计算", "大数据", "机器人", "智能制造", "高端制造", "高端装备"]):
        return "科技"
    if any(k in name for k in ["医药", "医疗", "创新药", "生物医药", "中药", "疫苗", "医疗器械", "医疗服务"]):
        return "医药"
    if any(k in name for k in ["新能源", "光伏", "风电", "储能", "电池", "锂电", "新能源汽车", "碳中和"]):
        return "新能源"
    if any(k in name for k in ["有色", "黄金", "白银", "铜", "铝", "稀土", "矿业"]):
        return "有色金属"
    if any(k in name for k in ["银行", "证券", "保险", "金融", "券商", "非银"]):
        return "金融"
    if any(k in name for k in ["消费", "食品饮料", "白酒", "酒", "家电", "农业", "养殖", "畜牧"]):
        return "消费"
    if any(k in name for k in ["军工", "国防", "航天", "航空"]):
        return "军工"
    if any(k in name for k in ["300", "500", "1000", "2000", "创业板", "上证", "深证", "中证", "A50", "红利", "央企", "国企"]):
        return "宽基"
    if any(k in name for k in ["纳指", "纳斯达克", "标普", "恒生", "港股", "中概", "日经", "德国", "法国", "亚太"]):
        return "海外"
    return "其他"

def save_to_db(etfs):
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    inserted = 0
    for e in etfs:
        industry = classify_etf(e["name"])
        try:
            cur.execute("""
                INSERT OR IGNORE INTO stock_basic (ts_code, name, area, industry, market, list_date, is_hs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (e["ts_code"], e["name"], "", industry, e["market"], "", ""))
            inserted += 1
        except Exception as ex:
            print(f"  插入失败 {e['ts_code']}: {ex}")
    conn.commit()
    conn.close()
    print(f"  已存入 {inserted} 只 ETF 到 stock_basic")

def main():
    print("从腾讯财经拉取全量 ETF 列表...")
    all_etfs = []
    all_etfs.extend(fetch_board("aH30201", "深市ETF"))
    all_etfs.extend(fetch_board("aH30202", "沪市ETF"))
    seen = set()
    unique = []
    for e in all_etfs:
        if e["ts_code"] not in seen:
            seen.add(e["ts_code"])
            unique.append(e)
    print(f"共拉取 {len(unique)} 只 ETF")
    cats = Counter(classify_etf(e["name"]) for e in unique)
    print("分类统计:")
    for cat, cnt in cats.most_common():
        print(f"  {cat}: {cnt}")
    save_to_db(unique)
    backup = ROOT / "data" / "etf_list_full.json"
    with open(backup, "w") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    print(f"备份已保存: {backup}")

if __name__ == "__main__":
    main()
