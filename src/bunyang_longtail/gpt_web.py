from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from shutil import which
from typing import Any

from .config import GPT_PROFILE_DIR, GPT_WEB_ARTIFACT_DIR, ensure_data_dir

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - import 환경 차이 방어
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None

CHATGPT_URL = "https://chatgpt.com/"
COMPOSER_SELECTORS = [
    'textarea[placeholder*="Message"]',
    'textarea[placeholder*="메시지"]',
    'textarea[data-id="root"]',
    "textarea",
    '[contenteditable="true"][translate="no"]',
    '[contenteditable="true"]',
]
NEW_CHAT_SELECTORS = [
    'a:has-text("새 채팅")',
    'a:has-text("New chat")',
    'button[aria-label*="New chat"]',
    'button:has-text("New chat")',
    'button:has-text("새 채팅")',
]
SEND_BUTTON_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="전송"]',
]
STOP_BUTTON_SELECTORS = [
    'button[aria-label*="Stop"]',
    'button:has-text("Stop")',
    'button:has-text("중지")',
]
ASSISTANT_TURN_SELECTORS = [
    'article[data-testid*="conversation-turn-"]',
    '[data-message-author-role="assistant"]',
    'main article',
]
LOGIN_HINT_SELECTORS = [
    '[data-testid="login-button"]',
    'button:has-text("Log in")',
    'button:has-text("로그인")',
    'a:has-text("로그인")',
    'a[href*="auth/login"]',
    'button:has-text("Continue with Google")',
    'button:has-text("무료로 회원 가입")',
]
CHALLENGE_SELECTORS = [
    'iframe[title*="Cloudflare"]',
    'iframe[src*="challenge-platform"]',
    'text=/Verify you are human/i',
]
IMAGE_SELECTORS = [
    'img[src*="/backend-api/estuary/content"]',
    'img[src*="/backend-api/files/"]',
    'img[src^="blob:"]',
    'img[src^="data:image"]',
    'img[alt*="Generated"]',
    'img[alt*="generated"]',
]
GPT_WEB_ENV_CANDIDATES = [
    Path("/home/kj/app/bunyang_longtail/dev/.env"),
    Path("/home/kj/app/bunyang_longtail/dev/.env.local"),
    Path("/home/kj/app/bunyang/blog-cheongyak-automation/.env"),
    Path("/home/kj/app/bunyang/blog-cheongyak-automation/.env.local"),
]
GPT_WEB_GOOGLE_EMAIL_KEYS = [
    "GPT_WEB_GOOGLE_EMAIL",
    "CHATGPT_GOOGLE_EMAIL",
    "GOOGLE_LOGIN_EMAIL",
]
GPT_WEB_GOOGLE_PASSWORD_KEYS = [
    "GPT_WEB_GOOGLE_PASSWORD",
    "CHATGPT_GOOGLE_PASSWORD",
    "GOOGLE_LOGIN_PASSWORD",
]
GOOGLE_CONTINUE_SELECTORS = [
    'button:has-text("Google로 계속하기")',
    'button:has-text("Continue with Google")',
    'div[role="button"]:has-text("Google로 계속하기")',
    'div[role="button"]:has-text("Continue with Google")',
]
GOOGLE_EMAIL_INPUT_SELECTORS = [
    'input[type="email"]',
    'input[autocomplete="username"]',
    '#identifierId',
]
GOOGLE_PASSWORD_INPUT_SELECTORS = [
    'input[type="password"]',
    'input[name="Passwd"]',
]
GOOGLE_NEXT_SELECTORS = [
    '#identifierNext button',
    '#passwordNext button',
    'button:has-text("다음")',
    'button:has-text("Next")',
]
GOOGLE_APPROVAL_SELECTORS = [
    'button:has-text("계속")',
    'button:has-text("Continue")',
    'button:has-text("허용")',
    'button:has-text("Allow")',
]
COOKIE_ACCEPT_SELECTORS = [
    'button:has-text("모두 허용")',
    'button:has-text("Accept all")',
    'button:has-text("허용")',
    'button:has-text("Accept")',
]


class GptWebExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "GPT_WEB_ERROR", artifact_dir: str | None = None):
        super().__init__(message)
        self.code = code
        self.artifact_dir = artifact_dir



def _classify_launch_failure_message(message: str) -> tuple[str, str] | None:
    lowered = message.lower()
    if "missing x server" in lowered or "without having a xserver running" in lowered:
        return (
            "GPT_WEB_XSERVER_MISSING",
            "headed 브라우저를 띄울 X 서버가 없습니다. 로컬 GUI가 없으면 `xvfb-run -a python3 run.py probe-gpt-web --headed ...` 형태로 실행해 주세요.",
        )
    return None



