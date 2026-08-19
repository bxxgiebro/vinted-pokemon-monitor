# Vinted Pokemon Deal Monitor

Watches Vinted searches for Pokemon cards/sealed product, scores each new
listing against configurable rules, and pings a Discord channel when it finds
a good deal. Runs entirely on GitHub Actions' free tier — no server, no paid
database.

## How it works

```
config.yaml  →  src/monitor.py  →  vinted_scraper (Vinted's search API)
                       │
                       ├─ dedupe against data/seen_items.json
                       ├─ score via src/deal_scorer.py
                       └─ alert via src/discord_notifier.py (Discord webhook)
```

A scheduled GitHub Actions workflow (`.github/workflows/monitor.yml`) runs
`monitor.py` every 15 minutes, then commits the updated `data/*.json` files
back to the repo. That's the entire persistence layer — no external DB.

## Setup

1. **Create the repo as public.** Public repos get unlimited GitHub Actions
   minutes; private repos are capped at 2,000 min/month on the free tier. At
   15-minute intervals with ~1-2 min runs, you'd use roughly 3,000-6,000
   min/month — comfortably free on public, but would blow past the private
   free quota. If you need it private, drop to every 30-60 min instead.

2. **Add the Discord webhook secret.** In your Discord server:
   Server Settings → Integrations → Webhooks → New Webhook → copy the URL.
   In GitHub: repo → Settings → Secrets and variables → Actions → New
   repository secret → name it `DISCORD_WEBHOOK_URL`.
   (Optional: `DISCORD_ROLE_ID` if you want a role pinged on alerts.)

3. **Edit `config.yaml`** — this is the only file you should need to touch
   regularly:
   - `searches`: one block per thing you're hunting for. `params` mirrors
     Vinted's own search filters (search_text, catalog_ids, price range...).
   - `rules`: your deal criteria per search — see "Tuning what counts as a
     good deal" below.

4. **Push to GitHub.** The workflow starts running on its schedule
   automatically, or trigger it manually from the Actions tab
   (`workflow_dispatch`) to test.

## Tuning what counts as a "good deal"

Rather than hard-coding prices that go stale, each search combines two
things:

- **Rules you set** in `config.yaml`: `max_price`, `min_price`,
  `include_keywords`, `exclude_keywords`, `min_score_to_alert`.
- **A self-updating rolling average**: the bot remembers the last ~50 prices
  it has seen for that search (`data/price_history.json`) and rewards
  listings priced well below that recent average
  (`below_rolling_average_pct`). As the market moves up or down over the
  months, this bar moves with it automatically — you shouldn't need to touch
  numbers every month, only when your own preferences change (e.g. you now
  only want PSA 10s, not raw cards).

Score is 0-100; see `src/deal_scorer.py` for exact logic. Everything you'd
realistically want to tune lives in `config.yaml`, not the code.

## Honest limitations — please read before relying on this

- **Vinted actively fights scraping** (Datadome anti-bot). The `vinted_scraper`
  library handles session cookies automatically and works today, but Vinted
  changes its defenses periodically and libraries like this occasionally
  break until patched upstream. If runs start failing, check for a new
  release of `vinted_scraper` first.
- **Shared GitHub Actions runner IPs are sometimes flagged** by anti-bot
  systems more aggressively than a home IP would be, since many unrelated
  projects share the same IP ranges. If you see persistent failures, options
  are: reduce frequency, or self-host the script on a Raspberry Pi/home
  server via cron instead of Actions (same code, just change how it's
  triggered — nothing else needs to change).
- **This is not financial/market data** — the rolling average is only built
  from what *this bot* has seen, not true sold-comp pricing. Treat scores as
  a filter to reduce noise, not gospel.
- `catalog_ids` in the example config (2971 = trading cards on Vinted FR) may
  differ by country domain — search on the Vinted site once with your
  filters set, then copy the `catalog[]=` value from the URL into config.yaml.

## Local testing

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python src/monitor.py
```
