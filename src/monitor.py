"""
monitor.py

Entry point run on a schedule by GitHub Actions.

For each country domain in config.yaml (global.domains), and for each search:
  1. Query that country's Vinted site via the `vinted_scraper` library.
  2. Skip listings we've already alerted on (tracked in data/seen_items.json).
  3. Score each new listing against that search's rules (see deal_scorer.py).
  4. Send a Discord alert for anything that clears its min_score_to_alert.
  5. Update price history + seen-items state and write it back to disk so the
     next run (and the workflow's git commit step) persists it.

State is stored as plain JSON files under data/ — no database needed, which
keeps this entirely within GitHub's free tier (state is just committed back
to the repo by the workflow after each run).

IMPORTANT: state and price history are keyed by "{domain_name}:{search_name}",
never mixed across countries — different domains use different currencies
(EUR/CZK/PLN), so averaging across them would be meaningless and a listing
seen on one country's site is tracked independently from another's.
"""

from __future__ import annotations
import json
import os
import sys
import time
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from deal_scorer import score_item
from discord_notifier import send_deal_alert

from vinted_scraper import VintedScraper

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "data" / "seen_items.json"
HISTORY_PATH = ROOT / "data" / "price_history.json"


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    global_cfg = config["global"]
    searches = config["searches"]
    discord_cfg = config["discord"]
    domains = global_cfg["domains"]

    webhook_url = os.environ.get(discord_cfg["webhook_url_env_var"], "")
    if not webhook_url:
        print(f"ERROR: {discord_cfg['webhook_url_env_var']} is not set. "
              f"Add it as a GitHub Actions secret.", file=sys.stderr)
        sys.exit(1)
    mention_role_id = os.environ.get(discord_cfg.get("mention_role_id_env_var", ""), None)

    # Keyed by "{domain_name}:{search_name}" so countries never mix.
    seen_items: dict = load_json(SEEN_PATH, {})
    price_history: dict = load_json(HISTORY_PATH, {})

    items_per_query = global_cfg.get("items_per_query", 40)
    history_window = global_cfg.get("price_history_window", 50)
    query_delay = global_cfg.get("delay_between_queries_sec", 5)
    domain_delay = global_cfg.get("delay_between_domains_sec", 3)

    total_alerts = 0

    for domain in domains:
        domain_name = domain["name"]
        base_url = domain["base_url"]
        print(f"=== Domain: {domain_name} ({base_url}) ===")

        try:
            scraper = VintedScraper(base_url)
        except Exception as exc:
            print(f"[{domain_name}] failed to init scraper: {exc}", file=sys.stderr)
            continue

        for search in searches:
            search_name = search["name"]
            key = f"{domain_name}:{search_name}"
            rules = search.get("rules", {})
            params = dict(search.get("params", {}))
            params.setdefault("per_page", items_per_query)

            print(f"[{key}] searching...")
            try:
                results = scraper.search(params)
            except Exception as exc:  # network / anti-bot failures shouldn't kill the whole run
                print(f"[{key}] search failed: {exc}", file=sys.stderr)
                time.sleep(query_delay)
                continue

            seen_ids = set(seen_items.get(key, []))
            history = price_history.get(key, [])
            new_seen_ids = list(seen_items.get(key, []))

            for item in results:
                item_id = str(item.id)
                if item_id in seen_ids:
                    continue  # already processed in a previous run

                price = float(item.price or 0)
                title = item.title or ""
                description = item.description or ""

                result = score_item(
                    price=price,
                    title=title,
                    description=description,
                    rules=rules,
                    recent_prices=history,
                )

                print(f"[{key}] {title[:60]!r} - {price} {item.currency} - "
                      f"score {result.score} - deal={result.is_deal}")

                if result.is_deal:
                    image_url = item.photos[0].url if item.photos else None
                    try:
                        send_deal_alert(
                            webhook_url,
                            search_name=f"{search_name} ({domain_name})",
                            title=title,
                            price=price,
                            currency=item.currency or "",
                            url=item.url or f"{base_url}{item.path}",
                            image_url=image_url,
                            score=result.score,
                            reasons=result.reasons,
                            mention_role_id=mention_role_id,
                        )
                        total_alerts += 1
                    except Exception as exc:
                        print(f"[{key}] failed to send Discord alert: {exc}", file=sys.stderr)

                # Track every valid-priced listing in history (deal or not) so the
                # rolling average reflects the real market for this domain, not
                # just past deals.
                if price > 0:
                    history.append(price)
                    history = history[-history_window:]

                new_seen_ids.append(item_id)

            # Cap seen-items list so the JSON file doesn't grow forever
            seen_items[key] = new_seen_ids[-(items_per_query * 20):]
            price_history[key] = history

            time.sleep(query_delay)

        time.sleep(domain_delay)

    save_json(SEEN_PATH, seen_items)
    save_json(HISTORY_PATH, price_history)
    print(f"Done. {total_alerts} alert(s) sent.")


if __name__ == "__main__":
    main()

