#!/usr/bin/env python3
"""ETF 等权组合回测对比脚本（腾讯财经前复权日K）。

对比两组 ETF 等权组合：
  A. 聚焦四只：半导体设备(159516) + 科创芯片(588200) + 芯片(159995) + 纳指(159941)
  B. 纯科技五只（A 股纯科技，可调）：半导体(512480) + 半导体设备(159516) + 芯片(159995)
     + 通信(515880) + 人工智能(159819)

输出：Markdown 报告 + PNG 净值对比图 + JSON 原始数据。
"""

import json
import math
import statistics
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import requests

TENCENT_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TRADING_DAYS = 252  # A股/ETF 年化交易日

# (名称, 交易所前缀, 代码)
ETF_POOL = [
    ("半导体设备ETF", "sz", "159516"),
    ("科创芯片ETF", "sh", "588200"),
    ("芯片ETF华夏", "sz", "159995"),
    ("纳指ETF广发", "sz", "159941"),
    ("半导体ETF", "sh", "512480"),
    ("通信ETF", "sh", "515880"),
    ("人工智能ETF", "sz", "159819"),
    ("科创50ETF", "sh", "588000"),
    ("恒生科技ETF", "sz", "159740"),
    ("中概互联50ETF", "sh", "513050"),
    ("芯片ETF国泰", "sh", "512760"),
    ("芯片ETF天弘", "sz", "159310"),
    ("纳指ETF国泰", "sh", "513100"),
]

PORTFOLIO_A = ["半导体设备ETF", "科创芯片ETF", "芯片ETF华夏", "纳指ETF广发"]
PORTFOLIO_B = ["半导体ETF", "半导体设备ETF", "芯片ETF华夏", "通信ETF", "人工智能ETF"]

DATA_DAYS = 760
CACHE_FILE = Path("data/etf_kline_cache.json")
OUT_DIR = Path("reports")
OUT_STAMP = "20260819"


def load_font() -> None:
    """加载系统中文字体，保证图内中文正常渲染。"""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            try:
                fm.fontManager.addfont(str(p))
                name = fm.FontProperties(fname=str(p)).get_name()
                plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
                plt.rcParams["axes.unicode_minus"] = False
                return
            except Exception:
                continue


def fetch_kline(symbol: str, days: int) -> list | None:
    """从腾讯财经拉取前复权日K，返回 [{day, close}] 列表。"""
    r = requests.get(
        TENCENT_FQKLINE,
        params={"param": f"{symbol},day,,,{days},qfq"},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    payload = r.json()
    sec = payload.get("data", {}).get(symbol)
    if not sec:
        return None
    rows = sec.get("qfqday") or sec.get("day") or []
    return [{"day": x[0], "close": x[2]} for x in rows]


def load_klines() -> dict[str, list[dict]]:
    """带缓存地拉取全部 ETF 日K。"""
    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("days") == DATA_DAYS and cached.get("source") == "tx-qfq" and all(
            name in cached["data"] for name, _, _ in ETF_POOL
        ):
            print(f"[缓存] 命中 {CACHE_FILE}")
            return cached["data"]

    result: dict[str, list[dict]] = {}
    for name, ex, code in ETF_POOL:
        symbol = f"{ex}{code}"
        try:
            klines = fetch_kline(symbol, DATA_DAYS)
            if klines:
                result[name] = klines
                print(f"[OK] {name} {symbol} {len(klines)} 根")
            else:
                print(f"[FAIL] {name} {symbol} 空数据")
        except Exception as e:
            print(f"[FAIL] {name} {symbol}: {e}")
        time.sleep(0.35)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"source": "tx-qfq", "days": DATA_DAYS, "data": result}, ensure_ascii=False)
    )
    return result


def to_frame(klines: list[dict]) -> tuple[list[str], list[float]]:
    """返回 (日期列表, 收盘价列表)，按日期升序。"""
    rows = sorted(klines, key=lambda x: x["day"])
    return [r["day"] for r in rows], [float(r["close"]) for r in rows]


