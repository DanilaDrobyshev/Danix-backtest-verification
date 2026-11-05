"""Shared helpers: loading data and Danix outputs, indicators, trade comparison."""
import csv
import gzip
import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEE_DEFAULT = 0.055  # percent, Bybit USDT perpetual taker (VIP 0)
EXCHANGE = "bybit"  # verify.py --exchange; bybit keeps the original layout, others live in <exchange>/ subfolders

# Liquidation (Danix LiquidationCalculator, default risk parameters):
# price = entry x (1 - (1/leverage - MMR - taker fee - insurance)) for a long
MMR, LIQ_TAKER, INSURANCE = 0.005, 0.00055, 0.001


def path(*parts):
    return os.path.join(ROOT, *parts)


def sub(*parts):
    """Folder of the selected exchange: danix_runs, data/candles, ... for bybit, danix_runs/kucoin, data/kucoin/candles, ..."""
    if EXCHANGE == "bybit":
        return path(*parts)
    head, *rest = parts
    return path(head, EXCHANGE, *rest)


def load_runs():
    with open(path("config", "runs.json" if EXCHANGE == "bybit" else f"runs_{EXCHANGE}.json")) as f:
        return json.load(f)


def load_danix(run_id):
    """Danix output of one run (tools/collect.py keeps results, settings, source and the trades)."""
    p = sub("danix_runs", run_id + ".json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


class Candles:
    def __init__(self, rows):
        a = np.array(rows, dtype=float)
        self.t = a[:, 0].astype(np.int64)
        self.o, self.h, self.l, self.c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        self.v = a[:, 5]

    def __len__(self):
        return len(self.t)

    def index(self, t_ms):
        i = int(np.searchsorted(self.t, t_ms))
        if i >= len(self.t) or self.t[i] != t_ms:
            raise KeyError(f"candle {t_ms} not in data")
        return i


def load_candles(name):
    """data/candles/<name>.csv or scenarios/<name>.csv (Danix CSV format, timestamp in seconds)."""
    p = sub("data", "candles", name + ".csv")
    if not os.path.exists(p):
        p = path("scenarios", name + ".csv")
    rows = []
    with open(p) as f:
        r = csv.reader(f)
        next(r)
        for x in r:
            t = int(float(x[0]))
            if t < 10**11:
                t *= 1000
            rows.append([t] + [float(v) for v in x[1:6]])
    return Candles(rows)


def load_ticks(symbol, day):
    """Bybit public trade archive of one day, exactly as Danix reads it (Data/TickArchive.cpp):
    time = seconds with the first 3 decimals (ms, truncated), then a stable sort by time.
    The archive is written newest first, so trades of the same millisecond keep the file order."""
    p = path("data", "ticks", f"{symbol}{day}.csv.gz")
    with gzip.open(p, "rt") as f:
        r = csv.reader(f)
        head = next(r)
        ti, pi = head.index("timestamp"), head.index("price")
        rows = []
        for x in r:
            s = x[ti]
            if "." in s:
                sec, frac = s.split(".", 1)
                ms = int(sec) * 1000 + int((frac + "000")[:3])
            else:
                ms = int(s)
                ms = ms // 1000 if ms > 10**14 else (ms * 1000 if ms < 10**11 else ms)
            rows.append((ms, float(x[pi])))
    rows.sort(key=lambda z: z[0])  # list.sort is stable
    t = np.array([z[0] for z in rows], dtype=np.int64)
    p_ = np.array([z[1] for z in rows], dtype=float)
    return t, p_


def load_funding(symbol):
    """data/[<exchange>/]funding/<symbol>.csv (tools/fetch_funding.py): exchange funding times in ms and rates, oldest first."""
    t, r = [], []
    with open(sub("data", "funding", symbol + ".csv")) as f:
        rd = csv.reader(f)
        next(rd)
        for x in rd:
            t.append(int(x[0]))
            r.append(float(x[1]))
    return np.array(t, dtype=np.int64), np.array(r, dtype=float)


def ema(x, n):
    """EMA seeded with the first value, alpha = 2 / (n + 1) (Danix SDK/Indicators)."""
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    v = x[0]
    for i in range(len(x)):
        v = x[0] if i == 0 else a * x[i] + (1 - a) * v
        out[i] = v
    return out


def atr_sma(h, l, c, n):
    """ATR as the simple mean of the last n true ranges (Danix; not Wilder's smoothing)."""
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    cs = np.r_[0.0, np.cumsum(tr)]
    out = np.empty(len(tr))
    for i in range(len(tr)):
        j = max(0, i - n + 1)
        out[i] = (cs[i + 1] - cs[j]) / (i + 1 - j)
    return out


def danix_trades(d):
    """Closed positions of a Danix run as dicts, oldest first."""
    pos = d.get("positions", [])
    closed = [p for p in pos if not p.get("open")]
    return sorted(closed, key=lambda p: (p["open_t"], p["close_t"]))


def compare_trades(mine, theirs, price_rel=1e-9, pnl_abs=1e-6, time_ms=0, qty_rel=1e-9, fields=("open_t", "close_t", "entry", "exit", "quantity", "pnl")):
    """Trade-by-trade comparison. Returns (matched, total, first_mismatch_text)."""
    n = max(len(mine), len(theirs))
    matched = 0
    first = None
    for i in range(n):
        if i >= len(mine) or i >= len(theirs):
            first = first or f"trade #{i + 1}: only in {'Danix' if i >= len(mine) else 'reference'}"
            continue
        a, b = mine[i], theirs[i]
        bad = []
        for k in fields:
            if k not in a or k not in b:
                continue
            x, y = float(a[k]), float(b[k])
            if k.endswith("_t"):
                ok = abs(x - y) <= time_ms
            elif k == "pnl":
                ok = abs(x - y) <= pnl_abs
            elif k == "quantity":
                ok = abs(x - y) <= qty_rel * max(abs(y), 1e-12)
            else:
                ok = abs(x - y) <= price_rel * max(abs(y), 1e-12)
            if not ok:
                bad.append(f"{k} ref {a[k]} vs Danix {b[k]}")
        if bad:
            first = first or f"trade #{i + 1}: " + "; ".join(bad)
        else:
            matched += 1
    return matched, n, first
