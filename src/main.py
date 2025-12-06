import os
import time
from pathlib import Path
from dotenv import load_dotenv
import requests

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# Load environment variables from polykeys.env
def load_env():
    """Loads Polymarket environment variables from polykeys.env."""
    env_path = Path(__file__).parent / "polykeys.env"
    load_dotenv(env_path)

    private_key = os.getenv("PRIVATE_KEY")
    proxy_addr = os.getenv("PROXY_ADDRESS")
    chain_id = os.getenv("CHAIN_ID")
    host = os.getenv("HOST")

    # Basic validation
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

# Initialize the ClobClient
def load_client():
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host=cfg["host"],
        key=cfg["private_key"],
        chain_id=cfg["chain_id"],
        signature_type=1,
        funder=cfg["proxy_addr"]  # Your Polymarket proxy address
    )
    print("Client created successfully:", client)
    return client

# Healthcheck function to verify connectivity
def healthcheck():
    import requests

    url = "https://data-api.polymarket.com/"

    response = requests.get(url)
    if response.status_code == 200:
        print("Healthcheck OK")
        return True
    else:
        print("Healthcheck FAILED:" + str(response.status_code) + " " + response.text)
        return False


def get_live_prices(token_ids):
    url = "https://clob.polymarket.com/prices"
    payload = [{"token_id": tid, "side": "BUY"} for tid in token_ids] + \
              [{"token_id": tid, "side": "SELL"} for tid in token_ids]

    res = requests.post(url, json=payload)
    res.raise_for_status()
    return res.json()



def get_crypto_15m_series():
    url = "https://gamma-api.polymarket.com/series"

    slugs = [
        "btc-up-or-down-15m",
        "eth-up-or-down-15m",
        "sol-up-or-down-15m",
        "xrp-up-or-down-15m",
    ]

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

import json

def save_series_dump(series_id, data):
    """Save the raw series JSON response to a file for debugging."""
    filename = f"series_{series_id}_dump.txt"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"✔ Saved debug dump to {filename}")


def get_market_from_series(series_id: int):
    """
    Correct way to fetch the 15m crypto market.
    Series→markets→market
    """
    url = f"https://gamma-api.polymarket.com/markets/slug/{series_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()


    return data

def get_event_from_series(series_id: int):
    """
    Correct way to fetch the 15m crypto market.
    Series→markets→market
    """
    url = f"https://gamma-api.polymarket.com/events/slug/{series_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()


    return data


import requests

def get_amm_prices(market):
    prices_str = market.get("outcomePrices")
    if not prices_str:
        return None, None

    prices = json.loads(prices_str)
    yes_price = float(prices[0])
    no_price = float(prices[1])
    return yes_price, no_price

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

def get_yes_no_tokens(market: dict):
    token_str = market.get("clobTokenIds")
    if not token_str:
        raise ValueError(f"No clobTokenIds on market {market.get('id')}")

    # Parse the JSON-encoded list
    tokens = json.loads(token_str)

    if len(tokens) < 2:
        raise ValueError(f"Expected 2 tokens, got {tokens}")

    yes_token = tokens[0]
    no_token = tokens[1]
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



from datetime import datetime, timezone

def safe_price(value):
    try:
        return float(value)
    except:
        return 0.0


def should_enter_market(market, base_threshold=0.9):
    # Time logic
    end_time = datetime.fromisoformat(market["endDate"].replace("Z","")).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds_left = (end_time - now).total_seconds()
    minutes_left = seconds_left / 60

    # RULE: Only trade if UNDER 2 minutes left
    if minutes_left > 4:
        return False

    # RULE: Avoid super late entry (< 30 seconds)
    if seconds_left < 30:
        return False

    # Dynamic threshold (< 1 min → lower threshold)
    threshold = 0.85 if seconds_left < 180 else base_threshold
    threshold = 0.75 if seconds_left < 100 else base_threshold

    # Get tokens
    yes_token, no_token = get_yes_no_tokens(market)

    # Fetch live prices
    prices = get_live_prices([yes_token, no_token])

    yes_price = safe_price(prices.get(yes_token, {}).get("BUY"))
    no_price  = safe_price(prices.get(no_token, {}).get("BUY"))

    # RULE: Avoid entering expensive prices
    if yes_price > 0.98 or no_price > 0.98:
        return False

    # Entry decisions
    if yes_price >= threshold:
        return ("YES", yes_token)

    if no_price >= threshold:
        return ("NO", no_token)

    return False


