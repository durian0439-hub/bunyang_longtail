from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import bunyang_longtail.naver_bundle_publish as target  # noqa: E402
from bunyang_longtail.database import init_db, migrate_db  # noqa: E402
from bunyang_longtail.naver_bundle_publish import (  # noqa: E402
    _build_gpt_publish_image_plans,
    _persist_publish_result,
    build_publish_bundle,
    build_publish_title,
    markdown_to_html,
    parse_publish_sections,
)
from bunyang_longtail.planner import list_variants, replenish_queue, stats  # noqa: E402
from bunyang_longtail.workers import complete_text_job, queue_text_job, start_job  # noqa: E402


SAMPLE_ARTICLE = """1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까
상단 요약

30대 맞벌이도 1순위 조건을 맞춰 청약이 가능한 경우가 많습니다.

이 글에서 바로 답하는 질문

맞벌이면 1순위가 안 되는 건가
소득이 높으면 청약 자체가 끝인가

핵심 조건 정리

일반공급 1순위는 통장과 거주요건이 먼저입니다.
특별공급은 소득과 자산 기준이 핵심입니다.

헷갈리기 쉬운 예외

특별공급이 안 돼도 일반공급 1순위는 가능할 수 있습니다.

실전 예시 시나리오

맞벌이 신혼부부, 무주택, 통장 조건이 갖춰진 경우를 먼저 보시면 됩니다.

체크리스트

청약통장 가입기간이 충분한가
신청 주택형에 맞는 예치금이 있는가

FAQ

Q1. 맞벌이면 무조건 불리한가요?
아닙니다.

마무리 결론

최종 판단은 공고문과 청약홈으로 다시 확인하셔야 합니다.
"""

SPARSE_INSTITUTION_ARTICLE = """# 기관추천 특별공급 배우자 이력이 있을 때, 노부모 부양 세대도 가능할까

노부모 부양 세대 기준으로 결론부터 말씀드리면, 기관추천 특별공급는 배우자 이력이 있을 때 상황에서 먼저 확인할 기준이 분명합니다.

## 상단 요약
- 핵심 결론: 기관추천 특별공급와 소득기준를 같이 보면 판단이 빨라집니다.
- 체크 포인트: 가능여부 목적이면 조건, 일정, 리스크를 먼저 보셔야 합니다.

## FAQ
Q. 지금 바로 신청 판단이 가능합니까?
A. 공고문과 본인 조건을 대조한 뒤 결정하셔야 합니다.
"""

SPARSE_CASHFLOW_ARTICLE = """# 계약금 중도금 잔금 계약금이 빠듯할 때, 얼마가 필요한지 계산해보기

30대 맞벌이 기준으로 결론부터 말씀드리면, 계약금 중도금 잔금는 계약금이 빠듯할 때 상황에서 먼저 확인할 기준이 분명합니다.

## 상단 요약
- 핵심 결론: 계약금 중도금 잔금와 계약금을 같이 보면 판단이 빨라집니다.
- 체크 포인트: 계산 목적이면 조건, 일정, 리스크를 먼저 보셔야 합니다.

## 체크리스트
- 모집공고 기준일 확인
- 자격 유지 여부 확인
- 자금 계획과 중도금 일정 확인
"""

INLINE_TABLE_ARTICLE = """기관추천 특별공급과 일반공급, 노부모 부양 세대는 무엇이 다를까
상단 요약

노부모 부양 세대라고 해서 기관추천 특별공급이 자동으로 되는 것은 아닙니다.

핵심 조건 정리

핵심은 신청의 출발점이 다르다는 점입니다.
| 비교 항목 | 기관추천 특별공급 | 일반공급 |
| --- | --- | --- |
| 시작 조건 | 추천기관 대상 여부가 우선 | 청약통장, 지역, 순위가 우선 |
| 소득기준 | 적용되는 경우가 많아 먼저 확인 필요 | 보통 직접 핵심은 아니지만 공급유형별 예외 있음 |

FAQ

Q1. 노부모를 모시면 자동으로 기관추천 특별공급이 되나요?
아닙니다.

마무리 결론

최종 판단은 모집공고를 다시 확인하셔야 합니다.
"""

