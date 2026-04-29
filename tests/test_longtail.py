from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.cli import main
from bunyang_longtail.codex_cli import (
    CodexCLIExecutionError,
    _resolve_codex_executable,
    _validate_house_style,
    execute_text_job as execute_text_job_codex_cli,
)
from bunyang_longtail.config import OPENAI_COMPAT_IMAGE_MODEL, OPENAI_COMPAT_TEXT_MODEL
from bunyang_longtail.cron_publish import (
    describe_unpublishable_run_result,
    is_recent_publish_conflict,
    run_bundle_target_from_candidate,
    select_publish_candidate,
)
from bunyang_longtail.prompt_builder import build_prompt_package
from bunyang_longtail.article_quality import score_article_quality
from bunyang_longtail.database import connect, init_db, migrate_db
from bunyang_longtail.gpt_web import (
    GptWebExecutionError,
    _classify_launch_failure_message,
    _detect_page_state,
    _has_new_generated_image,
    _looks_like_complete_article,
    _looks_like_generated_image_src,
    _prepare_page,
    _resolve_google_login_credentials,
    _submit_prompt,
    _try_start_new_chat,
    _wait_for_image_response,
    build_image_prompt,
    build_text_prompt,
)
from bunyang_longtail.local_image_fallback import _summary_points, _summary_source, render_fallback_thumbnail
from bunyang_longtail.humanize_style import detect_ai_tell_findings, summarize_findings
from bunyang_longtail.openai_compat import OpenAICompatExecutionError, probe_openai_compat
from bunyang_longtail.planner import _topic_scene, export_prompts, get_prompt, list_variants, mark_published, replenish_queue, stats
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
    _ensure_bundle_in_conn,
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
            cluster_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(topic_cluster)").fetchall()
            }
            self.assertIn("domain", cluster_columns)
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

    def test_auction_title_scene_deduplicates_repeated_headword(self) -> None:
        self.assertEqual(_topic_scene("경매 공부", "공부 순서를 잡을 때"), "경매 공부 순서를 잡을 때")

    def test_replenish_auction_domain_generates_auction_prompts(self) -> None:
        result = replenish_queue(self.db_path, min_queued=60, variants_per_cluster=3, domain="auction")
        self.assertEqual(result["domain"], "auction")
        self.assertGreaterEqual(result["queued"], 60)
        rows = list_variants(self.db_path, status="queued", limit=20, domain="auction")
        self.assertTrue(rows)
        self.assertTrue(all(row["domain"] == "auction" for row in rows))
        prompt = get_prompt(self.db_path, variant_id=rows[0]["id"])
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["domain"], "auction")
        self.assertIn("경매", prompt["prompt_json"]["system"])
        self.assertNotIn("청약 전문", prompt["prompt_json"]["system"])
        self.assertIn("법원경매정보", " ".join(prompt["prompt_json"]["user"]["writing_rules"]))

    def test_replenish_tax_domain_generates_tax_prompts(self) -> None:
        result = replenish_queue(self.db_path, min_queued=60, variants_per_cluster=3, domain="tax")
        self.assertEqual(result["domain"], "tax")
        self.assertGreaterEqual(result["queued"], 60)
        rows = list_variants(self.db_path, status="queued", limit=20, domain="tax")
        self.assertTrue(rows)
        self.assertTrue(all(row["domain"] == "tax" for row in rows))
        prompt = get_prompt(self.db_path, variant_id=rows[0]["id"])
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["domain"], "tax")
        self.assertIn("부동산 세금", prompt["prompt_json"]["system"])
        self.assertNotIn("청약 전문", prompt["prompt_json"]["system"])
        self.assertIn("홈택스", " ".join(prompt["prompt_json"]["user"]["writing_rules"]))
        self.assertIn("wetax.go.kr", prompt["policy_json"]["keyword_sources"])

    def test_replenish_loan_domain_generates_loan_prompts(self) -> None:
        result = replenish_queue(self.db_path, min_queued=60, variants_per_cluster=3, domain="loan")
        self.assertEqual(result["domain"], "loan")
        self.assertGreaterEqual(result["queued"], 60)
        rows = list_variants(self.db_path, status="queued", limit=20, domain="loan")
        self.assertTrue(rows)
        self.assertTrue(all(row["domain"] == "loan" for row in rows))
        prompt = get_prompt(self.db_path, variant_id=rows[0]["id"])
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["domain"], "loan")
        self.assertIn("부동산 대출", prompt["prompt_json"]["system"])
        self.assertNotIn("청약 전문", prompt["prompt_json"]["system"])
        self.assertIn("은행 상담", " ".join(prompt["prompt_json"]["user"]["writing_rules"]))
        self.assertIn("fss.or.kr", prompt["policy_json"]["keyword_sources"])

    def test_prompt_contains_required_sections(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)
        rows = list_variants(self.db_path, status="queued", limit=1)
        prompt = get_prompt(self.db_path, variant_id=rows[0]["id"])
        self.assertIsNotNone(prompt)
        prompt_json = prompt["prompt_json"]
        self.assertIn("system", prompt_json)
        self.assertIn("user", prompt_json)
        headings = [section["heading"] for section in prompt_json["user"]["outline"]]
        self.assertIn("상단 요약", headings)
        self.assertIn("FAQ", headings)
        self.assertEqual(prompt_json["user"]["content_format"]["name"], "롱테일 정보 전달형 네이버 블로그 글")
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

    def test_select_publish_candidate_skips_excluded_variant_ids(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)
        with connect(self.db_path) as conn:
            first = select_publish_candidate(conn)
            self.assertIsNotNone(first)
            second = select_publish_candidate(conn, excluded_variant_ids={first["id"]})
        self.assertIsNotNone(second)
        self.assertNotEqual(first["id"], second["id"])

    def test_select_publish_candidate_skips_recent_same_topic_only(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            published = conn.execute(
                """
                SELECT tv.id, tv.cluster_id, tc.family, tc.primary_keyword, tv.angle
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.status = 'queued'
                ORDER BY tv.id ASC
                LIMIT 1
                """,
            ).fetchone()

        mark_published(self.db_path, published["id"], "https://blog.naver.com/example/recent-guard")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)
            self.assertIsNotNone(candidate)
            selected = conn.execute(
                """
                SELECT tv.cluster_id, tc.family, tc.primary_keyword, tv.angle
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.id = ?
                """,
                (candidate["id"],),
            ).fetchone()

        self.assertNotEqual(selected["cluster_id"], published["cluster_id"])

    def test_auction_select_publish_candidate_blocks_already_published_semantic_topic(self) -> None:
        result = replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2, domain="auction")
        self.assertEqual(result["domain"], "auction")

        with connect(self.db_path) as conn:
            pair = conn.execute(
                """
                SELECT a.id AS first_id, b.id AS second_id
                FROM topic_variant a
                JOIN topic_variant b ON b.cluster_id = a.cluster_id AND b.id <> a.id
                JOIN topic_cluster tc ON tc.id = a.cluster_id
                WHERE tc.domain = 'auction'
                ORDER BY a.cluster_id ASC, a.id ASC, b.id ASC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(pair)

        mark_published(self.db_path, pair["first_id"], "https://blog.naver.com/example/auction-duplicate-guard")
        with connect(self.db_path) as conn:
            conflict = is_recent_publish_conflict(conn, variant_id=pair["second_id"])
            candidate = select_publish_candidate(conn, domain="auction")

        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["conflict_reason"], "topic")
        self.assertTrue(candidate is None or candidate["id"] != pair["second_id"])

    def test_auction_replenish_queue_varies_possible_bid_titles(self) -> None:
        replenish_queue(self.db_path, min_queued=80, variants_per_cluster=1, domain="auction")
        with connect(self.db_path) as conn:
            repeated = conn.execute(
                """
                SELECT COUNT(*)
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tc.domain = 'auction'
                  AND tv.title LIKE '%입찰해도 될지 보는 기준%'
                """
            ).fetchone()[0]
        self.assertEqual(repeated, 0)

    def test_auction_select_publish_candidate_prefers_recent_family_diversity(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids = {}
            for idx, (family, keyword, intent) in enumerate(
                [
                    ("경매기초", "경매 체크리스트", "가능여부"),
                    ("권리분석", "말소기준권리", "실수방지"),
                ],
                start=1,
            ):
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES ('auction', ?, ?, ?, '보조', '경매초보', ?, '공부 순서', '비교', 100, '[]', '{}')
                    """,
                    (f"auction-diversity-{idx}", family, keyword, intent),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', ?, ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, f"auction-diversity-variant-{idx}", f"{keyword} 테스트", f"auction-diversity-{idx}"),
                )
                variant_ids[family] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        mark_published(self.db_path, variant_ids["경매기초"], "https://blog.naver.com/example/auction-family-diversity")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn, domain="auction")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["family"], "권리분석")
        self.assertEqual(candidate["primary_keyword"], "말소기준권리")

    def test_auction_select_publish_candidate_prefers_secondary_scenario_diversity(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids = {}
            rows = [
                ("published", "경매 사이트", "법원경매정보", "가능여부", "공부 순서를 잡을 때"),
                ("same_pattern", "무료 경매 사이트", "법원경매정보", "가능여부", "공부 순서를 잡을 때"),
                ("different_pattern", "말소기준권리", "권리분석", "실수방지", "입찰 전 점검"),
            ]
            for key, primary_keyword, secondary_keyword, intent, scenario in rows:
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES ('auction', ?, '경매기초', ?, ?, '경매초보', ?, ?, '비교', 10000, '[]', '{}')
                    """,
                    (f"auction-secondary-diversity-{key}", primary_keyword, secondary_keyword, intent, scenario),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', ?, ?, 100, '{}', 'queued')
                    """,
                    (
                        cluster_id,
                        f"auction-secondary-diversity-variant-{key}",
                        f"{primary_keyword} 테스트",
                        f"auction-secondary-diversity-{key}",
                    ),
                )
                variant_ids[key] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                """
                UPDATE topic_variant
                SET status = 'published'
                WHERE id NOT IN (?, ?, ?)
                  AND cluster_id IN (SELECT id FROM topic_cluster WHERE domain = 'auction')
                """,
                (variant_ids["published"], variant_ids["same_pattern"], variant_ids["different_pattern"]),
            )

        mark_published(self.db_path, variant_ids["published"], "https://blog.naver.com/example/auction-secondary-diversity")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn, domain="auction")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], variant_ids["different_pattern"])
        self.assertEqual(candidate["secondary_keyword"], "권리분석")
        self.assertEqual(candidate["scenario"], "입찰 전 점검")

    def test_select_publish_candidate_scopes_published_title_by_domain(self) -> None:
        with connect(self.db_path) as conn:
            for domain, semantic_key, variant_key in [
                ("cheongyak", "same-title-cheongyak", "same-title-cheongyak-variant"),
                ("auction", "same-title-auction", "same-title-auction-variant"),
            ]:
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, ?, '테스트', '동일제목', '보조', '테스트 독자', '조건정리', '테스트 상황', '비교', 100, '[]', '{}')
                    """,
                    (domain, semantic_key),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', '도메인별 같은 제목 테스트', ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, variant_key, f"{variant_key}-slug"),
                )

        with connect(self.db_path) as conn:
            cheongyak_id = conn.execute(
                """
                SELECT tv.id
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tc.domain = 'cheongyak'
                """
            ).fetchone()[0]
            auction_id = conn.execute(
                """
                SELECT tv.id
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tc.domain = 'auction'
                """
            ).fetchone()[0]

        mark_published(self.db_path, cheongyak_id, "https://blog.naver.com/example/domain-title")
        with connect(self.db_path) as conn:
            self.assertIsNone(is_recent_publish_conflict(conn, variant_id=auction_id))
            candidate = select_publish_candidate(conn, domain="auction")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], auction_id)
        self.assertEqual(candidate["domain"], "auction")

    def test_select_publish_candidate_allows_primary_keyword_reuse_for_different_topic(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids: list[int] = []
            for idx in range(1, 3):
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, '테스트', '김치', '보조', '30대 맞벌이', '계산', ?, '비교', 100, '[]', '{}')
                    """,
                    (f"same-keyword-different-topic-{idx}", f"상황{idx}"),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', ?, ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, f"same-keyword-different-topic-variant-{idx}", f"김치 테스트 {idx}", f"same-keyword-different-topic-{idx}"),
                )
                variant_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        mark_published(self.db_path, variant_ids[0], "https://blog.naver.com/example/same-keyword")
        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], variant_ids[1])

    def test_select_publish_candidate_allows_angle_reuse_when_other_guards_do_not_match(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            candidates = conn.execute(
                """
                SELECT tv.id, tv.angle, tc.primary_keyword, tc.family
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.status = 'queued'
                ORDER BY tv.id ASC
                """,
            ).fetchall()
            published = None
            for row in candidates:
                sibling = conn.execute(
                    """
                    SELECT tv.id
                    FROM topic_variant tv
                    JOIN topic_cluster tc ON tc.id = tv.cluster_id
                    WHERE tv.status = 'queued'
                      AND tv.id <> ?
                      AND tv.angle = ?
                      AND tc.primary_keyword <> ?
                    LIMIT 1
                    """,
                    (row["id"], row["angle"], row["primary_keyword"]),
                ).fetchone()
                if sibling is not None:
                    published = row
                    break
            self.assertIsNotNone(published)

        mark_published(self.db_path, published["id"], "https://blog.naver.com/example/angle-reuse")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)
            self.assertIsNotNone(candidate)
            selected = conn.execute(
                """
                SELECT tc.family, tc.primary_keyword, tv.angle
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.id = ?
                """,
                (candidate["id"],),
            ).fetchone()

        self.assertNotEqual(selected["primary_keyword"], published["primary_keyword"])

    def test_is_recent_publish_conflict_detects_recent_same_topic_or_title(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            pair = conn.execute(
                """
                SELECT a.id AS first_id, b.id AS second_id
                FROM topic_variant a
                JOIN topic_variant b ON b.cluster_id = a.cluster_id AND b.id <> a.id
                ORDER BY a.cluster_id ASC, a.id ASC, b.id ASC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(pair)
            conn.execute(
                """
                INSERT INTO article_draft (variant_id, title, article_markdown, prompt_version, status)
                VALUES (?, 'recent draft', 'body', 'v1', 'drafted')
                """,
                (pair['first_id'],),
            )
            draft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                """
                INSERT INTO publish_history (
                    bundle_id, variant_id, draft_id, channel, target_account, publish_mode, published_title, naver_url, published_at, result_json
                ) VALUES (?, ?, ?, 'naver_blog', 'default', 'publish', 'recent post', 'https://example.com/dup-check', CURRENT_TIMESTAMP, '{}')
                """,
                (None, pair['first_id'], draft_id),
            )
            conflict = is_recent_publish_conflict(conn, variant_id=pair['second_id'])

        self.assertIsNotNone(conflict)
        self.assertIn(conflict['conflict_reason'], {'topic', 'title'})

    def test_select_publish_candidate_weighted_random_falls_back_to_drafted_when_queued_conflicts(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            queued = conn.execute(
                """
                SELECT tv.id, tc.primary_keyword
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.status = 'queued'
                ORDER BY tv.id ASC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(queued)
            conn.execute(
                "UPDATE topic_variant SET status = 'drafted' WHERE status = 'queued' AND id <> ?",
                (queued['id'],),
            )
            conn.execute(
                """
                INSERT INTO article_draft (variant_id, title, article_markdown, prompt_version, status)
                SELECT id, title, 'body', 'v1', 'drafted'
                FROM topic_variant
                WHERE status = 'drafted'
                """
            )
            conn.execute(
                """
                INSERT INTO article_draft (variant_id, title, article_markdown, prompt_version, status)
                VALUES (?, 'recent draft', 'body', 'v1', 'drafted')
                """,
                (queued['id'],),
            )
            draft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                """
                INSERT INTO publish_history (
                    bundle_id, variant_id, draft_id, channel, target_account, publish_mode, published_title, naver_url, published_at, result_json
                ) VALUES (?, ?, ?, 'naver_blog', 'default', 'publish', 'recent queued conflict', 'https://example.com/conflict', CURRENT_TIMESTAMP, '{}')
                """,
                (None, queued['id'], draft_id),
            )
            random.seed(7)
            selected = select_publish_candidate(conn)

        self.assertIsNotNone(selected)
        self.assertNotEqual(selected['id'], queued['id'])

    def test_select_publish_candidate_returns_none_when_recent_guard_blocks_all_candidates(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            variants = conn.execute(
                """
                SELECT tv.id, tc.family, tc.primary_keyword
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.status = 'queued'
                  AND tc.family != '대안'
                ORDER BY tv.id ASC
                """,
            ).fetchall()

        variant_by_key = {}
        for row in variants:
            variant_by_key.setdefault((row["family"], row["primary_keyword"]), row["id"])

        self.assertGreaterEqual(len(variant_by_key), 4)

        for idx, variant_id in enumerate(list(variant_by_key.values())[:4], start=1):
            mark_published(self.db_path, variant_id, f"https://blog.naver.com/example/recent-guard-{idx}")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)

        self.assertIsNotNone(candidate)
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT tc.family, tc.primary_keyword
                FROM topic_variant tv
                JOIN topic_cluster tc ON tc.id = tv.cluster_id
                WHERE tv.id = ?
                """,
                (candidate["id"],),
            ).fetchone()

        blocked_keys = set(list(variant_by_key.keys())[:4])
        self.assertNotIn((row["family"], row["primary_keyword"]), blocked_keys)

    def test_select_publish_candidate_blocks_previously_published_cluster_even_when_not_recent(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)
        with connect(self.db_path) as conn:
            pair = conn.execute(
                """
                SELECT a.id AS first_id, b.id AS second_id, a.cluster_id
                FROM topic_variant a
                JOIN topic_variant b ON b.cluster_id = a.cluster_id AND b.id <> a.id
                WHERE a.status = 'queued' AND b.status = 'queued'
                ORDER BY a.cluster_id ASC, a.id ASC, b.id ASC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(pair)
            filler_ids = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT tv.id
                    FROM topic_variant tv
                    JOIN topic_cluster tc ON tc.id = tv.cluster_id
                    WHERE tv.cluster_id <> ?
                    GROUP BY tc.primary_keyword
                    ORDER BY tv.id ASC
                    LIMIT 4
                    """,
                    (pair["cluster_id"],),
                ).fetchall()
            ]
        self.assertGreaterEqual(len(filler_ids), 4)

        mark_published(self.db_path, pair["first_id"], "https://blog.naver.com/example/old-cluster")
        for idx, variant_id in enumerate(filler_ids, start=1):
            mark_published(self.db_path, variant_id, f"https://blog.naver.com/example/recent-filler-{idx}")

        with connect(self.db_path) as conn:
            conn.execute("UPDATE topic_variant SET status = 'published' WHERE id <> ?", (pair["second_id"],))
            candidate = select_publish_candidate(conn)

        self.assertIsNone(candidate)

    def test_select_publish_candidate_allows_previously_published_primary_keyword_when_topic_differs(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids: list[int] = []
            for idx, primary_keyword in enumerate(["중복키워드", "중복키워드", "필러1", "필러2", "필러3", "필러4"], start=1):
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, '테스트', ?, '보조', '30대 맞벌이', '계산', ?, '비교', 100, '[]', '{}')
                    """,
                    (f"dup-primary-{idx}", primary_keyword, f"상황{idx}"),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', ?, ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, f"dup-primary-variant-{idx}", f"{primary_keyword} 테스트 {idx}", f"dup-primary-{idx}"),
                )
                variant_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        first_id, second_id, *filler_ids = variant_ids
        mark_published(self.db_path, first_id, "https://blog.naver.com/example/old-primary-keyword")
        for idx, variant_id in enumerate(filler_ids, start=1):
            mark_published(self.db_path, variant_id, f"https://blog.naver.com/example/recent-primary-filler-{idx}")

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], second_id)

    def test_select_publish_candidate_does_not_recover_bundle_from_previously_published_cluster(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)
        with connect(self.db_path) as conn:
            pair = conn.execute(
                """
                SELECT a.id AS first_id, b.id AS second_id, a.cluster_id
                FROM topic_variant a
                JOIN topic_variant b ON b.cluster_id = a.cluster_id AND b.id <> a.id
                WHERE a.status = 'queued' AND b.status = 'queued'
                ORDER BY a.cluster_id ASC, a.id ASC, b.id ASC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(pair)
            conn.execute(
                """
                INSERT INTO article_bundle (variant_id, bundle_status, primary_draft_id, selected_image_ids_json, generation_strategy)
                VALUES (?, 'queued', 999, '[]', 'gpt_web_first')
                """,
                (pair["second_id"],),
            )
        mark_published(self.db_path, pair["first_id"], "https://blog.naver.com/example/old-cluster-recovery")
        with connect(self.db_path) as conn:
            conn.execute("UPDATE topic_variant SET status = 'published' WHERE id <> ?", (pair["second_id"],))
            candidate = select_publish_candidate(conn)

        self.assertIsNone(candidate)

    def test_select_publish_candidate_recovers_bundle_when_only_primary_keyword_matches(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids: list[int] = []
            for idx in range(1, 3):
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, '테스트', '중복키워드', '보조', '30대 맞벌이', '계산', ?, '비교', 100, '[]', '{}')
                    """,
                    (f"dup-primary-recovery-{idx}", f"상황{idx}"),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', ?, ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, f"dup-primary-recovery-variant-{idx}", f"중복키워드 테스트 {idx}", f"dup-primary-recovery-{idx}"),
                )
                variant_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO article_bundle (variant_id, bundle_status, primary_draft_id, selected_image_ids_json, generation_strategy)
                VALUES (?, 'queued', 999, '[]', 'gpt_web_first')
                """,
                (variant_ids[1],),
            )

        mark_published(self.db_path, variant_ids[0], "https://blog.naver.com/example/old-primary-keyword-recovery")
        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["id"], variant_ids[1])
        self.assertIsNotNone(candidate.get("bundle_id"))

    def test_select_publish_candidate_blocks_exact_title_republish(self) -> None:
        with connect(self.db_path) as conn:
            variant_ids: list[int] = []
            for idx in range(1, 3):
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, '테스트', ?, '보조', '30대 맞벌이', '계산', ?, '비교', 100, '[]', '{}')
                    """,
                    (f"exact-title-{idx}", f"키워드{idx}", f"상황{idx}"),
                )
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO topic_variant (cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status)
                    VALUES (?, ?, '판단형', '완전히 같은 제목', ?, 100, '{}', 'queued')
                    """,
                    (cluster_id, f"exact-title-variant-{idx}", f"exact-title-{idx}"),
                )
                variant_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        mark_published(self.db_path, variant_ids[0], "https://blog.naver.com/example/exact-title")
        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)

        self.assertIsNone(candidate)

    def test_select_publish_candidate_recovers_stale_rendering_image_bundle(self) -> None:
        replenish_queue(self.db_path, min_queued=30, variants_per_cluster=2)

        with connect(self.db_path) as conn:
            candidate = select_publish_candidate(conn)
            self.assertIsNotNone(candidate)
            bundle = _ensure_bundle_in_conn(conn, variant_id=candidate["id"])

        text_job = queue_text_job(self.db_path, bundle_id=bundle["id"])
        start_job(self.db_path, text_job["job_id"])
        complete_text_job(
            self.db_path,
            job_id=text_job["job_id"],
            article_markdown="# 제목\n\n본문",
        )
        image_job = queue_image_job(self.db_path, bundle_id=bundle["id"], image_role="thumbnail")
        start_job(self.db_path, image_job["job_id"])
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE generation_job SET started_at = datetime('now', '-30 minutes') WHERE id = ?",
                (image_job["job_id"],),
            )

        with connect(self.db_path) as conn:
            recovered = _ensure_bundle_in_conn(conn, variant_id=candidate["id"])
            job = conn.execute("SELECT status, error_code FROM generation_job WHERE id = ?", (image_job["job_id"],)).fetchone()

        self.assertEqual(recovered["bundle_status"], "queued")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_code"], "STALE_RUNNING_JOB")

    def test_describe_unpublishable_run_result_detects_missing_primary_draft(self) -> None:
        result = {
            "mode": "failed",
            "bundle": {
                "id": 24,
                "variant_id": 27,
                "bundle_status": "queued",
                "primary_draft_id": None,
            },
            "errors": [
                {
                    "code": "CODEX_CLI_STYLE_GUARD_FAILED",
                    "message": "금지된 상투 표현이 감지됐습니다: 이 전략이 맞습니다",
                }
            ],
        }
        blocker = describe_unpublishable_run_result(result)
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker["reason"], "run_bundle_reported_errors")
        self.assertEqual(blocker["first_error_code"], "CODEX_CLI_STYLE_GUARD_FAILED")
        self.assertIsNone(blocker["primary_draft_id"])

    def test_describe_unpublishable_run_result_returns_none_for_publishable_bundle(self) -> None:
        result = {
            "mode": "codex_cli_complete",
            "bundle": {
                "id": 25,
                "variant_id": 28,
                "bundle_status": "bundled",
                "primary_draft_id": 101,
            },
            "errors": [],
        }
        self.assertIsNone(describe_unpublishable_run_result(result))

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

    def test_run_bundle_fails_without_local_canvas_fallback_when_gpt_web_image_fails(self) -> None:
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
                artifact_root=Path(self.tmpdir.name) / "gpt_artifacts",
            )
        self.assertEqual(result["mode"], "partial_failed")
        self.assertEqual(result["bundle"]["bundle_status"], "queued")
        self.assertEqual(result["errors"][0]["code"], "GPT_WEB_LOGIN_REQUIRED")
        summary = stats(self.db_path)
        self.assertEqual(summary["image_assets"], 0)
        with connect(self.db_path) as conn:
            payload_raw = conn.execute(
                "SELECT response_payload_json FROM generation_job WHERE id = ?",
                (result["image_jobs"][0]["job_id"],),
            ).fetchone()[0]
        self.assertNotIn("local_canvas_fallback", payload_raw or "")

    def test_run_bundle_rejects_local_canvas_image_fallback_option(self) -> None:
        with self.assertRaises(ValueError):
            run_bundle(self.db_path, image_fallback="local_canvas")

    def test_run_bundle_playwright_requires_generated_image_file(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        markdown_path = Path(self.tmpdir.name) / "article.md"
        markdown_path.write_text("# 제목\n\n본문 결론", encoding="utf-8")
        missing_path = Path(self.tmpdir.name) / "missing.png"
        with patch(
            "bunyang_longtail.workers.execute_image_job",
            return_value={"file_path": str(missing_path), "response_payload": {"mode": "playwright"}},
        ):
            result = run_bundle(
                self.db_path,
                variant_id=1,
                markdown_file=markdown_path,
                image_roles=["thumbnail"],
                executor_mode="playwright",
            )
        self.assertEqual(result["mode"], "partial_failed")
        self.assertEqual(result["errors"][0]["code"], "GPT_WEB_IMAGE_FILE_MISSING")
        summary = stats(self.db_path)
        self.assertEqual(summary["image_assets"], 0)

    def test_run_bundle_with_markdown_file_still_executes_images(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=1)
        markdown_path = Path(self.tmpdir.name) / "article.md"
        markdown_path.write_text("# 제목\n\n본문 결론", encoding="utf-8")
        image_path = Path(self.tmpdir.name) / "generated.png"
        Image.new("RGB", (321, 123), "white").save(image_path)
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
        with connect(self.db_path) as conn:
            dimensions = conn.execute("SELECT width, height FROM image_asset LIMIT 1").fetchone()
        self.assertEqual(tuple(dimensions), (321, 123))

    def test_probe_openai_compat_without_api_key_fails(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(OpenAICompatExecutionError) as exc_info:
                probe_openai_compat(base_url="https://example.com")
        self.assertEqual(exc_info.exception.code, "OPENAI_COMPAT_API_KEY_MISSING")

    def test_resolve_codex_executable_uses_known_fallback_path(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "bunyang_longtail.codex_cli.shutil.which",
            return_value=None,
        ), patch(
            "bunyang_longtail.codex_cli._is_executable",
            side_effect=lambda path: str(path) == "/home/kj/.npm-global/bin/codex",
        ):
            resolved = _resolve_codex_executable()
        self.assertEqual(resolved, "/home/kj/.npm-global/bin/codex")

    def test_resolve_codex_executable_raises_clear_error_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "bunyang_longtail.codex_cli.shutil.which",
            return_value=None,
        ), patch(
            "bunyang_longtail.codex_cli._is_executable",
            return_value=False,
        ):
            with self.assertRaises(CodexCLIExecutionError) as exc_info:
                _resolve_codex_executable()
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_NOT_FOUND")
        self.assertIn("PATH", str(exc_info.exception))

    def test_validate_house_style_rejects_banned_phrase(self) -> None:
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style("# 제목\n\n이 경우 공급유형부터 나눠야 판단이 맞습니다.")
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_humanize_style_detector_flags_korean_ai_tells(self) -> None:
        findings = detect_ai_tell_findings(
            "# 제목\n\n결론적으로 이 제도를 통해 효율을 높일 수 있습니다. 또한 시사하는 바가 크다."
        )
        summary = summarize_findings(findings)
        self.assertTrue(any(finding.pattern_id == "D-1" for finding in findings))
        self.assertTrue(any(finding.pattern_id == "A-2" for finding in findings))
        self.assertIn("D-1", summary)

    def test_validate_house_style_rejects_humanize_korean_ai_tell(self) -> None:
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style("# 제목\n\n결론적으로 세금 기준은 주택 수 산정을 통해 달라질 수 있습니다.")
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")
        self.assertIn("humanize-korean", str(exc_info.exception))

    def test_validate_house_style_rejects_weak_intro(self) -> None:
        weak_intro = "# 제목\n\n청약 판단을 빨리 끝내려면 조건을 한 줄씩 분리해서 보셔야 합니다.\n\n당첨 직후 가장 먼저 확인할 결론은 단순합니다. 무주택 실수요자라도 입주자모집공고 기준과 제출 서류가 맞으면 진행할 수 있지만, 기준일과 세대 범위, 주택 수 판단이 어긋나면 당첨 이후에도 계약이 어려워질 수 있습니다."
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style(weak_intro)
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_validate_house_style_rejects_meta_intro_and_bullet_stack(self) -> None:
        bad_intro = "# 제목\n\n규제지역이라고 해서 처음부터 막히는 것은 아닙니다. 편이 더 유리합니다.\n\n- 규제지역에서 가능한가\n- 재당첨 제한은 어떤가\n- 기존 집은 언제 파는가\n\n정리하면 조건부로 가능합니다."
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style(bad_intro)
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_validate_house_style_rejects_ellipsis_and_truncated_claim(self) -> None:
        bad_body = "# 제목\n\n기존 집 처분 계획은 중요합니다.\n\n당첨 이후 자금 흐름이 흔들릴 수 있습니다..."
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style(bad_body)
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_validate_house_style_rejects_global_truncated_phrase(self) -> None:
        bad_body = "# 제목\n\n핵심 조건 정리\n\n정리하면\n\n재당첨 제한을 먼저 확인해야 합니다."
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style(bad_body)
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_validate_house_style_rejects_artificial_concept_phrase(self) -> None:
        bad_body = "# 제목\n\n1주택 갈아타기는 결국 일시적 2주택 관리 싸움입니다."
        with self.assertRaises(CodexCLIExecutionError) as exc_info:
            _validate_house_style(bad_body)
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

    def test_build_prompt_package_includes_house_style_guards(self) -> None:
        cluster = {
            "outline_json": json.dumps({"sections": []}, ensure_ascii=False),
            "primary_keyword": "1순위 조건",
            "secondary_keyword": "특별공급",
            "comparison_keyword": "일반공급",
            "audience": "30대 맞벌이",
            "search_intent": "가능여부",
            "scenario": "기준이 헷갈릴 때",
        }
        variant = {"title": "테스트 제목", "angle": "실수방지형"}
        prompt = build_prompt_package(cluster, variant)
        rules = "\n".join(prompt["user"]["writing_rules"] + prompt["user"]["quality_gates"])
        self.assertIn("판단이 맞습니다", rules)
        self.assertIn("안전합니다", rules)
        self.assertIn("그렇습니다.", rules)
        self.assertIn("도입부 첫 2문단", rules)
        self.assertIn("검색 누적형 자산", rules)
        self.assertIn("홈판 낚시", rules)
        self.assertNotIn("본문 끝에는 누가 이 전략이 맞는지", rules)

    def test_build_prompt_package_includes_domain_data_delivery_requirements(self) -> None:
        cluster = {
            "domain": "auction",
            "outline_json": json.dumps([{"heading": "상단 요약", "points": ["핵심"]}], ensure_ascii=False),
            "primary_keyword": "경락잔금대출",
            "secondary_keyword": "입찰가 산정",
            "comparison_keyword": "명도비",
            "audience": "경매 초보",
            "search_intent": "입찰준비",
            "scenario": "잔금 대출을 알아볼 때",
        }
        variant = {"title": "경락잔금대출 잔금 대출을 알아볼 때", "angle": "실수방지형"}
        prompt = build_prompt_package(cluster, variant)
        prompt_text = build_text_prompt(prompt)
        self.assertIn("데이터 전달 필수 조건", prompt_text)
        self.assertIn("사건번호·소재지·면적·감정가·최저가·입찰보증금", prompt_text)
        self.assertIn("입찰가 산정은 실거래가·전세가·낙찰가율", prompt_text)
        self.assertIn("매각물건명세서", prompt_text)

    def test_score_article_quality_penalizes_missing_domain_data_axes(self) -> None:
        weak_tax_article = "# 공시가격 세금 정리\n\n공시가격은 세금에서 중요합니다. 홈택스에서 확인합니다."
        score, meta = score_article_quality(weak_tax_article)
        self.assertLess(score, 10.0)
        self.assertIn("조건 변수", meta["data_delivery_missing"])

    def test_build_text_prompt_uses_neutral_action_guide_phrase(self) -> None:
        cluster = {
            "outline_json": json.dumps(
                [
                    {"heading": "상단 요약", "points": ["핵심 1", "핵심 2"]},
                    {"heading": "FAQ", "points": ["질문 1", "질문 2"]},
                ],
                ensure_ascii=False,
            ),
            "primary_keyword": "계약금",
            "secondary_keyword": "중도금",
            "comparison_keyword": "잔금",
            "audience": "30대 맞벌이",
            "search_intent": "계산",
            "scenario": "월 상환액을 따질 때",
        }
        variant = {"title": "테스트 제목", "angle": "비교형"}
        prompt = build_prompt_package(cluster, variant)
        prompt_text = build_text_prompt(prompt)
        self.assertIn("롱테일 정보 전달형 네이버 블로그 글", prompt_text)
        self.assertIn("본문은 정보 전달형 블로그 글처럼", prompt_text)
        self.assertIn("어떤 독자에게 더 적합한지 행동 가이드 1문장", prompt_text)
        self.assertNotIn("이 전략에 맞는지", prompt_text)

    def test_codex_execute_text_job_rewrites_after_style_guard_failure(self) -> None:
        cluster = {
            "outline_json": json.dumps(
                [
                    {"heading": "상단 요약", "points": ["핵심 1", "핵심 2"]},
                    {"heading": "FAQ", "points": ["질문 1", "질문 2"]},
                ],
                ensure_ascii=False,
            ),
            "primary_keyword": "계약금",
            "secondary_keyword": "중도금",
            "comparison_keyword": "잔금",
            "audience": "30대 맞벌이",
            "search_intent": "계산",
            "scenario": "월 상환액을 따질 때",
        }
        variant = {"title": "테스트 제목", "angle": "비교형"}
        prompt = build_prompt_package(cluster, variant)
        with patch(
            "bunyang_longtail.codex_cli._resolve_codex_executable",
            return_value="/home/kj/.npm-global/bin/codex",
        ), patch(
            "bunyang_longtail.codex_cli._run_codex_exec",
            side_effect=[
                "# 테스트 제목\n\n이 전략이 맞습니다.",
                "# 테스트 제목\n\n계약금이 빠듯한 30대 맞벌이라면 잔금 준비가 흔들리지 않게 납부 시점을 미리 나눠 두는 쪽이 안전합니다.",
            ],
        ):
            result = execute_text_job_codex_cli(
                job_id=999,
                prompt_payload=prompt,
                artifact_root=Path(self.tmpdir.name) / "codex_cli_artifacts",
                workdir=Path(self.tmpdir.name),
            )
        self.assertEqual(result["response_payload"]["style_rewrite_attempts"], 1)
        self.assertEqual(len(result["response_payload"]["style_guard_failures"]), 1)
        self.assertNotIn("이 전략이 맞습니다", result["article_markdown"])

    def test_codex_execute_text_job_soft_fallback_after_style_rewrite_limit(self) -> None:
        cluster = {
            "outline_json": json.dumps(
                [
                    {"heading": "상단 요약", "points": ["핵심 1", "핵심 2"]},
                    {"heading": "FAQ", "points": ["질문 1", "질문 2"]},
                    {"heading": "마무리 결론", "points": ["다음 순서"]},
                ],
                ensure_ascii=False,
            ),
            "primary_keyword": "계약금",
            "secondary_keyword": "중도금",
            "comparison_keyword": "잔금",
            "audience": "30대 맞벌이",
            "search_intent": "계산",
            "scenario": "월 상환액을 따질 때",
        }
        variant = {"title": "테스트 제목", "angle": "비교형"}
        prompt = build_prompt_package(cluster, variant)
        repeated_guard_phrase = " ".join(["판단"] * 14)
        structurally_complete_article = f"""# 테스트 제목

