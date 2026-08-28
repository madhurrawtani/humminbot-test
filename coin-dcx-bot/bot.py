import os
import time
import json
import hmac
import hashlib
import requests
from decimal import Decimal

from strategy import DogeFuturesStrategy, FuturesConfig


BASE_URL = "https://api.coindcx.com"
PAIR = "B-DOGE_USDT"
TEST_SECONDS = 300


API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_SECRET_KEY")


def signed_get(path, body=None):
    body = body or {}
    body["timestamp"] = int(time.time() * 1000)

    payload = json.dumps(
        body,
        separators=(",", ":")
    )

    signature = hmac.new(
        API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
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


def public_get(path, params=None):
    r = requests.get(
        BASE_URL + path,
        params=params,
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


def get_positions():
    return signed_get(
        "/exchange/v1/derivatives/futures/positions",
        {
            "page": "1",
            "size": "50",
            "pairs": PAIR,
            "margin_currency_short_name": ["INR"],
        },
    )


def get_orderbook():
    r = requests.get(
        f"https://public.coindcx.com/"
        f"market_data/v3/orderbook/{PAIR}-futures/50",
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


class PaperAccount:

    def __init__(self, capital):
        self.starting_balance = Decimal(str(capital))
        self.balance = Decimal(str(capital))

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        self.realized_pnl = Decimal("0")
        self.unrealized_pnl = Decimal("0")
        self.fees = Decimal("0")

        self.entries = 0
        self.exits = 0

    def mark(self, price):
        if self.position == 0:
            self.unrealized_pnl = Decimal("0")
            return

        price = Decimal(str(price))

        if self.position > 0:
            self.unrealized_pnl = (
                price - self.entry_price
            ) * abs(self.position)
        else:
            self.unrealized_pnl = (
                self.entry_price - price
            ) * abs(self.position)

    def enter(self, side, price, quantity, strategy):

        if self.position != 0:
            return False

        price = Decimal(str(price))
        quantity = Decimal(str(quantity))

        notional = price * quantity

        if notional > strategy.max_margin:
            return False

        fee = strategy.fee(notional)

        if fee > self.balance:
            return False

        self.balance -= fee
        self.fees += fee

        self.position = (
            quantity if side == "LONG"
            else -quantity
        )

        self.entry_price = price
        self.entry_side = side
        self.entries += 1

        return True

    def exit(self, price, strategy):

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

        net = gross - entry_fee - exit_fee

        self.balance += gross - exit_fee
        self.realized_pnl += net
        self.fees += exit_fee
        self.exits += 1

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
        raise RuntimeError("Missing COINDCX_API_KEY")

    if not API_SECRET:
        raise RuntimeError("Missing COINDCX_SECRET_KEY")

    print("API credentials detected.")
    print("Pair:", PAIR)
    print("Test duration:", TEST_SECONDS, "seconds")
    print("Live orders: DISABLED")

    # -----------------------------------------
    # Instrument
    # -----------------------------------------

    instrument = get_instrument()

    print("\n=== INSTRUMENT ===")
    print(json.dumps(instrument, indent=2))

    # -----------------------------------------
    # Wallet
    # -----------------------------------------

    print("\n=== FUTURES WALLET ===")

    try:
        wallet = get_wallet()
        print(json.dumps(wallet, indent=2))
    except requests.HTTPError as e:
        print(
            "Wallet unavailable; continuing paper-only:",
            e
        )

    # -----------------------------------------
    # Positions
    # -----------------------------------------

    print("\n=== POSITIONS ===")

    try:
        positions = get_positions()
        print(json.dumps(positions, indent=2))
    except requests.HTTPError as e:
        print(
            "Positions unavailable; continuing paper-only:",
            e
        )

    # -----------------------------------------
    # Strategy
    # -----------------------------------------

    config = FuturesConfig(
        capital_inr=Decimal("1000"),
        leverage=Decimal("1"),
        maker_fee_bps=Decimal("2.36"),
        round_trip_fee_bps=Decimal("4.72"),
        profit_buffer_bps=Decimal("1.00"),
        max_margin_fraction=Decimal("0.80"),
    )

    strategy = DogeFuturesStrategy(config)
    paper = PaperAccount(config.capital_inr)

    print("\n=== PAPER ACCOUNT ===")
    print("Capital:", config.capital_inr, "INR")
    print("Max margin:", strategy.max_margin)
    print(
        "Minimum net edge:",
        strategy.minimum_net_edge_bps,
        "bps"
    )

    # -----------------------------------------
    # 5-minute market loop
    # -----------------------------------------

    print("\n=== STARTING 300-SECOND TEST ===")

    end_time = time.monotonic() + TEST_SECONDS
    previous_mid = None

    while time.monotonic() < end_time:

        book = get_orderbook()

        bids = book.get("bids", {})
        asks = book.get("asks", {})

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
            best_bid + best_ask
        ) / Decimal("2")

        spread = strategy.spread_bps(
            best_bid,
            best_ask
        )

        paper.mark(mid)

        # -------------------------------------
        # Paper position management
        # -------------------------------------

        if paper.position != 0:

            if strategy.should_exit(
                paper.entry_price,
                mid,
                time.time(),
            ):

                pnl = paper.exit(
                    mid,
                    strategy
                )

                strategy.close(mid)

                print(
                    f"EXIT | "
                    f"{mid} | "
                    f"PnL={pnl:.6f}"
                )

        # -------------------------------------
        # Entry
        # -------------------------------------

        if (
            paper.position == 0
            and previous_mid is not None
        ):

            if strategy.should_enter(
                previous_mid,
                mid,
                best_bid,
                best_ask,
            ):

                direction = strategy.choose_direction(
                    previous_mid,
                    mid
                )

                if direction:

                    quantity = Decimal("1")

                    price = (
                        best_ask
                        if direction == "LONG"
                        else best_bid
                    )

                    if paper.enter(
                        direction,
                        price,
                        quantity,
                        strategy,
                    ):

                        strategy.open(
                            direction,
                            price,
                            quantity,
                            time.time(),
                        )

                        print(
                            f"ENTRY | "
                            f"{direction} | "
                            f"{price}"
                        )

        previous_mid = mid

        print(
            f"BID={best_bid} "
            f"ASK={best_ask} "
            f"SPREAD={spread:.3f}bps "
            f"POS={paper.position} "
            f"REALIZED={paper.realized_pnl:.6f} "
            f"UNREALIZED={paper.unrealized_pnl:.6f}"
        )

        time.sleep(1)

    # -----------------------------------------
    # Final report
    # -----------------------------------------

    if paper.position != 0:
        paper.mark(previous_mid)

    print("\n==========================================")
    print(" PAPER TEST COMPLETE")
    print("==========================================")

    print("Pair:", PAIR)
    print("Starting:", paper.starting_balance, "INR")
    print("Entries:", paper.entries)
    print("Exits:", paper.exits)
    print("Realized PnL:", paper.realized_pnl, "INR")
    print("Unrealized PnL:", paper.unrealized_pnl, "INR")
    print("Fees:", paper.fees, "INR")
    print("Final balance:", paper.balance, "INR")
    print("LIVE ORDERS: NONE")


if __name__ == "__main__":
    main()
