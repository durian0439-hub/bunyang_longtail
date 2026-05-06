from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from .catalog import AUCTION_DOMAIN, DEFAULT_DOMAIN, LOAN_DOMAIN, SUPPORTED_DOMAINS, TAX_DOMAIN
from .database import connect, fetch_all, fetch_one, init_db
from .planner import _domain_keyword_sources, _estimate_seo_score, _hash, _slugify
from .prompt_builder import build_prompt_package

CURRICULUM_TRACK_KEY = "real-estate-a-z"
CURRICULUM_TRACK_TITLE = "부동산 실전 A-Z"
CURRICULUM_HUB_POST_TITLE = "부동산 공부 A-Z 전체 목차: 청약·분양·경매·대출·세금"
CURRICULUM_TARGET_RATIO = 0.75

DOMAIN_LABELS = {
    DEFAULT_DOMAIN: "청약·분양",
    AUCTION_DOMAIN: "경매",
    TAX_DOMAIN: "세금",
    LOAN_DOMAIN: "대출",
}


@dataclass(frozen=True)
class CurriculumNodeSpec:
    chapter_no: int
    part_no: int
    part_title: str
    title: str
    domain: str
    family: str
    primary_keyword: str
    secondary_keyword: str
    audience: str
    search_intent: str
    scenario: str
    comparison_keyword: str
    angle: str = "판단형"
    required: bool = True

    @property
    def node_key(self) -> str:
        return f"{CURRICULUM_TRACK_KEY}-{self.chapter_no:03d}"


PARTS = {
    1: "내 집 마련 전에 알아야 할 기본기",
    2: "청약 A-Z",
    3: "분양가와 입지 판단",
    4: "대출 A-Z",
    5: "세금 A-Z",
    6: "경매 A-Z",
    7: "실전 의사결정",
}


def _node(
    chapter_no: int,
    part_no: int,
    title: str,
    domain: str,
    family: str,
    primary: str,
    secondary: str,
    audience: str,
    intent: str,
    scenario: str,
    comparison: str,
    angle: str = "판단형",
) -> CurriculumNodeSpec:
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError(f"지원하지 않는 domain 입니다: {domain}")
    return CurriculumNodeSpec(
        chapter_no=chapter_no,
        part_no=part_no,
        part_title=PARTS[part_no],
        title=title,
        domain=domain,
        family=family,
        primary_keyword=primary,
        secondary_keyword=secondary,
        audience=audience,
        search_intent=intent,
        scenario=scenario,
        comparison_keyword=comparison,
        angle=angle,
    )


