from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FuturesConfig:
    capital_inr: Decimal = Decimal("1000")

    # Start at 1x. No leverage amplification for testing.
    leverage: Decimal = Decimal("1")

    # CoinDCX INR-margin futures maker fee = 0.02% = 2 bps.
    maker_fee_bps: Decimal = Decimal("2")

    # Entry + exit.
    round_trip_fee_bps: Decimal = Decimal("4")

    # Extra protection against spread/slippage.
    safety_buffer_bps: Decimal = Decimal("2")

    # Minimum gross price movement required.
    min_edge_bps: Decimal = Decimal("6")

    # Never commit the entire account as margin.
    max_margin_fraction: Decimal = Decimal("0.80")


class DogeFuturesStrategy:

    def __init__(self, config=None):

        self.config = config or FuturesConfig()

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        self.realized_pnl = Decimal("0")
        self.total_fees = Decimal("0")
        self.trade_count = 0

    @property
    def max_margin(self):

        return (
            self.config.capital_inr
            * self.config.max_margin_fraction
        )

    @property
    def required_edge_bps(self):

        return (
            self.config.round_trip_fee_bps
            + self.config.safety_buffer_bps
        )

    def calculate_move_bps(
        self,
        old_price,
        new_price,
    ):

        old_price = Decimal(str(old_price))
        new_price = Decimal(str(new_price))

        if old_price <= 0:
            return Decimal("0")

        return (
            abs(new_price - old_price)
            / old_price
            * Decimal("10000")
        )

    def calculate_fee(self, notional):

        notional = Decimal(str(notional))

        return (
            notional
            * self.config.maker_fee_bps
            / Decimal("10000")
        )

    def choose_direction(
        self,
        previous_mid,
        current_mid,
    ):

        previous_mid = Decimal(str(previous_mid))
        current_mid = Decimal(str(current_mid))

        if current_mid > previous_mid:
            return "LONG"

        if current_mid < previous_mid:
            return "SHORT"

        return None

    def should_enter(
        self,
        previous_mid,
        current_mid,
        bid,
        ask,
    ):

        if self.position != 0:
            return False

        previous_mid = Decimal(str(previous_mid))
        current_mid = Decimal(str(current_mid))
        bid = Decimal(str(bid))
        ask = Decimal(str(ask))

        mid = (
            bid + ask
        ) / Decimal("2")

        if mid <= 0:
            return False

        spread_bps = (
            (ask - bid)
            / mid
            * Decimal("10000")
        )

        # Don't enter if the current spread itself
        # already consumes our available edge.
        if spread_bps >= self.required_edge_bps:
            return False

        movement_bps = self.calculate_move_bps(
            previous_mid,
            current_mid,
        )

        if movement_bps < self.config.min_edge_bps:
            return False

        return True

    def open_position(
        self,
        side,
        price,
        quantity,
    ):

        if self.position != 0:
            return False

        price = Decimal(str(price))
        quantity = Decimal(str(quantity))

        if price <= 0 or quantity <= 0:
            return False

        if side == "LONG":
            self.position = quantity

        elif side == "SHORT":
            self.position = -quantity

        else:
            return False

        self.entry_price = price
        self.entry_side = side

        entry_notional = (
            price * quantity
        )

        self.total_fees += (
            self.calculate_fee(
                entry_notional
            )
        )

        return True

    def should_exit(
        self,
        current_price,
    ):

        if self.position == 0:
            return False

        movement_bps = self.calculate_move_bps(
            self.entry_price,
            current_price,
        )

        return (
            movement_bps
            >= self.config.min_edge_bps
        )

    def close_position(
        self,
        price,
    ):

        if self.position == 0:
            return Decimal("0")

        price = Decimal(str(price))

        quantity = abs(self.position)

        if self.position > 0:

            gross_pnl = (
                price - self.entry_price
            ) * quantity

        else:

            gross_pnl = (
                self.entry_price - price
            ) * quantity

        exit_notional = (
            price * quantity
        )

        exit_fee = self.calculate_fee(
            exit_notional
        )

        net_pnl = (
            gross_pnl
            - exit_fee
        )

        self.realized_pnl += net_pnl

        self.total_fees += exit_fee

        self.trade_count += 1

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None

        return net_pnl
