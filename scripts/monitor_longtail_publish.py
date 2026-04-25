#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROD_ROOT = Path(os.getenv("LONGTAIL_PROD_ROOT", "/home/kj/app/bunyang_longtail/prod")).resolve()
CODE_ROOT = Path(os.getenv("LONGTAIL_PROD_CODE_ROOT", PROD_ROOT / "runtime" / "current")).resolve()
DATA_DIR = Path(os.getenv("BUNYANG_LONGTAIL_DATA_DIR", CODE_ROOT / "data")).resolve()
LOG_PATH = Path(os.getenv("LONGTAIL_PUBLISH_LOG", PROD_ROOT / "logs" / "longtail_publish.log")).resolve()
STATE_PATH = Path(os.getenv("LONGTAIL_MONITOR_STATE", PROD_ROOT / "run" / "longtail_monitor_state.json")).resolve()
DB_PATH = Path(os.getenv("LONGTAIL_PROD_DB_PATH", DATA_DIR / "cdp_probe5.sqlite3")).resolve()
RUN_CMD = os.getenv("LONGTAIL_RUN_CMD", str(CODE_ROOT / "scripts" / "run_longtail_publish_prod.sh"))
TARGET = os.getenv("LONGTAIL_NOTIFY_TARGET", "8272573727")


@dataclass
class CheckResult:
    status: str
    detail: str
    should_retry: bool = False


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_publish_id() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM publish_history").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def tail_log() -> str:
    if not LOG_PATH.exists():
        return ""
    return LOG_PATH.read_text(encoding="utf-8", errors="ignore")[-12000:]


def evaluate() -> CheckResult:
    log_text = tail_log()
    if "error: 리베이스로 풀하기 할 수 없습니다" in log_text:
        return CheckResult("git_dirty", "cron 실행이 dirty working tree 때문에 중단됐습니다.", True)
    if '"status": "noop"' in log_text:
        return CheckResult("noop", "발행 가능한 후보가 없어 noop으로 종료됐습니다.")
    if "done: longtail publish prod" in log_text:
        return CheckResult("ok", "최근 배치가 정상 종료됐습니다.")
    return CheckResult("unknown", "최근 배치 상태를 로그만으로 확정하지 못했습니다.")


def send_telegram(message: str) -> None:
    subprocess.run(
        [
            "openclaw",
            "message",
            "send",
            "--channel",
            "telegram",
            "--account",
            "openclaw",
            "--target",
            TARGET,
            "--message",
            message,
        ],
        check=True,
    )


def run_retry() -> tuple[int, str]:
    proc = subprocess.run(RUN_CMD, shell=True, capture_output=True, text=True)
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return proc.returncode, output[-4000:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--retry-on-failure", action="store_true")
    args = parser.parse_args()

    state = load_state()
    before_publish_id = latest_publish_id()
    result = evaluate()
    message = f"[longtail-monitor] status={result.status} | {result.detail}"

    retried_at = state.get("last_retry_message")
    if result.should_retry and args.retry_on_failure and retried_at != message:
        rc, output = run_retry()
        after_publish_id = latest_publish_id()
        retried_result = evaluate()
        message += (
            f"\n자동 재실행 rc={rc}"
            f"\n재실행 후 status={retried_result.status}"
            f"\npublish_history: {before_publish_id} -> {after_publish_id}"
        )
        if output.strip():
            message += f"\n출력 요약:\n{output}"
        state["last_retry_message"] = message

    if args.notify:
        last_sent = state.get("last_message")
        if last_sent != message:
            send_telegram(message)
            state["last_message"] = message

    save_state(state)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
