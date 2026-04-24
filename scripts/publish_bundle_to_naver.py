#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bunyang_longtail.cron_publish import is_recent_publish_conflict
from bunyang_longtail.database import connect
from bunyang_longtail.naver_bundle_publish import publish_bundle_to_naver


def main() -> int:
    parser = argparse.ArgumentParser(description="bunyang_longtail bundle 을 네이버 블로그에 업로드")
    parser.add_argument("--db", required=True)
    parser.add_argument("--bundle-id", type=int, required=True)
    parser.add_argument("--mode", choices=["draft", "private", "publish"], default="private")
    parser.add_argument("--title")
    parser.add_argument("--output-root")
    parser.add_argument("--image-provider", choices=["local", "auto", "gpt_web", "openai_compat"], default="auto")
    parser.add_argument("--category-no")
    parser.add_argument("--category-name")
    args = parser.parse_args()

    with connect(args.db) as conn:
        row = conn.execute("SELECT variant_id FROM article_bundle WHERE id = ?", (args.bundle_id,)).fetchone()
        if row is None:
            raise SystemExit(f"bundle_id={args.bundle_id} 를 찾지 못했습니다.")
        conflict = is_recent_publish_conflict(conn, variant_id=int(row[0]))
        if conflict is not None:
            print(json.dumps({
                "status": "blocked",
                "reason": "recent_publish_conflict",
                "bundle_id": args.bundle_id,
                "variant_id": int(row[0]),
                "conflict": conflict,
            }, ensure_ascii=False, indent=2))
            return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = args.output_root or f"data/naver_publish/bundle_{args.bundle_id}_{timestamp}"
    result = publish_bundle_to_naver(
        db_path=args.db,
        bundle_id=args.bundle_id,
        output_root=output_root,
        mode=args.mode,
        title_override=args.title,
        image_provider=args.image_provider,
        category_no=args.category_no,
        category_name=args.category_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
