# 공시 Agent 구현 아키텍처

## 목표와 원칙

시스템은 제공 코퍼스만을 사실 출처로 사용한다. LLM은 검색 결과를 문장화하는 선택적 계층이고, 문서 선택·수치 추출·단위 해석·계산·인용은 결정론적 코드가 담당한다. 근거가 없으면 답변하지 않으며 raw 원본은 수정하지 않는다.

## 실행 흐름

```text
manifest / universe / raw
  → 형식 판별(XML, 실제 HTML, 예외 PDF)
  → 메모리 복구와 파싱
  → document / section / chunk / table / cell / correction / event
  → SQLite 정규화 + FTS5
  → 질문 메타데이터 분석
  → 정형 셀 조회 + 본문·표·이벤트 검색
  → Decimal 계산 및 정정본 정책
  → citation 포함 답변
  → 선택적으로 HyperCLOVA X로 근거 범위 내 문장화
```

## 파서

- `periodic_parser.py`: DART DOCUMENT XML 계열의 공통 트리 순회. 이름은 기존 호환성을 위해 유지하지만 major와 holding에도 재사용한다.
- `xml_recovery.py`: strict parse 실패 시 raw bytes를 메모리에서만 복구한다.
- `table_parser.py`: physical table을 rowspan/colspan grid로 펼치고 logical table 및 canonical cell을 만든다.
- `exchange_parser.py`: 확장자가 `.xml`인 거래소 HTML을 인코딩 fallback과 함께 해석한다.
- `periodic_parser.py`의 PDF fallback: XML이 제공되지 않은 3건은 공식 PDF 페이지 본문을 추출하고 페이지 locator를 남긴다. 레이아웃 손실 가능성 때문에 PDF 표를 cell로 승격하지 않는다.
- `correction_parser.py`: 정정 전·후, 사유, 현재 유효 값 포인터를 보존한다.
- `event_normalizer.py`: 주요사항·거래소·지분공시를 공통 event/field 레코드로 바꾼다.

## 저장소

SQLite는 JSON 교환 스키마의 반복 `source`를 관계형으로 정규화한다. 핵심 테이블은 `documents`, `sections`, `chunks`, `logical_tables`, `cells`, `corrections`, `correction_items`, `events`, `event_fields`이다. `chunks_fts`, `tables_fts`, `events_fts`는 검색용이며 숫자는 반드시 `cells.numeric_value` 또는 `event_fields.numeric_value`에서 조회한다.

문서·표·셀은 모두 `doc_id`, `rcept_no`, source file, locator로 원문에 역추적 가능하다. 배치는 한 문서 단위 트랜잭션이며 이미 완료한 `doc_id`는 재실행 때 건너뛴다.

## 버전과 정정

정기공시는 `corp_code + doc_subtype + base_year + base_month`로 강하게 연결하여 마지막 접수본을 최신으로 표시한다. 주요사항·거래소·지분공시는 동일 유형의 독립 사건이 반복될 수 있어 날짜·유형만으로 억지 연결하지 않는다. 정정표 자체의 before/after는 항상 보존하며 비정기 체인은 명확한 최초 제출일과 관련공시 키를 추가 추출한 뒤 연결하는 것이 안전하다.

## 검색과 계산

질문 분석은 회사명/상장명/종목코드, 연도·분기, 연결/별도, 지표, 공시군, lookup/comparison/calculation intent를 추출한다. 정형 재무 질문은 exact cell 조회를 우선하고, 서술 질문은 FTS5 결과를 합친다. 검색 결과는 최신 유효본 필터를 기본 적용한다.

계산은 `Decimal` 기반이며 입력값·연산·공식·결과를 함께 남긴다. 빈칸, dash, 해당 없음은 0으로 바꾸지 않는다. 서로 다른 통화·scale은 자동 합산하지 않는다.

## 개인정보와 로그

지분공시 원문은 공개자료지만 검색·로그에는 필요한 최소 정보만 둔다. 주민등록번호 형식, 사업자등록번호, 휴대전화, 이메일은 파생 검색 텍스트에서 마스킹한다. raw와 정확한 구조화 cell은 로컬에 남기되 외부 응답에는 질문과 무관한 개인 식별정보를 노출하지 않는다.

## 남은 외부 의존 작업

HyperCLOVA X endpoint·키 입력 후 실제 응답 형식과 rate limit을 검증해야 한다. 대회 제출 조직/브랜치·배포 환경·최종 설계 승인 역시 사용자 권한이 필요하다. 이 세 항목 외의 파싱·검색·계산·API·테스트는 로컬에서 독립 실행 가능하다.
