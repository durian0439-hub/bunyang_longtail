from __future__ import annotations

import re
from collections import Counter

REPEATED_ENDINGS = [
    "확인해야 합니다",
    "보셔야 합니다",
    "확인하시기 바랍니다",
    "중요합니다",
]


def _clean_lines(markdown: str) -> list[str]:
    return [line.strip() for line in str(markdown or "").splitlines() if line.strip()]


def score_article_quality(markdown: str) -> tuple[float, dict[str, float | int | list[str]]]:
    lines = _clean_lines(markdown)
    text = "\n".join(lines)
    penalties = 0.0
    repeated_hits: list[str] = []

    for ending in REPEATED_ENDINGS:
        count = text.count(ending)
        if count >= 3:
            penalties += (count - 2) * 0.8
            repeated_hits.append(f"ending:{ending}:{count}")

    paragraphs = [line for line in lines if not line.startswith("#") and not line.startswith("- ") and not line.startswith("Q")]
    long_paragraphs = [line for line in paragraphs if len(line) >= 180]
    if long_paragraphs:
        penalties += len(long_paragraphs) * 0.4

    phrases = [
        "맞벌이",
        "일반공급",
        "특별공급",
        "청약 1순위",
        "확인",
    ]
    phrase_counts = Counter()
    for phrase in phrases:
        phrase_counts[phrase] = text.count(phrase)
    overused = [f"phrase:{k}:{v}" for k, v in phrase_counts.items() if v >= 8]
    penalties += max(0, len(overused) - 1) * 0.6
    repeated_hits.extend(overused)

    score = max(0.0, 10.0 - penalties)
    return score, {
        "line_count": len(lines),
        "paragraph_count": len(paragraphs),
        "long_paragraphs": len(long_paragraphs),
        "repeated_hits": repeated_hits,
        "penalty": round(penalties, 2),
    }
