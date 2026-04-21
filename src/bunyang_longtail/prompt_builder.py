from __future__ import annotations

import json
from typing import Any

from .catalog import ANGLE_PROMPTS, NAVER_SEO_SECTIONS


def build_prompt_package(cluster: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    outline = json.loads(cluster["outline_json"])
    system_prompt = (
        "당신은 청약 전문 네이버 블로그 에디터입니다. "
        "첫 문단에서 결론을 먼저 말하고, 검색 사용자가 실제로 판단할 수 있게 써야 합니다. "
        "문장은 자연스럽고 쉽게 쓰되, 얄팍한 반복 표현과 과장형 카피는 피합니다. "
        "핵심 키워드는 제목, 소제목, 본문 초반에 자연스럽게 배치합니다. "
        "본문은 검색의도 해결, 조건 정리, 예외 설명, 체크리스트, FAQ 순으로 체류시간을 늘리는 구조를 따릅니다."
    )
    user_prompt = {
        "title": variant["title"],
        "primary_keyword": cluster["primary_keyword"],
        "secondary_keyword": cluster["secondary_keyword"],
        "comparison_keyword": cluster["comparison_keyword"],
        "audience": cluster["audience"],
        "intent": cluster["search_intent"],
        "scenario": cluster["scenario"],
        "angle": variant["angle"],
        "angle_rule": ANGLE_PROMPTS[variant["angle"]],
        "required_sections": NAVER_SEO_SECTIONS,
        "outline": outline,
        "writing_rules": [
            "첫 3문장 안에 가능/불가 또는 유리/불리 결론을 넣습니다.",
            "키워드 뜻풀이만 하지 말고 실제 판단 기준을 넣습니다.",
            "표현만 바꾼 중복 글처럼 보이지 않도록 도입 훅, 예시, FAQ 질문을 달리합니다.",
            "네이버 검색 유입을 위해 제목과 소제목에 대상자, 조건, 예외상황을 자연스럽게 포함합니다.",
            "본문 끝에는 누가 이 전략이 맞는지 한 줄 행동 가이드를 넣습니다.",
        ],
        "output_format": {
            "title": "string",
            "excerpt": "3문장 요약",
            "sections": [
                {"heading": "상단 요약", "body": "..."},
                {"heading": "이 글에서 바로 답하는 질문", "body": "..."},
                {"heading": "핵심 조건 정리", "body": "..."},
                {"heading": "헷갈리기 쉬운 예외", "body": "..."},
                {"heading": "실전 예시 시나리오", "body": "..."},
                {"heading": "체크리스트", "body": "..."},
                {"heading": "FAQ", "body": "질문 6개 이상"},
                {"heading": "마무리 결론", "body": "..."},
            ],
        },
    }
    return {"system": system_prompt, "user": user_prompt}
