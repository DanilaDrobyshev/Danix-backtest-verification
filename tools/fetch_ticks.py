"""Download the Bybit public trade archive (public.bybit.com) for the tick runs of config/runs.json
into data/ticks/ and check it against data/ticks/SHA256SUMS.txt.

    python tools/fetch_ticks.py               download what is missing, check the hashes
    python tools/fetch_ticks.py --write-sums  record the hashes of the files you have

The archive is not stored in this repository: it is Bybit's data, and BTCUSDT alone is tens of MB a day.
These are the same files Danix downloads for its tick backtest.
"""
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "ticks")
SUMS = os.path.join(DIR, "SHA256SUMS.txt")


def needed():
    cfg = json.load(open(os.path.join(ROOT, "config", "runs.json")))
    out = set()
    for r in cfg["runs"]:
        a = r["args"]
        if r["tool"] != "run_backtest" or not a.get("ticks"):
            continue
        s = a["source"]
        d0, d1 = dt.date.fromisoformat(s["from"]), dt.date.fromisoformat(s["to"])
        while d0 <= d1:
            out.add((s["symbol"], d0.isoformat()))
            d0 += dt.timedelta(days=1)
    return sorted(out)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(DIR, exist_ok=True)
    files = []
    for sym, day in needed():
        name = f"{sym}{day}.csv.gz"
        p = os.path.join(DIR, name)
        if not os.path.exists(p):
            url = f"https://public.bybit.com/trading/{sym}/{name}"
            print("download", url)
            urllib.request.urlretrieve(url, p + ".part")
            os.replace(p + ".part", p)
        files.append(name)
    if "--write-sums" in sys.argv:
        with open(SUMS, "w") as f:
            for n in files:
                f.write(f"{sha(os.path.join(DIR, n))}  {n}\n")
        print("wrote", SUMS)
        return
    if not os.path.exists(SUMS):
        print("no SHA256SUMS.txt yet: files downloaded but not checked")
        return
    known = dict(line.split()[::-1] for line in open(SUMS) if line.strip())
    bad = [n for n in files if known.get(n) != sha(os.path.join(DIR, n))]
    if bad:
        raise SystemExit("hash mismatch: " + ", ".join(bad))
    print(f"{len(files)} files, hashes match")


if __name__ == "__main__":
    main()
