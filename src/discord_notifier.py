"""
discord_notifier.py

Sends a Discord embed via webhook for a matched deal.
"""

from __future__ import annotations
import requests


def send_deal_alert(
    webhook_url: str,
    *,
    search_name: str,
    title: str,
    price: float,
    currency: str,
    url: str,
    image_url: str | None,
    score: int,
    reasons: list[str],
    mention_role_id: str | None = None,
) -> None:
    embed = {
        "title": title[:256] if title else "Vinted listing",
        "color": 0x2ECC71 if score >= 80 else 0xF1C40F,
        "fields": [
            {"name": "Price", "value": f"{price} {currency}", "inline": True},
            {"name": "Deal score", "value": f"{score}/100", "inline": True},
            {"name": "Search", "value": search_name, "inline": True},
            {"name": "Why", "value": "\n".join(f"- {r}" for r in reasons)[:1024]},
        ],
    }
    # Discord's embed API rejects an empty/invalid "url" with a 400 for the
    # WHOLE payload — better to just omit it than let a bad URL silently kill
    # every alert (this exact bug happened once already).
    if url and (url.startswith("http://") or url.startswith("https://")):
        embed["url"] = url
    else:
        # Still surface the link somewhere even if it's not embed-url-valid.
        embed["fields"].append({"name": "Link", "value": url or "(no URL available)"})

    if image_url:
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "content": f"<@&{mention_role_id}>" if mention_role_id else None,
        "embeds": [embed],
    }

    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()

