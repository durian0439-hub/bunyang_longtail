#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_SCRIPT = ROOT / "scripts" / "gpt_web_session.py"
DEFAULT_PROFILE = ROOT / "data" / "gpt_profiles" / "gpt_terminal_profile_dev"
SHUTTING_DOWN = False


def run_json(command: list[str]) -> dict:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(command)}")
    return json.loads(result.stdout)


def build_common_args(args: argparse.Namespace) -> list[str]:
    return [
        "--display",
        args.display,
        "--port",
        str(args.port),
        "--profile",
        str(Path(args.profile).expanduser().resolve()),
        "--url",
        args.url,
        "--screen",
        args.screen,
        "--window-size",
        args.window_size,
        "--start-timeout",
        str(args.start_timeout),
    ]


def ensure_started(args: argparse.Namespace) -> dict:
    command = [sys.executable, str(SESSION_SCRIPT), "start", *build_common_args(args)]
    return run_json(command)


def read_status(args: argparse.Namespace) -> dict:
    command = [sys.executable, str(SESSION_SCRIPT), "status", *build_common_args(args)]
    return run_json(command)


def stop_session(args: argparse.Namespace) -> None:
    command = [sys.executable, str(SESSION_SCRIPT), "stop", *build_common_args(args)]
    try:
        run_json(command)
    except Exception:
        pass


def is_healthy(status: dict) -> bool:
    return bool(status.get("xvfb_alive") and status.get("chrome_alive") and status.get("cdp_ready"))


def handle_signal(signum, _frame) -> None:
    global SHUTTING_DOWN
    SHUTTING_DOWN = True
    print(f"[gpt-web-daemon] signal={signum} 종료 요청 수신", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GPT 웹 세션 감시 데몬")
    parser.add_argument("--display", default=":100")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--url", default="https://chatgpt.com/")
    parser.add_argument("--screen", default="1440x1100x24")
    parser.add_argument("--window-size", default="1440,1100")
    parser.add_argument("--start-timeout", type=float, default=20.0)
    parser.add_argument("--check-interval", type=float, default=15.0)
    return parser


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    args = build_parser().parse_args()
    print(
        f"[gpt-web-daemon] 시작 display={args.display} port={args.port} profile={Path(args.profile).expanduser().resolve()}",
        flush=True,
    )

    while not SHUTTING_DOWN:
        try:
            status = read_status(args)
            if not is_healthy(status):
                print(f"[gpt-web-daemon] 비정상 상태 감지, 세션 재기동: {json.dumps(status, ensure_ascii=False)}", flush=True)
                started = ensure_started(args)
                print(f"[gpt-web-daemon] 세션 기동 완료: {json.dumps(started, ensure_ascii=False)}", flush=True)
            else:
                print("[gpt-web-daemon] 세션 정상", flush=True)
        except Exception as exc:
            print(f"[gpt-web-daemon] 점검 실패, 재시도 예정: {exc}", flush=True)
            try:
                started = ensure_started(args)
                print(f"[gpt-web-daemon] 복구 기동 결과: {json.dumps(started, ensure_ascii=False)}", flush=True)
            except Exception as start_exc:
                print(f"[gpt-web-daemon] 복구 기동 실패: {start_exc}", flush=True)
        sleep_left = args.check_interval
        while sleep_left > 0 and not SHUTTING_DOWN:
            chunk = min(1.0, sleep_left)
            time.sleep(chunk)
            sleep_left -= chunk

    stop_session(args)
    print("[gpt-web-daemon] 종료 완료", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
