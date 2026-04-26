#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.asset_retention import cleanup_published_media, default_blog_output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="영상 발행 완료 후 3일 지난 롱테일 이미지/영상 파일 정리")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output-base", required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--blog-output-dir", default=str(default_blog_output_dir()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = cleanup_published_media(
        db_path=args.db,
        output_base=args.output_base,
        retention_days=args.days,
        blog_output_dir=args.blog_output_dir,
        dry_run=args.dry_run,
    )
    print(json.dumps({"status": "ok", **summary.as_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
