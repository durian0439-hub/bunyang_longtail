from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from statistics import mean
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("BUNYANG_LONGTAIL_ROOT", Path(__file__).resolve().parents[2])).resolve()

from .config import GPT_WEB_ARTIFACT_DIR, OPENAI_COMPAT_ARTIFACT_DIR
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
    "auction": {
        "이 글에서 바로 답하는 질문": "입찰 전 먼저 확인할 것",
        "핵심 조건 정리": "경매 핵심 판단 기준",
        "헷갈리기 쉬운 예외": "초보가 자주 놓치는 경매 리스크",
        "실전 예시 시나리오": "입찰 가능·보류·회피 사례",
        "체크리스트": "경매 입찰 전 체크리스트",
        "FAQ": "경매 초보 FAQ",
        "마무리 결론": "최종 입찰 판단",
    },
    "tax": {
        "이 글에서 바로 답하는 질문": "세금 계산 전에 먼저 볼 것",
        "핵심 조건 정리": "부동산 세금 핵심 기준",
        "헷갈리기 쉬운 예외": "감면·중과·비과세에서 자주 틀리는 부분",
        "실전 예시 시나리오": "사례로 보는 세금 흐름",
        "체크리스트": "신고 전 체크리스트",
        "FAQ": "부동산 세금 FAQ",
        "마무리 결론": "최종 확인 순서",
    },
    "loan": {
        "이 글에서 바로 답하는 질문": "대출 가능성 전에 먼저 볼 것",
        "핵심 조건 정리": "부동산 대출 핵심 기준",
        "헷갈리기 쉬운 예외": "한도·금리·심사에서 자주 막히는 부분",
        "실전 예시 시나리오": "사례로 보는 대출 흐름",
        "체크리스트": "은행 상담 전 체크리스트",
        "FAQ": "부동산 대출 FAQ",
        "마무리 결론": "최종 확인 순서",
    },
}

TOPIC_COLORS = {
    "ranking": {"accent": "#2563EB", "soft": "#EFF6FF", "bg": "#EAF4FF"},
    "institution": {"accent": "#0F766E", "soft": "#ECFDF5", "bg": "#ECFDF5"},
    "cashflow": {"accent": "#EA580C", "soft": "#FFF7ED", "bg": "#FFF7ED"},
    "auction": {"accent": "#B45309", "soft": "#FEF3C7", "bg": "#FFF7ED"},
    "tax": {"accent": "#047857", "soft": "#ECFDF5", "bg": "#F0FDF4"},
    "loan": {"accent": "#1D4ED8", "soft": "#EFF6FF", "bg": "#F0F9FF"},
    "generic": {"accent": "#4338CA", "soft": "#EEF2FF", "bg": "#EEF2FF"},
}

THUMBNAIL_CHIPS = {
    "ranking": ["통장·거주요건", "특별공급 소득", "세대 기준 확인"],
    "institution": ["배우자 이력 구분", "세대 무주택 확인", "추천기관 기준 확인"],
    "cashflow": ["계약금 6천만원", "옵션비·취득세", "잔금 현금 확인"],
    "auction": ["권리·점유 확인", "입찰가 상한", "잔금·명도 점검"],
    "tax": ["세금 발생 시점", "감면·중과 확인", "신고기한 점검"],
    "loan": ["DSR·LTV 확인", "한도·금리 점검", "실행일 체크"],
    "generic": ["핵심 조건 먼저", "일정과 자금 점검", "공고문 최종 확인"],
}

NAVER_TAG_LIMIT = 30

DEFAULT_TAGS = [
    "청약",
    "분양청약",
    "아파트청약",
    "청약정보",
    "청약전략",
    "청약체크리스트",
    "청약가이드",
    "청약준비",
    "청약초보",
    "청약홈",
    "분양정보",
    "분양가이드",
    "아파트분양",
    "내집마련",
    "무주택자청약",
    "입주자모집공고",
    "일반공급",
    "특별공급",
    "청약통장",
    "부동산정보",
]

TAX_DEFAULT_TAGS = [
    "부동산세금",
    "주택세금",
    "아파트세금",
    "취득세",
    "양도소득세",
    "양도세",
    "재산세",
    "종합부동산세",
    "종부세",
    "증여세",
    "상속세",
    "부동산절세",
    "세금계산",
    "세금신고",
    "홈택스",
    "위택스",
    "부동산정보",
    "내집마련",
    "부동산공부",
]

LOAN_DEFAULT_TAGS = [
    "부동산대출",
    "주택담보대출",
    "주담대",
    "대출한도",
    "대출금리",
    "DSR",
    "LTV",
    "DTI",
    "스트레스DSR",
    "중도금대출",
    "잔금대출",
    "전세자금대출",
    "디딤돌대출",
    "보금자리론",
    "신생아특례대출",
    "전세퇴거자금대출",
    "대환대출",
    "은행상담",
    "부동산정보",
    "내집마련",
]

AUCTION_DEFAULT_TAGS = [
    "경매",
    "부동산경매",
    "법원경매",
    "법원경매정보",
    "경매정보",
    "경매공부",
    "경매초보",
    "경매입찰",
    "경매체크리스트",
    "경매권리분석",
    "아파트경매",
    "주택경매",
    "매각물건명세서",
    "등기부등본",
    "현황조사서",
    "입찰보증금",
    "낙찰",
    "낙찰후절차",
    "잔금납부",
    "명도",
    "부동산정보",
]

