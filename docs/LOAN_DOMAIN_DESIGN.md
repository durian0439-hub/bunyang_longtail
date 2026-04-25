# 부동산 대출 롱테일 도메인 설계

## 목표

`bunyang_longtail`에 부동산 대출 도메인을 `loan`으로 추가한다. 기존 `tax` 도메인처럼 분양·경매와 독립된 1급 도메인으로 운영하되, 글 생성과 GPT 이미지 생성 파이프라인은 새로 만들지 않고 도메인별 키워드·카테고리·품질 가드만 바꿔 재사용한다.

## 운영 카테고리

- 네이버 카테고리명: `부동산 대출`
- 네이버 categoryNo: `19`
- 환경변수 기본값
  - `LONGTAIL_LOAN_NAVER_CATEGORY_NO=19`
  - `LONGTAIL_LOAN_NAVER_CATEGORY_NAME=부동산 대출`

## 크론 실행 순서

기존 순서를 유지하고 대출을 마지막에 추가한다.

1. `cheongyak` — 분양 롱테일
2. `auction` — 경매 롱테일
3. `tax` — 세금 롱테일
4. `loan` — 대출 롱테일

각 도메인은 독립 실행한다. 앞 도메인이 실패해도 다음 도메인은 계속 실행하고, 최종 종료 코드는 실패 도메인을 취합해 마지막에 판단한다.

## 구현 원칙

### 1. 도메인 추가는 설정 중심으로 한다

신규 상수:

```python
LOAN_DOMAIN = "loan"
SUPPORTED_DOMAINS = (DEFAULT_DOMAIN, AUCTION_DOMAIN, TAX_DOMAIN, LOAN_DOMAIN)
```

추가 매핑:

```python
DOMAIN_FAMILY_PRESETS[LOAN_DOMAIN] = LOAN_FAMILY_PRESETS
DOMAIN_TOPIC_BLUEPRINTS[LOAN_DOMAIN] = LOAN_TOPIC_BLUEPRINTS
```

`semantic_key`는 기존 정책대로 `domain + primary + audience + intent + scenario` salt를 넣는다. 따라서 청약·경매·세금 키워드와 겹쳐도 대출 도메인 후보는 별도로 쌓인다.

### 2. 글 생성 로직은 공용화한다

대출 글은 `prompt_builder.py`에 `domain == LOAN_DOMAIN` 분기만 추가한다.

- 제목 생성, outline 생성, candidate/replenish, bundle 생성은 기존 함수 재사용
- 대출 전용 변경점은 아래만 둔다.
  - 도메인별 family/keyword catalog
  - 대출 전용 writing rules
  - 대출 전용 quality gates
  - 대출 전용 tags/disclaimer

### 3. 이미지 생성 로직은 그대로 재사용한다

GPT 이미지 생성은 `tax`와 동일하게 `build_publish_bundle → _build_gpt_publish_image_plans → _render_gpt_publish_assets` 흐름을 그대로 사용한다.

대출 도메인 추가 시 필요한 최소 변경:

- `TOPIC_COLORS["loan"]`
- `THUMBNAIL_CHIPS["loan"]`
- `SECTION_HEADING_RENAMES["loan"]`
- `_content_domain()`의 대출 키워드 판별
- `LOAN_DEFAULT_TAGS`

이미지 프롬프트는 새 엔진을 만들지 않고, 주제별 문구만 대출형으로 바꾼다.

예시 톤:

- 썸네일: `DSR·LTV·잔금대출 체크`
- 요약 보드: `대출 가능 여부를 보기 전 확인할 3가지`
- 흐름도: `소득 → 규제지역 → 주택수 → 한도 → 실행일`
- 비교 보드: `중도금대출 vs 잔금대출 vs 주담대`
- 시나리오: `입주 직전 잔금 부족 사례`

## 키워드 카탈로그 설계

기본 family는 아래 12개로 시작한다.

1. `대출기초`
2. `주택담보대출`
3. `중도금대출`
4. `잔금대출`
5. `전세대출`
6. `DSR_LTV_DTI`
7. `정책대출`
8. `청약분양대출`
9. `경매대출`
10. `갈아타기대출`
11. `신용소득심사`
12. `대출실행체크`

### family별 primary keyword 예시

