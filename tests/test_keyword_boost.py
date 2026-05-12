from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.gpt_web import build_text_prompt  # noqa: E402
from bunyang_longtail.naver_bundle_publish import PublishSection, build_publish_markdown, default_tags  # noqa: E402
from bunyang_longtail.prompt_builder import build_prompt_package  # noqa: E402


class KeywordBoostTest(unittest.TestCase):
    def test_prompt_package_uses_explicit_keyword_boost_pack(self) -> None:
        pack = {
            "primary_keyword": "청약통장1순위조건",
            "heading_keywords": ["청약통장1순위조건", "무주택세대구성원"],
            "faq_keywords": ["청약통장1순위조건"],
            "tag_keywords": ["청약통장1순위조건"],
            "engagement_prompt": "청약통장1순위조건 기준으로 보면 내 상황은 가능, 보류, 추가 확인 중 어디에 가까운지도 같이 보면 좋겠습니다.",
            "targets": [{"keyword": "청약통장1순위조건"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            pack_path = Path(tmpdir) / "keyword_pack.json"
            pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            with patch.dict(os.environ, {"ATOZ_KEYWORD_BOOST_PACK": str(pack_path)}):
                prompt_package = build_prompt_package(
                    {
                        "domain": "cheongyak",
                        "primary_keyword": "청약통장",
                        "secondary_keyword": "1순위 조건",
                        "comparison_keyword": "예치금",
                        "audience": "청약초보",
                        "search_intent": "가능여부",
                        "scenario": "처음 준비할 때",
                        "semantic_key": "청약통장",
                        "family": "기초",
                        "outline_json": json.dumps([{"heading": "상단 요약", "focus": "청약통장 1순위 조건"}], ensure_ascii=False),
                    },
                    {"title": "청약통장 1순위 조건, 처음 준비할 때 뭐부터 볼까", "angle": "판단형"},
                )

        self.assertEqual(prompt_package["user"]["keyword_boost_pack"]["primary_keyword"], "청약통장1순위조건")
        prompt_text = build_text_prompt(prompt_package)
        self.assertIn("네이버 키워드팩", prompt_text)
        self.assertIn("대표 검색어: 청약통장1순위조건", prompt_text)
        self.assertIn("문장이 어색해지면 키워드 반복보다 사람이 쓰는 표현을 우선", "\n".join(prompt_package["user"]["writing_rules"]))

    def test_publish_markdown_and_tags_can_use_keyword_pack(self) -> None:
        pack = {
            "tag_keywords": ["분양계약금계산"],
            "engagement_prompt": "분양계약금계산 기준으로 보면 내 상황은 가능, 보류, 추가 확인 중 어디에 가까운지도 같이 보면 좋겠습니다.",
        }
        markdown = build_publish_markdown(
            title="분양 계약금 계산, 현금은 얼마 남겨야 할까",
            sections=[
                PublishSection(
                    raw_heading="상단 요약",
                    publish_heading="30초 결론",
                    lines=["계약금과 옵션비를 같이 봅니다."],
                )
            ],
            assets=[],
            domain="cheongyak",
            keyword_pack=pack,
        )
        tags = default_tags("분양 계약금 계산", domain="cheongyak", keyword_pack=pack)

        self.assertIn("분양계약금계산 기준으로 보면", markdown)
        self.assertEqual(tags[0], "분양계약금계산")


if __name__ == "__main__":
    unittest.main()
