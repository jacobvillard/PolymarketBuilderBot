import os
import time
from pathlib import Path
from dotenv import load_dotenv
import requests
import json
from datetime import datetime, timezone

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"

# -----------------------------
# CONFIG: slugs per timeframe
# -----------------------------
CRYPTO_SLUGS_15M = [
    "btc-up-or-down-15m",
    "eth-up-or-down-15m",
    "sol-up-or-down-15m",
    "xrp-up-or-down-15m",
]

CRYPTO_SLUGS_HOURLY = [
    "btc-up-or-down-hourly",
    "eth-up-or-down-hourly",
    "sol-up-or-down-hourly",
    "xrp-up-or-down-hourly",
]

CRYPTO_SLUGS_4H = [
    "btc-up-or-down-4h",
    "eth-up-or-down-4h",
    "sol-up-or-down-4h",
    "xrp-up-or-down-4h",
]

CRYPTO_SLUGS_DAILY = [
    "btc-up-or-down-daily",
    "eth-up-or-down-daily",
    "sol-up-or-down-daily",
    "xrp-up-or-down-daily",
]

PLACED_MARKETS_FILE = "placed_markets.json"   # remembers one-time orders  ### NEW

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

# -----------------------------
# ENV + CLIENT
# -----------------------------
def load_env():
    env_path = Path(__file__).parent / "polykeys.env"
    load_dotenv(env_path)

    private_key = os.getenv("PRIVATE_KEY")
    proxy_addr  = os.getenv("PROXY_ADDRESS")
    chain_id    = os.getenv("CHAIN_ID")
    host        = os.getenv("HOST")

    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY not found in environment file.")
    if not proxy_addr:
        raise ValueError("POLY_PROXY_ADDRESS not found in environment file.")
    if not chain_id:
        raise ValueError("POLY_CHAIN_ID not found in environment file.")
    if not host:
        raise ValueError("POLY_HOST not found in environment file.")

    return {
        "private_key": private_key,
        "proxy_addr": proxy_addr,
        "chain_id": int(chain_id),
        "host": host
    }


def load_client():
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host=cfg["host"],
        key=cfg["private_key"],
        chain_id=cfg["chain_id"],
        signature_type=1,
        funder=cfg["proxy_addr"]
    )
    print("Client created successfully:", client)
    return client


def healthcheck():
    url = "https://data-api.polymarket.com/"
    response = requests.get(url)
    if response.status_code == 200:
        print("Healthcheck OK")
        return True
    else:
        print("Healthcheck FAILED:" + str(response.status_code) + " " + response.text)
        return False

# -----------------------------
# BASIC HELPERS
# -----------------------------
def get_live_prices(token_ids):
    url = f"{CLOB_BASE}/prices"
    payload = [{"token_id": tid, "side": "BUY"} for tid in token_ids] + \
              [{"token_id": tid, "side": "SELL"} for tid in token_ids]
    res = requests.post(url, json=payload)
    res.raise_for_status()
    return res.json()


def get_crypto_series(slugs):
    """Generic series fetch for any list of slugs."""
    url = f"{GAMMA_BASE}/series"
    results = []
    for slug in slugs:
        resp = requests.get(url, params={"slug": slug})
        resp.raise_for_status()
        series = resp.json()
        if isinstance(series, list):
            results.extend(series)
        else:
            results.append(series)
    return results


# Backwards-compatible 15m helper (still used by run_bot if you ever call it)
def get_crypto_15m_series():
    return get_crypto_series(CRYPTO_SLUGS_15M)


def get_market_from_series(series_slug: str):
    url = f"{GAMMA_BASE}/markets/slug/{series_slug}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_event_from_series(series_slug: str):
    url = f"{GAMMA_BASE}/events/slug/{series_slug}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_amm_prices(market):
    prices_str = market.get("outcomePrices")
    if not prices_str:
        return None, None
    prices = json.loads(prices_str)
    yes_price = float(prices[0])
    no_price  = float(prices[1])
    return yes_price, no_price


