# AGENTS.md

이 폴더는 `/home/kj/app/bunyang_longtail/AGENTS.md` 의 설계 규칙을 그대로 따른다.

추가 로컬 원칙:
- 이 폴더에서만 개발한다.
- 모든 변경 후 테스트를 실행한다.
- DB 스키마, 상태모델, prompt 구조 변경은 설계 변경으로 취급한다.
- 글 생성/이미지 생성 워커는 기본적으로 GPT 웹 + Playwright/MCP 경로를 우선 설계한다.
- `run.py`, `src/`, `tests/`, `data/` 구조를 임의로 흐리지 않는다.

루트 AGENTS를 먼저 읽고 그 원칙을 우선 적용한다.
