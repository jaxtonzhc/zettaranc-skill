"""回测引擎层。

核心函数：
  precompute(rows) -> PrecomputedIndicators
  run_strategy(pc, cfg) -> (nav, trades, stats)
  run_portfolio(etf_dict, dates, cfg) -> PortfolioResult
  metrics(nav) -> PerformanceMetrics
"""
import statistics
from dataclasses import dataclass, field
from . import indicators as ind


# ---------- 数据结构 ----------

class Precomputed:
    """懒加载指标容器：基础价格量 + 指标按需计算并缓存。
    新增指标只需在 _LAZY 注册一个计算方法，过滤器即可通过 pc.<name> 访问，
    首次访问时才计算，避免每次回测算全部指标。"""

    def __init__(self, opens, highs, lows, closes, vols):
        self.opens = opens
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.vols = vols
        self.n = len(closes)
        self._cache = {}

    def _get(self, name, fn):
        if name not in self._cache:
            self._cache[name] = fn()
        return self._cache[name]

    # ---- 注册表：名字 -> 计算函数（返回单序列或元组） ----
    @property
    def dif(self): return self._get("macd", lambda: ind.macd(self.closes))[0]
    @property
    def dea(self): return self._get("macd", lambda: ind.macd(self.closes))[1]
    @property
    def ks(self): return self._get("kdj", lambda: ind.kdj(self.highs, self.lows, self.closes))[0]
    @property
    def ds(self): return self._get("kdj", lambda: ind.kdj(self.highs, self.lows, self.closes))[1]
    @property
    def rsi_v(self): return self._get("rsi", lambda: ind.rsi(self.closes))
    @property
    def boll_lo(self): return self._get("boll", lambda: ind.boll(self.closes))[0]
    @property
    def boll_mid(self): return self._get("boll", lambda: ind.boll(self.closes))[1]
    @property
    def boll_up(self): return self._get("boll", lambda: ind.boll(self.closes))[2]
    @property
    def pdi(self): return self._get("dmi", lambda: ind.dmi(self.highs, self.lows, self.closes))[0]
    @property
    def mdi(self): return self._get("dmi", lambda: ind.dmi(self.highs, self.lows, self.closes))[1]
    @property
    def adx(self): return self._get("dmi", lambda: ind.dmi(self.highs, self.lows, self.closes))[2]
    @property
    def bbi_v(self): return self._get("bbi", lambda: ind.bbi(self.closes))
    @property
    def vr(self): return self._get("vr", lambda: ind.vol_ratio(self.vols))
    @property
    def macd_gold(self): return self._get("macd_x", lambda: ind.crosses(self.dif, self.dea))[0]
    @property
    def macd_dead(self): return self._get("macd_x", lambda: ind.crosses(self.dif, self.dea))[1]
    @property
    def kdj_gold(self): return self._get("kdj_x", lambda: ind.crosses(self.ks, self.ds))[0]
    @property
    def kdj_dead(self): return self._get("kdj_x", lambda: ind.crosses(self.ks, self.ds))[1]
    @property
    def chip_profit(self): return self._get("chip", lambda: ind.chip_peak(self.highs, self.lows, self.closes, self.vols))[0]
    @property
    def chip_cost(self): return self._get("chip", lambda: ind.chip_peak(self.highs, self.lows, self.closes, self.vols))[1]


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    hold_days: int
    ret_pct: float


@dataclass
class SingleResult:
    nav: list
    trades: list
    buys: int
    exits: int
    in_days: int
    total_ret: float


@dataclass
class PortfolioResult:
    nav: list
    per_etf: dict
    m: dict
    trades: list


@dataclass
class Config:
    fee: float = 0.0003
    buy_window: int = 2
    dead_window: int = 2
    reduce_ratio: float = 0.5
    buy_filter: callable = None      # (i, pc) -> bool
    sell_filter: callable = None     # (i, pc) -> bool
    regime: list = None              # list[bool] 防守总开关
    use_trend_filter: bool = True    # DMI/RSI 趋势过滤（死叉需趋势转弱）
    stateful: object = None          # 有状态策略钩子: StatefulPolicy 实例，接管信号循环


