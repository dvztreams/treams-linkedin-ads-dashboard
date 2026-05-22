"""
One-time helper: walk through the LinkedIn OAuth flow to get a refresh token.

Prerequisites in .env:
    LINKEDIN_CLIENT_ID=...
    LINKEDIN_CLIENT_SECRET=...

And in your LinkedIn Developer App → Auth tab:
    Redirect URL configured: http://localhost:8080/callback

Run:
    python generate_refresh_token.py

The script:
  1. Prints a URL — open it in your browser
  2. You sign in and authorize
  3. LinkedIn redirects to localhost (your browser will show "can't connect" — that's fine)
  4. Copy the FULL URL from the address bar and paste it here
  5. Script exchanges the code for tokens and prints what to put in .env
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "r_ads r_ads_reporting r_organization_social"


def main() -> None:
    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env first.")

    # Step 1: build authorization URL
    auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "treams_setup",
    })

    print("=" * 70)
    print("STEP 1 — Open this URL in your browser:")
    print("=" * 70)
    print(auth_url)
    print()
    print("=" * 70)
    print("STEP 2 — Sign in and authorize.")
    print("STEP 3 — Your browser will try to load http://localhost:8080/...")
    print("         You'll see 'This site can't be reached'. That's fine!")
    print("STEP 4 — Copy the FULL URL from the address bar (starts with")
    print("         http://localhost:8080/callback?code=...) and paste below.")
    print("=" * 70)
    print()

    pasted = input("Paste the full redirected URL here: ").strip()
    if not pasted:
        sys.exit("No URL provided. Aborting.")

    parsed = urlparse(pasted)
    qs = parse_qs(parsed.query)
    code = qs.get("code", [None])[0]
    if not code:
        sys.exit("Couldn't find ?code= in the URL you pasted. Try again.")

    print("\n✓ Got authorization code. Exchanging for tokens...")

    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"Token exchange failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")

    print("\n" + "=" * 70)
    print("✓ SUCCESS")
    print("=" * 70)
    if access_token:
        print(f"Access token  (valid {expires_in} sec ≈ {expires_in // 86400} days):")
        print(f"  {access_token}\n")
    if refresh_token:
        print("Refresh token (valid 365 days, use this in .env):")
        print(f"  {refresh_token}\n")
        print("Put this in your .env:")
        print(f"  LINKEDIN_REFRESH_TOKEN={refresh_token}")
        print("And you can clear LINKEDIN_ACCESS_TOKEN — the script will refresh as needed.")
    else:
        print("⚠ LinkedIn did not return a refresh_token.")
        print("This usually means your app doesn't have refresh token support enabled.")
        print("In the LinkedIn Developer Portal, under Products, ensure")
        print("'Sign In with LinkedIn using OpenID Connect' or the relevant product is added.")


if __name__ == "__main__":
    main()
