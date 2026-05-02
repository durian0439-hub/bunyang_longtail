from __future__ import annotations

import re
from collections import Counter

REPEATED_ENDINGS = [
    "확인해야 합니다",
    "보셔야 합니다",
    "확인하시기 바랍니다",
    "중요합니다",
]

DOMAIN_MARKERS = {
    "auction": ("경매", "입찰", "낙찰", "매각물건명세서", "등기부등본", "명도", "배당"),
    "tax": ("취득세", "양도세", "재산세", "종부세", "종합부동산세", "공시가격", "과세표준", "홈택스", "위택스"),
    "loan": ("대출", "DSR", "LTV", "DTI", "주담대", "잔금대출", "경락잔금대출", "은행 상담"),
}

DECISION_CHECK_INTRO_GROUPS = {
    "검색 의도": ("검색", "궁금", "헷갈", "막히", "상황"),
    "조건부 결론": ("가능", "보류", "회피", "추가 확인", "유리", "불리", "먼저"),
    "읽는 순서": ("읽는 순서", "순서로", "먼저", "다음", "마지막"),
}

FIRST_SCREEN_EVIDENCE_GROUPS = {
    "숫자·기준일": (
        "원",
        "만원",
        "억",
        "%",
        "세대",
        "㎡",
        "평",
        "일",
        "년",
        "회",
        "DSR",
        "LTV",
        "DTI",
    ),
    "공식 확인처": (
        "청약홈",
        "입주자모집공고",
        "모집공고",
        "법원경매정보",
        "매각물건명세서",
        "등기부등본",
        "홈택스",
        "위택스",
        "국세청",
        "은행 상담",
        "주택도시기금",
    ),
}


DATA_DELIVERY_GROUPS = {
    "auction": {
        "물건 기본정보": ("사건번호", "소재지", "면적", "감정가", "최저가", "입찰보증금"),
        "권리·임차인": ("권리", "임차인", "배당", "말소기준권리", "대항력"),
        "입찰가 상한": ("입찰가", "실거래가", "낙찰가율", "수리비", "명도비", "취득세"),
        "확인 서류": ("법원경매정보", "매각물건명세서", "현황조사서", "감정평가서", "등기부등본", "전입세대열람"),
    },
    "tax": {
        "기준일·신고기한": ("기준일", "6월 1일", "신고기한", "예정신고", "납부기한"),
        "공식 확인처": ("홈택스", "위택스", "국세청", "지자체"),
        "조건 변수": ("주택 수", "보유기간", "거주기간", "명의", "취득가액", "필요경비"),
        "세금 구분": ("취득세", "재산세", "종부세", "양도세", "증여세", "상속세"),
    },
    "loan": {
        "심사 변수": ("DSR", "LTV", "DTI", "소득", "기존 대출", "담보가치"),
        "실행 일정": ("잔금일", "실행일", "본심사", "사전상담"),
        "확인처": ("은행", "주택도시기금", "한국주택금융공사", "금감원", "HF"),
        "준비서류": ("소득증빙", "재직", "계약서", "등기", "신용"),
    },
}


def _detect_domain(text: str) -> str | None:
    scores = {
        domain: sum(text.count(marker) for marker in markers)
        for domain, markers in DOMAIN_MARKERS.items()
    }
    domain, score = max(scores.items(), key=lambda item: (item[1], item[0] == "tax", item[0] == "loan"))
    return domain if score > 0 else None


def _data_delivery_findings(text: str) -> tuple[list[str], list[str]]:
    domain = _detect_domain(text)
    if not domain:
        return [], []
    groups = DATA_DELIVERY_GROUPS[domain]
    hits: list[str] = []
    missing: list[str] = []
    for label, tokens in groups.items():
        if any(token in text for token in tokens):
            hits.append(label)
        else:
            missing.append(label)
    return hits, missing


def _clean_lines(markdown: str) -> list[str]:
    return [line.strip() for line in str(markdown or "").splitlines() if line.strip()]


