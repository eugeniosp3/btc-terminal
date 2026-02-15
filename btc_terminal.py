"""Real-time BTC price terminal — Binance + Coinbase + Chainlink.

Shows live prices from:
  - Binance WebSocket: BTCUSDT
  - Coinbase WebSocket: BTC-USD, BTC-USDT
  - Chainlink BTC/USD oracle (Ethereum mainnet)

Plus cross-source averages and spreads.

Usage:  python3 btc_terminal.py
"""

import asyncio
import json
import time
import sys

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets")

try:
    import aiohttp
except ImportError:
    sys.exit("pip install aiohttp")


# ── Chainlink BTC/USD on Ethereum mainnet ──────────────────────────
CHAINLINK_BTC_USD = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"
LATEST_ROUND_DATA = "0xfeaf968c"
ETH_RPC = "https://eth.llamarpc.com"
CHAINLINK_POLL_INTERVAL = 5

# ── Binance WebSocket ─────────────────────────────────────────────
BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"

# ── Coinbase WebSocket ─────────────────────────────────────────────
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

# ── Display ────────────────────────────────────────────────────────
REFRESH_MS = 500

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
WHITE = "\033[97m"
MAGENTA = "\033[35m"


class PriceStore:
    def __init__(self):
        self.prices = {}

    def update(self, key: str, price: float):
        self.prices[key] = (price, time.time())

    def get(self, key: str):
        return self.prices.get(key)

    def age(self, key: str) -> float:
        entry = self.prices.get(key)
        if entry is None:
            return float("inf")
        return time.time() - entry[1]


store = PriceStore()


# ── Binance WS ─────────────────────────────────────────────────────

