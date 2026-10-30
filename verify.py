"""Check every Danix run in danix_runs/ against independent calculations.

    python verify.py            all checks, table in the console and report/REPORT.md
    python verify.py --fast     skip backtesting.py / vectorbt

Exit code 1 if any check fails. A check is SKIP when its input is missing (for example the tick
archive was not downloaded: python tools/fetch_ticks.py).
"""
import json
import os
import sys
import time

from verification import common as cm
from verification import ref_engine as re_

RESULTS = []


def add(group, name, status, detail=""):
    RESULTS.append((group, name, status, detail))
    mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip", "INFO": "info", "KNOWN": "knwn"}[status]
    print(f"[{mark}] {group:<17} {name:<34} {detail}")


def run_setup(run, d):
    a = run["args"]
    params = dict(a.get("parameters", {}))
    src = d["source"]
    if run["kind"] == "scenario":
        C = cm.load_candles(run["scenario"])
    else:
        C = cm.load_candles(f"{run['pair']}_{a['source']['interval']}")
    start = C.index(int(src["tested_first_ms"]))
    end = C.index(int(src["tested_last_ms"])) + 1
    warmup = int(src.get("warmup", a.get("warmup", 0)) or 0)
    if start - warmup < 0:
        raise ValueError(f"candle file starts too late for warmup {warmup}")
    ex = re_.Exec(float(a.get("margin_fee_percent", cm.FEE_DEFAULT)), float(a.get("spread_pct", 0)),
                  int(a.get("latency_ms", 0)))
    strat = re_.FixedTpSl if "Fixed" in a["name"] else re_.EmaAtr
    return C, start, end, warmup, params, ex, strat


def check_totals(d, book):
    r = d["results"]
    pnl = sum(t["pnl"] for t in book.trades)
    fees = sum(t["fees"] for t in book.trades) + (book.pos["fee_in"] if book.pos else 0)
    bad = []
    if abs(pnl - r["realized_pnl"]) > 1e-4:
        bad.append(f"realized {pnl:.6f} vs {r['realized_pnl']}")
    if abs(fees - r["commission_paid"]) > 1e-4:
        bad.append(f"fees {fees:.6f} vs {r['commission_paid']}")
    return bad, pnl


