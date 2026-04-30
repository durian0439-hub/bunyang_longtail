#!/usr/bin/env python3
"""기존 네이버 블로그 발행글에 리드 수집 CTA 블록을 안전하게 재삽입한다.

기본값은 dry-run 이며, 실제 네이버 수정은 --apply 를 명시해야 실행된다.
운영 산출물(body.md/publish_bundle.json)은 읽기만 하고, 수정본/결과는 dev data 아래에 별도 저장한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.naver_bundle_publish import (  # noqa: E402
    BOOK_NOTICE_TEXT,
    CTA_DOMAIN_LABELS,
    DEFAULT_LEAD_CTA_FORM_URL,
    markdown_to_html,
)

DEFAULT_DB = "/home/kj/app/bunyang_longtail/prod/runtime/current/data/cdp_probe5.sqlite3"
DEFAULT_ARTIFACT_ROOT = "/home/kj/app/bunyang_longtail/prod/runtime/current/data/naver_publish"
DEFAULT_OUT = ROOT / "data" / "cta_backfill"
BLOG_ROOT = Path(os.getenv("LONGTAIL_BLOG_AUTOMATION_ROOT", "/home/kj/app/bunyang/blog-cheongyak-automation"))
CTA_HEADING = "## 내 상황 기준 체크리스트 받아보기"


@dataclass(frozen=True)
class PublishRow:
    history_id: int
    bundle_id: int
    variant_id: int
    draft_id: int
    title: str
    naver_url: str
    domain: str
    created_at: str


def _clean(value: object) -> str:
    return str(value or "").strip()


def _blog_update_url(view_url: str) -> str:
    try:
        parsed = urlparse(view_url)
        query = parse_qs(parsed.query)
        blog_id = _clean((query.get("blogId") or [""])[0])
        log_no = _clean((query.get("logNo") or [""])[0])
        category_no = _clean((query.get("categoryNo") or ["0"])[0]) or "0"
        if blog_id and log_no:
            return "https://blog.naver.com/PostUpdateForm.naver?" + urlencode(
                {"blogId": blog_id, "logNo": log_no, "categoryNo": category_no}
            )
    except Exception:
        return ""
    return ""


def _load_rows(db_path: Path, *, limit: int, bundle_ids: set[int], domain: str) -> list[PublishRow]:
    where = ["ph.channel = 'naver_blog'", "COALESCE(ph.naver_url, '') <> ''"]
    params: list[object] = []
    if bundle_ids:
        placeholders = ",".join("?" for _ in bundle_ids)
        where.append(f"ph.bundle_id IN ({placeholders})")
        params.extend(sorted(bundle_ids))
    if domain:
        where.append("COALESCE(tc.domain, 'cheongyak') = ?")
        params.append(domain)
    params.append(limit)
    sql = f"""
        SELECT
            ph.id AS history_id,
            ph.bundle_id,
            ph.variant_id,
            ph.draft_id,
            ph.published_title,
            ph.naver_url,
            COALESCE(tc.domain, 'cheongyak') AS domain,
            ph.created_at
        FROM publish_history ph
        LEFT JOIN topic_variant tv ON tv.id = ph.variant_id
        LEFT JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE {' AND '.join(where)}
        ORDER BY ph.id DESC
        LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [
        PublishRow(
            history_id=int(row["history_id"]),
            bundle_id=int(row["bundle_id"]),
            variant_id=int(row["variant_id"]),
            draft_id=int(row["draft_id"]),
            title=_clean(row["published_title"]),
            naver_url=_clean(row["naver_url"]),
            domain=_clean(row["domain"]) or "cheongyak",
            created_at=_clean(row["created_at"]),
        )
        for row in rows
    ]


