Vinted Pokemon Deal Monitor
Watches Vinted searches for Pokemon cards/sealed product, scores each new
listing against configurable rules, and pings a Discord channel when it finds
a good deal. Runs entirely on GitHub Actions' free tier — no server, no paid
database.
How it works
```
config.yaml  →  src/monitor.py  →  vinted_scraper (Vinted's search API)
                       │
                       ├─ dedupe against data/seen_items.json
                       ├─ score via src/deal_scorer.py
                       └─ alert via src/discord_notifier.py (Discord webhook)
```
A GitHub Actions workflow (`.github/workflows/monitor.yml`) runs
`monitor.py` roughly every 5 minutes — triggered by an external free cron
pinger rather than GitHub's own `schedule:` trigger, see the section below
on why — then commits the updated `data/*.json` files back to the repo.
That's the entire persistence layer — no external DB.
Setup
Create the repo as public. Public repos get unlimited GitHub Actions
minutes; private repos are capped at 2,000 min/month on the free tier. At
5-minute intervals with ~1-2 min runs, that's roughly 6,000-12,000
min/month — comfortably free on public, but well past the private free
quota. If you need it private, drop the cron-job.org interval to every
20-30 min instead.
Add the Discord webhook secret. In your Discord server:
Server Settings → Integrations → Webhooks → New Webhook → copy the URL.
In GitHub: repo → Settings → Secrets and variables → Actions → New
repository secret → name it `DISCORD_WEBHOOK_URL`.
(Optional: `DISCORD_ROLE_ID` if you want a role pinged on alerts.)
Edit `config.yaml` — this is the only file you should need to touch
regularly:
`searches`: one block per thing you're hunting for. `params` mirrors
Vinted's own search filters (search_text, catalog_ids, price range...).
`rules`: your deal criteria per search — see "What counts as a deal"
below.
Push to GitHub, then set up the external cron pinger (see "Why the
schedule might not fire on its own" below) — that's what actually drives
this workflow. You can also trigger it manually from the Actions tab
(`workflow_dispatch`) to test before the pinger is set up.
What counts as a deal: genuine steals only
This bot is deliberately tuned to catch undercuts, not "slightly below
average" listings. A listing only triggers an alert if both are true:
It's at least `min_pct_below_average`% cheaper than the recent average
price the bot has seen for that search on that country's site.
It also undercuts the single cheapest price recently seen (`must_beat_recent_low`)
— not just cheaper than the average, but a new floor.
Nothing is filtered by keyword or minimum price by default. No "fake"/
"proxy" exclusion, no price-floor sanity check. Judging whether a
suspiciously cheap listing is mispriced, fake, or a genuine steal is left to
you — the bot's job is speed and price, not authentication. If you want that
filtering back, set `include_keywords`, `exclude_keywords`, `min_price`, or
`max_price` per search in `config.yaml`; they're all `null`/empty by
default.
Cold-start behavior: each search needs `min_history_size` (default 5)
real prices logged before it will alert on anything — with no history yet,
the bot can't tell what "far below average" even means for that search. In
practice this means the first several runs after you add a new search will
just be building up `data/price_history.json` silently, with no alerts,
even if the current listings are genuinely cheap. This is a one-time ramp-up
per search, not an ongoing limitation.
If you already know a good price, skip the wait: set
`absolute_steal_price` on a search in `config.yaml` (default `null`/off). If
a listing's price is at or below that number, it alerts immediately,
bypassing the history requirement entirely. Useful right after you add a
search, or whenever you already have a strong sense of what a steal costs
for that set.
Diagnosing "no alerts": every run now ends each search with a summary
line like:
```
[Destined Rivals ETB] SUMMARY: fetched=12 already_seen=8 new=4 alerted=0
excluded_by_keyword=1 price_filtered=0 building_history=3 seen_but_not_steal=0
```
Read this before assuming anything's broken — it tells you exactly which of
these is happening:
`fetched=0` → Vinted returned nothing. Either Vinted access failed this
run (check for an `ERROR: Vinted unavailable` line above it — see "Honest
limitations" below) or your `search_text`/`catalog_ids` genuinely matched
nothing on Vinted right now.
`excluded_by_keyword` > 0 → real listings exist but your
`include_keywords`/`exclude_keywords` are rejecting them. Check the
per-item reason lines above the summary to see which listing and why.
`building_history` > 0 → those listings are waiting on 5+ price samples
before the relative "steal" logic can judge them (expected/temporary —
set `absolute_steal_price` to skip this wait).
`alerted=0` with everything else at 0 too → genuinely nothing new to
report this run, working as intended.
Per-listing reasons are also still printed above the summary (Actions tab →
the run → "Run monitor" step) for line-by-line detail.
Runs every 5 minutes, triggered by your external cron-job.org pinger.
See `src/deal_scorer.py` for the exact comparison logic.
Sources: Vinted only — what happened with Bazos.sk and Facebook
Vinted (Slovakia only) is the only automated source. Here's why the
other two aren't, so this doesn't look like an oversight:
Bazos.sk was attempted and removed. The plan was to use their RSS feed
rather than scrape their search pages (which `robots.txt` explicitly
disallows — confirmed directly, not assumed). Once actually tested against
real traffic, though, Bazos's RSS turned out to ignore keyword search
parameters entirely — it always returns the same generic "latest ~50 ads
sitewide" feed regardless of what you search for, and at their volume, a
relevant listing scrolls off that feed within seconds. There's no
keyword-filterable, robots.txt-compliant way to automate Bazos monitoring
right now. What Bazos does offer instead: their own app has a built-in
"search agent" feature — save a search and it notifies you natively
when new matching listings appear. That's the right tool for Bazos
specifically, just not one that can feed into this same Discord bot.
Facebook Marketplace was never built. No public API, most listings
need a logged-in session to view, and automating that means running your
personal account through repeated automated logins against Facebook's own
anti-bot detection — a real risk of getting flagged or restricted, not a
hypothetical one. Facebook's own Marketplace has a native "save search +
alerts" feature that push-notifies you in-app — the safe substitute, just
also not feeding into this Discord bot.
So in practice: this bot covers Vinted automatically, and Bazos + Facebook
are best covered by each platform's own native saved-search alerts
alongside it.
Scope: Slovakia only, sealed product only
Country: Only `vinted.sk` is searched. Cross-border shipping
availability differs by country, so this stays scoped to what you can
actually order from directly.
Product type: each search targets one specific sealed PRODUCT, not just
one set — an ETB, a booster box, and an SPC for the same set are priced
completely differently, so they're separate searches with separate price
history. Currently tracking:
Search	Absolute steal price
Destined Rivals ETB	≤125€
Destined Rivals Booster Box	≤300€
Prismatic Evolutions ETB	≤140€
Prismatic Evolutions SPC (Super Premium Collection)	≤250€
Ascended Heroes ETB	≤140€
Each search uses two keyword lists to stay scoped to its own product only:
`include_keywords`: product-specific terms (e.g. "etb"/"elite trainer box"
for an ETB search, "booster box"/"booster bundle" for a booster box
search) — deliberately NOT generic terms like "sealed", since that would
let one product type leak into another search's price history and skew
its "steal" math.
`exclude_keywords`: filters out opened/loose-card listings, AND the other
product types for that same set (e.g. the ETB search excludes "booster
box" so a booster box listing can't be miscounted as an ETB price).
To add a new product or set as it releases: copy the closest existing block
in `searches`, change `name`, `params.search_text`, and the
`include_keywords`/`exclude_keywords`/`absolute_steal_price` for that
product. No code changes needed.
Why the schedule might not fire on its own (and the fix)
GitHub's native `schedule:` cron trigger is documented as best effort, not
guaranteed — under load, GitHub can delay or silently drop scheduled runs,
and if a repo goes quiet for a stretch, GitHub can pause its scheduled
workflows until someone manually re-enables them. This is a known GitHub
limitation, not something specific to this project.
The reliable fix: have a free external cron service call GitHub's API to
trigger the workflow directly, instead of relying on GitHub's own scheduler.
The workflow already supports this (`repository_dispatch` trigger in
`monitor.yml`) — you just need to point a pinger at it.
Setup (~5 minutes, no code changes):
Create a GitHub token. Go to
github.com/settings/personal-access-tokens → "Generate new token" (the
fine-grained kind). Give it a name, set expiration to something like 1
year, under "Repository access" pick "Only select repositories" and
choose this repo, then under "Permissions" find "Contents" and set it
to "Read and write" (this is the permission the `/dispatches`
endpoint actually checks — "Actions" permission alone will get you a 403
even though it looks like the more obvious choice). Generate, then copy
the token somewhere safe — GitHub only shows it once.
Sign up at cron-job.org (free, no credit
card). Create a new cron job with:
URL: `https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/dispatches`
Request method: POST
Headers: add `Authorization: Bearer YOUR_TOKEN` and
`Accept: application/vnd.github+json`
Body (raw JSON): `{"event_type": "run-monitor"}`
Schedule: every 5 minutes
Save and enable it. cron-job.org will now ping GitHub every 5
minutes, which fires the `repository_dispatch` trigger and runs the
monitor — independent of whether GitHub's own scheduler would have fired.
Note: `monitor.yml` used to also keep GitHub's native `schedule:`
trigger as a "just in case" backup alongside the pinger. That's been
removed — having both firing on roughly the same interval meant two
triggers landing close together, which occasionally caused a real bug: one
run's state-file push would get rejected because the other run (or a manual
edit to `config.yaml` on GitHub) had already updated `main` in between that
run's checkout and its push. The workflow now retries a rejected push by
fetching and rebasing (up to 5 attempts) before giving up gracefully, but
removing the redundant trigger cuts down on how often that race happens in
the first place.
Honest limitations — please read before relying on this
Vinted actively fights scraping (Datadome anti-bot). To be precise
about what's actually going on, since a vague "anti-bot stuff happens"
isn't useful: `vinted_scraper` already does the standard mitigations
correctly — it rotates through a weighted list of real browser
User-Agent strings and retries the cookie fetch internally with backoff
before giving up. A 406 that survives all of that (as happened in
testing) points more at Vinted blocking the IP address itself —
GitHub Actions runners share cloud IP ranges that anti-bot systems
commonly flag — rather than anything fixable by tweaking headers in this
repo's code. The workflow retries 3 more times on top of the library's
own retries and won't crash the whole run over it, but if Vinted is
blocking the IP range outright, no amount of retrying from another
GitHub-hosted IP fixes that.
The one thing that plausibly does fix it: self-hosting `monitor.py`
on a home device (Raspberry Pi, always-on PC, etc.) via a local cron job
instead of GitHub Actions — a residential ISP IP is far less likely to be
blocked than a datacenter IP. Same code, just a different trigger; ask if
you want help setting that up.
Check the per-search SUMMARY line first, not just "0 alerts." See
"Diagnosing 'no alerts'" above — `fetched=0` means a Vinted access
problem, `excluded_by_keyword>0` means a config problem, and
`building_history>0` means it's working but waiting on data. Don't debug
blind; the log tells you which one it is.
This is not financial/market data — the rolling average is only built
from what this bot has seen, not true sold-comp pricing. Treat alerts as
a filter to reduce noise, not gospel.
`catalog_ids: "2971"` in config.yaml is the trading cards catalog on
Vinted — double check it still matches on vinted.sk by browsing the
category there once and copying the `catalog[]=` value from the URL.
Local testing
```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python src/monitor.py
```
