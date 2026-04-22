from datamodel import Listing, Observation, Order, OrderDepth, ProsperityEncoder, Symbol, Trade, TradingState
from typing import Any, Dict, List, Optional, Tuple

import json
import numpy as np
import math


POS_LIMITS = {
    "ASH_COATED_OSMIUM": 80,
    "INTARIAN_PEPPER_ROOT": 80,
}

### Add this to get logs and run local backtester

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(
            self.to_json(
                [
                    self.compress_state(state, ""),
                    self.compress_orders(orders),
                    conversions,
                    "",
                    "",
                ]
            )
        )

        max_item_length = (self.max_log_length - base_length) // 3

        print(
            self.to_json(
                [
                    self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                    self.compress_orders(orders),
                    conversions,
                    self.truncate(trader_data, max_item_length),
                    self.truncate(self.logs, max_item_length),
                ]
            )
        )

        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [
            state.timestamp,
            trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings: dict[str, Listing]) -> list[list[Any]]:
        compressed = []
        for listing in listings.values():
            compressed.append([listing.symbol, listing.product, listing.denomination])
        return compressed

    def compress_order_depths(self, order_depths: dict[str, OrderDepth]) -> dict[str, list[Any]]:
        compressed = {}
        for symbol, order_depth in order_depths.items():
            compressed[symbol] = [order_depth.buy_orders, order_depth.sell_orders]
        return compressed

    def compress_trades(self, trades: dict[str, list[Trade]]) -> list[list[Any]]:
        compressed = []
        for arr in trades.values():
            for trade in arr:
                compressed.append(
                    [
                        trade.symbol,
                        trade.price,
                        trade.quantity,
                        trade.buyer,
                        trade.seller,
                        trade.timestamp,
                    ]
                )
        return compressed

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, observation in observations.conversionObservations.items():
            conversion_observations[product] = [
                observation.bidPrice,
                observation.askPrice,
                observation.transportFees,
                observation.exportTariff,
                observation.importTariff,
                observation.sugarPrice,
                observation.sunlightIndex,
            ]
        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders: dict[str, list[Order]]) -> list[list[Any]]:
        compressed = []
        for arr in orders.values():
            for order in arr:
                compressed.append([order.symbol, order.price, order.quantity])
        return compressed

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        lo, hi = 0, min(len(value), max_length)
        out = ""

        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = value[:mid]
            if len(candidate) < len(value):
                candidate += "..."
            encoded_candidate = json.dumps(candidate)
            if len(encoded_candidate) <= max_length:
                out = candidate
                lo = mid + 1
            else:
                hi = mid - 1

        return out


logger = Logger()


### Base Trader Class

