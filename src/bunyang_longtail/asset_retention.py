from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .database import connect

MEDIA_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".webm",
    ".m4v",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
}
VIDEO_RESULT_SUFFIX = ".youtube.json"
VIDEO_COMPLETE_NAVE_CLIP_STATUSES = {"public_saved", "private_saved", "uploaded", "published", "ok"}
VIDEO_COMPLETE_TIKTOK_STATUSES = {"submitted", "published", "ok"}


@dataclass
class CleanupSummary:
    scanned_bundles: int = 0
    eligible_bundles: int = 0
    skipped_bundles: list[dict[str, Any]] = field(default_factory=list)
    deleted_files: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_files)

    @property
    def freed_bytes(self) -> int:
        return sum(int(item.get("bytes") or 0) for item in self.deleted_files)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned_bundles": self.scanned_bundles,
            "eligible_bundles": self.eligible_bundles,
            "deleted_count": self.deleted_count,
            "freed_bytes": self.freed_bytes,
            "dry_run": self.dry_run,
            "deleted_files": self.deleted_files,
            "skipped_bundles": self.skipped_bundles,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_naver_clip_complete(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("status") or "").strip().lower() in VIDEO_COMPLETE_NAVE_CLIP_STATUSES


def _is_tiktok_complete(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("status") or "").strip().lower() in VIDEO_COMPLETE_TIKTOK_STATUSES


def is_video_publish_complete(result: dict[str, Any]) -> bool:
    """영상 산출물이 외부 발행까지 끝났는지 보수적으로 판단합니다."""
    if str(result.get("status") or "").strip().lower() != "ok":
        return False

    youtube_upload = str(result.get("youtube_upload") or "").strip().lower()
    if youtube_upload == "uploaded" and (str(result.get("video_id") or "").strip() or str(result.get("video_url") or "").strip()):
        return True

    if _is_naver_clip_complete(result.get("naver_clip_upload")):
        return True

    if _is_tiktok_complete(result.get("tiktok_upload")):
        return True

    return False


