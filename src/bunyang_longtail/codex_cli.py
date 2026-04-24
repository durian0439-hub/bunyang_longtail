from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import ensure_data_dir
from .gpt_web import build_text_prompt


class CodexCLIExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "CODEX_CLI_ERROR", artifact_dir: str | None = None):
        super().__init__(message)
        self.code = code
        self.artifact_dir = artifact_dir


KNOWN_CODEX_PATHS = (
    Path.home() / ".npm-global/bin/codex",
    Path("/home/kj/.npm-global/bin/codex"),
    Path.home() / ".local/bin/codex",
    Path("/usr/local/bin/codex"),
    Path("/usr/bin/codex"),
)

BANNED_STYLE_PATTERNS = (
    r"판단이 맞습니다",
    r"보는 게 맞습니다",
    r"이 전략이 맞습니다",
    r"청약 판단을 빨리 끝내려면",
    r"결론은 단순합니다",
    r"먼저 보셔야 합니다",
    r"먼저 확인해야 합니다",
    r"쉽게 말하면",
    r"작동 방식",
    r"판단 기준",
    r"적용 기준",
    r"유지 전략",
    r"가능 여부 기준",
    r"결과는 ",
    r"다음 행동은 ",
    r"무게가 실립니다",
    r"성격이 강해",
    r"쪽으로 갈 수 있습니다",
    r"조건:",
    r"결과:",
    r"다음 행동:",
    r"입장권에 가깝습니다",
    r"현실적입니다",
    r"현실에 가깝습니다",
    r"보는 편이 좋습니다",
    r"같이 봐야 합니다",
    r"먼저 비교해보는 편이 좋습니다",
    r"큰 역할을 합니다",
    r"유리하게 작용",
    r"자연스럽습니다",
    r"구조는 아닙니다",
    r"정리하면 조건부로 가능합니다",
    r"끝까지 설명할 수 있는지가",
    r"편이 더 유리합니다",
    r"일시적 2주택 관리 싸움",
    r"출구 규정",
    r"일정 규정",
    r"다음 선택지 규정",
    r"\.\.\.",
)

MAX_STYLE_REWRITE_ATTEMPTS = 2


def _artifact_dir(job_id: int, artifact_root: str | Path | None = None) -> Path:
    base = Path(artifact_root) if artifact_root else Path("/home/kj/app/bunyang_longtail/dev/data/codex_cli_artifacts")
    path = base / f"text_job_{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path



def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)



