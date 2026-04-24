# 터미널 전용 GPT 웹 세션 운용

목표: API 없이 `Xvfb + Chrome + CDP` 조합으로 ChatGPT 웹 세션을 **필요한 동안만** 유지하고, `run-bundle` 종료 시 세션까지 함께 정리되도록 한다.

## 1. 세션 시작
```bash
cd /home/kj/app/bunyang_longtail/dev
python3 scripts/gpt_web_session.py start
```

기본값:
- display: `:100`
- cdp port: `9333`
- profile: `data/gpt_profiles/gpt_terminal_profile_dev`
- url: `https://chatgpt.com/`

세션이 올라오면 아래 상태 파일과 로그가 생긴다.
- 상태: `data/browser_runtime/session_state.json`
- Xvfb 로그: `data/browser_runtime/xvfb.log`
- Chrome 로그: `data/browser_runtime/chrome.log`

## 2. 상태 확인
```bash
python3 scripts/gpt_web_session.py status
```

확인 항목:
- Xvfb PID 생존 여부
- Chrome PID 생존 여부
- CDP 준비 여부
- 현재 열린 탭 title/url

## 3. 세션 중지 / 재시작
```bash
python3 scripts/gpt_web_session.py stop
python3 scripts/gpt_web_session.py restart
```

## 4. run-bundle 바로 실행
아래 래퍼는 세션을 먼저 올리고 같은 프로필로 `run-bundle`을 실행한 뒤, 기본적으로 세션까지 자동 종료한다.

```bash
./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role thumbnail --wait-for-ready-seconds 20 --response-timeout-seconds 600
```

실제로 내부에서 추가되는 옵션:
- `--executor playwright`
- `--cdp-url http://127.0.0.1:9333`
- `--text-profile <same profile>`
- `--image-profile <same profile>`

기본 동작:
- 시작 시 세션 자동 기동
- 종료 시 세션 자동 정리
- 장시간 디버깅이 꼭 필요할 때만 `GPT_WEB_KEEP_SESSION=1` 로 유지 가능

## 5. 레거시 상주 서비스
아래 user service는 과거 상주형 운용을 위해 만들었던 레거시 옵션입니다.
- 서비스 파일: `~/.config/systemd/user/bunyang-gpt-web-session.service`
- 서비스명: `bunyang-gpt-web-session.service`

현재 권장 경로는 아닙니다.
- 기본값은 `run_bundle_cdp.sh` 온디맨드 실행
- 상주 서비스는 메모리/브라우저 잔여세션 관점에서 비권장
- 필요 시에만 수동으로 켜고, 끝나면 반드시 내립니다.

## 6. 중요한 운영 원칙
- 기본 원칙은 작업 시작 시 세션 기동, 작업 종료 시 세션 정리입니다.
- 브라우저를 오래 살려두면 편할 수 있지만, 메모리와 Chrome 잔여세션 누적 위험이 커집니다.
- 아래 경우에는 로그인/검증이 다시 필요할 수 있습니다.
  - Chrome 프로세스를 종료한 경우
  - 서버 재부팅 후 세션이 초기화된 경우
  - ChatGPT 로그인 세션이 만료된 경우
  - Cloudflare 검증이 다시 뜬 경우
- 따라서 가장 실용적인 방식은 아래 둘입니다.
  1. 기본은 `run_bundle_cdp.sh` 온디맨드 실행
  2. 꼭 필요할 때만 `GPT_WEB_KEEP_SESSION=1` 로 잠시 유지 후 수동 정리

## 7. 현재 bunyang_longtail 권장 실행 흐름
```bash
cd /home/kj/app/bunyang_longtail/dev
./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role thumbnail --wait-for-ready-seconds 20 --response-timeout-seconds 600
```

## 8. 현재 한계
- 새로 띄운 프로필에서 Cloudflare challenge가 다시 걸리면 완전 무인화는 보장되지 않는다.
- 따라서 상주형 유지보다, 필요한 때만 올리고 끝나면 내리는 쪽이 현재 운영 원칙에 더 맞다.
