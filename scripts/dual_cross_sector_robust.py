#!/usr/bin/env python3
"""检验：双金叉变体在不同属性板块上是否都有效？
分为 科技成长 / 防御弱势 / 消费白马 三篮，分别等权回测：
  持有 / 纯双金叉 / 死叉破中轨才清 / 死叉破中轨+创业板MA120总开关
数据：32-ETF 池（2018~2026 后复权），口径同前。
"""
import json
import sys
from pathlib import Path

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
sys.path.insert(0, str(ROOT / "scripts"))
import dual_cross_addons as M  # 复用 precompute / run_dual_cross / metrics

d = json.load(open(ROOT / "data/momentum_etf_hfq.json"))["data"]
idx = {nm: {r["day"]: r for r in d[nm]} for nm in d}
common = sorted(set().union(*[set(idx[nm].keys()) for nm in d]))
common = [dd for dd in common if dd >= "2019-01-01"]

TECH = ['创业板', '科创50', '机器人', '半导体', '半导体设备', '芯片天弘', '芯片华夏',
        '通信', '人工智能', '科创芯片', '芯片国泰', '5G', '纳指', '恒生科技', '新能源', '光伏']
DEF = ['黄金', '煤炭', '红利', '红利低波', '银行', '证券', '房地产']
CON = ['消费', '酒', '医药', '创新药', '军工', '中概互联', '有色金属']


def cyb_regime():
    cyb = {k: v for k, v in json.load(open(ROOT / "data/cyb_index.json"))["data"].items()}
    cd = sorted(cyb.keys())
    ma120 = {}
    for i, dt in enumerate(cd):
        if i >= 120:
            ma120[dt] = sum(cyb[cd[j]] for j in range(i - 119, i + 1)) / 120
    return [cyb.get(dd, 0) >= ma120.get(dd, 1e9) for dd in common]


_cyb = None
_cyb_ma120 = None
def cyb():
    global _cyb
    if _cyb is None:
        _cyb = {k: v for k, v in json.load(open(ROOT / "data/cyb_index.json"))["data"].items()}
    return _cyb
def cyb_ma120():
    global _cyb_ma120
    if _cyb_ma120 is None:
        c = cyb()
        cd = sorted(c.keys())
        m = {}
        for i, dt in enumerate(cd):
            if i >= 120:
                m[dt] = sum(c[cd[j]] for j in range(i - 119, i + 1)) / 120
        _cyb_ma120 = m
    return _cyb_ma120


def basket(names, label):
    bn = sorted(set.intersection(*[set(idx[nm].keys()) for nm in names]))
    bn = [dd for dd in bn if dd >= "2019-01-01"]
    print(f"\n=== {label} 篮（{len(names)}只：{','.join(names)}）===")
    print(f"窗口 {bn[0]}~{bn[-1]} {len(bn)}日")
    print(f"{'策略':<26}{'累计':>9}{'年化':>9}{'最大回撤':>10}{'夏普':>7}{'波动率':>9}{'在场':>7}")

    def port_run(**kw):
        navs = []
        for nm in names:
            rows = [idx[nm][dd] for dd in bn]
            pc = M.precompute(rows)
            nav, *_ = M.run_dual_cross(pc, **kw)
            navs.append(nav)
        return [sum(navs[j][i] for j in range(len(names))) / len(names) for i in range(len(bn))]

    # 持有
    nav_h = [sum(idx[nm][bn[i]]["close"] / idx[nm][bn[0]]["close"] for nm in names) / len(names) for i in range(len(bn))]
    mh = M.metrics(common, nav_h)
    print(f"{'买入持有':<26}{M.pct(mh['total_return']):>9}{M.pct(mh['annualized_return']):>9}{M.pct(mh['max_drawdown']):>10}{mh['sharpe']:>7.2f}{M.pct(mh['vol']):>9}{'100%':>7}")

    rg = [cyb().get(dd, 0) >= cyb_ma120().get(dd, 1e9) for dd in bn]
    variants = {
        "纯双金叉": {},
        "死叉破中轨才清": {"sell_extra": lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1]},
        "死叉破中轨+MA120开关": {"sell_extra": lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1], "regime": rg},
    }
    for vname, kw in variants.items():
        nav = port_run(**kw)
        m = M.metrics(bn, nav)
        print(f"{vname:<26}{M.pct(m['total_return']):>9}{M.pct(m['annualized_return']):>9}{M.pct(m['max_drawdown']):>10}{m['sharpe']:>7.2f}{M.pct(m['vol']):>9}{'n/a':>7}")


if __name__ == "__main__":
    basket(TECH, "科技成长")
    basket(DEF, "防御/弱势")
    basket(CON, "消费白马")
