# Adding a product trader

The entry point is `Trader.run` in `trader.py`. It loops over a **symbol → class** map, builds one instance per product that has an order book that round, then merges their orders.

## 1. Register the product and position limit

In `POS_LIMITS`, add the same string the engine uses for the product (e.g. `"MY_NEW_GOOD": 80`).

## 2. Subclass `BaseTrader`

- **Constructor** must look like: `def __init__(self, state, new_trader_data):` and call  
  `super().__init__("<PRODUCT_SYMBOL>", state, new_trader_data)`  
  with the **exact** symbol used in `state.order_depths` and `POS_LIMITS`.

- **Override** `place_orders(self) -> dict[str, list[Order]]`.  
  The default return shape is **one key**: your product symbol, and the value is the list of orders for that product, e.g. `return {self.name: self.orders}`.

## 3. What `BaseTrader` gives you

| Use this | For |
|----------|-----|
| `self.name` | Product symbol (same as the key in `POS_LIMITS`) |
| `self.state` | Full `TradingState` (timestamp, positions, `own_trades`, `order_depths`, etc.) |
| `self.position_limit` | From `POS_LIMITS` |
| `self.initial_position` | Your position at the start of the round |
| `self.mkt_buy_orders` / `self.mkt_sell_orders` | Book for **this** product (asks are stored with positive sizes here) |
| `self.best_bid` / `self.best_ask` | Best prices on that book |
| `self.get_matched_volume_mid()` | Optional fair mid from “same size on bid and ask” |
| `self.bid(price, volume)` / `self.ask(price, volume)` | Append a buy or sell to `self.orders` and **respect** remaining size vs. the position limit |
| `self.last_traderData` | `json.loads(state.traderData)` if valid, else `{}` (full blob from the last submission) |
| `self.new_trader_data` | **Shared** dict; you should **write** your serialisable state here so it is returned to the engine (see below) |

For **sells**, prefer `self.ask(...)` (or `Order(..., negative quantity)` to match the datamodel). **Buys** are positive quantity.

## 4. `last_traderData` and `new_trader_data`

These are two sides of the same persistence pipe: **read what you saved last time**, **write what you want saved for next time**.

| | **`last_traderData`** | **`new_trader_data`** |
|--|----------------------|------------------------|
| **Role** | **Input** from the engine | **Output** you fill for the engine |
| **Where it comes from** | `BaseTrader.get_last_traderData()` parses `state.traderData` (full JSON blob from your **previous** submission) | `Trader.run` creates **one shared** `dict` per round and passes it into each product trader |
| **Typical use** | `self.last_traderData.get(self.name, {})` to restore `matched_mid_history`, fair anchors, day quotes, etc. | `self.new_trader_data[self.name] = { ... }` with updated fields your strategy needs next tick |

**Tick-to-tick flow**

1. **End of previous tick:** `Trader.run` returns `final_trader_data = json.dumps(new_trader_data)`. The simulator stores that string.
2. **Start of this tick:** `state.traderData` **is** that string. After `super().__init__`, `self.last_traderData` holds the parsed dict (`{}` if empty or invalid JSON).
3. **`place_orders`:** Read fields from `last_traderData` (often under `self.name`), update your logic in memory (e.g. append to `matched_hist`).
4. **Before return:** Assign `self.new_trader_data[self.name] = { ... serialisable snapshot ... }` so Ash/Intarian each store state under their product symbol.
5. **Next tick:** Step 1 runs again — whatever you wrote to `new_trader_data` becomes the next `state.traderData`, which loads into `last_traderData` again.

So: **`last_traderData`** = last saved file; **`place_orders`** = read–modify in memory; **`new_trader_data`** = what you commit for the **next** round. If you never assign `new_trader_data[self.name]`, your strategy loses cross-tick memory (unless the engine merges other keys).

## 5. Persisting state across ticks

- **Read** previous data for *this* product: e.g. `persisted = self.last_traderData.get(self.name, {})`.
- **Write** before returning: `self.new_trader_data[self.name] = <dict you can json serialise>`.

`Trader.run` does `json.dumps(new_trader_data)` and the engine stores that in the next call’s `state.traderData`, so only **JSON-friendly** values (dicts, lists, numbers, strings, bools).

## 6. Register the class

In `Trader.run`, add an entry to `product_traders`:

```python
product_traders = {
    "INTARIAN_PEPPER_ROOT": IntarianTrader,
    "ASH_COATED_OSMIUM": AshTrader,
    "MY_NEW_GOOD": MyNewTrader,
}
```

The loop only runs your trader **`if symbol in state.order_depths`**, so your code is skipped when that product has no book that timestep.

## 7. Minimal skeleton

```python
class MyNewTrader(BaseTrader):
    def __init__(self, state, new_trader_data):
        super().__init__("MY_NEW_GOOD", state, new_trader_data)

    def place_orders(self) -> dict:
        persisted = self.last_traderData.get(self.name, {})
        # ... strategy ...
        self.new_trader_data[self.name] = persisted  # optional
        return {self.name: self.orders}
```

See `IntarianTrader` and `AshTrader` in `trader.py` for full examples.
