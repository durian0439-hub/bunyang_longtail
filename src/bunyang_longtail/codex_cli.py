from __future__ import annotations

import json
import re
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



def _artifact_dir(job_id: int, artifact_root: str | Path | None = None) -> Path:
    base = Path(artifact_root) if artifact_root else Path("/home/kj/app/bunyang_longtail/dev/data/codex_cli_artifacts")
    path = base / f"text_job_{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path



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

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(workdir or "/home/kj/app/bunyang_longtail/dev"),
        "-o",
        str(output_file),
        "아래 지침대로 네이버 블로그용 한국어 마크다운 본문만 작성하세요. 불필요한 설명 없이 본문만 출력하세요.\n\n" + prompt_text,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    (artifact_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (artifact_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
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
    excerpt = _extract_excerpt(article_markdown) or article_markdown[:180]
    return {
        "article_markdown": article_markdown,
        "excerpt": excerpt,
        "response_payload": {
            "artifact_dir": str(artifact_dir),
            "executor": "codex_cli",
            "response_preview": article_markdown[:500],
        },
    }
