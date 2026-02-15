"""Real-time crypto price terminal — Binance + Coinbase + Chainlink.

Shows live prices from:
  - Binance WebSocket: BTC, ETH, SOL, XRP
  - Coinbase WebSocket: BTC, ETH, SOL, XRP
  - Chainlink oracles: BTC, ETH, SOL, XRP (Ethereum mainnet)

Plus cross-source averages and spreads per asset.

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


# ── Assets ───────────────────────────────────────────────────────
ASSETS = ["BTC", "ETH", "SOL", "XRP"]

BINANCE_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

COINBASE_PRODUCTS = {
    "BTC-USD": "BTC", "BTC-USDT": "BTC",
    "ETH-USD": "ETH", "ETH-USDT": "ETH",
    "SOL-USD": "SOL", "SOL-USDT": "SOL",
    "XRP-USD": "XRP", "XRP-USDT": "XRP",
}

# Chainlink price feed addresses on Ethereum mainnet
CHAINLINK_FEEDS = {
    "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
    "ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
    "SOL": "0x4ffC43a60e009B551865A93d232E33Fce9f01507",
    "XRP": "0xCed2660c6Dd1Ffd856A5A82C67f3482d88C50b12",
}

LATEST_ROUND_DATA = "0xfeaf968c"
ETH_RPC = "https://eth.llamarpc.com"
CHAINLINK_POLL_INTERVAL = 5

# ── WebSocket URLs ───────────────────────────────────────────────
BINANCE_STREAMS = "/".join(f"{s}@trade" for s in BINANCE_SYMBOLS.values())
BINANCE_WS = f"wss://stream.binance.com:9443/stream?streams={BINANCE_STREAMS}"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

# ── Display ──────────────────────────────────────────────────────
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
BLUE = "\033[34m"

ASSET_COLORS = {"BTC": YELLOW, "ETH": BLUE, "SOL": MAGENTA, "XRP": GREEN}


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


# ── Binance WS (combined stream) ─────────────────────────────────

async def binance_ws():
    # Reverse lookup: btcusdt -> BTC
    sym_to_asset = {v: k for k, v in BINANCE_SYMBOLS.items()}
    while True:
        try:
            async with websockets.connect(BINANCE_WS, ping_interval=20) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    payload = data.get("data", data)
                    symbol = payload.get("s", "").lower()
                    asset = sym_to_asset.get(symbol)
                    if asset:
                        store.update(f"binance_{asset}", float(payload["p"]))
        except Exception as e:
            print(f"\r{DIM}Binance WS error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(3)


# ── Coinbase WS (ticker for all products) ─────────────────────────

async def coinbase_ws():
    products = list(COINBASE_PRODUCTS.keys())
    sub_msg = json.dumps({
        "type": "subscribe",
        "channels": [{"name": "ticker", "product_ids": products}],
    })

    while True:
        try:
            async with websockets.connect(COINBASE_WS, ping_interval=20) as ws:
                await ws.send(sub_msg)
                async for msg in ws:
                    data = json.loads(msg)
                    if data.get("type") != "ticker":
                        continue
                    product = data.get("product_id", "")
                    asset = COINBASE_PRODUCTS.get(product)
                    if not asset:
                        continue
                    is_usd = product.endswith("-USD")
                    suffix = "usd" if is_usd else "usdt"
                    bid = float(data.get("best_bid", 0))
                    ask = float(data.get("best_ask", 0))
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2
                    else:
                        price = float(data["price"])
                    store.update(f"coinbase_{asset}_{suffix}", price)
        except Exception as e:
            print(f"\r{DIM}Coinbase WS error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(3)


# ── Chainlink oracles via eth_call ────────────────────────────────

async def chainlink_poller():
    async with aiohttp.ClientSession() as session:
        while True:
            for asset, address in CHAINLINK_FEEDS.items():
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_call",
                        "params": [
                            {"to": address, "data": LATEST_ROUND_DATA},
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
                            store.update(f"chainlink_{asset}", price)
                except Exception as e:
                    print(f"\r{DIM}Chainlink {asset} error: {e}{RESET}", end="", flush=True)
            await asyncio.sleep(CHAINLINK_POLL_INTERVAL)


# ── Terminal display ──────────────────────────────────────────────

def fmt_price(price, width=12):
    if price >= 100:
        return f"${price:>{width},.2f}"
    elif price >= 1:
        return f"${price:>{width},.4f}"
    else:
        return f"${price:>{width},.5f}"


def fmt_age(seconds):
    if seconds == float("inf"):
        return "waiting..."
    if seconds < 1:
        return f"{DIM}<1s{RESET}"
    elif seconds < 60:
        return f"{DIM}{seconds:.0f}s{RESET}"
    else:
        return f"{RED}{seconds:.0f}s{RESET}"


def render_asset(asset):
    """Render price rows for one asset."""
    lines = []
    color = ASSET_COLORS.get(asset, WHITE)

    lines.append(f"  {BOLD}{color}  {asset}{RESET}")
    lines.append(f"  {DIM}  {'─' * 56}{RESET}")

    rows = [
        ("Binance", f"{asset}/USDT", f"binance_{asset}"),
        ("Coinbase", f"{asset}/USD", f"coinbase_{asset}_usd"),
        ("Coinbase", f"{asset}/USDT", f"coinbase_{asset}_usdt"),
        ("Chainlink", f"{asset}/USD", f"chainlink_{asset}"),
    ]

    prices_for_avg = []
    for source, pair, key in rows:
        entry = store.get(key)
        if entry:
            price_str = fmt_price(entry[0])
            age_str = fmt_age(store.age(key))
            prices_for_avg.append(entry[0])
        else:
            price_str = f"{'—':>13}"
            age_str = "waiting..."
        lines.append(f"  {color}  {source:<12}{RESET} {pair:<13} {WHITE}{price_str}{RESET}   {age_str}")

    # Cross-source average
    if len(prices_for_avg) >= 2:
        avg = sum(prices_for_avg) / len(prices_for_avg)
        spread = max(prices_for_avg) - min(prices_for_avg)
        lines.append(f"  {DIM}  {'':12} {'AVG':<13} {WHITE}{fmt_price(avg)}{RESET}   {DIM}spread ${spread:,.2f}  ({len(prices_for_avg)} src){RESET}")

    return lines


def render():
    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{CYAN}{'=' * 60}{RESET}")
    lines.append(f"  {BOLD}{CYAN}   CRYPTO PRICE TERMINAL  —  Binance / Coinbase / Chainlink{RESET}")
    lines.append(f"  {BOLD}{CYAN}{'=' * 60}{RESET}")
    lines.append("")
    lines.append(f"  {BOLD}{WHITE}  {'SOURCE':<12} {'PAIR':<13} {'PRICE':>13}   AGE{RESET}")

    for asset in ASSETS:
        lines.append("")
        lines.extend(render_asset(asset))

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


# ── Main ──────────────────────────────────────────────────────────

async def main():
    print(f"{CYAN}Starting Crypto Price Terminal...{RESET}")
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
