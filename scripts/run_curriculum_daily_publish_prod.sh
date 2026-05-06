#!/usr/bin/env bash
set -euo pipefail

export PATH="/home/kj/.npm-global/bin:/home/kj/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export LONGTAIL_NAVER_CATEGORY_NO="${LONGTAIL_NAVER_CATEGORY_NO:-16}"
export LONGTAIL_NAVER_CATEGORY_NAME="${LONGTAIL_NAVER_CATEGORY_NAME:-How To 분양}"
export LONGTAIL_AUCTION_NAVER_CATEGORY_NO="${LONGTAIL_AUCTION_NAVER_CATEGORY_NO:-17}"
export LONGTAIL_AUCTION_NAVER_CATEGORY_NAME="${LONGTAIL_AUCTION_NAVER_CATEGORY_NAME:-How To 경매}"
export LONGTAIL_TAX_NAVER_CATEGORY_NO="${LONGTAIL_TAX_NAVER_CATEGORY_NO:-18}"
export LONGTAIL_TAX_NAVER_CATEGORY_NAME="${LONGTAIL_TAX_NAVER_CATEGORY_NAME:-How To 세금}"
export LONGTAIL_LOAN_NAVER_CATEGORY_NO="${LONGTAIL_LOAN_NAVER_CATEGORY_NO:-19}"
export LONGTAIL_LOAN_NAVER_CATEGORY_NAME="${LONGTAIL_LOAN_NAVER_CATEGORY_NAME:-부동산 대출}"
export LONGTAIL_GPT_IMAGE_SPEED="${LONGTAIL_GPT_IMAGE_SPEED:-fast}"
export NAVER_BLOG_GPT_IMAGE_MAX_ASSETS="${NAVER_BLOG_GPT_IMAGE_MAX_ASSETS:-11}"
export NAVER_BLOG_GPT_IMAGE_TIMEOUT_SEC="${NAVER_BLOG_GPT_IMAGE_TIMEOUT_SEC:-480}"
export LONGTAIL_PUBLISH_MODE="${LONGTAIL_PUBLISH_MODE:-publish}"
export LONGTAIL_VIDEO_UPLOAD="${LONGTAIL_VIDEO_UPLOAD:-1}"
export LONGTAIL_BLOG_INLINE_VIDEO="${LONGTAIL_BLOG_INLINE_VIDEO:-1}"
export LONGTAIL_VIDEO_TTS_ENABLED="${LONGTAIL_VIDEO_TTS_ENABLED:-1}"
export LONGTAIL_VIDEO_TTS_STRICT="${LONGTAIL_VIDEO_TTS_STRICT:-1}"
export LONGTAIL_NAVER_CLIP_UPLOAD="${LONGTAIL_NAVER_CLIP_UPLOAD:-1}"
export LONGTAIL_NAVER_CLIP_VISIBILITY="${LONGTAIL_NAVER_CLIP_VISIBILITY:-public}"
export LONGTAIL_NAVER_CLIP_THUMBNAIL_FREEZE_SEC="${LONGTAIL_NAVER_CLIP_THUMBNAIL_FREEZE_SEC:-3}"
# A-Z curriculum 발행은 네이버 본문/클립을 우선하며, YouTube 토큰 만료가 블로그 발행을 막지 않도록 기본 skip 처리한다.
export LONGTAIL_YOUTUBE_UPLOAD="${LONGTAIL_YOUTUBE_UPLOAD:-0}"
export LONGTAIL_YOUTUBE_PRIVACY_STATUS="${LONGTAIL_YOUTUBE_PRIVACY_STATUS:-${LONGTAIL_YOUTUBE_PRIVACY:-public}}"
export LONGTAIL_TIKTOK_UPLOAD="${LONGTAIL_TIKTOK_UPLOAD:-0}"
export LONGTAIL_TIKTOK_PRIVACY_LEVEL="${LONGTAIL_TIKTOK_PRIVACY_LEVEL:-SELF_ONLY}"
export LONGTAIL_VIDEO_MAKER_ROOT="${LONGTAIL_VIDEO_MAKER_ROOT:-/home/kj/app/video_maker}"
export LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR="${LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR:-/home/kj/app/bunyang/blog-cheongyak-automation/outputs/publish_longtail}"

