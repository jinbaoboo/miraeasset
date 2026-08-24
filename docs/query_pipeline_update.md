# Query Pipeline Update

## 목적

기존 Agent는 공시 DB와 검색 구조는 갖추고 있었지만, 복합 질의에서 필요한 실행 흐름이 한 덩어리로 섞여 있었다. 이번 변경은 자연어 질문을 바로 RAG 검색에 던지지 않고, 아래 순서로 처리하도록 정리한 것이다.

```text
1. 질문 분해
2. 문서 후보 필터링
3. 구조화 값 추출
4. 질문 유형별 정형 도구 실행
5. 계산/비교 및 근거 답변 생성
```

핵심 방향은 LLM이 숫자나 공식을 추측하지 않게 하고, DB 조회와 코드 계산이 답변의 뼈대를 만들게 하는 것이다.

## 1. 질문 분해

수정 파일: `src/retrieval/query_analyzer.py`

사용자 질문에서 다음 정보를 plan으로 뽑는다.

- 기업: `corp_code`, `corp_name`, `listed_name`
- 기간: 연도, 월, 분기
- 지표: 매출액, 영업이익, 설비투자, 계약금액 등
- 공시군: 정기공시, 주요사항, 거래소공시, 지분공시
- 작업 유형: 조회, 비교, 계산
- 계산 의도: 전년대비 증가율, 차이, 합계, 비율
- 핵심 질문 유형: `financial_metric`, `investment_plan`, `capex_comparison`,
  `financing_history`, `contract_termination`, `business_change`, `correction_history`

예시:

```text
질문: 테스트 2025년 전년대비 매출액 증가율은?
```

```json
{
  "metric": "revenue",
  "required_metrics": ["revenue"],
  "intent": "calculation",
  "calculation": {
    "operation": "growth_rate",
    "target_year": 2025,
    "baseline_year": 2024
  }
}
```

추가로 영업이익률, 순이익률, 부채비율, ROE처럼 여러 지표가 필요한 계산도 plan에 필요한 입력값을 명시한다.

## 2. 문서 후보 필터링

수정 파일: `src/retrieval/hybrid_search.py`, `src/agent/disclosure_agent.py`

질문 plan을 SQL 조건으로 바꿔 먼저 볼 공시 문서를 좁힌다.

- 회사 조건
- 연도 조건
- 공시군 조건
- 보고서 유형 조건
- 최신 유효본 조건

전년대비 질문처럼 기준연도가 필요한 경우에는 사용자가 2025년만 말해도 2024년 후보 문서까지 자동 포함한다.

이후 `cells`, `event_fields`, FTS 검색은 후보 `doc_id` 안에서만 수행된다.

## 3. 구조화 값 추출

수정 파일: `src/retrieval/hybrid_search.py`, `src/agent/disclosure_agent.py`

후보 문서 안에서 `required_metrics`에 맞는 값을 구조화 테이블에서 꺼낸다.

- 재무 수치: `cells`
- 계약금액, 비율 등 이벤트 값: `event_fields`

예시:

```text
영업이익률 질문
→ required_metrics = ["operating_profit", "revenue"]
→ 영업이익과 매출액을 각각 cells에서 조회
```

값만 가져오는 것이 아니라 단위, 연결/별도 기준, 보고서명, 접수번호 등 citation도 함께 유지한다.

추가로 다음 전용 조회를 사용한다.

- 투자계획: 제목·단위 표와 데이터 표가 나뉘어 있어도 인접 logical table의 단위를 승계하고, 행·연간계획·분기실적을 복원한다.
- 설비투자 비교: 연결 현금흐름표의 `유형자산의 취득`을 우선하고, 단위를 원화로 환산한 후 현금유출 절대값을 비교한다.
- 자금조달: 유상증자·CB·BW·EB 결정·정정 체인을 집계하고, 완료 공시 coverage가 없으면 실제 조달을 단정하지 않는다.
- 계약 생애주기: 정정본을 원계약에 묶고 관련공시일·계약명·상대방·기간으로 후속 해지를 연결한다.
- 정정: original, before, after, current effective를 전 공시군에서 같은 방식으로 조회한다.
- 사업 변화: 두 연도의 사업보고서 `II. 사업의 내용`을 8개 동일 근거 분류와 사업 신호로 비교한다.

## 4. 계산/비교 실행

수정 파일: `src/agent/disclosure_agent.py`, 기존 계산 도구 `src/tools/calculator.py`

추출된 구조화 값을 코드가 직접 계산한다. LLM은 계산하지 않는다.

현재 처리하는 주요 계산:

- 전년대비 증가율: `(current - baseline) / abs(baseline) * 100`
- 차이: `current - baseline`
- 비율: `numerator / denominator * 100`
- 합계: `sum(values)`

계산 전에는 다음을 검사한다.

- 필요한 값이 모두 있는가
- 통화와 단위 scale이 있는가
- 연결/별도 기준이 일치하는가
- 분모가 0이 아닌가

예시 답변:

```text
테스트의 2025년 영업이익 증감률은(는) 25.00% 증가입니다.
입력값은 2025년 1000, 2024년 800입니다.
계산식: (1000000000 - 800000000) / abs(800000000) * 100.
근거 접수번호: ...
```

## 테스트

수정 파일: `tests/test_storage_and_agent.py`

추가/확인한 테스트:

- 전년대비 증가율 질문이 `growth_rate`로 분해되는지
- 영업이익률 질문이 영업이익/매출액 입력값으로 분해되는지
- 후보 문서 필터가 회사/기간/분기 조건을 적용하는지
- 전년대비 질문에서 기준연도 문서가 포함되는지
- 후보 문서 안에서 구조화 값이 추출되는지
- 전년대비 증가율 답변이 코드 계산으로 생성되는지
- 영업이익률 답변이 코드 계산으로 생성되는지

검증 명령:

```bash
python -m unittest discover -s tests -v
```

현재 결과:

```text
74 tests passed
```

## 주의사항

자금조달 답변은 공시 접수일과 발행 `결정`을 기준으로 하며 납입·실시 완료를 단정하지 않는다. 계약 해지 결과도 제공 코퍼스에 존재하는 후속 해지 공시와 명확히 연결되는 건에 한정한다.
