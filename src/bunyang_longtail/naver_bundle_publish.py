from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .local_image_fallback import _pick_font_path, _summary_source

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Pillow 가 없어 네이버 업로드용 이미지를 생성할 수 없습니다.") from exc


RAW_SECTION_ORDER = [
    "상단 요약",
    "이 글에서 바로 답하는 질문",
    "핵심 조건 정리",
    "헷갈리기 쉬운 예외",
    "실전 예시 시나리오",
    "체크리스트",
    "FAQ",
    "마무리 결론",
]

BASE_PUBLISH_HEADING_MAP = {
    "상단 요약": "30초 결론",
    "이 글에서 바로 답하는 질문": "무엇부터 따져야 하는지",
    "핵심 조건 정리": "핵심 판단 기준",
    "헷갈리기 쉬운 예외": "자주 틀리는 판단",
    "실전 예시 시나리오": "사례로 보면",
    "체크리스트": "신청 전 체크리스트",
    "FAQ": "자주 묻는 질문",
    "마무리 결론": "최종 정리",
}

TOPIC_PUBLISH_HEADING_OVERRIDES = {
    "ranking": {
        "이 글에서 바로 답하는 질문": "30대 맞벌이 청약 1순위에서 가장 많이 묻는 질문",
        "핵심 조건 정리": "일반공급 1순위 조건, 먼저 확인할 것",
        "헷갈리기 쉬운 예외": "30대 맞벌이 청약에서 자주 틀리는 판단",
        "실전 예시 시나리오": "30대 맞벌이 청약, 우리 상황에 대입해 보기",
        "체크리스트": "청약 1순위 신청 전 체크리스트",
        "FAQ": "청약 1순위 FAQ",
    },
    "institution": {
        "핵심 조건 정리": "배우자 주택 이력부터 구분하기",
        "헷갈리기 쉬운 예외": "노부모 부양 세대에서 자주 틀리는 판단",
        "실전 예시 시나리오": "우리 세대 조건에 대입해 보기",
        "체크리스트": "기관추천 신청 전 체크리스트",
    },
    "cashflow": {
        "이 글에서 바로 답하는 질문": "분양 계약금 계산에서 가장 많이 묻는 질문",
        "핵심 조건 정리": "계약금 중도금 잔금 비율, 먼저 어떻게 계산하나",
        "헷갈리기 쉬운 예외": "분양 옵션비와 취득세, 왜 같이 계산해야 하나",
        "실전 예시 시나리오": "분양가 6억이면 실제 현금은 얼마 필요한가",
        "체크리스트": "분양 계약 전 체크리스트",
        "FAQ": "분양 계약금 FAQ",
    },
}

TOPIC_COLORS = {
    "ranking": {"accent": "#2563EB", "soft": "#EFF6FF", "bg": "#EAF4FF"},
    "institution": {"accent": "#0F766E", "soft": "#ECFDF5", "bg": "#ECFDF5"},
    "cashflow": {"accent": "#EA580C", "soft": "#FFF7ED", "bg": "#FFF7ED"},
    "generic": {"accent": "#4338CA", "soft": "#EEF2FF", "bg": "#EEF2FF"},
}

THUMBNAIL_CHIPS = {
    "ranking": ["통장·거주요건", "특별공급 소득", "세대 기준 확인"],
    "institution": ["배우자 이력 구분", "세대 무주택 확인", "추천기관 기준 확인"],
    "cashflow": ["계약금 6천만원", "옵션비·취득세", "잔금 현금 확인"],
    "generic": ["핵심 조건 먼저", "일정과 자금 점검", "공고문 최종 확인"],
}

DEFAULT_TAGS = [
    "청약",
    "분양청약",
    "청약전략",
    "청약체크리스트",
    "부동산정보",
]

PUBLISH_ENV_CANDIDATES = [
    Path("/home/kj/app/bunyang/blog-cheongyak-automation/.env"),
    Path("/home/kj/app/bunyang/blog-cheongyak-automation/.env.local"),
]
PUBLISH_GPT_IMAGE_PROFILE = "gpt_image_profile_dev"


@dataclass
class PublishSection:
    raw_heading: str
    publish_heading: str
    lines: list[str]


@dataclass
class PublishAsset:
    slot: str
    kind: str
    label: str
    path: str


@dataclass
class PublishImagePlan:
    slot: str
    kind: str
    label: str
    image_role: str
    prompt_text: str


@dataclass
class PublishBundle:
    apt_id: str
    title: str
    markdown: str
    images: list[str]
    tags: list[str]
    body_html: str
    meta_path: str
    assets: list[PublishAsset]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim_text(text: str, max_len: int = 72) -> str:
    normalized = _clean(text)
    if len(normalized) <= max_len:
        return normalized
    shortened = normalized[: max_len - 3].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,") + "..."


def _strip_heading_markers(text: str) -> str:
    return re.sub(r"^#+\s*", "", str(text or "").strip()).strip()


def _topic_kind(title: str) -> str:
    text = _clean(_strip_heading_markers(title))
    if "1순위 조건" in text and "맞벌이" in text:
        return "ranking"
    if "기관추천 특별공급" in text and "노부모" in text:
        return "institution"
    if "계약금 중도금 잔금" in text or ("계약금" in text and "중도금" in text and "잔금" in text):
        return "cashflow"
    return "generic"


def _publish_heading_map(title: str) -> dict[str, str]:
    mapping = dict(BASE_PUBLISH_HEADING_MAP)
    mapping.update(TOPIC_PUBLISH_HEADING_OVERRIDES.get(_topic_kind(title), {}))
    return mapping


def _publish_heading_for(title: str, raw_heading: str) -> str:
    return _publish_heading_map(title).get(raw_heading, raw_heading)