PROD_ROOT="${LONGTAIL_PROD_ROOT:-/home/kj/app/bunyang_longtail/prod}"
CODE_ROOT="${LONGTAIL_PROD_CODE_ROOT:-$PROD_ROOT/runtime/current}"
DATA_DIR="${BUNYANG_LONGTAIL_DATA_DIR:-$CODE_ROOT/data}"
DB_PATH="${LONGTAIL_PROD_DB_PATH:-$DATA_DIR/cdp_probe5.sqlite3}"
OUTPUT_BASE="${LONGTAIL_NAVER_OUTPUT_BASE:-$DATA_DIR/naver_publish/curriculum_daily}"
LOG_DIR="$PROD_ROOT/logs"
RUN_DIR="$PROD_ROOT/run"
LOCK_FILE="$RUN_DIR/longtail_publish.lock"
LOG_FILE="$LOG_DIR/curriculum_daily_publish.log"

export BUNYANG_LONGTAIL_ROOT="$CODE_ROOT"
export BUNYANG_LONGTAIL_DATA_DIR="$DATA_DIR"
export LONGTAIL_PROD_DB_PATH="$DB_PATH"
export LONGTAIL_NAVER_OUTPUT_BASE="$OUTPUT_BASE"

mkdir -p "$LOG_DIR" "$RUN_DIR" "$OUTPUT_BASE"

if [[ "${LONGTAIL_LOCK_ALREADY_HELD:-0}" != "1" ]]; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[$(date '+%F %T')] skip: previous longtail publish job still running" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  echo "[$(date '+%F %T')] start: curriculum daily publish prod"
  echo "prod code root: $CODE_ROOT"
  echo "prod data dir: $DATA_DIR"

  if [[ ! -d "$CODE_ROOT/.git" ]]; then
    echo "error: production code checkout is missing: $CODE_ROOT"
    exit 1
  fi

  cd "$CODE_ROOT"

  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" != "main" ]]; then
    echo "error: curriculum daily cron must run on prod main branch, current=$current_branch"
    exit 1
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: curriculum daily cron requires a clean production working tree"
    git status --short
    exit 1
  fi

  if [[ ! -f "$DB_PATH" ]]; then
    echo "error: production DB is missing: $DB_PATH"
    exit 1
  fi

  /usr/bin/python3 -m unittest tests.test_longtail tests.test_naver_bundle_publish

  /usr/bin/python3 - <<'PY'
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, 'src')
from bunyang_longtail.cron_publish import describe_unpublishable_run_result, run_bundle_target_from_candidate, select_curriculum_publish_candidate
from bunyang_longtail.curriculum import CURRICULUM_TRACK_KEY
from bunyang_longtail.curriculum_hub_publish import publish_curriculum_hub_to_naver
from bunyang_longtail.database import connect
from bunyang_longtail.workers import run_bundle

DB_PATH = os.environ['LONGTAIL_PROD_DB_PATH']
OUTPUT_BASE = Path(os.environ['LONGTAIL_NAVER_OUTPUT_BASE'])
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
MAX_VARIANT_ATTEMPTS = 3
PUBLISH_RETRY_MAX = max(1, int(os.environ.get('LONGTAIL_PUBLISH_RETRY_MAX', '6') or '6'))
PUBLISH_RETRY_COOLDOWN_SEC = max(0, int(os.environ.get('LONGTAIL_PUBLISH_RETRY_COOLDOWN_SEC', '180') or '180'))
PUBLISH_COMMAND_TIMEOUT_SEC = max(60, int(os.environ.get('LONGTAIL_PUBLISH_COMMAND_TIMEOUT_SEC', '2400') or '2400'))
TEXT_RESPONSE_TIMEOUT_SEC = max(60, int(os.environ.get('LONGTAIL_TEXT_RESPONSE_TIMEOUT_SEC', '600') or '600'))
PUBLISH_MODE = os.environ.get('LONGTAIL_PUBLISH_MODE', 'publish').strip().lower() or 'publish'
if PUBLISH_MODE not in {'draft', 'private', 'publish'}:
    raise SystemExit(f"invalid LONGTAIL_PUBLISH_MODE: {PUBLISH_MODE}")

