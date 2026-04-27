# bunyang_longtail dev 세션 인계 메모

작성 시각: 2026-04-20 23:00 KST, 최종 갱신: 2026-04-21 12:02 KST

## 현재 목표
- 무료 웹 우선 경로로 `run-bundle`에서 본문 + 이미지 세트를 실제 생성
- ChatGPT 웹/OpenAI 이미지 생성이 실패하면 로컬 이미지로 완료 처리하지 않고 실패를 드러내게 유지

## 이미 끝난 작업
- `article_bundle` 중심 구조 정리 완료
- `run-bundle` 구현 완료
- `gpt_web.py` 실행기 추가 완료
- `openai_compat.py` fallback 추가 완료
- bundle 재개/재시도, attempt_no 증가 로직 추가 완료
- `CDP attach` 지원 추가 완료
  - `probe-gpt-web --cdp-url http://127.0.0.1:9222` 성공 확인
- 테스트 통과
  - 2026-04-21 기준 `26/26` 통과
- 2026-04-21 추가 보정 완료
  - CDP attach 시 기존 사용자 탭을 재사용하지 않고 새 탭으로 작업하도록 수정
  - 이미지 응답 감지를 `estuary/blob/data:image` 기반으로 강화
  - 기존 텍스트가 유지된 채 이미지가 추가되는 경우도 새 이미지로 판정하도록 보정
  - 게스트 홈/로그인 버튼/Cloudflare challenge 판정 보정
  - `local_canvas` 이미지 fallback은 제거됨
  - 명시적 local 렌더링용 `summary_card` 레이아웃 및 요약 문장 정규화 추가
  - `markdown_file` 사용 시 이미지 executor가 건너뛰는 버그 수정
- 2026-04-21 추가 운용 스크립트 구현
  - `scripts/gpt_web_session.py`: 터미널에서 `Xvfb + Chrome + CDP` 세션 시작/중지/상태확인
  - `scripts/gpt_web_session_daemon.py`: 세션 감시 데몬, 15초 주기 자동 복구
  - `scripts/run_bundle_cdp.sh`: 세션 자동 기동 후 `run-bundle` 실행 래퍼
  - 기본 전용 세션 값: display `:100`, port `9333`, profile `data/gpt_profiles/gpt_terminal_profile_dev`
  - 참고: `bunyang-gpt-web-session.service` 는 이후 레거시 상주형으로 분류됐고, 현재 기본 운용은 `run_bundle_cdp.sh` 온디맨드 시작/종료다.

## 현재까지 확인된 사실
### 1) ChatGPT 웹 세션
- 사람이 직접 연 브라우저에 remote debugging port `9222`로 attach 가능
- 아래 명령으로 열린 브라우저에 붙는 구조 확인
  - `google-chrome --remote-debugging-port=9222 --user-data-dir=/home/kj/app/bunyang_longtail/dev/data/gpt_profiles/gpt_text_profile_dev https://chatgpt.com/`
- 자동 프로필 재실행은 Cloudflare에 다시 막히지만, 이미 열린 사람 브라우저에는 CDP attach 가능

### 2) 텍스트 생성
- 일반 장문 테스트는 성공
  - 토마토 파스타 레시피 프롬프트에서 약 3742자 응답 확인
- 청약 프롬프트는 초기에 예고성 짧은 답만 줬음
- `build_text_prompt()`에 아래 규칙을 추가해 개선함
  - 계획/예고 문장 금지
  - 실시간 조회 가정 금지
  - 최신 수치는 공고문/청약홈 확인 안내로 처리
- 그 뒤 실제 청약 본문은 브라우저 상에서 약 6461자 완성본까지 확인됨
  - FAQ 포함
  - 체크리스트 포함
  - 마무리 결론 포함
- 추가 보정 완료
  - `_looks_like_complete_article()` 추가
  - timeout 직전 마지막 응답이 충분히 긴 완성본이면 성공으로 간주하도록 완화
- 즉, 텍스트는 현재 거의 해결된 상태로 봐도 됨

### 3) 이미지 생성
- 기존 실패 아티팩트 재분석 결과, timeout 스크린샷 안에 실제 생성 이미지가 보이는 케이스가 확인됨
- 즉, 이미지 자체가 전혀 안 나온 것이 아니라 아래 2가지 코드 문제가 겹쳤을 가능성이 큼
  1. CDP attach 시 사용자가 열어둔 기존 탭을 그대로 재사용해 이전 대화 문맥이 섞임
  2. 이미지 감지 로직이 텍스트 변화 중심이라, 기존 텍스트는 그대로이고 이미지 DOM만 새로 붙는 경우를 놓침
- 위 2가지는 2026-04-21 오전에 코드로 보정 완료
- 남은 실제 외부 검증 포인트는 `사람 브라우저 재attach 후 end-to-end 재실행` 1건

## 최신 실행 상태
### DB / 산출물 상태
- `data/cdp_probe5.sqlite3`
  - bundle 상태: `bundled` 3건
  - draft: 3건
  - image asset: 7건
  - queued variant: 0건
- 즉, 현재 dev 검증 DB 기준으로 큐에 있던 3개 variant는 모두 bundle 완료 상태입니다.

