import os
import time
import json
import hmac
import hashlib
import requests
from decimal import Decimal

from strategy import DogeFuturesStrategy, FuturesConfig


BASE_URL = "https://api.coindcx.com"

API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_SECRET_KEY")

TEST_SECONDS = 300
TARGET_PAIR = "B-DOGE_USDT"


def public_get(path, params=None):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def signed_get(path, body=None):

    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "CoinDCX API secrets are missing."
        )

    body = body or {}

    # CoinDCX documentation uses timestamp in the
    # authenticated request body.
    body["timestamp"] = int(time.time() * 1000)

    payload = json.dumps(
        body,
        separators=(",", ":"),
    )

    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    response = requests.get(
        BASE_URL + path,
        data=payload,
        headers=headers,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def signed_post(path, body=None):

    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "CoinDCX API secrets are missing."
        )

    body = body or {}

    body["timestamp"] = int(time.time() * 1000)

    payload = json.dumps(
        body,
        separators=(",", ":"),
    )

    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    response = requests.post(
        BASE_URL + path,
        data=payload,
        headers=headers,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def get_active_instruments():

    return public_get(
        "/exchange/v1/derivatives/futures/data/active_instruments",
        {
            "margin_currency_short_name[]": "INR",
        },
    )


def find_doge_instrument(instruments):

    # Exact DOGE only.
    for instrument in instruments:

        if instrument == TARGET_PAIR:
            return instrument

    raise RuntimeError(
        f"{TARGET_PAIR} was not found in active INR futures."
    )


def get_instrument(pair):

    return public_get(
        "/exchange/v1/derivatives/futures/data/instrument",
        {
            "pair": pair,
            "margin_currency_short_name": "INR",
        },
    )


def get_wallet():

    return signed_get(
        "/exchange/v1/derivatives/futures/wallets",
        {},
    )


def get_positions():

    return signed_post(
        "/exchange/v1/derivatives/futures/positions",
        {
            "page": "1",
            "size": "50",
            "margin_currency_short_name": ["INR"],
            "pairs": TARGET_PAIR,
        },
    )


def get_orderbook(pair):

    url = (
        "https://public.coindcx.com/"
        "market_data/v3/orderbook/"
        f"{pair}-futures/50"
    )

    response = requests.get(
        url,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


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

        if notional > strategy.max_margin:
            return False

        fee = strategy.calculate_fee(
            notional
        )

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

    def mark_to_market(self, price):

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

        exit_notional = price * quantity

        exit_fee = strategy.calculate_fee(
            exit_notional
        )

        net = gross - exit_fee

        self.balance += net
        self.realized_pnl += net
        self.fees += exit_fee

        self.exits += 1

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None
        self.unrealized_pnl = Decimal("0")

        return net


def main():

    print(
        "=========================================="
    )

    print(
        " COINDCX DOGE FUTURES PAPER BOT"
    )

    print(
        "=========================================="
    )

    if not API_KEY:
        raise RuntimeError(
            "Missing secret: COINDCX_API_KEY"
        )

    if not API_SECRET:
        raise RuntimeError(
            "Missing secret: COINDCX_SECRET_KEY"
        )

    print("API credentials detected.")

    # ==========================================
    # FIND EXACT DOGE CONTRACT
    # ==========================================

    instruments = get_active_instruments()

    pair = find_doge_instrument(
        instruments
    )

    print(
        "\nSelected instrument:",
        pair,
    )

    # ==========================================
    # INSTRUMENT DETAILS
    # ==========================================

    instrument = get_instrument(pair)

    print("\n=== INSTRUMENT ===")

    print(
        json.dumps(
            instrument,
            indent=2,
        )
    )

    # ==========================================
    # REAL ACCOUNT WALLET
    # ==========================================

    print("\n=== ACCOUNT WALLET ===")

    try:

        wallet = get_wallet()

        print(
            json.dumps(
                wallet,
                indent=2,
            )
        )

    except requests.HTTPError as error:

        if (
            error.response is not None
            and error.response.status_code == 404
        ):

            print(
                "Futures wallet does not exist yet."
            )

            print(
                "Continuing in PAPER-ONLY mode."
            )

        else:

            raise

    # ==========================================
    # REAL POSITIONS
    # ==========================================

    print("\n=== CURRENT DOGE POSITIONS ===")

    try:

        positions = get_positions()

        print(
            json.dumps(
                positions,
                indent=2,
            )
        )

    except requests.HTTPError as error:

        print(
            "Could not read positions:",
            error,
        )

        print(
            "Continuing in PAPER-ONLY mode."
        )

    # ==========================================
    # PAPER STRATEGY
    # ==========================================

    config = FuturesConfig(
        capital_inr=Decimal("1000"),
        leverage=Decimal("1"),
        maker_fee_bps=Decimal("2"),
        round_trip_fee_bps=Decimal("4"),
        safety_buffer_bps=Decimal("2"),
        min_edge_bps=Decimal("6"),
        max_margin_fraction=Decimal("0.80"),
    )

    strategy = DogeFuturesStrategy(
        config
    )

    paper = PaperAccount(
        config.capital_inr
    )

    print("\n=== PAPER ACCOUNT ===")

    print(
        "Virtual capital:",
        paper.starting_balance,
        "INR",
    )

    print(
        "Leverage:",
        config.leverage,
    )

    print(
        "Required edge:",
        strategy.required_edge_bps,
        "bps",
    )

    # ==========================================
    # 5 MINUTE PAPER TEST
    # ==========================================

    print(
        "\n=== STARTING 5-MINUTE PAPER TEST ==="
    )

    end_time = (
        time.time()
        + TEST_SECONDS
    )

    previous_mid = None

    while time.time() < end_time:

        try:

            book = get_orderbook(pair)

            bids = book.get(
                "bids",
                {},
            )

            asks = book.get(
                "asks",
                {},
            )

            if not bids or not asks:

                print(
                    "Order book unavailable."
                )

                time.sleep(1)

                continue

            best_bid = max(
                Decimal(str(price))
                for price in bids.keys()
            )

            best_ask = min(
                Decimal(str(price))
                for price in asks.keys()
            )

            mid = (
                best_bid + best_ask
            ) / Decimal("2")

            spread_bps = (
                (best_ask - best_bid)
                / mid
                * Decimal("10000")
            )

            # ==================================
            # STRATEGY
            # ==================================

            if previous_mid is not None:

                if strategy.should_enter(
                    previous_mid,
                    mid,
                    best_bid,
                    best_ask,
                ):

                    direction = (
                        strategy.choose_direction(
                            previous_mid,
                            mid,
                        )
                    )

                    if direction:

                        quantity = Decimal("1")

                        entry_price = (
                            best_ask
                            if direction == "LONG"
                            else best_bid
                        )

                        entered = paper.enter(
                            direction,
                            entry_price,
                            quantity,
                            strategy,
                        )

                        if entered:

                            strategy.open_position(
                                direction,
                                entry_price,
                                quantity,
                            )

                            print(
                                f"ENTRY | "
                                f"{direction} | "
                                f"{quantity} DOGE | "
                                f"@ {entry_price}"
                            )

                # ==================================
                # EXIT
                # ==================================

                if paper.position != 0:

                    paper.mark_to_market(
                        mid
                    )

                    if strategy.should_exit(
                        mid
                    ):

                        pnl = paper.exit(
                            mid,
                            strategy,
                        )

                        strategy.close_position(
                            mid
                        )

                        print(
                            f"EXIT | "
                            f"price={mid} | "
                            f"net_pnl={pnl:.6f} INR"
                        )

            previous_mid = mid

            print(
                f"BID={best_bid} "
                f"ASK={best_ask} "
                f"SPREAD={spread_bps:.2f}bps "
                f"POSITION={paper.position} "
                f"PNL={paper.realized_pnl:.6f}"
            )

        except Exception as error:

            print(
                "MARKET ERROR:",
                repr(error),
            )

        time.sleep(1)

    # ==========================================
    # FINAL REPORT
    # ==========================================

    if paper.position != 0:

        paper.mark_to_market(
            previous_mid
        )

    print(
        "\n=========================================="
    )

    print(
        " PAPER TEST COMPLETE"
    )

    print(
        "=========================================="
    )

    print(
        "Pair:",
        pair,
    )

    print(
        "Starting balance:",
        paper.starting_balance,
        "INR",
    )

    print(
        "Realized PnL:",
        paper.realized_pnl,
        "INR",
    )

    print(
        "Unrealized PnL:",
        paper.unrealized_pnl,
        "INR",
    )

    print(
        "Fees:",
        paper.fees,
        "INR",
    )

    print(
        "Entries:",
        paper.entries,
    )

    print(
        "Exits:",
        paper.exits,
    )

    print(
        "Final paper balance:",
        paper.balance,
        "INR",
    )

    print(
        "\nNO LIVE ORDERS WERE SENT."
    )


if __name__ == "__main__":
    main()
