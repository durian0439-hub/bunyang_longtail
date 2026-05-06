# bunyang_longtail 아키텍처 v2

작성일: 2026-04-20
상태: 설계 기준 문서

이 문서는 bunyang_longtail의 **DB 스키마 v2, A-Z 커리큘럼 spine, GPT 웹 워커 구조, 상태머신, 중복 방지 규칙**을 고정하기 위한 설계 문서입니다.

---

## 1. 목표

이 시스템의 목표는 아래 4가지를 동시에 만족하는 것입니다.

1. 책 한 권처럼 읽히는 `부동산 실전 A-Z` 고정 목차를 DB 자산으로 장기 관리한다.
2. 청약 롱테일 주제는 A-Z 본문을 보조하는 branch로 유지한다.
3. 같은 의미의 주제를 다른 검색의도와 다른 서술각도로 지속 변형한다.
4. 글 생성과 이미지 생성은 **GPT 웹 경로를 우선 사용**하여 현재 요금제를 최대한 활용한다.
5. 발행 결과는 중복 없이 누적되고, 성과에 따라 다음 주제 선택과 변형 전략이 개선된다.

---

## 2. 고정 파이프라인

고정 순서:

1. Curriculum Track / Node Seed
2. Curriculum Hub Post Render
3. Topic Catalog
4. Cluster Generator
5. Variant Generator
6. Prompt Package Builder
7. Article Bundle Orchestrator
8. GPT Web Draft Worker
9. GPT Web Image Worker
10. Similarity / Policy Guard
11. Review Queue
12. Publish Queue
13. Publish History
14. Performance Feedback

설명:
- Curriculum Track은 책 한 권 단위 발행 spine이다.
- Curriculum Node는 챕터 1개 단위 고정 목차다.
- Curriculum Hub Post는 전체 목차 공지글/고정글 본문을 관리한다.
- Cluster는 의미 단위다.
- Variant는 표현 단위다.
- Article Bundle은 **글 1개와 그 글에 종속된 이미지 세트**를 묶는 실행 단위다.
- Draft는 실제 생성 결과다.
- Generation Job은 내부 워커 실행 기록이다.
- Image Asset은 이미지 결과물이다.
- Publish History는 발행 결과다.
- Performance Feedback은 후속 최적화용 지표다.

---

## 3. 엔터티 정의

## 3.1 curriculum_track

역할:
- A-Z 책 목차형 발행 트랙의 최상위 단위
- 기본 트랙은 `real-estate-a-z` / `부동산 실전 A-Z`
- 운영 전략은 `A-Z spine 70~80% + longtail branch 20~30%`를 기본값으로 둔다.

### 필수 컬럼
- `id`
- `track_key` unique
- `title`
- `description`
- `strategy_json`
- `target_ratio`
- `status`
- `created_at`
- `updated_at`

---

## 3.2 curriculum_node

역할:
- 책의 챕터 1개에 해당하는 고정 발행 단위
- 기본 seed는 65개 챕터이며, 청약·분양·대출·세금·경매를 하나의 실전 목차로 묶는다.
- `chapter_no` 순서가 발행 우선순위다.

### 필수 컬럼
- `id`
- `track_id` FK
- `node_key` unique
- `chapter_no`
- `part_no`
- `part_title`
- `title`
- `domain`
- `family`
- `primary_keyword`
- `secondary_keyword`
- `audience`
- `search_intent`
- `scenario`
- `comparison_keyword`
- `angle`
- `required`
- `priority`
- `status`
- `outline_json`
- `policy_json`
- `published_at`
- `created_at`
- `updated_at`

### 상태
- `queued`
- `active`
- `published`
- `blocked`
- `archived`

---

## 3.3 curriculum_node_variant

역할:
- `curriculum_node`와 실제 발행 후보인 `topic_variant`를 연결한다.
- 기본 역할은 `primary`이며, 필요 시 후속 보강글은 `branch` 역할로 확장할 수 있다.

### 필수 컬럼
- `id`
- `node_id` FK
- `variant_id` FK
- `variant_role`
- `created_at`