def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("_") or "default"



def _profile_dir(profile_name: str, profile_root: str | Path | None = None) -> Path:
    base = Path(profile_root) if profile_root else GPT_PROFILE_DIR
    path = base / _safe_name(profile_name)
    path.mkdir(parents=True, exist_ok=True)
    return path



def _artifact_dir(kind: str, job_id: int, artifact_root: str | Path | None = None) -> Path:
    base = Path(artifact_root) if artifact_root else GPT_WEB_ARTIFACT_DIR
    path = base / f"{kind}_job_{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path



def _strip_env_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text



def _load_env_candidates(paths: list[Path] | None = None) -> None:
    protected = set(os.environ)
    for env_path in paths or GPT_WEB_ENV_CANDIDATES:
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



def _read_first_env_value(keys: list[str]) -> str:
    for key in keys:
        value = _strip_env_quotes(os.getenv(key, ""))
        if value:
            return value
    return ""



def _resolve_google_login_credentials() -> dict[str, str]:
    _load_env_candidates()
    return {
        "email": _read_first_env_value(GPT_WEB_GOOGLE_EMAIL_KEYS),
        "password": _read_first_env_value(GPT_WEB_GOOGLE_PASSWORD_KEYS),
    }



def _safe_page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""



def _safe_click(locator: Any, *, timeout: int = 3000) -> bool:
    try:
        locator.click(timeout=timeout)
        return True
    except Exception:
        pass
    try:
        locator.click(timeout=timeout, force=True)
        return True
    except Exception:
        pass
    try:
        locator.dispatch_event("click")
        return True
    except Exception:
        pass
    try:
        locator.evaluate("(el) => el.click()")
        return True
    except Exception:
        return False



def _fill_visible_input(page: Any, selectors: list[str], value: str) -> bool:
    if not value:
        return False
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=1000):
                continue
            try:
                locator.fill(value, timeout=3000)
            except Exception:
                locator.click(timeout=3000)
                try:
                    locator.press("Control+A")
                except Exception:
                    pass
                page.keyboard.insert_text(value)
            return True
        except Exception:
            continue
    return False



def _click_first_visible(page: Any, selectors: list[str]) -> bool:
    visible = _first_visible(page, selectors)
    if not visible:
        return False
    selector, locator = visible
    if (
        "login-button" in selector
        or "signup-button" in selector
        or "Continue with Google" in selector
        or "Google 계정으로 계속하기" in selector
        or "Google로 계속하기" in selector
    ):
        try:
            locator.evaluate("(el) => el.click()")
            return True
        except Exception:
            pass
    return _safe_click(locator)



def _click_challenge_checkbox(page: Any) -> bool:
    frames: list[Any] = []
    try:
        frames.extend(page.frames)
    except Exception:
        pass
    for frame in frames:
        try:
            checkbox = frame.get_by_role("checkbox", name=re.compile("verify you are human", re.I)).first
            if checkbox.is_visible(timeout=500) and _safe_click(checkbox):
                return True
        except Exception:
            continue
    return False



def _click_google_account_chooser(page: Any, email: str) -> bool:
    if not email:
        return False
    candidates = [email]
    if "@" in email:
        candidates.append(email.split("@", 1)[0])
    for candidate in candidates:
        try:
            locator = page.get_by_text(candidate, exact=False).first
            if locator.is_visible(timeout=1000) and _safe_click(locator):
                return True
        except Exception:
            continue
    return False



def _click_cookie_banner(page: Any) -> bool:
    return _click_first_visible(page, COOKIE_ACCEPT_SELECTORS)



def _submit_google_step(page: Any) -> bool:
    if _click_first_visible(page, GOOGLE_NEXT_SELECTORS):
        return True
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False



