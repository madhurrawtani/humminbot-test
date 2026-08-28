from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FuturesConfig:
    capital_inr: Decimal = Decimal("10000")
    leverage: Decimal = Decimal("1")

    maker_fee_bps: Decimal = Decimal("2.36")
    round_trip_fee_bps: Decimal = Decimal("4.72")

    profit_buffer_bps: Decimal = Decimal("1.00")

    max_margin_fraction: Decimal = Decimal("0.80")

    max_position_seconds: int = 60

    # Use a reasonable fraction of available paper capital
    # for each position.
    position_margin_fraction: Decimal = Decimal("0.10")


class DogeFuturesStrategy:

    def __init__(self, config=None):

        self.config = config or FuturesConfig()

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None
        self.entry_time = 0

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
    def position_margin(self):

        return (
            self.max_margin
            * self.config.position_margin_fraction
        )

    @property
    def minimum_net_edge_bps(self):

        return (
            self.config.round_trip_fee_bps
            + self.config.profit_buffer_bps
        )

    def fee(self, notional):

        return (
            Decimal(str(notional))
            * self.config.maker_fee_bps
            / Decimal("10000")
        )

    def mid_price(self, bid, ask):

        return (
            Decimal(str(bid))
            + Decimal(str(ask))
        ) / Decimal("2")

    def spread_bps(self, bid, ask):

        bid = Decimal(str(bid))
        ask = Decimal(str(ask))

        mid = self.mid_price(bid, ask)

        if mid <= 0:
            return Decimal("0")

        return (
            (ask - bid)
            / mid
            * Decimal("10000")
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

        if previous_mid <= 0 or current_mid <= 0:
            return False

        if ask <= bid:
            return False

        movement_bps = self.calculate_move_bps(
            previous_mid,
            current_mid,
        )

        # Current strategy signal.
        if movement_bps < Decimal("1.00"):
            return False

        return True

    def should_exit(
        self,
        entry_price,
        current_price,
        current_time,
    ):

        if self.position == 0:
            return False

        entry_price = Decimal(str(entry_price))
        current_price = Decimal(str(current_price))

        if entry_price <= 0:
            return False

        if self.position > 0:

            move_bps = (
                (current_price - entry_price)
                / entry_price
                * Decimal("10000")
            )

        else:

            move_bps = (
                (entry_price - current_price)
                / entry_price
                * Decimal("10000")
            )

        if move_bps >= self.minimum_net_edge_bps:
            return True

        if (
            current_time - self.entry_time
            >= self.config.max_position_seconds
        ):
            return True

        return False

    def open(
        self,
        side,
        price,
        quantity,
        timestamp,
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
        self.entry_time = timestamp

        return True

    def close(self, price):

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

        entry_notional = (
            self.entry_price * quantity
        )

        exit_notional = (
            price * quantity
        )

        entry_fee = self.fee(
            entry_notional
        )

        exit_fee = self.fee(
            exit_notional
        )

        total_fee = (
            entry_fee + exit_fee
        )

        net_pnl = gross_pnl - total_fee

        self.realized_pnl += net_pnl
        self.total_fees += total_fee
        self.trade_count += 1

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_side = None
        self.entry_time = 0

        return net_pnl
