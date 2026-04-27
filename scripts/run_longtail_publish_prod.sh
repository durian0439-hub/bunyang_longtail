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
export LONGTAIL_NAVER_CLIP_UPLOAD="${LONGTAIL_NAVER_CLIP_UPLOAD:-1}"
export LONGTAIL_NAVER_CLIP_VISIBILITY="${LONGTAIL_NAVER_CLIP_VISIBILITY:-public}"
export LONGTAIL_YOUTUBE_UPLOAD="${LONGTAIL_YOUTUBE_UPLOAD:-1}"
export LONGTAIL_YOUTUBE_PRIVACY_STATUS="${LONGTAIL_YOUTUBE_PRIVACY_STATUS:-${LONGTAIL_YOUTUBE_PRIVACY:-unlisted}}"
export LONGTAIL_TIKTOK_UPLOAD="${LONGTAIL_TIKTOK_UPLOAD:-0}"
export LONGTAIL_TIKTOK_PRIVACY_LEVEL="${LONGTAIL_TIKTOK_PRIVACY_LEVEL:-SELF_ONLY}"
export LONGTAIL_VIDEO_MAKER_ROOT="${LONGTAIL_VIDEO_MAKER_ROOT:-/home/kj/app/video_maker}"
export LONGTAIL_MEDIA_CLEANUP_ENABLED="${LONGTAIL_MEDIA_CLEANUP_ENABLED:-1}"
export LONGTAIL_MEDIA_RETENTION_DAYS="${LONGTAIL_MEDIA_RETENTION_DAYS:-3}"
export LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR="${LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR:-/home/kj/app/bunyang/blog-cheongyak-automation/outputs/publish_longtail}"

PROD_ROOT="${LONGTAIL_PROD_ROOT:-/home/kj/app/bunyang_longtail/prod}"
CODE_ROOT="${LONGTAIL_PROD_CODE_ROOT:-$PROD_ROOT/runtime/current}"
DATA_DIR="${BUNYANG_LONGTAIL_DATA_DIR:-$CODE_ROOT/data}"
DB_PATH="${LONGTAIL_PROD_DB_PATH:-$DATA_DIR/cdp_probe5.sqlite3}"
OUTPUT_BASE="${LONGTAIL_NAVER_OUTPUT_BASE:-$DATA_DIR/naver_publish/cron_runs}"
LOG_DIR="$PROD_ROOT/logs"
RUN_DIR="$PROD_ROOT/run"
LOCK_FILE="$RUN_DIR/longtail_publish.lock"
LOG_FILE="$LOG_DIR/longtail_publish.log"

export BUNYANG_LONGTAIL_ROOT="$CODE_ROOT"
export BUNYANG_LONGTAIL_DATA_DIR="$DATA_DIR"
export LONGTAIL_PROD_DB_PATH="$DB_PATH"
export LONGTAIL_NAVER_OUTPUT_BASE="$OUTPUT_BASE"

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ "${LONGTAIL_LOCK_ALREADY_HELD:-0}" != "1" ]]; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[$(date '+%F %T')] skip: previous longtail publish job still running" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  echo "[$(date '+%F %T')] start: longtail publish prod"
  echo "prod code root: $CODE_ROOT"
  echo "prod data dir: $DATA_DIR"

  if [[ ! -d "$CODE_ROOT/.git" ]]; then
    echo "error: production code checkout is missing: $CODE_ROOT"
    echo "hint: clone origin/main into $CODE_ROOT before running prod cron"
    exit 1
  fi

  cd "$CODE_ROOT"

  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" != "main" ]]; then
    echo "error: longtail cron must run on prod main branch, current=$current_branch"
    exit 1
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: longtail cron requires a clean production working tree before sync"
    git status --short
    exit 1
  fi

  git fetch origin main
  local_rev="$(git rev-parse HEAD)"
  remote_rev="$(git rev-parse origin/main)"
  if [[ "$local_rev" == "$remote_rev" ]]; then
    echo "git status: prod main already synced with origin/main"
  else
    echo "error: prod checkout is not synced with origin/main; sync must finish before running the in-repo runner"
    echo "local=$local_rev remote=$remote_rev"
    exit 1
  fi

  if [[ ! -f "$DB_PATH" ]]; then
    echo "error: production DB is missing: $DB_PATH"
    exit 1
  fi

  mkdir -p "$OUTPUT_BASE"

  /usr/bin/python3 -m unittest tests.test_naver_bundle_publish tests.test_longtail

  /usr/bin/python3 - <<'PY'
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, 'src')
from bunyang_longtail.cron_publish import describe_unpublishable_run_result, run_bundle_target_from_candidate, select_publish_candidate
from bunyang_longtail.database import connect
from bunyang_longtail.workers import run_bundle

