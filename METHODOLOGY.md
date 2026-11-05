# Methodology

This file describes exactly how the Danix backtester fills orders, what this repository checks and what it does not.
`verification/ref_engine.py` implements the rules below in about 250 lines of Python without any Danix code;
`verify.py` compares its trades with the Danix trades one by one.

## Two execution models

### Candle model (`ticks: false`)

For every candle, in this order:

1. **TP/SL** of the open position against the candle high/low. If the candle touched both levels, **the stop loss is
   assumed to come first** (a candle has no order inside, so this is the conservative choice). If the candle opened
   beyond the level (a gap), the fill is at the **open**, not at the level.
2. **Liquidation** if the low (long) reached the liquidation price.
3. **The strategy runs once on the candle close.** `Index 0` is the candle that has just closed. A market order fills
   at that **close** (the same as `trade_on_close=True` in backtesting.py).

### Tick model (`ticks: true`, default in Danix)

Danix downloads the public Bybit trade archive (`https://public.bybit.com/trading/<SYMBOL>/<SYMBOL><date>.csv.gz`),
the same files `tools/fetch_ticks.py` downloads. For every trade of a candle, in this order:

1. TP/SL and liquidation on the move from the previous trade price to this trade price. A triggered TP/SL fills
   **at its level** (or at the previous trade price if that was already beyond the level).
2. The candle being formed is updated with this trade, then **the strategy runs**. Here `Index 0` is the forming
   candle and `Index 1` the last closed one.
3. A market order fills at the price of this trade, with the time of that trade.

Candles that have no trades in the archive (for example today) run with the candle model; `results.ticks` and the log
list them. The checks in this repository use days fully covered by the archive.

### The strategies of this pack

All three are in `strategies/` and can be opened in Danix.

| Strategy | Used for | Logic |
|---|---|---|
| `VERIFY EMA ATR bar-close` | candle runs | Long when the fast EMA crosses above the slow EMA on the closed candle (`Index 0/1`), exit when it is below; SL = fill − ATR Stop × ATR, TP = fill + Reward Ratio × that distance. |
| `VERIFY EMA ATR next-tick` | tick runs | The same logic on the last **closed** candle (`Index 1/2`), so it can only enter at the first trade after a candle closes. |
| `VERIFY Fixed TP SL` | hand-checked scenarios | One long at the close of candle #N, TP and SL at fixed multiples of the fill. |

Both EMA strategies enter **at most once per candle** (`Last Entry Candle`). They are the same signal; only the
place where the order can be filled differs: the candle close in the candle model, the next trade in the tick model.

Size: `Position USDT / price` (1000 USDT, leverage 1). Starting balance 10 000 USDT.

Set TP/SL is refused when a level is already on the wrong side of the market price (this can happen with ping: the order fills later at another price). The strategies then close the position at once, as they would live.

## Indicators

- **EMA**: seeded with the first loaded candle, `alpha = 2 / (period + 1)`. Danix loads `warmup` candles before the
  tested period (500 here) so the seed does not matter by the time trading starts.
- **ATR**: the **simple mean** of the last `period` true ranges. This is *not* Wilder's smoothing used by TradingView's
  `ta.atr`; with Wilder's ATR only 10 of 58 trades of the BTCUSDT default run have the same exit.

## Costs

| Cost | Rule |
|---|---|
| Fee | Taker 0.055% of the notional on every market fill, TP/SL included (Bybit USDT perpetual, VIP 0). Maker 0.02% for passive limit orders (not used here). The runs pass the fee explicitly so the result does not depend on an account. |
| Spread | `spread_pct`: candle and trade prices are treated as mid prices; a market buy pays half the spread above, a sell half below. Applies to market orders and TP/SL fills, not to maker limit fills or liquidation. |
| Ping | `latency_ms`: in the tick model a market order or a triggered TP/SL fills at the first trade at least that many ms after the signal; in the candle model a strategy order fills at the next candle open. |
| Liquidation | Isolated margin. Long: `entry × (1 − (1/leverage − 0.5% MMR − 0.055% − 0.1% insurance))`. The whole margin and the entry fee are lost; no exit fee. |
| Funding | Historical Bybit rates from the public API (`data/funding/`). At each funding timestamp an open position pays `qty × candle open × rate` (the trade price in the tick model); a long pays a positive rate, a short receives it. It is part of `realized_pnl` (`funding_paid`), not of the trade PnL. Spot has none. |
| Netting | One position per symbol and side, as on Bybit. |
| Unrealized PnL | Of a position still open at the end: `(last close - entry) x qty - entry fee` (the entry fee is already paid). It is part of `return_pct`, not of `realized_pnl`. |

