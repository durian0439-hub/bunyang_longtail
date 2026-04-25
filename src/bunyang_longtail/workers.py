from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .catalog import AUCTION_DOMAIN, TAX_DOMAIN
from .config import (
    GPT_WEB_ARTIFACT_DIR,
    OPENAI_COMPAT_ARTIFACT_DIR,
    OPENAI_COMPAT_IMAGE_MODEL,
    OPENAI_COMPAT_TEXT_MODEL,
    SIMULATED_ASSET_DIR,
)
from .database import connect, fetch_all, fetch_one, init_db
from .gpt_web import GptWebExecutionError, execute_image_job, execute_text_job
from .local_image_fallback import render_fallback_thumbnail
from .article_quality import score_article_quality
from .openai_compat import (
    OpenAICompatExecutionError,
    execute_image_job as execute_image_job_openai,
    execute_text_job as execute_text_job_openai,
)
from .codex_cli import CodexCLIExecutionError, execute_text_job as execute_text_job_codex_cli

TEXT_ROUTE_DEFAULT = "codex_cli"
IMAGE_ROUTE_DEFAULT = "gpt_web_playwright"
TEXT_PROFILE_DEFAULT = "gpt_text_profile_dev"
IMAGE_PROFILE_DEFAULT = "gpt_image_profile_dev"
TEXT_MODEL_DEFAULT = "gpt-web-text"
IMAGE_MODEL_DEFAULT = "gpt-web-image"
SIMULATED_PNG_BYTES = bytes.fromhex(
    "89504E470D0A1A0A"
    "0000000D49484452000000010000000108060000001F15C489"
    "0000000D49444154789C6360180500008200814D0D0A2DB4"
    "0000000049454E44AE426082"
)



def _hash(value: str, size: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:size]



def _normalize_title(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", title).lower()



def _content_hash(article_markdown: str) -> str:
    return hashlib.sha256(article_markdown.encode("utf-8")).hexdigest()



def _decode_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default



def _fetch_variant(conn: Any, variant_id: int) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        SELECT v.id, v.title, v.slug, v.prompt_json, v.prompt_version, v.route_policy, v.status,
               c.id AS cluster_id, c.domain, c.semantic_key, c.primary_keyword, c.secondary_keyword,
               c.audience, c.search_intent, c.scenario
        FROM topic_variant v
        JOIN topic_cluster c ON c.id = v.cluster_id
        WHERE v.id = ?
        """,
        (variant_id,),
    )
    if not row:
        raise ValueError(f"variant_id={variant_id} 를 찾지 못했습니다.")
    result = dict(row)
    result["prompt_json"] = _decode_json(result["prompt_json"], {})
    return result



def _fetch_bundle(conn: Any, bundle_id: int) -> dict[str, Any]:
    row = fetch_one(conn, "SELECT * FROM article_bundle WHERE id = ?", (bundle_id,))
    if not row:
        raise ValueError(f"bundle_id={bundle_id} 를 찾지 못했습니다.")
    result = dict(row)
    result["selected_image_ids_json"] = _decode_json(result["selected_image_ids_json"], [])
    return result



def _fetch_job(conn: Any, job_id: int) -> dict[str, Any]:
    row = fetch_one(conn, "SELECT * FROM generation_job WHERE id = ?", (job_id,))
    if not row:
        raise ValueError(f"job_id={job_id} 를 찾지 못했습니다.")
    result = dict(row)
    result["request_payload_json"] = _decode_json(result["request_payload_json"], {})
    result["response_payload_json"] = _decode_json(result["response_payload_json"], {})
    return result



def _jobs_for_bundle(conn: Any, bundle_id: int, *, worker_type: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM generation_job WHERE bundle_id = ?"
    params: list[Any] = [bundle_id]
    if worker_type:
        query += " AND worker_type = ?"
        params.append(worker_type)
    query += " ORDER BY id ASC"
    rows = fetch_all(conn, query, tuple(params))
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["request_payload_json"] = _decode_json(item["request_payload_json"], {})
        item["response_payload_json"] = _decode_json(item["response_payload_json"], {})
        items.append(item)
    return items



def _latest_bundle_job(
    conn: Any,
    *,
    bundle_id: int,
    worker_type: str,
    image_role: str | None = None,
) -> dict[str, Any] | None:
    for row in reversed(_jobs_for_bundle(conn, bundle_id, worker_type=worker_type)):
        if image_role is not None and row["request_payload_json"].get("image_role") != image_role:
            continue
        return row
    return None



def _next_attempt_no(
    conn: Any,
    *,
    bundle_id: int,
    worker_type: str,
    image_role: str | None = None,
) -> int:
    attempt_no = 0
    for row in _jobs_for_bundle(conn, bundle_id, worker_type=worker_type):
        if image_role is not None and row["request_payload_json"].get("image_role") != image_role:
            continue
        attempt_no = max(attempt_no, int(row.get("attempt_no") or 0))
    return attempt_no + 1



def _rendered_image_roles(conn: Any, bundle_id: int) -> set[str]:
    rows = fetch_all(
        conn,
        "SELECT DISTINCT image_role FROM image_asset WHERE bundle_id = ? AND status = 'rendered'",
        (bundle_id,),
    )
    return {str(row["image_role"]) for row in rows if row["image_role"]}



def _latest_active_bundle(conn: Any, variant_id: int) -> dict[str, Any] | None:
    row = fetch_one(
        conn,
        """
        SELECT * FROM article_bundle
        WHERE variant_id = ?
          AND bundle_status IN ('queued', 'drafting_text', 'rendering_image', 'bundled', 'reviewed')
        ORDER BY id DESC
        LIMIT 1
        """,
        (variant_id,),
    )
    if not row:
        return None
    result = dict(row)
    result["selected_image_ids_json"] = _decode_json(result["selected_image_ids_json"], [])
    return result


def _is_stale_running_job(job: dict[str, Any], *, stale_minutes: int = 20) -> bool:
    from datetime import datetime, timedelta

    status = str(job.get("status") or "")
    started_at = job.get("started_at") or job.get("created_at")
    if status != "running" or not started_at:
        return False
    raw_started = str(started_at).strip()
    candidates = [raw_started]
    if ' ' in raw_started and 'T' not in raw_started:
        candidates.append(raw_started.replace(' ', 'T'))
    for candidate in candidates:
        try:
            started = datetime.fromisoformat(candidate.replace('Z', '+00:00'))
            break
        except ValueError:
            started = None
    if started is None:
        return False
    now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
    return now - started >= timedelta(minutes=stale_minutes)


def _cleanup_stale_bundle_jobs(conn: Any, bundle: dict[str, Any]) -> None:
    bundle_id = int(bundle["id"])
    jobs = _jobs_for_bundle(conn, bundle_id)
    stale_jobs = [job for job in jobs if _is_stale_running_job(job)]
    if not stale_jobs:
        return
    for job in stale_jobs:
        conn.execute(
            """
            UPDATE generation_job
            SET status = 'failed',
                error_code = COALESCE(error_code, 'STALE_RUNNING_JOB'),
                error_message = COALESCE(error_message, 'stale running job cleaned up before retry'),
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job["id"],),
        )
    conn.execute(
        "UPDATE article_bundle SET bundle_status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (bundle_id,),
    )



