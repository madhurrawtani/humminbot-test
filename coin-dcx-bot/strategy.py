from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FuturesConfig:
    capital_inr: Decimal = Decimal("1000")

    # Initial effective exposure = 1x.
    leverage: Decimal = Decimal("1")

    # CoinDCX INR-margin futures maker fee.
    maker_fee_bps: Decimal = Decimal("2")

    # Two maker executions: entry + exit.
    round_trip_fee_bps: Decimal = Decimal("4")

    # Small safety buffer above fees.
    safety_buffer_bps: Decimal = Decimal("2")

    min_edge_bps: Decimal = Decimal("6")

    # Never use the entire account as active margin.
    max_margin_fraction: Decimal = Decimal("0.80")


class DogeFuturesStrategy:

    def __init__(self, config: FuturesConfig):
        self.config = config

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        self.realized_pnl = Decimal("0")
        self.total_fees = Decimal("0")

    @property
    def max_margin(self):
        return (
            self.config.capital_inr
            * self.config.max_margin_fraction
        )

    def required_edge_bps(self):
        return (
            self.config.round_trip_fee_bps
            + self.config.safety_buffer_bps
        )

    def price_move_bps(self, entry_price, current_price):

        if entry_price <= 0:
            return Decimal("0")

        return (
            abs(current_price - entry_price)
            / entry_price
            * Decimal("10000")
        )

    def should_enter(self, bid, ask):

        if self.position != 0:
            return False

        mid = (bid + ask) / Decimal("2")

        spread_bps = (
            (ask - bid)
            / mid
            * Decimal("10000")
        )

        # Don't enter when the spread itself is too expensive.
        if spread_bps >= self.required_edge_bps():
            return False

        return True

    def choose_direction(self, previous_mid, current_mid):

        if current_mid > previous_mid:
            return "LONG"

        if current_mid < previous_mid:
            return "SHORT"

        return None

    def should_exit(self, entry_price, current_price):

        move = self.price_move_bps(
            entry_price,
            current_price,
        )

        return move >= self.config.min_edge_bps

    def calculate_fee(self, notional):

        return (
            notional
            * self.config.maker_fee_bps
            / Decimal("10000")
        )

    def open_position(
        self,
        side,
        price,
        quantity,
    ):

        if self.position != 0:
            return False

        self.position = (
            quantity
            if side == "LONG"
            else -quantity
        )

        self.entry_price = price
        self.entry_side = side

        return True

    def close_position(
        self,
        price,
    ):

        if self.position == 0:
            return Decimal("0")

        quantity = abs(self.position)

        if self.position > 0:
            gross_pnl = (
                price - self.entry_price
            ) * quantity

        else:
            gross_pnl = (
                self.entry_price - price
            ) * quantity

        entry_notional = (
            self.entry_price * quantity
        )

        exit_notional = (
            price * quantity
        )

        entry_fee = self.calculate_fee(
            entry_notional
        )

        exit_fee = self.calculate_fee(
            exit_notional
        )

        fees = entry_fee + exit_fee

        net_pnl = gross_pnl - fees

        self.realized_pnl += net_pnl
        self.total_fees += fees

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        return net_pnl
