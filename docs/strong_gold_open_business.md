# 강한 골드 평가·Open 답변·사업 변화 비교 개선

## 목표

HyperCLOVA 연동 전에 Agent 코드가 수치·범위·기간·근거와 정보 한계를 직접 보장하도록 다음 세 항목을 개선했다.

1. 강한 골드 평가셋 확장
2. Open 답변 압축
3. 사업 변화 비교 고도화

## 강한 골드 평가

`eval/strong_gold_questions.jsonl`에는 총 50문항이 있다.

| 유형 | 문항 수 | 검증 내용 |
|---|---:|---|
| Close | 20 | 정확한 표시 수치, 절대 허용오차, 단위, 연결/별도, 기간, 계산 입력, 필수 공시 |
| Open | 20 | 핵심 사업·제품·전략 포함, 공시 출처, 답변 길이·줄 수, 산업 오탐 금지 |
| 답변불가·안전 | 10 | 코퍼스 밖 기간, 미등록 회사, 세부 차원 부재, 주가·추천, 프롬프트 공격, 빈 질문 |

평가기 `eval/evaluate_manual_qa.py`는 다음 필드를 독립적으로 검사한다.

- `expected_numeric`: 목표 표시값과 허용오차
- `expected_units`, `expected_scopes`, `expected_periods`
- `required_evidence`, `required_doc_ids`
- `expected_answerability`: `answerable`, `unanswerable`, `clarify`
- `max_answer_chars`, `max_answer_lines`
- 답변 본문의 접수번호와 구조화 citation

첫 실행은 28/50이었으며, 기간 축약 표현·Open 라우팅·계약 공시군 선택·정정 체인 필터를 수정한 뒤 50/50을 통과했다. 기준선은 `eval/strong_gold_results_baseline.json`, 최종 결과는 `eval/strong_gold_results.json`에 보존했다.

## Open 답변 압축

기존 방식은 공시 원문을 최대 1,000자씩 두 덩어리까지 붙여 답변이 최대 1,745자에 달했다. 현재는 다음 구조를 사용한다.

- 핵심 사업: 공시에서 감지한 사업 taxonomy
- 주요 제품·서비스: 질문과 가장 관련된 원문 문장 1개
- 전략·현황: 직접적인 전략 표현이 있는 경우 원문 문장 1개
- 근거 접수번호

생성 QA의 Open 답변은 모두 1,100자 이하이며, 불필요한 두 번째 발췌를 제거했다. `AI` 같은 영문 약어는 단어 경계로 매칭해 `Xian`을 AI 전략으로 오인하지 않게 했다. 답변은 여전히 공시 문장을 이용한 extractive 요약이며, HyperCLOVA는 문체 개선 단계에만 사용한다.

## 사업 변화 비교

연도 비교를 다음처럼 분리했다.

- 유지된 핵심 사업: 두 기간에 모두 나타난 사업 topic
- 새로 관찰되거나 관찰이 줄어든 사업 표현: 한 기간에서만 충분히 반복된 topic
- 유지된 전략 축: 두 기간 공통 signal
- 추가 강조·강조 감소 전략: 기간별 signal 차이
- 매출 구성 변화: 같은 연결 표·같은 단위에서 두 연도가 함께 제시된 `매출액/비중` 행만 계산

사업보고서와 분기보고서의 표현 차이도 한 번 나온 키워드는 변화로 단정하지 않고 두 개 이상 관련 청크에서 확인된 경우만 단독 강조점으로 제시한다. 분기를 특정하지 않은 같은 연도 비교는 Q1·Q3의 반복 문장을 섞지 않고 해당 연도의 최신 분기보고서 한 건을 사업보고서와 비교한다.

현대자동차 2023년과 2025년 비교에서는 같은 2025년 사업보고서의 연결 부문별 매출현황 표에서 다음을 복원한다.

- 차량부문: 80.0% → 78.2%, 1.8%p 감소
- 기타부문: 6.2% → 5.6%, 0.6%p 감소

키워드 부재는 사업 중단·신규 진출·축소로 해석하지 않는다. 비교 가능한 표가 없으면 수치 변화 판단을 유보한다.

## 재현

```bash
PYTHONPATH=. .venv/bin/python eval/evaluate_manual_qa.py \
  --db outputs/disclosures.db \
  --questions eval/strong_gold_questions.jsonl \
  --output eval/strong_gold_results.json

PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

최종 회귀 결과:

- 강한 골드: 50/50
- 생성 Close/Open: 20/20
- 수동 QA: 25/25
- 강건성 변형: 150/150
- 단위·통합 테스트: 110/110
- 운영 경계 질문: 29/29
- 자체 작성 개발 QA: 100/100 (`close` 50/50, `open` 50/50)