def metrics_from_prices(dates: list[str], closes: list[float]) -> dict:
    """单资产/组合核心指标。"""
    n = len(closes)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    total = closes[-1] / closes[0] - 1
    years = (n - 1) / TRADING_DAYS
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else 0.0
    vol = statistics.pstdev(rets) * math.sqrt(TRADING_DAYS)
    sharpe = ann / vol if vol > 0 else 0.0

    peak = closes[0]
    mdd = 0.0
    for px in closes:
        peak = max(peak, px)
        mdd = min(mdd, px / peak - 1)

    yearly: dict[str, float] = {}
    year_prices: dict[str, list[float]] = {}
    for d, c in zip(dates, closes):
        year_prices.setdefault(d[:4], []).append(c)
    for y, px in year_prices.items():
        if len(px) >= 2:
            yearly[y] = px[-1] / px[0] - 1

    return {
        "dates": dates,
        "closes": closes,
        "total_return": total,
        "annualized_return": ann,
        "annualized_vol": vol,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "yearly": yearly,
        "start": dates[0],
        "end": dates[-1],
    }


def equal_weight_portfolio(
    frames: dict[str, tuple[list[str], list[float]]], names: list[str]
) -> dict:
    """等权组合净值：每日再平衡（当日收益率 = 成分等权平均）。"""
    common = set(frames[names[0]][0])
    for nm in names[1:]:
        common &= set(frames[nm][0])
    common = sorted(common)
    if len(common) < 30:
        raise ValueError(f"公共交易日不足: {len(common)}")

    closes_by: dict[str, dict[str, float]] = {}
    for nm in names:
        dates, closes = frames[nm]
        closes_by[nm] = dict(zip(dates, closes))

    nav = [1.0]
    for i in range(1, len(common)):
        r = sum(
            closes_by[nm][common[i]] / closes_by[nm][common[i - 1]] - 1 for nm in names
        ) / len(names)
        nav.append(nav[-1] * (1 + r))
    return metrics_from_prices(common, nav)


def buy_hold_portfolio(
    frames: dict[str, tuple[list[str], list[float]]], names: list[str]
) -> dict:
    """买入持有等权：期初等额买入，不做再平衡。"""
    common = set(frames[names[0]][0])
    for nm in names[1:]:
        common &= set(frames[nm][0])
    common = sorted(common)
    closes_by: dict[str, dict[str, float]] = {}
    for nm in names:
        dates, closes = frames[nm]
        closes_by[nm] = dict(zip(dates, closes))

    nav = []
    for d in common:
        v = 0.0
        for nm in names:
            p0 = closes_by[nm][common[0]]
            v += closes_by[nm][d] / p0
        nav.append(v / len(names))
    return metrics_from_prices(common, nav)


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def fmt_sharpe(x: float) -> str:
    return f"{x:.2f}"