AUCTION_LONG_ARTICLE = """# 경매 체크리스트와 입찰 전 점검, 경매초보는 무엇부터 봐야 할까

경매초보가 첫 물건을 고르는 중이라면, 권리·점유·자금 확인 없이 입찰했다가 보증금 인수나 잔금 공백으로 손해가 커질 수 있습니다.

## 상단 요약

경매 체크리스트는 좋은 물건을 찾는 도구라기보다, 입찰하면 안 되는 물건을 먼저 제외하는 기준입니다.
공부 순서는 법원경매정보 → 매각물건명세서 → 현황조사서 → 감정평가서 → 등기부등본 → 전입세대열람 순서로 잡으면 흐름을 따라가기 쉽습니다.
입찰 전에는 권리 인수, 점유자 명도, 잔금·대출·세금·수리비까지 계산한 뒤 가능·보류·회피를 나눠야 합니다.

## 이 글에서 바로 답하는 질문

경매초보가 가장 많이 막히는 지점은 이 물건이 싼 건지보다 입찰해도 되는지입니다.
일반 매매와 경매는 판단 순서가 다르며, 낙찰 뒤 정해진 기한 안에 잔금을 내야 하고 점유자 문제도 직접 풀어야 할 수 있습니다.
그래서 경매 체크리스트는 가격표가 아니라 필터입니다.

## 핵심 조건 정리: 무엇부터 봐야 하는지

### 1. 법원경매정보에서 사건 기본값을 봅니다

처음에는 법원경매정보에서 사건번호, 매각기일, 최저가, 보증금, 물건 종류를 먼저 잡는 흐름이 낫습니다.
최저가가 낮아진 이유가 단순 유찰인지, 권리관계나 점유 문제 때문인지 구분해야 합니다.

### 2. 매각물건명세서에서 인수될 수 있는 권리를 확인합니다

경매초보가 가장 먼저 읽어야 할 서류는 매각물건명세서입니다.
여기에는 임차인, 점유자, 배당요구 여부, 인수될 수 있는 권리 등이 정리됩니다.
매수인에게 대항할 수 있는 임차인처럼 보이는 내용은 그냥 넘기면 안 됩니다.

## 헷갈리기 쉬운 예외: 여기서 비용이 커집니다

유찰이 반복되면 최저가가 낮아져 매력적으로 보입니다.
하지만 유찰 이유가 권리 인수, 점유 갈등, 대출 제한, 건물 하자라면 낮은 가격이 이미 리스크를 반영한 것일 수 있습니다.
임차인이 있어도 배당요구를 했고 보증금이 배당으로 정리될 가능성이 높다면 큰 문제가 아닐 수 있습니다.

## 실전 예시 시나리오

최저가가 시세보다 낮은 아파트라도 선순위 임차인 보증금이 남거나 점유자가 협조하지 않을 가능성이 크면 초보자는 보류가 맞습니다.
반대로 권리 인수 위험이 낮고 잔금대출과 명도 비용까지 계산이 끝난 물건이라면 입찰가 상한을 정한 뒤 접근할 수 있습니다.

## 체크리스트

법원경매정보에서 사건번호와 매각기일을 확인했는가
매각물건명세서에서 인수 권리와 임차인 정보를 확인했는가
현황조사서와 전입세대열람으로 점유 상태를 맞춰 봤는가
등기부등본에서 말소기준권리보다 앞선 권리를 확인했는가
잔금대출, 취득세, 명도 비용까지 현금표에 넣었는가

## FAQ

Q1. 경매초보는 무엇부터 봐야 하나요?
A. 가격보다 법원경매정보, 매각물건명세서, 점유 상태, 자금 계획 순서로 보시는 것이 안전합니다.

Q2. 유찰이 많으면 좋은 물건인가요?
A. 아닐 수 있습니다. 왜 유찰됐는지 권리관계와 점유, 대출 가능성까지 확인하셔야 합니다.

## 마무리 결론

경매초보에게 첫 목표는 낙찰이 아니라 손실을 피하는 것입니다. 입찰 전에는 권리, 점유, 자금, 명도 리스크를 한 장 체크리스트로 정리하고 하나라도 설명이 안 되면 보류하는 기준을 먼저 세우셔야 합니다.
"""


