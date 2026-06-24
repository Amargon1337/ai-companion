"""Lightweight Russian-friendly text similarity via character n-grams."""
from __future__ import annotations

import re


def get_ngrams(text: str, n: int = 3) -> set[str]:
    clean = re.sub(r"[^\w\s]", "", text.lower())
    ngrams: set[str] = set()
    for word in clean.split():
        if len(word) < n:
            ngrams.add(word)
        else:
            for i in range(len(word) - n + 1):
                ngrams.add(word[i : i + n])
    return ngrams


def get_mixed_ngrams(text: str) -> set[str]:
    """2- и 3-граммы — лучше ловят русские окончания."""
    return get_ngrams(text, 2) | get_ngrams(text, 3)


def text_overlap(a: str, b: str) -> float:
    na, nb = get_mixed_ngrams(a), get_mixed_ngrams(b)
    if not na or not nb:
        return 0.0
    inter = len(na & nb)
    # max-норма (как в рекомендации) + Dice для близких перефразировок
    score_max = inter / max(len(na), len(nb))
    score_dice = (2 * inter) / (len(na) + len(nb))
    return max(score_max, score_dice)