REAL_ESTATE_AZ_NODES: tuple[CurriculumNodeSpec, ...] = (
    _node(1, 1, "지금 집을 사야 할까, 청약을 기다려야 할까", DEFAULT_DOMAIN, "기초", "내 집 마련", "청약", "무주택 실수요자", "비교", "매수와 청약 사이에서 고민할 때", "매매", "비교형"),
    _node(2, 1, "청약, 분양, 매매, 경매의 차이", DEFAULT_DOMAIN, "기초", "청약 분양 매매 경매", "분양", "청약초보", "비교", "처음 준비할 때", "경매", "비교형"),
    _node(3, 1, "무주택자 기준 제대로 이해하기", DEFAULT_DOMAIN, "기초", "무주택 기준", "주택 수 판정", "무주택 실수요자", "조건정리", "기준이 헷갈릴 때", "세대 기준"),
    _node(4, 1, "세대주, 세대원, 배우자 기준 정리", DEFAULT_DOMAIN, "기초", "세대주 세대원", "배우자 기준", "청약초보", "조건정리", "세대분리 전", "무주택 기준"),
    _node(5, 1, "부동산 자금 계획의 기본 구조", DEFAULT_DOMAIN, "자금", "부동산 자금 계획", "계약금 중도금 잔금", "무주택 실수요자", "계산", "자금 계획을 세울 때", "대출"),
    _node(6, 1, "계약금·중도금·잔금 흐름 이해하기", DEFAULT_DOMAIN, "자금", "계약금 중도금 잔금", "중도금 대출", "청약초보", "조건정리", "처음 분양 계약을 볼 때", "잔금 대출"),
    _node(7, 2, "청약통장 기본 구조", DEFAULT_DOMAIN, "기초", "청약통장", "납입횟수", "청약초보", "조건정리", "통장만 있을 때", "예치금"),
    _node(8, 2, "1순위 조건과 자주 틀리는 기준", DEFAULT_DOMAIN, "기초", "1순위 조건", "2순위", "청약초보", "실수방지", "청약 처음 넣기 전", "예치금", "실수방지형"),
    _node(9, 2, "민영주택과 국민주택 차이", DEFAULT_DOMAIN, "민간분양", "민영주택 국민주택", "납입횟수", "청약초보", "비교", "주택 유형이 헷갈릴 때", "예치금", "비교형"),
    _node(10, 2, "지역별 예치금과 거주기간", DEFAULT_DOMAIN, "기초", "지역별 예치금", "거주기간", "무주택 실수요자", "조건정리", "지역을 옮겼을 때", "당해 기타지역"),
    _node(11, 2, "특별공급 전체 구조", DEFAULT_DOMAIN, "특공", "특별공급", "일반공급", "청약초보", "조건정리", "특공끼리 비교할 때", "일반공급"),
    _node(12, 2, "신혼부부 특별공급", DEFAULT_DOMAIN, "특공", "신혼부부 특별공급", "소득기준", "신혼부부", "가능여부", "소득이 애매할 때", "생애최초 특별공급"),
    _node(13, 2, "생애최초 특별공급", DEFAULT_DOMAIN, "특공", "생애최초 특별공급", "소득기준", "생애최초 준비자", "가능여부", "무주택 이력이 헷갈릴 때", "신혼부부 특별공급"),
    _node(14, 2, "다자녀·노부모 특별공급", DEFAULT_DOMAIN, "특공", "다자녀 노부모 특별공급", "가구원수", "다자녀 가구", "비교", "가족 구성 기준이 헷갈릴 때", "신혼부부 특별공급", "비교형"),
    _node(15, 2, "일반공급 가점제와 추첨제", DEFAULT_DOMAIN, "일반공급", "가점제 추첨제", "청약가점", "저가점 청약자", "비교", "가점이 낮을 때", "당첨확률", "비교형"),
    _node(16, 2, "부적격이 나는 대표 사례", DEFAULT_DOMAIN, "규제", "부적격 당첨", "중복당첨", "청약초보", "실수방지", "서류 준비 전", "무주택 기준", "실수방지형"),
    _node(17, 2, "예비당첨과 추가입주자모집", DEFAULT_DOMAIN, "실무", "예비당첨", "추가입주자모집", "예비당첨자", "조건정리", "예비당첨 연락 전", "본당첨"),
    _node(18, 2, "줍줍·무순위 청약 판단법", DEFAULT_DOMAIN, "대안", "무순위 청약", "줍줍", "저가점 청약자", "판단", "무순위만 기다릴 때", "잔여세대"),
    _node(19, 2, "입주자모집공고 읽는 법", DEFAULT_DOMAIN, "실무", "입주자모집공고", "청약홈", "청약초보", "체크리스트", "청약 직전", "서류 기준일", "체크리스트형"),
    _node(20, 2, "당첨 후 계약 전 체크리스트", DEFAULT_DOMAIN, "당첨후", "당첨 후 계약", "서류 보완", "본당첨자", "체크리스트", "계약 직전", "계약 포기", "체크리스트형"),
    _node(21, 3, "분양가가 싼지 비싼지 보는 법", DEFAULT_DOMAIN, "입지", "분양가 비교", "주변 시세", "실거주 예정자", "비교", "분양가가 애매할 때", "시세 비교", "비교형"),
    _node(22, 3, "주변 시세 비교하는 법", DEFAULT_DOMAIN, "입지", "주변 시세", "실거래가", "실거주 예정자", "체크리스트", "시세를 비교할 때", "분양가", "체크리스트형"),
    _node(23, 3, "역세권 판단 기준", DEFAULT_DOMAIN, "입지", "역세권", "직주근접", "실거주 예정자", "조건정리", "교통이 애매할 때", "생활권"),
    _node(24, 3, "학군·상권·병원·공원 보는 법", DEFAULT_DOMAIN, "입지", "생활 인프라", "학군", "신혼부부", "체크리스트", "입주 전 현장 볼 때", "상권", "체크리스트형"),
    _node(25, 3, "입주 시점 리스크", DEFAULT_DOMAIN, "실무", "입주 시점", "전세 만기", "전세 만기 예정자", "실수방지", "입주가 겹칠 때", "잔금 일정", "실수방지형"),
    _node(26, 3, "전매제한·실거주의무·재당첨제한", DEFAULT_DOMAIN, "규제", "전매제한 거주의무 재당첨제한", "규제지역", "실거주 예정자", "조건정리", "당첨 이후", "부적격 당첨"),
    _node(27, 3, "미분양 단지 판단법", DEFAULT_DOMAIN, "대안", "미분양", "잔여세대", "당장 입주 필요한 수요자", "판단", "미분양 검토할 때", "무순위 청약"),
    _node(28, 3, "좋은 청약과 피해야 할 청약", DEFAULT_DOMAIN, "실무", "좋은 청약 피해야 할 청약", "입주자모집공고", "무주택 실수요자", "비교", "청약 직전", "분양가", "비교형"),
    _node(29, 4, "LTV, DSR, DTI 기본 개념", LOAN_DOMAIN, "주택담보대출", "LTV DSR DTI", "대출 한도", "대출초보", "조건정리", "은행 상담 전", "주택담보대출"),
    _node(30, 4, "중도금 대출 구조", LOAN_DOMAIN, "청약분양대출", "중도금 대출", "분양권 대출", "청약 당첨자", "조건정리", "중도금 대출이 필요할 때", "잔금 대출"),
    _node(31, 4, "잔금 대출 준비", LOAN_DOMAIN, "청약분양대출", "잔금 대출", "중도금 대출", "입주 예정자", "체크리스트", "잔금이 걱정될 때", "주택담보대출", "체크리스트형"),
    _node(32, 4, "주택담보대출 한도 계산", LOAN_DOMAIN, "주택담보대출", "주택담보대출 한도", "DSR", "대출초보", "계산", "월 상환액을 따질 때", "LTV"),
    _node(33, 4, "전세퇴거자금 대출", LOAN_DOMAIN, "전세퇴거자금", "전세퇴거자금 대출", "보증금 반환", "1주택 갈아타기 준비자", "가능여부", "보증금 반환이 필요할 때", "주택담보대출"),
    _node(34, 4, "대환대출 판단법", LOAN_DOMAIN, "주택담보대출", "대환대출", "금리", "기존 대출 보유자", "판단", "금리가 부담될 때", "중도상환수수료"),
    _node(35, 4, "금리와 월 상환액 계산", LOAN_DOMAIN, "주택담보대출", "월 상환액", "금리", "대출초보", "계산", "월 상환액을 따질 때", "DSR"),
    _node(36, 4, "대출이 막히는 대표 사례", LOAN_DOMAIN, "주택담보대출", "대출 거절", "DSR", "대출초보", "실수방지", "대출 심사 전에", "기존대출", "실수방지형"),
    _node(37, 5, "취득세 기본 구조", TAX_DOMAIN, "취득세", "취득세", "주택수", "부동산 매수자", "조건정리", "잔금 전에", "중과"),
    _node(38, 5, "보유세와 재산세", TAX_DOMAIN, "보유세", "재산세", "보유세", "주택 보유자", "조건정리", "보유 중일 때", "종합부동산세"),
    _node(39, 5, "종부세 기본 판단", TAX_DOMAIN, "보유세", "종합부동산세", "공시가격", "주택 보유자", "판단", "공시가격이 올랐을 때", "재산세"),
    _node(40, 5, "양도세 기본 구조", TAX_DOMAIN, "양도세", "양도세", "보유기간", "매도 예정자", "조건정리", "매도 전에", "비과세"),
    _node(41, 5, "1세대 1주택 비과세", TAX_DOMAIN, "양도세", "1세대 1주택 비과세", "거주기간", "1주택자", "가능여부", "매도 전에", "일시적 2주택"),
    _node(42, 5, "분양권·입주권 세금", TAX_DOMAIN, "분양입주권세금", "분양권 입주권 세금", "양도세", "분양권 보유자", "비교", "분양권을 살 때", "입주권", "비교형"),
    _node(43, 5, "공동명의 세금", TAX_DOMAIN, "공동명의", "공동명의 세금", "증여세", "부부 공동명의 검토자", "판단", "공동명의를 고민할 때", "양도세"),
    _node(44, 5, "증여·상속 기본", TAX_DOMAIN, "증여상속", "증여 상속", "증여세", "가족 간 이전 검토자", "조건정리", "가족 간 이전을 고민할 때", "상속세"),
    _node(45, 5, "임대소득세와 간주임대료", TAX_DOMAIN, "임대소득세", "임대소득세", "간주임대료", "임대수익 보유자", "계산", "월세나 보증금이 있을 때", "종합소득세"),
    _node(46, 6, "경매가 일반 매매와 다른 점", AUCTION_DOMAIN, "경매기초", "경매 일반 매매 차이", "법원경매정보", "경매초보", "비교", "경매 공부를 시작할 때", "일반 매매", "비교형"),
    _node(47, 6, "법원경매정보 보는 법", AUCTION_DOMAIN, "물건검색", "법원경매정보", "사건번호", "경매초보", "체크리스트", "법원경매 사이트를 볼 때", "매각물건명세서", "체크리스트형"),
    _node(48, 6, "권리분석 기본", AUCTION_DOMAIN, "권리분석", "권리분석", "등기부등본", "권리분석 입문자", "조건정리", "등기부등본을 볼 때", "말소기준권리"),
    _node(49, 6, "말소기준권리 이해하기", AUCTION_DOMAIN, "권리분석", "말소기준권리", "근저당", "권리분석 입문자", "조건정리", "말소기준권리를 찾을 때", "인수되는 권리"),
    _node(50, 6, "임차인과 대항력", AUCTION_DOMAIN, "임차인배당", "임차인 대항력", "전입일자", "경매초보", "가능여부", "선순위 임차인이 있을 때", "보증금 인수"),
    _node(51, 6, "배당과 보증금 위험", AUCTION_DOMAIN, "임차인배당", "배당 보증금 위험", "배당요구", "실거주 매수자", "실수방지", "보증금 인수가 걱정될 때", "대항력", "실수방지형"),
    _node(52, 6, "현장조사 체크리스트", AUCTION_DOMAIN, "물건검색", "현장조사", "임장 체크리스트", "임장 전 독자", "체크리스트", "임장 전에", "시세조사", "체크리스트형"),
    _node(53, 6, "입찰가 산정법", AUCTION_DOMAIN, "수익분석", "입찰가 산정", "낙찰가율", "직장인 투자자", "계산", "입찰가를 정할 때", "안전마진"),
    _node(54, 6, "낙찰 후 잔금과 대출", AUCTION_DOMAIN, "낙찰후", "낙찰 후 잔금 대출", "경락잔금대출", "낙찰 예정자", "조건정리", "낙찰 직후", "잔금납부"),
    _node(55, 6, "명도와 점유 리스크", AUCTION_DOMAIN, "명도점유", "명도 점유 리스크", "인도명령", "낙찰 예정자", "실수방지", "점유자를 만날 때", "강제집행", "실수방지형"),
    _node(56, 6, "경매 세금과 수리비", AUCTION_DOMAIN, "자금세금", "경매 세금 수리비", "취득세", "소액 투자자", "계산", "잔금납부기한이 다가올 때", "관리비 체납"),
    _node(57, 6, "초보자가 피해야 할 경매 물건", AUCTION_DOMAIN, "특수물건", "피해야 할 경매 물건", "특수물건", "경매초보", "실수방지", "특수권리가 보일 때", "유치권", "실수방지형"),
    _node(58, 7, "청약이 나은 사람", DEFAULT_DOMAIN, "실무", "청약이 나은 사람", "무주택 기준", "무주택 실수요자", "판단", "선택지를 비교할 때", "매매"),
    _node(59, 7, "매매가 나은 사람", DEFAULT_DOMAIN, "대안", "매매가 나은 사람", "기존주택 매수", "당장 입주 필요한 수요자", "판단", "청약을 기다리기 어려울 때", "청약"),
    _node(60, 7, "경매가 나은 사람", AUCTION_DOMAIN, "경매기초", "경매가 나은 사람", "일반 매매", "실거주 매수자", "판단", "경매와 매매를 비교할 때", "청약"),
    _node(61, 7, "전세 유지가 나은 사람", DEFAULT_DOMAIN, "대안", "전세 유지", "매수 대기", "무주택 실수요자", "판단", "자금이 부족할 때", "청약"),
    _node(62, 7, "자금 부족할 때 선택지", LOAN_DOMAIN, "주택담보대출", "자금 부족 선택지", "잔금 대출", "현금이 빠듯한 실수요자", "판단", "자금이 부족할 때", "전세퇴거자금"),
    _node(63, 7, "가족 명의·공동명의 판단", TAX_DOMAIN, "공동명의", "가족 명의 공동명의", "증여세", "가족 간 이전 검토자", "판단", "공동명의를 고민할 때", "양도세"),
    _node(64, 7, "갈아타기 전략", DEFAULT_DOMAIN, "갈아타기", "갈아타기 전략", "기존주택 처분", "1주택 갈아타기 준비자", "전략", "기존 집 매도 전", "잔금 일정"),
    _node(65, 7, "최종 체크리스트", DEFAULT_DOMAIN, "실무", "부동산 최종 체크리스트", "입주자모집공고", "무주택 실수요자", "체크리스트", "최종 결정 전", "자금 계획", "체크리스트형"),
)


