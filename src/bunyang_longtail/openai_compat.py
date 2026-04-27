from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import (
    OPENAI_COMPAT_ARTIFACT_DIR,
    OPENAI_COMPAT_BASE_URL,
    OPENAI_COMPAT_IMAGE_MODEL,
    OPENAI_COMPAT_TEXT_MODEL,
    ensure_data_dir,
)
from .gpt_web import build_image_prompt, build_text_prompt


class OpenAICompatExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "OPENAI_COMPAT_ERROR", artifact_dir: str | None = None):
        super().__init__(message)
        self.code = code
        self.artifact_dir = artifact_dir



def _artifact_dir(kind: str, job_id: int, artifact_root: str | Path | None = None) -> Path:
    base = Path(artifact_root) if artifact_root else OPENAI_COMPAT_ARTIFACT_DIR
    path = base / f"{kind}_job_{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path



def _resolve_api_key(api_key: str | None = None) -> str:
    value = api_key or os.getenv("OPENAI_COMPAT_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not value:
        raise OpenAICompatExecutionError(
            "OpenAI 호환 API 키가 없습니다. OPENAI_COMPAT_API_KEY 또는 OPENAI_API_KEY를 설정해 주세요.",
            code="OPENAI_COMPAT_API_KEY_MISSING",
        )
    return value



def _resolve_base_url(base_url: str | None = None) -> str:
    return (base_url or OPENAI_COMPAT_BASE_URL).rstrip("/")



def _json_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }



