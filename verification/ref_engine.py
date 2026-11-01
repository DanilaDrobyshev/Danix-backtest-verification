"""Independent re-implementation of the Danix execution rules (METHODOLOGY.md), in plain Python.

It does not use any Danix code. It reads the same candles and the same public Bybit trade archive and
produces a trade list that verify.py compares with Danix trade by trade.
Long-only strategies of this pack: one position at a time, at most one entry per candle.
"""
import numpy as np

from .common import INSURANCE, LIQ_TAKER, MMR, atr_sma, ema


class Exec:
    """Fees, half-spread and ping of market orders (TradingStatsManager::ApplyExecution)."""

    def __init__(self, fee_pct, spread_pct=0.0, latency_ms=0):
        self.fee = fee_pct / 100.0
        self.spread_pct = spread_pct
        self.latency = latency_ms

    def spread(self, price, is_buy):
        half = price * self.spread_pct / 100.0 / 2.0
        if half <= 0:
            return price
        p = price + half if is_buy else price - half
        return p if p > 0 else price


class Book:
    """One long position with TP/SL and the isolated-margin liquidation price."""

    def __init__(self, ex, leverage=1.0):
        self.ex, self.lev = ex, leverage
        self.pos = None
        self.trades = []
        self.funding_paid, self.funding_charges = 0.0, 0

    def open(self, t, price, qty):
        liq = price * (1.0 - (1.0 / self.lev - MMR - LIQ_TAKER - INSURANCE))
        self.pos = dict(open_t=t, entry=price, quantity=qty, sl=0.0, tp=0.0, liq=liq,
                        fee_in=price * qty * self.ex.fee)

    def close(self, t, price):
        p = self.pos
        fee_out = price * p["quantity"] * self.ex.fee
        pnl = (price - p["entry"]) * p["quantity"] - p["fee_in"] - fee_out
        self.trades.append(dict(open_t=p["open_t"], close_t=t, entry=p["entry"], exit=price,
                                quantity=p["quantity"], pnl=pnl, fees=p["fee_in"] + fee_out))
        self.pos = None

    def liquidate(self, t):
        p = self.pos
        margin = p["entry"] * p["quantity"] / self.lev
        self.trades.append(dict(open_t=p["open_t"], close_t=t, entry=p["entry"], exit=p["liq"],
                                quantity=p["quantity"], pnl=-margin - p["fee_in"], fees=p["fee_in"], liquidation=True))
        self.pos = None

    def process_orders(self, t, open_, low, high, fill):
        """TP/SL: SL first when both are touched; a gap fills at the open (ProcessOrders)."""
        p = self.pos
        if p is None:
            return
        if p["sl"] > 0 and low <= p["sl"]:
            self.close(*fill(t, min(p["sl"], open_), False, True))
        elif p["tp"] > 0 and high >= p["tp"]:
            self.close(*fill(t, max(p["tp"], open_), False, True))

    def check_liquidation(self, t, low):
        if self.pos is not None and low <= self.pos["liq"]:
            self.liquidate(t)


class Funding:
    """Funding (TradingStatsManager::ApplyFunding): every funding time T up to the current candle open or trade
    is charged once, before TP/SL, liquidation and the strategy of that candle or trade, at its price (candle open
    or trade price, not the mark price), to a position opened at or before T: qty x price x rate, a long pays a
    positive rate. It comes out of the balance and realized PnL, not out of the trade PnL."""

    def __init__(self, data):
        self.t, self.rate = data if data is not None else ((), ())
        self.k = 0

    def apply(self, book, t, price):
        while self.k < len(self.t) and self.t[self.k] <= t:
            p = book.pos
            if p is not None and p["open_t"] <= self.t[self.k]:
                book.funding_paid += p["quantity"] * price * self.rate[self.k]
                book.funding_charges += 1
            self.k += 1


def set_tp_sl(book, tp, sl, market, close_fill):
    """Set TP/SL like the Set TP/SL node: refused when a level is on the wrong side of the current market
    price (possible with ping: the fill came later at another price). The strategies then close the position
    at once (Set TP/SL Success = false -> Close Long)."""
    if tp <= market or sl >= market:
        book.close(*close_fill())
        return False
    book.pos["tp"], book.pos["sl"] = tp, sl
    return True


# ---------------------------------------------------------------- strategies

class EmaAtr:
    """EMA cross + ATR stop (strategies/VERIFY EMA ATR *.json)."""

    def __init__(self, C, lo, end, params):
        self.fast, self.slow = int(params.get("Fast EMA", 20)), int(params.get("Slow EMA", 50))
        self.atr_n = int(params.get("ATR Period", 14))
        self.k_sl, self.rr = float(params.get("ATR Stop", 2)), float(params.get("Reward Ratio", 2))
        self.usd, self.lev = float(params.get("Position USDT", 1000)), float(params.get("Leverage", 1))
        n = len(C)
        self.F = np.full(n, np.nan)
        self.S = np.full(n, np.nan)
        self.F[lo:end] = ema(C.c[lo:end], self.fast)
        self.S[lo:end] = ema(C.c[lo:end], self.slow)
        self.A = atr_sma(C.h, C.l, C.c, self.atr_n)
        self.lo = lo

    def entry(self, j):
        return j - 1 >= self.lo and self.F[j] > self.S[j] and self.F[j - 1] < self.S[j - 1]

    def exit(self, j):
        return self.F[j] < self.S[j]

    def levels(self, fill, j):
        d = self.k_sl * self.A[j]
        return fill + self.rr * d, fill - d