def _normalize_section_lines(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    previous_blank = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            if previous_blank:
                continue
            normalized.append("")
            previous_blank = True
            continue
        normalized.append(line.strip())
        previous_blank = False
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return normalized


def _split_article(article_markdown: str) -> tuple[str, dict[str, list[str]]]:
    lines = [line.rstrip() for line in str(article_markdown or "").splitlines()]
    filtered = [line for line in lines]
    title = _clean(_strip_heading_markers(filtered[0])) if filtered else "청약 how to"
    current_heading: str | None = None
    sections: dict[str, list[str]] = {heading: [] for heading in RAW_SECTION_ORDER}

    for raw_line in filtered[1:]:
        line = raw_line.strip()
        normalized_heading = _strip_heading_markers(line)
        if normalized_heading in RAW_SECTION_ORDER:
            current_heading = normalized_heading
            continue
        if current_heading is None:
            continue
        sections[current_heading].append(raw_line)

    return title, {heading: _normalize_section_lines(value) for heading, value in sections.items() if _normalize_section_lines(value)}


def _section_count(article_markdown: str) -> int:
    _, sections = _split_article(article_markdown)
    return sum(1 for heading in RAW_SECTION_ORDER if sections.get(heading))


def _needs_article_expansion(article_markdown: str) -> bool:
    return len(_clean(article_markdown)) < 1400 or _section_count(article_markdown) < 6


def _synthesize_article_for_publish(title: str) -> str:
    normalized_title = _clean(_strip_heading_markers(title)) or "분양청약 how to"

    if "기관추천 특별공급" in normalized_title and "노부모 부양" in normalized_title:
        return f"""{normalized_title}
상단 요약

결론부터 말씀드리면, 기관추천 특별공급에서 배우자 주택 이력이 있다고 해서 노부모 부양 세대가 무조건 탈락하는 것은 아닙니다. 다만 노부모를 모시는 세대라는 사실만으로 배우자 이력이 상쇄되지는 않기 때문에, 현재 세대 무주택 인정 여부, 배우자 과거 소유·처분 시점, 재당첨 제한, 추천기관 세부 기준을 함께 보셔야 합니다.

즉, 질문의 핵심은 노부모를 부양하니 가능하냐가 아니라 배우자 이력이 현재 자격판정에 어떤 식으로 반영되느냐입니다. 마지막 판단은 반드시 입주자모집공고와 추천기관 기준으로 하셔야 안전합니다.

이 글에서 바로 답하는 질문

배우자 과거 주택 보유 이력이 있으면 기관추천 특별공급이 바로 막히는가
노부모 부양 세대면 무주택 판정에서 유리해지는가
배우자 이력은 현재 소유인지 과거 소유인지 어떻게 다르게 보는가
기관추천 특별공급과 노부모 부양 특별공급은 같은 기준으로 보면 되는가

핵심 조건 정리

기관추천 특별공급은 먼저 추천대상 여부를 보고, 그다음 세대 무주택과 공고문 자격을 봅니다. 여기서 노부모 부양 세대라는 점은 가족구성 설명에는 도움이 되지만, 배우자 이력 자체를 자동으로 지워주는 요소는 아닙니다.

실무에서는 아래 네 가지를 같이 보시면 됩니다.
- 현재 세대 기준 무주택으로 인정되는지
- 배우자의 주택 이력이 현재 보유인지 과거 처분 완료인지
- 과거 당첨 또는 재당첨 제한에 걸리는지
- 해당 기관의 추천요건과 공고문 특별공급 자격이 동시에 맞는지

노부모 부양 세대가 중요한 이유는 세대구성과 부양관계 증빙 때문이지, 배우자 주택 이력 판정 규칙이 달라지기 때문은 아닙니다. 그래서 자격은 반드시 세대 전체 기준으로 다시 보셔야 합니다.

헷갈리기 쉬운 예외

1) 배우자에게 과거 집이 있었으니 무조건 불가라고 단정하는 경우
과거 보유 이력만으로 바로 끝나는 것은 아닙니다. 보유 시점, 처분 시점, 현재 세대 무주택 인정 여부에 따라 판단이 달라질 수 있습니다.

2) 노부모를 모시면 특별공급 자격이 자동 보완된다고 생각하는 경우
노부모 부양은 별도 공급유형이나 가족구성 판단에서는 중요하지만, 기관추천 특별공급의 배우자 이력 판정 자체를 바꾸는 것은 아닙니다.

3) 기관추천 특별공급과 노부모 부양 특별공급을 같은 제도로 보는 경우
둘은 공급유형과 우선 확인 항목이 다를 수 있습니다. 기관추천은 추천기관 요건이 먼저고, 노부모 부양 특별공급은 부양기간과 무주택 등 별도 요건을 같이 봅니다.

4) 현재 무주택이면 과거 당첨 이력은 무시해도 된다고 보는 경우
재당첨 제한이나 과거 특별공급 당첨 이력은 따로 보셔야 합니다. 현재 무주택이라는 사실 하나로 모두 해결되지는 않습니다.

실전 예시 시나리오

시나리오 1. 배우자가 과거 1주택을 보유했지만 현재는 처분했고, 세대 전체가 무주택인 경우
이 경우 기관추천 특별공급 가능성이 남아 있을 수 있습니다. 다만 처분 시점과 공고문 기준일, 과거 당첨 이력 여부를 같이 확인하셔야 합니다.

시나리오 2. 배우자가 현재도 지분 또는 주택을 보유한 경우
세대 무주택 요건에서 바로 불리해질 수 있습니다. 이 경우 노부모 부양 세대라는 점보다 현재 보유 사실이 더 핵심입니다.

시나리오 3. 노부모를 오래 모셨지만 추천기관 자격이 애매한 경우
기관추천 특별공급은 결국 추천기관 요건을 통과해야 합니다. 노부모 부양 사실이 있어도 추천기관 자격이 안 맞으면 진행이 어렵습니다.

체크리스트

현재 세대 전원이 무주택으로 인정되는지 확인했는가
배우자 주택 보유 이력이 현재 보유인지 과거 보유인지 정리했는가
과거 특별공급 당첨, 재당첨 제한 여부를 확인했는가
추천기관 대상 자격과 제출서류를 확인했는가
입주자모집공고의 기관추천 특별공급 항목을 원문으로 읽었는가
청약홈과 사업주체 문의처로 최종 확인했는가

FAQ

Q1. 배우자 과거 주택 보유 이력이 있으면 기관추천 특별공급은 무조건 안 되나요?
아닙니다. 과거 보유인지 현재 보유인지, 현재 세대 무주택 인정 여부, 재당첨 제한 등을 함께 보셔야 합니다.

Q2. 노부모 부양 세대면 배우자 이력이 있어도 유리한가요?
노부모 부양 세대라는 사실이 자격판정의 핵심 예외가 되는 것은 아닙니다. 배우자 이력과 세대 무주택 판단을 별도로 보셔야 합니다.

Q3. 기관추천 특별공급과 노부모 부양 특별공급은 같은 기준인가요?
같지 않습니다. 공급유형과 우선 검토 항목이 다를 수 있으니 공고문 기준으로 나눠 보셔야 합니다.

Q4. 최종 판단은 어디서 확인하는 게 안전한가요?
입주자모집공고, 청약홈, 그리고 추천기관 안내 기준을 같이 보는 것이 가장 안전합니다.

마무리 결론

기관추천 특별공급에서 배우자 주택 이력이 있을 때 노부모 부양 세대도 가능할 수는 있지만, 노부모를 부양한다는 이유만으로 자동 통과되는 구조는 아닙니다. 결국 핵심은 현재 세대 무주택 인정 여부, 배우자 보유·처분 이력, 과거 당첨 제한, 추천기관 자격을 한 번에 맞추는지입니다. 신청 직전에는 입주자모집공고와 추천기관 기준을 대조해서 마지막 판정을 받으시는 것이 안전합니다.
"""

    if "계약금 중도금 잔금" in normalized_title:
        return f"""{normalized_title}
상단 요약

분양가 6억원이면 계약금 6천만원으로 끝나지 않습니다. 옵션비, 취득세, 잔금 시점 추가 현금까지 같이 보면 실제 준비해야 할 돈이 생각보다 크게 늘어납니다.

그래서 계약금만 낼 수 있느냐보다 계약부터 입주까지 총 현금을 버틸 수 있느냐를 먼저 보셔야 합니다. 분양 계약금, 중도금, 잔금을 따로 보지 말고 하나의 자금표로 묶어서 계산하셔야 실수가 줄어듭니다.

이 글에서 바로 답하는 질문

분양 계약금 중도금 잔금 비율은 어떻게 계산하나
중도금 무이자 분양이면 자기자본은 얼마나 줄어드나
분양 취득세와 옵션비는 언제 같이 계산해야 하나
분양가 6억원이면 실제 현금은 얼마까지 준비해야 하나

핵심 조건 정리

분양 계약금 중도금 잔금은 시점이 다른 돈이라서 따로 계산하면 빠뜨리는 항목이 생기기 쉽습니다. 계산 순서는 보통 이렇게 잡으시면 됩니다.
- 분양가와 납부 일정을 먼저 확인합니다.
- 계약금 비율과 중도금 대출 가능 여부를 확인합니다.
- 잔금 시점에 취득세, 등기비용, 옵션비를 더합니다.
- 기존 보증금 회수, 매도대금, 잔금대출 실행액을 차감합니다.

간단한 계산식은 이렇게 보시면 됩니다.
필요 자기자본 = 계약금 + 중도금 자납분 + 옵션비 + 취득세·등기비용 + 이사비 + 예비비 - 실행 가능한 대출금 - 회수 가능한 보증금

분양가 6억원, 계약금 10%, 중도금 60%, 잔금 30%라고 가정하면 계약금은 6천만원, 중도금은 3억6천만원, 잔금은 1억8천만원입니다. 중도금이 전액 대출로 연결되더라도 계약금과 별도 비용은 현금으로 준비하셔야 합니다.

헷갈리기 쉬운 예외

1) 계약금만 있으면 된다고 보는 경우
실제로는 입주 직전 잔금과 부대비용이 더 크게 느껴질 수 있습니다. 계약금만 보고 들어가면 뒤에서 현금이 막히기 쉽습니다.

2) 중도금 무이자면 현금 부담이 거의 없다고 생각하는 경우
무이자 여부와 별개로 잔금 대출 한도, DSR, 입주 시점 금리 변화가 최종 부담을 바꿉니다.

3) 옵션비와 취득세를 빼고 계산하는 경우
발코니 확장, 시스템에어컨, 취득세, 법무비, 이사비를 누락하면 실제 필요 현금이 수천만원 차이날 수 있습니다.

4) 기존 집 매도나 전세보증금 회수 시점을 낙관적으로 보는 경우
자금이 들어오는 시점이 늦어지면 잔금일에 브릿지 자금이 필요해질 수 있습니다.

실전 예시 시나리오

시나리오 1. 분양가 6억원, 계약금 10%, 중도금 대출 가능
계약금 6천만원에 옵션비와 초기 부대비용 1천만~2천만원 정도를 더해 첫 현금 부담을 먼저 봐야 합니다. 잔금 시점에는 잔금대출 가능액과 취득세를 포함해 다시 계산하셔야 합니다.

시나리오 2. 계약금은 되지만 잔금이 불안한 경우
이 경우는 분양가가 아니라 입주 시 총자금표를 먼저 만드셔야 합니다. 잔금대출 한도가 줄면 필요한 자기자본이 생각보다 커질 수 있습니다.

시나리오 3. 기존 전세보증금이나 기존 주택 매도대금이 들어올 예정인 경우
예정 자금이 실제로 언제 들어오는지 일정표에 반영해야 합니다. 일정이 늦으면 단기 자금 공백이 생길 수 있습니다.

체크리스트

분양가와 계약금, 중도금, 잔금 비율을 확인했는가
중도금 대출 가능 여부와 조건을 확인했는가
잔금대출 예상 한도와 금리를 따져봤는가
옵션비, 취득세, 등기비용, 이사비를 포함했는가
기존 보증금 회수나 주택 매도 일정까지 자금표에 넣었는가
최악의 경우를 대비한 예비비를 잡아두었는가

FAQ

Q1. 중도금 무이자 분양이면 자기자본은 얼마나 필요한가요?
중도금 이자 부담은 줄어들 수 있지만, 계약금, 옵션비, 취득세, 잔금 시점 현금은 여전히 따로 준비하셔야 합니다.

Q2. 분양 취득세는 언제 같이 계산해야 하나요?
잔금 시점을 보기 시작할 때 바로 포함하셔야 합니다. 취득세를 늦게 넣으면 잔금일 현금 계획이 틀어지기 쉽습니다.

Q3. 잔금대출 한도가 부족하면 어떻게 되나요?
부족한 금액은 자기자본이나 단기 자금으로 채워야 합니다. 그래서 입주 전에는 잔금대출 예상 한도를 미리 보셔야 합니다.

Q4. 계약금만 마련되면 분양 계약부터 해도 되나요?
권하지 않습니다. 잔금과 부대비용까지 포함한 총 자기자본 계획이 먼저 나와야 안전합니다.

마무리 결론

분양 계약금 중도금 잔금을 계산할 때는 계약금만 보는 방식이 가장 위험합니다. 실제로는 잔금 시점의 대출 한도, 옵션비, 취득세, 이사비까지 포함한 총자금 계획이 있어야 분양 일정이 버텨집니다. 계약 전에는 분양가 일정표와 총 현금표를 먼저 만들고 들어가셔야 실수가 줄어듭니다.
"""

    return f"""{normalized_title}
상단 요약

결론부터 말씀드리면, 이 주제는 단일 조건 하나로 판단하기보다 자격 요건, 일정, 자금, 리스크를 함께 보셔야 정확합니다.

이 글에서 바로 답하는 질문

무엇을 먼저 확인해야 하는가
공고문에서 놓치기 쉬운 조건은 무엇인가
신청 직전 마지막 점검은 어떻게 해야 하는가

핵심 조건 정리

먼저 핵심 자격요건을 분리하고, 그다음 일정과 자금 계획을 같이 보셔야 합니다. 마지막 판단은 항상 입주자모집공고 기준으로 다시 확인하셔야 합니다.

헷갈리기 쉬운 예외

인터넷 요약만 보고 바로 결론을 내리면 공고문 세부 조건을 놓치기 쉽습니다. 특히 세대 기준과 일정 기준일은 따로 보셔야 합니다.

실전 예시 시나리오

내 조건은 맞는 것 같은데 세부 자격이 애매하다면, 현재 조건과 공고문 원문을 한 줄씩 대조해 보시는 것이 가장 빠릅니다.

체크리스트

공고문 기준일을 확인했는가
세대 기준 조건을 확인했는가
자금 계획과 일정표를 같이 봤는가

FAQ

Q1. 마지막 판단은 어디서 해야 하나요?
A. 입주자모집공고와 청약홈에서 최종 확인하시는 것이 안전합니다.

마무리 결론

검색으로 방향을 잡을 수는 있지만, 최종 신청 판단은 반드시 공고문 원문과 본인 조건 대조까지 끝내고 하셔야 합니다.
"""


def _prepare_article_for_publish(*, title: str, article_markdown: str) -> str:
    if not _needs_article_expansion(article_markdown):
        return article_markdown
    return _synthesize_article_for_publish(title)


def parse_publish_sections(article_markdown: str, *, title_hint: str | None = None) -> tuple[str, list[PublishSection]]:
    original_title, sections = _split_article(article_markdown)
    base_title = title_hint or original_title
    heading_map = _publish_heading_map(base_title)
    ordered: list[PublishSection] = []
    for heading in RAW_SECTION_ORDER:
        lines = sections.get(heading) or []
        if not lines:
            continue
        ordered.append(
            PublishSection(
                raw_heading=heading,
                publish_heading=heading_map.get(heading, heading),
                lines=lines,
            )
        )
    return original_title, ordered


def build_publish_title(original_title: str) -> str:
    title = _clean(original_title)
    if "1순위 조건" in title and "30대 맞벌이" in title:
        return "30대 맞벌이 청약 1순위 조건, 일반공급과 특별공급 뭐가 다를까?"
    if "기관추천 특별공급" in title and "노부모 부양" in title:
        return "기관추천 특별공급 배우자 주택 이력 있을 때, 노부모 부양 세대 가능 여부 정리"
    if "계약금 중도금 잔금" in title:
        return "분양 계약금 중도금 잔금, 실제 필요한 현금은 얼마일까?"
    return title if "청약" in title else f"{title} | 분양청약 how to 정리"


def _intro_text(sections: list[PublishSection]) -> str:
    if not sections:
        return "오늘은 분양청약 판단이 헷갈릴 때 어떻게 순서를 잡아야 하는지 빠르게 정리해보겠습니다."
    source = _summary_source(sections[0].publish_heading, "", article_markdown="\n".join(sections[0].lines))
    text = _trim_text(source, max_len=120)
    if text:
        return text
    return "오늘은 분양청약 판단이 헷갈릴 때 어떻게 순서를 잡아야 하는지 빠르게 정리해보겠습니다."


def _font(size: int):
    return ImageFont.truetype(_pick_font_path(), size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int | None = None) -> list[str]:
    normalized = str(text or "").replace("\n", " ").strip()
    if not normalized:
        return [""]

    words = [token for token in normalized.split(" ") if token]
    if len(words) > 1:
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = word
            if max_lines and len(lines) >= max_lines - 1:
                break
        if current:
            lines.append(current)
        if max_lines:
            lines = lines[:max_lines]
        return [line.strip() for line in lines if line.strip()] or [""]

    lines = []
    current = ""
    for char in normalized:
        candidate = f"{current}{char}"
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = char
            if max_lines and len(lines) >= max_lines - 1:
                break
            continue
        current = candidate

    if current:
        lines.append(current)
    if max_lines:
        lines = lines[:max_lines]
    return [line.strip() for line in lines if line.strip()] or [""]


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, *, max_width: int, font_size: int, max_lines: int) -> list[str]:
    return _wrap_text(draw, _clean(text), _font(font_size), max_width, max_lines)


