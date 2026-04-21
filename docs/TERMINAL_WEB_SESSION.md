# 터미널 전용 GPT 웹 세션 운용

목표: API 없이 `Xvfb + Chrome + CDP` 조합으로 ChatGPT 웹 세션을 서버에서 계속 유지하고, `run-bundle`이 그 세션에 붙어 작업하도록 한다.

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
systemctl --user status bunyang-gpt-web-session.service --no-pager
```

확인 항목:
- Xvfb PID 생존 여부
- Chrome PID 생존 여부
- CDP 준비 여부
- 현재 열린 탭 title/url
- systemd 감시 데몬 활성 여부

## 3. 세션 중지 / 재시작
```bash
python3 scripts/gpt_web_session.py stop
python3 scripts/gpt_web_session.py restart
systemctl --user restart bunyang-gpt-web-session.service
```

## 4. run-bundle 바로 실행
아래 래퍼는 세션을 먼저 올리고 같은 프로필로 `run-bundle`을 실행한다.

```bash
./scripts/run_bundle_cdp.sh --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --image-role thumbnail --wait-for-ready-seconds 20 --response-timeout-seconds 600
```

실제로 내부에서 추가되는 옵션:
- `--executor playwright`
- `--cdp-url http://127.0.0.1:9333`
- `--text-profile <same profile>`
- `--image-profile <same profile>`

## 5. 부팅 자동 시작
아래 user service를 설치하고 활성화해 두었습니다.
- 서비스 파일: `~/.config/systemd/user/bunyang-gpt-web-session.service`
- 서비스명: `bunyang-gpt-web-session.service`

주요 동작:
- 부팅 후 자동 시작
- 세션 상태 15초 주기 점검
- Chrome/Xvfb 비정상 종료 시 자동 재기동

확인 명령:
```bash
systemctl --user status bunyang-gpt-web-session.service --no-pager
journalctl --user -u bunyang-gpt-web-session.service -n 50 --no-pager
```

## 6. 중요한 운영 원칙
- 브라우저 프로세스를 계속 살려두면 사용자가 매번 직접 창을 열 필요는 없다.
- 다만 아래 경우에는 로그인/검증이 다시 필요할 수 있다.
  - Chrome 프로세스를 종료한 경우
  - 서버 재부팅 후 세션이 초기화된 경우
  - ChatGPT 로그인 세션이 만료된 경우
  - Cloudflare 검증이 다시 뜬 경우
- 따라서 가장 실용적인 방식은 아래 둘 중 하나다.
  1. `gpt_web_session.py start`로 전용 세션을 장시간 유지
  2. 필요 시 `restart` 후 같은 프로필로 재사용

## 7. 현재 bunyang_longtail 권장 실행 흐름
```bash
cd /home/kj/app/bunyang_longtail/dev
python3 scripts/gpt_web_session.py start
python3 run.py --db data/cdp_probe5.sqlite3 run-bundle --bundle-id 1 --executor playwright --cdp-url http://127.0.0.1:9333 --text-profile data/gpt_profiles/gpt_terminal_profile_dev --image-profile data/gpt_profiles/gpt_terminal_profile_dev --image-role thumbnail --wait-for-ready-seconds 20 --response-timeout-seconds 600
```

## 8. 현재 한계
- 새로 띄운 프로필에서 Cloudflare challenge가 다시 걸리면 완전 무인화는 보장되지 않는다.
- 하지만 브라우저 세션을 계속 살아 있게 유지하면, 매번 사용자가 직접 브라우저를 열 필요는 없어진다.