def _curriculum_outline(spec: CurriculumNodeSpec) -> list[dict[str, Any]]:
    return [
        {"heading": "상단 요약", "points": [f"{spec.title}의 결론을 먼저 제시", f"{spec.audience}가 바로 확인할 기준 3가지"]},
        {"heading": "이 글에서 바로 답하는 질문", "points": [f"{spec.primary_keyword}에서 가장 먼저 갈리는 기준", f"{spec.comparison_keyword}와 비교해 달라지는 점"]},
        {"heading": "핵심 조건 정리", "points": [f"{spec.primary_keyword} 기본 구조", f"{spec.secondary_keyword}와 함께 봐야 하는 조건"]},
        {"heading": "헷갈리기 쉬운 예외", "points": [f"{spec.scenario} 자주 놓치는 예외", "부적격·비용·기한 문제로 이어질 수 있는 지점"]},
        {"heading": "실전 예시 시나리오", "points": [f"{spec.audience} 사례 1개", "가능/보류/회피 또는 다음 행동 판단 흐름"]},
        {"heading": "체크리스트", "points": ["신청·계약·상담 전에 확인할 항목 5개 이상", "무엇을 어디서 왜 확인하는지까지 설명"]},
        {"heading": "FAQ", "points": [f"{spec.primary_keyword} 관련 자주 묻는 질문 6개 이상", "본문 반복이 아닌 후속 질문 중심"]},
        {"heading": "마무리 결론", "points": [f"{spec.audience}에게 맞는 다음 행동", "A-Z 다음 장으로 자연스럽게 연결할 내부링크 문장"]},
    ]


