from __future__ import annotations

from dataclasses import dataclass

NAVER_SEO_SECTIONS = [
    "상단 요약",
    "이 글에서 바로 답하는 질문",
    "핵심 조건 정리",
    "헷갈리기 쉬운 예외",
    "실전 예시 시나리오",
    "체크리스트",
    "FAQ",
    "마무리 결론",
]

ANGLE_PROMPTS = {
    "판단형": "첫 2문단에서 가능/불가 또는 유리/불리를 명확히 결론내립니다.",
    "비교형": "비슷한 제도나 상황과 비교해 차이를 빠르게 이해시키는 방식으로 씁니다.",
    "실수방지형": "탈락, 부적격, 비용 누락 같은 실수 포인트를 전면에 배치합니다.",
    "체크리스트형": "실행 순서와 점검 항목을 짧고 명확하게 끊어서 씁니다.",
    "사례형": "가상의 독자 사례를 앞세워 판단 흐름을 따라가게 씁니다.",
    "FAQ형": "자주 묻는 질문을 연속 배치해 검색 체류시간을 높이는 방식으로 씁니다.",
}

FAMILY_PRESETS = {
    "기초": {
        "audiences": ["청약초보", "청년 1인 가구", "사회초년생", "무주택 실수요자", "30대 맞벌이"],
        "intents": ["조건정리", "가능여부", "비교", "실수방지", "체크리스트"],
        "scenarios": ["처음 준비할 때", "세대분리 전", "통장만 있을 때", "기준이 헷갈릴 때"],
        "secondaries": ["1순위", "예치금", "세대주", "무주택", "청약홈"],
        "priority": 100,
    },
    "특공": {
        "audiences": ["신혼부부", "예비부부", "생애최초 준비자", "다자녀 가구", "노부모 부양 세대"],
        "intents": ["조건정리", "가능여부", "비교", "실수방지", "사례"],
        "scenarios": ["소득이 애매할 때", "자녀 수가 기준선일 때", "배우자 이력이 있을 때", "혼인 시점이 걸릴 때"],
        "secondaries": ["소득기준", "자산기준", "부적격", "신혼희망타운", "우선공급"],
        "priority": 95,
    },
    "일반공급": {
        "audiences": ["무주택 실수요자", "저가점 청약자", "고가점 청약자", "1주택 갈아타기 준비자"],
        "intents": ["조건정리", "가능여부", "비교", "전략", "실수방지"],
        "scenarios": ["가점이 낮을 때", "추첨제 물량을 볼 때", "규제지역 청약 전", "당해가 애매할 때"],
        "secondaries": ["가점제", "추첨제", "당해", "예비당첨", "커트라인"],
        "priority": 92,
    },
    "규제": {
        "audiences": ["무주택 실수요자", "1주택 갈아타기 준비자", "분양권 매수 검토자", "청약초보"],
        "intents": ["조건정리", "가능여부", "비교", "실수방지"],
        "scenarios": ["규제지역일 때", "당첨 이후", "매수 전에", "전매 계획이 있을 때"],
        "secondaries": ["재당첨 제한", "전매제한", "거주의무", "부적격", "주택수"],
        "priority": 90,
    },
    "자금": {
        "audiences": ["무주택 실수요자", "30대 맞벌이", "신혼부부", "저가점 청약자"],
        "intents": ["계산", "조건정리", "실수방지", "체크리스트", "사례"],
        "scenarios": ["계약금이 빠듯할 때", "중도금 대출이 필요할 때", "잔금이 걱정될 때", "월 상환액을 따질 때"],
        "secondaries": ["계약금", "중도금", "잔금", "대출", "취득세"],
        "priority": 93,
    },
    "대안": {
        "audiences": ["저가점 청약자", "무주택 실수요자", "1주택 갈아타기 준비자", "투자 겸 실거주 검토자"],
        "intents": ["가능여부", "비교", "전략", "실수방지", "사례"],
        "scenarios": ["당첨이 어려울 때", "청약통장이 약할 때", "당장 입주가 필요할 때", "매수와 청약 사이에서 고민할 때"],
        "secondaries": ["무순위", "잔여세대", "분양권", "입주권", "미분양"],
        "priority": 88,
    },
    "실무": {
        "audiences": ["청약초보", "무주택 실수요자", "신혼부부", "사회초년생"],
        "intents": ["체크리스트", "실수방지", "조건정리", "FAQ"],
        "scenarios": ["청약 직전", "청약홈 접속 전", "서류 준비 전", "당첨 직후"],
        "secondaries": ["입주자모집공고", "청약홈", "서류", "체크리스트", "당첨 후 절차"],
        "priority": 91,
    },
}