def _maybe_auto_login(page: Any, credentials: dict[str, str]) -> bool:
    action_taken = False
    if _click_cookie_banner(page):
        action_taken = True
        page.wait_for_timeout(1000)
    if _click_challenge_checkbox(page):
        action_taken = True
        page.wait_for_timeout(1500)

    current_url = _safe_page_url(page).lower()
    email = credentials.get("email", "")
    password = credentials.get("password", "")

    if "accounts.google.com" in current_url:
        if _click_google_account_chooser(page, email):
            page.wait_for_timeout(1500)
            return True
        if _fill_visible_input(page, GOOGLE_EMAIL_INPUT_SELECTORS, email):
            _submit_google_step(page)
            page.wait_for_timeout(1500)
            return True
        if _fill_visible_input(page, GOOGLE_PASSWORD_INPUT_SELECTORS, password):
            _submit_google_step(page)
            page.wait_for_timeout(1500)
            return True
        if _click_first_visible(page, GOOGLE_APPROVAL_SELECTORS):
            page.wait_for_timeout(1500)
            return True
        return action_taken

    if "auth.openai.com" in current_url:
        if _click_first_visible(page, GOOGLE_APPROVAL_SELECTORS):
            page.wait_for_timeout(1500)
            return True

    if _click_first_visible(page, LOGIN_HINT_SELECTORS):
        action_taken = True
        deadline = time.time() + 6
        while time.time() < deadline:
            page.wait_for_timeout(800)
            if _click_first_visible(page, GOOGLE_CONTINUE_SELECTORS):
                page.wait_for_timeout(1500)
                return True
        return action_taken
    if _click_first_visible(page, GOOGLE_CONTINUE_SELECTORS):
        page.wait_for_timeout(1500)
        return True
    return action_taken



def _first_visible(page: Any, selectors: list[str]) -> tuple[str, Any] | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(min(count, 5)):
            target = locator.nth(index)
            try:
                if target.is_visible():
                    return selector, target
            except Exception:
                continue
    return None



