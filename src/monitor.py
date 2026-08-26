"""
monitor.py

Entry point run on a schedule by GitHub Actions.

For each search in config.yaml:
  1. Query Vinted (Slovakia) via the `vinted_scraper` library.
  2. Query Bazos.sk via their RSS feed (see bazos_scraper.py for why RSS,
     not their disallowed search pages).
  3. Merge both sources' results — both are EUR/Slovakia, so price history
     is shared across them per search, giving one combined market picture
     rather than two separate ones.
  4. Skip listings we've already alerted on (tracked in data/seen_items.json).
  5. Score each new listing against that search's rules (see deal_scorer.py).
  6. Send a Discord alert for anything that clears its min_score_to_alert.
  7. Update price history + seen-items state and write it back to disk so the
     next run (and the workflow's git commit step) persists it.

State is stored as plain JSON files under data/ — no database needed, which
keeps this entirely within GitHub's free tier (state is just committed back
to the repo by the workflow after each run). State is keyed by search name
only now (single country, shared currency across both sources).
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
from bazos_scraper import fetch_bazos_items

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


def fetch_vinted_items(scraper, params: dict, items_per_query: int):
    """Returns a list of normalized dicts: id, title, description, price, currency, url, image_url, source."""
    query = dict(params)
    query.setdefault("per_page", items_per_query)
    results = scraper.search(query)
    normalized = []
    for item in results:
        normalized.append({
            "id": f"vinted-{item.id}",
            "title": item.title or "",
            "description": item.description or "",
            "price": float(item.price or 0),
            "currency": item.currency or "EUR",
            "url": item.url or "",
            "image_url": item.photos[0].url if item.photos else None,
            "source": "Vinted",
        })
    return normalized


def main() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    global_cfg = config["global"]
    searches = config["searches"]
    discord_cfg = config["discord"]
    vinted_domain = global_cfg["vinted_base_url"]

    webhook_url = os.environ.get(discord_cfg["webhook_url_env_var"], "")
    if not webhook_url:
        print(f"ERROR: {discord_cfg['webhook_url_env_var']} is not set. "
              f"Add it as a GitHub Actions secret.", file=sys.stderr)
        sys.exit(1)
    mention_role_id = os.environ.get(discord_cfg.get("mention_role_id_env_var", ""), None)

    seen_items: dict = load_json(SEEN_PATH, {})
    price_history: dict = load_json(HISTORY_PATH, {})

    items_per_query = global_cfg.get("items_per_query", 40)
    history_window = global_cfg.get("price_history_window", 50)
    query_delay = global_cfg.get("delay_between_queries_sec", 5)

    try:
        vinted_scraper = VintedScraper(vinted_domain)
    except Exception as exc:
        print(f"Failed to init Vinted scraper: {exc}", file=sys.stderr)
        vinted_scraper = None

    total_alerts = 0

    for search in searches:
        search_name = search["name"]
        rules = search.get("rules", {})
        vinted_params = dict(search.get("params", {}))
        bazos_cfg = search.get("bazos", {})

        all_items = []

        if vinted_scraper is not None:
            print(f"[{search_name}] searching Vinted...")
            try:
                all_items.extend(fetch_vinted_items(vinted_scraper, vinted_params, items_per_query))
            except Exception as exc:
                print(f"[{search_name}] Vinted search failed: {exc}", file=sys.stderr)
            time.sleep(query_delay)

        if bazos_cfg.get("enabled", False):
            print(f"[{search_name}] searching Bazos.sk...")
            try:
                bazos_results = fetch_bazos_items(
                    categories=bazos_cfg.get("categories", ["ostatne", "deti"]),
                    search_text=bazos_cfg.get("search_text", vinted_params.get("search_text", search_name)),
                    keyword_filter=bazos_cfg.get("keyword_filter"),
                )
                for b in bazos_results:
                    all_items.append({
                        "id": b.id, "title": b.title, "description": b.description,
                        "price": b.price, "currency": b.currency, "url": b.url,
                        "image_url": b.image_url, "source": "Bazos.sk",
                    })
            except Exception as exc:
                print(f"[{search_name}] Bazos search failed: {exc}", file=sys.stderr)
            time.sleep(query_delay)

        seen_ids = set(seen_items.get(search_name, []))
        history = price_history.get(search_name, [])
        new_seen_ids = list(seen_items.get(search_name, []))

        for item in all_items:
            item_id = item["id"]
            if item_id in seen_ids:
                continue

            price = item["price"]
            title = item["title"]
            description = item["description"]

            result = score_item(
                price=price,
                title=title,
                description=description,
                rules=rules,
                recent_prices=history,
            )

            print(f"[{search_name}] ({item['source']}) {title[:60]!r} - "
                  f"{price} {item['currency']} - score {result.score} - deal={result.is_deal}")
            for reason in result.reasons:
                print(f"    -> {reason}")

            if result.is_deal:
                try:
                    send_deal_alert(
                        webhook_url,
                        search_name=f"{search_name} ({item['source']})",
                        title=title,
                        price=price,
                        currency=item["currency"],
                        url=item["url"],
                        image_url=item["image_url"],
                        score=result.score,
                        reasons=result.reasons,
                        mention_role_id=mention_role_id,
                    )
                    total_alerts += 1
                except Exception as exc:
                    print(f"[{search_name}] failed to send Discord alert: {exc}", file=sys.stderr)

            if price > 0:
                history.append(price)
                history = history[-history_window:]

            new_seen_ids.append(item_id)

        seen_items[search_name] = new_seen_ids[-(items_per_query * 20):]
        price_history[search_name] = history

    save_json(SEEN_PATH, seen_items)
    save_json(HISTORY_PATH, price_history)
    print(f"Done. {total_alerts} alert(s) sent.")


if __name__ == "__main__":
    main()

