"""过滤器注册表。

买入过滤器: (i, pc) -> bool，i 为当前日，pc 为 Precomputed
卖出过滤器: (i, pc) -> bool
防守开关: regime(dates) -> list[bool]

每个过滤器有名字、描述、类型。runner.py 通过名字选择组合。
"""
from .engine import Precomputed


# ---------- 买入过滤器 ----------
BUY_FILTERS = {}


def buy_filter(name, desc):
    def deco(fn):
        BUY_FILTERS[name] = (desc, fn)
        return fn
    return deco


@buy_filter("none", "无过滤（基准）")
def _no_buy(i, pc):
    return True


@buy_filter("vol_1.2", "放量确认（量比>1.2）")
def _vol(i, pc):
    return pc.vr[i - 1] > 1.2


@buy_filter("adx20", "ADX>20（有趋势力度）")
def _adx20(i, pc):
    return pc.adx[i - 1] > 20


@buy_filter("adx25", "ADX>25（强趋势）")
def _adx25(i, pc):
    return pc.adx[i - 1] >= 25


@buy_filter("mom20_pos", "20日动量>0（不在下跌通道）")
def _mom20(i, pc):
    ref = max(0, i - 20)
    return pc.closes[ref] > 0 and (pc.closes[i - 1] / pc.closes[ref] - 1) > 0


@buy_filter("j_low", "KDJ J值<50（低位金叉）")
def _j_low(i, pc):
    return pc.ks[i - 1] < 50


@buy_filter("ma60_above", "收盘价>MA60（中线向上）")
def _ma60(i, pc):
    start = max(0, i - 59)
    ma60 = sum(pc.closes[start:i + 1]) / (i + 1 - start)
    return pc.closes[i - 1] > ma60


@buy_filter("boll_mid_above", "收盘价>布林中轨（站上中轨）")
def _boll_mid(i, pc):
    return pc.closes[i - 1] > pc.boll_mid[i - 1]


@buy_filter("vol_adx", "放量+ADX>20")
def _vol_adx(i, pc):
    return pc.vr[i - 1] > 1.2 and pc.adx[i - 1] > 20


# ---------- 卖出过滤器 ----------
SELL_FILTERS = {}


def sell_filter(name, desc):
    def deco(fn):
        SELL_FILTERS[name] = (desc, fn)
        return fn
    return deco


@sell_filter("none", "双死叉即清（无确认）")
def _no_sell(i, pc):
    return True


@sell_filter("boll_mid_break", "双死叉+收盘跌破布林中轨才清")
def _boll_break(i, pc):
    return pc.closes[i - 1] < pc.boll_mid[i - 1]


@sell_filter("bbi_break", "双死叉+收盘跌破BBI才清")
def _bbi_break(i, pc):
    return pc.closes[i - 1] < pc.bbi_v[i - 1]


@sell_filter("adx_down", "双死叉+ADX较前日下降才清")
def _adx_down(i, pc):
    if i < 2:
        return True
    return pc.adx[i - 1] < pc.adx[i - 2]


@sell_filter("mom20_neg", "双死叉+20日动量<0才清")
def _mom_neg(i, pc):
    ref = max(0, i - 20)
    return pc.closes[ref] > 0 and (pc.closes[i - 1] / pc.closes[ref] - 1) < 0


# ---------- 防守开关 ----------
REGIME_SWITCHES = {}


def regime_switch(name, desc):
    def deco(fn):
        REGIME_SWITCHES[name] = (desc, fn)
        return fn
    return deco


@regime_switch("cyb_ma120", "创业板指>MA120（科技牛熊总开关）")
def _cyb_ma120(dates):
    from . import data as dmod
    cyb = dmod.load_index("cyb")
    ma120 = dmod.ma_of_index(cyb, 120)
    return [cyb.get(dd, 0) >= ma120.get(dd, 1e9) for dd in dates]


@regime_switch("sh_ma120", "上证指数>MA120（大盘总开关）")
def _sh_ma120(dates):
    from . import data as dmod
    sh = dmod.load_index("sh")
    ma120 = dmod.ma_of_index(sh, 120)
    return [sh.get(dd, 0) >= ma120.get(dd, 1e9) for dd in dates]


# ---------- 常用组合 ----------
PRESETS = {
    "baseline": ("双金叉原版（双死叉即清，无过滤）", "none", "none", None, True),
    "v2": ("双金叉+破中轨卖出（推荐）", "none", "boll_mid_break", None, True),
    "v2_vol": ("双金叉+放量买入+破中轨卖出", "vol_1.2", "boll_mid_break", None, True),
    "v2_cyb": ("双金叉+破中轨卖出+创业板MA120总开关", "none", "boll_mid_break", "cyb_ma120", True),
    "v2_full": ("双金叉+放量买入+破中轨卖出+创业板MA120总开关", "vol_1.2", "boll_mid_break", "cyb_ma120", True),
    # 科技ETF专用战法（仅用于 tech_db 池，勿用于防守/消费池）
    "tech_b1": ("科技双金叉战法B1: 双金叉买+双死叉且破布林中轨卖（仅科技ETF）", "none", "boll_mid_break", None, True),
}

# ---------- 有状态策略：放量下跌减半仓 ----------
from .engine import StatefulPolicy


class TechB1VolDump(StatefulPolicy):
    """科技B1 + 放量下跌果断减半:
    买入: 双金叉(同tech_b1)。
    卖出: 持仓中若当日 量比>vr_th 且收阴(收盘<开盘) -> 次日减半仓;
          剩余半仓按 tech_b1 规则(双死叉且破布林中轨)清仓。
    减半只触发一次(用pos.state标记)，避免连续阴跌反复减半。
    """
    def __init__(self, vr_th=1.5):
        self.vr_th = vr_th

    def _win(self, arr, i, w=2):
        return any(arr[j] for j in range(max(0, i - w + 1), i + 1))

    def decide(self, i, pc, pos):
        j = i - 1  # 信号看前一收盘
        if not pos["in_pos"]:
            if self._win(pc.macd_gold, j) and self._win(pc.kdj_gold, j):
                return "BUY", 0.0
            return None, 0.0
        # 持仓中
        st = pos["state"]
        # 放量下跌减半(只一次)
        if not st.get("halved") and pc.vr[j] > self.vr_th and pc.closes[j] < pc.opens[j]:
            st["halved"] = True
            return "REDUCE", 0.5
        # 双死叉且破中轨 -> 清仓剩余
        if (self._win(pc.macd_dead, j) and self._win(pc.kdj_dead, j)
                and pc.closes[j] < pc.boll_mid[j]):
            return "EXIT", 0.0
        return None, 0.0


POLICIES = {
    "tech_b1_voldump": ("科技B1+放量下跌减半仓(量比>1.5收阴减一半,余仓双死叉破中轨清)",
                        lambda: TechB1VolDump(vr_th=1.5)),
    "tech_b1_voldump2": ("科技B1+放量大跌减半仓(量比>2.0收阴减一半)",
                         lambda: TechB1VolDump(vr_th=2.0)),
}