계약금이 빠듯한 30대 맞벌이라면 잔금 일정이 밀리면서 선택지가 줄어듭니다. 은행 상담 전에는 필요한 현금과 실행일을 먼저 맞춥니다.

## 상단 요약
계약금, 중도금, 잔금은 시점이 달라서 한 번에 계산하면 빠지는 돈이 생깁니다.
대출 가능성은 소득, 기존 대출, 담보가치, 실행일을 함께 봅니다.
최종 승인과 금리는 금융기관 심사로 확인합니다.

## 이 글에서 바로 답하는 질문
대출을 어디까지 받을지보다 언제 필요한지가 먼저입니다. {repeated_guard_phrase}
상담 전에는 계약서, 소득증빙, 기존 대출 내역을 같은 표에 적어 둡니다.

## 핵심 조건 정리
은행은 소득과 담보만 보지 않고 기존 대출과 상환 부담까지 함께 봅니다. {repeated_guard_phrase}
잔금일이 다가올수록 서류 보완 시간이 줄어들기 때문에 실행 가능성을 먼저 맞춰야 합니다.

## 헷갈리기 쉬운 예외
사전 상담 한도와 본심사 한도는 달라질 여지가 있습니다. {repeated_guard_phrase}
신용대출을 새로 쓰거나 카드론이 늘면 본심사에서 숫자가 바뀝니다.

