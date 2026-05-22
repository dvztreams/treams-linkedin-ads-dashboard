"""
Pull last 30 days of analytics per active campaign, detect layer,
apply RAG scoring against the linkedin-ads-audit skill benchmarks,
and print a per-layer report. Also writes a CSV.

Run:
    python pull_analytics.py
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date, timedelta
import requests
from dotenv import load_dotenv

from smoke_test import get_access_token, headers, BASE_URL
from list_campaigns import list_campaigns

load_dotenv()

LOOKBACK_DAYS = 30
DAILY_LOOKBACK_DAYS = 90

COLD_OBJECTIVES = {"BRAND_AWARENESS", "ENGAGEMENT", "VIDEO_VIEW"}
WARM_OBJECTIVES = {"WEBSITE_CONVERSION", "LEAD_GENERATION", "WEBSITE_VISIT"}

COLD_THRESHOLDS = {
    "engagement_rate": {"good": 0.15, "avg": 0.10, "higher_is_better": True},
    "social_engagement_rate": {"good": 0.005, "avg": 0.003, "higher_is_better": True},
    "video_view_rate": {"good": 0.25, "avg": 0.15, "higher_is_better": True},
    "lpv_rate": {"good": 0.10, "avg": 0.05, "higher_is_better": True},
    "cpm": {"good": 30, "avg": 60, "higher_is_better": False},
}
WARM_THRESHOLDS = {
    "ctr": {"good": 0.008, "avg": 0.004, "higher_is_better": True},
    "cpc": {"good": 5, "avg": 10, "higher_is_better": False},
    "cpm": {"good": 30, "avg": 60, "higher_is_better": False},
    "cpl": {"good": 50, "avg": 150, "higher_is_better": False},
}


def parse_layer(name: str, objective: str) -> tuple[str, str]:
    parts = (name or "").split("_")
    if len(parts) >= 2 and parts[0].lower() == "treams":
        layer = parts[1].lower()
        if layer == "cold":
            return "Cold", "name"
        if layer == "warm":
            return "Warm", "name"
    if objective in COLD_OBJECTIVES:
        return "Cold", "heuristic"
    if objective in WARM_OBJECTIVES:
        return "Warm", "heuristic"
    return "Unlabeled", "unlabeled"


def rag(value, good, avg, higher_is_better):
    if value is None:
        return "⚪"
    if higher_is_better:
        if value >= good:
            return "🟢"
        if value >= avg:
            return "🟡"
        return "🔴"
    else:
        if value <= good:
            return "🟢"
        if value <= avg:
            return "🟡"
        return "🔴"


def safe_div(a, b):
    return (a / b) if b else None


def fetch_analytics(token: str, campaign_ids: list[int], pivot: str = "CAMPAIGN") -> dict[int, dict]:
    if not campaign_ids:
        return {}
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    date_range = (
        f"(start:(year:{start.year},month:{start.month},day:{start.day}),"
        f"end:(year:{end.year},month:{end.month},day:{end.day}))"
    )
    campaign_urns = ",".join(
        f"urn%3Ali%3AsponsoredCampaign%3A{cid}" for cid in campaign_ids
    )
    fields = ",".join([
        "pivotValues", "impressions", "clicks", "costInLocalCurrency",
        "likes", "comments", "shares", "follows",
        "videoViews", "videoCompletions",
        "oneClickLeads", "landingPageClicks", "externalWebsiteConversions",
        "approximateUniqueImpressions",
    ])
    query = (
        f"q=analytics&pivot={pivot}&timeGranularity=ALL"
        f"&dateRange={date_range}&campaigns=List({campaign_urns})&fields={fields}"
    )
    url = f"{BASE_URL}/adAnalytics?{query}"
    resp = requests.get(url, headers=headers(token), timeout=60)
    if not resp.ok:
        print(f"Analytics request failed: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()
    out: dict[int, dict] = {}
    for row in resp.json().get("elements", []):
        pivot_values = row.get("pivotValues", [])
        if not pivot_values:
            continue
        urn = pivot_values[0]
        try:
            pid = int(urn.split(":")[-1])
        except (ValueError, AttributeError):
            continue
        out[pid] = row
    return out


def fetch_analytics_daily(token: str, campaign_ids: list[int], pivot: str = "CAMPAIGN") -> list[dict]:
    if not campaign_ids:
        return []
    end = date.today()
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)
    date_range = (
        f"(start:(year:{start.year},month:{start.month},day:{start.day}),"
        f"end:(year:{end.year},month:{end.month},day:{end.day}))"
    )
    campaign_urns = ",".join(
        f"urn%3Ali%3AsponsoredCampaign%3A{cid}" for cid in campaign_ids
    )
    fields = ",".join([
        "dateRange", "pivotValues", "impressions", "clicks", "costInLocalCurrency",
        "likes", "comments", "shares", "follows",
        "videoViews", "videoCompletions",
        "oneClickLeads", "landingPageClicks", "externalWebsiteConversions",
        "approximateUniqueImpressions",
    ])
    query = (
        f"q=analytics&pivot={pivot}&timeGranularity=DAILY"
        f"&dateRange={date_range}&campaigns=List({campaign_urns})&fields={fields}"
    )
    url = f"{BASE_URL}/adAnalytics?{query}"
    resp = requests.get(url, headers=headers(token), timeout=120)
    if not resp.ok:
        print(f"Daily analytics request failed: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()
    out: list[dict] = []
    for row in resp.json().get("elements", []):
        pivot_values = row.get("pivotValues", [])
        if not pivot_values:
            continue
        urn = pivot_values[0]
        try:
            pid = int(urn.split(":")[-1])
        except (ValueError, AttributeError):
            continue
        dr = row.get("dateRange", {}).get("start", {})
        try:
            day = date(dr["year"], dr["month"], dr["day"]).isoformat()
        except (KeyError, TypeError):
            continue
        out.append({"date": day, "pivot_id": pid, **row})
    return out


def fetch_company_reach(token: str, campaign_ids: list[int]) -> list[dict]:
    if not campaign_ids:
        return []
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    date_range = (
        f"(start:(year:{start.year},month:{start.month},day:{start.day}),"
        f"end:(year:{end.year},month:{end.month},day:{end.day}))"
    )
    campaign_urns = ",".join(
        f"urn%3Ali%3AsponsoredCampaign%3A{cid}" for cid in campaign_ids
    )
    fields = ",".join([
        "pivotValues", "impressions", "clicks", "costInLocalCurrency",
        "likes", "comments", "shares", "follows",
        "approximateUniqueImpressions",
    ])
    query = (
        f"q=analytics&pivot=MEMBER_COMPANY&timeGranularity=ALL"
        f"&dateRange={date_range}&campaigns=List({campaign_urns})&fields={fields}"
    )
    url = f"{BASE_URL}/adAnalytics?{query}"
    resp = requests.get(url, headers=headers(token), timeout=120)
    if not resp.ok:
        print(f"Company-reach request failed: {resp.status_code}")
        print(resp.text)
        return []
    out: list[dict] = []
    for row in resp.json().get("elements", []):
        pivot_values = row.get("pivotValues", [])
        if not pivot_values:
            continue
        company_urn = pivot_values[0]
        out.append({"company_urn": company_urn, **row})
    return out


def fetch_creatives(token: str, account_urn: str, campaign_ids: list[int]) -> dict[int, dict]:
    if not campaign_ids:
        return {}
    account_id = account_urn.split(":")[-1]
    campaign_urns = ",".join(
        f"urn%3Ali%3AsponsoredCampaign%3A{cid}" for cid in campaign_ids
    )
    url = (
        f"{BASE_URL}/adAccounts/{account_id}/creatives"
        f"?q=criteria&campaigns=List({campaign_urns})&count=100"
    )
    resp = requests.get(url, headers=headers(token), timeout=30)
    if not resp.ok:
        print(f"Creatives request failed: {resp.status_code}")
        print(resp.text)
        return {}
    out: dict[int, dict] = {}
    for cr in resp.json().get("elements", []):
        cr_urn = cr.get("id") or ""
        try:
            cid = int(cr_urn.split(":")[-1])
        except ValueError:
            continue
        out[cid] = cr
    return out


def compute_metrics(stats: dict) -> dict:
    impressions = stats.get("impressions", 0) or 0
    clicks = stats.get("clicks", 0) or 0
    spend = float(stats.get("costInLocalCurrency", 0) or 0)
    likes = stats.get("likes", 0) or 0
    comments = stats.get("comments", 0) or 0
    shares = stats.get("shares", 0) or 0
    follows = stats.get("follows", 0) or 0
    video_views = stats.get("videoViews", 0) or 0
    leads = stats.get("oneClickLeads", 0) or 0
    landing_page_clicks = stats.get("landingPageClicks", 0) or 0
    unique_members = stats.get("approximateUniqueImpressions", 0) or 0

    social_actions = likes + comments + shares + follows
    total_engagement = social_actions + clicks

    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "social_actions": social_actions,
        "video_views": video_views,
        "leads": leads,
        "landing_page_clicks": landing_page_clicks,
        "unique_members": unique_members,
        "frequency": safe_div(impressions, unique_members),
        "engagement_rate": safe_div(total_engagement, impressions),
        "social_engagement_rate": safe_div(social_actions, impressions),
        "ctr": safe_div(clicks, impressions),
        "video_view_rate": safe_div(video_views, impressions),
        "lpv_rate": safe_div(landing_page_clicks, clicks),
        "cpm": safe_div(spend * 1000, impressions),
        "cpc": safe_div(spend, clicks),
        "cpl": safe_div(spend, leads),
    }


def fmt_pct(v):
    return f"{v * 100:.2f}%" if v is not None else "—"


def fmt_money(v):
    return f"€{v:.2f}" if v is not None else "—"


def print_layer(layer: str, rows: list[dict]) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {layer.upper()} LAYER ({len(rows)} campaign(s)) — last {LOOKBACK_DAYS} days")
    print(f"{'═' * 70}")
    if not rows:
        print("  No campaigns.")
        return

    total_spend = sum(r["metrics"]["spend"] for r in rows)
    total_impressions = sum(r["metrics"]["impressions"] for r in rows)
    print(f"  Total spend: {fmt_money(total_spend)} | Impressions: {total_impressions:,}\n")

    for r in rows:
        m = r["metrics"]
        print(f"  ▸ {r['name']}  [{r['layer_source']}]")
        print(f"    spend {fmt_money(m['spend'])}  |  impressions {m['impressions']:,}  |  clicks {m['clicks']:,}")
        if m["unique_members"]:
            freq = m["frequency"]
            freq_str = f"{freq:.2f}x" if freq else "—"
            print(f"    unique members reached: {m['unique_members']:,}  |  frequency: {freq_str}")
        if layer == "Cold":
            er_rag = rag(m["engagement_rate"], **COLD_THRESHOLDS["engagement_rate"])
            cpm_rag = rag(m["cpm"], **COLD_THRESHOLDS["cpm"])
            lpv_rag = rag(m["lpv_rate"], **COLD_THRESHOLDS["lpv_rate"]) if m["clicks"] else "⚪"
            vvr_rag = rag(m["video_view_rate"], **COLD_THRESHOLDS["video_view_rate"]) if m["video_views"] else "⚪"
            print(f"    {er_rag} engagement rate: {fmt_pct(m['engagement_rate'])}  (target ≥15%)")
            print(f"    {cpm_rag} CPM:             {fmt_money(m['cpm'])}                  (target ≤€30)")
            if m["clicks"]:
                print(f"    {lpv_rag} LPV rate:        {fmt_pct(m['lpv_rate'])}  (P.S. CTA effect, target ≥10%, {m['landing_page_clicks']}/{m['clicks']})")
            if m["video_views"]:
                print(f"    {vvr_rag} video view rate: {fmt_pct(m['video_view_rate'])}  (target ≥25%)")
        elif layer == "Warm":
            ctr_rag = rag(m["ctr"], **WARM_THRESHOLDS["ctr"])
            cpc_rag = rag(m["cpc"], **WARM_THRESHOLDS["cpc"])
            cpm_rag = rag(m["cpm"], **WARM_THRESHOLDS["cpm"])
            cpl_rag = rag(m["cpl"], **WARM_THRESHOLDS["cpl"]) if m["leads"] else "⚪"
            print(f"    {ctr_rag} CTR:             {fmt_pct(m['ctr'])}  (target ≥0.8%)")
            print(f"    {cpc_rag} CPC:             {fmt_money(m['cpc'])}     (target ≤€5)")
            print(f"    {cpm_rag} CPM:             {fmt_money(m['cpm'])}     (target ≤€30)")
            if m["leads"]:
                print(f"    {cpl_rag} CPL:             {fmt_money(m['cpl'])}     (target ≤€50, {m['leads']} leads)")

        ads = r.get("ads", [])
        ads_with_spend = [a for a in ads if a["metrics"]["impressions"] > 0]
        if ads_with_spend:
            print(f"    └─ ads ({len(ads_with_spend)} with delivery):")
            for a in sorted(ads_with_spend, key=lambda x: -x["metrics"]["spend"]):
                am = a["metrics"]
                key_metric = ""
                if layer == "Cold":
                    er = am["engagement_rate"]
                    er_r = rag(er, **COLD_THRESHOLDS["engagement_rate"]) if er is not None else "⚪"
                    key_metric = f"{er_r} ER {fmt_pct(er)}"
                elif layer == "Warm":
                    ctr = am["ctr"]
                    ctr_r = rag(ctr, **WARM_THRESHOLDS["ctr"]) if ctr is not None else "⚪"
                    key_metric = f"{ctr_r} CTR {fmt_pct(ctr)}"
                print(f"        ad {a['id']}  spend {fmt_money(am['spend'])}  imp {am['impressions']:,}  {key_metric}")
        print()


def write_csv(rows: list[dict], path: str) -> None:
    fieldnames = [
        "campaign_id", "name", "status", "layer", "layer_source", "objective",
        "spend", "impressions", "clicks", "social_actions", "video_views",
        "leads", "landing_page_clicks", "unique_members", "frequency",
        "engagement_rate", "social_engagement_rate", "ctr", "video_view_rate",
        "lpv_rate", "cpm", "cpc", "cpl",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            m = r["metrics"]
            writer.writerow({
                "campaign_id": r["id"],
                "name": r["name"],
                "status": r["status"],
                "layer": r["layer"],
                "layer_source": r["layer_source"],
                "objective": r["objective"],
                **m,
            })


def write_companies_csv(rows: list[dict], path: str, total_spend_l30: float = 0.0) -> None:
    fieldnames = [
        "company_urn", "company_id", "linkedin_url",
        "impressions", "clicks", "social_actions",
        "spend", "unique_members", "frequency",
        "total_spend_l30",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            likes = row.get("likes", 0) or 0
            comments = row.get("comments", 0) or 0
            shares = row.get("shares", 0) or 0
            follows = row.get("follows", 0) or 0
            social_actions = likes + comments + shares + follows
            impressions = row.get("impressions", 0) or 0
            unique = row.get("approximateUniqueImpressions", 0) or 0
            urn = row["company_urn"]
            try:
                company_id = urn.split(":")[
