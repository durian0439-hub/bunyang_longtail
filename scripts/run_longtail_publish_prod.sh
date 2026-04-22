#!/usr/bin/env bash
set -euo pipefail

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
  git pull --rebase origin main

  /usr/bin/python3 -m unittest tests.test_naver_bundle_publish tests.test_longtail

  /usr/bin/python3 run.py replenish --min-queued 30 --variants-per-cluster 3

  /usr/bin/python3 - <<'PY'
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, 'src')
from bunyang_longtail.database import connect, fetch_one
from bunyang_longtail.workers import run_bundle

DB_PATH = 'data/cdp_probe5.sqlite3'
OUTPUT_BASE = Path('data/naver_publish/cron_runs')
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

with connect(DB_PATH) as conn:
    row = fetch_one(
        conn,
        """
        WITH last_published AS (
            SELECT ph.variant_id, tc.family, tv.angle, ph.published_at,
                   ROW_NUMBER() OVER (ORDER BY ph.published_at DESC, ph.id DESC) AS rn
            FROM publish_history ph
            JOIN topic_variant tv ON tv.id = ph.variant_id
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE ph.channel = 'naver_blog'
        )
        SELECT tv.id, tv.title
        FROM topic_variant tv
        JOIN topic_cluster tc ON tc.id = tv.cluster_id
        WHERE tv.status = 'queued'
          AND NOT EXISTS (
              SELECT 1 FROM publish_history ph WHERE ph.variant_id = tv.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM last_published lp
              WHERE lp.rn <= 4 AND (lp.family = tc.family OR lp.angle = tv.angle)
          )
        ORDER BY tc.priority DESC, tv.created_at ASC, tv.id ASC
        LIMIT 1
        """,
    )
    if not row:
        row = fetch_one(
            conn,
            """
            SELECT tv.id, tv.title
            FROM topic_variant tv
            JOIN topic_cluster tc ON tc.id = tv.cluster_id
            WHERE tv.status = 'queued'
              AND NOT EXISTS (
                  SELECT 1 FROM publish_history ph WHERE ph.variant_id = tv.id
              )
            ORDER BY tc.priority DESC, tv.created_at ASC, tv.id ASC
            LIMIT 1
            """,
        )

if not row:
    print(json.dumps({'status': 'noop', 'reason': 'no queued unpublished variant'}, ensure_ascii=False))
    raise SystemExit(0)

variant_id = row['id']
run_result = run_bundle(
    DB_PATH,
    variant_id=variant_id,
    executor_mode='codex_cli',
    text_route='codex_cli',
    image_roles=None,
    simulate=False,
)

bundle_id = run_result['bundle']['id']
out_dir = OUTPUT_BASE / f'bundle_{bundle_id}'
out_dir.mkdir(parents=True, exist_ok=True)

cmd = [
    '/usr/bin/python3',
    'scripts/publish_bundle_to_naver.py',
    '--db', DB_PATH,
    '--bundle-id', str(bundle_id),
    '--mode', 'publish',
    '--image-provider', 'gpt_web',
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
