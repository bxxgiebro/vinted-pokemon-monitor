"""
deal_scorer.py

Decides whether a listing is a genuine STEAL — not just "a bit cheaper than
average", but priced well below anything recently seen, worth grabbing
before someone else does.

Two knobs drive this (set per search in config.yaml):

  1. min_pct_below_average: the listing must be at least this % cheaper than
     the rolling average of recent listings for this search.
  2. must_beat_recent_low: if true (default), the listing must ALSO undercut
     the single cheapest price seen recently — not just the average.

Or set absolute_steal_price to alert instantly on a known-good price,
bypassing the average/history check entirely.

Deliberately NOT filtered here: suspiciously low prices, "fake"/"proxy"
keywords, condition claims. That judgment call is left to you. Use
include_keywords/exclude_keywords/min_price/max_price in config.yaml only
if YOU want that filtering; none of it is applied unless you set it.

Every return includes a `reason_code` — a short machine-readable string —
so monitor.py can build an accurate per-search summary (how many were
excluded by keyword vs. still building history vs. actually alerted)
instead of guessing from prose.
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
    reason_code: str  # one of: above_max_price, below_min_price, excluded_keyword,
                       # missing_include_keyword, absolute_steal, insufficient_history,
                       # not_steal_enough, relative_steal


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
    reasons: list[str] = []
    text = f"{title or ''} {description or ''}"

    if rules.get("max_price") is not None and price > rules["max_price"]:
        return ScoredItem(0, False, ["above max_price"], None, "above_max_price")

    if rules.get("min_price") is not None and price < rules["min_price"]:
        return ScoredItem(0, False, ["below min_price filter"], None, "below_min_price")

    exclude_keywords = rules.get("exclude_keywords") or []
    if _text_has_any(text, exclude_keywords):
        return ScoredItem(0, False, ["matched an exclude_keyword"], None, "excluded_keyword")

    include_keywords = rules.get("include_keywords") or []
    if include_keywords and not _text_matches_keywords(text, include_keywords):
        return ScoredItem(0, False, ["missing required include_keywords"], None, "missing_include_keyword")

    # --- Optional absolute backstop: alerts instantly, bypassing history. ---
    absolute_price = rules.get("absolute_steal_price")
    if absolute_price is not None and price <= absolute_price:
        return ScoredItem(
            100, True,
            [f"at or below your absolute_steal_price of {absolute_price}"],
            None, "absolute_steal",
        )

    # --- Need enough price history to know what a "steal" even means here ---
    min_history = rules.get("min_history_size", 5)
    if len(recent_prices) < min_history:
        return ScoredItem(
            0, False,
            [f"only {len(recent_prices)} prices seen so far, need {min_history} "
             f"before judging relative steals for this search"],
            None, "insufficient_history",
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

    score = max(0, min(100, int(pct_below_avg)))
    reason_code = "relative_steal" if is_deal else "not_steal_enough"

    return ScoredItem(score, is_deal, reasons, rolling_average, reason_code)

