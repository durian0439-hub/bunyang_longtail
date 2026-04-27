# bunyang_longtail (dev)

청약 A to Z 롱테일 주제/변형/프롬프트 관리용 개발 프로젝트입니다.

## 목표
- 청약 롱테일 주제를 DB에서 지속 관리
- 동일 의미 클러스터를 여러 서술 각도로 재생산
- 네이버 SEO형 제목/아웃라인/프롬프트를 자동 생성
- 발행 전까지 variant 기준 `queued -> drafting -> drafted -> published`, bundle 기준 `queued -> drafting_text -> rendering_image -> bundled -> published` 상태 추적

## 현재 포함 범위
- SQLite 기반 DB 스키마
- 무한 확장 가능한 주제 클러스터 생성기
- 네이버 SEO형 제목/아웃라인/프롬프트 패키지 생성기
- 기본 CLI
- 기본 테스트
- v2 설계 문서 초안

## 설계 문서
- 아키텍처 v2: `docs/ARCHITECTURE_V2.md`
- DB 스키마 v2 초안: `docs/schema_v2.sql`

## 빠른 시작
```bash
cd /home/kj/app/bunyang_longtail/dev
python3 run.py init-db
python3 run.py replenish --min-queued 300 --variants-per-cluster 4
python3 run.py stats
python3 run.py list --status queued --limit 20
python3 run.py show-prompt --id 1
```

## 기본 DB 경로
- `data/longtail.sqlite3`

## 주요 명령
```bash
python3 run.py init-db
python3 run.py migrate-v2
python3 run.py replenish --min-queued 500 --variants-per-cluster 4
python3 run.py list --status queued --limit 50
python3 run.py export --status queued --limit 100 --output data/queued_prompts.jsonl
python3 run.py show-prompt --id 1
python3 run.py create-bundle --id 97
python3 run.py run-bundle --id 97
python3 run.py run-bundle --id 97 --executor mock --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --id 97 --executor openai_compat --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --id 97 --markdown-file data/article.md --image-spec thumbnail=data/thumb.png
python3 run.py run-bundle --id 97 --simulate --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --id 97 --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --bundle-id 1 --executor mock --image-role thumbnail --image-role summary_card
python3 run.py probe-gpt-web --headed --profile gpt_text_profile_dev
python3 run.py probe-openai-compat
python3 run.py queue-text --id 97
python3 run.py queue-image --bundle-id 1 --image-role thumbnail
python3 run.py start-job --job-id 1
python3 run.py complete-text --job-id 1 --markdown-file data/article.md
python3 run.py complete-image --job-id 2 --image-role thumbnail --prompt-text "썸네일 프롬프트" --file data/thumb.png --width 1080 --height 1080
python3 run.py fail-job --job-id 3 --code RATE_LIMIT --message "GPT 웹 응답 지연"
python3 run.py mark-published --id 1 --url "https://blog.naver.com/example/123"
python3 run.py job-stats
python3 run.py stats
python3 scripts/cleanup_published_media.py --db data/cdp_probe5.sqlite3 --output-base data/naver_publish/cron_runs --days 3 --dry-run
```

## 발행 산출물 보관 정책
- 네이버 블로그와 영상 발행이 모두 완료된 번들만 정리 대상입니다.
- 생성 후 3일이 지난 이미지/영상 파일만 삭제하고, DB 행·발행 이력·JSON 메타데이터는 보존합니다.
- 운영 크론은 `LONGTAIL_MEDIA_CLEANUP_ENABLED=1`, `LONGTAIL_MEDIA_RETENTION_DAYS=3` 기본값으로 로컬·OCI 동일하게 후처리 정리를 수행합니다.

## 설계 포인트
- `topic_cluster`: 동일 의미 주제 클러스터
- `topic_variant`: 같은 의미를 다른 훅/각도/제목으로 푼 변형안
- `article_bundle`: 사용자 관점의 글 1개 단위 묶음(본문 + 이미지 세트)
- `article_draft`: 실제 작성/발행 이력
- v2에서는 `generation_job`, `image_asset`, `publish_history`, `performance_feedback` 를 추가해 bundle 내부 워커를 추적합니다.
- 중복 방지 단위는 `semantic_key`(클러스터), `variant_key`(표현형), `content_hash`(본문)
- 네이버 SEO 규칙은 제목 선두 키워드, 검색의도 중심 소제목, FAQ/체크리스트 포함을 기본값으로 둡니다.
- 글/이미지 생성 워커는 GPT 웹 + Playwright 우선 경로로 시작했지만, 현재는 OpenAI 호환 API fallback도 지원합니다.

## GPT 웹 준비
- 기본 실행 경로는 `run-bundle` + `--executor playwright` 입니다. 기본값도 `playwright` 입니다.
- 처음에는 로그인/Cloudflare 검증 때문에 아래 명령으로 dev 전용 프로필을 먼저 준비해야 합니다.