class BaseTrader:
    def __init__(self, name, state, new_trader_data):

        self.orders = []
        self.name = name
        self.state = state
        self.new_trader_data = new_trader_data

        self.last_traderData = self.get_last_traderData()
        self.position_limit = POS_LIMITS.get(self.name, 0)
        self.initial_position = self.state.position.get(self.name, 0)

        self.mkt_buy_orders, self.mkt_sell_orders = self.get_order_depth()
        self.bid_wall, self.wall_mid, self.ask_wall = self.get_walls()
        self.best_bid, self.best_ask = self.get_best_bid_ask()
        self.max_allowed_buy_volume, self.max_allowed_sell_volume = self.get_max_allowed_volume()
        self.total_mkt_buy_volume, self.total_mkt_sell_volume = self.get_total_market_buy_sell_volume()

    def get_last_traderData(self):
        last_traderData = {}
        try:
            if self.state.traderData != '':
                last_traderData = json.loads(self.state.traderData)
        except:
            pass
        return last_traderData

    def get_best_bid_ask(self):
        best_bid = best_ask = None
        try:
            if len(self.mkt_buy_orders) > 0:
                best_bid = max(self.mkt_buy_orders.keys())
            if len(self.mkt_sell_orders) > 0:
                best_ask = min(self.mkt_sell_orders.keys())
        except:
            pass
        return best_bid, best_ask

    def get_matched_volume_mid(self) -> Optional[float]:
        """Volume-weighted mid across bid/ask levels that share the same resting size."""
        if not self.mkt_buy_orders or not self.mkt_sell_orders:
            return None

        bid_prices_by_volume: Dict[int, List[int]] = {}
        ask_prices_by_volume: Dict[int, List[int]] = {}
        for price, volume in self.mkt_buy_orders.items():
            bid_prices_by_volume.setdefault(abs(int(volume)), []).append(int(price))
        for price, volume in self.mkt_sell_orders.items():
            ask_prices_by_volume.setdefault(abs(int(volume)), []).append(int(price))

        common_volumes = set(bid_prices_by_volume.keys()) & set(ask_prices_by_volume.keys())
        if not common_volumes:
            return None

        weighted_mid_sum = 0.0
        total_weight = 0
        for volume in common_volumes:
            best_bid_for_vol = max(bid_prices_by_volume[volume])
            best_ask_for_vol = min(ask_prices_by_volume[volume])
            mid_for_volume = (best_bid_for_vol + best_ask_for_vol) / 2
            weighted_mid_sum += mid_for_volume * volume
            total_weight += volume

        if total_weight == 0:
            return None
        return weighted_mid_sum / total_weight

    def get_walls(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        bid_wall = wall_mid = ask_wall = None
        try:
            bid_wall = min([x for x, _ in self.mkt_buy_orders.items()])
        except:
            pass
        try:
            ask_wall = max([x for x, _ in self.mkt_sell_orders.items()])
        except:
            pass
        try:
            wall_mid = (bid_wall + ask_wall) / 2
        except:
            pass
        return bid_wall, wall_mid, ask_wall

    def get_total_market_buy_sell_volume(self):
        market_bid_volume = market_ask_volume = 0
        try:
            market_bid_volume = sum([v for p, v in self.mkt_buy_orders.items()])
            market_ask_volume = sum([v for p, v in self.mkt_sell_orders.items()])
        except:
            pass
        return market_bid_volume, market_ask_volume

    def get_max_allowed_volume(self):
        max_allowed_buy_volume = self.position_limit - self.initial_position
        max_allowed_sell_volume = self.position_limit + self.initial_position
        return max_allowed_buy_volume, max_allowed_sell_volume

    def get_order_depth(self):
        order_depth, buy_orders, sell_orders = {}, {}, {}
        try:
            order_depth: OrderDepth = self.state.order_depths[self.name]
        except:
            pass
        try:
            buy_orders = {bp: abs(bv) for bp, bv in sorted(order_depth.buy_orders.items(), key=lambda x: x[0], reverse=True)}
        except:
            pass
        try:
            sell_orders = {sp: abs(sv) for sp, sv in sorted(order_depth.sell_orders.items(), key=lambda x: x[0])}
        except:
            pass
        return buy_orders, sell_orders

    def bid(self, price, volume):
        abs_volume = min(abs(int(volume)), self.max_allowed_buy_volume)
        order = Order(self.name, int(price), abs_volume)
        self.max_allowed_buy_volume -= abs_volume
        self.orders.append(order)

    def ask(self, price, volume):
        # NOTE: sell orders must carry a negative quantity per the datamodel convention.
        abs_volume = min(abs(int(volume)), self.max_allowed_sell_volume)
        order = Order(self.name, int(price), -abs_volume)   # fixed: was +abs_volume (bug)
        self.max_allowed_sell_volume -= abs_volume
        self.orders.append(order)

    ### Overwrite this method in subTrader class
    def place_orders(self):
        return {}


# ---------------------------------------------------------------------------
# IntarianTrader — passive market-maker with drift-based fair value
# ---------------------------------------------------------------------------

class IntarianTrader(BaseTrader):

    ORDER_SIZE = 12
    SELL_PREMIUM_OVER_FAIR = 5

    def __init__(self, state, new_trader_data):
        super().__init__("INTARIAN_PEPPER_ROOT", state, new_trader_data)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _round_to_nearest_thousand(price: float) -> int:
        """Round half-up to the nearest 1 000 (e.g. 12 500 → 13 000)."""
        return int(math.floor((price + 500.0) / 1000.0) * 1000)

    def _get_fair_value(self, persisted: dict) -> Optional[float]:
        """
        Fair = base + 0.001 * timestamp.
        Base is anchored once to the first observed mid, rounded to the nearest 1 000.
        """
        fair_bases = persisted.setdefault("fair_bases", {})
        base = fair_bases.get(self.name)

        if base is None and self.best_bid is not None and self.best_ask is not None:
            midpoint = (float(self.best_bid) + float(self.best_ask)) / 2.0
            base = self._round_to_nearest_thousand(midpoint)
            fair_bases[self.name] = int(base)

        if base is None:
            return None

        return float(base) + 0.001 * float(self.state.timestamp)

    def _update_last_sold_price(self, persisted: dict) -> None:
        """Persist the price of our most recent sell fill; clear it after a buy fill."""
        own = self.state.own_trades.get(self.name, [])
        if not own:
            return

        processed_ts = persisted.setdefault("last_processed_own_trade_ts", {})
        last_seen_ts = int(processed_ts.get(self.name, -1))
        last_sold_price = persisted.setdefault("last_sold_price", {})

        for trade in sorted(own, key=lambda t: t.timestamp):
            if trade.timestamp <= last_seen_ts:
                continue
            last_seen_ts = int(trade.timestamp)
            if trade.seller == "SUBMISSION":
                last_sold_price[self.name] = int(trade.price)
            elif trade.buyer == "SUBMISSION":
                last_sold_price.pop(self.name, None)

        processed_ts[self.name] = last_seen_ts

    # -- main ---------------------------------------------------------------

    def place_orders(self) -> dict:
        # Load persisted state for this product, namespaced by product name.
        persisted = self.last_traderData.get(self.name, {})

        # Detect day rollover (timestamp reset) and archive yesterday's quotes.
        last_timestamp = persisted.get("last_timestamp")
        if last_timestamp is not None and self.state.timestamp < last_timestamp:
            persisted["prev_day_quotes"] = persisted.get("day_quotes", {})
            persisted["day_quotes"] = {}
            persisted["last_processed_own_trade_ts"] = {}
            persisted["last_sold_price"] = {}

        day_quotes = persisted.setdefault("day_quotes", {})
        prev_day_quotes = persisted.get("prev_day_quotes", {})

        # Keep a running record of the best bid/ask seen today for fallback.
        if self.best_bid is not None:
            day_quotes["best_bid"] = self.best_bid
        if self.best_ask is not None:
            day_quotes["best_ask"] = self.best_ask

        # Resolve live → today's record → yesterday's record.
        best_bid = (self.best_bid
                    or day_quotes.get("best_bid")
                    or prev_day_quotes.get("best_bid"))
        best_ask = (self.best_ask
                    or day_quotes.get("best_ask")
                    or prev_day_quotes.get("best_ask"))

        fair_value = self._get_fair_value(persisted)
        self._update_last_sold_price(persisted)

        pos   = self.initial_position
        limit = self.position_limit

        # ---- Phase 1: bootstrap (first 1 000 ticks) — accumulate long ----
        if self.state.timestamp < 1000:
            remaining_room = max(0, limit - pos)

            if remaining_room > 0 and best_ask is not None:
                # Passive: quote buy at best_ask - 3
                buy_quote = best_ask - 3
                buy_qty = min(9, remaining_room)
                if buy_qty > 0 and buy_quote > 0:
                    self.bid(buy_quote, buy_qty)
                    remaining_room -= buy_qty

                # Aggressive: lift the best ask with any remaining room
                best_ask_volume = self.mkt_sell_orders.get(best_ask, 0)
                take_qty = min(best_ask_volume, remaining_room)
                if take_qty > 0:
                    self.bid(best_ask, take_qty)

        # ---- Phase 2: passive two-sided market-making ---------------------
        elif best_bid is not None and best_ask is not None and best_ask > best_bid:
            buy_quote = best_bid + 1
            sell_quote = best_ask - 1

            # Push sell quote up to maintain a minimum premium over fair value.
            if fair_value is not None:
                sell_quote = max(sell_quote,
                                 int(fair_value) + self.SELL_PREMIUM_OVER_FAIR)

            # Safety: never cross our own quotes.
            if buy_quote >= sell_quote:
                buy_quote = sell_quote - 1

            buy_qty  = max(0, min(self.ORDER_SIZE, limit - pos))
            sell_qty = max(0, min(self.ORDER_SIZE, limit + pos))

            if buy_qty > 0 and buy_quote > 0 and buy_quote <= best_ask - 1:
                self.bid(buy_quote, buy_qty)

            if sell_qty > 0 and sell_quote > 0:
                self.ask(sell_quote, sell_qty)

        persisted["last_timestamp"] = self.state.timestamp
        self.new_trader_data[self.name] = persisted

        return {self.name: self.orders}


# ---------------------------------------------------------------------------
# AshTrader — inventory-aware taker + maker with matched-volume fair price
# ---------------------------------------------------------------------------

class AshTrader(BaseTrader):

    TAKE_CAP               = 10
    MAKE_SIZE              = 10
    INVENTORY_SKEW_THRESHOLD = 30
    EDGE_FROM_FAIR         = 4
    HISTORY_KEEP           = 5

    def __init__(self, state, new_trader_data):
        super().__init__("ASH_COATED_OSMIUM", state, new_trader_data)

        # Restore matched-mid history from persisted state.
        persisted = self.last_traderData.get(self.name, {})
        hist = persisted.get("matched_mid_history", [])
        self.matched_hist: List[float] = [
            float(x) for x in hist[-self.HISTORY_KEEP:]
            if isinstance(x, (int, float))
        ]

    # -- fair price ---------------------------------------------------------

    def _get_fair(self) -> float:
        """
        Priority: matched-volume mid → average of last two stored MVMs
                  → wall mid (BaseTrader.wall_mid) → 10 000.
        """
        mvm = self.get_matched_volume_mid()   # BaseTrader helper
        if mvm is not None:
            return float(mvm)
        tail = self.matched_hist[-2:]
        if tail:
            return sum(tail) / len(tail)
        if self.wall_mid is not None:         # BaseTrader: (min_bid + max_ask) / 2
            return float(self.wall_mid)
        return 10_000.0

    # -- taker layer --------------------------------------------------------

    def _add_taker_orders(self, fair: float, pos: int, inventory_stressed: bool) -> int:
        """
        Normal mode   : buy when fair − ask ≥ EDGE_FROM_FAIR; sell when bid − fair ≥ EDGE_FROM_FAIR.
        Stressed long  : sell into bids ≥ fair + 1 to unwind.
        Stressed short : lift asks ≤ fair − 1 to unwind.
        Returns updated position.

        Uses BaseTrader.max_allowed_* inside loops (bid/ask update them). Mixed buy/sell in one tick
        does not auto-adjust the opposite limit, so we resync from ``pos`` before the normal-mode
        sell pass and once more at exit for downstream maker/flatten.
        """
        if inventory_stressed:
            if pos > self.INVENTORY_SKEW_THRESHOLD:
                for bid_price in sorted(self.mkt_buy_orders.keys(), reverse=True):
                    if self.max_allowed_sell_volume <= 0 or float(bid_price) < fair + 1.0:
                        break
                    sell_qty = min(
                        self.mkt_buy_orders[bid_price],
                        self.max_allowed_sell_volume,
                        self.TAKE_CAP,
                    )
                    if sell_qty > 0:
                        self.ask(bid_price, sell_qty)
                        pos -= sell_qty

            elif pos < -self.INVENTORY_SKEW_THRESHOLD:
                for ask_price in sorted(self.mkt_sell_orders.keys()):
                    if self.max_allowed_buy_volume <= 0 or float(ask_price) > fair - 1.0:
                        break
                    buy_qty = min(
                        self.mkt_sell_orders[ask_price],
                        self.max_allowed_buy_volume,
                        self.TAKE_CAP,
                    )
                    if buy_qty > 0:
                        self.bid(ask_price, buy_qty)
                        pos += buy_qty

            self._sync_volume_limits_from_position(pos)
            return pos

        # Normal: only cross when edge is sufficient (buys then sells).
        for ask_price in sorted(self.mkt_sell_orders.keys()):
            if fair - float(ask_price) < self.EDGE_FROM_FAIR:
                break
            if self.max_allowed_buy_volume <= 0:
                break
            buy_qty = min(
                self.mkt_sell_orders[ask_price],
                self.max_allowed_buy_volume,
                self.TAKE_CAP,
            )
            if buy_qty > 0:
                self.bid(ask_price, buy_qty)
                pos += buy_qty

        # ``ask()`` does not increase buy room; ``pos`` does — refresh sell envelope after buys.
        self.max_allowed_sell_volume = max(0, self.position_limit + pos)

        for bid_price in sorted(self.mkt_buy_orders.keys(), reverse=True):
            if float(bid_price) - fair < self.EDGE_FROM_FAIR:
                break
            if self.max_allowed_sell_volume <= 0:
                break
            sell_qty = min(
                self.mkt_buy_orders[bid_price],
                self.max_allowed_sell_volume,
                self.TAKE_CAP,
            )
            if sell_qty > 0:
                self.ask(bid_price, sell_qty)
                pos -= sell_qty

        self._sync_volume_limits_from_position(pos)
        return pos

    def _sync_volume_limits_from_position(self, pos: int) -> None:
        """Align caps with simulated inventory after aggressive fills (bid/ask don't bump opposite side)."""
        self.max_allowed_buy_volume = max(0, self.position_limit - pos)
        self.max_allowed_sell_volume = max(0, self.position_limit + pos)

    # -- maker layer --------------------------------------------------------

    def _add_maker_orders(self, fair: float, pos: int, inventory_stressed: bool) -> None:
        """
        Two-sided book  : bid = best_bid + 1, ask = best_ask − 1 (requires spread ≥ 2).
        One-sided book  : anchor the missing side to round(fair) ± 1.
        Empty book      : bid = round(fair) − 1, ask = round(fair) + 1.

        Normal: only rest a quote when it is at least EDGE_FROM_FAIR ticks from fair.
        Stressed: quote only the reducing side, no edge filter.
        """
        fair_anchor = int(round(fair))
        best_bid = self.best_bid
        best_ask = self.best_ask

        bid_quote: Optional[int] = None
        ask_quote: Optional[int] = None

        if best_bid is not None and best_ask is not None:
            if best_ask - best_bid < 2:
                return
            bid_quote = best_bid + 1
            ask_quote = best_ask - 1
        elif best_bid is None and best_ask is not None:
            bid_quote = fair_anchor + 1
            ask_quote = best_ask - 1
        elif best_bid is not None and best_ask is None:
            bid_quote = best_bid + 1
            ask_quote = fair_anchor - 1
        else:
            bid_quote = fair_anchor - 1
            ask_quote = fair_anchor + 1

        bid_size = min(self.MAKE_SIZE, self.max_allowed_buy_volume)
        ask_size = min(self.MAKE_SIZE, self.max_allowed_sell_volume)

        def ok_bid() -> bool:
            if bid_quote is None:
                return False
            if best_ask is not None and bid_quote >= best_ask:
                return False
            if ask_quote is not None and bid_quote >= ask_quote:
                return False
            return True

        def ok_ask() -> bool:
            if ask_quote is None:
                return False
            if best_bid is not None and ask_quote <= best_bid:
                return False
            if bid_quote is not None and ask_quote <= bid_quote:
                return False
            return True

        if inventory_stressed:
            # Only quote the side that reduces inventory.
            if pos > self.INVENTORY_SKEW_THRESHOLD:
                bid_size = 0
            elif pos < -self.INVENTORY_SKEW_THRESHOLD:
                ask_size = 0
        else:
            # Enforce minimum edge from fair in normal mode.
            if fair - float(bid_quote) < self.EDGE_FROM_FAIR:
                bid_size = 0
            if float(ask_quote) - fair < self.EDGE_FROM_FAIR:
                ask_size = 0

        if bid_size > 0 and ok_bid():
            self.bid(bid_quote, bid_size)
        if ask_size > 0 and ok_ask():
            self.ask(ask_quote, ask_size)

    # -- inventory flatten --------------------------------------------------

    def _add_inventory_flatten_orders(self, fair: float, pos: int) -> None:
        """
        Passive unwind when inventory is skewed past threshold:
        long  → sell at round(fair) − 1
        short → buy  at round(fair) + 1
        """
        anchor = int(round(fair))

        if pos > self.INVENTORY_SKEW_THRESHOLD:
            excess = pos - self.INVENTORY_SKEW_THRESHOLD
            sell_qty = min(self.MAKE_SIZE, excess, self.max_allowed_sell_volume)
            if sell_qty > 0:
                self.ask(anchor - 1, sell_qty)

        elif pos < -self.INVENTORY_SKEW_THRESHOLD:
            excess = -pos - self.INVENTORY_SKEW_THRESHOLD
            buy_qty = min(self.MAKE_SIZE, excess, self.max_allowed_buy_volume)
            if buy_qty > 0:
                self.bid(anchor + 1, buy_qty)

    # -- main ---------------------------------------------------------------

    def place_orders(self) -> dict:
        fair = self._get_fair()
        pos  = self.initial_position
        inventory_stressed = abs(pos) > self.INVENTORY_SKEW_THRESHOLD

        # Extend matched-mid history with this tick's observation (if valid).
        mvm = self.get_matched_volume_mid()
        if mvm is not None:
            self.matched_hist.append(mvm)
            self.matched_hist = self.matched_hist[-self.HISTORY_KEEP:]

        # Layer 1: aggressive taker orders (also updates pos).
        pos = self._add_taker_orders(fair, pos, inventory_stressed)

        # Layer 2: passive maker quotes.
        self._add_maker_orders(fair, pos, inventory_stressed)

        # Layer 3: passive inventory unwind.
        self._add_inventory_flatten_orders(fair, pos)

        # Persist state for next tick, namespaced by product name.
        self.new_trader_data[self.name] = {
            "matched_mid_history": self.matched_hist,
            "last_fair_price": fair,
        }

        return {self.name: self.orders}


### Main loop for trading
class Trader:

    def run(self, state: TradingState):
        result: dict[str, list[Order]] = {}
        new_trader_data = {}

        product_traders = {
            "INTARIAN_PEPPER_ROOT": IntarianTrader,
            "ASH_COATED_OSMIUM":    AshTrader,
        }

        conversions = 0
        for symbol, product_trader in product_traders.items():
            if symbol in state.order_depths:
                try:
                    trader = product_trader(state, new_trader_data)
                    result.update(trader.place_orders())
                except:
                    pass

        try:
            final_trader_data = json.dumps(new_trader_data)
        except:
            final_trader_data = ''

        logger.flush(state, result, conversions, final_trader_data)
        return result, conversions, final_trader_data
