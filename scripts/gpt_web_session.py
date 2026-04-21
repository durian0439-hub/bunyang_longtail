#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "browser_runtime"
STATE_PATH = DATA_DIR / "session_state.json"
XVFB_LOG = DATA_DIR / "xvfb.log"
CHROME_LOG = DATA_DIR / "chrome.log"
DEFAULT_PROFILE = ROOT / "data" / "gpt_profiles" / "gpt_terminal_profile_dev"
DEFAULT_URL = "https://chatgpt.com/"


def ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    ensure_dir()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def http_get_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def cdp_ready(port: int) -> bool:
    try:
        payload = http_get_json(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        return bool(payload.get("Browser"))
    except Exception:
        return False


def cdp_pages(port: int) -> list[dict[str, Any]]:
    try:
        payload = http_get_json(f"http://127.0.0.1:{port}/json/list", timeout=2.0)
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def wait_until(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def spawn_process(command: list[str], *, env: dict[str, str] | None = None, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    return subprocess.Popen(
        command,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(ROOT),
    )


def terminate_pid(pid: int | None, *, grace_seconds: float = 5.0) -> None:
    if not is_pid_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return
    if wait_until(lambda: not is_pid_alive(pid), timeout=grace_seconds, interval=0.2):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            return


def start_session(args: argparse.Namespace) -> int:
    ensure_dir()
    profile_dir = Path(args.profile).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    state = load_state()
    xvfb_pid = state.get("xvfb_pid")
    chrome_pid = state.get("chrome_pid")

    if not is_pid_alive(xvfb_pid):
        xvfb_cmd = [
            "Xvfb",
            args.display,
            "-screen",
            "0",
            args.screen,
            "-nolisten",
            "tcp",
            "-ac",
        ]
        xvfb = spawn_process(xvfb_cmd, log_path=XVFB_LOG)
        if not wait_until(lambda: is_pid_alive(xvfb.pid), timeout=3):
            print("Xvfb 시작 실패", file=sys.stderr)
            return 1
        xvfb_pid = xvfb.pid
        state["xvfb_pid"] = xvfb_pid

    if not is_pid_alive(chrome_pid) or not cdp_ready(args.port):
        chrome_env = os.environ.copy()
        chrome_env["DISPLAY"] = args.display
        chrome_cmd = [
            "google-chrome",
            f"--remote-debugging-port={args.port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--password-store=basic",
            f"--window-size={args.window_size}",
            "--new-window",
            args.url,
        ]
        chrome = spawn_process(chrome_cmd, env=chrome_env, log_path=CHROME_LOG)
        if not wait_until(lambda: cdp_ready(args.port), timeout=args.start_timeout, interval=0.5):
            print("Chrome CDP 포트가 준비되지 않았습니다.", file=sys.stderr)
            print(f"chrome log: {CHROME_LOG}", file=sys.stderr)
            return 1
        chrome_pid = chrome.pid
        state["chrome_pid"] = chrome_pid

    state.update(
        {
            "display": args.display,
            "port": args.port,
            "profile": str(profile_dir),
            "url": args.url,
            "screen": args.screen,
            "window_size": args.window_size,
            "updated_at": int(time.time()),
        }
    )
    save_state(state)
    pages = cdp_pages(args.port)
    print(
        json.dumps(
            {
                "ok": True,
                "display": args.display,
                "cdp_url": f"http://127.0.0.1:{args.port}",
                "profile": str(profile_dir),
                "xvfb_pid": xvfb_pid,
                "chrome_pid": chrome_pid,
                "pages": [
                    {"title": page.get("title"), "url": page.get("url")} for page in pages[:5]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def stop_session(_: argparse.Namespace) -> int:
    state = load_state()
    terminate_pid(state.get("chrome_pid"))
    terminate_pid(state.get("xvfb_pid"))
    save_state({})
    print(json.dumps({"ok": True, "stopped": True}, ensure_ascii=False, indent=2))
    return 0


def status_session(_: argparse.Namespace) -> int:
    state = load_state()
    port = state.get("port", 9222)
    payload = {
        "display": state.get("display"),
        "cdp_url": f"http://127.0.0.1:{port}",
        "profile": state.get("profile"),
        "xvfb_alive": is_pid_alive(state.get("xvfb_pid")),
        "chrome_alive": is_pid_alive(state.get("chrome_pid")),
        "cdp_ready": cdp_ready(port),
        "xvfb_pid": state.get("xvfb_pid"),
        "chrome_pid": state.get("chrome_pid"),
        "pages": [
            {"title": page.get("title"), "url": page.get("url")} for page in cdp_pages(port)[:10]
        ],
        "logs": {
            "xvfb": str(XVFB_LOG),
            "chrome": str(CHROME_LOG),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def restart_session(args: argparse.Namespace) -> int:
    stop_session(args)
    return start_session(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="터미널에서 유지되는 GPT 웹 Chrome 세션 관리자")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--display", default=":100")
        subparser.add_argument("--port", type=int, default=9333)
        subparser.add_argument("--profile", default=str(DEFAULT_PROFILE))
        subparser.add_argument("--url", default=DEFAULT_URL)
        subparser.add_argument("--screen", default="1440x1100x24")
        subparser.add_argument("--window-size", default="1440,1100")
        subparser.add_argument("--start-timeout", type=float, default=20.0)

    start = subparsers.add_parser("start", help="Xvfb + Chrome 세션 시작")
    add_common(start)
    start.set_defaults(func=start_session)

    stop = subparsers.add_parser("stop", help="세션 종료")
    add_common(stop)
    stop.set_defaults(func=stop_session)

    status = subparsers.add_parser("status", help="세션 상태 확인")
    add_common(status)
    status.set_defaults(func=status_session)

    restart = subparsers.add_parser("restart", help="세션 재시작")
    add_common(restart)
    restart.set_defaults(func=restart_session)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