---

## 3.4 curriculum_hub_post

역할:
- A-Z 전체 목차 전용 허브글을 DB에서 관리한다.
- 발행된 챕터는 링크로, 미발행 챕터는 `발행 예정`으로 렌더링한다.
- 네이버 공지글 또는 고정글로 운영하고, 개별 글에는 전체 목차를 길게 붙이지 않고 허브글 링크만 연결한다.
- 새 A-Z 글이 발행되면 허브 본문을 다시 렌더링하고 `needs_sync=1`로 표시한다.

### 필수 컬럼
- `id`
- `track_id` FK
- `hub_key` unique
- `title`
- `naver_url`
- `status`
- `body_markdown`
- `body_hash`
- `linked_node_count`
- `total_node_count`
- `needs_sync`
- `pinned`
- `last_rendered_at`
- `last_synced_at`
- `created_at`
- `updated_at`

### 상태
- `draft`
- `published`
- `archived`

---

## 3.5 topic_cluster

역할:
- 같은 의미의 주제를 묶는 최상위 단위
- 중복 방지의 1차 기준

핵심 예시:
- `신혼부부 특별공급 외벌이 소득 기준 초과 시 가능 여부`
- `무주택 기준 분양권 보유 시 청약 가능 여부`

### 필수 컬럼
- `id`
- `semantic_key` unique
- `family`
- `primary_keyword`
- `secondary_keyword`
- `audience`
- `search_intent`
- `scenario`
- `comparison_keyword`
- `priority`
- `outline_json`
- `policy_json`
- `status`
- `created_at`
- `updated_at`

---

## 3.6 topic_variant

역할:
- 같은 의미를 다른 제목/각도/훅으로 푼 발행 후보
- 중복 방지의 2차 기준

예시:
- 판단형
- 비교형
- 실수방지형
- 체크리스트형
- 사례형
- FAQ형

### 필수 컬럼
- `id`
- `cluster_id` FK
- `variant_key` unique
- `angle`
- `title`
- `slug` unique
- `seo_score`
- `prompt_json`
- `prompt_version`
- `route_policy`
- `status`
- `use_count`
- `last_used_at`
- `created_at`
- `updated_at`

### 상태
- `queued`
- `reserved`
- `drafting`
- `drafted`
- `published`
- `blocked`
- `archived`

---

## 3.7 article_bundle

역할:
- 글 1개 기준으로 본문과 이미지 생성을 묶는 상위 실행 단위
- 사용자 관점의 실제 작업 단위

### 필수 컬럼
- `id`
- `variant_id` FK
- `bundle_status`
- `primary_draft_id`
- `primary_thumbnail_id`
- `selected_image_ids_json`
- `generation_strategy`
- `created_at`
- `updated_at`

### 상태
- `queued`
- `drafting_text`
- `rendering_image`
- `bundled`
- `reviewed`
- `published`
- `blocked`

규칙:
- 글이 없는데 이미지만 있는 bundle 금지
- 글과 이미지 의미가 어긋나면 bundled 처리 금지

---

## 3.8 generation_job

역할:
- GPT 웹 워커 실행 이력 저장
- text/image 생성 작업을 공통 포맷으로 관리

### 필수 컬럼
- `id`
- `variant_id` FK
- `worker_type` (`text`, `image`)
- `route` (`gpt_web_playwright`, `gpt_web_mcp`, `fallback`)
- `profile_name`
- `model_label`
- `prompt_version`
- `request_payload_json`
- `response_payload_json`
- `status`
- `attempt_no`
- `error_code`
- `error_message`
- `started_at`
- `finished_at`
- `created_at`

### 상태
- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `blocked`

### 핵심 원칙
- GPT 웹을 쓸 때마다 job을 남긴다.
- text와 image는 job을 분리한다.
- fallback은 route에 명시한다.

---

## 3.9 article_draft

역할:
- 실제 글 초안 저장
- 생성 결과와 검수 상태를 추적

