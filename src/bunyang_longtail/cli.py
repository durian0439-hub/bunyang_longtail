from __future__ import annotations

import argparse
import json
from pathlib import Path
from pprint import pprint

from .config import DEFAULT_DB_PATH, DEFAULT_EXPORT_PATH, ensure_data_dir
from .database import init_db, migrate_db
from .gpt_web import GptWebExecutionError, probe_gpt_web
from .openai_compat import OpenAICompatExecutionError, probe_openai_compat
from .planner import export_prompts, get_prompt, list_variants, mark_published, replenish_queue, stats
from .workers import (
    complete_image_job,
    complete_text_job,
    create_bundle,
    fail_job,
    job_stats,
    queue_image_job,
    queue_text_job,
    run_bundle,
    start_job,
)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="청약 롱테일 주제/프롬프트 관리 도구")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB 경로")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="DB 초기화")
    subparsers.add_parser("migrate-v2", help="v2 스키마 마이그레이션")

    replenish_parser = subparsers.add_parser("replenish", help="대기열 보충")
    replenish_parser.add_argument("--min-queued", type=int, default=500)
    replenish_parser.add_argument("--variants-per-cluster", type=int, default=4)

    list_parser = subparsers.add_parser("list", help="변형 주제 조회")
    list_parser.add_argument("--status", default="queued")
    list_parser.add_argument("--limit", type=int, default=20)

    prompt_parser = subparsers.add_parser("show-prompt", help="프롬프트 조회")
    prompt_group = prompt_parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--id", type=int)
    prompt_group.add_argument("--slug")

    export_parser = subparsers.add_parser("export", help="JSONL로 프롬프트 내보내기")
    export_parser.add_argument("--status", default="queued")
    export_parser.add_argument("--limit", type=int, default=100)
    export_parser.add_argument("--output", default=str(DEFAULT_EXPORT_PATH))

    create_bundle_parser = subparsers.add_parser("create-bundle", help="글 1개 단위 article bundle 생성")
    create_bundle_parser.add_argument("--id", type=int, required=True)
    create_bundle_parser.add_argument("--strategy", default="gpt_web_first")

    run_bundle_parser = subparsers.add_parser("run-bundle", help="글+이미지 세트를 article bundle 단위로 실행")
    run_bundle_target = run_bundle_parser.add_mutually_exclusive_group(required=False)
    run_bundle_target.add_argument("--id", type=int)
    run_bundle_target.add_argument("--bundle-id", type=int)
    run_bundle_parser.add_argument("--image-role", action="append", dest="image_roles")
    run_bundle_parser.add_argument("--image-spec", action="append", default=[], help="thumbnail=data/thumb.png 형식")
    run_bundle_parser.add_argument("--markdown-file")
    run_bundle_parser.add_argument("--simulate", action="store_true")
    run_bundle_parser.add_argument("--simulate-output-dir")
    run_bundle_parser.add_argument("--executor", choices=["playwright", "openai_compat", "codex_cli", "mock", "none"], default="codex_cli")
    run_bundle_parser.add_argument("--headed", action="store_true")
    run_bundle_parser.add_argument("--wait-for-ready-seconds", type=int, default=180)
    run_bundle_parser.add_argument("--response-timeout-seconds", type=int, default=300)
    run_bundle_parser.add_argument("--profile-root")
    run_bundle_parser.add_argument("--artifact-root")
    run_bundle_parser.add_argument("--cdp-url")
    run_bundle_parser.add_argument("--text-route", default="codex_cli")
    run_bundle_parser.add_argument("--text-profile", default="gpt_text_profile_dev")
    run_bundle_parser.add_argument("--text-model-label", default="codex-cli-text")
    run_bundle_parser.add_argument("--image-route", default="gpt_web_playwright")
    run_bundle_parser.add_argument("--image-profile", default="gpt_image_profile_dev")
    run_bundle_parser.add_argument("--image-model-label", default="gpt-web-image")
    run_bundle_parser.add_argument("--quality-score", type=float)
    run_bundle_parser.add_argument("--similarity-score", type=float)
    run_bundle_parser.add_argument("--image-fallback", choices=["none", "local_canvas"], default="none")

    probe_parser = subparsers.add_parser("probe-gpt-web", help="GPT 웹 로그인/준비 상태 점검")
    probe_parser.add_argument("--profile", default="gpt_text_profile_dev")
    probe_parser.add_argument("--headed", action="store_true")
    probe_parser.add_argument("--wait-for-ready-seconds", type=int, default=180)
    probe_parser.add_argument("--browser-channel", default="chrome")
    probe_parser.add_argument("--profile-root")
    probe_parser.add_argument("--artifact-root")
    probe_parser.add_argument("--cdp-url")

    api_probe_parser = subparsers.add_parser("probe-openai-compat", help="OpenAI 호환 API 연결/인증 점검")
    api_probe_parser.add_argument("--base-url")
    api_probe_parser.add_argument("--api-key")
    api_probe_parser.add_argument("--timeout-seconds", type=int, default=30)
    api_probe_parser.add_argument("--artifact-root")

    queue_text_parser = subparsers.add_parser("queue-text", help="텍스트 생성 job 큐잉")
    queue_text_parser.add_argument("--id", type=int)
    queue_text_parser.add_argument("--bundle-id", type=int)
    queue_text_parser.add_argument("--route", default="codex_cli")
    queue_text_parser.add_argument("--profile", default="gpt_text_profile_dev")
    queue_text_parser.add_argument("--model-label", default="codex-cli-text")

    queue_image_parser = subparsers.add_parser("queue-image", help="이미지 생성 job 큐잉")
    queue_image_parser.add_argument("--id", type=int)
    queue_image_parser.add_argument("--bundle-id", type=int)
    queue_image_parser.add_argument("--image-role", default="thumbnail")
    queue_image_parser.add_argument("--route", default="gpt_web_playwright")
    queue_image_parser.add_argument("--profile", default="gpt_image_profile_dev")
    queue_image_parser.add_argument("--model-label", default="gpt-web-image")

    start_job_parser = subparsers.add_parser("start-job", help="job 실행 시작 처리")
    start_job_parser.add_argument("--job-id", type=int, required=True)

    complete_text_parser = subparsers.add_parser("complete-text", help="텍스트 job 완료 처리")
    complete_text_parser.add_argument("--job-id", type=int, required=True)
    complete_text_parser.add_argument("--markdown-file", required=True)
    complete_text_parser.add_argument("--title")
    complete_text_parser.add_argument("--excerpt")
    complete_text_parser.add_argument("--quality-score", type=float)
    complete_text_parser.add_argument("--similarity-score", type=float)

    complete_image_parser = subparsers.add_parser("complete-image", help="이미지 job 완료 처리")
    complete_image_parser.add_argument("--job-id", type=int, required=True)
    complete_image_parser.add_argument("--image-role", required=True)
    complete_image_parser.add_argument("--prompt-text", required=True)
    complete_image_parser.add_argument("--file", required=True)
    complete_image_parser.add_argument("--mime", default="image/png")
    complete_image_parser.add_argument("--width", type=int)
    complete_image_parser.add_argument("--height", type=int)
    complete_image_parser.add_argument("--phash")

    fail_job_parser = subparsers.add_parser("fail-job", help="job 실패 처리")
    fail_job_parser.add_argument("--job-id", type=int, required=True)
    fail_job_parser.add_argument("--code", required=True)
    fail_job_parser.add_argument("--message", required=True)

    published_parser = subparsers.add_parser("mark-published", help="발행 완료 처리")
    published_parser.add_argument("--id", type=int, required=True)
    published_parser.add_argument("--url", required=True)

    subparsers.add_parser("stats", help="통계 보기")
    subparsers.add_parser("job-stats", help="job 통계 보기")
    return parser