## 실전 예시 시나리오
맞벌이 부부가 6억 원 주택을 계약하고 자기자금 2억 원을 준비했다고 가정합니다.
취득세와 중개보수, 이사비까지 더하면 필요한 금액은 단순 잔금보다 커집니다. {repeated_guard_phrase}

## 체크리스트
- 소득증빙을 먼저 모읍니다. 인정 소득이 달라지면 한도가 달라집니다.
- 기존 대출을 적습니다. DSR 계산에서 빠지면 본심사 때 막힙니다.
- 잔금일을 확인합니다. 실행일이 어긋나면 계약 일정이 흔들립니다.

## FAQ
### Q1. 대출한도는 바로 확정되나요?
아닙니다. 예상 한도와 본심사 한도는 다를 수 있습니다.
### Q2. 맞벌이면 무조건 유리한가요?
소득 인정 방식과 기존 대출에 따라 달라집니다.
### Q3. 신용대출을 줄이면 도움이 되나요?
DSR 부담이 줄어드는 경우가 있어 은행 상담에서 확인합니다.
### Q4. 정책대출도 같이 봐야 하나요?
한국주택금융공사와 주택도시기금 요건을 함께 확인합니다.
### Q5. 금리만 비교하면 되나요?
우대조건, 실행일, 중도상환수수료까지 함께 봅니다.
### Q6. 서류는 언제 준비하나요?
잔금일 전에 보완 시간이 남도록 미리 준비합니다.