def _ensure_bundle_in_conn(
    conn: Any,
    *,
    variant_id: int,
    generation_strategy: str | None = None,
) -> dict[str, Any]:
    variant = _fetch_variant(conn, variant_id)
    bundle = _latest_active_bundle(conn, variant_id)
    if bundle:
        _cleanup_stale_bundle_jobs(conn, bundle)
        refreshed_bundle = _fetch_bundle(conn, bundle["id"])
        image_jobs = _jobs_for_bundle(conn, bundle["id"], worker_type="image")
        has_running_image = any(str(job.get("status") or "") == "running" for job in image_jobs)
        if refreshed_bundle.get("bundle_status") == "rendering_image" and not has_running_image:
            conn.execute(
                "UPDATE article_bundle SET bundle_status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (bundle["id"],),
            )
            refreshed_bundle = _fetch_bundle(conn, bundle["id"])
        return refreshed_bundle
    strategy = generation_strategy or variant["route_policy"] or "gpt_web_first"
    conn.execute(
        """
        INSERT INTO article_bundle (
            variant_id, bundle_status, selected_image_ids_json, generation_strategy
        ) VALUES (?, 'queued', '[]', ?)
        """,
        (variant_id, strategy),
    )
    bundle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return _fetch_bundle(conn, bundle_id)



