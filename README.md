# Danix backtest verification

**Execution rule in one sentence:** if a candle touches both the take profit and the stop loss, the candle backtest
fills the stop loss (we cannot know the order inside a candle); the tick backtest fills whichever was hit first in the
real Bybit trades. In the candle runs of this pack that rule decides 66 of 919 trades (0 of 181 with the default
parameters, 66 of 738 with the fast ones).

This repository lets you check the Danix backtester yourself instead of trusting us. It holds the Danix trade lists
of 43 runs on BTCUSDT, SOLUSDT and STRKUSDT, the input data, and Python code that recomputes every trade independently:
a small reference engine, [backtesting.py](https://github.com/kernc/backtesting.py) and
[vectorbt](https://github.com/polakowo/vectorbt). Losing runs are published together with the rest.

How the engine fills orders and what is **not** modelled: [METHODOLOGY.md](METHODOLOGY.md).
Latest result: [report/REPORT.md](report/REPORT.md).

## Check it without Danix (about 5 minutes)

```bash
git clone https://github.com/<owner>/Danix-backtest-verification
cd Danix-backtest-verification
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                   # Python 3.11+
python tools/fetch_ticks.py                       # Bybit trade archive for the tick runs, hash-checked
python verify.py
```

`verify.py` prints one line per check and writes `report/REPORT.md`. It exits with code 1 if anything differs.
The only accepted difference is listed with its explanation in `config/known_differences.json`: one trade where
backtesting.py and vectorbt round a take-profit level 1e-14 above a candle high that equals it exactly.
The same runs on every push in GitHub Actions (`.github/workflows/verify.yml`).

## Reproduce the Danix side

1. Install Danix.
2. `powershell -ExecutionPolicy Bypass -File tools\run_danix.ps1 -Danix "C:\path\to\Danix.exe"`
   copies the three strategies and the scenario files into Danix, runs every backtest of `config/runs.json`
   through the Danix command line and rewrites `danix_runs/` and `data/candles/`.
3. `python verify.py`, and `git diff danix_runs` shows whether your Danix produced the same trades.

Backtests are deterministic: the same data and settings always give the same trades.

## What is in here

| Path | Contents |
|---|---|
| `config/runs.json` | Every Danix call with all its arguments: strategy, symbol, period, fees, spread, ping, parameters. |
| `danix_runs/` | Danix results: summary, settings, data source and every trade. |
| `data/candles/` | Bybit candles used by the runs (1h for Jan–Sep 2026, 5m for the tick days), with SHA-256 in `data/SHA256SUMS.txt`. |
| `data/ticks/` | Filled by `tools/fetch_ticks.py` from public.bybit.com; hashes in `data/ticks/SHA256SUMS.txt`. |
| `scenarios/` | Seven 8-candle CSV files and `expected.json` with the result computed by hand. |
| `strategies/` | The three strategies as Danix files. |
| `verification/` | `ref_engine.py` (independent engine), `external.py` (backtesting.py, vectorbt), helpers. |
| `verify.py` | Runs all checks. |

## The checks

- **Hand-calculated scenarios:** take profit, stop loss, both in one candle, gap through the stop, gap through the
  take profit, position still open at the end, liquidation at x10.
- **Trade-by-trade match** with the reference engine for all candle and tick runs: times to the millisecond, prices,
  quantity, PnL, fees.
- **Other engines:** backtesting.py and vectorbt on the candle runs.
- **Look-ahead:** cutting the history in the middle does not change any earlier trade.
- **Costs:** the same strategy without fees, with fees, with 0.05% spread and with ping.
- **Ticks vs candles** on the same days.
- **Walk-forward** 70/30 and the full parameter grid around the chosen values.

## Periods

| Runs | Timeframe | Period (UTC) |
|---|---|---|
| Candle runs | 1h | 2026-01-01 – 2026-09-30, 500 candles of warmup before it |
| Tick runs | 5m | 2026-06-23 – 2026-06-24 |
| Look-ahead | 1h | 2026-01-01 – 2026-05-15 |

The strategies are test strategies, not trading advice; several of the runs lose money.

## License

Code: MIT. Market data belongs to Bybit and is included only to make the checks reproducible.
