"""
deal_scorer.py

Decides whether a listing counts as a "good deal" and produces a 0-100 score.

Design goal: the Pokemon card market shifts month to month, so instead of
hard-coding "a good Charizard PSA 10 is under $150", the score blends:

  1. Hard rules from config.yaml (price ceiling/floor, keywords) — you control these.
  2. A *self-learning* rolling average: the bot remembers recent prices it has
     seen for each search and rewards listings priced well below that recent
     average. This means the bar for "good deal" adjusts automatically as the
     market rises or falls, without you having to edit numbers constantly.

You only ever need to touch config.yaml. This file is the logic and shouldn't
need monthly edits.
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
    Returns a ScoredItem with a 0-100 score and whether it clears the
    configured min_score_to_alert threshold.
    """
    reasons: list[str] = []
    text = f"{title or ''} {description or ''}"

    # --- Hard disqualifiers first (score = 0, no alert) ---
    if rules.get("max_price") is not None and price > rules["max_price"]:
        return ScoredItem(0, False, ["above max_price"], None)

    if rules.get("min_price") is not None and price < rules["min_price"]:
        return ScoredItem(0, False, ["below min_price (likely mispriced/scam)"], None)

    exclude_keywords = rules.get("exclude_keywords") or []
    if _text_has_any(text, exclude_keywords):
        return ScoredItem(0, False, ["matched an exclude_keyword"], None)

    include_keywords = rules.get("include_keywords") or []
    if not _text_matches_keywords(text, include_keywords):
        return ScoredItem(0, False, ["missing required include_keywords"], None)

    # --- Scoring starts at a baseline once it passes hard filters ---
    score = 40
    reasons.append("passed price range + keyword filters (+40)")

    rolling_average = None
    if recent_prices:
        rolling_average = sum(recent_prices) / len(recent_prices)
        threshold_pct = rules.get("below_rolling_average_pct", 0)
        if rolling_average > 0:
            pct_below = (rolling_average - price) / rolling_average * 100
            if pct_below >= threshold_pct:
                # Scale bonus points by how far below average it is, capped at +50
                bonus = min(50, int(pct_below * 1.5))
                score += bonus
                reasons.append(
                    f"{pct_below:.1f}% below recent average of {rolling_average:.2f} (+{bonus})"
                )
            else:
                reasons.append(
                    f"only {pct_below:.1f}% below recent average "
                    f"(needs {threshold_pct}%)"
                )
    else:
        # No history yet for this search — give a small neutral bonus so the
        # first few runs aren't stuck with zero alerts while history builds up.
        score += 10
        reasons.append("no price history yet, neutral bonus (+10)")

    score = max(0, min(100, score))
    min_score = rules.get("min_score_to_alert", 60)
    is_deal = score >= min_score

    return ScoredItem(score, is_deal, reasons, rolling_average)