def create_bundle(
    db_path: str | Path,
    *,
    variant_id: int,
    generation_strategy: str | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        return _ensure_bundle_in_conn(conn, variant_id=variant_id, generation_strategy=generation_strategy)



def _reserve_variant(conn: Any, variant_id: int | None = None) -> dict[str, Any]:
    if variant_id is not None:
        row = fetch_one(conn, "SELECT id, status FROM topic_variant WHERE id = ?", (variant_id,))
        if not row:
            raise ValueError(f"variant_id={variant_id} 를 찾지 못했습니다.")
        if row["status"] not in {"queued", "reserved", "drafted"}:
            raise ValueError(f"variant_id={variant_id} 상태가 queued/reserved/drafted 가 아닙니다: {row['status']}")
        conn.execute(
            "UPDATE topic_variant SET status = 'reserved', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (variant_id,),
        )
        return _fetch_variant(conn, variant_id)

    row = fetch_one(
        conn,
        """
        SELECT id FROM topic_variant
        WHERE status = 'queued'
        ORDER BY seo_score DESC, id ASC
        LIMIT 1
        """,
    )
    if not row:
        raise ValueError("예약 가능한 queued variant가 없습니다.")
    conn.execute(
        "UPDATE topic_variant SET status = 'reserved', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (row["id"],),
    )
    return _fetch_variant(conn, row["id"])



def queue_text_job(
    db_path: str | Path,
    *,
    variant_id: int | None = None,
    bundle_id: int | None = None,
    route: str = TEXT_ROUTE_DEFAULT,
    profile_name: str = TEXT_PROFILE_DEFAULT,
    model_label: str = TEXT_MODEL_DEFAULT,
) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        if bundle_id is not None:
            bundle = _fetch_bundle(conn, bundle_id)
            variant = _reserve_variant(conn, bundle["variant_id"])
        else:
            variant = _reserve_variant(conn, variant_id)
            bundle = _ensure_bundle_in_conn(conn, variant_id=variant["id"])

        request_payload = {
            "kind": "text_generation",
            "bundle_id": bundle["id"],
            "variant_id": variant["id"],
            "slug": variant["slug"],
            "route": route,
            "profile_name": profile_name,
            "model_label": model_label,
            "prompt": variant["prompt_json"],
        }
        attempt_no = _next_attempt_no(conn, bundle_id=bundle["id"], worker_type="text")
        conn.execute(
            """
            INSERT INTO generation_job (
                bundle_id, variant_id, worker_type, route, profile_name, model_label,
                prompt_version, request_payload_json, status, attempt_no
            ) VALUES (?, ?, 'text', ?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                bundle["id"],
                variant["id"],
                route,
                profile_name,
                model_label,
                variant["prompt_version"],
                json.dumps(request_payload, ensure_ascii=False),
                attempt_no,
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {
            "job_id": job_id,
            "bundle_id": bundle["id"],
            "variant_id": variant["id"],
            "title": variant["title"],
            "route": route,
            "profile_name": profile_name,
            "model_label": model_label,
        }



def queue_image_job(
    db_path: str | Path,
    *,
    variant_id: int | None = None,
    bundle_id: int | None = None,
    image_role: str = "thumbnail",
    route: str = IMAGE_ROUTE_DEFAULT,
    profile_name: str = IMAGE_PROFILE_DEFAULT,
    model_label: str = IMAGE_MODEL_DEFAULT,
) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        if bundle_id is not None:
            bundle = _fetch_bundle(conn, bundle_id)
        elif variant_id is not None:
            bundle = _latest_active_bundle(conn, variant_id)
            if not bundle:
                raise ValueError("활성 article bundle이 없습니다. 먼저 텍스트 번들을 시작해야 합니다.")
        else:
            raise ValueError("bundle_id 또는 variant_id 중 하나는 필요합니다.")

        if not bundle.get("primary_draft_id"):
            raise ValueError("본문 초안이 없는 bundle입니다. 이미지는 글 생성 후에 연결해야 합니다.")

        draft = fetch_one(
            conn,
            "SELECT id, title, excerpt, article_markdown FROM article_draft WHERE id = ?",
            (bundle["primary_draft_id"],),
        )
        if not draft:
            raise ValueError("bundle의 primary_draft를 찾지 못했습니다.")

        prompt_text = (
            f"네이버 블로그용 {image_role} 이미지. 제목: {draft['title']}. "
            f"요약: {(draft['excerpt'] or '')[:160]}"
        )
        request_payload = {
            "kind": "image_generation",
            "bundle_id": bundle["id"],
            "variant_id": bundle["variant_id"],
            "draft_id": draft["id"],
            "image_role": image_role,
            "route": route,
            "profile_name": profile_name,
            "model_label": model_label,
            "prompt_text": prompt_text,
        }
        attempt_no = _next_attempt_no(conn, bundle_id=bundle["id"], worker_type="image", image_role=image_role)
        conn.execute(
            """
            INSERT INTO generation_job (
                bundle_id, variant_id, worker_type, route, profile_name, model_label,
                prompt_version, request_payload_json, status, attempt_no
            ) VALUES (?, ?, 'image', ?, ?, ?, 'v1', ?, 'queued', ?)
            """,
            (
                bundle["id"],
                bundle["variant_id"],
                route,
                profile_name,
                model_label,
                json.dumps(request_payload, ensure_ascii=False),
                attempt_no,
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {
            "job_id": job_id,
            "bundle_id": bundle["id"],
            "variant_id": bundle["variant_id"],
            "image_role": image_role,
            "route": route,
            "profile_name": profile_name,
            "model_label": model_label,
        }



def start_job(db_path: str | Path, job_id: int) -> None:
    with connect(db_path) as conn:
        job = fetch_one(conn, "SELECT id, worker_type, variant_id, bundle_id FROM generation_job WHERE id = ?", (job_id,))
        if not job:
            raise ValueError(f"job_id={job_id} 를 찾지 못했습니다.")
        conn.execute(
            "UPDATE generation_job SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job_id,),
        )
        if job["worker_type"] == "text":
            conn.execute(
                "UPDATE topic_variant SET status = 'drafting', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job["variant_id"],),
            )
            if job["bundle_id"]:
                conn.execute(
                    "UPDATE article_bundle SET bundle_status = 'drafting_text', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job["bundle_id"],),
                )
        elif job["bundle_id"]:
            conn.execute(
                "UPDATE article_bundle SET bundle_status = 'rendering_image', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job["bundle_id"],),
            )



def _rewrite_blog_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        return cleaned
    cleaned = cleaned.replace("입주자모집공고과", "입주자모집공고와")
    cleaned = cleaned.replace("거주의무과", "거주의무와")
    parts = [part.strip() for part in cleaned.split(',') if part.strip()]
    if len(parts) >= 2:
        cleaned = ' '.join(parts[:2])
    cleaned = cleaned.replace(' FAQ 기준', '')
    cleaned = cleaned.replace(' FAQ', '')
    cleaned = cleaned.replace(' 정리 기준', ' 정리')
    cleaned = cleaned.replace(' 기준 정리', ' 정리')
    cleaned = ' '.join(cleaned.split())
    if cleaned.endswith('기준') and len(cleaned) > 18:
        cleaned = cleaned[:-2].rstrip()
    priority_tokens = [' 놓치면 탈락하는 포인트', ' 탈락 포인트', ' 확인포인트', ' 핵심 포인트', ' 체크 포인트', ' 포인트 정리', ' 가능 여부 기준', ' 정리']
    shortened = cleaned
    for token in priority_tokens:
        if token in shortened:
            shortened = shortened.replace(token, '')
            shortened = ' '.join(shortened.split())
    return shortened[:60].rstrip()


def _extract_h1_title(article_markdown: str) -> str | None:
    for line in article_markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith('# '):
            return stripped[2:].strip()
    return None


def complete_text_job(
    db_path: str | Path,
    *,
    job_id: int,
    article_markdown: str,
    title: str | None = None,
    excerpt: str | None = None,
    structured_json: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    quality_score: float | None = None,
    similarity_score: float | None = None,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        job = fetch_one(conn, "SELECT * FROM generation_job WHERE id = ?", (job_id,))
        if not job:
            raise ValueError(f"job_id={job_id} 를 찾지 못했습니다.")
        if job["worker_type"] != "text":
            raise ValueError("text 완료 처리 대상이 아닙니다.")
        variant = _fetch_variant(conn, job["variant_id"])
        bundle = _fetch_bundle(conn, job["bundle_id"])
        h1_title = _extract_h1_title(article_markdown)
        final_title = _rewrite_blog_title(h1_title or title or variant["title"])
        final_excerpt = excerpt or article_markdown.strip().splitlines()[0][:180]
        content_hash = _content_hash(article_markdown)
        normalized_title_hash = _hash(_normalize_title(final_title), size=24)
        computed_quality_score, quality_meta = score_article_quality(article_markdown)
        effective_quality_score = quality_score if quality_score is not None else computed_quality_score
        conn.execute(
            """
            INSERT INTO article_draft (
                bundle_id, variant_id, source_job_id, title, excerpt, article_markdown, structured_json,
                prompt_version, content_hash, normalized_title_hash, similarity_score,
                quality_score, model_route, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'drafted')
            """,
            (
                bundle["id"],
                variant["id"],
                job_id,
                final_title,
                final_excerpt,
                article_markdown,
                json.dumps(structured_json, ensure_ascii=False) if structured_json else None,
                variant["prompt_version"],
                content_hash,
                normalized_title_hash,
                similarity_score,
                effective_quality_score,
                job["route"],
            ),
        )
        draft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO similarity_index (draft_id, semantic_key, content_hash, normalized_title_hash)
            VALUES (?, ?, ?, ?)
            """,
            (draft_id, variant["semantic_key"], content_hash, normalized_title_hash),
        )
        merged_response_payload = dict(response_payload or {})
        merged_response_payload.setdefault("quality_meta", quality_meta)
        merged_response_payload.setdefault("computed_quality_score", computed_quality_score)
        conn.execute(
            "UPDATE generation_job SET status = 'succeeded', response_payload_json = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(merged_response_payload, ensure_ascii=False), job_id),
        )
        conn.execute(
            "UPDATE topic_variant SET status = 'drafted', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (variant["id"],),
        )
        conn.execute(
            """
            UPDATE article_bundle
            SET primary_draft_id = ?, bundle_status = 'rendering_image', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (draft_id, bundle["id"]),
        )
        return {
            "bundle_id": bundle["id"],
            "draft_id": draft_id,
            "variant_id": variant["id"],
            "content_hash": content_hash,
        }



def complete_image_job(
    db_path: str | Path,
    *,
    job_id: int,
    image_role: str,
    prompt_text: str,
    file_path: str,
    mime_type: str = "image/png",
    width: int | None = None,
    height: int | None = None,
    phash: str | None = None,
    response_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        job = fetch_one(conn, "SELECT * FROM generation_job WHERE id = ?", (job_id,))
        if not job:
            raise ValueError(f"job_id={job_id} 를 찾지 못했습니다.")
        if job["worker_type"] != "image":
            raise ValueError("image 완료 처리 대상이 아닙니다.")
        bundle = _fetch_bundle(conn, job["bundle_id"])
        conn.execute(
            """
            INSERT INTO image_asset (
                bundle_id, variant_id, source_job_id, image_role, prompt_text, prompt_hash,
                file_path, mime_type, width, height, phash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rendered')
            """,
            (
                bundle["id"],
                job["variant_id"],
                job_id,
                image_role,
                prompt_text,
                _hash(prompt_text, size=24),
                file_path,
                mime_type,
                width,
                height,
                phash,
            ),
        )
        asset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        image_ids = list(bundle["selected_image_ids_json"])
        image_ids.append(asset_id)
        primary_thumbnail_id = bundle["primary_thumbnail_id"]
        if image_role == "thumbnail" and not primary_thumbnail_id:
            primary_thumbnail_id = asset_id
        bundle_status = "bundled" if bundle["primary_draft_id"] else "rendering_image"
        conn.execute(
            """
            UPDATE article_bundle
            SET primary_thumbnail_id = ?, selected_image_ids_json = ?, bundle_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                primary_thumbnail_id,
                json.dumps(image_ids, ensure_ascii=False),
                bundle_status,
                bundle["id"],
            ),
        )
        conn.execute(
            "UPDATE generation_job SET status = 'succeeded', response_payload_json = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(response_payload, ensure_ascii=False) if response_payload else None, job_id),
        )
        return {"bundle_id": bundle["id"], "asset_id": asset_id, "variant_id": job["variant_id"]}



def _build_simulated_article_markdown(variant: dict[str, Any], image_roles: list[str]) -> str:
    primary_keyword = variant["primary_keyword"]
    secondary_keyword = variant["secondary_keyword"]
    audience = variant["audience"]
    scenario = variant["scenario"]
    search_intent = variant["search_intent"]
    image_text = ", ".join(image_roles)
    if variant.get("domain") == AUCTION_DOMAIN:
        return "\n".join(
            [
                f"# {variant['title']}",
                "",
                f"{audience} 기준으로 결론부터 보면, {primary_keyword}는 {scenario} 상황에서 입찰 전 보류해야 할 조건부터 확인하는 편이 좋습니다.",
                "",
                "## 상단 요약",
                f"- 핵심 결론: {primary_keyword}와 {secondary_keyword}를 같이 보면 권리와 비용 리스크를 더 빨리 걸러낼 수 있습니다.",
                f"- 체크 포인트: {search_intent} 목적이면 서류, 점유, 자금, 출구 순서로 확인해야 합니다.",
                f"- 이미지 세트: {image_text}",
                "",
                "## 왜 이 글을 먼저 봐야 하나",
                f"이 변형안은 {audience}가 가장 자주 막히는 장면인 '{scenario}'를 기준으로 정리했습니다.",
                "",
                "## 핵심 판단",
                f"{primary_keyword}만 보고 들어가기보다 {secondary_keyword}를 함께 봐야 실제 입찰가와 손실 가능성을 나눠 볼 수 있습니다.",
                "",
                "## 체크리스트",
                "- 매각물건명세서와 등기부등본 확인",
                "- 점유자와 임차인 권리 확인",
                "- 잔금대출, 취득세, 수리비 확인",
                "",
                "## FAQ",
                "Q. 지금 바로 입찰해도 되나요?",
                "A. 법원 서류와 현장, 대출 가능 여부를 대조한 뒤 결정해야 합니다.",
            ]
        )
    if variant.get("domain") == TAX_DOMAIN:
        return "\n".join(
            [
                f"# {variant['title']}",
                "",
                f"{audience} 기준으로 결론부터 보면, {primary_keyword}는 {scenario} 상황에서 세금 발생 시점과 계산 기준을 먼저 나눠봐야 합니다.",
                "",
                "## 상단 요약",
                f"- 핵심 결론: {primary_keyword}와 {secondary_keyword}를 같이 보면 세금 계산에서 빠지는 항목을 줄일 수 있습니다.",
                f"- 체크 포인트: {search_intent} 목적이면 주택 수, 명의, 보유기간, 신고기한 순서로 확인해야 합니다.",
                f"- 이미지 세트: {image_text}",
                "",
                "## 왜 이 글을 먼저 봐야 하나",
                f"이 변형안은 {audience}가 가장 자주 막히는 장면인 '{scenario}'를 기준으로 정리했습니다.",
                "",
                "## 핵심 판단",
                f"{primary_keyword}만 보고 끝내기보다 {secondary_keyword}와 공식 확인 경로를 함께 봐야 실제 세금 부담을 더 현실적으로 볼 수 있습니다.",
                "",
                "## 체크리스트",
                "- 주택 수, 명의, 보유기간 확인",
                "- 취득가액, 필요경비, 공시가격 확인",
                "- 홈택스, 위택스, 지자체 안내 확인",
                "",
                "## FAQ",
                "Q. 계산기 결과만 보고 신고해도 되나요?",
                "A. 계산기는 참고용입니다. 실제 적용은 시점과 개인 조건에 따라 달라질 수 있어 공식 안내와 전문가 확인이 필요할 수 있습니다.",
            ]
        )
    return "\n".join(
        [
            f"# {variant['title']}",
            "",
            f"{audience} 기준으로 결론부터 말씀드리면, {primary_keyword}는 {scenario} 상황에서 먼저 확인할 기준이 분명합니다.",
            "",
            "## 상단 요약",
            f"- 핵심 결론: {primary_keyword}와 {secondary_keyword}를 같이 보면 판단이 빨라집니다.",
            f"- 체크 항목: {search_intent} 목적이면 조건, 일정, 리스크를 먼저 나눠 확인합니다.",
            f"- 이미지 세트: {image_text}",
            "",
            "## 왜 이 글을 먼저 봐야 하나",
            f"이 변형안은 {audience}가 가장 자주 막히는 장면인 '{scenario}'를 기준으로 정리했습니다.",
            "",
            "## 핵심 판단",
            f"{primary_keyword} 단독 판단보다 {secondary_keyword}와 같이 봐야 실제 청약 의사결정에 도움이 됩니다.",
            "",
            "## 체크리스트",
            "- 모집공고 기준일 확인",
            "- 자격 유지 여부 확인",
            "- 자금 계획과 중도금 일정 확인",
            "",
            "## FAQ",
            "Q. 지금 바로 신청 판단이 가능합니까?",
            "A. 공고문과 본인 조건을 대조한 뒤 결정하셔야 합니다.",
        ]
    )

def _write_simulated_image(file_path: str | Path) -> str:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SIMULATED_PNG_BYTES)
    return str(path)