def _resolve_codex_executable() -> str:
    for env_name in ("CODEX_BIN", "CODEX_CLI_BIN"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if _is_executable(candidate):
            return str(candidate)

    resolved = shutil.which("codex")
    if resolved:
        return resolved

    for candidate in KNOWN_CODEX_PATHS:
        if _is_executable(candidate):
            return str(candidate)

    searched = [str(path) for path in KNOWN_CODEX_PATHS]
    raise CodexCLIExecutionError(
        "Codex CLI 실행 파일을 찾지 못했습니다. PATH 또는 CODEX_BIN을 확인하세요. "
        f"PATH={os.environ.get('PATH', '')} searched={searched}",
        code="CODEX_CLI_NOT_FOUND",
    )



def _validate_house_style(article_markdown: str) -> None:
    for pattern in BANNED_STYLE_PATTERNS:
        if re.search(pattern, article_markdown):
            raise CodexCLIExecutionError(
                f"금지된 상투 표현이 감지됐습니다: {pattern}",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )

    if article_markdown.count("안전합니다") > 1:
        raise CodexCLIExecutionError(
            "'안전합니다' 표현이 과도하게 반복됐습니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )

    if re.search(r"(^|\n)Q[^\n]*\n그렇습니다\.", article_markdown):
        raise CodexCLIExecutionError(
            "FAQ 답변이 '그렇습니다.'로 시작합니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )

    if re.search(r"\.{3,}", article_markdown):
        raise CodexCLIExecutionError(
            "말줄임표를 쓰지 않습니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )

    if re.search(r"(중요합니다\.|필요합니다\.|가능합니다\.|불리합니다\.)", article_markdown):
        short_lines = [line.strip() for line in article_markdown.splitlines() if line.strip()]
        for line in short_lines:
            if len(line) <= 18 and re.search(r"(중요합니다|필요합니다|가능합니다|불리합니다)\.$", line):
                raise CodexCLIExecutionError(
                    "의미가 생략된 짧은 단정형 문장이 있습니다.",
                    code="CODEX_CLI_STYLE_GUARD_FAILED",
                )

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", article_markdown) if part.strip()]
    plain_lines = [line.strip() for line in article_markdown.splitlines() if line.strip() and not line.strip().startswith('#')]
    if plain_lines:
        first_line = plain_lines[0]
        if re.match(r"^(청약|부동산|내 집 마련|주택 청약|판단|결론)", first_line) and ("입니다" in first_line or "해야" in first_line):
            raise CodexCLIExecutionError(
                "첫 문장이 일반론/설명문으로 시작합니다. 검색자 상황과 결론부터 바로 말해야 합니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if "보셔야 합니다" in first_line or "확인해야 합니다" in first_line:
            raise CodexCLIExecutionError(
                "첫 문장이 훈수형 안내체로 시작합니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if ("통장만 있고" in first_line or "청약통장만 있고" in first_line) and not any(token in first_line for token in ["불리", "손해", "밀리", "늦", "끊기", "잃"]):
            raise CodexCLIExecutionError(
                "첫 문장이 상황 설명만 있고 손해/결과가 바로 안 나옵니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )

    body_for_style = "\n".join(
        line.strip()
        for line in article_markdown.splitlines()
        if line.strip()
        and not line.strip().startswith('#')
        and line.strip() not in {"상단 요약", "이 글에서 바로 답하는 질문", "핵심 조건 정리", "헷갈리기 쉬운 예외", "실전 예시 시나리오", "체크리스트", "FAQ", "마무리 결론"}
    )
    abstract_terms = ("판단", "작동", "구조", "유지", "적용", "흐름", "가능 여부")
    abstract_noun_hits = []
    for term in abstract_terms:
        abstract_noun_hits.extend(re.findall(term, body_for_style))
    if len(abstract_noun_hits) >= 10:
        raise CodexCLIExecutionError(
            "추상 명사체가 과도하게 반복됩니다. 사람 말투로 다시 풀어 써야 합니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )

    repetitive_answer_tone_hits = re.findall(r"(먼저 .*봅니다|먼저 .*확인합니다|함께 .*봅니다|중요합니다\.|현실적입니다\.|필요합니다\.|좋습니다\.)", article_markdown)
    if len(repetitive_answer_tone_hits) >= 8:
        raise CodexCLIExecutionError(
            "답안형 설명 문장이 과도하게 반복됩니다. 블로그 말투로 다시 풀어 써야 합니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )
    body_paragraphs = [p for p in paragraphs if not p.startswith('#') and p not in {"상단 요약", "이 글에서 바로 답하는 질문"}]
    intro = " ".join(body_paragraphs[:2])
    if intro:
        if intro.count("당첨 직후") > 1:
            raise CodexCLIExecutionError(
                "도입부에서 같은 상황 표현이 반복됩니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if len(intro) > 260:
            raise CodexCLIExecutionError(
                "도입부가 너무 길고 장황합니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if "보셔야 합니다" in intro or "확인해야 합니다" in intro:
            raise CodexCLIExecutionError(
                "도입부가 조언체로만 흘러서 답이 늦습니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if intro.count('- ') >= 3:
            raise CodexCLIExecutionError(
                "도입부가 질문 불릿 나열 위주라서 블로그 초입 흐름이 약합니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )

    short_lines = [line.strip() for line in article_markdown.splitlines() if line.strip()]
    for line in short_lines:
        if line.startswith('#'):
            continue
        if len(line) <= 18 and re.search(r"(중요합니다|필요합니다|가능합니다|불리합니다)\.$", line):
            raise CodexCLIExecutionError(
                "의미가 생략된 짧은 단정형 문장이 있습니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )
        if re.search(r"(정리하면|결론만 말하면|쉽게 말해)\s*$", line):
            raise CodexCLIExecutionError(
                "문장을 열어놓고 핵심 설명을 생략하는 표현이 있습니다.",
                code="CODEX_CLI_STYLE_GUARD_FAILED",
            )



def _build_style_rewrite_prompt(*, article_markdown: str, failure_message: str, attempt_no: int) -> str:
    return "\n\n".join(
        [
            "아래 네이버 블로그용 한국어 마크다운 본문을 전체 다시 써 주세요.",
            "핵심 정보, 제목, 섹션 구조, 사례, FAQ 개수는 유지하고 말투만 교정해야 합니다.",
            f"이번 교정 사유: {failure_message}",
            "교정 규칙:",
            "- '판단이 맞습니다', '보는 게 맞습니다', '이 전략이 맞습니다', '청약 판단을 빨리 끝내려면', '결론은 단순합니다' 같은 상투 표현을 쓰지 않습니다.",
            "- '안전합니다' 종결은 최대 1회만 허용하고, 반복되면 다른 자연스러운 표현으로 바꿉니다.",
            "- FAQ 답변을 '그렇습니다.' 같은 한 단어 단정형으로 시작하지 않습니다.",
            "- 도입부 첫 2문단은 260자 안쪽으로 짧게 쓰고, 바로 상황과 결론을 말합니다.",
            "- 첫 문장은 반드시 검색자의 상황과 손해/불리/밀림/기회 상실 같은 결과를 함께 넣습니다. 예: '~라면 해지는 불리합니다', '~라면 순위가 밀릴 수 있습니다'.",
            "- 첫 문장은 일반 정의나 설명으로 열지 않습니다. '청약통장은', '통장만 있어도', '청약은'처럼 제도 설명으로 시작하지 않습니다.",
            "- 도입부 첫 2문단에서는 '보셔야 합니다', '확인해야 합니다' 같은 조언체 반복을 쓰지 않습니다.",
            "- 도입부 첫 2문단에서 질문 불릿을 3개 이상 나열하지 않습니다. 먼저 설명형 문단으로 흐름을 만듭니다.",
            "- 같은 상황 표현을 도입부에서 두 번 반복하지 않습니다. 예: '당첨 직후', '무주택 실수요자'.",
            "- '정리하면 조건부로 가능합니다', '끝까지 설명할 수 있는지가', '편이 더 유리합니다' 같은 메타 문장은 쓰지 않습니다.",
            "- '일시적 2주택 관리 싸움', '출구 규정', '일정 규정', '다음 선택지 규정'처럼 억지로 개념화한 표현을 쓰지 않습니다.",
            "- 말줄임표(...)를 쓰지 않습니다.",
            "- '중요합니다', '필요합니다', '가능합니다'처럼 끝내지 말고 왜 중요한지, 무엇이 필요한지, 어떤 조건에서 가능한지까지 문장 안에서 끝까지 설명합니다.",
            "- '결과는', '다음 행동은', '무게가 실립니다', '성격이 강해', '~쪽으로 갈 수 있습니다', '입장권에 가깝습니다', '현실적입니다', '현실에 가깝습니다', '보는 편이 좋습니다', '같이 봐야 합니다' 같은 답안지체 연결문은 쓰지 않습니다.",
            "- 실제 블로그 문장처럼 '막상 넣어보면', '여기서 많이 틀립니다', '당첨보다 그다음이 더 어렵습니다', '생각보다 여기서 막히는 경우가 많습니다', '정작 문제는', '많이들 여기서 틀어집니다' 같은 생활형 설명 문장을 우선합니다.",
            "- 마지막 행동 가이드는 '이 전략' 표현 대신 어떤 독자에게 더 적합한지 담백하게 씁니다.",
            "- 출력은 수정된 전체 마크다운 본문만 내보냅니다.",
            f"현재 교정 시도: {attempt_no}",
            article_markdown,
        ]
    )



def _run_codex_exec(
    *,
    codex_executable: str,
    workdir: str | Path,
    request_text: str,
    output_file: Path,
    stdout_file: Path,
    stderr_file: Path,
    timeout_seconds: int,
    artifact_dir: Path,
) -> str:
    cmd = [
        codex_executable,
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(workdir),
        "-o",
        str(output_file),
        request_text,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    except FileNotFoundError as exc:
        raise CodexCLIExecutionError(
            f"Codex CLI 실행 파일 호출 실패: {exc}",
            code="CODEX_CLI_NOT_FOUND",
            artifact_dir=str(artifact_dir),
        ) from exc
    stdout_file.write_text(proc.stdout or "", encoding="utf-8")
    stderr_file.write_text(proc.stderr or "", encoding="utf-8")
    if proc.returncode != 0:
        raise CodexCLIExecutionError(
            f"Codex CLI 텍스트 생성 실패: returncode={proc.returncode}, stderr={(proc.stderr or '')[:500]}",
            code="CODEX_CLI_EXEC_FAILED",
            artifact_dir=str(artifact_dir),
        )
    if not output_file.exists():
        raise CodexCLIExecutionError(
            "Codex CLI 출력 파일이 생성되지 않았습니다.",
            code="CODEX_CLI_OUTPUT_MISSING",
            artifact_dir=str(artifact_dir),
        )
    article_markdown = output_file.read_text(encoding="utf-8").strip()
    if not article_markdown:
        raise CodexCLIExecutionError(
            "Codex CLI 출력이 비어 있습니다.",
            code="CODEX_CLI_EMPTY_OUTPUT",
            artifact_dir=str(artifact_dir),
        )
    return article_markdown



def _extract_excerpt(article_markdown: str) -> str:
    lines = [line.strip() for line in article_markdown.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith('#'):
            continue
        if line in {"상단 요약", "이 글에서 바로 답하는 질문", "핵심 조건 정리", "헷갈리기 쉬운 예외", "실전 예시 시나리오", "체크리스트", "FAQ", "마무리 결론"}:
            continue
        if re.fullmatch(r'#+\s*.+', line):
            continue
        cleaned.append(line)
    excerpt_parts: list[str] = []
    for line in cleaned:
        excerpt_parts.append(line)
        joined = ' '.join(excerpt_parts)
        if len(joined) >= 140 or len(excerpt_parts) >= 3:
            break
    return ' '.join(excerpt_parts)[:220].strip()


def execute_text_job(
    *,
    job_id: int,
    prompt_payload: dict[str, Any] | str,
    timeout_seconds: int = 1800,
    artifact_root: str | Path | None = None,
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir(job_id, artifact_root)
    payload_dict = prompt_payload if isinstance(prompt_payload, dict) else json.loads(prompt_payload)
    prompt_text = build_text_prompt(payload_dict)
    prompt_file = artifact_dir / "request_prompt.txt"
    output_file = artifact_dir / "last_message.txt"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    codex_executable = _resolve_codex_executable()
    workdir_path = workdir or "/home/kj/app/bunyang_longtail/dev"
    article_markdown = _run_codex_exec(
        codex_executable=codex_executable,
        workdir=workdir_path,
        request_text="아래 지침대로 네이버 블로그용 한국어 마크다운 본문만 작성하세요. 불필요한 설명 없이 본문만 출력하세요.\n\n" + prompt_text,
        output_file=output_file,
        stdout_file=artifact_dir / "stdout.log",
        stderr_file=artifact_dir / "stderr.log",
        timeout_seconds=timeout_seconds,
        artifact_dir=artifact_dir,
    )

    style_guard_failures: list[str] = []
    style_rewrite_attempts = 0
    while True:
        try:
            _validate_house_style(article_markdown)
            break
        except CodexCLIExecutionError as exc:
            if exc.code != "CODEX_CLI_STYLE_GUARD_FAILED":
                raise
            style_guard_failures.append(str(exc))
            if style_rewrite_attempts >= MAX_STYLE_REWRITE_ATTEMPTS:
                raise CodexCLIExecutionError(
                    f"Codex CLI 스타일 교정 실패: {style_guard_failures[-1]}",
                    code=exc.code,
                    artifact_dir=str(artifact_dir),
                )
            style_rewrite_attempts += 1
            rewrite_prompt = _build_style_rewrite_prompt(
                article_markdown=article_markdown,
                failure_message=str(exc),
                attempt_no=style_rewrite_attempts,
            )
            article_markdown = _run_codex_exec(
                codex_executable=codex_executable,
                workdir=workdir_path,
                request_text=rewrite_prompt,
                output_file=artifact_dir / f"style_rewrite_{style_rewrite_attempts}.md",
                stdout_file=artifact_dir / f"style_rewrite_{style_rewrite_attempts}_stdout.log",
                stderr_file=artifact_dir / f"style_rewrite_{style_rewrite_attempts}_stderr.log",
                timeout_seconds=timeout_seconds,
                artifact_dir=artifact_dir,
            )
            output_file.write_text(article_markdown, encoding="utf-8")

    excerpt = _extract_excerpt(article_markdown) or article_markdown[:180]
    return {
        "article_markdown": article_markdown,
        "excerpt": excerpt,
        "response_payload": {
            "artifact_dir": str(artifact_dir),
            "executor": "codex_cli",
            "codex_executable": codex_executable,
            "response_preview": article_markdown[:500],
            "style_rewrite_attempts": style_rewrite_attempts,
            "style_guard_failures": style_guard_failures,
        },
    }