def _policy_json(spec: CurriculumNodeSpec, *, track_key: str) -> str:
    return json.dumps(
        {
            "route_policy": "gpt_web_first",
            "domain": spec.domain,
            "keyword_sources": _domain_keyword_sources(spec.domain),
            "curriculum": {
                "track_key": track_key,
                "node_key": spec.node_key,
                "chapter_no": spec.chapter_no,
                "part_no": spec.part_no,
                "part_title": spec.part_title,
                "strategy": "a_z_spine",
            },
        },
        ensure_ascii=False,
    )


def _cluster_payload(spec: CurriculumNodeSpec, *, track_key: str) -> dict[str, Any]:
    outline = _curriculum_outline(spec)
    return {
        "domain": spec.domain,
        "semantic_key": _hash(f"curriculum|{track_key}|{spec.node_key}|{spec.domain}|{spec.primary_keyword}", size=20),
        "family": spec.family,
        "primary_keyword": spec.primary_keyword,
        "secondary_keyword": spec.secondary_keyword,
        "audience": spec.audience,
        "search_intent": spec.search_intent,
        "scenario": spec.scenario,
        "comparison_keyword": spec.comparison_keyword,
        "priority": 1000 - spec.chapter_no,
        "outline_json": json.dumps(outline, ensure_ascii=False),
        "policy_json": _policy_json(spec, track_key=track_key),
    }


