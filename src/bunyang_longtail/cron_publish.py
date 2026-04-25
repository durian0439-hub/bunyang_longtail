from __future__ import annotations

import random
from collections.abc import Collection, Mapping
from typing import Any

from .database import fetch_all, fetch_one


def cleanup_stale_queued_bundles(conn: Any) -> int:
    rows = fetch_all(
        conn,
        """
        SELECT ab.id
        FROM article_bundle ab
        JOIN topic_variant tv ON tv.id = ab.variant_id
        WHERE ab.bundle_status = 'queued'
          AND tv.status = 'published'
        """,
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
            SELECT tv.id AS variant_id, tc.id AS cluster_id, tc.primary_keyword
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
                tc.id AS cluster_id,
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
                WHEN rp.cluster_id = c.cluster_id THEN 'cluster'
                WHEN rp.primary_keyword = c.primary_keyword THEN 'primary_keyword'
                ELSE 'unknown'
            END AS conflict_reason
        FROM candidate c
        JOIN recent_published rp
          ON rp.rn <= ?
         AND (
              rp.cluster_id = c.cluster_id
              OR rp.primary_keyword = c.primary_keyword
         )
        ORDER BY rp.publish_id DESC
        LIMIT 1
    """
    row = fetch_one(conn, query, (variant_id, guard_count))
    return dict(row) if row else None


def select_publish_candidate(conn: Any, *, excluded_variant_ids: Collection[int] | None = None) -> Any:
    cleanup_stale_queued_bundles(conn)
    excluded_ids = sorted({int(variant_id) for variant_id in (excluded_variant_ids or [])})
    exclude_sql = ""
    params: list[Any] = []
    if excluded_ids:
        placeholders = ", ".join("?" for _ in excluded_ids)
        exclude_sql = f" AND tv.id NOT IN ({placeholders})"
        params.extend(excluded_ids)

    recovery_query = f"""
        WITH recent_published AS (
            SELECT
                ph.variant_id,
                tc.id AS cluster_id,
                tc.primary_keyword,
                ROW_NUMBER() OVER (ORDER BY ph.id DESC) AS rn
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        ),
        published_clusters AS (
            SELECT DISTINCT tc.id AS cluster_id
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        )
        SELECT
            tv.id,
            tv.title,
            tv.status,
            tv.use_count,
            tc.priority,
            tc.id AS cluster_id,
            tc.primary_keyword,
            ab.id AS bundle_id,
            1 AS recovery_priority
        FROM article_bundle ab
        JOIN topic_variant tv ON tv.id = ab.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE ab.bundle_status = 'queued'
          AND ab.primary_draft_id IS NOT NULL
          AND ab.primary_thumbnail_id IS NULL
          {exclude_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM published_clusters pc
              WHERE pc.cluster_id = tc.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM recent_published rp
              WHERE rp.rn <= ?
                AND (
                    rp.cluster_id = tc.id
                    OR rp.primary_keyword = tc.primary_keyword
                )
          )
        ORDER BY ab.id ASC
        LIMIT 1
    """
    recovery = fetch_one(conn, recovery_query, tuple([*params, RECENT_PUBLISH_GUARD_COUNT]))
    if recovery:
        return dict(recovery)

    query = f"""
        WITH recent_published AS (
            SELECT
                ph.variant_id,
                tc.id AS cluster_id,
                tc.primary_keyword,
                ROW_NUMBER() OVER (ORDER BY ph.id DESC) AS rn
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        ),
        published_clusters AS (
            SELECT DISTINCT tc.id AS cluster_id
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        )
        SELECT
            tv.id,
            tv.title,
            tv.status,
            tv.use_count,
            tc.priority,
            tc.id AS cluster_id,
            tc.primary_keyword
        FROM topic_variant tv
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE tv.status IN ('queued', 'drafted')
          {exclude_sql}
          AND NOT EXISTS (
              SELECT 1 FROM publish_history ph WHERE ph.variant_id = tv.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM published_clusters pc
              WHERE pc.cluster_id = tc.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM recent_published rp
              WHERE rp.rn <= ?
                AND (
                    rp.cluster_id = tc.id
                    OR rp.primary_keyword = tc.primary_keyword
                )
          )
    """
    rows = [dict(row) for row in fetch_all(conn, query, tuple([*params, RECENT_PUBLISH_GUARD_COUNT]))]
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
