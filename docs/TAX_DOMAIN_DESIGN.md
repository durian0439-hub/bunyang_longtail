# 부동산 세금 롱테일 도메인 설계

작성일: 2026-04-25
상태: 설계 초안
대상 프로젝트: `bunyang_longtail`

## 1. 설계 결론

`tax` 도메인을 `cheongyak`, `auction`과 같은 1급 도메인으로 추가한다.

- `cheongyak`: 청약/분양 제도와 공고 기반 검색 의도
- `auction`: 경매 입찰/권리/명도/물건 검색 의도
- `tax`: 부동산 취득·보유·양도·증여·상속·임대·경매/분양 세금 검색 의도

기존 청약 도메인의 `세금` family는 청약 문맥의 보조 주제로 유지한다. 새 `tax` 도메인은 부동산 세금 자체를 검색하는 장기 롱테일 자산으로 분리한다.

## 2. 도메인 원칙

### 2.1 중복 방지

- `domain + semantic_key` 기준으로 분리한다.
- `취득세`, `양도세`처럼 broad keyword가 청약/경매와 겹쳐도 도메인이 다르면 차단하지 않는다.
- 동일 `tax` 도메인 안에서는 아래 기준으로 차단한다.
  - 같은 세목
  - 같은 대상자
  - 같은 시점
  - 같은 질문/검색 의도

### 2.2 글 톤

- 세무 상담처럼 단정하지 않는다.
- 세법·세율·감면 조건은 바뀔 수 있다는 전제를 자연스럽게 둔다.
- 독자가 해야 할 행동은 “계산 예시 → 공식 확인 → 필요 시 세무사 상담” 순서로 안내한다.
- 절세는 “합법적 체크” 수준으로만 다룬다.
- 탈세, 명의신탁, 허위 계약, 편법 증여를 부추기는 표현은 금지한다.

### 2.3 공식 확인 출처

`policy_json.keyword_sources` 기본값:

- Google autocomplete
- Naver autocomplete
- Bing OSJSON
- Daum suggest
- 국세청 / 홈택스
- 위택스
- 행정안전부 지방세 안내
- 법제처 국가법령정보센터
- 정부24

## 3. Tax family 설계

### 3.1 세금기초

목적: 세금 이름과 발생 시점 자체를 이해시키는 입문형 주제.

대표 키워드:

- 부동산 세금
- 주택 세금
- 취득세
- 재산세
- 종합부동산세
- 양도소득세
- 종합소득세
- 과세표준
- 공시가격
- 기준시가
- 실거래가

기본 audience:

- 부동산 세금 초보
- 첫 주택 매수자
- 무주택 실수요자
- 30대 맞벌이
- 신혼부부
- 1주택 갈아타기 준비자

### 3.2 취득세

목적: 매수·분양·경매 낙찰 시점의 초기 세금 검색 의도 대응.

대표 키워드:

- 주택 취득세
- 아파트 취득세
- 분양권 취득세
- 입주권 취득세
- 오피스텔 취득세
- 상가 취득세
- 토지 취득세
- 취득세 중과
- 일시적 2주택 취득세
- 생애최초 취득세 감면
- 농어촌특별세
- 지방교육세

### 3.3 보유세

목적: 집을 보유하는 동안 매년 나오는 세금 검색 의도 대응.

대표 키워드:

- 재산세
- 종합부동산세
- 보유세
- 과세기준일 6월 1일
- 공정시장가액비율
- 1세대 1주택 종부세
- 공동명의 종부세
- 재산세 납부기간
- 종부세 납부기간
- 고령자 장기보유 공제

### 3.4 양도세

목적: 매도 전 가장 많이 검색하는 양도소득세 판단 의도 대응.

대표 키워드:

- 양도소득세
- 양도세 계산
- 1세대 1주택 비과세
- 일시적 2주택 양도세
- 장기보유특별공제
- 조정대상지역 양도세
- 분양권 양도세
- 입주권 양도세
- 필요경비
- 양도세 신고기한
- 양도세 예정신고

