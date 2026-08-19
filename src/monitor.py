"""
monitor.py

Entry point run on a schedule by GitHub Actions.

For each search in config.yaml:
  1. Query Vinted's catalog search via the `vinted_scraper` library.
  2. Skip listings we've already alerted on (tracked in data/seen_items.json).
  3. Score each new listing against that search's rules (see deal_scorer.py).
  4. Send a Discord alert for anything that clears its min_score_to_alert.
  5. Update price history + seen-items state and write it back to disk so the
     next run (and the workflow's git commit step) persists it.

State is stored as plain JSON files under data/ — no database needed, which
keeps this entirely within GitHub's free tier (state is just committed back
to the repo by the workflow after each run).
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

    webhook_url = os.environ.get(discord_cfg["webhook_url_env_var"], "")
    if not webhook_url:
        print(f"ERROR: {discord_cfg['webhook_url_env_var']} is not set. "
              f"Add it as a GitHub Actions secret.", file=sys.stderr)
        sys.exit(1)
    mention_role_id = os.environ.get(discord_cfg.get("mention_role_id_env_var", ""), None)

    # seen_items: {search_name: [item_id, ...]}  (capped list, oldest dropped)
    seen_items: dict = load_json(SEEN_PATH, {})
    # price_history: {search_name: [price, ...]} rolling window
    price_history: dict = load_json(HISTORY_PATH, {})

    scraper = VintedScraper(global_cfg["base_url"])
    items_per_query = global_cfg.get("items_per_query", 40)
    history_window = global_cfg.get("price_history_window", 50)
    delay = global_cfg.get("delay_between_queries_sec", 5)

    total_alerts = 0

    for search in searches:
        name = search["name"]
        rules = search.get("rules", {})
        params = dict(search.get("params", {}))
        params.setdefault("per_page", items_per_query)

        print(f"[{name}] searching Vinted...")
        try:
            results = scraper.search(params)
        except Exception as exc:  # network / anti-bot failures shouldn't kill the whole run
            print(f"[{name}] search failed: {exc}", file=sys.stderr)
            time.sleep(delay)
            continue

        seen_ids = set(seen_items.get(name, []))
        history = price_history.get(name, [])
        new_seen_ids = list(seen_items.get(name, []))

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

            print(f"[{name}] {title[:60]!r} - {price} - score {result.score} "
                  f"- deal={result.is_deal}")

            if result.is_deal:
                image_url = item.photos[0].url if item.photos else None
                try:
                    send_deal_alert(
                        webhook_url,
                        search_name=name,
                        title=title,
                        price=price,
                        currency=item.currency or "EUR",
                        url=item.url or f"{global_cfg['base_url']}{item.path}",
                        image_url=image_url,
                        score=result.score,
                        reasons=result.reasons,
                        mention_role_id=mention_role_id,
                    )
                    total_alerts += 1
                except Exception as exc:
                    print(f"[{name}] failed to send Discord alert: {exc}", file=sys.stderr)

            # Track every valid-priced listing in history (deal or not) so the
            # rolling average reflects the real market, not just past deals.
            if price > 0:
                history.append(price)
                history = history[-history_window:]

            new_seen_ids.append(item_id)

        # Cap seen-items list so the JSON file doesn't grow forever
        seen_items[name] = new_seen_ids[-(items_per_query * 20):]
        price_history[name] = history

        time.sleep(delay)

    save_json(SEEN_PATH, seen_items)
    save_json(HISTORY_PATH, price_history)
    print(f"Done. {total_alerts} alert(s) sent.")


if __name__ == "__main__":
    main()