def _thumbnail_title_lines(draw: ImageDraw.ImageDraw, title: str) -> list[str]:
    normalized = _clean(title)
    if "," in normalized:
        first, second = [part.strip() for part in normalized.split(",", 1)]
        lines = _wrap_text(draw, f"{first},", _font(60), 560, 2)
        lines.extend(_wrap_text(draw, second, _font(60), 560, 2))
        return lines[:3]
    return _fit_lines(draw, normalized, max_width=560, font_size=62, max_lines=3)


def _topic_palette(title: str) -> dict[str, str]:
    return TOPIC_COLORS.get(_topic_kind(title), TOPIC_COLORS["generic"])


def _thumbnail_chips(title: str, sections: list[PublishSection]) -> list[str]:
    chips = list(THUMBNAIL_CHIPS.get(_topic_kind(title), THUMBNAIL_CHIPS["generic"]))
    if len(chips) >= 3:
        return chips[:3]
    for section in sections:
        label = _trim_text(section.publish_heading.replace("1단계, ", "").replace("2단계, ", "").replace("3단계, ", "").replace("4단계, ", ""), 18)
        if label not in chips:
            chips.append(label)
        if len(chips) >= 3:
            break
    return chips[:3]


def _draw_thumbnail_icon(draw: ImageDraw.ImageDraw, *, kind: str, accent: str) -> None:
    if kind == "cashflow":
        draw.rounded_rectangle((760, 250, 940, 520), radius=28, outline=accent, width=8, fill="white")
        draw.rounded_rectangle((790, 280, 910, 340), radius=14, fill="#F8FAFC", outline="#CBD5E1", width=2)
        for row in range(3):
            for col in range(3):
                x1 = 790 + col * 44
                y1 = 370 + row * 44
                draw.rounded_rectangle((x1, y1, x1 + 30, y1 + 30), radius=8, fill="#EFF6FF")
        draw.line((780, 560, 930, 620), fill=accent, width=10)
        draw.ellipse((900, 590, 980, 670), outline=accent, width=10)
        return
    if kind == "institution":
        draw.polygon([(820, 250), (710, 340), (930, 340)], fill="#D1FAE5", outline=accent)
        draw.rounded_rectangle((750, 340, 890, 520), radius=18, fill="white", outline=accent, width=6)
        draw.rounded_rectangle((805, 410, 845, 520), radius=12, fill="#ECFDF5", outline=accent, width=4)
        draw.line((720, 600, 780, 660), fill=accent, width=12)
        draw.line((780, 660, 920, 520), fill=accent, width=12)
        return
    draw.rounded_rectangle((730, 260, 960, 520), radius=28, fill="white", outline=accent, width=6)
    draw.rounded_rectangle((770, 300, 860, 380), radius=14, fill="#EFF6FF")
    draw.line((780, 430, 910, 430), fill=accent, width=10)
    draw.line((780, 470, 880, 470), fill="#94A3B8", width=10)
    draw.line((780, 510, 930, 510), fill="#94A3B8", width=10)
    draw.ellipse((900, 320, 980, 400), fill=accent)
    draw.line((925, 350, 944, 369), fill="white", width=8)
    draw.line((944, 369, 972, 330), fill="white", width=8)