def _last_locator(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        if count > 0:
            return locator.nth(count - 1)
    return None



def _last_visible(page: Any, selectors: list[str]) -> tuple[str, Any] | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:
            continue
        for index in range(min(count, 10) - 1, -1, -1):
            target = locator.nth(index)
            try:
                if target.is_visible():
                    return selector, target
            except Exception:
                continue
    return None



def _count_locators(page: Any, selectors: list[str]) -> int:
    total = 0
    for selector in selectors:
        try:
            total += page.locator(selector).count()
        except Exception:
            continue
    return total



def _take_artifacts(page: Any, artifact_dir: Path, prefix: str) -> None:
    try:
        page.screenshot(path=str(artifact_dir / f"{prefix}.png"), full_page=True)
    except Exception:
        pass
    try:
        (artifact_dir / f"{prefix}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass



def _load_json(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    return json.loads(value)



def _looks_like_complete_article(text: str) -> bool:
    normalized = text.strip()
    if len(normalized) < 2500:
        return False
    strong_signals = ["FAQ", "체크리스트", "마무리 결론", "상단 요약"]
    matched = sum(1 for item in strong_signals if item in normalized)
    return matched >= 3



def _looks_like_generated_image_src(src: str) -> bool:
    normalized = (src or "").strip()
    return bool(
        normalized
        and (
            "/backend-api/estuary/content" in normalized
            or "/backend-api/files/" in normalized
            or normalized.startswith("blob:")
            or normalized.startswith("data:image")
        )
    )



def _has_new_generated_image(before: list[str], after: list[str]) -> bool:
    before_set = {item for item in before if item}
    return any(item and item not in before_set for item in after)



def _collect_generated_image_sources(page: Any) -> list[str]:
    try:
        images = page.locator("img").evaluate_all(
            """
            (nodes) => nodes.map((node) => ({
              src: node.getAttribute('src') || '',
              visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
            }))
            """
        )
    except Exception:
        return []
    collected: list[str] = []
    for item in images:
        if not item.get("visible"):
            continue
        src = item.get("src") or ""
        if _looks_like_generated_image_src(src):
            collected.append(src)
    return collected



def build_text_prompt(prompt_payload: dict[str, Any]) -> str:
    system_prompt = prompt_payload.get("system", "")
    user_prompt = prompt_payload.get("user", {})
    outline = user_prompt.get("outline", [])
    outline_lines = []
    for section in outline:
        heading = section.get("heading") or section.get("title") or "섹션"
        focus = section.get("focus") or section.get("body") or ""
        outline_lines.append(f"- {heading}: {focus}".strip())
    writing_rules = user_prompt.get("writing_rules", [])
    rule_lines = [f"- {item}" for item in writing_rules]
    required_sections = user_prompt.get("required_sections", [])
    required_lines = [f"- {item}" for item in required_sections]
    return "\n".join(
        [
            system_prompt,
            "",
            "아래 조건을 지켜 한국어 마크다운 본문만 작성해 주세요.",
            "코드블록, JSON, 설명 문구 없이 결과 본문만 출력하세요.",
            "작업 계획, 예고성 문장, 자기설명은 금지합니다. 완성된 본문만 즉시 출력하세요.",
            "'확인해볼게', '정리해줄게', '바로 글로 묶겠다' 같은 문장은 쓰지 마세요.",
            "청약처럼 기준이 바뀔 수 있는 주제라도 실시간 조회를 했다고 가정하지 말고, 일반적인 판단 프레임과 확인 포인트 중심으로 완성본을 작성하세요.",
            "최신 수치나 세부 요건이 달라질 수 있는 부분은 단정하지 말고, 입주자모집공고와 청약홈 확인 필요성을 자연스럽게 안내하세요.",
            f"제목: {user_prompt.get('title', '')}",
            f"핵심 키워드: {user_prompt.get('primary_keyword', '')}, {user_prompt.get('secondary_keyword', '')}",
            f"대상자: {user_prompt.get('audience', '')}",
            f"검색의도: {user_prompt.get('intent', '')}",
            f"상황: {user_prompt.get('scenario', '')}",
            f"서술 각도: {user_prompt.get('angle', '')}",
            f"각도 규칙: {user_prompt.get('angle_rule', '')}",
            "",
            "반드시 포함할 섹션:",
            *required_lines,
            "",
            "권장 아웃라인:",
            *outline_lines,
            "",
            "작성 규칙:",
            *rule_lines,
            "",
            "출력 형식:",
            "- 첫 줄은 H1 제목",
            "- 상단 요약 3문장",
            "- FAQ 6개 이상",
            "- 마지막은 어떤 독자에게 더 적합한지 행동 가이드 1문장",
        ]
    ).strip()



def build_image_prompt(
    *,
    prompt_text: str,
    title: str,
    excerpt: str | None,
    image_role: str,
) -> str:
    role_hint = {
        "thumbnail": "네이버 블로그 썸네일",
        "summary_card": "핵심 요약 카드",
        "section_visual": "본문 섹션 보조 시각자료",
        "faq_visual": "FAQ 보조 이미지",
    }.get(image_role, image_role)
    excerpt_text = excerpt or ""
    return "\n".join(
        [
            f"{role_hint} 이미지를 생성해 주세요.",
            f"제목: {title}",
            f"요약: {excerpt_text}",
            f"추가 지시: {prompt_text}",
            "출력 조건:",
            "- 한국 부동산/청약 블로그에 어울리는 깔끔한 스타일",
            "- 핵심 메시지가 한 장에서 바로 보이는 구도",
            "- 과한 텍스트 오버레이 지양",
            "- 1:1 비율 이미지를 우선 생성",
            "- 가능하면 답변 텍스트보다 이미지를 우선 제공",
        ]
    ).strip()



def _launch_context(
    *,
    profile_dir: Path,
    headed: bool,
    browser_channel: str | None,
    cdp_url: str | None = None,
) -> tuple[Any, Any, Any | None, bool]:
    if sync_playwright is None:
        raise GptWebExecutionError("playwright 패키지가 없어 GPT 웹 실행기를 사용할 수 없습니다.", code="PLAYWRIGHT_IMPORT_ERROR")

    playwright = sync_playwright().start()
    if cdp_url:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            contexts = browser.contexts
            if not contexts:
                playwright.stop()
                raise GptWebExecutionError(
                    "CDP로 연결한 브라우저에서 사용 가능한 context를 찾지 못했습니다.",
                    code="GPT_WEB_CDP_NO_CONTEXT",
                )
            return playwright, contexts[0], browser, False
        except Exception as exc:
            playwright.stop()
            raise GptWebExecutionError(
                f"CDP 브라우저 연결에 실패했습니다: {exc}",
                code="GPT_WEB_CDP_CONNECT_FAILED",
            )

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "headless": not headed,
        "viewport": {"width": 1440, "height": 1100},
        "accept_downloads": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--disable-dev-shm-usage",
        ],
    }
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    try:
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        return playwright, context, None, True
    except Exception as first_exc:
        launch_kwargs.pop("channel", None)
        chrome_path = which("google-chrome") or which("google-chrome-stable")
        if chrome_path:
            try:
                launch_kwargs["executable_path"] = chrome_path
                context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                return playwright, context, None, True
            except Exception as second_exc:
                classified = _classify_launch_failure_message(str(second_exc)) or _classify_launch_failure_message(str(first_exc))
                playwright.stop()
                if classified:
                    code, message = classified
                    raise GptWebExecutionError(message, code=code)
                raise
        classified = _classify_launch_failure_message(str(first_exc))
        playwright.stop()
        if classified:
            code, message = classified
            raise GptWebExecutionError(message, code=code)
        raise



def _prepare_page(
    context: Any,
    artifact_dir: Path,
    *,
    preserve_current_page: bool = False,
    open_new_page: bool = False,
) -> Any:
    if open_new_page:
        page = context.new_page()
    else:
        page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(15000)
    if preserve_current_page and not open_new_page:
        try:
            page.bring_to_front()
        except Exception:
            pass
        page.wait_for_timeout(1000)
    else:
        page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    _take_artifacts(page, artifact_dir, "loaded")
    return page



def _detect_page_state(page: Any) -> str:
    title = ""
    html = ""
    current_url = _safe_page_url(page).lower()
    try:
        title = page.title()
    except Exception:
        pass
    try:
        html = page.content().lower()
    except Exception:
        pass
    normalized_title = title.lower()
    has_visible_composer = _first_visible(page, COMPOSER_SELECTORS) is not None
    has_visible_login_hint = _first_visible(page, LOGIN_HINT_SELECTORS) is not None

    # ChatGPT 새 탭은 로그인된 상태여도 Cloudflare bootstrap 스크립트가 HTML에 남아
    # false positive 가 날 수 있다. 다만 게스트 홈처럼 로그인 버튼이 실제로 보이면
    # ready 가 아니라 login_required 로 판정해야 한다.
    if has_visible_composer and not has_visible_login_hint:
        return "ready"
    if (
        "just a moment" in normalized_title
        or "잠시만 기다리십시오" in title
        or "verify you are human" in html
        or "cf-turnstile-response" in html
        or "/cdn-cgi/challenge-platform/" in html
        or _count_locators(page, CHALLENGE_SELECTORS) > 0
    ):
        return "challenge"
    if "accounts.google.com" in current_url or "auth.openai.com" in current_url:
        return "login_required"
    if has_visible_login_hint:
        return "login_required"
    return "unknown"



def _wait_until_ready(page: Any, artifact_dir: Path, wait_for_ready_seconds: int) -> None:
    deadline = time.time() + wait_for_ready_seconds
    last_state = "unknown"
    credentials = _resolve_google_login_credentials()
    attempted_auto_login = False
    while time.time() < deadline:
        last_state = _detect_page_state(page)
        if last_state == "ready":
            return
        if last_state in {"challenge", "login_required", "unknown"}:
            try:
                acted = _maybe_auto_login(page, credentials)
            except Exception:
                acted = False
            if acted:
                attempted_auto_login = True
                page.wait_for_timeout(1500)
                continue
        page.wait_for_timeout(1500)
    _take_artifacts(page, artifact_dir, "not_ready")
    if last_state == "challenge":
        raise GptWebExecutionError(
            "Cloudflare 또는 브라우저 검증이 해결되지 않아 ChatGPT 준비 상태에 도달하지 못했습니다. --headed 로 probe-gpt-web을 먼저 실행해 로그인/검증을 통과시켜 주세요.",
            code="GPT_WEB_CHALLENGE",
            artifact_dir=str(artifact_dir),
        )
    if last_state == "login_required":
        current_url = _safe_page_url(page).lower()
        if attempted_auto_login and ("accounts.google.com" in current_url or "auth.openai.com" in current_url):
            raise GptWebExecutionError(
                "ChatGPT 구글 로그인 자동 입력까지 시도했지만 추가 비밀번호 입력, 2차 인증, 또는 보안 확인 단계가 남아 있습니다.",
                code="GPT_WEB_LOGIN_REQUIRED",
                artifact_dir=str(artifact_dir),
            )
        suffix = " .env에 GPT_WEB_GOOGLE_EMAIL, GPT_WEB_GOOGLE_PASSWORD를 넣으면 구글 로그인을 자동 입력할 수 있습니다." if not credentials.get("email") or not credentials.get("password") else ""
        raise GptWebExecutionError(
            f"ChatGPT 로그인 상태가 없어 실행할 수 없습니다. --headed 로 probe-gpt-web을 먼저 실행해 세션을 저장해 주세요.{suffix}",
            code="GPT_WEB_LOGIN_REQUIRED",
            artifact_dir=str(artifact_dir),
        )
    raise GptWebExecutionError(
        "ChatGPT 입력창을 찾지 못했습니다. 셀렉터 또는 UI 변경 가능성이 있습니다.",
        code="GPT_WEB_COMPOSER_NOT_FOUND",
        artifact_dir=str(artifact_dir),
    )



def _try_start_new_chat(page: Any) -> None:
    visible = _first_visible(page, NEW_CHAT_SELECTORS)
    if not visible:
        return
    _, locator = visible
    try:
        locator.click(timeout=3000)
        page.wait_for_timeout(1000)
    except Exception:
        pass



def _fill_prompt(page: Any, prompt_text: str) -> None:
    target = _first_visible(page, COMPOSER_SELECTORS)
    if not target:
        raise GptWebExecutionError("ChatGPT 입력창을 찾지 못했습니다.", code="GPT_WEB_COMPOSER_NOT_FOUND")
    selector, locator = target
    try:
        locator.click(timeout=3000)
    except Exception:
        pass

    if selector.startswith("textarea"):
        try:
            locator.fill(prompt_text)
        except Exception:
            locator.click()
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(prompt_text)
    else:
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt_text)