def _safe_path(path_value: Any) -> Path | None:
    raw = str(path_value or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _is_relative_to(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _media_paths_from_payload(payload: Any) -> set[Path]:
    paths: set[Path] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key).lower()
            if key_text == "path" or key_text.endswith("_path") or key_text in {"images", "video_path"}:
                paths.update(_media_paths_from_payload(value))
            elif isinstance(value, (dict, list, tuple)):
                paths.update(_media_paths_from_payload(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            paths.update(_media_paths_from_payload(item))
    else:
        path = _safe_path(payload)
        if path and path.suffix.lower() in MEDIA_SUFFIXES:
            paths.add(path)
    return paths


def _collect_run_media_paths(run_dir: Path, meta: dict[str, Any], video_results: list[dict[str, Any]]) -> set[Path]:
    paths = _media_paths_from_payload({"images": meta.get("images"), "assets": meta.get("assets")})
    for result in video_results:
        paths.update(_media_paths_from_payload(result))
    for child in (run_dir / "images").glob("**/*") if (run_dir / "images").exists() else []:
        if child.is_file() and child.suffix.lower() in MEDIA_SUFFIXES:
            paths.add(child.resolve())
    for child in (run_dir / "video").glob("**/*") if (run_dir / "video").exists() else []:
        if child.is_file() and child.suffix.lower() in MEDIA_SUFFIXES:
            paths.add(child.resolve())
    return paths


def _collect_blog_screenshots(*, blog_output_dir: Path | None, bundle_id: int) -> set[Path]:
    if blog_output_dir is None or not blog_output_dir.exists():
        return set()
    apt_id = f"longtail-bundle-{bundle_id}"
    paths: set[Path] = set()
    for path in blog_output_dir.glob(f"{apt_id}*.png"):
        if path.is_file():
            paths.add(path.resolve())
    return paths


def _bundle_is_published(conn: sqlite3.Connection, bundle_id: int) -> bool:
    row = conn.execute(
        """
        SELECT ab.bundle_status, COUNT(ph.id) AS publish_count
        FROM article_bundle ab
        LEFT JOIN publish_history ph ON ph.bundle_id = ab.id AND ph.naver_url IS NOT NULL AND ph.naver_url != ''
        WHERE ab.id = ?
        GROUP BY ab.id, ab.bundle_status
        """,
        (bundle_id,),
    ).fetchone()
    return bool(row and row["bundle_status"] == "published" and int(row["publish_count"] or 0) > 0)


def _path_is_older_than(path: Path, cutoff_ts: float) -> bool:
    try:
        return path.stat().st_mtime <= cutoff_ts
    except FileNotFoundError:
        return False


def _prune_empty_dirs(run_dir: Path) -> None:
    for child_name in ("images", "video"):
        child = run_dir / child_name
        if not child.exists():
            continue
        for directory in sorted([p for p in child.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            child.rmdir()
        except OSError:
            pass


def cleanup_published_media(
    *,
    db_path: str | Path,
    output_base: str | Path,
    retention_days: int = 3,
    blog_output_dir: str | Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupSummary:
    """블로그+영상 발행이 완료된 번들의 오래된 이미지/영상 파일만 삭제합니다.

    DB 행과 JSON 메타데이터는 보존하고, 파일 경로도 운영 산출물 루트 아래로 제한합니다.
    """
    base = Path(output_base).expanduser().resolve()
    blog_root = Path(blog_output_dir).expanduser().resolve() if blog_output_dir else None
    summary = CleanupSummary(dry_run=dry_run)
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if not base.exists():
        return summary

    current = now or datetime.now(timezone.utc)
    cutoff_ts = (current - timedelta(days=retention_days)).timestamp()

    with connect(db_path) as conn:
        for meta_path in sorted(base.glob("**/publish_bundle.json")):
            summary.scanned_bundles += 1
            run_dir = meta_path.parent.resolve()
            meta = _read_json(meta_path)
            try:
                bundle_id = int(meta.get("bundle_id") or 0)
            except (TypeError, ValueError):
                bundle_id = 0
            if bundle_id <= 0:
                summary.skipped_bundles.append({"meta_path": str(meta_path), "reason": "bundle_id missing"})
                continue
            if not _bundle_is_published(conn, bundle_id):
                summary.skipped_bundles.append({"bundle_id": bundle_id, "reason": "bundle not published"})
                continue

            video_result_paths = sorted((run_dir / "video").glob(f"*{VIDEO_RESULT_SUFFIX}")) if (run_dir / "video").exists() else []
            video_results = [_read_json(path) for path in video_result_paths]
            if not any(is_video_publish_complete(result) for result in video_results):
                summary.skipped_bundles.append({"bundle_id": bundle_id, "reason": "video publish not complete"})
                continue

            allowed_roots = [run_dir]
            if blog_root is not None:
                allowed_roots.append(blog_root)
            candidate_paths = _collect_run_media_paths(run_dir, meta, video_results)
            candidate_paths.update(_collect_blog_screenshots(blog_output_dir=blog_root, bundle_id=bundle_id))

            deleted_for_bundle = 0
            for path in sorted(candidate_paths):
                if not path.exists() or not path.is_file():
                    continue
                if path.suffix.lower() not in MEDIA_SUFFIXES:
                    continue
                if not _is_relative_to(path, allowed_roots):
                    continue
                if not _path_is_older_than(path, cutoff_ts):
                    continue
                size = path.stat().st_size
                if not dry_run:
                    path.unlink()
                summary.deleted_files.append({"bundle_id": bundle_id, "path": str(path), "bytes": size})
                deleted_for_bundle += 1
            if deleted_for_bundle:
                summary.eligible_bundles += 1
                if not dry_run:
                    _prune_empty_dirs(run_dir)
            else:
                summary.skipped_bundles.append({"bundle_id": bundle_id, "reason": "no old media files"})

    return summary


def default_blog_output_dir() -> Path:
    return Path(os.getenv("LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR", "/home/kj/app/bunyang/blog-cheongyak-automation/outputs/publish_longtail"))
