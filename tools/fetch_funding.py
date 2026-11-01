"""Download the Bybit funding rate history (public API) for the symbols of config/runs.json into
data/funding/<SYMBOL>.csv (timestamp_ms,rate, oldest first).

    python tools/fetch_funding.py

These are the rates Danix loads for its backtests (/v5/market/funding/history, category linear).
The files are small and stored in this repository; their hashes are in data/SHA256SUMS.txt.
"""
import csv
import datetime as dt
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "funding")
URL = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol={}&startTime={}&endTime={}&limit=200"


def ms(day):
    return int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), dt.timezone.utc).timestamp() * 1000)


def periods():
    """Per symbol: from the earliest `from` to the day after the latest `to` of all linear exchange sources."""
    cfg = json.load(open(os.path.join(ROOT, "config", "runs.json")))
    out = {}
    for item in cfg["exports"] + cfg["runs"]:
        s = item["args"].get("source", {})
        if s.get("type") != "exchange" or s.get("category", "linear") != "linear":
            continue
        a, b = ms(s["from"]), ms(s["to"]) + 2 * 86400000
        lo, hi = out.get(s["symbol"], (a, b))
        out[s["symbol"]] = (min(lo, a), max(hi, b))
    return out


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
        rows = fetch(symbol, start, end)
        with open(os.path.join(DIR, symbol + ".csv"), "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["timestamp_ms", "rate"])
            w.writerows(rows)
        print(f"{symbol}: {len(rows)} rates")


if __name__ == "__main__":
    main()
