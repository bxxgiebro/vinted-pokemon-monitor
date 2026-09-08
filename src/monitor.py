"""
monitor.py

Entry point run on a schedule by GitHub Actions.

For each search in config.yaml:
  1. Query Vinted (Slovakia) via the `vinted_scraper` library.
  2. Skip listings we've already alerted on (tracked in data/seen_items.json).
  3. Score each new listing against that search's rules (see deal_scorer.py).
  4. Send a Discord alert for anything that clears its min_score_to_alert.
  5. Update price history + seen-items state and write it back to disk.

Every search prints a one-line SUMMARY at the end (fetched / excluded by
keyword / still building history / alerted counts) — check that line first
when diagnosing "no alerts": it tells you whether Vinted returned nothing
(an access problem, see README) vs. returned results that got filtered out
(a config problem) vs. results that are still waiting on price history
(expected, temporary).

NOTE: this only covers Vinted. Bazos.sk integration was attempted and
removed — see README "Sources" section for why.
"""

from __future__ import annotations
import json
import os
import sys
import time
import yaml
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from deal_scorer import score_item
from discord_notifier import send_deal_alert

from vinted_scraper import VintedScraper

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "data" / "seen_items.json"
HISTORY_PATH = ROOT / "data" / "price_history.json"

# Status codes commonly returned by anti-bot systems (Datadome, Cloudflare, etc)
# when the request itself is being blocked, as opposed to a real error.
ANTI_BOT_STATUS_HINTS = ("401", "403", "406", "429")


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


def init_vinted_scraper(vinted_domain: str):
    last_exc = None
    for attempt in range(1, 4):
        try:
            return VintedScraper(vinted_domain)
        except Exception as exc:
            last_exc = exc
            print(f"WARNING: Vinted scraper init failed (attempt {attempt}/3): {exc}", file=sys.stderr)
            if attempt < 3:
                time.sleep(5 * attempt)

    msg = str(last_exc)
    looks_like_anti_bot = any(code in msg for code in ANTI_BOT_STATUS_HINTS)
    print("ERROR: Vinted unavailable after 3 attempts. This run will have "
          "ZERO Vinted results — any 'no alerts' this run is expected, not a "
          "config problem.", file=sys.stderr)
    if looks_like_anti_bot:
        print("This looks like an anti-bot block (status code in the error "
              "matches a known anti-bot pattern), most likely Vinted blocking "
              "GitHub Actions' shared IP ranges rather than anything wrong "
              "with your search terms or rules. See README 'Honest "
              "limitations' — this isn't fixable from this repo's code alone.",
              file=sys.stderr)
    return None


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

    vinted_scraper = init_vinted_scraper(vinted_domain)

    total_alerts = 0
    total_fetched = 0

    for search in searches:
        search_name = search["name"]
        rules = search.get("rules", {})
        vinted_params = dict(search.get("params", {}))

        all_items = []

        if vinted_scraper is not None:
            try:
                all_items = fetch_vinted_items(vinted_scraper, vinted_params, items_per_query)
            except Exception as exc:
                print(f"[{search_name}] Vinted search failed: {exc}", file=sys.stderr)
            time.sleep(query_delay)

        total_fetched += len(all_items)

        seen_ids = set(seen_items.get(search_name, []))
        history = price_history.get(search_name, [])
        new_seen_ids = list(seen_items.get(search_name, []))

        already_seen_count = 0
        code_counts: Counter = Counter()

        for item in all_items:
            item_id = item["id"]
            if item_id in seen_ids:
                already_seen_count += 1
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
            code_counts[result.reason_code] += 1

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

        # --- Per-search summary: the key diagnostic line ---
        alerted = code_counts.get("absolute_steal", 0) + code_counts.get("relative_steal", 0)
        excluded = code_counts.get("excluded_keyword", 0) + code_counts.get("missing_include_keyword", 0)
        price_filtered = code_counts.get("above_max_price", 0) + code_counts.get("below_min_price", 0)
        building_history = code_counts.get("insufficient_history", 0)
        not_a_steal = code_counts.get("not_steal_enough", 0)
        vinted_note = "" if vinted_scraper is not None else " [Vinted unavailable this run]"
        print(f"[{search_name}] SUMMARY: fetched={len(all_items)} "
              f"already_seen={already_seen_count} new={len(all_items) - already_seen_count} "
              f"alerted={alerted} excluded_by_keyword={excluded} "
              f"price_filtered={price_filtered} building_history={building_history} "
              f"seen_but_not_steal={not_a_steal}{vinted_note}")

    save_json(SEEN_PATH, seen_items)
    save_json(HISTORY_PATH, price_history)
    print(f"Done. {total_fetched} total items fetched across all searches. "
          f"{total_alerts} alert(s) sent.")
    if total_fetched == 0 and vinted_scraper is not None:
        print("WARNING: Vinted scraper initialized OK but every search returned "
              "0 items. Check search_text/catalog_ids in config.yaml — this "
              "usually means the search terms aren't matching anything, not "
              "an access problem.", file=sys.stderr)


if __name__ == "__main__":
    main()

