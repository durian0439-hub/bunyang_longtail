from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.cli import main
from bunyang_longtail.config import OPENAI_COMPAT_IMAGE_MODEL, OPENAI_COMPAT_TEXT_MODEL
from bunyang_longtail.database import connect, init_db, migrate_db
from bunyang_longtail.gpt_web import (
    GptWebExecutionError,
    _classify_launch_failure_message,
    _detect_page_state,
    _has_new_generated_image,
    _looks_like_complete_article,
    _looks_like_generated_image_src,
    _resolve_google_login_credentials,
    _wait_for_image_response,
)
from bunyang_longtail.local_image_fallback import _summary_points, _summary_source, render_fallback_thumbnail
from bunyang_longtail.openai_compat import OpenAICompatExecutionError, probe_openai_compat
from bunyang_longtail.planner import export_prompts, get_prompt, list_variants, mark_published, replenish_queue, stats
from bunyang_longtail.workers import (
    complete_image_job,
    complete_text_job,
    create_bundle,
    fail_job,
    job_stats,
    queue_image_job,
    queue_text_job,
    run_bundle,
    start_job,
)


class LongtailPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.sqlite3"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_migrate_v2_adds_new_tables_and_columns(self) -> None:
        migrate_db(self.db_path)
        with connect(self.db_path) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn("article_bundle", tables)
            self.assertIn("generation_job", tables)
            self.assertIn("image_asset", tables)
            self.assertIn("publish_history", tables)
            article_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(article_draft)").fetchall()
            }
            self.assertIn("bundle_id", article_columns)
            self.assertIn("source_job_id", article_columns)
            self.assertIn("normalized_title_hash", article_columns)
            variant_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(topic_variant)").fetchall()
            }
            self.assertIn("prompt_version", variant_columns)
            self.assertIn("route_policy", variant_columns)
            job_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(generation_job)").fetchall()
            }
            self.assertIn("bundle_id", job_columns)

    def test_replenish_generates_unique_variants(self) -> None:
        result = replenish_queue(self.db_path, min_queued=60, variants_per_cluster=3)
        self.assertGreaterEqual(result["queued"], 60)
        rows = list_variants(self.db_path, status="queued", limit=100)
        titles = [row["title"] for row in rows]
        self.assertEqual(len(titles), len(set(titles)))

    def test_prompt_contains_required_sections(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=2)
        rows = list_variants(self.db_path, status="queued", limit=1)
        prompt = get_prompt(self.db_path, variant_id=rows[0]["id"])
        self.assertIsNotNone(prompt)
        prompt_json = prompt["prompt_json"]
        self.assertIn("system", prompt_json)
        self.assertIn("user", prompt_json)
        headings = [section["heading"] for section in prompt_json["user"]["outline"]]
        self.assertIn("상단 요약", headings)
        self.assertIn("FAQ", headings)
        self.assertIn(prompt["primary_keyword"], prompt_json["user"]["title"])
        self.assertEqual(prompt["prompt_version"], "v1")
        self.assertEqual(prompt["route_policy"], "gpt_web_first")

    def test_text_job_lifecycle_creates_draft(self) -> None:
        replenish_queue(self.db_path, min_queued=20, variants_per_cluster=2)
        queued = queue_text_job(self.db_path)
        start_job(self.db_path, queued["job_id"])
        result = complete_text_job(
            self.db_path,
            job_id=queued["job_id"],
            article_markdown="# 제목\n\n첫 문단 결론입니다.\n\nFAQ 1\nFAQ 2",
            excerpt="요약입니다.",
            quality_score=0.92,
            similarity_score=0.11,
        )
        self.assertIn("draft_id", result)
        self.assertIn("bundle_id", result)
        summary = stats(self.db_path)
        self.assertEqual(summary["bundles"], 1)
        self.assertEqual(summary["jobs"], 1)
        self.assertEqual(summary["drafts"], 1)
        bundle_status_map = {row["bundle_status"]: row["cnt"] for row in summary["by_bundle_status"]}
        self.assertEqual(bundle_status_map.get("rendering_image"), 1)
        job_summary = job_stats(self.db_path)
        status_map = {row["status"]: row["cnt"] for row in job_summary["by_status"]}
        self.assertEqual(status_map.get("succeeded"), 1)

    def test_image_job_lifecycle_creates_asset(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        variant_id = list_variants(self.db_path, status="queued", limit=1)[0]["id"]
        text_job = queue_text_job(self.db_path, variant_id=variant_id)
        start_job(self.db_path, text_job["job_id"])
        complete_text_job(
            self.db_path,
            job_id=text_job["job_id"],
            article_markdown="# 제목\n\n본문 결론",
        )
        queued = queue_image_job(self.db_path, bundle_id=text_job["bundle_id"], image_role="thumbnail")
        start_job(self.db_path, queued["job_id"])
        image_path = Path(self.tmpdir.name) / "thumb.png"
        image_path.write_bytes(b"fakepng")
        result = complete_image_job(
            self.db_path,
            job_id=queued["job_id"],
            image_role="thumbnail",
            prompt_text="썸네일 이미지 생성 프롬프트",
            file_path=str(image_path),
            width=1080,
            height=1080,
        )
        self.assertIn("asset_id", result)
        self.assertIn("bundle_id", result)
        summary = stats(self.db_path)
        self.assertEqual(summary["image_assets"], 1)
        bundle_status_map = {row["bundle_status"]: row["cnt"] for row in summary["by_bundle_status"]}
        self.assertEqual(bundle_status_map.get("bundled"), 1)

    def test_mark_published_records_article_history(self) -> None:
        replenish_queue(self.db_path, min_queued=5, variants_per_cluster=1)
        rows = list_variants(self.db_path, status="queued", limit=1)
        variant_id = rows[0]["id"]
        queued = queue_text_job(self.db_path, variant_id=variant_id)
        start_job(self.db_path, queued["job_id"])
        complete_text_job(
            self.db_path,
            job_id=queued["job_id"],
            article_markdown="# 본문\n\n결론 먼저 씁니다.",
        )
        mark_published(self.db_path, variant_id, "https://blog.naver.com/example/1")
        summary = stats(self.db_path)
        status_map = {row["status"]: row["cnt"] for row in summary["by_status"]}
        self.assertEqual(status_map.get("published"), 1)
        self.assertEqual(summary["publish_history"], 1)
        bundle_status_map = {row["bundle_status"]: row["cnt"] for row in summary["by_bundle_status"]}
        self.assertEqual(bundle_status_map.get("published"), 1)

    def test_run_bundle_simulate_completes_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        result = run_bundle(
            self.db_path,
            image_roles=["thumbnail", "summary_card"],
            simulate=True,
            simulate_output_dir=Path(self.tmpdir.name) / "simulated_assets",
        )
        self.assertEqual(result["mode"], "simulated_complete")
        self.assertEqual(result["bundle"]["bundle_status"], "bundled")
        self.assertEqual(len(result["image_jobs"]), 2)
        self.assertEqual(len(result["image_results"]), 2)
        summary = stats(self.db_path)
        self.assertEqual(summary["bundles"], 1)
        self.assertEqual(summary["jobs"], 3)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 2)

    def test_run_bundle_mock_executor_completes_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        result = run_bundle(
            self.db_path,
            image_roles=["thumbnail", "summary_card"],
            executor_mode="mock",
        )
        self.assertEqual(result["mode"], "mock_complete")
        self.assertEqual(result["bundle"]["bundle_status"], "bundled")
        self.assertEqual(len(result["errors"]), 0)
        summary = stats(self.db_path)
        self.assertEqual(summary["bundles"], 1)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 2)

    def test_run_bundle_resume_existing_bundle_skips_completed_roles(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        first = run_bundle(
            self.db_path,
            image_roles=["thumbnail"],
            simulate=True,
            simulate_output_dir=Path(self.tmpdir.name) / "simulated_assets",
        )
        result = run_bundle(
            self.db_path,
            bundle_id=first["bundle"]["id"],
            image_roles=["thumbnail", "summary_card"],
            executor_mode="mock",
            text_route="reuse_existing",
        )
        self.assertEqual(result["mode"], "mock_complete")
        self.assertTrue(result["text_result"]["resumed"])
        self.assertEqual(result["skipped_image_roles"], ["thumbnail"])
        self.assertEqual(len(result["image_jobs"]), 1)
        self.assertEqual(result["image_jobs"][0]["image_role"], "summary_card")
        summary = stats(self.db_path)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 2)
        self.assertEqual(summary["jobs"], 3)

    def test_retry_text_job_increments_attempt_no(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        queued = queue_text_job(self.db_path)
        start_job(self.db_path, queued["job_id"])
        fail_job(self.db_path, job_id=queued["job_id"], error_code="GPT_WEB_CHALLENGE", error_message="검증 실패")
        retried = queue_text_job(self.db_path, bundle_id=queued["bundle_id"])
        with connect(self.db_path) as conn:
            attempt_no = conn.execute(
                "SELECT attempt_no FROM generation_job WHERE id = ?",
                (retried["job_id"],),
            ).fetchone()[0]
        self.assertEqual(attempt_no, 2)

    def test_run_bundle_openai_compat_executor_completes_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        with patch(
            "bunyang_longtail.workers.execute_text_job_openai",
            return_value={
                "article_markdown": "# 제목\n\nAPI 본문 요약\n\n## FAQ\n- 답변",
                "excerpt": "API 본문 요약",
                "response_payload": {"mode": "openai_compat"},
            },
        ), patch(
            "bunyang_longtail.workers.execute_image_job_openai",
            return_value={
                "file_path": str(Path(self.tmpdir.name) / "api_thumb.png"),
                "response_payload": {"mode": "openai_compat"},
            },
        ):
            Path(self.tmpdir.name, "api_thumb.png").write_bytes(b"fakepng")
            result = run_bundle(
                self.db_path,
                image_roles=["thumbnail"],
                executor_mode="openai_compat",
            )
        self.assertEqual(result["mode"], "openai_compat_complete")
        self.assertEqual(result["text_job"]["route"], "openai_compat")
        self.assertEqual(result["text_job"]["model_label"], OPENAI_COMPAT_TEXT_MODEL)
        self.assertEqual(result["image_jobs"][0]["route"], "openai_compat")
        self.assertEqual(result["image_jobs"][0]["model_label"], OPENAI_COMPAT_IMAGE_MODEL)
        summary = stats(self.db_path)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 1)

    def test_run_bundle_local_canvas_fallback_completes_when_gpt_web_image_fails(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        text_job = queue_text_job(self.db_path)
        start_job(self.db_path, text_job["job_id"])
        complete_text_job(
            self.db_path,
            job_id=text_job["job_id"],
            article_markdown="# 제목\n\n본문 결론",
            excerpt="맞벌이라도 일반공급 1순위는 가능할 수 있습니다.",
        )
        with patch(
            "bunyang_longtail.workers.execute_image_job",
            side_effect=GptWebExecutionError(
                "로그인이 필요합니다.",
                code="GPT_WEB_LOGIN_REQUIRED",
                artifact_dir=str(Path(self.tmpdir.name) / "probe_artifacts"),
            ),
        ):
            result = run_bundle(
                self.db_path,
                bundle_id=text_job["bundle_id"],
                image_roles=["thumbnail"],
                executor_mode="playwright",
                text_route="reuse_existing",
                image_fallback="local_canvas",
                artifact_root=Path(self.tmpdir.name) / "local_fallback_artifacts",
            )
        self.assertEqual(result["mode"], "playwright_complete")
        self.assertEqual(result["bundle"]["bundle_status"], "bundled")
        self.assertEqual(len(result["errors"]), 0)
        summary = stats(self.db_path)
        self.assertEqual(summary["image_assets"], 1)
        with connect(self.db_path) as conn:
            payload_raw = conn.execute(
                "SELECT response_payload_json FROM generation_job WHERE id = ?",
                (result["image_jobs"][0]["job_id"],),
            ).fetchone()[0]
            asset_path = conn.execute("SELECT file_path FROM image_asset WHERE bundle_id = ?", (text_job["bundle_id"],)).fetchone()[0]
        payload = json.loads(payload_raw)
        self.assertEqual(payload["mode"], "local_canvas_fallback")
        self.assertEqual(payload["source_error_code"], "GPT_WEB_LOGIN_REQUIRED")
        self.assertTrue(Path(asset_path).exists())

    def test_run_bundle_with_markdown_file_still_executes_images(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        markdown_path = Path(self.tmpdir.name) / "article.md"
        markdown_path.write_text("# 제목\n\n본문 결론", encoding="utf-8")
        image_path = Path(self.tmpdir.name) / "generated.png"
        image_path.write_bytes(b"fakepng")
        with patch(
            "bunyang_longtail.workers.execute_image_job",
            return_value={"file_path": str(image_path), "response_payload": {"mode": "playwright"}},
        ) as mocked_execute_image_job:
            result = run_bundle(
                self.db_path,
                variant_id=1,
                markdown_file=markdown_path,
                image_roles=["thumbnail"],
                executor_mode="playwright",
            )
        self.assertEqual(result["mode"], "playwright_complete")
        self.assertEqual(result["bundle"]["bundle_status"], "bundled")
        mocked_execute_image_job.assert_called_once()
        summary = stats(self.db_path)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 1)

    def test_probe_openai_compat_without_api_key_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(OpenAICompatExecutionError) as exc_info:
                probe_openai_compat(base_url="https://example.com")
        self.assertEqual(exc_info.exception.code, "OPENAI_COMPAT_API_KEY_MISSING")

    def test_run_bundle_cli_simulate_completes_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=5, variants_per_cluster=1)
        exit_code = main(
            [
                "--db",
                str(self.db_path),
                "run-bundle",
                "--simulate",
                "--image-role",
                "thumbnail",
            ]
        )
        self.assertEqual(exit_code, 0)
        summary = stats(self.db_path)
        self.assertEqual(summary["bundles"], 1)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 1)

    def test_run_bundle_cli_mock_completes_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=5, variants_per_cluster=1)
        exit_code = main(
            [
                "--db",
                str(self.db_path),
                "run-bundle",
                "--executor",
                "mock",
                "--image-role",
                "thumbnail",
            ]
        )
        self.assertEqual(exit_code, 0)
        summary = stats(self.db_path)
        self.assertEqual(summary["bundles"], 1)
        self.assertEqual(summary["drafts"], 1)
        self.assertEqual(summary["image_assets"], 1)

    def test_classify_launch_failure_message_for_missing_xserver(self) -> None:
        result = _classify_launch_failure_message("Looks like you launched a headed browser without having a XServer running.")
        self.assertIsNotNone(result)
        code, message = result
        self.assertEqual(code, "GPT_WEB_XSERVER_MISSING")
        self.assertIn("xvfb-run", message)

    def test_detect_page_state_prefers_visible_composer_over_hidden_challenge_markup(self) -> None:
        class DummyPage:
            def title(self) -> str:
                return "ChatGPT"

            def content(self) -> str:
                return '<html><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></html>'

        def fake_first_visible(_page, selectors):
            if selectors and selectors[0].startswith("textarea"):
                return ("textarea", object())
            return None

        with patch("bunyang_longtail.gpt_web._first_visible", side_effect=fake_first_visible), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=0
        ):
            self.assertEqual(_detect_page_state(DummyPage()), "ready")

    def test_detect_page_state_returns_login_required_when_login_button_is_visible(self) -> None:
        class DummyPage:
            url = "https://chatgpt.com/"

            def title(self) -> str:
                return "ChatGPT"

            def content(self) -> str:
                return "<html></html>"

        def fake_first_visible(_page, selectors):
            if selectors and selectors[0].startswith("textarea"):
                return ("textarea", object())
            if selectors and ('Log in' in selectors[0] or 'login-button' in selectors[0]):
                return (selectors[0], object())
            return None

        with patch("bunyang_longtail.gpt_web._first_visible", side_effect=fake_first_visible), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=0
        ):
            self.assertEqual(_detect_page_state(DummyPage()), "login_required")

    def test_detect_page_state_returns_login_required_on_google_auth_url(self) -> None:
        class DummyPage:
            url = "https://accounts.google.com/v3/signin/identifier"

            def title(self) -> str:
                return "Google 계정으로 로그인"

            def content(self) -> str:
                return "<html><body>Google login</body></html>"

        with patch("bunyang_longtail.gpt_web._first_visible", return_value=None), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=0
        ):
            self.assertEqual(_detect_page_state(DummyPage()), "login_required")

    def test_resolve_google_login_credentials_reads_env_candidate_file(self) -> None:
        env_path = Path(self.tmpdir.name) / ".env"
        env_path.write_text(
            "GPT_WEB_GOOGLE_EMAIL=bear0439@gmail.com\nGPT_WEB_GOOGLE_PASSWORD=secret-pass\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True), patch(
            "bunyang_longtail.gpt_web.GPT_WEB_ENV_CANDIDATES",
            [env_path],
        ):
            creds = _resolve_google_login_credentials()
        self.assertEqual(creds["email"], "bear0439@gmail.com")
        self.assertEqual(creds["password"], "secret-pass")

    def test_looks_like_complete_article_detects_long_blog_shape(self) -> None:
        text = "\n".join(
            [
                "제목",
                "상단 요약",
                "A" * 1200,
                "체크리스트",
                "B" * 1200,
                "FAQ",
                "C" * 1200,
                "마무리 결론",
            ]
        )
        self.assertTrue(_looks_like_complete_article(text))
        self.assertFalse(_looks_like_complete_article("짧은 글\nFAQ"))

    def test_generated_image_helpers_detect_new_estuary_asset(self) -> None:
        before = [
            "https://chatgpt.com/backend-api/estuary/content?id=file_old",
        ]
        after = before + [
            "https://chatgpt.com/backend-api/estuary/content?id=file_new",
        ]
        self.assertTrue(_looks_like_generated_image_src(after[-1]))
        self.assertFalse(_looks_like_generated_image_src("https://lh3.googleusercontent.com/avatar"))
        self.assertTrue(_has_new_generated_image(before, after))
        self.assertFalse(_has_new_generated_image(before, before))

    def test_wait_for_image_response_captures_image_on_final_recheck(self) -> None:
        output_path = Path(self.tmpdir.name) / "image.png"
        artifact_dir = Path(self.tmpdir.name) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        class DummyImageLocator:
            def get_attribute(self, name: str) -> str:
                if name == "src":
                    return "data:image/png;base64,iVBORw0KGgo="
                return ""

        class DummyPage:
            def wait_for_timeout(self, _ms: int) -> None:
                return None

            def evaluate(self, *_args, **_kwargs):
                return None

        with patch("bunyang_longtail.gpt_web._last_locator", return_value=None), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=0
        ), patch(
            "bunyang_longtail.gpt_web._collect_generated_image_sources",
            return_value=["https://chatgpt.com/backend-api/estuary/content?id=file_new"],
        ), patch(
            "bunyang_longtail.gpt_web._last_visible",
            return_value=("img[src*='estuary']", DummyImageLocator()),
        ), patch("bunyang_longtail.gpt_web._has_stop_button", return_value=False), patch(
            "bunyang_longtail.gpt_web._take_artifacts"
        ):
            result = _wait_for_image_response(
                DummyPage(),
                artifact_dir=artifact_dir,
                output_path=output_path,
                timeout_seconds=0,
                before_count=0,
                before_text="",
                before_image_sources=[],
            )
        self.assertEqual(result["file_path"], str(output_path))
        self.assertTrue(output_path.exists())
        self.assertGreater(output_path.stat().st_size, 0)

    def test_summary_source_uses_article_body_when_excerpt_is_placeholder(self) -> None:
        article_markdown = "# 제목\n\n상단 요약\n\n30대 맞벌이도 일반공급 1순위는 충분히 가능할 수 있습니다.\n\nFAQ"
        summary = _summary_source("제목", "상단 요약", article_markdown)
        self.assertIn("30대 맞벌이도", summary)

    def test_render_fallback_thumbnail_creates_summary_card_png(self) -> None:
        output = Path(self.tmpdir.name) / "summary_card.png"
        render_fallback_thumbnail(
            title="1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까",
            excerpt="상단 요약",
            article_markdown="# 제목\n\n- 맞벌이라도 일반공급 1순위는 가능할 수 있습니다.\n- 특별공급은 소득과 자산 기준을 따로 계산해야 합니다.\n- 최종 확인은 공고문과 청약홈에서 해야 합니다.",
            output_path=output,
            image_role="summary_card",
        )
        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 1000)
        self.assertEqual(output.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_summary_points_merge_short_lead_sentence_with_next_sentence(self) -> None:
        points = _summary_points(
            "제목",
            "그래서 결론은 하나입니다. 맞벌이라서 자동 불가가 아니라, 어떤 공급에서 1순위를 따지는지부터 나눠 봐야 합니다.",
            None,
        )
        self.assertIn("맞벌이라서 자동 불가가 아니라", points[0])

    def test_summary_points_skips_meta_bullets(self) -> None:
        article_markdown = "# 제목\n\n- 이미지 세트: thumbnail, summary_card\n- 실제 핵심 판단은 배우자 이력과 추천기관 기준을 따로 봐야 합니다."
        points = _summary_points("제목", "상단 요약", article_markdown)
        self.assertIn("배우자 이력", points[0])
        self.assertTrue(all("이미지 세트" not in point for point in points))

    def test_export_writes_jsonl(self) -> None:
        replenish_queue(self.db_path, min_queued=12, variants_per_cluster=2)
        export_path = Path(self.tmpdir.name) / "queued.jsonl"
        count = export_prompts(self.db_path, export_path, limit=7)
        self.assertEqual(count, 7)
        lines = export_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 7)
        payload = json.loads(lines[0])
        self.assertIn("prompt_json", payload)


if __name__ == "__main__":
    unittest.main()