## What is checked

| Check | How |
|---|---|
| Hand-calculated scenarios (`scenarios/`) | 7 small CSV files: TP, SL, both in one candle, gap through the stop, gap through the take profit, position open at the end, liquidation at x10. The expected numbers are in `scenarios/expected.json` with the arithmetic written out. |
| Reference engine | Every candle and tick run: the same trades (open/close time to the millisecond, prices and quantity to 1e-9, PnL to 1e-6), the same total PnL, fees and funding. |
| backtesting.py 0.6.6 and vectorbt 1.2.0 | Candle runs without spread and ping: the same entry and exit time and price for every trade and the same PnL within 0.01 USDT. |
| Look-ahead | The same run with the history cut on 2026-05-15 must give exactly the same trades up to the cut. A strategy or engine that peeks at future candles would change them. |
| Costs | The same strategy with no fees, with fees, with spread and with ping; every one of these runs is also checked by the reference engine. |
| Ticks vs candles | The same days in both models: how many candle-model entries reappear at the first trade of the next candle, and how far apart the prices are. |
| Walk-forward | Danix's optimizer on the first 70% of the period, the top parameter sets re-run on the last 30%, with the mean result of neighbouring parameters. All sets are published, profitable or not. This part is reported, not pass/fail. |

## KuCoin

The KuCoin runs (`config/runs_kucoin.json`, candle model only) use the same execution rules and the same reference
engine; only the data and the fee differ.

| | KuCoin USDT-M futures |
|---|---|
| Symbols | XBTUSDTM, SOLUSDTM, STRKUSDTM (Danix also accepts BTCUSDT and maps it to the contract). |
| Candles | `/api/v1/kline/query`. KuCoin sends no candle for an interval without trades; Danix inserts one at the previous close (open = high = low = close, volume 0), as Bybit and Binance deliver it. Volume is converted from lots to coins (× contract multiplier). |
| Fee | Taker 0.06%, maker 0.02% (VIP 0), passed explicitly by the runs. |
| Funding | `/api/v1/contract/funding-rates`; the time is `timepoint` (the settlement time), applied by the same rule as on Bybit. The interval follows the data (STRKUSDTM settles every 4 hours, BTC and SOL every 8). |
| Quantity | Not rounded to the contract lot in a backtest, as on Bybit. |
| Liquidation | The same default formula; it does not occur in these runs (leverage 1). |

## Known limitations

We list these so you do not have to find them yourself.

1. **No order book.** Market orders fill at the trade price (plus the spread you set), whatever the size. There is no
   slippage model beyond spread and ping.
2. **Stops fill at the level in the tick model**, not at the trade that crossed it. In a 3-day SOLUSDT 5m run (105 trades), where the
   triggering trade was beyond the level it was on average 0.9 bps (max 3.5 bps) worse than the recorded fill. Use `spread_pct` or ping to
   cover it.
3. **TP and SL in one candle** in the candle model: SL first. Real order inside the candle may differ; the tick model
   resolves it.
4. **Funding uses the candle open or the trade price**, not the mark price the exchange uses.
5. **Liquidation uses fixed default risk parameters** (MMR 0.5%), not Bybit's tiered risk limits for large positions.
6. **Trades within the same millisecond** keep the order of the Bybit archive, which is written newest first, so
   within one millisecond they are replayed in reverse. It only matters when several trades of one millisecond cross a
   level.
7. **`Index 0` in the tick model is the forming candle.** A strategy that reads `Index 0` sees the candle change on
   every trade and can trade several times inside one candle (the built-in example made 2642 trades on ticks against
   354 on candles over one month of SOLUSDT 5m). That is why the strategies of this pack read closed candles and
   enter once per candle. Write strategies for ticks the same way.
8. **History limit:** a run loads at most 200 000 candles.
9. **Live trading is not covered here.** This repository checks the backtester. A comparison of live bot fills on
   Bybit with a tick backtest of the same period is a separate stage.
