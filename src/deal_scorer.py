"""
deal_scorer.py

Decides whether a listing is a genuine STEAL — not just "a bit cheaper than
average", but priced well below anything recently seen, worth grabbing
before someone else does.

Two knobs drive this (set per search in config.yaml):

  1. min_pct_below_average: the listing must be at least this % cheaper than
     the rolling average of recent listings for this search+country.
  2. must_beat_recent_low: if true (default), the listing must ALSO undercut
     the single cheapest price seen recently — not just the average. This is
     what filters out "cheaper than average but still not special" listings.

Deliberately NOT filtered here: suspiciously low prices, "fake"/"proxy"
keywords, condition claims. That judgment call is left to you — the bot's
job is speed and price, not authentication. Use include_keywords /
exclude_keywords / min_price in config.yaml only if YOU want that filtering;
none of it is applied by default.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ScoredItem:
    score: int
    is_deal: bool
    reasons: list[str]
    rolling_average: Optional[float]


def _text_matches_keywords(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _text_has_any(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def score_item(
    price: float,
    title: str,
    description: str,
    rules: dict,
    recent_prices: list[float],
) -> ScoredItem:
    """
    Returns a ScoredItem. is_deal=True only for genuine below-the-floor steals,
    once enough price history exists to judge that.
    """
    reasons: list[str] = []
    text = f"{title or ''} {description or ''}"

    # --- Optional hard filters (off unless you set them in config.yaml) ---
    if rules.get("max_price") is not None and price > rules["max_price"]:
        return ScoredItem(0, False, ["above max_price"], None)

    if rules.get("min_price") is not None and price < rules["min_price"]:
        return ScoredItem(0, False, ["below min_price filter (you disabled this by default)"], None)

    exclude_keywords = rules.get("exclude_keywords") or []
    if _text_has_any(text, exclude_keywords):
        return ScoredItem(0, False, ["matched an exclude_keyword"], None)

    include_keywords = rules.get("include_keywords") or []
    if include_keywords and not _text_matches_keywords(text, include_keywords):
        return ScoredItem(0, False, ["missing required include_keywords"], None)

    # --- Need enough price history to know what a "steal" even means here ---
    min_history = rules.get("min_history_size", 5)
    if len(recent_prices) < min_history:
        return ScoredItem(
            0, False,
            [f"only {len(recent_prices)} prices seen so far, need {min_history} "
             f"before judging steals for this search"],
            None,
        )

    rolling_average = sum(recent_prices) / len(recent_prices)
    recent_low = min(recent_prices)

    pct_below_avg = (rolling_average - price) / rolling_average * 100 if rolling_average > 0 else 0
    threshold_pct = rules.get("min_pct_below_average", 30)
    must_beat_low = rules.get("must_beat_recent_low", True)

    clears_avg_threshold = pct_below_avg >= threshold_pct
    beats_recent_low = price <= recent_low

    if not clears_avg_threshold:
        reasons.append(
            f"only {pct_below_avg:.1f}% below recent average of {rolling_average:.2f} "
            f"(needs {threshold_pct}%)"
        )
    if must_beat_low and not beats_recent_low:
        reasons.append(
            f"cheapest recently seen was {recent_low:.2f}, this is {price:.2f} "
            f"(doesn't undercut it)"
        )

    is_deal = clears_avg_threshold and (beats_recent_low or not must_beat_low)

    if is_deal:
        reasons = [
            f"{pct_below_avg:.1f}% below recent average of {rolling_average:.2f}",
            f"new low: cheapest recently seen was {recent_low:.2f}" if beats_recent_low
            else "below average threshold, recent-low check disabled",
        ]

    # Score is informational only now (shown in the Discord embed), not a gate.
    score = max(0, min(100, int(pct_below_avg)))

    return ScoredItem(score, is_deal, reasons, rolling_average)