| family | primary keywords |
|---|---|
| 대출기초 | 주택담보대출, 대출한도, 대출금리, 원리금상환, 만기상환 |
| 주택담보대출 | 주담대 한도, 주담대 금리, 변동금리 고정금리, 대환대출 |
| 중도금대출 | 중도금 대출, 중도금 이자후불제, 집단대출, 중도금 보증 |
| 잔금대출 | 잔금대출, 입주 잔금, 잔금 부족, 분양 잔금대출 |
| 전세대출 | 전세자금대출, 버팀목전세대출, 보증보험, 전세대출 연장 |
| DSR_LTV_DTI | DSR, LTV, DTI, 스트레스 DSR, 소득산정 |
| 정책대출 | 디딤돌대출, 보금자리론, 신생아 특례대출, 청년전용대출 |
| 청약분양대출 | 분양 중도금 대출, 청약 당첨 대출, 옵션비 대출, 입주자 대출 |
| 경매대출 | 경락잔금대출, 낙찰 잔금대출, 경매 대출 한도, 경매 명도자금 |
| 갈아타기대출 | 기존 주담대, 일시적 2주택 대출, 처분조건 대출, 전세퇴거자금 |
| 신용소득심사 | 신용점수, 소득증빙, 재직기간, 프리랜서 대출, 사업자 대출 |
| 대출실행체크 | 대출 실행일, 필요서류, 은행 심사, 대출 거절, 금리 비교 |

## 대출 도메인 프롬프트 가드

대출 콘텐츠는 금융상품 추천이나 확정 한도 안내처럼 보이면 안 된다. 아래 가드를 둔다.

### writing rules

- 첫 3문장 안에 독자의 상황, 확인할 대출 종류, 먼저 볼 기준을 제시한다.
- 대출 가능 여부를 단정하지 않는다.
- 한도·금리·승인 가능성은 소득, 신용, 주택수, 규제지역, 담보가치, 은행 심사에 따라 달라진다고 설명한다.
- LTV, DSR, DTI는 개념 설명과 확인 순서 중심으로 다룬다.
- 특정 은행·상품을 확정 추천하지 않는다.
- 정책대출은 한국주택금융공사, 주택도시기금, 은행 상담, 금융감독원 안내 등 공식 확인 경로를 넣는다.
- 무리한 대출, 허위 소득증빙, 명의 차용, 편법 대출을 암시하지 않는다.
- 계산 예시는 단순 예시로만 쓰고 실제 가능 금액은 금융기관 심사로 확인하도록 안내한다.

### quality gates

- 첫 문장이 대출 일반론이나 금융상품 홍보로 시작하면 실패다.
- DSR, LTV, 소득, 신용, 주택수, 담보가치, 실행일 중 주제에 맞는 핵심 변수가 최소 2개 이상 나와야 한다.
- 대출 승인, 한도, 금리를 확정적으로 단정하면 실패다.
- 허위 서류, 우회 대출, 명의 차용으로 읽힐 수 있는 문장이 나오면 실패다.
- 공식 확인 경로나 은행 상담 안내가 전혀 없으면 실패다.
- FAQ 답변이 본문 문장을 그대로 반복하면 실패다.
- 체크리스트가 이유 없는 단답 나열이면 실패다.

## 네이버 발행 설정

`run_longtail_publish_prod.sh`에 추가한다.

```bash
export LONGTAIL_LOAN_NAVER_CATEGORY_NO="${LONGTAIL_LOAN_NAVER_CATEGORY_NO:-19}"
export LONGTAIL_LOAN_NAVER_CATEGORY_NAME="${LONGTAIL_LOAN_NAVER_CATEGORY_NAME:-부동산 대출}"
```

`DOMAIN_CONFIGS` 마지막에 추가한다.

```python
{
    'domain': 'loan',
    'label': '대출 롱테일',
    'category_no': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NO', '19').strip(),
    'category_name': os.environ.get('LONGTAIL_LOAN_NAVER_CATEGORY_NAME', '부동산 대출'),
},
```

## 테스트 계획

필수 테스트:

1. `loan` 도메인 replenish가 후보를 생성하는지
2. `loan` semantic_key가 다른 도메인과 충돌하지 않는지
3. `loan` prompt에 대출 전용 writing rules/quality gates가 들어가는지
4. `loan` publish bundle에 대출 태그와 categoryNo 19가 반영되는지
5. 크론 도메인 순서가 `cheongyak → auction → tax → loan`인지
6. GPT 이미지 plan이 기존 공용 구조로 생성되는지

실행 검증:

```bash
python3 -m unittest tests.test_naver_bundle_publish tests.test_longtail
```

## 최종 구조

대출 도메인은 별도 생성 엔진을 만들지 않는다.

- 바뀌는 것: 키워드, family, 프롬프트 가드, 태그, 카테고리
- 재사용하는 것: 후보 생성, 제목 생성, 글 생성, 이미지 생성, 네이버 업로드, 크론 실행, 중복 방지

이 구조면 이후 `부동산 정책`, `부동산 상식` 같은 도메인도 같은 방식으로 키워드와 가드만 추가해 확장할 수 있다.