### 필수 컬럼
- `id`
- `variant_id` FK
- `source_job_id` FK
- `title`
- `excerpt`
- `article_markdown`
- `structured_json`
- `content_hash`
- `normalized_title_hash`
- `similarity_score`
- `quality_score`
- `model_route`
- `prompt_version`
- `status`
- `review_note`
- `created_at`
- `updated_at`

### 상태
- `drafted`
- `reviewed`
- `approved`
- `published`
- `rejected`

---

## 3.10 image_asset

역할:
- GPT 웹 이미지 생성 결과 저장
- 썸네일/요약카드/보조 시각자료를 분리 관리

### 필수 컬럼
- `id`
- `variant_id` FK
- `source_job_id` FK
- `image_role` (`thumbnail`, `summary_card`, `section_visual`, `faq_visual`)
- `prompt_text`
- `prompt_hash`
- `file_path`
- `mime_type`
- `width`
- `height`
- `phash`
- `status`
- `created_at`

### 상태
- `rendered`
- `approved`
- `published`
- `rejected`

---

## 3.11 publish_history

역할:
- 실제 발행 결과 저장
- 나중에 중복 차단과 성과 연결의 기준점

### 필수 컬럼
- `id`
- `variant_id` FK
- `draft_id` FK
- `channel`
- `target_account`
- `publish_mode`
- `published_title`
- `naver_url`
- `published_at`
- `result_json`

---

## 3.12 similarity_index

역할:
- 제목/본문/의미 유사도 검사용 인덱스 저장

### 필수 컬럼
- `id`
- `draft_id` FK
- `semantic_key`
- `content_hash`
- `normalized_title_hash`
- `embedding_ref`
- `created_at`

---

## 3.13 performance_feedback

역할:
- 발행 성과를 다음 주제 선택과 변형 전략에 반영

### 필수 컬럼
- `id`
- `publish_history_id` FK
- `metric_date`
- `views`
- `comments`
- `likes`
- `manual_score`
- `dwell_proxy`
- `note`
- `created_at`

---

## 4. 상태머신

## 4.1 article_bundle 상태머신

`queued -> drafting_text -> rendering_image -> bundled -> reviewed -> published`

예외:
- `blocked`

규칙:
- bundled는 본문 1개와 이미지 1개 이상이 연결되어야 한다.
- published는 publish_history와 연결되어야 한다.

## 4.2 variant 상태머신

`queued -> reserved -> drafting -> drafted -> published`

예외:
- `blocked`
- `archived`

규칙:
- published는 draft와 publish_history가 둘 다 있어야 한다.
- blocked는 정책 또는 중복 차단에 걸린 상태다.

## 4.3 generation_job 상태머신

`queued -> running -> succeeded`

예외:
- `failed`
- `cancelled`
- `blocked`

규칙:
- failed 후 재시도 시 `attempt_no` 증가
- fallback 사용 시 새 job으로 남긴다

## 4.4 article_draft 상태머신

`drafted -> reviewed -> approved -> published`

예외:
- `rejected`

규칙:
- approved 전 자동 발행 금지
- published 전 similarity guard 재통과 필수

---

## 5. GPT 웹 워커 설계

## 5.1 우선순위
1. `gpt_web_playwright`
2. `gpt_web_mcp`
3. `fallback`

## 5.2 워커 분리
- `text_worker`
- `image_worker`

분리 이유:
- 실패 패턴이 다르다
- 세션 관리 방식이 다르다
- 병렬 처리 전략이 다르다

단,
- **제품 단위는 worker가 아니라 article bundle** 이다.
- 즉 외부에서는 "글 1개 생성"으로 보이고, 내부에서만 text/image worker가 분리된다.

## 5.3 브라우저 프로필 정책
- `gpt_text_profile_dev`
- `gpt_image_profile_dev`
- `gpt_text_profile_prod`
- `gpt_image_profile_prod`

규칙:
- text와 image 프로필을 섞지 않는다.
- dev와 prod 프로필을 섞지 않는다.
- 동일 프로필에서 동시 다중 작업 금지