from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

def place_buy_order(client, token_id, price, size=5):
    # Build the order
    args = OrderArgs(
        token_id=str(token_id),
        price=price,
        size=size,
        side=BUY
    )

    # Sign order
    signed = client.create_order(args)

    # Send it as GTC (limit order)
    resp = client.post_order(signed, OrderType.GTC)
    return resp


def run_bot(series_ids, entry_threshold=0.88, entry_minutes=2):
    print("\nBot is now running... checking markets every 20 seconds.\n")
    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print("="*70)
        print(f"CHECKING MARKETS @ {now} UTC")
        print("="*70)

        for sid in series_ids:
            market = get_market_from_series(sid)

            if not market:
                print(f"{RED}[{sid}] ERROR: Could not load market data.{RESET}")
                continue

            print(f"[{sid}] Market loaded: {market['question']}")

            decision = should_enter_market(market,
                                           threshold=entry_threshold,
                                           minutes_left=entry_minutes)

            if decision:
                side, token = decision
                print(f"{GREEN}ENTERING {side} for market {sid} (token {token}){RESET}")
                place_buy_order(polyclient, token, price=entry_threshold)
            else:
                print(f"{RED}[{sid}] No entry signal right now.{RESET}")

        time.sleep(20)

def get_active_event_from_series(series_obj):
    """
    Given a series object (with all events), return the event whose endDate
    is the closest FUTURE event. This is the correct 15m active market.
    """
    events = series_obj.get("events", [])
    if not events:
        return None

    now = datetime.now(timezone.utc)

    future_events = []

    for ev in events:
        try:
            end_time = datetime.fromisoformat(ev["endDate"].replace("Z","")).replace(tzinfo=timezone.utc)
        except:
            continue

        if end_time > now:
            future_events.append((end_time, ev))

    if not future_events:
        return None

    # Return the event with the earliest endDate in the future
    future_events.sort(key=lambda x: x[0])
    return future_events[0][1]