def build_report(
    frames,
    stats: dict[str, dict],
    pa: dict,
    pb: dict,
    pa_bh: dict,
    pb_bh: dict,
    pa1y: dict,
    pb1y: dict,
) -> str:
    lines: list[str] = []
    w = lines.append
    w("# ETF 等权组合回测对比")
    w("")
    w(f"> 数据源：腾讯财经前复权日K，截止 {pa['end']}，共 {len(pa['dates'])} 个交易日。")
    w(f"> 年化基准：{TRADING_DAYS} 交易日/年，Sharpe 无风险利率 = 0。")
    w("")

    w("## 一、组合定义")
    w("")
    w("| 组合 | 成分 | 权重 |")
    w("|------|------|------|")
    w("| A 聚焦四只 | " + " + ".join(PORTFOLIO_A) + " | 各 25% |")
    w("| B 纯科技五只 | " + " + ".join(PORTFOLIO_B) + " | 各 20% |")
    w("")

    w("## 二、组合对比（每日再平衡等权）")
    w("")
    w("| 指标 | A 聚焦四只 | B 纯科技五只 | 差额 |")
    w("|------|-----------|-------------|------|")
    rows = [
        ("累计收益", pa["total_return"], pb["total_return"]),
        ("年化收益", pa["annualized_return"], pb["annualized_return"]),
        ("年化波动", pa["annualized_vol"], pb["annualized_vol"]),
        ("最大回撤", pa["max_drawdown"], pb["max_drawdown"]),
    ]
    for label, a, b in rows:
        diff = a - b
        w(f"| {label} | {fmt_pct(a)} | {fmt_pct(b)} | {fmt_pct(diff)} |")
    w(
        f"| 夏普(0rf) | {fmt_sharpe(pa['sharpe'])} | {fmt_sharpe(pb['sharpe'])} "
        f"| {fmt_sharpe(pa['sharpe'] - pb['sharpe'])} |"
    )
    w("")
    w("| 指标 | A 买入持有 | B 买入持有 |")
    w("|------|-----------|-----------|")
    for label, fmt in [
        ("total_return", fmt_pct),
        ("annualized_return", fmt_pct),
        ("max_drawdown", fmt_pct),
        ("sharpe", fmt_sharpe),
    ]:
        w(f"| {label} | {fmt(pa_bh[label])} | {fmt(pb_bh[label])} |")
    w("")

    w("## 三、近一年（250 交易日）快照（每日再平衡等权）")
    w("")
    w("| 指标 | A 聚焦四只 | B 纯科技五只 |")
    w("|------|-----------|-------------|")
    for label, key, fmt in [
        ("累计收益", "total_return", fmt_pct),
        ("最大回撤", "max_drawdown", fmt_pct),
        ("夏普(0rf)", "sharpe", fmt_sharpe),
    ]:
        w(f"| {label} | {fmt(pa1y[key])} | {fmt(pb1y[key])} |")
    w("")

    w("## 四、分年度收益（每日再平衡等权）")
    w("")
    years = sorted(set(pa["yearly"]) | set(pb["yearly"]))
    w("| 年度 | A 聚焦四只 | B 纯科技五只 |")
    w("|------|-----------|-------------|")
    for y in years:
        a = pa["yearly"].get(y, 0.0)
        b = pb["yearly"].get(y, 0.0)
        w(f"| {y} | {fmt_pct(a)} | {fmt_pct(b)} |")
    w("")

    w("## 五、单只 ETF 表现（同一公共窗口）")
    w("")
    w("| ETF | 累计 | 年化 | 波动 | 最大回撤 | 夏普 |")
    w("|-----|------|------|------|---------|------|")
    for name, _, _ in ETF_POOL:
        nm = name
        if nm not in stats:
            continue
        s = stats[nm]
        w(
            f"| {nm} | {fmt_pct(s['total_return'])} | {fmt_pct(s['annualized_return'])} "
            f"| {fmt_pct(s['annualized_vol'])} | {fmt_pct(s['max_drawdown'])} | {s['sharpe']:.2f} |"
        )
    w("")

    w("## 六、结论速览")
    w("")
    a_better = pa["sharpe"] > pb["sharpe"]
    better = "A 聚焦四只" if a_better else "B 纯科技五只"
    w(f"- 夏普口径：**{better}** 更优（A={pa['sharpe']:.2f} vs B={pb['sharpe']:.2f}）。")
    w(f"- 回撤口径：A 最大回撤 {fmt_pct(pa['max_drawdown'])} vs B {fmt_pct(pb['max_drawdown'])}。")
    w(f"- 收益口径：A 累计 {fmt_pct(pa['total_return'])} vs B 累计 {fmt_pct(pb['total_return'])}。")
    w("- 注：B 组合成分为假设值（A股纯科技 5 只候选），如与之前不同请提供代码，可立即重跑。")
    return "\n".join(lines)


