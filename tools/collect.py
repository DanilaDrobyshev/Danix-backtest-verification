"""danix_runs/raw/*.json (answers of the Danix command line) -> danix_runs/<id>.json, data/candles/*.csv, SHA256SUMS.

    python tools/collect.py [--exchange kucoin]   (kucoin: config/runs_kucoin.json, danix_runs/kucoin, data/kucoin)
"""
import csv
import glob
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCHANGE = sys.argv[sys.argv.index("--exchange") + 1].lower() if "--exchange" in sys.argv else "bybit"
CONFIG = os.path.join(ROOT, "config", "runs.json" if EXCHANGE == "bybit" else f"runs_{EXCHANGE}.json")
RUNS = os.path.join(ROOT, "danix_runs", *([] if EXCHANGE == "bybit" else [EXCHANGE]))
DATA = os.path.join(ROOT, "data", *([] if EXCHANGE == "bybit" else [EXCHANGE]))
RAW = os.path.join(RUNS, "raw")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def check_exchange(name, args, d):
    """Danix takes candles, ticks and funding from the exchange selected in its settings, not from the arguments."""
    want = args.get("source", {}).get("exchange")
    got = (d.get("source") or {}).get("exchange")
    if want and got and want != got:
        raise SystemExit(f"{name}: Danix answered with {got} data, the pack needs {want}: "
                         f"switch Settings > Exchanges to {want} and run again")


def main():
    cfg = json.load(open(CONFIG))
    # before anything is written: a run on the wrong exchange must not overwrite data/candles
    for name, args, p in [(e["file"], e["args"], os.path.join(RAW, "candles_" + e["file"] + ".json")) for e in cfg["exports"]] +             [(r["id"], r["args"], os.path.join(RAW, r["id"] + ".json")) for r in cfg["runs"]]:
        if os.path.exists(p):
            check_exchange(name, args, json.load(open(p, encoding="utf-8-sig")))
    os.makedirs(os.path.join(DATA, "candles"), exist_ok=True)
    for e in cfg["exports"]:
        p = os.path.join(RAW, "candles_" + e["file"] + ".json")
        if not os.path.exists(p):
            continue
        rows = json.load(open(p, encoding="utf-8-sig"))["candles"]
        with open(os.path.join(DATA, "candles", e["file"]), "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["timestamp_ms", "open", "high", "low", "close", "volume"])
            w.writerows([[int(r[0])] + r[1:6] for r in rows])
    for r in cfg["runs"]:
        p = os.path.join(RAW, r["id"] + ".json")
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8-sig"))
        if r["tool"] == "run_backtest":
            cd = d.get("chart_data", {})
            # chart_data.truncated is about plots and marks; the trades must all be there
            res = d.get("results", {})
            need = int(res.get("closed_positions", 0)) + int(res.get("open_positions", 0))
            if len(cd.get("positions", [])) != need:
                raise SystemExit(f"{r['id']}: {len(cd.get('positions', []))} positions in chart_data, expected {need}")
            out = {k: d.get(k) for k in ("strategy", "parameters", "results", "settings", "source", "log")}
            out["positions"] = cd.get("positions", [])
        else:
            out = d
        out["id"], out["args"] = r["id"], r["args"]
        with open(os.path.join(RUNS, r["id"] + ".json"), "w") as f:
            json.dump(out, f, indent=1)
    if os.path.exists(os.path.join(RAW, "_meta.json")):
        shutil.copy(os.path.join(RAW, "_meta.json"), os.path.join(RUNS, "_meta.json"))
    files = sorted(glob.glob(os.path.join(DATA, "candles", "*.csv")) + glob.glob(os.path.join(DATA, "funding", "*.csv"))
                   + (glob.glob(os.path.join(ROOT, "scenarios", "*.csv")) if EXCHANGE == "bybit" else []))
    with open(os.path.join(DATA, "SHA256SUMS.txt"), "w") as f:
        for p in files:
            f.write(f"{sha(p)}  {os.path.relpath(p, ROOT).replace(os.sep, '/')}\n")
    print(f"collected {len(cfg['runs'])} runs, {len(files)} data files")


if __name__ == "__main__":
    main()