@dataclass(frozen=True)
class TopicBlueprint:
    family: str
    primary_keyword: str
    secondary_keywords: tuple[str, ...] = ()
    audiences: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    scenarios: tuple[str, ...] = ()
    comparison_keyword: str | None = None


TOPIC_BLUEPRINTS = [
    TopicBlueprint("기초", "청약통장", comparison_keyword="청년주택드림청약통장"),
    TopicBlueprint("기초", "무주택 기준", comparison_keyword="주택 수 판정"),
    TopicBlueprint("기초", "세대주 세대원", comparison_keyword="세대분리"),
    TopicBlueprint("기초", "1순위 조건", comparison_keyword="2순위"),
    TopicBlueprint("기초", "예치금", comparison_keyword="납입횟수"),
    TopicBlueprint("실무", "입주자모집공고", comparison_keyword="청약홈"),
    TopicBlueprint("실무", "청약홈", comparison_keyword="입주자모집공고"),
    TopicBlueprint("특공", "신혼부부 특별공급", comparison_keyword="생애최초 특별공급"),
    TopicBlueprint("특공", "생애최초 특별공급", comparison_keyword="신혼부부 특별공급"),
    TopicBlueprint("특공", "다자녀 특별공급", comparison_keyword="노부모 부양 특별공급"),
    TopicBlueprint("특공", "노부모 부양 특별공급", comparison_keyword="다자녀 특별공급"),
    TopicBlueprint("특공", "기관추천 특별공급", comparison_keyword="일반공급"),
    TopicBlueprint("특공", "신생아 특별공급", comparison_keyword="신혼부부 특별공급"),
    TopicBlueprint("특공", "신혼희망타운", comparison_keyword="공공분양"),
    TopicBlueprint("일반공급", "가점제", comparison_keyword="추첨제"),
    TopicBlueprint("일반공급", "추첨제", comparison_keyword="가점제"),
    TopicBlueprint("일반공급", "당해", comparison_keyword="기타지역"),
    TopicBlueprint("일반공급", "예비당첨", comparison_keyword="본당첨"),
    TopicBlueprint("규제", "재당첨 제한", comparison_keyword="특별공급 1회 제한"),
    TopicBlueprint("규제", "전매제한", comparison_keyword="거주의무"),
    TopicBlueprint("규제", "거주의무", comparison_keyword="전매제한"),
    TopicBlueprint("규제", "부적격 당첨", comparison_keyword="중복당첨"),
    TopicBlueprint("자금", "계약금 중도금 잔금", comparison_keyword="중도금 대출"),
    TopicBlueprint("자금", "중도금 대출", comparison_keyword="잔금 대출"),
    TopicBlueprint("자금", "취득세", comparison_keyword="양도세"),
    TopicBlueprint("자금", "월 상환액", comparison_keyword="자기자본"),
    TopicBlueprint("대안", "무순위 청약", comparison_keyword="잔여세대"),
    TopicBlueprint("대안", "잔여세대", comparison_keyword="무순위 청약"),
    TopicBlueprint("대안", "분양권", comparison_keyword="입주권"),
    TopicBlueprint("대안", "입주권", comparison_keyword="분양권"),
    TopicBlueprint("대안", "미분양", comparison_keyword="무순위 청약"),
]