def start_trading_bot():
    # Load 15m crypto series
    series_list = get_crypto_15m_series()
    series_ids = []

    for s in series_list:
        active_event = get_active_event_from_series(s)


        if not active_event:
            print("No active upcoming event found.")
            continue

        print("Series:", s["title"])
        print(" Active event slug:", active_event["slug"])
        print(" Active event id:", active_event["id"])
        print(" Ends at:", active_event["endDate"])
        series_ids.append(active_event["slug"])

    print("\nBot is now running... checking markets every 20 seconds.\n")

    while True:
        print("\n" + "="*60)
        print("CHECKING MARKETS @", datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
        print("="*60)

        for sid in series_ids:

            # Fetch active market
            market = get_market_from_series(sid)


            if not market:
                print(RED + f"[{sid}] No active market right now." + RESET)
                continue

            question = market.get("question", "Unknown question")

            # Get timing
            end_time = datetime.fromisoformat(market["endDate"].replace("Z", "")).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            minutes_remaining = round((end_time - now).total_seconds() / 60, 2)

            print(f"\n[{sid}] {question}")
            print(f" - Time remaining: {minutes_remaining} min")

            # ----- AUTO-RESTART IF UNDER 30 SECONDS LEFT -----
            seconds_left = (end_time - now).total_seconds()
            if seconds_left < 30:
                print(RED + " <30 seconds left — ending trading bot loop..." + RESET)
                return  # <-- clean exit from start_trading_bot()

            # Extract tokens
            yes_token, no_token = get_yes_no_tokens(market)

            # Orderbook prices
            # --- LIVE CLOB PRICES (REAL MARKET ODDS) ---
            prices = get_live_prices([yes_token, no_token])

            yes_live = prices.get(yes_token, {})
            no_live = prices.get(no_token, {})

            yes_buy = float(yes_live.get("BUY", 0))
            yes_sell = float(yes_live.get("SELL", 0))

            no_buy = float(no_live.get("BUY", 0))
            no_sell = float(no_live.get("SELL", 0))

            print(f" - YES Buy/Sell: {yes_buy} / {yes_sell}")
            print(f" -  NO Buy/Sell: {no_buy} / {no_sell}")

            print(f"xAxisValue = {market.get('xAxisValue')}")
            print(f"yAxisValue = {market.get('yAxisValue')}")
            print(f"outcomePrices = {market.get('outcomePrices')}")
            print(f"ammType = {market.get('ammType')}")
            print(f"volume = {market.get('volume')}")
            print(f"liquidity = {market.get('liquidity')}")

            # Decision logic
            decision = should_enter_market(market)

            if not decision:
                print(" - Status: Too early or threshold not met")
                continue

            side, token = decision
            # Determine correct live price to use
            if side == "YES":
                entry_price = yes_sell  # You pay the sell/ask
            else:
                entry_price = no_sell

            print(GREEN + f" >>> ENTERING {side} at market price {entry_price}! Token={token}" + RESET)

            try:
                order = place_buy_order(polyclient, token_id=token, price=entry_price)
                print(GREEN + f" - Order placed! ID: {order}" + RESET)
            except Exception as e:
                print(RED + f" - Order failed: {e}" + RESET)






        time.sleep(20)

def place_orders_for_future_market():
    print("\n=== Grabbing Upcoming 15m Markets and Placing Orders Once ===\n")

    # Get all crypto 15m series
    series_list = get_crypto_15m_series()

    for s in series_list:
        print(f"\nSeries: {s['title']}")

        # Step 1: Get the next upcoming event
        active_event = get_active_event_from_series(s)
        if not active_event:
            print(" No upcoming event found, skipping.")
            continue

        slug = active_event["slug"]
        print(f" Upcoming Event Slug: {slug}")

        # Step 2: Get the MARKET info from the event slug
        market = get_market_from_series(slug)
        if not market:
            print(" ERROR: Could not load market.")
            continue

        print(f" Market Question: {market.get('question')}")

        # Step 3: Get YES / NO tokens
        yes_token, no_token = get_yes_no_tokens(market)
        print(f" YES token = {yes_token}")
        print(f" NO  token = {no_token}")

        # Step 4: Build order price + size
        price = 0.49
        size = 5

        # Step 5: Place orders ONCE for each market
        print("\n Placing YES order...")
        try:
            order_yes = place_buy_order(polyclient, yes_token, price=price, size=size)
            print(GREEN + f" YES ORDER PLACED: {order_yes}" + RESET)
        except Exception as e:
            print(RED + f" Failed YES order: {e}" + RESET)

        print(" Placing NO order...")
        try:
            order_no = place_buy_order(polyclient, no_token, price=price, size=size)
            print(GREEN + f" NO ORDER PLACED: {order_no}" + RESET)
        except Exception as e:
            print(RED + f" Failed NO order: {e}" + RESET)

    print("\n=== All orders placed once. Exiting. ===\n")



def run_every_15_minutes():
    print("\n===== 15-MINUTE MASTER LOOP STARTED =====\n")

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n=== New Cycle @ {now} UTC ===")

        # 1. Place orders for FUTURE market (49¢ YES/NO)
        try:
            print("\nRunning future-market order placement...")
            place_orders_for_future_market()
        except Exception as e:
            print(RED + f"Error in future market orders: {e}" + RESET)

        # 2. Run the live-trading bot for current markets
        try:
            print("\nRunning live trading bot...")
            start_trading_bot()
        except Exception as e:
            print(RED + f"Error in live trading bot: {e}" + RESET)

        # 3. Sleep 15 minutes
        print(GREEN + "\nSleeping for 15 minutes...\n" + RESET)
        time.sleep(60)


if __name__ == "__main__":

    cfg = load_env()
    print("Env loaded OK:", cfg["host"], cfg["chain_id"])

    polyclient = load_client()
    polyclient.set_api_creds(polyclient.create_or_derive_api_creds())


    if (healthcheck()):
        print(f"{GREEN}All systems go!{RESET}")
    else:
        print(f"{RED}System check failed.{RESET}")
        Exception("Healthcheck failed")

    run_every_15_minutes()