def _upsert_track(conn: Any, *, track_key: str) -> int:
    strategy = {
        "mode": "a_z_spine_plus_longtail_branch",
        "spine_ratio": CURRICULUM_TARGET_RATIO,
        "branch_ratio": 1 - CURRICULUM_TARGET_RATIO,
        "description": "책 목차처럼 고정된 A-Z 본문을 먼저 발행하고, 성과·검색 기반 롱테일은 보조 가지로 붙인다.",
    }
    row = fetch_one(conn, "SELECT id FROM curriculum_track WHERE track_key = ?", (track_key,))
    if row:
        conn.execute(
            """
            UPDATE curriculum_track
            SET title = ?, description = ?, strategy_json = ?, target_ratio = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                CURRICULUM_TRACK_TITLE,
                "청약·분양·대출·세금·경매를 책 한 권처럼 읽히게 만드는 A-Z 고정 발행 spine",
                json.dumps(strategy, ensure_ascii=False),
                CURRICULUM_TARGET_RATIO,
                row["id"],
            ),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO curriculum_track (track_key, title, description, strategy_json, target_ratio, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            track_key,
            CURRICULUM_TRACK_TITLE,
            "청약·분양·대출·세금·경매를 책 한 권처럼 읽히게 만드는 A-Z 고정 발행 spine",
            json.dumps(strategy, ensure_ascii=False),
            CURRICULUM_TARGET_RATIO,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _upsert_node(conn: Any, track_id: int, spec: CurriculumNodeSpec, *, track_key: str) -> int:
    outline_json = json.dumps(_curriculum_outline(spec), ensure_ascii=False)
    policy_json = _policy_json(spec, track_key=track_key)
    row = fetch_one(conn, "SELECT id, status FROM curriculum_node WHERE node_key = ?", (spec.node_key,))
    values = (
        track_id,
        spec.chapter_no,
        spec.part_no,
        spec.part_title,
        spec.title,
        spec.domain,
        spec.family,
        spec.primary_keyword,
        spec.secondary_keyword,
        spec.audience,
        spec.search_intent,
        spec.scenario,
        spec.comparison_keyword,
        spec.angle,
        1 if spec.required else 0,
        1000 - spec.chapter_no,
        outline_json,
        policy_json,
    )
    if row:
        conn.execute(
            """
            UPDATE curriculum_node
            SET track_id = ?, chapter_no = ?, part_no = ?, part_title = ?, title = ?, domain = ?, family = ?,
                primary_keyword = ?, secondary_keyword = ?, audience = ?, search_intent = ?, scenario = ?,
                comparison_keyword = ?, angle = ?, required = ?, priority = ?, outline_json = ?, policy_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*values, row["id"]),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO curriculum_node (
            track_id, node_key, chapter_no, part_no, part_title, title, domain, family,
            primary_keyword, secondary_keyword, audience, search_intent, scenario,
            comparison_keyword, angle, required, priority, status, outline_json, policy_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        """,
        (track_id, spec.node_key, *values[1:]),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _upsert_cluster(conn: Any, cluster: dict[str, Any]) -> int:
    row = fetch_one(conn, "SELECT id FROM topic_cluster WHERE semantic_key = ?", (cluster["semantic_key"],))
    values = (
        cluster["domain"],
        cluster["family"],
        cluster["primary_keyword"],
        cluster["secondary_keyword"],
        cluster["audience"],
        cluster["search_intent"],
        cluster["scenario"],
        cluster["comparison_keyword"],
        cluster["priority"],
        cluster["outline_json"],
        cluster["policy_json"],
    )
    if row:
        conn.execute(
            """
            UPDATE topic_cluster
            SET domain = ?, family = ?, primary_keyword = ?, secondary_keyword = ?, audience = ?, search_intent = ?,
                scenario = ?, comparison_keyword = ?, priority = ?, outline_json = ?, policy_json = ?,
                status = 'active', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*values, row["id"]),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO topic_cluster (
            domain, semantic_key, family, primary_keyword, secondary_keyword, audience,
            search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (cluster["domain"], cluster["semantic_key"], *values[1:]),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _upsert_variant(conn: Any, cluster_id: int, cluster: dict[str, Any], spec: CurriculumNodeSpec, *, track_key: str) -> int:
    variant_key = _hash(f"curriculum-variant|{track_key}|{spec.node_key}|primary|{spec.title}", size=24)
    slug = _slugify(spec.title, variant_key[:6])
    variant = {"angle": spec.angle, "title": spec.title}
    prompt_payload = build_prompt_package(cluster, variant)
    prompt_payload.setdefault("user", {})["curriculum"] = {
        "track_key": track_key,
        "node_key": spec.node_key,
        "chapter_no": spec.chapter_no,
        "part_no": spec.part_no,
        "part_title": spec.part_title,
        "hub_link_policy": "본문 안에는 전체 목차를 길게 붙이지 말고, 발행 단계에서 목차 허브글 링크 1개만 짧게 연결합니다.",
        "next_chapter_hint": "본문 끝에서 A-Z 다음 장을 자연스럽게 안내합니다.",
    }
    seo_score = _estimate_seo_score(spec.title, cluster)
    row = fetch_one(conn, "SELECT id, status FROM topic_variant WHERE variant_key = ?", (variant_key,))
    if row:
        conn.execute(
            """
            UPDATE topic_variant
            SET cluster_id = ?, angle = ?, title = ?, slug = ?, seo_score = ?, prompt_json = ?,
                prompt_version = 'az_v1', route_policy = 'gpt_web_first', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (cluster_id, spec.angle, spec.title, slug, seo_score, json.dumps(prompt_payload, ensure_ascii=False), row["id"]),
        )
        return int(row["id"])
    conn.execute(
        """
        INSERT INTO topic_variant (
            cluster_id, variant_key, angle, title, slug, seo_score,
            prompt_json, prompt_version, route_policy, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'az_v1', 'gpt_web_first', 'queued')
        """,
        (cluster_id, variant_key, spec.angle, spec.title, slug, seo_score, json.dumps(prompt_payload, ensure_ascii=False)),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _hub_key(track_key: str) -> str:
    return f"{track_key}-hub"


def _hub_markdown_hash(markdown: str) -> str:
    return hashlib.sha256(str(markdown or "").encode("utf-8")).hexdigest()


def _curriculum_rows_for_track_in_conn(conn: Any, track_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        WITH latest_publish AS (
            SELECT variant_id, MAX(id) AS publish_id
            FROM publish_history
            WHERE channel = 'naver_blog'
            GROUP BY variant_id
        )
        SELECT
            cn.chapter_no,
            cn.part_no,
            cn.part_title,
            cn.node_key,
            cn.title AS chapter_title,
            cn.domain,
            cn.primary_keyword,
            cn.status AS node_status,
            tv.id AS variant_id,
            tv.status AS variant_status,
            ph.naver_url,
            ph.published_at
        FROM curriculum_node cn
        LEFT JOIN curriculum_node_variant cnv ON cnv.node_id = cn.id AND cnv.variant_role = 'primary'
        LEFT JOIN topic_variant tv ON tv.id = cnv.variant_id
        LEFT JOIN latest_publish lp ON lp.variant_id = tv.id
        LEFT JOIN publish_history ph ON ph.id = lp.publish_id
        WHERE cn.track_id = ?
        ORDER BY cn.chapter_no ASC
        """,
        (track_id,),
    )
    return [dict(row) for row in rows]


def render_curriculum_hub_markdown(*, track: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    linked = sum(1 for row in rows if str(row.get("naver_url") or "").strip())
    title = CURRICULUM_HUB_POST_TITLE
    lines: list[str] = [
        f"# {title}",
        "",
        "청약, 분양, 대출, 세금, 경매를 처음부터 순서대로 볼 수 있게 정리한 전체 목차입니다.",
        "발행된 챕터는 제목에 링크를 걸고, 아직 발행 전인 챕터는 발행 예정으로 남겨둡니다.",
        "",
        f"진행률: {linked}/{total}",
        "",
    ]
    current_part: tuple[int, str] | None = None
    for row in rows:
        part = (int(row["part_no"]), str(row["part_title"]))
        if part != current_part:
            if current_part is not None:
                lines.append("")
            lines.append(f"## {part[0]}부. {part[1]}")
            lines.append("")
            current_part = part
        chapter_no = int(row["chapter_no"])
        chapter_title = str(row["chapter_title"])
        domain_label = DOMAIN_LABELS.get(str(row.get("domain") or DEFAULT_DOMAIN), str(row.get("domain") or ""))
        url = str(row.get("naver_url") or "").strip()
        if url:
            lines.append(f'{chapter_no}. <a href="{escape(url, quote=True)}">{escape(chapter_title)}</a>')
        else:
            lines.append(f"{chapter_no}. {chapter_title} (발행 예정)")
        lines.append(f"   - {domain_label} · {row.get('primary_keyword') or ''}".rstrip())
    lines.extend(
        [
            "",
            "---",
            "",
            "새 글이 발행되면 이 목차에 링크를 계속 업데이트합니다.",
            "각 글 본문에는 전체 목차를 길게 반복하지 않고, 이 목차글 링크만 짧게 연결합니다.",
        ]
    )
    markdown = "\n".join(lines).strip() + "\n"
    return {
        "title": title,
        "markdown": markdown,
        "body_hash": _hub_markdown_hash(markdown),
        "linked_node_count": linked,
        "total_node_count": total,
    }


def refresh_curriculum_hub_post_in_conn(conn: Any, *, track_id: int) -> dict[str, Any]:
    track_row = fetch_one(conn, "SELECT id, track_key, title FROM curriculum_track WHERE id = ?", (track_id,))
    if not track_row:
        raise ValueError(f"track_id={track_id} 를 찾지 못했습니다.")
    track = dict(track_row)
    rows = _curriculum_rows_for_track_in_conn(conn, track_id)
    rendered = render_curriculum_hub_markdown(track=track, rows=rows)
    hub_key = _hub_key(str(track["track_key"]))
    existing = fetch_one(conn, "SELECT id, body_hash, needs_sync, naver_url, status FROM curriculum_hub_post WHERE track_id = ?", (track_id,))
    if existing:
        body_changed = str(existing["body_hash"] or "") != rendered["body_hash"]
        needs_sync = 1 if body_changed else int(existing["needs_sync"] or 0)
        conn.execute(
            """
            UPDATE curriculum_hub_post
            SET hub_key = ?, title = ?, body_markdown = ?, body_hash = ?, linked_node_count = ?, total_node_count = ?,
                needs_sync = ?, last_rendered_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                hub_key,
                rendered["title"],
                rendered["markdown"],
                rendered["body_hash"],
                rendered["linked_node_count"],
                rendered["total_node_count"],
                needs_sync,
                existing["id"],
            ),
        )
        hub_id = int(existing["id"])
    else:
        conn.execute(
            """
            INSERT INTO curriculum_hub_post (
                track_id, hub_key, title, status, body_markdown, body_hash,
                linked_node_count, total_node_count, needs_sync, pinned, last_rendered_at
            ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, 1, 1, CURRENT_TIMESTAMP)
            """,
            (
                track_id,
                hub_key,
                rendered["title"],
                rendered["markdown"],
                rendered["body_hash"],
                rendered["linked_node_count"],
                rendered["total_node_count"],
            ),
        )
        hub_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    refreshed = fetch_one(conn, "SELECT * FROM curriculum_hub_post WHERE id = ?", (hub_id,))
    result = dict(refreshed) if refreshed else {}
    result["track_key"] = track["track_key"]
    return result


def refresh_curriculum_hub_posts_for_variant_in_conn(conn: Any, *, variant_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT DISTINCT cn.track_id
        FROM curriculum_node_variant cnv
        JOIN curriculum_node cn ON cn.id = cnv.node_id
        WHERE cnv.variant_id = ?
        """,
        (variant_id,),
    )
    return [refresh_curriculum_hub_post_in_conn(conn, track_id=int(row["track_id"])) for row in rows]


def refresh_curriculum_hub_post(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        track = fetch_one(conn, "SELECT id FROM curriculum_track WHERE track_key = ?", (track_key,))
        if not track:
            raise ValueError(f"track_key={track_key} 를 찾지 못했습니다. seed-curriculum을 먼저 실행하세요.")
        return refresh_curriculum_hub_post_in_conn(conn, track_id=int(track["id"]))


def get_curriculum_hub_post(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """
            SELECT chp.*, ct.track_key
            FROM curriculum_hub_post chp
            JOIN curriculum_track ct ON ct.id = chp.track_id
            WHERE ct.track_key = ?
            """,
            (track_key,),
        )
    return dict(row) if row else None


def set_curriculum_hub_url(
    db_path: str | Path,
    url: str,
    *,
    track_key: str = CURRICULUM_TRACK_KEY,
    synced: bool = True,
) -> dict[str, Any]:
    if not str(url or "").strip():
        raise ValueError("목차 허브글 URL이 필요합니다.")
    init_db(db_path)
    with connect(db_path) as conn:
        track = fetch_one(conn, "SELECT id FROM curriculum_track WHERE track_key = ?", (track_key,))
        if not track:
            raise ValueError(f"track_key={track_key} 를 찾지 못했습니다. seed-curriculum을 먼저 실행하세요.")
        hub = refresh_curriculum_hub_post_in_conn(conn, track_id=int(track["id"]))
        conn.execute(
            """
            UPDATE curriculum_hub_post
            SET naver_url = ?, status = 'published', needs_sync = ?,
                last_synced_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_synced_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(url).strip(), 0 if synced else 1, 1 if synced else 0, hub["id"]),
        )
        refreshed = fetch_one(conn, "SELECT * FROM curriculum_hub_post WHERE id = ?", (hub["id"],))
        result = dict(refreshed) if refreshed else {}
        result["track_key"] = track_key
        return result


def mark_curriculum_hub_synced(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        row = fetch_one(
            conn,
            """
            SELECT chp.id
            FROM curriculum_hub_post chp
            JOIN curriculum_track ct ON ct.id = chp.track_id
            WHERE ct.track_key = ?
            """,
            (track_key,),
        )
        if not row:
            raise ValueError(f"track_key={track_key} 목차 허브글을 찾지 못했습니다.")
        conn.execute(
            """
            UPDATE curriculum_hub_post
            SET needs_sync = 0, last_synced_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        )
        refreshed = fetch_one(conn, "SELECT * FROM curriculum_hub_post WHERE id = ?", (row["id"],))
        result = dict(refreshed) if refreshed else {}
        result["track_key"] = track_key
        return result


def seed_az_curriculum(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY) -> dict[str, Any]:
    init_db(db_path)
    created = {"tracks": 0, "nodes": 0, "clusters": 0, "variants": 0, "links": 0, "hub_posts": 0}
    with connect(db_path) as conn:
        track_existed = fetch_one(conn, "SELECT id FROM curriculum_track WHERE track_key = ?", (track_key,)) is not None
        track_id = _upsert_track(conn, track_key=track_key)
        created["tracks"] = 0 if track_existed else 1
        for spec in REAL_ESTATE_AZ_NODES:
            node_existed = fetch_one(conn, "SELECT id FROM curriculum_node WHERE node_key = ?", (spec.node_key,)) is not None
            node_id = _upsert_node(conn, track_id, spec, track_key=track_key)
            created["nodes"] += 0 if node_existed else 1
            cluster = _cluster_payload(spec, track_key=track_key)
            cluster_existed = fetch_one(conn, "SELECT id FROM topic_cluster WHERE semantic_key = ?", (cluster["semantic_key"],)) is not None
            cluster_id = _upsert_cluster(conn, cluster)
            created["clusters"] += 0 if cluster_existed else 1
            variant_key = _hash(f"curriculum-variant|{track_key}|{spec.node_key}|primary|{spec.title}", size=24)
            variant_existed = fetch_one(conn, "SELECT id FROM topic_variant WHERE variant_key = ?", (variant_key,)) is not None
            variant_id = _upsert_variant(conn, cluster_id, cluster, spec, track_key=track_key)
            created["variants"] += 0 if variant_existed else 1
            link_existed = fetch_one(conn, "SELECT id FROM curriculum_node_variant WHERE node_id = ? AND variant_id = ?", (node_id, variant_id)) is not None
            if not link_existed:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO curriculum_node_variant (node_id, variant_id, variant_role)
                    VALUES (?, ?, 'primary')
                    """,
                    (node_id, variant_id),
                )
                created["links"] += 1
        hub_existed = fetch_one(conn, "SELECT id FROM curriculum_hub_post WHERE track_id = ?", (track_id,)) is not None
        refresh_curriculum_hub_post_in_conn(conn, track_id=track_id)
        created["hub_posts"] = 0 if hub_existed else 1
    return {
        "track_key": track_key,
        "track_title": CURRICULUM_TRACK_TITLE,
        "target_ratio": CURRICULUM_TARGET_RATIO,
        "total_nodes": len(REAL_ESTATE_AZ_NODES),
        **created,
    }


def list_curriculum_plan(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY, limit: int | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    limit_clause = "LIMIT ?" if limit else ""
    params: tuple[Any, ...] = (track_key, limit) if limit else (track_key,)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            f"""
            WITH latest_publish AS (
                SELECT variant_id, MAX(id) AS publish_id
                FROM publish_history
                GROUP BY variant_id
            )
            SELECT
                cn.chapter_no,
                cn.part_no,
                cn.part_title,
                cn.node_key,
                cn.title AS chapter_title,
                cn.domain,
                cn.family,
                cn.primary_keyword,
                cn.search_intent,
                cn.status AS node_status,
                tv.id AS variant_id,
                tv.title AS variant_title,
                tv.status AS variant_status,
                tv.seo_score,
                ph.naver_url,
                ph.published_at
            FROM curriculum_node cn
            JOIN curriculum_track ct ON ct.id = cn.track_id
            LEFT JOIN curriculum_node_variant cnv ON cnv.node_id = cn.id AND cnv.variant_role = 'primary'
            LEFT JOIN topic_variant tv ON tv.id = cnv.variant_id
            LEFT JOIN latest_publish lp ON lp.variant_id = tv.id
            LEFT JOIN publish_history ph ON ph.id = lp.publish_id
            WHERE ct.track_key = ?
            ORDER BY cn.chapter_no ASC
            {limit_clause}
            """,
            params,
        )
    return [dict(row) for row in rows]


def curriculum_stats(db_path: str | Path, *, track_key: str = CURRICULUM_TRACK_KEY) -> dict[str, Any]:
    init_db(db_path)
    with connect(db_path) as conn:
        track = fetch_one(conn, "SELECT id, title, target_ratio, status FROM curriculum_track WHERE track_key = ?", (track_key,))
        if not track:
            return {"track_key": track_key, "exists": False}
        by_domain = fetch_all(
            conn,
            """
            SELECT cn.domain, COUNT(*) AS nodes,
                   SUM(CASE WHEN tv.status = 'published' THEN 1 ELSE 0 END) AS published,
                   SUM(CASE WHEN tv.status IN ('queued', 'drafted') THEN 1 ELSE 0 END) AS publishable
            FROM curriculum_node cn
            LEFT JOIN curriculum_node_variant cnv ON cnv.node_id = cn.id AND cnv.variant_role = 'primary'
            LEFT JOIN topic_variant tv ON tv.id = cnv.variant_id
            WHERE cn.track_id = ?
            GROUP BY cn.domain
            ORDER BY cn.domain
            """,
            (track["id"],),
        )
        totals = fetch_one(
            conn,
            """
            SELECT COUNT(*) AS nodes,
                   SUM(CASE WHEN tv.status = 'published' THEN 1 ELSE 0 END) AS published,
                   SUM(CASE WHEN tv.status IN ('queued', 'drafted') THEN 1 ELSE 0 END) AS publishable
            FROM curriculum_node cn
            LEFT JOIN curriculum_node_variant cnv ON cnv.node_id = cn.id AND cnv.variant_role = 'primary'
            LEFT JOIN topic_variant tv ON tv.id = cnv.variant_id
            WHERE cn.track_id = ?
            """,
            (track["id"],),
        )
        hub = fetch_one(conn, "SELECT status, naver_url, needs_sync, linked_node_count, total_node_count FROM curriculum_hub_post WHERE track_id = ?", (track["id"],))
    total_nodes = int(totals["nodes"] or 0)
    published = int(totals["published"] or 0)
    return {
        "track_key": track_key,
        "exists": True,
        "title": track["title"],
        "target_ratio": track["target_ratio"],
        "status": track["status"],
        "nodes": total_nodes,
        "published": published,
        "publishable": int(totals["publishable"] or 0),
        "progress_pct": (published / total_nodes * 100) if total_nodes else 0,
        "hub_post": dict(hub) if hub else None,
        "by_domain": [dict(row) for row in by_domain],
    }
