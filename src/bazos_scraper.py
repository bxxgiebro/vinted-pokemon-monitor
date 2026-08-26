"""
bazos_scraper.py

Fetches listings from Bazos.sk via their RSS feed.

IMPORTANT — why RSS and not the search pages: Bazos.sk's robots.txt
disallows automated access to their search/listing pages. Their RSS feature
(rss.php) is a separate, intentionally-provided mechanism explicitly meant
for "watch these listings without visiting the site" — Bazos documents it
themselves for exactly this use case. This module only ever calls rss.php,
never the disallowed search pages.

VERIFICATION NOTE: Bazos's rss.php is expected to accept the same query
params as their search.php (hledat=keyword, etc.), scoped per category
subdomain (e.g. ostatne.bazos.sk/rss.php, deti.bazos.sk/rss.php). This
wasn't fully verified end-to-end before shipping — check the very first
run's logs to confirm titles actually match your search term. As a safety
net, this module also does its own keyword filter on the RSS results
client-side, so even if the server-side "hledat" filter doesn't work as
expected, you won't get a flood of unrelated sitewide listings — you'll
instead get zero results, which is a much safer failure mode. If you see
zero Bazos results across several runs, that's the first thing to check.
"""

from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import requests

USER_AGENT = "Mozilla/5.0 (compatible; personal-deal-monitor/1.0)"


@dataclass
class BazosItem:
    id: str
    title: str
    description: str
    price: float | None
    currency: str
    url: str
    image_url: str | None


def _parse_price_from_title(raw_title: str) -> tuple[str, float | None]:
    """
    Bazos titles look like "Predám ps4: 100" or "Peugeot Boxer: V texte".
    Splits off the trailing price; returns (clean_title, price_or_None).
    Negotiable/"see description" prices ("V texte", "Dohodou") -> None.
    """
    if ":" not in raw_title:
        return raw_title.strip(), None
    name, _, price_part = raw_title.rpartition(":")
    price_str = price_part.strip().replace("\xa0", "").replace(" ", "")
    price_str = price_str.replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", price_str):
        return name.strip(), float(price_str)
    return name.strip(), None


def _extract_image_url(description_html: str) -> str | None:
    match = re.search(r'<img src="([^"]+)"', description_html or "")
    return match.group(1) if match else None


def _text_matches(text: str, terms: list[str]) -> bool:
    if not terms:
        return True
    text_lower = text.lower()
    return any(term.lower() in text_lower for term in terms)


def fetch_bazos_items(
    categories: list[str],
    search_text: str,
    keyword_filter: list[str] | None = None,
    timeout: int = 15,
) -> list[BazosItem]:
    """
    Fetches RSS results for `search_text` from each Bazos category subdomain
    in `categories` (e.g. ["ostatne", "deti"]).

    `keyword_filter`: client-side safety net — if given, only items whose
    title contains at least one of these terms are kept, regardless of
    whether the server-side search already filtered. Defaults to matching
    on `search_text` itself if not given.
    """
    if keyword_filter is None:
        keyword_filter = [search_text]

    items: list[BazosItem] = []
    headers = {"User-Agent": USER_AGENT}

    for category in categories:
        url = f"https://{category}.bazos.sk/rss.php?hledat={quote(search_text)}"
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            print(f"[bazos:{category}] fetch/parse failed: {exc}")
            continue

        for item_el in root.findall(".//item"):
            raw_title = (item_el.findtext("title") or "").strip()
            link = (item_el.findtext("link") or "").strip()
            description_html = item_el.findtext("description") or ""
            guid = (item_el.findtext("guid") or link).strip()

            if not raw_title or not link:
                continue

            # Client-side safety net (see module docstring).
            if not _text_matches(raw_title + " " + description_html, keyword_filter):
                continue

            title, price = _parse_price_from_title(raw_title)
            if price is None:
                continue  # negotiable/"see description" prices aren't scoreable

            id_match = re.search(r"/inzerat/(\d+)/", guid) or re.search(r"/inzerat/(\d+)/", link)
            item_id = f"bazos-{id_match.group(1)}" if id_match else f"bazos-{guid}"

            # Strip the leading <img> tag from the description for a cleaner text field.
            clean_description = re.sub(r"<img[^>]*>", "", description_html).strip()
            clean_description = re.sub(r"<[^>]+>", "", clean_description)

            items.append(BazosItem(
                id=item_id,
                title=title,
                description=clean_description,
                price=price,
                currency="EUR",
                url=link,
                image_url=_extract_image_url(description_html),
            ))

    return items