DB_PATH = os.environ['LONGTAIL_PROD_DB_PATH']
OUTPUT_BASE = Path(os.environ['LONGTAIL_NAVER_OUTPUT_BASE'])
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
MAX_VARIANT_ATTEMPTS = 3
PUBLISH_MODE = os.environ.get('LONGTAIL_PUBLISH_MODE', 'publish').strip().lower() or 'publish'
if PUBLISH_MODE not in {'draft', 'private', 'publish'}:
    raise SystemExit(f"invalid LONGTAIL_PUBLISH_MODE: {PUBLISH_MODE}")

DOMAIN_CONFIGS = [
    {
        'domain': 'cheongyak',
        'label': '분양 롱테일',
        'category_no': os.environ.get('LONGTAIL_NAVER_CATEGORY_NO', '16'),
        'category_name': os.environ.get('LONGTAIL_NAVER_CATEGORY_NAME', 'How To 분양'),
    },
    {
        'domain': 'auction',
        'label': '경매 롱테일',
        'category_no': os.environ.get('LONGTAIL_AUCTION_NAVER_CATEGORY_NO', '17').strip(),
        'category_name': os.environ.get('LONGTAIL_AUCTION_NAVER_CATEGORY_NAME', 'How To 경매'),
    },
    {
        'domain': 'tax',
        'label': '세금 롱테일',
        'category_no': os.environ.get('LONGTAIL_TAX_NAVER_CATEGORY_NO', '18').strip(),
        'category_name': os.environ.get('LONGTAIL_TAX_NAVER_CATEGORY_NAME', 'How To 세금'),
    },
    {
        'domain': 'loan',
        'label': '대출 롱테일',
        'category_no': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NO', '19').strip(),
        'category_name': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NAME', '부동산 대출'),
    },
]

# 기본 운영은 항상 전체 도메인 발행입니다.
# 과거 LONGTAIL_DOMAINS 환경변수가 남아 있으면 특정 도메인만 발행되는 문제가 있었기 때문에,
# 명시적으로 LONGTAIL_ALLOW_DOMAIN_FILTER=1 을 준 경우에만 필터를 허용합니다.
requested_domains_raw = os.environ.get('LONGTAIL_DOMAINS', '').strip()
allow_domain_filter = os.environ.get('LONGTAIL_ALLOW_DOMAIN_FILTER', '').strip().lower() in {'1', 'true', 'yes', 'on'}
if requested_domains_raw and allow_domain_filter:
    requested_domains = [item.strip() for item in requested_domains_raw.split(',') if item.strip()]
    known_domains = {item['domain'] for item in DOMAIN_CONFIGS}
    unknown_domains = sorted(set(requested_domains) - known_domains)
    if unknown_domains:
        raise SystemExit(f"unknown LONGTAIL_DOMAINS: {', '.join(unknown_domains)}")
    DOMAIN_CONFIGS = [item for item in DOMAIN_CONFIGS if item['domain'] in requested_domains]
