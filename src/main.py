import os
from pathlib import Path
from dotenv import load_dotenv

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

import requests

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

if __name__ == "__main__":

    cfg = load_env()
    print("Env loaded OK:", cfg["host"], cfg["chain_id"])

    polyclient = load_client()

    if (healthcheck()):
        print(f"{GREEN}All systems go!{RESET}")
    else:
        print(f"{RED}System check failed.{RESET}")
        Exception("Healthcheck failed")

    series = get_crypto_15m_series()

    print(f"Found {len(series)} crypto 15m series:")
    for s in series:
        print("-", s["title"], "slug:", s["slug"], "id:", s["id"])








