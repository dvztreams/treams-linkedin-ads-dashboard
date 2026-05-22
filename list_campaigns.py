"""
List all campaigns in the pinned Treams ad account.

Run:
    python list_campaigns.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
import requests
from dotenv import load_dotenv

from smoke_test import get_access_token, headers, BASE_URL

load_dotenv()


def fmt_budget(b: dict | None) -> str:
    if not b:
        return "—"
    return f"{b.get('currencyCode', '')} {b.get('amount', '?')}"


def fmt_date(d) -> str:
    if d is None:
        return "—"
    if isinstance(d, int):
        return datetime.fromtimestamp(d / 1000).strftime("%Y-%m-%d")
    if isinstance(d, dict):
        try:
            return f"{d['year']}-{d['month']:02d}-{d['day']:02d}"
        except (KeyError, TypeError):
            return str(d)
    return str(d)


def fmt_schedule(sched: dict | None) -> str:
    if not sched:
        return "—"
    start = fmt_date(sched.get("start"))
    end = fmt_date(sched.get("end")) if sched.get("end") else "ongoing"
    return f"{start} → {end}"


def list_campaigns(token: str, account_urn: str) -> list[dict]:
    account_id = account_urn.split(":")[-1]
    url = f"{BASE_URL}/adAccounts/{account_id}/adCampaigns"
    params = {"q": "search", "count": 100}

    all_campaigns: list[dict] = []
    start = 0
    while True:
        params["start"] = start
        resp = requests.get(url, headers=headers(token), params=params, timeout=30)
        if not resp.ok:
            print(f"Request failed: {resp.status_code}")
            print(resp.text)
            resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        all_campaigns.extend(elements)
        paging = data.get("paging", {})
        total = paging.get("total", len(all_campaigns))
        if len(all_campaigns) >= total or not elements:
            break
        start += len(elements)
    return all_campaigns


def main() -> None:
    account_urn = os.getenv("LINKEDIN_AD_ACCOUNT_URN")
    if not account_urn:
        sys.exit("LINKEDIN_AD_ACCOUNT_URN not set in .env")

    token = get_access_token()
    campaigns = list_campaigns(token, account_urn)
    print(f"Found {len(campaigns)} campaign(s) in {account_urn}\n")

    by_status: dict[str, list[dict]] = {}
    for c in campaigns:
        by_status.setdefault(c.get("status", "UNKNOWN"), []).append(c)

    for status in sorted(by_status.keys()):
        items = by_status[status]
        print(f"━━━ {status} ({len(items)}) ━━━")
        for c in items:
            print(f"\n  {c.get('name', '(no name)')}")
            print(f"    id:         {c.get('id')}")
            print(f"    type:       {c.get('type')}")
            print(f"    objective:  {c.get('objectiveType', '—')}")
            print(f"    format:     {c.get('format', '—')}")
            print(f"    daily:      {fmt_budget(c.get('dailyBudget'))}")
            print(f"    total:      {fmt_budget(c.get('totalBudget'))}")
            print(f"    schedule:   {fmt_schedule(c.get('runSchedule'))}")
        print()


if __name__ == "__main__":
    main()