### 3.5 분양권·입주권 세금

목적: 기존 청약 방문자와 자연스럽게 연결되는 세금 롱테일.

대표 키워드:

- 분양권 세금
- 분양권 전매 양도세
- 분양권 주택수 포함
- 입주권 세금
- 입주권 양도세
- 입주권 취득세
- 재개발 입주권 세금
- 재건축 입주권 세금
- 옵션비 취득세
- 발코니 확장 세금

### 3.6 경매 세금

목적: 경매 도메인과 연결되는 낙찰 후 비용/세금 검색 의도 대응.

대표 키워드:

- 경매 취득세
- 경매 낙찰 취득세
- 경매 법무비용
- 경매 등록면허세
- 경매 잔금 세금
- 낙찰 후 세금
- 경매 양도세
- 경매 수리비 필요경비
- 경매 체납세금
- 경매 배당 세금

### 3.7 임대소득 세금

목적: 보유 후 임대수익을 생각하는 독자 대응.

대표 키워드:

- 주택임대소득세
- 월세 소득세
- 전세보증금 간주임대료
- 임대소득 분리과세
- 종합소득세 주택임대
- 임대사업자 세금
- 주택임대사업자 등록
- 부가가치세 상가임대
- 오피스텔 임대소득세

### 3.8 증여·상속

목적: 가족 간 이전, 부모 자녀 명의, 상속주택 관련 장기 검색 대응.

대표 키워드:

- 부동산 증여세
- 자녀 주택 증여
- 부담부증여
- 부부 증여
- 증여 취득세
- 자금출처조사
- 부동산 상속세
- 상속주택 양도세
- 상속주택 취득세
- 부모 자녀 매매 세금

### 3.9 공동명의·가족명의

목적: 30대 맞벌이·신혼부부·가족 명의 검색 의도 대응.

대표 키워드:

- 부부 공동명의 세금
- 공동명의 종부세
- 공동명의 양도세
- 공동명의 취득세
- 배우자 증여세
- 자녀 명의 부동산
- 세대분리 세금
- 가족 간 매매 세금

### 3.10 물건유형별 세금

목적: 아파트 외 부동산 유형별 검색 의도 대응.

대표 키워드:

- 아파트 세금
- 빌라 세금
- 오피스텔 세금
- 상가 세금
- 토지 세금
- 농지 세금
- 다가구주택 세금
- 다세대주택 세금
- 생활형숙박시설 세금

### 3.11 신고·납부 실무

목적: 계산 후 실제 신고/납부 행동까지 이어지는 검색 의도 대응.

대표 키워드:

- 홈택스 양도세 신고
- 위택스 취득세 납부
- 재산세 납부 방법
- 종부세 납부 방법
- 양도세 신고 서류
- 취득세 신고 서류
- 세무사 상담 전 준비
- 부동산 세금 계산기
- 가산세
- 무신고 가산세

## 4. 공통 조합축

### 4.1 audience

- 부동산 세금 초보
- 첫 주택 매수자
- 무주택 실수요자
- 30대 맞벌이
- 신혼부부
- 1주택 갈아타기 준비자
- 다주택 정리 예정자
- 분양권 보유자
- 입주권 보유자
- 경매 낙찰 예정자
- 임대수익 검토자
- 부모님 집 상속 예정자
- 공동명의 고민 부부
- 오피스텔 매수자
- 상가 투자 검토자
- 토지 투자 검토자

### 4.2 intent

- 조건정리
- 계산
- 가능여부
- 비교
- 실수방지
- 체크리스트
- 신고방법
- 사례
- FAQ

### 4.3 scenario

