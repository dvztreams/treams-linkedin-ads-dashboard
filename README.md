# LinkedIn Ads Dashboard — smoke test

Feasibility-check voor het dashboard-project. Doel van deze stap: bewijzen dat we met onze credentials de LinkedIn Marketing API kunnen aanspreken.

## Setup

```bash
cd marketing/tools/linkedin-ads-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Vul .env in met je credentials
python smoke_test.py
```

## Wat het script doet

1. Pakt het access token uit `.env` (direct, of via refresh-token exchange)
2. Roept `GET /rest/adAccounts?q=search` aan
3. Print de ad accounts waar het token toegang toe heeft

## Wat een succesvolle run betekent

We kunnen vanaf hier campaigns, creatives en analytics gaan trekken — de rest is bouwwerk. Als dit faalt, ligt het vrijwel zeker aan de scopes van het token (`r_ads` minimaal, `r_ads_reporting` voor analytics) of aan ontbrekende Marketing Developer Platform-toegang.

## Volgende stap (nog niet gebouwd)

- `pull_campaigns.py` — campaigns + campaign groups per account
- `pull_analytics.py` — daily metrics per campaign
- Sheet-writer
- Scoring-config
- Looker Studio dashboard