def precompute(rows):
    return Precomputed(
        [r["open"] for r in rows], [r["high"] for r in rows],
        [r["low"] for r in rows], [r["close"] for r in rows],
        [r["vol"] for r in rows])


def _win(arr, i, w=2):
    return any(arr[j] for j in range(max(0, i - w + 1), i + 1))


def run_strategy(pc, cfg=None):
    if cfg is None:
        cfg = Config()
    if cfg.stateful is not None:
        return cfg.stateful.run(pc, cfg)
    n = pc.n
    opens, closes = pc.opens, pc.closes
    cash, shares = 1.0, 0.0
    nav = []
    trades = []
    buys = exits = in_days = 0
    in_pos = False
    entry_price = 0.0
    entry_idx = 0

    for i in range(n):
        # 防守总开关
        if cfg.regime is not None and i < len(cfg.regime) and not cfg.regime[i]:
            if in_pos:
                cash += shares * opens[i] * (1 - cfg.fee)
                shares = 0.0
                exits += 1
                trades.append(Trade(entry_idx, i, entry_price, opens[i],
                                    i - entry_idx, (opens[i] / entry_price - 1) * 100))
                in_pos = False
            nav.append(cash + shares * closes[i])
            continue

        order = None
        if i > 0:
            sig_buy = _win(pc.macd_gold, i - 1, cfg.buy_window) and _win(pc.kdj_gold, i - 1, cfg.buy_window)
            if not in_pos:
                if sig_buy and (cfg.buy_filter is None or cfg.buy_filter(i, pc)):
                    order = "BUY"
            else:
                double_dead = _win(pc.macd_dead, i - 1, cfg.dead_window) and _win(pc.kdj_dead, i - 1, cfg.dead_window)
                single_dead = (pc.macd_dead[i - 1] and not pc.kdj_dead[i - 1]) or                               (pc.kdj_dead[i - 1] and not pc.macd_dead[i - 1])
                if cfg.use_trend_filter:
                    weak = (pc.pdi[i - 1] >= pc.mdi[i - 1]) and pc.adx[i - 1] >= 25 and pc.rsi_v[i - 1] >= 50
                else:
                    weak = True
                sell_ok = cfg.sell_filter is None or cfg.sell_filter(i, pc)
                if double_dead and not weak and sell_ok:
                    order = "EXIT"
                elif single_dead and not weak and sell_ok:
                    order = "REDUCE"
                elif sig_buy and (cfg.buy_filter is None or cfg.buy_filter(i, pc)):
                    order = "BUY_MORE"

        if order == "BUY" and not in_pos:
            shares = cash / (opens[i] * (1 + cfg.fee))
            cash = 0.0
            entry_price = opens[i]
            entry_idx = i
            in_pos = True
            buys += 1
        elif order == "BUY_MORE" and in_pos and cash > 0:
            add = cash / (opens[i] * (1 + cfg.fee))
            shares += add
            cash = 0.0
            buys += 1
        elif order == "REDUCE" and in_pos:
            s = shares * cfg.reduce_ratio
            cash += s * opens[i] * (1 - cfg.fee)
            shares -= s
        elif order == "EXIT" and in_pos:
            cash += shares * opens[i] * (1 - cfg.fee)
            shares = 0.0
            exits += 1
            trades.append(Trade(entry_idx, i, entry_price, opens[i],
                                i - entry_idx, (opens[i] / entry_price - 1) * 100))
            in_pos = False

        if shares > 0:
            in_days += 1
        nav.append(cash + shares * closes[i])

    # 最后未平仓
    if in_pos:
        trades.append(Trade(entry_idx, n - 1, entry_price, closes[-1],
                            n - 1 - entry_idx, (closes[-1] / entry_price - 1) * 100))
    return SingleResult(nav, trades, buys, exits, in_days, nav[-1] - 1)