def _write_local_fallback_image(
    *,
    bundle_id: int,
    image_role: str,
    title: str,
    excerpt: str,
    article_markdown: str | None = None,
    output_dir: str | Path | None = None,
) -> str:
    base_dir = Path(output_dir) if output_dir else SIMULATED_ASSET_DIR / "local_fallback"
    output_path = base_dir / f"bundle_{bundle_id}_{image_role}_fallback.png"
    return render_fallback_thumbnail(
        title=title,
        excerpt=excerpt,
        output_path=output_path,
        image_role=image_role,
        article_markdown=article_markdown,
    )



def run_bundle(
    db_path: str | Path,
    *,
    variant_id: int | None = None,
    bundle_id: int | None = None,
    image_roles: list[str] | None = None,
    text_route: str = TEXT_ROUTE_DEFAULT,
    text_profile_name: str = TEXT_PROFILE_DEFAULT,
    text_model_label: str = TEXT_MODEL_DEFAULT,
    image_route: str = IMAGE_ROUTE_DEFAULT,
    image_profile_name: str = IMAGE_PROFILE_DEFAULT,
    image_model_label: str = IMAGE_MODEL_DEFAULT,
    markdown_file: str | Path | None = None,
    image_specs: dict[str, str] | None = None,
    simulate: bool = False,
    simulate_output_dir: str | Path | None = None,
    quality_score: float | None = None,
    similarity_score: float | None = None,
    executor_mode: str = "playwright",
    headed: bool = False,
    wait_for_ready_seconds: int = 180,
    response_timeout_seconds: int = 300,
    profile_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    cdp_url: str | None = None,
    image_fallback: str = "none",
) -> dict[str, Any]:
    init_db(db_path)
    if simulate and markdown_file:
        raise ValueError("simulate 와 markdown_file 은 동시에 사용할 수 없습니다.")
    if simulate and executor_mode not in {"playwright", "codex_cli"}:
        raise ValueError("simulate 사용 시 executor_mode 는 playwright 또는 codex_cli 여야 합니다.")

    if image_fallback not in {"none", "local_canvas"}:
        raise ValueError(f"지원하지 않는 image_fallback 입니다: {image_fallback}")

    if executor_mode == "openai_compat":
        if text_route == TEXT_ROUTE_DEFAULT:
            text_route = "openai_compat"
        if image_route == IMAGE_ROUTE_DEFAULT:
            image_route = "openai_compat"
        if text_model_label == TEXT_MODEL_DEFAULT:
            text_model_label = OPENAI_COMPAT_TEXT_MODEL
        if image_model_label == IMAGE_MODEL_DEFAULT:
            image_model_label = OPENAI_COMPAT_IMAGE_MODEL

    image_specs = image_specs or {}
    requested_image_roles = ["thumbnail"] if image_roles is None else list(image_roles)
    roles = list(dict.fromkeys(requested_image_roles + list(image_specs.keys())))

    existing_bundle: dict[str, Any] | None = None
    if bundle_id is not None:
        with connect(db_path) as conn:
            existing_bundle = _fetch_bundle(conn, bundle_id)

    final_bundle_id = existing_bundle["id"] if existing_bundle else None
    text_job: dict[str, Any] | None = None
    text_result: dict[str, Any] | None = None
    image_jobs: list[dict[str, Any]] = []
    image_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped_image_roles: list[str] = []

    def _bundle_payload(mode: str) -> dict[str, Any]:
        if final_bundle_id is None:
            raise ValueError("bundle_id가 정해지지 않았습니다.")
        with connect(db_path) as conn:
            bundle = _fetch_bundle(conn, final_bundle_id)
        return {
            "mode": mode,
            "bundle": bundle,
            "text_job": text_job,
            "text_result": text_result,
            "image_jobs": image_jobs,
            "image_results": image_results,
            "errors": errors,
            "skipped_image_roles": skipped_image_roles,
        }

    reuse_existing_draft = bool(
        existing_bundle
        and existing_bundle.get("primary_draft_id")
        and markdown_file is None
        and not simulate
        and (
            bundle_id is not None
            or text_route in {"reuse_existing", "reuse_or_codex_cli"}
        )
    )

    if reuse_existing_draft:
        final_bundle_id = existing_bundle["id"]
        with connect(db_path) as conn:
            latest_text_job = _latest_bundle_job(conn, bundle_id=final_bundle_id, worker_type="text")
            variant = _fetch_variant(conn, existing_bundle["variant_id"])
        if latest_text_job:
            text_job = {
                "job_id": latest_text_job["id"],
                "bundle_id": latest_text_job["bundle_id"],
                "variant_id": latest_text_job["variant_id"],
                "title": variant["title"],
                "route": latest_text_job["route"],
                "profile_name": latest_text_job["profile_name"],
                "model_label": latest_text_job["model_label"],
            }
        text_result = {
            "bundle_id": existing_bundle["id"],
            "draft_id": existing_bundle["primary_draft_id"],
            "variant_id": existing_bundle["variant_id"],
            "resumed": True,
        }
        if executor_mode == "none":
            return _bundle_payload("already_drafted")
    else:
        text_job = queue_text_job(
            db_path,
            variant_id=variant_id,
            bundle_id=bundle_id,
            route=text_route,
            profile_name=text_profile_name,
            model_label=text_model_label,
        )
        start_job(db_path, text_job["job_id"])
        final_bundle_id = text_job["bundle_id"]

        if not simulate and markdown_file is None and executor_mode == "none":
            return _bundle_payload("text_started")

        if simulate or executor_mode == "mock":
            with connect(db_path) as conn:
                variant = _fetch_variant(conn, text_job["variant_id"])
            article_markdown = _build_simulated_article_markdown(variant, roles)
            if variant.get("domain") == AUCTION_DOMAIN:
                excerpt = f"{variant['audience']} 기준으로 {variant['primary_keyword']} 입찰 전 확인 순서를 빠르게 정리한 초안입니다."
            elif variant.get("domain") == TAX_DOMAIN:
                excerpt = f"{variant['audience']} 기준으로 {variant['primary_keyword']} 세금 확인 순서를 빠르게 정리한 초안입니다."
            else:
                excerpt = f"{variant['audience']} 기준으로 {variant['primary_keyword']} 판단 포인트를 빠르게 정리한 초안입니다."
            text_result = complete_text_job(
                db_path,
                job_id=text_job["job_id"],
                article_markdown=article_markdown,
                excerpt=excerpt,
                quality_score=quality_score,
                similarity_score=similarity_score,
                response_payload={"mode": "simulate" if simulate else "mock"},
            )
        elif markdown_file is not None:
            article_markdown = Path(markdown_file).read_text(encoding="utf-8")
            text_result = complete_text_job(
                db_path,
                job_id=text_job["job_id"],
                article_markdown=article_markdown,
                quality_score=quality_score,
                similarity_score=similarity_score,
                response_payload={"mode": "file"},
            )
        elif executor_mode == "playwright":
            with connect(db_path) as conn:
                text_job_row = _fetch_job(conn, text_job["job_id"])
            try:
                execution = execute_text_job(
                    job_id=text_job["job_id"],
                    profile_name=text_profile_name,
                    prompt_payload=text_job_row["request_payload_json"].get("prompt"),
                    headed=headed,
                    wait_for_ready_seconds=wait_for_ready_seconds,
                    response_timeout_seconds=response_timeout_seconds,
                    profile_root=profile_root,
                    artifact_root=artifact_root or GPT_WEB_ARTIFACT_DIR,
                    cdp_url=cdp_url,
                )
                text_result = complete_text_job(
                    db_path,
                    job_id=text_job["job_id"],
                    article_markdown=execution["article_markdown"],
                    excerpt=execution.get("excerpt"),
                    quality_score=quality_score,
                    similarity_score=similarity_score,
                    response_payload=execution.get("response_payload"),
                )
            except GptWebExecutionError as exc:
                fail_job(db_path, job_id=text_job["job_id"], error_code=exc.code, error_message=str(exc))
                errors.append({"job_id": text_job["job_id"], "code": exc.code, "message": str(exc), "artifact_dir": exc.artifact_dir})
                return _bundle_payload("failed")
        elif executor_mode == "openai_compat":
            with connect(db_path) as conn:
                text_job_row = _fetch_job(conn, text_job["job_id"])
            try:
                execution = execute_text_job_openai(
                    job_id=text_job["job_id"],
                    prompt_payload=text_job_row["request_payload_json"].get("prompt"),
                    model_label=text_job_row["model_label"],
                    timeout_seconds=response_timeout_seconds,
                    artifact_root=artifact_root or OPENAI_COMPAT_ARTIFACT_DIR,
                )
                text_result = complete_text_job(
                    db_path,
                    job_id=text_job["job_id"],
                    article_markdown=execution["article_markdown"],
                    excerpt=execution.get("excerpt"),
                    quality_score=quality_score,
                    similarity_score=similarity_score,
                    response_payload=execution.get("response_payload"),
                )
            except OpenAICompatExecutionError as exc:
                fail_job(db_path, job_id=text_job["job_id"], error_code=exc.code, error_message=str(exc))
                errors.append({"job_id": text_job["job_id"], "code": exc.code, "message": str(exc), "artifact_dir": exc.artifact_dir})
                return _bundle_payload("failed")
        elif executor_mode == "codex_cli":
            with connect(db_path) as conn:
                text_job_row = _fetch_job(conn, text_job["job_id"])
            try:
                execution = execute_text_job_codex_cli(
                    job_id=text_job["job_id"],
                    prompt_payload=text_job_row["request_payload_json"].get("prompt"),
                    timeout_seconds=response_timeout_seconds,
                    artifact_root=artifact_root,
                )
                text_result = complete_text_job(
                    db_path,
                    job_id=text_job["job_id"],
                    article_markdown=execution["article_markdown"],
                    excerpt=execution.get("excerpt"),
                    quality_score=quality_score,
                    similarity_score=similarity_score,
                    response_payload=execution.get("response_payload"),
                )
            except CodexCLIExecutionError as exc:
                fail_job(db_path, job_id=text_job["job_id"], error_code=exc.code, error_message=str(exc))
                errors.append({"job_id": text_job["job_id"], "code": exc.code, "message": str(exc), "artifact_dir": exc.artifact_dir})
                return _bundle_payload("failed")
        else:
            raise ValueError(f"지원하지 않는 executor_mode 입니다: {executor_mode}")

    with connect(db_path) as conn:
        bundle = _fetch_bundle(conn, final_bundle_id)
        rendered_roles = _rendered_image_roles(conn, final_bundle_id)
        draft_row = fetch_one(
            conn,
            "SELECT title, excerpt, article_markdown FROM article_draft WHERE id = ?",
            (bundle["primary_draft_id"],),
        )
    pending_roles = [role for role in roles if role not in rendered_roles]
    skipped_image_roles = [role for role in roles if role in rendered_roles]

    if not pending_roles:
        return _bundle_payload("already_complete")

    for role in pending_roles:
        image_job = queue_image_job(
            db_path,
            bundle_id=final_bundle_id,
            image_role=role,
            route=image_route,
            profile_name=image_profile_name,
            model_label=image_model_label,
        )
        image_jobs.append(image_job)
        start_job(db_path, image_job["job_id"])

        with connect(db_path) as conn:
            job_row = _fetch_job(conn, image_job["job_id"])
        prompt_text = job_row["request_payload_json"].get("prompt_text") or f"{role} 이미지"
        file_path = image_specs.get(role)

        if simulate or executor_mode == "mock":
            if simulate or not file_path:
                output_dir = Path(simulate_output_dir) if simulate_output_dir else SIMULATED_ASSET_DIR
                file_path = _write_simulated_image(output_dir / f"bundle_{final_bundle_id}_{role}.png")
            image_results.append(
                complete_image_job(
                    db_path,
                    job_id=image_job["job_id"],
                    image_role=role,
                    prompt_text=prompt_text,
                    file_path=str(file_path),
                    width=1080,
                    height=1080,
                    response_payload={"mode": "simulate" if simulate else "mock"},
                )
            )
            continue

        if executor_mode == "playwright":
            if not file_path:
                artifact_base = Path(artifact_root) if artifact_root else GPT_WEB_ARTIFACT_DIR
                file_path = artifact_base / f"image_job_{image_job['job_id']}_{role}.png"
            try:
                execution = execute_image_job(
                    job_id=image_job["job_id"],
                    profile_name=image_profile_name,
                    prompt_text=prompt_text,
                    title=draft_row["title"],
                    excerpt=draft_row["excerpt"],
                    image_role=role,
                    output_path=file_path,
                    headed=headed,
                    wait_for_ready_seconds=wait_for_ready_seconds,
                    response_timeout_seconds=response_timeout_seconds,
                    profile_root=profile_root,
                    artifact_root=artifact_root or GPT_WEB_ARTIFACT_DIR,
                    cdp_url=cdp_url,
                )
                image_results.append(
                    complete_image_job(
                        db_path,
                        job_id=image_job["job_id"],
                        image_role=role,
                        prompt_text=prompt_text,
                        file_path=execution["file_path"],
                        width=1080,
                        height=1080,
                        response_payload=execution.get("response_payload"),
                    )
                )
            except GptWebExecutionError as exc:
                if image_fallback == "local_canvas":
                    fallback_path = _write_local_fallback_image(
                        bundle_id=final_bundle_id,
                        image_role=role,
                        title=draft_row["title"],
                        excerpt=draft_row["excerpt"] or draft_row["title"],
                        article_markdown=draft_row["article_markdown"],
                        output_dir=artifact_root,
                    )
                    image_results.append(
                        complete_image_job(
                            db_path,
                            job_id=image_job["job_id"],
                            image_role=role,
                            prompt_text=prompt_text,
                            file_path=fallback_path,
                            width=1080,
                            height=1080,
                            response_payload={
                                "mode": "local_canvas_fallback",
                                "source_executor": "playwright",
                                "source_error_code": exc.code,
                                "source_error_message": str(exc),
                                "source_artifact_dir": exc.artifact_dir,
                            },
                        )
                    )
                else:
                    fail_job(db_path, job_id=image_job["job_id"], error_code=exc.code, error_message=str(exc))
                    errors.append({"job_id": image_job["job_id"], "code": exc.code, "message": str(exc), "artifact_dir": exc.artifact_dir})
                    return _bundle_payload("partial_failed")
            continue

        if executor_mode == "openai_compat":
            if not file_path:
                artifact_base = Path(artifact_root) if artifact_root else OPENAI_COMPAT_ARTIFACT_DIR
                file_path = artifact_base / f"image_job_{image_job['job_id']}_{role}.png"
            try:
                execution = execute_image_job_openai(
                    job_id=image_job["job_id"],
                    prompt_text=prompt_text,
                    title=draft_row["title"],
                    excerpt=draft_row["excerpt"],
                    image_role=role,
                    output_path=file_path,
                    model_label=image_job["model_label"],
                    timeout_seconds=response_timeout_seconds,
                    artifact_root=artifact_root or OPENAI_COMPAT_ARTIFACT_DIR,
                )
                image_results.append(
                    complete_image_job(
                        db_path,
                        job_id=image_job["job_id"],
                        image_role=role,
                        prompt_text=prompt_text,
                        file_path=execution["file_path"],
                        width=1024,
                        height=1024,
                        response_payload=execution.get("response_payload"),
                    )
                )
            except OpenAICompatExecutionError as exc:
                fail_job(db_path, job_id=image_job["job_id"], error_code=exc.code, error_message=str(exc))
                errors.append({"job_id": image_job["job_id"], "code": exc.code, "message": str(exc), "artifact_dir": exc.artifact_dir})
                return _bundle_payload("partial_failed")
            continue

        if not file_path:
            continue
        image_results.append(
            complete_image_job(
                db_path,
                job_id=image_job["job_id"],
                image_role=role,
                prompt_text=prompt_text,
                file_path=str(file_path),
                width=1080,
                height=1080,
                response_payload={"mode": "file"},
            )
        )

    if simulate:
        return _bundle_payload("simulated_complete")
    if executor_mode == "mock":
        return _bundle_payload("mock_complete")
    if executor_mode == "playwright":
        return _bundle_payload("playwright_complete")
    if executor_mode == "openai_compat":
        return _bundle_payload("openai_compat_complete")
    if image_results and len(image_results) == len(pending_roles):
        return _bundle_payload("file_complete")
    return _bundle_payload("text_complete_image_started")