- 매수 전에
- 계약 직전에
- 잔금 전에
- 입주 직전에
- 보유 중에
- 매도 전에
- 갈아타기할 때
- 일시적 2주택이 될 때
- 공동명의를 고민할 때
- 임대를 놓기 전에
- 경매 낙찰 후
- 분양권 전매 전에
- 입주권을 받을 때
- 상속을 앞두고
- 증여를 고민할 때
- 종합소득세 신고 전에
- 6월 1일 전에
- 세금 고지서를 받았을 때

## 5. 제목 생성 규칙

`tax` 도메인은 청약의 “탈락”이나 경매의 “입찰” 프레임을 쓰지 않는다.

기본 패턴:

- 판단형: `{topic_scene}, {audience}가 먼저 볼 기준`
- 계산형: `{topic_scene}, 실제 세금 계산 순서`
- 비교형: `{primary}와 {comparison}, 어떤 세금이 더 부담될까`
- 실수방지형: `{topic_scene}, 신고 전에 많이 놓치는 부분`
- 체크리스트형: `{topic_scene}, 신고 전 체크리스트`
- 사례형: `{topic_scene}, 사례로 보는 세금 흐름`
- FAQ형: `{primary} FAQ, {audience}가 가장 헷갈리는 질문`

금지:

- 무조건 절세
- 세금 안 내는 법
- 100% 비과세
- 세무조사 피하는 법
- 확정 세율 단정형 제목

## 6. Tax outline

기존 고정 섹션은 유지하되, 각 섹션의 의미를 세금 문맥으로 바꾼다.

1. 상단 요약
   - 세금이 언제 발생하는지
   - 지금 독자가 먼저 확인할 3가지
2. 이 글에서 바로 답하는 질문
   - 어떤 세금인지
   - 누가/언제 부담하는지
3. 핵심 조건 정리
   - 과세 대상
   - 계산 기준
   - 주택 수/보유기간/거주기간/명의 등 핵심 변수
4. 헷갈리기 쉬운 예외
   - 감면, 중과, 비과세, 신고기한, 가산세
5. 실전 예시 시나리오
   - 독자 상황 기반 계산 흐름
6. 체크리스트
   - 계약/잔금/매도/신고 전 확인 항목
7. FAQ
   - 실제 검색 질문 6개 이상
8. 마무리 결론
   - 계산기 확인, 공식 안내 확인, 필요 시 세무사 상담

## 7. Prompt guard

### 반드시 포함

- 세법은 시점과 조건에 따라 달라질 수 있음
- 국세청/홈택스/위택스/지자체 안내로 최종 확인
- 금액 예시는 단순 계산 예시
- 취득가액, 보유기간, 거주기간, 주택 수, 명의, 지역, 취득/양도 시점 확인

### 금지

- 세율·감면·비과세 확정 단정
- 불법 절세, 명의신탁, 허위 계약 권유
- 세무사 상담을 대체한다고 보이는 문장
- 수익 보장 또는 투자 권유
- 오래된 정책을 현재 기준처럼 단정

## 8. 코드 반영 지점

### 8.1 `src/bunyang_longtail/catalog.py`

- `TAX_DOMAIN = "tax"` 추가
- `SUPPORTED_DOMAINS = (DEFAULT_DOMAIN, AUCTION_DOMAIN, TAX_DOMAIN)`로 확장
- `TAX_FAMILY_PRESETS` 추가
- `TAX_TOPIC_BLUEPRINTS` 추가
- `DOMAIN_FAMILY_PRESETS[TAX_DOMAIN]` 추가
- `DOMAIN_TOPIC_BLUEPRINTS[TAX_DOMAIN]` 추가

### 8.2 `src/bunyang_longtail/planner.py`

- `TAX_DOMAIN` import
- `_build_tax_outline(cluster)` 추가
- `_compose_tax_title(cluster, angle)` 추가
- `_compose_title()`에서 tax 분기 추가
- `policy_json.keyword_sources`에 tax 공식 출처 추가
- `_estimate_seo_score()`는 세금 제목 길이와 계산/신고/FAQ 키워드 보너스 반영

