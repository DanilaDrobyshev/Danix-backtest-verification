"""The same candle strategy in two mature open-source engines: backtesting.py and vectorbt.

Both get the same candles, the same signals (EMA seeded with the first loaded candle, ATR = simple mean
of true ranges) and the same fee; fills at the signal candle close (trade_on_close), TP/SL intrabar.
They are independent of Danix and of ref_engine.py.
"""
import warnings

import numpy as np
import pandas as pd

from .common import atr_sma, ema

warnings.filterwarnings("ignore")


def _signals(C, start, end, warmup, params):
    lo = start - warmup
    fast, slow = int(params.get("Fast EMA", 20)), int(params.get("Slow EMA", 50))
    F, S = ema(C.c[lo:end], fast), ema(C.c[lo:end], slow)
    A = atr_sma(C.h, C.l, C.c, int(params.get("ATR Period", 14)))[lo:end]
    active = np.zeros(end - lo, bool)
    active[start - lo:] = True
    prevF, prevS = np.r_[np.nan, F[:-1]], np.r_[np.nan, S[:-1]]
    ent = active & (F > S) & (prevF < prevS)
    ex = active & (F < S)
    df = pd.DataFrame({"Open": C.o[lo:end], "High": C.h[lo:end], "Low": C.l[lo:end], "Close": C.c[lo:end],
                       "Volume": C.v[lo:end]}, index=pd.to_datetime(C.t[lo:end], unit="ms"))
    return df, ent, ex, A


def _ms(ts):
    return int(pd.Timestamp(ts).value // 10**6)


def backtesting_py(C, start, end, warmup, params, fee_pct):
    from backtesting import Strategy
    from backtesting.lib import FractionalBacktest

    df, ent, ex, A = _signals(C, start, end, warmup, params)
    k_sl, rr = float(params.get("ATR Stop", 2)), float(params.get("Reward Ratio", 2))
    usd = float(params.get("Position USDT", 1000))
    unit = 1e-8

    class S(Strategy):
        def init(self):
            pass

        def next(self):
            i = len(self.data) - 1
            if self.position:
                if ex[i]:
                    self.position.close()
            elif ent[i]:
                e = self.data.Close[-1]  # FractionalBacktest shows prices per fractional unit
                k = e / df.Close.values[i]
                self.buy(size=round(usd / e), sl=e - k_sl * A[i] * k, tp=e + k_sl * rr * A[i] * k)

    bt = FractionalBacktest(df, S, cash=10_000, commission=fee_pct / 100.0, trade_on_close=True,
                            exclusive_orders=False, fractional_unit=unit, finalize_trades=False)
    st = bt.run()
    out = []
    for r in st._trades.itertuples():
        out.append(dict(open_t=_ms(r.EntryTime), close_t=_ms(r.ExitTime), entry=r.EntryPrice, exit=r.ExitPrice,
                        quantity=r.Size, pnl=r.PnL))
    return out


def vectorbt(C, start, end, warmup, params, fee_pct):
    import vectorbt as vbt

    df, ent, ex, A = _signals(C, start, end, warmup, params)
    k_sl, rr = float(params.get("ATR Stop", 2)), float(params.get("Reward Ratio", 2))
    usd = float(params.get("Position USDT", 1000))
    pf = vbt.Portfolio.from_signals(df.Close, entries=ent, exits=ex, open=df.Open, high=df.High, low=df.Low,
                                    size=usd, size_type="value", fees=fee_pct / 100.0,
                                    sl_stop=k_sl * A / df.Close.values, tp_stop=k_sl * rr * A / df.Close.values,
                                    init_cash=10_000_000)
    out = []
    for _, r in pf.trades.records_readable.iterrows():
        if r.get("Status") == "Open":
            continue
        out.append(dict(open_t=_ms(r["Entry Timestamp"]), close_t=_ms(r["Exit Timestamp"]), entry=r["Avg Entry Price"],
                        exit=r["Avg Exit Price"], quantity=r["Size"], pnl=r["PnL"]))
    return out