def _parse_image_specs(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"image-spec 형식이 잘못됐습니다: {value}")
        role, file_path = value.split("=", 1)
        role = role.strip()
        file_path = file_path.strip()
        if not role or not file_path:
            raise ValueError(f"image-spec 형식이 잘못됐습니다: {value}")
        result[role] = file_path
    return result



def main(argv: list[str] | None = None) -> int:
    ensure_data_dir()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db(args.db)
        print(f"DB 초기화 완료: {args.db}")
        return 0

    if args.command == "migrate-v2":
        migrate_db(args.db)
        print(f"DB 마이그레이션 완료: {args.db}")
        return 0

    if args.command == "replenish":
        result = replenish_queue(args.db, min_queued=args.min_queued, variants_per_cluster=args.variants_per_cluster)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list":
        rows = list_variants(args.db, status=args.status, limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if args.command == "show-prompt":
        prompt = get_prompt(args.db, variant_id=args.id, slug=args.slug)
        if not prompt:
            print("대상을 찾지 못했습니다.")
            return 1
        print(json.dumps(prompt, ensure_ascii=False, indent=2))
        return 0

    if args.command == "export":
        count = export_prompts(args.db, args.output, status=args.status, limit=args.limit)
        print(f"{count}건 내보냈습니다: {args.output}")
        return 0

    if args.command == "create-bundle":
        result = create_bundle(args.db, variant_id=args.id, generation_strategy=args.strategy)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-bundle":
        result = run_bundle(
            args.db,
            variant_id=args.id,
            bundle_id=args.bundle_id,
            image_roles=args.image_roles,
            text_route=args.text_route,
            text_profile_name=args.text_profile,
            text_model_label=args.text_model_label,
            image_route=args.image_route,
            image_profile_name=args.image_profile,
            image_model_label=args.image_model_label,
            markdown_file=args.markdown_file,
            image_specs=_parse_image_specs(args.image_spec),
            simulate=args.simulate,
            simulate_output_dir=args.simulate_output_dir,
            quality_score=args.quality_score,
            similarity_score=args.similarity_score,
            executor_mode=args.executor,
            headed=args.headed,
            wait_for_ready_seconds=args.wait_for_ready_seconds,
            response_timeout_seconds=args.response_timeout_seconds,
            profile_root=args.profile_root,
            artifact_root=args.artifact_root,
            cdp_url=args.cdp_url,
            image_fallback=args.image_fallback,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "probe-gpt-web":
        try:
            result = probe_gpt_web(
                profile_name=args.profile,
                headed=args.headed,
                wait_for_ready_seconds=args.wait_for_ready_seconds,
                browser_channel=args.browser_channel,
                profile_root=args.profile_root,
                artifact_root=args.artifact_root,
                cdp_url=args.cdp_url,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except GptWebExecutionError as exc:
            print(
                json.dumps(
                    {
                        "ready": False,
                        "code": exc.code,
                        "message": str(exc),
                        "artifact_dir": exc.artifact_dir,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    if args.command == "probe-openai-compat":
        try:
            result = probe_openai_compat(
                base_url=args.base_url,
                api_key=args.api_key,
                timeout_seconds=args.timeout_seconds,
                artifact_root=args.artifact_root,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except OpenAICompatExecutionError as exc:
            print(
                json.dumps(
                    {
                        "ready": False,
                        "code": exc.code,
                        "message": str(exc),
                        "artifact_dir": exc.artifact_dir,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    if args.command == "queue-text":
        result = queue_text_job(
            args.db,
            variant_id=args.id,
            bundle_id=args.bundle_id,
            route=args.route,
            profile_name=args.profile,
            model_label=args.model_label,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "queue-image":
        result = queue_image_job(
            args.db,
            variant_id=args.id,
            bundle_id=args.bundle_id,
            image_role=args.image_role,
            route=args.route,
            profile_name=args.profile,
            model_label=args.model_label,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "start-job":
        start_job(args.db, args.job_id)
        print(f"job_id={args.job_id} 시작 처리 완료")
        return 0

    if args.command == "complete-text":
        markdown = Path(args.markdown_file).read_text(encoding="utf-8")
        result = complete_text_job(
            args.db,
            job_id=args.job_id,
            article_markdown=markdown,
            title=args.title,
            excerpt=args.excerpt,
            quality_score=args.quality_score,
            similarity_score=args.similarity_score,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "complete-image":
        result = complete_image_job(
            args.db,
            job_id=args.job_id,
            image_role=args.image_role,
            prompt_text=args.prompt_text,
            file_path=args.file,
            mime_type=args.mime,
            width=args.width,
            height=args.height,
            phash=args.phash,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "fail-job":
        fail_job(args.db, job_id=args.job_id, error_code=args.code, error_message=args.message)
        print(f"job_id={args.job_id} 실패 처리 완료")
        return 0

    if args.command == "mark-published":
        mark_published(args.db, args.id, args.url)
        print(f"variant_id={args.id} 발행 완료 처리")
        return 0

    if args.command == "stats":
        pprint(stats(args.db))
        return 0

    if args.command == "job-stats":
        pprint(job_stats(args.db))
        return 0

    parser.print_help()
    return 1
