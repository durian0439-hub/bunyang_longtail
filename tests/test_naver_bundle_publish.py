from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import bunyang_longtail.naver_bundle_publish as target  # noqa: E402
from bunyang_longtail.naver_bundle_publish import (  # noqa: E402
    _build_gpt_publish_image_plans,
    build_publish_bundle,
    build_publish_title,
    parse_publish_sections,
)


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


class TestNaverBundlePublish(unittest.TestCase):
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
            self.assertTrue(all(Path(path).is_absolute() for path in meta["images"]))
            self.assertIn("30대 맞벌이 청약은 막연히 불리한 게임이 아니라, 어떤 공급에서 판단하느냐에 따라 결과가 크게 갈립니다.", bundle.markdown)
            self.assertIn("## 청약 1순위 FAQ", bundle.markdown)

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