def metrics(nav):
    total = nav[-1] / nav[0] - 1
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    n = len(rets)
    ann = (1 + total) ** (252 / n) - 1 if n else 0
    peak = nav[0]
    mdd = 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    mean_r = statistics.mean(rets) if rets else 0
    std_r = statistics.stdev(rets) if len(rets) > 1 else 1
    sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r else 0
    return dict(total_return=total, annualized_return=ann, max_drawdown=mdd,
                sharpe=sharpe, vol=std_r * (252 ** 0.5), n=n)


def run_portfolio(etf_dict, dates, cfg=None):
    """多 ETF 等权组合。"""
    if cfg is None:
        cfg = Config()
    lookup = {nm: {r["day"]: r for r in rows} for nm, rows in etf_dict.items()}
    per_etf = {}
    all_trades = []
    for nm in etf_dict:
        rows = [lookup[nm][dd] for dd in dates]
        pc = precompute(rows)
        r = run_strategy(pc, cfg)
        per_etf[nm] = r
        all_trades.extend(r.trades)
    port = [sum(per_etf[nm].nav[i] for nm in per_etf) / len(per_etf) for i in range(len(dates))]
    m = metrics(port)
    return PortfolioResult(port, per_etf, m, all_trades)


def hold_nav(etf_dict, dates):
    lookup = {nm: {r["day"]: r["close"] for r in rows} for nm, rows in etf_dict.items()}
    return [sum(lookup[nm][dates[i]] / lookup[nm][dates[0]] for nm in etf_dict) / len(etf_dict) for i in range(len(dates))]


def fmt_pct(x):
    return f"{x * 100:.1f}%"

class StatefulPolicy:
    """有状态策略基类。子类实现 decide(i, pc, pos) -> (order, ratio)。"""
    def decide(self, i, pc, pos):
        return None, 0.0

    def run(self, pc, cfg):
        n = pc.n
        opens, closes = pc.opens, pc.closes
        cash, shares = 1.0, 0.0
        nav, trades = [], []
        buys = exits = in_days = 0
        pos = {"in_pos": False, "entry_idx": 0, "entry_price": 0.0, "state": {}}
        for i in range(n):
            if i > 0:
                order, ratio = self.decide(i, pc, pos)
                if order == "BUY" and not pos["in_pos"]:
                    shares = cash / (opens[i] * (1 + cfg.fee)); cash = 0.0
                    pos["in_pos"] = True; pos["entry_idx"] = i; pos["entry_price"] = opens[i]
                    buys += 1
                elif order == "BUY_MORE" and pos["in_pos"] and cash > 0:
                    shares += cash / (opens[i] * (1 + cfg.fee)); cash = 0.0; buys += 1
                elif order == "REDUCE" and pos["in_pos"]:
                    s = shares * (ratio or cfg.reduce_ratio)
                    cash += s * opens[i] * (1 - cfg.fee); shares -= s
                elif order == "EXIT" and pos["in_pos"]:
                    cash += shares * opens[i] * (1 - cfg.fee); shares = 0.0
                    exits += 1
                    trades.append(Trade(pos["entry_idx"], i, pos["entry_price"], opens[i],
                                        i - pos["entry_idx"], (opens[i] / pos["entry_price"] - 1) * 100))
                    pos["in_pos"] = False
            if shares > 0:
                in_days += 1
            nav.append(cash + shares * closes[i])
        if pos["in_pos"]:
            trades.append(Trade(pos["entry_idx"], n - 1, pos["entry_price"], closes[-1],
                                n - 1 - pos["entry_idx"], (closes[-1] / pos["entry_price"] - 1) * 100))
        return SingleResult(nav, trades, buys, exits, in_days, nav[-1] - 1)