class TestNaverBundlePublish(unittest.TestCase):
    def test_prod_cron_runs_tax_after_cheongyak_and_auction(self) -> None:
        script = (ROOT / "scripts" / "run_longtail_publish_prod.sh").read_text(encoding="utf-8")
        cheongyak_pos = script.index("'domain': 'cheongyak'")
        auction_pos = script.index("'domain': 'auction'")
        tax_pos = script.index("'domain': 'tax'")
        loop_pos = script.index("for config in DOMAIN_CONFIGS:")

        self.assertLess(cheongyak_pos, auction_pos)
        self.assertLess(auction_pos, tax_pos)
        self.assertLess(tax_pos, loop_pos)
        self.assertIn("LONGTAIL_TAX_NAVER_CATEGORY_NO", script)
        self.assertIn("'category_no': os.environ.get('LONGTAIL_TAX_NAVER_CATEGORY_NO', '18').strip()", script)

    def test_auction_publish_bundle_uses_auction_disclaimer_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_publish_bundle(
                bundle_id=7,
                variant_title="경매 권리분석과 말소기준권리, 경매초보는 무엇부터 봐야 할까",
                article_markdown="""# 경매 권리분석과 말소기준권리, 경매초보는 무엇부터 봐야 할까

## 상단 요약
경매초보라면 입찰 전에 권리와 점유를 먼저 확인해야 합니다.

## FAQ
Q. 바로 입찰해도 되나요?
A. 서류와 현장을 다시 확인해야 합니다.
""",
                output_root=Path(temp_dir),
                image_provider="local",
                domain="auction",
            )

            meta = json.loads(Path(result.meta_path).read_text(encoding="utf-8"))
            self.assertEqual(meta["domain"], "auction")
            self.assertIn("법원경매정보", result.markdown)
            self.assertIn("## 경매 입찰 전 체크리스트", result.markdown)
            self.assertNotIn("청약홈", result.markdown)
            self.assertNotIn("입주자모집공고", result.markdown)
            self.assertIn("경매권리분석", result.tags)
            self.assertIn("말소기준권리", result.tags)
            self.assertIn("https://link.coupang.com/a/espLX0", result.markdown)
            self.assertNotIn("https://link.coupang.com/a/esfszm", result.markdown)
            self.assertNotIn("청약정보", result.tags)

    def test_auction_long_article_with_colon_headings_is_not_replaced_by_cheongyak_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_publish_bundle(
                bundle_id=8,
                variant_title="경매 체크리스트와 입찰 전 점검, 경매초보는 무엇부터 봐야 할까",
                article_markdown=AUCTION_LONG_ARTICLE,
                output_root=Path(temp_dir),
                image_provider="local",
                domain="auction",
            )

            self.assertIn("매각물건명세서에서 인수될 수 있는 권리를 확인합니다", result.markdown)
            self.assertIn("## 경매 핵심 판단 기준", result.markdown)
            self.assertIn("## 경매 입찰 전 체크리스트", result.markdown)
            self.assertNotIn("청약홈", result.markdown)
            self.assertNotIn("입주자모집공고", result.markdown)

    def test_auction_publish_validation_blocks_cheongyak_terms(self) -> None:
        with self.assertRaisesRegex(ValueError, "청약 도메인 용어"):
            target._validate_domain_publish_markdown(
                "# 경매 글\n\n최종 확인은 청약홈과 입주자모집공고로 합니다.",
                domain="auction",
            )

    def test_persist_publish_result_marks_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(db_path)
            migrate_db(db_path)
            replenish_queue(db_path, min_queued=5, variants_per_cluster=1)
            variant_id = list_variants(db_path, status="queued", limit=1)[0]["id"]
            queued = queue_text_job(db_path, variant_id=variant_id)
            start_job(db_path, queued["job_id"])
            complete_text_job(
                db_path,
                job_id=queued["job_id"],
                article_markdown="# 본문\n\n결론 먼저 씁니다.",
            )

            _persist_publish_result(
                db_path,
                {"variant_id": variant_id, "title": "후보 제목 전체 문장"},
                {"ok": True, "current_url": "https://blog.naver.com/example/1", "published_title": "실제 발행 제목 전체 문장"},
            )

            summary = stats(db_path)
            self.assertEqual(summary["publish_history"], 1)
            status_map = {row["status"]: row["cnt"] for row in summary["by_status"]}
            self.assertEqual(status_map.get("published"), 1)
            with sqlite3.connect(db_path) as conn:
                published_title = conn.execute("SELECT published_title FROM publish_history").fetchone()[0]
            self.assertEqual(published_title, "실제 발행 제목 전체 문장")

    def test_load_bundle_article_returns_latest_related_link_in_same_category_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(db_path)
            migrate_db(db_path)

            def add_bundle(
                conn: sqlite3.Connection,
                *,
                domain: str,
                semantic_key: str,
                title: str,
                published_url: str | None = None,
                published_at: str | None = None,
            ) -> int:
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword,
                        audience, search_intent, scenario, priority, outline_json, policy_json
                    ) VALUES (?, ?, '기초', ?, '', '청약초보', '조건정리', '처음 준비할 때', 10, '{}', '{}')
                    """,
                    (domain, semantic_key, title),
                )
                cluster_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO topic_variant (
                        cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status
                    ) VALUES (?, ?, '체크리스트형', ?, ?, 80, '{}', 'drafted')
                    """,
                    (cluster_id, f"variant-{semantic_key}", title, f"slug-{semantic_key}"),
                )
                variant_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute("INSERT INTO article_bundle (variant_id, bundle_status) VALUES (?, 'bundled')", (variant_id,))
                bundle_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO article_draft (bundle_id, variant_id, title, article_markdown)
                    VALUES (?, ?, ?, ?)
                    """,
                    (bundle_id, variant_id, title, f"# {title}\n\n본문"),
                )
                draft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute("UPDATE article_bundle SET primary_draft_id = ? WHERE id = ?", (draft_id, bundle_id))
                if published_url and published_at:
                    conn.execute(
                        """
                        INSERT INTO publish_history (
                            bundle_id, variant_id, draft_id, channel, target_account,
                            publish_mode, published_title, naver_url, published_at, result_json
                        ) VALUES (?, ?, ?, 'naver_blog', 'default', 'publish', ?, ?, ?, '{}')
                        """,
                        (bundle_id, variant_id, draft_id, title, published_url, published_at),
                    )
                return bundle_id

            with sqlite3.connect(db_path) as conn:
                current_bundle_id = add_bundle(
                    conn,
                    domain="cheongyak",
                    semantic_key="current-cheongyak",
                    title="현재 분양 글",
                )
                add_bundle(
                    conn,
                    domain="cheongyak",
                    semantic_key="old-cheongyak",
                    title="오래된 분양 글",
                    published_url="https://blog.naver.com/example/old-cheongyak",
                    published_at="2026-04-24 01:00:00",
                )
                add_bundle(
                    conn,
                    domain="cheongyak",
                    semantic_key="new-cheongyak",
                    title="가장 최근 분양 글",
                    published_url="https://blog.naver.com/example/new-cheongyak",
                    published_at="2026-04-25 01:00:00",
                )
                add_bundle(
                    conn,
                    domain="auction",
                    semantic_key="new-auction",
                    title="가장 최근 경매 글",
                    published_url="https://blog.naver.com/example/new-auction",
                    published_at="2026-04-25 02:00:00",
                )

            article = target.load_bundle_article(db_path, current_bundle_id)

            self.assertEqual(
                article["related_links"],
                [
                    {
                        "category_name": "How To 분양",
                        "title": "가장 최근 분양 글",
                        "url": "https://blog.naver.com/example/new-cheongyak",
                    }
                ],
            )

    def test_load_bundle_article_uses_variant_title_not_truncated_draft_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            init_db(db_path)
            migrate_db(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword,
                        audience, search_intent, scenario, priority, outline_json, policy_json
                    ) VALUES ('cheongyak', 'title-source-test', '자금', '계약금', '', '30대 맞벌이', '실수방지', '월 상환액을 따질 때', 10, '{}', '{}')
                    """
                )
                cluster_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                full_title = "계약금 중도금 잔금 월 상환액을 따질 때, 30대 맞벌이가 놓치면 탈락하는 포인트"
                conn.execute(
                    """
                    INSERT INTO topic_variant (
                        cluster_id, variant_key, angle, title, slug, seo_score, prompt_json, status
                    ) VALUES (?, 'title-source-variant', '실수방지형', ?, 'title-source-slug', 80, '{}', 'drafted')
                    """,
                    (cluster_id, full_title),
                )
                variant_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute("INSERT INTO article_bundle (variant_id, bundle_status) VALUES (?, 'bundled')", (variant_id,))
                bundle_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO article_draft (bundle_id, variant_id, title, article_markdown)
                    VALUES (?, ?, '계약금 중도금 잔금 월 상환액을 따질 때 30대 맞벌이가 놓치면 탈락하는 포', '# 본문')
                    """,
                    (bundle_id, variant_id),
                )
                draft_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute("UPDATE article_bundle SET primary_draft_id = ? WHERE id = ?", (draft_id, bundle_id))

            article = target.load_bundle_article(db_path, bundle_id)

            self.assertEqual(article["title"], full_title)
            self.assertTrue(article["title"].endswith("포인트"))

    def test_publish_bundle_script_forwards_category_args(self) -> None:
        script_path = ROOT / "scripts" / "publish_bundle_to_naver.py"
        spec = importlib.util.spec_from_file_location("publish_bundle_script", script_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = False
        fake_conn.execute.return_value.fetchone.return_value = (999,)

        with patch.object(
            module,
            "publish_bundle_to_naver",
            return_value={"status": "ok"},
        ) as mocked_publish, patch.object(module, "connect", return_value=fake_conn), patch.object(
            module,
            "is_recent_publish_conflict",
            return_value=None,
        ), patch.object(
            sys,
            "argv",
            [
                "publish_bundle_to_naver.py",
                "--db",
                "test.sqlite3",
                "--bundle-id",
                "20",
                "--mode",
                "publish",
                "--category-no",
                "16",
                "--category-name",
                "How To 분양",
            ],
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        kwargs = mocked_publish.call_args.kwargs
        self.assertEqual(kwargs["category_no"], "16")
        self.assertEqual(kwargs["category_name"], "How To 분양")

    def test_publish_bundle_to_naver_blocks_local_fallback_when_gpt_requested(self) -> None:
        with patch.object(
            target,
            "load_bundle_article",
            return_value={
                "title": "경매 체크리스트와 입찰 전 점검, 경매초보는 무엇부터 봐야 할까",
                "article_markdown": AUCTION_LONG_ARTICLE,
                "related_links": [],
                "domain": "auction",
                "variant_id": 1,
            },
        ), patch.object(
            target,
            "build_publish_bundle",
            return_value=SimpleNamespace(
                apt_id="longtail-bundle-gpt-fallback-test",
                title="경매 체크리스트와 입찰 전 점검 경매초보는 무엇부터 봐야 할까",
                body_html="<p>본문</p>",
                images=[],
                markdown="본문",
                tags=["경매"],
                meta_path="/tmp/meta.json",
                image_provider="local",
                image_provider_requested="gpt_web",
                image_provider_fallback_from="gpt_web",
                image_provider_fallback_reason="timeout",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "GPT 이미지 생성이 실패"):
                target.publish_bundle_to_naver(
                    db_path="test.sqlite3",
                    bundle_id=1,
                    output_root="/tmp/out",
                    mode="private",
                    image_provider="gpt_web",
                    category_no="17",
                    category_name="How To 경매",
                )

    def test_publish_bundle_to_naver_restores_category_env_after_publish(self) -> None:
        fake_src = types.ModuleType("src")
        fake_publisher = types.ModuleType("src.publisher")
        fake_naver = types.ModuleType("src.publisher.naver_playwright")
        seen_env: dict[str, str | None] = {}

        def fake_publish(apt_id, title, body_html, images, **kwargs):
            seen_env["category_no"] = os.environ.get("NAVER_BLOG_CATEGORY_NO")
            seen_env["category_name"] = os.environ.get("NAVER_BLOG_CATEGORY_NAME")
            out_dir = Path(kwargs["out"])
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{apt_id}.json").write_text(
                json.dumps({"status": "ok", "current_url": "https://blog.naver.com/example/17"}),
                encoding="utf-8",
            )
            return True

        fake_src.publisher = fake_publisher
        fake_publisher.naver_playwright = fake_naver
        fake_naver.publish = fake_publish
        previous_no = os.environ.get("NAVER_BLOG_CATEGORY_NO")
        previous_name = os.environ.get("NAVER_BLOG_CATEGORY_NAME")
        os.environ["NAVER_BLOG_CATEGORY_NO"] = "99"
        os.environ["NAVER_BLOG_CATEGORY_NAME"] = "Old Category"
        try:
            with patch.dict(
                sys.modules,
                {
                    "src": fake_src,
                    "src.publisher": fake_publisher,
                    "src.publisher.naver_playwright": fake_naver,
                },
            ), patch.object(
                target,
                "load_bundle_article",
                return_value={
                    "title": "경매 체크리스트와 입찰 전 점검, 경매초보는 무엇부터 봐야 할까",
                    "article_markdown": AUCTION_LONG_ARTICLE,
                    "related_links": [],
                    "domain": "auction",
                    "variant_id": 1,
                },
            ), patch.object(
                target,
                "build_publish_bundle",
                return_value=SimpleNamespace(
                    apt_id="longtail-bundle-env-test",
                    title="경매 체크리스트와 입찰 전 점검 경매초보는 무엇부터 봐야 할까",
                    body_html="<p>본문</p>",
                    images=[],
                    markdown="본문",
                    tags=["경매"],
                    meta_path="/tmp/meta.json",
                    image_provider="local",
                    image_provider_requested="local",
                    image_provider_fallback_from=None,
                    image_provider_fallback_reason=None,
                ),
            ), patch.object(target, "_persist_publish_result"):
                result = target.publish_bundle_to_naver(
                    db_path="test.sqlite3",
                    bundle_id=1,
                    output_root="/tmp/out",
                    mode="private",
                    category_no="17",
                    category_name="How To 경매",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(seen_env["category_no"], "17")
            self.assertEqual(seen_env["category_name"], "How To 경매")
            self.assertEqual(os.environ.get("NAVER_BLOG_CATEGORY_NO"), "99")
            self.assertEqual(os.environ.get("NAVER_BLOG_CATEGORY_NAME"), "Old Category")
        finally:
            if previous_no is None:
                os.environ.pop("NAVER_BLOG_CATEGORY_NO", None)
            else:
                os.environ["NAVER_BLOG_CATEGORY_NO"] = previous_no
            if previous_name is None:
                os.environ.pop("NAVER_BLOG_CATEGORY_NAME", None)
            else:
                os.environ["NAVER_BLOG_CATEGORY_NAME"] = previous_name

    def test_is_visually_blank_publish_image_detects_white_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blank.png"
            Image.new("RGB", (800, 800), (250, 250, 250)).save(path)
            self.assertTrue(target._is_visually_blank_publish_image(path))

    def test_is_visually_blank_publish_image_allows_chalkboard_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chalk.png"
            Image.new("RGB", (800, 800), (20, 60, 40)).save(path)
            self.assertFalse(target._is_visually_blank_publish_image(path))

    def test_parse_publish_sections_returns_expected_order(self) -> None:
        original_title, sections = parse_publish_sections(SAMPLE_ARTICLE, title_hint="1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까")
        self.assertEqual(original_title, "1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까")
        self.assertEqual(len(sections), 8)
        self.assertEqual(sections[0].publish_heading, "30초 결론")
        self.assertEqual(sections[2].publish_heading, "일반공급 1순위 조건, 먼저 확인할 것")
        self.assertEqual(sections[-1].publish_heading, "최종 정리")

    def test_build_publish_title_rewrites_primary_topic_for_seo(self) -> None:
        title = build_publish_title("1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까")
        self.assertIn("30대 맞벌이 청약 1순위 조건", title)
        self.assertIn("뭐가 다를까", title)

    def test_build_publish_title_does_not_cut_sentence_tail(self) -> None:
        title = build_publish_title("무료 경매 사이트와 법원경매정보, 경매초보는 무엇부터 봐야 할까")
        self.assertEqual(title, "무료 경매 사이트와 법원경매정보, 경매초보는 무엇부터 봐야 할까")
        self.assertTrue(title.endswith("봐야 할까"))

    def test_build_publish_bundle_writes_markdown_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_publish_bundle(
                bundle_id=1,
                variant_title="1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까",
                article_markdown=SAMPLE_ARTICLE,
                output_root=tmpdir,
            )
            self.assertTrue(bundle.markdown.startswith("# "))
            self.assertEqual(bundle.markdown.count("[[IMAGE:"), 4)
            self.assertEqual(len(bundle.images), 4)
            self.assertTrue(all(Path(path).exists() for path in bundle.images))
            self.assertTrue(Path(tmpdir, "body.md").exists())
            meta = json.loads(Path(bundle.meta_path).read_text(encoding="utf-8"))
            self.assertEqual(meta["bundle_id"], 1)
            self.assertEqual(len(meta["images"]), 4)
            self.assertEqual(meta["image_provider"], "local")
            self.assertEqual(bundle.image_provider, "local")
            self.assertTrue(all(Path(path).is_absolute() for path in meta["images"]))
            self.assertIn("30대 맞벌이 청약은 막연히 불리한 게임이 아니라, 어떤 공급에서 판단하느냐에 따라 결과가 크게 갈립니다.", bundle.markdown)
            self.assertIn("## 청약 1순위 FAQ", bundle.markdown)

    def test_build_publish_markdown_does_not_truncate_intro_with_ellipsis(self) -> None:
        article = """가점제 규제지역 청약 전 1주택 갈아타기 준비자가 놓치면 탈락하는 포인트
