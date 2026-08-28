import os
import time
import json
import hmac
import hashlib
import requests
from decimal import Decimal

from strategy import (
    DogeFuturesStrategy,
    FuturesConfig,
)


BASE_URL = "https://api.coindcx.com"
PAIR = "B-DOGE_USDT"

TEST_SECONDS = 300

API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_SECRET_KEY")


def public_get(path, params=None):

    r = requests.get(
        BASE_URL + path,
        params=params,
        timeout=10,
    )

    r.raise_for_status()

    return r.json()


def signed_get(path, body=None):

    body = body or {}

    body["timestamp"] = int(
        time.time() * 1000
    )

    payload = json.dumps(
        body,
        separators=(",", ":"),
    )

    signature = hmac.new(
        API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    r = requests.get(
        BASE_URL + path,
        data=payload,
        headers=headers,
        timeout=10,
    )

    r.raise_for_status()

    return r.json()


def get_instrument():

    return public_get(
        "/exchange/v1/derivatives/futures/data/instrument",
        {
            "pair": PAIR,
            "margin_currency_short_name": "INR",
        },
    )


def get_wallet():

    return signed_get(
        "/exchange/v1/derivatives/futures/wallets"
    )


def get_orderbook():

    r = requests.get(
        "https://public.coindcx.com/"
        "market_data/v3/orderbook/"
        f"{PAIR}-futures/50",
        timeout=10,
    )

    r.raise_for_status()

    return r.json()


class PaperAccount:

    def __init__(self, capital):

        self.starting_balance = Decimal(
            str(capital)
        )

        self.balance = Decimal(
            str(capital)
        )

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        self.realized_pnl = Decimal("0")
        self.unrealized_pnl = Decimal("0")
        self.fees = Decimal("0")

        self.entries = 0
        self.exits = 0

        self.wins = 0
        self.losses = 0

        self.equity_peak = self.balance
        self.max_drawdown = Decimal("0")

    def mark(self, price):

        if self.position == 0:

            self.unrealized_pnl = Decimal("0")

        else:

            price = Decimal(str(price))

            if self.position > 0:

                self.unrealized_pnl = (
                    price - self.entry_price
                ) * abs(self.position)

            else:

                self.unrealized_pnl = (
                    self.entry_price - price
                ) * abs(self.position)

        equity = (
            self.balance
            + self.unrealized_pnl
        )

        if equity > self.equity_peak:
            self.equity_peak = equity

        drawdown = (
            self.equity_peak - equity
        )

        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown

    def enter(
        self,
        side,
        price,
        quantity,
        strategy,
    ):

        if self.position != 0:
            return False

        price = Decimal(str(price))
        quantity = Decimal(str(quantity))

        notional = price * quantity

        # 5x maximum exposure.
        if (
            notional
            > strategy.config.capital_inr
            * strategy.config.max_leverage
        ):
            return False

        fee = strategy.fee(notional)

        if fee > self.balance:
            return False

        self.balance -= fee
        self.fees += fee

        if side == "LONG":

            self.position = quantity

        elif side == "SHORT":

            self.position = -quantity

        else:

            return False

        self.entry_price = price
        self.entry_side = side

        self.entries += 1

        return True

    def exit(
        self,
        price,
        strategy,
    ):

        if self.position == 0:
            return Decimal("0")

        price = Decimal(str(price))

        quantity = abs(self.position)

        if self.position > 0:

            gross = (
                price - self.entry_price
            ) * quantity

        else:

            gross = (
                self.entry_price - price
            ) * quantity

        entry_fee = strategy.fee(
            self.entry_price * quantity
        )

        exit_fee = strategy.fee(
            price * quantity
        )

        net = (
            gross
            - entry_fee
            - exit_fee
        )

        self.balance += (
            gross - exit_fee
        )

        self.realized_pnl += net

        self.fees += exit_fee

        self.exits += 1

        if net > 0:
            self.wins += 1

        elif net < 0:
            self.losses += 1

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None
        self.unrealized_pnl = Decimal("0")

        return net


def main():

    print("==========================================")
    print(" COINDCX DOGE FUTURES PAPER BOT")
    print("==========================================")

    if not API_KEY:
        raise RuntimeError(
            "Missing COINDCX_API_KEY"
        )

    if not API_SECRET:
        raise RuntimeError(
            "Missing COINDCX_SECRET_KEY"
        )

    print("API credentials detected.")
    print("Pair:", PAIR)
    print("Duration:", TEST_SECONDS, "seconds")
    print("Capital: ₹1000 virtual")
    print("Maximum leverage: 5x")
    print("Risk per trade: 1%")
    print("Daily loss limit: 5%")
    print("Live orders: DISABLED")

    # -----------------------------------------
    # Instrument
    # -----------------------------------------

    instrument = get_instrument()

    print("\n=== INSTRUMENT ===")

    print(
        json.dumps(
            instrument,
            indent=2,
        )
    )

    # -----------------------------------------
    # Wallet
    # -----------------------------------------

    print("\n=== FUTURES WALLET ===")

    try:

        wallet = get_wallet()

        print(
            json.dumps(
                wallet,
                indent=2,
            )
        )

    except requests.HTTPError:

        print(
            "Futures wallet unavailable."
        )

        print(
            "Continuing paper-only."
        )

    # -----------------------------------------
    # Strategy
    # -----------------------------------------

    config = FuturesConfig(
        capital_inr=Decimal("1000"),
        max_leverage=Decimal("5"),
        risk_per_trade=Decimal("0.01"),
        max_risk_per_trade=Decimal("0.02"),
        daily_loss_limit=Decimal("0.05"),
        max_consecutive_losses=3,
        cooldown_seconds=3600,
        maker_fee_bps=Decimal("2.36"),
        round_trip_fee_bps=Decimal("4.72"),
        slippage_buffer_bps=Decimal("1.00"),
        max_spread_pct=Decimal("0.10"),
        imbalance_window=20,
        candle_window=20,
        fast_ema_period=5,
        slow_ema_period=13,
        atr_period=14,
        stop_atr_multiplier=Decimal("1.5"),
        target_atr_multiplier=Decimal("2.0"),
        minimum_score=Decimal("0.60"),
        max_position_seconds=300,
    )

    strategy = DogeFuturesStrategy(config)

    paper = PaperAccount(
        config.capital_inr
    )

    prices = []
    candles = []

    print("\n=== PAPER CONFIG ===")

    print(
        "Risk/trade:",
        config.capital_inr
        * config.risk_per_trade,
        "INR"
    )

    print(
        "Max risk/trade:",
        config.capital_inr
        * config.max_risk_per_trade,
        "INR"
    )

    print(
        "Daily loss cap:",
        config.capital_inr
        * config.daily_loss_limit,
        "INR"
    )

    # -----------------------------------------
    # Test loop
    # -----------------------------------------

    print(
        "\n=== STARTING 300-SECOND TEST ==="
    )

    end_time = (
        time.monotonic()
        + TEST_SECONDS
    )

    candle_start = time.time()
    candle_open = None
    candle_high = None
    candle_low = None
    candle_close = None

    while time.monotonic() < end_time:

        now = time.time()

        book = get_orderbook()

        bids = book.get(
            "bids",
            {}
        )

        asks = book.get(
            "asks",
            {}
        )

        if not bids or not asks:

            time.sleep(1)
            continue

        best_bid = max(
            Decimal(str(x))
            for x in bids
        )

        best_ask = min(
            Decimal(str(x))
            for x in asks
        )

        mid = (
            best_bid
            + best_ask
        ) / Decimal("2")

        prices.append(mid)

        if len(prices) > 200:
            prices.pop(0)

        # -------------------------------------
        # Build 1-second candles
        # -------------------------------------

        if candle_open is None:

            candle_open = mid
            candle_high = mid
            candle_low = mid
            candle_close = mid
            candle_start = now

        else:

            candle_high = max(
                candle_high,
                mid
            )

            candle_low = min(
                candle_low,
                mid
            )

            candle_close = mid

        if now - candle_start >= 1:

            candles.append(
                {
                    "open": candle_open,
                    "high": candle_high,
                    "low": candle_low,
                    "close": candle_close,
                }
            )

            if len(candles) > 100:
                candles.pop(0)

            candle_open = None

        # -------------------------------------
        # Calculate indicators
        # -------------------------------------

        imbalance = (
            strategy.orderbook_imbalance(
                bids,
                asks
            )
        )

        atr_value = strategy.atr(
            candles
        )

        signal, score = (
            strategy.composite_signal(
                prices,
                imbalance,
                atr_value,
                best_bid,
                best_ask,
            )
        )

        # -------------------------------------
        # Daily risk halt
        # -------------------------------------

        if strategy.daily_loss_exceeded():

            print(
                "DAILY LOSS LIMIT REACHED."
            )

            break

        # -------------------------------------
        # Mark current position
        # -------------------------------------

        paper.mark(mid)

        # -------------------------------------
        # Exit
        # -------------------------------------

        if paper.position != 0:

            should_exit, reason = (
                strategy.should_exit(
                    mid,
                    signal,
                    now,
                )
            )

            if should_exit:

                pnl = paper.exit(
                    mid,
                    strategy,
                )

                strategy.close(mid)

                strategy.register_result(
                    pnl,
                    now,
                )

                print(
                    f"EXIT | "
                    f"{reason} | "
                    f"price={mid} | "
                    f"PnL={pnl:.6f} | "
                    f"loss_streak="
                    f"{strategy.consecutive_losses}"
                )

        # -------------------------------------
        # Entry
        # -------------------------------------

        if (
            paper.position == 0
            and signal in ("LONG", "SHORT")
            and strategy.can_trade(now)
            and atr_value is not None
        ):

            if strategy.edge_is_sufficient(
                atr_value,
                mid,
            ):

                quantity = (
                    strategy.position_size(
                        mid,
                        atr_value,
                    )
                )

                if quantity > 0:

                    # Paper fill at bid/ask.
                    price = (
                        best_ask
                        if signal == "LONG"
                        else best_bid
                    )

                    entered = paper.enter(
                        signal,
                        price,
                        quantity,
                        strategy,
                    )

                    if entered:

                        strategy.open_position(
                            signal,
                            price,
                            atr_value,
                            now,
                        )

                        print(
                            f"ENTRY | "
                            f"{signal} | "
                            f"qty={quantity:.2f} | "
                            f"price={price} | "
                            f"score={score:.2f} | "
                            f"ATR={atr_value}"
                        )

        print(
            f"BID={best_bid} "
            f"ASK={best_ask} "
            f"IMB={imbalance:.3f} "
            f"SIGNAL={signal} "
            f"SCORE={score:.2f} "
            f"POS={paper.position} "
            f"REALIZED={paper.realized_pnl:.6f} "
            f"UNREALIZED={paper.unrealized_pnl:.6f}"
        )

        time.sleep(1)

    # -----------------------------------------
    # Final report
    # -----------------------------------------

    paper.mark(mid)

    total_pnl = (
        paper.realized_pnl
        + paper.unrealized_pnl
    )

    return_pct = (
        total_pnl
        / paper.starting_balance
        * Decimal("100")
    )

    win_rate = Decimal("0")

    if paper.exits > 0:

        win_rate = (
            Decimal(str(paper.wins))
            / Decimal(str(paper.exits))
            * Decimal("100")
        )

    print("\n==========================================")
    print(" PAPER TEST COMPLETE")
    print("==========================================")

    print("Pair:", PAIR)

    print(
        "Starting capital:",
        paper.starting_balance,
        "INR"
    )

    print(
        "Entries:",
        paper.entries
    )

    print(
        "Completed exits:",
        paper.exits
    )

    print(
        "Wins:",
        paper.wins
    )

    print(
        "Losses:",
        paper.losses
    )

    print(
        "Win rate:",
        f"{win_rate:.2f}%"
    )

    print(
        "Realized PnL:",
        paper.realized_pnl,
        "INR"
    )

    print(
        "Unrealized PnL:",
        paper.unrealized_pnl,
        "INR"
    )

    print(
        "Total PnL:",
        total_pnl,
        "INR"
    )

    print(
        "Return:",
        f"{return_pct:.4f}%"
    )

    print(
        "Fees:",
        paper.fees,
        "INR"
    )

    print(
        "Max drawdown:",
        paper.max_drawdown,
        "INR"
    )

    print(
        "Final balance:",
        paper.balance,
        "INR"
    )

    print(
        "LIVE ORDERS SENT: 0"
    )


if __name__ == "__main__":
    main()
