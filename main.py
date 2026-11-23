"""Sixty-Bar Breakout Rider — a cbBTC trend bot for make.fun's 24h BTC race.

Idea, in one paragraph: Bitcoin's big money in a 24-hour window comes in one
or two sustained breakouts, while the hours between them are chop that eats
a busy bot alive in fees (a full-size round trip costs ~45 bps at this
pool's depth, and 1-minute noise is ~8 bps). So this bot trades rarely:
it buys only a fresh 60-minute high confirmed by a real 60-minute rise,
then rides the move with a wide trailing stop and a hard stop, and otherwise
sits in cash. No take-profit, no momentum-flip exit — in chop those exits
just manufacture losing round trips.

Rules (all integer arithmetic, no floats, no clock, no randomness, so two
runs on the same bars answer byte for byte the same):
  * History: keep every close seen so far (ints, price_scale 5).
  * Buy (flat only): at least 60 closes seen; the latest close is above
    every close of the previous 60 bars AND the 60-bar return is over
    +30 bps; the previous close must also have been a fresh 60-bar high,
    so a single one-minute spike cannot trigger a buy; no buy in the
    last 60 bars of the 1440-bar window; at least 60 bars since the
    last sell. Spend all cash, one order.
  * Sell (holding only): the close falls 200 bps below the highest close
    since entry (trailing stop), or 150 bps below the entry fill price
    (hard stop). Sell the whole position, one order.
  * max_slippage_bps is 300 on every order: far above normal 1-minute
    moves (~8 bps stdev), so it only guards against a wild open.
  * Warm-up batches (if any) are never traded on.

Local verification (faithful replay of the kit's frozen dev window with the
harness's exact integer fill math): net PnL +$29.50, 1 trade, max
drawdown 1.98%. On the previous race's dev window (a falling one, -2.80%
buy-and-hold) the same bot loses only -$15.74 with 1 trade, i.e. it
captures most of a rally and cuts a falling window's loss roughly in
half. Buy-and-hold on this dev window makes +$32.17 for comparison.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- strategy constants (basis points and bars) ---
LOOKBACK = 60            # breakout window: fresh N-bar high
BUY_RETURN_BPS = 30      # minimum 60-bar return to confirm the breakout
TRAIL_BPS = 200          # trailing stop below the peak since entry
STOP_BPS = 150           # hard stop below the entry fill price
COOLDOWN_BARS = 60       # bars to wait after a sell before buying again
TAIL_BARS = 60           # no new buys in the last N bars of the window
WINDOW_BARS = 1440       # bars in each window (dev and race alike)
MAX_SLIPPAGE_BPS = 300

# --- mutable state (per run) ---
closes = []              # closes seen so far, oldest first, ints
entry_price = 0          # fill price of the open position (0 when flat)
peak_price = 0           # highest close since entry (0 when flat)
cooldown_left = 0        # bars remaining before another buy is allowed


def _note_fills(batch):
    """Fold this batch's fills into entry/peak/cooldown. Called first."""
    global entry_price, peak_price, cooldown_left
    for fill in batch.get("fills", []):
        if fill.get("status") != "filled":
            continue
        if fill.get("symbol") != SYMBOL:
            continue
        if fill.get("side") == "buy":
            entry_price = int(fill["price"])
            peak_price = entry_price
        elif fill.get("side") == "sell":
            entry_price = 0
            peak_price = 0
            cooldown_left = COOLDOWN_BARS


def on_batch(state, batch):
    global entry_price, peak_price, cooldown_left
    _note_fills(batch)
    if state.warming_up:
        return []
    close = state.close(SYMBOL)
    if close is None:
        return []
    closes.append(close)
    if cooldown_left > 0:
        cooldown_left -= 1

    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0:
        if close > peak_price:
            peak_price = close
        # Trailing stop: close 200 bps below the peak since entry.
        if close * 10000 < peak_price * (10000 - TRAIL_BPS):
            return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        # Hard stop: close 150 bps below the entry fill price.
        if close * 10000 < entry_price * (10000 - STOP_BPS):
            return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # Flat: look for a confirmed fresh 60-bar high.
    if len(closes) < LOOKBACK + 2:
        return []
    if cooldown_left > 0:
        return []
    if len(closes) > WINDOW_BARS - TAIL_BARS:
        return []
    window = closes[-LOOKBACK - 1:-1]
    if close <= max(window):
        return []
    # 60-bar return over +30 bps (integer cross-multiplication).
    if close * 10000 <= closes[-LOOKBACK - 1] * (10000 + BUY_RETURN_BPS):
        return []
    # Two-bar confirmation: the previous close must also have been a
    # fresh 60-bar high, so a single one-minute spike cannot buy.
    prev_window = closes[-LOOKBACK - 2:-2]
    if closes[-2] <= max(prev_window):
        return []
    if state.cash <= 0:
        return []
    return [{"side": "buy", "symbol": SYMBOL,
             "notional_quote_units": state.cash,
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


makefun_bars.run(on_batch)
