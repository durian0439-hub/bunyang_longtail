from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class HumanizeFinding:
    pattern_id: str
    category: str
    severity: str
    text: str
    reason: str


AI_TELL_PATTERNS: tuple[tuple[str, str, str, str, str], ...] = (
    ("A-1", "번역투", "S2", r"에 있어서", "'~에 있어서' 번역투가 남아 있습니다."),
    ("A-2", "번역투", "S2", r"를 통해|을 통해", "'~를 통해'가 반복되면 AI 번역투처럼 보입니다."),
    ("A-3", "번역투", "S2", r"에 대해|에 대한", "'~에 대해/대한' 의존 표현이 과합니다."),
    ("A-8", "이중 피동", "S1", r"되어진|되어지는|되어졌다", "이중 피동은 결정적인 AI 문체 신호입니다."),
    ("A-10", "가능형 남용", "S2", r"할 수 있습니다|될 수 있습니다|볼 수 있습니다", "가능형이 반복되면 단정이 흐려집니다."),
    ("C-1", "기계적 병렬", "S2", r"첫째[,·.]|둘째[,·.]|셋째[,·.]", "기계적 순서 나열은 AI 글처럼 보입니다."),
    ("D-1", "AI 관용구", "S1", r"결론적으로|요약하면|정리하자면", "결론 라벨링 관용구는 삭제 우선입니다."),
    ("D-2", "AI 관용구", "S1", r"시사하는 바가 크다|주목할 만하다", "AI식 평가 관용구입니다."),
    ("D-3", "AI 관용구", "S1", r"혁신적인|획기적인", "근거 없는 과장 수식은 AI 문체 신호입니다."),
    ("F-1", "과잉 수식", "S3", r"매우|정말|대단히", "정도부사가 반복되면 글이 기계적으로 보입니다."),
    ("H-1", "문두 접속사", "S2", r"(?:^|\n)\s*(또한|따라서|즉|나아가|그리고|하지만)[,\s]", "문두 접속사 남발은 AI 글 리듬을 만듭니다."),
    ("I-1", "형식명사", "S2", r"것입니다|것이다|할 필요가 있습니다|할 필요가 있다", "형식명사·필요 표현이 문장을 늘립니다."),
    ("J-1", "장식 과다", "S2", r"—|✨|✅|🔥|👉", "블로그 본문 장식이 과하면 AI 생성물처럼 보입니다."),
)


REPETITION_THRESHOLDS: tuple[tuple[str, str, str, int, str], ...] = (
    ("A-2", "번역투 반복", r"를 통해|을 통해", 3, "'~를 통해'가 3회 이상 반복됩니다."),
    ("A-10", "가능형 반복", r"할 수 있습니다|될 수 있습니다|볼 수 있습니다", 5, "가능형이 5회 이상 반복됩니다."),
    ("H-1", "문두 접속사 반복", r"(?:^|\n)\s*(또한|따라서|즉|나아가|그리고|하지만)[,\s]", 4, "문두 접속사가 4회 이상 반복됩니다."),
    ("I-1", "형식명사 반복", r"것입니다|것이다|할 필요가 있습니다|할 필요가 있다", 5, "형식명사가 5회 이상 반복됩니다."),
)


PROTECTED_LINE_PREFIXES = ("#", "http://", "https://")


def _iter_scannable_lines(text: str) -> Iterable[str]:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(PROTECTED_LINE_PREFIXES):
            continue
        yield line


def detect_ai_tell_findings(article_markdown: str, *, min_severity: str = "S2") -> list[HumanizeFinding]:
    """Return lightweight Korean AI-tell findings adapted from im-not-ai taxonomy.

    This is intentionally deterministic and conservative. It does not rewrite text;
    it only blocks clear AI-style residue before Naver publishing.
    """
    severity_rank = {"S1": 1, "S2": 2, "S3": 3}
    max_rank = severity_rank.get(min_severity, 2)
    text = "\n".join(_iter_scannable_lines(article_markdown))
    findings: list[HumanizeFinding] = []

    for pattern_id, category, severity, pattern, reason in AI_TELL_PATTERNS:
        if severity_rank.get(severity, 3) > max_rank:
            continue
        match = re.search(pattern, text)
        if match:
            findings.append(
                HumanizeFinding(
                    pattern_id=pattern_id,
                    category=category,
                    severity=severity,
                    text=match.group(0).strip(),
                    reason=reason,
                )
            )

    for pattern_id, category, pattern, threshold, reason in REPETITION_THRESHOLDS:
        matches = re.findall(pattern, text, flags=re.MULTILINE)
        if len(matches) >= threshold:
            findings.append(
                HumanizeFinding(
                    pattern_id=pattern_id,
                    category=category,
                    severity="S2",
                    text=str(matches[0] if matches else pattern),
                    reason=reason,
                )
            )

    # Preserve order but remove duplicate pattern IDs to keep retry prompts concise.
    deduped: list[HumanizeFinding] = []
    seen: set[str] = set()
    for finding in findings:
        key = f"{finding.pattern_id}:{finding.text}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def summarize_findings(findings: list[HumanizeFinding], *, limit: int = 3) -> str:
    return "; ".join(
        f"{finding.pattern_id}/{finding.severity} '{finding.text}': {finding.reason}"
        for finding in findings[:limit]
    )