def check_ref(run, d, tick_cache):
    C, start, end, warmup, params, ex, strat = run_setup(run, d)
    if run["kind"] == "ticks":
        sym = run["pair"]
        key = (sym, int(C.t[start]), int(C.t[end - 1]))
        if key not in tick_cache:
            first_day = int(C.t[start]) // 86400000
            alld = [time.strftime("%Y-%m-%d", time.gmtime(day * 86400))
                    for day in range(first_day, int(C.t[end - 1]) // 86400000 + 1)]
            tt, pp = [], []
            for day in alld:
                try:
                    a, b = cm.load_ticks(sym, day)
                except FileNotFoundError:
                    tick_cache[key] = None
                    break
                tt.append(a)
                pp.append(b)
            else:
                import numpy as np
                tick_cache[key] = (np.concatenate(tt), np.concatenate(pp))
        if tick_cache[key] is None:
            return "SKIP", "tick archive not downloaded (python tools/fetch_ticks.py)", None
        book = re_.run_ticks(C, start, end, warmup, strat, params, ex, *tick_cache[key])
    else:
        book = re_.run_candles(C, start, end, warmup, strat, params, ex)
    dz = cm.danix_trades(d)
    m, n, first = cm.compare_trades(book.trades, dz)
    bad, pnl = check_totals(d, book)
    detail = f"{m}/{n} trades identical, PnL {pnl:.4f} vs Danix {d['results']['realized_pnl']:.4f}"
    if first:
        detail += f" | first difference: {first}"
    if bad:
        detail += " | " + "; ".join(bad)
    return ("PASS" if m == n and not bad and n > 0 else "FAIL"), detail, book


def main():
    fast = "--fast" in sys.argv
    cfg = cm.load_runs()
    runs = {r["id"]: r for r in cfg["runs"]}
    danix = {k: cm.load_danix(k) for k in runs}
    tick_cache = {}
    books = {}
    print("Danix backtest verification\n")

    # 1. hand-calculated scenarios
    with open(cm.path("scenarios", "expected.json")) as f:
        expected = json.load(f)
    for rid, run in runs.items():
        if run["kind"] != "scenario":
            continue
        d = danix[rid]
        if d is None:
            add("scenarios", run["scenario"], "SKIP", "no Danix output")
            continue
        e = expected[run["scenario"]]
        dz = cm.danix_trades(d)
        m, n, first = cm.compare_trades(e["trades"], dz)
        r = d["results"]
        bad = [first] if first else []
        for k in ("open_positions", "liquidations", "commission_paid", "unrealized_pnl"):
            if k in e and abs(float(r[k]) - e[k]) > 1e-6:
                bad.append(f"{k} {r[k]} vs expected {e[k]}")
        st, det, book = check_ref(run, d, tick_cache)
        ok = not bad and st == "PASS" if e["trades"] else not bad
        add("scenarios", run["scenario"], "PASS" if ok else "FAIL",
            ("hand calculation and reference engine agree with Danix" if ok else "; ".join(bad + ([det] if st == "FAIL" and e["trades"] else []))))

    # 2. reference engine on every candle and tick run
    for rid, run in runs.items():
        if run["kind"] not in ("candles", "ticks"):
            continue
        d = danix[rid]
        if d is None:
            add(run["group"], rid, "SKIP", "no Danix output")
            continue
        st, det, book = check_ref(run, d, tick_cache)
        books[rid] = book
        add(run["group"] if run["group"] != "ticks_vs_candles" else "candles", rid, st, "reference engine: " + det)

    # 3. backtesting.py and vectorbt on the candle runs without spread and ping
    if not fast:
        from verification import external
        with open(cm.path("config", "known_differences.json")) as f:
            known = {(k["run"], e): k for k in json.load(f) for e in k["engines"]}
        for rid, run in runs.items():
            if run["group"] != "candles" or danix[rid] is None:
                continue
            d = danix[rid]
            C, start, end, warmup, params, ex, strat = run_setup(run, d)
            fee = float(run["args"].get("margin_fee_percent", cm.FEE_DEFAULT))
            dz = cm.danix_trades(d)
            for name, fn in (("backtesting.py", external.backtesting_py), ("vectorbt", external.vectorbt)):
                try:
                    tr = fn(C, start, end, warmup, params, fee)
                except Exception as e:  # library missing or API change: report, do not hide
                    add("other engines", f"{rid} / {name}", "FAIL", f"{type(e).__name__}: {e}")
                    continue
                m, n, first = cm.compare_trades(tr, dz, price_rel=1e-6, fields=("open_t", "close_t", "entry", "exit"))
                pnl = sum(t["pnl"] for t in tr)
                det = f"{m}/{n} trades (entry/exit time and price), PnL {pnl:.4f} vs Danix {d['results']['realized_pnl']:.4f}"
                ok = m == n and n > 0 and abs(pnl - d["results"]["realized_pnl"]) < 0.01
                k = known.get((rid, name))
                if not ok and k and m == n - 1 and first and first.startswith(f"trade #{k['trade']}:") \
                        and abs(pnl - d["results"]["realized_pnl"]) < 0.01:
                    add("other engines", f"{rid} / {name}", "KNOWN", det + f" | trade #{k['trade']}: {k['reason']}")
                    continue
                add("other engines", f"{rid} / {name}", "PASS" if ok else "FAIL", det + (f" | {first}" if first else ""))

    # 4. look-ahead: a run cut in the middle must give the same first trades
    for rid, run in runs.items():
        if run["group"] != "lookahead":
            continue
        cut, full = danix[rid], danix.get(run["full"])
        if cut is None or full is None:
            add("lookahead", rid, "SKIP", "no Danix output")
            continue
        last = int(cut["source"]["tested_last_ms"])
        a = cm.danix_trades(cut)
        b = [t for t in cm.danix_trades(full) if t["close_t"] <= last]
        m, n, first = cm.compare_trades(a, b, price_rel=0, pnl_abs=0)
        add("lookahead", rid, "PASS" if m == n and n > 0 else "FAIL",
            f"history cut at {cut['source'].get('tested_last_text', cut['source']['tested_last_ms'])}: {m}/{n} trades closed before the cut are identical to the full run"
            + (f" | {first}" if first else ""))

    # 5. costs: what fees, spread and ping take (numbers from Danix, each run verified above)
    cost_rows = []
    for p in sorted({r.get("pair") for r in runs.values() if r.get("pair")}):
        for model, base in (("candles 1h", f"candles_{p}_fast"), ("ticks 5m", f"ticks_{p}")):
            row = [p, model]
            for suffix in ("_nofee", "", "_spread", "_ping"):
                rid = base + suffix if suffix != "" else base
                d = danix.get(rid)
                row.append(f"{d['results']['return_pct']:.2f}% / {d['results']['trades']}" if d else "-")
            cost_rows.append(row)

    # 6. ticks vs candles on the same days
    tvc_rows = []
    for rid, run in runs.items():
        if run["group"] != "ticks_vs_candles":
            continue
        c, t = danix[rid], danix.get(run["ticks_run"])
        if c is None or t is None:
            add("ticks vs candles", rid, "SKIP", "no Danix output")
            continue
        step = 300000
        ct, tt = cm.danix_trades(c), cm.danix_trades(t)
        by_candle = {}
        for x in tt:
            by_candle.setdefault(x["open_t"] // step * step, x)
        same, diffs, same_exit = 0, [], 0
        for x in ct:
            y = by_candle.get(x["open_t"] + step)  # candle model enters at the close, tick model at the next trade
            if y is None:
                continue
            same += 1
            diffs.append(abs(y["entry"] - x["entry"]) / x["entry"] * 1e4)
            same_exit += int(y["close_t"] // step == x["close_t"] // step)
        diffs.sort()
        med = diffs[len(diffs) // 2] if diffs else 0
        tvc_rows.append([run["pair"], len(ct), len(tt), same, f"{med:.2f}", same_exit,
                         f"{c['results']['realized_pnl']:.2f}", f"{t['results']['realized_pnl']:.2f}"])
        add("ticks vs candles", run["pair"], "INFO",
            f"{same}/{len(ct)} candle entries found at the first trade of the next candle, median entry difference {med:.2f} bps, {same_exit} exits in the same candle")

    # 7. walk-forward and the parameter neighbourhood (reported, not a pass/fail check)
    wf_rows = []
    for rid, run in runs.items():
        if run["group"] != "walkforward":
            continue
        d = danix[rid]
        if d is None:
            add("walkforward", rid, "SKIP", "no Danix output")
            continue
        for s in d.get("top", []):
            pv = s.get("parameters", {})
            tr, te = s.get("train", {}), s.get("test", {})
            wf_rows.append([run["pair"], pv.get("Fast EMA"), pv.get("Slow EMA"), tr.get("return_pct"), tr.get("trades"),
                            te.get("return_pct"), te.get("trades"), s.get("stability")])
        add("walkforward", rid, "INFO", f"{len(d.get('top', []))} parameter sets reported with train and test results")

    write_report(cost_rows, tvc_rows, wf_rows)
    fails = [r for r in RESULTS if r[2] == "FAIL"]
    passes = [r for r in RESULTS if r[2] == "PASS"]
    skips = [r for r in RESULTS if r[2] == "SKIP"]
    known = [r for r in RESULTS if r[2] == "KNOWN"]
    print(f"\n{len(passes)} passed, {len(fails)} failed, {len(known)} known differences "
          f"(config/known_differences.json), {len(skips)} skipped. Report: report/REPORT.md")
    sys.exit(1 if fails else 0)


def write_report(cost_rows, tvc_rows, wf_rows):
    L = ["# Verification report", "", "Generated by `python verify.py`. Every line is recomputed from the files in this repository.", ""]
    L += ["| Group | Check | Result | Detail |", "|---|---|---|---|"]
    for g, n, s, d in RESULTS:
        L.append(f"| {g} | {n} | {s} | {d.replace('|', '/')} |")
    if cost_rows:
        L += ["", "## Costs: return / trades", "", "| Pair | Model | No fees | Fees 0.055% | + spread 0.05% | + ping |", "|---|---|---|---|---|---|"]
        L += ["| " + " | ".join(map(str, r)) + " |" for r in cost_rows]
        L += ["", "Ping: 500 ms on candles (the order fills at the next candle open), 200 ms on ticks (the first trade 200 ms after the signal)."]
    if tvc_rows:
        L += ["", "## Ticks vs candles, same days, same signals", "",
              "| Pair | Candle trades | Tick trades | Same signal found | Median entry diff, bps | Exit in same candle | PnL candles | PnL ticks |",
              "|---|---|---|---|---|---|---|---|"]
        L += ["| " + " | ".join(map(str, r)) + " |" for r in tvc_rows]
    if wf_rows:
        L += ["", "## Walk-forward (70% train / 30% test) and the parameter grid", "",
              "| Pair | Fast | Slow | Train return % | Train trades | Test return % | Test trades | Neighbours' mean |",
              "|---|---|---|---|---|---|---|---|"]
        L += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in wf_rows]
    os.makedirs(cm.path("report"), exist_ok=True)
    with open(cm.path("report", "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
