# Clash of Clans Progress Tracker

A Streamlit dashboard that tracks a clan's Clash of Clans progress over time:
member snapshots, wars, Clan War League, and Clan Capital raids.

## Local setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in your real `COC_API_TOKEN` and `CLAN_TAG`.
   Get a token at https://developer.clashofclans.com - keys are locked to the
   IP address(es) you whitelist when creating them.
3. `streamlit run app.py`

By default this stores data locally in `data/clash.db` (SQLite). No extra setup needed.

## Deploying to Render

1. Push this repo to GitHub (`.env` is git-ignored - never commit it).
2. Create a free Postgres database at https://neon.tech (permanent free tier -
   Render's own free Postgres expires after 30 days, and Render's free web-service
   disk doesn't persist across deploys, so Neon is what keeps your history around).
3. On Render, create a new Web Service from this repo. It will pick up `render.yaml`
   automatically. If not, set manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true`
4. In the Render service's Environment settings, set:
   - `COC_API_TOKEN` - your Clash of Clans API token
   - `CLAN_TAG` - e.g. `#2PP`
   - `DATABASE_URL` - the connection string Neon gives you (starts with `postgresql://`)
5. **IP whitelisting**: your CoC API token only works from IP addresses you've
   whitelisted for it. Render's free tier doesn't have a fixed outbound IP, so if
   the token stops working after a redeploy, run `scripts/refresh_coc_key.py`
   (see the docstring in that file) to re-whitelist the current IP and update
   `COC_API_TOKEN` in Render with the new token it prints.

## Scheduled data collection

`scripts/collect_data.py` can be run on a schedule (Render Cron Job, Task
Scheduler, etc.) to collect snapshots/wars/CWL/capital data without opening
the dashboard. Because it uses the same `DATABASE_URL`-aware storage layer,
a Render Cron Job service can share the same Neon database as the web service.
