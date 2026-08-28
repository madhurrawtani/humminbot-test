from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FuturesConfig:
    capital_inr: Decimal = Decimal("1000")

    # Maximum allowed leverage for the strategy.
    max_leverage: Decimal = Decimal("5")

    # Risk controls.
    risk_per_trade: Decimal = Decimal("0.01")       # 1% = ₹10
    max_risk_per_trade: Decimal = Decimal("0.02")   # 2% = ₹20
    daily_loss_limit: Decimal = Decimal("0.05")     # 5% = ₹50

    # Circuit breaker.
    max_consecutive_losses: int = 3
    cooldown_seconds: int = 3600

    # CoinDCX DOGE futures instrument data.
    maker_fee_bps: Decimal = Decimal("2.36")

    # Conservative allowance for the complete round trip.
    round_trip_fee_bps: Decimal = Decimal("4.72")

    # Extra buffer above fees.
    slippage_buffer_bps: Decimal = Decimal("1.00")

    # Market filters.
    max_spread_pct: Decimal = Decimal("0.10")
    imbalance_window: int = 20
    candle_window: int = 20

    # Momentum.
    fast_ema_period: int = 5
    slow_ema_period: int = 13

    # ATR.
    atr_period: int = 14
    stop_atr_multiplier: Decimal = Decimal("1.5")
    target_atr_multiplier: Decimal = Decimal("2.0")

    # Signal.
    minimum_score: Decimal = Decimal("0.60")

    # Position timeout.
    max_position_seconds: int = 300