### 8.3 `src/bunyang_longtail/prompt_builder.py`

- `TAX_DOMAIN` import
- tax 전용 system prompt 추가
- tax 전용 writing_rules / quality_gates / style_targets 추가

### 8.4 `src/bunyang_longtail/workers.py`

- simulate/mock article에서 tax 분기 추가
- tax excerpt를 “세금 발생 시점·계산 기준·공식 확인 순서” 중심으로 생성

### 8.5 `src/bunyang_longtail/naver_bundle_publish.py`

- `_content_domain()` explicit domain에 `tax` 추가
- 세금 키워드 기반 domain 추론 추가
- `_topic_kind()`에 `tax` 추가
- `TOPIC_PUBLISH_HEADING_OVERRIDES["tax"]` 추가
- `TOPIC_COLORS["tax"]`, `THUMBNAIL_CHIPS["tax"]` 추가
- `TAX_DEFAULT_TAGS` 추가
- `RELATED_CATEGORY_LABELS["tax"] = "How To 세금"` 추가
- tax lead block 추가
- tax 최종 고지 문구 추가
- 강조 키워드에 취득세/양도세/재산세/종부세/증여세/상속세/홈택스/위택스 추가

### 8.6 `scripts/run_longtail_publish_prod.sh`

- `DOMAIN_CONFIGS`에 tax 추가
- 기본값:
  - `domain`: `tax`
  - `label`: `부동산 세금 롱테일`
  - `category_no`: `LONGTAIL_TAX_NAVER_CATEGORY_NO` 환경변수
  - `category_name`: `LONGTAIL_TAX_NAVER_CATEGORY_NAME`, 기본 `How To 세금`
- 기존 loop 구조를 유지해 cheongyak/auction 실패와 무관하게 tax도 실행되게 한다.

### 8.7 테스트

`tests/test_longtail.py`

- `test_replenish_tax_domain_generates_tax_prompts`
- `test_tax_domain_candidate_selection_scoped_by_domain`
- `test_tax_title_does_not_use_auction_or_cheongyak_frame`

`tests/test_naver_bundle_publish.py`

- tax category label/related link 테스트
- tax tags 테스트
- tax content domain 테스트
- tax publish markdown 고지문 테스트

## 9. 발행 카테고리

권장 기본 카테고리명:

- `How To 세금`

카테고리 번호는 네이버 실제 카테고리 생성 후 환경변수로 주입한다.

- `LONGTAIL_TAX_NAVER_CATEGORY_NO`
- `LONGTAIL_TAX_NAVER_CATEGORY_NAME=How To 세금`

## 10. 초기 replenish 규모 예상

초기 설계는 family 11개, blueprint 120~180개를 목표로 한다.

예상 조합:

- 120 blueprint × 평균 audience 5개 × intent 4개 × scenario 5개 = 12,000 cluster 후보
- 180 blueprint 기준 약 18,000 cluster 후보
- variant 3개 기준 36,000~54,000 발행 후보

경매 도메인처럼 “넓은 키워드 풀 + domain salt + 우선순위 분산” 구조로 운영한다.

## 11. 구현 순서

1. 설계 확정
2. tax family/blueprint 1차 코드 반영
3. replenish/list/prompt 단위 테스트
4. publish markdown/tags/category 테스트
5. mock bundle 생성 테스트
6. dev DB에서 `replenish --domain tax --min-queued 30 --variants-per-cluster 3` 검증
7. 발행 전 categoryNo 확인
8. main 반영 후 prod sync

## 12. 운영 판단 기준

당일 조회수보다 아래 지표로 판단한다.

- 30일 누적 조회수
- 같은 family 내 상위 키워드 반복 노출
- 단지글/경매글에서 내부링크 클릭 가능성
- 발행 후 2~4주 뒤 검색 유입 유지 여부
- 제목이 질문/상황/판단 기준을 충분히 담는지

