from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


CHEONGYAK_AUTOMATION_ROOT = Path(
    os.getenv("CHEONGYAK_AUTOMATION_ROOT", "/home/kj/app/bunyang/blog-cheongyak-automation")
).resolve()

if str(CHEONGYAK_AUTOMATION_ROOT) not in sys.path:
    sys.path.insert(0, str(CHEONGYAK_AUTOMATION_ROOT))

try:
    from src.seo.naver_keyword_boost import empty_keyword_boost_pack, keyword_texts, resolve_keyword_boost_pack
except Exception:  # pragma: no cover - common module can be absent in isolated test envs.
    empty_keyword_boost_pack = None
    keyword_texts = None
    resolve_keyword_boost_pack = None


def resolve_atoz_keyword_pack(
    *,
    cluster: dict[str, Any] | None = None,
    variant: dict[str, Any] | None = None,
    title: str = "",
    article_markdown: str = "",
    domain: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    seeds = _seed_keywords(cluster=cluster, variant=variant, title=title, article_markdown=article_markdown, domain=domain)
    if resolve_keyword_boost_pack is None:
        return _empty_pack(warnings=["keyword_boost_common_module_missing"])
    return resolve_keyword_boost_pack(
        seed_keywords=seeds,
        content_type="atoz",
        env_prefix="ATOZ",
        enabled_default=False,
        limit=limit,
    )


def keyword_tag_texts(pack: dict[str, Any] | None, *, limit: int = 10) -> list[str]:
    if keyword_texts is None:
        return []
    return keyword_texts(pack, "tag_keywords", limit=limit)


def keyword_prompt_lines(pack: dict[str, Any] | None, *, limit: int = 8) -> list[str]:
    if not _has_keywords(pack) or keyword_texts is None:
        return []
    primary = str((pack or {}).get("primary_keyword") or "").strip()
    heading_terms = keyword_texts(pack, "heading_keywords", limit=limit)
    faq_terms = keyword_texts(pack, "faq_keywords", limit=4)
    lines: list[str] = []
    if primary:
        lines.append(f"대표 검색어: {primary}")
    if heading_terms:
        lines.append("소제목 후보: " + ", ".join(heading_terms))
    if faq_terms:
        lines.append("FAQ 후보: " + ", ".join(faq_terms))
    return lines


def keyword_engagement_prompt(pack: dict[str, Any] | None) -> str:
    if not isinstance(pack, dict):
        return ""
    return " ".join(str(pack.get("engagement_prompt") or "").split()).strip()


def prepend_keyword_tags(base_tags: list[str], pack: dict[str, Any] | None, *, limit: int) -> list[str]:
    tags = [*keyword_tag_texts(pack, limit=10), *base_tags]
    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = " ".join(str(tag or "").split()).replace(" ", "")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered[:limit]


def has_keyword_boost(pack: dict[str, Any] | None) -> bool:
    return _has_keywords(pack)


def _seed_keywords(
    *,
    cluster: dict[str, Any] | None,
    variant: dict[str, Any] | None,
    title: str,
    article_markdown: str,
    domain: str,
) -> list[str]:
    cluster = cluster or {}
    variant = variant or {}
    raw_items = [
        title,
        variant.get("title"),
        cluster.get("primary_keyword"),
        cluster.get("secondary_keyword"),
        cluster.get("comparison_keyword"),
        cluster.get("semantic_key"),
        cluster.get("family"),
        cluster.get("search_intent"),
        domain,
    ]
    for heading in _markdown_headings(article_markdown, limit=4):
        raw_items.append(heading)
    return _dedupe([str(item or "") for item in raw_items])


def _markdown_headings(markdown: str, *, limit: int) -> list[str]:
    headings: list[str] = []
    for line in str(markdown or "").splitlines():
        text = line.strip()
        if not text.startswith("#"):
            continue
        heading = text.lstrip("#").strip()
        if heading:
            headings.append(heading)
        if len(headings) >= limit:
            break
    return headings


def _has_keywords(pack: dict[str, Any] | None) -> bool:
    return isinstance(pack, dict) and bool(pack.get("primary_keyword") or pack.get("targets"))


def _empty_pack(*, warnings: list[str] | None = None) -> dict[str, Any]:
    if empty_keyword_boost_pack is not None:
        return empty_keyword_boost_pack(content_type="atoz", warnings=warnings)
    return {
        "schema_version": "naver-keyword-boost.v1",
        "content_type": "atoz",
        "source": "disabled",
        "primary_keyword": "",
        "targets": [],
        "tag_keywords": [],
        "engagement_prompt": "",
        "warnings": list(warnings or []),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        key = text.replace(" ", "").casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