DOMAIN_CONFIGS = {
    'cheongyak': {
        'domain': 'cheongyak',
        'label': '분양 A-Z',
        'category_no': os.environ.get('LONGTAIL_NAVER_CATEGORY_NO', '16').strip(),
        'category_name': os.environ.get('LONGTAIL_NAVER_CATEGORY_NAME', 'How To 분양'),
    },
    'auction': {
        'domain': 'auction',
        'label': '경매 A-Z',
        'category_no': os.environ.get('LONGTAIL_AUCTION_NAVER_CATEGORY_NO', '17').strip(),
        'category_name': os.environ.get('LONGTAIL_AUCTION_NAVER_CATEGORY_NAME', 'How To 경매'),
    },
    'tax': {
        'domain': 'tax',
        'label': '세금 A-Z',
        'category_no': os.environ.get('LONGTAIL_TAX_NAVER_CATEGORY_NO', '18').strip(),
        'category_name': os.environ.get('LONGTAIL_TAX_NAVER_CATEGORY_NAME', 'How To 세금'),
    },
    'loan': {
        'domain': 'loan',
        'label': '대출 A-Z',
        'category_no': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NO', '19').strip(),
        'category_name': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NAME', '부동산 대출'),
    },
}

RECOVERABLE_BUNDLE_ERROR_CODES = (
    'CODEX_CLI_',
    'GPT_WEB_',
    'OPENAI_COMPAT_',
)

RECOVERABLE_GPT_IMAGE_MARKERS = (
    'GPT 이미지 생성 실패',
    'GPT_WEB_TIMEOUT',
    'GPT_WEB_NAVIGATION_TIMEOUT',
    'GPT_WEB_IMAGE_TIMEOUT',
    'GPT_WEB_RATE_LIMIT',
    'GPT_WEB_SUBMIT_FAILED',
    'Locator.click: Timeout',
    'element is not visible',
    'prompt-textarea',
    'Playwright 대기 시간이 초과',
    'Page.goto: Timeout',
    'Failed to create a ProcessSingleton',
    'publish command timed out',
)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _is_recoverable_gpt_image_failure(output: str) -> bool:
    return any(marker in output for marker in RECOVERABLE_GPT_IMAGE_MARKERS)


def _is_recoverable_generation_blocker(blocker: dict) -> bool:
    code = str(blocker.get('first_error_code') or '')
    return any(code.startswith(prefix) for prefix in RECOVERABLE_BUNDLE_ERROR_CODES)


def _reset_gpt_runtime_session(domain: str, bundle_id: int, attempt: int) -> None:
    runtime_dir = Path(os.environ.get('BUNYANG_LONGTAIL_DATA_DIR', 'data')) / 'gpt_profiles' / '_runtime'
    shutil.rmtree(runtime_dir, ignore_errors=True)
    os.environ['LONGTAIL_GPT_RECOVERY_TOKEN'] = f'curriculum_{domain}_{bundle_id}_{attempt}_{int(time.time())}'
    _print_json({
        'status': 'gpt_image_session_reset',
        'domain': domain,
        'bundle_id': bundle_id,
        'attempt': attempt,
        'removed_runtime_profile_dir': str(runtime_dir),
    })