def _intro_decision_check_findings(markdown: str) -> tuple[list[str], list[str]]:
    intro_lines: list[str] = []
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("## ") and intro_lines:
            break
        if line.startswith("# "):
            continue
        if line:
            intro_lines.append(line)
        if len(" ".join(intro_lines)) >= 900:
            break
    intro = "\n".join(intro_lines)[:900]
    hits: list[str] = []
    missing: list[str] = []
    for label, tokens in DECISION_CHECK_INTRO_GROUPS.items():
        if any(token in intro for token in tokens):
            hits.append(label)
        else:
            missing.append(label)
    return hits, missing


def _first_screen_evidence_findings(markdown: str) -> tuple[list[str], list[str]]:
    first_screen = str(markdown or "")[:500]
    hits: list[str] = []
    missing: list[str] = []
    for label, tokens in FIRST_SCREEN_EVIDENCE_GROUPS.items():
        has_hit = any(token in first_screen for token in tokens)
        if label == "숫자·기준일":
            has_hit = has_hit or bool(re.search(r"\d", first_screen))
        if has_hit:
            hits.append(label)
        else:
            missing.append(label)
    return hits, missing


def score_article_quality(markdown: str) -> tuple[float, dict[str, float | int | list[str]]]:
    lines = _clean_lines(markdown)
    text = "\n".join(lines)
    penalties = 0.0
    repeated_hits: list[str] = []

    for ending in REPEATED_ENDINGS:
        count = text.count(ending)
        if count >= 3:
            penalties += (count - 2) * 0.8
            repeated_hits.append(f"ending:{ending}:{count}")

    paragraphs = [line for line in lines if not line.startswith("#") and not line.startswith("- ") and not line.startswith("Q")]
    long_paragraphs = [line for line in paragraphs if len(line) >= 180]
    if long_paragraphs:
        penalties += len(long_paragraphs) * 0.4

    phrases = [
        "맞벌이",
        "일반공급",
        "특별공급",
        "청약 1순위",
        "확인",
    ]
    phrase_counts = Counter()
    for phrase in phrases:
        phrase_counts[phrase] = text.count(phrase)
    overused = [f"phrase:{k}:{v}" for k, v in phrase_counts.items() if v >= 8]
    penalties += max(0, len(overused) - 1) * 0.6
    repeated_hits.extend(overused)

    data_delivery_hits, data_delivery_missing = _data_delivery_findings(text)
    if data_delivery_missing:
        penalties += min(2.0, len(data_delivery_missing) * 0.5)
        repeated_hits.extend(f"data_missing:{item}" for item in data_delivery_missing)

    decision_intro_hits, decision_intro_missing = _intro_decision_check_findings(markdown)
    if decision_intro_missing:
        penalties += min(1.5, len(decision_intro_missing) * 0.5)
        repeated_hits.extend(f"decision_intro_missing:{item}" for item in decision_intro_missing)

    first_screen_evidence_hits, first_screen_evidence_missing = _first_screen_evidence_findings(markdown)
    if first_screen_evidence_missing:
        penalties += min(1.2, len(first_screen_evidence_missing) * 0.6)
        repeated_hits.extend(f"first_screen_missing:{item}" for item in first_screen_evidence_missing)

    score = max(0.0, 10.0 - penalties)
    return score, {
        "line_count": len(lines),
        "paragraph_count": len(paragraphs),
        "long_paragraphs": len(long_paragraphs),
        "repeated_hits": repeated_hits,
        "data_delivery_hits": data_delivery_hits,
        "data_delivery_missing": data_delivery_missing,
        "decision_intro_hits": decision_intro_hits,
        "decision_intro_missing": decision_intro_missing,
        "first_screen_evidence_hits": first_screen_evidence_hits,
        "first_screen_evidence_missing": first_screen_evidence_missing,
        "penalty": round(penalties, 2),
    }
