import logging
import os
from decimal import Decimal
from typing import Dict, List

from pydantic import Field

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import (
    MarketDict,
    OrderType,
    PriceType,
    TradeType,
)
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class SmallEdgePMMConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)

    controllers_config: List[str] = []

    # Exchange / market
    exchange: str = Field("kraken_paper_trade")
    trading_pair: str = Field("DOGE-USD")

    # Small-capital test
    order_amount: Decimal = Field(Decimal("5"))

    # Required round-trip edge.
    # 80 bps = 0.80%
    min_edge_bps: Decimal = Field(Decimal("80"))

    # Give orders time to fill.
    order_refresh_time: int = Field(10)

    price_type: str = Field("mid")

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets[self.exchange] = (
            markets.get(self.exchange, set()) | {self.trading_pair}
        )
        return markets


class SmallEdgePMM(StrategyV2Base):

    create_timestamp = 0
    price_source = PriceType.MidPrice

    def __init__(
        self,
        connectors: Dict[str, ConnectorBase],
        config: SmallEdgePMMConfig,
    ):
        super().__init__(connectors, config)

        self.config = config

        self.price_source = (
            PriceType.LastTrade
            if config.price_type == "last"
            else PriceType.MidPrice
        )

    def on_tick(self):

        if self.create_timestamp > self.current_timestamp:
            return

        # Cancel previous quotes before replacing them.
        self.cancel_all_orders()

        proposal = self.create_proposal()

        # Prevent orders from exceeding available paper balance.
        proposal_adjusted = self.adjust_proposal_to_budget(proposal)

        self.place_orders(proposal_adjusted)

        self.create_timestamp = (
            self.current_timestamp
            + self.config.order_refresh_time
        )

    def create_proposal(self) -> List[OrderCandidate]:

        connector = self.connectors[self.config.exchange]

        mid_price = connector.get_price_by_type(
            self.config.trading_pair,
            self.price_source,
        )

        # Split the required round-trip edge equally
        # between the buy and sell side.
        #
        # Example:
        # 80 bps total
        # BUY  = 40 bps below mid
        # SELL = 40 bps above mid

        half_edge = (
            self.config.min_edge_bps
            / Decimal("2")
            / Decimal("10000")
        )

        buy_price = mid_price * (
            Decimal("1") - half_edge
        )

        sell_price = mid_price * (
            Decimal("1") + half_edge
        )

        buy_order = OrderCandidate(
            trading_pair=self.config.trading_pair,
            is_maker=True,
            order_type=OrderType.LIMIT,
            order_side=TradeType.BUY,
            amount=self.config.order_amount,
            price=buy_price,
        )

        sell_order = OrderCandidate(
            trading_pair=self.config.trading_pair,
            is_maker=True,
            order_type=OrderType.LIMIT,
            order_side=TradeType.SELL,
            amount=self.config.order_amount,
            price=sell_price,
        )

        return [
            buy_order,
            sell_order,
        ]

    def adjust_proposal_to_budget(
        self,
        proposal: List[OrderCandidate],
    ) -> List[OrderCandidate]:

        return self.connectors[
            self.config.exchange
        ].budget_checker.adjust_candidates(
            proposal,
            all_or_none=True,
        )

    def place_orders(
        self,
        proposal: List[OrderCandidate],
    ):

        for order in proposal:

            # IMPORTANT:
            # The previous test generated zero-size BUY
            # orders because of insufficient USD balance.
            # Never submit a zero-sized order.

            if order.amount <= Decimal("0"):
                continue

            if order.order_side == TradeType.BUY:

                self.buy(
                    connector_name=self.config.exchange,
                    trading_pair=order.trading_pair,
                    amount=order.amount,
                    order_type=order.order_type,
                    price=order.price,
                )

            elif order.order_side == TradeType.SELL:

                self.sell(
                    connector_name=self.config.exchange,
                    trading_pair=order.trading_pair,
                    amount=order.amount,
                    order_type=order.order_type,
                    price=order.price,
                )

    def cancel_all_orders(self):

        for order in self.get_active_orders(
            connector_name=self.config.exchange
        ):

            self.cancel(
                self.config.exchange,
                order.trading_pair,
                order.client_order_id,
            )

    def did_fill_order(
        self,
        event: OrderFilledEvent,
    ):

        message = (
            f"FILL | "
            f"{event.trade_type.name} "
            f"{event.amount} "
            f"{event.trading_pair} "
            f"@ {event.price}"
        )

        self.log_with_clock(
            logging.INFO,
            message,
        )

        self.notify_hb_app_with_timestamp(
            message
        )