### 2026-04-21 추가 검증
- `python3 -m unittest tests.test_longtail -v` 실행
  - 결과: `26/26 OK`
- `python3 scripts/gpt_web_session.py start` 실행
  - 결과: display `:100`, port `9333`, profile `gpt_terminal_profile_dev` 세션 정상 기동
- `systemctl --user enable --now bunyang-gpt-web-session.service` 실행
  - 결과: 부팅 자동 시작 + 감시 데몬 활성화 완료
- `python3 scripts/gpt_web_session.py status` 실행
  - 결과: `xvfb_alive=true`, `chrome_alive=true`, `cdp_ready=true`
- Chrome 프로세스를 강제 종료 후 자동 복구 검증
  - 결과: 새 chrome pid로 자동 재기동 확인
- `python3 run.py probe-gpt-web --profile gpt_terminal_profile_dev --cdp-url http://127.0.0.1:9333 --wait-for-ready-seconds 20` 실행
  - 결과: `GPT_WEB_CHALLENGE` 또는 `GPT_WEB_LOGIN_REQUIRED` 환경이 반복 확인됨
  - artifact: `data/gpt_web_artifacts/probe_job_*`, `image_job_*`
- `./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role summary_card_v2` 실행
  - 결과: `playwright_complete`
  - 과거 처리: 웹 challenge를 `local_canvas_fallback`으로 성공 처리했으나, 현재는 실패로 드러내야 함
- `./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --id 2 --markdown-file data/generated_variant_2.md --image-role thumbnail --image-role summary_card` 실행
  - 최초 실행에서 `markdown_file` 분기 버그 발견, 수정 후 bundle `2` 성공
- `./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --id 3 --markdown-file data/generated_variant_3.md --image-role thumbnail --image-role summary_card` 실행
  - 결과: bundle `3` 성공
- 해석
  - 브라우저를 터미널만으로 상시 유지하는 방식은 구현 완료
  - 로그인/Cloudflare 검증이 안 풀려도 이미지 파이프라인 자체는 더 이상 막히지 않음
  - 진짜 GPT 생성 이미지를 쓰려면 별도 로그인 세션 준비는 여전히 필요

## 최근 중요 산출물/경로
- 텍스트 아티팩트
  - `/home/kj/app/bunyang_longtail/dev/data/gpt_web_artifacts/text_job_1/`
  - `/home/kj/app/bunyang_longtail/dev/data/gpt_web_artifacts/text_job_2/`
  - 브라우저상 완성본 최종 확인 대화 제목: `30대 맞벌이 1순위 조건`
- 이미지 아티팩트
  - `/home/kj/app/bunyang_longtail/dev/data/gpt_web_artifacts/image_job_2/`
  - `/home/kj/app/bunyang_longtail/dev/data/gpt_web_artifacts/image_job_3/`
- 테스트/검증용 DB
  - `data/cdp_probe.sqlite3`
  - `data/cdp_probe2.sqlite3`
  - `data/cdp_probe3.sqlite3`
  - `data/cdp_probe4.sqlite3`
  - `data/cdp_probe5.sqlite3`

## 재개 절차
1. 서비스 상태부터 확인
```bash
systemctl --user status bunyang-gpt-web-session.service --no-pager
python3 scripts/gpt_web_session.py status
```
2. 비정상이면 서비스 재시작
```bash
systemctl --user restart bunyang-gpt-web-session.service
```
3. 그 다음 아래 명령으로 ChatGPT 준비 여부 확인
```bash
python3 run.py probe-gpt-web --profile gpt_terminal_profile_dev --cdp-url http://127.0.0.1:9333 --wait-for-ready-seconds 20
```
4. 일반 실행은 아래 wrapper를 우선 사용
```bash
./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role thumbnail
```
5. 기대 결과
   - 재부팅 후에도 서비스가 자동 기동
   - Chrome/Xvfb 비정상 종료 시 15초 내 자동 복구
   - 새 탭에서 깨끗한 채팅 시작
   - 웹 이미지 생성 실패 시 이미지 asset 생성 없이 실패 처리
   - bundle 상태가 `bundled`로 전환
6. 실패 시 추가 확인 포인트
   - Cloudflare / 로그인 검증이 새 프로필 시작 시 다시 뜨는지
   - 실제 stop 버튼이 끝까지 사라지지 않는지
   - 이미지가 대화 중간에만 생기고 마지막 turn에는 안 붙는지
   - 모델/도구 선택 UI가 계정 상태에 따라 별도 필요한지

## 참고
- 현재 repo는 git repo가 아님. `git status` 불가 확인됨.
- 무료 웹 경로를 고집하면 CDP attach 방식이 가장 유력함.
- OpenAI 호환 API 경로는 이미 구현/검증 완료되어 있음. 단, 사용자 요구는 웹 우선이며 로컬 이미지 fallback은 금지.
- 사용자는 `200불짜리라 제한없음`이라고 언급함. 즉, 플랜 제한보다 UI/도구 호출 문제가 우선 의심됨.
- 현재 dev 기준 권장 기본 경로는 `run_bundle_cdp.sh`이며, 로컬 이미지 fallback은 사용하지 않는다.
