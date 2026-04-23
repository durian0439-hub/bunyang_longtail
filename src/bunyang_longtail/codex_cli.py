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

    if article_markdown.count("안전합니다") > 2:
        raise CodexCLIExecutionError(
            "'안전합니다' 표현이 과도하게 반복됐습니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )

    if re.search(r"(^|\n)Q[^\n]*\n그렇습니다\.", article_markdown):
        raise CodexCLIExecutionError(
            "FAQ 답변이 '그렇습니다.'로 시작합니다.",
            code="CODEX_CLI_STYLE_GUARD_FAILED",
        )



def _build_style_rewrite_prompt(*, article_markdown: str, failure_message: str, attempt_no: int) -> str:
    return "\n\n".join(
        [
            "아래 네이버 블로그용 한국어 마크다운 본문을 전체 다시 써 주세요.",
            "핵심 정보, 제목, 섹션 구조, 사례, FAQ 개수는 유지하고 말투만 교정해야 합니다.",
            f"이번 교정 사유: {failure_message}",
            "교정 규칙:",
            "- '판단이 맞습니다', '보는 게 맞습니다', '이 전략이 맞습니다' 같은 상담체 상투 표현을 쓰지 않습니다.",
            "- '안전합니다' 종결은 최대 1회만 허용하고, 반복되면 다른 자연스러운 표현으로 바꿉니다.",
            "- FAQ 답변을 '그렇습니다.' 같은 한 단어 단정형으로 시작하지 않습니다.",
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