elif requested_domains_raw:
    print(json.dumps({
        'status': 'domain_filter_ignored',
        'reason': 'publish_all_domains_by_default',
        'LONGTAIL_DOMAINS': requested_domains_raw,
        'hint': 'set LONGTAIL_ALLOW_DOMAIN_FILTER=1 to intentionally restrict domains',
    }, ensure_ascii=False, indent=2))


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise RuntimeError(f'{domain} publish command failed with code {result.returncode}')

    return {
        'status': 'published',
        'domain': domain,
        'bundle_id': bundle_id,
        'publish_mode': PUBLISH_MODE,
        'output_root': str(out_dir),
    }


def run_domain(config: dict) -> dict:
    domain = config['domain']
    _print_json({'status': 'domain_start', **config})

    replenish_cmd = [
        '/usr/bin/python3',
        'run.py',
        '--db', DB_PATH,
        'replenish',
        '--domain', domain,
        '--min-queued', '30',
        '--variants-per-cluster', '3',
    ]
    replenish = subprocess.run(replenish_cmd, capture_output=True, text=True)
    print(replenish.stdout)
    if replenish.returncode != 0:
        if replenish.stderr:
            print(replenish.stderr, file=sys.stderr)
        raise RuntimeError(f'{domain} replenish failed with code {replenish.returncode}')

    attempted_variant_ids: set[int] = set()
    selected_run_result = None

    for attempt in range(1, MAX_VARIANT_ATTEMPTS + 1):
        with connect(DB_PATH) as conn:
            row = select_publish_candidate(conn, domain=domain, excluded_variant_ids=attempted_variant_ids)

        if not row:
            break

        variant_id = int(row['id'])
        attempted_variant_ids.add(variant_id)
        _print_json({
            'status': 'selected_variant',
            'domain': domain,
            'attempt': attempt,
            'variant_id': variant_id,
            'title': row['title'],
        })

        run_target = run_bundle_target_from_candidate(row)
        run_result = run_bundle(
            DB_PATH,
            **run_target,
            executor_mode='codex_cli',
            text_route='codex_cli',
            image_roles=[],
            simulate=False,
        )
        _print_json(run_result)

        blocker = describe_unpublishable_run_result(run_result)
        if blocker:
            _print_json({
                'status': 'skip_publish',
                'domain': domain,
                'attempt': attempt,
                **blocker,
            })
            continue

        selected_run_result = run_result
        break

    if not selected_run_result:
        payload = {
            'status': 'noop',
            'domain': domain,
            'reason': 'no publishable bundle after attempts',
            'attempted_variant_ids': sorted(attempted_variant_ids),
        }
        _print_json(payload)
        return payload

    bundle_id = int(selected_run_result['bundle']['id'])
    publish_result = _run_publish_command(
        domain=domain,
        bundle_id=bundle_id,
        category_no=config.get('category_no') or '',
        category_name=config.get('category_name') or '',
    )
    _print_json({'status': 'domain_done', **publish_result})
    return publish_result


results = []
for config in DOMAIN_CONFIGS:
    try:
        results.append(run_domain(config))
    except Exception as exc:
        payload = {
            'status': 'failed',
            'domain': config['domain'],
            'error': str(exc),
            'traceback': traceback.format_exc(limit=5),
        }
        _print_json(payload)
        results.append(payload)
        # 분양이 실패해도 경매는 반드시 이어서 실행한다. 마지막에만 실패 코드를 반환한다.
        continue

_print_json({'status': 'cron_domain_summary', 'results': results})
if any(item.get('status') == 'failed' for item in results):
    raise SystemExit(1)
PY

  if [[ "${LONGTAIL_MEDIA_CLEANUP_ENABLED}" == "1" ]]; then
    /usr/bin/python3 scripts/cleanup_published_media.py \
      --db "$DB_PATH" \
      --output-base "$OUTPUT_BASE" \
      --days "$LONGTAIL_MEDIA_RETENTION_DAYS" \
      --blog-output-dir "$LONGTAIL_BLOG_PUBLISH_OUTPUT_DIR"
  fi

  echo "[$(date '+%F %T')] done: longtail publish prod"
} >> "$LOG_FILE" 2>&1