def _post_json(
    *,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: int,
    artifact_dir: Path,
    request_name: str,
) -> dict[str, Any]:
    request_path = artifact_dir / f"{request_name}.request.json"
    response_path = artifact_dir / f"{request_name}.response.json"
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_json_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        response_path.write_text(body, encoding="utf-8")
        raise OpenAICompatExecutionError(
            f"OpenAI 호환 API 호출이 실패했습니다. status={exc.code}, body={body[:500]}",
            code="OPENAI_COMPAT_HTTP_ERROR",
            artifact_dir=str(artifact_dir),
        )
    except urllib.error.URLError as exc:
        raise OpenAICompatExecutionError(
            f"OpenAI 호환 API 연결에 실패했습니다: {exc}",
            code="OPENAI_COMPAT_NETWORK_ERROR",
            artifact_dir=str(artifact_dir),
        )
    response_path.write_text(raw, encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAICompatExecutionError(
            f"OpenAI 호환 API 응답 JSON 파싱에 실패했습니다: {exc}",
            code="OPENAI_COMPAT_INVALID_JSON",
            artifact_dir=str(artifact_dir),
        )



def _extract_text_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise OpenAICompatExecutionError("텍스트 응답 choices가 비어 있습니다.", code="OPENAI_COMPAT_EMPTY_TEXT_RESPONSE")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                chunks.append(str(item["text"]))
        text = "\n".join(chunks).strip()
        if text:
            return text
    raise OpenAICompatExecutionError("텍스트 응답 본문을 찾지 못했습니다.", code="OPENAI_COMPAT_EMPTY_TEXT_RESPONSE")



def _download_binary(url: str, output_path: Path, timeout_seconds: int) -> None:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        output_path.write_bytes(response.read())



def probe_openai_compat(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: int = 30,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("probe", 0, artifact_root)
    resolved_api_key = _resolve_api_key(api_key)
    resolved_base_url = _resolve_base_url(base_url)
    url = f"{resolved_base_url}/models"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {resolved_api_key}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        (artifact_dir / "probe.response.json").write_text(body, encoding="utf-8")
        raise OpenAICompatExecutionError(
            f"OpenAI 호환 모델 목록 조회에 실패했습니다. status={exc.code}, body={body[:500]}",
            code="OPENAI_COMPAT_HTTP_ERROR",
            artifact_dir=str(artifact_dir),
        )
    except urllib.error.URLError as exc:
        raise OpenAICompatExecutionError(
            f"OpenAI 호환 API 연결에 실패했습니다: {exc}",
            code="OPENAI_COMPAT_NETWORK_ERROR",
            artifact_dir=str(artifact_dir),
        )
    (artifact_dir / "probe.response.json").write_text(raw, encoding="utf-8")
    data = json.loads(raw)
    models = [item.get("id") for item in data.get("data", []) if item.get("id")]
    return {
        "ready": True,
        "base_url": resolved_base_url,
        "models": models[:20],
        "artifact_dir": str(artifact_dir),
    }



def execute_text_job(
    *,
    job_id: int,
    prompt_payload: dict[str, Any] | str,
    model_label: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: int = 120,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("text", job_id, artifact_root)
    resolved_api_key = _resolve_api_key(api_key)
    resolved_base_url = _resolve_base_url(base_url)
    resolved_model = model_label or OPENAI_COMPAT_TEXT_MODEL
    payload_dict = prompt_payload if isinstance(prompt_payload, dict) else json.loads(prompt_payload)
    prompt_text = build_text_prompt(payload_dict)
    (artifact_dir / "request_prompt.txt").write_text(prompt_text, encoding="utf-8")
    response = _post_json(
        url=f"{resolved_base_url}/chat/completions",
        payload={
            "model": resolved_model,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": 0.7,
        },
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        artifact_dir=artifact_dir,
        request_name="text",
    )
    article_markdown = _extract_text_content(response)
    lines = [line.strip() for line in article_markdown.splitlines() if line.strip()]
    excerpt = lines[1][:180] if len(lines) > 1 else article_markdown[:180]
    (artifact_dir / "response.md").write_text(article_markdown, encoding="utf-8")
    return {
        "article_markdown": article_markdown,
        "excerpt": excerpt,
        "response_payload": {
            "artifact_dir": str(artifact_dir),
            "base_url": resolved_base_url,
            "model_label": resolved_model,
            "response_preview": article_markdown[:500],
        },
    }



def execute_image_job(
    *,
    job_id: int,
    prompt_text: str,
    title: str,
    excerpt: str | None,
    image_role: str,
    output_path: str | Path,
    model_label: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: int = 180,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("image", job_id, artifact_root)
    resolved_api_key = _resolve_api_key(api_key)
    resolved_base_url = _resolve_base_url(base_url)
    resolved_model = model_label or OPENAI_COMPAT_IMAGE_MODEL
    final_prompt = build_image_prompt(
        prompt_text=prompt_text,
        title=title,
        excerpt=excerpt,
        image_role=image_role,
    )
    (artifact_dir / "request_prompt.txt").write_text(final_prompt, encoding="utf-8")
    payload: dict[str, Any] = {
        "model": resolved_model,
        "prompt": final_prompt,
        "size": "1024x1024",
    }
    image_quality = str(os.getenv("OPENAI_COMPAT_IMAGE_QUALITY") or os.getenv("LONGTAIL_OPENAI_IMAGE_QUALITY") or "").strip()
    if image_quality:
        payload["quality"] = image_quality

    response = _post_json(
        url=f"{resolved_base_url}/images/generations",
        payload=payload,
        api_key=resolved_api_key,
        timeout_seconds=timeout_seconds,
        artifact_dir=artifact_dir,
        request_name="image",
    )
    data = response.get("data") or []
    if not data:
        raise OpenAICompatExecutionError("이미지 응답 data가 비어 있습니다.", code="OPENAI_COMPAT_EMPTY_IMAGE_RESPONSE", artifact_dir=str(artifact_dir))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    item = data[0]
    if item.get("b64_json"):
        output.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        _download_binary(str(item["url"]), output, timeout_seconds)
    else:
        raise OpenAICompatExecutionError(
            "이미지 응답에서 b64_json 또는 url을 찾지 못했습니다.",
            code="OPENAI_COMPAT_EMPTY_IMAGE_RESPONSE",
            artifact_dir=str(artifact_dir),
        )
    if not output.exists() or output.stat().st_size <= 0:
        raise OpenAICompatExecutionError(
            f"이미지 파일 저장에 실패했거나 파일이 비어 있습니다: {output}",
            code="OPENAI_COMPAT_IMAGE_FILE_MISSING",
            artifact_dir=str(artifact_dir),
        )
    return {
        "file_path": str(output),
        "response_payload": {
            "artifact_dir": str(artifact_dir),
            "base_url": resolved_base_url,
            "model_label": resolved_model,
            "reply_preview": str(item)[:500],
        },
    }
