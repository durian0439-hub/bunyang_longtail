from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.asset_retention import cleanup_published_media, is_video_publish_complete  # noqa: E402
from bunyang_longtail.database import init_db  # noqa: E402


class TestAssetRetention(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / "longtail.sqlite3"
        self.output_base = self.root / "cron_runs"
        self.blog_output_dir = self.root / "blog_outputs"
        self.output_base.mkdir()
        self.blog_output_dir.mkdir()
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _insert_published_bundle(self, bundle_id: int = 1) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                INSERT INTO topic_cluster (
                    id, domain, semantic_key, family, primary_keyword, secondary_keyword,
                    audience, search_intent, scenario, comparison_keyword, priority,
                    outline_json, policy_json, status
                ) VALUES (?, 'auction', ?, '경매기초', '경매', '입찰', '초보', 'howto', '기초', '', 1, '{}', '{}', 'active')
                """,
                (bundle_id, f"semantic-{bundle_id}"),
            )
            conn.execute(
                """
                INSERT INTO topic_variant (
                    id, cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status
                ) VALUES (?, ?, ?, 'angle', 'title', ?, 80, '{}', 'published')
                """,
                (bundle_id, bundle_id, f"variant-{bundle_id}", f"slug-{bundle_id}"),
            )
            conn.execute(
                "INSERT INTO article_bundle (id, variant_id, bundle_status) VALUES (?, ?, 'published')",
                (bundle_id, bundle_id),
            )
            conn.execute(
                "INSERT INTO article_draft (id, bundle_id, variant_id, title, status) VALUES (?, ?, ?, 'title', 'published')",
                (bundle_id, bundle_id, bundle_id),
            )
            conn.execute(
                """
                INSERT INTO publish_history (
                    bundle_id, variant_id, draft_id, channel, target_account, publish_mode,
                    published_title, naver_url, published_at, result_json
                ) VALUES (?, ?, ?, 'naver_blog', 'default', 'published', 'title', 'https://blog.naver.com/example/1', '2026-04-20 00:00:00', '{}')
                """,
                (bundle_id, bundle_id, bundle_id),
            )

    def _write_completed_run(self, bundle_id: int = 1) -> tuple[Path, Path, Path, Path]:
        run_dir = self.output_base / f"auction_bundle_{bundle_id}"
        images_dir = run_dir / "images"
        video_dir = run_dir / "video"
        images_dir.mkdir(parents=True)
        video_dir.mkdir()
        image_path = images_dir / "01_thumbnail.png"
        video_path = video_dir / f"bundle-{bundle_id}.mp4"
        screenshot_path = self.blog_output_dir / f"longtail-bundle-{bundle_id}.png"
        image_path.write_bytes(b"image")
        video_path.write_bytes(b"video")
        screenshot_path.write_bytes(b"screenshot")
        (run_dir / "publish_bundle.json").write_text(
            json.dumps({"bundle_id": bundle_id, "images": [str(image_path)]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (video_dir / f"bundle-{bundle_id}.youtube.json").write_text(
            json.dumps(
                {
                    "status": "ok",
                    "youtube_upload": "uploaded",
                    "video_id": "video123",
                    "video_path": str(video_path),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return run_dir, image_path, video_path, screenshot_path

    def _age_file(self, path: Path, *, days_old: int) -> None:
        now_ts = datetime(2026, 4, 26, tzinfo=timezone.utc).timestamp()
        ts = now_ts - days_old * 24 * 60 * 60
        os.utime(path, (ts, ts))

    def test_is_video_publish_complete_requires_external_publish_marker(self) -> None:
        self.assertTrue(is_video_publish_complete({"status": "ok", "youtube_upload": "uploaded", "video_id": "abc"}))
        self.assertTrue(is_video_publish_complete({"status": "ok", "naver_clip_upload": {"status": "public_saved"}}))
        self.assertFalse(is_video_publish_complete({"status": "ok", "youtube_upload": "skipped"}))
        self.assertFalse(is_video_publish_complete({"status": "error", "youtube_upload": "uploaded", "video_id": "abc"}))

    def test_cleanup_deletes_only_old_media_after_video_publish_complete(self) -> None:
        self._insert_published_bundle(1)
        _, image_path, video_path, screenshot_path = self._write_completed_run(1)
        for path in (image_path, video_path, screenshot_path):
            self._age_file(path, days_old=4)

        summary = cleanup_published_media(
            db_path=self.db_path,
            output_base=self.output_base,
            retention_days=3,
            blog_output_dir=self.blog_output_dir,
            now=datetime(2026, 4, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(summary.deleted_count, 3)
        self.assertFalse(image_path.exists())
        self.assertFalse(video_path.exists())
        self.assertFalse(screenshot_path.exists())
        self.assertTrue((self.output_base / "auction_bundle_1" / "publish_bundle.json").exists())

    def test_cleanup_skips_media_when_video_publish_is_not_complete(self) -> None:
        self._insert_published_bundle(1)
        run_dir, image_path, video_path, _ = self._write_completed_run(1)
        (run_dir / "video" / "bundle-1.youtube.json").write_text(
            json.dumps({"status": "ok", "youtube_upload": "skipped", "video_path": str(video_path)}),
            encoding="utf-8",
        )
        for path in (image_path, video_path):
            self._age_file(path, days_old=4)

        summary = cleanup_published_media(
            db_path=self.db_path,
            output_base=self.output_base,
            retention_days=3,
            now=datetime(2026, 4, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(summary.deleted_count, 0)
        self.assertTrue(image_path.exists())
        self.assertTrue(video_path.exists())

    def test_cleanup_keeps_recent_media_even_after_video_publish_complete(self) -> None:
        self._insert_published_bundle(1)
        _, image_path, video_path, _ = self._write_completed_run(1)
        for path in (image_path, video_path):
            self._age_file(path, days_old=2)

        summary = cleanup_published_media(
            db_path=self.db_path,
            output_base=self.output_base,
            retention_days=3,
            now=datetime(2026, 4, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(summary.deleted_count, 0)
        self.assertTrue(image_path.exists())
        self.assertTrue(video_path.exists())


if __name__ == "__main__":
    unittest.main()