def draw_chart(
    frames,
    pa: dict,
    pb: dict,
    stats: dict[str, dict],
) -> Path:
    load_font()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw={"height_ratios": [3, 2]})

    ax = axes[0]
    ax.plot(pa["dates"], pa["closes"], label="A 聚焦四只（等权再平衡）", lw=2)
    ax.plot(pb["dates"], pb["closes"], label="B 纯科技五只（等权再平衡）", lw=2, alpha=0.85)
    ax.set_title("ETF 等权组合净值对比（起始=1）")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_ylabel("净值")

    ax2 = axes[1]
    for nm in PORTFOLIO_A:
        s = stats[nm]
        ax2.plot(s["dates"], s["closes"], label=nm, lw=1.2)
    ax2.set_title("聚焦四只单资产归一净值（起始=1）")
    ax2.legend(fontsize=8, ncol=2)
    ax2.grid(alpha=0.3)
    ax2.set_ylabel("净值")

    fig.tight_layout()
    out = OUT_DIR / f"etf_portfolio_{OUT_STAMP}.png"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def main() -> int:
    klines = load_klines()
    frames = {name: to_frame(k) for name, k in klines.items()}

    common_start = max(frames[nm][0][0] for nm in PORTFOLIO_A)
    stats: dict[str, dict] = {}
    for name, _, _ in ETF_POOL:
        if name not in frames:
            continue
        dates, closes = frames[name]
        idx = [i for i, d in enumerate(dates) if d >= common_start]
        if len(idx) < 60:
            continue
        i0 = idx[0]
        stats[name] = metrics_from_prices(dates[i0:], closes[i0:])

    pa = equal_weight_portfolio(frames, PORTFOLIO_A)
    pb = equal_weight_portfolio(frames, PORTFOLIO_B)
    pa_bh = buy_hold_portfolio(frames, PORTFOLIO_A)
    pb_bh = buy_hold_portfolio(frames, PORTFOLIO_B)

    # 近一年快照（最近 250 个公共交易日）
    def slice_last(port: dict, n: int = 250) -> dict:
        d, c = port["dates"], port["closes"]
        return metrics_from_prices(d[-n:], c[-n:])

    pa1y = slice_last(pa)
    pb1y = slice_last(pb)

    chart = draw_chart(frames, pa, pb, stats)
    report = build_report(frames, stats, pa, pb, pa_bh, pb_bh, pa1y, pb1y)
    report_path = OUT_DIR / f"etf_portfolio_{OUT_STAMP}.md"
    report_path.write_text(report)

    summary = {
        "window": {"start": pa["start"], "end": pa["end"], "days": len(pa["dates"])},
        "portfolio_a": {k: v for k, v in pa.items() if k not in ("dates", "closes")},
        "portfolio_b": {k: v for k, v in pb.items() if k not in ("dates", "closes")},
        "portfolio_a_1y": {k: v for k, v in pa1y.items() if k not in ("dates", "closes")},
        "portfolio_b_1y": {k: v for k, v in pb1y.items() if k not in ("dates", "closes")},
        "etfs": {
            k: {kk: vv for kk, vv in v.items() if kk not in ("dates", "closes")}
            for k, v in stats.items()
        },
    }
    (OUT_DIR / f"etf_portfolio_{OUT_STAMP}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    print("\n=== 回测结果（每日再平衡等权）===")
    print(f"窗口: {pa['start']} ~ {pa['end']} ({len(pa['dates'])} 交易日)")
    print(f"{'指标':<8}{'A 聚焦四只':>12}{'B 纯科技五只':>14}")
    for label, key in [
        ("累计收益", "total_return"),
        ("年化收益", "annualized_return"),
        ("年化波动", "annualized_vol"),
        ("最大回撤", "max_drawdown"),
        ("夏普", "sharpe"),
    ]:
        a, b = pa[key], pb[key]
        if key == "sharpe":
            print(f"{label:<8}{a:>12.2f}{b:>14.2f}")
        else:
            print(f"{label:<8}{fmt_pct(a):>12}{fmt_pct(b):>14}")
    print(f"\n--- 近一年快照 ({pa1y['start']} ~ {pa1y['end']}) ---")
    for label, key in [
        ("累计收益", "total_return"),
        ("最大回撤", "max_drawdown"),
        ("夏普", "sharpe"),
    ]:
        a, b = pa1y[key], pb1y[key]
        if key == "sharpe":
            print(f"{label:<8}{a:>12.2f}{b:>14.2f}")
        else:
            print(f"{label:<8}{fmt_pct(a):>12}{fmt_pct(b):>14}")
    print(f"\n报告: {report_path}")
    print(f"图表: {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
