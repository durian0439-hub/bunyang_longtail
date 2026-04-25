from __future__ import annotations

import hashlib
import json
import re
from itertools import product
from pathlib import Path
from typing import Any

from .catalog import (
    ANGLE_PROMPTS,
    AUCTION_DOMAIN,
    DEFAULT_DOMAIN,
    DOMAIN_FAMILY_PRESETS,
    DOMAIN_TOPIC_BLUEPRINTS,
    FAMILY_PRESETS,
    NAVER_SEO_SECTIONS,
    SUPPORTED_DOMAINS,
    TAX_DOMAIN,
    TOPIC_BLUEPRINTS,
)
from .database import connect, fetch_all, fetch_one, init_db
from .prompt_builder import build_prompt_package

ANGLE_ORDER = ["판단형", "비교형", "실수방지형", "체크리스트형", "사례형", "FAQ형"]

INTENT_LABELS = {
    "조건정리": "조건 정리",
    "가능여부": "가능 여부",
    "비교": "비교 판단",
    "실수방지": "실수 방지",
    "체크리스트": "체크리스트",
    "전략": "전략",
    "계산": "계산",
    "사례": "사례",
    "FAQ": "FAQ",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _hash(value: str, size: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:size]


def _slugify(title: str, suffix: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", title).strip("-").lower()
    slug = re.sub(r"-+", "-", slug)
    return f"{slug[:80]}-{suffix}"


def _pick_secondary_keyword(blueprint: Any, preset: dict[str, Any]) -> str:
    candidates = [*blueprint.secondary_keywords, *preset["secondaries"]]
    for item in candidates:
        if item and item != blueprint.primary_keyword:
            return item
    return blueprint.comparison_keyword or blueprint.primary_keyword


def _normalize_domain(domain: str | None) -> str:
    normalized = (domain or DEFAULT_DOMAIN).strip()
    if normalized not in SUPPORTED_DOMAINS:
        raise ValueError(f"지원하지 않는 domain 입니다: {domain}")
    return normalized


def _semantic_key(primary_keyword: str, audience: str, search_intent: str, scenario: str, *, domain: str = DEFAULT_DOMAIN) -> str:
    # 기존 청약 DB와 호환되도록 cheongyak semantic_key는 v1 산식을 유지하고,
    # 신규 경매 도메인만 domain salt를 추가한다.
    parts = [primary_keyword, audience, search_intent, scenario]
    if domain != DEFAULT_DOMAIN:
        parts.insert(0, domain)
    canonical = "|".join(parts)
    return _hash(canonical, size=20)


def _has_final_consonant(word: str) -> bool:
    korean_chars = [ch for ch in word if "가" <= ch <= "힣"]
    if not korean_chars:
        return False
    last = korean_chars[-1]
    return (ord(last) - ord("가")) % 28 != 0


def _particle(word: str, pair: tuple[str, str]) -> str:
    return pair[0] if _has_final_consonant(word) else pair[1]


def _topic_scene(primary: str, scenario: str) -> str:
    """제목에서 `경매 공부 공부 순서`처럼 키워드와 시나리오가 반복되는 것을 줄인다."""
    primary = _clean(primary)
    scenario = _clean(scenario)
    if not scenario:
        return primary
    if scenario.startswith(primary):
        scenario = scenario[len(primary):].strip()
    else:
        last_token = primary.split()[-1] if primary.split() else ""
        if last_token and scenario.startswith(last_token):
            scenario = scenario[len(last_token):].strip()
    return _clean(f"{primary} {scenario}") if scenario else primary


def _build_outline(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    primary = cluster["primary_keyword"]
    secondary = cluster["secondary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    comparison = cluster["comparison_keyword"] or secondary
    return [
        {
            "heading": "상단 요약",
            "points": [
                f"{audience} 기준 {primary} 결론을 먼저 제시",
                f"{scenario} 바로 체크해야 할 핵심 3가지",
            ],
        },
        {
            "heading": "이 글에서 바로 답하는 질문",
            "points": [
                f"{primary}{_particle(primary, ('이', '가'))} 왜 지금 문제인지",
                f"{comparison}{_particle(comparison, ('과', '와'))} 무엇이 다른지",
            ],
        },
        {
            "heading": "핵심 조건 정리",
            "points": [
                f"{primary} 기본 조건",
                f"{secondary}{_particle(secondary, ('과', '와'))} 연결되는 판단 기준",
            ],
        },
        {
            "heading": "헷갈리기 쉬운 예외",
            "points": [
                f"{scenario} 자주 틀리는 예외",
                "부적격 또는 탈락으로 이어질 수 있는 포인트",
            ],
        },
        {
            "heading": "실전 예시 시나리오",
            "points": [
                f"{audience} 가상 사례 1개",
                "신청 가능/불가 또는 유리/불리 판정 흐름",
            ],
        },
        {
            "heading": "체크리스트",
            "points": [
                "신청 전에 확인할 항목 5개 이상",
                "문서/자금/기한/자격 순서로 정리",
            ],
        },
        {
            "heading": "FAQ",
            "points": [
                f"{primary} 관련 자주 묻는 질문 6개 이상",
                "짧고 명확한 답변 형태 유지",
            ],
        },
        {
            "heading": "마무리 결론",
            "points": [
                f"{audience}에게 맞는 행동 제안 1줄",
                "지금 바로 확인할 다음 단계 제시",
            ],
        },
    ]


def _build_auction_outline(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    primary = cluster["primary_keyword"]
    secondary = cluster["secondary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    comparison = cluster["comparison_keyword"] or secondary
    return [
        {
            "heading": "상단 요약",
            "points": [
                f"{audience} 기준 {primary}에서 먼저 걸러야 할 결론",
                f"{scenario} 확인할 비용·권리·점유 리스크 3가지",
            ],
        },
        {
            "heading": "이 글에서 바로 답하는 질문",
            "points": [
                f"{primary}{_particle(primary, ('이', '가'))} 왜 입찰 전에 중요한지",
                f"{comparison}{_particle(comparison, ('과', '와'))} 어떤 순서로 비교해야 하는지",
            ],
        },
        {
            "heading": "핵심 조건 정리",
            "points": [
                f"{primary} 기본 의미와 확인 서류",
                f"{secondary}{_particle(secondary, ('과', '와'))} 함께 봐야 하는 판단 기준",
            ],
        },
        {
            "heading": "헷갈리기 쉬운 예외",
            "points": [
                f"{scenario} 자주 놓치는 예외",
                "권리 인수, 점유, 잔금, 대출에서 손실로 이어질 수 있는 포인트",
            ],
        },
        {
            "heading": "실전 예시 시나리오",
            "points": [
                f"{audience} 가상 사례 1개",
                "입찰 가능/보류/회피 판단 흐름",
            ],
        },
        {
            "heading": "체크리스트",
            "points": [
                "입찰 전 확인할 항목 5개 이상",
                "서류/권리/점유/자금/출구 순서로 정리",
            ],
        },
        {
            "heading": "FAQ",
            "points": [
                f"{primary} 관련 자주 묻는 질문 6개 이상",
                "단정 대신 확인 순서와 리스크 범위를 짧게 답변",
            ],
        },
        {
            "heading": "마무리 결론",
            "points": [
                f"{audience}에게 맞는 다음 행동 제안 1줄",
                "법원 서류, 현장, 대출 가능 여부를 다시 확인하도록 안내",
            ],
        },
    ]


def _build_tax_outline(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    primary = cluster["primary_keyword"]
    secondary = cluster["secondary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    comparison = cluster["comparison_keyword"] or secondary
    return [
        {
            "heading": "상단 요약",
            "points": [
                f"{audience} 기준 {primary}에서 먼저 확인할 세금 발생 시점",
                f"{scenario} 계산 전에 봐야 할 주택 수·명의·기한 3가지",
            ],
        },
        {
            "heading": "이 글에서 바로 답하는 질문",
            "points": [
                f"{primary}{_particle(primary, ('이', '가'))} 언제 생기는 세금인지",
                f"{comparison}{_particle(comparison, ('과', '와'))} 어떤 기준으로 비교해야 하는지",
            ],
        },
        {
            "heading": "핵심 조건 정리",
            "points": [
                f"{primary} 과세 대상과 계산 기준",
                f"{secondary}{_particle(secondary, ('과', '와'))} 함께 확인할 주택 수·보유기간·명의 기준",
            ],
        },
        {
            "heading": "헷갈리기 쉬운 예외",
            "points": [
                f"{scenario} 자주 놓치는 감면·중과·비과세 예외",
                "신고기한, 가산세, 공식 계산 기준에서 달라질 수 있는 포인트",
            ],
        },
        {
            "heading": "실전 예시 시나리오",
            "points": [
                f"{audience} 가상 사례 1개",
                "계산 예시와 최종 확인 순서",
            ],
        },
        {
            "heading": "체크리스트",
            "points": [
                "계약·잔금·매도·신고 전 확인할 항목 5개 이상",
                "홈택스, 위택스, 지자체 안내, 세무 상담 전 준비 순서",
            ],
        },
        {
            "heading": "FAQ",
            "points": [
                f"{primary} 관련 자주 묻는 질문 6개 이상",
                "단정 대신 조건과 공식 확인 위치를 짧게 답변",
            ],
        },
        {
            "heading": "마무리 결론",
            "points": [
                f"{audience}에게 맞는 다음 확인 순서 1줄",
                "세법 적용 시점과 개인 조건은 공식 안내와 전문가 상담으로 재확인하도록 안내",
            ],
        },
    ]


def _compose_auction_title(cluster: dict[str, Any], angle: str) -> str:
    primary = cluster["primary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    intent = cluster["search_intent"]
    comparison = cluster["comparison_keyword"] or cluster["secondary_keyword"]
    topic_scene = _topic_scene(primary, scenario)

    if angle == "비교형":
        title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, {audience}는 무엇부터 봐야 할까"
    elif angle == "실수방지형":
        title = f"{topic_scene}, 입찰 전 놓치면 손해 보는 기준"
    elif angle == "체크리스트형":
        title = f"{topic_scene}, 입찰 전 체크리스트"
    elif angle == "사례형":
        title = f"{topic_scene}, 실제 사례로 보는 판단 순서"
    elif angle == "FAQ형":
        title = f"{primary} FAQ, {audience}가 가장 헷갈리는 질문"
    else:
        if intent == "가능여부":
            title = f"{topic_scene}, 입찰해도 될지 보는 기준"
        elif intent == "비교":
            title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, 경매초보가 헷갈리는 차이"
        elif intent == "실수방지":
            title = f"{topic_scene}, 가장 많이 놓치는 위험"
        elif intent == "체크리스트":
            title = f"{topic_scene}, 확인 순서 한 번에 정리"
        elif intent == "전략":
            title = f"{topic_scene}, 무리하지 않는 입찰 전략"
        elif intent == "계산":
            title = f"{topic_scene}, 실제 비용 계산 순서"
        elif intent == "사례":
            title = f"{topic_scene}, 케이스로 보면 답이 보인다"
        elif intent == "FAQ":
            title = f"{primary} 자주 묻는 질문, 입찰 전 기준 정리"
        else:
            title = f"{topic_scene}, {audience} 기준 한 번에 정리"
    return _clean(title)


def _compose_tax_title(cluster: dict[str, Any], angle: str) -> str:
    primary = cluster["primary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    intent = cluster["search_intent"]
    comparison = cluster["comparison_keyword"] or cluster["secondary_keyword"]
    topic_scene = _topic_scene(primary, scenario)

    if angle == "비교형":
        title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, 어떤 세금이 더 부담될까"
    elif angle == "실수방지형":
        title = f"{topic_scene}, 신고 전에 많이 놓치는 부분"
    elif angle == "체크리스트형":
        title = f"{topic_scene}, 신고 전 체크리스트"
    elif angle == "사례형":
        title = f"{topic_scene}, 사례로 보는 세금 흐름"
    elif angle == "FAQ형":
        title = f"{primary} FAQ, {audience}가 가장 헷갈리는 질문"
    else:
        if intent == "계산":
            title = f"{topic_scene}, 실제 세금 계산 순서"
        elif intent == "가능여부":
            title = f"{topic_scene}, 감면이나 비과세 가능할까"
        elif intent == "비교":
            title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, {audience} 기준 차이"
        elif intent == "실수방지":
            title = f"{topic_scene}, 세금에서 자주 틀리는 기준"
        elif intent == "체크리스트":
            title = f"{topic_scene}, 먼저 확인할 순서"
        elif intent == "신고방법":
            title = f"{topic_scene}, 신고 전에 준비할 것"
        elif intent == "사례":
            title = f"{topic_scene}, 실제 사례로 보는 기준"
        elif intent == "FAQ":
            title = f"{primary} 자주 묻는 질문, 세금 기준 정리"
        else:
            title = f"{topic_scene}, {audience}가 먼저 볼 기준"
    return _clean(title)


def _compose_title(cluster: dict[str, Any], angle: str) -> str:
    if cluster.get("domain") == AUCTION_DOMAIN:
        return _compose_auction_title(cluster, angle)
    if cluster.get("domain") == TAX_DOMAIN:
        return _compose_tax_title(cluster, angle)

    primary = cluster["primary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    intent = cluster["search_intent"]
    comparison = cluster["comparison_keyword"] or cluster["secondary_keyword"]
    topic_scene = _topic_scene(primary, scenario)

    if angle == "비교형":
        title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, {audience}는 무엇이 다를까"
    elif angle == "실수방지형":
        title = f"{topic_scene}, {audience}가 놓치면 탈락하는 포인트"
    elif angle == "체크리스트형":
        title = f"{topic_scene}, {audience} 신청 전 체크리스트"
    elif angle == "사례형":
        title = f"{topic_scene}, {audience} 사례로 보면 답이 보인다"
    elif angle == "FAQ형":
        title = f"{primary} FAQ, {audience}가 가장 헷갈리는 질문"
    else:
        if intent == "가능여부":
            title = f"{topic_scene}, {audience}도 가능할까"
        elif intent == "비교":
            title = f"{primary}{_particle(primary, ('과', '와'))} {comparison}, {audience}는 무엇이 유리할까"
        elif intent == "실수방지":
            title = f"{topic_scene}, 가장 많이 틀리는 기준은 이것"
        elif intent == "체크리스트":
            title = f"{topic_scene}, 꼭 확인할 순서 한 번에 정리"
        elif intent == "전략":
            title = f"{topic_scene}, {audience} 전략은 이렇게 짜야 한다"
        elif intent == "계산":
            title = f"{topic_scene}, 얼마가 필요한지 계산해보기"
        elif intent == "사례":
            title = f"{topic_scene}, 실제 케이스로 보면 이렇게 풀린다"
        elif intent == "FAQ":
            title = f"{primary} 자주 묻는 질문, {audience} 기준으로 정리"
        else:
            title = f"{topic_scene}, {audience} 기준 한 번에 정리"
    return _clean(title)


def _ensure_unique_title(conn: Any, title: str, cluster: dict[str, Any]) -> str:
    if not fetch_one(conn, "SELECT id FROM topic_variant WHERE title = ?", (title,)):
        return title
    extras = [
        INTENT_LABELS.get(cluster["search_intent"], cluster["search_intent"]),
        cluster["scenario"],
        cluster["audience"],
        cluster["primary_keyword"],
    ]
    for extra in extras:
        candidate = _clean(f"{title}, {extra} 기준")
        if not fetch_one(conn, "SELECT id FROM topic_variant WHERE title = ?", (candidate,)):
            return candidate
    return _clean(f"{title}, {_hash(cluster['semantic_key'], size=6)}")


def _tax_keyword_sources() -> list[str]:
    return [
        "google_autocomplete",
        "naver_autocomplete",
        "bing_osjson",
        "daum_suggest",
        "nts.go.kr",
        "hometax.go.kr",
        "wetax.go.kr",
        "moleg.go.kr",
        "gov.kr",
    ]


def _domain_keyword_sources(domain: str) -> list[str]:
    if domain == AUCTION_DOMAIN:
        return ["google_autocomplete", "naver_autocomplete", "bing_osjson", "daum_suggest", "courtauction.go.kr", "onbid.co.kr"]
    if domain == TAX_DOMAIN:
        return _tax_keyword_sources()
    return []


def _estimate_seo_score(title: str, cluster: dict[str, Any]) -> int:
    score = 40
    primary = cluster["primary_keyword"]
    audience = cluster["audience"]
    scenario = cluster["scenario"]
    comparison = cluster["comparison_keyword"] or cluster["secondary_keyword"]

    if title.startswith(primary):
        score += 20
    elif primary in title[:16]:
        score += 12
    if audience in title:
        score += 10
    if scenario in title:
        score += 10
    if comparison and comparison in title:
        score += 5
    if 18 <= len(title) <= 42:
        score += 10
    if any(word in title for word in ["정리", "가능할까", "체크리스트", "FAQ", "차이"]):
        score += 5
    if cluster.get("domain") == TAX_DOMAIN and any(word in title for word in ["계산", "신고", "감면", "비과세", "세금", "기준"]):
        score += 5
    return min(score, 100)


def iter_cluster_candidates(domain: str = DEFAULT_DOMAIN) -> list[dict[str, Any]]:
    domain = _normalize_domain(domain)
    family_presets = DOMAIN_FAMILY_PRESETS[domain]
    topic_blueprints = DOMAIN_TOPIC_BLUEPRINTS[domain]
    family_buckets: dict[str, list[dict[str, Any]]] = {family: [] for family in family_presets}
    for blueprint in topic_blueprints:
        preset = family_presets[blueprint.family]
        audiences = blueprint.audiences or tuple(preset["audiences"])
        intents = blueprint.intents or tuple(preset["intents"])
        scenarios = blueprint.scenarios or tuple(preset["scenarios"])
        secondary_keyword = _pick_secondary_keyword(blueprint, preset)
        for audience, intent, scenario in product(audiences, intents, scenarios):
            semantic_key = _semantic_key(blueprint.primary_keyword, audience, intent, scenario, domain=domain)
            cluster = {
                "domain": domain,
                "semantic_key": semantic_key,
                "family": blueprint.family,
                "primary_keyword": blueprint.primary_keyword,
                "secondary_keyword": secondary_keyword,
                "audience": audience,
                "search_intent": intent,
                "scenario": scenario,
                "comparison_keyword": blueprint.comparison_keyword or secondary_keyword,
                "priority": preset["priority"],
                "policy_json": json.dumps({
                    "route_policy": "gpt_web_first",
                    "domain": domain,
                    "keyword_sources": _domain_keyword_sources(domain),
                }, ensure_ascii=False),
            }
            if domain == AUCTION_DOMAIN:
                outline = _build_auction_outline(cluster)
            elif domain == TAX_DOMAIN:
                outline = _build_tax_outline(cluster)
            else:
                outline = _build_outline(cluster)
            cluster["outline_json"] = json.dumps(outline, ensure_ascii=False)
            family_buckets[blueprint.family].append(cluster)

    for family, rows in family_buckets.items():
        rows.sort(key=lambda row: (row["primary_keyword"], row["audience"], row["search_intent"], row["scenario"]))

    family_order = [
        family
        for family, _ in sorted(family_presets.items(), key=lambda item: -item[1]["priority"])
    ]
    candidates: list[dict[str, Any]] = []
    added = True
    while added:
        added = False
        for family in family_order:
            bucket = family_buckets[family]
            if bucket:
                candidates.append(bucket.pop(0))
                added = True
    return candidates


def replenish_queue(db_path: str | Path, min_queued: int = 500, variants_per_cluster: int = 4, *, domain: str = DEFAULT_DOMAIN) -> dict[str, int]:
    domain = _normalize_domain(domain)
    init_db(db_path)
    created_clusters = 0
    created_variants = 0
    cluster_candidates = iter_cluster_candidates(domain=domain)
    target_cluster_count = len(cluster_candidates)
    max_new_clusters_per_run = max(min_queued, 64)

    with connect(db_path) as conn:
        queued_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM topic_variant tv
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE tv.status = 'queued' AND tc.domain = ?
            """,
            (domain,),
        ).fetchone()[0]
        cluster_count = conn.execute("SELECT COUNT(*) FROM topic_cluster WHERE domain = ?", (domain,)).fetchone()[0]
        if queued_count >= min_queued and cluster_count >= target_cluster_count:
            return {
                "queued": queued_count,
                "created_clusters": 0,
                "created_variants": 0,
                "cluster_count": cluster_count,
                "target_cluster_count": target_cluster_count,
                "domain": domain,
            }

        family_counts = {
            row[0]: row[1]
            for row in conn.execute("SELECT family, COUNT(*) FROM topic_cluster WHERE domain = ? GROUP BY family", (domain,)).fetchall()
        }
        keyword_counts = {
            row[0]: row[1]
            for row in conn.execute("SELECT primary_keyword, COUNT(*) FROM topic_cluster WHERE domain = ? GROUP BY primary_keyword", (domain,)).fetchall()
        }
        existing_semantic = {
            row[0] for row in conn.execute("SELECT semantic_key FROM topic_cluster WHERE domain = ?", (domain,)).fetchall()
        }

        def cluster_sort_key(cluster: dict[str, Any]) -> tuple[int, int, int, str, str]:
            family_count = int(family_counts.get(cluster['family'], 0))
            keyword_count = int(keyword_counts.get(cluster['primary_keyword'], 0))
            return (keyword_count, family_count, -int(cluster['priority']), cluster['family'], cluster['scenario'])

        missing_clusters = [c for c in cluster_candidates if c['semantic_key'] not in existing_semantic]
        missing_clusters.sort(key=cluster_sort_key)

        selected_clusters: list[dict[str, Any]] = []
        seen_keywords: set[str] = set()
        for cluster in missing_clusters:
            keyword = str(cluster['primary_keyword'])
            if keyword in seen_keywords:
                continue
            selected_clusters.append(cluster)
            seen_keywords.add(keyword)
            if len(selected_clusters) >= max_new_clusters_per_run:
                break
        if len(selected_clusters) < max_new_clusters_per_run:
            for cluster in missing_clusters:
                if cluster in selected_clusters:
                    continue
                selected_clusters.append(cluster)
                if len(selected_clusters) >= max_new_clusters_per_run:
                    break

        if not selected_clusters and queued_count < min_queued:
            existing_candidates = [c for c in cluster_candidates if c['semantic_key'] in existing_semantic]
            existing_candidates.sort(key=cluster_sort_key)
            for cluster in existing_candidates:
                keyword = str(cluster['primary_keyword'])
                if keyword in seen_keywords:
                    continue
                selected_clusters.append(cluster)
                seen_keywords.add(keyword)
                if len(selected_clusters) >= max_new_clusters_per_run:
                    break
            if len(selected_clusters) < max_new_clusters_per_run:
                for cluster in existing_candidates:
                    if cluster in selected_clusters:
                        continue
                    selected_clusters.append(cluster)
                    if len(selected_clusters) >= max_new_clusters_per_run:
                        break

        for cluster in selected_clusters:
            existing_cluster = fetch_one(
                conn,
                "SELECT id FROM topic_cluster WHERE domain = ? AND semantic_key = ?",
                (domain, cluster["semantic_key"]),
            )
            if not existing_cluster:
                conn.execute(
                    """
                    INSERT INTO topic_cluster (
                        domain, semantic_key, family, primary_keyword, secondary_keyword, audience,
                        search_intent, scenario, comparison_keyword, priority, outline_json, policy_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cluster["domain"],
                        cluster["semantic_key"],
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
                    ),
                )
                created_clusters += 1
                family_counts[cluster['family']] = int(family_counts.get(cluster['family'], 0)) + 1
                keyword_counts[cluster['primary_keyword']] = int(keyword_counts.get(cluster['primary_keyword'], 0)) + 1
                cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                cluster_id = existing_cluster["id"]

            existing_variants = fetch_one(
                conn,
                "SELECT COUNT(*) AS cnt FROM topic_variant WHERE cluster_id = ?",
                (cluster_id,),
            )["cnt"]
            if existing_variants >= variants_per_cluster:
                continue

            for angle in ANGLE_ORDER:
                if existing_variants >= variants_per_cluster or queued_count >= min_queued:
                    break
                title = _compose_title(cluster, angle)
                variant_seed = f"{cluster['semantic_key']}|{angle}|{title}"
                variant_key = _hash(variant_seed, size=24)
                if fetch_one(conn, "SELECT id FROM topic_variant WHERE variant_key = ?", (variant_key,)):
                    continue
                title = _ensure_unique_title(conn, title, cluster)
                variant = {"angle": angle, "title": title}
                prompt_payload = build_prompt_package(cluster, variant)
                slug = _slugify(title, variant_key[:6])
                seo_score = _estimate_seo_score(title, cluster)
                conn.execute(
                    """
                    INSERT INTO topic_variant (
                        cluster_id, variant_key, angle, title, slug, seo_score,
                        prompt_json, prompt_version, route_policy, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'v1', 'gpt_web_first', 'queued')
                    """,
                    (
                        cluster_id,
                        variant_key,
                        angle,
                        title,
                        slug,
                        seo_score,
                        json.dumps(prompt_payload, ensure_ascii=False),
                    ),
                )
                created_variants += 1
                existing_variants += 1
                queued_count += 1

        cluster_count = conn.execute("SELECT COUNT(*) FROM topic_cluster WHERE domain = ?", (domain,)).fetchone()[0]

    return {
        "queued": queued_count,
        "created_clusters": created_clusters,
        "created_variants": created_variants,
        "cluster_count": cluster_count,
        "target_cluster_count": target_cluster_count,
        "domain": domain,
    }

def list_variants(db_path: str | Path, status: str = "queued", limit: int = 20, *, domain: str = DEFAULT_DOMAIN) -> list[dict[str, Any]]:
    domain = _normalize_domain(domain)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT v.id, v.title, v.slug, v.angle, v.seo_score, v.status,
                   c.domain, c.primary_keyword, c.secondary_keyword, c.audience, c.search_intent, c.scenario
            FROM topic_variant v
            JOIN topic_cluster c ON c.id = v.cluster_id
            WHERE v.status = ? AND c.domain = ?
            ORDER BY v.seo_score DESC, v.id ASC
            LIMIT ?
            """,
            (status, domain, limit),
        )
    return [dict(row) for row in rows]


def get_prompt(db_path: str | Path, variant_id: int | None = None, slug: str | None = None) -> dict[str, Any] | None:
    if not variant_id and not slug:
        raise ValueError("variant_id 또는 slug 중 하나는 필요합니다.")
    query = (
        """
        SELECT v.id, v.title, v.slug, v.angle, v.prompt_json, v.prompt_version, v.route_policy,
               c.domain, c.primary_keyword, c.secondary_keyword, c.audience, c.search_intent, c.scenario, c.policy_json
        FROM topic_variant v
        JOIN topic_cluster c ON c.id = v.cluster_id
        WHERE v.id = ?
        """
        if variant_id
        else
        """
        SELECT v.id, v.title, v.slug, v.angle, v.prompt_json, v.prompt_version, v.route_policy,
               c.domain, c.primary_keyword, c.secondary_keyword, c.audience, c.search_intent, c.scenario, c.policy_json
        FROM topic_variant v
        JOIN topic_cluster c ON c.id = v.cluster_id
        WHERE v.slug = ?
        """
    )
    value = variant_id if variant_id is not None else slug
    with connect(db_path) as conn:
        row = fetch_one(conn, query, (value,))
    if not row:
        return None
    result = dict(row)
    result["prompt_json"] = json.loads(result["prompt_json"])
    if result.get("policy_json"):
        result["policy_json"] = json.loads(result["policy_json"])
    return result


def export_prompts(db_path: str | Path, output_path: str | Path, status: str = "queued", limit: int = 100, *, domain: str = DEFAULT_DOMAIN) -> int:
    domain = _normalize_domain(domain)
    with connect(db_path) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT v.id, v.title, v.slug, v.angle, v.seo_score, v.prompt_json, v.prompt_version, v.route_policy,
                   c.domain, c.primary_keyword, c.secondary_keyword, c.audience, c.search_intent, c.scenario
            FROM topic_variant v
            JOIN topic_cluster c ON c.id = v.cluster_id
            WHERE v.status = ? AND c.domain = ?
            ORDER BY v.seo_score DESC, v.id ASC
            LIMIT ?
            """,
            (status, domain, limit),
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            item = dict(row)
            item["prompt_json"] = json.loads(item["prompt_json"])
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(rows)

def mark_published(db_path: str | Path, variant_id: int, url: str, *, published_title: str | None = None) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = fetch_one(conn, "SELECT title FROM topic_variant WHERE id = ?", (variant_id,))
        if not row:
            raise ValueError(f"variant_id={variant_id} 를 찾지 못했습니다.")
        draft = fetch_one(
            conn,
            "SELECT id, title, bundle_id FROM article_draft WHERE variant_id = ? ORDER BY id DESC LIMIT 1",
            (variant_id,),
        )
        if draft:
            draft_id = draft["id"]
            bundle_id = draft["bundle_id"]
            published_title = published_title or draft["title"]
            conn.execute(
                """
                UPDATE article_draft
                SET status = 'published', naver_url = ?, published_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (url, draft_id),
            )
        else:
            bundle = fetch_one(
                conn,
                "SELECT id FROM article_bundle WHERE variant_id = ? ORDER BY id DESC LIMIT 1",
                (variant_id,),
            )
            bundle_id = bundle["id"] if bundle else None
            conn.execute(
                """
                INSERT INTO article_draft (bundle_id, variant_id, title, status, naver_url, published_at)
                VALUES (?, ?, ?, 'published', ?, CURRENT_TIMESTAMP)
                """,
                (bundle_id, variant_id, row["title"], url),
            )
            draft_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            published_title = published_title or row["title"]
        conn.execute(
            """
            UPDATE topic_variant
            SET status = 'published', use_count = use_count + 1, last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (variant_id,),
        )
        if bundle_id is not None:
            conn.execute(
                "UPDATE article_bundle SET bundle_status = 'published', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (bundle_id,),
            )
        conn.execute(
            """
            INSERT INTO publish_history (
                bundle_id, variant_id, draft_id, channel, target_account, publish_mode,
                published_title, naver_url, published_at, result_json
            ) VALUES (?, ?, ?, 'naver_blog', 'default', 'published', ?, ?, CURRENT_TIMESTAMP, ?)
            """,
            (bundle_id, variant_id, draft_id, published_title, url, json.dumps({"url": url}, ensure_ascii=False)),
        )


def stats(db_path: str | Path, *, domain: str | None = DEFAULT_DOMAIN) -> dict[str, Any]:
    if domain is not None:
        domain = _normalize_domain(domain)
    init_db(db_path)
    domain_where = "WHERE domain = ?" if domain else ""
    domain_params: tuple[Any, ...] = (domain,) if domain else ()
    variant_domain_join = "JOIN topic_cluster tc ON tc.id = tv.cluster_id"
    variant_domain_filter = "AND tc.domain = ?" if domain else ""
    with connect(db_path) as conn:
        cluster_count = conn.execute(f"SELECT COUNT(*) FROM topic_cluster {domain_where}", domain_params).fetchone()[0]
        total_variants = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM topic_variant tv
            {variant_domain_join}
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        total_bundles = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM article_bundle ab
            JOIN topic_variant tv ON tv.id = ab.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        total_jobs = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM generation_job gj
            JOIN topic_variant tv ON tv.id = gj.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        total_drafts = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM article_draft ad
            JOIN topic_variant tv ON tv.id = ad.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        total_assets = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM image_asset ia
            JOIN topic_variant tv ON tv.id = ia.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        total_published = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            """,
            domain_params,
        ).fetchone()[0]
        status_rows = fetch_all(
            conn,
            f"""
            SELECT tv.status AS status, COUNT(*) AS cnt
            FROM topic_variant tv
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            GROUP BY tv.status
            ORDER BY tv.status
            """,
            domain_params,
        )
        bundle_status_rows = fetch_all(
            conn,
            f"""
            SELECT ab.bundle_status AS bundle_status, COUNT(*) AS cnt
            FROM article_bundle ab
            JOIN topic_variant tv ON tv.id = ab.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE 1=1 {variant_domain_filter}
            GROUP BY ab.bundle_status
            ORDER BY ab.bundle_status
            """,
            domain_params,
        )
        family_rows = fetch_all(
            conn,
            f"SELECT family, COUNT(*) AS cnt FROM topic_cluster {domain_where} GROUP BY family ORDER BY cnt DESC",
            domain_params,
        )
        domain_rows = fetch_all(conn, "SELECT domain, COUNT(*) AS cnt FROM topic_cluster GROUP BY domain ORDER BY domain")
        top_rows = fetch_all(
            conn,
            f"""
            SELECT v.id, v.title, v.seo_score, c.domain, c.primary_keyword, c.audience
            FROM topic_variant v
            JOIN topic_cluster c ON c.id = v.cluster_id
            WHERE 1=1 {('AND c.domain = ?' if domain else '')}
            ORDER BY v.seo_score DESC, v.id ASC
            LIMIT 10
            """,
            domain_params,
        )
    return {
        "domain": domain or "all",
        "clusters": cluster_count,
        "variants": total_variants,
        "bundles": total_bundles,
        "jobs": total_jobs,
        "drafts": total_drafts,
        "image_assets": total_assets,
        "publish_history": total_published,
        "by_status": [dict(row) for row in status_rows],
        "by_bundle_status": [dict(row) for row in bundle_status_rows],
        "by_family": [dict(row) for row in family_rows],
        "by_domain": [dict(row) for row in domain_rows],
        "top_titles": [dict(row) for row in top_rows],
        "angle_count": len(ANGLE_PROMPTS),
        "section_count": len(NAVER_SEO_SECTIONS),
    }