PUBLISH_ENV_CANDIDATES = [
    ROOT / ".env",
    ROOT / ".env.local",
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
    image_provider: str
    image_provider_requested: str
    image_provider_fallback_from: str | None = None
    image_provider_fallback_reason: str | None = None


@dataclass
class PublishAssetRenderResult:
    assets: list[PublishAsset]
    image_provider: str
    image_provider_requested: str
    image_provider_fallback_from: str | None = None
    image_provider_fallback_reason: str | None = None


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


def _content_domain(title: str, explicit_domain: str | None = None) -> str:
    if explicit_domain in {"auction", "cheongyak", "tax", "loan"}:
        return str(explicit_domain)
    text = _clean(_strip_heading_markers(title))
    auction_tokens = (
        "경매", "입찰", "낙찰", "말소기준권리", "권리분석", "선순위 임차인", "대항력",
        "배당요구", "명도", "인도명령", "유치권", "법정지상권", "지분경매", "온비드", "공매",
    )
    if any(token in text for token in auction_tokens):
        return "auction"
    tax_tokens = (
        "부동산 세금", "주택 세금", "취득세", "양도세", "양도소득세", "재산세", "종합부동산세", "종부세",
        "증여세", "상속세", "홈택스", "위택스", "비과세", "장기보유특별공제", "공동명의", "간주임대료", "필요경비",
    )
    if any(token in text for token in tax_tokens):
        return "tax"
    loan_tokens = (
        "부동산 대출", "주택담보대출", "주담대", "대출한도", "대출금리", "중도금 대출", "잔금대출",
        "전세자금대출", "DSR", "LTV", "DTI", "스트레스 DSR", "디딤돌대출", "보금자리론",
        "신생아 특례대출", "경락잔금대출", "전세퇴거자금", "대환대출", "은행 심사",
    )
    if any(token in text for token in loan_tokens):
        return "loan"
    return "cheongyak"


def _topic_kind(title: str) -> str:
    text = _clean(_strip_heading_markers(title))
    if _content_domain(text) == "auction":
        return "auction"
    if _content_domain(text) == "tax":
        return "tax"
    if _content_domain(text) == "loan":
        return "loan"
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


def _canonical_raw_heading(line: str) -> str | None:
    normalized = _clean(_strip_heading_markers(line)).rstrip(":：-–— ")
    for heading in RAW_SECTION_ORDER:
        if normalized == heading:
            return heading
        if normalized.startswith(f"{heading}:") or normalized.startswith(f"{heading}："):
            return heading
        if normalized.startswith(f"{heading} -") or normalized.startswith(f"{heading} –") or normalized.startswith(f"{heading} —"):
            return heading
    return None


def _split_article(article_markdown: str) -> tuple[str, dict[str, list[str]]]:
    lines = [line.rstrip() for line in str(article_markdown or "").splitlines()]
    filtered = [line for line in lines]
    title = _clean(_strip_heading_markers(filtered[0])) if filtered else "청약 how to"
    current_heading: str | None = None
    sections: dict[str, list[str]] = {heading: [] for heading in RAW_SECTION_ORDER}

    for raw_line in filtered[1:]:
        line = raw_line.strip()
        canonical_heading = _canonical_raw_heading(line)
        if canonical_heading:
            current_heading = canonical_heading
            continue
        if current_heading is None:
            continue
        sections[current_heading].append(raw_line)

    return title, {heading: _normalize_section_lines(value) for heading, value in sections.items() if _normalize_section_lines(value)}


def _section_count(article_markdown: str) -> int:
    _, sections = _split_article(article_markdown)
    return sum(1 for heading in RAW_SECTION_ORDER if sections.get(heading))


def _needs_article_expansion(article_markdown: str) -> bool:
    cleaned_len = len(_clean(article_markdown))
    if cleaned_len < 700:
        return True
    return cleaned_len < 1400 and _section_count(article_markdown) < 6


def _synthesize_article_for_publish(title: str, *, domain: str | None = None) -> str:
    content_domain = _content_domain(title, domain)
    normalized_title = _clean(_strip_heading_markers(title)) or ("부동산 경매 how to" if content_domain == "auction" else "부동산 세금 how to" if content_domain == "tax" else "부동산 대출 how to" if content_domain == "loan" else "분양청약 how to")

    if content_domain == "loan":
        return f"""{normalized_title}
상단 요약

부동산 대출은 상품 이름보다 내 소득, 기존 대출, 주택 수, 담보가치, 실행일을 먼저 봐야 합니다. 같은 주택담보대출이나 잔금대출이라도 DSR, LTV, 신용, 은행 심사에 따라 한도와 금리가 달라질 수 있습니다.

이 글은 금융 상담을 대신하는 글이 아니라, 은행 상담 전에 내 상황에서 무엇을 정리해야 하는지 보는 체크용 글입니다.

이 글에서 바로 답하는 질문

어떤 대출을 먼저 확인해야 하는가
한도와 금리는 어떤 기준에서 달라지는가
DSR, LTV, 실행일에서 자주 막히는 부분은 무엇인가
은행 상담 전에 어떤 서류와 조건을 준비해야 하는가

핵심 조건 정리

부동산 대출은 보통 매매계약 전, 중도금, 잔금, 전세보증금 반환, 대환 단계로 나눠서 보면 덜 헷갈립니다. 먼저 내가 지금 어느 단계에 있는지 정해야 합니다.
- 매매 전이면 주담대 한도, LTV, DSR, 자기자금을 봅니다.
- 분양 중이면 중도금대출과 잔금대출 전환 가능성을 봅니다.
- 입주 직전이면 잔금 실행일, 필요서류, 기존 대출을 봅니다.
- 전세보증금을 돌려줘야 하면 전세퇴거자금과 보증금 반환 한도를 봅니다.
- 금리가 부담되면 대환 가능성과 중도상환수수료를 함께 봅니다.

헷갈리기 쉬운 예외

대출한도는 단순히 집값에 비례하지 않습니다. 소득, 신용점수, 기존 대출, 주택 수, 규제지역, 담보가치, 금융기관 심사가 같이 들어갑니다.

정책대출도 이름만 보고 가능하다고 판단하면 안 됩니다. 소득기준, 자산기준, 주택가격, 세대 요건, 신청 시점이 맞아야 합니다.

실전 예시 시나리오

예를 들어 분양 아파트 입주를 앞둔 실수요자라면 잔금대출만 보면 부족합니다. 중도금대출 상환, 옵션비, 취득세, 이사비, 기존 전세보증금 회수 시점까지 한 표에 놓고 봐야 합니다.

체크리스트

내 대출 목적이 매매, 중도금, 잔금, 전세, 대환 중 어디에 해당하는지 나눴는가
연소득, 기존 대출, 신용점수, 주택 수를 정리했는가
DSR, LTV, DTI 중 어떤 기준이 내 한도에 영향을 주는지 확인했는가
대출 실행일과 잔금일, 입주일이 맞는지 확인했는가
필요서류와 은행 심사 기간을 미리 확인했는가
한국주택금융공사, 주택도시기금, 금융감독원, 은행 상담으로 최종 조건을 확인했는가

FAQ

Q1. 대출한도 계산기만 보면 되나요?
계산기는 대략적인 범위를 보는 데 도움이 됩니다. 다만 실제 한도는 소득, 신용, 기존 대출, 담보평가, 은행 심사에 따라 달라질 수 있습니다.

Q2. DSR이 낮으면 무조건 대출이 많이 나오나요?
그렇게 단정하기 어렵습니다. DSR 외에도 LTV, 주택 수, 담보가치, 금융기관 내부 기준이 함께 작용합니다.

Q3. 정책대출은 조건만 맞으면 바로 되나요?
조건에 가까워 보여도 소득, 자산, 주택가격, 세대 요건, 신청 가능 시점이 맞아야 합니다. 공식 안내와 은행 상담으로 확인해야 합니다.

Q4. 최종 확인은 어디서 해야 하나요?
정책대출은 한국주택금융공사와 주택도시기금 안내를 먼저 보고, 실제 실행 가능성은 취급 은행 상담으로 확인하는 것이 좋습니다.

마무리 결론

부동산 대출은 상품명을 외우는 문제가 아니라 내 소득, 주택 수, 기존 대출, 담보가치, 실행일을 순서대로 맞추는 문제입니다. 먼저 필요한 대출 종류를 나누고, DSR과 LTV, 필요서류를 정리한 뒤 은행 상담으로 마지막 확인을 거치면 실수를 줄일 수 있습니다.
"""

    if content_domain == "tax":
        return f"""{normalized_title}
상단 요약

부동산 세금은 세목 이름보다 세금이 생기는 시점과 계산 기준을 먼저 나눠야 합니다. 같은 취득세나 양도세라도 주택 수, 보유기간, 명의, 취득·양도 시점에 따라 결과가 달라질 수 있습니다.

이 글은 세무 상담을 대신하는 글이 아니라, 홈택스·위택스·지자체 안내를 확인하기 전에 내 상황에서 무엇을 먼저 정리해야 하는지 보는 체크용 글입니다.

이 글에서 바로 답하는 질문

어떤 세금이 언제 발생하는가
계산 전에 어떤 기준을 먼저 확인해야 하는가
감면, 중과, 비과세에서 자주 놓치는 부분은 무엇인가
신고 전에 어떤 서류와 기한을 챙겨야 하는가

핵심 조건 정리

부동산 세금은 보통 취득, 보유, 양도, 증여·상속, 임대소득 단계로 나눠서 보면 헷갈림이 줄어듭니다. 먼저 내가 지금 어느 단계에 있는지 정해야 합니다.
- 매수나 분양 잔금 전이면 취득세와 지방교육세를 봅니다.
- 보유 중이면 재산세, 종합부동산세, 과세기준일을 봅니다.
- 매도 전이면 양도세, 보유기간, 거주기간, 필요경비를 봅니다.
- 가족 간 이전이면 증여세, 상속세, 자금출처를 함께 봅니다.
- 임대를 놓으면 임대소득 신고 여부를 확인합니다.

헷갈리기 쉬운 예외

감면이나 비과세는 이름만 보고 적용하면 안 됩니다. 생애최초 취득세 감면, 1세대 1주택 비과세, 장기보유특별공제처럼 익숙한 제도도 취득 시점, 주택 수, 보유·거주기간, 가격 기준에 따라 달라질 수 있습니다.

공동명의도 항상 유리하다고 단정하기 어렵습니다. 종부세, 양도세, 증여세, 건강보험료 같은 다른 부담까지 같이 봐야 합니다.

실전 예시 시나리오

예를 들어 1주택자가 새 집을 먼저 사고 기존 집을 나중에 파는 경우라면 취득세와 양도세를 따로 보면 안 됩니다. 새 집 취득 시점, 기존 집 처분 기한, 일시적 2주택 요건, 양도세 비과세 가능성을 한 표에 놓고 봐야 합니다.

분양권이나 입주권을 가진 경우도 마찬가지입니다. 일반 주택과 세금 처리 시점이 다를 수 있으니 계약일, 잔금일, 전매 여부, 주택 수 포함 여부를 따로 확인해야 합니다.

체크리스트

내 상황이 취득, 보유, 양도, 증여·상속, 임대 중 어디에 해당하는지 나눴는가
주택 수, 명의, 보유기간, 거주기간을 날짜 기준으로 정리했는가
취득가액, 필요경비, 공시가격, 실거래가 중 어떤 금액을 써야 하는지 확인했는가
감면이나 비과세 요건을 공식 안내 기준으로 다시 봤는가
신고기한과 납부기한을 놓치지 않게 캘린더에 표시했는가
홈택스, 위택스, 지자체 안내 또는 세무 전문가 확인이 필요한 부분을 따로 표시했는가

FAQ

Q1. 부동산 세금은 계산기로만 보면 되나요?
계산기는 대략적인 금액을 보는 데 도움이 됩니다. 다만 주택 수, 보유기간, 감면 요건을 잘못 넣으면 결과도 달라지기 때문에 공식 안내와 함께 봐야 합니다.

Q2. 1주택이면 세금 걱정이 적은가요?
1주택이라고 항상 단순하지는 않습니다. 취득 시점, 보유기간, 거주기간, 가격 기준에 따라 취득세와 양도세 결과가 달라질 수 있습니다.

Q3. 공동명의는 무조건 절세인가요?
그렇게 단정하기 어렵습니다. 세목마다 유리한 지점이 다르고, 증여세나 건강보험료 같은 다른 부담도 같이 봐야 합니다.

Q4. 최종 확인은 어디서 해야 하나요?
취득세와 재산세는 위택스와 지자체 안내를, 양도세·증여세·상속세·종합소득세는 국세청과 홈택스를 먼저 확인하는 것이 좋습니다. 금액이 크거나 조건이 복잡하면 세무 전문가 상담이 필요할 수 있습니다.

마무리 결론

부동산 세금은 한 번에 외우는 문제가 아니라 내 거래 시점과 보유 상황을 순서대로 맞추는 문제입니다. 먼저 세금이 생기는 단계를 나누고, 주택 수와 날짜, 명의, 금액 기준을 정리한 뒤 공식 안내로 마지막 확인을 거치면 실수를 줄일 수 있습니다.
"""

    if content_domain == "auction":
        return f"""{normalized_title}
상단 요약

경매초보라면 입찰 전 좋은 물건을 찾기보다 피해야 할 리스크를 먼저 걸러야 합니다. 권리관계, 점유 상태, 자금 계획 중 하나라도 설명이 안 되면 바로 입찰보다 보류가 안전합니다.

법원경매정보의 사건 기본값을 확인한 뒤 매각물건명세서, 현황조사서, 감정평가서, 등기부등본, 전입세대열람을 순서대로 맞춰 보셔야 합니다. 마지막에는 입찰보증금, 잔금, 취득세, 명도 비용까지 현금 흐름으로 계산해야 합니다.

이 글에서 바로 답하는 질문

경매초보가 입찰 전에 무엇부터 확인해야 하는가
법원경매정보와 매각물건명세서를 어떤 순서로 봐야 하는가
권리분석, 점유, 잔금 리스크 중 어디서 손실이 커지는가
입찰 가능, 보류, 회피를 어떻게 나눠야 하는가

핵심 조건 정리

경매 체크리스트는 가격이 싸 보이는 물건을 고르는 표가 아니라 입찰하면 안 되는 물건을 먼저 제외하는 필터입니다. 처음에는 아래 순서로 보시면 판단이 덜 흔들립니다.
- 법원경매정보에서 사건번호, 매각기일, 최저가, 보증금을 확인합니다.
- 매각물건명세서에서 인수될 수 있는 권리와 임차인 정보를 봅니다.
- 현황조사서와 전입세대열람으로 실제 점유 상태를 맞춰 봅니다.
- 등기부등본으로 말소기준권리와 후순위 권리를 확인합니다.
- 대출 가능액, 잔금 납부기한, 취득세, 명도 비용을 현금표에 넣습니다.

헷갈리기 쉬운 예외

유찰이 많다고 무조건 기회는 아닙니다. 권리 인수, 점유 갈등, 대출 제한, 수리비 때문에 낮아진 가격일 수 있습니다.

임차인이 있다고 모두 위험한 것도 아닙니다. 핵심은 임차인 유무가 아니라 낙찰자가 인수할 보증금이나 추가 비용이 남는지입니다.

명도는 법적 절차만으로 끝나지 않을 수 있습니다. 협의 비용, 인도명령, 강제집행 가능성까지 시간을 잡아야 합니다.

실전 예시 시나리오

최저가가 시세보다 낮은 아파트라도 선순위 임차인 보증금이 남거나 점유자가 협조하지 않을 가능성이 크면 초보자는 보류가 맞습니다. 반대로 권리 인수 위험이 낮고 잔금대출과 명도 비용까지 계산이 끝난 물건이라면 입찰가 상한을 정한 뒤 접근할 수 있습니다.

체크리스트

법원경매정보에서 사건번호, 매각기일, 보증금을 확인했는가
매각물건명세서에서 인수 권리와 임차인 정보를 확인했는가
현황조사서와 전입세대열람으로 점유 상태를 맞춰 봤는가
등기부등본에서 말소기준권리보다 앞선 권리를 확인했는가
입찰가 상한, 잔금대출, 취득세, 명도 비용을 한 번에 계산했는가
현장 방문이나 주변 시세 확인 없이 가격만 보고 판단하지 않았는가

FAQ

Q1. 경매초보는 무엇부터 봐야 하나요?
A. 가격보다 법원경매정보, 매각물건명세서, 점유 상태, 자금 계획 순서로 보시는 것이 안전합니다.

Q2. 유찰이 많으면 좋은 물건인가요?
A. 아닐 수 있습니다. 왜 유찰됐는지 권리관계와 점유, 대출 가능성까지 확인하셔야 합니다.

Q3. 권리분석이 어려우면 입찰해도 되나요?
A. 설명이 안 되는 권리가 있으면 보류하는 편이 낫습니다. 초보자는 모르는 리스크를 가격으로 보상받기 어렵습니다.

Q4. 최종 확인은 어디서 해야 하나요?
A. 법원경매정보와 사건 서류를 기본으로 보고, 필요하면 등기부등본, 전입세대열람, 대출 상담, 현장 확인까지 맞춰 보셔야 합니다.

마무리 결론

경매초보에게 첫 목표는 낙찰이 아니라 손실을 피하는 것입니다. 입찰 전에는 권리, 점유, 자금, 명도 리스크를 한 장 체크리스트로 정리하고 하나라도 설명이 안 되면 보류하는 기준을 먼저 세우셔야 합니다.
"""

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


def _prepare_article_for_publish(*, title: str, article_markdown: str, domain: str | None = None) -> str:
    if not _needs_article_expansion(article_markdown):
        return article_markdown
    return _synthesize_article_for_publish(title, domain=domain)


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
    title = title.replace('입주자모집공고과', '입주자모집공고와')
    title = title.replace('거주의무과', '거주의무와')
    title = title.replace(' | 분양청약 how to 정리', '')
    title = title.replace('분양청약 how to 정리', '')
    title = title.replace('확인포인트', '')
    title = title.replace('핵심 포인트', '')
    title = title.replace('체크 포인트', '')
    title = title.replace('탈락 포인트', '')
    title = re.sub(r'([가-힣A-Za-z0-9]+)\s+기준\s+기준\b', r'\1 기준', title)
    title = re.sub(r'([가-힣A-Za-z0-9]+)\s+정리\s+정리\b', r'\1 정리', title)
    title = ' '.join(title.split())
    return title.rstrip(' ,')


def _intro_text(sections: list[PublishSection], *, domain: str | None = None) -> str:
    fallback = (
        "오늘은 경매 입찰 전 권리, 점유, 자금 리스크를 어떤 순서로 확인해야 하는지 빠르게 정리해보겠습니다."
        if domain == "auction"
        else "오늘은 부동산 세금이 언제 생기고 어떤 기준으로 계산되는지 빠르게 정리해보겠습니다."
        if domain == "tax"
        else "오늘은 분양청약 판단이 헷갈릴 때 어떻게 순서를 잡아야 하는지 빠르게 정리해보겠습니다."
    )
    if not sections:
        return fallback
    source = _summary_source(sections[0].publish_heading, "", article_markdown="\n".join(sections[0].lines))
    text = _clean(source).replace("...", "").replace("…", "")
    if text:
        return text
    return fallback


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


def render_thumbnail_image(*, title: str, sections: list[PublishSection], output_path: str | Path, domain: str | None = None) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    content_domain = _content_domain(title, domain)
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
        "auction": "경매 입찰",
    }.get(kind, "경매" if content_domain == "auction" else "분양청약")
    draw.rounded_rectangle((88, 88, 286, 142), radius=22, fill="#0F172A")
    draw.text((187, 115), badge_text, fill="white", font=_font(30), anchor="mm")

    title_lines = _thumbnail_title_lines(draw, title)
    y = 190
    for idx, line in enumerate(title_lines):
        draw.text((92, y), line, fill="#0F172A" if idx < len(title_lines) - 1 else accent, font=_font(60))
        y += 76

    summary_lines = _fit_lines(draw, _intro_text(sections, domain=content_domain), max_width=540, font_size=30, max_lines=3)
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
    footer_text = (
        "최종 확인은 법원경매정보와 사건 서류·현장 점검으로 확인"
        if content_domain == "auction"
        else "표 중심 본문, 최종 자격과 일정은 공고문과 청약홈으로 확인"
    )
    draw.text((540, 883), footer_text, fill="#334155", font=_font(27), anchor="mm")

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
    width = height = 1080
    image = Image.new("RGB", (width, height), "#163B2E")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        top = (20, 58, 45)
        bottom = (14, 42, 33)
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[idx] * (1 - ratio) + bottom[idx] * ratio) for idx in range(3))
        draw.line((0, y, width, y), fill=color)

    for x in range(0, width, 32):
        shade = 18 + (x % 64)
        draw.line((x, 0, x, height), fill=(18, 52 + shade // 8, 40 + shade // 10), width=1)
    for y in range(0, height, 28):
        draw.line((0, y, width, y), fill=(22, 63, 48), width=1)

    board_fill = "#1B4B3A"
    border = "#A7F3D0"
    chalk_white = "#F8FAFC"
    chalk_yellow = "#FDE68A"
    chalk_mint = "#A7F3D0"
    chalk_blue = "#BFDBFE"
    chalk_pink = "#F9A8D4"

    draw.rounded_rectangle((42, 42, 1038, 1038), radius=44, fill=board_fill, outline=border, width=4)
    draw.rounded_rectangle((86, 84, 292, 140), radius=24, fill="#0F2E23", outline=chalk_mint, width=2)
    draw.text((189, 112), "칠판 표 정리", fill=chalk_yellow, font=_font(28), anchor="mm")
    draw.text((950, 104), "데일리어셈블", fill=chalk_mint, font=_font(18), anchor="mm")

    title_lines = _fit_lines(draw, title, max_width=860, font_size=34, max_lines=2)
    y = 182
    for line in title_lines:
        draw.text((88, y), line, fill=chalk_blue, font=_font(32))
        y += 40

    draw.text((88, 272), _trim_text(label, 32), fill=chalk_white, font=_font(46))
    draw.text((88, 328), "표도 본문과 같은 칠판 컨셉으로 맞춰 한눈에 보이게 정리했습니다.", fill=chalk_mint, font=_font(24))

    left = 82
    right = 998
    top = 392
    header_height = 78
    table_width = right - left
    ratios = _table_column_widths(len(headers))
    widths = [int(table_width * ratio) for ratio in ratios]
    widths[-1] = table_width - sum(widths[:-1])

    table_bottom = 926
    draw.rounded_rectangle((left, top, right, table_bottom), radius=28, outline=chalk_white, width=3)

    x = left
    header_colors = [chalk_yellow, chalk_mint, chalk_blue, chalk_pink]
    for index, header in enumerate(headers):
        cell_right = x + widths[index]
        fill = "#204F3D" if index % 2 == 0 else "#1E4637"
        draw.rounded_rectangle((x, top, cell_right, top + header_height), radius=0, fill=fill, outline=chalk_white, width=2)
        header_lines = _fit_lines(draw, header, max_width=widths[index] - 30, font_size=24, max_lines=2)
        draw.multiline_text(
            (x + widths[index] / 2, top + header_height / 2),
            "\n".join(header_lines),
            fill=header_colors[index % len(header_colors)],
            font=_font(24),
            anchor="mm",
            align="center",
            spacing=4,
        )
        x = cell_right

    row_y = top + header_height
    row_palette = ["#173F31", "#1A4636"]
    for row_index, row in enumerate(rows[:5]):
        cell_lines: list[list[str]] = []
        max_lines = 1
        for column_index, cell in enumerate(row):
            column_width = widths[column_index] - 28
            lines = _wrap_text(draw, str(cell or ""), _font(22), column_width, 4)
            cell_lines.append(lines)
            max_lines = max(max_lines, len(lines))

        row_height = max(90, 28 * max_lines + 34)
        x = left
        fill = row_palette[row_index % len(row_palette)]
        text_color = chalk_white if row_index % 2 == 0 else chalk_mint
        for column_index, lines in enumerate(cell_lines):
            cell_right = x + widths[column_index]
            draw.rectangle((x, row_y, cell_right, row_y + row_height), fill=fill, outline=chalk_white, width=2)
            text_y = row_y + 16
            line_color = text_color if column_index != 0 else chalk_yellow
            for line in lines:
                draw.text((x + 14, text_y), line, fill=line_color, font=_font(22))
                text_y += 28
            x = cell_right
        row_y += row_height
        if row_y > 900:
            break

    draw.rounded_rectangle((84, 950, 996, 998), radius=18, fill="#0F2E23", outline=chalk_mint, width=2)
    footer_text = (
        "권리와 점유는 법원 서류와 현장 기준으로 다시 확인"
        if _content_domain(title) == "auction"
        else "숫자와 자격은 최종 공고문 기준으로 다시 확인"
    )
    draw.text((540, 974), footer_text, fill=chalk_yellow, font=_font(24), anchor="mm")

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

    if kind == "auction":
        checklist_rows = _simple_rows_from_lines((checklist_section.lines if checklist_section else []), limit=5)
        return [
            {
                "slot": key_slot,
                "file_name": "01_auction_risk_table.png",
                "label": "경매 입찰 전 리스크 판단표",
                "headers": ["확인 항목", "먼저 볼 것", "주의 포인트"],
                "rows": [
                    ["권리", "매각물건명세서·등기부등본", "인수 권리와 말소기준권리 확인"],
                    ["점유", "현황조사서·전입세대열람", "명도 시간과 비용 예상"],
                    ["자금", "보증금·잔금·대출 상담", "잔금 공백과 취득세 반영"],
                    ["가격", "실거래·낙찰가율·수리비", "입찰가 상한을 먼저 고정"],
                ],
            },
            {
                "slot": checklist_slot,
                "file_name": "02_auction_checklist_table.png",
                "label": "경매 입찰 전 체크리스트",
                "headers": ["순서", "확인 항목"],
                "rows": [[str(idx + 1), item] for idx, item in enumerate(checklist_rows or [
                    "법원경매정보에서 사건번호와 매각기일 확인하기",
                    "매각물건명세서로 인수 권리 확인하기",
                    "현황조사서와 전입세대열람으로 점유 상태 맞추기",
                    "잔금대출, 취득세, 명도 비용까지 현금표에 넣기",
                ])],
            },
        ]

    fallback_rows = _simple_rows_from_lines((checklist_section.lines if checklist_section else []), limit=4)
    return [
        {
            "slot": key_slot,
            "file_name": "01_decision_table.png",
            "label": "핵심 판단 기준표",
            "headers": ["확인 항목", "먼저 볼 기준", "주의 포인트"],
            "rows": [
                ["자격", "세대·주택 수·거주요건", "공고문 기준일로 다시 확인"],
                ["일정", "모집공고·접수·발표일", "마감 시간을 따로 체크"],
                ["자금", "계약금·중도금·잔금", "대출 가능 여부를 먼저 상담"],
                ["리스크", "부적격·예비당첨·계약 포기", "신청 전 체크리스트로 재점검"],
            ],
        },
        {
            "slot": checklist_slot,
            "file_name": "02_checklist_table.png",
            "label": "신청 전 체크리스트",
            "headers": ["순서", "확인 항목"],
            "rows": [[str(idx + 1), item] for idx, item in enumerate(fallback_rows or [
                "핵심 자격요건 먼저 확인하기",
                "일정과 자금 계획 같이 보기",
                "세대 기준 조건 다시 확인하기",
                "공고문과 청약홈으로 최종 점검하기",
            ])],
        },
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
    if kind == "auction":
        return "법원경매정보와 사건 서류, 체크리스트를 함께 보는 장면, 따뜻한 브라운 계열, 실전형 경매 인포그래픽 톤"
    return "한국 부동산 정보 블로그에 맞는 깔끔한 인포그래픽 톤"


def _thumbnail_prompt_text(title: str, sections: list[PublishSection]) -> str:
    chips = ", ".join(THUMBNAIL_CHIPS.get(_topic_kind(title), THUMBNAIL_CHIPS["generic"])[:2])
    summary = _publish_image_excerpt(title, sections)
    return (
        "Use the uploaded image as a visual reference for mood and composition. "
        "Create a high-quality Korean chalkboard thumbnail in a realistic classroom style. "
        "A dark green blackboard must fill the whole frame edge to edge. No white poster background, no white card, no empty light gray square, no paper sheet. "
        "Front view, realistic chalk dust, thick handwritten chalk typography, strong contrast, clean and premium composition. "
        "Make it feel cute, smart, organized, and easy to read at a glance. "
        "Place one very large Korean title or hook directly on the chalkboard, plus one or two tiny support chips. "
        "The title text should actually appear on the image in Korean, large and readable. "
        "Use very short Korean wording only, 1 to 6 words per text block, max 3 text blocks total. "
        "Add tiny chalk doodles like arrows, stars, underline strokes, and a small house or check icon. "
        "Add a very small watermark text '데일리어셈블' near the upper right corner, placed diagonally, subtle but readable. "
        "Color palette: white, pink, yellow, sky blue, mint green on deep green chalkboard. "
        f"핵심 주제는 '{title}' 이고, 강조 포인트는 {chips} 입니다. 핵심 요약은 '{summary}' 입니다. "
        "Negative prompt: white background, white card, blank poster, empty square, washed out board, public service campaign poster, government PSA, corporate ad banner, garbled Korean text, random symbols, extra English letters, cluttered layout, tiny text, low contrast, blurry chalk, warped perspective, printed poster font, overdecorated composition."
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



def _visual_prompt_style(raw_heading: str, kind: str) -> str:
    if kind == "summary":
        return (
            "Create a Korean summary board with three oversized takeaway cards, one short headline, and one small conclusion chip. "
            "Do not use a 4-panel grid. Use asymmetric spacing and make the middle takeaway visually dominant. "
        )
    if kind == "flow":
        return (
            "Create a Korean flowchart board. Show a left-to-right or top-to-bottom decision flow with arrows, circles, and only 3 or 4 steps. "
            "Do not use repeated equal-size boxes. The image must read like a process, not a poster grid. "
        )
    if kind == "comparison":
        return (
            "Create a Korean comparison board with two large side-by-side cards. Left is one option, right is the other option. "
            "Use contrast colors and a clear versus structure. Do not add extra mini boxes beyond the two main cards and one small verdict strip. "
        )
    if kind == "decision_board":
        return (
            "Create a Korean decision board derived from a table. Put one huge central keyword or verdict, one left checklist column, one right why-it-matters column, and one small bottom reminder strip. "
            "Do not draw spreadsheet cells, rigid grid lines, or a literal matrix table. It should feel like a hand-drawn chalkboard judgment board. "
        )
    if kind in {"checklist", "mini_checklist"}:
        return (
            "Create a Korean checklist board with large checkboxes, short action items, and one highlighted warning note. "
            "Make it feel like a practical inspection sheet, not an infographic poster. "
        )
    if kind == "scenario":
        return (
            "Create a Korean scenario board. Show one realistic case as a branching path: condition, result, next action. "
            "At the bottom, add one concrete action strip telling the reader what to do next. "
        )
    if kind == "faq":
        return (
            "Create a Korean Q&A board with one big question bubble and 2 or 3 short answer chips. "
            "It should feel conversational, not like a repeated template. "
        )
    if kind == "conclusion":
        return (
            "Create a Korean CTA board. One large closing headline, three short action boxes, and one strong final reminder. "
            "It should feel like an action prompt, not a summary poster. "
        )
    if kind == "focus":
        return (
            "Create a Korean spotlight card with one dominant metric or rule, one short explanation, and one tiny note. "
            "Keep the layout minimal and centered. "
        )
    return (
        "Create a Korean explainer board with one dominant headline, one main diagram, and one short takeaway strip. "
        "Avoid any repeated 4-box layout. "
    )



def _chalkboard_explainer_prompt(*, title: str, focus_title: str, detail_lines: list[str], kind: str) -> str:
    normalized_details = [_trim_text(_clean(line), max_len=28) for line in detail_lines if _clean(line)]
    details_text = " / ".join(normalized_details[:4]) or _trim_text(title, max_len=60)
    style = _visual_prompt_style(focus_title, kind)
    return (
        "Use the uploaded image as a visual reference for mood and composition. "
        "Create a high-quality Korean chalkboard infographic in a realistic classroom style. "
        "A dark green blackboard must fill the whole frame edge to edge. No white poster background, no white card, no blank square, no empty light background. "
        "Front view, realistic chalk dust, handwritten chalk typography, neat educational poster design, thick chalk lines, strong contrast. "
        "Keep one image focused on only one micro-topic. Reduce the amount of information per image and maximize visibility. "
        "Use much larger chalk text, fewer boxes, fewer arrows, and lots of empty space. "
        f"{style}"
        "Typography: - Large readable Korean chalk headings - Only short Korean phrases, not long paragraphs - Accurate spacing and line breaks - Handwritten chalk style, soft but legible. "
        "Add a very small watermark text '데일리어셈블' near the upper right corner, placed diagonally, subtle but readable. "
        "Color palette: white, pink, yellow, sky blue, mint green on deep green chalkboard. "
        "Mood: smart, cute, analytical, organized, visually satisfying. "
        f"주제 설명 ({title}). 이번 이미지의 중심 주제는 '{focus_title}' 입니다. 꼭 반영할 핵심 내용은 {details_text} 입니다. "
        "각 텍스트 블록은 1~6단어의 짧은 한국어로 제한하고, 레이아웃은 이 이미지 종류에 맞게 서로 다르게 구성해 주세요. "
        "Negative prompt: white background, white card, blank poster, empty square, washed out board, public service campaign poster, government PSA, corporate ad banner, garbled Korean text, random symbols, extra English letters, repeated 4-panel layout, messy composition, cluttered layout, tiny text, low contrast, blurry chalk, warped perspective, printed poster font, overdecorated layout."
    )



def _section_visual_kind(raw_heading: str) -> str:
    return SECTION_VISUAL_KIND_MAP.get(raw_heading, "diagram")



def _section_prompt_text(title: str, section: PublishSection) -> str:
    kind = _section_visual_kind(section.raw_heading)
    detail_lines = [section.publish_heading, *_simple_rows_from_lines(section.lines, limit=4)]
    return _chalkboard_explainer_prompt(title=title, focus_title=section.publish_heading, detail_lines=detail_lines, kind=kind)



def _spec_prompt_text(title: str, spec: dict[str, Any]) -> str:
    headers = [_clean(header) for header in spec.get("headers", [])]
    rows = [" | ".join(_clean(cell) for cell in row) for row in spec.get("rows", [])[:3]]
    label = _clean(spec.get("label")) or "보조 설명"
    kind = "mini_checklist" if "체크리스트" in label else "focus"
    detail_lines = [label, *headers, *rows]
    return _chalkboard_explainer_prompt(title=title, focus_title=label, detail_lines=detail_lines, kind=kind)



def _inline_table_visual_kind(spec: dict[str, Any]) -> str:
    label = _clean(spec.get("label"))
    headers = [_clean(header) for header in spec.get("headers", []) if _clean(header)]
    if "체크리스트" in label:
        return "mini_checklist"
    if len(headers) >= 3:
        return "decision_board"
    return "focus"



def _inline_table_prompt_text(title: str, spec: dict[str, Any]) -> str:
    headers = [_clean(header) for header in spec.get("headers", []) if _clean(header)]
    rows = [list(row) for row in spec.get("rows", [])[:4]]
    label = _clean(spec.get("label")) or "표 시각화 자료"
    kind = _inline_table_visual_kind(spec)
    detail_lines: list[str] = [label, *headers[:3]]
    for row in rows:
        cells = [_clean(cell) for cell in row if _clean(cell)]
        if not cells:
            continue
        if len(cells) >= 3:
            detail_lines.append(f"{cells[0]} / {cells[1]} / {cells[2]}")
        elif len(cells) == 2:
            detail_lines.append(f"{cells[0]} / {cells[1]}")
        else:
            detail_lines.append(cells[0])
    prompt = _chalkboard_explainer_prompt(title=title, focus_title=label, detail_lines=detail_lines[:6], kind=kind)
    if kind == "decision_board":
        prompt += (
            " This visual is derived from a markdown table. Do not render a spreadsheet, grid table, cell matrix, or white card. "
            "Use one huge center keyword, one left checklist area, one right why-it-matters area, and one small bottom reminder strip. "
            "Make it look like a hand-drawn chalkboard decision board, not a literal table screenshot."
        )
    return prompt



def _build_gpt_publish_image_plans(
    title: str,
    sections: list[PublishSection],
    inline_table_specs: list[dict[str, Any]] | None = None,
) -> list[PublishImagePlan]:
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

    for spec in inline_table_specs or []:
        label = _clean(spec.get("label")) or "표 시각화 자료"
        plans.append(
            PublishImagePlan(
                slot=str(spec.get("slot") or "__inline_table__:unknown"),
                kind=_inline_table_visual_kind(spec),
                label=label,
                image_role="section_visual",
                prompt_text=_inline_table_prompt_text(title, spec),
            )
        )
    return plans


def _render_gpt_publish_assets(
    *,
    title: str,
    sections: list[PublishSection],
    output_dir: str | Path,
    provider: str,
    inline_table_specs: list[dict[str, Any]] | None = None,
) -> list[PublishAsset]:
    resolved_provider = _resolve_publish_image_provider(provider)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    excerpt = _publish_image_excerpt(title, sections)
    plans = _build_gpt_publish_image_plans(title, sections, inline_table_specs=inline_table_specs)
    max_assets_raw = str(os.getenv("NAVER_BLOG_GPT_IMAGE_MAX_ASSETS", "0")).strip()
    max_assets = int(max_assets_raw) if max_assets_raw.isdigit() else 0
    if max_assets > 0:
        plans = plans[:max_assets]
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
                    artifact_root=OPENAI_COMPAT_ARTIFACT_DIR / "naver_publish",
                )
            except OpenAICompatExecutionError as exc:
                detail = str(exc)
                if exc.artifact_dir:
                    detail += f" (artifact: {exc.artifact_dir})"
                raise RuntimeError(f"GPT 이미지 생성 실패: {plan.label}, provider={resolved_provider}, {detail}") from exc
            assets.append(PublishAsset(slot=plan.slot, kind=plan.kind, label=plan.label, path=str(Path(execution["file_path"]).resolve())))
        return assets

    for index, plan in enumerate(plans, start=1):
        output_path = output_root / f"{index:02d}_{plan.kind}.png"
        if output_path.exists() and output_path.stat().st_size > 100_000 and not _is_visually_blank_publish_image(output_path):
            assets.append(PublishAsset(slot=plan.slot, kind=plan.kind, label=plan.label, path=str(output_path.resolve())))
            continue
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "generate_publish_image.py"),
            "--job-id", str(job_seed + index),
            "--profile-name", PUBLISH_GPT_IMAGE_PROFILE,
            "--prompt-text", plan.prompt_text,
            "--title", title,
            "--excerpt", excerpt,
            "--image-role", plan.image_role,
            "--output-path", str(output_path),
            "--artifact-root", str(GPT_WEB_ARTIFACT_DIR / "naver_publish"),
        ]
        timeout_seconds = int(str(os.getenv("NAVER_BLOG_GPT_IMAGE_TIMEOUT_SEC", "480")).strip() or "480")
        last_detail = ""
        for attempt in range(1, 3):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                last_detail = f"timeout={timeout_seconds}s, attempt={attempt}"
                continue
            if proc.returncode != 0:
                last_detail = (proc.stderr or proc.stdout or "").strip()
                continue
            try:
                execution = json.loads(proc.stdout)
            except Exception:
                last_detail = "invalid json response"
                continue
            resolved_path = str(Path(execution["file_path"]).resolve())
            if _is_visually_blank_publish_image(resolved_path):
                last_detail = f"흰색에 가까운 빈 이미지로 판정됨 ({resolved_path})"
                continue
            assets.append(PublishAsset(slot=plan.slot, kind=plan.kind, label=plan.label, path=resolved_path))
            break
        else:
            raise RuntimeError(f"GPT 이미지 생성 실패: {plan.label}, provider={resolved_provider}, {last_detail}")
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


def _sample_brightness(rgb: tuple[int, int, int]) -> float:
    return (float(rgb[0]) + float(rgb[1]) + float(rgb[2])) / 3.0


def _is_visually_blank_publish_image(asset_path: str | Path) -> bool:
    path = Path(asset_path)
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            points = [
                (width // 2, height // 2),
                (width // 4, height // 4),
                (width * 3 // 4, height // 4),
                (width // 4, height * 3 // 4),
                (width * 3 // 4, height * 3 // 4),
            ]
            samples = [_sample_brightness(rgb.getpixel((max(0, min(x, width - 1)), max(0, min(y, height - 1))))) for x, y in points]
            return mean(samples) >= 242.0
    except Exception:
        return False



def _render_publish_assets_local(
    *,
    title: str,
    sections: list[PublishSection],
    output_dir: str | Path,
    inline_table_specs: list[dict[str, Any]] | None = None,
    domain: str | None = None,
) -> list[PublishAsset]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    assets: list[PublishAsset] = []
    thumbnail_path = render_thumbnail_image(title=title, sections=sections, output_path=output_root / "00_thumbnail.png", domain=domain)
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
        assets.extend(_render_inline_table_assets_local(title=title, inline_table_specs=inline_table_specs or [], output_dir=output_root))
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
        assets.extend(_render_inline_table_assets_local(title=title, inline_table_specs=inline_table_specs or [], output_dir=output_root))
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
    assets.extend(_render_inline_table_assets_local(title=title, inline_table_specs=inline_table_specs or [], output_dir=output_root))
    return assets



def _render_publish_assets_result(
    *,
    title: str,
    sections: list[PublishSection],
    output_dir: str | Path,
    image_provider: str = "local",
    inline_table_specs: list[dict[str, Any]] | None = None,
    domain: str | None = None,
) -> PublishAssetRenderResult:
    requested_provider = _clean(image_provider or "local").lower() or "local"
    resolved_provider = _resolve_publish_image_provider(image_provider)
    if resolved_provider == "local":
        return PublishAssetRenderResult(
            assets=_render_publish_assets_local(
                title=title,
                sections=sections,
                output_dir=output_dir,
                inline_table_specs=inline_table_specs,
                domain=domain,
            ),
            image_provider="local",
            image_provider_requested=requested_provider,
        )

    try:
        assets = _render_gpt_publish_assets(
            title=title,
            sections=sections,
            output_dir=output_dir,
            provider=resolved_provider,
            inline_table_specs=inline_table_specs,
        )
        return PublishAssetRenderResult(
            assets=assets,
            image_provider=resolved_provider,
            image_provider_requested=requested_provider,
        )
    except RuntimeError as exc:
        fallback_assets = _render_publish_assets_local(
            title=title,
            sections=sections,
            output_dir=output_dir,
            inline_table_specs=inline_table_specs,
            domain=domain,
        )
        return PublishAssetRenderResult(
            assets=fallback_assets,
            image_provider="local",
            image_provider_requested=requested_provider,
            image_provider_fallback_from=resolved_provider,
            image_provider_fallback_reason=str(exc),
        )



def render_publish_assets(
    *,
    title: str,
    sections: list[PublishSection],
    output_dir: str | Path,
    image_provider: str = "local",
    inline_table_specs: list[dict[str, Any]] | None = None,
) -> list[PublishAsset]:
    return _render_publish_assets_result(
        title=title,
        sections=sections,
        output_dir=output_dir,
        image_provider=image_provider,
        inline_table_specs=inline_table_specs,
    ).assets


INLINE_TABLE_MARKER_RE = re.compile(r"\[\[INLINE_TABLE:([A-Za-z0-9_-]+)\]\]")


def _split_markdown_table_cells(line: str) -> list[str]:
    text = str(line or "").strip()
    if text.count("|") < 2:
        return []
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells = [cell.strip() for cell in text.split("|")]
    return cells if len(cells) >= 2 and all(cell for cell in cells) else []


def _is_markdown_table_separator(line: str, column_count: int) -> bool:
    cells = _split_markdown_table_cells(line)
    if len(cells) != column_count:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _inline_table_marker(table_id: str) -> str:
    return f"[[INLINE_TABLE:{table_id}]]"


def _inline_table_marker_id(line: str) -> str | None:
    match = INLINE_TABLE_MARKER_RE.fullmatch(str(line or "").strip())
    return match.group(1) if match else None


def _inline_table_asset_slot(table_id: str) -> str:
    return f"__inline_table__:{table_id}"


def _extract_inline_markdown_table_specs(
    sections: list[PublishSection],
) -> tuple[list[PublishSection], list[dict[str, Any]]]:
    specs: list[dict[str, Any]] = []
    cleaned_sections: list[PublishSection] = []
    table_seq = 1

    for section in sections:
        cleaned_lines: list[str] = []
        index = 0
        while index < len(section.lines):
            line = section.lines[index].strip()
            header_cells = _split_markdown_table_cells(line)
            if index + 1 < len(section.lines) and len(header_cells) >= 2 and _is_markdown_table_separator(section.lines[index + 1], len(header_cells)):
                rows: list[list[str]] = []
                row_index = index + 2
                while row_index < len(section.lines):
                    row_cells = _split_markdown_table_cells(section.lines[row_index])
                    if len(row_cells) != len(header_cells):
                        break
                    rows.append(row_cells)
                    row_index += 1

                if rows:
                    table_id = f"t{table_seq:02d}"
                    table_seq += 1
                    specs.append(
                        {
                            "slot": _inline_table_asset_slot(table_id),
                            "file_name": f"inline_table_{table_id}.png",
                            "label": f"{section.publish_heading} 시각화 자료",
                            "headers": header_cells,
                            "rows": rows,
                        }
                    )
                    cleaned_lines.append(_inline_table_marker(table_id))
                    index = row_index
                    continue

            cleaned_lines.append(section.lines[index])
            index += 1

        cleaned_sections.append(
            PublishSection(
                raw_heading=section.raw_heading,
                publish_heading=section.publish_heading,
                lines=_normalize_section_lines(cleaned_lines),
            )
        )

    return cleaned_sections, specs



def _render_inline_table_assets_local(
    *,
    title: str,
    inline_table_specs: list[dict[str, Any]],
    output_dir: str | Path,
) -> list[PublishAsset]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    assets: list[PublishAsset] = []
    for spec in inline_table_specs:
        table_path = render_table_image(
            title=title,
            label=str(spec.get("label") or "표 정리"),
            headers=[str(cell) for cell in spec.get("headers", [])],
            rows=[[str(cell) for cell in row] for row in spec.get("rows", [])],
            output_path=output_root / str(spec.get("file_name") or "inline_table.png"),
        )
        assets.append(
            PublishAsset(
                slot=str(spec.get("slot") or "__inline_table__:unknown"),
                kind="table",
                label=str(spec.get("label") or "표 정리"),
                path=str(Path(table_path).resolve()),
            )
        )
    return assets


def _append_section_publish_lines(lines: list[str], section_lines: list[str], inline_table_indexes: dict[str, int]) -> None:
    for raw_line in section_lines:
        table_id = _inline_table_marker_id(raw_line)
        if table_id:
            image_index = inline_table_indexes.get(table_id)
            if image_index:
                lines.append(f"[[IMAGE:{image_index}]]")
                lines.append("")
            continue
        lines.append(raw_line)


def _section_lines_for_publish(title: str, section: PublishSection) -> list[str]:
    if _topic_kind(title) == "ranking" and section.raw_heading == "FAQ":
        return [
            "Q1. 맞벌이 합산 소득이 높으면 어디서 먼저 탈락하나요?",
            "일반공급은 통장과 거주요건이 먼저라 바로 탈락으로 이어지지 않을 수 있습니다. 반대로 신혼부부나 생애최초 특별공급은 소득과 자산 기준에서 먼저 걸릴 가능성이 큽니다.",
            "",
            "Q2. 특별공급이 어렵다면 다음 선택지는 무엇인가요?",
            "이 경우에는 일반공급 1순위 가능성을 바로 따져보는 것이 현실적입니다. 통장 가입기간, 예치금, 지역 우선순위를 먼저 확인하시면 됩니다.",
            "",
            "Q3. 청약통장은 충분한데 세대 조건이 애매하면 어떻게 하나요?",
            "세대주 요건, 무주택 판정, 재당첨 제한을 공고문 기준으로 다시 보셔야 합니다. 이 부분은 단지마다 달라서 마지막에 공고문 확인이 필수입니다.",
            "",
            "Q4. 신청 직전에 가장 먼저 해야 할 한 가지는 무엇인가요?",
            "공급유형을 일반공급과 특별공급으로 나누고, 우리 부부가 어느 쪽에서 유리한지 먼저 결정하시는 것이 가장 빠릅니다. 그 다음에 통장이나 소득 기준을 확인하셔야 판단이 흔들리지 않습니다.",
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
        if section.raw_heading in {"이 글에서 바로 답하는 질문", "체크리스트"} and not line.startswith(("- ", "Q")) and not _inline_table_marker_id(line):
            out.append(f"- {_strip_heading_markers(line)}")
        elif section.raw_heading == "체크리스트" and line.startswith("- "):
            out.append(f"- {_strip_heading_markers(line[2:].strip())}")
        else:
            out.append(line)
        previous_blank = False
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _auction_lead_line(title: str) -> str:
    options = [
        "경매는 최저가만 보고 들어가기보다, 남는 권리와 점유 상태를 먼저 나눠봐야 합니다.",
        "같은 경매 물건이라도 권리관계, 점유, 잔금 계획을 같이 볼 때 판단이 달라집니다.",
        "경매 공부의 핵심은 싸 보이는 물건을 고르는 것보다 위험한 조건을 먼저 거르는 데 있습니다.",
        "입찰 전에는 낙찰가보다 보증금, 잔금, 명도 리스크가 감당 가능한지부터 확인해야 합니다.",
        "경매 물건은 가격표보다 등기부, 매각물건명세서, 현장 점유 상태를 함께 봐야 판단이 섭니다.",
    ]
    digest = hashlib.sha1(_clean(title).encode("utf-8")).hexdigest()
    return options[int(digest[:2], 16) % len(options)]


def _lead_blocks(title: str, sections: list[PublishSection], *, domain: str | None = None) -> list[str]:
    content_domain = _content_domain(title, domain)
    topic = _topic_kind(title)
    if content_domain == "auction":
        return [
            _auction_lead_line(title),
            "",
            _intro_text(sections, domain="auction"),
        ]
    if content_domain == "tax":
        return [
            "부동산 세금은 세목 이름보다 언제 발생하고 어떤 기준으로 계산되는지부터 나눠보는 것이 중요합니다.",
            "",
            _intro_text(sections, domain="tax"),
        ]
    if content_domain == "loan":
        return [
            "부동산 대출은 상품명보다 소득·기존 대출·주택 수·실행일을 먼저 나눠보는 것이 중요합니다.",
            "",
            _intro_text(sections, domain="loan"),
        ]
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
            "30대 맞벌이 청약은 막연히 불리한 게임이 아니라, 어떤 공급에서 판단하느냐에 따라 결과가 크게 갈립니다.",
            "",
            "이 글에서 바로 가져가실 것 3가지",
            "- 일반공급과 특별공급을 어디서 갈라서 봐야 하는지",
            "- 맞벌이 부부가 실제로 가장 자주 막히는 지점이 어디인지",
            "- 신청 직전에 무엇부터 확인해야 실수가 줄어드는지",
        ]
    return [
        "청약은 세대 기준, 일정 기준일, 자금 계획을 따로 나눠 보면 실수가 줄어듭니다.",
        "",
        _intro_text(sections, domain=content_domain),
    ]


BOOK_LINK_URL = "https://link.coupang.com/a/esfszm"
AUCTION_BOOK_LINK_URL = "https://link.coupang.com/a/espLX0"
BOOK_NOTICE_TEXT = '"이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."'
RELATED_CATEGORY_LABELS = {
    "cheongyak": "How To 분양",
    "auction": "How To 경매",
    "tax": "How To 세금",
    "loan": "부동산 대출",
}


def _book_link_url_for_domain(domain: str | None = None) -> str:
    return AUCTION_BOOK_LINK_URL if _content_domain("", domain) == "auction" else BOOK_LINK_URL


def _related_category_label(domain: str | None = None) -> str:
    return RELATED_CATEGORY_LABELS.get(_content_domain("", domain), "카테고리")


def _append_book_and_related_blocks(lines: list[str], *, related_links: list[dict[str, str]] | None = None, domain: str | None = None) -> None:
    lines.append("---")
    lines.append("")
    lines.append(BOOK_NOTICE_TEXT)
    book_link_url = _book_link_url_for_domain(domain)
    if book_link_url:
        lines.append(book_link_url)

    cleaned_related: list[dict[str, str]] = []
    for item in related_links or []:
        title = (item.get("title") or "이전 글").strip()
        url = (item.get("url") or "").strip()
        if not url:
            continue
        category_name = (item.get("category_name") or item.get("category") or _related_category_label(domain)).strip()
        cleaned_related.append({"category_name": category_name, "title": title, "url": url})

    if cleaned_related:
        lines.append("")
        lines.append("관련 글")
        for item in cleaned_related:
            lines.append(f"{item['category_name']} 최신 글")
            lines.append(item["title"])
            lines.append(item["url"])
            lines.append("")


def _validate_domain_publish_markdown(markdown: str, *, domain: str | None = None) -> None:
    content_domain = _content_domain("", domain)
    if content_domain not in {"auction", "tax", "loan"}:
        return
    forbidden_map = {
        "auction": ["청약홈", "입주자모집공고", "분양청약"],
        "tax": ["입찰보증금", "매각물건명세서", "인도명령"],
        "loan": ["매각물건명세서", "인도명령", "세무조사 피하는 법"],
    }
    forbidden_terms = forbidden_map[content_domain]
    found = [term for term in forbidden_terms if term in str(markdown or "")]
    if found:
        if content_domain == "auction":
            raise ValueError(f"경매 발행 본문에 청약 도메인 용어가 섞였습니다: {', '.join(found)}")
        if content_domain == "tax":
            raise ValueError(f"세금 발행 본문에 다른 도메인 용어가 섞였습니다: {', '.join(found)}")
        raise ValueError(f"대출 발행 본문에 다른 도메인 용어가 섞였습니다: {', '.join(found)}")


def build_publish_markdown(*, title: str, sections: list[PublishSection], assets: list[PublishAsset], related_links: list[dict[str, str]] | None = None, domain: str | None = None) -> str:
    lines: list[str] = [f"# {title}", ""]

    slot_to_indexes: dict[str, list[int]] = {}
    inline_table_indexes: dict[str, int] = {}
    for index, asset in enumerate(assets, start=1):
        slot_to_indexes.setdefault(asset.slot, []).append(index)
        if asset.slot.startswith("__inline_table__:"):
            inline_table_indexes[asset.slot.split(":", 1)[1]] = index

    for image_index in slot_to_indexes.get("lead", []):
        lines.append(f"[[IMAGE:{image_index}]]")
        lines.append("")

    lines.extend(_lead_blocks(title, sections, domain=domain))
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
        _append_section_publish_lines(lines, _section_lines_for_publish(title, section), inline_table_indexes)
        lines.append("")
        for image_index in slot_to_indexes.get(f"{section.publish_heading}::after", []):
            lines.append(f"[[IMAGE:{image_index}]]")
            lines.append("")

    content_domain = _content_domain(title, domain)
    if content_domain == "auction":
        lines.append("경매 일정과 권리관계, 점유 상태, 대출 가능 여부는 사건별로 달라질 수 있으니 입찰 전 법원경매정보와 관련 서류로 다시 확인해보시기 바랍니다.")
    elif content_domain == "tax":
        lines.append("세율과 감면, 비과세 적용은 시점과 개인 조건에 따라 달라질 수 있으니 홈택스·위택스·지자체 안내와 필요 시 세무 전문가 확인을 함께 거치시기 바랍니다.")
    elif content_domain == "loan":
        lines.append("대출 한도와 금리, 승인 여부는 소득·신용·주택 수·담보가치·금융기관 심사에 따라 달라질 수 있으니 공식 안내와 은행 상담으로 최종 확인하시기 바랍니다.")
    else:
        lines.append("일정과 비용은 수집 시점 기준일 수 있으니, 계약 전에는 입주자모집공고와 사업주체 안내로 다시 확인해보시기 바랍니다.")
    lines.append("")
    _append_book_and_related_blocks(lines, related_links=related_links, domain=_content_domain(title, domain))
    return "\n".join(lines).strip() + "\n"


def default_tags(title: str, *, domain: str | None = None) -> list[str]:
    content_domain = _content_domain(title, domain)
    text = _clean(title)
    if content_domain == "auction":
        tags = list(AUCTION_DEFAULT_TAGS)
        if any(token in text for token in ["말소기준권리", "권리분석", "근저당", "가압류", "가처분", "등기부"]):
            tags.extend(["말소기준권리", "권리분석", "근저당", "가압류", "가처분", "등기부등본확인", "특수권리"])
        if any(token in text for token in ["임차인", "대항력", "배당", "보증금", "확정일자"]):
            tags.extend(["선순위임차인", "대항력", "확정일자", "배당요구", "임차인배당", "보증금인수"])
        if any(token in text for token in ["명도", "인도명령", "점유"]):
            tags.extend(["명도", "인도명령", "점유자", "점유이전금지가처분", "명도소송"])
        if any(token in text for token in ["대출", "잔금", "취득세", "수리비", "자금", "비용"]):
            tags.extend(["경락잔금대출", "경매비용", "취득세", "수리비", "체납관리비", "낙찰가율", "입찰가계산"])
        tags.extend(["경매서류", "경매물건검색", "경매리스크", "부동산경매입찰", "경매실전", "부동산투자", "경매시세", "경매사건번호", "경매감정가", "경매최저가", "경매유찰"])
    elif content_domain == "tax":
        tags = list(TAX_DEFAULT_TAGS)
        if any(token in text for token in ["취득세", "감면", "생애최초", "위택스"]):
            tags.extend(["취득세계산", "취득세감면", "생애최초취득세", "위택스", "지방세"])
        if any(token in text for token in ["양도세", "양도소득세", "비과세", "장기보유", "필요경비"]):
            tags.extend(["양도세계산", "양도세비과세", "장기보유특별공제", "필요경비", "홈택스"])
        if any(token in text for token in ["재산세", "종부세", "종합부동산세", "보유세", "공시가격"]):
            tags.extend(["보유세", "재산세계산", "종부세계산", "공시가격", "과세기준일"])
        if any(token in text for token in ["증여", "상속", "자금출처"]):
            tags.extend(["증여세", "상속세", "자금출처조사", "부담부증여", "상속주택"])
        if any(token in text for token in ["임대", "월세", "간주임대료", "종합소득세"]):
            tags.extend(["주택임대소득세", "월세소득세", "간주임대료", "종합소득세", "임대사업자"])
        tags.extend(["부동산세금계산", "세금신고기한", "부동산세무", "절세체크리스트", "세무상담", "세금납부", "지방세납부", "국세청"])
    elif content_domain == "loan":
        tags = list(LOAN_DEFAULT_TAGS)
        if any(token in text for token in ["DSR", "LTV", "DTI", "스트레스"]):
            tags.extend(["스트레스DSR", "대출규제", "주담대한도", "소득산정", "주택수산정"])
        if any(token in text for token in ["중도금", "잔금", "분양", "입주"]):
            tags.extend(["중도금대출", "잔금대출", "입주잔금", "분양권대출", "집단대출"])
        if any(token in text for token in ["신생아", "디딤돌", "보금자리", "정책"]):
            tags.extend(["정책대출", "디딤돌대출", "보금자리론", "신생아특례", "주택도시기금"])
        if any(token in text for token in ["전세", "퇴거", "보증금", "역전세"]):
            tags.extend(["전세대출", "전세퇴거자금", "보증금반환", "버팀목전세대출", "역전세"])
        tags.extend(["은행상담", "대출심사", "금리비교", "대출체크리스트", "내집마련대출"])
    else:
        tags = list(DEFAULT_TAGS)
        topic = _topic_kind(title)
        if topic == "ranking":
            tags.extend(["1순위조건", "청약1순위", "청약가점", "가점제", "추첨제", "당해지역", "예치금", "무주택세대구성원", "30대청약", "맞벌이청약"])
        elif topic == "institution":
            tags.extend(["기관추천특별공급", "노부모부양", "특별공급자격", "무주택판정", "소득기준", "자산기준", "추천기관", "부적격주의"])
        elif topic == "cashflow":
            tags.extend(["분양계약금", "중도금대출", "잔금대출", "분양잔금", "분양자금계획", "분양취득세", "옵션비", "현금흐름", "월상환액", "대출한도"])
        else:
            tags.extend(["청약조건", "청약자격", "청약일정", "무주택", "거주요건", "소득기준", "자산기준", "부적격주의", "분양계약", "분양체크리스트"])
        if any(token in text for token in ["재당첨", "전매", "거주의무", "규제지역", "주택수"]):
            tags.extend(["재당첨제한", "전매제한", "거주의무", "규제지역청약", "주택수판정"])
        if any(token in text for token in ["무순위", "잔여세대", "줍줍", "미분양"]):
            tags.extend(["무순위청약", "잔여세대", "줍줍", "미분양", "청약대안"])

    ordered: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        cleaned = _clean(tag).replace(" ", "")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered[:NAVER_TAG_LIMIT]


def _emphasize_publish_text(line: str) -> str:
    text = str(line or "").strip()
    if not text or text.startswith("[[IMAGE:"):
        return text
    text = re.sub(r"^(주의|핵심|결론|요약|포인트|체크|질문|답변|예시|정리)(\s*[:：])", r"<strong>\1\2</strong>", text)
    text = re.sub(r"(일반공급 1순위|특별공급|청약통장|예치금|거주요건|무주택|소득과 자산|계약금|중도금|잔금|취득세|체크리스트|FAQ|How To 분양|법원경매|권리분석|말소기준권리|입찰보증금|선순위 임차인|대항력|배당요구|명도|인도명령|유치권|경락잔금대출|How To 경매|부동산 세금|양도세|양도소득세|재산세|종합부동산세|종부세|증여세|상속세|홈택스|위택스|비과세|감면|신고기한|How To 세금)", r"<strong>\1</strong>", text)
    return text


def _is_faq_question_line(line: str) -> bool:
    return bool(re.fullmatch(r"(?:###\s*)?Q(?:\d+)?\.\s+.+", str(line or "").strip()))


def _normalize_faq_question_line(line: str) -> str:
    return re.sub(r"^###\s*", "", str(line or "").strip())


def _insert_video_marker_after_lead_image(markdown: str, video_index: int = 1) -> str:
    lines = str(markdown or "").splitlines()
    marker = f"[[VIDEO:{video_index}]]"
    if marker in lines:
        return str(markdown or "")
    for index, line in enumerate(lines):
        if re.fullmatch(r"\[\[IMAGE:\d+\]\]", line.strip()):
            insert_at = index + 1
            while insert_at < len(lines) and not lines[insert_at].strip():
                insert_at += 1
            lines[insert_at:insert_at] = ["", marker, ""]
            return "\n".join(lines).strip() + "\n"
    if lines and lines[0].startswith("# "):
        lines[1:1] = ["", marker, ""]
        return "\n".join(lines).strip() + "\n"
    return f"{marker}\n\n{str(markdown or '').strip()}\n"


def markdown_to_html(markdown: str) -> str:
    html_lines: list[str] = [
        "<style>",
        "body{font-family:'Pretendard Variable','Pretendard','SUIT Variable','SUIT','Noto Sans KR','Noto Sans CJK KR',sans-serif;max-width:860px;margin:0 auto;padding:40px 24px 72px;line-height:1.85;color:#1f2937;background:#faf7f2;}",
        "h1,h2,h3,p,hr{margin:0;} h1{font-size:28px;font-weight:800;line-height:1.28;color:#123a70;letter-spacing:-0.03em;margin-bottom:22px;}",
        "h2{font-size:25px;font-weight:800;line-height:1.38;color:#123a70;letter-spacing:-0.02em;margin:38px 0 16px;padding-left:14px;border-left:5px solid #ea643f;}",
        "h3{font-size:21px;font-weight:800;line-height:1.5;color:#111827;letter-spacing:-0.02em;margin:30px 0 10px;}",
        "p{font-size:18px;margin-top:14px;word-break:keep-all;} p strong{font-weight:800;color:#111827;}",
        "hr{border:0;border-top:1px solid #e5e7eb;margin:32px 0;}",
        "</style>",
    ]
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            html_lines.append(f"<h1>{_emphasize_publish_text(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{_emphasize_publish_text(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{_emphasize_publish_text(line[4:].strip())}</h3>")
        elif _is_faq_question_line(line):
            html_lines.append(f"<h3>{_emphasize_publish_text(_normalize_faq_question_line(line))}</h3>")
        elif line == "---":
            html_lines.append("<hr>")
        elif re.fullmatch(r"\[\[(?:IMAGE|VIDEO):\d+\]\]", line):
            html_lines.append(f"<p>{line}</p>")
        elif line.startswith("- "):
            html_lines.append(f"<p>• {_emphasize_publish_text(line[2:].strip())}</p>")
        else:
            html_lines.append(f"<p>{_emphasize_publish_text(line)}</p>")
    return "\n".join(html_lines)


def build_publish_bundle(
    *,
    bundle_id: int,
    variant_title: str,
    article_markdown: str,
    output_root: str | Path,
    title_override: str | None = None,
    image_provider: str = "local",
    related_links: list[dict[str, str]] | None = None,
    domain: str | None = None,
) -> PublishBundle:
    publish_title = title_override or build_publish_title(variant_title)
    content_domain = _content_domain(publish_title or variant_title, domain)
    prepared_article = _prepare_article_for_publish(title=variant_title, article_markdown=article_markdown, domain=content_domain)
    original_title, sections = parse_publish_sections(prepared_article, title_hint=variant_title)
    publish_title = title_override or build_publish_title(variant_title or original_title)
    content_domain = _content_domain(publish_title, content_domain)
    output_dir = Path(output_root).resolve()
    images_dir = output_dir / "images"
    sections, inline_table_specs = _extract_inline_markdown_table_specs(sections)
    asset_result = _render_publish_assets_result(
        title=publish_title,
        sections=sections,
        output_dir=images_dir,
        image_provider=image_provider,
        inline_table_specs=inline_table_specs,
        domain=content_domain,
    )
    min_publish_side_px = int(str(os.getenv("NAVER_BLOG_MIN_IMAGE_SIDE_PX", "800")).strip() or "800")
    assets = [
        PublishAsset(slot=asset.slot, kind=asset.kind, label=asset.label, path=_ensure_publish_asset_min_side(asset.path, min_publish_side_px))
        for asset in asset_result.assets
    ]
    markdown = build_publish_markdown(title=publish_title, sections=sections, assets=assets, related_links=related_links, domain=content_domain)
    _validate_domain_publish_markdown(markdown, domain=content_domain)
    body_html = markdown_to_html(markdown)
    tags = default_tags(publish_title, domain=content_domain)
    apt_id = f"longtail-bundle-{bundle_id}"
    meta = {
        "bundle_id": bundle_id,
        "title": publish_title,
        "assets": [asset.__dict__ for asset in assets],
        "images": [asset.path for asset in assets],
        "tags": tags,
        "image_provider": asset_result.image_provider,
        "image_provider_requested": asset_result.image_provider_requested,
        "domain": content_domain,
    }
    if asset_result.image_provider_fallback_from:
        meta["image_provider_fallback_from"] = asset_result.image_provider_fallback_from
        meta["image_provider_fallback_reason"] = asset_result.image_provider_fallback_reason
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
        image_provider=asset_result.image_provider,
        image_provider_requested=asset_result.image_provider_requested,
        image_provider_fallback_from=asset_result.image_provider_fallback_from,
        image_provider_fallback_reason=asset_result.image_provider_fallback_reason,
    )


def load_bundle_article(db_path: str | Path, bundle_id: int) -> dict[str, Any]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bundle = conn.execute(
        """
        SELECT ab.id, ab.variant_id, ab.primary_draft_id, tc.domain, tv.title AS variant_title
        FROM article_bundle ab
        JOIN topic_variant tv ON tv.id = ab.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE ab.id = ?
        """,
        (bundle_id,),
    ).fetchone()
    if not bundle:
        raise ValueError(f"bundle_id={bundle_id} 를 찾지 못했습니다.")
    draft = conn.execute(
        "SELECT id, title, article_markdown FROM article_draft WHERE id = ?",
        (bundle["primary_draft_id"],),
    ).fetchone()
    if not draft:
        raise ValueError(f"draft_id={bundle['primary_draft_id']} 를 찾지 못했습니다.")
    related_rows = conn.execute(
        """
        SELECT published_title, naver_url, tc.domain
        FROM publish_history ph
        JOIN topic_variant tv ON tv.id = ph.variant_id
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE ph.channel = 'naver_blog'
          AND ph.naver_url IS NOT NULL
          AND ph.bundle_id != ?
          AND tc.domain = ?
        ORDER BY ph.published_at DESC, ph.id DESC
        LIMIT 1
        """,
        (bundle_id, bundle["domain"]),
    ).fetchall()
    related_links = [
        {
            "category_name": _related_category_label(row["domain"]),
            "title": row["published_title"],
            "url": row["naver_url"],
        }
        for row in related_rows
        if row["naver_url"]
    ]
    return {
        "bundle_id": bundle["id"],
        "variant_id": bundle["variant_id"],
        "title": bundle["variant_title"] or draft["title"],
        "domain": bundle["domain"],
        "article_markdown": draft["article_markdown"],
        "related_links": related_links,
    }


def _persist_publish_result(db_path: str | Path, article: dict[str, Any], result: dict[str, Any]) -> None:
    if not result.get("ok"):
        return

    publish_url = _clean(str(result.get("current_url") or result.get("url") or result.get("naver_url") or ""))
    if not publish_url:
        raise RuntimeError("네이버 발행은 성공했지만 저장할 URL이 결과에 없습니다.")

    from .planner import mark_published

    published_title = _clean(str(result.get("published_title") or article.get("title") or "")) or None
    mark_published(db_path, int(article["variant_id"]), publish_url, published_title=published_title)


def _persist_video_publish_result(
    db_path: str | Path,
    *,
    bundle_id: int,
    variant_id: int,
    video_result: dict[str, Any],
) -> None:
    from .database import connect

    with connect(db_path) as conn:
        try:
            row = conn.execute(
                """
                SELECT id, result_json
                FROM publish_history
                WHERE bundle_id = ? AND variant_id = ?
                ORDER BY COALESCE(published_at, created_at) DESC, id DESC
                LIMIT 1
                """,
                (bundle_id, variant_id),
            ).fetchone()
        except sqlite3.OperationalError:
            return
        if row is None:
            return
        try:
            payload = json.loads(row["result_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        payload["video_publish"] = video_result
        conn.execute(
            "UPDATE publish_history SET result_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _longtail_video_python(video_maker_root: Path) -> str:
    override = _clean(os.getenv("LONGTAIL_VIDEO_MAKER_PYTHON"))
    if override:
        return override
    venv_python = video_maker_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return "/usr/bin/python3"


def _longtail_blog_category(domain: str | None, fallback: str | None = None) -> str:
    value = _clean(fallback)
    if value:
        return value
    domain_value = _clean(domain).lower()
    if domain_value == "auction":
        return "How To 경매"
    if domain_value == "tax":
        return "How To 세금"
    if domain_value == "loan":
        return "부동산 대출"
    return "How To 분양"


def _render_longtail_video_for_blog(
    *,
    publish_bundle: PublishBundle,
    category_name: str | None,
) -> dict[str, Any]:
    # 롱테일 블로그는 도메인(분양/경매/세금/대출)과 무관하게 썸네일 바로 아래에
    # 재생 가능한 영상 컴포넌트를 기본 삽입한다. 특정 점검 실행에서만 명시적으로 끌 수 있다.
    if not _env_flag("LONGTAIL_BLOG_INLINE_VIDEO", default=True):
        return {"status": "skipped", "reason": "LONGTAIL_BLOG_INLINE_VIDEO disabled"}
    if not _env_flag("LONGTAIL_VIDEO_UPLOAD", default=False):
        return {"status": "skipped", "reason": "LONGTAIL_VIDEO_UPLOAD disabled"}

    meta_path = Path(publish_bundle.meta_path).resolve()
    video_maker_root = Path(os.getenv("LONGTAIL_VIDEO_MAKER_ROOT", "/home/kj/app/video_maker")).resolve()
    output_dir = meta_path.parent / "video"
    output_dir.mkdir(parents=True, exist_ok=True)

    domain = ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        domain = _clean(meta.get("domain"))
    except Exception:
        domain = ""

    blog_category = _longtail_blog_category(domain, category_name)
    cmd = [
        _longtail_video_python(video_maker_root),
        "-m",
        "src.cli.publish_project_video",
        "--project",
        "longtail",
        "--source",
        str(meta_path),
        "--output-dir",
        str(output_dir),
        "--privacy",
        _clean(os.getenv("LONGTAIL_YOUTUBE_PRIVACY_STATUS")) or "private",
        "--client-secrets",
        _clean(os.getenv("LONGTAIL_YOUTUBE_CLIENT_SECRETS")) or "/home/kj/app/openclaw_youtube/client_secrets.json",
        "--token-file",
        _clean(os.getenv("LONGTAIL_YOUTUBE_TOKEN_FILE")) or str(video_maker_root / "token.pickle"),
        "--expected-channel-id",
        _clean(os.getenv("LONGTAIL_YOUTUBE_EXPECTED_CHANNEL_ID")) or "UCjuIOmk641DSX8u_gkPYeuA",
        "--blog-home-url",
        _clean(os.getenv("LONGTAIL_BLOG_HOME_URL")) or "https://blog.naver.com/bear0439",
        "--blog-category",
        blog_category,
        "--clip-topic",
        blog_category,
        "--skip-comment",
        "--skip-youtube",
    ]
    if _env_flag("LONGTAIL_VIDEO_TTS_ENABLED", default=True):
        cmd.append("--with-tts")
        if _env_flag("LONGTAIL_VIDEO_TTS_STRICT", default=False):
            cmd.append("--tts-strict")

    proc = subprocess.run(
        cmd,
        cwd=str(video_maker_root),
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        timeout=int(str(os.getenv("LONGTAIL_VIDEO_TIMEOUT_SEC", "3600")).strip() or "3600"),
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        payload = {
            "status": "error",
            "returncode": proc.returncode,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "output_dir": str(output_dir),
        }
        if _env_flag("LONGTAIL_BLOG_INLINE_VIDEO_STRICT", default=False):
            raise RuntimeError("longtail_blog_inline_video_render_failed\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {"status": "error", "reason": "missing video render stdout", "output_dir": str(output_dir)}
    result = json.loads(lines[-1])
    result["status"] = result.get("status") or "ok"
    result["output_dir"] = str(output_dir)
    result["blog_category"] = blog_category
    return result


def _maybe_publish_longtail_video(
    *,
    publish_bundle: PublishBundle,
    publish_result: dict[str, Any],
    category_name: str | None,
    reuse_existing_video: bool = False,
) -> dict[str, Any]:
    if not _env_flag("LONGTAIL_VIDEO_UPLOAD", default=False):
        return {"status": "skipped", "reason": "LONGTAIL_VIDEO_UPLOAD disabled"}

    blog_url = _clean(str(publish_result.get("current_url") or publish_result.get("url") or publish_result.get("naver_url") or ""))
    if not blog_url:
        return {"status": "skipped", "reason": "blog_url missing"}

    meta_path = Path(publish_bundle.meta_path).resolve()
    video_maker_root = Path(os.getenv("LONGTAIL_VIDEO_MAKER_ROOT", "/home/kj/app/video_maker")).resolve()
    output_dir = meta_path.parent / "video"
    output_dir.mkdir(parents=True, exist_ok=True)

    domain = ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        domain = _clean(meta.get("domain"))
    except Exception:
        domain = ""

    blog_category = _longtail_blog_category(domain, category_name)
    cmd = [
        _longtail_video_python(video_maker_root),
        "-m",
        "src.cli.publish_project_video",
        "--project",
        "longtail",
        "--source",
        str(meta_path),
        "--output-dir",
        str(output_dir),
        "--privacy",
        _clean(os.getenv("LONGTAIL_YOUTUBE_PRIVACY_STATUS")) or "private",
        "--client-secrets",
        _clean(os.getenv("LONGTAIL_YOUTUBE_CLIENT_SECRETS")) or "/home/kj/app/openclaw_youtube/client_secrets.json",
        "--token-file",
        _clean(os.getenv("LONGTAIL_YOUTUBE_TOKEN_FILE")) or str(video_maker_root / "token.pickle"),
        "--expected-channel-id",
        _clean(os.getenv("LONGTAIL_YOUTUBE_EXPECTED_CHANNEL_ID")) or "UCjuIOmk641DSX8u_gkPYeuA",
        "--blog-url",
        blog_url,
        "--blog-home-url",
        _clean(os.getenv("LONGTAIL_BLOG_HOME_URL")) or "https://blog.naver.com/bear0439",
        "--blog-category",
        blog_category,
        "--clip-topic",
        blog_category,
        "--skip-comment",
    ]
    if not _env_flag("LONGTAIL_YOUTUBE_UPLOAD", default=True):
        cmd.append("--skip-youtube")
    if _env_flag("LONGTAIL_TIKTOK_UPLOAD", default=False):
        cmd.extend([
            "--upload-tiktok",
            "--tiktok-privacy-level",
            _clean(os.getenv("LONGTAIL_TIKTOK_PRIVACY_LEVEL")).upper() or "SELF_ONLY",
        ])
        token_file = _clean(os.getenv("LONGTAIL_TIKTOK_ACCESS_TOKEN_FILE"))
        if token_file:
            cmd.extend(["--tiktok-access-token-file", token_file])
    if reuse_existing_video:
        cmd.append("--reuse-existing-video")
    if _env_flag("LONGTAIL_VIDEO_TTS_ENABLED", default=True) and not reuse_existing_video:
        cmd.append("--with-tts")
        if _env_flag("LONGTAIL_VIDEO_TTS_STRICT", default=False):
            cmd.append("--tts-strict")
    if _env_flag("LONGTAIL_NAVER_CLIP_UPLOAD", default=True):
        clip_visibility = _clean(os.getenv("LONGTAIL_NAVER_CLIP_VISIBILITY")).lower() or "public"
        if clip_visibility not in {"private", "public"}:
            clip_visibility = "public"
        cmd.extend(["--upload-naver-clip", "--naver-clip-visibility", clip_visibility])

    proc = subprocess.run(
        cmd,
        cwd=str(video_maker_root),
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        timeout=int(str(os.getenv("LONGTAIL_VIDEO_TIMEOUT_SEC", "3600")).strip() or "3600"),
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        payload = {
            "status": "error",
            "returncode": proc.returncode,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
            "output_dir": str(output_dir),
        }
        if _env_flag("LONGTAIL_VIDEO_STRICT", default=False):
            raise RuntimeError("longtail_video_publish_failed\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        return payload

    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {"status": "error", "reason": "missing video publish stdout", "output_dir": str(output_dir)}
    result = json.loads(lines[-1])
    result["status"] = result.get("status") or "ok"
    result["output_dir"] = str(output_dir)
    result["blog_category"] = blog_category
    return result


def _requires_gpt_publish_images(image_provider: str) -> bool:
    return _clean(image_provider).lower() in {"gpt_web", "openai_compat"}


def publish_bundle_to_naver(
    *,
    db_path: str | Path,
    bundle_id: int,
    output_root: str | Path,
    mode: str = "public",
    title_override: str | None = None,
    image_provider: str = "auto",
    category_no: str | None = None,
    category_name: str | None = None,
) -> dict[str, Any]:
    article = load_bundle_article(db_path, bundle_id)
    publish_bundle = build_publish_bundle(
        bundle_id=bundle_id,
        variant_title=article["title"],
        article_markdown=article["article_markdown"],
        output_root=output_root,
        title_override=title_override,
        image_provider=image_provider,
        related_links=article.get("related_links"),
        domain=article.get("domain"),
    )

    if _requires_gpt_publish_images(image_provider) and publish_bundle.image_provider != _clean(image_provider).lower():
        raise RuntimeError(
            "GPT 이미지 생성이 실패해 로컬 fallback 이미지가 생성되었습니다. "
            f"요청 provider={image_provider}, 실제 provider={publish_bundle.image_provider}, "
            f"원인={publish_bundle.image_provider_fallback_reason or 'unknown'}"
        )

    inline_video_enabled = _env_flag("LONGTAIL_BLOG_INLINE_VIDEO", default=True)
    inline_video_result = _render_longtail_video_for_blog(
        publish_bundle=publish_bundle,
        category_name=category_name,
    )
    inline_video_path = _clean(str(inline_video_result.get("video_path") or "")) if inline_video_result.get("status") == "ok" else ""
    inline_videos = [inline_video_path] if inline_video_path else []
    if inline_video_enabled and _env_flag("LONGTAIL_VIDEO_UPLOAD", default=False) and not inline_videos:
        raise RuntimeError(
            "longtail_blog_inline_video_required_but_missing\n"
            + json.dumps(inline_video_result, ensure_ascii=False, indent=2)
        )
    if inline_videos:
        publish_bundle.markdown = _insert_video_marker_after_lead_image(publish_bundle.markdown, 1)
        publish_bundle.body_html = markdown_to_html(publish_bundle.markdown)
        try:
            meta_path = Path(publish_bundle.meta_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["blog_inline_videos"] = inline_videos
            meta["blog_inline_video_result"] = inline_video_result
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (meta_path.parent / "body.md").write_text(publish_bundle.markdown, encoding="utf-8")
            (meta_path.parent / "body.html").write_text(publish_bundle.body_html, encoding="utf-8")
        except Exception:
            pass

    blog_root = Path("/home/kj/app/bunyang/blog-cheongyak-automation")
    if str(blog_root) not in sys.path:
        sys.path.insert(0, str(blog_root))

    from src.publisher.naver_playwright import publish as naver_publish

    env_overrides: dict[str, str | None] = {}
    if category_no is not None:
        env_overrides["NAVER_BLOG_CATEGORY_NO"] = str(category_no).strip() or None
    if category_name is not None:
        env_overrides["NAVER_BLOG_CATEGORY_NAME"] = str(category_name).strip() or None
    previous_env = {key: os.environ.get(key) for key in env_overrides}

    out_dir = blog_root / "outputs" / "publish_longtail"
    try:
        for key, value in env_overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ok = naver_publish(
            publish_bundle.apt_id,
            publish_bundle.title,
            publish_bundle.body_html,
            publish_bundle.images,
            mode=mode,
            out=str(out_dir),
            body_markdown=publish_bundle.markdown,
            tags=publish_bundle.tags,
            videos=inline_videos,
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    result_path = out_dir / f"{publish_bundle.apt_id}.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result["ok"] = ok
    result["bundle_id"] = bundle_id
    result["meta_path"] = publish_bundle.meta_path
    result["published_title"] = publish_bundle.title
    result["image_provider"] = publish_bundle.image_provider
    result["image_provider_requested"] = publish_bundle.image_provider_requested
    if publish_bundle.image_provider_fallback_from:
        result["image_provider_fallback_from"] = publish_bundle.image_provider_fallback_from
        result["image_provider_fallback_reason"] = publish_bundle.image_provider_fallback_reason
    result["blog_inline_video"] = inline_video_result
    result["publish_review"] = {
        "viewer_url_present": bool(result.get("current_url")),
        "viewer_image_count_matches": result.get("viewer_image_count") == len(publish_bundle.images),
        "inline_image_count_matches": result.get("inline_image_count") == len(publish_bundle.images),
        "no_trailing_images": int(result.get("trailing_image_count") or 0) == 0,
        "no_low_res_images": int(result.get("viewer_low_res_image_count") or 0) == 0,
        "manual_review_required": bool(result.get("manual_review_required")),
    }
    _persist_publish_result(db_path, article, result)
    result["video_publish"] = _maybe_publish_longtail_video(
        publish_bundle=publish_bundle,
        publish_result=result,
        category_name=category_name,
        reuse_existing_video=bool(inline_videos),
    )
    if result.get("ok"):
        _persist_video_publish_result(
            db_path,
            bundle_id=bundle_id,
            variant_id=int(article["variant_id"]),
            video_result=result["video_publish"],
        )
    return result