## 5.4 기록 원칙
GPT 웹 생성 결과는 아래를 반드시 남긴다.
- route
- profile_name
- model_label
- prompt_version
- source_variant_id
- request/response payload 요약

---

## 6. 중복 방지 3단 가드

## 6.1 1차: semantic_key
- 같은 의미 cluster 재생성 금지

## 6.2 2차: variant_key / normalized_title_hash
- 제목만 바꾼 중복 금지

## 6.3 3차: content_hash + embedding similarity
- 본문 의미 유사도 기준 초과 시 차단

### 기본 제안 임계치
- normalized title exact match: 차단
- content hash exact match: 차단
- embedding similarity >= 0.88: 검토 또는 차단

---

## 6.4 발행 후보 선택 순서

기본 선택 순서:
1. 미완성 복구 후보: 초안은 있는데 이미지가 비어 있는 bundle
2. A-Z curriculum 후보: 해당 도메인의 가장 앞선 미발행 `chapter_no`
3. Longtail branch 후보: 기존 `topic_variant` 대기열 중 다양성/가중치 기준 후보

규칙:
- A-Z 후보는 책 목차 순서를 유지하기 위해 `chapter_no` 오름차순으로 선택한다.
- 이미 발행된 cluster/semantic_key/title/slug는 기존 중복 가드로 차단한다.
- A-Z 후보가 없을 때만 기존 무한 롱테일 branch를 사용한다.
- `mark_published`는 `topic_variant`뿐 아니라 연결된 `curriculum_node`도 `published`로 함께 갱신한다.
- 발행 URL이 생기면 `curriculum_hub_post.body_markdown`을 다시 렌더링하고 `needs_sync=1`로 표시한다.
- `publish-curriculum-hub`는 저장된 목차글 URL이 있으면 네이버 수정 URL로 기존 글을 갱신하고, 없으면 새 목차글을 발행한다.
- 개별 글에는 전체 목차를 길게 삽입하지 않고, 목차 허브글 URL이 있을 때 `전체 목차 보기` 링크만 관련 글 영역에 넣는다.

---

## 7. 네이버 SEO 생성 원칙

모든 prompt package는 아래를 유지한다.
- 첫 문단 결론 우선
- 제목에 대상자 + 조건 + 상황 포함
- FAQ 최소 6개
- 체크리스트 포함
- 뜻풀이형 글 금지
- 같은 cluster라도 도입 훅과 사례 흐름은 다르게

고정 섹션:
1. 상단 요약
2. 이 글에서 바로 답하는 질문
3. 핵심 조건 정리
4. 헷갈리기 쉬운 예외
5. 실전 예시 시나리오
6. 체크리스트
7. FAQ
8. 마무리 결론

---

## 8. 동일 내용을 다르게 푸는 허용 기준

허용:
- 대상자 다름
- 상황 다름
- 검색의도 다름
- 비교축 다름
- 실제 판단 결과 다름

비허용:
- 제목만 바꿈
- 조사만 바꿈
- 숫자만 바꿈
- FAQ 순서만 바꿈
- 같은 결론을 다른 말로만 반복

---

## 9. 구현 우선순위

### Phase 2-1
- generation_job 테이블 추가
- article_draft 확장
- image_asset 테이블 추가
- 상태머신 코드 반영

### Phase 2-2
- GPT Web text worker 구현
- GPT Web image worker 구현
- job logging 반영

### Phase 3
- similarity_index 구현
- content hash + title hash + embedding guard 구현
- fallback route 정책 구현

### Phase 4
- publish_history / performance_feedback 구현
- bunyang 기존 발행 파이프라인 연동

---

## 10. 이번 설계에서 고정할 기준

이 시스템은 아래 한 줄을 지켜야 한다.

**같은 청약 주제를 오래 돌려도, 글 1개와 그에 맞는 이미지 세트를 하나의 작업으로 보면서, 구조는 고정하고, DB를 기준으로, GPT 웹을 효율적으로 활용하고, 중복 없이 장기 운영 가능해야 한다.**