async def binance_ws():
    while True:
        try:
            async with websockets.connect(BINANCE_WS, ping_interval=20) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    price = float(data["p"])
                    store.update("binance_btcusdt", price)
        except Exception as e:
            print(f"\r{DIM}Binance WS error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(3)


# ── Coinbase WS (ticker → best_bid/best_ask midpoint) ─────────────

async def coinbase_ws():
    """Ticker channel with bid/ask midpoint for sub-second updates on BTC-USD."""
    sub_msg = json.dumps({
        "type": "subscribe",
        "channels": [{"name": "ticker", "product_ids": ["BTC-USD", "BTC-USDT"]}],
    })
    store_keys = {"BTC-USD": "coinbase_btcusd", "BTC-USDT": "coinbase_btcusdt"}

    while True:
        try:
            async with websockets.connect(COINBASE_WS, ping_interval=20) as ws:
                await ws.send(sub_msg)
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("type") != "ticker":
                        continue
                    product = data.get("product_id", "")
                    key = store_keys.get(product)
                    if not key:
                        continue
                    bid = float(data.get("best_bid", 0))
                    ask = float(data.get("best_ask", 0))
                    if bid > 0 and ask > 0:
                        store.update(key, (bid + ask) / 2)
                    else:
                        store.update(key, float(data["price"]))
        except Exception as e:
            print(f"\r{DIM}Coinbase WS error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(3)


# ── Chainlink oracle via eth_call ──────────────────────────────────

async def chainlink_poller():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [
                        {"to": CHAINLINK_BTC_USD, "data": LATEST_ROUND_DATA},
                        "latest",
                    ],
                }
                async with session.post(
                    ETH_RPC,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = await resp.json()
                    hex_data = result["result"]
                    answer_hex = hex_data[66:130]
                    answer_int = int(answer_hex, 16)
                    if answer_int >= 2**255:
                        answer_int -= 2**256
                    price = answer_int / 1e8
                    if price > 0:
                        store.update("chainlink_btcusd", price)
            except Exception as e:
                print(f"\r{DIM}Chainlink error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(CHAINLINK_POLL_INTERVAL)


# ── Terminal display ───────────────────────────────────────────────

def fmt_price(price, width=12):
    return f"${price:>{width},.2f}"


def fmt_age(seconds):
    if seconds == float("inf"):
        return "waiting..."
    if seconds < 1:
        return f"{DIM}<1s{RESET}"
    elif seconds < 60:
        return f"{DIM}{seconds:.0f}s{RESET}"
    else:
        return f"{RED}{seconds:.0f}s{RESET}"


def render():
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{CYAN}{'=' * 58}{RESET}")
    lines.append(f"  {BOLD}{CYAN}   BTC PRICE TERMINAL  —  Binance / Coinbase / Chainlink{RESET}")
    lines.append(f"  {BOLD}{CYAN}{'=' * 58}{RESET}")
    lines.append("")

    lines.append(f"  {BOLD}{WHITE}  SOURCE            PAIR          PRICE         AGE{RESET}")
    lines.append(f"  {DIM}  {'─' * 54}{RESET}")

    rows = [
        ("Binance", "BTC/USDT", "binance_btcusdt", GREEN),
        ("Coinbase", "BTC/USD", "coinbase_btcusd", MAGENTA),
        ("Coinbase", "BTC/USDT", "coinbase_btcusdt", MAGENTA),
        ("Chainlink", "BTC/USD", "chainlink_btcusd", YELLOW),
    ]

    for source, pair, key, color in rows:
        entry = store.get(key)
        if entry:
            price_str = fmt_price(entry[0])
            age_str = fmt_age(store.age(key))
        else:
            price_str = f"{'—':>13}"
            age_str = "waiting..."
        lines.append(f"  {color}  {source:<16}{RESET} {pair:<13} {WHITE}{price_str}{RESET}   {age_str}")

    lines.append("")

    # ── Averages ───────────────────────────────────────────────────
    lines.append(f"  {BOLD}{CYAN}  CROSS-SOURCE AVERAGES{RESET}")
    lines.append(f"  {DIM}  {'─' * 54}{RESET}")

    # BTC/USD average: Coinbase + Chainlink
    cb_usd = store.get("coinbase_btcusd")
    cl_usd = store.get("chainlink_btcusd")
    if cb_usd and cl_usd:
        avg = (cb_usd[0] + cl_usd[0]) / 2
        spread = abs(cb_usd[0] - cl_usd[0])
        lines.append(f"  {CYAN}  BTC/USD  avg       {RESET}{WHITE}{fmt_price(avg)}{RESET}   {DIM}spread ${spread:,.2f}{RESET}")
    else:
        lines.append(f"  {CYAN}  BTC/USD  avg       {RESET}{DIM}waiting for both sources...{RESET}")

    # BTC/USDT average: Binance + Coinbase
    bn_usdt = store.get("binance_btcusdt")
    cb_usdt = store.get("coinbase_btcusdt")
    if bn_usdt and cb_usdt:
        avg = (bn_usdt[0] + cb_usdt[0]) / 2
        spread = abs(bn_usdt[0] - cb_usdt[0])
        lines.append(f"  {CYAN}  BTC/USDT avg       {RESET}{WHITE}{fmt_price(avg)}{RESET}   {DIM}spread ${spread:,.2f}{RESET}")
    else:
        lines.append(f"  {CYAN}  BTC/USDT avg       {RESET}{DIM}waiting for both sources...{RESET}")

    # All-source grand average
    all_keys = ["binance_btcusdt", "coinbase_btcusd", "coinbase_btcusdt", "chainlink_btcusd"]
    available = [store.get(k) for k in all_keys]
    prices = [p[0] for p in available if p is not None]
    if len(prices) >= 3:
        grand_avg = sum(prices) / len(prices)
        lines.append(f"  {BOLD}{CYAN}  GRAND AVG          {RESET}{BOLD}{WHITE}{fmt_price(grand_avg)}{RESET}   {DIM}({len(prices)} sources){RESET}")

    lines.append("")
    lines.append(f"  {DIM}  Updated: {time.strftime('%H:%M:%S')}  |  Ctrl+C to exit{RESET}")
    lines.append("")

    return "\n".join(lines)


async def display_loop():
    while True:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write(render())
        sys.stdout.flush()
        await asyncio.sleep(REFRESH_MS / 1000)


# ── Main ───────────────────────────────────────────────────────────

async def main():
    print(f"{CYAN}Starting BTC Price Terminal...{RESET}")
    print(f"{DIM}Connecting to Binance + Coinbase + Chainlink...{RESET}")

    await asyncio.gather(
        binance_ws(),
        coinbase_ws(),
        chainlink_poller(),
        display_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{DIM}Terminal closed.{RESET}")
