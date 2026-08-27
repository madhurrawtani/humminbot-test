import os
import time
import json
import hmac
import hashlib
import requests
from decimal import Decimal


BASE_URL = "https://api.coindcx.com"

# GitHub Actions Secrets
API_KEY = os.getenv("COINDCX_API_KEY")
API_SECRET = os.getenv("COINDCX_SECRET_KEY")

CAPITAL_INR = Decimal("1000")
LEVERAGE = Decimal("1")

MAKER_FEE_BPS = Decimal("2")
MIN_EDGE_BPS = Decimal("6")

TARGET = "DOGE"


def signed_request(method, path, body=None):

    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "CoinDCX API secrets are missing."
        )

    body = body or {}

    body["timestamp"] = int(
        time.time() * 1000
    )

    payload = json.dumps(
        body,
        separators=(",", ":")
    )

    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }

    response = requests.request(
        method,
        BASE_URL + path,
        data=payload,
        headers=headers,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def get_active_instruments():

    url = (
        BASE_URL
        + "/exchange/v1/derivatives/futures/data/"
        + "active_instruments"
    )

    response = requests.get(
        url,
        params={
            "margin_currency_short_name[]": "INR"
        },
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def find_doge_instrument(instruments):

    matches = []

    for instrument in instruments:

        text = str(instrument).upper()

        if TARGET in text:
            matches.append(instrument)

    if not matches:

        raise RuntimeError(
            "DOGE INR-margin futures instrument not found."
        )

    print("\nDOGE instruments found:")

    for item in matches:
        print(item)

    return matches[0]


def get_instrument(pair):

    url = (
        BASE_URL
        + "/exchange/v1/derivatives/futures/data/"
        + "instrument"
    )

    response = requests.get(
        url,
        params={
            "pair": pair,
            "margin_currency_short_name": "INR",
        },
        timeout=10,
    )

    response.raise_for_status()

    return response.json()


def get_wallet():

    return signed_request(
        "GET",
        "/exchange/v1/derivatives/futures/wallets",
    )


def get_positions():

    return signed_request(
        "POST",
        "/exchange/v1/derivatives/futures/positions",
        {
            "page": "1",
            "size": "50",
            "margin_currency_short_name": ["INR"],
        },
    )


def get_open_orders():

    return signed_request(
        "POST",
        "/exchange/v1/derivatives/futures/orders",
        {
            "status": "open",
            "page": "1",
            "size": "50",
            "margin_currency_short_name": ["INR"],
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


class PaperEngine:

    def __init__(self):

        self.balance = CAPITAL_INR

        self.position = Decimal("0")

        self.entry_price = Decimal("0")

        self.realized_pnl = Decimal("0")

        self.total_fees = Decimal("0")

        self.trade_count = 0

    def mark_to_market(self, price):

        if self.position == 0:
            return Decimal("0")

        return (
            price - self.entry_price
        ) * self.position

    def print_status(self, price):

        unrealized = self.mark_to_market(price)

        print(
            f"POSITION={self.position} "
            f"ENTRY={self.entry_price} "
            f"PRICE={price} "
            f"REALIZED={self.realized_pnl:.4f} "
            f"UNREALIZED={unrealized:.4f}"
        )


def main():

    print(
        "=== CoinDCX DOGE INR FUTURES PAPER BOT ==="
    )

    if not API_KEY:
        raise RuntimeError(
            "Missing GitHub Secret: COINDCX_API_KEY"
        )

    if not API_SECRET:
        raise RuntimeError(
            "Missing GitHub Secret: COINDCX_SECRET_KEY"
        )

    print("API credentials detected.")

    # -------------------------------------------------
    # 1. Find active INR-margin futures instruments
    # -------------------------------------------------

    instruments = get_active_instruments()

    pair = find_doge_instrument(
        instruments
    )

    print(
        "\nSelected DOGE instrument:",
        pair
    )

    # -------------------------------------------------
    # 2. Verify instrument details
    # -------------------------------------------------

    instrument = get_instrument(pair)

    print("\n=== INSTRUMENT DETAILS ===")

    print(
        json.dumps(
            instrument,
            indent=2
        )
    )

    # -------------------------------------------------
    # 3. Read authenticated futures wallet
    # -------------------------------------------------

    print("\n=== FUTURES WALLET ===")

    wallet = get_wallet()

    print(
        json.dumps(
            wallet,
            indent=2
        )
    )

    # -------------------------------------------------
    # 4. Read current positions
    # -------------------------------------------------

    print("\n=== CURRENT POSITIONS ===")

    positions = get_positions()

    print(
        json.dumps(
            positions,
            indent=2
        )
    )

    # -------------------------------------------------
    # 5. Read open orders
    # -------------------------------------------------

    print("\n=== OPEN ORDERS ===")

    orders = get_open_orders()

    print(
        json.dumps(
            orders,
            indent=2
        )
    )

    # -------------------------------------------------
    # 6. Start PAPER engine
    # -------------------------------------------------

    paper = PaperEngine()

    print("\n=== PAPER ENGINE ===")

    print(
        "Virtual capital:",
        CAPITAL_INR,
        "INR"
    )

    print(
        "Leverage:",
        LEVERAGE
    )

    print(
        "Maker fee:",
        MAKER_FEE_BPS,
        "bps"
    )

    print(
        "Minimum edge:",
        MIN_EDGE_BPS,
        "bps"
    )

    # -------------------------------------------------
    # 7. Read market for 5 minutes
    # -------------------------------------------------

    print(
        "\n=== LIVE MARKET DATA / PAPER MODE ==="
    )

    end_time = time.time() + 300

    while time.time() < end_time:

        try:

            book = get_orderbook(pair)

            bids = book.get(
                "bids",
                {}
            )

            asks = book.get(
                "asks",
                {}
            )

            if not bids or not asks:

                print(
                    "Order book unavailable..."
                )

                time.sleep(1)

                continue

            best_bid = max(
                (
                    Decimal(str(price))
                    for price in bids.keys()
                )
            )

            best_ask = min(
                (
                    Decimal(str(price))
                    for price in asks.keys()
                )
            )

            mid = (
                best_bid + best_ask
            ) / Decimal("2")

            spread_bps = (
                (best_ask - best_bid)
                / mid
                * Decimal("10000")
            )

            print(
                f"BID={best_bid} "
                f"ASK={best_ask} "
                f"MID={mid} "
                f"SPREAD={spread_bps:.2f}bps"
            )

            paper.print_status(mid)

        except Exception as error:

            print(
                "Market-data error:",
                error
            )

        time.sleep(1)

    # -------------------------------------------------
    # 8. Final result
    # -------------------------------------------------

    print("\n=== PAPER TEST COMPLETE ===")

    print(
        "Trades:",
        paper.trade_count
    )

    print(
        "Realized PnL:",
        paper.realized_pnl,
        "INR"
    )

    print(
        "Fees:",
        paper.total_fees,
        "INR"
    )

    print(
        "Final paper balance:",
        paper.balance,
        "INR"
    )

    print(
        "\nNO LIVE ORDERS WERE SENT."
    )


if __name__ == "__main__":
    main()
