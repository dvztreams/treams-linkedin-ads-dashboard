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

# Layer detection — primary via naming convention Treams_[Cold|Warm]_...
COLD_OBJECTIVES = {"BRAND_AWARENESS", "ENGAGEMENT", "VIDEO_VIEW"}
WARM_OBJECTIVES = {"WEBSITE_CONVERSION", "LEAD_GENERATION", "WEBSITE_VISIT"}

# RAG thresholds from linkedin-ads-audit.skill
# Cold Layer: higher is better for most metrics; CPM lower is better
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
    """Return (layer, source). source = 'name' or 'heuristic' or 'unlabeled'."""
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
    """Pull aggregated analytics for the lookback window.

    pivot=CAMPAIGN  → one row per campaign, keyed by campaign id
    pivot=CREATIVE  → one row per creative, keyed by creative id
    """
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
        "pivotValues",
        "impressions",
        "clicks",
        "costInLocalCurrency",
        "likes",
        "comments",
        "shares",
        "follows",
        "videoViews",
        "videoCompletions",
        "oneClickLeads",
        "landingPageClicks",
        "externalWebsiteConversions",
        "approximateUniqueImpressions",
    ])

    query = (
        f"q=analytics"
        f"&pivot={pivot}"
        f"&timeGranularity=ALL"
        f"&dateRange={date_range}"
        f"&campaigns=List({campaign_urns})"
        f"&fields={fields}"
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
    """Pull DAILY analytics over DAILY_LOOKBACK_DAYS. Returns list of rows,
    each with date + pivot_id + raw metric fields."""
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
        "dateRange",
        "pivotValues",
        "impressions",
        "clicks",
        "costInLocalCurrency",
        "likes",
        "comments",
        "shares",
        "follows",
        "videoViews",
        "videoCompletions",
        "oneClickLeads",
        "landingPageClicks",
        "externalWebsiteConversions",
        "approximateUniqueImpressions",
    ])

    query = (
        f"q=analytics"
        f"&pivot={pivot}"
        f"&timeGranularity=DAILY"
        f"&dateRange={date_range}"
        f"&campaigns=List({campaign_urns})"
        f"&fields={fields}"
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
    """Pull per-company reach over the lookback window via MEMBER_COMPANY pivot."""
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
        "pivotValues",
        "impressions",
        "clicks",
        "costInLocalCurrency",
        "likes",
        "comments",
        "shares",
        "follows",
        "approximateUniqueImpressions",
    ])

    query = (
        f"q=analytics"
        f"&pivot=MEMBER_COMPANY"
        f"&timeGranularity=ALL"
        f"&dateRange={date_range}"
        f"&campaigns=List({campaign_urns})"
        f"&fields={fields}"
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
    """Fetch creative metadata for the given campaigns. Keyed by creative id."""
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

        # Ad-level breakdown
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


def write_companies_csv(rows: list[dict], path: str) -> None:
    fieldnames = [
        "company_urn", "impressions", "clicks", "social_actions",
        "spend", "unique_members", "frequency",
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
            writer.writerow({
                "company_urn": row["company_urn"],
                "impressions": impressions,
                "clicks": row.get("clicks", 0) or 0,
                "social_actions": social_actions,
                "spend": float(row.get("costInLocalCurrency", 0) or 0),
                "unique_members": unique,
                "frequency": safe_div(impressions, unique),
            })


def write_daily_campaigns_csv(daily_rows: list[dict], campaigns_by_id: dict, path: str) -> None:
    fieldnames = [
        "date", "campaign_id", "campaign_name", "layer", "layer_source", "objective",
        "spend", "impressions", "clicks", "social_actions", "video_views",
        "leads", "landing_page_clicks", "unique_members", "frequency",
        "engagement_rate", "social_engagement_rate", "ctr", "video_view_rate",
        "lpv_rate", "cpm", "cpc", "cpl",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in daily_rows:
            c = campaigns_by_id.get(row["pivot_id"], {})
            layer, source = parse_layer(c.get("name", ""), c.get("objectiveType", ""))
            metrics = compute_metrics(row)
            writer.writerow({
                "date": row["date"],
                "campaign_id": row["pivot_id"],
                "campaign_name": c.get("name", ""),
                "layer": layer,
                "layer_source": source,
                "objective": c.get("objectiveType", ""),
                **metrics,
            })


def write_daily_ads_csv(
    daily_rows: list[dict],
    creatives_meta: dict,
    campaigns_by_id: dict,
    path: str,
) -> None:
    fieldnames = [
        "date", "ad_id", "campaign_id", "campaign_name", "layer", "ad_type", "ad_status",
        "spend", "impressions", "clicks", "social_actions", "video_views",
        "leads", "landing_page_clicks", "unique_members", "frequency",
        "engagement_rate", "social_engagement_rate", "ctr", "video_view_rate",
        "lpv_rate", "cpm", "cpc", "cpl",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in daily_rows:
            ad_id = row["pivot_id"]
            cr = creatives_meta.get(ad_id, {})
            campaign_urn = cr.get("campaign", "")
            try:
                cid = int(campaign_urn.split(":")[-1])
            except (ValueError, AttributeError):
                cid = None
            c = campaigns_by_id.get(cid, {})
            layer, _ = parse_layer(c.get("name", ""), c.get("objectiveType", ""))
            metrics = compute_metrics(row)
            writer.writerow({
                "date": row["date"],
                "ad_id": ad_id,
                "campaign_id": cid,
                "campaign_name": c.get("name", ""),
                "layer": layer,
                "ad_type": cr.get("type", ""),
                "ad_status": cr.get("intendedStatus") or cr.get("status", ""),
                **metrics,
            })


def write_ads_csv(rows: list[dict], path: str) -> None:
    fieldnames = [
        "campaign_id", "campaign_name", "layer", "ad_id", "ad_status", "ad_type",
        "spend", "impressions", "clicks", "social_actions", "video_views",
        "leads", "landing_page_clicks", "unique_members", "frequency",
        "engagement_rate", "social_engagement_rate", "ctr", "video_view_rate",
        "lpv_rate", "cpm", "cpc", "cpl",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            for a in r.get("ads", []):
                writer.writerow({
                    "campaign_id": r["id"],
                    "campaign_name": r["name"],
                    "layer": r["layer"],
                    "ad_id": a["id"],
                    "ad_status": a.get("status"),
                    "ad_type": a.get("type"),
                    **a["metrics"],
                })


def main() -> None:
    account_urn = os.getenv("LINKEDIN_AD_ACCOUNT_URN")
    if not account_urn:
        sys.exit("LINKEDIN_AD_ACCOUNT_URN not set in .env")

    token = get_access_token()
    print(f"Pulling campaigns for {account_urn}...")
    campaigns = list_campaigns(token, account_urn)
    active = [c for c in campaigns if c.get("status") == "ACTIVE"]
    print(f"  {len(campaigns)} total, {len(active)} active.\n")

    if not active:
        sys.exit("No active campaigns. Nothing to analyze.")

    campaign_ids = [c["id"] for c in active]

    print(f"Pulling campaign analytics (last {LOOKBACK_DAYS} days)...")
    analytics = fetch_analytics(token, campaign_ids, pivot="CAMPAIGN")
    print(f"  Got data for {len(analytics)} campaign(s).")

    print("Pulling creative metadata and per-ad analytics...")
    creatives_meta = fetch_creatives(token, account_urn, campaign_ids)
    creative_analytics = fetch_analytics(token, campaign_ids, pivot="CREATIVE")
    print(f"  Got {len(creatives_meta)} creative(s), analytics for {len(creative_analytics)}.\n")

    rows: list[dict] = []
    for c in active:
        layer, source = parse_layer(c.get("name", ""), c.get("objectiveType", ""))
        stats = analytics.get(c["id"], {})
        # Find creatives belonging to this campaign
        campaign_urn = f"urn:li:sponsoredCampaign:{c['id']}"
        ads = []
        for cr_id, cr in creatives_meta.items():
            if cr.get("campaign") == campaign_urn:
                ad_stats = creative_analytics.get(cr_id, {})
                ads.append({
                    "id": cr_id,
                    "status": cr.get("intendedStatus") or cr.get("status"),
                    "type": cr.get("type"),
                    "metrics": compute_metrics(ad_stats),
                })
        rows.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "status": c.get("status"),
            "objective": c.get("objectiveType", ""),
            "layer": layer,
            "layer_source": source,
            "metrics": compute_metrics(stats),
            "ads": ads,
        })

    for layer in ["Cold", "Warm", "Unlabeled"]:
        layer_rows = [r for r in rows if r["layer"] == layer]
        if layer_rows or layer != "Unlabeled":
            print_layer(layer, layer_rows)

    csv_path = "analytics_latest.csv"
    write_csv(rows, csv_path)
    print(f"\n📄 Saved campaign-level (L{LOOKBACK_DAYS}d snapshot): {csv_path}")

    ads_csv_path = "analytics_ads.csv"
    write_ads_csv(rows, ads_csv_path)
    print(f"📄 Saved ad-level (L{LOOKBACK_DAYS}d snapshot):       {ads_csv_path}")

    # Daily time-series for trend / decay detection in Looker
    print(f"\nPulling DAILY analytics (last {DAILY_LOOKBACK_DAYS} days)...")
    campaigns_by_id = {c["id"]: c for c in active}
    daily_campaigns = fetch_analytics_daily(token, campaign_ids, pivot="CAMPAIGN")
    daily_ads = fetch_analytics_daily(token, campaign_ids, pivot="CREATIVE")
    print(f"  {len(daily_campaigns)} campaign-day rows, {len(daily_ads)} ad-day rows.")

    daily_camp_path = "analytics_campaigns_daily.csv"
    write_daily_campaigns_csv(daily_campaigns, campaigns_by_id, daily_camp_path)
    print(f"📄 Saved daily campaigns: {daily_camp_path}")

    daily_ads_path = "analytics_ads_daily.csv"
    write_daily_ads_csv(daily_ads, creatives_meta, campaigns_by_id, daily_ads_path)
    print(f"📄 Saved daily ads:       {daily_ads_path}")

    # Company-level reach (Cold Layer account penetration)
    print(f"\nPulling company-level reach (last {LOOKBACK_DAYS} days)...")
    companies = fetch_company_reach(token, campaign_ids)
    total_impressions_with_company = sum((r.get("impressions") or 0) for r in companies)
    total_unique_members_company = sum((r.get("approximateUniqueImpressions") or 0) for r in companies)
    print(f"  Reached {len(companies)} unique companies")
    print(f"  Approx unique members reached: {total_unique_members_company:,}")
    if total_unique_members_company:
        print(f"  Frequency (impressions / members): {total_impressions_with_company / total_unique_members_company:.2f}x")

    if companies:
        print("\n  Top 10 companies by impressions:")
        top = sorted(companies, key=lambda r: -(r.get("impressions") or 0))[:10]
        for c in top:
            urn = c["company_urn"]
            print(f"    {urn}  imp {c.get('impressions', 0):,}  clicks {c.get('clicks', 0):,}")

    companies_path = "analytics_companies.csv"
    write_companies_csv(companies, companies_path)
    print(f"\n📄 Saved companies:       {companies_path}")

    # Push everything to the Google Sheet (no-op if not configured)
    try:
        from sheet_writer import push_all
        push_all()
    except Exception as e:
        print(f"⚠ Sheet upload failed: {e}")


if __name__ == "__main__":
    main()
