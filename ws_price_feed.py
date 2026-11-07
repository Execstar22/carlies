# =============================================================================
# Polymarket BTC Up/Down Bot — WebSocket Real-time Price Feed
# =============================================================================
# Streams live price updates from Polymarket CLOB WebSocket API.
# Replaces REST polling for price data (bot.py fetch_price calls).
#
# Usage:
#   feed = WebSocketPriceFeed(on_price_update=callback)
#   feed.subscribe(up_token_id, down_token_id)
#   feed.start()   # runs in background thread
#   feed.stop()
# =============================================================================

import json
import time
import asyncio
import logging
import threading
import ssl
from datetime import datetime, timezone

log = logging.getLogger("ws_feed")


class WebSocketPriceFeed:
    """Real-time price feed via Polymarket CLOB WebSocket.

    Maintains a persistent WebSocket connection and streams best_bid/ask
    prices for subscribed token IDs.  Falls back gracefully when the
    connection drops.
    """

    def __init__(self, ws_url, reconnect_delay=2, max_reconnect=5,
                 ping_interval=30, on_price_update=None):
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay
        self.max_reconnect = max_reconnect
        self.ping_interval = ping_interval
        self.on_price_update = on_price_update

        # Current subscription
        self._up_token = None
        self._down_token = None

        # Cached prices (thread-safe via GIL for simple reads/writes)
        self._prices = {}          # token_id -> {"buy": float, "sell": float, "mid": float}
        self._last_update = {}     # token_id -> timestamp

        # Connection state
        self._connected = False
        self._running = False
        self._thread = None
        self._loop = None
        self._reconnect_count = 0
        self._total_messages = 0
        self._ws = None

        # Fallback flag: True when WS is down and bot should use REST
        self.use_rest_fallback = False

    # -- Public API -----------------------------------------------------------

    def subscribe(self, up_token_id, down_token_id):
        """Set token IDs to subscribe to.  Can be called before or after start()."""
        changed = (up_token_id != self._up_token or
                   down_token_id != self._down_token)
        self._up_token = up_token_id
        self._down_token = down_token_id

        if changed:
            self._prices = {}
            self._last_update = {}
            log.info(f"📡 WS subscribe: UP={up_token_id[:16]}... DOWN={down_token_id[:16]}...")

            # If already connected, re-subscribe on the fly
            if self._connected and self._ws and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._send_subscription(), self._loop
                )

    def get_price(self, token_id, side="buy"):
        """Get latest cached price for a token.  Returns None if unavailable."""
        entry = self._prices.get(token_id)
        if entry is None:
            return None
        return entry.get(side)

    def get_midpoint(self, token_id):
        """Get latest cached midpoint price."""
        entry = self._prices.get(token_id)
        if entry is None:
            return None
        return entry.get("mid")

    def get_age(self, token_id):
        """Seconds since last update for this token.  Returns None if never updated."""
        ts = self._last_update.get(token_id)
        if ts is None:
            return None
        return time.time() - ts

    @property
    def connected(self):
        return self._connected

    def start(self):
        """Start WebSocket feed in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self.use_rest_fallback = False
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ws-price-feed"
        )
        self._thread.start()
        log.info("📡 WebSocket price feed thread started")

    def stop(self):
        """Stop the WebSocket feed."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        log.info("📡 WebSocket price feed stopped")

    def status(self):
        """Return status dict for dashboard display."""
        return {
            "connected": self._connected,
            "use_rest_fallback": self.use_rest_fallback,
            "reconnect_count": self._reconnect_count,
            "total_messages": self._total_messages,
            "subscribed_tokens": [self._up_token, self._down_token],
            "cached_prices": dict(self._prices),
        }

    # -- Internal: Event Loop -------------------------------------------------

    def _run_loop(self):
        """Entry point for the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            log.error(f"WS feed loop crashed: {e}")
        finally:
            self._loop.close()
            self._connected = False

    async def _connect_loop(self):
        """Outer reconnection loop with exponential backoff."""
        try:
            import websockets
        except ImportError:
            log.error("❌ websockets library not installed! Run: pip install websockets>=12.0")
            self.use_rest_fallback = True
            return

        delay = self.reconnect_delay

        while self._running:
            try:
                log.info(f"📡 Connecting to {self.ws_url}...")

                # SSL context for secure WebSocket connection
                ssl_context = ssl.create_default_context()

                async with websockets.connect(
                    self.ws_url,
                    ssl=ssl_context,
                    ping_interval=self.ping_interval,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._reconnect_count = 0
                    self.use_rest_fallback = False
                    delay = self.reconnect_delay  # Reset backoff
                    log.info("✅ WebSocket connected!")

                    # Send subscription
                    await self._send_subscription()

                    # Message loop
                    async for message in ws:
                        if not self._running:
                            break
                        self._handle_message(message)

            except Exception as e:
                self._connected = False
                self._ws = None
                self._reconnect_count += 1

                if self._reconnect_count >= self.max_reconnect:
                    log.warning(
                        f"⚠️ WS reconnect failed {self._reconnect_count}x. "
                        f"Falling back to REST polling."
                    )
                    self.use_rest_fallback = True
                    delay = min(delay * 2, 60)
                else:
                    log.warning(
                        f"⚠️ WS disconnected ({e}). "
                        f"Reconnecting in {delay}s... "
                        f"(attempt {self._reconnect_count}/{self.max_reconnect})"
                    )

                if self._running:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)

    async def _send_subscription(self):
        """Send subscription message for current token IDs."""
        if not self._ws or not self._up_token:
            return

        assets = []
        if self._up_token:
            assets.append(self._up_token)
        if self._down_token:
            assets.append(self._down_token)

        if not assets:
            return

        sub_msg = {
            "assets_ids": assets,
            "type": "market",
        }

        try:
            await self._ws.send(json.dumps(sub_msg))
            log.info(f"📡 WS subscribed to {len(assets)} tokens")
        except Exception as e:
            log.warning(f"WS subscription send failed: {e}")

    # -- Internal: Message Handling -------------------------------------------

    def _handle_message(self, raw):
        """Parse and process a single WebSocket message."""
        self._total_messages += 1

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Polymarket WS can send arrays (list) or objects (dict)
        # Handle arrays first to avoid 'list has no attribute get' error
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._handle_single_message(item)
            return

        if isinstance(data, dict):
            self._handle_single_message(data)

    def _handle_single_message(self, data):
        """Process a single dict message from WebSocket."""
        event_type = data.get("event_type", "")

        if event_type == "price_change":
            self._handle_price_change(data)
        elif event_type == "book":
            self._handle_book_snapshot(data)
        elif event_type == "tick_size_change":
            pass  # Ignore
        elif event_type == "last_trade_price":
            self._handle_last_trade(data)
        else:
            self._handle_generic(data)

    def _handle_price_change(self, data):
        token_id = data.get("asset_id", "")
        if not token_id:
            return
        price = data.get("price")
        if price is not None:
            self._update_price(token_id, buy=float(price))

    def _handle_book_snapshot(self, data):
        token_id = data.get("asset_id", "")
        if not token_id:
            return
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None

        updates = {}
        if best_bid is not None:
            updates["sell"] = best_bid
        if best_ask is not None:
            updates["buy"] = best_ask
        if best_bid and best_ask:
            updates["mid"] = (best_bid + best_ask) / 2

        if updates:
            self._update_price(token_id, **updates)

    def _handle_last_trade(self, data):
        token_id = data.get("asset_id", "")
        price = data.get("price")
        if token_id and price is not None:
            self._update_price(token_id, buy=float(price))

    def _handle_generic(self, data):
        """Try to extract price from unknown message format."""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self._handle_generic(item)
            return

        token_id = data.get("asset_id") or data.get("token_id", "")
        if not token_id:
            return

        for field in ("price", "best_ask", "best_bid", "mid", "last_price"):
            val = data.get(field)
            if val is not None:
                try:
                    price = float(val)
                    if field in ("best_ask", "price"):
                        self._update_price(token_id, buy=price)
                    elif field == "best_bid":
                        self._update_price(token_id, sell=price)
                    elif field == "mid":
                        self._update_price(token_id, mid=price)
                    break
                except (ValueError, TypeError):
                    pass

    def _update_price(self, token_id, **kwargs):
        """Update cached price and trigger callback."""
        if token_id not in self._prices:
            self._prices[token_id] = {}

        for key, val in kwargs.items():
            if val is not None:
                self._prices[token_id][key] = val

        self._last_update[token_id] = time.time()

        if self.on_price_update:
            for key, val in kwargs.items():
                if val is not None:
                    try:
                        self.on_price_update(token_id, val, key)
                    except Exception:
                        pass