def _submit_prompt(page: Any) -> None:
    visible = _first_visible(page, SEND_BUTTON_SELECTORS)
    if visible:
        _, locator = visible
        try:
            locator.click(timeout=3000)
            return
        except Exception:
            pass
    page.keyboard.press("Enter")



def _has_stop_button(page: Any) -> bool:
    return _first_visible(page, STOP_BUTTON_SELECTORS) is not None



def _wait_for_text_response(
    page: Any,
    *,
    artifact_dir: Path,
    timeout_seconds: int,
    before_count: int = 0,
    before_text: str = "",
) -> str:
    deadline = time.time() + timeout_seconds
    last_text = ""
    while time.time() < deadline:
        locator = _last_locator(page, ASSISTANT_TURN_SELECTORS)
        current_count = _count_locators(page, ASSISTANT_TURN_SELECTORS)
        if locator is not None and current_count >= max(1, before_count):
            try:
                text = locator.inner_text(timeout=2000).strip()
            except Exception:
                text = ""
            if text:
                last_text = text
            response_changed = bool(text and (current_count > before_count or text != before_text))
            if response_changed and not _has_stop_button(page):
                page.wait_for_timeout(2000)
                try:
                    stable_text = locator.inner_text(timeout=2000).strip()
                except Exception:
                    stable_text = text
                stable_changed = bool(stable_text and (current_count > before_count or stable_text != before_text))
                if stable_changed and stable_text == text and not _has_stop_button(page):
                    _take_artifacts(page, artifact_dir, "text_response")
                    return stable_text
        page.wait_for_timeout(1500)
    _take_artifacts(page, artifact_dir, "text_timeout")
    if _looks_like_complete_article(last_text):
        return last_text
    raise GptWebExecutionError(
        f"ChatGPT 텍스트 응답 대기 시간이 초과됐습니다. 마지막 응답: {last_text[:200]}",
        code="GPT_WEB_TEXT_TIMEOUT",
        artifact_dir=str(artifact_dir),
    )