def fail_job(db_path: str | Path, *, job_id: int, error_code: str, error_message: str) -> None:
    with connect(db_path) as conn:
        job = fetch_one(conn, "SELECT id, worker_type, variant_id, bundle_id FROM generation_job WHERE id = ?", (job_id,))
        if not job:
            raise ValueError(f"job_id={job_id} 를 찾지 못했습니다.")
        conn.execute(
            """
            UPDATE generation_job
            SET status = 'failed', error_code = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error_code, error_message, job_id),
        )
        if job["worker_type"] == "text":
            conn.execute(
                "UPDATE topic_variant SET status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job["variant_id"],),
            )
            if job["bundle_id"]:
                conn.execute(
                    "UPDATE article_bundle SET bundle_status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job["bundle_id"],),
                )
        elif job["bundle_id"]:
            conn.execute(
                "UPDATE article_bundle SET bundle_status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job["bundle_id"],),
            )



def job_stats(db_path: str | Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        by_status = [
            dict(row)
            for row in fetch_all(
                conn,
                "SELECT status, COUNT(*) AS cnt FROM generation_job GROUP BY status ORDER BY status",
            )
        ]
        by_worker = [
            dict(row)
            for row in fetch_all(
                conn,
                "SELECT worker_type, COUNT(*) AS cnt FROM generation_job GROUP BY worker_type ORDER BY worker_type",
            )
        ]
        by_bundle_status = [
            dict(row)
            for row in fetch_all(
                conn,
                "SELECT bundle_status, COUNT(*) AS cnt FROM article_bundle GROUP BY bundle_status ORDER BY bundle_status",
            )
        ]
    return {"by_status": by_status, "by_worker": by_worker, "by_bundle_status": by_bundle_status}
