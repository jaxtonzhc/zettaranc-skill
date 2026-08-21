---
name: zhc-trade-reverse-fit-strategy
description: >
  给定任意股票/ETF，先过拟合找理论最优买卖点，再从极值点反推指标规律，
  提炼成 T+1 可执行的通用策略并严格回测。Use when 用户要找波段策略、问怎么买卖最赚、
  从理论最优反推可执行规则、过拟合反推、reverse-fit。不绑定具体标的结论。
---

# 过拟合反推通用策略

给定任意股票/ETF，先用未来函数找理论最优买卖点，再从极值点反推规律与指标，提炼成 T+1 可执行的通用策略并严格回测。本 skill 是流程框架，不绑定任何具体结论。

## 何时使用

- 用户给出一只/一池股票或 ETF，想找出能跑赢持有的波段策略
- 用户问这个票怎么买卖最赚、有什么指标能抓主升浪
- 用户想验证某个技术策略，或从理论最优反推实际可执行规则
- 任何需要先知道天花板在哪、再谈怎么接近它的量化探索

## 铁律

1. T+1 成交：T 日收盘出信号，T+1 开盘成交。禁止当日信号当日成交（未来函数）
2. 手续费：双边万三（0.0003/边）；A 股个股单笔不足 5 元按 5 元
3. 未来函数只用于找理论上限，绝不进入最终策略
4. 样本外验证：随机起点滚动入场 + 留出未参与训练的时段，防过拟合自嗨

## 反向思维五步法

### 1. 锁定标的与数据

- 确认 ETF 还是个股，复权口径必须统一（前后复权价差大会误导价位）
- 数据优先走 SQLite（如 zettaranc-skill 的 data/stock_data.db daily_kline）
- 指标：MACD / KDJ / DMI / BOLL / 量比 / 金叉死叉等

### 2. 过拟合找理论上限（知道未来）

- 峰谷检测：局部极小值次日买、局部极大值次日卖，涨超阈值（如 5%）才成段
- 目的是回答这标的最多能赚多少，给后续策略做参照，不是用来抄
- 可用区间 DP 求 K 笔交易最优分段（K=3/5/8），看天花板
- 记录每段买卖日期、价格、涨幅

### 3. 反推极值点规律（最关键）

把第二步的买卖点，回看当时指标状态并统计占比：

- MACD/KDJ 金叉死叉占比
- 价格相对 MA20/MA60 的位置
- DMI（PDI/MDI）动能、量比、J 值

常见反直觉：最优买点往往不在金叉（金叉已涨一段），而在短期超卖 + 长期趋势未坏；最优卖点往往在动能极致但未死叉。这一步会推翻顺动能直觉。

### 4. 提炼通用策略规则

从统计特征反推可量化买卖条件，例如：

- 买入：短期超卖（破 MA20 / J<0）+ 长期趋势未坏（MA60 向上）+ 过滤（量比>1.2 或 DIF 上拐）
- 卖出：动能极致（PDI 高位钝化）或放量长上影或破 MA20 且 MACD 柱转负，不要求死叉
- 空仓：MA20 下穿 MA60 彻底空仓，不抄下跌趋势票
- 规则必须可回测，避免差不多、明显这类模糊词

### 5. T+1 严格回测验证

- 用第四步规则实跑，对照买入持有，看收益/回撤/夏普/胜率
- 若跑输持有或不稳定：回到第三步补特征（趋势过滤、排除单边下跌票）
- 滚动入场：随机起点，确认非低点起步也有效
- 达标后再固化成预设或过滤器

## 复用代码模板

依赖 zettaranc-skill 的 etf_bt.indicators（若在该仓库回测）：

```python
from etf_bt import indicators as ind

def peaks(cl, win=8):
    pts = []
    for i in range(win, len(cl) - win):
        seg = cl[i - win:i + win + 1]
        if cl[i] <= min(seg) and cl[i] <= cl[i - win]:
            pts.append((i, "L"))
        elif cl[i] >= max(seg) and cl[i] >= cl[i + win]:
            pts.append((i, "H"))
    return pts

def backtest_ideal(closes):
    pts = peaks(closes, 8)
    cash, pos = 1.0, None
    for idx, t in pts:
        if t == "L" and pos is None:
            pos = idx
        elif t == "H" and pos is not None:
            if closes[idx] > closes[pos] * 1.05:
                cash *= closes[idx] / closes[pos]
            pos = None
    return cash - 1
```

## 交付物

- 该标的的理论收益天花板（过拟合上限）
- 极值点指标特征统计表
- 一套 T+1 回测过的通用买卖规则
- 与买入持有的对比结论（是否值得做波段）

## 避坑

- 复权口径不统一：价位判断会全错
- 数据源断层：分段拉历史（单次接口约 800 条），检查单日跳变 >50% 的脏数据
- 过拟合切太碎：段数过多会把噪声当趋势；用最小涨幅阈值约束
- 顺动能陷阱：双金叉/双死叉在波动大的票上假信号多，需趋势过滤
- 防御资产/单边下跌票：动量策略常失效，应先排除或加趋势门控

## 调用上下文（可选）

在 zettaranc-skill 仓库回测时：

- 数据：data/stock_data.db
- 指标：scripts/etf_bt/indicators.py
- 回测：scripts/etf_bt/engine.py + filters.py
