"""
Smoke test: can we talk to the LinkedIn Marketing API?

Run:
    pip install -r requirements.txt
    cp .env.example .env  # then fill in credentials
    python smoke_test.py

Success looks like a printed list of ad accounts the token has access to.
"""

from __future__ import annotations

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

LINKEDIN_VERSION = "202604"
BASE_URL = "https://api.linkedin.com/rest"


def get_access_token() -> str:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if token:
        return token

    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    refresh_token = os.getenv("LINKEDIN_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        sys.exit(
            "No credentials found. Set LINKEDIN_ACCESS_TOKEN, or all of "
            "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / LINKEDIN_REFRESH_TOKEN in .env."
        )

    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def list_ad_accounts(token: str) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/adAccounts",
        headers=headers(token),
        params={"q": "search"},
        timeout=30,
    )
    if not resp.ok:
        print(f"Request failed: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()
    return resp.json().get("elements", [])


def main() -> None:
    print("Authenticating...")
    token = get_access_token()
    print("OK — token acquired.\n")

    print("Fetching ad accounts...")
    accounts = list_ad_accounts(token)
    print(f"Found {len(accounts)} ad account(s):\n")
    for acc in accounts:
        print(f"  - {acc.get('name', '(no name)')}")
        print(f"    id:       {acc.get('id')}")
        print(f"    status:   {acc.get('status')}")
        print(f"    currency: {acc.get('currency')}")
        print(f"    type:     {acc.get('type')}")
        print()

    if not accounts:
        print("No accounts returned. Either the token lacks rw_ads/r_ads scope,")
        print("or this user has no Campaign Manager access on any account.")
        sys.exit(1)

    print("Smoke test passed. We can read from the LinkedIn Marketing API.")


if __name__ == "__main__":
    main()
