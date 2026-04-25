from __future__ import annotations

import random
from collections.abc import Collection, Mapping
from typing import Any

from .catalog import DEFAULT_DOMAIN, SUPPORTED_DOMAINS
from .database import fetch_all, fetch_one


def _normalize_domain(domain: str | None) -> str:
    normalized = (domain or DEFAULT_DOMAIN).strip()
    if normalized not in SUPPORTED_DOMAINS:
        raise ValueError(f"지원하지 않는 domain 입니다: {domain}")
    return normalized


def cleanup_stale_queued_bundles(conn: Any, *, domain: str = DEFAULT_DOMAIN) -> int:
    domain = _normalize_domain(domain)
    rows = fetch_all(
        conn,
        """
        SELECT ab.id
        FROM article_bundle ab
        JOIN topic_variant tv ON tv.id = ab.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE ab.bundle_status = 'queued'
          AND tv.status = 'published'
          AND tc.domain = ?
        """,
        (domain,),
    )
    bundle_ids = [int(row["id"]) for row in rows]
    for bundle_id in bundle_ids:
        conn.execute(
            "UPDATE article_bundle SET bundle_status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (bundle_id,),
        )
    return len(bundle_ids)

RECENT_PUBLISH_GUARD_COUNT = 4


def run_bundle_target_from_candidate(candidate_row: Mapping[str, Any]) -> dict[str, int]:
    """Return the safest run_bundle target for a selected cron candidate.

    Recovery candidates already have an article_bundle row with a valid draft.
    Passing variant_id would create a new text job and skip the intended resume path,
    so bundle_id must win when it is available.
    """
    row = dict(candidate_row or {})
    bundle_id = row.get("bundle_id")
    if bundle_id is not None:
        return {"bundle_id": int(bundle_id)}

    variant_id = row.get("id") or row.get("variant_id")
    if variant_id is None:
        raise ValueError("cron candidate must include id/variant_id or bundle_id")
    return {"variant_id": int(variant_id)}


def is_recent_publish_conflict(
    conn: Any,
    *,
    variant_id: int,
    guard_count: int = RECENT_PUBLISH_GUARD_COUNT,
) -> dict[str, Any] | None:
    query = """
        WITH candidate AS (
            SELECT
                tv.id AS variant_id,
                tv.title AS variant_title,
                tv.slug AS variant_slug,
                tc.id AS cluster_id,
                tc.domain,
                tc.semantic_key,
                tc.primary_keyword
            FROM topic_variant tv
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE tv.id = ?
        ),
        recent_published AS (
            SELECT
                ph.id AS publish_id,
                ph.variant_id,
                ph.published_title,
                ph.published_at,
                tv.title AS variant_title,
                tv.slug AS variant_slug,
                tc.id AS cluster_id,
                tc.domain,
                tc.semantic_key,
                tc.primary_keyword,
                ROW_NUMBER() OVER (ORDER BY ph.id DESC) AS rn
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        )
        SELECT
            rp.publish_id,
            rp.variant_id,
            rp.published_title,
            rp.published_at,
            CASE
                WHEN rp.cluster_id = c.cluster_id OR rp.semantic_key = c.semantic_key THEN 'topic'
                WHEN rp.variant_title = c.variant_title OR rp.variant_slug = c.variant_slug THEN 'title'
                ELSE 'unknown'
            END AS conflict_reason
        FROM candidate c
        JOIN recent_published rp
          ON rp.rn <= ?
         AND rp.domain = c.domain
         AND (
              rp.cluster_id = c.cluster_id
              OR rp.semantic_key = c.semantic_key
              OR rp.variant_title = c.variant_title
              OR rp.variant_slug = c.variant_slug
         )
        ORDER BY rp.publish_id DESC
        LIMIT 1
    """
    row = fetch_one(conn, query, (variant_id, guard_count))
    return dict(row) if row else None