class FixedTpSl:
    """strategies/VERIFY Fixed TP SL.json: one long at the close of candle Index == Entry Index."""

    def __init__(self, C, lo, end, params):
        self.idx = int(params.get("Entry Index", 2))
        self.usd, self.lev = float(params.get("Position USDT", 1000)), float(params.get("Leverage", 1))
        self.tpm, self.slm = float(params.get("TP Mult", 1.02)), float(params.get("SL Mult", 0.98))
        self.lo = lo

    def entry(self, j):
        return j - self.lo == self.idx

    def exit(self, j):
        return False

    def levels(self, fill, j):
        return fill * self.tpm, fill * self.slm


# ---------------------------------------------------------------- candle model

def run_candles(C, start, end, warmup, strategy_cls, params, ex, funding=None):
    """Candle backtest: funding at the candle open, orders by the candle high/low, then the strategy once on
    the candle close (Index 0 = the candle that just closed); a market order fills at that close, or with ping
    at the next candle open."""
    lo = start - warmup
    st = strategy_cls(C, lo, end, params)
    book = Book(ex, st.lev)
    fund = Funding(funding)
    step = int(C.t[1] - C.t[0]) if len(C) > 1 else 60000
    last_entry = None
    for i in range(start, end):
        nxt = C.t[i + 1] if i + 1 < len(C) else C.t[i] + step
        fund.apply(book, int(C.t[i]), C.o[i])
        book.process_orders(int(C.t[i]), C.o[i], C.l[i], C.h[i], lambda t, p, buy, tpsl: (t, ex.spread(p, buy)))
        book.check_liquidation(int(C.t[i]), C.l[i])

        def fill(t, price, buy):
            if ex.latency > 0:
                t = int(nxt - 1 + ex.latency)
                price = C.o[i + 1] if i + 1 < len(C) else C.c[i]
            return t, ex.spread(price, buy)

        if book.pos is not None:
            if st.exit(i):
                book.close(*fill(int(C.t[i]), C.c[i], False))
        elif st.entry(i) and last_entry != i:
            t, px = fill(int(C.t[i]), C.c[i], True)
            book.open(t, px, st.usd / C.c[i])
            set_tp_sl(book, *st.levels(px, i), C.c[i], lambda: fill(int(C.t[i]), C.c[i], False))
            last_entry = i
    return book


# ---------------------------------------------------------------- tick model

def run_ticks(C, start, end, warmup, strategy_cls, params, ex, tick_t, tick_p, funding=None):
    """Tick backtest: for every trade of the candle, TP/SL and liquidation on the move from the previous
    trade, then the strategy on the forming candle. The strategy of this pack reads only closed candles
    (Index 1 = the candle before the forming one), so it can enter at the first trade of a candle.
    Candles without trades fall back to the candle model."""
    lo = start - warmup
    st = strategy_cls(C, lo, end, params)
    book = Book(ex, st.lev)
    fund = Funding(funding)
    step = int(C.t[1] - C.t[0])
    last_entry = None
    last_price = C.c[start - 1] if start > 0 else None
    info = dict(with_ticks=0, without_ticks=0, ticks=0)
    for i in range(start, end):
        t0 = int(C.t[i])
        t1 = int(C.t[i + 1]) if i + 1 < len(C) else t0 + step
        a, b = np.searchsorted(tick_t, t0), np.searchsorted(tick_t, t1)
        j = i - 1  # last closed candle as the strategy sees it
        if a == b:
            info["without_ticks"] += 1
            fund.apply(book, t0, C.o[i])
            book.process_orders(t0, C.o[i], C.l[i], C.h[i], lambda t, p, buy, tpsl: (t, ex.spread(p, buy)))
            book.check_liquidation(t0, C.l[i])

            def cfill(t, price, buy):
                if ex.latency > 0:
                    t = int(t1 - 1 + ex.latency)
                    price = C.o[i + 1] if i + 1 < len(C) else C.c[i]
                return t, ex.spread(price, buy)

            if book.pos is not None:
                if st.exit(j):
                    book.close(*cfill(t0, C.c[i], False))
            elif st.entry(j) and last_entry != i:
                t, px = cfill(t0, C.c[i], True)
                book.open(t, px, st.usd / C.c[i])
                set_tp_sl(book, *st.levels(px, j), C.c[i], lambda: cfill(t0, C.c[i], False))
                last_entry = i
            last_price = C.c[i]
            continue
        info["with_ticks"] += 1
        info["ticks"] += int(b - a)
        for k in range(a, b):
            tt, tp = int(tick_t[k]), tick_p[k]

            def fill(t, price, buy, _k=k, _tt=tt):
                if ex.latency > 0:
                    target = _tt + ex.latency
                    m = _k + int(np.searchsorted(tick_t[_k:b], target))
                    if m < b:
                        price = tick_p[m]
                    elif i + 1 < len(C):
                        price = C.o[i + 1]
                    else:
                        price = tick_p[b - 1]
                    t = target
                return t, ex.spread(price, buy)

            frm = last_price if last_price is not None else tp
            low, high = min(frm, tp), max(frm, tp)
            fund.apply(book, tt, tp)
            book.process_orders(tt, frm, low, high, lambda t, p, buy, tpsl: fill(t, p, buy))
            book.check_liquidation(tt, low)
            last_price = tp
            close = C.c[i] if k == b - 1 else tp  # on the last trade the forming candle becomes the exchange candle
            if book.pos is not None:
                if st.exit(j):
                    book.close(*fill(tt, close, False))
            elif st.entry(j) and last_entry != i:
                t, px = fill(tt, close, True)
                book.open(t, px, st.usd / close)
                set_tp_sl(book, *st.levels(px, j), close, lambda: fill(tt, close, False))
                last_entry = i
    book.info = info
    return book
