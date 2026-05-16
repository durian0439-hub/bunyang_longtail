from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .curriculum import CURRICULUM_TRACK_KEY, mark_curriculum_hub_synced, refresh_curriculum_hub_post, set_curriculum_hub_url
from .naver_bundle_publish import default_tags, markdown_to_html, render_curriculum_hub_thumbnail_image


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _blog_update_url(naver_url: str) -> str:
    parsed = urlparse(str(naver_url or ""))
    query = parse_qs(parsed.query)
    blog_id = _clean((query.get("blogId") or [""])[0])
    log_no = _clean((query.get("logNo") or [""])[0])
    category_no = _clean((query.get("categoryNo") or [""])[0])
    if not log_no:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] not in {"PostView.naver", "PostWriteForm.naver"}:
            blog_id = blog_id or parts[0]
            log_no = parts[1]
    if not blog_id or not log_no:
        return ""
    url = f"https://blog.naver.com/PostWriteForm.naver?blogId={blog_id}&Redirect=Update&logNo={log_no}"
    if category_no:
        url += f"&categoryNo={category_no}"
    return url


def _hub_tags(title: str) -> list[str]:
    tags = [
        "부동산공부",
        "청약공부",
        "분양청약",
        "청약AtoZ",
        "부동산AtoZ",
        "내집마련",
        "청약가이드",
        "경매공부",
        "부동산대출",
        "부동산세금",
    ]
    for tag in default_tags(title, domain="cheongyak"):
        if tag not in tags:
            tags.append(tag)
    return tags[:30]


def _insert_lead_image_marker(markdown: str, image_index: int = 1) -> str:
    marker = f"[[IMAGE:{image_index}]]"
    lines = str(markdown or "").splitlines()
    if marker in {line.strip() for line in lines}:
        return str(markdown or "")
    if lines and lines[0].startswith("# "):
        lines[1:1] = ["", marker, ""]
        return "\n".join(lines).strip() + "\n"
    return f"{marker}\n\n{str(markdown or '').strip()}\n"


def publish_curriculum_hub_to_naver(
    *,
    db_path: str | Path,
    output_root: str | Path,
    track_key: str = CURRICULUM_TRACK_KEY,
    mode: str = "private",
    category_no: str | None = None,
    category_name: str | None = None,
    force_new: bool = False,
) -> dict[str, Any]:
    hub = refresh_curriculum_hub_post(db_path, track_key=track_key)
    title = _clean(hub.get("title")) or "부동산 공부 A-Z 전체 목차: 청약·분양·경매·대출·세금"
    markdown = str(hub.get("body_markdown") or "")
    if not markdown.strip():
        raise RuntimeError("목차 허브 본문이 비어 있습니다.")

    apt_id = f"curriculum-hub-{hub['id']}"
    output_dir = Path(output_root).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = render_curriculum_hub_thumbnail_image(
        title=title,
        output_path=output_dir / "images" / "00_curriculum_hub_thumbnail.png",
        linked_count=int(hub.get("linked_node_count") or 0),
        total_count=int(hub.get("total_node_count") or 0),
    )
    publish_markdown = _insert_lead_image_marker(markdown, 1)
    body_html = markdown_to_html(publish_markdown)
    (output_dir / "body.md").write_text(publish_markdown, encoding="utf-8")
    (output_dir / "body.html").write_text(body_html, encoding="utf-8")
    meta = {
        "hub_id": hub["id"],
        "track_key": track_key,
        "title": title,
        "naver_url": hub.get("naver_url"),
        "linked_node_count": hub.get("linked_node_count"),
        "total_node_count": hub.get("total_node_count"),
        "needs_sync": hub.get("needs_sync"),
        "mode": mode,
        "category_no": category_no,
        "category_name": category_name,
        "images": [str(Path(thumbnail_path).resolve())],
        "note": "네이버 공지/고정글로 쓰는 A-Z 전체 목차 허브글입니다.",
    }
    (output_dir / "publish_bundle.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    blog_root = Path(os.getenv("LONGTAIL_BLOG_AUTOMATION_ROOT", "/home/kj/app/bunyang/blog-cheongyak-automation"))
    if str(blog_root) not in sys.path:
        sys.path.insert(0, str(blog_root))
    from src.publisher.naver_playwright import publish as naver_publish  # noqa: WPS433,E402

    write_url = "" if force_new else _blog_update_url(str(hub.get("naver_url") or ""))
    env_overrides: dict[str, str | None] = {}
    if category_no is not None:
        env_overrides["NAVER_BLOG_CATEGORY_NO"] = str(category_no).strip() or None
    if category_name is not None:
        env_overrides["NAVER_BLOG_CATEGORY_NAME"] = str(category_name).strip() or None
    previous_env = {key: os.environ.get(key) for key in env_overrides}
    out_dir = output_dir / "naver_results"
    try:
        for key, value in env_overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ok = naver_publish(
            apt_id,
            title,
            body_html,
            [thumbnail_path],
            mode=mode,
            out=str(out_dir),
            body_markdown=publish_markdown,
            tags=_hub_tags(title),
            write_url=write_url or None,
            notice=True,
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    result_path = out_dir / f"{apt_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result["ok"] = bool(ok)
    result["hub_id"] = hub["id"]
    result["track_key"] = track_key
    result["meta_path"] = str(output_dir / "publish_bundle.json")
    result["body_markdown_path"] = str(output_dir / "body.md")
    result["update_mode"] = bool(write_url)

    publish_url = _clean(str(result.get("current_url") or result.get("url") or result.get("naver_url") or hub.get("naver_url") or ""))
    if ok and publish_url:
        set_curriculum_hub_url(db_path, publish_url, track_key=track_key, synced=True)
        result["stored_hub_url"] = publish_url
    elif ok:
        mark_curriculum_hub_synced(db_path, track_key=track_key)
    return result
