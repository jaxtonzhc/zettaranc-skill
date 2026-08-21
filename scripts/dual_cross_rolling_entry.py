#!/usr/bin/env python3
"""滚动起始日分析：在不同时间点"随机"买入，策略和持有哪个赚到钱？

核心问题：从最早低位起步→持有永远暴赚。但如果在牛市顶/熊市中/震荡市随机入场呢？

方法：每隔 N 个交易日取一个起点，从该点开始同时跑：
  1) 买入持有（一直不动）
  2) 纯双金叉（原版）
  3) 死叉+破中轨才清（增强版）
每种起点跑到 2026-08-19，统计：收益中位数、赚钱概率、最大回撤中位数。

分板块篮：科技 / 防御 / 消费白马 / 全32只
"""
import json
import statistics
from pathlib import Path

ROOT = Path("/Users/krystal/Projects/zhc_projects/zettaranc-skill")
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import dual_cross_addons as M

d = json.load(open(ROOT / "data/momentum_etf_hfq.json"))["data"]
idx = {nm: {r["day"]: r for r in d[nm]} for nm in d}

# 创业板指 MA120
_cyb = {k: v for k, v in json.load(open(ROOT / "data/cyb_index.json"))["data"].items()}
_cyb_cd = sorted(_cyb.keys())
_cyb_ma120 = {}
for i, dt in enumerate(_cyb_cd):
    if i >= 120:
        _cyb_ma120[dt] = sum(_cyb[_cyb_cd[j]] for j in range(i - 119, i + 1)) / 120


def cyb_regime_for_dates(dates):
    return [_cyb.get(dd, 0) >= _cyb_ma120.get(dd, 1e9) for dd in dates]


TECH = ['创业板', '科创50', '机器人', '半导体', '芯片华夏', '通信',
        '人工智能', '科创芯片', '5G', '纳指', '恒生科技']
DEF = ['黄金', '煤炭', '红利', '红利低波', '银行', '证券']
CON = ['消费', '酒', '医药', '创新药', '军工', '有色金属']
ALL_32 = list(d.keys())


def analyze(basket_names, label, step=60):
    # 全局公共交易日
    common = sorted(set.intersection(*[set(idx[nm].keys()) for nm in basket_names]))
    if len(common) < 300:
        print(f"\n{label}：公共日不足 {len(common)}，跳过")
        return
    end = common[-1]
    # 起始点：从第 200 天起，每隔 step 天取一个起点（保证至少 200 日历史可算指标）
    starts = [common[i] for i in range(199, len(common) - 1, step)]

    results = {"hold": [], "dc": [], "dc_gm": []}  # 收益率列表
    mdd_hold = []
    mdd_dc = []
    mdd_dc_gm = []

    for si, s_date in enumerate(starts):
        si_idx = common.index(s_date)
        sub_dates = common[si_idx:]
        if len(sub_dates) < 30:
            continue

        # --- 买入持有 ---
        nav_h = [sum(idx[nm][sub_dates[i]]["close"] / idx[nm][sub_dates[0]]["close"]
                     for nm in basket_names) / len(basket_names) for i in range(len(sub_dates))]
        ret_h = nav_h[-1] - 1
        pk = max(nav_h); mdd_h = min(v / pk - 1 for v in nav_h)
        results["hold"].append(ret_h)
        mdd_hold.append(mdd_h)

        # --- 纯双金叉 ---
        nav_dc = []
        for nm in basket_names:
            rows = [idx[nm][dd] for dd in sub_dates]
            if len(rows) < 50:
                continue
            pc = M.precompute(rows)
            n, *_ = M.run_dual_cross(pc)
            # normalize to 1.0
            nav_dc.append([v / n[0] for v in n])
        if nav_dc:
            port_dc = [sum(nv[i] for nv in nav_dc) / len(nav_dc) for i in range(len(sub_dates))]
            results["dc"].append(port_dc[-1] - 1)
            pk = max(port_dc); mdd_dc.append(min(v / pk - 1 for v in port_dc))

        # --- 死叉破中轨才清 ---
        nav_gm = []
        for nm in basket_names:
            rows = [idx[nm][dd] for dd in sub_dates]
            if len(rows) < 50:
                continue
            pc = M.precompute(rows)
            n, *_ = M.run_dual_cross(pc, sell_extra=lambda i, pc: pc["closes"][i - 1] < pc["mid"][i - 1])
            nav_gm.append([v / n[0] for v in n])
        if nav_gm:
            port_gm = [sum(nv[i] for nv in nav_gm) / len(nav_gm) for i in range(len(sub_dates))]
            results["dc_gm"].append(port_gm[-1] - 1)
            pk = max(port_gm); mdd_dc_gm.append(min(v / pk - 1 for v in port_gm))

    print(f"\n{'='*70}")
    print(f"{label}（{len(basket_names)}只，{len(starts)} 个起始点，每个起点跑到 {end}）")
    print(f"{'='*70}")
    header = f"{'':20}{'中位收益':>10}{'平均收益':>10}{'赚钱概率':>10}{'中位回撤':>10}"
    print(header)
    for key, name, mdd_list in [
        ("hold", "买入持有", mdd_hold),
        ("dc", "纯双金叉", mdd_dc),
        ("dc_gm", "死叉破中轨", mdd_dc_gm),
    ]:
        vals = results[key]
        if not vals:
            continue
        med_r = statistics.median(vals)
        avg_r = statistics.mean(vals)
        win_pct = sum(1 for v in vals if v > 0) / len(vals)
        med_mdd = statistics.median(mdd_list) if mdd_list else 0
        print(f"{name:20}{med_r*100:>9.1f}%{avg_r*100:>9.1f}%{win_pct*100:>9.1f}%{med_mdd*100:>9.1f}%")
    print(f"\n全部起始点收益明细：")
    for key, name in [("hold", "持有"), ("dc", "双金叉"), ("dc_gm", "破中轨")]:
        vals = results[key]
        if vals:
            print(f"  {name}: {[f'{v*100:.0f}%' for v in vals[:10]]}{'...' if len(vals) > 10 else ''}")


if __name__ == "__main__":
    print("滚动起始日分析：从不同时间点入场，策略 vs 持有")
    print("每 60 个交易日取一个起点，跑到 2026-08-19")
    analyze(TECH, "科技成长", step=60)
    analyze(DEF, "防御/弱势", step=60)
    analyze(CON, "消费白马", step=60)
    analyze(ALL_32, "全部32只等权", step=60)