## 마무리 결론
계약금과 잔금 일정이 빠듯한 30대 맞벌이는 한도표보다 실행일과 기존 대출 정리부터 맞추는 흐름이 알맞습니다.
"""
        with patch(
            "bunyang_longtail.codex_cli._resolve_codex_executable",
            return_value="/home/kj/.npm-global/bin/codex",
        ), patch(
            "bunyang_longtail.codex_cli._run_codex_exec",
            side_effect=[structurally_complete_article, structurally_complete_article, structurally_complete_article],
        ):
            result = execute_text_job_codex_cli(
                job_id=1000,
                prompt_payload=prompt,
                artifact_root=Path(self.tmpdir.name) / "codex_cli_artifacts",
                workdir=Path(self.tmpdir.name),
            )
        payload = result["response_payload"]
        self.assertEqual(payload["style_rewrite_attempts"], 2)
        self.assertTrue(payload["style_guard_soft_failed"])
        self.assertTrue(payload["manual_review_required"])
        self.assertGreaterEqual(len(payload["style_guard_failures"]), 3)
        self.assertIn("FAQ", result["article_markdown"])

    def test_codex_execute_text_job_strict_style_guard_still_raises_after_rewrite_limit(self) -> None:
        prompt = build_prompt_package(
            {
                "outline_json": json.dumps([{"heading": "상단 요약", "points": ["핵심"]}], ensure_ascii=False),
                "primary_keyword": "계약금",
                "secondary_keyword": "중도금",
                "comparison_keyword": "잔금",
                "audience": "30대 맞벌이",
                "search_intent": "계산",
                "scenario": "월 상환액을 따질 때",
            },
            {"title": "테스트 제목", "angle": "비교형"},
        )
        long_but_bad_article = "# 테스트 제목\n\n" + "판단 " * 400 + "\n\n## 상단 요약\n본문\n\n## FAQ\n본문\n\n## 마무리 결론\n본문"
        with patch.dict(os.environ, {"LONGTAIL_STYLE_GUARD_STRICT": "1"}), patch(
            "bunyang_longtail.codex_cli._resolve_codex_executable",
            return_value="/home/kj/.npm-global/bin/codex",
        ), patch(
            "bunyang_longtail.codex_cli._run_codex_exec",
            side_effect=[long_but_bad_article, long_but_bad_article, long_but_bad_article],
        ):
            with self.assertRaises(CodexCLIExecutionError) as exc_info:
                execute_text_job_codex_cli(
                    job_id=1001,
                    prompt_payload=prompt,
                    artifact_root=Path(self.tmpdir.name) / "codex_cli_artifacts",
                    workdir=Path(self.tmpdir.name),
                )
        self.assertEqual(exc_info.exception.code, "CODEX_CLI_STYLE_GUARD_FAILED")

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

    def test_run_bundle_explicit_empty_image_roles_does_not_queue_image_job(self) -> None:
        replenish_queue(self.db_path, min_queued=5, variants_per_cluster=1)
        with connect(self.db_path) as conn:
            variant_id = conn.execute("SELECT id FROM topic_variant WHERE status = 'queued' ORDER BY id ASC LIMIT 1").fetchone()[0]

        result = run_bundle(self.db_path, variant_id=variant_id, executor_mode="mock", image_roles=[])

        self.assertEqual(result["mode"], "already_complete")
        with connect(self.db_path) as conn:
            image_job_count = conn.execute(
                "SELECT COUNT(*) FROM generation_job WHERE bundle_id = ? AND worker_type = 'image'",
                (result["bundle"]["id"],),
            ).fetchone()[0]
            text_job_count = conn.execute(
                "SELECT COUNT(*) FROM generation_job WHERE bundle_id = ? AND worker_type = 'text'",
                (result["bundle"]["id"],),
            ).fetchone()[0]
        self.assertEqual(text_job_count, 1)
        self.assertEqual(image_job_count, 0)

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

    def test_prepare_page_retries_chatgpt_navigation_timeout(self) -> None:
        class DummyPage:
            def __init__(self) -> None:
                self.goto_calls: list[tuple[str, int | None]] = []
                self.waits: list[int] = []

            def set_default_timeout(self, _timeout: int) -> None:
                pass

            def goto(self, url: str, **kwargs):
                self.goto_calls.append((url, kwargs.get("timeout")))
                chatgpt_calls = [call for call in self.goto_calls if call[0] == "https://chatgpt.com/"]
                if url == "https://chatgpt.com/" and len(chatgpt_calls) == 1:
                    raise TimeoutError("first navigation timeout")
                return None

            def wait_for_timeout(self, ms: int) -> None:
                self.waits.append(ms)

        class DummyContext:
            def __init__(self) -> None:
                self.page = DummyPage()
                self.pages = [self.page]

            def new_page(self):
                return self.page

        artifact_dir = Path(self.tmpdir.name) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        context = DummyContext()
        with patch.dict(
            os.environ,
            {
                "GPT_WEB_NAVIGATION_TIMEOUT_SEC": "90",
                "GPT_WEB_NAVIGATION_RETRIES": "2",
                "GPT_WEB_NAVIGATION_RETRY_BACKOFF_SEC": "0",
            },
        ), patch("bunyang_longtail.gpt_web.PlaywrightTimeoutError", TimeoutError), patch("bunyang_longtail.gpt_web._take_artifacts"):
            _prepare_page(context, artifact_dir)
        chatgpt_calls = [call for call in context.page.goto_calls if call[0] == "https://chatgpt.com/"]
        self.assertEqual(len(chatgpt_calls), 2)
        self.assertTrue(all(timeout == 90000 for _url, timeout in chatgpt_calls))

    def test_prepare_page_raises_navigation_timeout_with_specific_code(self) -> None:
        class DummyPage:
            def set_default_timeout(self, _timeout: int) -> None:
                pass

            def goto(self, url: str, **_kwargs):
                if url == "https://chatgpt.com/":
                    raise TimeoutError("navigation timeout")
                return None

            def wait_for_timeout(self, _ms: int) -> None:
                pass

        class DummyContext:
            pages = [DummyPage()]

            def new_page(self):
                return self.pages[0]

        artifact_dir = Path(self.tmpdir.name) / "artifacts_timeout"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        with patch.dict(
            os.environ,
            {
                "GPT_WEB_NAVIGATION_TIMEOUT_SEC": "10",
                "GPT_WEB_NAVIGATION_RETRIES": "1",
            },
        ), patch("bunyang_longtail.gpt_web.PlaywrightTimeoutError", TimeoutError), patch("bunyang_longtail.gpt_web._take_artifacts"):
            with self.assertRaises(GptWebExecutionError) as exc_info:
                _prepare_page(DummyContext(), artifact_dir)
        self.assertEqual(exc_info.exception.code, "GPT_WEB_NAVIGATION_TIMEOUT")

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

    def test_try_start_new_chat_skips_click_on_blank_root_page(self) -> None:
        class DummyPage:
            url = "https://chatgpt.com/"

        clicked = {"value": False}

        class DummyLocator:
            def click(self, **_kwargs) -> None:
                clicked["value"] = True

        with patch("bunyang_longtail.gpt_web._count_locators", return_value=0), patch(
            "bunyang_longtail.gpt_web._first_visible", return_value=("a:has-text('새 채팅')", DummyLocator())
        ):
            _try_start_new_chat(DummyPage())
        self.assertFalse(clicked["value"])

    def test_submit_prompt_raises_when_send_does_not_start_conversation(self) -> None:
        class DummyKeyboard:
            def __init__(self) -> None:
                self.presses: list[str] = []

            def press(self, key: str) -> None:
                self.presses.append(key)

        class DummyPage:
            url = "https://chatgpt.com/"

            def __init__(self) -> None:
                self.keyboard = DummyKeyboard()

            def wait_for_timeout(self, _ms: int) -> None:
                pass

        class DummyLocator:
            def is_enabled(self, **_kwargs) -> bool:
                return True

            def click(self, **_kwargs) -> None:
                pass

        page = DummyPage()
        with patch("bunyang_longtail.gpt_web._first_visible", return_value=("button", DummyLocator())), patch(
            "bunyang_longtail.gpt_web._has_stop_button", return_value=False
        ), patch("bunyang_longtail.gpt_web._count_locators", return_value=0), patch(
            "bunyang_longtail.gpt_web.time.time", side_effect=[0, 10, 20, 30, 40, 50]
        ):
            with self.assertRaises(GptWebExecutionError) as exc_info:
                _submit_prompt(page, before_count=0)
        self.assertEqual(exc_info.exception.code, "GPT_WEB_SUBMIT_FAILED")
        self.assertEqual(page.keyboard.presses, ["Enter", "Control+Enter"])

    def test_submit_prompt_accepts_increased_conversation_turn_after_click(self) -> None:
        class DummyKeyboard:
            def press(self, _key: str) -> None:
                raise AssertionError("keyboard fallback should not be used")

        class DummyPage:
            url = "https://chatgpt.com/c/test"
            keyboard = DummyKeyboard()

            def wait_for_timeout(self, _ms: int) -> None:
                pass

        class DummyLocator:
            def __init__(self) -> None:
                self.clicked = False

            def is_enabled(self, **_kwargs) -> bool:
                return True

            def click(self, **_kwargs) -> None:
                self.clicked = True

        locator = DummyLocator()
        with patch("bunyang_longtail.gpt_web._first_visible", return_value=("button", locator)), patch(
            "bunyang_longtail.gpt_web._has_stop_button", return_value=False
        ), patch("bunyang_longtail.gpt_web._count_locators", return_value=2), patch(
            "bunyang_longtail.gpt_web.time.time", side_effect=[0, 1]
        ):
            _submit_prompt(DummyPage(), before_count=1)
        self.assertTrue(locator.clicked)

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

    def test_build_image_prompt_includes_fast_instruction_when_enabled(self) -> None:
        with patch.dict(os.environ, {"LONGTAIL_GPT_IMAGE_SPEED": "fast"}):
            prompt = build_image_prompt(
                prompt_text="깔끔한 청약 설명 이미지",
                title="청약 테스트",
                excerpt="요약",
                image_role="thumbnail",
            )
        self.assertIn("속도 우선 조건", prompt)
        self.assertIn("Fast/빠른 생성 모드", prompt)

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
        self.assertTrue(_looks_like_generated_image_src("https://sdmntprwestus3.oaiusercontent.com/files/abc/raw?sig=1"))
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
            "bunyang_longtail.gpt_web._new_generated_image_locator",
            return_value=DummyImageLocator(),
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

    def test_wait_for_image_response_does_not_save_old_image_when_only_text_changes(self) -> None:
        output_path = Path(self.tmpdir.name) / "old.png"
        artifact_dir = Path(self.tmpdir.name) / "artifacts_old"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        class DummyTextLocator:
            def inner_text(self, *_args, **_kwargs) -> str:
                return "이미지를 만들 수 없습니다."

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

        with patch("bunyang_longtail.gpt_web._last_locator", return_value=DummyTextLocator()), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=2
        ), patch(
            "bunyang_longtail.gpt_web._collect_generated_image_sources",
            return_value=["https://chatgpt.com/backend-api/estuary/content?id=file_old"],
        ), patch(
            "bunyang_longtail.gpt_web._new_generated_image_locator",
            return_value=None,
        ), patch("bunyang_longtail.gpt_web._has_stop_button", return_value=False), patch(
            "bunyang_longtail.gpt_web._take_artifacts"
        ):
            with self.assertRaises(GptWebExecutionError) as exc_info:
                _wait_for_image_response(
                    DummyPage(),
                    artifact_dir=artifact_dir,
                    output_path=output_path,
                    timeout_seconds=0,
                    before_count=1,
                    before_text="",
                    before_image_sources=["https://chatgpt.com/backend-api/estuary/content?id=file_old"],
                )
        self.assertEqual(exc_info.exception.code, "GPT_WEB_IMAGE_TIMEOUT")
        self.assertFalse(output_path.exists())

    def test_wait_for_image_response_detects_rate_limit_text(self) -> None:
        output_path = Path(self.tmpdir.name) / "rate_limited.png"
        artifact_dir = Path(self.tmpdir.name) / "artifacts_rate"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        class DummyTextLocator:
            def inner_text(self, *_args, **_kwargs) -> str:
                return "Too many requests. Please try again later."

        class DummyPage:
            def wait_for_timeout(self, _ms: int) -> None:
                return None

            def locator(self, _selector: str):
                return DummyTextLocator()

            def evaluate(self, *_args, **_kwargs):
                return None

        with patch("bunyang_longtail.gpt_web._last_locator", return_value=DummyTextLocator()), patch(
            "bunyang_longtail.gpt_web._count_locators", return_value=2
        ), patch(
            "bunyang_longtail.gpt_web._collect_generated_image_sources",
            return_value=[],
        ), patch(
            "bunyang_longtail.gpt_web._new_generated_image_locator",
            return_value=None,
        ), patch("bunyang_longtail.gpt_web._has_stop_button", return_value=False), patch(
            "bunyang_longtail.gpt_web._take_artifacts"
        ):
            with self.assertRaises(GptWebExecutionError) as exc_info:
                _wait_for_image_response(
                    DummyPage(),
                    artifact_dir=artifact_dir,
                    output_path=output_path,
                    timeout_seconds=30,
                    before_count=1,
                    before_text="",
                    before_image_sources=[],
                )
        self.assertEqual(exc_info.exception.code, "GPT_WEB_RATE_LIMIT")
        self.assertFalse(output_path.exists())

    def test_rate_limit_raises_without_global_cooldown(self) -> None:
        artifact_dir = Path(self.tmpdir.name) / "rate_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        cooldown_path = Path(self.tmpdir.name) / "cooldown.json"

        class DummyTextLocator:
            def inner_text(self, *_args, **_kwargs) -> str:
                return "요청이 너무 많습니다. 잠시 후 다시 시도하세요."

        class DummyPage:
            def locator(self, _selector: str):
                return DummyTextLocator()

        with patch("bunyang_longtail.gpt_web._take_artifacts"):
            with self.assertRaises(GptWebExecutionError) as exc_info:
                from bunyang_longtail.gpt_web import _raise_if_rate_limited

                _raise_if_rate_limited(DummyPage(), artifact_dir)

            self.assertEqual(exc_info.exception.code, "GPT_WEB_RATE_LIMIT")
            self.assertFalse(cooldown_path.exists())

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




    def test_fail_image_job_requeues_bundle(self) -> None:
        from bunyang_longtail.workers import create_bundle, fail_job, run_bundle

        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=2)
        with connect(self.db_path) as conn:
            variant_id = conn.execute("SELECT id FROM topic_variant WHERE status = 'queued' ORDER BY id ASC LIMIT 1").fetchone()[0]
        bundle = create_bundle(self.db_path, variant_id=variant_id)
        run_bundle(self.db_path, bundle_id=bundle["id"], executor_mode="mock")
        with connect(self.db_path) as conn:
            image_job = conn.execute(
                "SELECT id FROM generation_job WHERE bundle_id = ? AND worker_type = 'image' ORDER BY id DESC LIMIT 1",
                (bundle["id"],),
            ).fetchone()
        self.assertIsNotNone(image_job)
        fail_job(self.db_path, job_id=image_job["id"], error_code="TEST_IMAGE_FAIL", error_message="boom")
        with connect(self.db_path) as conn:
            refreshed = conn.execute("SELECT bundle_status FROM article_bundle WHERE id = ?", (bundle["id"],)).fetchone()
            last_text_job_id = conn.execute(
                "SELECT id FROM generation_job WHERE bundle_id = ? AND worker_type = 'text' ORDER BY id DESC LIMIT 1",
                (bundle["id"],),
            ).fetchone()[0]
        self.assertEqual(refreshed["bundle_status"], "queued")

        rerun = run_bundle(self.db_path, bundle_id=bundle["id"], executor_mode="mock")
        self.assertIn(rerun["mode"], {"mock_complete", "already_complete"})
        with connect(self.db_path) as conn:
            rerun_bundle = conn.execute(
                "SELECT bundle_status, primary_thumbnail_id FROM article_bundle WHERE id = ?",
                (bundle["id"],),
            ).fetchone()
            rerun_text_job_id = conn.execute(
                "SELECT id FROM generation_job WHERE bundle_id = ? AND worker_type = 'text' ORDER BY id DESC LIMIT 1",
                (bundle["id"],),
            ).fetchone()[0]
        self.assertEqual(rerun_text_job_id, last_text_job_id)
        self.assertIn(rerun_bundle["bundle_status"], {"queued", "bundled"})
        if rerun_bundle["bundle_status"] == "bundled":
            self.assertIsNotNone(rerun_bundle["primary_thumbnail_id"])



    def test_run_bundle_target_from_candidate_prefers_recovery_bundle_id(self) -> None:
        self.assertEqual(
            run_bundle_target_from_candidate({"id": 10, "bundle_id": 55}),
            {"bundle_id": 55},
        )
        self.assertEqual(
            run_bundle_target_from_candidate({"id": 10}),
            {"variant_id": 10},
        )
        self.assertEqual(
            run_bundle_target_from_candidate({"variant_id": 11}),
            {"variant_id": 11},
        )

    def test_select_publish_candidate_cleans_up_published_variant_queued_bundles(self) -> None:
        replenish_queue(self.db_path, min_queued=10, variants_per_cluster=2)
        with connect(self.db_path) as conn:
            variant_id = conn.execute(
                "SELECT id FROM topic_variant WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO article_bundle (variant_id, bundle_status, primary_draft_id, selected_image_ids_json, generation_strategy) VALUES (?, 'queued', 999, '[]', 'gpt_web_first')",
                (variant_id,),
            )
            bundle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "UPDATE topic_variant SET status = 'published' WHERE id = ?",
                (variant_id,),
            )
            conn.execute(
                "INSERT INTO publish_history (variant_id, draft_id, channel, published_title) VALUES (?, 999, 'naver_blog', 'cleanup')",
                (variant_id,),
            )
            candidate = select_publish_candidate(conn)
            cleaned = conn.execute("SELECT bundle_status FROM article_bundle WHERE id = ?", (bundle_id,)).fetchone()
        self.assertEqual(cleaned['bundle_status'], 'failed')
        self.assertIsNotNone(candidate)

if __name__ == "__main__":
    unittest.main()