def _run_publish_command(*, domain: str, bundle_id: int, category_no: str, category_name: str) -> dict:
    out_dir = OUTPUT_BASE / f'{domain}_bundle_{bundle_id}'
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        '/usr/bin/xvfb-run',
        '-a',
        '--server-args=-screen 0 1440x1100x24',
        '/usr/bin/python3',
        'scripts/publish_bundle_to_naver.py',
        '--db', DB_PATH,
        '--bundle-id', str(bundle_id),
        '--mode', PUBLISH_MODE,
        '--image-provider', 'gpt_web',
        '--category-name', category_name,
        '--output-root', str(out_dir),
    ]
    if category_no:
        cmd.extend(['--category-no', category_no])

    last_output = ''
    for publish_attempt in range(1, PUBLISH_RETRY_MAX + 1):
        _print_json({
            'status': 'publish_attempt_start',
            'domain': domain,
            'bundle_id': bundle_id,
            'attempt': publish_attempt,
            'max_attempts': PUBLISH_RETRY_MAX,
            'output_root': str(out_dir),
        })
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=PUBLISH_COMMAND_TIMEOUT_SEC,
            )
            returncode = result.returncode
            stdout = result.stdout or ''
            stderr = result.stderr or ''
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = exc.stdout or ''
            stderr = exc.stderr or ''
            if isinstance(stdout, bytes):
                stdout = stdout.decode('utf-8', errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', errors='replace')
            stderr = (stderr + '\n' if stderr else '') + f'publish command timed out after {PUBLISH_COMMAND_TIMEOUT_SEC}s'
        last_output = stdout + ('\n' + stderr if stderr else '')
        if stdout:
            print(stdout)
        if returncode == 0:
            return {
                'status': 'published',
                'domain': domain,
                'bundle_id': bundle_id,
                'publish_mode': PUBLISH_MODE,
                'output_root': str(out_dir),
            }
        if stderr:
            print(stderr, file=sys.stderr)
        recoverable = _is_recoverable_gpt_image_failure(last_output)
        _print_json({
            'status': 'publish_attempt_failed',
            'domain': domain,
            'bundle_id': bundle_id,
            'attempt': publish_attempt,
            'returncode': returncode,
            'recoverable_gpt_image_failure': recoverable,
            'cooldown_seconds': PUBLISH_RETRY_COOLDOWN_SEC if recoverable and publish_attempt < PUBLISH_RETRY_MAX else 0,
        })
        if not recoverable or publish_attempt >= PUBLISH_RETRY_MAX:
            break
        _reset_gpt_runtime_session(domain, bundle_id, publish_attempt)
        if PUBLISH_RETRY_COOLDOWN_SEC:
            time.sleep(PUBLISH_RETRY_COOLDOWN_SEC)

    raise RuntimeError(
        f'{domain} curriculum publish command failed after {PUBLISH_RETRY_MAX} attempts; '
        f'last output tail={last_output[-1200:]}'
    )


def _sync_hub() -> dict:
    out_dir = OUTPUT_BASE / f'hub_sync_{int(time.time())}'
    return publish_curriculum_hub_to_naver(
        db_path=DB_PATH,
        output_root=out_dir,
        track_key=CURRICULUM_TRACK_KEY,
        mode=PUBLISH_MODE,
        category_no=os.environ.get('LONGTAIL_NAVER_CATEGORY_NO', '16').strip(),
        category_name=os.environ.get('LONGTAIL_NAVER_CATEGORY_NAME', 'How To 분양'),
    )


attempted_variant_ids: set[int] = set()
selected_run_result = None
selected_candidate = None

try:
    for attempt in range(1, MAX_VARIANT_ATTEMPTS + 1):
        with connect(DB_PATH) as conn:
            row = select_curriculum_publish_candidate(conn, excluded_variant_ids=attempted_variant_ids)
        if not row:
            break
        selected_candidate = dict(row)
        variant_id = int(row['id'])
        attempted_variant_ids.add(variant_id)
        domain = row['domain']
        _print_json({
            'status': 'selected_curriculum_variant',
            'attempt': attempt,
            'variant_id': variant_id,
            'domain': domain,
            'chapter_no': row.get('curriculum_chapter_no'),
            'title': row['title'],
        })
        run_result = run_bundle(
            DB_PATH,
            **run_bundle_target_from_candidate(row),
            executor_mode='codex_cli',
            text_route='codex_cli',
            image_roles=[],
            simulate=False,
            response_timeout_seconds=TEXT_RESPONSE_TIMEOUT_SEC,
        )
        _print_json(run_result)
        blocker = describe_unpublishable_run_result(run_result)
        if blocker:
            _print_json({
                'status': 'skip_publish',
                'attempt': attempt,
                'domain': domain,
                **blocker,
            })
            if _is_recoverable_generation_blocker(blocker):
                raise RuntimeError(
                    f"recoverable curriculum generation failure; retry same variant on next slot: "
                    f"variant_id={variant_id}, code={blocker.get('first_error_code')}"
                )
            continue
        selected_run_result = run_result
        break

    if not selected_run_result or not selected_candidate:
        _print_json({
            'status': 'noop',
            'reason': 'no publishable curriculum bundle after attempts',
            'attempted_variant_ids': sorted(attempted_variant_ids),
        })
        raise SystemExit(0)

    domain = selected_candidate['domain']
    config = DOMAIN_CONFIGS[domain]
    bundle_id = int(selected_run_result['bundle']['id'])
    publish_result = _run_publish_command(
        domain=domain,
        bundle_id=bundle_id,
        category_no=config.get('category_no') or '',
        category_name=config.get('category_name') or '',
    )
    _print_json({'status': 'curriculum_daily_published', **publish_result})
    if publish_result.get('status') == 'published':
        hub_result = _sync_hub()
        _print_json({'status': 'curriculum_hub_synced_after_publish', 'ok': bool(hub_result.get('ok')), 'hub_result': hub_result})
except Exception as exc:
    _print_json({
        'status': 'failed',
        'error': str(exc),
        'traceback': traceback.format_exc(limit=5),
    })
    raise
PY

  echo "[$(date '+%F %T')] done: curriculum daily publish prod"
} >> "$LOG_FILE" 2>&1
