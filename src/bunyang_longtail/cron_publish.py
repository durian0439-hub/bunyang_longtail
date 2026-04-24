from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from .database import fetch_one

RECENT_PUBLISH_GUARD_COUNT = 4


def select_publish_candidate(conn: Any, *, excluded_variant_ids: Collection[int] | None = None) -> Any:
    excluded_ids = sorted({int(variant_id) for variant_id in (excluded_variant_ids or [])})
    exclude_sql = ""
    params: list[Any] = []
    if excluded_ids:
        placeholders = ", ".join("?" for _ in excluded_ids)
        exclude_sql = f" AND tv.id NOT IN ({placeholders})"
        params.extend(excluded_ids)

    query = f"""
        WITH recent_published AS (
            SELECT
                ph.variant_id,
                tc.id AS cluster_id,
                tc.family,
                tc.primary_keyword,
                tv.angle,
                ROW_NUMBER() OVER (ORDER BY ph.id DESC) AS rn
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        )
        SELECT tv.id, tv.title
        FROM topic_variant tv
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE tv.status = 'queued'
          {exclude_sql}
          AND NOT EXISTS (
              SELECT 1 FROM publish_history ph WHERE ph.variant_id = tv.id
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
        ORDER BY tc.priority DESC, tv.created_at ASC, tv.id ASC
        LIMIT 1
    """
    return fetch_one(conn, query, tuple([*params, RECENT_PUBLISH_GUARD_COUNT]))


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
