"""Real-time crypto price terminal — Binance + Coinbase.

Shows live prices from:
  - Coinbase WebSocket: BTC, ETH, SOL, XRP (USD + USDT)
  - Binance WebSocket: BTC, ETH, SOL, XRP (USDT)

Plus a self-playing snake game at the bottom.

Usage:  python3 btc_terminal.py
"""

import asyncio
import json
import time
import sys
import random

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets")


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

# ── WebSocket URLs ───────────────────────────────────────────────
BINANCE_STREAMS = "/".join(f"{s}@trade" for s in BINANCE_SYMBOLS.values())
BINANCE_WS = f"wss://stream.binance.com:9443/stream?streams={BINANCE_STREAMS}"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

# ── Display ──────────────────────────────────────────────────────
REFRESH_MS = 150  # faster for smooth snake

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
BG_GREEN = "\033[42m"
BG_RED = "\033[41m"


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


# ── Snake Game ────────────────────────────────────────────────────

class Snake:
    WIDTH = 56
    HEIGHT = 12

    def __init__(self):
        self.reset()

    def reset(self):
        mid_y = self.HEIGHT // 2
        mid_x = self.WIDTH // 4
        self.body = [(mid_y, mid_x + 2), (mid_y, mid_x + 1), (mid_y, mid_x)]
        self.direction = (0, 1)  # moving right
        self.food = None
        self.score = 0
        self.high_score = 0
        self.place_food()
        self.tick_count = 0

    def place_food(self):
        occupied = set(self.body)
        attempts = 0
        while attempts < 200:
            r = random.randint(0, self.HEIGHT - 1)
            c = random.randint(0, self.WIDTH - 1)
            if (r, c) not in occupied:
                self.food = (r, c)
                return
            attempts += 1
        self.food = (0, 0)

    def ai_direction(self):
        """Simple AI: chase food, avoid walls and self."""
        head = self.body[0]
        hr, hc = head
        fr, fc = self.food

        occupied = set(self.body[:-1])

        # Possible moves
        moves = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        # Don't reverse
        reverse = (-self.direction[0], -self.direction[1])
        moves = [m for m in moves if m != reverse]

        def is_safe(dr, dc):
            nr, nc = hr + dr, hc + dc
            return (0 <= nr < self.HEIGHT and 0 <= nc < self.WIDTH
                    and (nr, nc) not in occupied)

        safe = [m for m in moves if is_safe(*m)]
        if not safe:
            return self.direction  # doomed

        # Prefer moves toward food
        def dist_to_food(dr, dc):
            nr, nc = hr + dr, hc + dc
            return abs(nr - fr) + abs(nc - fc)

        safe.sort(key=lambda m: dist_to_food(*m))
        return safe[0]

    def tick(self):
        self.tick_count += 1
        # Only move snake every 2 render frames for readable speed
        if self.tick_count % 2 != 0:
            return

        self.direction = self.ai_direction()
        hr, hc = self.body[0]
        dr, dc = self.direction
        new_head = (hr + dr, hc + dc)

        # Wall or self collision = reset
        if (new_head[0] < 0 or new_head[0] >= self.HEIGHT
                or new_head[1] < 0 or new_head[1] >= self.WIDTH
                or new_head in set(self.body)):
            if self.score > self.high_score:
                self.high_score = self.score
            self.reset()
            return

        self.body.insert(0, new_head)

        if new_head == self.food:
            self.score += 1
            self.place_food()
        else:
            self.body.pop()

    def render(self):
        grid = [[" "] * self.WIDTH for _ in range(self.HEIGHT)]

        # Food
        if self.food:
            fr, fc = self.food
            grid[fr][fc] = "◆"

        # Body
        for i, (r, c) in enumerate(self.body):
            if i == 0:
                grid[r][c] = "█"  # head
            else:
                grid[r][c] = "▓"

        lines = []
        border_color = GREEN if self.score < 10 else YELLOW if self.score < 25 else CYAN
        top = f"  {border_color}┌{'─' * self.WIDTH}┐{RESET}  {DIM}score: {WHITE}{self.score}{RESET}  {DIM}hi: {WHITE}{self.high_score}{RESET}"
        lines.append(top)
        for row in grid:
            row_str = ""
            for ch in row:
                if ch == "█":
                    row_str += f"{GREEN}{ch}{RESET}"
                elif ch == "▓":
                    row_str += f"{GREEN}{DIM}{ch}{RESET}"
                elif ch == "◆":
                    row_str += f"{RED}{BOLD}{ch}{RESET}"
                else:
                    row_str += ch
            lines.append(f"  {border_color}│{RESET}{row_str}{border_color}│{RESET}")
        lines.append(f"  {border_color}└{'─' * self.WIDTH}┘{RESET}")
        return lines


snake = Snake()


# ── Binance WS (combined stream) ─────────────────────────────────

async def binance_ws():
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
                    suffix = "usd" if product.endswith("-USD") else "usdt"
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


def render_row(coin, pair, key, color):
    entry = store.get(key)
    if entry:
        price_str = fmt_price(entry[0])
        age_str = fmt_age(store.age(key))
    else:
        price_str = f"{'—':>13}"
        age_str = "waiting..."
    return f"  {color}  {coin:<6}{RESET} {pair:<13} {WHITE}{price_str}{RESET}   {age_str}"


def render():
    snake.tick()

    lines = []
    lines.append("")
    lines.append(f"  {BOLD}{CYAN}{'=' * 60}{RESET}")
    lines.append(f"  {BOLD}{CYAN}     CRYPTO PRICE TERMINAL  —  Coinbase / Binance{RESET}")
    lines.append(f"  {BOLD}{CYAN}{'=' * 60}{RESET}")
    lines.append("")
    lines.append(f"  {BOLD}{WHITE}  {'COIN':<6} {'PAIR':<13} {'PRICE':>13}   AGE{RESET}")

    # ── USD PAIRS (priority) ─────────────────────────────────────
    lines.append("")
    lines.append(f"  {BOLD}{MAGENTA}  COINBASE  {DIM}USD{RESET}")
    lines.append(f"  {DIM}  {'─' * 50}{RESET}")
    for asset in ASSETS:
        lines.append(render_row(asset, f"{asset}/USD", f"coinbase_{asset}_usd", MAGENTA))

    # ── USDT PAIRS ───────────────────────────────────────────────
    lines.append("")
    lines.append(f"  {BOLD}{GREEN}  BINANCE  {DIM}USDT{RESET}")
    lines.append(f"  {DIM}  {'─' * 50}{RESET}")
    for asset in ASSETS:
        lines.append(render_row(asset, f"{asset}/USDT", f"binance_{asset}", GREEN))

    lines.append("")
    lines.append(f"  {BOLD}{MAGENTA}  COINBASE  {DIM}USDT{RESET}")
    lines.append(f"  {DIM}  {'─' * 50}{RESET}")
    for asset in ASSETS:
        lines.append(render_row(asset, f"{asset}/USDT", f"coinbase_{asset}_usdt", MAGENTA))

    # ── Snake ────────────────────────────────────────────────────
    lines.append("")
    lines.extend(snake.render())

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
    print(f"{DIM}Connecting to Coinbase + Binance...{RESET}")

    await asyncio.gather(
        binance_ws(),
        coinbase_ws(),
        display_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{DIM}Terminal closed.{RESET}")
