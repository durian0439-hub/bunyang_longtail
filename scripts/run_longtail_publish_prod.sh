#!/usr/bin/env bash
set -euo pipefail

export PATH="/home/kj/.npm-global/bin:/home/kj/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export LONGTAIL_NAVER_CATEGORY_NO="${LONGTAIL_NAVER_CATEGORY_NO:-16}"
export LONGTAIL_NAVER_CATEGORY_NAME="${LONGTAIL_NAVER_CATEGORY_NAME:-How To 분양}"

DEV_ROOT="/home/kj/app/bunyang_longtail/dev"
PROD_ROOT="/home/kj/app/bunyang_longtail/prod"
DB_PATH="$DEV_ROOT/data/cdp_probe5.sqlite3"
LOG_DIR="$PROD_ROOT/logs"
RUN_DIR="$PROD_ROOT/run"
LOCK_FILE="$RUN_DIR/longtail_publish.lock"
LOG_FILE="$LOG_DIR/longtail_publish.log"

mkdir -p "$LOG_DIR" "$RUN_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] skip: previous longtail publish job still running" >> "$LOG_FILE"
  exit 0
fi

{
  echo "[$(date '+%F %T')] start: longtail publish prod"

  cd "$DEV_ROOT"

  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" != "main" ]]; then
    echo "error: longtail cron must run on main branch, current=$current_branch"
    exit 1
  fi

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: longtail cron requires a clean working tree before sync"
    git status --short
    exit 1
  fi

  git fetch origin main
  local_rev="$(git rev-parse HEAD)"
  remote_rev="$(git rev-parse origin/main)"
  base_rev="$(git merge-base HEAD origin/main)"
  if [[ "$local_rev" == "$remote_rev" ]]; then
    echo "git status: main already synced with origin/main"
  elif [[ "$local_rev" == "$base_rev" ]]; then
    git merge --ff-only origin/main
  else
    echo "error: local main is ahead of or diverged from origin/main; push/merge before cron execution"
    echo "local=$local_rev remote=$remote_rev base=$base_rev"
    exit 1
  fi

  /usr/bin/python3 -m unittest tests.test_naver_bundle_publish tests.test_longtail

  /usr/bin/python3 run.py replenish --min-queued 30 --variants-per-cluster 3

  /usr/bin/python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, 'src')
from bunyang_longtail.cron_publish import describe_unpublishable_run_result, run_bundle_target_from_candidate, select_publish_candidate
from bunyang_longtail.database import connect
from bunyang_longtail.workers import run_bundle

DB_PATH = 'data/cdp_probe5.sqlite3'
OUTPUT_BASE = Path('data/naver_publish/cron_runs')
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
MAX_VARIANT_ATTEMPTS = 3

attempted_variant_ids: set[int] = set()
selected_run_result = None

for attempt in range(1, MAX_VARIANT_ATTEMPTS + 1):
    with connect(DB_PATH) as conn:
        row = select_publish_candidate(conn, excluded_variant_ids=attempted_variant_ids)

    if not row:
        break

    variant_id = int(row['id'])
    attempted_variant_ids.add(variant_id)
    print(json.dumps({
        'status': 'selected_variant',
        'attempt': attempt,
        'variant_id': variant_id,
        'title': row['title'],
    }, ensure_ascii=False))

    run_target = run_bundle_target_from_candidate(row)
    run_result = run_bundle(
        DB_PATH,
        **run_target,
        executor_mode='codex_cli',
        text_route='codex_cli',
        image_roles=None,
        simulate=False,
    )
    print(json.dumps(run_result, ensure_ascii=False, indent=2))

    blocker = describe_unpublishable_run_result(run_result)
    if blocker:
        print(json.dumps({
            'status': 'skip_publish',
            'attempt': attempt,
            **blocker,
        }, ensure_ascii=False))
        continue

    selected_run_result = run_result
    break

if not selected_run_result:
    print(json.dumps({
        'status': 'noop',
        'reason': 'no publishable bundle after attempts',
        'attempted_variant_ids': sorted(attempted_variant_ids),
    }, ensure_ascii=False))
    raise SystemExit(0)

bundle_id = selected_run_result['bundle']['id']
out_dir = OUTPUT_BASE / f'bundle_{bundle_id}'
out_dir.mkdir(parents=True, exist_ok=True)

cmd = [
    '/usr/bin/xvfb-run',
    '-a',
    '--server-args=-screen 0 1440x1100x24',
    '/usr/bin/python3',
    'scripts/publish_bundle_to_naver.py',
    '--db', DB_PATH,
    '--bundle-id', str(bundle_id),
    '--mode', 'publish',
    '--image-provider', 'gpt_web',
    '--category-no', os.environ.get('LONGTAIL_NAVER_CATEGORY_NO', '16'),
    '--category-name', os.environ.get('LONGTAIL_NAVER_CATEGORY_NAME', 'How To 분양'),
    '--output-root', str(out_dir),
]
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
if result.returncode != 0:
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)
PY

  echo "[$(date '+%F %T')] done: longtail publish prod"
} >> "$LOG_FILE" 2>&1