def _save_generated_image(page: Any, image_locator: Any, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = image_locator.get_attribute('src') or ''
    except Exception:
        src = ''

    if src.startswith('data:image'):
        _, encoded = src.split(',', 1)
        output_path.write_bytes(base64.b64decode(encoded))
        return

    if src.startswith('blob:'):
        payload = page.evaluate(
            """async (src) => {
                const res = await fetch(src);
                const blob = await res.blob();
                const buffer = await blob.arrayBuffer();
                const bytes = Array.from(new Uint8Array(buffer));
                return { bytes };
            }""",
            src,
        )
        if payload and payload.get('bytes'):
            output_path.write_bytes(bytes(payload['bytes']))
            return
        raise RuntimeError(f'blob 원본 다운로드 실패: {src}')

    if src.startswith('http://') or src.startswith('https://') or '/backend-api/' in src:
        try:
            payload = page.evaluate(
                """async (src) => {
                    const res = await fetch(src, { credentials: 'include' });
                    if (!res.ok) {
                        throw new Error(`fetch failed: ${res.status}`);
                    }
                    const buffer = await res.arrayBuffer();
                    const bytes = Array.from(new Uint8Array(buffer));
                    return { bytes };
                }""",
                src,
            )
            if payload and payload.get('bytes'):
                output_path.write_bytes(bytes(payload['bytes']))
                return
        except Exception as exc:
            raise RuntimeError(f'브라우저 세션 다운로드 실패: {src}, {exc}') from exc

    raise RuntimeError(f'다운로드 가능한 원본 이미지 src를 찾지 못했습니다: {src}')



def _wait_for_image_response(
    page: Any,
    *,
    artifact_dir: Path,
    output_path: Path,
    timeout_seconds: int,
    before_count: int = 0,
    before_text: str = "",
    before_image_sources: list[str] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    seen_before = before_image_sources or []
    while time.time() < deadline:
        locator = _last_locator(page, ASSISTANT_TURN_SELECTORS)
        current_count = _count_locators(page, ASSISTANT_TURN_SELECTORS)
        reply_text = ""
        response_changed = current_count > before_count
        if locator is not None and current_count >= max(1, before_count):
            try:
                reply_text = locator.inner_text(timeout=2000).strip()
            except Exception:
                reply_text = ""
            response_changed = current_count > before_count or (reply_text and reply_text != before_text)
        current_image_sources = _collect_generated_image_sources(page)
        has_new_image = _has_new_generated_image(seen_before, current_image_sources)
        visible_image = _last_visible(page, IMAGE_SELECTORS)
        image_locator = visible_image[1] if visible_image else None
        if image_locator is not None and (has_new_image or response_changed) and not _has_stop_button(page):
            try:
                _save_generated_image(page, image_locator, output_path)
            except Exception as exc:
                raise GptWebExecutionError(
                    f"생성된 이미지를 저장하지 못했습니다: {exc}",
                    code="GPT_WEB_IMAGE_SAVE_FAILED",
                    artifact_dir=str(artifact_dir),
                ) from exc
            _take_artifacts(page, artifact_dir, "image_response")
            return {
                "file_path": str(output_path),
                "reply_text": reply_text,
            }
        page.wait_for_timeout(2000)

    current_image_sources = _collect_generated_image_sources(page)
    visible_image = _last_visible(page, IMAGE_SELECTORS)
    image_locator = visible_image[1] if visible_image else None
    if image_locator is not None and _has_new_generated_image(seen_before, current_image_sources) and not _has_stop_button(page):
        try:
            _save_generated_image(page, image_locator, output_path)
        except Exception as exc:
            raise GptWebExecutionError(
                f"생성된 이미지를 저장하지 못했습니다: {exc}",
                code="GPT_WEB_IMAGE_SAVE_FAILED",
                artifact_dir=str(artifact_dir),
            ) from exc
        _take_artifacts(page, artifact_dir, "image_response")
        return {
            "file_path": str(output_path),
            "reply_text": "",
        }

    _take_artifacts(page, artifact_dir, "image_timeout")
    raise GptWebExecutionError(
        "ChatGPT 이미지 응답을 찾지 못했습니다. 로그인 상태 또는 모델/도구 가용성을 확인해 주세요.",
        code="GPT_WEB_IMAGE_TIMEOUT",
        artifact_dir=str(artifact_dir),
    )



def probe_gpt_web(
    *,
    profile_name: str,
    headed: bool = False,
    wait_for_ready_seconds: int = 180,
    browser_channel: str | None = "chrome",
    profile_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("probe", int(time.time()), artifact_root)
    profile_dir = _profile_dir(profile_name, profile_root)
    playwright = None
    context = None
    managed_context = True
    try:
        playwright, context, _browser, managed_context = _launch_context(
            profile_dir=profile_dir,
            headed=headed,
            browser_channel=browser_channel,
            cdp_url=cdp_url,
        )
        page = _prepare_page(context, artifact_dir, preserve_current_page=bool(cdp_url))
        _wait_until_ready(page, artifact_dir, wait_for_ready_seconds)
        return {
            "ready": True,
            "profile_name": profile_name,
            "profile_dir": str(profile_dir),
            "artifact_dir": str(artifact_dir),
            "url": page.url,
            "cdp_url": cdp_url,
        }
    finally:
        if context is not None and managed_context:
            context.close()
        if playwright is not None:
            playwright.stop()



def execute_text_job(
    *,
    job_id: int,
    profile_name: str,
    prompt_payload: dict[str, Any] | str,
    browser_channel: str | None = "chrome",
    headed: bool = False,
    wait_for_ready_seconds: int = 180,
    response_timeout_seconds: int = 240,
    profile_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("text", job_id, artifact_root)
    profile_dir = _profile_dir(profile_name, profile_root)
    playwright = None
    context = None
    page = None
    managed_context = True
    close_page_when_done = bool(cdp_url)
    try:
        playwright, context, _browser, managed_context = _launch_context(
            profile_dir=profile_dir,
            headed=headed,
            browser_channel=browser_channel,
            cdp_url=cdp_url,
        )
        page = _prepare_page(
            context,
            artifact_dir,
            preserve_current_page=bool(cdp_url),
            open_new_page=bool(cdp_url),
        )
        _wait_until_ready(page, artifact_dir, wait_for_ready_seconds)
        _try_start_new_chat(page)
        before_count = _count_locators(page, ASSISTANT_TURN_SELECTORS)
        before_locator = _last_locator(page, ASSISTANT_TURN_SELECTORS)
        before_text = ""
        if before_locator is not None:
            try:
                before_text = before_locator.inner_text(timeout=1000).strip()
            except Exception:
                before_text = ""
        prompt_text = build_text_prompt(_load_json(prompt_payload))
        (artifact_dir / "request_prompt.txt").write_text(prompt_text, encoding="utf-8")
        _fill_prompt(page, prompt_text)
        _submit_prompt(page)
        _take_artifacts(page, artifact_dir, "submitted")
        article_markdown = _wait_for_text_response(
            page,
            artifact_dir=artifact_dir,
            timeout_seconds=response_timeout_seconds,
            before_count=before_count,
            before_text=before_text,
        )
        excerpt = article_markdown.strip().splitlines()[1][:180] if len(article_markdown.strip().splitlines()) > 1 else article_markdown[:180]
        response_payload = {
            "artifact_dir": str(artifact_dir),
            "profile_name": profile_name,
            "response_preview": article_markdown[:500],
            "cdp_url": cdp_url,
        }
        (artifact_dir / "response.md").write_text(article_markdown, encoding="utf-8")
        return {
            "article_markdown": article_markdown,
            "excerpt": excerpt,
            "response_payload": response_payload,
        }
    except PlaywrightTimeoutError as exc:
        raise GptWebExecutionError(
            f"Playwright 대기 시간이 초과됐습니다: {exc}",
            code="GPT_WEB_TIMEOUT",
            artifact_dir=str(artifact_dir),
        )
    finally:
        if page is not None and close_page_when_done:
            try:
                page.close()
            except Exception:
                pass
        if context is not None and managed_context:
            context.close()
        if playwright is not None:
            playwright.stop()



def execute_image_job(
    *,
    job_id: int,
    profile_name: str,
    prompt_text: str,
    title: str,
    excerpt: str | None,
    image_role: str,
    output_path: str | Path,
    browser_channel: str | None = "chrome",
    headed: bool = False,
    wait_for_ready_seconds: int = 180,
    response_timeout_seconds: int = 300,
    profile_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    ensure_data_dir()
    artifact_dir = _artifact_dir("image", job_id, artifact_root)
    profile_dir = _profile_dir(profile_name, profile_root)
    playwright = None
    context = None
    page = None
    managed_context = True
    close_page_when_done = bool(cdp_url)
    try:
        playwright, context, _browser, managed_context = _launch_context(
            profile_dir=profile_dir,
            headed=headed,
            browser_channel=browser_channel,
            cdp_url=cdp_url,
        )
        page = _prepare_page(
            context,
            artifact_dir,
            preserve_current_page=bool(cdp_url),
            open_new_page=bool(cdp_url),
        )
        _wait_until_ready(page, artifact_dir, wait_for_ready_seconds)
        _try_start_new_chat(page)
        before_count = _count_locators(page, ASSISTANT_TURN_SELECTORS)
        before_locator = _last_locator(page, ASSISTANT_TURN_SELECTORS)
        before_text = ""
        if before_locator is not None:
            try:
                before_text = before_locator.inner_text(timeout=1000).strip()
            except Exception:
                before_text = ""
        before_image_sources = _collect_generated_image_sources(page)
        final_prompt = build_image_prompt(
            prompt_text=prompt_text,
            title=title,
            excerpt=excerpt,
            image_role=image_role,
        )
        (artifact_dir / "request_prompt.txt").write_text(final_prompt, encoding="utf-8")
        _fill_prompt(page, final_prompt)
        _submit_prompt(page)
        _take_artifacts(page, artifact_dir, "submitted")
        image_result = _wait_for_image_response(
            page,
            artifact_dir=artifact_dir,
            output_path=Path(output_path),
            timeout_seconds=response_timeout_seconds,
            before_count=before_count,
            before_text=before_text,
            before_image_sources=before_image_sources,
        )
        image_result["response_payload"] = {
            "artifact_dir": str(artifact_dir),
            "profile_name": profile_name,
            "reply_preview": image_result.get("reply_text", "")[:500],
            "cdp_url": cdp_url,
        }
        return image_result
    except PlaywrightTimeoutError as exc:
        raise GptWebExecutionError(
            f"Playwright 대기 시간이 초과됐습니다: {exc}",
            code="GPT_WEB_TIMEOUT",
            artifact_dir=str(artifact_dir),
        )
    finally:
        if page is not None and close_page_when_done:
            try:
                page.close()
            except Exception:
                pass
        if context is not None and managed_context:
            context.close()
        if playwright is not None:
            playwright.stop()
