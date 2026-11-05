"""Download the funding rate history (public API) for the symbols of config/runs.json into
data/funding/<SYMBOL>.csv (timestamp_ms,rate, oldest first).

    python tools/fetch_funding.py [--exchange kucoin]

These are the rates Danix loads for its backtests: Bybit /v5/market/funding/history (category linear);
KuCoin /api/v1/contract/funding-rates (USDT-M contract, e.g. XBTUSDTM; time = timepoint, the settlement time),
config/runs_kucoin.json -> data/kucoin/funding.
The files are small and stored in this repository; their hashes are in data/SHA256SUMS.txt.
"""
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCHANGE = sys.argv[sys.argv.index("--exchange") + 1].lower() if "--exchange" in sys.argv else "bybit"
CONFIG = os.path.join(ROOT, "config", "runs.json" if EXCHANGE == "bybit" else f"runs_{EXCHANGE}.json")
DIR = os.path.join(ROOT, "data", *([] if EXCHANGE == "bybit" else [EXCHANGE]), "funding")
URL = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol={}&startTime={}&endTime={}&limit=200"
KUCOIN_URL = "https://api-futures.kucoin.com/api/v1/contract/funding-rates?symbol={}&from={}&to={}"


def ms(day):
    return int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), dt.timezone.utc).timestamp() * 1000)


def periods():
    """Per symbol: from the earliest `from` to the day after the latest `to` of all linear exchange sources."""
    cfg = json.load(open(CONFIG))
    out = {}
    for item in cfg["exports"] + cfg["runs"]:
        s = item["args"].get("source", {})
        if s.get("type") != "exchange" or s.get("category", "linear") != "linear":
            continue
        a, b = ms(s["from"]), ms(s["to"]) + 2 * 86400000
        lo, hi = out.get(s["symbol"], (a, b))
        out[s["symbol"]] = (min(lo, a), max(hi, b))
    return out


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "danix-verification"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch_kucoin(symbol, start, end):
    """KuCoin returns the newest rates up to `to`, newest first: page backwards (as Danix does)."""
    got, last = {}, end
    while True:
        answer = get(KUCOIN_URL.format(symbol, start, last))
        if answer.get("code") != "200000":
            raise SystemExit(f"{symbol}: KuCoin {answer.get('msg')}")
        rows = answer.get("data") or []
        for x in rows:
            got[int(x["timepoint"])] = repr(float(x["fundingRate"]))
        oldest = min((int(x["timepoint"]) for x in rows), default=0)
        if not rows or oldest <= start or oldest >= last:
            break
        last = oldest - 1
    return sorted((t, v) for t, v in got.items() if start <= t <= end)


def fetch(symbol, start, end):
    """Bybit returns the newest 200 rates up to endTime: page backwards."""
    got, last = {}, end
    while True:
        req = urllib.request.Request(URL.format(symbol, start, last), headers={"User-Agent": "danix-verification"})
        with urllib.request.urlopen(req, timeout=30) as r:
            answer = json.load(r)
        if answer.get("retCode") != 0:
            raise SystemExit(f"{symbol}: Bybit {answer.get('retMsg')}")
        rows = answer["result"]["list"]
        for x in rows:
            got[int(x["fundingRateTimestamp"])] = x["fundingRate"]
        oldest = min((int(x["fundingRateTimestamp"]) for x in rows), default=0)
        if len(rows) < 200 or oldest <= start:
            break
        last = oldest - 1
    return sorted((t, v) for t, v in got.items() if start <= t <= end)


def main():
    os.makedirs(DIR, exist_ok=True)
    for symbol, (start, end) in sorted(periods().items()):
        rows = (fetch_kucoin if EXCHANGE == "kucoin" else fetch)(symbol, start, end)
        with open(os.path.join(DIR, symbol + ".csv"), "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["timestamp_ms", "rate"])
            w.writerows(rows)
        print(f"{symbol}: {len(rows)} rates")


if __name__ == "__main__":
    main()