상단 요약

1주택 갈아타기 준비자도 규제지역 청약이 아예 불가한 것은 아니지만, 가점제에서는 대체로 불리하고 추첨제에서 가능 여부를 따져보는 쪽이 현실적입니다. 문제는 신청보다 당첨 뒤입니다. 기존 주택 처분 조건, 세대 기준, 자금 일정을 하나라도 놓치면 부적격과 계약 포기가 생길 수 있습니다.

FAQ

Q1. 바로 신청해도 되나요?
아닙니다. 공고문과 처분 조건을 먼저 확인해야 합니다.
"""
        _, sections = parse_publish_sections(
            article,
            title_hint="가점제 규제지역 청약 전 1주택 갈아타기 준비자가 놓치면 탈락하는 포인트",
        )
        markdown = target.build_publish_markdown(
            title="가점제 규제지역 청약 전 1주택 갈아타기 준비자가 놓치면 탈락하는 포인트",
            sections=sections,
            assets=[],
        )

        self.assertNotIn("...", markdown)
        self.assertIn("기존 주택 처분 조건, 세대 기준, 자금 일정을 하나라도 놓치면", markdown)

    def test_build_publish_markdown_formats_related_link_on_separate_lines(self) -> None:
        _, sections = parse_publish_sections(
            SPARSE_CASHFLOW_ARTICLE,
            title_hint="분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?",
        )
        markdown = target.build_publish_markdown(
            title="분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?",
            sections=sections,
            assets=[],
            related_links=[
                {
                    "category_name": "How To 분양",
                    "title": "규제지역 거주의무와 재당첨 제한 1주택 갈아타기",
                    "url": "https://blog.naver.com/example/recent",
                }
            ],
        )

        self.assertIn(
            "관련 글\nHow To 분양 최신 글\n규제지역 거주의무와 재당첨 제한 1주택 갈아타기\nhttps://blog.naver.com/example/recent",
            markdown,
        )
        self.assertNotIn("[규제지역 거주의무와 재당첨 제한 1주택 갈아타기](https://blog.naver.com/example/recent)", markdown)

    def test_build_publish_markdown_omits_related_placeholder_when_empty(self) -> None:
        _, sections = parse_publish_sections(
            SPARSE_CASHFLOW_ARTICLE,
            title_hint="분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?",
        )
        markdown = target.build_publish_markdown(
            title="분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?",
            sections=sections,
            assets=[],
            related_links=[],
        )

        self.assertNotIn("다음 글부터 관련 글이 자동으로 연결됩니다", markdown)
        self.assertNotIn("관련 글\n-", markdown)

    def test_default_tags_expands_cheongyak_auction_and_tax_to_naver_limit(self) -> None:
        cheongyak_tags = target.default_tags(
            "분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?",
            domain="cheongyak",
        )
        auction_tags = target.default_tags(
            "경매 권리분석과 말소기준권리, 잔금 대출까지 입찰 전 점검",
            domain="auction",
        )
        tax_tags = target.default_tags(
            "일시적 2주택 양도세, 1주택 갈아타기 준비자가 먼저 볼 기준",
            domain="tax",
        )

        self.assertEqual(len(cheongyak_tags), target.NAVER_TAG_LIMIT)
        self.assertEqual(len(auction_tags), target.NAVER_TAG_LIMIT)
        self.assertEqual(len(tax_tags), target.NAVER_TAG_LIMIT)
        self.assertEqual(len(cheongyak_tags), len(set(cheongyak_tags)))
        self.assertEqual(len(auction_tags), len(set(auction_tags)))
        self.assertEqual(len(tax_tags), len(set(tax_tags)))
        self.assertIn("분양계약금", cheongyak_tags)
        self.assertIn("중도금대출", cheongyak_tags)
        self.assertIn("말소기준권리", auction_tags)
        self.assertIn("경락잔금대출", auction_tags)
        self.assertIn("양도세계산", tax_tags)
        self.assertIn("홈택스", tax_tags)
        self.assertTrue(all(" " not in tag for tag in cheongyak_tags + auction_tags + tax_tags))

    def test_build_publish_bundle_tax_domain_uses_tax_copy_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_publish_bundle(
                bundle_id=123,
                variant_title="일시적 2주택 양도세, 1주택 갈아타기 준비자가 먼저 볼 기준",
                article_markdown="# 세금 초안\n\n짧은 초안",
                output_root=tmpdir,
                image_provider="local",
                domain="tax",
            )
            meta = json.loads(Path(bundle.meta_path).read_text(encoding="utf-8"))
            self.assertEqual(meta["domain"], "tax")
            self.assertIn("부동산 세금", bundle.markdown)
            self.assertIn("홈택스", bundle.markdown)
            self.assertIn("위택스", bundle.markdown)
            self.assertIn("양도세계산", bundle.tags)
            self.assertIn("홈택스", bundle.tags)
            self.assertNotIn("입주자모집공고", bundle.markdown)

    def test_build_publish_bundle_falls_back_to_local_when_gpt_image_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            target,
            "_render_gpt_publish_assets",
            side_effect=RuntimeError("GPT 이미지 생성 실패: 요약 카드, provider=gpt_web, 응답 없음"),
        ):
            bundle = build_publish_bundle(
                bundle_id=99,
                variant_title="기관추천 특별공급 배우자 이력이 있을 때, 노부모 부양 세대도 가능할까",
                article_markdown=SPARSE_INSTITUTION_ARTICLE,
                output_root=tmpdir,
                image_provider="gpt_web",
            )
            meta = json.loads(Path(bundle.meta_path).read_text(encoding="utf-8"))
            self.assertEqual(bundle.image_provider, "local")
            self.assertEqual(bundle.image_provider_requested, "gpt_web")
            self.assertEqual(bundle.image_provider_fallback_from, "gpt_web")
            self.assertIn("GPT 이미지 생성 실패", bundle.image_provider_fallback_reason or "")
            self.assertEqual(meta["image_provider"], "local")
            self.assertEqual(meta["image_provider_requested"], "gpt_web")
            self.assertEqual(meta["image_provider_fallback_from"], "gpt_web")
            self.assertIn("GPT 이미지 생성 실패", meta["image_provider_fallback_reason"])
            self.assertTrue(all(Path(path).exists() for path in bundle.images))

    def test_parse_publish_sections_supports_markdown_headings(self) -> None:
        original_title, sections = parse_publish_sections(
            SPARSE_INSTITUTION_ARTICLE,
            title_hint="기관추천 특별공급 배우자 이력이 있을 때, 노부모 부양 세대도 가능할까",
        )
        self.assertEqual(original_title, "기관추천 특별공급 배우자 이력이 있을 때, 노부모 부양 세대도 가능할까")
        self.assertGreaterEqual(len(sections), 2)
        self.assertEqual(sections[0].publish_heading, "30초 결론")

    def test_build_publish_bundle_expands_sparse_institution_article(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_publish_bundle(
                bundle_id=2,
                variant_title="기관추천 특별공급 배우자 이력이 있을 때, 노부모 부양 세대도 가능할까",
                article_markdown=SPARSE_INSTITUTION_ARTICLE,
                output_root=tmpdir,
            )
            self.assertEqual(bundle.title, "기관추천 특별공급 배우자 주택 이력 있을 때, 노부모 부양 세대 가능 여부 정리")
            self.assertEqual(len(bundle.images), 3)
            self.assertIn("## 기관추천 신청 전 체크리스트", bundle.markdown)
            self.assertIn("기관추천 특별공급", bundle.markdown)
            self.assertIn("[[IMAGE:2]]", bundle.markdown)

    def test_build_publish_bundle_expands_sparse_cashflow_article(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = build_publish_bundle(
                bundle_id=3,
                variant_title="계약금 중도금 잔금 계약금이 빠듯할 때, 얼마가 필요한지 계산해보기",
                article_markdown=SPARSE_CASHFLOW_ARTICLE,
                output_root=tmpdir,
            )
            self.assertEqual(bundle.title, "분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?")
            self.assertEqual(len(bundle.images), 5)
            self.assertIn("필요 자기자본 =", bundle.markdown)
            self.assertIn("## 분양 계약 전 체크리스트", bundle.markdown)
            self.assertIn("분양가 6억원이면 계약금 6천만원으로 끝나지 않습니다.", bundle.markdown)
            self.assertIn("[[IMAGE:4]]", bundle.markdown)

    def test_markdown_to_html_renders_faq_questions_as_large_subheadings(self) -> None:
        html = markdown_to_html(
            "# 제목\n\n## 자주 묻는 질문\n\n### Q1. FAQ 질문도 제목처럼 보여야 하나요?\n네, 해시가 아니라 큰 글자로 보여야 합니다.\n\nQ2. 번호형 질문도 크게 보여야 하나요?\n네, FAQ 질문은 크게 보여야 합니다.\n\n---\n"
        )

        self.assertIn("<h3>Q1. <strong>FAQ</strong> 질문도 제목처럼 보여야 하나요?</h3>", html)
        self.assertIn("<h3>Q2. 번호형 질문도 크게 보여야 하나요?</h3>", html)
        self.assertIn("<hr>", html)
        self.assertNotIn("### Q1.", html)
        self.assertNotIn("<p>Q2. 번호형 질문도 크게 보여야 하나요?</p>", html)

    def test_inline_markdown_table_is_converted_to_visual_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, sections = parse_publish_sections(
                INLINE_TABLE_ARTICLE,
                title_hint="기관추천 특별공급과 일반공급, 노부모 부양 세대는 무엇이 다를까",
            )
            cleaned_sections, inline_specs = target._extract_inline_markdown_table_specs(sections)
            table_assets = target._render_inline_table_assets_local(
                title="기관추천 특별공급과 일반공급 노부모 부양 세대는 무엇이 다를까",
                inline_table_specs=inline_specs,
                output_dir=tmpdir,
            )

            self.assertEqual(len(inline_specs), 1)
            self.assertEqual(len(table_assets), 1)
            self.assertEqual(inline_specs[0]["slot"], "__inline_table__:t01")
            self.assertTrue(Path(table_assets[0].path).exists())
            self.assertTrue(any("[[INLINE_TABLE:" in line for section in cleaned_sections for line in section.lines))
            self.assertFalse(any("| --- |" in line for section in cleaned_sections for line in section.lines))

            markdown = target.build_publish_markdown(
                title="기관추천 특별공급과 일반공급 노부모 부양 세대는 무엇이 다를까",
                sections=cleaned_sections,
                assets=table_assets,
            )
            self.assertIn("[[IMAGE:1]]", markdown)
            self.assertNotIn("| 비교 항목 |", markdown)
            self.assertNotIn("| --- |", markdown)

    def test_inline_markdown_table_creates_gpt_decision_board_plan(self) -> None:
        _, sections = parse_publish_sections(
            INLINE_TABLE_ARTICLE,
            title_hint="기관추천 특별공급과 일반공급, 노부모 부양 세대는 무엇이 다를까",
        )
        cleaned_sections, inline_specs = target._extract_inline_markdown_table_specs(sections)
        plans = _build_gpt_publish_image_plans(
            "기관추천 특별공급과 일반공급 노부모 부양 세대는 무엇이 다를까",
            cleaned_sections,
            inline_table_specs=inline_specs,
        )
        inline_plan = next(plan for plan in plans if plan.slot == "__inline_table__:t01")
        self.assertEqual(inline_plan.kind, "decision_board")
        self.assertIn("Do not render a spreadsheet", inline_plan.prompt_text)
        self.assertIn("hand-drawn chalkboard decision board", inline_plan.prompt_text)

    def test_render_table_image_uses_chalkboard_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "table.png"
            target.render_table_image(
                title="기관추천 특별공급과 일반공급 노부모 부양 세대는 무엇이 다를까",
                label="비교 표",
                headers=["비교 항목", "기관추천 특별공급", "일반공급"],
                rows=[["시작 조건", "추천기관 대상 여부", "청약통장과 순위"]],
                output_path=path,
            )
            self.assertTrue(path.exists())
            with Image.open(path) as image:
                avg = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
            self.assertLess(sum(avg) / 3, 120)
            self.assertGreater(avg[1], avg[0])

    def test_build_gpt_publish_image_plans_ranking_matches_expected_slots(self) -> None:
        _, sections = parse_publish_sections(SAMPLE_ARTICLE, title_hint="1순위 조건 기준이 헷갈릴 때, 30대 맞벌이도 가능할까")
        plans = _build_gpt_publish_image_plans(
            "30대 맞벌이 청약 1순위 조건, 일반공급과 특별공급 뭐가 다를까?",
            sections,
        )
        self.assertEqual(len(plans), 11)
        self.assertEqual(plans[0].slot, "lead")
        self.assertEqual(plans[1].slot, "30초 결론")
        self.assertEqual(plans[2].slot, "30대 맞벌이 청약 1순위에서 가장 많이 묻는 질문")
        self.assertEqual(plans[3].slot, "일반공급 1순위 조건, 먼저 확인할 것")
        self.assertEqual(plans[-1].slot, "청약 1순위 신청 전 체크리스트::before")
        self.assertEqual(plans[0].image_role, "thumbnail")
        self.assertIn("Use the uploaded image as a visual reference for mood and composition.", plans[1].prompt_text)
        self.assertIn("Create a Korean summary board", plans[1].prompt_text)
        self.assertIn("Do not use a 4-panel grid", plans[1].prompt_text)
        self.assertIn("public service campaign poster", plans[1].prompt_text)

    def test_build_gpt_publish_image_plans_auction_matches_cheongyak_structure(self) -> None:
        _, sections = parse_publish_sections(
            AUCTION_LONG_ARTICLE,
            title_hint="경매 체크리스트와 입찰 전 점검, 경매초보는 무엇부터 봐야 할까",
        )
        plans = _build_gpt_publish_image_plans(
            "경매 체크리스트와 입찰 전 점검 경매초보는 무엇부터 봐야 할까",
            sections,
        )
        self.assertEqual(len(plans), 11)
        self.assertEqual(plans[0].slot, "lead")
        self.assertEqual(plans[1].slot, "30초 결론")
        self.assertEqual(plans[2].slot, "입찰 전 먼저 확인할 것")
        self.assertEqual(plans[3].slot, "경매 핵심 판단 기준")
        self.assertEqual(plans[-1].slot, "경매 입찰 전 체크리스트::before")
        self.assertEqual(plans[0].image_role, "thumbnail")
        self.assertTrue(all("청약홈" not in plan.prompt_text for plan in plans))
        self.assertTrue(all("입주자모집공고" not in plan.prompt_text for plan in plans))
        self.assertTrue(any("Create a Korean summary board" in plan.prompt_text for plan in plans))
        self.assertTrue(any("Create a Korean checklist board" in plan.prompt_text for plan in plans))
        self.assertTrue(any("Create a Korean comparison board" in plan.prompt_text for plan in plans))

    def test_build_gpt_publish_image_plans_cashflow_uses_chalkboard_prompt(self) -> None:
        prepared_title = "분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?"
        _, sections = parse_publish_sections(SPARSE_CASHFLOW_ARTICLE, title_hint=prepared_title)
        plans = _build_gpt_publish_image_plans(prepared_title, sections)
        self.assertGreaterEqual(len(plans), 6)
        self.assertEqual(plans[0].slot, "lead")
        self.assertTrue(any(slot.endswith("::before") for slot in [plan.slot for plan in plans]))
        self.assertTrue(any("Use the uploaded image as a visual reference for mood and composition." in plan.prompt_text for plan in plans[1:]))
        self.assertTrue(any("Do not use a 4-panel grid" in plan.prompt_text or "Do not use repeated equal-size boxes" in plan.prompt_text for plan in plans[1:]))
        self.assertTrue(any("Create a Korean flowchart board" in plan.prompt_text or "Create a Korean checklist board" in plan.prompt_text or "Create a Korean comparison board" in plan.prompt_text for plan in plans[1:]))


if __name__ == "__main__":
    unittest.main()