def get_yes_no_tokens(market: dict):
    token_str = market.get("clobTokenIds")
    if not token_str:
        raise ValueError(f"No clobTokenIds on market {market.get('id')}")
    tokens = json.loads(token_str)
    if len(tokens) < 2:
        raise ValueError(f"Expected 2 tokens, got {tokens}")
    yes_token = tokens[0]
    no_token  = tokens[1]
    return yes_token, no_token


def get_best_bid_ask(token_id: str):
    resp = requests.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    resp.raise_for_status()
    ob = resp.json()
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    return best_bid, best_ask


def safe_price(value):
    try:
        return float(value)
    except Exception:
        return 0.0

# -----------------------------
# ORDER + STRATEGY
# -----------------------------
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY


def place_buy_order(client, token_id, price, size=5):
    args = OrderArgs(
        token_id=str(token_id),
        price=price,
        size=size,
        side=BUY
    )
    signed = client.create_order(args)
    resp = client.post_order(signed, OrderType.GTC)
    return resp


def should_enter_market(market, base_threshold=0.9):
    end_time = datetime.fromisoformat(market["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds_left = (end_time - now).total_seconds()
    minutes_left = seconds_left / 60

    if minutes_left > 4:
        return False
    if seconds_left < 30:
        return False

    # dynamic threshold
    threshold = base_threshold
    if seconds_left < 180:
        threshold = 0.85
    if seconds_left < 100:
        threshold = 0.75

    yes_token, no_token = get_yes_no_tokens(market)
    prices = get_live_prices([yes_token, no_token])
    yes_price = safe_price(prices.get(yes_token, {}).get("BUY"))
    no_price  = safe_price(prices.get(no_token, {}).get("BUY"))

    if yes_price > 0.98 or no_price > 0.98:
        return False

    if yes_price >= threshold:
        return ("YES", yes_token)
    if no_price >= threshold:
        return ("NO", no_token)
    return False

# -----------------------------
# EVENT SELECTION
# -----------------------------
def get_active_event_from_series(series_obj):
    """
    Return the event whose endDate is the closest FUTURE event.
    Works for 15m, hourly, 4h, daily, etc.
    """
    events = series_obj.get("events", [])
    if not events:
        return None

    now = datetime.now(timezone.utc)
    future_events = []

    for ev in events:
        try:
            end_time = datetime.fromisoformat(ev["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if end_time > now:
            future_events.append((end_time, ev))

    if not future_events:
        return None

    future_events.sort(key=lambda x: x[0])
    return future_events[0][1]

# -----------------------------
# LIVE TRADING BOT (ALL TIMEFRAMES)
# -----------------------------
def start_trading_bot():
    """
    Trades all crypto Up/Down markets:
      - 15m
      - hourly
      - 4h
      - daily
    using the same close-of-market strategy.
    """
    # combine all timeframes
    slugs_all = (
        CRYPTO_SLUGS_15M +
        CRYPTO_SLUGS_HOURLY +
        CRYPTO_SLUGS_4H +
        CRYPTO_SLUGS_DAILY
    )

    series_list = get_crypto_series(slugs_all)
    series_ids = []

    for s in series_list:
        active_event = get_active_event_from_series(s)
        if not active_event:
            print("No active upcoming event found for series:", s.get("title"))
            continue

        print("Series:", s.get("title"))
        print(" Active event slug:", active_event["slug"])
        print(" Active event id:",   active_event["id"])
        print(" Ends at:",           active_event["endDate"])
        series_ids.append(active_event["slug"])

    print("\nBot is now running... checking markets every 20 seconds.\n")

    while True:
        print("\n" + "="*60)
        print("CHECKING MARKETS @", datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        print("="*60)

        for sid in series_ids:
            market = get_market_from_series(sid)
            if not market:
                print(RED + f"[{sid}] No active market right now." + RESET)
                continue

            question = market.get("question", "Unknown question")
            end_time = datetime.fromisoformat(market["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            minutes_remaining = round((end_time - now).total_seconds() / 60, 2)
            seconds_left = (end_time - now).total_seconds()

            print(f"\n[{sid}] {question}")
            print(f" - Time remaining: {minutes_remaining} min")

            if seconds_left < 30:
                print(RED + " <30 seconds left — ending trading bot loop..." + RESET)
                return  # clean exit -> back to run_every_15_minutes()

            yes_token, no_token = get_yes_no_tokens(market)
            prices = get_live_prices([yes_token, no_token])

            yes_live = prices.get(yes_token, {})
            no_live  = prices.get(no_token, {})

            yes_buy  = float(yes_live.get("BUY", 0))
            yes_sell = float(yes_live.get("SELL", 0))
            no_buy   = float(no_live.get("BUY", 0))
            no_sell  = float(no_live.get("SELL", 0))

            print(f" - YES Buy/Sell: {yes_buy} / {yes_sell}")
            print(f" -  NO Buy/Sell: {no_buy} / {no_sell}")
            print(f" xAxisValue   = {market.get('xAxisValue')}")
            print(f" yAxisValue   = {market.get('yAxisValue')}")
            print(f" outcomePrices= {market.get('outcomePrices')}")
            print(f" ammType      = {market.get('ammType')}")
            print(f" volume       = {market.get('volume')}")
            print(f" liquidity    = {market.get('liquidity')}")

            decision = should_enter_market(market)
            if not decision:
                print(" - Status: Too early or threshold not met")
                continue

            side, token = decision
            entry_price = yes_sell if side == "YES" else no_sell

            print(GREEN + f" >>> ENTERING {side} at market price {entry_price}! Token={token}" + RESET)
            try:
                order = place_buy_order(polyclient, token_id=token, price=entry_price)
                print(GREEN + f" - Order placed! ID: {order}" + RESET)
            except Exception as e:
                print(RED + f" - Order failed: {e}" + RESET)

        time.sleep(20)

# -----------------------------
# ONE-TIME ORDERS @ 0.49 (HOURLY, 4H, DAILY)
# -----------------------------
def load_placed_market_ids():
    if not os.path.exists(PLACED_MARKETS_FILE):
        return set()
    try:
        with open(PLACED_MARKETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("market_ids", []))
    except Exception:
        return set()


def get_future_event_from_series(series_obj, future_index=0):
    """
    Returns a future event by index:
    future_index=0 → the nearest future event (current upcoming)
    future_index=1 → the NEXT bucket (the real future market)
    """

    events = series_obj.get("events", [])
    if not events:
        return None

    now = datetime.now(timezone.utc)
    future_events = []

    for ev in events:
        try:
            end_time = datetime.fromisoformat(ev["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
        except:
            continue

        if end_time > now:
            future_events.append((end_time, ev))

    # Sort by soonest expiry
    future_events.sort(key=lambda x: x[0])

    if len(future_events) <= future_index:
        return None

    # return event object only
    return future_events[future_index][1]


def save_placed_market_ids(market_ids: set):
    data = {"market_ids": list(market_ids)}
    with open(PLACED_MARKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def place_orders_for_future_longer_timeframes():
    """
    Place one-time orders @ 0.49 (5 YES, 5 NO) for:
      - hourly
      - 4h
      - daily
    Only once per market ID (tracked in placed_markets.json)
    """
    print("\n=== Grabbing Upcoming Hourly/4h/Daily Markets and Placing One-Time Orders ===\n")

    slugs_longer = CRYPTO_SLUGS_HOURLY + CRYPTO_SLUGS_4H + CRYPTO_SLUGS_DAILY + CRYPTO_SLUGS_15M
    series_list  = get_crypto_series(slugs_longer)

    placed_ids = load_placed_market_ids()

    for s in series_list:
        print(f"\nSeries: {s.get('title')}")
        active_event = get_future_event_from_series(s, future_index=1)
        if not active_event:
            print(" No upcoming event found, skipping.")
            continue

        slug = active_event["slug"]
        print(f" Upcoming Event Slug: {slug}")

        market = get_market_from_series(slug)
        if not market:
            print(" ERROR: Could not load market.")
            continue

        market_id = str(market.get("id"))
        if not market_id:
            print(" Market has no ID, skipping.")
            continue

        if market_id in placed_ids:
            print(" Orders already placed for this market (seen in JSON), skipping.")
            continue

        print(f" Market Question: {market.get('question')}")
        yes_token, no_token = get_yes_no_tokens(market)
        print(f" YES token = {yes_token}")
        print(f" NO  token = {no_token}")

        price = 0.49
        size  = 5

        # YES order
        print("\n Placing YES order...")
        try:
            order_yes = place_buy_order(polyclient, yes_token, price=price, size=size)
            print(GREEN + f" YES ORDER PLACED: {order_yes}" + RESET)
        except Exception as e:
            print(RED + f" Failed YES order: {e}" + RESET)

        # NO order
        print(" Placing NO order...")
        try:
            order_no = place_buy_order(polyclient, no_token, price=price, size=size)
            print(GREEN + f" NO ORDER PLACED: {order_no}" + RESET)
        except Exception as e:
            print(RED + f" Failed NO order: {e}" + RESET)

        # mark this market as done
        placed_ids.add(market_id)

    save_placed_market_ids(placed_ids)
    print("\n=== Finished one-time orders for hourly/4h/daily. ===\n")

# -----------------------------
# MASTER LOOP (EVERY ~15 MIN)
# -----------------------------
def run_every_15_minutes():
    print("\n===== 15-MINUTE MASTER LOOP STARTED =====\n")

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n=== New Cycle @ {now} UTC ===")

        # 1. One-time 0.49 orders on hourly/4h/daily
        try:
            print("\nRunning one-time order placement for hourly/4h/daily...")
            place_orders_for_future_longer_timeframes()
        except Exception as e:
            print(RED + f"Error in future market orders: {e}" + RESET)

        # 2. Run live trading bot for *all* timeframes (15m + others)
        try:
            print("\nRunning live trading bot on all timeframes...")
            #start_trading_bot()
        except Exception as e:
            print(RED + f"Error in live trading bot: {e}" + RESET)

        # 3. Sleep ~15 minutes
        print(GREEN + "\nSleeping for 1 minutes...\n" + RESET)
        time.sleep(60)

        if datetime.utcnow().minute % 15 == 0:
            redeem_all(cfg["private_key"])



import json
import requests


import os
import time
from pathlib import Path
from dotenv import load_dotenv
import requests
import json
from datetime import datetime, timezone

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"

# -----------------------------
# CONFIG: slugs per timeframe
# -----------------------------
CRYPTO_SLUGS_15M = [
    "btc-up-or-down-15m",
    "eth-up-or-down-15m",
    "sol-up-or-down-15m",
    "xrp-up-or-down-15m",
]

CRYPTO_SLUGS_HOURLY = [
    "btc-up-or-down-hourly",
    "eth-up-or-down-hourly",
    "sol-up-or-down-hourly",
    "xrp-up-or-down-hourly",
]

CRYPTO_SLUGS_4H = [
    "btc-up-or-down-4h",
    "eth-up-or-down-4h",
    "sol-up-or-down-4h",
    "xrp-up-or-down-4h",
]

CRYPTO_SLUGS_DAILY = [
    "btc-up-or-down-daily",
    "eth-up-or-down-daily",
    "sol-up-or-down-daily",
    "xrp-up-or-down-daily",
]

PLACED_MARKETS_FILE = "placed_markets.json"   # remembers one-time orders  ### NEW

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

# -----------------------------
# ENV + CLIENT
# -----------------------------
def load_env():
    env_path = Path(__file__).parent / "polykeys.env"
    load_dotenv(env_path)

    private_key = os.getenv("PRIVATE_KEY")
    proxy_addr  = os.getenv("PROXY_ADDRESS")
    chain_id    = os.getenv("CHAIN_ID")
    host        = os.getenv("HOST")

    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY not found in environment file.")
    if not proxy_addr:
        raise ValueError("POLY_PROXY_ADDRESS not found in environment file.")
    if not chain_id:
        raise ValueError("POLY_CHAIN_ID not found in environment file.")
    if not host:
        raise ValueError("POLY_HOST not found in environment file.")

    return {
        "private_key": private_key,
        "proxy_addr": proxy_addr,
        "chain_id": int(chain_id),
        "host": host
    }


def load_client():
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host=cfg["host"],
        key=cfg["private_key"],
        chain_id=cfg["chain_id"],
        signature_type=1,
        funder=cfg["proxy_addr"]
    )
    print("Client created successfully:", client)
    return client


def healthcheck():
    url = "https://data-api.polymarket.com/"
    response = requests.get(url)
    if response.status_code == 200:
        print("Healthcheck OK")
        return True
    else:
        print("Healthcheck FAILED:" + str(response.status_code) + " " + response.text)
        return False

# -----------------------------
# BASIC HELPERS
# -----------------------------
def get_live_prices(token_ids):
    url = f"{CLOB_BASE}/prices"
    payload = [{"token_id": tid, "side": "BUY"} for tid in token_ids] + \
              [{"token_id": tid, "side": "SELL"} for tid in token_ids]
    res = requests.post(url, json=payload)
    res.raise_for_status()
    return res.json()


def get_crypto_series(slugs):
    """Generic series fetch for any list of slugs."""
    url = f"{GAMMA_BASE}/series"
    results = []
    for slug in slugs:
        resp = requests.get(url, params={"slug": slug})
        resp.raise_for_status()
        series = resp.json()
        if isinstance(series, list):
            results.extend(series)
        else:
            results.append(series)
    return results


# Backwards-compatible 15m helper (still used by run_bot if you ever call it)
def get_crypto_15m_series():
    return get_crypto_series(CRYPTO_SLUGS_15M)


def get_market_from_series(series_slug: str):
    url = f"{GAMMA_BASE}/markets/slug/{series_slug}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_event_from_series(series_slug: str):
    url = f"{GAMMA_BASE}/events/slug/{series_slug}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def get_amm_prices(market):
    prices_str = market.get("outcomePrices")
    if not prices_str:
        return None, None
    prices = json.loads(prices_str)
    yes_price = float(prices[0])
    no_price  = float(prices[1])
    return yes_price, no_price


def get_yes_no_tokens(market: dict):
    token_str = market.get("clobTokenIds")
    if not token_str:
        raise ValueError(f"No clobTokenIds on market {market.get('id')}")
    tokens = json.loads(token_str)
    if len(tokens) < 2:
        raise ValueError(f"Expected 2 tokens, got {tokens}")
    yes_token = tokens[0]
    no_token  = tokens[1]
    return yes_token, no_token


def get_best_bid_ask(token_id: str):
    resp = requests.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    resp.raise_for_status()
    ob = resp.json()
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    best_bid = float(bids[0]["price"]) if bids else None
    best_ask = float(asks[0]["price"]) if asks else None
    return best_bid, best_ask


def safe_price(value):
    try:
        return float(value)
    except Exception:
        return 0.0

# -----------------------------
# ORDER + STRATEGY
# -----------------------------
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY


def place_buy_order(client, token_id, price, size=5):
    args = OrderArgs(
        token_id=str(token_id),
        price=price,
        size=size,
        side=BUY
    )
    signed = client.create_order(args)
    resp = client.post_order(signed, OrderType.GTC)
    return resp


def should_enter_market(market, base_threshold=0.9):
    end_time = datetime.fromisoformat(market["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds_left = (end_time - now).total_seconds()
    minutes_left = seconds_left / 60

    if minutes_left > 4:
        return False
    if seconds_left < 30:
        return False

    # dynamic threshold
    threshold = base_threshold
    if seconds_left < 180:
        threshold = 0.85
    if seconds_left < 100:
        threshold = 0.75

    yes_token, no_token = get_yes_no_tokens(market)
    prices = get_live_prices([yes_token, no_token])
    yes_price = safe_price(prices.get(yes_token, {}).get("BUY"))
    no_price  = safe_price(prices.get(no_token, {}).get("BUY"))

    if yes_price > 0.98 or no_price > 0.98:
        return False

    if yes_price >= threshold:
        return ("YES", yes_token)
    if no_price >= threshold:
        return ("NO", no_token)
    return False

# -----------------------------
# EVENT SELECTION
# -----------------------------
def get_active_event_from_series(series_obj):
    """
    Return the event whose endDate is the closest FUTURE event.
    Works for 15m, hourly, 4h, daily, etc.
    """
    events = series_obj.get("events", [])
    if not events:
        return None

    now = datetime.now(timezone.utc)
    future_events = []

    for ev in events:
        try:
            end_time = datetime.fromisoformat(ev["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if end_time > now:
            future_events.append((end_time, ev))

    if not future_events:
        return None

    future_events.sort(key=lambda x: x[0])
    return future_events[0][1]

# -----------------------------
# LIVE TRADING BOT (ALL TIMEFRAMES)
# -----------------------------
def start_trading_bot():
    """
    Trades all crypto Up/Down markets:
      - 15m
      - hourly
      - 4h
      - daily
    using the same close-of-market strategy.
    """
    # combine all timeframes
    slugs_all = (
        CRYPTO_SLUGS_15M +
        CRYPTO_SLUGS_HOURLY +
        CRYPTO_SLUGS_4H +
        CRYPTO_SLUGS_DAILY
    )

    series_list = get_crypto_series(slugs_all)
    series_ids = []

    for s in series_list:
        active_event = get_active_event_from_series(s)
        if not active_event:
            print("No active upcoming event found for series:", s.get("title"))
            continue

        print("Series:", s.get("title"))
        print(" Active event slug:", active_event["slug"])
        print(" Active event id:",   active_event["id"])
        print(" Ends at:",           active_event["endDate"])
        series_ids.append(active_event["slug"])

    print("\nBot is now running... checking markets every 20 seconds.\n")

    while True:
        print("\n" + "="*60)
        print("CHECKING MARKETS @", datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        print("="*60)

        for sid in series_ids:
            market = get_market_from_series(sid)
            if not market:
                print(RED + f"[{sid}] No active market right now." + RESET)
                continue

            question = market.get("question", "Unknown question")
            end_time = datetime.fromisoformat(market["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            minutes_remaining = round((end_time - now).total_seconds() / 60, 2)
            seconds_left = (end_time - now).total_seconds()

            print(f"\n[{sid}] {question}")
            print(f" - Time remaining: {minutes_remaining} min")

            if seconds_left < 30:
                print(RED + " <30 seconds left — ending trading bot loop..." + RESET)
                return  # clean exit -> back to run_every_15_minutes()

            yes_token, no_token = get_yes_no_tokens(market)
            prices = get_live_prices([yes_token, no_token])

            yes_live = prices.get(yes_token, {})
            no_live  = prices.get(no_token, {})

            yes_buy  = float(yes_live.get("BUY", 0))
            yes_sell = float(yes_live.get("SELL", 0))
            no_buy   = float(no_live.get("BUY", 0))
            no_sell  = float(no_live.get("SELL", 0))

            print(f" - YES Buy/Sell: {yes_buy} / {yes_sell}")
            print(f" -  NO Buy/Sell: {no_buy} / {no_sell}")
            print(f" xAxisValue   = {market.get('xAxisValue')}")
            print(f" yAxisValue   = {market.get('yAxisValue')}")
            print(f" outcomePrices= {market.get('outcomePrices')}")
            print(f" ammType      = {market.get('ammType')}")
            print(f" volume       = {market.get('volume')}")
            print(f" liquidity    = {market.get('liquidity')}")

            decision = should_enter_market(market)
            if not decision:
                print(" - Status: Too early or threshold not met")
                continue

            side, token = decision
            entry_price = yes_sell if side == "YES" else no_sell

            print(GREEN + f" >>> ENTERING {side} at market price {entry_price}! Token={token}" + RESET)
            try:
                order = place_buy_order(polyclient, token_id=token, price=entry_price)
                print(GREEN + f" - Order placed! ID: {order}" + RESET)
            except Exception as e:
                print(RED + f" - Order failed: {e}" + RESET)

        time.sleep(20)

# -----------------------------
# ONE-TIME ORDERS @ 0.49 (HOURLY, 4H, DAILY)
# -----------------------------
def load_placed_market_ids():
    if not os.path.exists(PLACED_MARKETS_FILE):
        return set()
    try:
        with open(PLACED_MARKETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("market_ids", []))
    except Exception:
        return set()


def get_future_event_from_series(series_obj, future_index=0):
    """
    Returns a future event by index:
    future_index=0 → the nearest future event (current upcoming)
    future_index=1 → the NEXT bucket (the real future market)
    """

    events = series_obj.get("events", [])
    if not events:
        return None

    now = datetime.now(timezone.utc)
    future_events = []

    for ev in events:
        try:
            end_time = datetime.fromisoformat(ev["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
        except:
            continue

        if end_time > now:
            future_events.append((end_time, ev))

    # Sort by soonest expiry
    future_events.sort(key=lambda x: x[0])

    if len(future_events) <= future_index:
        return None

    # return event object only
    return future_events[future_index][1]


def save_placed_market_ids(market_ids: set):
    data = {"market_ids": list(market_ids)}
    with open(PLACED_MARKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def place_orders_for_future_longer_timeframes():
    """
    Place one-time orders @ 0.49 (5 YES, 5 NO) for:
      - hourly
      - 4h
      - daily
    Only once per market ID (tracked in placed_markets.json)
    """
    print("\n=== Grabbing Upcoming Hourly/4h/Daily Markets and Placing One-Time Orders ===\n")

    slugs_longer = CRYPTO_SLUGS_HOURLY + CRYPTO_SLUGS_4H + CRYPTO_SLUGS_DAILY + CRYPTO_SLUGS_15M
    series_list  = get_crypto_series(slugs_longer)

    placed_ids = load_placed_market_ids()

    for s in series_list:
        print(f"\nSeries: {s.get('title')}")
        active_event = get_future_event_from_series(s, future_index=1)
        if not active_event:
            print(" No upcoming event found, skipping.")
            continue

        slug = active_event["slug"]
        print(f" Upcoming Event Slug: {slug}")

        market = get_market_from_series(slug)
        if not market:
            print(" ERROR: Could not load market.")
            continue

        market_id = str(market.get("id"))
        if not market_id:
            print(" Market has no ID, skipping.")
            continue

        if market_id in placed_ids:
            print(" Orders already placed for this market (seen in JSON), skipping.")
            continue

        print(f" Market Question: {market.get('question')}")
        yes_token, no_token = get_yes_no_tokens(market)
        print(f" YES token = {yes_token}")
        print(f" NO  token = {no_token}")

        price = 0.49
        size  = 5

        # YES order
        print("\n Placing YES order...")
        try:
            order_yes = place_buy_order(polyclient, yes_token, price=price, size=size)
            print(GREEN + f" YES ORDER PLACED: {order_yes}" + RESET)
        except Exception as e:
            print(RED + f" Failed YES order: {e}" + RESET)

        # NO order
        print(" Placing NO order...")
        try:
            order_no = place_buy_order(polyclient, no_token, price=price, size=size)
            print(GREEN + f" NO ORDER PLACED: {order_no}" + RESET)
        except Exception as e:
            print(RED + f" Failed NO order: {e}" + RESET)

        # mark this market as done
        placed_ids.add(market_id)

    save_placed_market_ids(placed_ids)
    print("\n=== Finished one-time orders for hourly/4h/daily. ===\n")

# -----------------------------
# MASTER LOOP (EVERY ~15 MIN)
# -----------------------------
def run_every_15_minutes():
    print("\n===== 15-MINUTE MASTER LOOP STARTED =====\n")

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n=== New Cycle @ {now} UTC ===")

        # 1. One-time 0.49 orders on hourly/4h/daily
        try:
            print("\nRunning one-time order placement for hourly/4h/daily...")
            place_orders_for_future_longer_timeframes()
        except Exception as e:
            print(RED + f"Error in future market orders: {e}" + RESET)

        # 2. Run live trading bot for *all* timeframes (15m + others)
        try:
            print("\nRunning live trading bot on all timeframes...")
            #start_trading_bot()
        except Exception as e:
            print(RED + f"Error in live trading bot: {e}" + RESET)

        # 3. Sleep ~15 minutes
        print(GREEN + "\nSleeping for 1 minutes...\n" + RESET)
        time.sleep(60)




import json
import requests





# -----------------------------
# MAIN
# -----------------------------
if __name__ == "__main__":
    cfg = load_env()
    print("Env loaded OK:", cfg["host"], cfg["chain_id"])

    polyclient = load_client()
    polyclient.set_api_creds(polyclient.create_or_derive_api_creds())

    if healthcheck():
        print(f"{GREEN}All systems go!{RESET}")
    else:
        print(f"{RED}System check failed.{RESET}")
        raise Exception("Healthcheck failed")

    run_every_15_minutes()