```bash
cd /home/kj/app/bunyang_longtail/dev
python3 run.py probe-gpt-web --headed --profile gpt_text_profile_dev
python3 run.py probe-gpt-web --headed --profile gpt_image_profile_dev
```

- 프로필은 `data/gpt_profiles/<profile_name>` 아래에 저장됩니다.
- 실행 중 스크린샷/HTML 아티팩트는 `data/gpt_web_artifacts/` 아래에 저장됩니다.
- GUI 없는 서버에서 `--headed`를 쓰면 X 서버가 없어 실패할 수 있습니다. 이 경우 `xvfb-run -a`로 감싸서 실행합니다.

```bash
xvfb-run -a python3 run.py probe-gpt-web --headed --profile gpt_text_profile_dev
```

- 매번 사용자가 브라우저를 직접 열지 않게 하려면, 전용 세션 관리 스크립트로 `Xvfb + Chrome + CDP` 세션을 **필요할 때만** 올렸다가 작업 종료 시 내리는 방식을 기본값으로 사용합니다. 기존 `gpt_text_profile_dev`와 충돌하지 않도록 기본적으로 `gpt_terminal_profile_dev`를 사용합니다.

```bash
python3 scripts/gpt_web_session.py start
python3 scripts/gpt_web_session.py status
python3 scripts/gpt_web_session.py stop
./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role thumbnail
```

- `run_bundle_cdp.sh` 는 시작 시 세션을 올리고, 종료 시 기본으로 세션을 자동 정리합니다.
- `run_bundle_cdp.sh` 는 `run-bundle` 호출 시 기본으로 아래 값을 자동 보강합니다.
  - `--wait-for-ready-seconds 60`
  - `--response-timeout-seconds 600`
- 필요하면 명시 인자로 덮어쓸 수 있습니다.
- 장시간 디버깅이 꼭 필요할 때만 `GPT_WEB_KEEP_SESSION=1 ./scripts/run_bundle_cdp.sh ...` 로 세션 유지가 가능합니다.
- `bunyang-gpt-web-session.service` 는 기본 경로가 아니라 레거시 상주형 옵션입니다.
- 자세한 운영 방식은 `docs/TERMINAL_WEB_SESSION.md` 를 참고합니다.
- Cloudflare 검증이 남아 있으면 `probe-gpt-web` 은 아래 코드로 실패합니다.
  - `GPT_WEB_CHALLENGE`
- 로그인 세션이 없으면 아래 코드로 실패합니다.
  - `GPT_WEB_LOGIN_REQUIRED`
- X 서버가 없으면 아래 코드로 실패합니다.
  - `GPT_WEB_XSERVER_MISSING`

## OpenAI 호환 API 준비
- GPT 웹이 Cloudflare에 막히면 `--executor openai_compat` 경로를 사용할 수 있습니다.
- 기본 환경변수는 아래 순서로 읽습니다.
  - API 키: `OPENAI_COMPAT_API_KEY` 또는 `OPENAI_API_KEY`
  - Base URL: `OPENAI_COMPAT_BASE_URL` (기본값 `https://api.openai.com/v1`)
  - 텍스트 모델: `OPENAI_COMPAT_TEXT_MODEL` (기본값 `gpt-4.1-mini`)
  - 이미지 모델: `OPENAI_COMPAT_IMAGE_MODEL` (기본값 `gpt-image-1`)
- 연결 점검:

```bash
export OPENAI_API_KEY=... 
python3 run.py probe-openai-compat
```

- 호출 아티팩트는 `data/openai_compat_artifacts/` 아래에 저장됩니다.

## run-bundle 실행 모드
- `--executor playwright`: 실제 GPT 웹 실행
- `--executor openai_compat`: OpenAI 호환 Chat Completions + Images API 실행
- `--executor mock`: 실제 외부 호출 없이 본문/이미지를 모의 생성해 bundle 상태 전이 검증
- `--executor none`: 텍스트 job만 큐잉하고 시작한 뒤 즉시 반환
- `--simulate`: 완전 시뮬레이션 모드, 본문 마크다운과 PNG를 로컬에서 생성
- 이미지 생성 실패 시 로컬 카드로 대체하지 않고 job을 실패 처리합니다.
- 같은 `bundle_id`로 다시 실행하면 기존 초안/성공한 이미지 역할을 재사용하고, 아직 비어 있는 역할만 이어서 생성합니다.
- 같은 bundle 내부 재시도 시 `generation_job.attempt_no`는 자동 증가합니다.

실행 예시:
```bash
python3 run.py run-bundle --id 97 --executor mock --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --id 97 --executor openai_compat --image-role thumbnail --image-role summary_card
python3 run.py run-bundle --id 97 --headed --wait-for-ready-seconds 180 --response-timeout-seconds 300
python3 run.py run-bundle --id 97 --executor none
python3 run.py run-bundle --bundle-id 1 --executor mock --image-role thumbnail --image-role summary_card
```

## 테스트
```bash
cd /home/kj/app/bunyang_longtail/dev
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