class DogeFuturesStrategy:

    def __init__(self, config=None):
        self.config = config or FuturesConfig()

        self.position = Decimal("0")
        self.entry_price = Decimal("0")
        self.entry_time = 0
        self.entry_side = None

        self.stop_price = Decimal("0")
        self.target_price = Decimal("0")

        self.realized_pnl = Decimal("0")
        self.total_fees = Decimal("0")

        self.consecutive_losses = 0
        self.cooldown_until = 0

    # --------------------------------------------------
    # Fees
    # --------------------------------------------------

    def fee(self, notional):
        return (
            Decimal(str(notional))
            * self.config.maker_fee_bps
            / Decimal("10000")
        )

    @property
    def required_edge_bps(self):
        return (
            self.config.round_trip_fee_bps
            + self.config.slippage_buffer_bps
        )

    # --------------------------------------------------
    # Basic market calculations
    # --------------------------------------------------

    def mid_price(self, bid, ask):
        return (
            Decimal(str(bid))
            + Decimal(str(ask))
        ) / Decimal("2")

    def spread_pct(self, bid, ask):
        mid = self.mid_price(bid, ask)

        if mid <= 0:
            return Decimal("999")

        return (
            (Decimal(str(ask)) - Decimal(str(bid)))
            / mid
            * Decimal("100")
        )

    # --------------------------------------------------
    # Order-book imbalance
    # --------------------------------------------------

    def orderbook_imbalance(self, bids, asks):

        bid_volume = sum(
            Decimal(str(qty))
            for qty in bids.values()
        )

        ask_volume = sum(
            Decimal(str(qty))
            for qty in asks.values()
        )

        total = bid_volume + ask_volume

        if total <= 0:
            return Decimal("0")

        return (
            bid_volume - ask_volume
        ) / total

    # --------------------------------------------------
    # EMA
    # --------------------------------------------------

    def ema(self, values, period):

        if len(values) < period:
            return None

        values = [
            Decimal(str(x))
            for x in values
        ]

        multiplier = (
            Decimal("2")
            / Decimal(str(period + 1))
        )

        result = values[0]

        for value in values[1:]:
            result = (
                (value - result)
                * multiplier
                + result
            )

        return result

    # --------------------------------------------------
    # ROC
    # --------------------------------------------------

    def roc(self, prices, period=5):

        if len(prices) <= period:
            return Decimal("0")

        old = Decimal(str(prices[-period - 1]))
        new = Decimal(str(prices[-1]))

        if old <= 0:
            return Decimal("0")

        return (
            (new - old)
            / old
            * Decimal("100")
        )

    # --------------------------------------------------
    # ATR
    # --------------------------------------------------

    def atr(self, candles):

        if len(candles) < self.config.atr_period + 1:
            return None

        true_ranges = []

        previous_close = None

        for candle in candles:

            high = Decimal(str(candle["high"]))
            low = Decimal(str(candle["low"]))
            close = Decimal(str(candle["close"]))

            if previous_close is None:

                tr = high - low

            else:

                tr = max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )

            true_ranges.append(tr)
            previous_close = close

        period = self.config.atr_period

        return (
            sum(true_ranges[-period:])
            / Decimal(str(period))
        )

    # --------------------------------------------------
    # Volatility regime
    # --------------------------------------------------

    def volatility_regime(
        self,
        atr_value,
        price,
    ):

        if atr_value is None or price <= 0:
            return "UNKNOWN"

        atr_pct = (
            atr_value
            / price
            * Decimal("100")
        )

        # Conservative starting regime bands.
        if atr_pct < Decimal("0.15"):
            return "LOW"

        if atr_pct > Decimal("1.50"):
            return "EXTREME"

        if atr_pct > Decimal("0.80"):
            return "HIGH"

        return "NORMAL"

    # --------------------------------------------------
    # Composite signal
    # --------------------------------------------------

    def composite_signal(
        self,
        prices,
        imbalance,
        atr_value,
        bid,
        ask,
    ):

        if len(prices) < self.config.slow_ema_period:
            return "NONE", Decimal("0")

        mid = self.mid_price(bid, ask)

        spread = self.spread_pct(
            bid,
            ask,
        )

        if spread > self.config.max_spread_pct:
            return "NONE", Decimal("0")

        regime = self.volatility_regime(
            atr_value,
            mid,
        )

        if regime in ("UNKNOWN", "EXTREME"):
            return "NONE", Decimal("0")

        fast = self.ema(
            prices,
            self.config.fast_ema_period,
        )

        slow = self.ema(
            prices,
            self.config.slow_ema_period,
        )

        if fast is None or slow is None:
            return "NONE", Decimal("0")

        roc = self.roc(
            prices,
            5,
        )

        score = Decimal("0")

        # 40% order-book imbalance.
        if imbalance > Decimal("0.20"):
            score += Decimal("0.40")

        elif imbalance < Decimal("-0.20"):
            score -= Decimal("0.40")

        # 35% EMA direction.
        if fast > slow:
            score += Decimal("0.35")

        elif fast < slow:
            score -= Decimal("0.35")

        # 25% short-term ROC.
        if roc > Decimal("0.05"):
            score += Decimal("0.25")

        elif roc < Decimal("-0.05"):
            score -= Decimal("0.25")

        if score >= self.config.minimum_score:
            return "LONG", score

        if score <= -self.config.minimum_score:
            return "SHORT", score

        return "NONE", score

    # --------------------------------------------------
    # Expected edge
    # --------------------------------------------------

    def expected_edge_bps(
        self,
        atr_value,
        price,
    ):

        if atr_value is None or price <= 0:
            return Decimal("0")

        atr_bps = (
            atr_value
            / price
            * Decimal("10000")
        )

        expected_move = (
            atr_bps
            * self.config.target_atr_multiplier
        )

        return expected_move

    def edge_is_sufficient(
        self,
        atr_value,
        price,
    ):

        return (
            self.expected_edge_bps(
                atr_value,
                price,
            )
            > self.required_edge_bps
        )

    # --------------------------------------------------
    # Risk-based position sizing
    # --------------------------------------------------

    def position_size(
        self,
        price,
        atr_value,
    ):

        if atr_value is None or price <= 0:
            return Decimal("0")

        risk_amount = (
            self.config.capital_inr
            * self.config.risk_per_trade
        )

        stop_distance = (
            atr_value
            * self.config.stop_atr_multiplier
        )

        if stop_distance <= 0:
            return Decimal("0")

        quantity = (
            risk_amount
            / stop_distance
        )

        # Respect 5x leverage.
        max_notional = (
            self.config.capital_inr
            * self.config.max_leverage
        )

        max_quantity = (
            max_notional
            / price
        )

        if quantity > max_quantity:
            quantity = max_quantity

        return quantity

    # --------------------------------------------------
    # Position management
    # --------------------------------------------------

    def open_position(
        self,
        side,
        price,
        atr_value,
        timestamp,
    ):

        if self.position != 0:
            return False

        if atr_value is None:
            return False

        quantity = self.position_size(
            price,
            atr_value,
        )

        if quantity <= 0:
            return False

        price = Decimal(str(price))

        stop_distance = (
            atr_value
            * self.config.stop_atr_multiplier
        )

        target_distance = (
            atr_value
            * self.config.target_atr_multiplier
        )

        if side == "LONG":

            self.position = quantity

            self.stop_price = (
                price - stop_distance
            )

            self.target_price = (
                price + target_distance
            )

        elif side == "SHORT":

            self.position = -quantity

            self.stop_price = (
                price + stop_distance
            )

            self.target_price = (
                price - target_distance
            )

        else:
            return False

        self.entry_price = price
        self.entry_side = side
        self.entry_time = timestamp

        return True

    def should_exit(
        self,
        price,
        current_signal,
        timestamp,
    ):

        if self.position == 0:
            return False, "NONE"

        price = Decimal(str(price))

        if self.position > 0:

            if price <= self.stop_price:
                return True, "STOP"

            if price >= self.target_price:
                return True, "TARGET"

            if current_signal == "SHORT":
                return True, "REVERSAL"

        else:

            if price >= self.stop_price:
                return True, "STOP"

            if price <= self.target_price:
                return True, "TARGET"

            if current_signal == "LONG":
                return True, "REVERSAL"

        if (
            timestamp - self.entry_time
            >= self.config.max_position_seconds
        ):
            return True, "TIMEOUT"

        return False, "NONE"

    # --------------------------------------------------
    # Cooldown
    # --------------------------------------------------

    def can_trade(self, timestamp):

        return timestamp >= self.cooldown_until

    def register_result(
        self,
        net_pnl,
        timestamp,
    ):

        net_pnl = Decimal(str(net_pnl))

        if net_pnl < 0:

            self.consecutive_losses += 1

        else:

            self.consecutive_losses = 0

        if (
            self.consecutive_losses
            >= self.config.max_consecutive_losses
        ):

            self.cooldown_until = (
                timestamp
                + self.config.cooldown_seconds
            )

    # --------------------------------------------------
    # Daily risk
    # --------------------------------------------------

    def daily_loss_exceeded(self):

        loss_limit = (
            self.config.capital_inr
            * self.config.daily_loss_limit
        )

        return (
            self.realized_pnl
            <= -loss_limit
        )
