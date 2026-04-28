#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.gpt_web import GptWebExecutionError, execute_image_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', type=int, required=True)
    parser.add_argument('--profile-name', required=True)
    parser.add_argument('--prompt-text', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--excerpt', default='')
    parser.add_argument('--image-role', required=True)
    parser.add_argument('--output-path', required=True)
    parser.add_argument('--artifact-root', required=True)
    parser.add_argument('--wait-for-ready-seconds', type=int, default=240)
    parser.add_argument('--response-timeout-seconds', type=int, default=600)
    args = parser.parse_args()

    try:
        result = execute_image_job(
            job_id=args.job_id,
            profile_name=args.profile_name,
            prompt_text=args.prompt_text,
            title=args.title,
            excerpt=args.excerpt,
            image_role=args.image_role,
            output_path=args.output_path,
            headed=True,
            wait_for_ready_seconds=args.wait_for_ready_seconds,
            response_timeout_seconds=args.response_timeout_seconds,
            artifact_root=args.artifact_root,
        )
    except GptWebExecutionError as exc:
        payload = {'error': str(exc), 'code': exc.code, 'artifact_dir': exc.artifact_dir}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
