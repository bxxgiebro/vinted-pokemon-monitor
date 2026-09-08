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
    mention_everyone: bool = False,
) -> None:
    embed = {
        "title": title[:256] if title else "Vinted listing",
        "url": url,
        "color": 0x2ECC71 if score >= 80 else 0xF1C40F,
        "fields": [
            {"name": "Price", "value": f"{price} {currency}", "inline": True},
            {"name": "Deal score", "value": f"{score}/100", "inline": True},
            {"name": "Search", "value": search_name, "inline": True},
            {"name": "Why", "value": "\n".join(f"- {r}" for r in reasons)[:1024]},
        ],
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}

    mentions = []
    if mention_everyone:
        mentions.append("@everyone")
    if mention_role_id:
        mentions.append(f"<@&{mention_role_id}>")
    content = " ".join(mentions) if mentions else None

    payload = {
        "content": content,
        "embeds": [embed],
        # Discord webhooks silently swallow @everyone/@here unless you
        # explicitly allow them here — content alone isn't enough.
        "allowed_mentions": {
            "parse": (["everyone"] if mention_everyone else []) + (["roles"] if mention_role_id else []),
        },
    }

    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()