def _published_topic_blocks(conn: Any, *, domain: str) -> dict[str, set[Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT
            ph.variant_id,
            tc.id AS cluster_id,
            tc.semantic_key,
            tv.title AS variant_title,
            tv.slug AS variant_slug
        FROM publish_history ph
        JOIN topic_variant tv ON tv.id = ph.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE ph.channel = 'naver_blog'
          AND tc.domain = ?
        """,
        (domain,),
    )
    blocks: dict[str, set[Any]] = {
        "variant_ids": set(),
        "cluster_ids": set(),
        "semantic_keys": set(),
        "titles": set(),
        "slugs": set(),
    }
    for row in rows:
        if row["variant_id"] is not None:
            blocks["variant_ids"].add(int(row["variant_id"]))
        if row["cluster_id"] is not None:
            blocks["cluster_ids"].add(int(row["cluster_id"]))
        if row["semantic_key"]:
            blocks["semantic_keys"].add(str(row["semantic_key"]))
        if row["variant_title"]:
            blocks["titles"].add(str(row["variant_title"]))
        if row["variant_slug"]:
            blocks["slugs"].add(str(row["variant_slug"]))
    return blocks


def _is_blocked_by_published_topic(row: Mapping[str, Any], blocks: Mapping[str, set[Any]]) -> bool:
    return (
        int(row.get("id") or row.get("variant_id") or 0) in blocks.get("variant_ids", set())
        or int(row.get("cluster_id") or 0) in blocks.get("cluster_ids", set())
        or str(row.get("semantic_key") or "") in blocks.get("semantic_keys", set())
        or str(row.get("title") or "") in blocks.get("titles", set())
        or str(row.get("slug") or "") in blocks.get("slugs", set())
    )


def select_publish_candidate(
    conn: Any,
    *,
    domain: str = DEFAULT_DOMAIN,
    excluded_variant_ids: Collection[int] | None = None,
) -> Any:
    domain = _normalize_domain(domain)
    cleanup_stale_queued_bundles(conn, domain=domain)
    excluded_ids = {int(variant_id) for variant_id in (excluded_variant_ids or [])}
    published_blocks = _published_topic_blocks(conn, domain=domain)

    recovery_query = """
        SELECT
            tv.id,
            tv.title,
            tv.slug,
            tv.status,
            tv.use_count,
            tc.priority,
            tc.id AS cluster_id,
            tc.domain,
            tc.semantic_key,
            tc.primary_keyword,
            ab.id AS bundle_id,
            1 AS recovery_priority
        FROM article_bundle ab
        JOIN topic_variant tv ON tv.id = ab.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE tc.domain = ?
          AND ab.bundle_status = 'queued'
          AND ab.primary_draft_id IS NOT NULL
          AND ab.primary_thumbnail_id IS NULL
        ORDER BY ab.id ASC
    """
    for recovery in fetch_all(conn, recovery_query, (domain,)):
        row = dict(recovery)
        if int(row["id"]) in excluded_ids:
            continue
        if _is_blocked_by_published_topic(row, published_blocks):
            continue
        return row

    query = """
        SELECT
            tv.id,
            tv.title,
            tv.slug,
            tv.status,
            tv.use_count,
            tc.priority,
            tc.id AS cluster_id,
            tc.domain,
            tc.semantic_key,
            tc.primary_keyword
        FROM topic_variant tv
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE tc.domain = ?
          AND tv.status IN ('queued', 'drafted')
    """
    rows = []
    for candidate in fetch_all(conn, query, (domain,)):
        row = dict(candidate)
        if int(row["id"]) in excluded_ids:
            continue
        if _is_blocked_by_published_topic(row, published_blocks):
            continue
        rows.append(row)
    if not rows:
        return None

    weighted_rows: list[dict[str, Any]] = []
    weights: list[float] = []
    for row in rows:
        priority = max(int(row.get('priority') or 0), 0)
        use_count = max(int(row.get('use_count') or 0), 0)
        status = str(row.get('status') or '')
        status_bonus = 1.25 if status == 'queued' else 1.0
        weight = (priority + 1) * status_bonus / (use_count + 1)
        weighted_rows.append(row)
        weights.append(weight)

    return random.choices(weighted_rows, weights=weights, k=1)[0]

def describe_unpublishable_run_result(run_result: Mapping[str, Any]) -> dict[str, Any] | None:
    bundle = dict(run_result.get("bundle") or {})
    errors = list(run_result.get("errors") or [])
    detail = {
        "mode": run_result.get("mode"),
        "bundle_id": bundle.get("id"),
        "variant_id": bundle.get("variant_id"),
        "bundle_status": bundle.get("bundle_status"),
        "primary_draft_id": bundle.get("primary_draft_id"),
    }

    if errors:
        first_error = dict(errors[0] or {}) if errors else {}
        detail.update(
            {
                "reason": "run_bundle_reported_errors",
                "error_count": len(errors),
                "first_error_code": first_error.get("code"),
                "first_error_message": first_error.get("message"),
            }
        )
        return detail

    if not bundle:
        detail["reason"] = "missing_bundle_payload"
        return detail

    if not bundle.get("primary_draft_id"):
        detail["reason"] = "missing_primary_draft_id"
        return detail

    return None