def _artifact_candidates(root: Path, bundle_id: int) -> list[Path]:
    patterns = [
        f"cron_runs/*_bundle_{bundle_id}",
        f"cron_runs/*bundle_{bundle_id}*",
        f"bundle_{bundle_id}_*",
        f"*bundle_{bundle_id}*",
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_dir() or path in seen:
                continue
            if not (path / "body.md").exists():
                continue
            seen.add(path)
            out.append(path)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _cta_block(domain: str, form_url: str) -> list[str]:
    label = CTA_DOMAIN_LABELS.get(domain, "부동산 상황")
    return [
        CTA_HEADING,
        "",
        f"{label}이 조금 복잡하게 느껴진다면 글만 보고 바로 결정하지 마시고, 현재 상황을 짧게 남겨주세요.",
        "청약·경매·대출·세금 중 어디에서 막혔는지 기준으로 먼저 확인해야 할 체크리스트를 정리해드립니다.",
        "",
        "부동산 상황 사전점검 신청",
        form_url,
        "",
        "※ 본 신청은 일반 정보 확인 및 체크리스트 제공 목적입니다. 세무·법률·대출 가능 여부는 실제 서류와 전문가 확인이 필요합니다.",
    ]


def _insert_cta(markdown: str, *, domain: str, form_url: str) -> tuple[str, bool, int]:
    body = str(markdown or "").strip()
    if not body:
        return markdown, False, -1
    if CTA_HEADING in body or form_url in body:
        return body + "\n", False, -1

    lines = body.splitlines()
    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == BOOK_NOTICE_TEXT:
            insert_at = idx
            j = idx - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0 and lines[j].strip() == "---":
                insert_at = j
            break

    block = _cta_block(domain, form_url)
    new_lines = lines[:insert_at]
    while new_lines and not new_lines[-1].strip():
        new_lines.pop()
    new_lines.extend(["", *block, ""])
    tail = lines[insert_at:]
    while tail and not tail[0].strip():
        tail.pop(0)
    new_lines.extend(tail)
    return "\n".join(new_lines).strip() + "\n", True, insert_at


def _parse_bundle_ids(values: list[str]) -> set[int]:
    result: set[int] = set()
    for value in values:
        for part in str(value or "").replace(",", " ").split():
            if part.strip():
                result.add(int(part))
    return result


def _has_publish_process() -> bool:
    try:
        import subprocess

        proc = subprocess.run(
            ["ps", "-eo", "cmd="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    needles = ["publish_bundle_to_naver.py", "naver_blog_publish.mjs", "run_longtail_publish_prod.sh"]
    inspector_markers = ["grep", " rg ", "ripgrep", "ps -eo", "sleep "]
    for line in (proc.stdout or "").splitlines():
        if "backfill_lead_cta_to_naver.py" in line:
            continue
        if any(marker in line for marker in inspector_markers):
            continue
        if any(needle in line for needle in needles):
            return True
    return False


def process_row(row: PublishRow, *, artifact_root: Path, out_dir: Path, form_url: str, apply: bool) -> dict:
    update_url = _blog_update_url(row.naver_url)
    candidates = _artifact_candidates(artifact_root, row.bundle_id)
    result: dict[str, object] = {
        "history_id": row.history_id,
        "bundle_id": row.bundle_id,
        "variant_id": row.variant_id,
        "draft_id": row.draft_id,
        "title": row.title,
        "domain": row.domain,
        "naver_url": row.naver_url,
        "update_url": update_url,
        "created_at": row.created_at,
    }
    if not update_url:
        result.update({"status": "skipped", "reason": "update_url_missing"})
        return result
    if not candidates:
        result.update({"status": "skipped", "reason": "artifact_dir_missing"})
        return result

    artifact_dir = candidates[0]
    body_path = artifact_dir / "body.md"
    meta = _load_json(artifact_dir / "publish_bundle.json")
    original = body_path.read_text(encoding="utf-8")
    updated, changed, insert_at = _insert_cta(original, domain=row.domain, form_url=form_url)
    preview_md = out_dir / f"history_{row.history_id}_bundle_{row.bundle_id}.md"
    preview_html = out_dir / f"history_{row.history_id}_bundle_{row.bundle_id}.html"
    preview_md.write_text(updated, encoding="utf-8")
    preview_html.write_text(markdown_to_html(updated), encoding="utf-8")

    images = [str(Path(p)) for p in meta.get("images") or []]
    if not images:
        images = [str(Path(item.get("path"))) for item in meta.get("assets") or [] if item.get("path")]
    tags = [str(tag) for tag in meta.get("tags") or []]
    clip_urls = [str(url).strip() for url in meta.get("blog_clip_urls") or [] if str(url).strip()] if "[[VIDEO:" in updated else []

    result.update(
        {
            "artifact_dir": str(artifact_dir),
            "preview_md": str(preview_md),
            "preview_html": str(preview_html),
            "changed": changed,
            "insert_at_line": insert_at,
            "image_count": len(images),
            "tag_count": len(tags),
            "clip_url_count": len(clip_urls),
        }
    )
    if not changed:
        result.update({"status": "skipped", "reason": "cta_already_present"})
        return result
    if not apply:
        result.update({"status": "dry_run"})
        return result

    if str(BLOG_ROOT) not in sys.path:
        sys.path.insert(0, str(BLOG_ROOT))
    from src.publisher.naver_playwright import publish as naver_publish  # noqa: WPS433,E402

    ok = naver_publish(
        f"longtail-cta-{row.history_id}",
        row.title,
        markdown_to_html(updated),
        images,
        mode="publish",
        out=str(out_dir / "naver_results"),
        body_markdown=updated,
        tags=tags,
        write_url=update_url,
        clip_urls=clip_urls,
    )
    publish_result = _load_json(out_dir / "naver_results" / f"longtail-cta-{row.history_id}.json")
    result.update(
        {
            "status": "updated" if ok else "error",
            "publish_result_path": str(out_dir / "naver_results" / f"longtail-cta-{row.history_id}.json"),
            "publish_result": publish_result,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="기존 롱테일 네이버 발행글에 Google Form CTA를 백필")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--form-url", default=DEFAULT_LEAD_CTA_FORM_URL)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--bundle-id", action="append", default=[])
    parser.add_argument("--domain", default="", choices=["", "cheongyak", "auction", "tax", "loan"])
    parser.add_argument("--apply", action="store_true", help="실제 네이버 글 수정 실행")
    parser.add_argument("--allow-concurrent", action="store_true", help="기존 발행 프로세스가 떠 있어도 실행")
    args = parser.parse_args()

    db_path = Path(args.db)
    artifact_root = Path(args.artifact_root)
    out_dir = Path(args.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.apply and not args.allow_concurrent and _has_publish_process():
        summary = {
            "status": "blocked",
            "reason": "publish_process_running",
            "message": "네이버 브라우저 프로필 충돌 방지를 위해 현재 발행 프로세스 종료 후 다시 실행하세요.",
            "out_dir": str(out_dir),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    rows = _load_rows(
        db_path,
        limit=max(1, int(args.limit)),
        bundle_ids=_parse_bundle_ids(args.bundle_id),
        domain=args.domain,
    )
    results = [
        process_row(row, artifact_root=artifact_root, out_dir=out_dir, form_url=args.form_url, apply=args.apply)
        for row in rows
    ]
    summary = {
        "status": "ok",
        "apply": bool(args.apply),
        "db": str(db_path),
        "artifact_root": str(artifact_root),
        "out_dir": str(out_dir),
        "count": len(results),
        "updated": sum(1 for item in results if item.get("status") == "updated"),
        "dry_run": sum(1 for item in results if item.get("status") == "dry_run"),
        "skipped": sum(1 for item in results if item.get("status") == "skipped"),
        "errors": sum(1 for item in results if item.get("status") == "error"),
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