def render_thumbnail_image(*, title: str, sections: list[PublishSection], output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    kind = _topic_kind(title)
    palette = _topic_palette(title)
    accent = palette["accent"]
    soft = palette["soft"]
    width = height = 1080
    image = Image.new("RGB", (width, height), palette["bg"])
    draw = ImageDraw.Draw(image)

    for y in range(height):
        top = (248, 250, 252)
        bottom = (226, 238, 255)
        ratio = y / height
        color = tuple(int(top[idx] * (1 - ratio) + bottom[idx] * ratio) for idx in range(3))
        draw.line((0, y, width, y), fill=color)

    for box, color in [
        ((-100, 720, 360, 1160), (191, 219, 254, 100)),
        ((760, -40, 1180, 360), (147, 197, 253, 100)),
    ]:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.ellipse(box, fill=color)
        overlay = overlay.filter(ImageFilter.GaussianBlur(30))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((52, 52, 1028, 1028), radius=42, fill="white", outline="#D7E5F5", width=3)
    badge_text = {
        "ranking": "청약 1순위",
        "institution": "특별공급",
        "cashflow": "분양 자금",
    }.get(kind, "분양청약")
    draw.rounded_rectangle((88, 88, 286, 142), radius=22, fill="#0F172A")
    draw.text((187, 115), badge_text, fill="white", font=_font(30), anchor="mm")

    title_lines = _thumbnail_title_lines(draw, title)
    y = 190
    for idx, line in enumerate(title_lines):
        draw.text((92, y), line, fill="#0F172A" if idx < len(title_lines) - 1 else accent, font=_font(60))
        y += 76

    summary_lines = _fit_lines(draw, _intro_text(sections), max_width=540, font_size=30, max_lines=3)
    draw.rounded_rectangle((92, 462, 630, 618), radius=28, fill=soft, outline="#DCE7F6", width=2)
    sy = 494
    for line in summary_lines:
        draw.text((120, sy), line, fill="#334155", font=_font(30))
        sy += 40

    _draw_thumbnail_icon(draw, kind=kind, accent=accent)

    chips = _thumbnail_chips(title, sections)[:2]
    chip_y = 736
    for idx, chip in enumerate(chips):
        x1 = 92 + idx * 214
        x2 = x1 + 186
        draw.rounded_rectangle((x1, chip_y, x2, chip_y + 56), radius=18, fill=soft, outline="#DCE7F6", width=2)
        chip_lines = _fit_lines(draw, chip, max_width=154, font_size=24, max_lines=2)
        chip_text = "\n".join(chip_lines[:2])
        draw.multiline_text((x1 + 93, chip_y + 28), chip_text, fill=accent, font=_font(24), anchor="mm", align="center", spacing=2)

    draw.rounded_rectangle((92, 838, 988, 928), radius=26, fill=soft, outline="#DCE7F6", width=2)
    draw.text((540, 883), "표 중심 본문, 최종 자격과 일정은 공고문과 청약홈으로 확인", fill="#334155", font=_font(27), anchor="mm")

    image.save(output)
    return str(output)


def render_soft_illustration_image(*, title: str, label: str, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    palette = _topic_palette(title)
    accent = palette["accent"]
    width = height = 1080
    image = Image.new("RGB", (width, height), "#FFF9F5")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        top = (255, 251, 245)
        bottom = (255, 237, 223)
        ratio = y / height
        color = tuple(int(top[idx] * (1 - ratio) + bottom[idx] * ratio) for idx in range(3))
        draw.line((0, y, width, y), fill=color)

    draw.rounded_rectangle((54, 54, 1026, 1026), radius=42, fill="white", outline="#F1E4DA", width=3)
    draw.rounded_rectangle((86, 86, 246, 138), radius=22, fill=accent)
    draw.text((166, 112), "삽화 요약", fill="white", font=_font(28), anchor="mm")
    draw.text((86, 188), _trim_text(label, 22), fill="#0F172A", font=_font(48))
    draw.text((86, 246), "복잡한 돈 흐름을 한 장면으로 이해하실 수 있게 정리했습니다.", fill="#64748B", font=_font(26))

    kind = _topic_kind(title)
    if kind == "ranking":
        draw.rounded_rectangle((120, 520, 410, 760), radius=32, fill="#EFF6FF", outline="#BFDBFE", width=2)
        draw.ellipse((210, 300, 300, 390), fill="#FCD34D")
        draw.rounded_rectangle((188, 388, 322, 562), radius=28, fill="#2563EB")
        draw.ellipse((280, 300, 370, 390), fill="#FDBA74")
        draw.rounded_rectangle((258, 388, 392, 562), radius=28, fill="#0F766E")
        draw.ellipse((240, 328, 260, 348), fill="#0F172A")
        draw.ellipse((330, 328, 350, 348), fill="#0F172A")

        draw.polygon([(700, 330), (830, 230), (960, 330)], fill="#DBEAFE", outline=accent)
        draw.rounded_rectangle((746, 330, 914, 540), radius=24, fill="white", outline=accent, width=4)
        draw.rounded_rectangle((806, 420, 854, 540), radius=14, fill="#EFF6FF", outline=accent, width=3)
        draw.line((620, 650, 900, 650), fill="#93C5FD", width=10)
        draw.line((620, 700, 840, 700), fill="#93C5FD", width=10)
        draw.rounded_rectangle((612, 744, 914, 804), radius=22, fill="#EFF6FF")
        draw.text((763, 774), "일반공급과 특별공급은 기준이 다릅니다", fill="#1D4ED8", font=_font(26), anchor="mm")

        draw.rounded_rectangle((86, 852, 996, 930), radius=24, fill="#EFF6FF", outline="#BFDBFE", width=2)
        draw.text((541, 891), "맞벌이라고 바로 탈락이 아니라, 먼저 어떤 공급인지부터 나눠 보셔야 합니다.", fill="#1D4ED8", font=_font(27), anchor="mm")
    else:
        draw.rounded_rectangle((120, 520, 410, 760), radius=32, fill="#FEF2F2", outline="#FECACA", width=2)
        draw.rectangle((180, 450, 350, 520), fill="#FDBA74", outline=accent, width=4)
        draw.rectangle((210, 420, 320, 450), fill="#FED7AA", outline=accent, width=4)
        draw.ellipse((220, 300, 310, 390), fill="#FDBA74")
        draw.rounded_rectangle((195, 388, 335, 560), radius=28, fill=accent)
        draw.ellipse((250, 325, 270, 345), fill="#0F172A")
        draw.ellipse((290, 325, 310, 345), fill="#0F172A")

        draw.rounded_rectangle((520, 300, 940, 760), radius=34, fill="#FFF7ED", outline="#FED7AA", width=2)
        draw.rounded_rectangle((576, 356, 884, 440), radius=24, fill="white", outline="#FDBA74", width=2)
        draw.text((730, 398), "계약금 6천만원", fill=accent, font=_font(34), anchor="mm")
        draw.line((620, 500, 836, 500), fill="#FDBA74", width=10)
        draw.line((620, 560, 796, 560), fill="#FDBA74", width=10)
        draw.line((620, 620, 860, 620), fill="#FDBA74", width=10)
        draw.rounded_rectangle((610, 680, 894, 734), radius=20, fill="#FFEDD5")
        draw.text((752, 707), "옵션비·취득세까지 함께 계산", fill="#9A3412", font=_font(26), anchor="mm")

        draw.rounded_rectangle((86, 852, 996, 930), radius=24, fill="#FFF7ED", outline="#FED7AA", width=2)
        draw.text((541, 891), "계약금만 보면 가볍지만, 입주 직전에는 현금 부담이 훨씬 커집니다.", fill="#7C2D12", font=_font(27), anchor="mm")

    image.save(output)
    return str(output)


def render_timeline_image(*, title: str, label: str, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    palette = _topic_palette(title)
    accent = palette["accent"]
    soft = palette["soft"]
    width = height = 1080
    image = Image.new("RGB", (width, height), "#F8FAFC")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((44, 44, 1036, 1036), radius=42, fill="white", outline="#E2E8F0", width=3)
    draw.rounded_rectangle((84, 84, 308, 136), radius=22, fill=accent)
    draw.text((196, 110), "자금 흐름도", fill="white", font=_font(28), anchor="mm")
    draw.text((86, 190), _trim_text(label, 22), fill="#0F172A", font=_font(46))
    draw.text((86, 246), "계약부터 입주까지 어느 구간에서 현금이 필요한지 한 번에 보시면 됩니다.", fill="#64748B", font=_font(26))

    y = 560
    draw.line((170, y, 910, y), fill="#FDBA74", width=10)
    steps = [
        ("계약일", "계약금 10%", "계약금은 현금으로 바로 준비"),
        ("중도금", "대출 가능 구간", "무이자 여부와 실행 조건 확인"),
        ("입주 전", "옵션비·취득세", "빠뜨리기 쉬운 별도비용 반영"),
        ("입주일", "잔금 + 부족분", "잔금대출 한도 부족 여부 최종 점검"),
    ]
    positions = [170, 400, 640, 910]
    for (step, amount, note), x in zip(steps, positions):
        draw.ellipse((x - 28, y - 28, x + 28, y + 28), fill=accent)
        draw.rounded_rectangle((x - 110, y - 190, x + 110, y - 56), radius=24, fill=soft, outline="#FED7AA", width=2)
        draw.text((x, y - 164), step, fill=accent, font=_font(28), anchor="mm")
        draw.text((x, y - 118), amount, fill="#0F172A", font=_font(30), anchor="mm")
        note_lines = _fit_lines(draw, note, max_width=200, font_size=22, max_lines=2)
        draw.multiline_text((x, y + 70), "\n".join(note_lines), fill="#475569", font=_font(22), anchor="mm", align="center", spacing=4)

    draw.rounded_rectangle((94, 846, 986, 930), radius=24, fill="#FFF7ED", outline="#FED7AA", width=2)
    draw.text((540, 889), "핵심은 계약금보다 잔금일 현금 부족이 더 치명적이라는 점입니다.", fill="#9A3412", font=_font(28), anchor="mm")

    image.save(output)
    return str(output)


def render_checklist_card_image(*, title: str, label: str, items: list[str], output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    palette = _topic_palette(title)
    accent = palette["accent"]
    soft = palette["soft"]
    width = height = 1080
    image = Image.new("RGB", (width, height), "#F8FAFC")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((44, 44, 1036, 1036), radius=42, fill="white", outline="#E2E8F0", width=3)
    draw.rounded_rectangle((84, 84, 296, 136), radius=22, fill=accent)
    draw.text((190, 110), "체크리스트", fill="white", font=_font(28), anchor="mm")
    draw.text((86, 190), _trim_text(label, 24), fill="#0F172A", font=_font(46))
    draw.text((86, 246), "계약 직전에 아래 항목만 다시 보면 큰 실수를 줄일 수 있습니다.", fill="#64748B", font=_font(26))

    y = 340
    for idx, item in enumerate(items[:5], start=1):
        draw.rounded_rectangle((86, y, 994, y + 110), radius=28, fill=soft, outline="#E2E8F0", width=2)
        draw.rounded_rectangle((112, y + 28, 164, y + 80), radius=18, fill=accent)
        draw.text((138, y + 54), str(idx), fill="white", font=_font(28), anchor="mm")
        item_lines = _fit_lines(draw, item, max_width=760, font_size=28, max_lines=2)
        draw.multiline_text((198, y + 28), "\n".join(item_lines), fill="#1F2937", font=_font(28), spacing=6)
        y += 136

    draw.rounded_rectangle((86, 948, 994, 998), radius=18, fill="#FFF7ED")
    draw.text((540, 973), "계약 직전에는 잔금대출 한도와 취득세까지 한 번 더 점검", fill="#9A3412", font=_font(24), anchor="mm")

    image.save(output)
    return str(output)


def _table_column_widths(column_count: int) -> list[float]:
    if column_count == 2:
        return [0.3, 0.7]
    if column_count == 3:
        return [0.22, 0.28, 0.5]
    return [1 / max(column_count, 1)] * max(column_count, 1)


def render_table_image(*, title: str, label: str, headers: list[str], rows: list[list[str]], output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    palette = _topic_palette(title)
    accent = palette["accent"]
    soft = palette["soft"]
    width = height = 1080
    image = Image.new("RGB", (width, height), "#F8FAFC")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((44, 44, 1036, 1036), radius=42, fill="white", outline="#E2E8F0", width=3)
    draw.rounded_rectangle((84, 84, 252, 136), radius=22, fill=accent)
    draw.text((168, 110), "핵심 표 정리", fill="white", font=_font(28), anchor="mm")

    title_lines = _fit_lines(draw, title, max_width=860, font_size=34, max_lines=2)
    y = 176
    for line in title_lines:
        draw.text((86, y), line, fill="#475569", font=_font(32))
        y += 40

    draw.text((86, 270), _trim_text(label, 34), fill="#0F172A", font=_font(48))
    draw.text((86, 326), "복잡한 설명보다 판단 포인트를 표로 먼저 보시면 빠릅니다.", fill="#64748B", font=_font(26))

    left = 82
    right = 998
    top = 390
    header_height = 72
    table_width = right - left
    ratios = _table_column_widths(len(headers))
    widths = [int(table_width * ratio) for ratio in ratios]
    widths[-1] = table_width - sum(widths[:-1])

    x = left
    for index, header in enumerate(headers):
        cell_right = x + widths[index]
        draw.rounded_rectangle((x, top, cell_right, top + header_height), radius=0, fill=accent)
        draw.text((x + 16, top + 22), header, fill="white", font=_font(26))
        x = cell_right

    row_y = top + header_height
    for row_index, row in enumerate(rows[:5]):
        cell_lines: list[list[str]] = []
        max_lines = 1
        for column_index, cell in enumerate(row):
            column_width = widths[column_index] - 28
            lines = _wrap_text(draw, str(cell or ""), _font(24), column_width, 4)
            cell_lines.append(lines)
            max_lines = max(max_lines, len(lines))

        row_height = max(74, 24 * max_lines + 34)
        x = left
        fill = "#F8FAFC" if row_index % 2 == 0 else soft
        for column_index, lines in enumerate(cell_lines):
            cell_right = x + widths[column_index]
            draw.rectangle((x, row_y, cell_right, row_y + row_height), fill=fill, outline="#E2E8F0", width=2)
            text_y = row_y + 18
            for line in lines:
                draw.text((x + 14, text_y), line, fill="#1F2937", font=_font(24))
                text_y += 28
            x = cell_right
        row_y += row_height
        if row_y > 920:
            break

    draw.rounded_rectangle((82, 948, 998, 996), radius=18, fill=soft)
    draw.text((540, 972), "숫자와 자격은 최종 공고문 기준으로 다시 확인", fill=accent, font=_font(24), anchor="mm")

    image.save(output)
    return str(output)


def _section_by_raw_heading(sections: list[PublishSection], raw_heading: str) -> PublishSection | None:
    for section in sections:
        if section.raw_heading == raw_heading:
            return section
    return None


def _simple_rows_from_lines(lines: list[str], *, limit: int = 4) -> list[str]:
    rows: list[str] = []
    for raw_line in lines:
        line = _clean(raw_line.lstrip("-• "))
        if not line:
            continue
        if line.startswith("Q") and "." in line:
            continue
        if line.endswith("체크리스트"):
            continue
        rows.append(line)
        if len(rows) >= limit:
            break
    return rows


def _table_specs(title: str, sections: list[PublishSection]) -> list[dict[str, Any]]:
    kind = _topic_kind(title)
    key_slot = _publish_heading_for(title, "핵심 조건 정리")
    checklist_slot = _publish_heading_for(title, "체크리스트")
    checklist_section = _section_by_raw_heading(sections, "체크리스트")

    if kind == "ranking":
        checklist_rows = _simple_rows_from_lines(checklist_section.lines if checklist_section else [], limit=4)
        return [
            {
                "slot": key_slot,
                "file_name": "01_compare_table.png",
                "label": "일반공급 vs 특별공급 판단표",
                "headers": ["구분", "먼저 볼 것", "체크 포인트"],
                "rows": [
                    ["일반공급", "통장, 예치금, 거주요건", "소득보다 통장과 지역 조건이 우선"],
                    ["신혼부부 특공", "혼인기간, 무주택, 소득", "맞벌이 소득이 직접 변수"],
                    ["생애최초 특공", "무주택, 소득, 자산", "합산 소득과 자산을 같이 확인"],
                ],
            },
            {
                "slot": checklist_slot,
                "file_name": "02_checklist_table.png",
                "label": "신청 전 체크리스트",
                "headers": ["순서", "확인 항목"],
                "rows": [[str(idx + 1), item] for idx, item in enumerate(checklist_rows or [
                    "일반공급과 특별공급을 분리해서 판단하기",
                    "청약통장, 거주요건, 세대 조건 확인하기",
                    "당첨 가능성과 1순위 가능성을 따로 보기",
                    "입주자모집공고와 청약홈 최종 확인하기",
                ])],
            },
        ]

    if kind == "institution":
        checklist_rows = _simple_rows_from_lines(checklist_section.lines if checklist_section else [], limit=4)
        return [
            {
                "slot": key_slot,
                "file_name": "01_judgement_table.png",
                "label": "기관추천 특별공급 판단표",
                "headers": ["체크 항목", "왜 중요한가"],
                "rows": [
                    ["세대 무주택 여부", "현재 세대 전체가 무주택으로 인정되는지 먼저 봐야 합니다."],
                    ["배우자 주택 이력", "현재 보유인지, 과거 처분인지에 따라 판단이 크게 달라집니다."],
                    ["재당첨 제한", "현재 무주택이어도 과거 당첨 제한은 별도로 확인해야 합니다."],
                    ["추천기관 요건", "기관추천은 추천기관 자격과 제출서류 충족이 함께 필요합니다."],
                ],
            },
            {
                "slot": checklist_slot,
                "file_name": "02_checklist_table.png",
                "label": "기관추천 신청 전 체크리스트",
                "headers": ["순서", "확인 항목"],
                "rows": [[str(idx + 1), item] for idx, item in enumerate(checklist_rows or [
                    "세대 전원이 무주택으로 인정되는지 확인하기",
                    "배우자 주택 보유 이력을 현재/과거로 구분하기",
                    "재당첨 제한과 과거 특별공급 이력 확인하기",
                    "추천기관 안내와 입주자모집공고 원문 대조하기",
                ])],
            },
        ]

    if kind == "cashflow":
        checklist_rows = _simple_rows_from_lines(checklist_section.lines if checklist_section else [], limit=4)
        scenario_slot = _publish_heading_for(title, "실전 예시 시나리오")
        return [
            {
                "slot": key_slot,
                "file_name": "01_cashflow_table.png",
                "label": "분양 자금 계산표",
                "headers": ["항목", "예시", "메모"],
                "rows": [
                    ["계약금", "6,000만원", "분양가 6억원, 계약금 10% 가정"],
                    ["중도금", "3억6,000만원", "대출 가능 여부와 금리 조건 확인"],
                    ["잔금", "1억8,000만원", "입주 시점 잔금대출 한도가 핵심"],
                    ["별도비용", "옵션비·취득세", "누락하면 실제 필요 현금이 크게 늘어납니다."],
                ],
            },
            {
                "slot": scenario_slot,
                "file_name": "02_example_budget.png",
                "label": "분양가 예시 자금표",
                "headers": ["구간", "금액", "확인 포인트"],
                "rows": [
                    ["초기 투입", "계약금 6,000만원", "옵션비와 초기 부대비용을 함께 반영"],
                    ["중간 구간", "중도금 3억6,000만원", "대출 실행 가능 여부와 금리 조건 체크"],
                    ["입주 시점", "잔금 1억8,000만원", "잔금대출 한도 부족 여부 확인"],
                    ["추가 비용", "취득세·등기비용", "현금 부족을 만드는 구간이라 따로 계산"],
                ],
            },
            {
                "slot": checklist_slot,
                "file_name": "03_contract_checklist.png",
                "label": "계약 전 체크리스트",
                "headers": ["순서", "확인 항목"],
                "rows": [[str(idx + 1), item] for idx, item in enumerate(checklist_rows or [
                    "계약금, 중도금, 잔금 비율 확인하기",
                    "잔금대출 한도와 금리 시나리오 보기",
                    "옵션비, 취득세, 등기비용 포함하기",
                    "기존 보증금 회수 일정과 예비비 확보하기",
                ])],
            },
        ]

    fallback_rows = _simple_rows_from_lines((checklist_section.lines if checklist_section else []), limit=4)
    return [
        {
            "slot": checklist_slot,
            "file_name": "01_checklist_table.png",
            "label": "신청 전 체크리스트",
            "headers": ["순서", "확인 항목"],
            "rows": [[str(idx + 1), item] for idx, item in enumerate(fallback_rows or [
                "핵심 자격요건 먼저 확인하기",
                "일정과 자금 계획 같이 보기",
                "세대 기준 조건 다시 확인하기",
                "공고문과 청약홈으로 최종 점검하기",
            ])],
        }
    ]


def _strip_env_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _load_publish_env_if_present() -> None:
    protected = set(os.environ)
    for env_path in PUBLISH_ENV_CANDIDATES:
        if not env_path.exists() or not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            if not key or key in protected:
                continue
            os.environ[key] = _strip_env_quotes(value)


def _resolve_publish_image_provider(image_provider: str) -> str:
    provider = _clean(image_provider or "local").lower() or "local"
    if provider not in {"local", "auto", "gpt_web", "openai_compat"}:
        raise ValueError(f"지원하지 않는 image_provider 입니다: {provider}")
    if provider == "openai_compat":
        _load_publish_env_if_present()
        return provider
    if provider != "auto":
        return provider
    _load_publish_env_if_present()
    if os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("OPENAI_API_KEY"):
        return "openai_compat"
    return "gpt_web"


def _publish_image_excerpt(title: str, sections: list[PublishSection]) -> str:
    summary_section = _section_by_raw_heading(sections, "상단 요약")
    if summary_section:
        summary = " ".join(_simple_rows_from_lines(summary_section.lines, limit=3))
        if summary:
            return summary
    question_section = _section_by_raw_heading(sections, "이 글에서 바로 답하는 질문")
    if question_section:
        question_text = " ".join(_simple_rows_from_lines(question_section.lines, limit=3))
        if question_text:
            return question_text
    return _trim_text(title, max_len=140)


def _topic_visual_style(title: str) -> str:
    kind = _topic_kind(title)
    if kind == "ranking":
        return "한국 30대 맞벌이 부부와 청약 자격 검토 장면, 신뢰감 있는 파란색 계열, 블로그 본문 삽화 톤"
    if kind == "institution":
        return "가족 구성과 주택 이력을 서류로 검토하는 장면, 차분한 청록 계열, 상담형 인포그래픽 톤"
    if kind == "cashflow":
        return "분양 자금 계획표와 계약 일정을 함께 보는 장면, 따뜻한 주황 계열, 실용적인 재무 인포그래픽 톤"
    return "한국 부동산 정보 블로그에 맞는 깔끔한 인포그래픽 톤"


def _thumbnail_prompt_text(title: str, sections: list[PublishSection]) -> str:
    chips = ", ".join(THUMBNAIL_CHIPS.get(_topic_kind(title), THUMBNAIL_CHIPS["generic"])[:2])
    summary = _publish_image_excerpt(title, sections)
    return (
        "Use the uploaded image as a visual reference for mood and composition. "
        "Create a high-quality Korean chalkboard thumbnail in a realistic classroom style. "
        "Dark green blackboard, front view, realistic chalk dust, soft handwritten chalk typography, clean and premium composition. "
        "Make it feel cute, smart, organized, and easy to read at a glance. "
        "One strong focal point only, lots of empty space, very large readable Korean title, very short Korean phrases only. "
        "Place one centered main title, two small support chips, and tiny chalk doodles like arrows, stars, underline strokes, and a small house or check icon. "
        "Color palette: white, pink, yellow, sky blue, mint green on deep green chalkboard. "
        f"핵심 주제는 '{title}' 이고, 강조 포인트는 {chips} 입니다. 핵심 요약은 '{summary}' 입니다. "
        "텍스트는 최대 3개 덩어리만 쓰고, 각 덩어리는 1~3단어만 사용해 주세요. "
        "Negative prompt: public service campaign poster, government PSA, corporate ad banner, garbled Korean text, random symbols, extra English letters, cluttered layout, tiny text, low contrast, blurry chalk, warped perspective, printed poster font, overdecorated composition."
    )


SECTION_VISUAL_KIND_MAP = {
    "상단 요약": "summary",
    "이 글에서 바로 답하는 질문": "flow",
    "핵심 조건 정리": "diagram",
    "헷갈리기 쉬운 예외": "comparison",
    "실전 예시 시나리오": "scenario",
    "체크리스트": "checklist",
    "FAQ": "faq",
    "마무리 결론": "conclusion",
}



def _chalkboard_explainer_prompt(*, title: str, focus_title: str, detail_lines: list[str]) -> str:
    normalized_details = [_trim_text(_clean(line), max_len=28) for line in detail_lines if _clean(line)]
    details_text = " / ".join(normalized_details[:4]) or _trim_text(title, max_len=60)
    return (
        "Use the uploaded image as a visual reference for mood and composition. "
        "Create a high-quality Korean chalkboard infographic in a realistic classroom style. "
        "Dark green blackboard, front view, realistic chalk dust, handwritten chalk typography, neat educational poster design. "
        "Keep one image focused on only one micro-topic. Reduce the amount of information per image and maximize visibility. "
        "Use much larger chalk text, stronger contrast, fewer boxes, fewer arrows, and lots of empty space. "
        "Composition: - Big centered Korean title at the top - Left column with a very simple numbered step or dialogue box - Right upper section with one minimal flow diagram using colored circles - Middle section with one or two comparison boxes only - Bottom section with 2 or 3 key bullet points and one highlighted conclusion box - Small chalk doodles such as hearts, stars, arrows, and underline strokes - Chalk tray at the bottom with pastel chalk pieces. "
        "Typography: - Large readable Korean chalk headings - Only short Korean phrases, not long paragraphs - Accurate spacing and line breaks - Handwritten chalk style, soft but legible. "
        "Color palette: white, pink, yellow, sky blue, mint green on deep green chalkboard. "
        "Mood: smart, cute, analytical, organized, visually satisfying. "
        'Include these Korean headings exactly: "흐름 분석" "핵심 구조 요약" "핵심 포인트" "결론". '
        f"주제 설명 ({title}). 이번 이미지의 중심 주제는 '{focus_title}' 입니다. 꼭 반영할 핵심 내용은 {details_text} 입니다. "
        "각 박스에는 1~3단어짜리 짧은 한국어만 넣고, 전체 텍스트 덩어리는 4개 이내로 제한해 주세요. "
        "Negative prompt: public service campaign poster, government PSA, corporate ad banner, garbled Korean text, random symbols, extra English letters, messy composition, cluttered layout, tiny text, low contrast, blurry chalk, warped perspective, printed poster font, overdecorated layout."
    )



def _section_visual_kind(raw_heading: str) -> str:
    return SECTION_VISUAL_KIND_MAP.get(raw_heading, "diagram")



def _section_prompt_text(title: str, section: PublishSection) -> str:
    detail_lines = [section.publish_heading, *_simple_rows_from_lines(section.lines, limit=4)]
    return _chalkboard_explainer_prompt(title=title, focus_title=section.publish_heading, detail_lines=detail_lines)



def _spec_prompt_text(title: str, spec: dict[str, Any]) -> str:
    headers = [_clean(header) for header in spec.get("headers", [])]
    rows = [" | ".join(_clean(cell) for cell in row) for row in spec.get("rows", [])[:3]]
    label = _clean(spec.get("label")) or "보조 설명"
    detail_lines = [label, *headers, *rows]
    return _chalkboard_explainer_prompt(title=title, focus_title=label, detail_lines=detail_lines)



def _build_gpt_publish_image_plans(title: str, sections: list[PublishSection]) -> list[PublishImagePlan]:
    plans: list[PublishImagePlan] = [
        PublishImagePlan(
            slot="lead",
            kind="thumbnail",
            label="썸네일",
            image_role="thumbnail",
            prompt_text=_thumbnail_prompt_text(title, sections),
        )
    ]
    for section in sections:
        plans.append(
            PublishImagePlan(
                slot=section.publish_heading,
                kind=_section_visual_kind(section.raw_heading),
                label=f"{section.publish_heading} 설명 이미지",
                image_role="section_visual",
                prompt_text=_section_prompt_text(title, section),
            )
        )

    for spec in _table_specs(title, sections):
        label = _clean(spec.get("label")) or "보조 설명 이미지"
        plans.append(
            PublishImagePlan(
                slot=f"{_clean(spec.get('slot')) or _publish_heading_for(title, '핵심 조건 정리')}::before",
                kind="focus" if "체크리스트" not in label else "mini_checklist",
                label=f"{label} 보조 이미지",
                image_role="section_visual",
                prompt_text=_spec_prompt_text(title, spec),
            )
        )
    return plans


def _render_gpt_publish_assets(*, title: str, sections: list[PublishSection], output_dir: str | Path, provider: str) -> list[PublishAsset]:
    resolved_provider = _resolve_publish_image_provider(provider)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    excerpt = _publish_image_excerpt(title, sections)
    plans = _build_gpt_publish_image_plans(title, sections)
    assets: list[PublishAsset] = []
    job_seed = int(time.time() * 1000)

    if resolved_provider == "openai_compat":
        from .openai_compat import OpenAICompatExecutionError, execute_image_job as execute_openai_image_job

        _load_publish_env_if_present()
        if not (os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("OPENAI_API_KEY")):
            raise RuntimeError("GPT 이미지 API 키가 없어 openai_compat 경로를 사용할 수 없습니다.")

        for index, plan in enumerate(plans, start=1):
            output_path = output_root / f"{index:02d}_{plan.kind}.png"
            try:
                execution = execute_openai_image_job(
                    job_id=job_seed + index,
                    prompt_text=plan.prompt_text,
                    title=title,
                    excerpt=excerpt,
                    image_role=plan.image_role,
                    output_path=output_path,
                    artifact_root=Path("/home/kj/app/bunyang_longtail/dev/data/openai_compat_artifacts") / "naver_publish",
                )
            except OpenAICompatExecutionError as exc:
                detail = str(exc)
                if exc.artifact_dir:
                    detail += f" (artifact: {exc.artifact_dir})"
                raise RuntimeError(f"GPT 이미지 생성 실패: {plan.label}, provider={resolved_provider}, {detail}") from exc
            assets.append(PublishAsset(slot=plan.slot, kind=plan.kind, label=plan.label, path=str(Path(execution["file_path"]).resolve())))
        return assets

    from .gpt_web import GptWebExecutionError, execute_image_job as execute_gpt_web_image_job

    for index, plan in enumerate(plans, start=1):
        output_path = output_root / f"{index:02d}_{plan.kind}.png"
        try:
            execution = execute_gpt_web_image_job(
                job_id=job_seed + index,
                profile_name=PUBLISH_GPT_IMAGE_PROFILE,
                prompt_text=plan.prompt_text,
                title=title,
                excerpt=excerpt,
                image_role=plan.image_role,
                output_path=output_path,
                headed=True,
                wait_for_ready_seconds=180,
                response_timeout_seconds=600,
                artifact_root=Path("/home/kj/app/bunyang_longtail/dev/data/gpt_web_artifacts") / "naver_publish",
            )
        except GptWebExecutionError as exc:
            detail = str(exc)
            if exc.artifact_dir:
                detail += f" (artifact: {exc.artifact_dir})"
            raise RuntimeError(f"GPT 이미지 생성 실패: {plan.label}, provider={resolved_provider}, {detail}") from exc
        assets.append(PublishAsset(slot=plan.slot, kind=plan.kind, label=plan.label, path=str(Path(execution["file_path"]).resolve())))
    return assets


def _ensure_publish_asset_min_side(asset_path: str | Path, min_side_px: int) -> str:
    path = Path(asset_path)
    try:
        with Image.open(path) as image:
            width, height = image.size
            if min(width, height) >= min_side_px:
                return str(path.resolve())
            scale = max(min_side_px / max(width, 1), min_side_px / max(height, 1))
            resized = image.resize((max(int(round(width * scale)), min_side_px), max(int(round(height * scale)), min_side_px)), Image.LANCZOS)
            resized.save(path)
    except Exception:
        return str(path.resolve())
    return str(path.resolve())



def render_publish_assets(*, title: str, sections: list[PublishSection], output_dir: str | Path, image_provider: str = "local") -> list[PublishAsset]:
    resolved_provider = _resolve_publish_image_provider(image_provider)
    if resolved_provider != "local":
        return _render_gpt_publish_assets(title=title, sections=sections, output_dir=output_dir, provider=resolved_provider)

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    assets: list[PublishAsset] = []
    thumbnail_path = render_thumbnail_image(title=title, sections=sections, output_path=output_root / "00_thumbnail.png")
    assets.append(PublishAsset(slot="lead", kind="thumbnail", label="썸네일", path=str(Path(thumbnail_path).resolve())))

    topic = _topic_kind(title)
    if topic == "ranking":
        checklist_section = _section_by_raw_heading(sections, "체크리스트")
        checklist_items = _simple_rows_from_lines(checklist_section.lines if checklist_section else [], limit=5) or [
            "일반공급과 특별공급을 먼저 분리해서 보기",
            "청약통장 가입기간과 예치금 확인하기",
            "세대 기준 무주택과 재당첨 제한 점검하기",
            "입주자모집공고와 청약홈으로 최종 확인하기",
        ]
        key_slot = _publish_heading_for(title, "핵심 조건 정리")
        checklist_slot = _publish_heading_for(title, "체크리스트")

        intro_path = render_soft_illustration_image(
            title=title,
            label="맞벌이라고 1순위가 바로 막히는 것은 아닙니다",
            output_path=output_root / "01_intro_illustration.png",
        )
        assets.append(PublishAsset(slot="intro", kind="illustration", label="도입 삽화", path=str(Path(intro_path).resolve())))

        compare_path = render_table_image(
            title=title,
            label="일반공급 vs 특별공급 판단표",
            headers=["구분", "먼저 볼 것", "체크 포인트"],
            rows=[
                ["일반공급", "통장, 예치금, 거주요건", "소득보다 통장과 지역 조건이 우선"],
                ["신혼부부 특공", "혼인기간, 무주택, 소득", "맞벌이 소득이 직접 변수"],
                ["생애최초 특공", "무주택, 소득, 자산", "합산 소득과 자산을 같이 확인"],
            ],
            output_path=output_root / "02_compare_table.png",
        )
        assets.append(PublishAsset(slot=key_slot, kind="table", label="일반공급 vs 특별공급 판단표", path=str(Path(compare_path).resolve())))

        checklist_path = render_checklist_card_image(
            title=title,
            label="청약 1순위 신청 전 체크리스트",
            items=checklist_items,
            output_path=output_root / "03_checklist_card.png",
        )
        assets.append(PublishAsset(slot=checklist_slot, kind="checklist", label="청약 1순위 체크리스트 카드", path=str(Path(checklist_path).resolve())))
        return assets

    if topic == "cashflow":
        checklist_section = _section_by_raw_heading(sections, "체크리스트")
        checklist_items = _simple_rows_from_lines(checklist_section.lines if checklist_section else [], limit=5) or [
            "계약금, 중도금, 잔금 비율을 먼저 확인하기",
            "중도금 대출 조건과 금리 시나리오 확인하기",
            "옵션비와 취득세를 따로 계산표에 넣기",
            "잔금대출 한도와 기존 보증금 회수 일정 점검하기",
        ]
        key_slot = _publish_heading_for(title, "핵심 조건 정리")
        scenario_slot = _publish_heading_for(title, "실전 예시 시나리오")
        checklist_slot = _publish_heading_for(title, "체크리스트")

        intro_path = render_soft_illustration_image(
            title=title,
            label="계약금만 보면 가볍지만, 실제 현금은 더 큽니다",
            output_path=output_root / "01_intro_illustration.png",
        )
        assets.append(PublishAsset(slot="intro", kind="illustration", label="도입 삽화", path=str(Path(intro_path).resolve())))

        timeline_path = render_timeline_image(
            title=title,
            label="계약금 중도금 잔금 흐름도",
            output_path=output_root / "02_timeline.png",
        )
        assets.append(PublishAsset(slot=key_slot, kind="timeline", label="계약부터 입주까지 흐름도", path=str(Path(timeline_path).resolve())))

        table_path = render_table_image(
            title=title,
            label="분양가 6억 예시 자금표",
            headers=["구간", "금액", "확인 포인트"],
            rows=[
                ["계약일", "계약금 6,000만원", "계약금은 현금으로 바로 준비"],
                ["중도금", "3억6,000만원", "대출 가능 여부와 실행 조건 확인"],
                ["입주 전", "옵션비·취득세", "누락 시 잔금일 현금이 부족해짐"],
                ["입주일", "잔금 1억8,000만원", "잔금대출 한도 부족 여부 최종 확인"],
            ],
            output_path=output_root / "03_budget_table.png",
        )
        assets.append(PublishAsset(slot=scenario_slot, kind="table", label="분양가 예시 자금표", path=str(Path(table_path).resolve())))

        checklist_path = render_checklist_card_image(
            title=title,
            label="분양 계약 전 체크리스트",
            items=checklist_items,
            output_path=output_root / "04_checklist_card.png",
        )
        assets.append(PublishAsset(slot=checklist_slot, kind="checklist", label="계약 전 체크리스트 카드", path=str(Path(checklist_path).resolve())))
        return assets

    for spec in _table_specs(title, sections):
        table_path = render_table_image(
            title=title,
            label=str(spec["label"]),
            headers=list(spec["headers"]),
            rows=[list(row) for row in spec["rows"]],
            output_path=output_root / str(spec["file_name"]),
        )
        assets.append(
            PublishAsset(
                slot=str(spec["slot"]),
                kind="table",
                label=str(spec["label"]),
                path=str(Path(table_path).resolve()),
            )
        )
    return assets


def _section_lines_for_publish(title: str, section: PublishSection) -> list[str]:
    if _topic_kind(title) == "ranking" and section.raw_heading == "FAQ":
        return [
            "Q1. 맞벌이 소득이 높으면 청약 1순위는 아예 불가능한가요?",
            "일반공급은 통장과 거주요건 중심이라 별도로 가능할 수 있습니다. 다만 특별공급은 소득과 자산 기준에서 갈릴 수 있습니다.",
            "",
            "Q2. 신혼부부 특별공급이 안 되면 일반공급도 못 넣나요?",
            "그렇지 않습니다. 특별공급 불가와 일반공급 1순위 불가는 같은 말이 아닙니다.",
            "",
            "Q3. 30대 맞벌이 청약에서 먼저 볼 것은 통장인가요, 소득인가요?",
            "일반공급이면 통장과 거주요건을 먼저 보시고, 특별공급이면 소득과 자산을 먼저 보시는 것이 빠릅니다.",
            "",
            "Q4. 세대주가 아니면 청약 1순위가 안 되나요?",
            "공급유형과 공고문에 따라 다릅니다. 세대주 요건이 필요한지부터 먼저 확인하셔야 합니다.",
        ]

    out: list[str] = []
    previous_blank = False
    for raw_line in section.lines:
        line = raw_line.strip()
        if not line:
            if previous_blank:
                continue
            out.append("")
            previous_blank = True
            continue
        if section.raw_heading in {"이 글에서 바로 답하는 질문", "체크리스트"} and not line.startswith(("- ", "Q")):
            out.append(f"- {line}")
        else:
            out.append(line)
        previous_blank = False
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _lead_blocks(title: str, sections: list[PublishSection]) -> list[str]:
    topic = _topic_kind(title)
    if topic == "cashflow":
        return [
            "분양가 6억원이면 계약금 6천만원으로 끝나지 않습니다. 옵션비와 취득세, 잔금 시점 현금까지 같이 보면 실제 준비금이 더 커집니다.",
            "",
            "이 글에서 바로 가져가실 것 3가지",
            "- 계약금, 중도금, 잔금을 어떤 순서로 계산해야 하는지",
            "- 취득세, 옵션비, 잔금대출 한도를 어디서 놓치기 쉬운지",
            "- 분양가 6억원 예시로 실제 현금을 어떻게 잡아야 하는지",
        ]
    if topic == "ranking":
        return [
            "30대 맞벌이라고 청약 1순위가 바로 막히는 것은 아닙니다. 일반공급은 통장과 거주요건, 특별공급은 소득과 자산에서 갈립니다.",
            "",
            "이 글에서 바로 가져가실 것 3가지",
            "- 일반공급과 특별공급을 어떻게 나눠 봐야 하는지",
            "- 30대 맞벌이 청약에서 어디서 가장 자주 막히는지",
            "- 신청 전 무엇을 먼저 확인해야 실수가 줄어드는지",
        ]
    return [
        "청약 판단을 빨리 끝내려면 조건을 한 줄씩 분리해서 보셔야 합니다.",
        "",
        _intro_text(sections),
    ]


def build_publish_markdown(*, title: str, sections: list[PublishSection], assets: list[PublishAsset]) -> str:
    lines: list[str] = [f"# {title}", ""]

    slot_to_indexes: dict[str, list[int]] = {}
    for index, asset in enumerate(assets, start=1):
        slot_to_indexes.setdefault(asset.slot, []).append(index)

    for image_index in slot_to_indexes.get("lead", []):
        lines.append(f"[[IMAGE:{image_index}]]")
        lines.append("")

    lines.extend(_lead_blocks(title, sections))
    lines.append("")

    for image_index in slot_to_indexes.get("intro", []):
        lines.append(f"[[IMAGE:{image_index}]]")
        lines.append("")

    for section in sections:
        lines.append(f"## {section.publish_heading}")
        lines.append("")
        for image_index in slot_to_indexes.get(section.publish_heading, []):
            lines.append(f"[[IMAGE:{image_index}]]")
            lines.append("")
        for image_index in slot_to_indexes.get(f"{section.publish_heading}::before", []):
            lines.append(f"[[IMAGE:{image_index}]]")
            lines.append("")
        lines.extend(_section_lines_for_publish(title, section))
        lines.append("")
        for image_index in slot_to_indexes.get(f"{section.publish_heading}::after", []):
            lines.append(f"[[IMAGE:{image_index}]]")
            lines.append("")

    lines.append("일정과 비용은 수집 시점 기준일 수 있으니, 계약 전에는 입주자모집공고와 사업주체 안내로 다시 확인해보시기 바랍니다.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def default_tags(title: str) -> list[str]:
    tags = list(DEFAULT_TAGS)
    topic = _topic_kind(title)
    if topic == "ranking":
        tags.extend(["1순위조건", "일반공급", "특별공급", "30대맞벌이청약"])
    elif topic == "institution":
        tags.extend(["기관추천특별공급", "노부모부양", "특별공급자격", "무주택판정"])
    elif topic == "cashflow":
        tags.extend(["분양계약금", "중도금대출", "분양자금계획", "분양취득세", "청약준비"])
    else:
        tags.extend(["청약정보", "분양가이드"])

    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = _clean(tag).replace(" ", "")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered[:10]


def markdown_to_html(markdown: str) -> str:
    html_lines: list[str] = []
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:].strip()}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:].strip()}</h2>")
        elif re.fullmatch(r"\[\[IMAGE:\d+\]\]", line):
            html_lines.append(f"<p>{line}</p>")
        elif line.startswith("- "):
            html_lines.append(f"<p>• {line[2:].strip()}</p>")
        else:
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)


def build_publish_bundle(
    *,
    bundle_id: int,
    variant_title: str,
    article_markdown: str,
    output_root: str | Path,
    title_override: str | None = None,
    image_provider: str = "local",
) -> PublishBundle:
    prepared_article = _prepare_article_for_publish(title=variant_title, article_markdown=article_markdown)
    original_title, sections = parse_publish_sections(prepared_article, title_hint=variant_title)
    publish_title = title_override or build_publish_title(variant_title or original_title)
    output_dir = Path(output_root).resolve()
    images_dir = output_dir / "images"
    assets = render_publish_assets(title=publish_title, sections=sections, output_dir=images_dir, image_provider=image_provider)
    min_publish_side_px = int(str(os.getenv("NAVER_BLOG_MIN_IMAGE_SIDE_PX", "800")).strip() or "800")
    assets = [
        PublishAsset(slot=asset.slot, kind=asset.kind, label=asset.label, path=_ensure_publish_asset_min_side(asset.path, min_publish_side_px))
        for asset in assets
    ]
    markdown = build_publish_markdown(title=publish_title, sections=sections, assets=assets)
    body_html = markdown_to_html(markdown)
    tags = default_tags(publish_title)
    apt_id = f"longtail-bundle-{bundle_id}"
    meta = {
        "bundle_id": bundle_id,
        "title": publish_title,
        "assets": [asset.__dict__ for asset in assets],
        "images": [asset.path for asset in assets],
        "tags": tags,
        "image_provider": _resolve_publish_image_provider(image_provider),
    }
    meta_path = output_dir / "publish_bundle.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "body.md").write_text(markdown, encoding="utf-8")
    (output_dir / "body.html").write_text(body_html, encoding="utf-8")
    return PublishBundle(
        apt_id=apt_id,
        title=publish_title,
        markdown=markdown,
        images=[asset.path for asset in assets],
        tags=tags,
        body_html=body_html,
        meta_path=str(meta_path),
        assets=assets,
    )


def load_bundle_article(db_path: str | Path, bundle_id: int) -> dict[str, Any]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bundle = conn.execute("SELECT id, variant_id, primary_draft_id FROM article_bundle WHERE id = ?", (bundle_id,)).fetchone()
    if not bundle:
        raise ValueError(f"bundle_id={bundle_id} 를 찾지 못했습니다.")
    draft = conn.execute(
        "SELECT id, title, article_markdown FROM article_draft WHERE id = ?",
        (bundle["primary_draft_id"],),
    ).fetchone()
    if not draft:
        raise ValueError(f"draft_id={bundle['primary_draft_id']} 를 찾지 못했습니다.")
    return {
        "bundle_id": bundle["id"],
        "variant_id": bundle["variant_id"],
        "title": draft["title"],
        "article_markdown": draft["article_markdown"],
    }


def publish_bundle_to_naver(
    *,
    db_path: str | Path,
    bundle_id: int,
    output_root: str | Path,
    mode: str = "private",
    title_override: str | None = None,
    image_provider: str = "auto",
) -> dict[str, Any]:
    article = load_bundle_article(db_path, bundle_id)
    publish_bundle = build_publish_bundle(
        bundle_id=bundle_id,
        variant_title=article["title"],
        article_markdown=article["article_markdown"],
        output_root=output_root,
        title_override=title_override,
        image_provider=image_provider,
    )

    blog_root = Path("/home/kj/app/bunyang/blog-cheongyak-automation")
    if str(blog_root) not in sys.path:
        sys.path.insert(0, str(blog_root))

    from src.publisher.naver_playwright import publish as naver_publish

    out_dir = blog_root / "outputs" / "publish_longtail"
    ok = naver_publish(
        publish_bundle.apt_id,
        publish_bundle.title,
        publish_bundle.body_html,
        publish_bundle.images,
        mode=mode,
        out=str(out_dir),
        body_markdown=publish_bundle.markdown,
        tags=publish_bundle.tags,
    )
    result_path = out_dir / f"{publish_bundle.apt_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result["ok"] = ok
    result["bundle_id"] = bundle_id
    result["meta_path"] = publish_bundle.meta_path
    result["image_provider"] = _resolve_publish_image_provider(image_provider)
    return result
